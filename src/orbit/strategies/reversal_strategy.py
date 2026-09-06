import pandas as pd

from orbit.strategies.strategies_base import Strategy
from orbit.utils.utils import generate_chart


class BollingerAdaptiveReversalStrategyBCH(Strategy):
    """
    Bollinger Band Reversal Strategy for BCH based on backtesting results.
    Uses Bollinger Bands with reversal patterns (engulfing, hammer, shooting star).
    """

    tp_rr = 2.0

    
    def __init__(self, data: pd.DataFrame, 
                 bb_period=20, bb_devfactor=3.0, sma_period=20, sl_pct=0.015):
        super().__init__(data)
        self.bb_period = bb_period
        self.bb_devfactor = bb_devfactor
        self.sma_period = sma_period
        self.sl_pct = sl_pct

    def compute_bollinger_bands(self, close_series):
        """Compute Bollinger Bands"""
        sma = close_series.rolling(window=self.bb_period).mean()
        std = close_series.rolling(window=self.bb_period).std()
        upper_band = sma + (self.bb_devfactor * std)
        middle_band = sma
        lower_band = sma - (self.bb_devfactor * std)
        return upper_band, middle_band, lower_band

    def compute_sma(self, close_series):
        """Compute Simple Moving Average"""
        return close_series.rolling(window=self.sma_period).mean()

    def is_bullish_reversal(self, df):
        """Detect bullish reversal patterns"""
        if len(df) < 2:
            return False
            
        close = df['close'].iloc[-1]
        open_ = df['open'].iloc[-1]
        low = df['low'].iloc[-1]
        prev_close = df['close'].iloc[-2]
        prev_open = df['open'].iloc[-2]

        # Bullish Engulfing: current candle engulfs previous red candle
        bullish_engulfing = (prev_close < prev_open) and (close > open_) and (close > prev_open) and (open_ < prev_close)

        # Hammer: small body, long lower shadow
        body = abs(close - open_)
        shadow = low < min(close, open_)
        hammer = shadow and ((min(close, open_) - low) > 2 * body)

        return bullish_engulfing or hammer

    def is_bearish_reversal(self, df):
        """Detect bearish reversal patterns"""
        if len(df) < 2:
            return False
            
        close = df['close'].iloc[-1]
        open_ = df['open'].iloc[-1]
        high = df['high'].iloc[-1]

        prev_close = df['close'].iloc[-2]
        prev_open = df['open'].iloc[-2]

        # Bearish Engulfing: current candle engulfs previous green candle
        bearish_engulfing = (prev_close > prev_open) and (close < open_) and (close < prev_open) and (open_ > prev_close)

        # Shooting Star: small body, long upper shadow
        body = abs(close - open_)
        shadow = high > max(close, open_)
        shooting_star = shadow and ((high - max(close, open_)) > 2 * body)

        return bearish_engulfing or shooting_star

    def generate_signals(self, symbol=None):
        """
        Generate trading signals based on Bollinger Band reversals.
        
        Returns:
            Signal dict with entry_price, stop_loss, take_profit, or None
        """
        lookup = 168
        df_15min = self.data.iloc[-lookup:]

        close = df_15min['close']
        current_close = close.iloc[-1]
        prev_close = close.iloc[-2] if len(close) > 1 else current_close

        # Calculate indicators
        bb_upper, bb_middle, bb_lower = self.compute_bollinger_bands(close)
        sma = self.compute_sma(close)

        # Check if we have enough data for indicators
        if bb_upper.iloc[-1] is None or bb_lower.iloc[-1] is None or sma.iloc[-1] is None:
            return None

        current_bb_upper = bb_upper.iloc[-1]
        current_bb_lower = bb_lower.iloc[-1]
        prev_bb_upper = bb_upper.iloc[-2] if len(bb_upper) > 1 else current_bb_upper
        prev_bb_lower = bb_lower.iloc[-2] if len(bb_lower) > 1 else current_bb_lower

        # LONG Entry: previous candle closed below lower band and current is bullish reversal
        if (prev_close < prev_bb_lower and 
            current_close > current_bb_lower and 
            self.is_bullish_reversal(df_15min)):
            
            entry_price = current_close
            stop_loss = entry_price * (1 - self.sl_pct)
            take_profit = entry_price + self.tp_rr * (entry_price - stop_loss)
            
            chart_path_raw = generate_chart(df_15min)
            return {
                "signal": "BUY",
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "pattern": "Bollinger Band Bullish Reversal",
                "chart_path": None,
                "chart_path_raw": chart_path_raw
            }

        # SHORT Entry: previous candle closed above upper band and current is bearish reversal
        elif (prev_close > prev_bb_upper and 
              current_close < current_bb_upper and 
              self.is_bearish_reversal(df_15min)):
            
            entry_price = current_close
            stop_loss = entry_price * (1 + self.sl_pct)
            take_profit = entry_price - self.tp_rr * (stop_loss - entry_price)

            chart_path_raw = generate_chart(df_15min)
            return {
                "signal": "SELL",
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "pattern": "Bollinger Band Bearish Reversal",
                "chart_path": None,
                "chart_path_raw": chart_path_raw
            }

        return None
