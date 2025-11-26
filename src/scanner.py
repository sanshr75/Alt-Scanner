# src/scanner.py

import os
import json
from pathlib import Path

import requests
import yaml
import pandas as pd

from src.indicators import ema, rsi, atr, macd_hist
from src.scoring import score_signal
from .mexc_client import fetch_klines


BASE_DIR = Path(__file__).resolve().parent.parent


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


def main():

    print("🚀 Alt-Scanner starting...")

    config = load_config()
    print("📄 Config loaded.")

    # ===============================
    # Fetch candles from MEXC
    # ===============================
    try:
        btc_5m_candles = fetch_klines("BTC_USDT", "5m", 50)

        # Indicators
        btc_5m_candles["ema20"] = ema(btc_5m_candles["close"], 20)
        btc_5m_candles["ema50"] = ema(btc_5m_candles["close"], 50)
        btc_5m_candles["rsi14"] = rsi(btc_5m_candles["close"], 14)
        btc_5m_candles["atr14"] = atr(btc_5m_candles, 14)
        btc_5m_candles["macd_hist"] = macd_hist(btc_5m_candles["close"])

        # Volume SMA for spike detection
        btc_5m_candles["vol_sma20"] = btc_5m_candles["volume"].rolling(20, min_periods=1).mean()

        print("\n📊 BTC_USDT last 5 rows:")
        print(
            btc_5m_candles[
                ["timestamp", "close", "ema20", "ema50", "rsi14", "atr14", "macd_hist"]
            ].tail().to_string(index=False)
        )

        # ===============================
        # Feature flags (last candle only)
        # ===============================
        last = btc_5m_candles.iloc[-1]
        last_vol_sma20 = btc_5m_candles["vol_sma20"].iloc[-1]

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
    # Build REAL feature payload
    # ===============================
    features = {
        "ema_align": bool(ema_align),
        "macd_pos": bool(macd_pos),
        "vol_spike": bool(vol_spike),

        # placeholders (future steps)
        "mtf_ema_align": False,
        "ctx_adj": 0,

        "tags": [
            tag for tag, val in {
                "EMA_TREND": ema_align,
                "MACD_MOMENTUM": macd_pos,
                "VOLUME_SPIKE": vol_spike,
            }.items() if val is True
        ]
    }

    # ===============================
    # Score the setup
    # ===============================
    scores = score_signal(features, config)

    # ===============================
    # Build test formatted message
    # ===============================
    fake_payload = {
        "id": "LIVE|TEST|BTC_USDT",
        "symbol": "BTC_USDT",
        "exchange": "MEXC",
        "tf": "5m",
        "side": "BUY" if ema_align and macd_pos else "NONE",
        "final_score": scores.get("final_score", 0),
        "tags": features["tags"],
    }

    text = (
        "📡 Alt-Scanner Live Check\n"
        f"Symbol: {fake_payload['symbol']}\n"
        f"Timeframe: {fake_payload['tf']}\n"
        f"Score: {fake_payload['final_score']}\n"
        f"Signal: {fake_payload['side']}\n"
        f"Tags: {', '.join(fake_payload['tags']) if fake_payload['tags'] else 'None'}\n"
        f"\nRaw: {json.dumps(fake_payload)}"
    )

    send_telegram(text)


if __name__ == "__main__":
    main()
