import unittest
from unittest.mock import MagicMock

from orbit.core.execution import ExecutionMode
from orbit.core.main import BinanceAutomation
from orbit.core.order_manager import OrderPreflight


class TestPaperAutomation(unittest.TestCase):
    def setUp(self):
        self.automation = BinanceAutomation.__new__(BinanceAutomation)
        self.automation.risk_management = {"PAXGUSDT": 0.01}
        self.automation.future_leverage = 2
        self.automation.order_manager = MagicMock()
        self.automation.order_manager.execution_settings.mode_for.return_value = (
            ExecutionMode.PAPER
        )
        self.automation.order_manager.mongo_handler = MagicMock()
        self.automation.send_logs = MagicMock()
        self.automation.send_alerts = MagicMock()
        self.signal = {
            "decision_id": "decision-1",
            "symbol": "PAXGUSDT",
            "signal": "BUY",
            "entry_price": 2400.0,
            "stop_loss": 2390.0,
            "take_profit": 2430.0,
        }

    def test_valid_paper_signal_is_recorded_without_failure_alert(self):
        self.automation.order_manager.preflight_paper_order.return_value = (
            OrderPreflight(
                True,
                "paper_validated",
                {"symbol": "PAXGUSDT", "quantity": 0.25},
                {"risk_pct": 0.0025},
            )
        )

        self.automation.process_signal(self.signal)

        self.automation.order_manager.place_order.assert_not_called()
        self.automation.send_alerts.assert_not_called()
        event = self.automation.order_manager.mongo_handler.append_decision_event.call_args.args[
            1
        ]
        self.assertEqual(event["status"], "paper_validated")

    def test_genuine_paper_issue_is_alerted_and_recorded(self):
        self.automation.order_manager.preflight_paper_order.return_value = (
            OrderPreflight(
                False,
                "reward_risk_below_minimum",
                {"symbol": "PAXGUSDT"},
                {"reward_risk_ratio": 1.0},
            )
        )

        self.automation.process_signal(self.signal)

        self.automation.send_alerts.assert_called_once()
        event = self.automation.order_manager.mongo_handler.append_decision_event.call_args.args[
            1
        ]
        self.assertEqual(event["status"], "reward_risk_below_minimum")


if __name__ == "__main__":
    unittest.main()
