from datetime import datetime, timezone

import json
from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.core.logging import logger
from app.schemas.signal import TradeSignal
from app.services.core_math_engine import calculate_atr, get_primary_timeframe
from app.services.decision_service import evaluate_trade_decision
from app.services.idempotency_service import is_duplicate_signal
from app.services.market_data_service import (
    fetch_order_flow_data,
    get_multi_timeframe_ohlcv,
)
from app.services.order_flow_engine import evaluate_order_flow
from app.services.smc_engine import run_smc_analysis
from app.services.trade_manager import trade_manager

router = APIRouter()


@router.post("/signal")
async def receive_signal(signal: TradeSignal):
    logger.info(
        f"Signal received: {signal.symbol} {signal.direction} from {signal.signal_source}"
    )

    # 1. Idempotency duplicate signal check
    duplicate = await is_duplicate_signal(
        signal.symbol, signal.direction, str(signal.timestamp)
    )
    if duplicate:
        logger.warning(f"Duplicate signal blocked: {signal.symbol} {signal.direction}")
        raise HTTPException(status_code=409, detail="Duplicate signal rejected")

    # 2. Fetch multi-timeframe OHLCV
    timeframes = ["1m", "5m", "15m", "1h", "4h"]
    limit = 100
    try:
        timeframe_data = await get_multi_timeframe_ohlcv(signal.symbol, timeframes, limit)
    except Exception as exc:
        logger.error(f"Error fetching market data for signal {signal.symbol}: {exc}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch market data: {str(exc)}",
        )

    # 3. Derive primary timeframe dynamically and get current price
    primary_tf = get_primary_timeframe(timeframes)
    primary_candles = timeframe_data.get(primary_tf)
    if not primary_candles:
        logger.error(
            f"No candle data returned for primary timeframe {primary_tf}"
        )
        raise HTTPException(
            status_code=502,
            detail=f"No candle data returned for primary timeframe {primary_tf}",
        )
    current_price = float(primary_candles[-1].close)

    # 4. Calculate ATR for dynamic leverage
    atr_value = calculate_atr(primary_candles)

    # 5. Run SMC Analysis
    try:
        analysis = await run_smc_analysis(signal.symbol, timeframe_data)
    except Exception as exc:
        logger.error(f"SMC analysis failed for signal {signal.symbol}: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"SMC analysis failed: {str(exc)}",
        )

    # 6. Order flow metrics + evaluation
    order_flow_data = await fetch_order_flow_data(signal.symbol)
    
    # Real-time WebSocket firehose data
    from app.services.data_loader import data_loader
    cvd = data_loader.memory_manager.get_normalized_cvd(signal.symbol)
    ob_imbalance = data_loader.memory_manager.get_order_book_imbalance(signal.symbol)
    liq_stats = data_loader.memory_manager.get_recent_liquidations_stats(signal.symbol)
    
    order_flow_result = evaluate_order_flow(
        order_flow_data,
        cvd=cvd,
        ob_imbalance=ob_imbalance,
        liquidations_stats=liq_stats
    )

    # 7. Decision pipeline
    decision = evaluate_trade_decision(analysis, current_price, order_flow_result)

    decision_dict = (
        decision.model_dump() if hasattr(decision, "model_dump") else decision.dict()
    )
    decision_dict["timestamp"] = (
        decision_dict["timestamp"].isoformat()
        if isinstance(decision_dict["timestamp"], datetime)
        else str(decision_dict["timestamp"])
    )

    # 8. Handle routing based on decision outcome
    if decision.decision in ["APPROVE_LONG", "APPROVE_SHORT"]:
        # FORCE_MANUAL override
        if settings.EXECUTION_MODE == "FORCE_MANUAL":
            logger.info(
                "Signal approved but EXECUTION_MODE is FORCE_MANUAL. "
                "Awaiting manual confirmation."
            )
            decision.execution_status = "AWAITING_CONFIRMATION"
            decision_dict["execution_status"] = "AWAITING_CONFIRMATION"
            return {
                "status": "awaiting_confirmation",
                "message": "Signal approved structurally, but auto-execution is disabled (FORCE_MANUAL).",
                "decision": decision_dict,
            }

        # Manual confirmation required by low confidence
        if decision.requires_manual_confirmation:
            logger.info(
                f"Signal approved but requires manual confirmation "
                f"(confidence {decision.confidence_score} < {settings.AUTO_EXECUTE_CONFIDENCE})."
            )
            decision.execution_status = "AWAITING_CONFIRMATION"
            decision_dict["execution_status"] = "AWAITING_CONFIRMATION"
            return {
                "status": "awaiting_confirmation",
                "message": "Signal approved but falls within manual confirmation confidence zone.",
                "decision": decision_dict,
            }

        # Auto-execution — pass ATR for dynamic leverage/sizing
        analysis_dict = (
            analysis.model_dump() if hasattr(analysis, "model_dump") else analysis.dict()
        )

        exec_res = await trade_manager.execute_trade(
            symbol=signal.symbol,
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
            decision_dict["execution_status"] = "AUTO_EXECUTED"
            decision_dict["trade_id"] = exec_res["trade_id"]
            decision_dict["executed_at"] = decision.executed_at

            return {
                "status": "executed",
                "message": "Signal successfully processed and auto-executed.",
                "trade_id": exec_res["trade_id"],
                "decision": decision_dict,
            }
        else:
            decision.execution_status = "IDLE"
            decision_dict["execution_status"] = "IDLE"
            return {
                "status": "error",
                "message": f"Execution failed: {exec_res.get('message')}",
                "decision": decision_dict,
            }

    # Rejected or wait
    decision.execution_status = "IDLE"
    decision_dict["execution_status"] = "IDLE"
    return {
        "status": "rejected",
        "message": f"Signal rejected: {decision.decision} - {decision.reason}",
        "decision": decision_dict,
    }
