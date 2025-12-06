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

MAX_SYMBOLS_SCAN = CONFIG.get("max_symbols_scan", 200)
MIN_VOLUME_USD = CONFIG.get("min_volume_usd", 0)

# timeframes (prefer top-level keys already in config.yaml)
TF_PRIMARY = CONFIG.get("tf_primary", "5m")
TF_CONFIRM = CONFIG.get("tf_confirm", "15m")
SWING_TFS = CONFIG.get("swing_timeframes", ["30m", "1h"])

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
        "swing_confirm": bool(data.get("swing_confirm", False)),
        "entry": float(data.get("entry", 0)) if data.get("entry") is not None else None,
        "sl": float(data.get("sl", 0)) if data.get("sl") is not None else None,
        "tp1": float(data.get("tp1", 0)) if data.get("tp1") is not None else None,
        "tp2": float(data.get("tp2", 0)) if data.get("tp2") is not None else None,
        "btc_ctx": int(data.get("btc_ctx", 0)),
        "breakout": bool(data.get("breakout", False)),
        "retest": bool(data.get("retest", False)),
        "resistance": float(data.get("resistance", 0)) if data.get("resistance") is not None else None,
        "support": float(data.get("support", 0)) if data.get("support") is not None else None,
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

def compute_swing_confirm(symbol: str) -> bool:
    """
    Swing confirmation: require uptrend + positive MACD on all swing_timeframes
    (e.g. 30m and 1h).
    """
    try:
        for tf in SWING_TFS:
            candles = fetch_klines(symbol, tf, 50)
            candles["ema20"] = ema(candles["close"], 20)
            candles["ema50"] = ema(candles["close"], 50)
            candles["macd_hist"] = macd_hist(candles["close"])
            last = candles.iloc[-1]
            ema_align_tf = last["close"] > last["ema20"] > last["ema50"]
            macd_pos_tf = last["macd_hist"] > 0
            if not (ema_align_tf and macd_pos_tf):
                return False
        return True
    except Exception as e:
        print(f"⚠ swing confirm failed for {symbol}: {e}")
        return False


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
        last_low = float(last["low"])
        last_high = float(last["high"])
        

    # --- simple support / resistance & breakout / bounce / support-retest / rejection detection ---
    window = 20

        # resistance from recent highs (ignore the current candle)
        if len(candles) > window + 1:
            recent_highs = candles["high"].iloc[-(window + 1):-1]
            resistance = float(recent_highs.max())
        else:
            resistance = float(candles["high"].max())

        # support from recent lows (ignore the current candle)
        if len(candles) > window + 1:
            recent_lows = candles["low"].iloc[-(window + 1):-1]
            support = float(recent_lows.min())
        else:
            support = float(candles["low"].min())

        min_move_pct = CONFIG.get("min_move_pct", 0.5)

        # fresh breakout above resistance
        breakout = last_close > resistance * (1 + min_move_pct / 100.0)

        # bounce from support in an uptrend zone (dip near support, close back above)
        support_zone_low = support
        support_zone_high = support + last_atr * 0.5
        bounce_from_support = (
            last_low <= support_zone_high
            and last_close > support
            and not breakout
        )

        # validated support retest: earlier bounce + current dip holds support again
        try:
            recent = candles.iloc[-6:-1]  # last 5 candles before current
        except Exception:
            recent = candles.iloc[:-1]

        recent_bounce_mask = (recent["low"] <= support_zone_high) & (recent["close"] > support)
        had_recent_bounce = bool(recent_bounce_mask.any())
        prev_candle = candles.iloc[-2]

        support_retest = (
            had_recent_bounce
            and last_low <= support_zone_high
            and last_close > support
            and last_close >= float(prev_candle["close"])
            and not breakout
        )

        # fall / rejection from resistance (wick into resistance zone, close back below)
        resistance_zone_low = resistance - last_atr * 0.5
        resistance_zone_high = resistance + last_atr * 0.5
        fall_from_resistance = (
            last_high >= resistance_zone_low
            and last_close < resistance
            and not breakout
        )

        # simple retest: price dipped near/through resistance and closed back above
        tol_atr_mult = 0.5
        retest_zone_low = resistance - last_atr * tol_atr_mult
        retest_zone_high = resistance + last_atr * tol_atr_mult
        retest = (
            last_low <= retest_zone_high
            and last_close >= resistance
            and not breakout  # treat as post-break pullback, not fresh breakout
        )

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

    # swing confirmation (30m + 1h)
    swing_confirm = compute_swing_confirm(symbol)

    print(
        f"🔍 {TF_PRIMARY} → ema_align={ema_align}, macd_pos={macd_pos}, "
        f"ema_down={ema_down}, macd_neg={macd_neg}, vol_spike={vol_spike}, "
        f"breakout={breakout}, retest={retest}, bounce_support={bounce_from_support}, "
        f"support_retest={support_retest}, fall_resistance={fall_from_resistance}"
    )

    print(f"📌 {TF_CONFIRM} confirm (bullish): {tf15_confirm}")
    print(f"📌 Swing confirm (30m+1h): {swing_confirm}")
    print(f"📌 resistance: {resistance:.4f}, support: {support:.4f}")

    # BTC context adjustment
    btc_ctx = compute_btc_context()
    print(f"📌 BTC context adjustment: {btc_ctx}")

    # Determine side
    if ema_align and macd_pos:
        side = "BUY"
    elif ema_down and macd_neg:
        side = "SELL"
    else:
        side = "NONE"

    # Features for scoring
    features_for_scoring = {
        "ema_align": bool(ema_align),
        "macd_pos": bool(macd_pos),
        "vol_spike": bool(vol_spike),
        "mtf_ema_align": bool(tf15_confirm),
        "swing_confirm": bool(swing_confirm),
        "breakout": bool(breakout),
        "retest": bool(retest),
        "bounce_support": bool(bounce_from_support),
        "support_retest": bool(support_retest),
        "fall_resistance": bool(fall_from_resistance),
        "ctx_adj": btc_ctx,
        "tags": [
            tag
            for tag, v in {
                "EMA": ema_align,
                "MACD": macd_pos,
                "VOL": vol_spike,
                "TF15": tf15_confirm,
                "BO": breakout,
                "RT": retest,
                "BSUP": bounce_from_support,
                "SRT": support_retest,
                "FRES": fall_from_resistance,
            }.items()
            if v
        ],
    }

    # Config-driven scoring
    try:
        scores = score_signal(features_for_scoring, config)
        base_score = int(scores.get("base_score", 0))
        final_score = int(scores.get("final_score", base_score))
    except Exception as e:
        print(f"⚠ score_signal failed, using 0 scores: {e}")
        base_score = 0
        final_score = 0

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

    # BUILD LEVELS depending on side using dynamic ATR TP multipliers from scanner.atr
    atr_cfg = SCANNER_CFG.get("atr", {})
    tp_multipliers = atr_cfg.get("tp_multipliers", [2.0, 3.0, 4.5, 6.0])
    sl_multiplier = atr_cfg.get("sl_multiplier", ATR_SL_MULT)

    entry = last_close
    if side == "BUY":
        sl = entry - last_atr * sl_multiplier
        tps = [entry + last_atr * m for m in tp_multipliers]
    elif side == "SELL":
        sl = entry + last_atr * sl_multiplier
        tps = [entry - last_atr * m for m in tp_multipliers]
    else:
        sl = None
        tps = []

    # backward compatibility for tp1 / tp2
    tp1 = tps[0] if len(tps) > 0 else None
    tp2 = tps[1] if len(tps) > 1 else None

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
        "tps": tps,
        "btc_ctx": btc_ctx,
        "breakout": breakout,
        "retest": retest,
        "bounce_support": bounce_from_support,
        "support_retest": support_retest,
        "fall_resistance": fall_from_resistance,
        "resistance": resistance,
        "support": support,
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
        )
        for i, level in enumerate(tps, start=1):
            levels_str += f"TP{i}: {level:.4f}\n"

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

    symbols_to_scan = SYMBOLS[:MAX_SYMBOLS_SCAN]
    for symbol in symbols_to_scan:
        analyze_symbol(symbol, config)


if __name__ == "__main__":
    main()
