import json
from unittest.mock import MagicMock, patch

from orbit.core.notification_feed import (
    _write_notification,
    list_notifications,
    record_notification,
)


@patch("orbit.core.notification_feed.create_redis_client")
def test_write_notification_prepends_and_bounds_feed(create_client: MagicMock) -> None:
    client = create_client.return_value

    _write_notification({"channel": "signal", "content": "BTCUSDT"})

    pipeline = client.pipeline.return_value
    serialized = pipeline.lpush.call_args.args[1]
    assert json.loads(serialized)["channel"] == "signal"
    pipeline.ltrim.assert_called_once_with("orbit:notifications", 0, 249)
    pipeline.execute.assert_called_once_with()
    client.close.assert_called_once_with()


@patch("orbit.core.notification_feed._ensure_writer_started")
@patch("orbit.core.notification_feed._notification_queue")
def test_record_notification_only_queues_work(
    notification_queue: MagicMock, ensure_started: MagicMock
) -> None:
    record_notification("signal", "BTCUSDT", "Order placed", [])

    event = notification_queue.put_nowait.call_args.args[0]
    assert event["channel"] == "signal"
    assert event["description"] == "Order placed"
    ensure_started.assert_called_once_with()


@patch("orbit.core.notification_feed.create_redis_client")
def test_list_notifications_skips_malformed_records(create_client: MagicMock) -> None:
    client = create_client.return_value
    client.lrange.return_value = [
        json.dumps({"id": "one", "channel": "alerts"}),
        "not-json",
    ]

    notifications = list_notifications(10)

    assert notifications == [{"id": "one", "channel": "alerts"}]
    client.lrange.assert_called_once_with("orbit:notifications", 0, 9)
    client.close.assert_called_once_with()
