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
    return manager


class TestOrderManager(unittest.TestCase):
    def setUp(self):
        self.manager = _order_manager()

    def test_exchange_filter_normalization(self):
        self.assertEqual(self.manager.adjust_price_tick("BTCUSDT", 12.34), 12.3)
        self.assertEqual(self.manager.adjust_quantity_step("BTCUSDT", 0.0056), 0.005)
        self.assertTrue(self.manager.validate_notional("BTCUSDT", 1000, 0.005))
        self.assertFalse(self.manager.validate_notional("BTCUSDT", 999, 0.005))

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

    @patch("orbit.core.order_manager.time.sleep")
    def test_guarded_probe_can_use_bounded_gtd_order(self, _sleep):
        self.manager.get_usdt_balance = MagicMock(return_value=1000)
        self.manager.calculate_risk_position_size = MagicMock(return_value=(1, 50))
        self.manager.get_daily_net_pnl = MagicMock(return_value=0)
        self.manager.validate_notional = MagicMock(return_value=True)
        self.manager.future_client.new_order.return_value = {
            "orderId": 123,
            "symbol": "BTCUSDT",
        }

        response, _, _ = self.manager.place_order(
            {"BTCUSDT": 0.01},
            "BTCUSDT",
            "BUY",
            price=100,
            sl=98,
            target=104,
            ros=True,
            trade_id="probe-1",
            time_in_force="GTD",
            good_till_date=1_900_000_000_000,
        )

        self.assertIsNotNone(response)
        params = self.manager.future_client.new_order.call_args.kwargs
        self.assertEqual(params["timeInForce"], "GTD")
        self.assertEqual(params["goodTillDate"], 1_900_000_000_000)

    def test_invalid_gtd_is_rejected_before_exchange_mutation(self):
        response = self.manager.place_order(
            {"BTCUSDT": 0.01},
            "BTCUSDT",
            "BUY",
            price=100,
            sl=98,
            target=104,
            trade_id="probe-2",
            time_in_force="GTD",
            good_till_date=None,
        )

        self.assertEqual(response, (None, None, None))
        self.manager.future_client.change_leverage.assert_not_called()
        self.manager.future_client.new_order.assert_not_called()


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
