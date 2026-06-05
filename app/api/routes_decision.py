import json
from datetime import datetime, timezone
from typing import List, Literal, Optional
from fastapi import APIRouter, Query, HTTPException, Body
from pydantic import BaseModel, Field

from app.schemas.analysis import MarketStateAnalysisResponse
from app.schemas.decision import TradeDecision
from app.services.market_data_service import get_multi_timeframe_ohlcv
from app.services.analysis_service import analyze_market_state
from app.services.decision_service import evaluate_trade_decision
from app.services.trade_manager import trade_manager
from app.core.config import settings
from app.core.logging import logger

router = APIRouter()

class ConfirmTradeRequest(BaseModel):
    symbol: str = Field(..., example="BTC/USDT")
    direction: Literal["LONG", "SHORT"] = Field(...)
    entry_price: float = Field(...)
    stop_loss: float = Field(...)
    take_profit_1: float = Field(...)
    take_profit_2: float = Field(...)

class CloseTradeRequest(BaseModel):
    symbol: str = Field(..., example="BTC/USDT")

@router.get(
    "/evaluate",
    response_model=TradeDecision,
    summary="Evaluate a trade decision from live market data",
    description=(
        "Fetches fresh multi-timeframe OHLCV data, runs full structural analysis, "
        "then evaluates a trade decision. Handles auto-execution or manual confirmation status, "
        "and checks for existing active trade records."
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

        # 1. Check Redis for any active trade on this symbol
        r_client = await trade_manager.get_redis()
        active_key = f"active_trade:{symbol}"
        active_trade_str = await r_client.get(active_key)
        
        # 2. Fetch multi-timeframe OHLCV & run analysis
        timeframe_data = await get_multi_timeframe_ohlcv(symbol, timeframes, limit)
        analysis = await analyze_market_state(symbol, timeframe_data)

        # Derive current price
        primary_tf = settings.PRIMARY_TIMEFRAME
        primary_candles = timeframe_data.get(primary_tf) or timeframe_data.get(next(iter(timeframe_data), ""))
        if not primary_candles:
            raise HTTPException(status_code=502, detail="No candle data returned for any timeframe.")
        current_price = float(primary_candles[-1].close)

        # Evaluate decision
        decision = evaluate_trade_decision(analysis, current_price)

        # 3. Handle active/executed overrides
        if active_trade_str:
            active_state = json.loads(active_trade_str)
            decision.execution_status = "AUTO_EXECUTED" if active_state.get("direction") == decision.direction else "MANUALLY_EXECUTED"
            decision.trade_id = active_state.get("trade_id")
            decision.executed_at = active_state.get("executed_at")
            # Override current risk profile to reflect open trade levels
            if decision.risk_profile:
                decision.risk_profile.entry_price = active_state.get("entry_price", current_price)
                decision.risk_profile.stop_loss = active_state.get("stop_loss", decision.risk_profile.stop_loss)
                decision.risk_profile.take_profit_1 = active_state.get("take_profit_1", decision.risk_profile.take_profit_1)
                decision.risk_profile.take_profit_2 = active_state.get("take_profit_2", decision.risk_profile.take_profit_2)
            return decision

        # 4. Handle new signal execution gates
        if decision.decision in ["APPROVE_LONG", "APPROVE_SHORT"]:
            if not decision.requires_manual_confirmation and decision.confidence_score >= settings.AUTO_EXECUTE_CONFIDENCE:
                # Auto Execution trigger
                analysis_dict = analysis.model_dump() if hasattr(analysis, "model_dump") else analysis.dict()
                decision_dict = decision.model_dump() if hasattr(decision, "model_dump") else decision.dict()
                
                # Make sure timestamp is string serialized
                decision_dict["timestamp"] = decision_dict["timestamp"].isoformat() if isinstance(decision_dict["timestamp"], datetime) else str(decision_dict["timestamp"])

                exec_res = await trade_manager.execute_trade(
                    symbol=symbol,
                    direction=decision.direction,
                    entry=current_price,
                    stop_loss=decision.risk_profile.stop_loss,
                    take_profit_1=decision.risk_profile.take_profit_1,
                    take_profit_2=decision.risk_profile.take_profit_2,
                    analysis_snapshot=analysis_dict,
                    decision_snapshot=decision_dict
                )
                if exec_res.get("status") == "success":
                    decision.execution_status = "AUTO_EXECUTED"
                    decision.trade_id = exec_res["trade_id"]
                    decision.executed_at = datetime.now(timezone.utc).isoformat()
            else:
                decision.execution_status = "AWAITING_CONFIRMATION"
        else:
            decision.execution_status = "IDLE"

        return decision

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Decision evaluation failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Decision pipeline failed: {str(e)}")


@router.post(
    "/confirm",
    summary="Manually confirm and execute a pending trade",
)
async def confirm_trade(request: ConfirmTradeRequest = Body(...)):
    try:
        logger.info(f"Manual confirmation received for {request.symbol} ({request.direction})")
        
        # 1. Fetch multi-timeframe OHLCV & run analysis to create snapshots
        timeframe_data = await get_multi_timeframe_ohlcv(request.symbol, ["1m", "5m", "15m", "1h", "4h"], 100)
        analysis = await analyze_market_state(request.symbol, timeframe_data)
        decision = evaluate_trade_decision(analysis, request.entry_price)
        
        analysis_dict = analysis.model_dump() if hasattr(analysis, "model_dump") else analysis.dict()
        decision_dict = decision.model_dump() if hasattr(decision, "model_dump") else decision.dict()
        decision_dict["timestamp"] = decision_dict["timestamp"].isoformat() if isinstance(decision_dict["timestamp"], datetime) else str(decision_dict["timestamp"])

        # 2. Execute order
        exec_res = await trade_manager.execute_trade(
            symbol=request.symbol,
            direction=request.direction,
            entry=request.entry_price,
            stop_loss=request.stop_loss,
            take_profit_1=request.take_profit_1,
            take_profit_2=request.take_profit_2,
            analysis_snapshot=analysis_dict,
            decision_snapshot=decision_dict
        )
        
        if exec_res.get("status") == "error":
            raise HTTPException(status_code=400, detail=exec_res.get("message"))
            
        return exec_res
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Manual trade confirmation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Execution failed: {str(e)}")


@router.post(
    "/close",
    summary="Manually/Emergency close an active trade position",
)
async def close_trade(request: CloseTradeRequest = Body(...)):
    try:
        res = await trade_manager.manual_close_position(request.symbol)
        if res.get("status") == "error":
            raise HTTPException(status_code=400, detail=res.get("message"))
        return res
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Manual position close failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Close operation failed: {str(e)}")
