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
        self.manager.get_current_open_orders.return_value = []
        self.manager.get_symbol_price.return_value = 100.07
        self.manager.adjust_price_tick.return_value = 98.0
        self.manager.fixed_asset_allocated.return_value = 0.0567
        self.manager.adjust_quantity_step.return_value = 0.056
        self.manager.validate_notional.return_value = True
        self.manager.submit_validation_order.return_value = {"orderId": 123}
        self.manager.cancel_order.return_value = {
            "orderId": 123,
            "status": "CANCELED",
        }
        self.sleep = MagicMock()
        self.validator = TestnetOrderValidator(self.manager, sleep=self.sleep)

    def test_places_off_market_order_and_cancels_it(self):
        result = self.validator.validate_symbol("BTCUSDT")

        self.assertEqual(result["order"], {"orderId": 123})
        self.assertEqual(result["cancellation"]["status"], "CANCELED")
        self.manager.refresh_symbol_filters.assert_called_once_with("BTCUSDT")
        self.manager.adjust_price_tick.assert_called_once_with(
            "BTCUSDT", 100.07 * 0.98
        )
        self.manager.submit_validation_order.assert_called_once_with(
            "BTCUSDT",
            side="BUY",
            type="LIMIT",
            timeInForce="GTX",
            quantity="0.056",
            price="98.0",
            recvWindow=60000,
        )
        self.manager.cancel_order.assert_called_once_with("BTCUSDT", 123)

    def test_run_once_skips_live_assets_and_alerts_testnet_failures(self):
        self.manager.validate_notional.return_value = False
        self.manager.get_symbol_filters.return_value = {
            "MIN_NOTIONAL": {"notional": "10"}
        }

        result = self.validator.run_once()

        self.assertEqual(result, {"BTCUSDT": False})
        self.manager.send_alerts.assert_called_once()
        self.assertNotIn(call("ETHUSDT"), self.manager.get_symbol_price.call_args_list)
        self.manager.submit_validation_order.assert_not_called()

    def test_refuses_live_asset(self):
        with self.assertRaisesRegex(ValueError, "non-testnet"):
            self.validator.validate_symbol("ETHUSDT")

    def test_run_once_includes_monitored_testnet_assets(self):
        self.manager.execution_settings = ExecutionSettings(
            {
                "BTCUSDT": ExecutionMode.TESTNET,
                "BNBUSDT": ExecutionMode.TESTNET,
                "ETHUSDT": ExecutionMode.LIVE,
            }
        )
        self.manager.config["trading_pairs_precision"]["BNBUSDT"] = 2

        result = self.validator.run_once()

        self.assertEqual(result, {"BNBUSDT": True, "BTCUSDT": True})
        self.manager.submit_validation_order.assert_any_call(
            "BNBUSDT",
            side="BUY",
            type="LIMIT",
            timeInForce="GTX",
            quantity="0.056",
            price="98.0",
            recvWindow=60000,
        )

    def test_monitored_asset_can_use_exchange_step_without_configured_precision(self):
        self.manager.execution_settings = ExecutionSettings(
            {"BNBUSDT": ExecutionMode.TESTNET}
        )

        result = self.validator.validate_symbol("BNBUSDT")

        self.assertEqual(result["cancellation"]["status"], "CANCELED")
        self.manager.adjust_quantity_step.assert_called_once_with(
            "BNBUSDT", 0.0567
        )

    def test_skips_asset_when_an_open_order_exists(self):
        self.manager.get_current_open_orders.return_value = [{"orderId": 77}]

        result = self.validator.validate_symbol("BTCUSDT")

        self.assertEqual(
            result, {"status": "SKIPPED", "reason": "open_order_present"}
        )
        self.manager.submit_validation_order.assert_not_called()
        self.manager.cancel_order.assert_not_called()

    def test_cancellation_failure_marks_daily_validation_failed(self):
        self.manager.cancel_order.return_value = None

        result = self.validator.run_once()

        self.assertEqual(result, {"BTCUSDT": False})
        self.manager.send_alerts.assert_called_once()
        self.assertEqual(self.manager.cancel_order.call_count, 3)

    def test_exchange_minimum_cannot_enlarge_validation_allocation(self):
        self.manager.adjust_quantity_step.return_value = 0.057

        with self.assertRaisesRegex(ValueError, "enlarged"):
            self.validator.validate_symbol("BTCUSDT")

        self.manager.submit_validation_order.assert_not_called()

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
