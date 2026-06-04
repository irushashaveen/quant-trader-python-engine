from fastapi import APIRouter, Query, HTTPException
from typing import List
from app.schemas.analysis import MarketStateAnalysisResponse
from app.services.market_data_service import get_multi_timeframe_ohlcv
from app.services.analysis_service import analyze_market_state
from app.core.logging import logger

router = APIRouter()

@router.get("/market-state", response_model=MarketStateAnalysisResponse)
async def get_market_state_analysis(
    symbol: str = Query("BTC/USDT", description="Trading pair symbol (e.g. BTC/USDT)"),
    limit: int = Query(100, ge=10, le=1000, description="Number of historical candles to fetch and analyze"),
    timeframes: List[str] = Query(
        ["1m", "5m", "15m", "1h", "4h"],
        description="Timeframes to include in multi-timeframe analysis"
    )
):
    try:
        logger.info(f"API Request: market state analysis for {symbol} (limit: {limit}) on timeframes: {timeframes}")
        
        # 1. Fetch cleaned multi-timeframe ohlcv data (already lookahead-bias safe)
        timeframe_data = await get_multi_timeframe_ohlcv(symbol, timeframes, limit)
        
        # 2. Perform market structure & liquidity analysis
        analysis_report = await analyze_market_state(symbol, timeframe_data)
        
        return analysis_report
    except Exception as e:
        logger.error(f"Error executing market state analysis for {symbol}: {str(e)}")
        raise HTTPException(
            status_code=502,
            detail=f"Analysis pipeline execution failed: {str(e)}"
        )
