import os
import pandas as pd
import redis
from orbit.utils.utils import generate_chart
from orbit.strategies.strategies_base import Strategy
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
import logging

logger = logging.getLogger("Orbit")

@dataclass
class SwingStrategyBTC_V2(Strategy):
    """
    Improved Real-time Swing Strategy for BTC.
    
    Features:
    - EMA200 Trend Filter: Ensures trades are only taken in the direction of the macro trend.
    - ADX(14) Regime Filter: Distinguishes between trending (>20) and ranging markets.
    - RSI(14) Confluence: Ensures entries are not made in already overbought/oversold conditions.
    - Volume Confirmation: Requires a volume spike (>1.2x of 20-period average) for valid breakouts.
    - Pivot Breakout Entries: Based on local structural highs/lows.
    - ATR Structural SL: Uses 1.5x ATR below/above pivot for a safer structural stop loss.
    - RR = 1:2 TP: Targets a risk-reward ratio of 1:2.
    """

    data: pd.DataFrame

    # Strategy parameters
    max_sl_limit: float = 2.0     # %
    atr_mult: float = 1.5         # Updated to 1.5x for structural stops
    tp_rr: float = 2.0            # target = entry + (2 × risk)
    n: int = 10
    symbol: str = "BITCOIN"

    # Runtime state (not constructor args)
    redis_client: Optional[redis.StrictRedis] = field(init=False, default=None)
    last_sw_h: Optional[float] = field(init=False, default=None)
    last_sw_l: Optional[float] = field(init=False, default=None)

    def __post_init__(self):
        # Initialize base Strategy (sets self.data)
        super().__init__(self.data)
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

        self.last_sw_h = None
        self.last_sw_l = None

    @staticmethod
    def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """
        Compute Relative Strength Index (RSI).
        Provides a momentum oscillator to measure the speed and change of price movements.
        """
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).fillna(0)
        loss = (-delta.where(delta < 0, 0)).fillna(0)
        
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def compute_adx(data: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Compute Average Directional Index (ADX).
        Used to quantify trend strength. Values > 20 generally indicate a trending market.
        """
        high = data['high']
        low = data['low']
        close = data['close']
        
        # Calculate +DM and -DM
        plus_dm = pd.Series(0.0, index=data.index)
        minus_dm = pd.Series(0.0, index=data.index)
        
        up_move = high.diff()
        down_move = low.shift(1) - low
        
        plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
        minus_dm[(down_move > up_move) & (down_move > 0)] = down_move
        
        # Calculate True Range
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Wilder's Smoothing
        atr = tr.ewm(alpha=1/period, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr)
        minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr)
        
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.ewm(alpha=1/period, adjust=False).mean()
        
        return adx

    # ---------------------------------------------------------------------
    # Redis helpers
    # ---------------------------------------------------------------------
    def _get_redis_key(self, key_type: str) -> str:
        return f"swing_v2:{key_type}:{self.symbol}:4h"

    def _load_last_pivots(self):
        try:
            ph = self.redis_client.get(self._get_redis_key("pivot_high"))
            pl = self.redis_client.get(self._get_redis_key("pivot_low"))
            return (float(ph) if ph else None, float(pl) if pl else None)
        except:
            return (None, None)

    def _save_pivots(self, ph, pl):
        try:
            if ph is not None:
                self.redis_client.set(self._get_redis_key("pivot_high"), str(ph))
            if pl is not None:
                self.redis_client.set(self._get_redis_key("pivot_low"), str(pl))
        except:
            pass

    # ---------------------------------------------------------------------
    # Pivot detection
    # ---------------------------------------------------------------------
    def pivot_high_centered(self, series, left, right):
        if len(series) < left + right + 1:
            return None
        window = series[-(left+right+1):]
        center = window[left]
        return center if center == max(window) else None

    def pivot_low_centered(self, series, left, right):
        if len(series) < left + right + 1:
            return None
        window = series[-(left+right+1):]
        center = window[left]
        return center if center == min(window) else None

    # ---------------------------------------------------------------------
    # SL / TP
    # ---------------------------------------------------------------------
    def compute_long_sl_tp(self, close):
        fixed_sl = close * (1 - self.max_sl_limit / 100)
        atr_val = float(self.atr.iloc[-1])
        struct_sl = self.last_sw_l - atr_val * self.atr_mult if self.last_sw_l else fixed_sl
        long_sl = max(fixed_sl, struct_sl)

        risk = close - long_sl
        long_tp = close + self.tp_rr * risk

        return long_sl, long_tp

    def compute_short_sl_tp(self, close):
        fixed_sl = close * (1 + self.max_sl_limit / 100)
        atr_val = float(self.atr.iloc[-1])
        struct_sl = self.last_sw_h + atr_val * self.atr_mult if self.last_sw_h else fixed_sl
        short_sl = min(fixed_sl, struct_sl)

        risk = short_sl - close
        short_tp = close - self.tp_rr * risk

        return short_sl, short_tp
    
    # ---------------------------------------------------------------------
    # Pine-style trailing SL/TP update
    # ---------------------------------------------------------------------
    def update_trailing_sl_tp(self, close: float, position_side: str = None) -> Optional[Dict[str, float]]:
        """
        Recompute SL/TP every bar and tighten only.
        """
        stop_loss, take_profit = None, None
        if position_side == "LONG":
            stop_loss, take_profit = self.compute_long_sl_tp(close)
        elif position_side == "SHORT":
            stop_loss, take_profit = self.compute_short_sl_tp(close)

        logger.info(
            f"[TRAIL UPDATE] {self.symbol} {position_side} "
            f"SL={stop_loss:.2f} TP={take_profit:.2f}"
        )

        return {
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }

    # ---------------------------------------------------------------------
    # Main Signal Generator
    # ---------------------------------------------------------------------
    def generate_signals(self, symbol=None, position_side=None) -> Optional[Dict[str, Any]]:
        lookup = 168

        # -----------------------
        # RESAMPLE TO 4H
        # -----------------------
        df_4h = (
            self.data.resample("4h")
            .agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            })
        )

        # Drop incomplete candles instead of exact size matching
        df_4h = df_4h.dropna()
        
        # Ensure we have enough data
        if len(df_4h) < lookup:
            return None
            
        lookback_4h = df_4h.iloc[-lookup:]
        close = df_4h['close'].iloc[-1]
        open_ = df_4h['open'].iloc[-1]

        # -----------------------
        # INDICATORS
        # -----------------------
        self.atr = self.compute_atr(df_4h)
        
        # EMA200 Trend Filter
        ema200 = self.compute_ema(df_4h['close'], period=200).iloc[-1]
        
        # ADX Regime Filter
        adx = self.compute_adx(df_4h, period=14).iloc[-1]
        
        # RSI Confluence
        rsi = self.compute_rsi(df_4h['close'], period=14).iloc[-1]
        
        # Volume Confirmation
        current_volume = df_4h['volume'].iloc[-1]
        volume_ma20 = df_4h['volume'].rolling(window=20).mean().iloc[-1]

        # =====================================================
        # TRAILING STOP UPDATE (when already in position)
        # =====================================================
        if position_side:
            trail = self.update_trailing_sl_tp(close, position_side=position_side)
            if trail:
                return {
                    "signal": "UPDATE_SL_TP",
                    "stop_loss": trail["stop_loss"],
                    "take_profit": trail["take_profit"],
                }

        last_time = df_4h.index[-1]
        now = self.data.index[-1]

        logger.info(f"Last 4H candle time: {last_time}, Current time: {now}")
        logger.info(f"Time since last 4H candle: {(now - last_time).total_seconds()} seconds")

        # Tolerant time check (±120s tolerance around 13500s)
        time_diff = (now - last_time).total_seconds()
        if abs(time_diff - 13500) > 120:
            return None  # candle still forming

        self.send_params(stock_df=df_4h, symbol=symbol, duration="4 HOURS")

        # -----------------------
        # Pivots
        # -----------------------
        highs = df_4h["high"].tolist()[-(2*self.n+1):]
        lows  = df_4h["low"].tolist()[-(2*self.n+1):]

        # load previous pivots
        self.last_sw_h, self.last_sw_l = self._load_last_pivots()

        ph = self.pivot_high_centered(highs, self.n, self.n)
        pl = self.pivot_low_centered(lows, self.n, self.n)

        if ph is not None:
            self.last_sw_h = ph
        if pl is not None:
            self.last_sw_l = pl
            
        logger.info(f"Last Pivot High: {self.last_sw_h}, Last Pivot Low: {self.last_sw_l}")
        
        # Notify pivot levels via Discord
        try:
            self.send_levels_info(data=None, description=f"Symbol = {self.symbol}", fields={'swing_high': self.last_sw_h, 'swing_low': self.last_sw_l})
        except AttributeError:
            pass

        # save pivots
        self._save_pivots(self.last_sw_h, self.last_sw_l)

        # -----------------------
        # Filters Checks
        # -----------------------
        # Trend filter
        is_long_trend = close > ema200
        is_short_trend = close < ema200
        
        # Regime filter
        is_trending_market = adx > 20
        
        # Momentum filter
        is_valid_long_rsi = 40 <= rsi <= 65
        is_valid_short_rsi = 35 <= rsi <= 60
        
        # Volume filter
        has_volume_confirmation = current_volume > (1.2 * volume_ma20)

        # -----------------------
        # Entry Conditions
        # -----------------------
        # Breakout criteria
        price_breakout_long = (self.last_sw_h is not None) and (close > self.last_sw_h) and (open_ < self.last_sw_h)
        price_breakout_short = (self.last_sw_l is not None) and (close < self.last_sw_l) and (open_ > self.last_sw_l)

        long_signal = (
            price_breakout_long and 
            is_long_trend and 
            is_trending_market and 
            is_valid_long_rsi and 
            has_volume_confirmation
        )
        
        short_signal = (
            price_breakout_short and 
            is_short_trend and 
            is_trending_market and 
            is_valid_short_rsi and 
            has_volume_confirmation
        )

        stop, target = None, None

        if long_signal:
            stop, target = self.compute_long_sl_tp(close)
        elif short_signal:
            stop, target = self.compute_short_sl_tp(close)

        # -----------------------
        # Return Signal
        # -----------------------
        if long_signal:
            chart_path_raw = generate_chart(lookback_4h)
            logger.info(f"Generated LONG signal for {self.symbol} at {close}, SL: {stop}, TP: {target}")
            return {
                "signal": "BUY",
                "entry_price": close,
                "stop_loss": stop,
                "take_profit": target,
                "chart_path": None,
                "chart_path_raw": chart_path_raw,
                "pattern": "Long Swing V2"
            }

        if short_signal:
            chart_path_raw = generate_chart(lookback_4h)
            logger.info(f"Generated SHORT signal for {self.symbol} at {close}, SL: {stop}, TP: {target}")
            return {
                "signal": "SELL",
                "entry_price": close,
                "stop_loss": stop,
                "take_profit": target,
                "chart_path": None,
                "chart_path_raw": chart_path_raw,
                "pattern": "Short Swing V2"
            }

        return None
