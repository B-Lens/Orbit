"""Intraday momentum-confluence strategy for ATOMUSDT on the 15-minute timeframe.

Strategy: VWAP Momentum + EMA Trend Confluence with RSI & ATR Risk Management

Design rationale
----------------
Cosmos (ATOM) is a mid-cap altcoin with moderate liquidity, higher volatility
relative to BTC/ETH, and clear intraday momentum regimes.  The strategy exploits
three complementary edges:

1. **VWAP as intraday fair-value anchor** – Price above/below anchored VWAP
   distinguishes buying vs. selling pressure within a single session.
2. **EMA 9/21 trend confirmation** – Fast-EMA crossovers on 15-minute bars
   capture momentum shifts without over-fitting to noise.
3. **RSI 14 momentum gate** – Avoids entries into extended moves; the ideal
   zone is 45–65 for longs and 35–55 for shorts.
4. **Volume confirmation** – A 1.5× volume spike above the 20-bar SMA reduces
   false breakouts that are common in low-liquidity altcoin sessions.
5. **ATR-based position sizing** – Dynamic stop and target levels that scale
   with current volatility, delivering a consistent 1:2.5 reward/risk ratio.

Timeframe: 15 minutes (native production feed candles, no resampling needed).
Minimum bars: 100 (satisfies EMA-21, ATR-14, and VWAP warm-up requirements).
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from orbit.strategies.strategies_base import Strategy

logger = logging.getLogger("Orbit")


@dataclass
class ATOMUSDTStrategy(Strategy):
    """15-minute VWAP + EMA + RSI intraday strategy for ATOMUSDT.

    Parameters
    ----------
    data:
        OHLCV DataFrame with a DatetimeIndex (15-minute candles preferred).
    ema_fast:
        Period of the fast EMA used for short-term momentum detection (default 9).
    ema_slow:
        Period of the slow EMA used for medium-term trend direction (default 21).
    rsi_period:
        Look-back period for Wilder's RSI (default 14).
    atr_period:
        Look-back period for Average True Range (default 14).
    atr_stop_multiple:
        Multiplier applied to ATR for stop-loss distance (default 1.5).
    reward_risk:
        Take-profit expressed as a multiple of the stop-loss distance (default 2.5).
    vol_sma_period:
        Rolling window for the volume baseline (default 20).
    vol_spike_threshold:
        Minimum ratio of current volume to its rolling mean (default 1.5).
    """

    data: pd.DataFrame
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    atr_period: int = 14
    atr_stop_multiple: float = 1.5
    reward_risk: float = 2.5
    vol_sma_period: int = 20
    vol_spike_threshold: float = 1.5

    def __post_init__(self) -> None:  # noqa: D401
        super().__init__(self.data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_rsi(self, series: pd.Series) -> pd.Series:
        """Wilder's smoothed RSI."""
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=self.rsi_period - 1, min_periods=self.rsi_period).mean()
        avg_loss = loss.ewm(com=self.rsi_period - 1, min_periods=self.rsi_period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _compute_vwap(data: pd.DataFrame) -> pd.Series:
        """Intraday anchored VWAP — resets at the start of each UTC calendar day.

        Each candle's typical price (H+L+C)/3 is volume-weighted and accumulated
        from the daily open.  This mirrors the convention used by most professional
        intraday traders on Binance perpetual markets.
        """
        df = data.copy()
        df["_typical"] = (df["high"] + df["low"] + df["close"]) / 3.0
        df["_date"] = df.index.normalize()  # UTC date anchor

        vwap_values = np.empty(len(df))
        vwap_values[:] = np.nan

        for _date, group in df.groupby("_date"):
            cum_tp_vol = (group["_typical"] * group["volume"]).cumsum()
            cum_vol = group["volume"].cumsum()
            day_vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
            # Resolve integer positions in the outer array using the parent index
            positions = df.index.get_indexer(group.index)
            vwap_values[positions] = day_vwap.values

        return pd.Series(vwap_values, index=df.index, name="vwap")

    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a copy of *df* enriched with all required indicators."""
        frame = df.copy()
        frame["ema_fast"] = self.compute_ema(frame["close"], self.ema_fast)
        frame["ema_slow"] = self.compute_ema(frame["close"], self.ema_slow)
        frame["rsi"] = self._compute_rsi(frame["close"])
        frame["atr"] = self.compute_atr(frame, self.atr_period)
        frame["vwap"] = self._compute_vwap(frame)
        frame["vol_sma"] = frame["volume"].rolling(self.vol_sma_period).mean()

        # EMA crossover flags (use shifted values to avoid look-ahead)
        frame["ema_cross_up"] = (frame["ema_fast"] > frame["ema_slow"]) & (
            frame["ema_fast"].shift(1) <= frame["ema_slow"].shift(1)
        )
        frame["ema_cross_down"] = (frame["ema_fast"] < frame["ema_slow"]) & (
            frame["ema_fast"].shift(1) >= frame["ema_slow"].shift(1)
        )
        return frame

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate_signals(
        self, symbol: Optional[str] = None, position_side: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Return a trade signal dict or ``None`` when no setup is present.

        Signal dict keys
        ----------------
        signal        : "BUY" | "SELL"
        entry_price   : float  – last close price
        stop_loss     : float  – ATR-based stop
        take_profit   : float  – ATR-based target
        pattern       : str    – human-readable setup description
        """
        if position_side:
            # No trailing-stop update implemented; caller manages open positions.
            return None

        min_bars = max(self.ema_slow, self.rsi_period, self.atr_period, self.vol_sma_period) + 10
        if len(self.data) < min_bars:
            return None

        frame = self._compute_indicators(self.data)
        current = frame.iloc[-1]
        prev = frame.iloc[-2]

        # ---- Gate 1: Volume spike ----------------------------------------
        if current["vol_sma"] == 0 or pd.isna(current["vol_sma"]):
            return None
        volume_ok = current["volume"] >= self.vol_spike_threshold * current["vol_sma"]
        if not volume_ok:
            return None

        # ---- Gate 2: ATR available (NaN during warm-up) ------------------
        if pd.isna(current["atr"]) or current["atr"] <= 0:
            return None

        close = float(current["close"])
        atr = float(current["atr"])
        rsi = float(current["rsi"])
        vwap = float(current["vwap"])
        ema_fast_now = float(current["ema_fast"])
        ema_slow_now = float(current["ema_slow"])

        # EMA crossover or momentum continuation within the last 3 bars
        recent_cross_up = frame["ema_cross_up"].iloc[-3:].any()
        recent_cross_down = frame["ema_cross_down"].iloc[-3:].any()

        # Continuation: fast EMA already above/below slow EMA
        trend_up = ema_fast_now > ema_slow_now
        trend_down = ema_fast_now < ema_slow_now

        # ---- LONG conditions ---------------------------------------------
        long_vwap = close > vwap                       # Price above daily VWAP
        long_ema = recent_cross_up or trend_up          # EMA momentum bullish
        long_rsi = 45.0 <= rsi <= 68.0                  # RSI in productive zone
        long_pullback = close >= ema_fast_now           # Not over-extended below fast EMA

        # ---- SHORT conditions --------------------------------------------
        short_vwap = close < vwap                       # Price below daily VWAP
        short_ema = recent_cross_down or trend_down     # EMA momentum bearish
        short_rsi = 32.0 <= rsi <= 55.0                 # RSI in productive zone
        short_pullback = close <= ema_fast_now          # Not over-extended above fast EMA

        risk = self.atr_stop_multiple * atr

        if long_vwap and long_ema and long_rsi and long_pullback:
            stop = close - risk
            target = close + self.reward_risk * risk
            pattern = (
                f"15m VWAP+EMA bullish | VWAP={vwap:.3f} close={close:.3f} "
                f"RSI={rsi:.1f} ATR={atr:.3f}"
            )
            logger.info(
                "ATOMUSDT BUY signal: entry=%.4f SL=%.4f TP=%.4f | %s",
                close, stop, target, pattern,
            )
            return {
                "signal": "BUY",
                "entry_price": close,
                "stop_loss": float(stop),
                "take_profit": float(target),
                "pattern": pattern,
            }

        if short_vwap and short_ema and short_rsi and short_pullback:
            stop = close + risk
            target = close - self.reward_risk * risk
            pattern = (
                f"15m VWAP+EMA bearish | VWAP={vwap:.3f} close={close:.3f} "
                f"RSI={rsi:.1f} ATR={atr:.3f}"
            )
            logger.info(
                "ATOMUSDT SELL signal: entry=%.4f SL=%.4f TP=%.4f | %s",
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
