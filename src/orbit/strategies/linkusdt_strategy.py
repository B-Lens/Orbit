"""Hourly EMA Crossover strategy for LINKUSDT.

Strategy: Triple EMA Momentum Crossover
---------------------------------------------------------------
Chainlink (LINK) tends to trend strongly once a move begins. This strategy
capitalizes on momentum by trading the crossover of a fast EMA (9) over a 
medium EMA (21), strictly filtered by the macro trend (EMA 200).

1. **Trend Filter**: Only take longs when price > EMA-200.
2. **Crossover**: Fast EMA (9) crosses Medium EMA (21).
3. **Momentum Confirmation**: RSI(14) > 50 for longs.
4. **Risk Management**: Dynamic ATR-based stops.
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
    """Hourly EMA Crossover strategy for LINKUSDT."""

    data: pd.DataFrame
    ema_fast: int = 9
    ema_medium: int = 21
    ema_slow: int = 200
    rsi_period: int = 14
    atr_period: int = 14
    atr_stop_multiple: float = 2.5
    reward_risk: float = 2.0
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

    def _compute_rsi(self, series: pd.Series) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=self.rsi_period - 1, min_periods=self.rsi_period).mean()
        avg_loss = loss.ewm(com=self.rsi_period - 1, min_periods=self.rsi_period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def _indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        frame = data.copy()
        
        frame["ema_fast"] = frame["close"].ewm(span=self.ema_fast, adjust=False).mean()
        frame["ema_medium"] = frame["close"].ewm(span=self.ema_medium, adjust=False).mean()
        frame["ema_slow"] = frame["close"].ewm(span=self.ema_slow, adjust=False).mean()
        frame["rsi"] = self._compute_rsi(frame["close"])
        
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
        
        return frame

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def generate_signals(  # type: ignore[override]
        self, symbol: Optional[str] = None, position_side: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        del symbol
        hourly, latest_closed = self._hourly_data()
        minimum_bars = self.ema_slow + 2
        recent = hourly.tail(minimum_bars)
        
        if (
            position_side
            or not latest_closed
            or len(recent) < minimum_bars
            or not self._has_contiguous_hours(recent)
        ):
            return None

        indicators = self._indicators(hourly)
        current = indicators.iloc[-1]
        prev = indicators.iloc[-2]
        
        if pd.isna(current["atr"]) or current["atr"] <= 0:
            return None

        close = float(current["close"])
        atr = float(current["atr"])
        risk = self.atr_stop_multiple * atr
        
        # Long Conditions
        # 1. Macro Trend is UP
        long_trend = current["ema_medium"] > current["ema_slow"]
        # 2. Fast EMA crosses Medium EMA UP
        long_cross = (prev["ema_fast"] <= prev["ema_medium"]) and (current["ema_fast"] > current["ema_medium"])
        # 3. RSI > 50 (Momentum)
        long_momentum = current["rsi"] > 50.0

        if long_trend and long_cross and long_momentum:
            stop = close - risk
            target = close + self.reward_risk * risk
            pattern = f"1H EMA Crossover Bullish | EMA9={current['ema_fast']:.3f} close={close:.3f} ATR={atr:.3f}"
            logger.info("LINKUSDT BUY signal: entry=%.4f SL=%.4f TP=%.4f | %s", close, stop, target, pattern)
            return {
                "signal": "BUY",
                "entry_price": close,
                "stop_loss": float(stop),
                "take_profit": float(target),
                "pattern": pattern,
            }
            
        # Short Conditions
        # 1. Macro Trend is DOWN
        short_trend = current["ema_medium"] < current["ema_slow"]
        # 2. Fast EMA crosses Medium EMA DOWN
        short_cross = (prev["ema_fast"] >= prev["ema_medium"]) and (current["ema_fast"] < current["ema_medium"])
        # 3. RSI < 50 (Momentum)
        short_momentum = current["rsi"] < 50.0

        if short_trend and short_cross and short_momentum:
            stop = close + risk
            target = close - self.reward_risk * risk
            pattern = f"1H EMA Crossover Bearish | EMA9={current['ema_fast']:.3f} close={close:.3f} ATR={atr:.3f}"
            logger.info("LINKUSDT SELL signal: entry=%.4f SL=%.4f TP=%.4f | %s", close, stop, target, pattern)
            return {
                "signal": "SELL",
                "entry_price": close,
                "stop_loss": float(stop),
                "take_profit": float(target),
                "pattern": pattern,
            }
            
        return None
