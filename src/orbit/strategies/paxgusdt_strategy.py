import pandas as pd
from orbit.strategies.strategies_base import Strategy
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger("Orbit")

class PAXGUSDTStrategy(Strategy):
    """
    Donchian Breakout strategy for PAXGUSDT on 15m.
    """
    
    def __init__(self, data: pd.DataFrame, symbol="PAXGUSDT"):
        super().__init__(data)
        self.symbol = symbol
        self.lookback = 48 # 12 hours
        self.atr_period = 14
        self.atr_multiplier_sl = 2.0
        self.atr_multiplier_tp = 6.0 

    def generate_signals(self, symbol=None, position_side=None) -> Optional[Dict[str, Any]]:
        df = self.data.copy()
        
        # Explicitly verify the last candle's close time to ensure we don't trade on an active candle
        import time
        last_time = df.index[-1]
        
        if isinstance(last_time, pd.Timestamp):
            last_ts_sec = last_time.timestamp()
        elif isinstance(last_time, (int, float)):
            last_ts_sec = last_time / 1000 if last_time > 1e11 else last_time
        else:
            last_ts_sec = 0
            
        if time.time() < last_ts_sec + 15 * 60:
            df = df.iloc[:-1]
            
        if len(df) < self.lookback:
            return None
        
        highest = df['high'].rolling(window=self.lookback).max()
        lowest = df['low'].rolling(window=self.lookback).min()
        
        atr = self.compute_atr(df, period=self.atr_period)
        
        current_close = df['close'].iloc[-1]
        current_atr = atr.iloc[-1]
        
        prev_close = df['close'].iloc[-2]
        
        # We need the highest high of the *previous* N bars to avoid comparing with the current bar's high
        prev_highest = highest.iloc[-2]
        prev_lowest = lowest.iloc[-2]
        
        if not symbol:
            symbol = self.symbol
            
        if position_side:
            return None

        long_signal = current_close > prev_highest
        short_signal = current_close < prev_lowest
        
        if long_signal:
            return {
                "signal": "BUY",
                "entry_price": current_close,
                "stop_loss": current_close - (current_atr * self.atr_multiplier_sl),
                "take_profit": current_close + (current_atr * self.atr_multiplier_tp),
                "pattern": "PAXGUSDT Donchian Breakout"
            }
            
        if short_signal:
            return {
                "signal": "SELL",
                "entry_price": current_close,
                "stop_loss": current_close + (current_atr * self.atr_multiplier_sl),
                "take_profit": current_close - (current_atr * self.atr_multiplier_tp),
                "pattern": "PAXGUSDT Donchian Breakout"
            }
            
        return None
