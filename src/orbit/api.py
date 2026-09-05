import os
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(
    title="Orbit API",
    description="API for the Orbit Trading System",
    version="1.0.0"
)

import redis
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware

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

@app.get("/api/status", response_model=StatusResponse)
def get_status():
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", 6379))
    db = int(os.environ.get("REDIS_DB", 0))
    try:
        r = redis.Redis(
            host=host, 
            port=port, 
            db=db, 
            socket_connect_timeout=1,
            socket_timeout=1
        )
        r.ping()
        return StatusResponse(status="online", version="1.0.0")
    except (redis.ConnectionError, redis.TimeoutError):
        raise HTTPException(status_code=503, detail="Service Unavailable: Redis disconnected or timed out")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("orbit.api:app", host="0.0.0.0", port=8000, reload=True)
