from pydantic import BaseModel, Field
from datetime import datetime
from typing import Dict, List, Literal, Optional

class SwingPoint(BaseModel):
    index: int = Field(..., description="Index of the candle where the swing occurred")
    timestamp: datetime = Field(..., description="Timestamp of the swing candle")
    type: Literal["HIGH", "LOW"] = Field(..., description="Swing High or Swing Low")
    price: float = Field(..., description="Price of the swing point")
    is_broken: bool = Field(..., description="Whether the swing point has been broken by a candle close")

class FairValueGap(BaseModel):
    type: Literal["BULLISH", "BEARISH"] = Field(..., description="Bullish or Bearish FVG")
    top: float = Field(..., description="Top price boundary of the gap")
    bottom: float = Field(..., description="Bottom price boundary of the gap")
    is_mitigated: bool = Field(..., description="Whether subsequent price action filled this gap")

class OrderBlock(BaseModel):
    type: Literal["BULLISH", "BEARISH"] = Field(..., description="Bullish or Bearish OB")
    top: float = Field(..., description="Top price of the OB candle body/range")
    bottom: float = Field(..., description="Bottom price of the OB candle body/range")
    is_mitigated: bool = Field(..., description="Whether subsequent price action breached this zone")

class MarketStructureEvent(BaseModel):
    type: Literal["BOS", "CHoCH"] = Field(..., description="BOS (Break of Structure) or CHoCH (Change of Character)")
    direction: Literal["BULLISH", "BEARISH"] = Field(..., description="Trend direction of the breakout")
    price: float = Field(..., description="Price level at which structure was broken")
    timestamp: datetime = Field(..., description="Timestamp when structural break occurred")

class LiquiditySweep(BaseModel):
    type: Literal["BULLISH", "BEARISH"] = Field(..., description="Bullish or Bearish liquidity grab/sweep")
    price_swept: float = Field(..., description="The extreme price level swept (swing high/low)")
    timestamp: datetime = Field(..., description="Timestamp of the sweep candle")

class TimeframeAnalysis(BaseModel):
    trend: Literal["BULLISH", "BEARISH", "NEUTRAL"] = Field(..., description="Trend direction on this timeframe")
    swings: List[SwingPoint] = Field(..., description="Detected swing points")
    structure_events: List[MarketStructureEvent] = Field(..., description="BOS and CHoCH events")
    liquidity_sweeps: List[LiquiditySweep] = Field(..., description="Detected liquidity grabs/sweeps")
    fvgs: List[FairValueGap] = Field(..., description="Active Fair Value Gaps")
    order_blocks: List[OrderBlock] = Field(..., description="Active Order Blocks")
    buy_pressure: float = Field(..., description="Normalized buy pressure metric [0.0, 1.0]")
    sell_pressure: float = Field(..., description="Normalized sell pressure metric [0.0, 1.0]")
    bias_score: float = Field(..., description="Timeframe bias score [-1.0, 1.0]")

class MarketStateAnalysisResponse(BaseModel):
    symbol: str = Field(..., json_schema_extra={"example": "BTC/USDT"})
    limit: int = Field(..., json_schema_extra={"example": 100})
    aggregate_bias_score: float = Field(..., description="Multi-timeframe aggregate bias score [-1.0, 1.0]")
    timeframe_analyses: Dict[str, TimeframeAnalysis] = Field(..., description="Analysis outputs grouped by timeframe")
    confidence: str = Field(..., description="Summary confidence or reliability note")
