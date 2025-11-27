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

import yaml
from pathlib import Path

# --- config loading ---
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"

with open(CONFIG_PATH, "r") as f:
    CONFIG = yaml.safe_load(f) or {}

SCANNER_CFG = CONFIG.get("scanner", {})

SYMBOLS = SCANNER_CFG.get(
    "symbols",
    ["BTC_USDT", "ETH_USDT", "SOL_USDT"]  # fallback if config missing
)

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

    safe = {
        "timestamp": str(data.get("timestamp")),
        "symbol": str(data.get("symbol")),
        "final_score": int(data.get("final_score", 0)),
        "base_score": int(data.get("base_score", 0)),
        "side": str(data.get("side", "NONE")),
        "tags": [str(t) for t in data.get("tags", [])],
        "ema_align": bool(data.get("ema_align", False)),
        "macd_pos": bool(data.get("macd_pos", False)),
        "vol_spike": bool(data.get("vol_spike", False)),
        "tf15_confirm": bool(data.get("tf15_confirm", False)),
        "entry": float(data.get("entry", 0)) if data.get("entry") is not None else None,
        "sl": float(data.get("sl", 0)) if data.get("sl") is not None else None,
        "tp1": float(data.get("tp1", 0)) if data.get("tp1") is not None else None,
        "tp2": float(data.get("tp2", 0)) if data.get("tp2") is not None else None,
    }

    with filename.open("a", encoding="utf-8") as f:
        f.write(json.dumps(safe) + "\n")

    print(f"🗂 Logged → {filename.name}")


def compute_tf15_confirm(symbol: str) -> bool:
    """
    Fetch 15m timeframe and return a simple trend-confirm flag.
    close > ema20 > ema50 AND macd_hist > 0
    """
    candles = fetch_klines(symbol, "15m", 50)

    candles["ema20"] = ema(candles["close"], 20)
    candles["ema50"] = ema(candles["close"], 50)
    candles["macd_hist"] = macd_hist(candles["close"])

    last = candles.iloc[-1]

    ema_align_15 = last["close"] > last["ema20"] > last["ema50"]
    macd_pos_15 = last["macd_hist"] > 0

    return bool(ema_align_15 and macd_pos_15)


def analyze_symbol(symbol: str, config: dict):
    print(f"\n================= {symbol} =================")

    try:
        candles = fetch_klines(symbol, "5m", 50)

        candles["ema20"] = ema(candles["close"], 20)
        candles["ema50"] = ema(candles["close"], 50)
        candles["macd_hist"] = macd_hist(candles["close"])
        candles["atr14"] = atr(candles, 14)
        candles["vol_sma20"] = candles["volume"].rolling(20, min_periods=1).mean()

        last = candles.iloc[-1]
        last_vol_sma20 = candles["vol_sma20"].iloc[-1]
        last_atr = float(candles["atr14"].iloc[-1])
        last_close = float(last["close"])

        # Bullish side conditions (5m)
        ema_align = last["close"] > last["ema20"] > last["ema50"]
        macd_pos = last["macd_hist"] > 0

        # Bearish side conditions (5m)
        ema_down = last["close"] < last["ema20"] < last["ema50"]
        macd_neg = last["macd_hist"] < 0

        vol_spike = last["volume"] > last_vol_sma20 * 1.5

    except Exception as e:
        print(f"❌ Fetch error for {symbol}: {e}")
        return

    # 15m confirmation (bullish only for now)
    try:
        tf15_confirm = compute_tf15_confirm(symbol)
    except Exception as e:
        print(f"⚠ 15m confirm failed for {symbol}: {e}")
        tf15_confirm = False

    print(
        f"🔍 5m → ema_align={ema_align}, macd_pos={macd_pos}, "
        f"ema_down={ema_down}, macd_neg={macd_neg}, vol_spike={vol_spike}"
    )
    print(f"📌 15m confirm (bullish): {tf15_confirm}")

    # Determine side
    if ema_align and macd_pos:
        side = "BUY"
    elif ema_down and macd_neg:
        side = "SELL"
    else:
        side = "NONE"

    # -------- Manual base_score (still bullish-weighted for now) --------
    base_score = 0
    if ema_align:
        base_score += 10
    if macd_pos:
        base_score += 10
    if vol_spike:
        base_score += 5
    if tf15_confirm:
        base_score += 15

    # -------- Features for central scoring engine --------
    features_for_scoring = {
        "ema_align": bool(ema_align),
        "macd_pos": bool(macd_pos),
        "vol_spike": bool(vol_spike),
        "mtf_ema_align": bool(tf15_confirm),
        "ctx_adj": 0,
        "tags": [
            tag
            for tag, v in {
                "EMA": ema_align,
                "MACD": macd_pos,
                "VOL": vol_spike,
                "TF15": tf15_confirm,
            }.items()
            if v
        ],
    }

    try:
        scores = score_signal(features_for_scoring, config)
        final_score = int(scores.get("final_score", base_score))
    except Exception as e:
        print(f"⚠ score_signal failed, using base_score only: {e}")
        final_score = base_score

    threshold = config.get("alert_threshold_aggressive", 65)

    print(f"📈 Base score: {base_score}")
    print(f"📈 Final score (after scoring.py): {final_score} / threshold {threshold}")
    print(f"📌 Decided side: {side}")

    # Default levels = None
    entry = None
    sl = None
    tp1 = None
    tp2 = None

    # BUILD LEVELS depending on side
    if side == "BUY":
        atr_mult_sl = 1.5
        atr_mult_tp1 = 2.0
        atr_mult_tp2 = 3.0

        entry = last_close
        sl = entry - last_atr * atr_mult_sl
        tp1 = entry + last_atr * atr_mult_tp1
        tp2 = entry + last_atr * atr_mult_tp2

    elif side == "SELL":
        atr_mult_sl = 1.5
        atr_mult_tp1 = 2.0
        atr_mult_tp2 = 3.0

        entry = last_close
        sl = entry + last_atr * atr_mult_sl
        tp1 = entry - last_atr * atr_mult_tp1
        tp2 = entry - last_atr * atr_mult_tp2

    record = {
        "timestamp": datetime.utcnow().isoformat(),
        "symbol": symbol,
        "final_score": final_score,
        "base_score": base_score,
        "side": side,
        "tags": features_for_scoring["tags"],
        "ema_align": ema_align,
        "macd_pos": macd_pos,
        "vol_spike": vol_spike,
        "tf15_confirm": tf15_confirm,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
    }

    log_signal(record)

    if final_score < threshold or side == "NONE":
        print(f"⚪ {symbol} below threshold or no direction — no alert.")
        return

    # Alert message with direction
    levels_str = ""
    if entry is not None:
        levels_str = (
            f"Entry: {entry:.4f}\n"
            f"SL: {sl:.4f}\n"
            f"TP1: {tp1:.4f}\n"
            f"TP2: {tp2:.4f}\n"
        )

    text = (
        f"📡 Alert: {symbol} (5m)\n"
        f"Side: {side}\n"
        f"Score: {final_score} / {threshold}\n"
        f"{levels_str}"
        f"Tags: {', '.join(features_for_scoring['tags']) if features_for_scoring['tags'] else 'None'}"
    )

    send_telegram(text)


def main():
    print("🚀 Alt-Scanner Start")

    config = load_config()

for symbol in SYMBOLS:
    analyze_symbol(symbol, config)


if __name__ == "__main__":
    main()
