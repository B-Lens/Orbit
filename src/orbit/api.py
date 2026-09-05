from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(
    title="Orbit API",
    description="API for the Orbit Trading System",
    version="1.0.0"
)

import redis
from fastapi import HTTPException

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
async def get_status():
    try:
        r = redis.Redis(host='localhost', port=6379, socket_connect_timeout=1)
        r.ping()
        return StatusResponse(status="online", version="1.0.0")
    except redis.ConnectionError:
        raise HTTPException(status_code=503, detail="Service Unavailable: Redis disconnected")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("orbit.api:app", host="0.0.0.0", port=8000, reload=True)
