import os
from contextlib import closing
from typing import Any, Dict, List

import redis
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from orbit.core.redis_manager import runtime_heartbeat_key
from orbit.core.notification_feed import list_notifications

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


@app.get("/api/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    runtime_ids = os.environ.get(
        "ORBIT_EXPECTED_RUNTIME_IDS", os.environ.get("ORBIT_RUNTIME_ID", "default")
    )
    expected_runtime_ids = [
        item.strip() for item in runtime_ids.split(",") if item.strip()
    ] or ["default"]
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
        notifications = [
            NotificationResponse.model_validate(item)
            for item in list_notifications(limit)
        ]
        return NotificationFeedResponse(notifications=notifications)
    except redis.RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Service Unavailable: notification feed unavailable",
        ) from exc


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("orbit.api:app", host="0.0.0.0", port=8000, reload=True)
