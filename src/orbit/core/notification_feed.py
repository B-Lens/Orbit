"""Redis-backed feed for notifications delivered to Discord."""

import json
import logging
import os
import queue
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, cast
from uuid import uuid4

import redis

logger = logging.getLogger("Orbit")

NOTIFICATION_FEED_KEY = "orbit:notifications"
DEFAULT_FEED_LIMIT = 250
MAX_FEED_LIMIT = 500
WRITE_QUEUE_LIMIT = 1_000

_notification_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(
    maxsize=WRITE_QUEUE_LIMIT
)
_worker_lock = threading.Lock()
_worker_started = False


def create_redis_client() -> redis.Redis:
    """Create a decoded Redis client using Orbit's standard environment."""
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "0")),
        username=os.getenv("REDIS_USERNAME"),
        password=os.getenv("REDIS_PASSWORD"),
        ssl=os.getenv("REDIS_SSL", "false").lower() == "true",
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )


def _write_notification(event: Dict[str, Any]) -> None:
    """Persist one queued event from the background writer."""
    try:
        client = create_redis_client()
        try:
            pipeline = client.pipeline()
            pipeline.lpush(NOTIFICATION_FEED_KEY, json.dumps(event))
            pipeline.ltrim(NOTIFICATION_FEED_KEY, 0, DEFAULT_FEED_LIMIT - 1)
            pipeline.execute()
        finally:
            client.close()
    except redis.RedisError as exc:
        logger.warning("Unable to mirror Discord notification to the UI feed: %s", exc)


def _notification_writer() -> None:
    """Drain notification events without blocking the calling trading thread."""
    while True:
        event = _notification_queue.get()
        try:
            _write_notification(event)
        except Exception:
            logger.exception("Unexpected error while writing the notification feed")
        finally:
            _notification_queue.task_done()


def _ensure_writer_started() -> None:
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        worker = threading.Thread(
            target=_notification_writer,
            name="orbit-notification-feed",
            daemon=True,
        )
        worker.start()
        _worker_started = True


def record_notification(
    channel: str,
    content: str,
    description: Optional[str],
    fields: List[Dict[str, Any]],
) -> None:
    """Queue a Discord delivery for persistence without blocking trading."""
    event = {
        "id": uuid4().hex,
        "channel": channel,
        "content": content,
        "description": description or "",
        "fields": fields,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _notification_queue.put_nowait(event)
    except queue.Full:
        logger.warning("Notification feed queue is full; dropping mirrored event")
        return
    _ensure_writer_started()


def list_notifications(limit: int = 100) -> List[Dict[str, Any]]:
    """Return newest-first notification records from the bounded feed."""
    safe_limit = max(1, min(limit, MAX_FEED_LIMIT))
    client = create_redis_client()
    try:
        records = cast(
            List[str], client.lrange(NOTIFICATION_FEED_KEY, 0, safe_limit - 1)
        )
    finally:
        client.close()
    notifications: List[Dict[str, Any]] = []
    for record in records:
        try:
            value = json.loads(record)
        except (TypeError, json.JSONDecodeError):
            logger.warning("Skipping malformed notification feed record")
            continue
        if isinstance(value, dict):
            notifications.append(value)
    return notifications
