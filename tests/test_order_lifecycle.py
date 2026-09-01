import unittest
from unittest.mock import MagicMock, patch

from binance.error import ClientError

from orbit.core.execution import ExecutionMode, ExecutionSettings
from orbit.core.order_manager import OrderManager
from orbit.core.redis_manager import RedisManager
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

    def test_trade_field_merge_uses_atomic_redis_script(self):
        redis_client = MagicMock()
        manager = RedisManager(redis_client=redis_client)

        manager.merge_trade_fields("decision-1", {"orderId": 123})

        redis_client.eval.assert_called_once()
        self.assertEqual(redis_client.eval.call_args.args[1:3], (1, "trade:decision-1"))

    def test_trade_field_merge_propagates_redis_failure(self):
        redis_client = MagicMock()
        redis_client.eval.side_effect = ConnectionError("redis unavailable")
        manager = RedisManager(redis_client=redis_client)

        with self.assertRaisesRegex(ConnectionError, "redis unavailable"):
            manager.merge_trade_fields("decision-1", {"orderId": 123})

    def test_exchange_filter_normalization(self):
        self.assertEqual(self.manager.adjust_price_tick("BTCUSDT", 12.34), 12.3)
        self.assertEqual(self.manager.adjust_quantity_step("BTCUSDT", 0.0056), 0.005)
        self.assertTrue(self.manager.validate_notional("BTCUSDT", 1000, 0.005))
        self.assertFalse(self.manager.validate_notional("BTCUSDT", 999, 0.005))

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

    def test_account_trade_history_paginates_full_pages(self):
        first_page = [{"id": trade_id} for trade_id in range(1000)]
        self.manager.future_client.get_account_trades.side_effect = [
            first_page,
            [{"id": 1000}],
        ]

        records = self.manager.get_account_trades("BTCUSDT", 100, 200)

        self.assertEqual(len(records), 1001)
        self.assertEqual(
            self.manager.future_client.get_account_trades.call_args_list[1].kwargs[
                "fromId"
            ],
            1000,
        )
        self.assertNotIn(
            "endTime",
            self.manager.future_client.get_account_trades.call_args_list[1].kwargs,
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
    def test_position_discovery_ignores_stale_entry_price_without_exposure(self):
        checker = TradeChecker.__new__(TradeChecker)
        checker.order_manager = MagicMock()
        checker.order_manager.execution_settings.active_modes = ["testnet"]
        client = MagicMock()
        checker.order_manager.futures_clients = {"testnet": client}
        checker._get_position_risk = MagicMock(
            return_value=[
                {"symbol": "ETHUSDT", "entryPrice": "3500", "positionAmt": "0"},
                {"symbol": "BTCUSDT", "entryPrice": "100000", "positionAmt": "0.01"},
            ]
        )
        checker.load_trade = MagicMock(return_value=None)
        checker.scan_trade_keys = MagicMock(return_value=[])
        checker.merge_trade_fields = MagicMock()

        trades = checker.activePosition_coolMaker()

        self.assertNotIn("ETHUSDT", trades)
        self.assertEqual(trades["BTCUSDT"]["quantity"], 0.01)

    def test_position_reconciliation_starts_cooldown_after_offline_exit(self):
        checker = TradeChecker.__new__(TradeChecker)
        checker.order_manager = MagicMock()
        checker.order_manager.execution_settings.active_modes = ["testnet"]
        client = MagicMock()
        checker.order_manager.futures_clients = {"testnet": client}
        checker._get_position_risk = MagicMock(
            return_value=[
                {"symbol": "ETHUSDT", "entryPrice": "3500", "positionAmt": "0"}
            ]
        )
        checker.scan_trade_keys = MagicMock(return_value=["trade:decision-1"])
        checker.load_trade = MagicMock(
            return_value={
                "trade_id": "decision-1",
                "symbol": "ETHUSDT",
                "sl_order_id": "101",
                "tp_order_id": "202",
            }
        )
        checker.set_cooldown = MagicMock()
        checker.delete_trade_with_orders = MagicMock()

        trades = checker.activePosition_coolMaker()

        self.assertEqual(trades, {})
        self.assertEqual(
            checker.order_manager.cancel_algo_conditional_order.call_count, 2
        )
        checker.set_cooldown.assert_called_once_with("ETHUSDT")
        checker.delete_trade_with_orders.assert_called_once_with("decision-1")

    def test_position_reconciliation_resolves_duplicate_records_by_open_order(self):
        checker = TradeChecker.__new__(TradeChecker)
        checker.order_manager = MagicMock()
        checker.order_manager.execution_settings.active_modes = ["testnet"]
        client = MagicMock()
        checker.order_manager.futures_clients = {"testnet": client}
        checker._get_position_risk = MagicMock(
            return_value=[
                {"symbol": "ETHUSDT", "entryPrice": "3500", "positionAmt": "0.5"}
            ]
        )
        checker.scan_trade_keys = MagicMock(
            return_value=["trade:stale", "trade:current"]
        )
        records = {
            "stale": {
                "trade_id": "stale",
                "symbol": "ETHUSDT",
                "sl_order_id": "101",
                "entered_at": "2026-08-01T00:00:00+00:00",
            },
            "current": {
                "trade_id": "current",
                "symbol": "ETHUSDT",
                "sl_order_id": "202",
                "entered_at": "2026-08-02T00:00:00+00:00",
            },
        }
        checker.load_trade = MagicMock(side_effect=lambda trade_id: records[trade_id])
        checker.order_manager.get_conditional_open_orders.return_value = [
            {
                "algoId": "202",
                "quantity": "0.5",
                "side": "SELL",
                "algoType": "CONDITIONAL",
                "orderType": "STOP_MARKET",
            }
        ]
        checker.delete_trade = MagicMock()
        checker.deregister_order = MagicMock()
        checker.register_order = MagicMock()
        checker.merge_trade_fields = MagicMock()

        trades = checker.activePosition_coolMaker()

        self.assertEqual(trades["ETHUSDT"]["trade_id"], "current")
        checker.order_manager.cancel_algo_conditional_order.assert_called_once_with(
            "ETHUSDT", "101"
        )
        checker.delete_trade.assert_called_once_with("stale")
        checker.deregister_order.assert_called_once_with("101")

    def test_duplicate_reconciliation_preserves_shared_order_mapping(self):
        checker = TradeChecker.__new__(TradeChecker)
        checker.order_manager = MagicMock()
        checker.order_manager.execution_settings.active_modes = ["testnet"]
        checker.order_manager.futures_clients = {"testnet": MagicMock()}
        checker._get_position_risk = MagicMock(
            return_value=[
                {"symbol": "ETHUSDT", "entryPrice": "3500", "positionAmt": "0.5"}
            ]
        )
        checker.scan_trade_keys = MagicMock(
            return_value=["trade:stale", "trade:current"]
        )
        records = {
            "stale": {
                "trade_id": "stale",
                "symbol": "ETHUSDT",
                "sl_order_id": "202",
                "entered_at": "2026-08-01T00:00:00+00:00",
            },
            "current": {
                "trade_id": "current",
                "symbol": "ETHUSDT",
                "sl_order_id": "202",
                "entered_at": "2026-08-02T00:00:00+00:00",
            },
        }
        checker.load_trade = MagicMock(side_effect=lambda trade_id: records[trade_id])
        checker.order_manager.get_conditional_open_orders.return_value = [
            {
                "algoId": "202",
                "quantity": "0.5",
                "side": "SELL",
                "algoType": "CONDITIONAL",
                "orderType": "STOP_MARKET",
            }
        ]
        checker.delete_trade = MagicMock()
        checker.deregister_order = MagicMock()
        checker.register_order = MagicMock()
        checker.merge_trade_fields = MagicMock()

        trades = checker.activePosition_coolMaker()

        self.assertEqual(trades["ETHUSDT"]["trade_id"], "current")
        checker.order_manager.cancel_algo_conditional_order.assert_not_called()
        checker.delete_trade.assert_called_once_with("stale")
        checker.deregister_order.assert_not_called()
        checker.register_order.assert_called_once_with("202", "current")

    def test_duplicate_reconciliation_rejects_wrong_side_stop(self):
        checker = TradeChecker.__new__(TradeChecker)
        checker.order_manager = MagicMock()
        checker.order_manager.execution_settings.active_modes = ["testnet"]
        checker.order_manager.futures_clients = {"testnet": MagicMock()}
        checker._get_position_risk = MagicMock(
            return_value=[
                {"symbol": "ETHUSDT", "entryPrice": "3500", "positionAmt": "0.5"}
            ]
        )
        checker.scan_trade_keys = MagicMock(return_value=["trade:one", "trade:two"])
        records = {
            "one": {"trade_id": "one", "symbol": "ETHUSDT", "sl_order_id": "101"},
            "two": {"trade_id": "two", "symbol": "ETHUSDT", "sl_order_id": "202"},
        }
        checker.load_trade = MagicMock(side_effect=lambda trade_id: records[trade_id])
        checker.order_manager.get_conditional_open_orders.return_value = [
            {
                "algoId": "101",
                "side": "BUY",
                "closePosition": True,
                "orderType": "STOP_MARKET",
            }
        ]

        with self.assertRaisesRegex(RuntimeError, "no verified full-position"):
            checker.activePosition_coolMaker()

    def test_flat_duplicate_cleanup_retains_state_when_cancellation_fails(self):
        checker = TradeChecker.__new__(TradeChecker)
        checker.order_manager = MagicMock()
        checker.order_manager.execution_settings.active_modes = ["testnet"]
        checker.order_manager.futures_clients = {"testnet": MagicMock()}
        checker._get_position_risk = MagicMock(return_value=[])
        checker.scan_trade_keys = MagicMock(return_value=["trade:one", "trade:two"])
        records = {
            "one": {"trade_id": "one", "symbol": "ETHUSDT", "sl_order_id": "101"},
            "two": {"trade_id": "two", "symbol": "ETHUSDT", "tp_order_id": "202"},
        }
        checker.load_trade = MagicMock(side_effect=lambda trade_id: records[trade_id])
        checker.order_manager.get_conditional_open_orders.return_value = [
            {"algoId": "101", "orderType": "STOP_MARKET"}
        ]
        checker.order_manager.cancel_algo_conditional_order.side_effect = RuntimeError(
            "timeout"
        )
        checker.mongo_handler = MagicMock()
        checker.mongo_handler.store_trade_reconciliation_block.return_value = True
        checker.set_cooldown = MagicMock()
        checker.delete_trade_with_orders = MagicMock()

        self.assertEqual(checker.activePosition_coolMaker(), {})

        checker.order_manager.cancel_algo_conditional_order.assert_called_once_with(
            "ETHUSDT", "101"
        )
        checker.delete_trade_with_orders.assert_not_called()

    def test_exit_starts_post_exit_cooldown(self):
        checker = TradeChecker.__new__(TradeChecker)
        checker.trades = {
            "ETHUSDT": {
                "trade_id": "ETHUSDT",
                "sl_order_id": "101",
                "tp_order_id": "202",
            }
        }
        checker.order_manager = MagicMock()
        checker._position_is_flat = MagicMock(return_value=True)
        checker.load_trade = MagicMock(
            return_value={"sl_order_id": "101", "tp_order_id": "202"}
        )
        checker.delete_trade_with_orders = MagicMock()
        checker.set_cooldown = MagicMock()

        checker._exit_trade("ETHUSDT", "ETHUSDT")

        cancel_calls = {
            call.args
            for call in checker.order_manager.cancel_algo_conditional_order.call_args_list
        }
        self.assertEqual(cancel_calls, {("ETHUSDT", "101"), ("ETHUSDT", "202")})
        checker.delete_trade_with_orders.assert_called_once_with("ETHUSDT")
        checker.set_cooldown.assert_called_once_with("ETHUSDT")
        self.assertNotIn("ETHUSDT", checker.trades)

    def test_pending_exit_keeps_trade_state_until_broker_confirms_flat(self):
        checker = TradeChecker.__new__(TradeChecker)
        checker.trades = {"ETHUSDT": {"trade_id": "ETHUSDT"}}
        checker.update_trade_fields = MagicMock()
        checker.delete_trade_with_orders = MagicMock()
        checker.order_manager = MagicMock()

        checker._mark_exit_pending("ETHUSDT", "ETHUSDT")

        self.assertTrue(checker.trades["ETHUSDT"]["exit_pending"])
        checker.update_trade_fields.assert_called_once_with(
            "ETHUSDT", {"exit_pending": True}
        )
        checker.delete_trade_with_orders.assert_not_called()
        checker.order_manager.cancel_algo_conditional_order.assert_not_called()

    def test_stop_threshold_waits_for_flat_position_before_cleanup(self):
        checker = TradeChecker.__new__(TradeChecker)
        checker.trades = {"ETHUSDT": {"trade_id": "ETHUSDT", "price": 100.0}}
        checker.send_false_alarm = MagicMock()
        checker._mark_exit_pending = MagicMock()
        checker._exit_trade = MagicMock()

        checker.long_check_trade(
            risk_management={},
            symbol="ETHUSDT",
            stop_loss=99.0,
            target=105.0,
            current_price=98.0,
            stop_loss_order={"algoId": "101"},
            quantity=1.0,
        )

        checker._mark_exit_pending.assert_called_once_with("ETHUSDT", "ETHUSDT")
        checker._exit_trade.assert_not_called()

    def test_exit_attempts_sibling_cancellation_when_filled_order_is_terminal(self):
        checker = TradeChecker.__new__(TradeChecker)
        checker.trades = {
            "ETHUSDT": {
                "trade_id": "ETHUSDT",
                "sl_order_id": "101",
                "tp_order_id": "202",
            }
        }
        checker.order_manager = MagicMock()
        checker._position_is_flat = MagicMock(return_value=True)
        checker.order_manager.cancel_algo_conditional_order.side_effect = [
            RuntimeError("order already terminal"),
            {"algoId": "202", "status": "CANCELED"},
        ]
        checker.load_trade = MagicMock(
            return_value={"sl_order_id": "101", "tp_order_id": "202"}
        )
        checker.delete_trade_with_orders = MagicMock()
        checker.set_cooldown = MagicMock()

        checker._exit_trade("ETHUSDT", "ETHUSDT")

        self.assertEqual(
            checker.order_manager.cancel_algo_conditional_order.call_count, 2
        )
        checker.delete_trade_with_orders.assert_called_once_with("ETHUSDT")

    def test_exit_retains_orders_and_state_when_position_is_not_flat(self):
        checker = TradeChecker.__new__(TradeChecker)
        checker.trades = {"ETHUSDT": {"trade_id": "ETHUSDT"}}
        checker.order_manager = MagicMock()
        checker.load_trade = MagicMock(
            return_value={"sl_order_id": "101", "tp_order_id": "202"}
        )
        checker._position_is_flat = MagicMock(return_value=False)
        checker.delete_trade_with_orders = MagicMock()
        checker.set_cooldown = MagicMock()

        exited = checker._exit_trade("ETHUSDT", "ETHUSDT")

        self.assertFalse(exited)
        checker.order_manager.cancel_algo_conditional_order.assert_not_called()
        checker.delete_trade_with_orders.assert_not_called()
        checker.set_cooldown.assert_not_called()
        self.assertIn("ETHUSDT", checker.trades)

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


def test_testnet_order_probe():
    from scripts import check_testnet_orders as SCRIPT
    from unittest.mock import MagicMock

    manager = MagicMock()
    manager.config = {}
    manager.config["risk_management"] = {"BTCUSDT": 0.01}
    manager.config["FUTURE_LEVERAGE"] = 2

    # 1. Existing order skips submission
    manager.get_open_orders.return_value = [{"orderId": 7, "status": "NEW"}]
    manager.get_conditional_open_orders.return_value = []
    assert SCRIPT.check_symbol(manager, "BTCUSDT", 10.0).status == "SKIPPED"
    manager.place_order.assert_not_called()

    # 2. No open orders, places and cancels order
    manager.get_open_orders.return_value = []
    manager.get_symbol_price.return_value = 100.0
    manager.place_order.return_value = ({"orderId": 123}, 0.1, {})
    manager.cancel_order.return_value = {"orderId": 123, "status": "CANCELED"}

    assert SCRIPT.check_symbol(manager, "BTCUSDT", 10.0).status == "PASSED"
    manager.place_order.assert_called_once()
    manager.cancel_order.assert_called_once_with("BTCUSDT", 123)
