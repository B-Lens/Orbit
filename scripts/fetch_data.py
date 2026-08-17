import os
import time
import requests
import pandas as pd
from datetime import datetime

def fetch_binance_klines(symbol="ETHUSDT", interval="15m", total_candles=6000):
    url = "https://api.binance.com/api/v3/klines"
    all_rows = []
    end_time = None
    
    print(f"Fetching {total_candles} candles for {symbol} ({interval})...")
    
    while len(all_rows) < total_candles:
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": 1000
        }
        if end_time is not None:
            params["endTime"] = end_time
            
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            print(f"Error fetching data: {r.status_code} {r.text}")
            break
            
        data = r.json()
        if not data or len(data) == 0:
            break
            
        all_rows = data + all_rows
        # oldest timestamp in this batch
        oldest_open_time = data[0][0]
        end_time = oldest_open_time - 1
        print(f"Fetched batch of {len(data)}, total so far: {len(all_rows)}, oldest timestamp: {datetime.utcfromtimestamp(oldest_open_time/1000)}")
        time.sleep(0.2)
        
    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"
    ])
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time")
    
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
        
    df.index = pd.to_datetime(df["open_time"], unit="ms")
    df = df[["open", "high", "low", "close", "volume"]]
    df.index.name = "timestamp"
    return df

if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    
    # 15m dataset (~6000 candles = ~62 days of 15m data)
    df_15m = fetch_binance_klines(symbol="ETHUSDT", interval="15m", total_candles=6000)
    df_15m.to_csv("data/ETHUSDT_15m.csv")
    print(f"Saved {len(df_15m)} 15m candles from {df_15m.index[0]} to {df_15m.index[-1]} to data/ETHUSDT_15m.csv")
    
    # 1h dataset (~4000 candles = ~166 days of 1h data)
    df_1h = fetch_binance_klines(symbol="ETHUSDT", interval="1h", total_candles=4000)
    df_1h.to_csv("data/ETHUSDT_1h.csv")
    print(f"Saved {len(df_1h)} 1h candles from {df_1h.index[0]} to {df_1h.index[-1]} to data/ETHUSDT_1h.csv")
