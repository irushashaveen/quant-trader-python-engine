"""
smc_engine.py
=============
Smart Money Concepts (SMC) orchestration layer.

All mathematical algorithms (swing detection, BOS/CHoCH, FVG, OB, bias
scoring, etc.) now live in ``core_math_engine``.  This module is responsible
only for:

  1. Iterating over the per-timeframe candle dictionaries.
  2. Calling ``core_math_engine.calculate_timeframe_analysis`` for each TF.
  3. Aggregating per-TF bias scores into a weighted ``aggregate_bias_score``.
  4. Returning the final ``MarketStateAnalysisResponse``.

This module does NOT contain any direct market-data fetching or WebSocket logic.
"""

from typing import Dict, List

from app.core.config import settings
from app.core.logging import logger
from app.schemas.analysis import MarketStateAnalysisResponse
from app.schemas.market import Candle
from app.services.core_math_engine import calculate_timeframe_analysis


async def run_smc_analysis(
    symbol: str,
    timeframe_data: Dict[str, List[Candle]],
) -> MarketStateAnalysisResponse:
    """
    Run the full SMC analysis pipeline across all supplied timeframes.

    Parameters
    ----------
    symbol         : Trading pair label (e.g. "BTC/USDT").
    timeframe_data : Mapping of timeframe → list of *closed* Candle objects.
                     The active (forming) candle must already have been removed
                     by the caller (``market_data_service.fetch_cleaned_ohlcv``).

    Returns
    -------
    MarketStateAnalysisResponse
    """
    timeframe_analyses: dict = {}
    timeframe_scores: dict = {}

    for tf, candles in timeframe_data.items():
        if not candles:
            continue
        try:
            analysis = calculate_timeframe_analysis(
                candles, tf, n=settings.SWING_WINDOW
            )
            timeframe_analyses[tf] = analysis
            timeframe_scores[tf] = analysis.bias_score
        except Exception as exc:
            logger.error(f"SMC analysis error on timeframe {tf} for {symbol}: {exc}")
            raise

    # Weighted aggregate bias score
    weights = settings.TIMEFRAME_WEIGHTS
    total_weight = 0.0
    weighted_score = 0.0

    for tf, score in timeframe_scores.items():
        w = weights.get(tf, 0.0)
        weighted_score += score * w
        total_weight += w

    aggregate_bias_score = (
        weighted_score / total_weight if total_weight > 0 else 0.0
    )

    # Confidence label
    abs_score = abs(aggregate_bias_score)
    if abs_score > 0.6:
        confidence = "HIGH CONFIDENCE BIAS"
    elif abs_score > 0.3:
        confidence = "MODERATE CONFIDENCE BIAS"
    else:
        confidence = "NEUTRAL / LOW CONFIDENCE"

    limit = (
        len(next(iter(timeframe_data.values()))) if timeframe_data else 100
    )

    return MarketStateAnalysisResponse(
        symbol=symbol,
        limit=limit,
        aggregate_bias_score=round(aggregate_bias_score, 4),
        timeframe_analyses=timeframe_analyses,
        confidence=confidence,
    )
