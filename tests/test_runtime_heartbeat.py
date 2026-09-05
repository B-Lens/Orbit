from unittest.mock import MagicMock

from orbit.core.main import (
    BinanceAutomation,
    RUNTIME_HEARTBEAT_INTERVAL,
    RUNTIME_HEARTBEAT_TTL,
)
from orbit.core.redis_manager import REDIS_KEY_RUNTIME_HEARTBEAT


def test_runtime_heartbeat_is_published_when_workers_are_alive() -> None:
    automation = BinanceAutomation.__new__(BinanceAutomation)
    automation.order_manager = MagicMock()
    worker = MagicMock()
    worker.is_alive.return_value = True
    automation.workers_to_monitor = [worker]
    stop_event = MagicMock()
    stop_event.is_set.side_effect = [False, True]

    automation.publish_runtime_heartbeat(stop_event)

    automation.order_manager.redis_client.setex.assert_called_once()
    args = automation.order_manager.redis_client.setex.call_args.args
    assert args[:2] == (REDIS_KEY_RUNTIME_HEARTBEAT, RUNTIME_HEARTBEAT_TTL)
    stop_event.wait.assert_called_once_with(RUNTIME_HEARTBEAT_INTERVAL)


def test_runtime_heartbeat_is_removed_when_a_worker_stops() -> None:
    automation = BinanceAutomation.__new__(BinanceAutomation)
    automation.order_manager = MagicMock()
    worker = MagicMock()
    worker.is_alive.return_value = False
    automation.workers_to_monitor = [worker]
    stop_event = MagicMock()
    stop_event.is_set.side_effect = [False, True]

    automation.publish_runtime_heartbeat(stop_event)

    automation.order_manager.redis_client.setex.assert_not_called()
    automation.order_manager.redis_client.delete.assert_called_once_with(
        REDIS_KEY_RUNTIME_HEARTBEAT
    )
