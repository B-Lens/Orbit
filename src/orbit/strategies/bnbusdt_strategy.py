"""Hourly EMA crossover strategy for BNB futures."""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import pandas as pd

from orbit.strategies.strategies_base import Strategy
from orbit.utils.utils import generate_chart

logger = logging.getLogger("Orbit")


@dataclass
class BNBStrategy(Strategy):
    """Trade hourly BNB using EMA crossovers and ATR-based stops.

    Parameters were selected via grid search on hourly data.
    """

    data: pd.DataFrame
    ema_fast_period: int = 20
    ema_slow_period: int = 100
    atr_period: int = 14
    atr_stop_multiple: float = 2.0
    reward_risk: float = 3.0
    symbol: str = "BNB"

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
        result["ema_fast"] = result["close"].ewm(
            span=self.ema_fast_period, adjust=False
        ).mean()
        result["ema_slow"] = result["close"].ewm(
            span=self.ema_slow_period, adjust=False
        ).mean()
        return result

    def _trailing_update(
        self, frame: pd.DataFrame, position_side: str
    ) -> Dict[str, Any]:
        # Using fixed SL/TP for BNB for now as optimized
        return {"signal": "UPDATE_SL_TP", "stop_loss": 0, "take_profit": 0}

    def generate_signals(
        self, symbol: Optional[str] = None, position_side: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        hourly, latest_closed = self._hourly_data()
        minimum_bars = max(self.ema_slow_period, self.atr_period) + 1
        if len(hourly) < minimum_bars:
            return None

        frame = self._indicators(hourly)
        if position_side:
            return self._trailing_update(frame, position_side)
        if not latest_closed:
            return None

        # Check for crossover
        current = frame.iloc[-1]
        previous = frame.iloc[-2]
        
        long_signal = current["ema_fast"] > current["ema_slow"] and previous["ema_fast"] <= previous["ema_slow"]
        short_signal = current["ema_fast"] < current["ema_slow"] and previous["ema_fast"] >= previous["ema_slow"]
        
        if not long_signal and not short_signal:
            return None

        close = float(current["close"])
        side = 1 if long_signal else -1
        risk = self.atr_stop_multiple * float(current["atr"])
        
        stop = close - side * risk
        target = close + side * self.reward_risk * risk
        action = "BUY" if long_signal else "SELL"
        pattern = f"1H EMA{self.ema_fast_period}/{self.ema_slow_period} Crossover"
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
