import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from config import COIN_TRADE_TYPE, TRAILING_STOPLOSS, TradeType
from orbit.strategies.linkusdt_strategy import LINKUSDTStrategy
from orbit.strategies.strategy_registry import STRATEGY_REGISTRY


def _hourly_data(
    direction: str = "flat", bars: int = 220, freq: str = "1h"
) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=bars, freq=freq)
    close = np.full(bars, 15.0)
    high = close + 0.1
    low = close - 0.1
    volume = np.full(bars, 100_000.0)
    if direction == "up":
        close[-1], high[-1], low[-1], volume[-1] = 16.0, 16.1, 14.9, 400_000.0
    if direction == "down":
        close[-1], high[-1], low[-1], volume[-1] = 14.0, 15.1, 13.9, 400_000.0
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


class TestLINKUSDTStrategy(unittest.TestCase):
    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_registry_resolves_testnet_strategy(self, _mock_discord):
        self.assertIs(STRATEGY_REGISTRY["LINKUSDT"], LINKUSDTStrategy)

    def test_testnet_reconciliation_uses_bracket_orders_without_trailing(self):
        self.assertIs(COIN_TRADE_TYPE["LINKUSDT"], TradeType.BRACKET_TRADE)
        self.assertFalse(TRAILING_STOPLOSS["LINKUSDT"])

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_insufficient_data_returns_none(self, _mock_discord):
        self.assertIsNone(LINKUSDTStrategy(_hourly_data(bars=200)).generate_signals())

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_long_breakout_uses_documented_reward_risk(self, _mock_discord):
        signal = LINKUSDTStrategy(_hourly_data("up")).generate_signals()
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["signal"], "BUY")
        risk = signal["entry_price"] - signal["stop_loss"]
        reward = signal["take_profit"] - signal["entry_price"]
        self.assertAlmostEqual(reward / risk, 3.0, places=5)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_quarter_hour_input_is_aggregated_to_complete_hours(self, _mock_discord):
        data = _hourly_data("up", bars=820, freq="15min")
        strategy = LINKUSDTStrategy(data)
        hourly = strategy._hourly_data()
        self.assertEqual(len(hourly), 205)
        self.assertEqual(hourly.index.freq, pd.Timedelta(hours=1))
        signal = strategy.generate_signals()
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["signal"], "BUY")

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_incomplete_quarter_hour_group_is_not_used(self, _mock_discord):
        data = _hourly_data("up", bars=821, freq="15min")
        strategy = LINKUSDTStrategy(data)
        self.assertEqual(len(strategy._hourly_data()), 205)
        self.assertIsNone(strategy.generate_signals())

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_short_breakout_uses_documented_reward_risk(self, _mock_discord):
        signal = LINKUSDTStrategy(_hourly_data("down")).generate_signals()
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["signal"], "SELL")
        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        self.assertAlmostEqual(reward / risk, 3.0, places=5)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_open_position_suppresses_entry(self, _mock_discord):
        strategy = LINKUSDTStrategy(_hourly_data("up"))
        self.assertIsNone(strategy.generate_signals(position_side="LONG"))
