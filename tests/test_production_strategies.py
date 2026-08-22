import unittest
from unittest.mock import patch

import pandas as pd

from orbit.strategies.eth_strategy import ETHStrategy
from orbit.strategies.reversal_strategy import BollingerAdaptiveReversalStrategyBCH
from orbit.strategies.paxgusdt_strategy import PAXGUSDTStrategy
from orbit.strategies.strategies_base import Strategy
from orbit.strategies.strategy_registry import STRATEGY_REGISTRY, _LazyStrategyRegistry
from orbit.strategies.swing_strategy import SwingStrategyBTC


class TestProductionStrategyOwnership(unittest.TestCase):
    def test_registry_resolves_internal_classes(self):
        self.assertIs(STRATEGY_REGISTRY["BTCUSDT"], SwingStrategyBTC)
        self.assertIs(STRATEGY_REGISTRY["ETHUSDT"], ETHStrategy)
        self.assertIs(
            STRATEGY_REGISTRY["BCHUSDT"], BollingerAdaptiveReversalStrategyBCH
        )
        self.assertIs(STRATEGY_REGISTRY["PAXGUSDT"], PAXGUSDTStrategy)

    def test_all_production_strategies_use_orbit_contract(self):
        for strategy_class in (
            SwingStrategyBTC,
            BollingerAdaptiveReversalStrategyBCH,
            ETHStrategy,
            PAXGUSDTStrategy,
        ):
            self.assertTrue(issubclass(strategy_class, Strategy))
            self.assertTrue(strategy_class.__module__.startswith("orbit.strategies."))

    def test_registry_loads_configured_strategy(self):
        registry = _LazyStrategyRegistry(
            {
                "strategies": {
                    "ETHUSDT": {
                        "strategy": "orbit.strategies.eth_strategy.ETHStrategy",
                        "execution_mode": "testnet",
                    }
                }
            }
        )
        self.assertIs(registry["ETHUSDT"], ETHStrategy)

class TestBCHStrategyRiskContract(unittest.TestCase):
    def setUp(self):
        index = pd.date_range("2026-01-01", periods=20, freq="15min")
        self.data = pd.DataFrame(
            {
                "open": [100.0] * 20,
                "high": [101.0] * 20,
                "low": [99.0] * 20,
                "close": [100.0] * 19 + [101.0],
                "volume": [1.0] * 20,
            },
            index=index,
        )

    def test_long_signal_has_two_to_one_reward_risk(self):
        strategy = BollingerAdaptiveReversalStrategyBCH(self.data)
        lower = pd.Series([101.0] * 19 + [100.0], index=self.data.index)
        upper = pd.Series([200.0] * 20, index=self.data.index)
        with (
            patch.object(strategy, "compute_bollinger_bands", return_value=(upper, upper, lower)),
            patch.object(strategy, "compute_sma", return_value=upper),
            patch.object(strategy, "is_bullish_reversal", return_value=True),
            patch.object(strategy, "send_params"),
            patch("orbit.strategies.reversal_strategy.generate_chart", return_value=None),
        ):
            signal = strategy.generate_signals(symbol="BCHUSDT")

        self.assertEqual(signal["signal"], "BUY")
        risk = signal["entry_price"] - signal["stop_loss"]
        reward = signal["take_profit"] - signal["entry_price"]
        self.assertAlmostEqual(reward / risk, 2.0)

    def test_short_signal_has_two_to_one_reward_risk(self):
        data = self.data.copy()
        data.iloc[-1, data.columns.get_loc("close")] = 99.0
        strategy = BollingerAdaptiveReversalStrategyBCH(data)
        upper = pd.Series([98.0] * 19 + [100.0], index=data.index)
        lower = pd.Series([0.0] * 20, index=data.index)
        with (
            patch.object(strategy, "compute_bollinger_bands", return_value=(upper, lower, lower)),
            patch.object(strategy, "compute_sma", return_value=upper),
            patch.object(strategy, "is_bearish_reversal", return_value=True),
            patch.object(strategy, "send_params"),
            patch("orbit.strategies.reversal_strategy.generate_chart", return_value=None),
        ):
            signal = strategy.generate_signals(symbol="BCHUSDT")

        self.assertEqual(signal["signal"], "SELL")
        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        self.assertAlmostEqual(reward / risk, 2.0)


if __name__ == "__main__":
    unittest.main()
