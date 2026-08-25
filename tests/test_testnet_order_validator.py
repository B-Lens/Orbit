import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

from orbit.core.execution import ExecutionMode, ExecutionSettings
from orbit.core.testnet_order_validator import TestnetOrderValidator


class TestTestnetOrderValidator(unittest.TestCase):
    def setUp(self):
        self.manager = MagicMock()
        self.manager.execution_settings = ExecutionSettings(
            {"BTCUSDT": ExecutionMode.TESTNET, "ETHUSDT": ExecutionMode.LIVE}
        )
        self.manager.config = {"trading_pairs_precision": {"BTCUSDT": 3}}
        self.manager.trading_pairs = ["BTCUSDT"]
        self.manager.get_symbol_price.return_value = 100.07
        self.manager.adjust_price_tick.return_value = 100.0
        self.manager.fixed_asset_allocated.return_value = 0.0567
        self.manager.adjust_quantity_step.return_value = 0.056
        self.manager.validate_notional.return_value = True
        self.manager.submit_test_order.return_value = {}
        self.validator = TestnetOrderValidator(self.manager)

    def test_uses_exchange_test_endpoint_with_normalized_values(self):
        result = self.validator.validate_symbol("BTCUSDT")

        self.assertEqual(result, {})
        self.manager.refresh_symbol_filters.assert_called_once_with("BTCUSDT")
        self.manager.submit_test_order.assert_called_once_with(
            "BTCUSDT",
            side="BUY",
            type="LIMIT",
            timeInForce="GTC",
            quantity="0.056",
            price="100.0",
            recvWindow=60000,
        )

    def test_run_once_skips_live_assets_and_alerts_testnet_failures(self):
        self.manager.validate_notional.return_value = False
        self.manager.get_symbol_filters.return_value = {
            "MIN_NOTIONAL": {"notional": "10"}
        }

        result = self.validator.run_once()

        self.assertEqual(result, {"BTCUSDT": False})
        self.manager.send_alerts.assert_called_once()
        self.assertNotIn(call("ETHUSDT"), self.manager.get_symbol_price.call_args_list)
        self.manager.submit_test_order.assert_not_called()

    def test_refuses_live_asset(self):
        with self.assertRaisesRegex(ValueError, "non-testnet"):
            self.validator.validate_symbol("ETHUSDT")

    def test_next_run_is_strictly_future(self):
        now = datetime(2026, 8, 25, 2, 7, tzinfo=timezone.utc)

        next_run = self.validator._next_run(now)

        self.assertEqual(next_run, datetime(2026, 8, 26, 2, 7, tzinfo=timezone.utc))

    @patch.dict(
        "os.environ", {"ORBIT_TESTNET_VALIDATION_ENABLED": "ture"}, clear=False
    )
    def test_invalid_enablement_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be true or false"):
            TestnetOrderValidator.from_env(self.manager)


if __name__ == "__main__":
    unittest.main()
