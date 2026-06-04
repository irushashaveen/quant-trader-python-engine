import pandas as pd
import numpy as np
from typing import Dict, List, Any
from app.core.config import settings
from app.core.logging import logger
from app.schemas.market import Candle
from app.schemas.analysis import (
    SwingPoint,
    FairValueGap,
    OrderBlock,
    MarketStructureEvent,
    LiquiditySweep,
    TimeframeAnalysis,
    MarketStateAnalysisResponse
)

def detect_swings(df: pd.DataFrame, n: int = 3) -> List[Dict[str, Any]]:
    swings = []
    length = len(df)
    if length < 2 * n + 1:
        return swings

    for i in range(n, length - n):
        # Swing High
        is_high = True
        for j in range(1, n + 1):
            if df['high'].iloc[i] <= df['high'].iloc[i - j] or df['high'].iloc[i] <= df['high'].iloc[i + j]:
                is_high = False
                break
        
        if is_high:
            swings.append({
                "index": i,
                "timestamp": df['timestamp'].iloc[i],
                "type": "HIGH",
                "price": float(df['high'].iloc[i]),
                "is_broken": False
            })

        # Swing Low
        is_low = True
        for j in range(1, n + 1):
            if df['low'].iloc[i] >= df['low'].iloc[i - j] or df['low'].iloc[i] >= df['low'].iloc[i + j]:
                is_low = False
                break
        
        if is_low:
            swings.append({
                "index": i,
                "timestamp": df['timestamp'].iloc[i],
                "type": "LOW",
                "price": float(df['low'].iloc[i]),
                "is_broken": False
            })
            
    return swings

def calculate_timeframe_analysis(candles: List[Candle], timeframe: str, n: int = 3) -> TimeframeAnalysis:
    if not candles:
        raise ValueError(f"No candles provided for timeframe {timeframe}")

    # Convert to DataFrame
    df = pd.DataFrame([c.model_dump() for c in candles])
    
    # 1. Swing Point Detection
    swings_raw = detect_swings(df, n=n)
    
    # 2. Track swing breaches & structure events (BOS/CHoCH)
    structure_events = []
    liquidity_sweeps = []
    active_highs = []
    active_lows = []
    last_break_direction = None
    
    # Process sequentially to avoid lookahead bias in BOS/CHoCH and sweeps
    for k in range(len(df)):
        close_val = float(df['close'].iloc[k])
        high_val = float(df['high'].iloc[k])
        low_val = float(df['low'].iloc[k])
        timestamp_val = df['timestamp'].iloc[k]
        
        # Add swings confirmed at index k (swing index k - n)
        confirmed_idx = k - n
        confirmed_swings = [s for s in swings_raw if s["index"] == confirmed_idx]
        for s in confirmed_swings:
            if s["type"] == "HIGH":
                active_highs.append(s)
            else:
                active_lows.append(s)
                
        # Filter active swings to only unbroken ones
        unbroken_highs = [h for h in active_highs if not h["is_broken"]]
        unbroken_lows = [l for l in active_lows if not l["is_broken"]]
        
        # Check Liquidity Sweeps
        if unbroken_lows:
            recent_low = unbroken_lows[-1]
            if low_val < recent_low["price"] and close_val > recent_low["price"]:
                liquidity_sweeps.append(
                    LiquiditySweep(
                        type="BULLISH",
                        price_swept=recent_low["price"],
                        timestamp=timestamp_val
                    )
                )
                
        if unbroken_highs:
            recent_high = unbroken_highs[-1]
            if high_val > recent_high["price"] and close_val < recent_high["price"]:
                liquidity_sweeps.append(
                    LiquiditySweep(
                        type="BEARISH",
                        price_swept=recent_high["price"],
                        timestamp=timestamp_val
                    )
                )
                
        # Check Market Structure Breaks (BOS / CHoCH)
        if unbroken_highs:
            recent_high = unbroken_highs[-1]
            if close_val > recent_high["price"]:
                direction = "BULLISH"
                event_type = "BOS" if last_break_direction == "BULLISH" else "CHoCH"
                structure_events.append(
                    MarketStructureEvent(
                        type=event_type,
                        direction=direction,
                        price=recent_high["price"],
                        timestamp=timestamp_val
                    )
                )
                last_break_direction = "BULLISH"
                # Mark high broken
                for h in active_highs:
                    if h["price"] <= recent_high["price"]:
                        h["is_broken"] = True
                        
        if unbroken_lows:
            recent_low = unbroken_lows[-1]
            if close_val < recent_low["price"]:
                direction = "BEARISH"
                event_type = "BOS" if last_break_direction == "BEARISH" else "CHoCH"
                structure_events.append(
                    MarketStructureEvent(
                        type=event_type,
                        direction=direction,
                        price=recent_low["price"],
                        timestamp=timestamp_val
                    )
                )
                last_break_direction = "BEARISH"
                # Mark low broken
                for l in active_lows:
                    if l["price"] >= recent_low["price"]:
                        l["is_broken"] = True

    # 3. Fair Value Gaps (FVG)
    fvgs = []
    for i in range(2, len(df)):
        high_i2 = float(df['high'].iloc[i - 2])
        low_i = float(df['low'].iloc[i])
        low_i2 = float(df['low'].iloc[i - 2])
        high_i = float(df['high'].iloc[i])
        
        # Bullish FVG
        if low_i > high_i2:
            top = low_i
            bottom = high_i2
            is_mitigated = False
            for k in range(i + 1, len(df)):
                if float(df['low'].iloc[k]) <= bottom:
                    is_mitigated = True
                    break
            fvgs.append(FairValueGap(type="BULLISH", top=top, bottom=bottom, is_mitigated=is_mitigated))
            
        # Bearish FVG
        elif high_i < low_i2:
            top = low_i2
            bottom = high_i
            is_mitigated = False
            for k in range(i + 1, len(df)):
                if float(df['high'].iloc[k]) >= top:
                    is_mitigated = True
                    break
            fvgs.append(FairValueGap(type="BEARISH", top=top, bottom=bottom, is_mitigated=is_mitigated))

    # 4. Order Blocks (OB)
    order_blocks = []
    df['body'] = (df['close'] - df['open']).abs()
    df['body_ma'] = df['body'].rolling(min(len(df), 10)).mean()
    
    for i in range(2, len(df)):
        body_ma = df['body_ma'].iloc[i - 1]
        if pd.isna(body_ma) or body_ma == 0:
            continue
            
        is_expansion = df['body'].iloc[i - 1] > 1.5 * body_ma
        close_prev = float(df['close'].iloc[i - 1])
        open_prev = float(df['open'].iloc[i - 1])
        low_i = float(df['low'].iloc[i])
        high_i2 = float(df['high'].iloc[i - 2])
        high_i = float(df['high'].iloc[i])
        low_i2 = float(df['low'].iloc[i - 2])
        
        # Bullish OB
        if is_expansion and close_prev > open_prev:
            if low_i > high_i2:
                ob_idx = None
                for offset in range(2, 6):
                    idx = i - offset
                    if idx >= 0 and float(df['close'].iloc[idx]) < float(df['open'].iloc[idx]):
                        ob_idx = idx
                        break
                if ob_idx is not None:
                    top = float(df['high'].iloc[ob_idx])
                    bottom = float(df['low'].iloc[ob_idx])
                    is_mitigated = False
                    for k in range(ob_idx + 1, len(df)):
                        if float(df['close'].iloc[k]) < bottom:
                            is_mitigated = True
                            break
                    order_blocks.append(OrderBlock(type="BULLISH", top=top, bottom=bottom, is_mitigated=is_mitigated))
                    
        # Bearish OB
        elif is_expansion and close_prev < open_prev:
            if high_i < low_i2:
                ob_idx = None
                for offset in range(2, 6):
                    idx = i - offset
                    if idx >= 0 and float(df['close'].iloc[idx]) > float(df['open'].iloc[idx]):
                        ob_idx = idx
                        break
                if ob_idx is not None:
                    top = float(df['high'].iloc[ob_idx])
                    bottom = float(df['low'].iloc[ob_idx])
                    is_mitigated = False
                    for k in range(ob_idx + 1, len(df)):
                        if float(df['close'].iloc[k]) > top:
                            is_mitigated = True
                            break
                    order_blocks.append(OrderBlock(type="BEARISH", top=top, bottom=bottom, is_mitigated=is_mitigated))

    # 5. Buy / Sell Pressure & Trend
    # Ratio of green vs red body sizes over last 14 candles
    lookback = min(len(df), 14)
    recent_df = df.iloc[-lookback:]
    green_bodies = 0.0
    red_bodies = 0.0
    for _, row in recent_df.iterrows():
        diff = float(row['close']) - float(row['open'])
        if diff >= 0:
            green_bodies += diff
        else:
            red_bodies += abs(diff)
            
    total_bodies = green_bodies + red_bodies
    if total_bodies > 0:
        buy_pressure = green_bodies / total_bodies
        sell_pressure = red_bodies / total_bodies
    else:
        buy_pressure = 0.5
        sell_pressure = 0.5
        
    # Trend with 50 EMA
    df['ema50'] = df['close'].ewm(span=min(len(df), 50), adjust=False).mean()
    last_close = float(df['close'].iloc[-1])
    last_ema50 = float(df['ema50'].iloc[-1])
    
    if last_close > last_ema50 * 1.0005:
        trend = "BULLISH"
    elif last_close < last_ema50 * 0.9995:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    # 6. Scoring timeframe bias
    bias_score = 0.0
    
    # Trend contribution (30% max)
    if trend == "BULLISH":
        bias_score += 0.3
    elif trend == "BEARISH":
        bias_score -= 0.3
        
    # Structure contribution (30% max)
    if last_break_direction == "BULLISH":
        bias_score += 0.3
    elif last_break_direction == "BEARISH":
        bias_score -= 0.3
        
    # FVGs (15% max)
    unmitigated_bullish_fvgs = [f for f in fvgs if f.type == "BULLISH" and not f.is_mitigated]
    unmitigated_bearish_fvgs = [f for f in fvgs if f.type == "BEARISH" and not f.is_mitigated]
    bias_score += min(len(unmitigated_bullish_fvgs) * 0.075, 0.15)
    bias_score -= min(len(unmitigated_bearish_fvgs) * 0.075, 0.15)
    
    # OBs (15% max)
    unmitigated_bullish_obs = [o for o in order_blocks if o.type == "BULLISH" and not o.is_mitigated]
    unmitigated_bearish_obs = [o for o in order_blocks if o.type == "BEARISH" and not o.is_mitigated]
    bias_score += min(len(unmitigated_bullish_obs) * 0.075, 0.15)
    bias_score -= min(len(unmitigated_bearish_obs) * 0.075, 0.15)
    
    # Liquidity Sweeps (10% max)
    # Check if a sweep occurred in the last 5 candles
    recent_sweeps = liquidity_sweeps[-5:]
    if any(s.type == "BULLISH" for s in recent_sweeps):
        bias_score += 0.1
    if any(s.type == "BEARISH" for s in recent_sweeps):
        bias_score -= 0.1
        
    bias_score = max(-1.0, min(1.0, bias_score))

    # Convert internal SwingPoint representations to Schema objects
    swings_schema = [
        SwingPoint(
            index=s["index"],
            timestamp=s["timestamp"].to_pydatetime(),
            type=s["type"],
            price=s["price"],
            is_broken=s["is_broken"]
        )
        for s in swings_raw
    ]

    return TimeframeAnalysis(
        trend=trend,
        swings=swings_schema,
        structure_events=structure_events,
        liquidity_sweeps=liquidity_sweeps,
        fvgs=fvgs,
        order_blocks=order_blocks,
        buy_pressure=buy_pressure,
        sell_pressure=sell_pressure,
        bias_score=bias_score
    )

async def analyze_market_state(
    symbol: str, 
    timeframe_data: Dict[str, List[Candle]]
) -> MarketStateAnalysisResponse:
    timeframe_analyses = {}
    timeframe_scores = {}
    
    for tf, candles in timeframe_data.items():
        if not candles:
            continue
        try:
            analysis = calculate_timeframe_analysis(candles, tf, n=settings.SWING_WINDOW)
            timeframe_analyses[tf] = analysis
            timeframe_scores[tf] = analysis.bias_score
        except Exception as e:
            logger.error(f"Error analyzing timeframe {tf}: {str(e)}")
            raise e

    # Aggregate scoring using timeframe weights
    weights = settings.TIMEFRAME_WEIGHTS
    total_weight = 0.0
    weighted_score = 0.0
    
    for tf, score in timeframe_scores.items():
        weight = weights.get(tf, 0.0)
        weighted_score += score * weight
        total_weight += weight
        
    if total_weight > 0:
        aggregate_bias_score = weighted_score / total_weight
    else:
        aggregate_bias_score = 0.0

    # Determine confidence description
    abs_score = abs(aggregate_bias_score)
    if abs_score > 0.6:
        confidence = "HIGH CONFIDENCE BIAS"
    elif abs_score > 0.3:
        confidence = "MODERATE CONFIDENCE BIAS"
    else:
        confidence = "NEUTRAL / LOW CONFIDENCE"

    limit = len(next(iter(timeframe_data.values()))) if timeframe_data else 100

    return MarketStateAnalysisResponse(
        symbol=symbol,
        limit=limit,
        aggregate_bias_score=round(aggregate_bias_score, 4),
        timeframe_analyses=timeframe_analyses,
        confidence=confidence
    )
