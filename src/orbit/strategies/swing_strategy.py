import os
import sys
import math
import numpy as np
import pandas as pd
import redis
from orbit.utils.utils import generate_chart
from orbit.strategies.strategies_base import Strategy
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
import logging

logger = logging.getLogger("Orbit")

@dataclass
class SwingStrategyBTC(Strategy):
    """
    Real-time Swing Strategy matching Backtrader logic:
    - EMA200 Trend Filter
    - Pivot breakout entries
    - ATR Structural SL
    - RR = 1:2 TP
    - Non-repainting pivots
    """

    data: pd.DataFrame

    # Strategy parameters
    max_sl_limit: float = 2.0     # %
    atr_mult: float = 1.0
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

    # ---------------------------------------------------------------------
    # Redis helpers
    # ---------------------------------------------------------------------
    def _get_redis_key(self, key_type: str) -> str:
        return f"swing:{key_type}:{self.symbol}:4h"

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
    # SL / TP identical to Backtrader
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
    # Pine-style trailing SL/TP update (matches Backtrader)
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

        # Keep only complete 4h periods
        df_4h = df_4h[self.data.resample("4h").size() == 16]
        lookback_4h = df_4h.iloc[-lookup:]
        close = df_4h['close'].iloc[-1]
        open_ = df_4h['open'].iloc[-1]

        # -----------------------
        # ATR
        # -----------------------
        self.atr = self.compute_atr(df_4h)

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
        logger.info(f"Time since last 4H candle: {(now - last_time).total_seconds() } seconds")

        if (now - last_time).total_seconds() != 13500: # 3h45m = 13500s, ensures we only generate signal once per new 4H candle after it has fully formed
            return None  # candle still forming based on (open candle timstamp)

        self.send_params(stock_df=df_4h, symbol=symbol, duration="4 HOURS")


        # -----------------------
        # EMA200 TREND FILTER
        # -----------------------
        ema200 = self.compute_ema(df_4h["close"], 200)
        ema200_now = float(ema200.iloc[-1])

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
        self.send_levels_info(data=None, description=f"Symbol = BITCOIN", fields={'swing_high': self.last_sw_h, 'swing_low': self.last_sw_l})

        # save pivots
        self._save_pivots(self.last_sw_h, self.last_sw_l)

        # -----------------------
        # Entry Conditions (same as BT)
        # -----------------------
        long_signal  = (self.last_sw_h is not None) and (close > self.last_sw_h) and (open_ < self.last_sw_h)
        short_signal = (self.last_sw_l is not None) and (close < self.last_sw_l) and (open_ > self.last_sw_l)

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
                "pattern": "Long Swing"
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
                "pattern": "Short Swing"
            }

        return None
