from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from orbit.api import get_status
from orbit.core.redis_manager import REDIS_KEY_RUNTIME_HEARTBEAT


@patch("orbit.api.redis.Redis")
def test_status_is_online_when_redis_and_runtime_are_healthy(redis_cls: MagicMock) -> None:
    redis_client = redis_cls.return_value
    redis_client.get.return_value = "2026-09-05T09:00:00+00:00"

    response = get_status()

    assert response.status == "online"
    redis_client.ping.assert_called_once_with()
    redis_client.get.assert_called_once_with(REDIS_KEY_RUNTIME_HEARTBEAT)


@patch("orbit.api.redis.Redis")
def test_status_fails_when_runtime_heartbeat_is_missing(redis_cls: MagicMock) -> None:
    redis_cls.return_value.get.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        get_status()

    assert exc_info.value.status_code == 503
    assert "heartbeat is missing" in str(exc_info.value.detail)
