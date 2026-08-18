import pandas as pd
from orbit.strategies.strategies_base import Strategy
from orbit.utils.utils import generate_chart
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger("Orbit")

class XAUUSDTStrategy(Strategy):
    """
    Intraday strategy for XAUUSDT (Gold) on 15m timeframe.
    - 200 EMA for trend direction
    - RSI for momentum/overbought/oversold
    - ATR for volatility-adjusted stop loss
    """
    
    def __init__(self, data: pd.DataFrame, symbol="XAUUSDT"):
        super().__init__(data)
        self.symbol = symbol
        self.ema_period = 200
        self.rsi_period = 14
        self.atr_period = 14
        self.atr_multiplier_sl = 1.5
        self.atr_multiplier_tp = 3.0 # 1:2 Risk-Reward

    @staticmethod
    def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def generate_signals(self, symbol=None, position_side=None) -> Optional[Dict[str, Any]]:
        if len(self.data) < self.ema_period:
            return None
        
        df = self.data.copy()
        
        # Calculate indicators
        ema_200 = self.compute_ema(df['close'], period=self.ema_period)
        rsi = self.compute_rsi(df['close'], period=self.rsi_period)
        atr = self.compute_atr(df, period=self.atr_period)
        
        current_close = df['close'].iloc[-1]
        current_ema = ema_200.iloc[-1]
        current_rsi = rsi.iloc[-1]
        prev_rsi = rsi.iloc[-2]
        current_atr = atr.iloc[-1]
        
        if not symbol:
            symbol = self.symbol
            
        if position_side:
            return None

        # Trend filter
        uptrend = current_close > current_ema
        downtrend = current_close < current_ema
        
        # Entry logic: 
        # Long: Uptrend, RSI was oversold (<30) and is crossing back up, or just hit <30
        long_signal = uptrend and prev_rsi < 30 and current_rsi >= 30
        
        # Short: Downtrend, RSI was overbought (>70) and is crossing back down
        short_signal = downtrend and prev_rsi > 70 and current_rsi <= 70
        
        if long_signal:
            stop_loss = current_close - (current_atr * self.atr_multiplier_sl)
            take_profit = current_close + (current_atr * self.atr_multiplier_tp)
            logger.info(f"Generated LONG signal for {symbol} at {current_close}")
            return {
                "signal": "BUY",
                "entry_price": current_close,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "chart_path_raw": None,
                "pattern": "XAUUSDT Intraday RSI Reversal"
            }
            
        if short_signal:
            stop_loss = current_close + (current_atr * self.atr_multiplier_sl)
            take_profit = current_close - (current_atr * self.atr_multiplier_tp)
            logger.info(f"Generated SHORT signal for {symbol} at {current_close}")
            return {
                "signal": "SELL",
                "entry_price": current_close,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "chart_path_raw": None,
                "pattern": "XAUUSDT Intraday RSI Reversal"
            }
            
        return None
