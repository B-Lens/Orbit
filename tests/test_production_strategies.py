import unittest
from unittest.mock import patch

import pandas as pd

from orbit.strategies.btc_strategy import BTCStrategy
from orbit.strategies.eth_strategy import ETHStrategy
from orbit.strategies.skyusdt_strategy import SKYUSDTStrategy
from orbit.strategies.paxgusdt_strategy import PAXGUSDTStrategy
from orbit.strategies.reversal_strategy import BollingerAdaptiveReversalStrategyBCH
from orbit.strategies.strategies_base import Strategy
from orbit.strategies.strategy_registry import STRATEGY_REGISTRY, _LazyStrategyRegistry


class TestProductionStrategyOwnership(unittest.TestCase):
    def test_registry_resolves_internal_classes(self):
        self.assertIs(STRATEGY_REGISTRY["BTCUSDT"], BTCStrategy)
        self.assertIs(STRATEGY_REGISTRY["ETHUSDT"], ETHStrategy)
        self.assertIs(
            STRATEGY_REGISTRY["BCHUSDT"], BollingerAdaptiveReversalStrategyBCH
        )
        self.assertIs(STRATEGY_REGISTRY["PAXGUSDT"], PAXGUSDTStrategy)
        self.assertIs(STRATEGY_REGISTRY["SKYUSDT"], SKYUSDTStrategy)

    def test_all_production_strategies_use_orbit_contract(self):
        for strategy_class in (
            BTCStrategy,
            BollingerAdaptiveReversalStrategyBCH,
            ETHStrategy,
            PAXGUSDTStrategy,
            SKYUSDTStrategy,
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


def hourly_btc_frame(closes: list[float]) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=len(closes), freq="1h")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [100.0] * len(closes),
        },
        index=index,
    )


class TestBTCStrategy(unittest.TestCase):
    @patch("orbit.strategies.btc_strategy.generate_chart", return_value=None)
    def test_long_breakout_has_three_to_one_reward_risk(self, _chart):
        signal = BTCStrategy(hourly_btc_frame([100.0] * 55 + [105.0])).generate_signals(
            symbol="BTCUSDT"
        )

        self.assertEqual(signal["signal"], "BUY")
        risk = signal["entry_price"] - signal["stop_loss"]
        reward = signal["take_profit"] - signal["entry_price"]
        self.assertAlmostEqual(reward / risk, 3.0)

    @patch("orbit.strategies.btc_strategy.generate_chart", return_value=None)
    def test_short_breakout_has_three_to_one_reward_risk(self, _chart):
        signal = BTCStrategy(hourly_btc_frame([100.0] * 55 + [95.0])).generate_signals(
            symbol="BTCUSDT"
        )

        self.assertEqual(signal["signal"], "SELL")
        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        self.assertAlmostEqual(reward / risk, 3.0)

    def test_partial_hour_does_not_emit_signal(self):
        hourly = hourly_btc_frame([100.0] * 55 + [105.0])
        rows = [
            (timestamp + pd.Timedelta(minutes=15 * offset), row.copy())
            for timestamp, row in hourly.iterrows()
            for offset in range(4)
        ]
        data = pd.DataFrame(
            [row for _, row in rows], index=[timestamp for timestamp, _ in rows]
        ).iloc[:-1]

        self.assertIsNone(BTCStrategy(data).generate_signals(symbol="BTCUSDT"))

    def test_trailing_stop_uses_recent_price_structure(self):
        data = hourly_btc_frame([100.0] * 55 + [105.0])
        signal = BTCStrategy(data).generate_signals(
            symbol="BTCUSDT", position_side="LONG"
        )

        self.assertEqual(signal["signal"], "UPDATE_SL_TP")
        self.assertLess(signal["stop_loss"], data["high"].iloc[-12:].max())


def hourly_sky_frame(final_close: float) -> pd.DataFrame:
    closes = [100.0] * 105 + [final_close]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [100.0] * len(closes),
        },
        index=pd.date_range("2025-01-01", periods=len(closes), freq="1h"),
    )


class TestSKYUSDTStrategy(unittest.TestCase):
    def test_long_breakout_has_four_to_one_reward_risk(self):
        data = hourly_sky_frame(105.0)
        with patch.object(
            SKYUSDTStrategy,
            "_current_hour",
            return_value=data.index[-1] + pd.Timedelta(hours=1),
        ):
            signal = SKYUSDTStrategy(data).generate_signals()

        self.assertEqual(signal["signal"], "BUY")
        risk = signal["entry_price"] - signal["stop_loss"]
        reward = signal["take_profit"] - signal["entry_price"]
        self.assertAlmostEqual(reward / risk, 4.0)

    def test_short_breakout_has_four_to_one_reward_risk(self):
        data = hourly_sky_frame(95.0)
        with patch.object(
            SKYUSDTStrategy,
            "_current_hour",
            return_value=data.index[-1] + pd.Timedelta(hours=1),
        ):
            signal = SKYUSDTStrategy(data).generate_signals()

        self.assertEqual(signal["signal"], "SELL")
        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        self.assertAlmostEqual(reward / risk, 4.0)

    def test_existing_position_suppresses_entry(self):
        signal = SKYUSDTStrategy(hourly_sky_frame(105.0)).generate_signals(
            position_side="LONG"
        )

        self.assertIsNone(signal)

    def test_incomplete_resampled_hour_suppresses_entry(self):
        hourly = hourly_sky_frame(105.0)
        rows = [
            (timestamp + pd.Timedelta(minutes=15 * offset), row)
            for timestamp, row in hourly.iterrows()
            for offset in range(4)
        ]
        partial = pd.DataFrame(
            [row for _, row in rows], index=[timestamp for timestamp, _ in rows]
        ).iloc[:-1]

        self.assertIsNone(SKYUSDTStrategy(partial).generate_signals())

    def test_stale_completed_hour_suppresses_entry(self):
        data = hourly_sky_frame(105.0)
        with patch.object(
            SKYUSDTStrategy,
            "_current_hour",
            return_value=data.index[-1] + pd.Timedelta(hours=2),
        ):
            signal = SKYUSDTStrategy(data).generate_signals()

        self.assertIsNone(signal)

    def test_missing_complete_hour_suppresses_entry(self):
        data = hourly_sky_frame(105.0)
        data = data.drop(data.index[-10])
        with patch.object(
            SKYUSDTStrategy,
            "_current_hour",
            return_value=data.index[-1] + pd.Timedelta(hours=1),
        ):
            signal = SKYUSDTStrategy(data).generate_signals()

        self.assertIsNone(signal)


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
            patch.object(
                strategy, "compute_bollinger_bands", return_value=(upper, upper, lower)
            ),
            patch.object(strategy, "compute_sma", return_value=upper),
            patch.object(strategy, "is_bullish_reversal", return_value=True),
            patch.object(strategy, "send_params"),
            patch(
                "orbit.strategies.reversal_strategy.generate_chart", return_value=None
            ),
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
            patch.object(
                strategy, "compute_bollinger_bands", return_value=(upper, lower, lower)
            ),
            patch.object(strategy, "compute_sma", return_value=upper),
            patch.object(strategy, "is_bearish_reversal", return_value=True),
            patch.object(strategy, "send_params"),
            patch(
                "orbit.strategies.reversal_strategy.generate_chart", return_value=None
            ),
        ):
            signal = strategy.generate_signals(symbol="BCHUSDT")

        self.assertEqual(signal["signal"], "SELL")
        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        self.assertAlmostEqual(reward / risk, 2.0)


if __name__ == "__main__":
    unittest.main()
