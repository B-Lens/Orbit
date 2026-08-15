import os
import unittest
from unittest.mock import MagicMock, patch

from orbit.core.authentication_manager import AuthenticationManager
from orbit.core.execution import ExecutionMode, ExecutionSettings
from orbit.core.performance import PerformanceTracker
from orbit.core.risk_manager import PreTradeRiskGuard


class TestExecutionSettings(unittest.TestCase):
    def test_safe_default_is_paper(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = ExecutionSettings.from_env()
        self.assertFalse(settings.can_submit_orders)
        self.assertEqual(settings.mode_for("BTCUSDT"), ExecutionMode.PAPER)

    def test_single_asset_can_use_testnet(self):
        env = {
            "ORBIT_ASSET_EXECUTION_MODES": "BCHUSDT:testnet",
            "BINANCE_TESTNET_API_KEY": "key",
            "BINANCE_TESTNET_SECRET_KEY": "secret",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = ExecutionSettings.from_env()
        self.assertTrue(settings.can_submit_orders)
        self.assertTrue(settings.can_submit_orders_for("BCHUSDT"))
        self.assertFalse(settings.can_submit_orders_for("BTCUSDT"))
        self.assertEqual(settings.mode_for("BCHUSDT"), ExecutionMode.TESTNET)

    def test_live_approval_must_exactly_match_live_assets(self):
        env = {
            "ORBIT_ASSET_EXECUTION_MODES": "BCHUSDT:live",
            "ORBIT_LIVE_ASSETS": "BTCUSDT",
            "BINANCE_API_KEY": "key",
            "BINANCE_SECRET_KEY": "secret",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                ExecutionSettings.from_env()

    def test_exact_live_asset_approval_is_accepted(self):
        env = {
            "ORBIT_ASSET_EXECUTION_MODES": "BCHUSDT:live",
            "ORBIT_LIVE_ASSETS": "BCHUSDT",
            "BINANCE_API_KEY": "key",
            "BINANCE_SECRET_KEY": "secret",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = ExecutionSettings.from_env()
        self.assertEqual(settings.mode_for("BCHUSDT"), ExecutionMode.LIVE)
        self.assertEqual(settings.mode_for("BTCUSDT"), ExecutionMode.PAPER)

    def test_futures_client_is_routed_by_asset_mode(self):
        paper_client = MagicMock()
        testnet_client = MagicMock()
        settings = ExecutionSettings(
            {"BCHUSDT": ExecutionMode.TESTNET},
        )
        manager = AuthenticationManager(
            spot_client=MagicMock(),
            futures_clients={
                ExecutionMode.PAPER: paper_client,
                ExecutionMode.TESTNET: testnet_client,
            },
            execution_settings=settings,
        )

        self.assertIs(manager.future_client_for("BCHUSDT"), testnet_client)
        self.assertIs(manager.future_client_for("BTCUSDT"), paper_client)

    def test_unknown_asset_configuration_is_rejected(self):
        settings = ExecutionSettings(
            {"DOGEUSDT": ExecutionMode.TESTNET},
        )
        with self.assertRaises(ValueError):
            AuthenticationManager(
                spot_client=MagicMock(),
                futures_client=MagicMock(),
                execution_settings=settings,
            )


class TestRiskGuard(unittest.TestCase):
    def setUp(self):
        self.guard = PreTradeRiskGuard({
            "max_leverage": 3,
            "max_position_notional_pct": 0.5,
            "max_risk_per_trade_pct": 0.01,
            "max_daily_loss_pct": 0.02,
            "min_reward_risk_ratio": 1.5,
        })

    def test_accepts_bounded_trade(self):
        result = self.guard.evaluate(
            equity=1000, entry_price=100, stop_loss=98,
            take_profit=104, quantity=2, leverage=2, side="BUY",
        )
        self.assertTrue(result.allowed)

    def test_rejects_daily_loss_limit(self):
        result = self.guard.evaluate(
            equity=1000, entry_price=100, stop_loss=98,
            take_profit=104, quantity=2, leverage=2, side="BUY", daily_net_pnl=-20,
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "daily_loss_limit")

    def test_rejects_poor_reward_risk(self):
        result = self.guard.evaluate(
            equity=1000, entry_price=100, stop_loss=98,
            take_profit=101, quantity=2, leverage=2, side="BUY",
        )
        self.assertEqual(result.reason, "reward_risk_below_minimum")

    def test_rejects_stop_on_wrong_side(self):
        result = self.guard.evaluate(
            equity=1000, entry_price=100, stop_loss=101,
            take_profit=104, quantity=2, leverage=2, side="BUY",
        )
        self.assertEqual(result.reason, "stop_on_wrong_side")


class TestPerformanceTracker(unittest.TestCase):
    def test_net_return_includes_fees_and_funding(self):
        records = [
            {"incomeType": "REALIZED_PNL", "income": "25"},
            {"incomeType": "COMMISSION", "income": "-2"},
            {"incomeType": "FUNDING_FEE", "income": "-1"},
        ]
        summary = PerformanceTracker.summarize(records, starting_equity=1000)
        self.assertEqual(summary.net_pnl, 22)
        self.assertAlmostEqual(summary.return_pct, 2.2)


if __name__ == "__main__":
    unittest.main()
