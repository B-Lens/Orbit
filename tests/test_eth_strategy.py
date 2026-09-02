import unittest
from unittest.mock import patch
import pandas as pd
import numpy as np

from orbit.strategies.eth_strategy import ETHStrategy


def _make_trending_data(direction="up", n=350):
    """Generate synthetic OHLCV data with a clear trend."""
    np.random.seed(42)
    index = pd.date_range("2025-01-01", periods=n, freq="1h")
    if direction == "up":
        # Start low, trend up with some noise
        base = 1800 + np.cumsum(np.random.normal(0.5, 2.0, n))
    else:
        # Start high, trend down with some noise
        base = 3500 + np.cumsum(np.random.normal(-0.5, 2.0, n))

    close = pd.Series(base, index=index)
    high = close + np.abs(np.random.normal(5, 2, n))
    low = close - np.abs(np.random.normal(5, 2, n))
    open_ = close.shift(1).fillna(close.iloc[0]) + np.random.normal(0, 1, n)

    # Make volume spike on recent bars to satisfy volume filter
    volume = np.random.uniform(1000, 3000, n)
    volume[-50:] = volume[-50:] * 2  # Higher recent volume

    return pd.DataFrame(
        {
            "open": open_.values,
            "high": high.values,
            "low": low.values,
            "close": close.values,
            "volume": volume,
        },
        index=index,
    )


class TestETHStrategy(unittest.TestCase):

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_partial_hour_is_excluded(self, mock_discord):
        index = pd.date_range("2025-01-01", periods=9, freq="15min")
        data = pd.DataFrame(
            {
                "open": range(9),
                "high": range(1, 10),
                "low": range(9),
                "close": range(1, 10),
                "volume": [1] * 9,
            },
            index=index,
        )
        strategy = ETHStrategy(data)
        self.assertEqual(len(strategy.data), 2)
        self.assertEqual(strategy.data.index[-1], pd.Timestamp("2025-01-01 01:00:00"))

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_no_signal_on_insufficient_data(self, mock_discord):
        """2. With < 200 bars, should return None"""
        data = _make_trending_data(n=100)
        strategy = ETHStrategy(data=data)
        self.assertIsNone(strategy.generate_signals())

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_returns_none_when_no_conditions_met(self, mock_discord):
        """3. Flat data should return None"""
        index = pd.date_range("2025-01-01", periods=250, freq="1h")
        data = pd.DataFrame(
            {"open": 2000, "high": 2005, "low": 1995, "close": 2000, "volume": 1000},
            index=index,
        )
        strategy = ETHStrategy(data=data)
        self.assertIsNone(strategy.generate_signals())


if __name__ == "__main__":
    unittest.main()
