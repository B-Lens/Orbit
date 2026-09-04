from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(
    title="Orbit API",
    description="API for the Orbit Trading System",
    version="1.0.0"
)

# Configure CORS for the UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify the UI's URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class StatusResponse(BaseModel):
    status: str
    version: str

@app.get("/api/status", response_model=StatusResponse)
async def get_status():
    return StatusResponse(status="online", version="1.0.0")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("orbit.api:app", host="0.0.0.0", port=8000, reload=True)
