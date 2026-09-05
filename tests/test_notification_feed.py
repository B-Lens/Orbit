import json
from unittest.mock import MagicMock, patch

from orbit.core.notification_feed import list_notifications, record_notification


@patch("orbit.core.notification_feed.create_redis_client")
def test_record_notification_prepends_and_bounds_feed(create_client: MagicMock) -> None:
    client = create_client.return_value

    record_notification(
        "signal",
        "BTCUSDT",
        "Order placed",
        [{"name": "Price", "value": "62000", "inline": True}],
    )

    pipeline = client.pipeline.return_value
    serialized = pipeline.lpush.call_args.args[1]
    assert json.loads(serialized)["channel"] == "signal"
    pipeline.ltrim.assert_called_once_with("orbit:notifications", 0, 249)
    pipeline.execute.assert_called_once_with()
    client.close.assert_called_once_with()


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
