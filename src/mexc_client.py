# src/mexc_client.py

import requests
import pandas as pd
from typing import Optional, List

BASE_URL = "https://api.mexc.com"


def _symbol_to_mexc(symbol: str) -> str:
    """
    Convert internal symbol style (e.g. 'BTC_USDT') to MEXC spot style ('BTCUSDT').
    """
    return symbol.replace("_", "").upper()


def fetch_klines(
    symbol: str,
    interval: str = "5m",
    limit: int = 200,
    timeout: int = 10,
) -> pd.DataFrame:
    """
    Fetch historical klines from MEXC Spot API and return a standardized OHLCV DataFrame.

    Columns:
        timestamp (datetime64[ns])
        open (float)
        high (float)
        low (float)
        close (float)
        volume (float)
    """
    mexc_symbol = _symbol_to_mexc(symbol)

    url = f"{BASE_URL}/api/v3/klines"
    params = {
        "symbol": mexc_symbol,
        "interval": interval,
        "limit": limit,
    }

    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    raw = response.json()

    # MEXC /api/v3/klines returns list of:
    # [
    #   [
    #     openTime, open, high, low, close, volume,
    #     closeTime, quoteAssetVolume, numberOfTrades,
    #     takerBuyBaseAssetVolume, takerBuyQuoteAssetVolume, ignore
    #   ], ...
    # ]
    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_asset_volume",
        "number_of_trades",
        "taker_buy_base",
        "taker_buy_quote",
        "ignore",
    ]

    df = pd.DataFrame(raw, columns=columns)

    # Convert types
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    # Standardized schema for the rest of the project
    df = df.rename(columns={"open_time": "timestamp"})

    return df[["timestamp", "open", "high", "low", "close", "volume"]]
