from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime

class TradeSignal(BaseModel):
    symbol: str = Field(..., json_schema_extra={"example": "BTC/USDT"})
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float | None = None
    timeframe: str = Field(..., json_schema_extra={"example": "15m"})
    signal_source: str = Field(..., json_schema_extra={"example": "n8n"})
    timestamp: datetime
