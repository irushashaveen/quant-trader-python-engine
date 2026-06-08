from typing import Dict, Any, Optional
from app.core.config import settings
from app.core.logging import logger

class OrderFlowResult:
    def __init__(self, action: str, open_interest: float, long_short_ratio: float, funding_rate: float, reason: str):
        self.action = action  # 'ALLOW', 'VETO_LONG', 'VETO_SHORT', 'NEUTRAL'
        self.open_interest = open_interest
        self.long_short_ratio = long_short_ratio
        self.funding_rate = funding_rate
        self.reason = reason

def evaluate_order_flow(
    order_flow_data: Dict[str, Any],
    cvd: Optional[float] = None,
    ob_imbalance: Optional[float] = None,
    liquidations_stats: Optional[Dict[str, Any]] = None,
) -> OrderFlowResult:
    """
    Evaluates order flow signals to act as an alpha filter for trades.
    Supports both traditional REST data and real-time WebSocket metrics.
    
    cvd: Normalized CVD ratio between -1.0 and +1.0
    ob_imbalance: Bid volume / (Bid volume + Ask volume) between 0.0 and 1.0
    liquidations_stats: Dict with 'count', 'total_qty', and 'total_vol'
    """
    long_short_ratio = float(order_flow_data.get('long_short_ratio', 1.0))
    open_interest = float(order_flow_data.get('open_interest', 0.0))
    funding_rate = float(order_flow_data.get('funding_rate', 0.0))
    
    logger.info(
        f"Evaluating Order Flow: L/S={long_short_ratio}, OI={open_interest}, Funding={funding_rate}, "
        f"Real-time CVD={cvd}, OB Imbalance={ob_imbalance}, Liquidations={liquidations_stats}"
    )

    # 1. Real-time CVD Veto Checks
    if cvd is not None:
        threshold = settings.ORDER_FLOW_CVD_THRESHOLD
        if cvd < -threshold:
            return OrderFlowResult(
                action="VETO_LONG",
                open_interest=open_interest,
                long_short_ratio=long_short_ratio,
                funding_rate=funding_rate,
                reason=f"Real-time CVD is heavily negative ({cvd:.2f} < -{threshold}). Vetoing long entry due to selling pressure."
            )
        elif cvd > threshold:
            return OrderFlowResult(
                action="VETO_SHORT",
                open_interest=open_interest,
                long_short_ratio=long_short_ratio,
                funding_rate=funding_rate,
                reason=f"Real-time CVD is heavily positive ({cvd:.2f} > {threshold}). Vetoing short entry due to buying pressure."
            )

    # 2. Real-time Order Book Imbalance Veto Checks
    if ob_imbalance is not None:
        long_threshold = settings.ORDER_FLOW_OB_IMBALANCE_LONG_THRESHOLD
        short_threshold = settings.ORDER_FLOW_OB_IMBALANCE_SHORT_THRESHOLD
        if ob_imbalance < long_threshold:
            return OrderFlowResult(
                action="VETO_LONG",
                open_interest=open_interest,
                long_short_ratio=long_short_ratio,
                funding_rate=funding_rate,
                reason=f"Order book imbalance is heavily skewed to asks ({ob_imbalance:.2f} < {long_threshold}). Vetoing long entry."
            )
        elif ob_imbalance > short_threshold:
            return OrderFlowResult(
                action="VETO_SHORT",
                open_interest=open_interest,
                long_short_ratio=long_short_ratio,
                funding_rate=funding_rate,
                reason=f"Order book imbalance is heavily skewed to bids ({ob_imbalance:.2f} > {short_threshold}). Vetoing short entry."
            )

    # 3. REST-based retail sentiment indicators
    # If retail is heavily long (> 2.5 ratio), whales will hunt longs -> VETO_LONG
    if long_short_ratio > 2.5:
        oi_desc = f" with open interest of {open_interest:.2f} contracts at risk" if open_interest > 0.0 else ""
        return OrderFlowResult(
            action="VETO_LONG",
            open_interest=open_interest,
            long_short_ratio=long_short_ratio,
            funding_rate=funding_rate,
            reason=f"Retail is heavily long (L/S ratio: {long_short_ratio:.2f}){oi_desc}. High probability of an institutional liquidation sweep/dump."
        )
        
    # If retail is heavily short (< 0.4 ratio), whales will hunt shorts -> VETO_SHORT
    if long_short_ratio < 0.4:
        oi_desc = f" with open interest of {open_interest:.2f} contracts at risk" if open_interest > 0.0 else ""
        return OrderFlowResult(
            action="VETO_SHORT",
            open_interest=open_interest,
            long_short_ratio=long_short_ratio,
            funding_rate=funding_rate,
            reason=f"Retail is heavily short (L/S ratio: {long_short_ratio:.2f}){oi_desc}. High probability of an institutional liquidation sweep/dump."
        )
        
    # 4. REST-based funding rate boundaries
    if funding_rate > 0.001:  # 0.1% per 8 hours
         return OrderFlowResult(
            action="VETO_LONG",
            open_interest=open_interest,
            long_short_ratio=long_short_ratio,
            funding_rate=funding_rate,
            reason=f"Funding rate extremely high ({funding_rate}). Longs are paying shorts heavily, risk of correction."
        )
         
    if funding_rate < -0.001: # -0.1% per 8 hours
         return OrderFlowResult(
            action="VETO_SHORT",
            open_interest=open_interest,
            long_short_ratio=long_short_ratio,
            funding_rate=funding_rate,
            reason=f"Funding rate extremely negative ({funding_rate}). Shorts are paying longs heavily, risk of short squeeze."
        )
        
    # 5. Default ALLOW with liquidation stats metadata
    liq_reason = ""
    if liquidations_stats is not None:
        liq_count = liquidations_stats.get("count", 0)
        liq_vol = liquidations_stats.get("total_vol", 0.0)
        if liq_count > 0:
            liq_reason = f" [Liquidation sweep: {liq_count} spikes, Vol: {liq_vol:.2f} USD]"

    return OrderFlowResult(
        action="ALLOW",
        open_interest=open_interest,
        long_short_ratio=long_short_ratio,
        funding_rate=funding_rate,
        reason="Order flow is balanced. Trade is allowed." + liq_reason
    )
