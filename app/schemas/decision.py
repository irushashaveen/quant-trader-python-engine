from pydantic import BaseModel, Field
from typing import Dict, List, Literal, Optional
from datetime import datetime


class RiskProfile(BaseModel):
    entry_price: float = Field(..., description="Proposed entry price")
    stop_loss: float = Field(..., description="Stop loss price level")
    stop_loss_source: Literal["SWING", "ORDER_BLOCK"] = Field(
        ..., description="Whether the SL was placed at a swing level or an Order Block boundary"
    )
    take_profit_1: float = Field(..., description="TP1: structure-based target (FVG midpoint or OB boundary)")
    take_profit_1_source: Literal["FVG", "ORDER_BLOCK", "FIXED_2R"] = Field(
        ..., description="Source used for TP1 calculation"
    )
    take_profit_2: float = Field(..., description="TP2: fixed 3R from entry, always present")
    risk_reward_ratio: float = Field(..., description="R:R ratio to TP1 (must meet minimum threshold)")
    risk_pct: float = Field(1.0, description="Placeholder risk percentage of account (configurable in Phase 6)")


class TradeDecision(BaseModel):
    symbol: str = Field(..., json_schema_extra={"example": "BTC/USDT"})
    decision: Literal[
        "APPROVE_LONG",
        "APPROVE_SHORT",
        "WAIT",
        "REJECT_LOW_CONFIDENCE",
        "REJECT_HIGH_RISK",
    ] = Field(..., description="Final trade decision output")
    direction: Literal["LONG", "SHORT", "NONE"] = Field(
        ..., description="Intended trade direction, or NONE if not approved"
    )
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0, description="Normalized confidence score [0.0, 1.0]"
    )
    aggregate_bias_score: float = Field(
        ..., description="Multi-timeframe weighted bias score [-1.0, 1.0], passed through from analysis"
    )
    reason: str = Field(..., description="Human-readable explanation of the decision")
    risk_profile: Optional[RiskProfile] = Field(
        None, description="Risk parameters — only populated when a trade is approved"
    )
    no_trade_conditions: List[str] = Field(
        default_factory=list,
        description="List of triggered no-trade guard conditions (may be non-empty even for APPROVE when overridden by score)"
    )
    requires_manual_confirmation: bool = Field(
        ...,
        description=(
            "True if confidence is in the 0.55–0.70 range. "
            "Informational in Phase 5; dashboard blocking comes in Phase 6."
        )
    )
    timeframe_votes: Dict[str, Literal["LONG", "SHORT", "NEUTRAL"]] = Field(
        ..., description="Per-timeframe directional vote based on bias_score"
    )
    timestamp: datetime = Field(..., description="UTC timestamp of when the decision was evaluated")

    # --- Phase 7: Order Execution details ---
    execution_status: Optional[Literal["IDLE", "PENDING", "AWAITING_CONFIRMATION", "AUTO_EXECUTED", "MANUALLY_EXECUTED"]] = Field(
        None, description="Actual execution status from the execution bridge"
    )
    executed_at: Optional[str] = Field(None, description="Timestamp of trade execution")
    trade_id: Optional[str] = Field(None, description="Supabase trade record UUID")

