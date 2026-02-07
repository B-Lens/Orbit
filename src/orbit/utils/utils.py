import numpy as np
from scipy.signal import argrelextrema
import datetime
import pytz
import pandas as pd
import tempfile
import mplfinance as mpf

import logging
logger = logging.getLogger("Orbit")


def get_indian_time():
    utc_now = datetime.datetime.utcnow()
    india_timezone = pytz.timezone("Asia/Kolkata")
    india_time = utc_now.replace(tzinfo=pytz.utc).astimezone(
        india_timezone
    )
    return india_time

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

