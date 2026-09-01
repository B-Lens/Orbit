"""Hourly breakout strategy for MKRUSDT Futures Testnet."""

from dataclasses import dataclass
from typing import Any, Dict, Optional

import pandas as pd

from orbit.strategies.strategies_base import Strategy


@dataclass
class MKRUSDTStrategy(Strategy):
    """Trade hourly Donchian breakouts in the EMA trend direction."""

    data: pd.DataFrame
    breakout_period: int = 24
    ema_period: int = 100
    atr_period: int = 14
    atr_stop_multiple: float = 1.5
    reward_risk: float = 4.0

    def __post_init__(self) -> None:
        super().__init__(self.data)

    def _hourly_data(self) -> tuple[pd.DataFrame, bool]:
        if self.data.empty or not isinstance(self.data.index, pd.DatetimeIndex):
            return self.data.copy(), False

        intervals = self.data.index.to_series().diff().dropna()
        interval = intervals.median() if not intervals.empty else pd.Timedelta(hours=1)
        if interval >= pd.Timedelta(hours=1):
            return self.data.copy(), True

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
        latest_closed = (
            self.data.index[-1] - hourly.index[-1] == pd.Timedelta(hours=1) - interval
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
        risk = self.atr_stop_multiple * float(current["atr"])
        return {
            "signal": "BUY" if long_signal else "SELL",
            "entry_price": close,
            "stop_loss": close - direction * risk,
            "take_profit": close + direction * self.reward_risk * risk,
            "pattern": "1H 24-bar Donchian breakout + EMA100 trend",
        }
