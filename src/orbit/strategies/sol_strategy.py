"""Web-researched, paper-only SOLUSDT momentum strategy.

The strategy deliberately has no exchange or order-management dependencies. It
only converts a completed OHLCV history into a hypothetical trade plan for the
isolated paper-trading runner.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


class SolanaVolatilityMomentumStrategy:
    """EMA trend signal confirmed by RSI, volume, and ATR-based risk."""

    FAST_EMA = 8
    SLOW_EMA = 24
    RSI_PERIOD = 14
    ATR_PERIOD = 14
    VOLUME_PERIOD = 20
    STOP_ATR = 2.0
    TARGET_ATR = 3.0
    MIN_ATR_FRACTION = 0.003
    MAX_ATR_FRACTION = 0.06

    def __init__(self, data: pd.DataFrame) -> None:
        self.data = data.copy()

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = -delta.clip(upper=0).ewm(alpha=1 / period, adjust=False).mean()
        relative_strength = gain / loss.replace(0, float("nan"))
        return (100 - (100 / (1 + relative_strength))).fillna(50.0)

    @staticmethod
    def _atr(data: pd.DataFrame, period: int) -> pd.Series:
        previous_close = data["close"].shift(1)
        true_range = pd.concat(
            [
                data["high"] - data["low"],
                (data["high"] - previous_close).abs(),
                (data["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return true_range.ewm(alpha=1 / period, adjust=False).mean()

    def generate_signals(self, symbol: str = "SOLUSDT") -> Optional[Dict[str, Any]]:
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(self.data.columns) or self.data.empty:
            return None

        data = self.data.sort_index().copy()
        latest = pd.Timestamp(data.index[-1])
        # Binance timestamps candles by open time. A xx:45 15-minute candle
        # completes its six-hour UTC bucket; evaluating only then avoids trading
        # on an unfinished higher-timeframe bar.
        if latest.minute != 45 or latest.hour % 6 != 5:
            return None
        data = data.resample("6h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
        if len(data) < self.SLOW_EMA + 2:
            return None
        close = data["close"].astype(float)
        fast = close.ewm(span=self.FAST_EMA, adjust=False).mean()
        slow = close.ewm(span=self.SLOW_EMA, adjust=False).mean()
        rsi = self._rsi(close, self.RSI_PERIOD)
        atr = self._atr(data, self.ATR_PERIOD)
        median_volume = data["volume"].astype(float).rolling(self.VOLUME_PERIOD).median()

        entry = float(close.iloc[-1])
        current_atr = float(atr.iloc[-1])
        if entry <= 0 or pd.isna(current_atr) or current_atr <= 0:
            return None
        atr_fraction = current_atr / entry
        if not self.MIN_ATR_FRACTION <= atr_fraction <= self.MAX_ATR_FRACTION:
            return None
        if float(data["volume"].iloc[-1]) < float(median_volume.iloc[-1]):
            return None

        fast_now = float(fast.iloc[-1])
        fast_before = float(fast.iloc[-2])
        slow_now = float(slow.iloc[-1])
        rsi_now = float(rsi.iloc[-1])
        signal: Optional[str] = None

        if entry > fast_now > slow_now and fast_now > fast_before and 52 <= rsi_now <= 72:
            signal = "BUY"
            stop_loss = entry - self.STOP_ATR * current_atr
            take_profit = entry + self.TARGET_ATR * current_atr
        elif entry < fast_now < slow_now and fast_now < fast_before and 28 <= rsi_now <= 48:
            signal = "SELL"
            stop_loss = entry + self.STOP_ATR * current_atr
            take_profit = entry - self.TARGET_ATR * current_atr
        else:
            return None

        return {
            "signal": signal,
            "entry_price": entry,
            "stop_loss": float(stop_loss),
            "take_profit": float(take_profit),
            "pattern": "volatility-scaled-time-series-momentum",
            "strategy_meta": {
                "symbol": symbol,
                "fast_ema": self.FAST_EMA,
                "slow_ema": self.SLOW_EMA,
                "rsi": rsi_now,
                "atr": current_atr,
                "atr_fraction": atr_fraction,
                "stop_atr": self.STOP_ATR,
                "target_atr": self.TARGET_ATR,
                "signal_interval": "6h",
                "volume_filter": "above-20-period-median",
                "source": "independent-web-research-2026-08-17",
            },
        }
