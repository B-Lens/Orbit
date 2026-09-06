"""Live operational state used by the Orbit command-center UI.

This module deliberately does not read from the Discord notification mirror.
Redis holds short-lived runtime/observability state, while signal decisions remain
in MongoDB's immutable decision ledger.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis

from orbit.core.redis_manager import (
    REDIS_KEY_MARKET_SENTIMENT,
    REDIS_KEY_SENTIMENT_LAST_RUN_SLOT,
    REDIS_KEY_SENTIMENT_RUN_SLOT_LEASE,
    REDIS_KEY_PENDING_SENTIMENT,
    REDIS_KEY_PENDING_SENTIMENT_COUNT,
    TRADE_KEY_PREFIX,
    runtime_heartbeat_key,
)

REDIS_KEY_RUNTIME_ACTIVITY_PREFIX = "orbit:runtime:activity"
REDIS_KEY_SENTIMENT_SNAPSHOT = "orbit:market:sentiment_snapshot"
REDIS_KEY_COMMAND_CENTER_LOGS = "orbit:observability:logs"
REDIS_KEY_COMMAND_CENTER_EXCEPTIONS = "orbit:observability:exceptions"

RUNTIME_ACTIVITY_TTL = 3_600
OBSERVABILITY_MAX_RECORDS = 500


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_activity_key(runtime_id: str) -> str:
    """Return the current-activity key for one runtime instance."""
    return f"{REDIS_KEY_RUNTIME_ACTIVITY_PREFIX}:{runtime_id}"


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _decode(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _load_json(raw: Any) -> Optional[Dict[str, Any]]:
    value = _decode(raw)
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def record_runtime_activity(
    client: Any,
    activity: str,
    detail: Optional[str] = None,
    runtime_id: Optional[str] = None,
) -> None:
    """Publish the runtime's current task as expiring operational state."""
    payload = {
        "activity": activity,
        "detail": detail,
        "updated_at": _utc_now(),
    }
    resolved_runtime_id = runtime_id or os.getenv("ORBIT_RUNTIME_ID") or "default"
    try:
        client.setex(
            runtime_activity_key(resolved_runtime_id),
            RUNTIME_ACTIVITY_TTL,
            json.dumps(payload, default=_json_value),
        )
    except Exception:
        pass


def record_sentiment_snapshot(client: Any, result: Dict[str, Any]) -> None:
    """Store details for the latest successful sentiment analysis."""
    payload = {
        "effective": result.get("effective_sentiment"),
        "observed": result.get("observed_sentiment") or result.get("sentiment"),
        "confidence": result.get("confidence"),
        "provider": result.get("provider"),
        "explanation": result.get("explanation"),
        "action": result.get("signal_action"),
        "confirmation_count": result.get("confirmation_count"),
        "updated_at": _utc_now(),
    }
    try:
        client.set(
            REDIS_KEY_SENTIMENT_SNAPSHOT,
            json.dumps(payload, default=_json_value),
        )
    except Exception:
        pass


def _append_record(client: Any, key: str, record: Dict[str, Any]) -> None:
    encoded = json.dumps(record, default=_json_value)
    pipeline = client.pipeline(transaction=False)
    pipeline.lpush(key, encoded)
    pipeline.ltrim(key, 0, OBSERVABILITY_MAX_RECORDS - 1)
    pipeline.execute()


def record_exception(
    client: Any,
    exception: BaseException,
    context: str,
    traceback_text: str,
) -> None:
    """Append a structured runtime exception to the bounded Redis history."""
    try:
        _append_record(
            client,
            REDIS_KEY_COMMAND_CENTER_EXCEPTIONS,
            {
                "id": str(uuid.uuid4()),
                "type": type(exception).__name__,
                "message": str(exception),
                "context": context,
                "traceback": traceback_text,
                "created_at": _utc_now(),
            },
        )
    except Exception:
        pass


class CommandCenterLogHandler(logging.Handler):
    """Write Orbit log records to a bounded Redis list for the live UI."""

    def __init__(self, client: Any) -> None:
        super().__init__()
        self.client = client

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _append_record(
                self.client,
                REDIS_KEY_COMMAND_CENTER_LOGS,
                {
                    "id": str(uuid.uuid4()),
                    "level": record.levelname,
                    "message": record.getMessage(),
                    "context": record.name,
                    "created_at": datetime.fromtimestamp(
                        record.created, tz=timezone.utc
                    ).isoformat(),
                },
            )
        except Exception:
            # Logging must never interrupt the trading runtime. Avoid logging the
            # failure here because that would recurse into this handler.
            pass


def install_command_center_log_handler(client: Any) -> None:
    """Install one Redis-backed handler on the shared Orbit logger."""
    orbit_logger = logging.getLogger("Orbit")
    for handler in orbit_logger.handlers:
        if isinstance(handler, CommandCenterLogHandler):
            handler.client = client
            return
    orbit_logger.addHandler(CommandCenterLogHandler(client))


def _list_records(client: Any, key: str, limit: int) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []
    records: List[Dict[str, Any]] = []
    for raw in client.lrange(key, 0, limit - 1):
        parsed = _load_json(raw)
        if parsed is not None:
            records.append(parsed)
    return records


def _position_from_trade(trade_id: str, trade: Dict[str, Any]) -> Dict[str, Any]:
    entry_price = trade.get("price") or trade.get("entry_price")
    current_price = trade.get("current_price")
    quantity = trade.get("quantity")
    side = str(trade.get("positionSide") or trade.get("side") or "").upper()
    unrealized_pnl: Optional[float] = None
    if entry_price is not None and current_price is not None and quantity is not None:
        try:
            direction = -1.0 if side in {"SELL", "SHORT"} else 1.0
            unrealized_pnl = (
                (float(current_price) - float(entry_price))
                * float(quantity)
                * direction
            )
        except (TypeError, ValueError):
            pass

    stop_loss = trade.get("stop_loss_price") or trade.get("stop_loss")
    take_profit = trade.get("target") or trade.get("take_profit")
    if stop_loss is not None and (trade.get("sl_order_id") or trade.get("stop_loss_order")):
        # Redis order IDs are not proof that the protective order remains open
        # at the broker. Keep the dashboard conservative until broker state is
        # available to this read-only snapshot.
        protection_status = "unverified"
    elif stop_loss is not None:
        protection_status = "pending"
    else:
        protection_status = "unprotected"

    return {
        "trade_id": str(trade.get("trade_id") or trade_id),
        "symbol": str(trade.get("symbol") or ""),
        "side": side,
        "quantity": quantity,
        "entry_price": entry_price,
        "current_price": current_price,
        "unrealized_pnl": unrealized_pnl,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "protection_status": protection_status,
        "execution_mode": trade.get("execution_mode"),
        "entered_at": trade.get("entered_at"),
        "exit_pending": bool(trade.get("exit_pending", False)),
    }


def read_runtime_state(client: Any, runtime_ids: List[str]) -> Dict[str, Any]:
    """Read health and current activity without consulting event history."""
    runtimes: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for runtime_id in runtime_ids:
        heartbeat = _decode(client.get(runtime_heartbeat_key(runtime_id)))
        activity = _load_json(client.get(runtime_activity_key(runtime_id)))
        state = {
            "runtime_id": runtime_id,
            "status": "online" if heartbeat else "offline",
            "heartbeat_at": heartbeat,
        }
        runtimes.append(state)
        if current is None and activity is not None:
            current = activity

    return {
        "status": "online" if runtimes and all(item["status"] == "online" for item in runtimes) else "degraded",
        "current_activity": current.get("activity") if current else None,
        "detail": current.get("detail") if current else None,
        "updated_at": current.get("updated_at") if current else None,
        "runtimes": runtimes,
    }


def read_positions(client: Any) -> List[Dict[str, Any]]:
    """Read all active trade records from Redis."""
    positions: List[Dict[str, Any]] = []
    for key in client.scan_iter(f"{TRADE_KEY_PREFIX}*"):
        raw_key = _decode(key) or ""
        trade = _load_json(client.get(key))
        if trade is None:
            continue
        positions.append(
            _position_from_trade(raw_key[len(TRADE_KEY_PREFIX):], trade)
        )
    return sorted(positions, key=lambda item: (item["symbol"], item["trade_id"]))


def read_sentiment(client: Any) -> Dict[str, Any]:
    """Read effective and latest detailed sentiment state."""
    snapshot = _load_json(client.get(REDIS_KEY_SENTIMENT_SNAPSHOT)) or {}
    snapshot["effective"] = _decode(client.get(REDIS_KEY_MARKET_SENTIMENT))
    snapshot["pending"] = _decode(client.get(REDIS_KEY_PENDING_SENTIMENT))
    pending_count = _decode(client.get(REDIS_KEY_PENDING_SENTIMENT_COUNT))
    try:
        snapshot["confirmation_count"] = int(pending_count) if pending_count else snapshot.get("confirmation_count")
    except ValueError:
        pass
    snapshot["last_completed_slot"] = _decode(client.get(REDIS_KEY_SENTIMENT_LAST_RUN_SLOT))
    snapshot["run_in_progress"] = bool(client.get(REDIS_KEY_SENTIMENT_RUN_SLOT_LEASE))
    return snapshot


def read_observability(
    client: Any, log_limit: int, exception_limit: int
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return bounded structured logs and exceptions, newest first."""
    return (
        _list_records(client, REDIS_KEY_COMMAND_CENTER_LOGS, log_limit),
        _list_records(client, REDIS_KEY_COMMAND_CENTER_EXCEPTIONS, exception_limit),
    )


def create_redis_client() -> Any:
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
