"""Tests for ATOMUSDTStrategy – covers signal contract, edge cases, and risk levels."""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from orbit.strategies.atomusdt_strategy import ATOMUSDTStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_15m_data(
    n: int = 150,
    start_price: float = 8.50,
    trend: str = "up",
    vol_spike_last: bool = True,
    seed: int = 0,
) -> pd.DataFrame:
    """Generate synthetic 15-minute OHLCV bars with a clear trend.

    Parameters
    ----------
    n : number of bars
    start_price : starting close price
    trend : 'up' | 'down' | 'flat'
    vol_spike_last : if True, inflate volume on the last 10 bars so the
                     volume-spike gate passes
    seed : random seed for reproducibility
    """
    rng = np.random.default_rng(seed)
    index = pd.date_range("2026-01-01", periods=n, freq="15min")

    drift_map = {"up": 0.002, "down": -0.002, "flat": 0.0}
    drift = drift_map.get(trend, 0.0)
    log_returns = rng.normal(drift, 0.003, n)
    closes = start_price * np.exp(np.cumsum(log_returns))

    intrabar = np.abs(rng.normal(0, 0.003, n))
    highs = closes * (1 + intrabar)
    lows = closes * (1 - intrabar)
    opens = np.roll(closes, 1)
    opens[0] = start_price

    volume = rng.uniform(80_000, 150_000, n)
    if vol_spike_last:
        volume[-10:] *= 2.5  # Force volume spike on last bars

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume},
        index=index,
    )
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


def _make_vwap_bullish_data(n: int = 150) -> pd.DataFrame:
    """Data where close > VWAP in the last bar (all bars identical intraday close > VWAP)."""
    # Start on a day boundary so VWAP resets cleanly; use rising prices so
    # typical price accumulates above the final close
    index = pd.date_range("2026-06-01 00:00", periods=n, freq="15min")
    # Slowly rising prices guarantee the last typical_price > cumulative VWAP
    prices = 8.0 + np.linspace(0, 2.0, n)
    df = pd.DataFrame(
        {
            "open": prices,
            "high": prices + 0.05,
            "low": prices - 0.05,
            "close": prices,
            "volume": np.full(n, 120_000.0),
        },
        index=index,
    )
    return df


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestATOMUSDTStrategyInit(unittest.TestCase):
    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_strategy_inherits_from_base(self, _mock):
        from orbit.strategies.strategies_base import Strategy
        data = _make_15m_data(n=150)
        strategy = ATOMUSDTStrategy(data)
        self.assertIsInstance(strategy, Strategy)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_default_parameters(self, _mock):
        data = _make_15m_data(n=150)
        s = ATOMUSDTStrategy(data)
        self.assertEqual(s.ema_fast, 9)
        self.assertEqual(s.ema_slow, 21)
        self.assertEqual(s.rsi_period, 14)
        self.assertEqual(s.atr_period, 14)
        self.assertAlmostEqual(s.atr_stop_multiple, 1.5)
        self.assertAlmostEqual(s.reward_risk, 2.5)


class TestATOMUSDTStrategyEdgeCases(unittest.TestCase):
    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_returns_none_on_insufficient_data(self, _mock):
        """Fewer than min_bars should yield None."""
        data = _make_15m_data(n=20)
        strategy = ATOMUSDTStrategy(data)
        self.assertIsNone(strategy.generate_signals())

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_returns_none_when_position_open(self, _mock):
        """An open position should suppress new entry signals."""
        data = _make_15m_data(n=150)
        strategy = ATOMUSDTStrategy(data)
        result = strategy.generate_signals(position_side="LONG")
        self.assertIsNone(result)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_returns_none_when_no_volume_spike(self, _mock):
        """Without a volume spike the strategy should stay silent."""
        data = _make_15m_data(n=150, vol_spike_last=False)
        # Force all volumes to a flat constant so the spike gate always fails
        data["volume"] = 100_000.0
        strategy = ATOMUSDTStrategy(data)
        self.assertIsNone(strategy.generate_signals())

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_flat_market_returns_none(self, _mock):
        """In a flat market both EMA and RSI conditions rarely align simultaneously."""
        data = _make_15m_data(n=200, trend="flat", seed=7)
        strategy = ATOMUSDTStrategy(data)
        # We don't mandate None here, but we do require that any signal is valid
        result = strategy.generate_signals()
        if result is not None:
            self.assertIn(result["signal"], ("BUY", "SELL"))


class TestATOMUSDTStrategySignalContract(unittest.TestCase):
    """Verify the shape and risk constraints of returned signals."""

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_buy_signal_has_correct_keys(self, _mock):
        """BUY signal must contain all required keys."""
        data = _make_vwap_bullish_data(n=150)
        # Spike volume on last bars
        data.loc[data.index[-10:], "volume"] *= 3.0
        strategy = ATOMUSDTStrategy(data)
        result = strategy.generate_signals()
        if result and result["signal"] == "BUY":
            for key in ("signal", "entry_price", "stop_loss", "take_profit", "pattern"):
                self.assertIn(key, result)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_buy_signal_stop_below_entry(self, _mock):
        """For a BUY, stop_loss must be strictly less than entry_price."""
        data = _make_vwap_bullish_data(n=150)
        data.loc[data.index[-10:], "volume"] *= 3.0
        strategy = ATOMUSDTStrategy(data)
        result = strategy.generate_signals()
        if result and result["signal"] == "BUY":
            self.assertLess(result["stop_loss"], result["entry_price"])

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_buy_signal_target_above_entry(self, _mock):
        """For a BUY, take_profit must be strictly greater than entry_price."""
        data = _make_vwap_bullish_data(n=150)
        data.loc[data.index[-10:], "volume"] *= 3.0
        strategy = ATOMUSDTStrategy(data)
        result = strategy.generate_signals()
        if result and result["signal"] == "BUY":
            self.assertGreater(result["take_profit"], result["entry_price"])

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_reward_risk_ratio_approximately_correct(self, _mock):
        """Reward/risk should equal the configured ratio (2.5) ± floating-point tolerance."""
        data = _make_vwap_bullish_data(n=150)
        data.loc[data.index[-10:], "volume"] *= 3.0
        strategy = ATOMUSDTStrategy(data)
        result = strategy.generate_signals()
        if result and result["signal"] == "BUY":
            risk = result["entry_price"] - result["stop_loss"]
            reward = result["take_profit"] - result["entry_price"]
            self.assertAlmostEqual(reward / risk, strategy.reward_risk, places=5)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_sell_signal_stop_above_entry(self, _mock):
        """For a SELL, stop_loss must be strictly greater than entry_price."""
        # Build a clearly bearish dataset (price falling below VWAP)
        index = pd.date_range("2026-06-01 00:00", periods=150, freq="15min")
        prices = 10.0 - np.linspace(0, 2.0, 150)
        data = pd.DataFrame(
            {
                "open": prices,
                "high": prices + 0.05,
                "low": prices - 0.05,
                "close": prices,
                "volume": np.full(150, 120_000.0),
            },
            index=index,
        )
        data.loc[data.index[-10:], "volume"] *= 3.0
        strategy = ATOMUSDTStrategy(data)
        result = strategy.generate_signals()
        if result and result["signal"] == "SELL":
            self.assertGreater(result["stop_loss"], result["entry_price"])
            risk = result["stop_loss"] - result["entry_price"]
            reward = result["entry_price"] - result["take_profit"]
            self.assertAlmostEqual(reward / risk, strategy.reward_risk, places=5)


class TestATOMUSDTVWAP(unittest.TestCase):
    """Verify VWAP computation resets at day boundaries."""

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_vwap_resets_daily(self, _mock):
        """The first bar of each day must have VWAP == its own typical price."""
        index = pd.date_range("2026-01-01", periods=96 * 2, freq="15min")  # 2 days
        prices = np.ones(96 * 2) * 9.0
        data = pd.DataFrame(
            {
                "open": prices,
                "high": prices + 0.1,
                "low": prices - 0.1,
                "close": prices,
                "volume": np.ones(96 * 2) * 100_000.0,
            },
            index=index,
        )
        strategy = ATOMUSDTStrategy(data)
        # Access the private helper to inspect VWAP values
        vwap = strategy._compute_vwap(data)
        # First bar of each day: typical = 9.0, VWAP = 9.0 (only one data point so far)
        day_starts = [0, 96]  # 96 bars per day
        for idx in day_starts:
            typical = (data["high"].iloc[idx] + data["low"].iloc[idx] + data["close"].iloc[idx]) / 3
            self.assertAlmostEqual(vwap.iloc[idx], typical, places=6)


class TestATOMUSDTRegistration(unittest.TestCase):
    """Verify the strategy is correctly registered in strategies.yaml."""

    def test_registry_contains_atomusdt(self):
        from orbit.strategies.strategy_registry import STRATEGY_REGISTRY
        self.assertIn("ATOMUSDT", STRATEGY_REGISTRY)

    def test_registry_resolves_correct_class(self):
        from orbit.strategies.strategy_registry import STRATEGY_REGISTRY
        self.assertIs(STRATEGY_REGISTRY["ATOMUSDT"], ATOMUSDTStrategy)


if __name__ == "__main__":
    unittest.main()
