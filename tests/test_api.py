from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from orbit.api import get_command_center, get_notifications, get_status
from orbit.core.redis_manager import runtime_heartbeat_key


@patch("orbit.api.redis.Redis")
def test_status_is_online_when_redis_and_runtime_are_healthy(redis_cls: MagicMock) -> None:
    redis_client = redis_cls.return_value
    redis_client.get.return_value = "2026-09-05T09:00:00+00:00"

    response = get_status()

    assert response.status == "online"
    redis_client.ping.assert_called_once_with()
    redis_client.get.assert_called_once_with(runtime_heartbeat_key("default"))
    redis_client.close.assert_called_once_with()


@patch("orbit.api.redis.Redis")
def test_status_fails_when_runtime_heartbeat_is_missing(redis_cls: MagicMock) -> None:
    redis_cls.return_value.get.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        get_status()

    assert exc_info.value.status_code == 503
    assert "missing runtime heartbeat" in str(exc_info.value.detail)
    redis_cls.return_value.close.assert_called_once_with()


@patch.dict("os.environ", {"ORBIT_EXPECTED_RUNTIME_IDS": "blue,green"}, clear=False)
@patch("orbit.api.redis.Redis")
def test_status_requires_every_expected_runtime(redis_cls: MagicMock) -> None:
    redis_cls.return_value.get.side_effect = ["alive", None]

    with pytest.raises(HTTPException) as exc_info:
        get_status()

    assert exc_info.value.status_code == 503
    assert "green" in str(exc_info.value.detail)


@patch("orbit.api.list_notifications")
def test_notifications_returns_mirrored_discord_events(list_feed: MagicMock) -> None:
    list_feed.return_value = [
        {"id": "incomplete-record"},
        {
            "id": "event-1",
            "channel": "signal",
            "content": "BTCUSDT",
            "description": "Order placed successfully",
            "fields": [{"name": "Price", "value": "62000", "inline": True}],
            "created_at": "2026-09-05T09:00:00+00:00",
        },
    ]

    response = get_notifications(limit=25)

    assert response.notifications[0].channel == "signal"
    assert response.notifications[0].description == "Order placed successfully"
    assert len(response.notifications) == 1
    list_feed.assert_called_once_with(25)


@patch("orbit.api._risk_execution_state")
@patch("orbit.api._recent_signals")
@patch("orbit.api.read_observability")
@patch("orbit.api.read_sentiment")
@patch("orbit.api.read_positions")
@patch("orbit.api.read_runtime_state")
@patch("orbit.api.create_redis_client")
def test_command_center_uses_live_state_not_notification_feed(
    create_client: MagicMock,
    read_runtime: MagicMock,
    read_position_state: MagicMock,
    read_sentiment_state: MagicMock,
    read_observability_state: MagicMock,
    recent_signals: MagicMock,
    risk_state: MagicMock,
) -> None:
    client = create_client.return_value
    read_runtime.return_value = {
        "status": "online",
        "current_activity": "analyzing_signal",
        "detail": "BTCUSDT",
        "updated_at": "2026-09-06T00:00:00+00:00",
        "runtimes": [
            {
                "runtime_id": "default",
                "status": "online",
                "heartbeat_at": "2026-09-06T00:00:00+00:00",
            }
        ],
    }
    read_position_state.return_value = [
        {
            "trade_id": "decision-1",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "quantity": 0.1,
            "entry_price": 60_000,
            "current_price": 61_000,
            "unrealized_pnl": 100,
            "stop_loss": 59_000,
            "take_profit": 63_000,
            "protection_status": "protected",
            "execution_mode": None,
            "entered_at": None,
            "exit_pending": False,
        }
    ]
    read_sentiment_state.return_value = {
        "effective": "BULLISH",
        "confidence": 0.8,
    }
    read_observability_state.return_value = ([], [])
    recent_signals.return_value = [
        {
            "decision_id": "decision-2",
            "symbol": "ETHUSDT",
            "signal": None,
            "outcome": "no_signal",
        }
    ]
    risk_state.return_value = {
        "active_modes": ["testnet"],
        "can_submit_orders": True,
        "asset_modes": {"BTCUSDT": "testnet"},
        "risk_limits": {"max_leverage": 5},
    }

    response = get_command_center()

    assert response.runtime.current_activity == "analyzing_signal"
    assert response.positions[0].execution_mode == "testnet"
    assert response.signals[0].outcome == "no_signal"
    assert response.sentiment.effective == "BULLISH"
    client.ping.assert_called_once_with()
    client.close.assert_called_once_with()
