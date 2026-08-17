import pandas as pd
import numpy as np
from dataclasses import dataclass
from orbit.strategies.strategies_base import Strategy

@dataclass
class ETHStrategy(Strategy):
    """
    EMA Confluence with RSI and MACD Filter Strategy for Ethereum.
    
    This strategy combines trend-following with momentum confirmation and mean-reversion filters.
    
    Indicators:
    1. EMA Trend Filter: EMA(21) and EMA(55) for trend direction, EMA(200) for macro trend
    2. RSI (14): Momentum filter - avoid overbought/oversold entries
    3. MACD (12,26,9): Momentum confirmation via histogram direction
    4. ATR (14): For dynamic stop-loss and take-profit placement
    5. Volume Filter: Current volume > 1.2x average volume over 20 periods
    """

    def __init__(self, data: pd.DataFrame):
        super().__init__(data)
        
    def _calculate_rsi(self, period: int = 14) -> pd.Series:
        delta = self.data['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        
        # Calculate RSI using exponential moving average (Wilder's Smoothing)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
        
    def _calculate_macd(self) -> tuple[pd.Series, pd.Series, pd.Series]:
        ema_12 = self.compute_ema(self.data['close'], 12)
        ema_26 = self.compute_ema(self.data['close'], 26)
        macd = ema_12 - ema_26
        signal = self.compute_ema(macd, 9)
        hist = macd - signal
        return macd, signal, hist

    def generate_signals(self, symbol=None):
        df = self.data.copy()
        
        if len(df) < 200:
            return None
            
        # Calculate Indicators
        df['ema_21'] = self.compute_ema(df['close'], 21)
        df['ema_55'] = self.compute_ema(df['close'], 55)
        df['ema_200'] = self.compute_ema(df['close'], 200)
        df['rsi'] = self._calculate_rsi(14)
        _, _, df['macd_hist'] = self._calculate_macd()
        df['atr'] = self.compute_atr(df, 14)
        df['vol_sma_20'] = df['volume'].rolling(window=20).mean()
        
        current = df.iloc[-1]
        
        # Volume Filter Check
        if current['volume'] <= 1.2 * current['vol_sma_20']:
            return None
            
        # MACD Histogram Direction
        hist_positive = current['macd_hist'] > 0 or current['macd_hist'] > df['macd_hist'].iloc[-2]
        hist_negative = current['macd_hist'] < 0 or current['macd_hist'] < df['macd_hist'].iloc[-2]
        
        # EMA Crossover
        df['ema_cross_up'] = (df['ema_21'] > df['ema_55']) & (df['ema_21'].shift(1) <= df['ema_55'].shift(1))
        df['ema_cross_down'] = (df['ema_21'] < df['ema_55']) & (df['ema_21'].shift(1) >= df['ema_55'].shift(1))
        
        recent_cross_up = df['ema_cross_up'].iloc[-3:].any()
        recent_cross_down = df['ema_cross_down'].iloc[-3:].any()
        
        # EMA Pullback
        pullback_up = (current['ema_21'] > current['ema_55']) and \
                      (abs(current['close'] - current['ema_21']) <= 0.5 * current['atr'])
        pullback_down = (current['ema_21'] < current['ema_55']) and \
                        (abs(current['close'] - current['ema_21']) <= 0.5 * current['atr'])

        # BUY Signal Rules
        buy_cond1 = current['close'] > current['ema_200']
        buy_cond2 = recent_cross_up or pullback_up
        buy_cond3 = 40 <= current['rsi'] <= 65
        buy_cond4 = hist_positive
        
        # SELL Signal Rules
        sell_cond1 = current['close'] < current['ema_200']
        sell_cond2 = recent_cross_down or pullback_down
        sell_cond3 = 35 <= current['rsi'] <= 60
        sell_cond4 = hist_negative
        
        if buy_cond1 and buy_cond2 and buy_cond3 and buy_cond4:
            pattern = f"EMA crossover/pullback bullish + RSI {current['rsi']:.1f} + MACD hist pos"
            entry = current['close']
            return {
                "signal": "BUY",
                "stop_loss": float(entry - 2.0 * current['atr']),
                "take_profit": float(entry + 3.0 * current['atr']),
                "pattern": pattern
            }
            
        if sell_cond1 and sell_cond2 and sell_cond3 and sell_cond4:
            pattern = f"EMA crossover/pullback bearish + RSI {current['rsi']:.1f} + MACD hist neg"
            entry = current['close']
            return {
                "signal": "SELL",
                "stop_loss": float(entry + 2.0 * current['atr']),
                "take_profit": float(entry - 3.0 * current['atr']),
                "pattern": pattern
            }
            
        return None
