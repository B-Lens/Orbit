import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from orbit.backtesting import BacktestReport, WalkForwardBacktester
from orbit.strategies.linkusdt_strategy import LINKUSDTStrategy
from orbit.strategies.strategy_registry import STRATEGY_REGISTRY


def _hourly_data(*, direction: str = "flat", bars: int = 260) -> pd.DataFrame:
    """Generate synthetic hourly OHLCV data for testing.
    
    Creates a long period of low volatility to trigger the squeeze,
    followed by a sudden breakout candle.
    """
    index = pd.date_range("2026-01-01", periods=bars, freq="1h")
    base_price = 15.00
    
    close = np.full(bars, base_price)
    high = close + 0.05
    low = close - 0.05
    volume = np.full(bars, 500_000.0)

    if direction == "up":
        # Breakout candle: strong volume, closes above upper BB and EMA
        close[-1] = 15.50
        high[-1] = 15.60
        low[-1] = 14.90
        volume[-1] = 1_000_000.0
    elif direction == "down":
        # Breakout candle: strong volume, closes below lower BB and EMA
        close[-1] = 14.50
        high[-1] = 15.10
        low[-1] = 14.40
        volume[-1] = 1_000_000.0
    elif direction != "flat":
        raise ValueError(f"Unknown direction: {direction}")

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


class TestLINKUSDTStrategy(unittest.TestCase):
    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_registry_resolves_testnet_strategy(self, _mock_discord):
        self.assertIs(STRATEGY_REGISTRY["LINKUSDT"], LINKUSDTStrategy)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_insufficient_bars_returns_none(self, _mock_discord):
        data = _hourly_data(bars=50)
        strategy = LINKUSDTStrategy(data, enforce_freshness=False)
        self.assertIsNone(strategy.generate_signals(symbol="LINKUSDT"))

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_flat_market_returns_none(self, _mock_discord):
        data = _hourly_data(direction="flat")
        with patch.object(
            LINKUSDTStrategy, "_current_hour", return_value=_fresh_hour(data)
        ):
            signal = LINKUSDTStrategy(data).generate_signals(symbol="LINKUSDT")
        self.assertIsNone(signal)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_buy_signal_has_correct_reward_risk(self, _mock_discord):
        data = _hourly_data(direction="up")
        with patch.object(LINKUSDTStrategy, "_current_hour", return_value=_fresh_hour(data)):
            strategy = LINKUSDTStrategy(data)
            signal = strategy.generate_signals(symbol="LINKUSDT")
        
        self.assertIsNotNone(signal)
        if signal is not None:
            self.assertEqual(signal["signal"], "BUY")
            risk = signal["entry_price"] - signal["stop_loss"]
            reward = signal["take_profit"] - signal["entry_price"]
            self.assertAlmostEqual(reward / risk, 2.0, places=1)
            self.assertGreater(risk, 0)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_sell_signal_has_correct_reward_risk(self, _mock_discord):
        data = _hourly_data(direction="down")
        with patch.object(LINKUSDTStrategy, "_current_hour", return_value=_fresh_hour(data)):
            strategy = LINKUSDTStrategy(data)
            signal = strategy.generate_signals(symbol="LINKUSDT")
            
        self.assertIsNotNone(signal)
        if signal is not None:
            self.assertEqual(signal["signal"], "SELL")
            risk = signal["stop_loss"] - signal["entry_price"]
            reward = signal["entry_price"] - signal["take_profit"]
            self.assertAlmostEqual(reward / risk, 2.0, places=1)
            self.assertGreater(risk, 0)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_open_position_suppresses_entry(self, _mock_discord):
        data = _hourly_data(direction="up")
        with patch.object(
            LINKUSDTStrategy, "_current_hour", return_value=_fresh_hour(data)
        ):
            strategy = LINKUSDTStrategy(data)
            self.assertIsNone(strategy.generate_signals(position_side="LONG"))

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_stale_completed_hour_suppresses_entry(self, _mock_discord):
        data = _hourly_data(direction="up")
        stale_hour = data.index[-1].floor("h") + pd.Timedelta(hours=1, minutes=15)
        with patch.object(LINKUSDTStrategy, "_current_hour", return_value=stale_hour):
            signal = LINKUSDTStrategy(data).generate_signals(symbol="LINKUSDT")
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
        hourly, _ = LINKUSDTStrategy(data)._hourly_data()
        self.assertEqual(len(hourly), 2)
        self.assertEqual(hourly.index[-1], pd.Timestamp("2026-01-01 01:00:00"))

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_contiguous_check_rejects_gap(self, _mock_discord):
        data = _hourly_data(direction="up")
        data = data.drop(data.index[120])
        with patch.object(
            LINKUSDTStrategy, "_current_hour", return_value=_fresh_hour(data)
        ):
            signal = LINKUSDTStrategy(data).generate_signals(symbol="LINKUSDT")
        self.assertIsNone(signal)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_strategy_works_with_walk_forward_backtester(self, _mock_discord):
        data = _hourly_data(direction="up", bars=260)
        extra_index = pd.date_range(
            data.index[-1] + pd.Timedelta(hours=1), periods=20, freq="1h"
        )
        extra = pd.DataFrame(
            {
                "open": np.linspace(15.5, 16.5, 20),
                "high": np.linspace(15.6, 16.6, 20),
                "low": np.linspace(15.4, 16.4, 20),
                "close": np.linspace(15.55, 16.55, 20),
                "volume": np.full(20, 500_000.0),
            },
            index=extra_index,
        )
        full_data = pd.concat([data, extra])

        report = WalkForwardBacktester(
            lambda frame: LINKUSDTStrategy(frame, enforce_freshness=False),
            fee_rate=0,
            slippage_bps=0,
        ).run(full_data, symbol="LINKUSDT", warmup_bars=200)
        self.assertIsInstance(report, BacktestReport)


if __name__ == "__main__":
    unittest.main()
