import unittest
from unittest.mock import MagicMock, patch

from orbit.core.main import BinanceAutomation
from orbit.core.order_manager import OrderManager


class TestReportingLifecycle(unittest.TestCase):
    def test_filled_order_is_appended_to_decision_ledger(self):
        automation = BinanceAutomation.__new__(BinanceAutomation)
        automation.order_manager = MagicMock()
        automation.order_manager.get_open_orders.return_value = {
            "status": "FILLED",
            "executedQty": "0.5",
            "avgPrice": "100.25",
        }
        automation.trade_checker = MagicMock()
        automation.trades = {}
        automation.send_signal_updates = MagicMock()

        automation.monitor_order_execution(
            "ETHUSDT", 123, "BUY", 0.5, 100.0, "decision-1"
        )

        automation.order_manager.mongo_handler.append_decision_event.assert_called_once_with(
            "decision-1",
            {
                "event_id": "order_filled:ETHUSDT:123",
                "status": "order_filled",
                "order_id": 123,
                "executed_quantity": "0.5",
                "average_price": "100.25",
            },
        )

    @patch("orbit.core.order_manager.time.sleep", return_value=None)
    def test_protective_order_submission_is_appended_to_decision_ledger(self, _sleep):
        manager = OrderManager.__new__(OrderManager)
        manager.config = {"trading_pairs_precision": {"ETHUSDT": 3}}
        manager.mongo_handler = MagicMock()
        manager.adjust_quantity_step = MagicMock(return_value=0.5)
        manager.place_algo_conditional_order = MagicMock(return_value={"algoId": 456})
        notify = MagicMock()

        response = manager._place_exit_order(
            symbol="ETHUSDT",
            side="SELL",
            price=98.0,
            quantity=0.5,
            trade_id="decision-1",
            order_type="STOP_MARKET",
            price_field="stopLossPrice",
            label="SL",
            notify=notify,
        )

        self.assertEqual(response, {"algoId": 456})
        manager.mongo_handler.append_decision_event.assert_called_once_with(
            "decision-1",
            {
                "status": "protective_order_submitted",
                "protective_order_type": "STOP_MARKET",
                "order_id": 456,
            },
        )


if __name__ == "__main__":
    unittest.main()
