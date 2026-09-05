from unittest.mock import MagicMock

import redis

from orbit.core.main import (
    BinanceAutomation,
    RUNTIME_HEARTBEAT_INTERVAL,
    RUNTIME_HEARTBEAT_TTL,
)
from orbit.core.redis_manager import runtime_heartbeat_key


HEARTBEAT_KEY = runtime_heartbeat_key("test")


def test_runtime_heartbeat_is_published_when_workers_are_alive() -> None:
    automation = BinanceAutomation.__new__(BinanceAutomation)
    automation.order_manager = MagicMock()
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
    automation = BinanceAutomation.__new__(BinanceAutomation)
    automation.order_manager = MagicMock()
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
    automation = BinanceAutomation.__new__(BinanceAutomation)
    automation.order_manager = MagicMock()
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
