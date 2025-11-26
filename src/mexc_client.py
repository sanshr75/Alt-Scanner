# src/mexc_client.py

import requests
import pandas as pd
from typing import Optional

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

    Standardized columns:
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

    if not raw:
        raise ValueError("No kline data returned from MEXC")

    # MEXC is returning a list of lists.
    # Each inner list has (at least) the first 6 items in this order:
    # 0: openTime (ms)
    # 1: open
    # 2: high
    # 3: low
    # 4: close
    # 5: volume
    #
    # There may be more items after that, but we don't need them now.
    df = pd.DataFrame(raw)

    # Convert types using positional columns
    df[0] = pd.to_datetime(df[0], unit="ms")  # openTime
    df[1] = df[1].astype(float)  # open
    df[2] = df[2].astype(float)  # high
    df[3] = df[3].astype(float)  # low
    df[4] = df[4].astype(float)  # close
    df[5] = df[5].astype(float)  # volume

    # Standardize column names for the rest of the project
    df = df.rename(
        columns={
            0: "timestamp",
            1: "open",
            2: "high",
            3: "low",
            4: "close",
            5: "volume",
        }
    )

    return df[["timestamp", "open", "high", "low", "close", "volume"]]
