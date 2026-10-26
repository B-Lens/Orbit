"""A rejected order must not generate a second Discord failure alert."""
import unittest
from unittest.mock import Mock, patch

from orbit.core.main import BinanceAutomation


class TestSignalOrderFailureNotifications(unittest.TestCase):
    def test_terminal_order_status_preserves_ledger_and_only_alerts_failures(self) -> None:
        for status in ("CANCELED", "REJECTED", "EXPIRED"):
            with self.subTest(status=status):
                automation = BinanceAutomation.__new__(BinanceAutomation)
                automation.order_manager = Mock()
                order = {"status": status}
                automation.order_manager.get_order.return_value = order
                automation.send_alerts = Mock()

                automation.monitor_order_execution(
                    "ETHUSDT", 123, "SELL", 0.389, 2406.99, 2420.0, 2380.0,
                    "decision-1"
                )

                automation.order_manager.mongo_handler.append_decision_event.assert_called_once_with(
                    "decision-1",
                    {
                        "event_id": f"order_{status.lower()}:ETHUSDT:123",
                        "status": f"order_{status.lower()}",
                        "order_id": 123,
                    },
                )
                automation.order_manager.cancel_order.assert_not_called()
                if status == "CANCELED":
                    automation.send_alerts.assert_not_called()
                else:
                    automation.send_alerts.assert_called_once_with(
                        data=None,
                        description=f"Order 123 for ETHUSDT was {status.lower()}",
                        fields=order,
                    )

    @patch("orbit.core.main.time.time", side_effect=[0, 600])
    def test_timeout_cancels_and_records_order_without_alert(self, _time: Mock) -> None:
        automation = BinanceAutomation.__new__(BinanceAutomation)
        automation.order_manager = Mock()
        automation.order_manager.cancel_order.return_value = {"status": "CANCELED"}
        automation.send_alerts = Mock()
        automation.handle_exception = Mock()

        automation.monitor_order_execution(
            "ETHUSDT", 123, "SELL", 0.389, 2406.99, 2420.0, 2380.0,
            "decision-1"
        )

        automation.order_manager.cancel_order.assert_called_once_with("ETHUSDT", 123)
        automation.order_manager.mongo_handler.append_decision_event.assert_called_once_with(
            "decision-1",
            {
                "event_id": "order_timeout_cancelled:ETHUSDT:123",
                "status": "order_timeout_cancelled",
                "order_id": 123,
            },
        )
        automation.send_alerts.assert_not_called()
        automation.handle_exception.assert_not_called()

    @patch("orbit.core.main.threading.Thread")
    @patch("orbit.core.main.time.sleep")
    @patch("orbit.core.main.position_lifecycle_lock")
    @patch("orbit.core.main.record_runtime_activity")
    def test_failed_order_does_not_alert_or_start_monitor(
        self, _activity, _lock, _sleep, thread,
    ):
        automation = BinanceAutomation.__new__(BinanceAutomation)
        automation.order_manager = Mock()
        automation.order_manager.place_order.return_value = (None, None, None)
        automation._trade_reasoner = Mock()
        automation._trade_reasoner.review_entry.return_value.take_trade = True
        automation.risk_management = {}
        automation.future_leverage = 5
        automation.send_alerts = Mock()

        automation.process_signal({
            "symbol": "ETHUSDT", "signal": "BUY", "entry_price": 1000,
            "stop_loss": 990, "take_profit": 1015,
        })

        automation.order_manager.place_order.assert_called_once()
        self.assertTrue(automation.order_manager.place_order.call_args.kwargs["ros"])
        automation.send_alerts.assert_not_called()
        thread.assert_not_called()
