# src/scanner.py
"""
First minimal scanner step:
- Load config.yaml
- Build a fake "signal" using the scoring module
- Send it to Telegram using the same bot you already wired
This is just to prove the pipeline: config -> scoring -> telegram works.
"""

import os
import json
from pathlib import Path

import requests
import yaml

from src.indicators import ema, rsi, atr, macd_hist  # not used yet, but we'll need them soon
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
        print("Telegram env vars missing, cannot send message.")
        print(text)
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    resp = requests.post(url, json=payload, timeout=15)
    print("Telegram status:", resp.status_code, resp.text)


def main():
    def main():
    # Existing code: config loading, etc.
    # ------------------------------------------------
    # For example:
    # config = load_config()
    # ...

    # --- New: Fetch and inspect BTC_USDT 5m candles from MEXC ---
    try:
        btc_5m_candles = fetch_klines(symbol="BTC_USDT", interval="5m", limit=50)
        print("[MEXC] Fetched BTC_USDT 5m candles:")
        # Print just the last few rows to keep CI logs readable
        print(btc_5m_candles.tail().to_string(index=False))
    except Exception as e:
        # Do not kill the whole pipeline yet; just log the problem.
        print(f"[MEXC] Error fetching BTC_USDT klines: {e}")

    # ------------------------------------------------
    # Existing dummy signal / scoring / Telegram logic continues here
    # (do NOT remove it yet; we will gradually replace it in later steps)
    # ------------------------------------------------
    # ... rest of your existing main() ...

    config = load_config()

    # --- fake features just to test scoring + telegram wiring ---
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

    # compact text message for now
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
