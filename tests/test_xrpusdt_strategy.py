import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from orbit.backtesting import BacktestReport, WalkForwardBacktester
from orbit.strategies.xrpusdt_strategy import XRPUSDTStrategy
from orbit.strategies.strategy_registry import STRATEGY_REGISTRY


def _hourly_data(*, direction: str = "flat", bars: int = 240) -> pd.DataFrame:
    """Generate synthetic hourly OHLCV data for testing.

    When direction is 'up', the last candle breaks above the upper
    Bollinger Band after a tight consolidation range.  When 'down',
    it breaks below the lower band.
    """
    index = pd.date_range("2026-01-01", periods=bars, freq="1h")
    close = np.full(bars, 0.60)  # XRP-realistic price
    high = close + 0.005
    low = close - 0.005
    volume = np.full(bars, 50_000.0)

    if direction == "up":
        # Tight consolidation then breakout above upper band
        close[-1] = 0.66  # Well above upper BB after tight range
        high[-1] = 0.67
        low[-1] = 0.59
        volume[-1] = 100_000.0  # Volume spike
    elif direction == "down":
        close[-1] = 0.54  # Well below lower BB
        high[-1] = 0.61
        low[-1] = 0.53
        volume[-1] = 100_000.0

    return pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=index,
    )


def _fresh_hour(data: pd.DataFrame) -> pd.Timestamp:
    """Return a time in the first scan window after the last completed hour."""
    return data.index[-1].floor("h") + pd.Timedelta(hours=1)


class TestXRPUSDTStrategy(unittest.TestCase):
    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_registry_resolves_testnet_strategy(self, _mock_discord):
        self.assertIs(STRATEGY_REGISTRY["XRPUSDT"], XRPUSDTStrategy)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_insufficient_bars_returns_none(self, _mock_discord):
        data = _hourly_data(bars=50)  # Less than minimum 201
        strategy = XRPUSDTStrategy(data, enforce_freshness=False)
        self.assertIsNone(strategy.generate_signals(symbol="XRPUSDT"))

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_flat_market_returns_none(self, _mock_discord):
        data = _hourly_data(direction="flat")
        with patch.object(
            XRPUSDTStrategy, "_current_hour", return_value=_fresh_hour(data)
        ):
            signal = XRPUSDTStrategy(data).generate_signals(symbol="XRPUSDT")
        self.assertIsNone(signal)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_buy_breakout_has_correct_reward_risk(self, _mock_discord):
        data = _hourly_data(direction="up")
        with patch.object(
            XRPUSDTStrategy, "_current_hour", return_value=_fresh_hour(data)
        ):
            signal = XRPUSDTStrategy(data).generate_signals(symbol="XRPUSDT")
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["signal"], "BUY")
        risk = signal["entry_price"] - signal["stop_loss"]
        reward = signal["take_profit"] - signal["entry_price"]
        self.assertAlmostEqual(reward / risk, 3.5, places=1)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_sell_breakout_has_correct_reward_risk(self, _mock_discord):
        data = _hourly_data(direction="down")
        with patch.object(
            XRPUSDTStrategy, "_current_hour", return_value=_fresh_hour(data)
        ):
            signal = XRPUSDTStrategy(data).generate_signals(symbol="XRPUSDT")
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["signal"], "SELL")
        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        self.assertAlmostEqual(reward / risk, 3.5, places=1)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_open_position_suppresses_entry(self, _mock_discord):
        data = _hourly_data(direction="up")
        with patch.object(
            XRPUSDTStrategy, "_current_hour", return_value=_fresh_hour(data)
        ):
            strategy = XRPUSDTStrategy(data)
            self.assertIsNone(strategy.generate_signals(position_side="LONG"))

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_stale_completed_hour_suppresses_entry(self, _mock_discord):
        """Regression: same breakout candle must not fire again at :15/:30/:45."""
        data = _hourly_data(direction="up")
        stale_hour = data.index[-1].floor("h") + pd.Timedelta(hours=1, minutes=15)
        with patch.object(XRPUSDTStrategy, "_current_hour", return_value=stale_hour):
            signal = XRPUSDTStrategy(data).generate_signals(symbol="XRPUSDT")
        self.assertIsNone(signal)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_incomplete_hour_is_excluded(self, _mock_discord):
        index = pd.date_range("2026-01-01", periods=10, freq="15min")
        data = pd.DataFrame(
            {
                "open": range(10),
                "high": range(1, 11),
                "low": range(10),
                "close": range(1, 11),
                "volume": np.ones(10),
            },
            index=index,
        )
        hourly, _ = XRPUSDTStrategy(data)._hourly_data()
        self.assertEqual(len(hourly), 2)
        self.assertEqual(hourly.index[-1], pd.Timestamp("2026-01-01 01:00:00"))

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_strategy_works_with_walk_forward_backtester(self, _mock_discord):
        data = _hourly_data(direction="up")
        # Add a bar after the signal bar for the backtester to enter and exit
        data.loc[data.index[-1] + pd.Timedelta(hours=1)] = [0.66, 0.80, 0.65, 0.78, 50000]

        report = WalkForwardBacktester(
            lambda frame: XRPUSDTStrategy(frame, enforce_freshness=False),
            fee_rate=0,
            slippage_bps=0,
        ).run(data, symbol="XRPUSDT", warmup_bars=200)
        self.assertIsInstance(report, BacktestReport)
        self.assertEqual(report.trades, 1)
        self.assertEqual(report.results[0].outcome, "target")

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_contiguous_check_rejects_gap(self, _mock_discord):
        """Non-contiguous hourly data must suppress signal generation."""
        data = _hourly_data(direction="up")
        # Remove a candle from the middle to create a gap
        data = data.drop(data.index[120])
        with patch.object(
            XRPUSDTStrategy, "_current_hour", return_value=_fresh_hour(data)
        ):
            signal = XRPUSDTStrategy(data).generate_signals(symbol="XRPUSDT")
        self.assertIsNone(signal)


if __name__ == "__main__":
    unittest.main()
