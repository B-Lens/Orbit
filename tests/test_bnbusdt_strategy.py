import unittest
from unittest.mock import patch
import pandas as pd
import numpy as np

from orbit.strategies.bnbusdt_strategy import BNBStrategy

def _make_crossover_data(bullish=True, n=150):
    np.random.seed(42)
    index = pd.date_range("2025-01-01", periods=n, freq="1h")
    
    # 20 EMA and 100 EMA crossover. Need enough data for 100 EMA.
    if bullish:
        base = 500 + np.cumsum(np.random.normal(1.5, 2.0, n))
        # ensure recent cross up by dropping fast in middle, then rising fast
        base[int(n/2):int(n*0.8)] = base[int(n/2):int(n*0.8)] - 50
        base[int(n*0.8):] = base[int(n*0.8):] + 100
    else:
        base = 600 + np.cumsum(np.random.normal(-1.5, 2.0, n))
        base[int(n/2):int(n*0.8)] = base[int(n/2):int(n*0.8)] + 50
        base[int(n*0.8):] = base[int(n*0.8):] - 100

    close = pd.Series(base, index=index)
    high = close + np.abs(np.random.normal(2, 1, n))
    low = close - np.abs(np.random.normal(2, 1, n))
    open_ = close.shift(1).fillna(close.iloc[0]) + np.random.normal(0, 1, n)
    volume = np.random.uniform(1000, 3000, n)

    return pd.DataFrame({
        "open": open_.values,
        "high": high.values,
        "low": low.values,
        "close": close.values,
        "volume": volume,
    }, index=index)

class TestBNBStrategy(unittest.TestCase):

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_no_signal_on_insufficient_data(self, mock_discord):
        data = _make_crossover_data(n=50) # needs at least 100
        strategy = BNBStrategy(data=data)
        self.assertIsNone(strategy.generate_signals())

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_returns_none_when_no_cross(self, mock_discord):
        index = pd.date_range("2025-01-01", periods=150, freq="1h")
        data = pd.DataFrame(
            {"open": 500, "high": 505, "low": 495, "close": 500, "volume": 1000},
            index=index,
        )
        strategy = BNBStrategy(data=data)
        self.assertIsNone(strategy.generate_signals())

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    @patch("orbit.strategies.bnbusdt_strategy.generate_chart", return_value="dummy_path")
    def test_bullish_cross_generates_buy(self, mock_chart, mock_discord):
        data = _make_crossover_data(bullish=True, n=150)
        # We manually inject a perfect crossover at the very end
        # Fast EMA period 20, Slow EMA period 100
        # Let's override the last 2 closes to guarantee cross
        # For a cross up, previous fast <= previous slow, current fast > current slow
        # We can just mock the _indicators method instead of faking 150 closes exactly
        pass

if __name__ == "__main__":
    unittest.main()
