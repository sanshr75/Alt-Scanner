# src/scanner.py

import os
import json
from pathlib import Path

import requests
import yaml

from src.indicators import ema, rsi, atr, macd_hist  # (not fully used yet)
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
        print("⚠ Telegram variables missing. Message printed instead:")
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
    # Fetch live candles from MEXC
    # ===============================
    try:
        btc_5m_candles = fetch_klines(symbol="BTC_USDT", interval="5m", limit=50)

        # EMA calculations
        btc_5m_candles["ema20"] = ema(btc_5m_candles["close"], 20)
        btc_5m_candles["ema50"] = ema(btc_5m_candles["close"], 50)

        # RSI(14)
        btc_5m_candles["rsi14"] = rsi(btc_5m_candles["close"], 14)

        # ATR(14)
        btc_5m_candles["atr14"] = atr(btc_5m_candles, 14)

        # MACD histogram
        btc_5m_candles["macd_hist"] = macd_hist(btc_5m_candles["close"])

        print("\n📊 BTC_USDT latest 5m candles with EMA, RSI, ATR, MACD Histogram:")
        print(
            btc_5m_candles[
                ["timestamp", "close", "ema20", "ema50", "rsi14", "atr14", "macd_hist"]
            ]
            .tail()
            .to_string(index=False)
        )

    except Exception as e:
        print(f"❌ MEXC fetch failed: {e}")
        return


    # ===============================
    # Dummy signal (placeholder)
    # ===============================
    features = {
        "ema_align": True,
        "macd_pos": True,
        "vol_spike": True,
        "mtf_ema_align": True,
        "ctx_adj": 0,
        "tags": ["TEST_SIGNAL", "DUMMY_DATA"],
    }

    scores = score_signal(features, config)

    fake_payload = {
        "id": "TEST|BUY|000000",
        "symbol": "TESTUSDT",
        "exchange": "MEXC",
        "side": "BUY",
        "tf": config.get("tf_primary", "5m"),
        "entry": 1.2345,
        "sl": 1.1111,
        "tp1": 1.3333,
        "tp2": 1.4444,
        "final_score": scores["final_score"],
        "base_score": scores["base_score"],
        "mtf_score": scores["mtf_score"],
        "ctx_adj": scores["ctx_adj"],
        "tags": scores["tags"],
    }

    text = (
        "🧪 Alt-Scanner test signal\n"
        f"Symbol: {fake_payload['symbol']} | Side: {fake_payload['side']}\n"
        f"TF: {fake_payload['tf']}\n"
        f"Entry: {fake_payload['entry']} | SL: {fake_payload['sl']} | "
        f"TP1: {fake_payload['tp1']} | TP2: {fake_payload['tp2']}\n"
        f"Score: {fake_payload['final_score']} "
        f"(base={fake_payload['base_score']}, mtf={fake_payload['mtf_score']}, ctx={fake_payload['ctx_adj']})\n"
        f"Tags: {', '.join(fake_payload['tags'])}\n\n"
        f"raw: {json.dumps(fake_payload)}"
    )

    send_telegram(text)


if __name__ == "__main__":
    main()
