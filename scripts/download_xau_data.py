import yfinance as yf
import pandas as pd
import os

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data_dir = os.path.join(project_root, 'data')
os.makedirs(data_dir, exist_ok=True)

print("Downloading XAUUSD 15m data...")
# yfinance provides 15m data for up to 60 days
# We use XAUUSD=X for Gold vs USD
data = yf.download("XAUUSD=X", interval="15m", period="60d")

if data.empty:
    print("Data is empty. Trying GC=F")
    data = yf.download("GC=F", interval="15m", period="60d")

# Check multi-index
if isinstance(data.columns, pd.MultiIndex):
    data.columns = data.columns.droplevel(1)

data.reset_index(inplace=True)
# yfinance uses 'Datetime' for intraday
if 'Datetime' in data.columns:
    data.rename(columns={'Datetime': 'timestamp'}, inplace=True)
elif 'Date' in data.columns:
    data.rename(columns={'Date': 'timestamp'}, inplace=True)

# Lowercase column names for Orbit compatibility
data.columns = [col.lower() for col in data.columns]

# Ensure we have the needed columns
cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
for c in cols:
    if c not in data.columns:
        print(f"Missing column: {c}")

data = data[cols]

output_path = os.path.join(data_dir, "XAUUSDT_15m.csv")
data.to_csv(output_path, index=False)
print(f"Data saved to {output_path} with {len(data)} rows.")
