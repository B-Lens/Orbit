"""Hourly breakout strategy for Bitcoin futures."""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import pandas as pd

from orbit.strategies.strategies_base import Strategy
from orbit.utils.utils import generate_chart

logger = logging.getLogger("Orbit")


@dataclass
class BTCStrategy(Strategy):
    """Trade hourly BTC breakouts in the direction of the prevailing trend.

    The parameters were selected on BTCUSDT hourly data from 2021 onward using
    a chronological 70/30 development/holdout split.  Breakout levels only use
    completed prior candles, so signals do not repaint.
    """

    data: pd.DataFrame
    breakout_period: int = 12
    ema_period: int = 50
    atr_period: int = 14
    atr_stop_multiple: float = 2.5
    reward_risk: float = 3.0
    symbol: str = "BITCOIN"

    def __post_init__(self) -> None:
        super().__init__(self.data)

    def _hourly_data(self) -> tuple[pd.DataFrame, bool]:
        """Return complete hourly candles and whether the latest just closed."""
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

        # Production data contains 15-minute candle-open timestamps.  At :45,
        # all four constituent candles of the hourly bar have been observed.
        latest_closed = self.data.index[-1] - hourly.index[-1] == pd.Timedelta(
            minutes=45
        )
        return hourly, latest_closed

    def _indicators(self, hourly: pd.DataFrame) -> pd.DataFrame:
        result = hourly.copy()
        previous_close = result["close"].shift(1)
        true_range = pd.concat(
            [
                result["high"] - result["low"],
                (result["high"] - previous_close).abs(),
                (result["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        result["atr"] = true_range.ewm(
            alpha=1 / self.atr_period, adjust=False
        ).mean()
        result["ema"] = result["close"].ewm(
            span=self.ema_period, adjust=False
        ).mean()
        result["breakout_high"] = (
            result["high"].shift(1).rolling(self.breakout_period).max()
        )
        result["breakout_low"] = (
            result["low"].shift(1).rolling(self.breakout_period).min()
        )
        return result

    def _trailing_update(
        self, frame: pd.DataFrame, position_side: str
    ) -> Dict[str, Any]:
        current = frame.iloc[-1]
        if position_side == "LONG":
            stop = (
                frame["high"].iloc[-self.breakout_period :].max()
                - self.atr_stop_multiple * current["atr"]
            )
        else:
            stop = (
                frame["low"].iloc[-self.breakout_period :].min()
                + self.atr_stop_multiple * current["atr"]
            )
        return {"signal": "UPDATE_SL_TP", "stop_loss": float(stop), "take_profit": None}

    def generate_signals(
        self, symbol: Optional[str] = None, position_side: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        hourly, latest_closed = self._hourly_data()
        minimum_bars = max(self.ema_period, self.breakout_period, self.atr_period) + 1
        if len(hourly) < minimum_bars:
            return None

        frame = self._indicators(hourly)
        if position_side:
            return self._trailing_update(frame, position_side)
        if not latest_closed:
            return None

        current = frame.iloc[-1]
        close = float(current["close"])
        long_signal = close > current["breakout_high"] and close > current["ema"]
        short_signal = close < current["breakout_low"] and close < current["ema"]
        if not long_signal and not short_signal:
            return None

        side = 1 if long_signal else -1
        risk = self.atr_stop_multiple * float(current["atr"])
        stop = close - side * risk
        target = close + side * self.reward_risk * risk
        action = "BUY" if long_signal else "SELL"
        pattern = f"1H {self.breakout_period}-bar breakout + EMA{self.ema_period} trend"
        chart_path_raw = generate_chart(frame.iloc[-168:])
        logger.info(
            "Generated %s signal for %s at %.2f, SL %.2f, TP %.2f",
            action,
            self.symbol,
            close,
            stop,
            target,
        )
        return {
            "signal": action,
            "entry_price": close,
            "stop_loss": float(stop),
            "take_profit": float(target),
            "chart_path": None,
            "chart_path_raw": chart_path_raw,
            "pattern": pattern,
        }
