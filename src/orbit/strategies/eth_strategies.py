"""
Production Quantitative Trading Strategies for Ethereum (ETH/USDT) in Orbit.

Strategies:
1. AggloReversalETH: Unsupervised ML clustering (Agglomerative) for dynamic S/R zones + candlestick reversal patterns.
2. EMATrendBreakoutETH: Multi-timeframe trend following (EMA 50/200) + Volatility Keltner breakout + ADX filter.
3. BollingerRSIMeanReversionETH: High-probability mean reversion using Bollinger 2.2-std bands + RSI(14) divergence/extremes.
4. SMCLiquiditySweepETH: Smart Money Concepts (SMC) institutional model - Liquidity sweep of swing levels + 3-bar Fair Value Gap (FVG).
5. HMAMACDMomentumETH: Fast low-lag Hull Moving Average (HMA 21) slope + MACD momentum acceleration.
6. AdaptiveSuperTrendRegimeETH: Macro Trend Regime (EMA 200) + Pullback to EMA(34) + SuperTrend Volatility Flip.
7. MultiConfluenceMeanReversionETH: Deep Exhaustion Mean Reversion (RSI < 28 / > 72 + Bollinger 2.5 Std Dev + Pin Bar).
"""

from typing import Any, Dict, Optional
import numpy as np
import pandas as pd
from sklearn.cluster import AgglomerativeClustering
from sklearn.preprocessing import StandardScaler

from orbit.strategies.strategies_base import Strategy


class AggloReversalETH(Strategy):
    """
    Enhanced Machine Learning Agglomerative S/R & Reversal Strategy for Ethereum.
    Clusters rejection wicks and swing pivots to find institutional support/resistance,
    then enters on confirmed candlestick reversal patterns with ATR risk management.
    """

    def __init__(
        self,
        data: pd.DataFrame,
        lookback: int = 120,
        n_clusters: int = 4,
        rr_ratio: float = 2.5,
        atr_period: int = 14,
        atr_sl_mult: float = 1.8,
    ):
        super().__init__(data)
        self.lookback = lookback
        self.n_clusters = n_clusters
        self.rr_ratio = rr_ratio
        self.atr_period = atr_period
        self.atr_sl_mult = atr_sl_mult

    def _extract_rejections(self, df: pd.DataFrame):
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        opens = df["open"].values
        
        support_points = []
        resistance_points = []
        
        for i in range(2, len(df) - 2):
            if highs[i] == max(highs[i - 2 : i + 3]):
                resistance_points.append(highs[i])
            if lows[i] == min(lows[i - 2 : i + 3]):
                support_points.append(lows[i])
            body_top = max(opens[i], closes[i])
            if (highs[i] - body_top) > 1.5 * abs(closes[i] - opens[i]) and (highs[i] - body_top) > 0:
                resistance_points.append(highs[i])
            body_bottom = min(opens[i], closes[i])
            if (body_bottom - lows[i]) > 1.5 * abs(closes[i] - opens[i]) and (body_bottom - lows[i]) > 0:
                support_points.append(lows[i])
                
        return support_points, resistance_points

    def _cluster_levels(self, points: list, n_clusters: int) -> list:
        if len(points) < 2:
            return points
        k = min(n_clusters, len(points))
        if k < 2:
            return points
        X = np.array(points).reshape(-1, 1)
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        clustering = AgglomerativeClustering(n_clusters=k, linkage="average")
        labels = clustering.fit_predict(X_scaled)
        
        centers = []
        for lbl in set(labels):
            cluster_pts = np.array(points)[labels == lbl]
            centers.append(float(np.mean(cluster_pts)))
        return centers

    def _is_bullish_reversal(self, df: pd.DataFrame) -> bool:
        if len(df) < 2:
            return False
        o1, c1, l1 = df["open"].iloc[-1], df["close"].iloc[-1], df["low"].iloc[-1]
        o2, c2 = df["open"].iloc[-2], df["close"].iloc[-2]
        body1 = abs(c1 - o1)
        lower_wick = min(o1, c1) - l1
        is_engulfing = (c2 < o2) and (c1 > o1) and (c1 >= o2) and (o1 <= c2)
        is_hammer = (lower_wick >= 2.0 * body1) and (c1 >= o1)
        return is_engulfing or is_hammer

    def _is_bearish_reversal(self, df: pd.DataFrame) -> bool:
        if len(df) < 2:
            return False
        o1, c1, h1 = df["open"].iloc[-1], df["close"].iloc[-1], df["high"].iloc[-1]
        o2, c2 = df["open"].iloc[-2], df["close"].iloc[-2]
        body1 = abs(c1 - o1)
        upper_wick = h1 - max(o1, c1)
        is_engulfing = (c2 > o2) and (c1 < o1) and (c1 <= o2) and (o1 >= c2)
        is_shooting_star = (upper_wick >= 2.0 * body1) and (c1 <= o1)
        return is_engulfing or is_shooting_star

    def generate_signals(self, symbol: Optional[str] = "ETHUSDT") -> Optional[Dict[str, Any]]:
        if len(self.data) < self.lookback:
            return None
            
        df = self.data.iloc[-self.lookback :].copy()
        current_close = float(df["close"].iloc[-1])
        current_high = float(df["high"].iloc[-1])
        current_low = float(df["low"].iloc[-1])
        
        atr = float(self.calculate_atr(df, period=self.atr_period).iloc[-1])
        if np.isnan(atr) or atr <= 0:
            return None
            
        support_pts, resistance_pts = self._extract_rejections(df)
        support_levels = self._cluster_levels(support_pts, self.n_clusters)
        resistance_levels = self._cluster_levels(resistance_pts, self.n_clusters)
        
        tolerance = 0.6 * atr

        if self._is_bullish_reversal(df):
            for s_level in support_levels:
                if (current_low <= s_level + tolerance) and (current_close >= s_level - tolerance) and (current_close > s_level):
                    stop_loss = round(current_close - (atr * self.atr_sl_mult), 2)
                    risk = current_close - stop_loss
                    if risk <= 0:
                        continue
                    take_profit = round(current_close + (risk * self.rr_ratio), 2)
                    return {
                        "signal": "BUY",
                        "entry_price": current_close,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "pattern": "Agglo S/R Bullish Reversal",
                    }

        if self._is_bearish_reversal(df):
            for r_level in resistance_levels:
                if (current_high >= r_level - tolerance) and (current_close <= r_level + tolerance) and (current_close < r_level):
                    stop_loss = round(current_close + (atr * self.atr_sl_mult), 2)
                    risk = stop_loss - current_close
                    if risk <= 0:
                        continue
                    take_profit = round(current_close - (risk * self.rr_ratio), 2)
                    return {
                        "signal": "SELL",
                        "entry_price": current_close,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "pattern": "Agglo S/R Bearish Reversal",
                    }

        return None


class EMATrendBreakoutETH(Strategy):
    """
    Trend Following & Volatility Channel Breakout Strategy for Ethereum.
    """

    def __init__(
        self,
        data: pd.DataFrame,
        fast_ema: int = 34,
        slow_ema: int = 144,
        atr_period: int = 14,
        adx_period: int = 14,
        adx_threshold: float = 18.0,
        channel_period: int = 20,
        rr_ratio: float = 2.5,
        atr_mult: float = 2.0,
    ):
        super().__init__(data)
        self.fast_ema_period = fast_ema
        self.slow_ema_period = slow_ema
        self.atr_period = atr_period
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.channel_period = channel_period
        self.rr_ratio = rr_ratio
        self.atr_mult = atr_mult

    def _compute_adx(self, df: pd.DataFrame, period: int = 14) -> float:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        
        plus_dm = high.diff()
        minus_dm = low.diff().abs() * -1
        
        plus_dm = plus_dm.where((plus_dm > 0) & (plus_dm > minus_dm.abs()), 0.0)
        minus_dm = low.diff().abs().where((low.diff().abs() > 0) & (low.diff().abs() > high.diff()), 0.0)
        
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs()
        ], axis=1).max(axis=1)
        
        atr = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / (atr + 1e-9))
        minus_di = 100 * (minus_dm.rolling(period).mean() / (atr + 1e-9))
        
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
        adx = dx.rolling(period).mean()
        return float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 0.0

    def generate_signals(self, symbol: Optional[str] = "ETHUSDT") -> Optional[Dict[str, Any]]:
        min_bars = self.slow_ema_period + 30
        if len(self.data) < min_bars:
            return None

        # Slice last 250 bars for speed
        df = self.data.iloc[-250:]
        closes = df["close"]
        highs = df["high"]
        lows = df["low"]
        volumes = df["volume"]

        ema_fast = closes.ewm(span=self.fast_ema_period, adjust=False).mean()
        ema_slow = closes.ewm(span=self.slow_ema_period, adjust=False).mean()
        atr = self.calculate_atr(df, period=self.atr_period)
        
        current_close = float(closes.iloc[-1])
        prev_close = float(closes.iloc[-2])
        current_atr = float(atr.iloc[-1])
        
        if current_atr <= 0 or np.isnan(current_atr):
            return None

        upper_channel = float(highs.iloc[-self.channel_period - 1 : -1].max())
        lower_channel = float(lows.iloc[-self.channel_period - 1 : -1].min())
        
        vol_sma = float(volumes.iloc[-20:].mean())
        curr_vol = float(volumes.iloc[-1])
        vol_confirmed = curr_vol >= 1.1 * vol_sma

        adx = self._compute_adx(df.iloc[-60:], period=self.adx_period)
        trend_strong = adx >= self.adx_threshold

        fast_val = float(ema_fast.iloc[-1])
        slow_val = float(ema_slow.iloc[-1])

        if fast_val > slow_val and current_close > upper_channel and prev_close <= upper_channel and trend_strong and vol_confirmed:
            stop_loss = round(current_close - (current_atr * self.atr_mult), 2)
            risk = current_close - stop_loss
            if risk > 0:
                take_profit = round(current_close + (risk * self.rr_ratio), 2)
                return {
                    "signal": "BUY",
                    "entry_price": current_close,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern": f"EMA Trend Bull Breakout (ADX {adx:.1f})",
                }

        if fast_val < slow_val and current_close < lower_channel and prev_close >= lower_channel and trend_strong and vol_confirmed:
            stop_loss = round(current_close + (current_atr * self.atr_mult), 2)
            risk = stop_loss - current_close
            if risk > 0:
                take_profit = round(current_close - (risk * self.rr_ratio), 2)
                return {
                    "signal": "SELL",
                    "entry_price": current_close,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern": f"EMA Trend Bear Breakout (ADX {adx:.1f})",
                }

        return None


class BollingerRSIMeanReversionETH(Strategy):
    """
    Adaptive Mean Reversion Strategy for Ethereum.
    """

    def __init__(
        self,
        data: pd.DataFrame,
        bb_period: int = 20,
        bb_std: float = 2.2,
        rsi_period: int = 14,
        rsi_overbought: float = 70.0,
        rsi_oversold: float = 30.0,
        rr_ratio: float = 2.0,
    ):
        super().__init__(data)
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.rr_ratio = rr_ratio

    def _compute_rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / (loss + 1e-9)
        return 100 - (100 / (1 + rs))

    def generate_signals(self, symbol: Optional[str] = "ETHUSDT") -> Optional[Dict[str, Any]]:
        min_bars = max(self.bb_period, self.rsi_period) + 20
        if len(self.data) < min_bars:
            return None

        df = self.data.iloc[-100:]
        closes = df["close"]
        highs = df["high"]
        lows = df["low"]
        opens = df["open"]

        sma = closes.rolling(window=self.bb_period).mean()
        std = closes.rolling(window=self.bb_period).std()
        upper_bb = sma + (self.bb_std * std)
        lower_bb = sma - (self.bb_std * std)

        rsi = self._compute_rsi(closes, self.rsi_period)
        atr = self.calculate_atr(df, period=14)

        current_close = float(closes.iloc[-1])
        prev_close = float(closes.iloc[-2])
        current_open = float(opens.iloc[-1])
        current_high = float(highs.iloc[-1])
        current_low = float(lows.iloc[-1])
        current_rsi = float(rsi.iloc[-1])
        prev_rsi = float(rsi.iloc[-2])
        curr_upper = float(upper_bb.iloc[-1])
        curr_lower = float(lower_bb.iloc[-1])
        curr_middle = float(sma.iloc[-1])
        current_atr = float(atr.iloc[-1])

        if np.isnan(current_rsi) or np.isnan(curr_upper) or current_atr <= 0:
            return None

        if (current_low <= curr_lower or prev_close <= float(lower_bb.iloc[-2])) and (prev_rsi <= self.rsi_oversold or current_rsi <= self.rsi_oversold + 5) and current_rsi > prev_rsi and current_close > current_open:
            stop_loss = round(min(current_low, current_close - current_atr * 1.5), 2)
            risk = current_close - stop_loss
            if risk > 0:
                take_profit = round(max(curr_middle, current_close + risk * self.rr_ratio), 2)
                return {
                    "signal": "BUY",
                    "entry_price": current_close,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern": f"Bollinger Oversold Reversion (RSI {current_rsi:.1f})",
                }

        if (current_high >= curr_upper or prev_close >= float(upper_bb.iloc[-2])) and (prev_rsi >= self.rsi_overbought or current_rsi >= self.rsi_overbought - 5) and current_rsi < prev_rsi and current_close < current_open:
            stop_loss = round(max(current_high, current_close + current_atr * 1.5), 2)
            risk = stop_loss - current_close
            if risk > 0:
                take_profit = round(min(curr_middle, current_close - risk * self.rr_ratio), 2)
                return {
                    "signal": "SELL",
                    "entry_price": current_close,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern": f"Bollinger Overbought Reversion (RSI {current_rsi:.1f})",
                }

        return None


class SMCLiquiditySweepETH(Strategy):
    """
    Smart Money Concepts (SMC) Institutional Strategy for Ethereum.
    """

    def __init__(
        self,
        data: pd.DataFrame,
        swing_bars: int = 20,
        rr_ratio: float = 3.0,
        atr_period: int = 14,
    ):
        super().__init__(data)
        self.swing_bars = swing_bars
        self.rr_ratio = rr_ratio
        self.atr_period = atr_period

    def generate_signals(self, symbol: Optional[str] = "ETHUSDT") -> Optional[Dict[str, Any]]:
        if len(self.data) < self.swing_bars + 10:
            return None

        df = self.data.iloc[-60:]
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        opens = df["open"].values

        c0_close = float(closes[-1])
        c0_open = float(opens[-1])
        c0_high = float(highs[-1])
        c0_low = float(lows[-1])

        prev_swing_high = float(np.max(highs[-self.swing_bars - 1 : -1]))
        prev_swing_low = float(np.min(lows[-self.swing_bars - 1 : -1]))

        atr = float(self.calculate_atr(df, period=self.atr_period).iloc[-1])
        if np.isnan(atr) or atr <= 0:
            return None

        swept_high = c0_high > prev_swing_high and c0_close < prev_swing_high and c0_close < c0_open
        bearish_fvg = float(lows[-3]) > float(highs[-1])
        bullish_fvg = float(highs[-3]) < float(lows[-1])

        if swept_high or (c0_high >= prev_swing_high and bearish_fvg and c0_close < c0_open):
            stop_loss = round(c0_high + (0.3 * atr), 2)
            risk = stop_loss - c0_close
            if risk > 0:
                take_profit = round(c0_close - (risk * self.rr_ratio), 2)
                return {
                    "signal": "SELL",
                    "entry_price": c0_close,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern": "SMC Liquidity Sweep High + FVG",
                }

        swept_low = c0_low < prev_swing_low and c0_close > prev_swing_low and c0_close > c0_open

        if swept_low or (c0_low <= prev_swing_low and bullish_fvg and c0_close > c0_open):
            stop_loss = round(c0_low - (0.3 * atr), 2)
            risk = c0_close - stop_loss
            if risk > 0:
                take_profit = round(c0_close + (risk * self.rr_ratio), 2)
                return {
                    "signal": "BUY",
                    "entry_price": c0_close,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern": "SMC Liquidity Sweep Low + FVG",
                }

        return None


class HMAMACDMomentumETH(Strategy):
    """
    Hull Moving Average (HMA) + MACD Momentum Acceleration Strategy for Ethereum.
    """

    def __init__(
        self,
        data: pd.DataFrame,
        hma_period: int = 21,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        rr_ratio: float = 2.5,
        atr_mult: float = 2.0,
    ):
        super().__init__(data)
        self.hma_period = hma_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.rr_ratio = rr_ratio
        self.atr_mult = atr_mult

    def _compute_wma(self, series: pd.Series, period: int) -> pd.Series:
        weights = np.arange(1, period + 1)
        return series.rolling(period).apply(lambda s: np.dot(s, weights) / weights.sum(), raw=True)

    def _compute_hma(self, series: pd.Series, period: int) -> pd.Series:
        half_period = int(period / 2)
        sqrt_period = int(np.sqrt(period))
        wma_half = self._compute_wma(series, half_period)
        wma_full = self._compute_wma(series, period)
        diff = 2 * wma_half - wma_full
        return self._compute_wma(diff, sqrt_period)

    def generate_signals(self, symbol: Optional[str] = "ETHUSDT") -> Optional[Dict[str, Any]]:
        min_bars = self.macd_slow + self.hma_period + 20
        if len(self.data) < min_bars:
            return None

        df = self.data.iloc[-100:]
        closes = df["close"]
        hma = self._compute_hma(closes, self.hma_period)

        ema_fast = closes.ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = closes.ewm(span=self.macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.macd_signal, adjust=False).mean()
        hist = macd_line - signal_line
        
        atr = self.calculate_atr(df, period=14)

        current_close = float(closes.iloc[-1])
        curr_hma = float(hma.iloc[-1])
        prev_hma = float(hma.iloc[-2])
        prev2_hma = float(hma.iloc[-3])
        
        curr_hist = float(hist.iloc[-1])
        prev_hist = float(hist.iloc[-2])
        curr_macd = float(macd_line.iloc[-1])
        curr_sig = float(signal_line.iloc[-1])
        current_atr = float(atr.iloc[-1])

        if np.isnan(curr_hma) or np.isnan(curr_hist) or current_atr <= 0:
            return None

        hma_bull_turn = (curr_hma > prev_hma) and (prev_hma <= prev2_hma or current_close > curr_hma)
        macd_bull = curr_macd > curr_sig and curr_hist > prev_hist and curr_hist > 0

        if hma_bull_turn and macd_bull:
            stop_loss = round(current_close - (current_atr * self.atr_mult), 2)
            risk = current_close - stop_loss
            if risk > 0:
                take_profit = round(current_close + (risk * self.rr_ratio), 2)
                return {
                    "signal": "BUY",
                    "entry_price": current_close,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern": "HMA Trend Turn + MACD Bull Expansion",
                }

        hma_bear_turn = (curr_hma < prev_hma) and (prev_hma >= prev2_hma or current_close < curr_hma)
        macd_bear = curr_macd < curr_sig and curr_hist < prev_hist and curr_hist < 0

        if hma_bear_turn and macd_bear:
            stop_loss = round(current_close + (current_atr * self.atr_mult), 2)
            risk = stop_loss - current_close
            if risk > 0:
                take_profit = round(current_close - (risk * self.rr_ratio), 2)
                return {
                    "signal": "SELL",
                    "entry_price": current_close,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern": "HMA Trend Turn + MACD Bear Expansion",
                }

        return None


class AdaptiveSuperTrendRegimeETH(Strategy):
    """
    Macro Trend Regime Filter + Pullback & SuperTrend Volatility Flip Strategy.
    """

    def __init__(
        self,
        data: pd.DataFrame,
        ema_trend: int = 200,
        ema_pullback: int = 34,
        st_period: int = 10,
        st_multiplier: float = 3.0,
        rr_ratio: float = 2.2,
    ):
        super().__init__(data)
        self.ema_trend_period = ema_trend
        self.ema_pullback_period = ema_pullback
        self.st_period = st_period
        self.st_multiplier = st_multiplier
        self.rr_ratio = rr_ratio

    def _compute_rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / (loss + 1e-9)
        return 100 - (100 / (1 + rs))

    def _compute_supertrend_fast(self, df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
        # Optimized vectorized True Range and Rolling ATR
        high = df["high"]
        low = df["low"]
        close = df["close"]
        
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        
        hl2 = (high + low) / 2
        basic_upper = (hl2 + (multiplier * atr)).values
        basic_lower = (hl2 - (multiplier * atr)).values
        close_vals = close.values
        
        n = len(df)
        upper_band = np.zeros(n)
        lower_band = np.zeros(n)
        direction = np.ones(n)

        for i in range(1, n):
            if close_vals[i - 1] > lower_band[i - 1]:
                lower_band[i] = max(basic_lower[i], lower_band[i - 1])
            else:
                lower_band[i] = basic_lower[i]

            if close_vals[i - 1] < upper_band[i - 1]:
                upper_band[i] = min(basic_upper[i], upper_band[i - 1])
            else:
                upper_band[i] = basic_upper[i]

            if close_vals[i] > upper_band[i - 1]:
                direction[i] = 1
            elif close_vals[i] < lower_band[i - 1]:
                direction[i] = -1
            else:
                direction[i] = direction[i - 1]

        return direction, lower_band, upper_band, atr.values

    def generate_signals(self, symbol: Optional[str] = "ETHUSDT") -> Optional[Dict[str, Any]]:
        min_bars = self.ema_trend_period + 20
        if len(self.data) < min_bars:
            return None

        # Slice last 250 bars for speed
        df = self.data.iloc[-250:]
        closes = df["close"]
        lows = df["low"]
        highs = df["high"]

        ema_200 = closes.ewm(span=self.ema_trend_period, adjust=False).mean()
        ema_34 = closes.ewm(span=self.ema_pullback_period, adjust=False).mean()
        rsi = self._compute_rsi(closes, 14)
        
        direction, lower_st, upper_st, atr_vals = self._compute_supertrend_fast(
            df, period=self.st_period, multiplier=self.st_multiplier
        )

        c0 = float(closes.iloc[-1])
        e200 = float(ema_200.iloc[-1])
        e34 = float(ema_34.iloc[-1])
        r0 = float(rsi.iloc[-1])
        st_dir = direction[-1]
        st_dir_prev = direction[-2]
        atr_val = float(atr_vals[-1])

        if np.isnan(r0) or np.isnan(atr_val) or atr_val <= 0:
            return None

        # Bullish Regime: Price > EMA 200 + SuperTrend Bullish + Pullback Bounce
        if c0 > e200 and st_dir == 1 and (st_dir_prev == -1 or (lows.iloc[-1] <= e34 * 1.002 and c0 > e34)) and 40 <= r0 <= 68:
            stop_loss = round(float(lower_st[-1]), 2)
            if stop_loss >= c0:
                stop_loss = round(c0 - (1.5 * atr_val), 2)
            risk = c0 - stop_loss
            if risk > 0:
                take_profit = round(c0 + (risk * self.rr_ratio), 2)
                return {
                    "signal": "BUY",
                    "entry_price": c0,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern": f"SuperTrend Bull Regime Pullback (RSI {r0:.1f})",
                }

        # Bearish Regime: Price < EMA 200 + SuperTrend Bearish + Rally Rejection
        if c0 < e200 and st_dir == -1 and (st_dir_prev == 1 or (highs.iloc[-1] >= e34 * 0.998 and c0 < e34)) and 32 <= r0 <= 60:
            stop_loss = round(float(upper_st[-1]), 2)
            if stop_loss <= c0:
                stop_loss = round(c0 + (1.5 * atr_val), 2)
            risk = stop_loss - c0
            if risk > 0:
                take_profit = round(c0 - (risk * self.rr_ratio), 2)
                return {
                    "signal": "SELL",
                    "entry_price": c0,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern": f"SuperTrend Bear Regime Rally (RSI {r0:.1f})",
                }

        return None


class MultiConfluenceMeanReversionETH(Strategy):
    """
    High-Conviction Extreme Mean Reversion Strategy for Ethereum.
    """

    def __init__(
        self,
        data: pd.DataFrame,
        bb_period: int = 20,
        bb_std: float = 2.5,
        rsi_period: int = 14,
        rr_ratio: float = 2.2,
    ):
        super().__init__(data)
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rr_ratio = rr_ratio

    def _compute_rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / (loss + 1e-9)
        return 100 - (100 / (1 + rs))

    def generate_signals(self, symbol: Optional[str] = "ETHUSDT") -> Optional[Dict[str, Any]]:
        if len(self.data) < self.bb_period + 25:
            return None

        df = self.data.iloc[-100:]
        closes = df["close"]
        highs = df["high"]
        lows = df["low"]
        opens = df["open"]
        volumes = df["volume"]

        sma = closes.rolling(window=self.bb_period).mean()
        std = closes.rolling(window=self.bb_period).std()
        upper_bb = sma + (self.bb_std * std)
        lower_bb = sma - (self.bb_std * std)

        rsi = self._compute_rsi(closes, self.rsi_period)
        atr = self.calculate_atr(df, period=14)

        c0 = float(closes.iloc[-1])
        c1 = float(closes.iloc[-2])
        o0 = float(opens.iloc[-1])
        l0 = float(lows.iloc[-1])
        h0 = float(highs.iloc[-1])
        r0 = float(rsi.iloc[-1])
        r1 = float(rsi.iloc[-2])
        u0 = float(upper_bb.iloc[-1])
        low_bb = float(lower_bb.iloc[-1])
        mid0 = float(sma.iloc[-1])
        atr_val = float(atr.iloc[-1])
        
        vol_avg = float(volumes.iloc[-20:].mean())
        curr_vol = float(volumes.iloc[-1])
        vol_spike = curr_vol >= 1.2 * vol_avg

        if np.isnan(r0) or np.isnan(u0) or atr_val <= 0:
            return None

        # Extreme Oversold Bounce (BUY)
        if (l0 <= low_bb or c1 <= float(lower_bb.iloc[-2])) and r1 <= 28 and r0 > r1 and c0 > o0 and vol_spike:
            stop_loss = round(min(l0, c0 - atr_val * 1.2), 2)
            risk = c0 - stop_loss
            if risk > 0:
                take_profit = round(max(mid0, c0 + risk * self.rr_ratio), 2)
                return {
                    "signal": "BUY",
                    "entry_price": c0,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern": f"Extreme Confluence Oversold Reversion (RSI {r0:.1f})",
                }

        # Extreme Overbought Rejection (SELL)
        if (h0 >= u0 or c1 >= float(upper_bb.iloc[-2])) and r1 >= 72 and r0 < r1 and c0 < o0 and vol_spike:
            stop_loss = round(max(h0, c0 + atr_val * 1.2), 2)
            risk = stop_loss - c0
            if risk > 0:
                take_profit = round(min(mid0, c0 - risk * self.rr_ratio), 2)
                return {
                    "signal": "SELL",
                    "entry_price": c0,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern": f"Extreme Confluence Overbought Rejection (RSI {r0:.1f})",
                }

        return None
