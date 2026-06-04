"""
Phase 5 — Signal Decision Service
===================================
Converts a MarketStateAnalysisResponse into a TradeDecision.

Pipeline (in order):
  A. Per-timeframe voting
  B. Weighted vote confluence check
  C. Confidence scoring (4 sub-factors)
  D. Liquidity / imbalance awareness checks  → no-trade conditions
  E. Stop loss & take profit calculation
  F. R:R minimum gate
  G. Confidence threshold gating
  H. Manual confirmation flag
  I. No-trade hard override (always last)
"""

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional, Tuple

from app.core.config import settings
from app.core.logging import logger
from app.schemas.analysis import (
    MarketStateAnalysisResponse,
    TimeframeAnalysis,
    OrderBlock,
    FairValueGap,
    SwingPoint,
)
from app.schemas.decision import RiskProfile, TradeDecision


# ---------------------------------------------------------------------------
# A. Per-Timeframe Voting
# ---------------------------------------------------------------------------

def _vote_for_timeframe(bias_score: float) -> Literal["LONG", "SHORT", "NEUTRAL"]:
    """Cast a directional vote for a single timeframe based on its bias score."""
    threshold = settings.VOTE_BIAS_THRESHOLD
    if bias_score >= threshold:
        return "LONG"
    elif bias_score <= -threshold:
        return "SHORT"
    return "NEUTRAL"


def _compute_timeframe_votes(
    timeframe_analyses: Dict[str, TimeframeAnalysis]
) -> Dict[str, Literal["LONG", "SHORT", "NEUTRAL"]]:
    return {
        tf: _vote_for_timeframe(ta.bias_score)
        for tf, ta in timeframe_analyses.items()
    }


# ---------------------------------------------------------------------------
# B. Weighted Vote Confluence
# ---------------------------------------------------------------------------

def _compute_vote_confluence(
    votes: Dict[str, Literal["LONG", "SHORT", "NEUTRAL"]],
    weights: Dict[str, float],
) -> Tuple[Literal["LONG", "SHORT", "NONE"], float, float]:
    """
    Returns (direction, long_ratio, short_ratio).
    direction is NONE when neither camp clears the confluence threshold.
    """
    long_weight = 0.0
    short_weight = 0.0
    total_weight = 0.0

    for tf, vote in votes.items():
        w = weights.get(tf, 0.0)
        total_weight += w
        if vote == "LONG":
            long_weight += w
        elif vote == "SHORT":
            short_weight += w

    if total_weight == 0:
        return "NONE", 0.0, 0.0

    long_ratio = long_weight / total_weight
    short_ratio = short_weight / total_weight
    threshold = settings.VOTE_CONFLUENCE_THRESHOLD

    if long_ratio >= threshold:
        return "LONG", long_ratio, short_ratio
    elif short_ratio >= threshold:
        return "SHORT", long_ratio, short_ratio
    return "NONE", long_ratio, short_ratio


# ---------------------------------------------------------------------------
# C. Confidence Scoring
# ---------------------------------------------------------------------------

def _score_structure_recency(tf_analysis: TimeframeAnalysis, lookback: int = 10) -> float:
    """
    0.15 if a BOS or CHoCH was confirmed in the last `lookback` events, else 0.
    We can't directly check candle index from here, so we use the count of
    recent structure events as a proxy (last event present = recency flag).
    """
    events = tf_analysis.structure_events
    if not events:
        return 0.0
    # The analysis pipeline appends events chronologically; last one is most recent.
    return 0.15


def _score_zone_alignment(
    tf_analysis: TimeframeAnalysis,
    direction: Literal["LONG", "SHORT", "NONE"],
) -> float:
    """
    0.15 if there is at least one unmitigated aligned FVG or Order Block, else 0.
    """
    if direction == "NONE":
        return 0.0

    if direction == "LONG":
        has_fvg = any(f for f in tf_analysis.fvgs if f.type == "BULLISH" and not f.is_mitigated)
        has_ob = any(o for o in tf_analysis.order_blocks if o.type == "BULLISH" and not o.is_mitigated)
    else:
        has_fvg = any(f for f in tf_analysis.fvgs if f.type == "BEARISH" and not f.is_mitigated)
        has_ob = any(o for o in tf_analysis.order_blocks if o.type == "BEARISH" and not o.is_mitigated)

    return 0.15 if (has_fvg or has_ob) else 0.0


def _compute_confidence_score(
    aggregate_bias_score: float,
    long_ratio: float,
    short_ratio: float,
    direction: Literal["LONG", "SHORT", "NONE"],
    primary_tf_analysis: Optional[TimeframeAnalysis],
) -> float:
    """
    Four sub-factors summed to [0.0, 1.0]:
      - Bias magnitude  × 0.40
      - Vote confluence × 0.30
      - Structure event recency × 0.15
      - Aligned zone present × 0.15
    """
    # Sub-factor 1: |aggregate_bias_score| scaled to 0.40
    bias_factor = min(abs(aggregate_bias_score), 1.0) * 0.40

    # Sub-factor 2: vote confluence ratio
    confluence_ratio = long_ratio if direction == "LONG" else short_ratio if direction == "SHORT" else 0.0
    vote_factor = confluence_ratio * 0.30

    # Sub-factors 3 & 4 require primary timeframe data
    structure_factor = 0.0
    zone_factor = 0.0
    if primary_tf_analysis is not None:
        structure_factor = _score_structure_recency(primary_tf_analysis)
        zone_factor = _score_zone_alignment(primary_tf_analysis, direction)

    raw = bias_factor + vote_factor + structure_factor + zone_factor
    return round(min(max(raw, 0.0), 1.0), 4)


# ---------------------------------------------------------------------------
# D. Liquidity / Imbalance No-Trade Conditions
# ---------------------------------------------------------------------------

def _check_no_trade_conditions(
    direction: Literal["LONG", "SHORT", "NONE"],
    current_price: float,
    primary_tf_analysis: Optional[TimeframeAnalysis],
) -> List[str]:
    """
    Returns a list of triggered no-trade condition strings.
    These are HARD overrides — any triggered condition → REJECT_HIGH_RISK.
    """
    conditions: List[str] = []

    if direction == "NONE" or primary_tf_analysis is None:
        return conditions

    # --- Condition 1: liquidity not yet swept on opposing side ---
    recent_sweeps = primary_tf_analysis.liquidity_sweeps[-5:]
    opposing_sweep_type = "BULLISH" if direction == "LONG" else "BEARISH"
    has_confirming_sweep = any(s.type == opposing_sweep_type for s in recent_sweeps)
    if not has_confirming_sweep:
        conditions.append("LIQUIDITY_NOT_SWEPT")

    # --- Condition 2: price inside an opposing unmitigated FVG without structural confirmation ---
    opposing_fvg_type = "BEARISH" if direction == "LONG" else "BULLISH"
    opposing_fvgs = [f for f in primary_tf_analysis.fvgs if f.type == opposing_fvg_type and not f.is_mitigated]
    price_in_opposing_fvg = any(f.bottom <= current_price <= f.top for f in opposing_fvgs)

    if price_in_opposing_fvg:
        # Only flag if there is no confirming BOS in the trade direction after the imbalance
        confirming_bos_type = "BULLISH" if direction == "LONG" else "BEARISH"
        has_confirming_bos = any(
            e.direction == confirming_bos_type
            for e in primary_tf_analysis.structure_events
        )
        if not has_confirming_bos:
            conditions.append("PRICE_IN_OPPOSING_IMBALANCE")

    return conditions


# ---------------------------------------------------------------------------
# E. Stop Loss Calculation (structure-first, prefer OB when cleaner & closer)
# ---------------------------------------------------------------------------

def _calculate_stop_loss(
    direction: Literal["LONG", "SHORT"],
    current_price: float,
    primary_tf_analysis: TimeframeAnalysis,
) -> Tuple[float, Literal["SWING", "ORDER_BLOCK"]]:
    """
    Returns (stop_loss_price, source_label).
    Logic:
      1. Find nearest unmitigated opposing swing level.
      2. Find nearest unmitigated opposing Order Block boundary.
      3. If the OB boundary is closer to current_price AND within 3× the swing distance, prefer OB.
    """
    buffer = settings.STOP_LOSS_BUFFER_PCT

    swing_sl: Optional[float] = None
    ob_sl: Optional[float] = None

    if direction == "LONG":
        # SL below price — find highest unbroken swing low below current price
        lows = [
            s.price for s in primary_tf_analysis.swings
            if s.type == "LOW" and not s.is_broken and s.price < current_price
        ]
        if lows:
            swing_sl = max(lows) * (1 - buffer)

        # OB: highest bullish OB bottom below price (price may tap into it)
        obs = [
            o.bottom for o in primary_tf_analysis.order_blocks
            if o.type == "BULLISH" and not o.is_mitigated and o.bottom < current_price
        ]
        if obs:
            ob_sl = max(obs) * (1 - buffer)

    else:  # SHORT
        # SL above price — find lowest unbroken swing high above current price
        highs = [
            s.price for s in primary_tf_analysis.swings
            if s.type == "HIGH" and not s.is_broken and s.price > current_price
        ]
        if highs:
            swing_sl = min(highs) * (1 + buffer)

        # OB: lowest bearish OB top above price
        obs = [
            o.top for o in primary_tf_analysis.order_blocks
            if o.type == "BEARISH" and not o.is_mitigated and o.top > current_price
        ]
        if obs:
            ob_sl = min(obs) * (1 + buffer)

    # Decision: prefer OB if it exists and is closer (tighter) than swing
    if swing_sl is not None and ob_sl is not None:
        swing_dist = abs(current_price - swing_sl)
        ob_dist = abs(current_price - ob_sl)
        # Prefer OB if it's cleaner (closer) but not implausibly tight (< 0.3× swing dist)
        if ob_dist < swing_dist and ob_dist >= 0.3 * swing_dist:
            return round(ob_sl, 6), "ORDER_BLOCK"
        return round(swing_sl, 6), "SWING"

    if ob_sl is not None:
        return round(ob_sl, 6), "ORDER_BLOCK"
    if swing_sl is not None:
        return round(swing_sl, 6), "SWING"

    # Fallback: fixed percentage stop
    fallback_pct = 0.015  # 1.5%
    if direction == "LONG":
        return round(current_price * (1 - fallback_pct), 6), "SWING"
    return round(current_price * (1 + fallback_pct), 6), "SWING"


# ---------------------------------------------------------------------------
# F. Take Profit Calculation (structure-first, fallback to fixed R)
# ---------------------------------------------------------------------------

def _calculate_take_profits(
    direction: Literal["LONG", "SHORT"],
    entry: float,
    stop_loss: float,
    primary_tf_analysis: TimeframeAnalysis,
) -> Tuple[float, Literal["FVG", "ORDER_BLOCK", "FIXED_2R"], float]:
    """
    Returns (tp1_price, tp1_source, tp2_price).
    TP1: nearest aligned FVG midpoint or OB boundary, fallback to 2R.
    TP2: always fixed 3R from entry.
    """
    risk = abs(entry - stop_loss)
    fixed_2r = entry + (risk * settings.TAKE_PROFIT_FIXED_R) if direction == "LONG" else entry - (risk * settings.TAKE_PROFIT_FIXED_R)
    fixed_3r = entry + (risk * settings.TAKE_PROFIT_2_R) if direction == "LONG" else entry - (risk * settings.TAKE_PROFIT_2_R)

    tp1_candidates: List[Tuple[float, str]] = []

    if direction == "LONG":
        # FVG: nearest bullish FVG midpoint above entry
        for fvg in primary_tf_analysis.fvgs:
            if fvg.type == "BULLISH" and not fvg.is_mitigated:
                mid = (fvg.top + fvg.bottom) / 2
                if mid > entry:
                    tp1_candidates.append((mid, "FVG"))

        # OB: nearest bearish OB bottom above entry (price is likely to react there)
        for ob in primary_tf_analysis.order_blocks:
            if ob.type == "BEARISH" and not ob.is_mitigated and ob.bottom > entry:
                tp1_candidates.append((ob.bottom, "ORDER_BLOCK"))

        # Take the closest target above entry (most conservative, highest probability)
        valid = [(p, src) for p, src in tp1_candidates if p > entry]
        if valid:
            best_p, best_src = min(valid, key=lambda x: x[0])
            rr = (best_p - entry) / risk if risk > 0 else 0
            # Only use structure target if it achieves at least minimum R:R
            if rr >= settings.MIN_RISK_REWARD:
                return round(best_p, 6), best_src, round(fixed_3r, 6)  # type: ignore[return-value]

    else:  # SHORT
        for fvg in primary_tf_analysis.fvgs:
            if fvg.type == "BEARISH" and not fvg.is_mitigated:
                mid = (fvg.top + fvg.bottom) / 2
                if mid < entry:
                    tp1_candidates.append((mid, "FVG"))

        for ob in primary_tf_analysis.order_blocks:
            if ob.type == "BULLISH" and not ob.is_mitigated and ob.top < entry:
                tp1_candidates.append((ob.top, "ORDER_BLOCK"))

        valid = [(p, src) for p, src in tp1_candidates if p < entry]
        if valid:
            best_p, best_src = max(valid, key=lambda x: x[0])
            rr = (entry - best_p) / risk if risk > 0 else 0
            if rr >= settings.MIN_RISK_REWARD:
                return round(best_p, 6), best_src, round(fixed_3r, 6)  # type: ignore[return-value]

    # Fallback: fixed 2R
    return round(fixed_2r, 6), "FIXED_2R", round(fixed_3r, 6)


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def evaluate_trade_decision(
    analysis: MarketStateAnalysisResponse,
    current_price: float,
) -> TradeDecision:
    """
    Full 8-step pipeline converting a MarketStateAnalysisResponse into a TradeDecision.
    """
    logger.info(f"Evaluating trade decision for {analysis.symbol} @ {current_price}")

    weights = settings.TIMEFRAME_WEIGHTS
    primary_tf = settings.PRIMARY_TIMEFRAME
    tf_analyses = analysis.timeframe_analyses

    primary_analysis = tf_analyses.get(primary_tf)

    # A. Per-timeframe voting
    votes = _compute_timeframe_votes(tf_analyses)

    # B. Weighted confluence
    direction, long_ratio, short_ratio = _compute_vote_confluence(votes, weights)

    # C. Confidence scoring
    confidence_score = _compute_confidence_score(
        analysis.aggregate_bias_score,
        long_ratio,
        short_ratio,
        direction,
        primary_analysis,
    )

    # D. No-trade conditions
    no_trade_conditions = _check_no_trade_conditions(direction, current_price, primary_analysis)

    # --- Build reason parts incrementally ---
    vote_summary = f"{sum(1 for v in votes.values() if v == direction)}/{len(votes)} TF votes {direction}"
    reason_parts: List[str] = [
        f"Aggregate bias: {analysis.aggregate_bias_score:+.3f}",
        f"Confidence: {confidence_score:.2f}",
        vote_summary,
    ]

    # F & G: Confidence threshold gating (before we try to compute risk profile)
    if direction == "NONE":
        return TradeDecision(
            symbol=analysis.symbol,
            decision="WAIT",
            direction="NONE",
            confidence_score=confidence_score,
            aggregate_bias_score=analysis.aggregate_bias_score,
            reason=f"No vote confluence ({long_ratio:.0%} long, {short_ratio:.0%} short). " + " | ".join(reason_parts),
            risk_profile=None,
            no_trade_conditions=no_trade_conditions,
            requires_manual_confirmation=False,
            timeframe_votes=votes,
            timestamp=datetime.now(timezone.utc),
        )

    if confidence_score < settings.MIN_CONFIDENCE_SCORE:
        return TradeDecision(
            symbol=analysis.symbol,
            decision="REJECT_LOW_CONFIDENCE",
            direction=direction,
            confidence_score=confidence_score,
            aggregate_bias_score=analysis.aggregate_bias_score,
            reason=f"Confidence {confidence_score:.2f} below minimum {settings.MIN_CONFIDENCE_SCORE}. " + " | ".join(reason_parts),
            risk_profile=None,
            no_trade_conditions=no_trade_conditions,
            requires_manual_confirmation=False,
            timeframe_votes=votes,
            timestamp=datetime.now(timezone.utc),
        )

    if confidence_score < settings.WAIT_CONFIDENCE_SCORE:
        return TradeDecision(
            symbol=analysis.symbol,
            decision="WAIT",
            direction=direction,
            confidence_score=confidence_score,
            aggregate_bias_score=analysis.aggregate_bias_score,
            reason=f"Confidence {confidence_score:.2f} in WAIT zone ({settings.MIN_CONFIDENCE_SCORE}–{settings.WAIT_CONFIDENCE_SCORE}). " + " | ".join(reason_parts),
            risk_profile=None,
            no_trade_conditions=no_trade_conditions,
            requires_manual_confirmation=False,
            timeframe_votes=votes,
            timestamp=datetime.now(timezone.utc),
        )

    # E. Risk profile (only computed when we have enough confidence to consider a trade)
    risk_profile: Optional[RiskProfile] = None
    rr_ratio = 0.0

    if primary_analysis is not None:
        sl_price, sl_source = _calculate_stop_loss(direction, current_price, primary_analysis)  # type: ignore[arg-type]
        tp1_price, tp1_source, tp2_price = _calculate_take_profits(direction, current_price, sl_price, primary_analysis)

        risk = abs(current_price - sl_price)
        reward = abs(tp1_price - current_price)
        rr_ratio = round(reward / risk, 4) if risk > 0 else 0.0

        risk_profile = RiskProfile(
            entry_price=current_price,
            stop_loss=sl_price,
            stop_loss_source=sl_source,
            take_profit_1=tp1_price,
            take_profit_1_source=tp1_source,
            take_profit_2=tp2_price,
            risk_reward_ratio=rr_ratio,
        )
        reason_parts.append(f"SL={sl_price} ({sl_source}) | TP1={tp1_price} ({tp1_source}) | R:R={rr_ratio:.2f}")

    # F. R:R gate
    if rr_ratio < settings.MIN_RISK_REWARD and primary_analysis is not None:
        return TradeDecision(
            symbol=analysis.symbol,
            decision="REJECT_HIGH_RISK",
            direction=direction,
            confidence_score=confidence_score,
            aggregate_bias_score=analysis.aggregate_bias_score,
            reason=f"R:R {rr_ratio:.2f} below minimum {settings.MIN_RISK_REWARD}. " + " | ".join(reason_parts),
            risk_profile=risk_profile,
            no_trade_conditions=no_trade_conditions,
            requires_manual_confirmation=False,
            timeframe_votes=votes,
            timestamp=datetime.now(timezone.utc),
        )

    # I. No-trade hard override (always last check before approval)
    if no_trade_conditions:
        return TradeDecision(
            symbol=analysis.symbol,
            decision="REJECT_HIGH_RISK",
            direction=direction,
            confidence_score=confidence_score,
            aggregate_bias_score=analysis.aggregate_bias_score,
            reason=f"Hard no-trade conditions triggered: {', '.join(no_trade_conditions)}. " + " | ".join(reason_parts),
            risk_profile=risk_profile,
            no_trade_conditions=no_trade_conditions,
            requires_manual_confirmation=False,
            timeframe_votes=votes,
            timestamp=datetime.now(timezone.utc),
        )

    # H. Manual confirmation flag
    requires_manual = confidence_score < settings.AUTO_EXECUTE_CONFIDENCE
    decision_str = f"APPROVE_{direction}"  # "APPROVE_LONG" or "APPROVE_SHORT"

    reason_parts.insert(0, f"✅ {decision_str}")
    if requires_manual:
        reason_parts.append(f"Manual confirmation required (confidence {confidence_score:.2f} < {settings.AUTO_EXECUTE_CONFIDENCE})")

    logger.info(f"Decision for {analysis.symbol}: {decision_str} (confidence={confidence_score:.2f}, R:R={rr_ratio:.2f})")

    return TradeDecision(
        symbol=analysis.symbol,
        decision=decision_str,  # type: ignore[arg-type]
        direction=direction,
        confidence_score=confidence_score,
        aggregate_bias_score=analysis.aggregate_bias_score,
        reason=" | ".join(reason_parts),
        risk_profile=risk_profile,
        no_trade_conditions=[],
        requires_manual_confirmation=requires_manual,
        timeframe_votes=votes,
        timestamp=datetime.now(timezone.utc),
    )
