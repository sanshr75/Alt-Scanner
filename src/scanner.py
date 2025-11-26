# src/scanner.py

import os
import json
from pathlib import Path
from datetime import datetime

import requests
import yaml
import pandas as pd

from src.indicators import ema, rsi, atr, macd_hist
from src.scoring import score_signal
from .mexc_client import fetch_klines


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


def load_config():
    cfg_path = BASE_DIR / "config.yaml"
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def send_telegram(text: str):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("⚠ Telegram variables missing. Printing message instead:")
        print(text)
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}

    resp = requests.post(url, json=payload, timeout=15)
    print("Telegram status:", resp.status_code, resp.text)


def log_signal(data: dict):
    DATA_DIR.mkdir(exist_ok=True)
    filename = DATA_DIR / f"alerts-{datetime.utcnow().strftime('%Y%m%d')}.json"

    entry = json.dumps(data)

    with filename.open("a", encoding="utf-8") as f:
        f.write(entry + "\n")

    print(f"🗂 Logged → {filename.name}")


def main():

    print("🚀 Alt-Scanner starting...")

    config = load_config()
    print("📄 Config loaded.")

    # ===============================
    # Fetch data from MEXC
    # ===============================
    try:
        candles = fetch_klines("BTC_USDT", "5m", 50)

        candles["ema20"] = ema(candles["close"], 20)
        candles["ema50"] = ema(candles["close"], 50)
        candles["rsi14"] = rsi(candles["close"], 14)
        candles["atr14"] = atr(candles, 14)
        candles["macd_hist"] = macd_hist(candles["close"])
        candles["vol_sma20"] = candles["volume"].rolling(20, min_periods=1).mean()

        last = candles.iloc[-1]
        last_vol_sma20 = candles["vol_sma20"].iloc[-1]

        ema_align = last["close"] > last["ema20"] > last["ema50"]
        macd_pos = last["macd_hist"] > 0
        vol_spike = last["volume"] > last_vol_sma20 * 1.5

        print("\n🧩 Feature flags:")
        print(f"ema_align: {ema_align}")
        print(f"macd_pos: {macd_pos}")
        print(f"vol_spike: {vol_spike}")

    except Exception as e:
        print(f"❌ MEXC fetch failed: {e}")
        return

    # ===============================
    # Build dataset
    # ===============================
    features = {
        "ema_align": bool(ema_align),
        "macd_pos": bool(macd_pos),
        "vol_spike": bool(vol_spike),
        "mtf_ema_align": False,
        "ctx_adj": 0,
        "tags": [
            tag for tag, v in {
                "EMA_TREND": ema_align,
                "MACD_MOMENTUM": macd_pos,
                "VOLUME_SPIKE": vol_spike,
            }.items() if v
        ]
    }

    # Score
    scores = score_signal(features, config)
    final_score = scores.get("final_score", 0)
    threshold = config.get("alert_threshold_aggressive", 65)

    print("\n📈 Scoring:")
    print(f"final_score: {final_score}")
    print(f"alert_threshold: {threshold}")

    side = "BUY" if ema_align and macd_pos else "NONE"

    # Build log object
    record = {
        "timestamp": datetime.utcnow().isoformat(),
        "symbol": "BTC_USDT",
        "score": final_score,
        "side": side,
        "tags": features["tags"],
        "ema_align": ema_align,
        "macd_pos": macd_pos,
        "vol_spike": vol_spike,
    }

    # Save it
    log_signal(record)

    # Check if alert needed
    if final_score < threshold:
        print("⚪ Below threshold → no alert.")
        return

    # Build signal message
    text = (
        "📡 Alt-Scanner Live Alert\n"
        f"Symbol: BTC_USDT\n"
        f"Score: {final_score} (min {threshold})\n"
        f"Signal: {side}\n"
        f"Tags: {', '.join(features['tags']) if features['tags'] else 'None'}\n"
        f"\nRaw: {json.dumps(record)}"
    )

    send_telegram(text)


if __name__ == "__main__":
    main()
