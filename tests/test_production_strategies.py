import unittest

from orbit.strategies.agglo_strategy import Agglo_ETHERIUM
from orbit.strategies.reversal_strategy import BollingerAdaptiveReversalStrategyBCH
from orbit.strategies.strategies_base import Strategy
from orbit.strategies.strategy_registry import STRATEGY_REGISTRY
from orbit.strategies.swing_strategy import SwingStrategyBTC


class TestProductionStrategyOwnership(unittest.TestCase):
    def test_registry_resolves_internal_classes(self):
        self.assertIs(STRATEGY_REGISTRY["BTCUSDT"], SwingStrategyBTC)
        self.assertIs(STRATEGY_REGISTRY["ETHUSDT"], Agglo_ETHERIUM)
        self.assertIs(
            STRATEGY_REGISTRY["BCHUSDT"], BollingerAdaptiveReversalStrategyBCH
        )

    def test_all_production_strategies_use_orbit_contract(self):
        for strategy_class in (
            SwingStrategyBTC,
            Agglo_ETHERIUM,
            BollingerAdaptiveReversalStrategyBCH,
        ):
            self.assertTrue(issubclass(strategy_class, Strategy))
            self.assertTrue(strategy_class.__module__.startswith("orbit.strategies."))


if __name__ == "__main__":
    unittest.main()
