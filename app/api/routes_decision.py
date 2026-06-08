import json
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import logger
from app.schemas.analysis import MarketStateAnalysisResponse
from app.schemas.decision import TradeDecision
from app.services.core_math_engine import calculate_atr, get_primary_timeframe
from app.services.decision_service import evaluate_trade_decision
from app.services.market_data_service import (
    fetch_order_flow_data,
    get_multi_timeframe_ohlcv,
)
from app.services.order_flow_engine import evaluate_order_flow
from app.services.smc_engine import run_smc_analysis
from app.services.trade_manager import trade_manager

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
        "then evaluates a trade decision. Handles auto-execution or manual confirmation "
        "status, and checks for existing active trade records. "
        "Leverage is determined dynamically from ATR volatility."
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

        # 2. Fetch multi-timeframe OHLCV & run SMC analysis
        timeframe_data = await get_multi_timeframe_ohlcv(symbol, timeframes, limit)
        analysis = await run_smc_analysis(symbol, timeframe_data)

        # 3. Derive primary timeframe and current price dynamically
        primary_tf = get_primary_timeframe(timeframes)
        primary_candles = timeframe_data.get(primary_tf) or timeframe_data.get(
            next(iter(timeframe_data), "")
        )
        if not primary_candles:
            raise HTTPException(
                status_code=502,
                detail="No candle data returned for any timeframe.",
            )
        current_price = float(primary_candles[-1].close)

        # 4. Calculate ATR from primary-TF candles (volatility for dynamic leverage)
        atr_value = calculate_atr(primary_candles)

        # 5. Fetch and evaluate order flow filter data
        order_flow_data = await fetch_order_flow_data(symbol)
        
        # Real-time WebSocket firehose data
        from app.services.data_loader import data_loader
        cvd = data_loader.memory_manager.get_normalized_cvd(symbol)
        ob_imbalance = data_loader.memory_manager.get_order_book_imbalance(symbol)
        liq_stats = data_loader.memory_manager.get_recent_liquidations_stats(symbol)
        
        order_flow_result = evaluate_order_flow(
            order_flow_data,
            cvd=cvd,
            ob_imbalance=ob_imbalance,
            liquidations_stats=liq_stats
        )

        # 6. Evaluate decision
        decision = await evaluate_trade_decision(analysis, current_price, order_flow_result)

        # 7. Handle active/executed overrides (unchanged DB logic)
        if active_trade_str:
            active_state = json.loads(active_trade_str)
            decision.execution_status = (
                "AUTO_EXECUTED"
                if active_state.get("direction") == decision.direction
                else "MANUALLY_EXECUTED"
            )
            decision.trade_id = active_state.get("trade_id")
            decision.executed_at = active_state.get("executed_at")
            if decision.risk_profile:
                decision.risk_profile.entry_price = active_state.get(
                    "entry_price", current_price
                )
                decision.risk_profile.stop_loss = active_state.get(
                    "stop_loss", decision.risk_profile.stop_loss
                )
                decision.risk_profile.take_profit_1 = active_state.get(
                    "take_profit_1", decision.risk_profile.take_profit_1
                )
                decision.risk_profile.take_profit_2 = active_state.get(
                    "take_profit_2", decision.risk_profile.take_profit_2
                )
            return decision

        # 8. Handle new signal execution gates
        if decision.decision in ["APPROVE_LONG", "APPROVE_SHORT"]:
            if (
                not decision.requires_manual_confirmation
                and decision.confidence_score >= settings.AUTO_EXECUTE_CONFIDENCE
            ):
                # Auto-execution — pass ATR for dynamic leverage/sizing
                analysis_dict = (
                    analysis.model_dump()
                    if hasattr(analysis, "model_dump")
                    else analysis.dict()
                )
                decision_dict = (
                    decision.model_dump()
                    if hasattr(decision, "model_dump")
                    else decision.dict()
                )
                decision_dict["timestamp"] = (
                    decision_dict["timestamp"].isoformat()
                    if isinstance(decision_dict["timestamp"], datetime)
                    else str(decision_dict["timestamp"])
                )

                exec_res = await trade_manager.execute_trade(
                    symbol=symbol,
                    direction=decision.direction,
                    entry=current_price,
                    stop_loss=decision.risk_profile.stop_loss,
                    take_profit_1=decision.risk_profile.take_profit_1,
                    take_profit_2=decision.risk_profile.take_profit_2,
                    atr_value=atr_value,           # ← dynamic leverage
                    analysis_snapshot=analysis_dict,
                    decision_snapshot=decision_dict,
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
    except Exception as exc:
        logger.error(f"Decision evaluation failed for {symbol}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Decision pipeline failed: {str(exc)}",
        )


@router.post(
    "/confirm",
    summary="Manually confirm and execute a pending trade",
)
async def confirm_trade(request: ConfirmTradeRequest = Body(...)):
    try:
        logger.info(
            f"Manual confirmation received for {request.symbol} ({request.direction})"
        )
        timeframes = ["1m", "5m", "15m", "1h", "4h"]

        # Fetch data & analysis
        timeframe_data = await get_multi_timeframe_ohlcv(request.symbol, timeframes, 100)
        analysis = await run_smc_analysis(request.symbol, timeframe_data)

        # ATR from primary TF
        primary_tf = get_primary_timeframe(timeframes)
        primary_candles = timeframe_data.get(primary_tf, [])
        atr_value = calculate_atr(primary_candles) if primary_candles else None

        # Order flow
        order_flow_data = await fetch_order_flow_data(request.symbol)
        
        # Real-time WebSocket firehose data
        from app.services.data_loader import data_loader
        cvd = data_loader.memory_manager.get_normalized_cvd(request.symbol)
        ob_imbalance = data_loader.memory_manager.get_order_book_imbalance(request.symbol)
        liq_stats = data_loader.memory_manager.get_recent_liquidations_stats(request.symbol)
        
        order_flow_result = evaluate_order_flow(
            order_flow_data,
            cvd=cvd,
            ob_imbalance=ob_imbalance,
            liquidations_stats=liq_stats
        )

        decision = await evaluate_trade_decision(analysis, request.entry_price, order_flow_result)

        analysis_dict = (
            analysis.model_dump() if hasattr(analysis, "model_dump") else analysis.dict()
        )
        decision_dict = (
            decision.model_dump() if hasattr(decision, "model_dump") else decision.dict()
        )
        decision_dict["timestamp"] = (
            decision_dict["timestamp"].isoformat()
            if isinstance(decision_dict["timestamp"], datetime)
            else str(decision_dict["timestamp"])
        )

        exec_res = await trade_manager.execute_trade(
            symbol=request.symbol,
            direction=request.direction,
            entry=request.entry_price,
            stop_loss=request.stop_loss,
            take_profit_1=request.take_profit_1,
            take_profit_2=request.take_profit_2,
            atr_value=atr_value,           # ← dynamic leverage
            analysis_snapshot=analysis_dict,
            decision_snapshot=decision_dict,
        )

        if exec_res.get("status") == "error":
            raise HTTPException(status_code=400, detail=exec_res.get("message"))

        return exec_res

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Manual trade confirmation failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Execution failed: {str(exc)}",
        )


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
    except Exception as exc:
        logger.error(f"Manual position close failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Close operation failed: {str(exc)}",
        )
