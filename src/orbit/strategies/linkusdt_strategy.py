"""Testnet-only hourly Donchian breakout strategy for LINKUSDT."""

from dataclasses import dataclass
from typing import Any, Dict, Optional

import pandas as pd

from orbit.strategies.strategies_base import Strategy


@dataclass
class LINKUSDTStrategy(Strategy):
    """Testnet Donchian/EMA/volume strategy evaluated in the LINKUSDT study.

    The parameters are fixed to the highest full-sample candidate in the
    predeclared grid. It is authorized only for monitored testnet forward
    validation; live promotion requires a separate reviewed change.
    """

    data: pd.DataFrame
    donchian_period: int = 72
    ema_period: int = 200
    atr_period: int = 14
    volume_period: int = 24
    volume_multiple: float = 1.4
    atr_stop_multiple: float = 2.0
    reward_risk: float = 3.0

    def __post_init__(self) -> None:
        super().__init__(self.data)

    @staticmethod
    def _has_contiguous_bars(data: pd.DataFrame, interval: pd.Timedelta) -> bool:
        """Return whether *data* has no missing candles at its native cadence."""
        if len(data) < 2:
            return True
        differences = data.index.to_series().diff().dropna()
        return bool((differences == interval).all())

    def _hourly_data(self) -> pd.DataFrame:
        """Return complete 1-hour candles from native hourly or 15-minute data."""
        if self.data.empty or not isinstance(self.data.index, pd.DatetimeIndex):
            return pd.DataFrame(columns=self.data.columns, index=self.data.index)

        data = self.data.sort_index()
        differences = data.index.to_series().diff().dropna()
        interval = differences.median() if not differences.empty else pd.Timedelta(hours=1)
        if interval >= pd.Timedelta(hours=1):
            return data if self._has_contiguous_bars(data, interval) else data.iloc[0:0]

        bars_per_hour = pd.Timedelta(hours=1) / interval
        if not bars_per_hour.is_integer() or not self._has_contiguous_bars(data, interval):
            return data.iloc[0:0]

        grouped = data.resample("1h", label="left", closed="left")
        hourly = grouped.agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        expected = int(bars_per_hour)
        complete = grouped["close"].count() == expected
        return hourly[complete].dropna()

    def _indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        frame = data.copy()
        frame["prior_high"] = frame["high"].shift(1).rolling(self.donchian_period).max()
        frame["prior_low"] = frame["low"].shift(1).rolling(self.donchian_period).min()
        frame["ema"] = self.compute_ema(frame["close"], self.ema_period)
        previous_close = frame["close"].shift(1)
        true_range = pd.concat(
            [
                frame["high"] - frame["low"],
                (frame["high"] - previous_close).abs(),
                (frame["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        # Wilder's smoothing is the ATR definition used in the exploratory grid.
        frame["atr"] = true_range.ewm(
            alpha=1 / self.atr_period, adjust=False
        ).mean()
        frame["average_volume"] = frame["volume"].shift(1).rolling(self.volume_period).mean()
        return frame

    def generate_signals(  # type: ignore[override]
        self, symbol: Optional[str] = None, position_side: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Return a candidate signal from complete, contiguous hourly candles."""
        del symbol
        hourly = self._hourly_data()
        minimum_bars = max(self.donchian_period, self.ema_period, self.volume_period) + 1
        if position_side or len(hourly) < minimum_bars:
            return None

        frame = self._indicators(hourly)
        current = frame.iloc[-1]
        if pd.isna(current[["prior_high", "prior_low", "atr", "average_volume"]]).any():
            return None

        close = float(current["close"])
        volume_confirmed = current["volume"] > self.volume_multiple * current["average_volume"]
        long_signal = (
            close > current["prior_high"]
            and close > current["ema"]
            and volume_confirmed
        )
        short_signal = (
            close < current["prior_low"]
            and close < current["ema"]
            and volume_confirmed
        )
        if not long_signal and not short_signal:
            return None

        direction = 1 if long_signal else -1
        risk = self.atr_stop_multiple * float(current["atr"])
        if risk <= 0:
            return None
        return {
            "signal": "BUY" if long_signal else "SELL",
            "entry_price": close,
            "stop_loss": close - direction * risk,
            "take_profit": close + direction * self.reward_risk * risk,
            "pattern": (
                "TESTNET: 1H Donchian72 + EMA200 + volume1.4 "
                f"| ATR={float(current['atr']):.4f}"
            ),
        }
