import threading
import time
from unittest.mock import MagicMock, patch

import redis

from orbit.core.main import (
    BinanceAutomation,
    RUNTIME_HEARTBEAT_INTERVAL,
    RUNTIME_HEARTBEAT_TTL,
)
from orbit.core.redis_manager import runtime_heartbeat_key


HEARTBEAT_KEY = runtime_heartbeat_key("test")


def heartbeat_automation() -> BinanceAutomation:
    automation = BinanceAutomation.__new__(BinanceAutomation)
    automation.order_manager = MagicMock()
    automation._runtime_progress = {
        "signal_analysis": time.monotonic(),
        "trade_checker": time.monotonic(),
    }
    automation._runtime_progress_lock = threading.Lock()
    return automation


def test_sentiment_cron_initialization_failure_does_not_stop_startup() -> None:
    automation = BinanceAutomation.__new__(BinanceAutomation)
    automation._croner = None
    automation.workers_to_monitor = []
    automation.handle_exception = MagicMock()

    with (
        patch("orbit.core.main.Croner", side_effect=ConnectionError("refused")),
        patch("orbit.core.main.threading.Thread") as thread,
    ):
        automation.handle_crons()

    automation.handle_exception.assert_called_once()
    assert (
        automation.handle_exception.call_args.kwargs["context_description"]
        == "Sentiment cron disabled because initialization failed"
    )
    thread.assert_not_called()
    assert automation.workers_to_monitor == []


def test_runtime_heartbeat_is_published_when_workers_are_alive() -> None:
    automation = heartbeat_automation()
    worker = MagicMock()
    worker.is_alive.return_value = True
    automation.workers_to_monitor = [worker]
    stop_event = MagicMock()
    stop_event.is_set.side_effect = [False, True]

    automation.publish_runtime_heartbeat(stop_event, HEARTBEAT_KEY)

    automation.order_manager.redis_client.setex.assert_called_once()
    args = automation.order_manager.redis_client.setex.call_args.args
    assert args[:2] == (HEARTBEAT_KEY, RUNTIME_HEARTBEAT_TTL)
    stop_event.wait.assert_called_once_with(RUNTIME_HEARTBEAT_INTERVAL)


def test_runtime_heartbeat_is_removed_when_a_worker_stops() -> None:
    automation = heartbeat_automation()
    worker = MagicMock()
    worker.is_alive.return_value = False
    automation.workers_to_monitor = [worker]
    stop_event = MagicMock()
    stop_event.is_set.side_effect = [False, True]

    automation.publish_runtime_heartbeat(stop_event, HEARTBEAT_KEY)

    automation.order_manager.redis_client.setex.assert_not_called()
    automation.order_manager.redis_client.delete.assert_called_once_with(
        HEARTBEAT_KEY
    )


def test_runtime_heartbeat_retries_after_redis_error() -> None:
    automation = heartbeat_automation()
    automation.order_manager.redis_client.setex.side_effect = [
        redis.ConnectionError("temporary failure"),
        None,
    ]
    worker = MagicMock()
    worker.is_alive.return_value = True
    automation.workers_to_monitor = [worker]
    stop_event = MagicMock()
    stop_event.is_set.side_effect = [False, False, True]

    automation.publish_runtime_heartbeat(stop_event, HEARTBEAT_KEY)

    assert automation.order_manager.redis_client.setex.call_count == 2
    assert stop_event.wait.call_count == 2


def test_runtime_heartbeat_is_removed_when_critical_progress_is_stale() -> None:
    automation = heartbeat_automation()
    automation._runtime_progress["trade_checker"] = 0
    worker = MagicMock()
    worker.is_alive.return_value = True
    automation.workers_to_monitor = [worker]
    stop_event = MagicMock()
    stop_event.is_set.side_effect = [False, True]

    automation.publish_runtime_heartbeat(stop_event, HEARTBEAT_KEY)

    automation.order_manager.redis_client.setex.assert_not_called()
    automation.order_manager.redis_client.delete.assert_called_once_with(
        HEARTBEAT_KEY
    )
