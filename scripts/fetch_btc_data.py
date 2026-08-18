import requests
import pandas as pd
from datetime import datetime, timezone
import time
import os

def fetch_historical_klines(symbol, interval, start_time_ms, end_time_ms, limit=1000):
    url = "https://api.binance.com/api/v3/klines"
    all_data = []
    
    current_start = start_time_ms
    
    while True:
        if current_start > end_time_ms:
            break
            
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_time_ms,
            "limit": limit
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if not data:
                break
                
            all_data.extend(data)
            print(f"Fetched {len(data)} candles. Total so far: {len(all_data)}")
            
            # The last candle's open time + 1 to avoid duplicates
            current_start = data[-1][0] + 1
            
            # Sleep to respect rate limits
            time.sleep(0.5)
            
        except Exception as e:
            print(f"Error fetching data: {e}")
            break
            
    return all_data

def process_and_save(data, filepath):
    columns = [
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
    ]
    df = pd.DataFrame(data, columns=columns)
    
    # We only need specific columns
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    
    # Convert types
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
        
    # Convert timestamp to datetime
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    # Save to csv
    df.to_csv(filepath)
    print(f"Data saved to {filepath}")
    
    return df

def main():
    symbol = "BTCUSDT"
    interval = "15m"
    start_date = datetime(2026, 2, 1, tzinfo=timezone.utc)
    end_date = datetime(2026, 8, 18, tzinfo=timezone.utc)
    
    start_time_ms = int(start_date.timestamp() * 1000)
    end_time_ms = int(end_date.timestamp() * 1000)
    
    out_file = "/root/agy-workspace/Orbit/data/BTCUSDT_15m.csv"
    
    print(f"Fetching {interval} data for {symbol} from {start_date} to {end_date}...")
    raw_data = fetch_historical_klines(symbol, interval, start_time_ms, end_time_ms)
    
    if raw_data:
        df = process_and_save(raw_data, out_file)
        print(f"Total rows: {len(df)}")
        print("\nFirst few rows:")
        print(df.head())
        print("\nLast few rows:")
        print(df.tail())
    else:
        print("No data fetched.")

if __name__ == "__main__":
    main()
