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

    with filename.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data) + "\n")

    print(f"🗂 Logged → {filename.name}")


def compute_tf_features(symbol, interval):
    """
    Fetches a timeframe and returns:
    ema_align flag and macd_pos flag
    """

    candles = fetch_klines(symbol, interval, 50)

    candles["ema20"] = ema(candles["close"], 20)
    candles["ema50"] = ema(candles["close"], 50)
    candles["macd_hist"] = macd_hist(candles["close"])

    last = candles.iloc[-1]

    ema_align = last["close"] > last["ema20"] > last["ema50"]
    macd_pos = last["macd_hist"] > 0

    return bool(ema_align and macd_pos)


def analyze_symbol(symbol: str, config: dict):
    print(f"\n================= {symbol} =================")

    try:
        candles = fetch_klines(symbol, "5m", 50)

        candles["ema20"] = ema(candles["close"], 20)
        candles["ema50"] = ema(candles["close"], 50)
        candles["macd_hist"] = macd_hist(candles["close"])
        candles["vol_sma20"] = candles["volume"].rolling(20, min_periods=1).mean()

        last = candles.iloc[-1]
        last_vol_sma20 = candles["vol_sma20"].iloc[-1]

        ema_align = last["close"] > last["ema20"] > last["ema50"]
        macd_pos = last["macd_hist"] > 0
        vol_spike = last["volume"] > last_vol_sma20 * 1.5

    except Exception as e:
        print(f"❌ Fetch error for {symbol}: {e}")
        return

    # ---- NEW MULTI-TF LOGIC ----
    tf15_align = compute_tf_features(symbol, "15m")
    tf1h_align = compute_tf_features(symbol, "1h")

    print(f"🔍 5m: {ema_align}, {macd_pos}, {vol_spike}")
    print(f"📌 15m confirm: {tf15_align}")
    print(f"📌 1h confirm: {tf1h_align}")

    # Build features
    features = {
        "ema_align": bool(ema_align),
        "macd_pos": bool(macd_pos),
        "vol_spike": bool(vol_spike),

        # new multi timeframe confirmations
        "mtf15": bool(tf15_align),
        "mtf1h": bool(tf1h_align),

        "tags": [
            tag for tag, v in {
                "EMA": ema_align,
                "MACD": macd_pos,
                "VOL": vol_spike,
                "TF15": tf15_align,
                "TF1H": tf1h_align,
            }.items() if v
        ]
    }

    base_score = 0
    if ema_align: base_score += 10
    if macd_pos: base_score += 10
    if vol_spike: base_score += 5

    if tf15_align: base_score += 10
    if tf1h_align: base_score += 20

    final_score = base_score
    threshold = config.get("alert_threshold_aggressive", 65)

    print(f"📈 Final score: {final_score} / threshold {threshold}")

    side = "BUY" if ema_align and macd_pos else "NONE"

    record = {
        "timestamp": datetime.utcnow().isoformat(),
        "symbol": symbol,
        "final_score": final_score,
        "side": side,
        "tags": features["tags"],
    }

    log_signal(record)

    if final_score < threshold:
        print(f"⚪ {symbol} below threshold — no alert.")
        return

    text = (
        f"📡 Alert: {symbol}\n"
        f"Score: {final_score}\n"
        f"Side: {side}\n"
        f"Tags: {', '.join(features['tags'])}"
    )

    send_telegram(text)


def main():
    print("🚀 Alt-Scanner Start")

    config = load_config()

    symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]

    for symbol in symbols:
        analyze_symbol(symbol, config)


if __name__ == "__main__":
    main()
