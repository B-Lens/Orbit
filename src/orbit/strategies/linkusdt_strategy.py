"""Hourly BB Squeeze Breakout strategy for LINKUSDT.

Strategy: Bollinger Band Squeeze Breakout with EMA Trend Filter
---------------------------------------------------------------
Chainlink (LINK) spends 70-80% of its time in tight ranges, leading to
volatility compression (squeeze). When breakouts occur, they are typically
impulsive and sustained. This strategy detects volatility compression using
Bollinger Band bandwidth and trades breakouts in the direction of the macro
trend (EMA-200).

1. **Bollinger Band Squeeze**: Bandwidth drops below its 48-hour minimum.
2. **Breakout**: Price closes outside the Bollinger Bands.
3. **Trend Filter**: Only take long breakouts above EMA-200, and short
   breakouts below EMA-200.
4. **Volume Confirmation**: Volume must spike >1.35x its 24-hour average.
5. **Risk Management**: ATR-based stops targeting 1:3.5 R:R.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from orbit.strategies.strategies_base import Strategy

logger = logging.getLogger("Orbit")


@dataclass
class LINKUSDTStrategy(Strategy):
    """Hourly BB Squeeze Breakout strategy for LINKUSDT."""

    data: pd.DataFrame
    bb_period: int = 20
    bb_std: float = 2.0
    squeeze_lookback: int = 48
    ema_period: int = 200
    atr_period: int = 14
    atr_stop_multiple: float = 3.0
    reward_risk: float = 2.0
    volume_period: int = 24
    volume_multiple: float = 1.20
    enforce_freshness: bool = True

    def __post_init__(self) -> None:
        super().__init__(self.data)

    # ------------------------------------------------------------------
    # Hourly data helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _current_hour() -> pd.Timestamp:
        return pd.Timestamp.now(tz="UTC")

    def _is_latest_completed_hour(self, timestamp: pd.Timestamp) -> bool:
        candle_hour = pd.Timestamp(timestamp)
        current_time = self._current_hour()
        if candle_hour.tzinfo is None:
            current_time = current_time.tz_localize(None)
        else:
            current_time = current_time.tz_convert(candle_hour.tzinfo)
        available_at = candle_hour + pd.Timedelta(hours=1)
        return bool(
            available_at
            <= current_time
            < available_at + pd.Timedelta(minutes=15)
        )

    @staticmethod
    def _has_contiguous_hours(data: pd.DataFrame) -> bool:
        if len(data) < 2:
            return True
        differences = data.index.to_series().diff().dropna()
        return bool((differences == pd.Timedelta(hours=1)).all())

    def _hourly_data(self) -> tuple[pd.DataFrame, bool]:
        if self.data.empty or not isinstance(self.data.index, pd.DatetimeIndex):
            return self.data.copy(), False

        differences = self.data.index.to_series().diff().dropna()
        interval = (
            differences.median() if not differences.empty else pd.Timedelta(hours=1)
        )
        if interval >= pd.Timedelta(hours=1):
            hourly = self.data.copy()
            if self.enforce_freshness:
                current_time = self._current_hour()
                current_hour = current_time.floor("1h")
                if hourly.index.tz is None:
                    current_hour = current_hour.tz_localize(None)
                else:
                    current_hour = current_hour.tz_convert(hourly.index.tz)
                hourly = hourly[hourly.index < current_hour]
            if hourly.empty:
                return hourly, False
            valid = not self.enforce_freshness or self._is_latest_completed_hour(
                hourly.index[-1]
            )
            return hourly, valid

        grouped = self.data.resample("1h")
        hourly = grouped.agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        expected_bars = round(pd.Timedelta(hours=1) / interval)
        hourly = hourly[grouped.size() == expected_bars].dropna()
        if hourly.empty:
            return hourly, False
        latest_closed = not self.enforce_freshness or self._is_latest_completed_hour(
            hourly.index[-1]
        )
        return hourly, latest_closed

    # ------------------------------------------------------------------
    # Indicator computation
    # ------------------------------------------------------------------

    def _indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        frame = data.copy()
        
        bb_middle = frame["close"].rolling(window=self.bb_period).mean()
        bb_std = frame["close"].rolling(window=self.bb_period).std()
        
        frame["bb_upper"] = bb_middle + self.bb_std * bb_std
        frame["bb_lower"] = bb_middle - self.bb_std * bb_std
        frame["bb_bandwidth"] = (frame["bb_upper"] - frame["bb_lower"]) / bb_middle
        
        # Min bandwidth over lookback (shifted to avoid look-ahead)
        frame["bb_bandwidth_min"] = (
            frame["bb_bandwidth"].shift(1).rolling(self.squeeze_lookback).min()
        )
        # Squeeze defined as current bandwidth within 5% of the lookback minimum
        frame["squeeze"] = frame["bb_bandwidth"] <= frame["bb_bandwidth_min"] * 1.05
        # Recent squeeze within the last 10 hours
        frame["squeeze_recent"] = (
            frame["squeeze"].rolling(10).max().fillna(0).astype(bool)
        )
        
        frame["ema"] = frame["close"].ewm(span=self.ema_period, adjust=False).mean()
        
        previous_close = frame["close"].shift(1)
        true_range = pd.concat(
            [
                frame["high"] - frame["low"],
                (frame["high"] - previous_close).abs(),
                (frame["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        frame["atr"] = true_range.ewm(alpha=1 / self.atr_period, adjust=False).mean()
        
        frame["average_volume"] = (
            frame["volume"].shift(1).rolling(self.volume_period).mean()
        )
        
        return frame

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def generate_signals(  # type: ignore[override]
        self, symbol: Optional[str] = None, position_side: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        del symbol
        hourly, latest_closed = self._hourly_data()
        minimum_bars = (
            max(
                self.bb_period + self.squeeze_lookback,
                self.ema_period,
                self.atr_period,
                self.volume_period,
            )
            + 1
        )
        recent = hourly.tail(minimum_bars)
        if (
            position_side
            or not latest_closed
            or len(recent) < minimum_bars
            or not self._has_contiguous_hours(recent)
        ):
            return None

        current = self._indicators(hourly).iloc[-1]
        
        if (
            pd.isna(current["atr"]) 
            or current["atr"] <= 0 
            or pd.isna(current["average_volume"])
            or current["average_volume"] <= 0
        ):
            return None

        close = float(current["close"])
        active_volume = current["volume"] > (
            self.volume_multiple * current["average_volume"]
        )
        squeeze_recent = bool(current["squeeze_recent"])
        atr = float(current["atr"])
        
        risk = self.atr_stop_multiple * atr
        
        long_signal = (
            close > current["bb_upper"]
            and close > current["ema"]
            and active_volume
            and squeeze_recent
        )
        
        short_signal = (
            close < current["bb_lower"]
            and close < current["ema"]
            and active_volume
            and squeeze_recent
        )
        
        if long_signal:
            stop = close - risk
            target = close + self.reward_risk * risk
            pattern = (
                f"1H BB Squeeze Breakout Bullish | EMA200={current['ema']:.3f} "
                f"close={close:.3f} ATR={atr:.3f}"
            )
            logger.info(
                "LINKUSDT BUY signal: entry=%.4f SL=%.4f TP=%.4f | %s",
                close, stop, target, pattern,
            )
            return {
                "signal": "BUY",
                "entry_price": close,
                "stop_loss": float(stop),
                "take_profit": float(target),
                "pattern": pattern,
            }
            
        elif short_signal:
            stop = close + risk
            target = close - self.reward_risk * risk
            pattern = (
                f"1H BB Squeeze Breakout Bearish | EMA200={current['ema']:.3f} "
                f"close={close:.3f} ATR={atr:.3f}"
            )
            logger.info(
                "LINKUSDT SELL signal: entry=%.4f SL=%.4f TP=%.4f | %s",
                close, stop, target, pattern,
            )
            return {
                "signal": "SELL",
                "entry_price": close,
                "stop_loss": float(stop),
                "take_profit": float(target),
                "pattern": pattern,
            }
            
        return None
