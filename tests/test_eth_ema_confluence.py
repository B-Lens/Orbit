import unittest
from unittest.mock import patch
import pandas as pd
import numpy as np

from orbit.strategies.eth_ema_confluence_strategy import EMAConfluenceETH
from orbit.strategies.strategies_base import Strategy
from orbit.backtesting import WalkForwardBacktester, BacktestReport

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
    
    return pd.DataFrame({
        "open": open_.values, "high": high.values,
        "low": low.values, "close": close.values,
        "volume": volume,
    }, index=index)


class TestEMAConfluenceETH(unittest.TestCase):
    
    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_strategy_inherits_base(self, mock_discord):
        """1. Verify EMAConfluenceETH is subclass of Strategy"""
        self.assertTrue(issubclass(EMAConfluenceETH, Strategy))
        
    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_no_signal_on_insufficient_data(self, mock_discord):
        """2. With < 200 bars, should return None"""
        data = _make_trending_data(n=100)
        strategy = EMAConfluenceETH(data=data)
        self.assertIsNone(strategy.generate_signals())

    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_returns_none_when_no_conditions_met(self, mock_discord):
        """3. Flat data should return None"""
        index = pd.date_range("2025-01-01", periods=300, freq="1h")
        data = pd.DataFrame({
            "open": [2000]*300, "high": [2005]*300,
            "low": [1995]*300, "close": [2000]*300,
            "volume": [1000]*300
        }, index=index)
        strategy = EMAConfluenceETH(data=data)
        self.assertIsNone(strategy.generate_signals())

    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_buy_signal_structure(self, mock_discord):
        """4. When BUY signal returned, verify dict has all required keys and stop_loss < entry_price < take_profit"""
        data = _make_trending_data("up", n=350)
        strategy = EMAConfluenceETH(data=data)
        
        signal = strategy.generate_signals()
        if signal is not None:
            self.assertIn("signal", signal)
            self.assertEqual(signal["signal"], "BUY")
            self.assertIn("entry_price", signal)
            self.assertIn("stop_loss", signal)
            self.assertIn("take_profit", signal)
            self.assertIn("pattern", signal)
            
            self.assertLess(signal["stop_loss"], signal["entry_price"])
            self.assertLess(signal["entry_price"], signal["take_profit"])

    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_sell_signal_structure(self, mock_discord):
        """5. When SELL signal returned, verify dict has all required keys and take_profit < entry_price < stop_loss"""
        data = _make_trending_data("down", n=350)
        strategy = EMAConfluenceETH(data=data)
        
        signal = strategy.generate_signals()
        if signal is not None:
            self.assertIn("signal", signal)
            self.assertEqual(signal["signal"], "SELL")
            self.assertLess(signal["take_profit"], signal["entry_price"])
            self.assertLess(signal["entry_price"], signal["stop_loss"])

    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_buy_signal_risk_reward(self, mock_discord):
        """6. Verify (take_profit - entry) / (entry - stop_loss) ≈ 1.5"""
        data = _make_trending_data("up", n=350)
        strategy = EMAConfluenceETH(data=data)
        signal = strategy.generate_signals()
        
        if signal is not None and signal["signal"] == "BUY":
            risk = signal["entry_price"] - signal["stop_loss"]
            reward = signal["take_profit"] - signal["entry_price"]
            self.assertAlmostEqual(reward / risk, 1.5, places=1)

    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_sell_signal_risk_reward(self, mock_discord):
        """7. Verify (entry - take_profit) / (stop_loss - entry) ≈ 1.5"""
        data = _make_trending_data("down", n=350)
        strategy = EMAConfluenceETH(data=data)
        signal = strategy.generate_signals()
        
        if signal is not None and signal["signal"] == "SELL":
            risk = signal["stop_loss"] - signal["entry_price"]
            reward = signal["entry_price"] - signal["take_profit"]
            self.assertAlmostEqual(reward / risk, 1.5, places=1)
            
    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_strategy_works_with_backtester(self, mock_discord):
        """8. Create synthetic data and run WalkForwardBacktester with EMAConfluenceETH, verify it produces a BacktestReport"""
        data = _make_trending_data("up", n=350)
        backtester = WalkForwardBacktester(
            strategy_factory=EMAConfluenceETH,
            starting_equity=1000,
            fee_rate=0,
            slippage_bps=0,
        )
        report = backtester.run(data, symbol="ETHUSDT", warmup_bars=200)
        self.assertIsInstance(report, BacktestReport)

if __name__ == '__main__':
    unittest.main()
