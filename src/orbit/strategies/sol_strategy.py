"""SOLUSDT mean-reversion strategy promoted from Orbit-Strategies research.

The implementation is a production-shaped port of
``Orbit-Strategies/research/producted/solana.py``.  It preserves the researched
BB + RSI entry filters and ATR risk model while returning Orbit's standard
signal dictionary.  Exchange execution remains controlled elsewhere by
ExecutionSettings; registering this strategy does not authorize live orders.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from orbit.strategies.strategies_base import Strategy


class SolanaMeanReversionStrategy(Strategy):
    """SOL mean-reversion candidate derived from the historical research model.

    Entry model:
    - Bollinger Bands (20, 2 standard deviations)
    - RSI(14), oversold 30 / overbought 70
    - current volume >= 80% of the prior 20-candle average
    - longs must be within 0.5% of the recent 10-candle low
    - shorts must be within 0.5% of the recent 10-candle high
    - high-volatility outliers are rejected using ATR/range medians

    Risk model:
    - initial stop = 1 ATR
    - reject entries whose stop distance exceeds 1.5% of entry
    - target = 2.5R

    Trailing-stop and profitable SMA exits are implemented by the SOL paper
    engine because they depend on bars observed after the entry signal.
    """

    bb_period = 20
    bb_dev = 2.0
    rsi_period = 14
    rsi_oversold = 30.0
    rsi_overbought = 70.0
    sl_atr_mult = 1.0
    tp_mult = 2.5
    outlier_mult = 2.5
    max_risk_percent = 0.015
    volume_ratio_min = 0.8
    recent_extreme_window = 10
    recent_extreme_tolerance = 0.005
    volatility_window = 30
    lookback = 168

    @staticmethod
    def _rsi_sma(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gains = delta.clip(lower=0).rolling(period).mean()
        losses = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gains / losses.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        # A run with no losses is maximally bullish; no gains is maximally bearish.
        rsi = rsi.where(~((losses == 0) & (gains > 0)), 100.0)
        rsi = rsi.where(~((gains == 0) & (losses > 0)), 0.0)
        return rsi

    def generate_signals(self, symbol: Optional[str] = None):
        if self.data is None or len(self.data) < self.lookback:
            return None

        df = self.data.iloc[-self.lookback :].copy()
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            return None

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float)

        sma = close.rolling(self.bb_period).mean()
        std = close.rolling(self.bb_period).std()
        lower_band = sma - self.bb_dev * std
        upper_band = sma + self.bb_dev * std
        rsi = self._rsi_sma(close, self.rsi_period)
        atr = self.compute_atr(df, period=14)
        candle_range = high - low

        values = (sma.iloc[-1], lower_band.iloc[-1], upper_band.iloc[-1], rsi.iloc[-1], atr.iloc[-1])
        if any(pd.isna(value) for value in values):
            return None

        current_atr = float(atr.iloc[-1])
        current_range = float(candle_range.iloc[-1])
        atr_history = atr.iloc[-self.volatility_window : -1].dropna()
        range_history = candle_range.iloc[-self.volatility_window : -1].dropna()
        if len(atr_history) >= self.volatility_window - 1:
            median_atr = float(atr_history.median())
            median_range = float(range_history.median())
            if (
                (median_atr > 0 and current_atr > self.outlier_mult * median_atr)
                or (median_range > 0 and current_range > self.outlier_mult * median_range)
            ):
                return None

        prior_volume = volume.iloc[-21:-1]
        avg_volume = float(prior_volume.mean()) if len(prior_volume) else 0.0
        volume_ratio = float(volume.iloc[-1]) / avg_volume if avg_volume > 0 else 1.0
        if volume_ratio <= self.volume_ratio_min:
            return None

        entry = float(close.iloc[-1])
        current_rsi = float(rsi.iloc[-1])
        recent_lows = low.iloc[-(self.recent_extreme_window + 1) : -1]
        recent_highs = high.iloc[-(self.recent_extreme_window + 1) : -1]
        if recent_lows.empty or recent_highs.empty:
            return None

        side = None
        if (
            entry < float(lower_band.iloc[-1])
            and current_rsi < self.rsi_oversold
            and entry <= float(recent_lows.min()) * (1.0 + self.recent_extreme_tolerance)
        ):
            side = "BUY"
            stop_loss = entry - current_atr * self.sl_atr_mult
            risk = entry - stop_loss
            take_profit = entry + risk * self.tp_mult
        elif (
            entry > float(upper_band.iloc[-1])
            and current_rsi > self.rsi_overbought
            and entry >= float(recent_highs.max()) * (1.0 - self.recent_extreme_tolerance)
        ):
            side = "SELL"
            stop_loss = entry + current_atr * self.sl_atr_mult
            risk = stop_loss - entry
            take_profit = entry - risk * self.tp_mult
        else:
            return None

        if risk <= 0 or risk / entry > self.max_risk_percent:
            return None

        return {
            "signal": side,
            "entry_price": entry,
            "stop_loss": float(stop_loss),
            "take_profit": float(take_profit),
            "pattern": "SOL Mean Reversion BB+RSI",
            "chart_path": None,
            "chart_path_raw": None,
            "strategy_meta": {
                "rsi": current_rsi,
                "atr": current_atr,
                "sma": float(sma.iloc[-1]),
                "volume_ratio": volume_ratio,
                "initial_r_multiple": self.tp_mult,
                "source": "Orbit-Strategies/research/producted/solana.py",
            },
        }


# Backwards-compatible name used by the first paper-trading PR revision.
BollingerAdaptiveReversalStrategySOL = SolanaMeanReversionStrategy
