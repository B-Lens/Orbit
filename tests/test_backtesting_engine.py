import unittest

import pandas as pd

from orbit.backtesting import WalkForwardBacktester


class _OneShotStrategy:
    def __init__(self, data):
        self.data = data

    def generate_signals(self, symbol=None):
        if len(self.data) == 2:
            return {
                "signal": "BUY", "entry_price": 100, "stop_loss": 98,
                "take_profit": 104, "pattern": "test",
            }
        return None


class TestWalkForwardBacktester(unittest.TestCase):
    def test_ignores_signal_entry_and_fills_at_next_open(self):
        data = pd.DataFrame(
            {
                "open": [100, 100, 102], "high": [101, 101, 105],
                "low": [99, 99, 101], "close": [100, 100, 104],
                "volume": [1, 1, 1],
            },
            index=pd.date_range("2026-01-01", periods=3, freq="15min"),
        )
        report = WalkForwardBacktester(
            _OneShotStrategy, starting_equity=1000, fee_rate=0, slippage_bps=0
        ).run(data, symbol="ETHUSDT", warmup_bars=1)
        self.assertEqual(report.results[0].entry_price, 102)
        self.assertEqual(report.results[0].entry_time, data.index[2])

    def test_fee_aware_target_trade(self):
        data = pd.DataFrame(
            {
                "open": [100, 100, 100], "high": [101, 101, 105],
                "low": [99, 99, 99], "close": [100, 100, 104],
                "volume": [1, 1, 1],
            },
            index=pd.date_range("2026-01-01", periods=3, freq="15min"),
        )
        report = WalkForwardBacktester(
            _OneShotStrategy, starting_equity=1000, fee_rate=0, slippage_bps=0
        ).run(data, symbol="BTCUSDT", warmup_bars=1)
        self.assertEqual(report.trades, 1)
        self.assertEqual(report.results[0].outcome, "target")
        self.assertAlmostEqual(report.net_pnl, 20)

    def test_same_bar_stop_and_target_uses_stop(self):
        data = pd.DataFrame(
            {
                "open": [100, 100, 100], "high": [101, 101, 105],
                "low": [99, 99, 97], "close": [100, 100, 100],
                "volume": [1, 1, 1],
            },
            index=pd.date_range("2026-01-01", periods=3, freq="15min"),
        )
        report = WalkForwardBacktester(
            _OneShotStrategy, starting_equity=1000, fee_rate=0, slippage_bps=0
        ).run(data, symbol="BTCUSDT", warmup_bars=1)
        self.assertEqual(report.results[0].outcome, "stop")
        self.assertAlmostEqual(report.net_pnl, -10)


if __name__ == "__main__":
    unittest.main()
