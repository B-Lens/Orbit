"""Hourly trend-breakout strategy for SOLUSDT testnet trading."""

from dataclasses import dataclass
from typing import Any, Dict, Optional

import pandas as pd

from orbit.strategies.strategies_base import Strategy


@dataclass
class SOLUSDTStrategy(Strategy):
    """Trade two-day breakouts that agree with SOL's long-term hourly trend."""

    data: pd.DataFrame
    breakout_period: int = 48
    ema_period: int = 200
    atr_period: int = 14
    atr_stop_multiple: float = 2.0
    reward_risk: float = 4.0
    volume_period: int = 24
    volume_multiple: float = 1.2
    enforce_freshness: bool = True

    def __post_init__(self) -> None:
        super().__init__(self.data)

    @staticmethod
    def _current_hour() -> pd.Timestamp:
        """Return the current UTC time for freshness checks."""
        return pd.Timestamp.now(tz="UTC")

    def _is_latest_completed_hour(self, timestamp: pd.Timestamp) -> bool:
        """Reject aligned but stale cached candles before they can trade."""
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
        """Return whether the indicator window contains consecutive hourly bars."""
        if len(data) < 2:
            return True
        differences = data.index.to_series().diff().dropna()
        return bool((differences == pd.Timedelta(hours=1)).all())

    def _hourly_data(self) -> tuple[pd.DataFrame, bool]:
        """Return hourly candles and whether the last bar is the freshly completed hour."""
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
        frame["average_volume"] = (
            frame["volume"].shift(1).rolling(self.volume_period).mean()
        )
        return frame

    def generate_signals(  # type: ignore[override]
        self, symbol: Optional[str] = None, position_side: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        del symbol
        hourly, latest_closed = self._hourly_data()
        minimum_bars = (
            max(
                self.breakout_period,
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
        close = float(current["close"])
        active_volume = current["volume"] > (
            self.volume_multiple * current["average_volume"]
        )
        long_signal = (
            close > current["breakout_high"]
            and close > current["ema"]
            and active_volume
        )
        short_signal = (
            close < current["breakout_low"] and close < current["ema"] and active_volume
        )
        if not long_signal and not short_signal:
            return None

        direction = 1 if long_signal else -1
        risk = self.atr_stop_multiple * float(current["atr"])
        return {
            "signal": "BUY" if long_signal else "SELL",
            "entry_price": close,
            "stop_loss": close - direction * risk,
            "take_profit": close + direction * self.reward_risk * risk,
            "pattern": "1H 48-bar Donchian breakout + EMA200 + volume",
        }
