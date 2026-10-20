"""Order failures remain observable locally without Discord notification spam."""
import unittest
from unittest.mock import Mock, patch

from binance.error import ClientError

from orbit.core.exception_manager import ExceptionManager
from orbit.core.order_manager import OrderManager


class TestOrderFailureNotifications(unittest.TestCase):
    def setUp(self):
        self.manager = OrderManager.__new__(OrderManager)
        self.manager.send_alerts = Mock()
        self.manager.send_exception = Mock()
        self.manager._record_order_rejection = Mock()
        self.manager.config = {"trading_pairs_precision": {"BTCUSDT": 3}}
        self.manager.adjust_price_tick = Mock(side_effect=lambda symbol, price: price)
        self.manager.adjust_trigger_price = Mock(side_effect=lambda *args: args[1])
        self.manager._get_opposite_side = Mock(return_value="SELL")
        self.manager.get_usdt_balance = Mock(return_value=1000)
        self.manager.get_available_usdt_balance = Mock(return_value=1000)
        self.manager.adjust_quantity_step = Mock(side_effect=lambda symbol, qty: qty)
        self.manager.get_daily_net_pnl = Mock(return_value=0)
        self.manager.risk_guard = Mock()
        self.manager.risk_guard.evaluate.return_value = Mock(
            allowed=True, reason="allowed", metrics={}
        )
        self.manager.validate_notional = Mock(return_value=True)
        self.manager.get_symbol_filters = Mock(return_value={})
        self.manager.get_symbol_price = Mock(return_value=1000)
        self.manager._order_client_for = Mock()

    def test_limit_validation_rejections_do_not_send_alerts(self):
        cases = [
            ({"price": None}, "missing_limit_price"),
            ({"quantity": 0}, "insufficient_funds_or_quantity"),
            ({"quantity": 0.00001}, "quantity_non_positive"),
            ({"sl": None}, "stop_loss_required"),
        ]
        for overrides, reason in cases:
            with self.subTest(reason=reason):
                params = dict(price=1000, quantity=0.01, sl=990, trade_id="decision-1")
                params.update(overrides)
                result = self.manager.place_order({}, "BTCUSDT", "BUY", **params)
                self.assertEqual(result, (None, None, None))
                self.assertEqual(
                    self.manager._record_order_rejection.call_args.args,
                    ("decision-1", reason),
                )
        self.manager.send_alerts.assert_not_called()
        self.manager.send_exception.assert_not_called()
        self.manager._order_client_for.assert_not_called()

    def test_risk_and_notional_rejections_do_not_send_alerts(self):
        for risk_allowed, reason in [(False, "daily_loss"), (True, "minimum_notional")]:
            with self.subTest(reason=reason):
                self.manager.risk_guard.evaluate.return_value = Mock(
                    allowed=risk_allowed, reason=reason, metrics={}
                )
                self.manager.validate_notional.return_value = False
                result = self.manager.place_order(
                    {}, "BTCUSDT", "BUY", price=1000, quantity=0.01,
                    sl=990, trade_id="decision-1",
                )
                self.assertEqual(result, (None, None, None))
                self.assertEqual(self.manager._record_order_rejection.call_args.args,
                                 ("decision-1", reason))
        self.manager.send_alerts.assert_not_called()
        self.manager._order_client_for.assert_not_called()

    def test_market_validation_rejections_do_not_send_alerts(self):
        self.manager.validate_notional.return_value = False
        for quantity in (0, 0.01):
            with self.subTest(quantity=quantity):
                self.assertIsNone(self.manager.place_market_order("BTCUSDT", "BUY", quantity))
        self.manager.send_alerts.assert_not_called()
        self.manager._order_client_for.assert_not_called()

    def test_entry_exception_requests_local_reporting_only(self):
        self.manager.get_symbol_price.side_effect = RuntimeError("order failed")
        self.manager.handle_exception = Mock()
        self.assertIsNone(self.manager.place_market_order("BTCUSDT", "BUY", 0.01))
        self.assertFalse(self.manager.handle_exception.call_args.kwargs["notify_discord"])

    def test_limit_and_market_exchange_rejections_request_local_reporting(self):
        error = ClientError(400, -2010, "Order rejected", {})
        self.manager.adjust_price_tick.side_effect = error
        self.manager.get_symbol_price.side_effect = error
        self.manager.clientExceptionHandler = Mock()
        self.assertEqual(
            self.manager.place_order({}, "BTCUSDT", "BUY", price=1000),
            (None, None, None),
        )
        self.assertFalse(
            self.manager.clientExceptionHandler.call_args.kwargs["notify_discord"]
        )
        self.assertIsNone(self.manager.place_market_order("BTCUSDT", "BUY", 0.01))
        self.assertFalse(
            self.manager.clientExceptionHandler.call_args.kwargs["notify_discord"]
        )
        self.manager.send_alerts.assert_not_called()

    @patch("orbit.core.exception_manager.get_commit_id", return_value="unknown")
    def test_exception_reporting_preserves_records_and_defaults(self, _commit):
        manager = ExceptionManager()
        manager._record_command_center_exception = Mock()
        manager.exception_trigger = Mock()
        error = RuntimeError("order failed")
        for method, kwargs in [
            (manager.handle_exception, {"exception": error, "context_description": "entry"}),
            (manager.clientExceptionHandler, {"symbol": "BTCUSDT", "error": error}),
        ]:
            with self.subTest(method=method.__name__):
                manager.exception_trigger.reset_mock()
                manager._record_command_center_exception.reset_mock()
                method(**kwargs, notify_discord=False)
                manager._record_command_center_exception.assert_called_once()
                manager.exception_trigger.assert_not_called()
                method(**kwargs)
                manager.exception_trigger.assert_called_once()
