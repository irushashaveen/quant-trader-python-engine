from fastapi import APIRouter, Query, HTTPException, Body
from typing import List

from app.schemas.analysis import MarketStateAnalysisResponse
from app.schemas.decision import TradeDecision
from app.services.market_data_service import get_multi_timeframe_ohlcv
from app.services.analysis_service import analyze_market_state
from app.services.decision_service import evaluate_trade_decision
from app.core.config import settings
from app.core.logging import logger

router = APIRouter()


@router.get(
    "/evaluate",
    response_model=TradeDecision,
    summary="Evaluate a trade decision from live market data",
    description=(
        "Fetches fresh multi-timeframe OHLCV data, runs full structural analysis, "
        "then evaluates a trade decision via the 8-step scoring pipeline. "
        "Returns one of: APPROVE_LONG, APPROVE_SHORT, WAIT, REJECT_LOW_CONFIDENCE, REJECT_HIGH_RISK."
    ),
)
async def evaluate_decision_live(
    symbol: str = Query("BTC/USDT", description="Trading pair symbol (e.g. BTC/USDT)"),
    limit: int = Query(100, ge=10, le=1000, description="Number of historical candles per timeframe"),
    timeframes: List[str] = Query(
        ["1m", "5m", "15m", "1h", "4h"],
        description="Timeframes to include in multi-timeframe analysis",
    ),
):
    try:
        logger.info(f"Decision evaluate (live): {symbol}, limit={limit}, TFs={timeframes}")

        # 1. Fetch multi-timeframe OHLCV
        timeframe_data = await get_multi_timeframe_ohlcv(symbol, timeframes, limit)

        # 2. Full structural analysis
        analysis = await analyze_market_state(symbol, timeframe_data)

        # 3. Derive current price from the primary timeframe's last close
        primary_tf = settings.PRIMARY_TIMEFRAME
        primary_candles = timeframe_data.get(primary_tf) or timeframe_data.get(next(iter(timeframe_data), ""))
        if not primary_candles:
            raise HTTPException(status_code=502, detail="No candle data returned for any timeframe.")

        current_price = float(primary_candles[-1].close)

        # 4. Evaluate decision
        decision = evaluate_trade_decision(analysis, current_price)

        return decision

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Decision evaluation failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Decision pipeline failed: {str(e)}")


@router.post(
    "/evaluate",
    response_model=TradeDecision,
    summary="Evaluate a trade decision from pre-computed analysis",
    description=(
        "Accepts an already-computed MarketStateAnalysisResponse and current_price. "
        "Skips data fetching and analysis, running only the decision scoring pipeline. "
        "Useful for n8n workflows and dashboard re-evaluation without extra round-trips."
    ),
)
async def evaluate_decision_from_analysis(
    current_price: float = Query(..., description="Current market price (last close)"),
    analysis: MarketStateAnalysisResponse = Body(...),
):
    try:
        logger.info(f"Decision evaluate (from analysis): {analysis.symbol} @ {current_price}")
        decision = evaluate_trade_decision(analysis, current_price)
        return decision
    except Exception as e:
        logger.error(f"Decision evaluation (from analysis) failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Decision scoring failed: {str(e)}")
