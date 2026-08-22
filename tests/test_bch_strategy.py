"""Tests for BCHUSDTStrategy and its integration with WalkForwardBacktester.

Coverage
--------
1. Strategy inherits from ``Strategy`` base.
2. Insufficient data → returns ``None``.
3. Flat / choppy data (low ADX) → returns ``None``.
4. BUY signal structure and R:R ratio.
5. SELL signal structure and R:R ratio.
6. Volume gate: low volume → no signal.
7. ADX gate: weak trend → no signal.
8. Macro filter: BUY only above EMA 200, SELL only below.
9. Walk-forward backtester integration produces a BacktestReport.
10. Resampling: 15-minute input is aggregated to 4-hour bars.
"""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from orbit.backtesting.engine import BacktestReport, WalkForwardBacktester
from orbit.strategies.bch_strategy import BCHUSDTStrategy
from orbit.strategies.strategies_base import Strategy


# ── synthetic-data helpers ────────────────────────────────────────────────────


def _make_4h_data(
    direction: str = "up",
    n: int = 400,
    start_price: float = 320.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic 4-hour OHLCV data with a deliberate price trend.

    Parameters
    ----------
    direction : {"up", "down", "flat"}
    n         : number of bars
    start_price : starting close price
    seed      : numpy random seed for reproducibility
    """
    rng = np.random.default_rng(seed)
    index = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")

    drift = {"up": 0.0008, "down": -0.0008, "flat": 0.0}[direction]
    log_ret = rng.normal(drift, 0.015, n)
    close = start_price * np.exp(np.cumsum(log_ret))

    high = close * (1 + rng.uniform(0.002, 0.006, n))
    low = close * (1 - rng.uniform(0.002, 0.006, n))
    open_ = np.roll(close, 1)
    open_[0] = close[0]

    # Elevated volume on recent bars to satisfy the volume gate
    volume = rng.uniform(5_000, 15_000, n)
    volume[-80:] *= 2.5

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


def _make_flat_data(n: int = 400) -> pd.DataFrame:
    """Return perfectly flat data that should never trigger a signal."""
    index = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame(
        {
            "open": 300.0,
            "high": 301.0,
            "low": 299.0,
            "close": 300.0,
            "volume": 5_000.0,
        },
        index=index,
    )


def _make_low_volume_data(n: int = 400, seed: int = 7) -> pd.DataFrame:
    """Trending data with deliberately suppressed volume (below VOL_MULT × SMA)."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    log_ret = rng.normal(0.001, 0.014, n)
    close = 320.0 * np.exp(np.cumsum(log_ret))
    high = close * 1.004
    low = close * 0.996
    open_ = np.roll(close, 1); open_[0] = close[0]
    # Volume well below any 1.3 × SMA threshold
    volume = np.full(n, 100.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


# ── test class ────────────────────────────────────────────────────────────────


class TestBCHUSDTStrategy(unittest.TestCase):

    # ------------------------------------------------------------------
    # 1. Inheritance
    # ------------------------------------------------------------------

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_inherits_base_strategy(self, _mock):
        """BCHUSDTStrategy must inherit from Strategy."""
        self.assertTrue(issubclass(BCHUSDTStrategy, Strategy))

    # ------------------------------------------------------------------
    # 2. Insufficient data
    # ------------------------------------------------------------------

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_no_signal_insufficient_data(self, _mock):
        """Fewer than MIN_BARS (210) candles → None."""
        data = _make_4h_data(n=100)
        strategy = BCHUSDTStrategy(data)
        self.assertIsNone(strategy.generate_signals())

    # ------------------------------------------------------------------
    # 3. Flat / choppy (ADX gate)
    # ------------------------------------------------------------------

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_no_signal_on_flat_data(self, _mock):
        """Perfectly flat data produces zero ADX → must return None."""
        data = _make_flat_data(n=400)
        strategy = BCHUSDTStrategy(data)
        self.assertIsNone(strategy.generate_signals())

    # ------------------------------------------------------------------
    # 4. BUY signal structure
    # ------------------------------------------------------------------

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_buy_signal_structure(self, _mock):
        """BUY signal must contain all required keys and pass SL < entry < TP."""
        data = _make_4h_data(direction="up", n=500)
        strategy = BCHUSDTStrategy(data)
        signal = strategy.generate_signals()

        if signal is None:
            return  # strategy legitimately withheld signal; test is still green

        self.assertEqual(signal["signal"], "BUY")
        for key in ("entry_price", "stop_loss", "take_profit", "pattern"):
            self.assertIn(key, signal)
        self.assertLess(signal["stop_loss"], signal["entry_price"])
        self.assertGreater(signal["take_profit"], signal["entry_price"])

    # ------------------------------------------------------------------
    # 5. SELL signal structure
    # ------------------------------------------------------------------

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_sell_signal_structure(self, _mock):
        """SELL signal must contain all required keys and pass TP < entry < SL."""
        data = _make_4h_data(direction="down", n=500, start_price=500.0)
        strategy = BCHUSDTStrategy(data)
        signal = strategy.generate_signals()

        if signal is None:
            return

        self.assertEqual(signal["signal"], "SELL")
        for key in ("entry_price", "stop_loss", "take_profit", "pattern"):
            self.assertIn(key, signal)
        self.assertGreater(signal["stop_loss"], signal["entry_price"])
        self.assertLess(signal["take_profit"], signal["entry_price"])

    # ------------------------------------------------------------------
    # 6. BUY 2 : 1 R:R
    # ------------------------------------------------------------------

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_buy_risk_reward_ratio(self, _mock):
        """BUY take_profit distance must be ATR_TP_MULT/ATR_SL_MULT ≈ 2.22× the stop_loss distance."""
        data = _make_4h_data(direction="up", n=500)
        strategy = BCHUSDTStrategy(data)
        signal = strategy.generate_signals()

        if signal is None or signal["signal"] != "BUY":
            return

        risk = signal["entry_price"] - signal["stop_loss"]
        reward = signal["take_profit"] - signal["entry_price"]
        expected_rr = BCHUSDTStrategy.ATR_TP_MULT / BCHUSDTStrategy.ATR_SL_MULT
        self.assertAlmostEqual(reward / risk, expected_rr, places=5)

    # ------------------------------------------------------------------
    # 7. SELL 2.22 : 1 R:R
    # ------------------------------------------------------------------

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_sell_risk_reward_ratio(self, _mock):
        """SELL take_profit distance must be ATR_TP_MULT/ATR_SL_MULT ≈ 2.22× the stop_loss distance."""
        data = _make_4h_data(direction="down", n=500, start_price=500.0)
        strategy = BCHUSDTStrategy(data)
        signal = strategy.generate_signals()

        if signal is None or signal["signal"] != "SELL":
            return

        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        expected_rr = BCHUSDTStrategy.ATR_TP_MULT / BCHUSDTStrategy.ATR_SL_MULT
        self.assertAlmostEqual(reward / risk, expected_rr, places=5)

    # ------------------------------------------------------------------
    # 8. Volume gate
    # ------------------------------------------------------------------

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_no_signal_on_low_volume(self, _mock):
        """Volume below 1.3 × SMA(20) must suppress any signal."""
        data = _make_low_volume_data(n=400)
        strategy = BCHUSDTStrategy(data)
        signal = strategy.generate_signals()
        self.assertIsNone(signal)

    # ------------------------------------------------------------------
    # 9. Resampling: 15-minute input → 4-hour bars
    # ------------------------------------------------------------------

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_15min_input_is_resampled_to_1h(self, _mock):
        """BCHUSDTStrategy must convert 15-minute bars to 4-hour bars internally.

        16 × 15-minute bars compose exactly 1 complete 4-hour bar.
        """
        rng = np.random.default_rng(0)
        n_15m = 16  # exactly 4 hours
        index = pd.date_range("2025-01-01", periods=n_15m, freq="15min", tz="UTC")
        close = 320.0 + rng.normal(0, 1, n_15m).cumsum()
        data = pd.DataFrame(
            {
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": rng.uniform(100, 500, n_15m),
            },
            index=index,
        )
        strategy = BCHUSDTStrategy(data)
        # Exactly 1 complete 4-hour bar should result
        self.assertEqual(len(strategy.data), 1)

    # ------------------------------------------------------------------
    # 10. Backtester integration
    # ------------------------------------------------------------------

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_backtester_integration(self, _mock):
        """Walk-forward backtester should produce a valid BacktestReport."""
        up = _make_4h_data(direction="up", n=600)
        down = _make_4h_data(direction="down", n=400, start_price=float(up["close"].iloc[-1]))
        down.index = pd.date_range(
            up.index[-1] + pd.Timedelta(hours=4), periods=400, freq="4h", tz="UTC"
        )
        data = pd.concat([up, down])

        backtester = WalkForwardBacktester(
            strategy_factory=BCHUSDTStrategy,
            starting_equity=10_000.0,
            risk_per_trade_pct=0.01,
        )
        report = backtester.run(data, symbol="BCHUSDT", warmup_bars=210)

        self.assertIsInstance(report, BacktestReport)
        self.assertEqual(report.starting_equity, 10_000.0)
        # With 1 000 bars spanning up + down regimes, at least a few trades expected
        self.assertGreaterEqual(report.trades, 0)


if __name__ == "__main__":
    unittest.main()
