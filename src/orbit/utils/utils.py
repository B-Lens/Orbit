import os
import numpy as np
import requests
from scipy.signal import argrelextrema
import datetime
import pytz
import pandas as pd
import tempfile
import mplfinance as mpf
from zoneinfo import ZoneInfo

import logging
logger = logging.getLogger("Orbit")

IST = ZoneInfo("Asia/Kolkata")

def require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise RuntimeError(f"{name} environment variable is not set")
    return value

def to_ist(dt: datetime) -> datetime:
    return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)

def get_indian_time() -> datetime.datetime:
    utc_now = datetime.datetime.utcnow()
    india_timezone = pytz.timezone("Asia/Kolkata")
    india_time = utc_now.replace(tzinfo=pytz.utc).astimezone(india_timezone)
    return india_time

def get_symbol_price(symbol: str):
    url = "https://api.binance.com/api/v3/ticker/price"
    params = {"symbol": symbol}

    response = requests.get(url, params=params, timeout=5)
    response.raise_for_status()

    data = response.json()
    return float(data["price"])


def generate_chart(df, support=None, resistance=None):
    """
    Save a temporary trade chart with support and resistance levels.

    Args:
        df (pd.DataFrame): OHLCV DataFrame with datetime index.
        support (float): Support level.
        resistance (float): Resistance level.

    Returns:
        save_path (str): Path to the saved chart image.
    """
    hlines = []
    hline_colors = []
    chart_size=(16, 10)
    if support is not None:
        hlines.append(support)
        hline_colors.append('orange')

    if resistance is not None:
        hlines.append(resistance)
        hline_colors.append('purple')

    # Create temporary file for the chart
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
    save_path = tmp_file.name
    tmp_file.close()
    
    plot_args = dict(
        type='candle',
        style='charles',
        title="Trade Chart",
        ylabel='Price',
        volume=True,
        returnfig=True,
        figsize=chart_size
    )

    # Only add hlines if present
    if hlines:
        plot_args['hlines'] = dict(hlines=hlines, colors=hline_colors, linewidths=1)

    # Call mplfinance plot with appropriate args
    fig, axes = mpf.plot(df, **plot_args)

    fig.savefig(save_path)
    print(f"Chart saved at: {save_path}")

    return save_path

def oc(df, index):
    return df['open'].iloc[index], df['close'].iloc[index]

