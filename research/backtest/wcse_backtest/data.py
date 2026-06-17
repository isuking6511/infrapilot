"""OHLCV fetcher with parquet cache.

Uses ccxt to pull spot OHLCV from the configured exchange, then caches
the result to data_cache/{exchange}_{symbol}_{tf}.parquet so subsequent
runs skip the network call.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

import ccxt
import pandas as pd

TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "12h": 43_200_000, "1d": 86_400_000, "1w": 604_800_000,
}


def _cache_path(cache_dir: str, exchange: str, symbol: str, tf: str) -> Path:
    safe_sym = symbol.replace("/", "")
    return Path(cache_dir) / f"{exchange}_{safe_sym}_{tf}.parquet"


def _make_exchange(exchange: str):
    klass = getattr(ccxt, exchange)
    ex = klass({"enableRateLimit": True})
    ex.load_markets()
    return ex


def fetch_ohlcv(
    exchange: str,
    symbol: str,
    timeframe: str,
    since_iso: str,
    cache_dir: str = "data_cache",
    refresh: bool = False,
) -> pd.DataFrame:
    """Fetch OHLCV and cache to parquet. Returns DataFrame indexed by UTC datetime."""
    cache = _cache_path(cache_dir, exchange, symbol, timeframe)
    cache.parent.mkdir(parents=True, exist_ok=True)

    if cache.exists() and not refresh:
        df = pd.read_parquet(cache)
        return df

    ex = _make_exchange(exchange)
    tf_ms = TF_MS[timeframe]
    since_ms = int(pd.Timestamp(since_iso).timestamp() * 1000)
    now_ms = int(time.time() * 1000)

    all_rows: list[list] = []
    cursor = since_ms
    limit = 1000

    while cursor < now_ms:
        try:
            batch = ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=limit)
        except ccxt.NetworkError as e:
            print(f"  network err, retry in 2s: {e}")
            time.sleep(2)
            continue
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        if last_ts <= cursor:
            break
        cursor = last_ts + tf_ms
        # gentle pacing
        time.sleep(ex.rateLimit / 1000.0)

    if not all_rows:
        raise RuntimeError(f"No OHLCV returned for {symbol} {timeframe}")

    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset="ts").sort_values("ts").reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("datetime")[["open", "high", "low", "close", "volume"]]
    df = df.astype("float64")
    df.to_parquet(cache)
    return df


if __name__ == "__main__":
    # quick smoke test
    df = fetch_ohlcv("binance", "BTC/USDT", "1d", "2022-01-01T00:00:00Z")
    print(df.head())
    print(df.tail())
    print(f"rows: {len(df)}")
