import requests
import pandas as pd
import os

def download_xau_data():
    print("Downloading XAUUSDT 15m data from Binance Futures...")
    url = 'https://fapi.binance.com/fapi/v1/klines'
    
    # Get max limit (1500)
    params = {
        'symbol': 'XAUUSDT',
        'interval': '15m',
        'limit': 1500
    }
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    
    df = pd.DataFrame(data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    
    # Format according to backtester expectations
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(os.path.join(project_root, 'data'), exist_ok=True)
    output_path = os.path.join(project_root, 'data', 'XAUUSDT_15m.csv')
    df.to_csv(output_path, index=False)
    print(f"Data saved to {output_path} with {len(df)} rows.")

if __name__ == '__main__':
    download_xau_data()
