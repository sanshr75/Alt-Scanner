import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any

import requests
import pandas as pd

# ---- CONFIG ----

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUT_DIR = BASE_DIR / "analysis"

# MEXC spot kline endpoint (adjust if you are using futures)
MEXC_KLINES_URL = "https://api.mexc.com/api/v3/klines"

# How many candles after the signal to inspect (e.g. 48 * 5m = 4 hours)
LOOKAHEAD_BARS = 48

# Timeframe of the signal (must match scanner TF_PRIMARY)
INTERVAL = "5m"


# ---- HELPERS ----

def load_alerts(file_path: Path) -> List[Dict[str, Any]]:
    alerts = []
    with file_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                alerts.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return alerts


def mexc_symbol_from_scanner(symbol: str) -> str:
    """
    Scanner uses BTC_USDT, MEXC spot klines want BTCUSDT.
    Adjust here if you work with futures symbols later.
    """
    return symbol.replace("_", "")


def fetch_future_klines(symbol: str, start_dt: datetime, bars: int) -> pd.DataFrame:
    """
    Fetch klines from MEXC starting at start_dt for a given number of bars.
    Uses /api/v3/klines. Times are in ms since epoch.
    """
    params = {
        "symbol": mexc_symbol_from_scanner(symbol),
        "interval": INTERVAL,
        "limit": bars,
        "startTime": int(start_dt.timestamp() * 1000),
    }
    resp = requests.get(MEXC_KLINES_URL, params=params, timeout=15)
    resp.raise_for_status()
    raw = resp.json()

    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume",
        "number_of_trades", "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume", "ignore",
    ]
    df = pd.DataFrame(raw, columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    return df[["open_time", "high", "low", "close"]]


def label_one_alert(alert: Dict[str, Any]) -> Dict[str, Any]:
    """
    Determine which levels were hit (SL, TP1..TPn) after the signal.
    Returns a dict with extra fields added.
    """
    ts_str = alert.get("timestamp")
    symbol = alert.get("symbol")
    side = alert.get("side")
    entry = float(alert.get("entry") or 0.0)
    sl = alert.get("sl")
    tps = alert.get("tps") or []

    out = dict(alert)  # copy
    out["hit_sl"] = False
    out["hit_tp1"] = False
    out["hit_tp2"] = False
    out["hit_tp3"] = False
    out["hit_tp4"] = False
    out["first_event"] = "NONE"  # SL / TP1 / TP2 / TP3 / TP4 / NONE
    out["max_tp_reached"] = 0
    out["rr_at_max_tp"] = 0.0

    if not ts_str or not symbol or side not in ("BUY", "SELL") or entry == 0.0 or sl is None:
        return out

    try:
        signal_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return out

    sl = float(sl)
    tp_levels = [float(tp) for tp in tps]

    # Fetch klines starting from the next bar after the signal
    start_dt = signal_time + timedelta(minutes=5)
    try:
        df = fetch_future_klines(symbol, start_dt, LOOKAHEAD_BARS)
    except Exception as e:
        out["error"] = f"kline_fetch_error: {e}"
        return out

    # Go through candles in time order, find first touch of any level
    first_event = "NONE"
    max_tp_idx = 0

    for _, row in df.iterrows():
        high = row["high"]
        low = row["low"]

        if side == "BUY":
            # SL hit if low <= sl
            sl_hit = low <= sl
            # TP hit if high >= tp
            tp_hits = [high >= tp for tp in tp_levels]
        else:  # SELL
            sl_hit = high >= sl
            tp_hits = [low <= tp for tp in tp_levels]

        # Determine first event in this candle
        # Assume within a candle: SL vs TP priority is decided by which side "more extreme"
        # For simplicity we check SL first, then TP1..TP4.
        if sl_hit:
            first_event = "SL"
            break

        for i, hit in enumerate(tp_hits, start=1):
            if hit:
                first_event = f"TP{i}"
                if i > max_tp_idx:
                    max_tp_idx = i
                # keep checking further candles for higher TP, but first_event is already TP1..TPn
                break

        if first_event.startswith("TP"):
            # continue scanning to see if higher TPs are reached
            continue

    # After scanning all candles, mark flags and RR
    if first_event == "SL":
        out["hit_sl"] = True
        max_tp_idx = 0
    elif first_event.startswith("TP"):
        out[f"hit_tp{first_event[2:]}"] = True

    if max_tp_idx == 0 and not out["hit_sl"]:
        out["first_event"] = "NONE"
        out["max_tp_reached"] = 0
        out["rr_at_max_tp"] = 0.0
        return out

    out["first_event"] = first_event
    out["max_tp_reached"] = max_tp_idx

    # Compute R:R at max TP
    risk = abs(entry - sl)
    if risk > 0 and max_tp_idx > 0:
        best_tp = tp_levels[max_tp_idx - 1]
        reward = abs(best_tp - entry)
        out["rr_at_max_tp"] = reward / risk
    elif risk > 0 and out["hit_sl"]:
        out["rr_at_max_tp"] = -1.0

    return out


def main():
    DATA_DIR.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)

    # Process all alerts-*.json files in data/
    json_files = sorted(DATA_DIR.glob("alerts-*.json"))
    if not json_files:
        print("No alerts-*.json files found in data/.")
        return

    all_rows = []
    for fp in json_files:
        alerts = load_alerts(fp)
        print(f"Processing {fp.name}: {len(alerts)} alerts")
        for a in alerts:
            labeled = label_one_alert(a)
            all_rows.append(labeled)

    if not all_rows:
        print("No alerts to analyse.")
        return

    df = pd.DataFrame(all_rows)
    out_csv = OUT_DIR / "alerts_analysis.csv"
    df.to_csv(out_csv, index=False)
    print(f"Written analysis to {out_csv}")


if __name__ == "__main__":
    main()
