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

# --- paths ---
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = BASE_DIR / "config.yaml"

# --- load config once (global) ---
with CONFIG_PATH.open("r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f) or {}

# scanner-specific config block
SCANNER_CFG = CONFIG.get("scanner", {})

# symbols list (from config)
SYMBOLS = SCANNER_CFG.get(
    "symbols",
    ["BTC_USDT", "ETH_USDT", "SOL_USDT"]  # fallback if config missing
)

# timeframes (prefer top-level keys already in config.yaml)
TF_PRIMARY = CONFIG.get("tf_primary", "5m")
TF_CONFIRM = CONFIG.get("tf_confirm", "15m")

# volume spike multiplier (prefer scanner.volume.spike_multiplier, fallback to vol_mult)
VOLUME_CFG = SCANNER_CFG.get("volume", {})
VOL_SPIKE_MULT = VOLUME_CFG.get("spike_multiplier", CONFIG.get("vol_mult", 1.5))

# ATR multipliers for SL/TP (scanner.atr.* with fallbacks)
ATR_CFG = SCANNER_CFG.get("atr", {})
ATR_SL_MULT = ATR_CFG.get("sl_multiplier", 1.5)
ATR_TP1_MULT = ATR_CFG.get("tp1_multiplier", 2.0)
ATR_TP2_MULT = ATR_CFG.get("tp2_multiplier", 3.0)

# Alert threshold (scanner.scoring.threshold, fallback to aggressive top-level)
SCORING_CFG = SCANNER_CFG.get("scoring", {})
ALERT_THRESHOLD = SCORING_CFG.get(
    "threshold",
    CONFIG.get("alert_threshold_aggressive", 65)
)


def load_config():
    # kept for compatibility with other modules
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
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
    Fetch confirm timeframe (default 15m) and return a simple trend-confirm flag.
    close > ema20 > ema50 AND macd_hist > 0
    """
    candles = fetch_klines(symbol, TF_CONFIRM, 50)

    candles["ema20"] = ema(candles["close"], 20)
    candles["ema50"] = ema(candles["close"], 50)
    candles["macd_hist"] = macd_hist(candles["close"])

    last = candles.iloc[-1]

    ema_align_15 = last["close"] > last["ema20"] > last["ema50"]
    macd_pos_15 = last["macd_hist"] > 0

    return bool(ema_align_15 and macd_pos_15)

def compute_btc_context() -> int:
    """
    Simple BTC trend context.
    Uses EMA fast/slow from config and context weights from config.
    Returns an integer adjustment for the scoring engine.
    """
    try:
        btc_symbol = "BTC_USDT"

        candles = fetch_klines(btc_symbol, TF_CONFIRM, 50)

        ema_fast_len = CONFIG.get("ema_fast", 20)
        ema_slow_len = CONFIG.get("ema_slow", 50)

        candles["ema_fast"] = ema(candles["close"], ema_fast_len)
        candles["ema_slow"] = ema(candles["close"], ema_slow_len)

        last = candles.iloc[-1]

        btc_up = last["close"] > last["ema_fast"] > last["ema_slow"]
        btc_down = last["close"] < last["ema_fast"] < last["ema_slow"]

        ctx_cfg = CONFIG.get("context", {})

        if btc_up:
            return int(ctx_cfg.get("btc_align", 0))
        if btc_down:
            return int(ctx_cfg.get("btc_oppose", 0))

        return 0

    except Exception as e:
        print(f"⚠ BTC context failed: {e}")
        return 0


def analyze_symbol(symbol: str, config: dict):
    print(f"\n================= {symbol} =================")

    try:
        # primary timeframe from config (default 5m)
        candles = fetch_klines(symbol, TF_PRIMARY, 50)

        candles["ema20"] = ema(candles["close"], 20)
        candles["ema50"] = ema(candles["close"], 50)
        candles["macd_hist"] = macd_hist(candles["close"])
        candles["atr14"] = atr(candles, 14)
        candles["vol_sma20"] = candles["volume"].rolling(20, min_periods=1).mean()

        last = candles.iloc[-1]
        last_vol_sma20 = candles["vol_sma20"].iloc[-1]
        last_atr = float(candles["atr14"].iloc[-1])
        last_close = float(last["close"])

        # Bullish side conditions (primary tf)
        ema_align = last["close"] > last["ema20"] > last["ema50"]
        macd_pos = last["macd_hist"] > 0

        # Bearish side conditions (primary tf)
        ema_down = last["close"] < last["ema20"] < last["ema50"]
        macd_neg = last["macd_hist"] < 0

        # volume spike using config multiplier
        vol_spike = last["volume"] > last_vol_sma20 * VOL_SPIKE_MULT

    except Exception as e:
        print(f"❌ Fetch error for {symbol}: {e}")
        return

    # confirmation timeframe (bullish only for now)
    try:
        tf15_confirm = compute_tf15_confirm(symbol)
    except Exception as e:
        print(f"⚠ confirm tf failed for {symbol}: {e}")
        tf15_confirm = False

           print(
        f"🔍 {TF_PRIMARY} → ema_align={ema_align}, macd_pos={macd_pos}, "
        f"ema_down={ema_down}, macd_neg={macd_neg}, vol_spike={vol_spike}"
    )
    print(f"📌 {TF_CONFIRM} confirm (bullish): {tf15_confirm}")

    # BTC context adjustment
    btc_ctx = compute_btc_context()
    print(f"📌 BTC context adjustment: {btc_ctx}")

    # Determine side
    if ema_align and macd_pos:
        side = "BUY"
    elif ema_down and macd_neg:
        side = "SELL"
        # future: add separate bearish scoring
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
        "ctx_adj": btc_ctx,
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

    # threshold from scanner.scoring.threshold or config fallback
    threshold = ALERT_THRESHOLD

    print(f"📈 Base score: {base_score}")
    print(f"📈 Final score (after scoring.py): {final_score} / threshold {threshold}")
    print(f"📌 Decided side: {side}")

    # Default levels = None
    entry = None
    sl = None
    tp1 = None
    tp2 = None

    # BUILD LEVELS depending on side using ATR multipliers from config
    if side == "BUY":
        entry = last_close
        sl = entry - last_atr * ATR_SL_MULT
        tp1 = entry + last_atr * ATR_TP1_MULT
        tp2 = entry + last_atr * ATR_TP2_MULT

    elif side == "SELL":
        entry = last_close
        sl = entry + last_atr * ATR_SL_MULT
        tp1 = entry - last_atr * ATR_TP1_MULT
        tp2 = entry - last_atr * ATR_TP2_MULT

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
        f"📡 Alert: {symbol} ({TF_PRIMARY})\n"
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
