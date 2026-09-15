import unittest
import pandas as pd
import numpy as np

from orbit.strategies.eth_strategies import (
    AggloReversalETH,
    EMATrendBreakoutETH,
    BollingerRSIMeanReversionETH,
    SMCLiquiditySweepETH,
    HMAMACDMomentumETH,
    AdaptiveSuperTrendRegimeETH,
    MultiConfluenceMeanReversionETH,
)
from orbit.strategies.strategies_base import Strategy
from orbit.backtesting import WalkForwardBacktester


class TestETHStrategies(unittest.TestCase):
    def setUp(self):
        # Create synthetic trending and mean-reverting OHLCV data
        np.random.seed(42)
        n = 300
        dates = pd.date_range("2026-01-01", periods=n, freq="15min")
        close_prices = 2000.0 + np.cumsum(np.random.randn(n) * 5)
        high_prices = close_prices + np.random.rand(n) * 4
        low_prices = close_prices - np.random.rand(n) * 4
        open_prices = low_prices + np.random.rand(n) * (high_prices - low_prices)
        volume = 100.0 + np.random.rand(n) * 50

        self.data = pd.DataFrame(
            {
                "open": open_prices,
                "high": high_prices,
                "low": low_prices,
                "close": close_prices,
                "volume": volume,
            },
            index=dates,
        )

    def test_all_strategies_subclass_base(self):
        classes = [
            AggloReversalETH,
            EMATrendBreakoutETH,
            BollingerRSIMeanReversionETH,
            SMCLiquiditySweepETH,
            HMAMACDMomentumETH,
            AdaptiveSuperTrendRegimeETH,
            MultiConfluenceMeanReversionETH,
        ]
        for cls in classes:
            self.assertTrue(issubclass(cls, Strategy))

    def test_signal_contract_validity(self):
        for cls in [
            AggloReversalETH,
            EMATrendBreakoutETH,
            BollingerRSIMeanReversionETH,
            SMCLiquiditySweepETH,
            HMAMACDMomentumETH,
            AdaptiveSuperTrendRegimeETH,
            MultiConfluenceMeanReversionETH,
        ]:
            strat = cls(self.data)
            sig = strat.generate_signals("ETHUSDT")
            if sig is not None:
                self.assertIn("signal", sig)
                self.assertIn(sig["signal"], ["BUY", "SELL"])
                self.assertIn("entry_price", sig)
                self.assertIn("stop_loss", sig)
                self.assertIn("take_profit", sig)
                self.assertIn("pattern", sig)
                if sig["signal"] == "BUY":
                    self.assertLess(sig["stop_loss"], sig["entry_price"])
                    self.assertGreater(sig["take_profit"], sig["entry_price"])
                elif sig["signal"] == "SELL":
                    self.assertGreater(sig["stop_loss"], sig["entry_price"])
                    self.assertLess(sig["take_profit"], sig["entry_price"])

    def test_backtester_execution_compatibility(self):
        backtester = WalkForwardBacktester(
            lambda df: BollingerRSIMeanReversionETH(df, bb_period=20),
            starting_equity=10000.0,
            fee_rate=0.0004,
            slippage_bps=2.0,
        )
        report = backtester.run(self.data, symbol="ETHUSDT", warmup_bars=50)
        self.assertEqual(report.starting_equity, 10000.0)
        self.assertIsInstance(report.return_pct, float)


if __name__ == "__main__":
    unittest.main()
