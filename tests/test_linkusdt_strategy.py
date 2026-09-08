import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from orbit.strategies.linkusdt_strategy import LINKUSDTResearchStrategy
from orbit.strategies.strategy_registry import STRATEGY_REGISTRY


def _hourly_data(direction: str = "flat", bars: int = 220) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=bars, freq="1h")
    close = np.full(bars, 15.0)
    high = close + 0.1
    low = close - 0.1
    volume = np.full(bars, 100_000.0)
    if direction == "up":
        close[-1], high[-1], low[-1], volume[-1] = 16.0, 16.1, 14.9, 200_000.0
    if direction == "down":
        close[-1], high[-1], low[-1], volume[-1] = 14.0, 15.1, 13.9, 200_000.0
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


class TestLINKUSDTResearchStrategy(unittest.TestCase):
    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_candidate_is_not_registered_for_execution(self, _mock_discord):
        self.assertNotIn("LINKUSDT", STRATEGY_REGISTRY)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_insufficient_data_returns_none(self, _mock_discord):
        self.assertIsNone(LINKUSDTResearchStrategy(_hourly_data(bars=200)).generate_signals())

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_long_breakout_uses_documented_reward_risk(self, _mock_discord):
        signal = LINKUSDTResearchStrategy(_hourly_data("up")).generate_signals()
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["signal"], "BUY")
        risk = signal["entry_price"] - signal["stop_loss"]
        reward = signal["take_profit"] - signal["entry_price"]
        self.assertAlmostEqual(reward / risk, 3.0, places=5)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_short_breakout_uses_documented_reward_risk(self, _mock_discord):
        signal = LINKUSDTResearchStrategy(_hourly_data("down")).generate_signals()
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["signal"], "SELL")
        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        self.assertAlmostEqual(reward / risk, 3.0, places=5)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_open_position_suppresses_entry(self, _mock_discord):
        strategy = LINKUSDTResearchStrategy(_hourly_data("up"))
        self.assertIsNone(strategy.generate_signals(position_side="LONG"))
