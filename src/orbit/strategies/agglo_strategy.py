import os
import redis
import json
import numpy as np
import pandas as pd
import logging
from abc import abstractmethod
from typing import Dict, List, Tuple, Any
from sklearn.cluster import AgglomerativeClustering
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score
from orbit.strategies.strategies_base import Strategy
from orbit.utils.utils import generate_chart


# Color codes for terminal output
class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    PURPLE = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'
    MAGENTA = "\033[35m"

# Custom colored logger
class ColoredLogger:
    def __init__(self, logger):
        self.logger = logger
    
    def info(self, message, color=Colors.WHITE):
        self.logger.info(f"{color}{message}{Colors.END}")

    def info_c(self, message, color=Colors.WHITE):
        self.logger.info(f"{color}{message}{Colors.END}")
    
    def debug(self, message, color=Colors.CYAN):
        self.logger.debug(f"{color}{message}{Colors.END}")
    
    def warning(self, message, color=Colors.YELLOW):
        self.logger.warning(f"{color}{message}{Colors.END}")
    
    def error(self, message, color=Colors.RED):
        self.logger.error(f"{color}{message}{Colors.END}")

    def ohlc(self, message, color=Colors.CYAN):
        self.logger.info(f"{color}{message}{Colors.END}")     
    
    def success(self, message):
        self.logger.debug(f"{Colors.GREEN}{Colors.BOLD}{message}{Colors.END}")
    
    def cluster(self, message):
        self.logger.debug(f"{Colors.PURPLE}{Colors.BOLD}{message}{Colors.END}")
    
    def resistance(self, message):
        self.logger.debug(f"{Colors.RED}{Colors.BOLD}{message}{Colors.END}")
    
    def support(self, message):
        self.logger.debug(f"{Colors.GREEN}{Colors.BOLD}{message}{Colors.END}")
    
    def params(self, message):
        self.logger.debug(f"{Colors.BLUE}{Colors.BOLD}{message}{Colors.END}")
    
    def filter_pass(self, message):
        self.logger.debug(f"{Colors.GREEN}{message}{Colors.END}")
    
    def filter_fail(self, message):
        self.logger.debug(f"{Colors.RED}{message}{Colors.END}")
    
    def sweep(self, message):
        self.logger.debug(f"{Colors.PURPLE}{Colors.BOLD}🔧 SWEEP: {message}{Colors.END}")
    
    def fibonacci(self, message):
        self.logger.debug(f"{Colors.CYAN}{Colors.BOLD}🔢 FIBONACCI: {message}{Colors.END}")
    
    def seperator(self, message = None ):
        """Logs a separator line with an optional title."""
        length = 60 
        character = "="
        if message:
            line = f" {message} ".center(60, character)
        else:
            line = character * length
        logger.info(line)

# Change to DEBUG for detailed logs
base_logger = logging.getLogger("Orbit")
logger = ColoredLogger(base_logger)


class AggloBase(Strategy):
    def __init__(self, data: pd.DataFrame):
        super().__init__(data)
        self.redis_client = None
    
        try:
            self.redis_client = redis.StrictRedis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                db=int(os.getenv("REDIS_DB", "0")),
                decode_responses=True,
            )
        except Exception as e:
            logger.error(f"Redis connection error: {e}")
            self.redis_client = None

    class PersistencePlugin:
        """Maintain and update SR levels across lookbacks."""
        def __init__(self, redis_client, **kwargs):
            self.redis_client = redis_client
            class_name = kwargs.get('class_name', None)
            logger.info(f"Class name = {class_name.split('_')}")
            self.COIN = class_name.split('_')[-1]
            try:
                data = self.redis_client.get(f"{self.COIN}_support_level")
                data = {float(k): v for k, v in json.loads(data).items()}
                self.support_levels: dict[float, float] = data if data else {}
                logger.info(f"Loaded support levels from Redis for {self.COIN}: {self.support_levels}")
            except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
                logger.error("⚠️ Redis is not accessible. Using empty support_levels.")
                self.support_levels = {}
            except Exception as e:
                logger.error(f"Error loading support levels from Redis: {e}")
                self.support_levels = {}

            try:
                data = self.redis_client.get(f"{self.COIN}_resistance_level")
                data = {float(k): v for k, v in json.loads(data).items()}
                self.resistance_levels: dict[float, float] = data if data else {}
                logger.info(f"Loaded resistance levels from Redis for {self.COIN}: {self.resistance_levels}")
            except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
                logger.error("⚠️ Redis is not accessible. Using empty resistance_levels.")
                self.resistance_levels = {}
            except Exception as e:
                logger.error(f"Error loading resistance levels from Redis: {e}")
                self.resistance_levels = {}

            self.breaked_support_level: List[float] = []  #
            self.breaked_resistance_level: List[float] = []  #
            self.inactive_bars = 10000  # Bars to consider a level inactive
            self.history = []  # list of (call_index, {level: score})
            self.call_index = 0

        def get_support_levels(self) -> List[float]:
            return sorted(list(self.support_levels.keys()), reverse=True)
        
        def get_resistance_levels(self) -> List[float]:
            return sorted(list(self.resistance_levels.keys()))

        def update(self, df: pd.DataFrame, **kwargs) -> List[float]:

            window_end = kwargs.get('window_end')
            close_price = df['close'].iloc[-1]

            if 'support_zones' in kwargs:
                for level in kwargs['support_zones']:
                    if level not in self.get_support_levels() and level < close_price:
                        self.support_levels[level] = window_end

            s_levels = self.get_support_levels()
            logger.info(f"Current Support Levels before update: {s_levels}")
            for level in s_levels:
                level_idx = self.support_levels[level]
                if df.iloc[level_idx:window_end]['close'].min() < level or  level > close_price:
                    self.breaked_support_level.append(level)
                    del self.support_levels[level]
                    logger.info(f"Support level {level} broken at index {window_end}")
                
                if window_end > level_idx + self.inactive_bars:  # 1000 bars cooldown
                    del self.support_levels[level]
                    logger.info(f"Support level {level} removed due to inactivity at index {window_end}")

                
            if 'resistance_zones' in kwargs:
                for level in kwargs['resistance_zones']:
                    if level not in self.get_resistance_levels() and level > close_price:
                        self.resistance_levels[level] = window_end                  

            r_levels = self.get_resistance_levels()
            logger.info(f"Current Resistance Levels before update: {r_levels}")
            for level in r_levels:
                level_idx = self.resistance_levels[level]

                if df.iloc[level_idx:window_end]['close'].max() > level or level <= close_price:
                    self.breaked_resistance_level.append(level)
                    del self.resistance_levels[level]
                    logger.info(f"Resistance level {level} broken at index {window_end}")

                if window_end > level_idx + self.inactive_bars:  # 1000 bars cooldown
                    del self.resistance_levels[level]
                    logger.info(f"Resistance level {level} removed due to inactivity at index {window_end}")

            try:
                self.redis_client.set(f"{self.COIN}_support_level", json.dumps(self.support_levels)) if 'support_zones' in kwargs else self.redis_client.set(f"{self.COIN}_resistance_level", json.dumps(self.resistance_levels))
                logger.info(f"Persisted levels to Redis for {self.COIN}")
            except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
                logger.error("⚠️ Redis is not accessible. Changes to levels won't be persisted.")
            return self.get_support_levels() if 'support_zones' in kwargs else self.get_resistance_levels()

    # Parameter configuration class
    class ParameterConfig:
        """Configuration class for all tunable parameters"""
        
        def __init__(self, **kwargs):
            # Default parameters for agglomerative clustering - LOOSENED for more detection
            self.agglo_n_clusters_range = kwargs.get('agglo_n_clusters_range', (2, 10))  # Range for silhouette optimization
            self.agglo_linkage = kwargs.get('agglo_linkage', 'average')  # 'ward', 'complete', 'average', 'single'
            self.agglo_distance_threshold = kwargs.get('agglo_distance_threshold', None)  # For distance-based clustering
            self.min_cluster_strength = kwargs.get('min_cluster_strength', 0.5)  # Reduced from 2.0 for more zones
            self.volume_filter_threshold = kwargs.get('volume_filter_threshold', 1.0)  # Reduced from 1.5 for more volume points
            self.wick_threshold = kwargs.get('wick_threshold', 0.4)  # Reduced from 0.75 for more wick detection
            self.lookback_period = kwargs.get('lookback_period', 250)  # Increased from 50 for more data
            self.step_size = kwargs.get('step_size', 30)
            self.min_data_points = kwargs.get('min_data_points', 250)
            self.rejection_atr_mul = kwargs.get('rejection_atr_mul', 2.5)  # ATR multiplier for rejection filtering
            self.volume_ma = kwargs.get('volume_ma', 14)  # Moving average period for volume filtering
            self.momentum_drop = kwargs.get('momentum_drop', 4)  # Minimum momentum drop to consider rejection
            # LOOSENED PARAMETERS for more detection
            self.recency_weight = kwargs.get('recency_weight', 0.5)  # Reduced from 0.7 for more balanced detection
            self.max_zones_per_type = kwargs.get('max_zones_per_type', 5)  # Increased from 3 for more zones
            self.price_relevance_threshold = kwargs.get('price_relevance_threshold', 0.25)  # Increased from 0.15 for more levels
            self.min_touches_per_zone = kwargs.get('min_touches_per_zone', 2)  # Reduced from 2 for more zones
            
            # DBSCAN parameters for noise filtering
            self.dbscan_eps_ratio = kwargs.get('dbscan_eps_ratio', 0.02)  # 2% of average price for epsilon
            self.dbscan_min_samples = kwargs.get('dbscan_min_samples', 2)  # Minimum samples for core point
            self.enable_dbscan_filtering = kwargs.get('enable_dbscan_filtering', True)  # Enable/disable DBSCAN filtering
            
            # Fibonacci parameters
            self.fibonacci_levels = kwargs.get('fibonacci_levels', [0.5, 0.618, 1])

            # Evalution parameters
            self.evalution_atr_period = kwargs.get('evalution_atr_period', 14)  # ATR period for evaluation
            self.evalution_lookahead = kwargs.get('evalution_lookahead', 25)  # Lookahead period for evaluation
            self.evalution_breakout_penalty = kwargs.get('evalution_breakout_penalty', 0.5)  # Penalty for breakouts
            self.persistence_decay = kwargs.get('persistence_decay', 0.9)  # Decay factor for persistence
            self.evalution_tolerance_factor = kwargs.get('evalution_tolerance_factor', 0.5)  # ATR multiplier for tolerance
            self.significant_move = kwargs.get('significant_move', 1.2)  # Minimum move size to consider significant
            self.swing_lookback = kwargs.get('swing_lookback', 30)  # Lookback for swing high/low detection
            self.swing_window = kwargs.get('swing_window', 5)  # Window size for swing detection
            
        def to_dict(self):
            """Convert parameters to dictionary for wandb logging"""
            return {
                'agglo_n_clusters_range': self.agglo_n_clusters_range,
                'agglo_linkage': self.agglo_linkage,
                'agglo_distance_threshold': self.agglo_distance_threshold,
                'min_cluster_strength': self.min_cluster_strength,
                'volume_filter_threshold': self.volume_filter_threshold,
                'wick_threshold': self.wick_threshold,
                'lookback_period': self.lookback_period,
                'step_size': self.step_size,
                'min_data_points': self.min_data_points,
                'recency_weight': self.recency_weight,
                'max_zones_per_type': self.max_zones_per_type,
                'price_relevance_threshold': self.price_relevance_threshold,
                'min_touches_per_zone': self.min_touches_per_zone,
                'dbscan_eps_ratio': self.dbscan_eps_ratio,
                'dbscan_min_samples': self.dbscan_min_samples,
                'enable_dbscan_filtering': self.enable_dbscan_filtering,
                'fibonacci_levels': self.fibonacci_levels,
                'volume_ma': self.volume_ma,
                'evalution_atr_period': self.evalution_atr_period,
                'evalution_lookahead': self.evalution_lookahead,
                'evalution_breakout_penalty': self.evalution_breakout_penalty,
                'persistence_decay': self.persistence_decay,
                'evalution_tolerance_factor': self.evalution_tolerance_factor,
                'significant_move': self.significant_move,
                'rejection_atr_mul': self.rejection_atr_mul,
                'swing_lookback': self.swing_lookback,
                'swing_window': self.swing_window,
                'momentum_drop': self.momentum_drop,
            }
        
        def log_params(self):
            """Log current parameters"""
            logger.params("🔧 Current Parameters:")
            for key, value in self.to_dict().items():
                logger.params(f"   - {key}: {value}")



    def find_swing_low(self, df:pd.DataFrame, window=3):
        """
        Identifies swing lows in OHLCV DataFrame.
        A swing low is defined as a low lower than its 'window' neighbors on both sides.

        Parameters:
        - df: DataFrame with a 'Low' column.
        - window: How many bars to look before and after for comparison.

        Returns:
        - Series of booleans (True where swing low occurs).
        """
        lows = df['low']
        swing_lows = lows.rolling(window=window*2+1, center=True).apply(
            lambda x: x[window] == min(x), raw=True
        ) == 1
        
        if swing_lows.any():
            # Return the value of the last swing low
            last_index = swing_lows[swing_lows].index[-1]
            return df.loc[last_index, 'low']
        # No swing low found, return lowest low
        return lows.min()
    
    def find_swing_high(self, df:pd.DataFrame, window:int=3) -> pd.Series:
        """
        Identifies swing highs in OHLCV DataFrame.
        A swing high is defined as a high higher than its 'window' neighbors on both sides.

        Parameters:
        - df: DataFrame with a 'High' column.
        - window: How many bars to look before and after for comparison.

        Returns:
        - Series of booleans (True where swing high occurs).
        """
        highs = df['high']
        swing_highs = highs.rolling(window=window*2+1, center=True).apply(
            lambda x: x[window] == max(x), raw=True
        ) == 1
        
        if swing_highs.any():
            # Return the value of the last swing high
            last_index = swing_highs[swing_highs].index[-1]
            return df.loc[last_index, 'high']
        # No swing high found, return highest high
        return highs.max()
    
    def SR_levls_implementation(self, df: pd.DataFrame, persistence: PersistencePlugin, params: ParameterConfig, window_end: int) -> Dict[str, List[float]]:

        current_data = df.iloc[window_end-params.lookback_period:window_end].copy()
        rejection_points, rejection_types, rejection_volumes = self.extract_price_rejection_points_momentum(
            current_data,
            params,
        )

        logger.debug(f"Rejection points extracted: {len(rejection_points)}")

        # Analyze clusters with agglomerative clustering
        results = self.analyze_clusters_agglomerative(
            rejection_points, rejection_types, rejection_volumes, current_data, params, #support_levels=iterate_and_analyze_sr_levels.persistence.get_support_levels(), resistance_levels=iterate_and_analyze_sr_levels.persistence.get_resistance_levels()
        )
        logger.debug(f"Support and resistance zones identified with rejection points: {len(rejection_points)}")

        resistance_zones = results.get('resistance_zones', [])
        support_zones = results.get('support_zones', [])


        support_levels = persistence.update(df=df, window_end=window_end, support_zones=support_zones)
        logger.info(f"Support Levels: {support_levels}")
        support_levels = support_levels[:50] # truncated for discord limit
        resistance_levels = persistence.update(df=df, window_end=window_end, resistance_zones=resistance_zones)
        logger.info(f"Resistance Levels: {resistance_levels}")
        resistance_levels = resistance_levels[:50] # truncated for discord limit
        return {'support': support_levels, 'resistance': resistance_levels}
    
    def extract_price_rejection_points_momentum(self, df: pd.DataFrame, params) -> Tuple[List[float], List[str], List[float]]:
        """
        Extract rejection points based on wick size, momentum-based rejection, and optionally pivot points.
        Includes ATR and volume filters.
        """
        recent_data = df

        rejection_points, rejection_types, rejection_volumes = [], [], []

        highs, lows = recent_data['high'].values, recent_data['low'].values
        closes = recent_data['close'].values
        volumes = recent_data['volume'].values

        
        # ATR (True Range)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift()).abs(),
            (df['low'] - df['close'].shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().values

        # avg_volume = recent_data['volume'].rolling(20).mean().values
        # vol_std = recent_data['volume'].rolling(20).std(ddof=0).replace(0, np.nan)
        # vol_zscore = ((recent_data['volume'] - avg_volume) / vol_std).values

        momentum_drop = params.momentum_drop

        range_window = 6
        range_tolerance = 0.3
        breakout_confirm = 2

        # Calculate swing high and swing low
        swing_high = max(highs)
        swing_low = min(lows)
        current_price = closes[-1]
        if swing_high > current_price and (abs(swing_high - current_price) / current_price ) * 100 >= params.momentum_drop:
            rejection_points.append(swing_high)
            rejection_types.append("pivot_high")
            rejection_volumes.append(volumes[np.argmax(recent_data['high'].values)])
        if swing_low < current_price and (abs(current_price - swing_low) / current_price ) * 100 >= params.momentum_drop:
            rejection_points.append(swing_low)
            rejection_types.append("pivot_low")
            rejection_volumes.append(volumes[np.argmin(recent_data['low'].values)])


        for i in range(14, len(recent_data) - 6):
            if np.isnan(atr[i]) or atr[i] <= 0:
                continue

            # ========== RESISTANCE ==========
            # Case 1: Peak -> Drop
            if highs[i] == max(highs[i-3:i+3]):  # local peak
                segment = lows[i+1:]
                if len(segment) == 0:
                    continue
                lowest_after_peak = min(segment)
                drop = ((highs[i] - lowest_after_peak) / highs[i] ) * 100
                if segment.size > 0 and drop >= momentum_drop:
                    lowest_index_after_peak = i + 1 + np.argmin(segment)
                    future_closes = closes[i:]
                    if not np.any(future_closes > highs[i]):
                        rejection_points.append(highs[i])
                        rejection_types.append("momentum_drop")
                        rejection_volumes.append(volumes[i])
                        i = lowest_index_after_peak  # Skip ahead to avoid double counting

            # Case 2: Peak + Range -> Drop
            if i >= 3 and i < len(highs) - 3 and  highs[i] == max(highs[i-3:i+3]):  # local peak
                range_high = np.max(highs[i+1:i+1+range_window])
                range_low  = np.min(lows[i+1:i+1+range_window])
                if (range_high - range_low) < atr[i] * range_tolerance:
                    # drop check after consolidation
                    if np.any(closes[i+range_window:i+range_window+breakout_confirm] < range_low):
                        future_closes = closes[i:]
                        if not np.any(future_closes > highs[i]):
                            rejection_points.append(highs[i])
                            rejection_types.append("momentum_drop")
                            rejection_volumes.append(volumes[i])

            # ========== SUPPORT ==========
            # Case 1: Trough -> Rise
            if lows[i] == min(lows[i-3:i+3]):  # local trough
                segment = highs[i+1:]
                if len(segment) == 0:
                    continue
                highest_after_trough = max(segment)
                rise = ((highest_after_trough - lows[i] ) / lows[i] ) * 100
                if segment.size > 0 and rise > momentum_drop:
                    highest_index_after_peak = i + 1 + np.argmax(segment)
                    future_closes = closes[i:]
                    if not np.any(future_closes < lows[i]):
                        rejection_points.append(lows[i])
                        rejection_types.append("momentum_rise")
                        rejection_volumes.append(volumes[i])
                        i = highest_index_after_peak  # Skip ahead to avoid double counting


            # Case 2: Trough + Range -> Rise
            if i >= 3 and i < len(lows) - 3 and lows[i] == min(lows[i-3:i+3]):  # local trough
                range_high = np.max(highs[i+1:i+1+range_window])
                range_low  = np.min(lows[i+1:i+1+range_window])
                if (range_high - range_low) < atr[i] * range_tolerance:
                    # rise check after consolidation
                    if np.any(closes[i+range_window:i+range_window+breakout_confirm] > range_high):
                        future_closes = closes[i:]
                        if not np.any(future_closes < lows[i]):
                            rejection_points.append(lows[i])
                            rejection_types.append("momentum_rise")
                            rejection_volumes.append(volumes[i])
                        

        logger.debug(f"Total Rejection Points Detected: {len(rejection_points)}")
        return rejection_points, rejection_types, rejection_volumes

    
    def analyze_clusters_agglomerative(self, rejection_points: List[float], rejection_types: List[str], 
                                    rejection_volumes: List[float], df: pd.DataFrame, 
                                    params: ParameterConfig, **kwargs) -> Dict[str, Any]:
        """Analyze clusters using agglomerative clustering with silhouette score optimization"""
        logger.debug(f"🔧 Analyzing clusters with agglomerative clustering - {len(rejection_points)} points")
        
        if len(rejection_points) < 2:
            logger.warning("Not enough rejection points for clustering")
            return {}
        
        # Separate support and resistance points
        resistance_points = []
        resistance_volumes = []
        support_points = []
        support_volumes = []

        if 'support_levels' in kwargs:
            for levels in  kwargs['support_levels']:
                support_points.append(levels)
                support_volumes.append(1)  # No volume data for pre-defined levels

        if 'resistance_levels' in kwargs:
            for levels in  kwargs['resistance_levels']:
                resistance_points.append(levels)
                resistance_volumes.append(1)  # No volume data for pre-defined levels

        logger.cluster(f"🔍 Initial Separation:")
        logger.resistance(f"   - Initial Resistance points: {(resistance_points)}")
        logger.support(f"   - Initial Support points: {(support_points)}")

        for i, (point, type_, volume) in enumerate(zip(rejection_points, rejection_types, rejection_volumes)):
            if type_ in ['upper_wick', 'pivot_high', 'fibonacci_resistance', 'momentum_drop']:# swing_high_support ]:
                resistance_points.append(point)
                resistance_volumes.append(volume)
            elif type_ in ['lower_wick', 'pivot_low', 'fibonacci_support', 'momentum_rise']:# 'swing_low_support']:
                support_points.append(point)
                support_volumes.append(volume)
        
        logger.cluster(f"🔍 Separated rejection points:")
        logger.resistance(f"   - Resistance points: {len(resistance_points)}")
        logger.support(f"   - Support points: {len(support_points)}")
        
        # Process resistance zones with agglomerative clustering
        resistance_zones = self.cluster_points_agglomerative_with_dbscan(
            resistance_points, resistance_volumes, 'resistance', df, params
        )
        
        # Process support zones with agglomerative clustering
        support_zones = self.cluster_points_agglomerative_with_dbscan(
            support_points, support_volumes, 'support', df, params
        )

        for i, (point, type_, volume) in enumerate(zip(rejection_points, rejection_types, rejection_volumes)):
            if type_ in ['pivot_high']:
                resistance_zones.append(point)
            elif type_ in ['pivot_low']:
                support_zones.append(point)
        
        # Calculate Fibonacci levels
        # fibonacci_data = calculate_fibonacci_levels(df, params.lookback_period)
        
        logger.cluster(f"📊 Final Results:")
        logger.resistance(f"   - Resistance zones: {[f'{z:.4f}' for z in resistance_zones]}")
        logger.support(f"   - Support zones: {[f'{z:.4f}' for z in support_zones]}")
        # logger.fibonacci(f"   - Fibonacci levels: {[f'{z:.4f}' for z in fibonacci_data['fibonacci_levels']]}")
        
        # Count rejection point types
        traditional_resistance = len([p for p, t in zip(rejection_points, rejection_types) if t in ['upper_wick', 'pivot_high']])
        traditional_support = len([p for p, t in zip(rejection_points, rejection_types) if t in ['lower_wick', 'pivot_low']])
        fibonacci_resistance = len([p for p, t in zip(rejection_points, rejection_types) if t == 'fibonacci_resistance'])
        fibonacci_support = len([p for p, t in zip(rejection_points, rejection_types) if t == 'fibonacci_support'])
        swing_resistance = len([p for p, t in zip(rejection_points, rejection_types) if t == 'swing_high_resistance'])
        swing_support = len([p for p, t in zip(rejection_points, rejection_types) if t == 'swing_low_support'])
        
        logger.debug(f"   - Rejection point breakdown:")
        logger.debug(f"     * Traditional: {traditional_resistance} resistance, {traditional_support} support")
        logger.fibonacci(f"     * Fibonacci: {fibonacci_resistance} resistance, {fibonacci_support} support")
        logger.fibonacci(f"     * Swing: {swing_resistance} resistance, {swing_support} support")
        
        return {
            'resistance_zones': resistance_zones,
            'support_zones': support_zones,
            # 'fibonacci_levels': fibonacci_data['fibonacci_levels'],
            # 'swing_high': fibonacci_data['swing_high'],
            # 'swing_low': fibonacci_data['swing_low'],
            # 'price_range': fibonacci_data['price_range'],
            'clustering_method': 'agglomerative',
            'linkage_method': params.agglo_linkage
        }

    @abstractmethod
    def generate_signals(self) -> Dict[str, Any]:
        pass

     
    def find_optimal_clusters_agglomerative(self, points: List[float], max_clusters: int = 10) -> Tuple[int, float]:
        """Find optimal number of clusters using silhouette score with agglomerative clustering"""
        if len(points) < 2:
            return 1, 0.0
        
        X = np.array(points).reshape(-1, 1)
        
        # Standardize the data
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        best_score = -1
        best_n_clusters = 1
        
        # Try different numbers of clusters
        for n_clusters in range(2, min(max_clusters + 1, len(points))):
            try:
                # Perform agglomerative clustering
                clustering = AgglomerativeClustering(n_clusters=n_clusters)
                cluster_labels = clustering.fit_predict(X_scaled)
                
                # Calculate silhouette score
                if len(set(cluster_labels)) > 1:  # Need at least 2 clusters for silhouette
                    score = silhouette_score(X_scaled, cluster_labels)
                    
                    if score > best_score:
                        best_score = score
                        best_n_clusters = n_clusters
                        
                    logger.debug(f"   - {n_clusters} clusters: silhouette score = {score:.4f}")
            except Exception as e:
                logger.debug(f"   - Error with {n_clusters} clusters: {e}")
                continue
        
        logger.cluster(f"🔧 Optimal clusters: {best_n_clusters} (silhouette score: {best_score:.4f})")
        return best_n_clusters, best_score


    def calculate_fibonacci_levels(self, df: pd.DataFrame, lookback_period: int = 50) -> Dict[str, List[float]]:
        """Calculate Fibonacci retracement levels based on swing high and low"""
        logger.fibonacci(f"Calculating Fibonacci levels with lookback: {lookback_period}")
        
        # if len(df) < lookback_period:
        #     logger.warning(f"Not enough data for Fibonacci calculation. Need {lookback_period}, have {len(df)}")
        #     return {'fibonacci_levels': [], 'swing_high': None, 'swing_low': None}
        
        # Get the recent data
        recent_data = df.tail(lookback_period)
        
        # Find swing high and low
        swing_high = recent_data['high'].max()
        swing_low = recent_data['low'].min()
        
        # Calculate Fibonacci levels
        price_range = swing_high - swing_low
        fibonacci_levels = []
        
        # Standard Fibonacci ratios
        fib_ratios = [0.5, 0.618, 1]
        
        for ratio in fib_ratios:
            level = swing_high - (price_range * ratio)
            fibonacci_levels.append(level)
        
        logger.fibonacci(f"Swing High: {swing_high:.4f}")
        logger.fibonacci(f"Swing Low: {swing_low:.4f}")
        logger.fibonacci(f"Price Range: {price_range:.4f}")
        logger.fibonacci(f"Fibonacci Levels: {[f'{level:.4f}' for level in fibonacci_levels]}")
        
        return {
            'fibonacci_levels': fibonacci_levels,
            'swing_high': swing_high,
            'swing_low': swing_low,
            'price_range': price_range
        }


    def cluster_points_agglomerative_with_dbscan(self, points: List[float], volumes: List[float], zone_type: str, 
                                            df: pd.DataFrame, params: ParameterConfig) -> List[float]:
        """Hybrid clustering: Agglomerative clustering followed by DBSCAN noise filtering"""
        if len(points) < 2:
            return []
        
        logger.cluster(f"🔧 Hybrid clustering (Agglomerative + DBSCAN) for {zone_type} with {len(points)} points")
        
        # Step 1: Agglomerative clustering (original approach)
        optimal_n_clusters, silhouette_score_val = self.find_optimal_clusters_agglomerative(
            points, max(params.agglo_n_clusters_range)
        )
        
        if optimal_n_clusters == 1:
            logger.cluster(f"Only one cluster found for {zone_type}")
            # return []
        
        # Prepare data for clustering
        X = np.array(points).reshape(-1, 1)
        
        # Standardize the data
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        # Perform agglomerative clustering with optimal number of clusters
        clustering = AgglomerativeClustering(
            n_clusters=optimal_n_clusters,
            linkage=params.agglo_linkage
        )
        cluster_labels = clustering.fit_predict(X_scaled)
        
        # Analyze agglomerative results
        unique_labels = set(cluster_labels)
        n_clusters = len(unique_labels)
        
        logger.cluster(f"📊 {zone_type.title()} Agglomerative Results:")
        logger.debug(f"   - Total points: {len(X)}")
        logger.debug(f"   - Clusters found: {n_clusters}")
        logger.debug(f"   - Silhouette score: {silhouette_score_val:.4f}")
        logger.debug(f"   - Linkage method: {params.agglo_linkage}")
        
        # Calculate current price for relevance filtering
        current_price = df['close'].iloc[-1]
        zones = []
        zone_scores = []
        
        for label in unique_labels:
            # Get points in this cluster
            cluster_mask = cluster_labels == label
            cluster_points = np.array(points)[cluster_mask]
            cluster_volumes = np.array(volumes)[cluster_mask]

            # Volume/recency weighted cluster center – heavier volume and recent touches
            # exert more influence, reflecting common S/R heuristics (Murphy 1999; Bulkowski 2005)
            volume_ma = df['volume'].tail(params.volume_ma).mean()
            volume_weights = cluster_volumes / volume_ma if volume_ma > 0 else np.ones(len(cluster_volumes))
            recency_weights = np.linspace(params.recency_weight, 1.0, len(cluster_points))
            weights = volume_weights * recency_weights
            weighted_center = np.average(cluster_points, weights=weights)

            weighted_center = np.inf

            # Use min/max as fallback if weighting fails
            if not np.isfinite(weighted_center):
                weighted_center = np.min(cluster_points) if zone_type == 'support' else np.max(cluster_points)
            cluster_center = float(round(weighted_center, 2))

            # Calculate cluster strength incorporating weights
            base_strength = weights.sum()
            volume_factor = np.mean(cluster_volumes) / volume_ma if volume_ma > 0 else 1.0

            cluster_strength = base_strength * volume_factor
            
            # # Volume filter
            high_volume_touches = sum(1 for vol in cluster_volumes if vol > volume_ma * params.volume_filter_threshold)
            
            # Price relevance filter
            price_distance = abs(cluster_center - current_price) / current_price
            is_price_relevant = price_distance <= params.price_relevance_threshold
            
            # Directional validation
            if zone_type == 'resistance':
                is_directionally_correct = cluster_center > current_price
            else:  # support
                is_directionally_correct = cluster_center < current_price
            
            logger.cluster(f"🔍 {zone_type.title()} Cluster {label} Analysis:")
            logger.debug(f"   - Center: {cluster_center:.4f}")
            logger.debug(f"   - Points: {len(cluster_points)}")
            # logger.debug(f"   - Point values: {[f'{p:.4f}' for p in cluster_points]}")
            logger.debug(f"   - Strength: {cluster_strength:.2f}")
            logger.debug(f"   - High volume touches: {high_volume_touches}")
            logger.debug(f"   - Price distance: {price_distance:.2%}")
            logger.debug(f"   - Price relevant: {is_price_relevant}")
            logger.debug(f"   - Directionally correct: {is_directionally_correct}")
            
            # Apply filters
            if (cluster_strength >= params.min_cluster_strength and 
                # high_volume_touches >= 1 and
                len(cluster_points) >= params.min_touches_per_zone and
                is_price_relevant and
                is_directionally_correct):
                
                # Calculate zone score for ranking
                zone_score = cluster_strength * (1 + volume_factor) * (1 - price_distance)
                zones.append(cluster_center)
                zone_scores.append(zone_score)
                
                logger.filter_pass(f"   ✅ {zone_type.title()} Cluster {label} PASSED filters")
                if zone_type == 'resistance':
                    logger.resistance(f"      → Added as {zone_type.upper()}: {cluster_center:.4f} (score: {zone_score:.2f})")
                else:
                    logger.support(f"      → Added as {zone_type.upper()}: {cluster_center:.4f} (score: {zone_score:.2f})")
            else:
                logger.filter_fail(f"   ❌ {zone_type.title()} Cluster {label} FAILED filters")
        
        # Sort zones by score and limit to max_zones_per_type
        # if zones and len(zones) > params.max_zones_per_type:
        #     sorted_indices = sorted(range(len(zone_scores)), key=lambda i: zone_scores[i], reverse=True)
        #     zones = [zones[i] for i in sorted_indices[:params.max_zones_per_type]]
        #     zone_scores = [zone_scores[i] for i in sorted_indices[:params.max_zones_per_type]]
            
        #     logger.debug(f"   Limited to top {params.max_zones_per_type} zones by score")
        
        # Step 2: Apply DBSCAN noise filtering to consolidate nearby zones
        # if zones and len(zones) > 1 and params.enable_dbscan_filtering:
        #     logger.cluster(f"🔧 Applying DBSCAN noise filtering to {len(zones)} agglomerative zones")
        #     zones = apply_dbscan_noise_filtering(zones, zone_type, eps_ratio=params.dbscan_eps_ratio, min_samples=params.dbscan_min_samples)
        
        return zones



    def _bullish_reversal(self, data) -> str:
        """Detects bullish reversal candlestick patterns."""
        if len(data) < 3:
            return

        o1, c1 = data.open.iloc[-1], data.close.iloc[-1]
        o2, c2, h2, l2 = data.open.iloc[-2], data.close.iloc[-2], data.high.iloc[-2], data.low.iloc[-2]
        o3, c3 = data.open.iloc[-3], data.close.iloc[-3]

        # ---- PATTERNS ----
        # 1. Bullish Engulfing
        engulfing = (c2 < o2) and (c1 > o1) and (o1 <= c2) and (c1 >= o2)

        # 2. Morning Star (three-candle pattern)
        # red -> small -> strong green closing into 1st candle’s body
        small_candle = abs(c2 - o2) < (abs(c3 - o3) * 0.5)
        morning_star = (
            (c3 < o3) and
            small_candle and
            (c1 > o1) and
            (c1 > (o3 + c3) / 2) and
            (o2 < c3)  # small gap down improves reliability
        )

        # 3. Doji + Bullish confirmation
        doji = abs(c2 - o2) <= (h2 - l2) * 0.1
        doji_reversal = doji and c1 > o1 and c1 > o2

        if engulfing:
            return 'engulfing' 
        if morning_star:
            return 'morning_star'
        if doji_reversal:
            return 'doji_reversal'

        return None


    def _bearish_reversal(self, data) -> str:
        """Detects bearish reversal candlestick patterns."""
        if len(data) < 3:
            return
        
        o1, c1, h1, l1 = data.open.iloc[-1], data.close.iloc[-1], data.high.iloc[-1], data.low.iloc[-1]
        o2, c2, h2, l2 = data.open.iloc[-2], data.close.iloc[-2], data.high.iloc[-2], data.low.iloc[-2]
        o3, c3 = data.open.iloc[-3], data.close.iloc[-3]

        body = abs(c1 - o1)
        upper_wick = h1 - max(o1, c1)
        lower_wick = min(o1, c1) - l1

        # ---- PATTERNS ----
        # 1. Bearish Engulfing
        engulfing = c2 > o2 and c1 < o1 and c1 <= o2 and o1 >= c2

        # 2. Shooting Star (long upper wick, small body)
        shooting_star =  upper_wick > body * 2 and lower_wick < body

        # 3. Evening Star (green -> small -> strong red)
        small_candle = abs(c2 - o2) < (abs(c3 - o3) * 0.5)
        evening_star = (
            c3 > o3 and
            small_candle and
            c1 < o1 and
            c1 < ((o3 + c3) / 2) and
            o2 > c3  # small gap up
        )

        # 4. Doji + Bearish confirmation
        doji = abs(c2 - o2) <= (h2 - l2) * 0.1
        doji_reversal = doji and c1 < o1 and c1 < o2


        if engulfing:
            return 'engulfing' 
        if shooting_star:
            return 'shooting_star'
        if evening_star:
            return 'evening_star'
        if doji_reversal:
            return 'doji_reversal'


        return None


class Agglo_ETHERIUM(AggloBase):
    """
    A strategy based on the Price Reversal
    """
    def __init__(self, data: pd.DataFrame):
        super().__init__(data)
        self.persistence = AggloBase.PersistencePlugin(redis_client=self.redis_client, class_name=self.__class__.__name__)
        logger.info("Agglo_ETHERIUM initialized")

    def generate_signals(self, symbol=None):
        """
        Implement the Reversal logic to generate signals.

        Returns:
            BUY, SELL OR NONE
        """

        swing_lookback = 30
        swing_lookback_window = 5
        tol = 0.5
        rr = 4
        atr_period = 14
        sl_atr_mul = 3
        momentum_drop = 4


        params =  AggloBase.ParameterConfig()
        params.evalution_tolerance_factor = tol
        params.rejection_atr_mul = 1.5
        params.swing_lookback = swing_lookback
        params.swing_window = swing_lookback_window
        params.momentum_drop = momentum_drop

        lookback_df = self.data.iloc[-params.lookback_period:].copy()
        current_idx = len(self.data) - 1
    
        self.zones = self.SR_levls_implementation(self.data, self.persistence, params, window_end=current_idx)
        self.send_levels_info(data=None, description=f"Symbol = ETHERIUM", fields=self.zones)

        open_ = lookback_df['open'].iloc[-1]
        close = round(lookback_df['close'].iloc[-1], 1)
        high = lookback_df['high'].iloc[-1]
        low = lookback_df['low'].iloc[-1]

        atr = self.calculate_atr(data=lookback_df, period=atr_period).iloc[-1]
        tol = atr * tol
        extra_params = {
            'atr': atr,
            'region_tolerance': tol,
        }
        self.send_params(stock_df=lookback_df, symbol=symbol, duration="15 MIN", **extra_params)

        for zone in self.zones["resistance"]:

            bearish_reversal = self._bearish_reversal(lookback_df)

            if (low <= zone + tol) and (high >= zone - tol) and bearish_reversal:
                # atr_stop = close +  atr * self.p.sl_atr_mult
                stop = self.find_swing_high(lookback_df.iloc[-swing_lookback:], swing_lookback_window) + atr * sl_atr_mul
                stop = max(stop, zone)
                risk = stop - close 
                target = close - risk * rr
                self.trade_type = f'Resistance reversal {bearish_reversal}'
                chart_path_raw = generate_chart(lookback_df)
                return {
                    "signal": "SELL",
                    "entry_price": close,
                    "stop_loss": stop,
                    "take_profit": target,
                    "chart_path": None,
                    "chart_path_raw": chart_path_raw,
                    "pattern": self.trade_type
                }
             
            if zone >= open_ and zone <= close:
                stop = self.find_swing_low(lookback_df.iloc[-swing_lookback:], swing_lookback_window)                
                risk = close - stop 
                target = close + risk * rr
                self.trade_type = 'Resistance breakout'
                chart_path_raw = generate_chart(lookback_df)
                return {
                    "signal": "BUY",
                    "entry_price": close,
                    "stop_loss": stop,
                    "take_profit": target,
                    "chart_path": None,
                    "chart_path_raw": chart_path_raw,
                    "pattern": self.trade_type
                }          
                  
        for zone in self.zones["support"]:

            bullish_reversal = self._bullish_reversal(lookback_df)

            if (low <= zone + tol) and (high >= zone - tol) and bullish_reversal:
                stop = self.find_swing_low(lookback_df.iloc[-swing_lookback:], swing_lookback_window) - atr * sl_atr_mul      
                stop = min(stop, zone)
                risk = close - stop 
                target = close + risk * rr
                self.trade_type = f'Support Reversal {bullish_reversal}'
                chart_path_raw = generate_chart(lookback_df)
                return {
                    "signal": "BUY",
                    "entry_price": close,
                    "stop_loss": stop,
                    "take_profit": target,
                    "chart_path": None,
                    "chart_path_raw": chart_path_raw,
                    "pattern": self.trade_type
                }
    
            if zone <= open_ and zone >= close:
                # atr_stop = close +  atr * self.p.sl_atr_mult
                stop = self.find_swing_high(lookback_df.iloc[-swing_lookback:], swing_lookback_window)
                risk = stop - close 
                target = close - risk * rr
                self.trade_type = 'Support breakout'
                chart_path_raw = generate_chart(lookback_df)
                return {
                    "signal": "SELL",
                    "entry_price": close,
                    "stop_loss": stop,
                    "take_profit": target,
                    "chart_path": None,
                    "chart_path_raw": chart_path_raw,
                    "pattern": self.trade_type
                }
            


        return None
    
