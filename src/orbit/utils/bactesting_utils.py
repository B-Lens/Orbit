import os
import numpy as np
from scipy.signal import argrelextrema
import pandas as pd
import mplfinance as mpf
from sklearn.cluster import DBSCAN


def save_candlestick_chart(
    df, s_val, r_val, trade_time, trade_type, entry_price=None, COIN_NAME='default'
):
    """
    Save candlestick chart with support, resistance levels, and a vertical line for entry price.

    :param df: DataFrame containing OHLCV data.
    :param s_val: Support level.
    :param r_val: Resistance level.
    :param trade_time: Time of trade.
    :param trade_type: "BUY" or "SELL".
    :param entry_price: The entry price to mark on the chart (optional).
    """
    # print(f"Debugging chart for {trade_time} ({trade_type}):")
    # print(f"DataFrame shape: {df.shape}")
    # print(f"DataFrame head:\n{df.head()}")
    # print(f"NaN check:\n{df[['open', 'high', 'low', 'close']].isna().sum()}")
    chart_data = df.copy()
    chart_data.index = pd.to_datetime(chart_data.index)

    # Define plot settings for support and resistance
    apds = [
        mpf.make_addplot(
            [s_val] * len(chart_data),
            color="blue",
            linestyle="dashed",
            label="Support",
        ),
        mpf.make_addplot(
            [r_val] * len(chart_data),
            color="red",
            linestyle="dashed",
            label="Resistance",
        ),
    ]

    # Add vertical line at trade_time to mark entry
    vline = dict(
        vlines=[trade_time],
        linestyle="--",
        colors="green",
        linewidths=2,
        alpha=0.7,
    )

    # Define a custom dark style
    dark_style = mpf.make_mpf_style(
        base_mpl_style="dark_background",  # Dark background
        marketcolors=mpf.make_marketcolors(
            up="green",  # Green for up candles
            down="red",  # Red for down candles
            wick="white",  # White wicks
            volume="gray",  # Gray volume bars
        ),
        gridcolor="white",  # Grid lines color
        gridstyle="--",  # Dashed grid lines
        facecolor="#1a1a1a",  # Background color (dark gray)
        edgecolor="white",  # Candle edge color
    )

    # Save chart
    file_name = f"charts/{COIN_NAME}/{trade_time.strftime('%Y-%m-%d_%H-%M-%S')}_{trade_type}.png"
    mpf.plot(
        chart_data,
        type="candle",
        style=dark_style,
        addplot=apds,
        vlines=vline,  # Add the vertical line
        title=f"{trade_type} at {trade_time} (Entry: {entry_price if entry_price else 'N/A'})",
        savefig=dict(fname=file_name, dpi=100),
        figsize=(
            30,
            16,
        ),  # Add this line to enlarge the chart (width=12, height=8)
    )
    # print(f"Saved chart: {file_name}")


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

def find_support_resistance_levels(df, cluster_eps=0.003, min_samples=3, order=5, 
                                  percent_tolerance=0.003, min_count=1, volume_percentile=80):
    """
    Identify support and resistance levels using DBSCAN clustering and argrelextrema.
    
    Parameters:
    - df: pandas DataFrame with 'close' and 'volume' columns
    - cluster_eps: DBSCAN epsilon as fraction of mean price (default: 0.003)
    - min_samples: Minimum points in a DBSCAN cluster (default: 3)
    - order: Number of points for argrelextrema (default: 5)
    - percent_tolerance: Tolerance for touch count as fraction of price (default: 0.003)
    - min_count: Minimum number of touches for a level (default: 1)
    - volume_percentile: Volume threshold percentile (default: 80)
    
    Returns:
    - support_levels: List of support price levels (below current price)
    - resistance_levels: List of resistance price levels (above current price)
    """
    # Validate input
    if not isinstance(df, pd.DataFrame) or 'close' not in df.columns or 'volume' not in df.columns:
        raise ValueError("df must be a pandas DataFrame with 'close' and 'volume' columns")
    if df['close'].isnull().any() or df['volume'].isnull().any():
        raise ValueError("DataFrame contains missing values")
    if len(df) < max(order * 2 + 1, min_samples * 2):
        return [], []  # Not enough data

    # Extract data
    prices = np.array(df['close'])
    volumes = np.array(df['volume'])
    current_price = prices[-1]  # Latest price for support/resistance classification

    # Step 1: DBSCAN clustering
    mean_price = np.mean(prices)
    eps = mean_price * cluster_eps
    clustering = DBSCAN(eps=eps, min_samples=min_samples).fit(prices.reshape(-1, 1))
    labels = clustering.labels_
    unique_labels = set(labels) - {-1}  # Exclude noise points

    # Step 2: Refine clusters with argrelextrema
    support_levels = []
    resistance_levels = []

    for label in unique_labels:
        cluster_indices = np.where(labels == label)[0]
        cluster_prices = prices[cluster_indices]
        cluster_volumes = volumes[cluster_indices]

        # Find local minima (support) and maxima (resistance)
        min_indices = argrelextrema(cluster_prices, np.less, order=order)[0]
        max_indices = argrelextrema(cluster_prices, np.greater, order=order)[0]

        # Validate support levels
        for idx in min_indices:
            level = cluster_prices[idx]
            tolerance = level * percent_tolerance
            touches = np.sum(np.abs(cluster_prices - level) < tolerance)
            if (touches >= min_count and 
                cluster_volumes[idx] > np.percentile(volumes, volume_percentile)):
                if level < current_price:  # Support if below current price
                    support_levels.append(level)

        # Validate resistance levels
        for idx in max_indices:
            level = cluster_prices[idx]
            tolerance = level * percent_tolerance
            touches = np.sum(np.abs(cluster_prices - level) < tolerance)
            if (touches >= min_count and 
                cluster_volumes[idx] > np.percentile(volumes, volume_percentile)):
                if level > current_price:  # Resistance if above current price
                    resistance_levels.append(level)

    # Return unique, sorted levels
    return sorted(set(support_levels)), sorted(set(resistance_levels))

def find_resistance_levels(data, order=5, percent_tolerance=0.003, min_count=2, volume_percentile=80):
    # Validate input
    if not isinstance(data, pd.DataFrame) or 'close' not in data.columns or 'volume' not in data.columns:
        raise ValueError("data must be a pandas DataFrame with 'close' and 'volume' columns")
    if data['close'].isnull().any() or data['volume'].isnull().any():
        raise ValueError("data contains missing values")
    
    closes = np.array(data['close'])
    volumes = np.array(data['volume'])
    
    # Adjust order for small datasets
    order = min(order, (len(closes) - 1) // 2)
    
    # Find local maxima
    indices = argrelextrema(closes, np.greater, order=order)[0]
    if len(indices) == 0:
        return []
    
    # Evaluate resistance levels
    levels = closes[indices]
    tolerance = levels * percent_tolerance
    counts = np.array([np.sum(np.abs(closes - level) < tol) for level, tol in zip(levels, tolerance)])
    volume_threshold = np.percentile(volumes, volume_percentile)
    valid = (counts >= min_count) & (volumes[indices] > volume_threshold)
    
    return sorted(set(levels[valid]))


def find_support_levels_v2(data, order=5, volume_percentile=60, eps=2, min_samples=2):
    # Validate input
    if not isinstance(data, pd.DataFrame) or 'close' not in data.columns or 'volume' not in data.columns:
        raise ValueError("data must be a pandas DataFrame with 'close' and 'volume' columns")
    if data['close'].isnull().any() or data['volume'].isnull().any():
        raise ValueError("data contains missing values")
    
    closes = np.array(data['close'])
    volumes = np.array(data['volume'])
    
    # Adjust order for small datasets
    order = min(order, (len(closes) - 1) // 2)
    
    # Find local minima
    indices = argrelextrema(closes, np.less, order=order)[0]
    if len(indices) == 0:
        return []
    
    levels = closes[indices]
    level_volumes = volumes[indices]

    # Filter out low-volume minima
    volume_threshold = np.percentile(volumes, volume_percentile)
    valid_levels = levels[level_volumes > volume_threshold]

    if len(valid_levels) == 0:
        return []

    # Cluster minima levels using DBSCAN
    def cluster_levels(levels, eps=2, min_samples=2):
        levels = np.array(levels).reshape(-1, 1)
        clustering = DBSCAN(eps=eps, min_samples=min_samples).fit(levels)
        labels = clustering.labels_
        unique_levels = []
        for label in set(labels):
            if label == -1:
                continue
            cluster_levels = levels[labels == label].flatten()
            unique_levels.append(np.mean(cluster_levels))
        return sorted(unique_levels)

    support_levels = cluster_levels(valid_levels, eps=eps, min_samples=min_samples)

    return support_levels


def find_support_levels(data, order=5, percent_tolerance=0.003, min_count=2, volume_percentile=60):
    # Validate input
    if not isinstance(data, pd.DataFrame) or 'close' not in data.columns or 'volume' not in data.columns:
        raise ValueError("data must be a pandas DataFrame with 'close' and 'volume' columns")
    if data['close'].isnull().any() or data['volume'].isnull().any():
        raise ValueError("data contains missing values")
    
    closes = np.array(data['close'])
    volumes = np.array(data['volume'])
    
    # Adjust order for small datasets
    order = min(order, (len(closes) - 1) // 2)
    
    # Find local minima
    indices = argrelextrema(closes, np.less, order=order)[0]
    if len(indices) == 0:
        return []
    
    mean_close = np.mean(closes)
    min_tolerance = mean_close * 0.002  # 0.2% of average price

    min_tolerance = max(mean_close * 0.003, 1.5)

    # Evaluate support levels
    levels = closes[indices]
    tolerance = np.maximum(levels * percent_tolerance, min_tolerance)
    counts = np.array([np.sum(np.abs(closes - level) < tol) for level, tol in zip(levels, tolerance)])
    volume_threshold = np.percentile(volumes, volume_percentile)
    valid = (counts >= min_count) & (volumes[indices] > volume_threshold)
    
    # Return unique, sorted levels
    return sorted(set(levels[valid]))

def save_reversal_mpf(**kwargs):
    df = kwargs.get('df')
    support_levels = kwargs.get('support_levels', [])
    resistance_levels = kwargs.get('resistance_levels', [])
    stop_loss = kwargs.get('stop_loss', [])
    target = kwargs.get("target", [])
    COIN_NAME = kwargs.get('COIN_NAME', 'UNKNOWN_COIN')
    trade_time = kwargs.get('trade_time')
    trade_type = kwargs.get('trade_type', 'Trade')
    entry_price = kwargs.get('entry_price', None)
    reversal_points = kwargs.get('reversal_points', [])  # List of (timestamp, price, label) tuples
    overlays = kwargs.get('overlays', {})  # Dict: {'EMA20': series, 'SMA50': series}

    if df is None or trade_time is None:
        raise ValueError("Required parameters 'df' and 'trade_time' must be provided in kwargs.")

    hline_levels = support_levels + resistance_levels
    hline_colors = ['#00ff00'] * len(support_levels) + ['#ff4040'] * len(resistance_levels)

    hline_levels = hline_levels + [stop_loss]
    hline_colors = hline_colors + ['#f18f32']

    hline_levels = hline_levels + [target]
    hline_colors = hline_colors + ["#3532f1"]

    vline = dict(
        vlines=[trade_time],
        linestyle="--",
        colors="yellow",
        linewidths=0.5,
        alpha=0.8,
    )

    file_name = f"charts/{COIN_NAME}/{trade_time.strftime('%Y-%m-%d_%H-%M-%S')}_{trade_type}.png"
    df.index = pd.to_datetime(df.index)

    # Add overlays to addplot
    apds = []
    for label, series in overlays.items():
        apds.append(mpf.make_addplot(series, color='orange' if 'EMA' in label else 'cyan', width=1.2, ylabel=label))

    # Add reversal markers
    for point in reversal_points:
        ts, price, label = point
        apds.append(mpf.make_addplot(
            [price if i == ts else None for i in df.index],
            type='scatter',
            markersize=200,
            marker='*',
            color='gold',
            secondary_y=False,
            panel=0
        ))

    # Modern dark theme
    dark_style = mpf.make_mpf_style(
        base_mpl_style="dark_background",
        marketcolors=mpf.make_marketcolors(
            up="#26ff26",
            down="#ff2626",
            wick="white",
            edge="white",
            volume="gray",
        ),
        gridcolor="#444",
        gridstyle="--",
        facecolor="#121212",
        edgecolor="#eeeeee",
    )

    title_text = f"{COIN_NAME} | {trade_type.upper()} at {trade_time.strftime('%Y-%m-%d %H:%M:%S')} | Entry: {entry_price if entry_price else 'N/A'}"

    plot_kwargs = dict(
        type='candle',
        hlines=dict(hlines=hline_levels, colors=hline_colors, linestyle='--'),
        style=dark_style,
        vlines=vline,
        title=f"{trade_type} at {trade_time} (Entry: {entry_price if entry_price else 'N/A'})",
        savefig=dict(fname=file_name, dpi=100),
        figsize=(30, 16),
    )

    # Only add addplot if it’s actually supplied
    if 'addplot' in kwargs and kwargs['addplot'] is not None:
        plot_kwargs['addplot'] = kwargs['addplot']

    # Now safe to plot
    mpf.plot(df, **plot_kwargs)
    # mpf.plot(
    #     df,
    #     type='candle',
    #     hlines=dict(hlines=hline_levels, colors=hline_colors, linestyle='--'),
    #     style=dark_style,
    #     vlines=vline,
    #     addplot=apds if apds else None,
    #     title=title_text,
    #     savefig=dict(fname=file_name, dpi=150, pad_inches=0.25, bbox_inches='tight'),
    #     figsize=(32, 18),
    #     tight_layout=True
    # )

def clear_folder(folder_path):
    #Make folder if it does not exist
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        
    # Remove all files in the folder
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        os.remove(file_path)


def resample_data(df, timeframe):
    return (
        df.copy()
        .resample(timeframe)
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )


def get_data(file_path: str):
    data = pd.read_csv(file_path)
    data["Date-time"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("Date-time")
    data = data.drop(["timestamp"], axis=1)
    data.columns = ["open", "high", "low", "close", "volume"]
    return data


def get_data_n_year(file_path: str, year=None):
    data = pd.read_csv(file_path)
    data["datetime"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("datetime")
    data = data.drop(["timestamp"], axis=1)
    data.columns = ["open", "high", "low", "close", "volume"]
    if not year:
        return data.copy()
    return data[year * 35040 * -1 :].copy()


def get_bitcoin_1min():
    data = pd.read_csv("../../data/bitcoin_1min.csv")
    data["Date-time"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("Date-time")
    data_15min = data.resample("15T").agg(
        {
            "high": "max",
            "low": "min",
            "open": "first",
            "close": "last",
            "volume": "sum",
        }
    )
    data_15min["Date"] = data_15min.index
    return data_15min


def get_bitcoin_data():
    data = pd.read_csv("../data/bitcoin.csv")
    data["Date-time"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("Date-time")
    data = data.drop(["timestamp"], axis=1)
    data.columns = ["open", "high", "low", "close", "volume"]
    return data


def get_bitcoin_data_1year():
    data = pd.read_csv("../data/BTCUSDT_15m_1years.csv")
    data["Date-time"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("Date-time")
    data = data.drop(["timestamp"], axis=1)
    data.columns = ["open", "high", "low", "close", "volume"]
    return data


def get_etherium_data():
    data = pd.read_csv("../data/etherium.csv")
    data["Date-time"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("Date-time")
    data = data.drop(["timestamp"], axis=1)
    data.columns = ["open", "high", "low", "close", "volume"]
    return data


def get_bnb_data():
    data = pd.read_csv("../../data/bnb.csv")
    data["Date-time"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("Date-time")
    data = data.drop(["timestamp"], axis=1)
    data.columns = ["open", "high", "low", "close", "volume"]
    return data


def get_bch_data():
    data = pd.read_csv("../../data/bch.csv")
    data["Date-time"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("Date-time")
    data = data.drop(["timestamp"], axis=1)
    data.columns = ["open", "high", "low", "close", "volume"]
    return data


def get_solana_data():
    data = pd.read_csv("../data/solana.csv")
    data["Date-time"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("Date-time")
    data = data.drop(["timestamp"], axis=1)
    data.columns = ["open", "high", "low", "close", "volume"]
    return data
