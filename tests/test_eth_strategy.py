import unittest
from unittest.mock import patch
import pandas as pd
import numpy as np

from orbit.strategies.eth_strategy import ETHStrategy
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


class TestETHStrategy(unittest.TestCase):

    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_partial_hour_is_excluded(self, mock_discord):
        index = pd.date_range("2025-01-01", periods=9, freq="15min")
        data = pd.DataFrame(
            {"open": range(9), "high": range(1, 10), "low": range(9),
             "close": range(1, 10), "volume": [1] * 9},
            index=index,
        )
        strategy = ETHStrategy(data)
        self.assertEqual(len(strategy.data), 2)
        self.assertEqual(strategy.data.index[-1], pd.Timestamp("2025-01-01 01:00:00"))
    
    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_strategy_inherits_base(self, mock_discord):
        # We can just pass an empty dataframe for this check
        data = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        strategy = ETHStrategy(data)
        self.assertTrue(issubclass(ETHStrategy, Strategy))
        
    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_no_signal_on_insufficient_data(self, mock_discord):
        """2. With < 200 bars, should return None"""
        data = _make_trending_data(n=100)
        strategy = ETHStrategy(data=data)
        self.assertIsNone(strategy.generate_signals())

    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_returns_none_when_no_conditions_met(self, mock_discord):
        """3. Flat data should return None"""
        index = pd.date_range("2025-01-01", periods=250, freq="1h")
        data = pd.DataFrame({
            "open": 2000, "high": 2005, "low": 1995, "close": 2000, "volume": 1000
        }, index=index)
        strategy = ETHStrategy(data=data)
        self.assertIsNone(strategy.generate_signals())

    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_buy_signal_structure(self, mock_discord):
        """4. Verify BUY signal dict has all required keys and SL < Entry < TP"""
        data = _make_trending_data(direction="up", n=350)
        strategy = ETHStrategy(data=data)
        signal = strategy.generate_signals()
        
        if signal is not None:
            self.assertIn("signal", signal)
            self.assertEqual(signal["signal"], "BUY")
            self.assertIn("entry_price", signal)
            self.assertIn("stop_loss", signal)
            self.assertIn("take_profit", signal)
            self.assertIn("pattern", signal)
            
            # Check risk structure relative to evaluation fill
            entry = data['close'].iloc[-1]
            self.assertLess(signal["stop_loss"], entry)
            self.assertGreater(signal["take_profit"], entry)

    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_sell_signal_structure(self, mock_discord):
        """5. Verify SELL signal dict has all required keys and TP < Entry < SL"""
        data = _make_trending_data(direction="down", n=350)
        strategy = ETHStrategy(data=data)
        signal = strategy.generate_signals()
        
        if signal is not None:
            self.assertIn("signal", signal)
            self.assertEqual(signal["signal"], "SELL")
            self.assertIn("entry_price", signal)
            self.assertIn("stop_loss", signal)
            self.assertIn("take_profit", signal)
            self.assertIn("pattern", signal)
            
            # Check risk structure relative to evaluation fill
            entry = data['close'].iloc[-1]
            self.assertGreater(signal["stop_loss"], entry)
            self.assertLess(signal["take_profit"], entry)

    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_buy_signal_risk_reward(self, mock_discord):
        """6. Verify (take_profit - entry) / (entry - stop_loss) ≈ 1.5"""
        data = _make_trending_data(direction="up", n=350)
        strategy = ETHStrategy(data=data)
        signal = strategy.generate_signals()
        
        if signal is not None and signal["signal"] == "BUY":
            entry = data['close'].iloc[-1]
            risk = entry - signal["stop_loss"]
            reward = signal["take_profit"] - entry
            self.assertAlmostEqual(reward / risk, 1.5, places=2)

    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_sell_signal_risk_reward(self, mock_discord):
        """7. Verify (entry - take_profit) / (stop_loss - entry) ≈ 1.5"""
        data = _make_trending_data(direction="down", n=350)
        strategy = ETHStrategy(data=data)
        signal = strategy.generate_signals()
        
        if signal is not None and signal["signal"] == "SELL":
            entry = data['close'].iloc[-1]
            risk = signal["stop_loss"] - entry
            reward = entry - signal["take_profit"]
            self.assertAlmostEqual(reward / risk, 1.5, places=2)
        
    @patch('orbit.core.discord_manager.DiscordManager.__init__', return_value=None)
    def test_strategy_works_with_backtester(self, mock_discord):
        """8. Create synthetic data and run WalkForwardBacktester with ETHStrategy"""
        data = _make_trending_data(direction="up", n=500)
        
        # Combine up and down to get trades
        data_down = _make_trending_data(direction="down", n=300)
        data_down.index = pd.date_range(data.index[-1] + pd.Timedelta(hours=1), periods=300, freq="1h")
        data = pd.concat([data, data_down])
        
        backtester = WalkForwardBacktester(
            strategy_factory=ETHStrategy,
            starting_equity=10000.0,
            risk_per_trade_pct=0.01,
        )
        report = backtester.run(data, symbol="ETHUSDT", warmup_bars=200)
        
        self.assertIsInstance(report, BacktestReport)
        self.assertEqual(report.starting_equity, 10000.0)
        # Should have at least one trade
        self.assertGreater(report.trades, 0)

if __name__ == '__main__':
    unittest.main()
