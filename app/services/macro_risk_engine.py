from typing import Dict, Any
from app.core.logging import logger

# 10-year historically backtested standard deviations and BTC correlation weights
MACRO_EVENT_CONFIGS = {
    "CPI": {
        "volatility": 0.24,
        "btc_correlation": -0.85
    },
    "NFP": {
        "volatility": 45.0,
        "btc_correlation": 0.60
    },
    "DEFAULT": {
        "volatility": 1.0,
        "btc_correlation": 0.0
    }
}

def get_macro_config(event_name: str) -> Dict[str, float]:
    """
    Fuzzy-matches a macro event name to CPI or NFP configs, falling back to DEFAULT.
    """
    event_upper = event_name.upper()
    if "CPI" in event_upper:
        return MACRO_EVENT_CONFIGS["CPI"]
    if "NFP" in event_upper or "NON-FARM" in event_upper or "NONFARM" in event_upper or "PAYROLL" in event_upper:
        return MACRO_EVENT_CONFIGS["NFP"]
    return MACRO_EVENT_CONFIGS["DEFAULT"]

def calculate_macro_risk(event_name: str, actual: float, forecast: float) -> Dict[str, Any]:
    """
    Calculates Economic Surprise Index (ESI) and Final Risk Score.
    ESI = (Actual Value - Forecast Value) / Historical_Volatility
    Final Risk Score = ESI * BTC_Correlation
    
    Classifies output:
      - Score < -0.5 -> HIGH_RISK_BEARISH (vetoes any LONG trades)
      - Score > 0.5  -> LOW_RISK_BULLISH
      - Otherwise    -> NEUTRAL
    """
    config = get_macro_config(event_name)
    volatility = config["volatility"]
    correlation = config["btc_correlation"]
    
    esi = (actual - forecast) / volatility if volatility > 0 else 0.0
    score = esi * correlation
    
    if score < -0.5:
        risk_class = "HIGH_RISK_BEARISH"
    elif score > 0.5:
        risk_class = "LOW_RISK_BULLISH"
    else:
        risk_class = "NEUTRAL"
        
    logger.info(
        f"Macro Risk: {event_name} (actual={actual}, forecast={forecast}) -> "
        f"ESI={esi:.4f}, Correlation={correlation}, Score={score:.4f} -> {risk_class}"
    )
    
    return {
        "event_name": event_name,
        "esi": esi,
        "correlation": correlation,
        "score": score,
        "classification": risk_class
    }
