from typing import Dict, Any, Optional
from app.core.logging import logger

class OrderFlowResult:
    def __init__(self, action: str, open_interest: float, long_short_ratio: float, funding_rate: float, reason: str):
        self.action = action  # 'ALLOW', 'VETO_LONG', 'VETO_SHORT', 'NEUTRAL'
        self.open_interest = open_interest
        self.long_short_ratio = long_short_ratio
        self.funding_rate = funding_rate
        self.reason = reason

def evaluate_order_flow(order_flow_data: Dict[str, Any]) -> OrderFlowResult:
    """
    Evaluates order flow signals to act as an alpha filter for trades.
    order_flow_data should contain:
      - 'long_short_ratio': float
      - 'open_interest': float
      - 'funding_rate': float
      - 'mark_price': float (optional context)
    """
    long_short_ratio = float(order_flow_data.get('long_short_ratio', 1.0))
    open_interest = float(order_flow_data.get('open_interest', 0.0))
    funding_rate = float(order_flow_data.get('funding_rate', 0.0))
    
    logger.info(f"Evaluating Order Flow: L/S={long_short_ratio}, OI={open_interest}, Funding={funding_rate}")

    # If retail is heavily long (> 2.5 ratio), whales will hunt longs -> VETO_LONG
    if long_short_ratio > 2.5:
        return OrderFlowResult(
            action="VETO_LONG",
            open_interest=open_interest,
            long_short_ratio=long_short_ratio,
            funding_rate=funding_rate,
            reason=f"Retail is heavily long (L/S ratio: {long_short_ratio}). Vetoing longs to avoid liquidation sweeps."
        )
        
    # If retail is heavily short (< 0.4 ratio), whales will hunt shorts -> VETO_SHORT
    if long_short_ratio < 0.4:
        return OrderFlowResult(
            action="VETO_SHORT",
            open_interest=open_interest,
            long_short_ratio=long_short_ratio,
            funding_rate=funding_rate,
            reason=f"Retail is heavily short (L/S ratio: {long_short_ratio}). Vetoing shorts to avoid liquidation sweeps."
        )
        
    # If funding rate is extremely high/positive, market is overheated long
    # Usually > 0.01% (0.0001 raw) or 0.1% depending on scale. Let's assume standard Binance funding scale where baseline is 0.0001
    if funding_rate > 0.001:  # 0.1% per 8 hours
         return OrderFlowResult(
            action="VETO_LONG",
            open_interest=open_interest,
            long_short_ratio=long_short_ratio,
            funding_rate=funding_rate,
            reason=f"Funding rate extremely high ({funding_rate}). Longs are paying shorts heavily, risk of correction."
        )
         
    # If funding rate is extremely low/negative, market is overheated short
    if funding_rate < -0.001: # -0.1% per 8 hours
         return OrderFlowResult(
            action="VETO_SHORT",
            open_interest=open_interest,
            long_short_ratio=long_short_ratio,
            funding_rate=funding_rate,
            reason=f"Funding rate extremely negative ({funding_rate}). Shorts are paying longs heavily, risk of short squeeze."
        )
        
    return OrderFlowResult(
        action="ALLOW",
        open_interest=open_interest,
        long_short_ratio=long_short_ratio,
        funding_rate=funding_rate,
        reason="Order flow is balanced. Trade is allowed."
    )
