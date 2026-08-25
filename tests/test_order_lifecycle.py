import unittest
from unittest.mock import MagicMock, patch

from binance.error import ClientError

from orbit.core.execution import ExecutionMode, ExecutionSettings
from orbit.core.order_manager import OrderManager
from orbit.core.trade_checker import TradeChecker, is_stop_order, is_take_profit_order


def _order_manager():
    manager = OrderManager(
        mongo_handler=MagicMock(),
        redis_client=MagicMock(),
        spot_client=MagicMock(),
        futures_client=MagicMock(),
        execution_settings=ExecutionSettings({"BTCUSDT": ExecutionMode.TESTNET}),
    )
    manager.config["trading_pairs_precision"]["BTCUSDT"] = 3
    manager.get_symbol_filters = MagicMock(
        return_value={
            "PRICE_FILTER": {"tickSize": "0.1", "minPrice": "0.1"},
            "LOT_SIZE": {"stepSize": "0.001", "minQty": "0.001"},
            "MIN_NOTIONAL": {"notional": "5"},
        }
    )
    manager.send_sl_update_notifier = MagicMock()
    manager.send_signal_updates = MagicMock()
    manager.get_available_usdt_balance = MagicMock(return_value=5000)
    return manager


class TestOrderManager(unittest.TestCase):
    def setUp(self):
        self.manager = _order_manager()

    def test_exchange_filter_normalization(self):
        self.assertEqual(self.manager.adjust_price_tick("BTCUSDT", 12.34), 12.3)
        self.assertEqual(self.manager.adjust_quantity_step("BTCUSDT", 0.0056), 0.005)
        self.assertTrue(self.manager.validate_notional("BTCUSDT", 1000, 0.005))
        self.assertFalse(self.manager.validate_notional("BTCUSDT", 999, 0.005))

    def test_test_order_submission_uses_testnet_gateway(self):
        self.manager.future_client.new_order_test.return_value = {}

        response = self.manager.submit_test_order(
            "BTCUSDT", side="BUY", type="LIMIT"
        )

        self.assertEqual(response, {})
        self.manager.future_client.new_order_test.assert_called_once_with(
            symbol="BTCUSDT", side="BUY", type="LIMIT"
        )

    def test_test_order_submission_refuses_live_asset(self):
        self.manager.execution_settings = ExecutionSettings(
            {"BTCUSDT": ExecutionMode.LIVE}
        )

        with self.assertRaisesRegex(ValueError, "live asset"):
            self.manager.submit_test_order("BTCUSDT", side="BUY", type="LIMIT")

        self.manager.future_client.new_order_test.assert_not_called()

    def test_filter_refresh_preserves_cached_value_when_fetch_fails(self):
        cached = {"MIN_NOTIONAL": {"notional": "5"}}
        self.manager._exchange_filters_cache["BTCUSDT"] = cached
        self.manager._fetch_symbol_filters = MagicMock(
            side_effect=RuntimeError("exchange unavailable")
        )

        try:
            with self.assertRaisesRegex(RuntimeError, "exchange unavailable"):
                self.manager.refresh_symbol_filters("BTCUSDT")

            self.assertIs(
                self.manager._exchange_filters_cache["BTCUSDT"], cached
            )
        finally:
            self.manager._exchange_filters_cache.pop("BTCUSDT", None)

    def test_get_order_uses_endpoint_that_includes_terminal_state(self):
        self.manager.future_client.query_order.return_value = {
            "orderId": 123,
            "status": "FILLED",
        }

        order = self.manager.get_order("BTCUSDT", 123)

        self.assertEqual(order["status"], "FILLED")
        self.manager.future_client.query_order.assert_called_once_with(
            symbol="BTCUSDT", orderId=123, recvWindow=60000
        )

    def test_risk_position_size_respects_position_notional_limit(self):
        self.manager.get_usdt_balance = MagicMock(return_value=5000)

        quantity, required_margin = self.manager.calculate_risk_position_size(
            "BTCUSDT", entry_price=4593.35, stop_price=4591.244, risk_perc=0.01
        )

        notional = 4593.35 * quantity
        self.assertLessEqual(notional, 5000 * 0.25)
        self.assertAlmostEqual(required_margin, notional)

    def test_risk_position_size_respects_leveraged_available_margin(self):
        self.manager.get_usdt_balance = MagicMock(return_value=100)
        self.manager.get_available_usdt_balance = MagicMock(return_value=40)
        self.manager.risk_guard.max_position_notional_pct = 10.0

        quantity, required_margin = self.manager.calculate_risk_position_size(
            "BTCUSDT", entry_price=100, stop_price=99.9, risk_perc=0.01, leverage=2
        )

        self.assertEqual(quantity, 0.784)
        self.assertEqual(required_margin, 39.2)

    def test_risk_position_size_rejects_leverage_above_policy(self):
        self.manager.get_usdt_balance = MagicMock(return_value=100)

        result = self.manager.calculate_risk_position_size(
            "BTCUSDT", entry_price=100, stop_price=99, risk_perc=0.01, leverage=6
        )

        self.assertEqual(result, (0.0, 0.0))

    def test_exchange_minimum_does_not_raise_quantity_above_safe_cap(self):
        self.manager.get_usdt_balance = MagicMock(return_value=10)

        result = self.manager.calculate_risk_position_size(
            "BTCUSDT", entry_price=1000, stop_price=999, risk_perc=0.01
        )

        self.assertEqual(result, (0.0, 0.0))

    def test_sl_and_target_share_normalized_exit_order_path(self):
        self.manager.place_algo_conditional_order = MagicMock(
            side_effect=[{"algoId": 1}, {"algoId": 2}]
        )
        sl = self.manager.place_sl_order(
            "BTCUSDT", "SELL", 41000.04, -0.0056, "trade-1"
        )
        target = self.manager.place_target_order(
            "BTCUSDT", "SELL", 45000.06, 0.0056, "trade-1"
        )

        self.assertEqual((sl["algoId"], target["algoId"]), (1, 2))
        calls = self.manager.place_algo_conditional_order.call_args_list
        self.assertEqual(calls[0].kwargs["order_type"], "STOP_MARKET")
        self.assertEqual(calls[1].kwargs["order_type"], "TAKE_PROFIT_MARKET")
        self.assertEqual([call.kwargs["quantity"] for call in calls], [0.006, 0.006])
        self.assertEqual(
            [call.kwargs["stop_price"] for call in calls], [41000.0, 45000.1]
        )

    def test_algo_order_registers_parent_trade(self):
        self.manager.future_client.sign_request.return_value = {"algoId": 123}
        response = self.manager.place_algo_conditional_order(
            "BTCUSDT", "SELL", "STOP_MARKET", 41000, 0.01, trade_id="trade-1"
        )
        self.assertEqual(response, {"algoId": 123})
        self.manager.redis_client.set.assert_called_once_with("order:123", "trade-1")

    def test_notional_rejection_is_attached_to_decision(self):
        self.manager.get_usdt_balance = MagicMock(return_value=1000)
        self.manager.calculate_risk_position_size = MagicMock(return_value=(0.004, 2))
        self.manager.validate_notional = MagicMock(return_value=False)

        response = self.manager.place_order(
            {"BTCUSDT": 0.01},
            "BTCUSDT",
            "BUY",
            price=1000,
            sl=990,
            target=1020,
            trade_id="decision-1",
        )

        self.assertEqual(response, (None, None, None))
        self.manager.mongo_handler.append_decision_event.assert_called_once()
        decision_id, event = (
            self.manager.mongo_handler.append_decision_event.call_args.args
        )
        self.assertEqual(decision_id, "decision-1")
        self.assertEqual(event["reason"], "minimum_notional")

    def test_order_uses_actual_required_margin_below_fixed_spend(self):
        self.manager.get_usdt_balance = MagicMock(return_value=1000)
        self.manager.get_available_usdt_balance = MagicMock(return_value=20)
        self.manager.get_daily_net_pnl = MagicMock(return_value=0)
        self.manager.future_client.new_order.return_value = {"orderId": 1}

        response, quantity, _ = self.manager.place_order(
            {"BTCUSDT": 0.01},
            "BTCUSDT",
            "BUY",
            price=100,
            sl=99,
            target=102,
            leverage=2,
            quantity=0.1,
            ros=True,
            trade_id="decision-low-margin",
        )

        self.assertEqual(response, {"orderId": 1})
        self.assertEqual(quantity, 0.1)
        self.manager.future_client.new_order.assert_called_once()

    def test_order_rounds_configured_quantity_precision_down(self):
        self.manager.get_usdt_balance = MagicMock(return_value=1000)
        self.manager.get_daily_net_pnl = MagicMock(return_value=0)
        self.manager.future_client.new_order.return_value = {"orderId": 1}
        self.manager.config["trading_pairs_precision"]["BTCUSDT"] = 2
        self.manager.get_symbol_filters.return_value["LOT_SIZE"] = {
            "stepSize": "0.001",
            "minQty": "0.001",
        }

        response, quantity, _ = self.manager.place_order(
            {"BTCUSDT": 0.01},
            "BTCUSDT",
            "BUY",
            price=1000,
            sl=990,
            target=1020,
            quantity=0.2499,
            ros=True,
            trade_id="decision-high-priced-asset",
        )

        self.assertEqual(response, {"orderId": 1})
        self.assertEqual(quantity, 0.24)

    def test_order_submission_is_persisted_before_post_submission_work(self):
        self.manager.get_usdt_balance = MagicMock(return_value=1000)
        self.manager.get_daily_net_pnl = MagicMock(return_value=0)
        self.manager.future_client.new_order.return_value = {
            "orderId": 123,
            "clientOrderId": "exchange-client-id",
        }

        def assert_submission_already_persisted(_seconds):
            self.manager.mongo_handler.append_decision_event.assert_called_once_with(
                "decision-1",
                {
                    "event_id": "order_submitted:BTCUSDT:123",
                    "status": "order_submitted",
                    "order_id": 123,
                    "client_order_id": "exchange-client-id",
                },
            )

        with patch(
            "orbit.core.order_manager.time.sleep",
            side_effect=assert_submission_already_persisted,
        ):
            response, _, _ = self.manager.place_order(
                {"BTCUSDT": 0.01},
                "BTCUSDT",
                "BUY",
                price=100,
                sl=99,
                target=102,
                quantity=0.1,
                ros=True,
                trade_id="decision-1",
            )

        self.assertEqual(response["orderId"], 123)


class TestTradeChecker(unittest.TestCase):
    def test_order_classification(self):
        stop_orders = [
            {"orderType": "STOP_MARKET"},
            {"algoType": "CONDITIONAL", "orderType": "STOP"},
            {"algoType": "STOP"},
        ]
        target_orders = [
            {"orderType": "TAKE_PROFIT_MARKET"},
            {"algoType": "ALGO", "orderType": "TAKE_PROFIT"},
        ]
        self.assertTrue(all(is_stop_order(order) for order in stop_orders))
        self.assertTrue(all(is_take_profit_order(order) for order in target_orders))
        self.assertFalse(is_stop_order(None))
        self.assertFalse(is_take_profit_order({"orderType": "LIMIT"}))

    @staticmethod
    def _client_error(status_code=408, error_code=-1007):
        error = ClientError.__new__(ClientError)
        Exception.__init__(error, "backend timeout")
        error.status_code = status_code
        error.error_code = error_code
        error.error_message = "backend timeout"
        return error

    @patch("orbit.core.trade_checker.time.sleep")
    def test_position_risk_retries_only_transient_timeouts(self, sleep):
        checker = TradeChecker.__new__(TradeChecker)
        client = MagicMock()
        snapshot = [{"symbol": "XRPUSDT", "positionAmt": "0"}]
        client.get_position_risk.side_effect = [self._client_error(), snapshot]
        self.assertEqual(checker._get_position_risk(client), snapshot)
        sleep.assert_called_once_with(1.0)

        client.reset_mock()
        client.get_position_risk.side_effect = self._client_error(400, -1121)
        with self.assertRaises(ClientError):
            checker._get_position_risk(client)
        client.get_position_risk.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
