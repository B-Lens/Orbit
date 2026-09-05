from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from orbit.api import get_status
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
