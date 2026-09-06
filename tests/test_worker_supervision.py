import threading
from unittest.mock import MagicMock, call, patch

import pytest

from orbit.core.main import BinanceAutomation, TRADE_CHECKER_RESTART_DELAY


def test_trade_checker_retries_after_startup_failure() -> None:
    automation = BinanceAutomation.__new__(BinanceAutomation)
    automation.order_manager = MagicMock()
    automation.trade_checker = MagicMock()
    automation.trade_checker.activePosition_coolMaker.side_effect = [
        RuntimeError("temporary broker failure"),
        {},
    ]
    automation.trade_checker.monitor_trades.side_effect = KeyboardInterrupt
    automation.trade_checker_pair = ["ETHUSDT"]
    automation.risk_management = {"stop_loss_percent": 1}
    automation.send_logs = MagicMock()
    automation.handle_exception = MagicMock()
    automation._runtime_progress = {}
    automation._runtime_progress_lock = threading.Lock()

    with patch("orbit.core.main.record_runtime_activity"), patch(
        "orbit.core.main.time.sleep"
    ) as sleep:
        with pytest.raises(KeyboardInterrupt):
            automation.start_trade_checker()

    assert automation.trade_checker.activePosition_coolMaker.call_count == 2
    automation.handle_exception.assert_called_once()
    assert sleep.call_args_list == [call(TRADE_CHECKER_RESTART_DELAY), call(3)]
    assert automation._runtime_progress["trade_checker"] > 0
    automation.trade_checker.monitor_trades.assert_called_once()


def test_trade_checker_failure_immediately_invalidates_health() -> None:
    automation = BinanceAutomation.__new__(BinanceAutomation)
    automation.order_manager = MagicMock()
    automation.trade_checker = MagicMock()
    automation.trade_checker.activePosition_coolMaker.side_effect = RuntimeError(
        "broker unavailable"
    )
    automation.trade_checker_pair = ["ETHUSDT"]
    automation.risk_management = {"stop_loss_percent": 1}
    automation.send_logs = MagicMock()
    automation.handle_exception = MagicMock()
    automation._runtime_progress = {"trade_checker": 123.0}
    automation._runtime_progress_lock = threading.Lock()

    with patch("orbit.core.main.record_runtime_activity"), patch(
        "orbit.core.main.time.sleep", side_effect=KeyboardInterrupt
    ):
        with pytest.raises(KeyboardInterrupt):
            automation.start_trade_checker()

    assert automation._runtime_progress["trade_checker"] == 0


def test_worker_monitor_alerts_once_for_same_stopped_thread() -> None:
    automation = BinanceAutomation.__new__(BinanceAutomation)
    worker = MagicMock()
    worker.name = "TradeCheckerThread"
    worker.is_alive.return_value = False
    automation.workers_to_monitor = [worker]
    automation.send_alerts = MagicMock()

    with patch(
        "orbit.core.main.time.sleep", side_effect=[None, KeyboardInterrupt]
    ):
        with pytest.raises(KeyboardInterrupt):
            automation.monitor_workers(check_interval=1)

    automation.send_alerts.assert_called_once_with(
        data=None, description="Worker TradeCheckerThread has stopped!"
    )
