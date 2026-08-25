#!/usr/bin/env python3
"""Download BTCUSDT 1h historical OHLCV data from Binance public API.

Usage:
    python scripts/download_btc_data.py [--start 2021-01-01] [--end 2026-08-25]
"""

import argparse
import os
import time

import pandas as pd
import requests

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
SYMBOL = "BTCUSDT"
INTERVAL = "1h"
LIMIT = 1000  # Binance max per request


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Fetch klines from Binance in paginated batches."""
    all_rows: list[list] = []
    current_start = start_ms

    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": LIMIT,
        }
        resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break

        all_rows.extend(data)
        # Next batch starts after the last candle's open time
        current_start = int(data[-1][0]) + 1
        time.sleep(0.2)  # Rate limit courtesy

    if not all_rows:
        raise RuntimeError("No data returned from Binance API")

    df = pd.DataFrame(
        all_rows,
        columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df.set_index("timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    df.sort_index(inplace=True)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Download BTCUSDT OHLCV data")
    parser.add_argument("--start", default="2021-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-08-25", help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    start_ms = int(pd.Timestamp(args.start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(args.end).timestamp() * 1000)

    print(f"Downloading {SYMBOL} {INTERVAL} data from {args.start} to {args.end}...")
    df = fetch_klines(SYMBOL, INTERVAL, start_ms, end_ms)
    print(f"Downloaded {len(df)} candles from {df.index[0]} to {df.index[-1]}")

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(project_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    out_path = os.path.join(data_dir, f"{SYMBOL}_1h.csv")
    df.to_csv(out_path)
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
