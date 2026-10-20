"""A rejected order must not generate a second Discord failure alert."""
import unittest
from unittest.mock import Mock, patch

from orbit.core.main import BinanceAutomation


class TestSignalOrderFailureNotifications(unittest.TestCase):
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
        automation.send_alerts.assert_not_called()
        thread.assert_not_called()
