from fastapi import APIRouter
from datetime import datetime

router = APIRouter()

start_time = datetime.now()

@router.get("/health")
def get_health():
    uptime = datetime.now() - start_time
    return {
        "status": "healthy",
        "version": "0.1.0",
        "uptime_seconds": int(uptime.total_seconds()),
        "timestamp": datetime.now().isoformat()
    }
