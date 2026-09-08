#!/usr/bin/env python3
"""Download LINKUSDT 1h candles from Binance Futures API and save as CSV."""

import time
import requests
import pandas as pd
from pathlib import Path

SYMBOL = "LINKUSDT"
INTERVAL = "1h"
LIMIT = 1500  # max per request
BASE_URL = "https://fapi.binance.com/fapi/v1/klines"

# Download ~2 years of data (Sep 2024 to Sep 2026)
# Start from 2024-09-01 00:00:00 UTC
start_ms = int(pd.Timestamp("2024-09-01", tz="UTC").timestamp() * 1000)
end_ms = int(pd.Timestamp("2026-09-07", tz="UTC").timestamp() * 1000)

all_candles = []
current_start = start_ms

while current_start < end_ms:
    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "startTime": current_start,
        "endTime": end_ms,
        "limit": LIMIT,
    }
    print(f"Fetching from {pd.Timestamp(current_start, unit='ms', tz='UTC')}...")
    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        break

    all_candles.extend(data)
    # Move start to after the last candle's open time
    current_start = data[-1][0] + 3600000  # +1 hour in ms
    time.sleep(0.5)  # rate limit

print(f"Downloaded {len(all_candles)} candles")

# Parse into DataFrame
df = pd.DataFrame(all_candles, columns=[
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades", "taker_buy_volume",
    "taker_buy_quote_volume", "ignore"
])

df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
df = df.set_index("timestamp")
df = df[["open", "high", "low", "close", "volume"]].astype(float)
df = df.sort_index()
df = df[~df.index.duplicated(keep="first")]

output_path = Path(__file__).resolve().parents[1] / "data" / f"{SYMBOL}_1h.csv"
output_path.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(output_path)
print(f"Saved {len(df)} candles to {output_path}")
print(f"Date range: {df.index[0]} to {df.index[-1]}")
