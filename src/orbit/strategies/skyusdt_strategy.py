"""Hourly breakout strategy for SKYUSDT Futures Testnet."""

from dataclasses import dataclass
from typing import Any, Dict, Optional

import pandas as pd

from orbit.strategies.strategies_base import Strategy


@dataclass
class SKYUSDTStrategy(Strategy):
    """Trade hourly Donchian breakouts in the EMA trend direction."""

    data: pd.DataFrame
    breakout_period: int = 24
    ema_period: int = 100
    atr_period: int = 14
    atr_stop_multiple: float = 1.5
    reward_risk: float = 4.0

    def __post_init__(self) -> None:
        super().__init__(self.data)

    @staticmethod
    def _current_hour() -> pd.Timestamp:
        """Return the current UTC hour boundary for freshness checks."""
        return pd.Timestamp.now(tz="UTC").floor("1h")

    def _is_latest_completed_hour(self, timestamp: pd.Timestamp) -> bool:
        """Reject aligned but stale cached candles before they can trade."""
        candle_hour = pd.Timestamp(timestamp)
        current_hour = self._current_hour()
        if candle_hour.tzinfo is None:
            current_hour = current_hour.tz_localize(None)
        else:
            current_hour = current_hour.tz_convert(candle_hour.tzinfo)
        return candle_hour == current_hour - pd.Timedelta(hours=1)

    @staticmethod
    def _has_contiguous_hours(data: pd.DataFrame) -> bool:
        """Return whether every strategy bar is exactly one hour apart."""
        if len(data) < 2:
            return True
        differences = data.index.to_series().diff().dropna()
        return bool((differences == pd.Timedelta(hours=1)).all())

    def _hourly_data(self) -> tuple[pd.DataFrame, bool]:
        if self.data.empty or not isinstance(self.data.index, pd.DatetimeIndex):
            return self.data.copy(), False

        intervals = self.data.index.to_series().diff().dropna()
        interval = intervals.median() if not intervals.empty else pd.Timedelta(hours=1)
        if interval >= pd.Timedelta(hours=1):
            hourly = self.data.copy()
            valid = self._has_contiguous_hours(
                hourly
            ) and self._is_latest_completed_hour(hourly.index[-1])
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
        latest_closed = self._has_contiguous_hours(
            hourly
        ) and self._is_latest_completed_hour(hourly.index[-1])
        return hourly, latest_closed

    def _indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        frame = data.copy()
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
        frame["ema"] = frame["close"].ewm(span=self.ema_period, adjust=False).mean()
        frame["breakout_high"] = (
            frame["high"].shift(1).rolling(self.breakout_period).max()
        )
        frame["breakout_low"] = (
            frame["low"].shift(1).rolling(self.breakout_period).min()
        )
        return frame

    def generate_signals(
        self, symbol: Optional[str] = None, position_side: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        hourly, latest_closed = self._hourly_data()
        minimum = max(self.breakout_period, self.ema_period, self.atr_period) + 1
        if position_side or not latest_closed or len(hourly) < minimum:
            return None

        current = self._indicators(hourly).iloc[-1]
        close = float(current["close"])
        long_signal = close > current["breakout_high"] and close > current["ema"]
        short_signal = close < current["breakout_low"] and close < current["ema"]
        if not long_signal and not short_signal:
            return None

        direction = 1 if long_signal else -1
        # Mongo deliberately omits the in-progress 15-minute candle.  Enter at
        # the just-completed breakout bar's close, which is the latest price
        # guaranteed by that data contract.
        entry_price = close
        risk = self.atr_stop_multiple * float(current["atr"])
        return {
            "signal": "BUY" if long_signal else "SELL",
            "entry_price": entry_price,
            "stop_loss": entry_price - direction * risk,
            "take_profit": entry_price + direction * self.reward_risk * risk,
            "pattern": "1H 24-bar Donchian breakout + EMA100 trend",
        }
