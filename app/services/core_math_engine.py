"""
core_math_engine.py
====================
Single source of truth for all SMC (Smart Money Concepts) mathematical
algorithms and quantitative risk helpers.

Both smc_engine.py and analysis_service.py import from here rather than
re-implementing the same logic.

Exported functions
------------------
SMC Algorithms:
  detect_swings                  – N-period pivot swing point detection
  detect_structure_and_sweeps    – Sequential BOS / CHoCH + liquidity sweeps
  detect_fvgs                    – Bullish / Bearish Fair Value Gaps
  detect_order_blocks            – Expansion-based Order Block detection
  calculate_pressure_and_trend   – EMA-50 trend + buy/sell pressure ratio
  calculate_bias_score           – Weighted per-timeframe bias score [-1.0, 1.0]
  calculate_timeframe_analysis   – Assembles all passes into TimeframeAnalysis

Risk / Dynamic Leverage:
  calculate_atr                  – 14-period ATR on closed candles (no lookahead)
  calculate_dynamic_leverage     – Volatility-scaled leverage (inverse to ATR%)
  get_primary_timeframe          – Infer primary TF from a sorted timeframe list
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.core.config import settings
from app.core.logging import logger
from app.schemas.analysis import (
    FairValueGap,
    LiquiditySweep,
    MarketStructureEvent,
    OrderBlock,
    SwingPoint,
    TimeframeAnalysis,
)
from app.schemas.market import Candle

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TF_SORT_ORDER: Dict[str, int] = {
    "1m": 0,
    "3m": 1,
    "5m": 2,
    "15m": 3,
    "30m": 4,
    "1h": 5,
    "2h": 6,
    "4h": 7,
    "6h": 8,
    "8h": 9,
    "12h": 10,
    "1d": 11,
    "3d": 12,
    "1w": 13,
}


# ---------------------------------------------------------------------------
# get_primary_timeframe
# ---------------------------------------------------------------------------

def get_primary_timeframe(timeframes: List[str]) -> str:
    """
    Return the 'middle' timeframe from a list (by canonical resolution order).
    Falls back to the lowest-resolution available if sorting fails.

    Examples
    --------
    ["1m","5m","15m","1h","4h"] → "15m"
    ["5m","1h"]                 → "5m"
    ["4h"]                      → "4h"
    """
    if not timeframes:
        return "15m"  # safe default

    sorted_tfs = sorted(timeframes, key=lambda tf: _TF_SORT_ORDER.get(tf, 99))
    mid_idx = max(0, (len(sorted_tfs) - 1) // 2)
    return sorted_tfs[mid_idx]


# ---------------------------------------------------------------------------
# detect_swings
# ---------------------------------------------------------------------------

def detect_swings(df: pd.DataFrame, n: int = 3) -> List[Dict[str, Any]]:
    """
    Identify swing highs and swing lows using an N-period pivot algorithm.

    A candle at index ``i`` is a Swing High if its ``high`` is strictly
    greater than the ``n`` candles on both sides.  Mirror logic for Swing Low.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain columns: timestamp, high, low.
    n : int
        Half-window size.  Default 3 means each neighbour band is 3 candles.

    Returns
    -------
    List of dicts with keys: index, timestamp, type ("HIGH"/"LOW"), price,
    is_broken (always False at detection time).
    """
    swings: List[Dict[str, Any]] = []
    length = len(df)

    if length < 2 * n + 1:
        return swings

    for i in range(n, length - n):
        # --- Swing High ---
        is_high = all(
            df["high"].iloc[i] > df["high"].iloc[i - j]
            and df["high"].iloc[i] > df["high"].iloc[i + j]
            for j in range(1, n + 1)
        )
        if is_high:
            swings.append(
                {
                    "index": i,
                    "timestamp": df["timestamp"].iloc[i],
                    "type": "HIGH",
                    "price": float(df["high"].iloc[i]),
                    "is_broken": False,
                }
            )

        # --- Swing Low ---
        is_low = all(
            df["low"].iloc[i] < df["low"].iloc[i - j]
            and df["low"].iloc[i] < df["low"].iloc[i + j]
            for j in range(1, n + 1)
        )
        if is_low:
            swings.append(
                {
                    "index": i,
                    "timestamp": df["timestamp"].iloc[i],
                    "type": "LOW",
                    "price": float(df["low"].iloc[i]),
                    "is_broken": False,
                }
            )

    return swings


# ---------------------------------------------------------------------------
# detect_structure_and_sweeps
# ---------------------------------------------------------------------------

def detect_structure_and_sweeps(
    df: pd.DataFrame,
    swings_raw: List[Dict[str, Any]],
    n: int = 3,
) -> Tuple[List[MarketStructureEvent], List[LiquiditySweep], Optional[str]]:
    """
    Sequential BOS / CHoCH detection and Liquidity Sweep identification.

    Processing is strictly chronological — swing confirmation is delayed by
    ``n`` candles to the right so there is **no lookahead bias**.

    Parameters
    ----------
    df : pd.DataFrame
        Full candle DataFrame (timestamp, open, high, low, close).
    swings_raw : List[dict]
        Output of ``detect_swings()``.
    n : int
        Swing confirmation delay (must match the ``n`` used in detect_swings).

    Returns
    -------
    (structure_events, liquidity_sweeps, last_break_direction)
        last_break_direction : "BULLISH" | "BEARISH" | None
    """
    structure_events: List[MarketStructureEvent] = []
    liquidity_sweeps: List[LiquiditySweep] = []
    active_highs: List[Dict[str, Any]] = []
    active_lows: List[Dict[str, Any]] = []
    last_break_direction: Optional[str] = None

    for k in range(len(df)):
        close_val = float(df["close"].iloc[k])
        high_val = float(df["high"].iloc[k])
        low_val = float(df["low"].iloc[k])
        timestamp_val = df["timestamp"].iloc[k]

        # Add swings that were confirmed at this candle (swing index = k - n)
        confirmed_idx = k - n
        for s in swings_raw:
            if s["index"] == confirmed_idx:
                if s["type"] == "HIGH":
                    active_highs.append(s)
                else:
                    active_lows.append(s)

        unbroken_highs = [h for h in active_highs if not h["is_broken"]]
        unbroken_lows = [lo for lo in active_lows if not lo["is_broken"]]

        # --- Liquidity Sweep (wick beyond, close within) ---
        if unbroken_lows:
            recent_low = unbroken_lows[-1]
            if low_val < recent_low["price"] and close_val > recent_low["price"]:
                liquidity_sweeps.append(
                    LiquiditySweep(
                        type="BULLISH",
                        price_swept=recent_low["price"],
                        timestamp=timestamp_val,
                    )
                )

        if unbroken_highs:
            recent_high = unbroken_highs[-1]
            if high_val > recent_high["price"] and close_val < recent_high["price"]:
                liquidity_sweeps.append(
                    LiquiditySweep(
                        type="BEARISH",
                        price_swept=recent_high["price"],
                        timestamp=timestamp_val,
                    )
                )

        # --- Market Structure Break: BOS or CHoCH ---
        if unbroken_highs:
            recent_high = unbroken_highs[-1]
            if close_val > recent_high["price"]:
                event_type = "BOS" if last_break_direction == "BULLISH" else "CHoCH"
                structure_events.append(
                    MarketStructureEvent(
                        type=event_type,
                        direction="BULLISH",
                        price=recent_high["price"],
                        timestamp=timestamp_val,
                    )
                )
                last_break_direction = "BULLISH"
                for h in active_highs:
                    if h["price"] <= recent_high["price"]:
                        h["is_broken"] = True

        if unbroken_lows:
            recent_low = unbroken_lows[-1]
            if close_val < recent_low["price"]:
                event_type = "BOS" if last_break_direction == "BEARISH" else "CHoCH"
                structure_events.append(
                    MarketStructureEvent(
                        type=event_type,
                        direction="BEARISH",
                        price=recent_low["price"],
                        timestamp=timestamp_val,
                    )
                )
                last_break_direction = "BEARISH"
                for lo in active_lows:
                    if lo["price"] >= recent_low["price"]:
                        lo["is_broken"] = True

    return structure_events, liquidity_sweeps, last_break_direction


# ---------------------------------------------------------------------------
# detect_fvgs
# ---------------------------------------------------------------------------

def detect_fvgs(df: pd.DataFrame) -> List[FairValueGap]:
    """
    Detect Bullish and Bearish Fair Value Gaps (three-candle pattern).

    Bullish FVG : candle[i].low > candle[i-2].high  (gap between i-2 and i)
    Bearish FVG : candle[i].high < candle[i-2].low

    Mitigation is determined by subsequent price action closing into the gap.
    This uses a forward-scan only on already-observed candles, so there is
    no lookahead bias relative to the candle sequence provided.

    Parameters
    ----------
    df : pd.DataFrame
        Columns: high, low (and any others ignored).

    Returns
    -------
    List[FairValueGap]
    """
    fvgs: List[FairValueGap] = []

    for i in range(2, len(df)):
        high_i2 = float(df["high"].iloc[i - 2])
        low_i2 = float(df["low"].iloc[i - 2])
        high_i = float(df["high"].iloc[i])
        low_i = float(df["low"].iloc[i])

        # --- Bullish FVG ---
        if low_i > high_i2:
            top = low_i
            bottom = high_i2
            is_mitigated = any(
                float(df["low"].iloc[k]) <= bottom for k in range(i + 1, len(df))
            )
            fvgs.append(
                FairValueGap(type="BULLISH", top=top, bottom=bottom, is_mitigated=is_mitigated)
            )

        # --- Bearish FVG ---
        elif high_i < low_i2:
            top = low_i2
            bottom = high_i
            is_mitigated = any(
                float(df["high"].iloc[k]) >= top for k in range(i + 1, len(df))
            )
            fvgs.append(
                FairValueGap(type="BEARISH", top=top, bottom=bottom, is_mitigated=is_mitigated)
            )

    return fvgs


# ---------------------------------------------------------------------------
# detect_order_blocks
# ---------------------------------------------------------------------------

def detect_order_blocks(df: pd.DataFrame) -> List[OrderBlock]:
    """
    Detect Order Blocks using body-expansion logic.

    An Order Block is the last opposing candle immediately before a strong
    expansion move that also creates a Fair Value Gap:

    Bullish OB:
      - expansion candle (body > 1.5× 10-period body MA) is green
      - expansion also created a bullish FVG (candle[i].low > candle[i-2].high)
      - look back up to 4 candles to find the last *bearish* candle → that is the OB
      - mitigated if any later close falls below OB bottom

    Bearish OB: mirror logic.

    Parameters
    ----------
    df : pd.DataFrame
        Columns: open, high, low, close.

    Returns
    -------
    List[OrderBlock]
    """
    order_blocks: List[OrderBlock] = []

    df = df.copy()
    df["body"] = (df["close"] - df["open"]).abs()
    # Use .shift(1) so the MA at row i is computed on data up to row i-1 (no lookahead)
    df["body_ma"] = df["body"].shift(1).rolling(min(len(df), 10), min_periods=1).mean()

    for i in range(2, len(df)):
        body_ma = df["body_ma"].iloc[i]
        if pd.isna(body_ma) or body_ma == 0:
            continue

        is_expansion = df["body"].iloc[i - 1] > 1.5 * body_ma
        close_prev = float(df["close"].iloc[i - 1])
        open_prev = float(df["open"].iloc[i - 1])
        low_i = float(df["low"].iloc[i])
        high_i = float(df["high"].iloc[i])
        high_i2 = float(df["high"].iloc[i - 2])
        low_i2 = float(df["low"].iloc[i - 2])

        # --- Bullish OB ---
        if is_expansion and close_prev > open_prev and low_i > high_i2:
            ob_idx = next(
                (
                    i - offset
                    for offset in range(2, 6)
                    if i - offset >= 0
                    and float(df["close"].iloc[i - offset]) < float(df["open"].iloc[i - offset])
                ),
                None,
            )
            if ob_idx is not None:
                top = float(df["high"].iloc[ob_idx])
                bottom = float(df["low"].iloc[ob_idx])
                is_mitigated = any(
                    float(df["close"].iloc[k]) < bottom for k in range(ob_idx + 1, len(df))
                )
                order_blocks.append(
                    OrderBlock(type="BULLISH", top=top, bottom=bottom, is_mitigated=is_mitigated)
                )

        # --- Bearish OB ---
        elif is_expansion and close_prev < open_prev and high_i < low_i2:
            ob_idx = next(
                (
                    i - offset
                    for offset in range(2, 6)
                    if i - offset >= 0
                    and float(df["close"].iloc[i - offset]) > float(df["open"].iloc[i - offset])
                ),
                None,
            )
            if ob_idx is not None:
                top = float(df["high"].iloc[ob_idx])
                bottom = float(df["low"].iloc[ob_idx])
                is_mitigated = any(
                    float(df["close"].iloc[k]) > top for k in range(ob_idx + 1, len(df))
                )
                order_blocks.append(
                    OrderBlock(type="BEARISH", top=top, bottom=bottom, is_mitigated=is_mitigated)
                )

    return order_blocks


# ---------------------------------------------------------------------------
# calculate_pressure_and_trend
# ---------------------------------------------------------------------------

def calculate_pressure_and_trend(
    df: pd.DataFrame,
) -> Tuple[float, float, str]:
    """
    Compute buy/sell pressure ratio and EMA-50 trend classification.

    Buy pressure  = sum of green candle body sizes / total body size (last 14)
    Sell pressure = 1 - buy_pressure
    Trend         = BULLISH / BEARISH / NEUTRAL relative to EMA-50 with 0.05% band

    Parameters
    ----------
    df : pd.DataFrame
        Columns: open, close.

    Returns
    -------
    (buy_pressure, sell_pressure, trend)
    """
    lookback = min(len(df), 14)
    recent_df = df.iloc[-lookback:]

    green_bodies = 0.0
    red_bodies = 0.0
    for _, row in recent_df.iterrows():
        diff = float(row["close"]) - float(row["open"])
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

    # EMA-50 (uses all available candles, capped at len(df))
    ema50_series = df["close"].ewm(span=min(len(df), 50), adjust=False).mean()
    last_close = float(df["close"].iloc[-1])
    last_ema50 = float(ema50_series.iloc[-1])

    if last_close > last_ema50 * 1.0005:
        trend = "BULLISH"
    elif last_close < last_ema50 * 0.9995:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    return buy_pressure, sell_pressure, trend


# ---------------------------------------------------------------------------
# calculate_bias_score
# ---------------------------------------------------------------------------

def calculate_bias_score(
    trend: str,
    last_break_direction: Optional[str],
    fvgs: List[FairValueGap],
    order_blocks: List[OrderBlock],
    liquidity_sweeps: List[LiquiditySweep],
) -> float:
    """
    Compute a weighted bias score in [-1.0, 1.0].

    Weights
    -------
    Trend (EMA-50)        : ± 0.30  (30%)
    Structure direction   : ± 0.30  (30%)
    Unmitigated bull FVGs : + 0.075 each, max +0.15  (15%)
    Unmitigated bear FVGs : - 0.075 each, max -0.15
    Unmitigated bull OBs  : + 0.075 each, max +0.15  (15%)
    Unmitigated bear OBs  : - 0.075 each, max -0.15
    Recent liquidity sweep: ± 0.10  (10%)  — last 5 events only

    Returns
    -------
    float clamped to [-1.0, 1.0]
    """
    score = 0.0

    # Trend
    if trend == "BULLISH":
        score += 0.30
    elif trend == "BEARISH":
        score -= 0.30

    # Structure
    if last_break_direction == "BULLISH":
        score += 0.30
    elif last_break_direction == "BEARISH":
        score -= 0.30

    # FVGs
    bull_fvgs = [f for f in fvgs if f.type == "BULLISH" and not f.is_mitigated]
    bear_fvgs = [f for f in fvgs if f.type == "BEARISH" and not f.is_mitigated]
    score += min(len(bull_fvgs) * 0.075, 0.15)
    score -= min(len(bear_fvgs) * 0.075, 0.15)

    # Order Blocks
    bull_obs = [o for o in order_blocks if o.type == "BULLISH" and not o.is_mitigated]
    bear_obs = [o for o in order_blocks if o.type == "BEARISH" and not o.is_mitigated]
    score += min(len(bull_obs) * 0.075, 0.15)
    score -= min(len(bear_obs) * 0.075, 0.15)

    # Liquidity sweeps (last 5 only)
    recent_sweeps = liquidity_sweeps[-5:]
    if any(s.type == "BULLISH" for s in recent_sweeps):
        score += 0.10
    if any(s.type == "BEARISH" for s in recent_sweeps):
        score -= 0.10

    return max(-1.0, min(1.0, score))


# ---------------------------------------------------------------------------
# calculate_timeframe_analysis  (main entry for per-TF work)
# ---------------------------------------------------------------------------

def calculate_timeframe_analysis(
    candles: List[Candle],
    timeframe: str,
    n: int = 3,
) -> TimeframeAnalysis:
    """
    Run all SMC analysis passes on a single timeframe and return a
    ``TimeframeAnalysis`` Pydantic model.

    Parameters
    ----------
    candles   : List of closed (confirmed) Candle objects — active candle
                must already have been removed by the caller.
    timeframe : Label string ("15m", "1h", etc.) used only for error messages.
    n         : Swing window half-size.

    Returns
    -------
    TimeframeAnalysis
    """
    if not candles:
        raise ValueError(f"No candles provided for timeframe {timeframe}")

    df = pd.DataFrame([c.model_dump() for c in candles])

    # Pass 1 — Swing points
    swings_raw = detect_swings(df, n=n)

    # Pass 2 — Structure events + liquidity sweeps
    structure_events, liquidity_sweeps, last_break_direction = detect_structure_and_sweeps(
        df, swings_raw, n=n
    )

    # Pass 3 — Fair Value Gaps
    fvgs = detect_fvgs(df)

    # Pass 4 — Order Blocks
    order_blocks = detect_order_blocks(df)

    # Pass 5 — Pressure + Trend
    buy_pressure, sell_pressure, trend = calculate_pressure_and_trend(df)

    # Pass 6 — Bias score
    bias_score = calculate_bias_score(
        trend, last_break_direction, fvgs, order_blocks, liquidity_sweeps
    )

    # Convert internal swing dicts → SwingPoint Pydantic models
    swings_schema = [
        SwingPoint(
            index=s["index"],
            timestamp=s["timestamp"].to_pydatetime(),
            type=s["type"],
            price=s["price"],
            is_broken=s["is_broken"],
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
        bias_score=bias_score,
    )


# ---------------------------------------------------------------------------
# calculate_atr  (Risk Management helper)
# ---------------------------------------------------------------------------

def calculate_atr(candles: List[Candle], period: Optional[int] = None) -> Optional[float]:
    """
    Calculate the Average True Range (ATR) on a list of closed candles.

    Uses Wilder's smoothing (EWM with alpha = 1/period) on the True Range.
    The active (forming) candle must have been removed **before** calling
    this function to avoid lookahead bias.

    Parameters
    ----------
    candles : List[Candle]  — closed candles only
    period  : int — ATR lookback (defaults to settings.ATR_PERIOD, usually 14)

    Returns
    -------
    float | None  — None if there are insufficient candles.
    """
    if period is None:
        period = getattr(settings, "ATR_PERIOD", 14)

    if not candles or len(candles) < period + 1:
        logger.warning(
            f"calculate_atr: insufficient candles ({len(candles)}) for period {period}"
        )
        return None

    df = pd.DataFrame([c.model_dump() for c in candles])

    # True Range components (shift(1) for previous close — no lookahead)
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Drop first row (NaN from shift)
    tr = tr.iloc[1:]

    # Wilder's ATR via EWM (span = period uses alpha ≈ 2/(period+1); for exact
    # Wilder's we use alpha = 1/period via com = period - 1)
    atr_series = tr.ewm(com=period - 1, adjust=False).mean()
    atr_value = float(atr_series.iloc[-1])

    return round(atr_value, 6) if not math.isnan(atr_value) else None


# ---------------------------------------------------------------------------
# calculate_dynamic_leverage
# ---------------------------------------------------------------------------

def calculate_dynamic_leverage(
    atr_value: float,
    current_price: float,
    min_leverage: Optional[int] = None,
    max_leverage: Optional[int] = None,
) -> int:
    """
    Compute leverage that scales **inversely** with market volatility.

    The ATR is expressed as a percentage of current price (ATR%).  Higher ATR%
    means higher volatility → lower leverage (and vice-versa).

    Mapping (ATR%)       Leverage
    ─────────────────────────────
    ≥ 3.0%              → MIN  (3×)   e.g. high-vol altcoin
    2.0 – 3.0%          → 5×
    1.0 – 2.0%          → 10×
    0.5 – 1.0%          → 15×
    < 0.5%              → MAX  (20×)  e.g. BTC in low-vol regime

    Parameters
    ----------
    atr_value     : float  — raw ATR value in price units
    current_price : float  — latest close price
    min_leverage  : int    — floor (defaults to settings.MIN_LEVERAGE)
    max_leverage  : int    — cap   (defaults to settings.MAX_LEVERAGE)

    Returns
    -------
    int — calculated leverage, clamped to [min_leverage, max_leverage]
    """
    if min_leverage is None:
        min_leverage = getattr(settings, "MIN_LEVERAGE", 3)
    if max_leverage is None:
        max_leverage = getattr(settings, "MAX_LEVERAGE", 20)

    if current_price <= 0 or atr_value <= 0:
        logger.warning("calculate_dynamic_leverage: invalid inputs — returning min leverage")
        return min_leverage

    atr_pct = (atr_value / current_price) * 100.0

    if atr_pct >= 3.0:
        raw_leverage = min_leverage         # ≥ 3% vol → 3×
    elif atr_pct >= 2.0:
        raw_leverage = 5
    elif atr_pct >= 1.0:
        raw_leverage = 10
    elif atr_pct >= 0.5:
        raw_leverage = 15
    else:
        raw_leverage = max_leverage         # < 0.5% vol → 20×

    leverage = int(max(min_leverage, min(max_leverage, raw_leverage)))
    logger.info(
        f"Dynamic leverage: ATR={atr_value:.4f} ({atr_pct:.3f}% of price) → {leverage}×"
    )
    return leverage
