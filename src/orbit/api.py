import os
import logging
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional

import redis
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError

from config.config import load_config
from orbit.core.command_center import (
    create_redis_client,
    read_observability,
    read_positions,
    read_runtime_state,
    read_sentiment,
)
from orbit.core.execution import ExecutionSettings
from orbit.core.mongo_handler import MongoHandler
from orbit.core.redis_manager import runtime_heartbeat_key
from orbit.core.notification_feed import list_notifications

logger = logging.getLogger("Orbit")

app = FastAPI(
    title="Orbit API",
    description="API for the Orbit Trading System",
    version="1.0.0"
)

# Configure CORS for the UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],  # In production, specify the UI's URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class StatusResponse(BaseModel):
    status: str
    version: str


class NotificationResponse(BaseModel):
    id: str
    channel: str
    content: str
    description: str
    fields: List[Dict[str, Any]]
    created_at: str


class NotificationFeedResponse(BaseModel):
    notifications: List[NotificationResponse]


class RuntimeInstanceResponse(BaseModel):
    runtime_id: str
    status: str
    heartbeat_at: Optional[str] = None


class RuntimeStateResponse(BaseModel):
    status: str
    current_activity: Optional[str] = None
    detail: Optional[str] = None
    updated_at: Optional[str] = None
    runtimes: List[RuntimeInstanceResponse]


class PositionResponse(BaseModel):
    trade_id: str
    symbol: str
    side: str
    quantity: Optional[float] = None
    entry_price: Optional[float] = None
    current_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    protection_status: str
    execution_mode: Optional[str] = None
    entered_at: Optional[str] = None
    exit_pending: bool = False


class SignalResponse(BaseModel):
    decision_id: str
    symbol: str
    signal: Optional[str] = None
    outcome: str
    reason: Optional[str] = None
    pattern: Optional[str] = None
    sentiment: Optional[str] = None
    execution_mode: Optional[str] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    analyzed_at: Optional[str] = None
    latest_status: Optional[str] = None


class SentimentResponse(BaseModel):
    effective: Optional[str] = None
    observed: Optional[str] = None
    confidence: Optional[float] = None
    provider: Optional[str] = None
    explanation: Optional[str] = None
    action: Optional[str] = None
    pending: Optional[str] = None
    confirmation_count: Optional[int] = None
    updated_at: Optional[str] = None
    last_completed_slot: Optional[str] = None
    run_in_progress: bool = False


class RiskExecutionResponse(BaseModel):
    active_modes: List[str]
    can_submit_orders: bool
    asset_modes: Dict[str, str]
    risk_limits: Dict[str, Any]


class LogResponse(BaseModel):
    id: str
    level: str
    message: str
    context: Optional[str] = None
    created_at: str


class ExceptionResponse(BaseModel):
    id: str
    type: str
    message: str
    context: str
    traceback: str
    created_at: str


class CommandCenterResponse(BaseModel):
    generated_at: str
    runtime: RuntimeStateResponse
    positions: List[PositionResponse]
    signals: List[SignalResponse]
    sentiment: SentimentResponse
    risk_execution: RiskExecutionResponse
    logs: List[LogResponse]
    exceptions: List[ExceptionResponse]


def _expected_runtime_ids() -> List[str]:
    configured = os.environ.get(
        "ORBIT_EXPECTED_RUNTIME_IDS", os.environ.get("ORBIT_RUNTIME_ID", "default")
    )
    return [item.strip() for item in configured.split(",") if item.strip()] or [
        "default"
    ]


def _iso_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _signal_response(record: Dict[str, Any]) -> Dict[str, Any]:
    events = record.get("execution_events")
    latest_status = None
    if isinstance(events, list) and events:
        latest = events[-1]
        if isinstance(latest, dict):
            latest_status = latest.get("status")
    return {
        "decision_id": str(record.get("decision_id", "")),
        "symbol": str(record.get("symbol", "")),
        "signal": record.get("signal"),
        "outcome": str(record.get("outcome", "unknown")),
        "reason": record.get("reason"),
        "pattern": record.get("pattern"),
        "sentiment": record.get("sentiment"),
        "execution_mode": record.get("execution_mode"),
        "entry_price": record.get("entry_price"),
        "stop_loss": record.get("stop_loss"),
        "take_profit": record.get("take_profit"),
        "analyzed_at": _iso_value(record.get("timestamp")),
        "latest_status": latest_status,
    }


@lru_cache(maxsize=1)
def _command_center_mongo_handler() -> MongoHandler:
    return MongoHandler(read_only=True)


def _recent_signals(limit: int) -> List[Dict[str, Any]]:
    try:
        handler = _command_center_mongo_handler()
        return [
            _signal_response(record)
            for record in handler.get_recent_trade_decisions(limit)
        ]
    except Exception:
        logger.exception("Unable to read recent signal decisions")
        return []


def _risk_execution_state() -> Dict[str, Any]:
    asset_modes: Dict[str, str] = {}
    modes: List[str] = []
    can_submit_orders = False
    try:
        execution_settings = ExecutionSettings.from_config()
        asset_modes = {
            symbol: mode.value
            for symbol, mode in execution_settings.asset_modes.items()
        }
        modes = sorted(mode.value for mode in execution_settings.active_modes)
        can_submit_orders = execution_settings.can_submit_orders
    except (RuntimeError, ValueError):
        logger.exception("Execution configuration is invalid; failing closed")
    try:
        config = load_config()
    except (OSError, ValueError):
        logger.exception("Unable to read risk configuration")
        config = {}
    return {
        "active_modes": modes,
        "can_submit_orders": can_submit_orders,
        "asset_modes": asset_modes,
        "risk_limits": dict(config.get("risk_policy", {})),
    }


@app.get("/api/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    expected_runtime_ids = _expected_runtime_ids()
    redis_url = os.environ.get("REDIS_URL")
    try:
        client = (
            redis.Redis.from_url(
                redis_url, socket_connect_timeout=1, socket_timeout=1
            )
            if redis_url
            else redis.Redis(
                host=os.environ.get("REDIS_HOST", "localhost"),
                port=int(os.environ.get("REDIS_PORT", 6379)),
                db=int(os.environ.get("REDIS_DB", 0)),
                username=os.environ.get("REDIS_USERNAME"),
                password=os.environ.get("REDIS_PASSWORD"),
                ssl=os.environ.get("REDIS_SSL", "false").lower() == "true",
                socket_connect_timeout=1,
                socket_timeout=1,
            )
        )
        with closing(client):
            client.ping()
            missing = [
                runtime_id
                for runtime_id in expected_runtime_ids
                if client.get(runtime_heartbeat_key(runtime_id)) is None
            ]
        if missing:
            raise HTTPException(
                status_code=503,
                detail=f"Service Unavailable: missing runtime heartbeat(s): {', '.join(missing)}",
            )
        return StatusResponse(status="online", version="1.0.0")
    except redis.RedisError as exc:
        raise HTTPException(
            status_code=503, detail="Service Unavailable: Redis unavailable"
        ) from exc


@app.get("/api/notifications", response_model=NotificationFeedResponse)
def get_notifications(limit: int = 100) -> NotificationFeedResponse:
    """Return the same successful events most recently delivered to Discord."""
    try:
        notifications = []
        for item in list_notifications(limit):
            try:
                notifications.append(NotificationResponse.model_validate(item))
            except ValidationError:
                logger.warning("Skipping schema-invalid notification feed record")
        return NotificationFeedResponse(notifications=notifications)
    except redis.RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Service Unavailable: notification feed unavailable",
        ) from exc


@app.get("/api/command-center", response_model=CommandCenterResponse)
def get_command_center(
    signal_limit: int = 25,
    log_limit: int = 100,
    exception_limit: int = 25,
) -> CommandCenterResponse:
    """Return live trading state without using the notification event buffer."""
    signal_limit = min(max(signal_limit, 0), 100)
    log_limit = min(max(log_limit, 0), 500)
    exception_limit = min(max(exception_limit, 0), 100)
    try:
        client = create_redis_client()
        with closing(client):
            client.ping()
            runtime = read_runtime_state(client, _expected_runtime_ids())
            positions = read_positions(client)
            sentiment = read_sentiment(client)
            logs, exceptions = read_observability(
                client, log_limit, exception_limit
            )
    except redis.RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Service Unavailable: command-center state unavailable",
        ) from exc

    risk_execution = _risk_execution_state()
    for position in positions:
        if not position.get("execution_mode"):
            position["execution_mode"] = risk_execution["asset_modes"].get(
                position["symbol"]
            )

    return CommandCenterResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        runtime=RuntimeStateResponse.model_validate(runtime),
        positions=[PositionResponse.model_validate(item) for item in positions],
        signals=[
            SignalResponse.model_validate(item)
            for item in _recent_signals(signal_limit)
        ],
        sentiment=SentimentResponse.model_validate(sentiment),
        risk_execution=RiskExecutionResponse.model_validate(risk_execution),
        logs=[LogResponse.model_validate(item) for item in logs],
        exceptions=[ExceptionResponse.model_validate(item) for item in exceptions],
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("orbit.api:app", host="0.0.0.0", port=8000, reload=True)
