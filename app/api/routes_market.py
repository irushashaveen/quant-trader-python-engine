from fastapi import APIRouter, Query, HTTPException
from typing import List
from app.schemas.market import MultiTimeframeOHLCVResponse
from app.services.market_data_service import get_multi_timeframe_ohlcv
from app.core.logging import logger

router = APIRouter()

@router.get("/ohlcv", response_model=MultiTimeframeOHLCVResponse)
async def get_ohlcv(
    symbol: str = Query("BTC/USDT", description="Trading pair symbol (e.g. BTC/USDT)"),
    limit: int = Query(100, ge=1, le=1000, description="Number of historical candles to fetch"),
    timeframes: List[str] = Query(
        ["1m", "5m", "15m", "1h", "4h"],
        description="List of timeframes to fetch"
    )
):
    try:
        logger.info(f"API Request: fetch OHLCV for {symbol} (limit: {limit}) on timeframes: {timeframes}")
        data = await get_multi_timeframe_ohlcv(symbol, timeframes, limit)
        return MultiTimeframeOHLCVResponse(
            symbol=symbol,
            limit=limit,
            data=data
        )
    except Exception as e:
        logger.error(f"Error handling /ohlcv request: {str(e)}")
        raise HTTPException(
            status_code=502,
            detail=f"Error fetching data from exchange: {str(e)}"
        )
