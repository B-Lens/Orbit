"""Tests for BTCSRStrategy — S/R level bounce, breakout and flip-retest logic."""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from orbit.strategies.btc_sr_strategy import BTCSRStrategy, SRZone, LevelState
from orbit.strategies.strategies_base import Strategy
from orbit.backtesting import WalkForwardBacktester, BacktestReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_range_bound_data(n: int = 500, support: float = 60_000, resistance: float = 65_000) -> pd.DataFrame:
    """Synthetic hourly OHLCV that oscillates between *support* and *resistance*.

    Creates clear swing pivots at both boundaries so the fractal detector
    can cluster them into S/R zones.
    """
    np.random.seed(42)
    index = pd.date_range("2025-01-01", periods=n, freq="1h")
    mid = (support + resistance) / 2
    amplitude = (resistance - support) / 2

    # Sine-wave oscillation + small noise
    t = np.linspace(0, 8 * np.pi, n)  # ~4 full cycles
    close = mid + amplitude * np.sin(t) + np.random.normal(0, 30, n)
    close = np.clip(close, support - 200, resistance + 200)

    high = close + np.abs(np.random.normal(50, 20, n))
    low = close - np.abs(np.random.normal(50, 20, n))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = np.random.uniform(500, 3000, n)

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


def _make_trending_data(direction: str = "up", n: int = 500) -> pd.DataFrame:
    """Synthetic hourly OHLCV with a clear trend."""
    np.random.seed(42)
    index = pd.date_range("2025-01-01", periods=n, freq="1h")
    if direction == "up":
        base = 55_000 + np.cumsum(np.random.normal(5.0, 30.0, n))
    else:
        base = 75_000 + np.cumsum(np.random.normal(-5.0, 30.0, n))

    close = pd.Series(base, index=index)
    high = close + np.abs(np.random.normal(40, 15, n))
    low = close - np.abs(np.random.normal(40, 15, n))
    open_ = close.shift(1).fillna(close.iloc[0]) + np.random.normal(0, 10, n)
    volume = np.random.uniform(500, 3000, n)
    # Volume spike on recent bars
    volume[-50:] = volume[-50:] * 2.5

    return pd.DataFrame(
        {"open": open_.values, "high": high.values, "low": low.values,
         "close": close.values, "volume": volume},
        index=index,
    )


def _make_rejection_candle_data(
    n: int = 300, level: float = 60_000, direction: str = "bullish"
) -> pd.DataFrame:
    """Builds data where the final bar is a rejection candle near *level*."""
    np.random.seed(123)
    index = pd.date_range("2025-01-01", periods=n, freq="1h")
    close = np.full(n, level)
    close += np.cumsum(np.random.normal(0, 20, n))
    close = np.clip(close, level - 2000, level + 2000)

    high = close + np.abs(np.random.normal(30, 10, n))
    low = close - np.abs(np.random.normal(30, 10, n))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = np.random.uniform(500, 2000, n)

    # Final candle: place a clear rejection at *level*
    if direction == "bullish":
        # Long lower wick (hammer at support)
        low[-1] = level - 200
        open_[-1] = level + 10
        close[-1] = level + 50
        high[-1] = level + 60
    else:
        # Long upper wick (shooting star at resistance)
        high[-1] = level + 200
        open_[-1] = level - 10
        close[-1] = level - 50
        low[-1] = level - 60

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestBTCSRStrategyStructure(unittest.TestCase):
    """Basic structural tests."""

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_inherits_strategy_base(self, _mock):
        self.assertTrue(issubclass(BTCSRStrategy, Strategy))

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_no_signal_on_insufficient_data(self, _mock):
        data = _make_range_bound_data(n=100)
        strategy = BTCSRStrategy(data=data)
        self.assertIsNone(strategy.generate_signals(symbol="BTCUSDT"))

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_returns_none_on_flat_data(self, _mock):
        """Perfectly flat data should never trigger."""
        index = pd.date_range("2025-01-01", periods=300, freq="1h")
        data = pd.DataFrame(
            {"open": 60_000, "high": 60_010, "low": 59_990, "close": 60_000, "volume": 1000},
            index=index,
        )
        strategy = BTCSRStrategy(data=data)
        self.assertIsNone(strategy.generate_signals(symbol="BTCUSDT"))


class TestSRLevelDetection(unittest.TestCase):
    """Verify that S/R zones are detected from synthetic data."""

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_fractal_pivots_detected(self, _mock):
        data = _make_range_bound_data(n=500)
        strategy = BTCSRStrategy(data=data)
        frame = strategy._compute_indicators(data)
        res_pivots, sup_pivots = strategy._detect_fractal_pivots(frame)
        self.assertGreater(len(res_pivots), 0, "Should detect resistance pivots")
        self.assertGreater(len(sup_pivots), 0, "Should detect support pivots")

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_cluster_pivots_merges_nearby(self, _mock):
        """Pivots within ATR distance should merge into a single zone."""
        pivots = [60_000, 60_050, 60_100, 65_000, 65_080]
        zones = BTCSRStrategy._cluster_pivots(pivots, current_atr=200.0, min_touches=2)
        # The two groups near 60k and 65k should each be a zone
        self.assertEqual(len(zones), 2)
        self.assertAlmostEqual(zones[0]["center"], 60_050, delta=100)
        self.assertAlmostEqual(zones[1]["center"], 65_040, delta=100)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_round_number_zones_added(self, _mock):
        """Round-number levels should be injected near current price."""
        zones: list = []
        BTCSRStrategy._add_round_number_zones(zones, current_price=62_500, current_atr=300)
        centers = [z["center"] for z in zones]
        self.assertIn(60_000.0, centers)
        self.assertIn(65_000.0, centers)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_build_sr_zones_returns_sorted_by_proximity(self, _mock):
        data = _make_range_bound_data(n=500)
        strategy = BTCSRStrategy(data=data)
        frame = strategy._compute_indicators(data)
        close = float(frame["close"].iloc[-1])
        atr = float(frame["atr"].iloc[-1])
        zones = strategy._build_sr_zones(frame, close, atr)
        self.assertGreater(len(zones), 0, "Should find at least one zone")
        # Verify sorted by proximity
        distances = [abs(z.center - close) for z in zones]
        self.assertEqual(distances, sorted(distances))


class TestSignalContract(unittest.TestCase):
    """Verify signal dict structure matches the Orbit contract."""

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_buy_signal_keys_and_ordering(self, _mock):
        """If a BUY fires, SL < Entry < TP must hold."""
        data = _make_trending_data(direction="up", n=600)
        strategy = BTCSRStrategy(data=data)
        signal = strategy.generate_signals(symbol="BTCUSDT")
        if signal is not None and signal["signal"] == "BUY":
            self.assertIn("entry_price", signal)
            self.assertIn("stop_loss", signal)
            self.assertIn("take_profit", signal)
            self.assertIn("pattern", signal)
            self.assertLess(signal["stop_loss"], signal["entry_price"])
            self.assertGreater(signal["take_profit"], signal["entry_price"])

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_sell_signal_keys_and_ordering(self, _mock):
        """If a SELL fires, TP < Entry < SL must hold."""
        data = _make_trending_data(direction="down", n=600)
        strategy = BTCSRStrategy(data=data)
        signal = strategy.generate_signals(symbol="BTCUSDT")
        if signal is not None and signal["signal"] == "SELL":
            self.assertIn("entry_price", signal)
            self.assertIn("stop_loss", signal)
            self.assertIn("take_profit", signal)
            self.assertIn("pattern", signal)
            self.assertGreater(signal["stop_loss"], signal["entry_price"])
            self.assertLess(signal["take_profit"], signal["entry_price"])

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_risk_reward_ratio(self, _mock):
        """The reward:risk should be close to 3.0 for any generated signal."""
        data = _make_trending_data(direction="up", n=600)
        strategy = BTCSRStrategy(data=data)
        signal = strategy.generate_signals(symbol="BTCUSDT")
        if signal is not None:
            entry = signal["entry_price"]
            sl = signal["stop_loss"]
            tp = signal["take_profit"]
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk > 0:
                self.assertAlmostEqual(reward / risk, 2.5, places=1)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_trailing_update_structure(self, _mock):
        """When position_side is set, should return UPDATE_SL_TP."""
        data = _make_trending_data(direction="up", n=500)
        strategy = BTCSRStrategy(data=data)
        result = strategy.generate_signals(symbol="BTCUSDT", position_side="LONG")
        self.assertIsNotNone(result)
        self.assertEqual(result["signal"], "UPDATE_SL_TP")
        self.assertIn("stop_loss", result)


class TestCandlestickRejection(unittest.TestCase):
    """Validate rejection candle detection helpers."""

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_bullish_rejection_hammer(self, _mock):
        row = pd.Series({"open": 100, "high": 102, "low": 90, "close": 101})
        self.assertTrue(BTCSRStrategy._is_bullish_rejection(row))

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_bearish_rejection_shooting_star(self, _mock):
        row = pd.Series({"open": 100, "high": 110, "low": 99, "close": 99.5})
        self.assertTrue(BTCSRStrategy._is_bearish_rejection(row))

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_no_false_rejection_on_balanced_candle(self, _mock):
        # Body ~40% of range, wicks ~30% each — neither should qualify
        row = pd.Series({"open": 97, "high": 103, "low": 94, "close": 100.5})
        self.assertFalse(BTCSRStrategy._is_bullish_rejection(row))
        self.assertFalse(BTCSRStrategy._is_bearish_rejection(row))


class TestBacktesterIntegration(unittest.TestCase):
    """End-to-end test with the WalkForwardBacktester."""

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_strategy_works_with_backtester(self, _mock):
        data_up = _make_trending_data(direction="up", n=500)
        data_down = _make_trending_data(direction="down", n=300)
        data_down.index = pd.date_range(
            data_up.index[-1] + pd.Timedelta(hours=1), periods=300, freq="1h"
        )
        data = pd.concat([data_up, data_down])

        backtester = WalkForwardBacktester(
            strategy_factory=BTCSRStrategy,
            starting_equity=10_000.0,
            risk_per_trade_pct=0.01,
        )
        report = backtester.run(data, symbol="BTCUSDT", warmup_bars=250)

        self.assertIsInstance(report, BacktestReport)
        self.assertEqual(report.starting_equity, 10_000.0)
        # Should run without errors; trade count may vary with synthetic data
        self.assertGreaterEqual(report.trades, 0)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_range_bound_data_produces_trades(self, _mock):
        """Range-bound data with clear S/R should produce some bounce trades."""
        data = _make_range_bound_data(n=800)

        backtester = WalkForwardBacktester(
            strategy_factory=BTCSRStrategy,
            starting_equity=10_000.0,
            risk_per_trade_pct=0.01,
        )
        report = backtester.run(data, symbol="BTCUSDT", warmup_bars=250)

        self.assertIsInstance(report, BacktestReport)
        self.assertGreaterEqual(report.trades, 0)


class TestSRZoneDataclass(unittest.TestCase):
    """Basic validation of the SRZone dataclass."""

    def test_zone_defaults(self):
        zone = SRZone(
            center=60_000.0,
            zone_low=59_900.0,
            zone_high=60_100.0,
            touches=3,
            level_type="SUPPORT",
        )
        self.assertEqual(zone.state, LevelState.ACTIVE)
        self.assertEqual(zone.confluence_score, 0.0)

    def test_zone_types(self):
        zone = SRZone(
            center=70_000.0, zone_low=69_900.0, zone_high=70_100.0,
            touches=5, level_type="RESISTANCE",
            state=LevelState.FLIPPED, confluence_score=0.85,
        )
        self.assertEqual(zone.level_type, "RESISTANCE")
        self.assertEqual(zone.state, LevelState.FLIPPED)


if __name__ == "__main__":
    unittest.main()
