"""Support/Resistance level strategy for BTCUSDT.

Mimics how a discretionary trader reads S/R zones: detect swing-pivot
clusters, score their confluence, and trade bounces, breakouts, and
S/R-flip retests with volatility-adaptive risk management.

Timeframe : 1H (resampled from 15 m production feed when necessary).
Lookback  : ~500 hourly bars for level detection; ~250 warmup minimum.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

from orbit.strategies.strategies_base import Strategy

logger = logging.getLogger("Orbit")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MIN_BARS = 250
_FRACTAL_WINDOW = 8
_FRACTAL_PROMINENCE_MULT = 0.3
_CLUSTER_ATR_MULT = 1.0
_MIN_TOUCHES = 2
_ROUND_NUMBER_STEP = 2_500.0  # $2 500 round-number grid
_ZONE_BUFFER_ATR = 0.75
_RSI_PERIOD = 14
_ATR_PERIOD = 14
_EMA_TREND_PERIOD = 200
_VOLUME_MA_PERIOD = 20
_VOLUME_SPIKE_MULT = 1.3  # RVOL threshold for breakout
_MAX_LEVEL_AGE_BARS = 500  # Decay stale levels


# ---------------------------------------------------------------------------
# Support / Resistance Zone data model
# ---------------------------------------------------------------------------
class LevelState(Enum):
    ACTIVE = "ACTIVE"
    TESTED = "TESTED"
    BROKEN = "BROKEN"
    FLIPPED = "FLIPPED"


@dataclass
class SRZone:
    """A single clustered support/resistance zone."""

    center: float
    zone_low: float
    zone_high: float
    touches: int
    level_type: str  # "SUPPORT" or "RESISTANCE"
    state: LevelState = LevelState.ACTIVE
    created_bar: int = 0
    last_tested_bar: int = 0
    confluence_score: float = 0.0


# ---------------------------------------------------------------------------
# Strategy class
# ---------------------------------------------------------------------------
@dataclass
class BTCSRStrategy(Strategy):
    """Trade BTCUSDT bounces, breakouts and S/R flips at clustered levels.

    1. **Level detection** — fractal swing pivots clustered into zones via a
       simple distance-merge (no sklearn dependency).
    2. **Confluence scoring** — touches, volume profile alignment, round-number
       proximity, and recency each contribute to a normalised 0-1 score.
    3. **Signal generation** — three trade setups:
       * *Bounce*: price enters a high-confluence zone and prints a rejection
         candle while RSI is not extreme in the wrong direction.
       * *Breakout*: price closes through a zone on elevated volume (RVOL ≥ 1.5×).
       * *S/R Flip retest*: a recently broken level retested from the opposite
         side on declining volume.
    4. **Risk management** — ATR-based stop behind the zone; 3:1 reward-risk.
    """

    data: pd.DataFrame
    atr_period: int = _ATR_PERIOD
    ema_trend_period: int = _EMA_TREND_PERIOD
    atr_stop_multiple: float = 1.5
    reward_risk: float = 2.5
    symbol: str = "BITCOIN"

    def __post_init__(self) -> None:
        super().__init__(self.data)

    # ------------------------------------------------------------------
    # Timeframe helpers (shared with BTCStrategy)
    # ------------------------------------------------------------------
    def _to_hourly(self) -> Tuple[pd.DataFrame, bool]:
        """Return completed hourly candles and whether the latest just closed."""
        if self.data.empty or not isinstance(self.data.index, pd.DatetimeIndex):
            return self.data.copy(), False

        intervals = self.data.index.to_series().diff().dropna()
        interval = intervals.median() if not intervals.empty else pd.Timedelta(hours=1)
        if interval >= pd.Timedelta(hours=1):
            return self.data.copy(), True

        grouped = self.data.resample("1h")
        hourly = grouped.agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        expected_bars = round(pd.Timedelta(hours=1) / interval)
        hourly = hourly[grouped.size() == expected_bars].dropna()
        if hourly.empty:
            return hourly, False
        latest_closed = self.data.index[-1] - hourly.index[-1] == pd.Timedelta(minutes=45)
        return hourly, latest_closed

    # ------------------------------------------------------------------
    # Indicator computation
    # ------------------------------------------------------------------
    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add ATR, EMA-200, RSI, RVOL to *df* in-place and return it."""
        out = df.copy()

        # ATR (Wilder EMA)
        prev_close = out["close"].shift(1)
        tr = pd.concat(
            [
                out["high"] - out["low"],
                (out["high"] - prev_close).abs(),
                (out["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        out["atr"] = tr.ewm(alpha=1 / self.atr_period, adjust=False).mean()

        # EMA trend filter
        out["ema_trend"] = out["close"].ewm(span=self.ema_trend_period, adjust=False).mean()

        # RSI (Wilder)
        delta = out["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=_RSI_PERIOD - 1, min_periods=_RSI_PERIOD).mean()
        avg_loss = loss.ewm(com=_RSI_PERIOD - 1, min_periods=_RSI_PERIOD).mean()
        rs = avg_gain / avg_loss
        out["rsi"] = 100 - (100 / (1 + rs))

        # Relative Volume
        vol_ma = out["volume"].rolling(_VOLUME_MA_PERIOD).mean()
        out["rvol"] = out["volume"] / vol_ma

        return out

    # ------------------------------------------------------------------
    # S/R level detection pipeline
    # ------------------------------------------------------------------
    @staticmethod
    def _detect_fractal_pivots(df: pd.DataFrame) -> Tuple[List[float], List[float]]:
        """Return lists of resistance-pivot and support-pivot prices."""
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)

        atr_vals = (df["high"] - df["low"]).rolling(14).mean().bfill().values.astype(float)
        prominence = float(np.mean(atr_vals[-_FRACTAL_WINDOW:])) * _FRACTAL_PROMINENCE_MULT

        res_idx, _ = find_peaks(highs, distance=_FRACTAL_WINDOW, prominence=prominence)
        sup_idx, _ = find_peaks(-lows, distance=_FRACTAL_WINDOW, prominence=prominence)

        return [float(highs[i]) for i in res_idx], [float(lows[i]) for i in sup_idx]

    @staticmethod
    def _cluster_pivots(
        pivots: List[float], current_atr: float, min_touches: int = _MIN_TOUCHES
    ) -> List[Dict[str, Any]]:
        """Merge nearby pivot prices into zones using simple distance-based clustering.

        This avoids an sklearn dependency while behaving like single-linkage
        clustering with an ATR-scaled distance threshold.
        """
        if len(pivots) < min_touches:
            return []

        sorted_pivots = sorted(pivots)
        eps = current_atr * _CLUSTER_ATR_MULT

        clusters: List[List[float]] = []
        current_cluster: List[float] = [sorted_pivots[0]]

        for price in sorted_pivots[1:]:
            if price - current_cluster[-1] <= eps:
                current_cluster.append(price)
            else:
                clusters.append(current_cluster)
                current_cluster = [price]
        clusters.append(current_cluster)

        zones = []
        for cluster in clusters:
            if len(cluster) < min_touches:
                continue
            zones.append(
                {
                    "center": float(np.mean(cluster)),
                    "zone_low": float(np.min(cluster)),
                    "zone_high": float(np.max(cluster)),
                    "touches": len(cluster),
                }
            )
        return zones

    @staticmethod
    def _add_round_number_zones(
        zones: List[Dict[str, Any]], current_price: float, current_atr: float
    ) -> List[Dict[str, Any]]:
        """Inject psychological round-number levels that are near current price."""
        # Cover ±20 % around current price
        low_bound = current_price * 0.80
        high_bound = current_price * 1.20
        rn = np.arange(
            np.floor(low_bound / _ROUND_NUMBER_STEP) * _ROUND_NUMBER_STEP,
            high_bound,
            _ROUND_NUMBER_STEP,
        )
        eps = current_atr * _CLUSTER_ATR_MULT
        for level in rn:
            # Only add if not already covered by an existing zone
            already_covered = any(abs(z["center"] - level) < eps for z in zones)
            if not already_covered:
                zones.append(
                    {
                        "center": float(level),
                        "zone_low": float(level - current_atr * 0.3),
                        "zone_high": float(level + current_atr * 0.3),
                        "touches": 1,  # implicit single "touch"
                        "is_round_number": True,
                    }
                )
        return zones

    def _build_sr_zones(
        self, df: pd.DataFrame, current_price: float, current_atr: float
    ) -> List[SRZone]:
        """Full pipeline: detect → cluster → score → classify."""
        res_pivots, sup_pivots = self._detect_fractal_pivots(df)
        all_pivots = res_pivots + sup_pivots
        raw_zones = self._cluster_pivots(all_pivots, current_atr)
        raw_zones = self._add_round_number_zones(raw_zones, current_price, current_atr)

        # Filter: only keep zones within 10% of current price
        max_distance = current_price * 0.10
        raw_zones = [z for z in raw_zones if abs(z["center"] - current_price) <= max_distance]

        sr_zones: List[SRZone] = []
        max_touches = max((z["touches"] for z in raw_zones), default=1)

        for z in raw_zones:
            level_type = "RESISTANCE" if z["center"] > current_price else "SUPPORT"

            # Confluence scoring (0-1)
            touch_score = min(z["touches"] / max(max_touches, 1), 1.0)
            distance_pct = abs(z["center"] - current_price) / current_price
            proximity_score = max(1.0 - distance_pct * 10, 0.0)  # closer = higher
            round_bonus = 0.15 if z.get("is_round_number") else 0.0
            score = 0.50 * touch_score + 0.35 * proximity_score + round_bonus
            score = min(score, 1.0)

            sr_zones.append(
                SRZone(
                    center=z["center"],
                    zone_low=z["zone_low"],
                    zone_high=z["zone_high"],
                    touches=z["touches"],
                    level_type=level_type,
                    confluence_score=score,
                )
            )

        # Sort by proximity to current price (nearest first), limit to 8
        sr_zones.sort(key=lambda z: abs(z.center - current_price))
        return sr_zones[:8]

    # ------------------------------------------------------------------
    # Candlestick rejection helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _is_bullish_rejection(row: pd.Series) -> bool:
        """Long lower wick relative to body → demand absorption."""
        body = abs(row["close"] - row["open"])
        lower_wick = min(row["close"], row["open"]) - row["low"]
        bar_range = row["high"] - row["low"]
        if bar_range == 0:
            return False
        return lower_wick / bar_range >= 0.45 and body / bar_range <= 0.40

    @staticmethod
    def _is_bearish_rejection(row: pd.Series) -> bool:
        """Long upper wick → supply absorption."""
        body = abs(row["close"] - row["open"])
        upper_wick = row["high"] - max(row["close"], row["open"])
        bar_range = row["high"] - row["low"]
        if bar_range == 0:
            return False
        return upper_wick / bar_range >= 0.45 and body / bar_range <= 0.40

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------
    def generate_signals(
        self, symbol: Optional[str] = None, position_side: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        hourly, latest_closed = self._to_hourly()
        if len(hourly) < _MIN_BARS:
            return None

        frame = self._compute_indicators(hourly)

        # If we already hold a position, emit a trailing-stop update
        if position_side:
            return self._trailing_update(frame, position_side)

        # Only act on freshly closed candles in production
        if not latest_closed:
            return None

        current = frame.iloc[-1]
        prev = frame.iloc[-2]
        close = float(current["close"])
        atr = float(current["atr"])
        rsi = float(current["rsi"])
        rvol = float(current["rvol"])
        ema_trend = float(current["ema_trend"])

        # Build S/R map
        sr_zones = self._build_sr_zones(frame, close, atr)
        if not sr_zones:
            return None

        # ---- Try each setup in priority order ----

        # 1. S/R Flip retest (highest edge)
        signal = self._check_flip_retest(frame, sr_zones, close, atr, rsi, rvol, ema_trend)
        if signal:
            return signal

        # 2. Bounce at high-confluence zone
        signal = self._check_bounce(frame, sr_zones, close, atr, rsi, rvol, ema_trend)
        if signal:
            return signal

        # 3. Breakout through a zone
        signal = self._check_breakout(frame, sr_zones, close, atr, rsi, rvol, ema_trend)
        if signal:
            return signal

        return None

    # ------------------------------------------------------------------
    # Setup detectors
    # ------------------------------------------------------------------
    def _check_bounce(
        self,
        frame: pd.DataFrame,
        zones: List[SRZone],
        close: float,
        atr: float,
        rsi: float,
        rvol: float,
        ema_trend: float,
    ) -> Optional[Dict[str, Any]]:
        """Buy at support / sell at resistance when rejection candle + RSI filter."""
        current = frame.iloc[-1]
        buffer = atr * _ZONE_BUFFER_ATR

        for zone in zones:
            if zone.confluence_score < 0.25:
                continue

            # --- Support bounce (long) ---
            if (
                zone.level_type == "SUPPORT"
                and zone.zone_low - buffer <= float(current["low"]) <= zone.zone_high + buffer
                and close > zone.center  # closed above zone
                and self._is_bullish_rejection(current)
                and rsi < 65  # not overbought
                and close >= ema_trend * 0.95  # allow if within 5% below EMA
            ):
                stop = zone.zone_low - self.atr_stop_multiple * atr
                risk = close - stop
                if risk <= 0 or risk > 4 * atr:
                    continue
                target = close + self.reward_risk * risk
                pattern = (
                    f"S/R Bounce LONG at support {zone.center:.0f} "
                    f"(score={zone.confluence_score:.2f}, touches={zone.touches}, RSI={rsi:.1f})"
                )
                logger.info("Generated BUY (bounce) at %.2f, SL %.2f, TP %.2f", close, stop, target)
                return {
                    "signal": "BUY",
                    "entry_price": close,
                    "stop_loss": float(stop),
                    "take_profit": float(target),
                    "pattern": pattern,
                }

            # --- Resistance bounce (short) ---
            if (
                zone.level_type == "RESISTANCE"
                and zone.zone_low - buffer <= float(current["high"]) <= zone.zone_high + buffer
                and close < zone.center  # closed below zone
                and self._is_bearish_rejection(current)
                and rsi > 35  # not oversold
                and close <= ema_trend * 1.05  # allow if within 5% above EMA
            ):
                stop = zone.zone_high + self.atr_stop_multiple * atr
                risk = stop - close
                if risk <= 0 or risk > 4 * atr:
                    continue
                target = close - self.reward_risk * risk
                pattern = (
                    f"S/R Bounce SHORT at resistance {zone.center:.0f} "
                    f"(score={zone.confluence_score:.2f}, touches={zone.touches}, RSI={rsi:.1f})"
                )
                logger.info("Generated SELL (bounce) at %.2f, SL %.2f, TP %.2f", close, stop, target)
                return {
                    "signal": "SELL",
                    "entry_price": close,
                    "stop_loss": float(stop),
                    "take_profit": float(target),
                    "pattern": pattern,
                }

        return None

    def _check_breakout(
        self,
        frame: pd.DataFrame,
        zones: List[SRZone],
        close: float,
        atr: float,
        rsi: float,
        rvol: float,
        ema_trend: float,
    ) -> Optional[Dict[str, Any]]:
        """Trade decisive breaks through S/R on elevated volume."""
        prev = frame.iloc[-2]
        prev_close = float(prev["close"])

        for zone in zones:
            if zone.confluence_score < 0.25:
                continue

            # --- Bullish breakout through resistance ---
            if (
                zone.level_type == "RESISTANCE"
                and prev_close < zone.zone_high  # was below zone
                and close > zone.zone_high  # now closed above
                and rvol >= _VOLUME_SPIKE_MULT
                and close > ema_trend
            ):
                stop = zone.center - self.atr_stop_multiple * atr
                risk = close - stop
                if risk <= 0:
                    continue
                target = close + self.reward_risk * risk
                pattern = (
                    f"S/R Breakout LONG through {zone.center:.0f} "
                    f"(RVOL={rvol:.1f}, score={zone.confluence_score:.2f})"
                )
                logger.info("Generated BUY (breakout) at %.2f, SL %.2f, TP %.2f", close, stop, target)
                return {
                    "signal": "BUY",
                    "entry_price": close,
                    "stop_loss": float(stop),
                    "take_profit": float(target),
                    "pattern": pattern,
                }

            # --- Bearish breakout through support ---
            if (
                zone.level_type == "SUPPORT"
                and prev_close > zone.zone_low  # was above zone
                and close < zone.zone_low  # now closed below
                and rvol >= _VOLUME_SPIKE_MULT
                and close < ema_trend
            ):
                stop = zone.center + self.atr_stop_multiple * atr
                risk = stop - close
                if risk <= 0:
                    continue
                target = close - self.reward_risk * risk
                pattern = (
                    f"S/R Breakout SHORT through {zone.center:.0f} "
                    f"(RVOL={rvol:.1f}, score={zone.confluence_score:.2f})"
                )
                logger.info("Generated SELL (breakout) at %.2f, SL %.2f, TP %.2f", close, stop, target)
                return {
                    "signal": "SELL",
                    "entry_price": close,
                    "stop_loss": float(stop),
                    "take_profit": float(target),
                    "pattern": pattern,
                }

        return None

    def _check_flip_retest(
        self,
        frame: pd.DataFrame,
        zones: List[SRZone],
        close: float,
        atr: float,
        rsi: float,
        rvol: float,
        ema_trend: float,
    ) -> Optional[Dict[str, Any]]:
        """Detect when a recently broken level is retested from the other side.

        A broken resistance retested as support = long.
        A broken support retested as resistance = short.

        Detection heuristic: the zone is classified one way (e.g. SUPPORT) but
        over the last 20 bars, price was *above* the zone for the majority and
        only recently dipped *into* the zone — implying a prior break upward
        and a pullback retest.
        """
        lookback = 20
        recent_closes = frame["close"].iloc[-lookback:].values.astype(float)

        for zone in zones:
            if zone.confluence_score < 0.25:
                continue

            buffer = atr * _ZONE_BUFFER_ATR

            # --- Resistance broken → retest as support (long) ---
            if zone.level_type == "SUPPORT":
                bars_above = np.sum(recent_closes > zone.zone_high)
                fraction_above = bars_above / lookback
                touching_zone = zone.zone_low - buffer <= float(frame.iloc[-1]["low"]) <= zone.zone_high + buffer
                pullback_on_low_vol = rvol < 1.2

                if fraction_above >= 0.6 and touching_zone and pullback_on_low_vol and close > zone.center:
                    if not self._is_bullish_rejection(frame.iloc[-1]):
                        continue
                    stop = zone.zone_low - self.atr_stop_multiple * atr
                    risk = close - stop
                    if risk <= 0:
                        continue
                    target = close + self.reward_risk * risk
                    pattern = (
                        f"S/R Flip retest LONG at {zone.center:.0f} "
                        f"(former resistance → support, score={zone.confluence_score:.2f})"
                    )
                    logger.info("Generated BUY (flip retest) at %.2f, SL %.2f, TP %.2f", close, stop, target)
                    return {
                        "signal": "BUY",
                        "entry_price": close,
                        "stop_loss": float(stop),
                        "take_profit": float(target),
                        "pattern": pattern,
                    }

            # --- Support broken → retest as resistance (short) ---
            if zone.level_type == "RESISTANCE":
                bars_below = np.sum(recent_closes < zone.zone_low)
                fraction_below = bars_below / lookback
                touching_zone = zone.zone_low - buffer <= float(frame.iloc[-1]["high"]) <= zone.zone_high + buffer
                pullback_on_low_vol = rvol < 1.2

                if fraction_below >= 0.6 and touching_zone and pullback_on_low_vol and close < zone.center:
                    if not self._is_bearish_rejection(frame.iloc[-1]):
                        continue
                    stop = zone.zone_high + self.atr_stop_multiple * atr
                    risk = stop - close
                    if risk <= 0:
                        continue
                    target = close - self.reward_risk * risk
                    pattern = (
                        f"S/R Flip retest SHORT at {zone.center:.0f} "
                        f"(former support → resistance, score={zone.confluence_score:.2f})"
                    )
                    logger.info("Generated SELL (flip retest) at %.2f, SL %.2f, TP %.2f", close, stop, target)
                    return {
                        "signal": "SELL",
                        "entry_price": close,
                        "stop_loss": float(stop),
                        "take_profit": float(target),
                        "pattern": pattern,
                    }

        return None

    # ------------------------------------------------------------------
    # Trailing stop (position management)
    # ------------------------------------------------------------------
    def _trailing_update(
        self, frame: pd.DataFrame, position_side: str
    ) -> Dict[str, Any]:
        """ATR-based trailing stop while in a position."""
        current = frame.iloc[-1]
        lookback = 12
        if position_side == "LONG":
            stop = float(
                frame["high"].iloc[-lookback:].max()
                - self.atr_stop_multiple * current["atr"]
            )
        else:
            stop = float(
                frame["low"].iloc[-lookback:].min()
                + self.atr_stop_multiple * current["atr"]
            )
        return {"signal": "UPDATE_SL_TP", "stop_loss": stop, "take_profit": None}
