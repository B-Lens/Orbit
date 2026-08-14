import os
import unittest
from unittest.mock import patch

from orbit.core.execution import ExecutionMode, ExecutionSettings, FUTURES_TESTNET_URL
from orbit.core.performance import PerformanceTracker
from orbit.core.risk_manager import PreTradeRiskGuard


class TestExecutionSettings(unittest.TestCase):
    def test_safe_default_is_paper(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = ExecutionSettings.from_env()
        self.assertEqual(settings.mode, ExecutionMode.PAPER)
        self.assertFalse(settings.can_submit_orders)

    def test_testnet_uses_separate_credentials(self):
        env = {
            "ORBIT_EXECUTION_MODE": "testnet",
            "BINANCE_TESTNET_API_KEY": "key",
            "BINANCE_TESTNET_SECRET_KEY": "secret",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = ExecutionSettings.from_env()
        self.assertEqual(settings.futures_base_url, FUTURES_TESTNET_URL)
        self.assertTrue(settings.can_submit_orders)

    def test_live_requires_acknowledgement(self):
        env = {
            "ORBIT_EXECUTION_MODE": "live",
            "BINANCE_API_KEY": "key",
            "BINANCE_SECRET_KEY": "secret",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                ExecutionSettings.from_env()


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
