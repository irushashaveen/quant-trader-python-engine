from fastapi import APIRouter, HTTPException
from app.schemas.signal import TradeSignal
from app.services.idempotency_service import is_duplicate_signal
from app.core.logging import logger

router = APIRouter()

@router.post("/signal")
async def receive_signal(signal: TradeSignal):
    logger.info(f"Signal received: {signal.symbol} {signal.direction}")

    duplicate = await is_duplicate_signal(
        signal.symbol, signal.direction, str(signal.timestamp)
    )
    if duplicate:
        logger.warning(f"Duplicate signal blocked: {signal.symbol} {signal.direction}")
        raise HTTPException(status_code=409, detail="Duplicate signal rejected")

    logger.info(f"Signal accepted: {signal.symbol} — processing next")
    return {"status": "accepted", "symbol": signal.symbol, "direction": signal.direction}
