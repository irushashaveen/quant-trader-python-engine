from pydantic import BaseModel, Field
from datetime import datetime
from typing import Dict, List

class Candle(BaseModel):
    timestamp: datetime = Field(..., description="Start time of the candle")
    open: float = Field(..., description="Opening price")
    high: float = Field(..., description="Highest price")
    low: float = Field(..., description="Lowest price")
    close: float = Field(..., description="Closing price")
    volume: float = Field(..., description="Trading volume")

class MultiTimeframeOHLCVResponse(BaseModel):
    symbol: str = Field(..., json_schema_extra={"example": "BTC/USDT"})
    limit: int = Field(..., json_schema_extra={"example": 100})
    data: Dict[str, List[Candle]] = Field(..., description="Candle data grouped by timeframe")
