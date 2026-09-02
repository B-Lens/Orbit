import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from orbit.backtesting import BacktestReport, WalkForwardBacktester
from orbit.strategies.solusdt_strategy import SOLUSDTStrategy
from orbit.strategies.strategy_registry import STRATEGY_REGISTRY


def _hourly_data(*, direction: str = "flat", bars: int = 240) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=bars, freq="1h")
    close = np.full(bars, 100.0)
    if direction == "up":
        close[-1] = 104.0
    elif direction == "down":
        close[-1] = 96.0
    volume = np.full(bars, 1_000.0)
    volume[-1] = 2_000.0
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": volume,
        },
        index=index,
    )


class TestSOLUSDTStrategy(unittest.TestCase):
    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_registry_resolves_testnet_strategy(self, _mock_discord):
        self.assertIs(STRATEGY_REGISTRY["SOLUSDT"], SOLUSDTStrategy)

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
        hourly = SOLUSDTStrategy(data)._hourly_data()
        self.assertEqual(len(hourly), 2)
        self.assertEqual(hourly.index[-1], pd.Timestamp("2026-01-01 01:00:00"))

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_buy_breakout_has_four_to_one_reward_risk(self, _mock_discord):
        strategy = SOLUSDTStrategy(_hourly_data(direction="up"))
        signal = strategy.generate_signals(symbol="SOLUSDT")
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["signal"], "BUY")
        risk = signal["entry_price"] - signal["stop_loss"]
        reward = signal["take_profit"] - signal["entry_price"]
        self.assertAlmostEqual(reward / risk, 4.0)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_sell_breakout_has_four_to_one_reward_risk(self, _mock_discord):
        strategy = SOLUSDTStrategy(_hourly_data(direction="down"))
        signal = strategy.generate_signals(symbol="SOLUSDT")
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["signal"], "SELL")
        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        self.assertAlmostEqual(reward / risk, 4.0)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_open_position_suppresses_entry(self, _mock_discord):
        strategy = SOLUSDTStrategy(_hourly_data(direction="up"))
        self.assertIsNone(strategy.generate_signals(position_side="LONG"))

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_strategy_works_with_walk_forward_backtester(self, _mock_discord):
        data = _hourly_data(direction="up")
        data.loc[data.index[-1] + pd.Timedelta(hours=1)] = [104, 130, 103, 125, 1000]
        report = WalkForwardBacktester(SOLUSDTStrategy, fee_rate=0, slippage_bps=0).run(
            data, symbol="SOLUSDT", warmup_bars=200
        )
        self.assertIsInstance(report, BacktestReport)
        self.assertEqual(report.trades, 1)
        self.assertEqual(report.results[0].outcome, "target")


if __name__ == "__main__":
    unittest.main()
