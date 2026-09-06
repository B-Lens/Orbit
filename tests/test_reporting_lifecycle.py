import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from orbit.core.main import BinanceAutomation
from orbit.core.order_manager import OrderManager
from orbit.core.signal_analyzer import SignalAnalyzer


class TestReportingLifecycle(unittest.TestCase):
    @patch("orbit.core.signal_analyzer.time.sleep", return_value=None)
    @patch("orbit.core.signal_analyzer.threading.Thread")
    @patch("orbit.core.signal_analyzer.record_runtime_activity")
    @patch("orbit.core.signal_analyzer.STRATEGY_REGISTRY")
    def test_ohlcv_reporting_start_failure_does_not_block_signal_generation(
        self, strategy_registry, _record_activity, thread_class, _sleep
    ):
        historical_data = pd.DataFrame(
            {
                "open": [100.0],
                "high": [102.0],
                "low": [99.0],
                "close": [101.0],
                "volume": [12.5],
            },
            index=pd.DatetimeIndex(["2026-09-06 12:00:00"]),
        )
        strategy = MagicMock()
        strategy.generate_signals.return_value = None
        strategy_class = MagicMock(return_value=strategy)
        strategy_class.__module__ = "orbit.strategies.example_strategy"
        strategy_class.__name__ = "ExampleStrategy"
        strategy_registry.get.return_value = strategy_class
        thread_class.return_value.start.side_effect = RuntimeError("thread unavailable")

        analyzer = SignalAnalyzer.__new__(SignalAnalyzer)
        analyzer.trading_pairs = ["ETHUSDT"]
        analyzer.redis_client = MagicMock()
        analyzer.mongo_handler = MagicMock()
        analyzer.mongo_handler.handle_mongo_data.return_value = historical_data
        analyzer.execution_settings = MagicMock()
        analyzer.execution_settings.mode_for.return_value.value = "testnet"
        analyzer.send_logs = MagicMock()
        analyzer.send_signal_updates = MagicMock()
        analyzer._record_decision = MagicMock()

        self.assertEqual(list(analyzer.analyze_market({})), [])

        thread_class.assert_called_once_with(
            target=strategy.send_params,
            kwargs={
                "stock_df": historical_data,
                "symbol": "ETHUSDT",
                "duration": "15 MIN",
            },
            daemon=True,
            name="OHLCVParams-ETHUSDT",
        )
        thread_class.return_value.start.assert_called_once_with()
        strategy.generate_signals.assert_called_once_with(symbol="ETHUSDT")

    def test_active_position_is_rejected_before_market_data_or_strategy_work(self):
        analyzer = SignalAnalyzer.__new__(SignalAnalyzer)
        analyzer.trading_pairs = ["ETHUSDT"]
        analyzer.mongo_handler = MagicMock()
        analyzer.send_logs = MagicMock()
        analyzer.send_cooldown_update = MagicMock()
        analyzer._record_decision = MagicMock()

        signals = list(
            analyzer.analyze_market(
                {
                    "ETHUSDT": {
                        "reason": "active_position",
                        "position_side": "BUY",
                        "position_quantity": 0.5,
                    }
                }
            )
        )

        self.assertEqual(signals, [])
        analyzer.mongo_handler.handle_mongo_data.assert_not_called()
        analyzer._record_decision.assert_not_called()

    def test_signal_blocks_distinguish_active_positions_and_post_exit_cooldowns(self):
        automation = BinanceAutomation.__new__(BinanceAutomation)
        automation.trading_pairs = ["BTCUSDT", "ETHUSDT", "PAXGUSDT"]
        automation.trade_checker = MagicMock()
        automation.trade_checker.activePosition_coolMaker.return_value = {
            "ETHUSDT": {"symbol": "ETHUSDT"}
        }
        automation.trade_checker.is_in_cooldown.side_effect = (
            lambda symbol: symbol == "PAXGUSDT"
        )
        automation.trade_checker.get_cooldown.return_value = "2026-08-28T12:00:00+05:30"

        unavailable_symbols = automation.refresh_active_positions()

        self.assertEqual(
            unavailable_symbols,
            {
                "ETHUSDT": {
                    "reason": "active_position",
                    "position_side": None,
                    "position_quantity": None,
                },
                "PAXGUSDT": {
                    "reason": "post_exit_cooldown",
                    "cooldown_until": "2026-08-28T12:00:00+05:30",
                },
            },
        )
        automation.trade_checker.is_in_cooldown.assert_any_call("BTCUSDT")
        automation.trade_checker.is_in_cooldown.assert_any_call("PAXGUSDT")

    def test_entry_fill_does_not_start_post_exit_cooldown(self):
        automation = BinanceAutomation.__new__(BinanceAutomation)
        automation.order_manager = MagicMock()
        automation.order_manager.get_order.return_value = {
            "status": "FILLED",
            "executedQty": "0.5",
            "avgPrice": "100.25",
            "time": 1_722_470_400_000,
        }
        automation.trade_checker = MagicMock()
        automation.trades = {}
        automation.send_signal_updates = MagicMock()

        automation.monitor_order_execution(
            "ETHUSDT", 123, "BUY", 0.5, 100.0, "decision-1"
        )

        automation.trade_checker.set_cooldown.assert_not_called()
        self.assertEqual(
            automation.trades["ETHUSDT"]["entered_at"],
            "2024-08-01T00:00:00+00:00",
        )

    def test_filled_order_is_appended_to_decision_ledger(self):
        automation = BinanceAutomation.__new__(BinanceAutomation)
        automation.order_manager = MagicMock()
        automation.order_manager.get_order.return_value = {
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

        automation.order_manager.get_order.assert_called_once_with("ETHUSDT", 123)

    @patch("orbit.core.order_manager.time.sleep", return_value=None)
    def test_protective_order_submission_is_appended_to_decision_ledger(self, _sleep):
        manager = OrderManager.__new__(OrderManager)
        manager.config = {"trading_pairs_precision": {"ETHUSDT": 3}}
        manager.mongo_handler = MagicMock()
        manager.adjust_quantity_step = MagicMock(return_value=0.5)
        manager.adjust_price_tick = MagicMock(return_value=98.0)
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
