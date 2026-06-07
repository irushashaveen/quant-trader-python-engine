from fastapi import APIRouter
from datetime import datetime
from pydantic import BaseModel
from app.core.config import settings
from app.core.logging import logger

router = APIRouter()

start_time = datetime.now()

class ModeUpdateRequest(BaseModel):
    mode: str  # "AUTO_EXECUTE", "FORCE_MANUAL", "TESTNET"

@router.get("/health")
def get_health():
    uptime = datetime.now() - start_time
    return {
        "status": "healthy",
        "version": "0.1.0",
        "uptime_seconds": int(uptime.total_seconds()),
        "timestamp": datetime.now().isoformat()
    }

@router.get("/api/v1/config/mode")
def get_mode():
    if settings.BINANCE_USE_TESTNET:
        return {"mode": "TESTNET"}
    return {"mode": settings.EXECUTION_MODE}

@router.post("/api/v1/config/mode")
async def update_mode(request: ModeUpdateRequest):
    mode = request.mode
    if mode not in ["AUTO_EXECUTE", "FORCE_MANUAL", "TESTNET"]:
        return {"status": "error", "message": "Invalid mode"}

    from app.services.exchange_service import exchange_service
    from app.services.data_loader import data_loader

    if mode == "TESTNET":
        settings.BINANCE_USE_TESTNET = True
    else:
        settings.BINANCE_USE_TESTNET = False
        settings.EXECUTION_MODE = mode

    # Dynamic recycling of exchange and websocket streams
    try:
        await exchange_service.close()
        await data_loader.stop_streams()
        
        # Re-start streams in sandbox/production mode depending on toggled settings
        default_symbols = ["BTC/USDT"]
        await data_loader.start_streams(default_symbols)
        logger.info(f"Engine connections successfully recycled for mode: {mode}")
    except Exception as e:
        logger.error(f"Error during engine mode connection recycle: {e}")
        return {"status": "error", "message": f"Recycling failed: {str(e)}"}

    return {"status": "success", "mode": mode}
