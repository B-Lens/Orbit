import numpy as np
from scipy.signal import argrelextrema
import datetime
import pytz
import pandas as pd
import tempfile
import mplfinance as mpf

import psutil
import os
import functools
import threading
import time
import logging
import multiprocessing
import sys
from pympler import asizeof
import psutil, os, tracemalloc

logger = logging.getLogger("Orbit")

prev_snapshot = None
process = None
def mem(tag, process=None):
    global prev_snapshot
    rss = process.memory_info().rss / (1024*1024)
    snapshot = tracemalloc.take_snapshot()

    stats = snapshot.compare_to(prev_snapshot, 'lineno')
    prev_snapshot = snapshot

    logger.warning(f"[{tag}] RSS={rss:.2f} MB")
    for s in stats[:10]:
        logger.warning(f"  {s}")

def print_memory_of_locals(local_vars, tag=""):
    logger.info(f"\n--- Memory usage snapshot {tag} ---")
    for name, value in local_vars.items():
        try:
            size = asizeof.asizeof(value)
        except Exception:
            size = sys.getsizeof(value)
        logger.info(f"{name:30s} : {size/1024/1024:.4f} MB")
    logger.info("--------------------------------------\n")

def get_self_attribute_sizes(obj):
    sizes = {}
    for attr, value in obj.__dict__.items():
        try:
            size = asizeof.asizeof(value) / (1024 * 1024)  # MB
        except Exception:
            size = 0
        sizes[attr] = size
    return sizes

def thread_memory(interval=30):
    """
    Decorator to monitor memory usage of the thread running this function.
    Logs memory every `interval` seconds with the thread name.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):

            thread_name = threading.current_thread().name

            def mem_logger():
                while True:
                    mem = process.memory_info().rss / (1024 * 1024)
                    logger.info(f"[MEM][{thread_name}] {mem:.2f} MB")
                    time.sleep(interval)

            # Start memory logger inside same thread context
            t = threading.Thread(target=mem_logger, daemon=True)
            t.start()

            return func(*args, **kwargs)

        return wrapper
    return decorator


def process_memory(interval=900):
    """
    Logs memory usage for the PROCESS running this function.
    Correctly shows multiprocessing.Process name + PID.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):

            proc = psutil.Process()
            pid = proc.pid

            # Get multiprocessing Process name if exists
            try:
                mp_process = multiprocessing.current_process()
                process_name = mp_process.name
            except Exception:
                process_name = proc.name()  # fallback

            def mem_logger():
                while True:
                    try:
                        mem = proc.memory_info().rss / (1024 * 1024)
                        logger.info(
                            f"[MEM][{process_name}][PID:{pid}] {mem:.2f} MB"
                        )
                    except Exception:
                        pass
                    time.sleep(interval)

            t = threading.Thread(target=mem_logger, daemon=True)
            t.start()

            return func(*args, **kwargs)

        return wrapper
    return decorator

def get_ohlc_df(klines: list):
    # Create a DataFrame with specific column names
    raw_df = pd.DataFrame(
        klines,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "timestamp_end",
            "quote_volume",
            "number_of_trades",
            "taker_base_volume",
            "taker_quote_volume",
            "ignore",
        ],
    )

    # Convert data types
    raw_df["timestamp"] = pd.to_datetime(
        raw_df["timestamp"], unit="ms"
    )  # Convert timestamp to datetime

    raw_df["timestamp"] = raw_df["timestamp"].dt.tz_localize("UTC")
    raw_df["timestamp"] = raw_df["timestamp"].dt.tz_convert("Asia/Kolkata")

    raw_df = raw_df.rename(
        columns={
            "timestamp": "Date-time",
        }
    )

    # Keep only OHLC and volume columns
    df = raw_df[["Date-time", "open", "high", "low", "close", "volume"]]

    df.loc[:, ["open", "high", "low", "close", "volume"]] = df[
        ["open", "high", "low", "close", "volume"]
    ].astype(float)

    df = df.set_index("Date-time")

    return df

def get_indian_time():
    utc_now = datetime.datetime.utcnow()
    india_timezone = pytz.timezone("Asia/Kolkata")
    india_time = utc_now.replace(tzinfo=pytz.utc).astimezone(
        india_timezone
    )
    return india_time

def find_sr_levels(df, order=10, tolerance=0.0025, min_touches=2):
    prices = df['close'].values
    current_price = prices[-1]

    # Local minima (support) and maxima (resistance)
    min_idx = argrelextrema(prices, np.less, order=order)[0]
    max_idx = argrelextrema(prices, np.greater, order=order)[0]

    levels = np.concatenate((prices[min_idx], prices[max_idx]))
    levels = np.sort(levels)

    # Filter levels by touch count within tolerance
    final_levels = []
    for level in levels:
        count = np.sum(np.abs(prices - level) < (level * tolerance))
        if count >= min_touches:
            final_levels.append(level)

    # Remove near-duplicate levels
    cleaned_levels = []
    for level in final_levels:
        if not cleaned_levels or abs(level - cleaned_levels[-1]) > (level * tolerance):
            cleaned_levels.append(level)

    # Split into support and resistance
    supports = [lvl for lvl in cleaned_levels if lvl < current_price]
    resistances = [lvl for lvl in cleaned_levels if lvl > current_price]

    return supports, resistances

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

def bullish_engulfing(df, support):
    if len(df) < 2:
        return None
    df = df.iloc[-2:]
    curr_open, curr_close = oc(df, -1)
    prev_open, prev_close = oc(df, -2)
    if (support and curr_close > curr_open and prev_close < prev_open and
        curr_open < prev_close and curr_close > prev_open):
        if df['low'].iloc[-1] <= support * 1.005:
            return 'Buy', 'Bullish Engulfing'
    return None

def bearish_engulfing(df, resistance):
    if len(df) < 2:
        return None
    df = df.iloc[-2:]
    curr_open, curr_close = oc(df, -1)
    prev_open, prev_close = oc(df, -2)
    if (resistance and curr_close < curr_open and prev_close > prev_open and
        curr_open > prev_close and curr_close < prev_open):
        if df['high'].iloc[-1] >= resistance * 0.995:
            return 'Sell', 'Bearish Engulfing'
    return None

def hammer(df, support):
    if len(df) < 1:
        return None
    df = df.iloc[-1:]
    curr_open, curr_close = oc(df, -1)
    body = abs(curr_close - curr_open)
    lower_wick = (curr_open - df['low'].iloc[-1]) if curr_close > curr_open else (curr_close - df['low'].iloc[-1])
    if (support and lower_wick > body * 2 and df['low'].iloc[-1] <= support * 1.005 and curr_close > curr_open):
        return 'Buy', 'Hammer'
    return None

def shooting_star(df, resistance):
    if len(df) < 1:
        return None
    df = df.iloc[-1:]
    curr_open, curr_close = oc(df, -1)
    body = abs(curr_close - curr_open)
    upper_wick = (df['high'].iloc[-1] - curr_close) if curr_close > curr_open else (df['high'].iloc[-1] - curr_open)
    if (resistance and upper_wick > body * 2 and df['high'].iloc[-1] >= resistance * 0.995 and curr_close < curr_open):
        return 'Sell', 'Shooting Star'
    return None

def morning_star(df, support):
    if len(df) < 3:
        return None
    df = df.iloc[-3:]
    prev2_open, prev2_close = oc(df, 0)
    prev_open, prev_close = oc(df, 1)
    curr_open, curr_close = oc(df, 2)
    prev2_body = abs(prev2_close - prev2_open)
    if (support and
        prev2_close < prev2_open and
        abs(prev_close - prev_open) < prev2_body * 0.5 and
        curr_close > curr_open and
        curr_close > (prev2_open + prev2_close) / 2 and
        df['low'].iloc[2] <= support * 1.005):
        return 'Buy', 'Morning Star'
    return None

def evening_star(df, resistance):
    if len(df) < 3:
        return None
    df = df.iloc[-3:]
    prev2_open, prev2_close = oc(df, 0)
    prev_open, prev_close = oc(df, 1)
    curr_open, curr_close = oc(df, 2)
    prev2_body = abs(prev2_close - prev2_open)
    if (resistance and
        prev2_close > prev2_open and
        abs(prev_close - prev_open) < prev2_body * 0.5 and
        curr_close < curr_open and
        curr_close < (prev2_open + prev2_close) / 2 and
        df['high'].iloc[2] >= resistance * 0.995):
        return 'Sell', 'Evening Star'
    return None

def tweezer_bottom(df, support):
    if len(df) < 2:
        return None
    df = df.iloc[-2:]
    prev_open, prev_close = oc(df, -2)
    curr_open, curr_close = oc(df, -1)
    if (support and
        prev_close < prev_open and curr_close > curr_open and
        abs(df['low'].iloc[-1] - df['low'].iloc[-2]) / df['low'].iloc[-1] < 0.001 and
        df['low'].iloc[-1] <= support * 1.005):
        return 'Buy', 'Tweezer Bottom'
    return None

def tweezer_top(df, resistance):
    if len(df) < 2:
        return None
    df = df.iloc[-2:]
    prev_open, prev_close = oc(df, -2)
    curr_open, curr_close = oc(df, -1)
    if (resistance and
        prev_close > prev_open and curr_close < curr_open and
        abs(df['high'].iloc[-1] - df['high'].iloc[-2]) / df['high'].iloc[-1] < 0.001 and
        df['high'].iloc[-1] >= resistance * 0.995):
        return 'Sell', 'Tweezer Top'
    return None

def piercing_line(df, support):
    if len(df) < 2:
        return None
    df = df.iloc[-2:]
    prev_open, prev_close = oc(df, -2)
    curr_open, curr_close = oc(df, -1)
    if (support and
        prev_close < prev_open and
        curr_close > curr_open and
        curr_open < df['low'].iloc[-2] and
        curr_close > (prev_open + prev_close) / 2 and
        df['low'].iloc[-1] <= support * 1.005):
        return 'Buy', 'Piercing Line'
    return None

def dark_cloud_cover(df, resistance):
    if len(df) < 2:
        return None
    df = df.iloc[-2:]
    prev_open, prev_close = oc(df, -2)
    curr_open, curr_close = oc(df, -1)
    if (resistance and
        prev_close > prev_open and
        curr_open > df['high'].iloc[-2] and
        curr_close < curr_open and
        curr_close < (prev_open + prev_close) / 2 and
        df['high'].iloc[-1] >= resistance * 0.995):
        return 'Sell', 'Dark Cloud Cover'
    return None
