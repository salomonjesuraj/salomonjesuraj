"""Conviction/win-probability scoring engine -- SMC Inception model,
now in its PROBABILISTIC form (2026-08-27, second revision same day).

The first revision of this model (see git history) hard-suppressed
anything more than 1.5% from an Order Block/FVG and hard-rejected
anything past 0.75% via a dedicated gate (REJECTED_CHASING_OB). That
gate is now REMOVED entirely: the design shifted from "hard suppression"
to "probabilistic grading + warning tags" -- the trader is the final
executor, and the system's job is to compute an honest win-probability
estimate and flag risk, not to make the entry/no-entry decision itself
by hiding setups outright. This is an explicit, disclosed philosophy
change, not a data-driven calibration (no backtest/paper-validation
window informed it) -- see the commit message for the full context.

Concretely, this changes two things from the first revision:
  1. Order Block/FVG proximity now decays SMOOTHLY and never hits a
     hard floor of zero while a real zone exists -- a setup 3% away
     from its zone still gets some structural credit, just much less
     than one sitting right at the base.
  2. A new HTF Momentum Continuation component (Marubozu/Engulfing on
     1H/Daily, api/routes/mtf.py's own per-timeframe candle read) can
     independently carry a moderately-extended setup up into the
     70-85 range even when OB/FVG proximity alone is weak -- exactly
     the "slightly extended but highly backed" case this revision was
     built for. OI-buildup scoring is also decoupled from requiring a
     live squeeze, for the same reason: a continuation setup by
     definition has already left its squeeze/coiling phase.

Bucket allocation (100 pts total) -- weighted so that a "textbook
inception" setup (full OB/FVG proximity + everything else confirming)
reaches 90+, and the "slightly extended but highly backed" case the
Phase 2 spec calls out explicitly (weak-to-moderate OB/FVG proximity,
but a strong HTF momentum candle + real OI buildup + HTF alignment
carrying it) lands in the 70-85 band on its own, without needing full
SMC-structure credit -- verified by
tests/unit/test_smc_conviction_model.py's own worked example, not just
asserted:
  SMC Structure & Timing      (22): OB/FVG proximity (0-15, soft decay)
                                     + liquidity sweep (0-7)
  HTF Momentum Continuation   (28): 1H/Daily Marubozu or direction-
                                     aligned Engulfing -- weighted the
                                     heaviest of the four buckets
                                     specifically because this is the
                                     signal meant to be ABLE to carry a
                                     moderately-extended setup on its
                                     own, per the spec's own framing.
  Order Flow & OI             (30): order-flow divergence (0-10, still
                                     requires compression -- this
                                     specific signal genuinely measures
                                     pre-move accumulation, so it keeps
                                     its own precondition) + OI buildup
                                     (0-20, no longer squeeze-gated)
  MTF Alignment & LTF Trigger (20): HTF 1H/1D alignment (0-12) + CHoCH
                                     near the zone (0-8)

Two real data-availability findings carried over from the first
revision, still true and still disclosed:

1. Order Block / FVG / liquidity-sweep / CHoCH detection already
   exists (feature_engine/features/ict.py, structure.py) -- but only
   on 1-MINUTE bars. True 15m/1H OB tracking would need
   structure.py's swing-pivot detection duplicated at each additional
   timeframe (state.swing_high_1/swing_low_1 are themselves 1m-only),
   a materially larger, separate piece of infrastructure this revision
   still doesn't build.
2. True Cumulative Volume Delta isn't computable from this pipeline's
   real feed (checked ingestion/adapters/upstox_codec.py directly --
   Upstox carries tbq/tsq resting-order totals and 5-level depth,
   never a per-trade buy/sell tag). book_imbalance/book_imbalance_ema
   (feature_engine/features/microstructure.py) is the honest analogue
   used here, named "order-flow divergence" throughout, deliberately
   never "CVD".

HTF (1H/1D) trend alignment, HTF candle pattern, and OI-buildup context
live in OTHER services feature-engine's own ml_features never
touches (api/routes/mtf.py's infusion:mtf:{symbol} cache;
api/futures_queue.py's infusion:futures:{symbol} hash). engine.py's
_process_candidate() reads both (two cheap Redis GETs) and passes them
in as mtf_data/futures_data. Both are commonly absent for any given
symbol at any given moment (mtf_queue.py only keeps ~50/208 symbols
warm at once, per EBIE-KNOWN-GAPS.md 1.7) -- absence scores 0,
deliberately not neutral credit, since these points are meant to be
EARNED higher-timeframe confirmation.

Grade mapping (unchanged):
  85-100 → A+  (exceptional)
  70-84  → A   (strong)
  55-69  → B   (moderate)
  40-54  → C   (marginal)
  0-39   → D   (weak — typically suppressed)
"""

from __future__ import annotations

from typing import Any

from infusion_models.smc import (
    is_price_compressed,
    nearest_ob_or_fvg_distance_pct,
    order_flow_divergence,
)

CHASEABLE_GRADE_CAP = 69.0  # top of B — below the 70-point A threshold

# ── SMC Structure & Timing (22 pts) ──────────────────────────────────
OB_FVG_MAX_POINTS = 15.0
OB_FVG_FULL_CREDIT_PCT = 0.5  # within this %, full credit
# Soft decay, not a hard floor of zero: a setup this far from its own
# zone still keeps OB_FVG_FLOOR_POINTS of structural credit rather than
# being zeroed out -- the whole point of the probabilistic revision is
# that distance alone no longer disqualifies a setup, other components
# (HTF momentum, OI, MTF) can still carry it.
OB_FVG_DECAY_TO_PCT = 5.0
OB_FVG_FLOOR_POINTS = 5.0
LIQUIDITY_SWEEP_POINTS = 7.0

# ── HTF Momentum Continuation (28 pts) ───────────────────────────────
# Weighted the heaviest of the four buckets deliberately -- this is the
# signal meant to be ABLE to carry a moderately-extended setup on its
# own into the 70-85 band, per the spec's own "slightly extended but
# highly backed" framing.
HTF_MOMENTUM_POINTS = 28.0
_MARUBOZU = {"Bullish Marubozu": "bullish", "Bearish Marubozu": "bearish"}
_ENGULFING = {"Bullish Engulfing": "bullish", "Bearish Engulfing": "bearish"}

# ── Order Flow & OI (30 pts) ─────────────────────────────────────────
ORDER_FLOW_DIVERGENCE_POINTS = 10.0
OI_BUILDUP_POINTS = 20.0

# ── MTF Alignment & LTF Trigger (20 pts) ─────────────────────────────
HTF_ALIGNMENT_FULL_POINTS = 12.0
HTF_ALIGNMENT_PARTIAL_POINTS = 6.0
CHOCH_TRIGGER_POINTS = 8.0
# CHoCH only counts as an LTF trigger "inside the HTF Order Block" if
# price is still within this distance of the zone.
CHOCH_MAX_OB_DISTANCE_PCT = 1.5

_CHOCH_ALIGNED = {"bullish": "Bullish CHOCH", "bearish": "Bearish CHOCH"}


def _liquidity_sweep_score(features: dict[str, Any], bearish: bool) -> float:
    """A sellside sweep (wick below a swing low, close back above) is a
    bullish stop-hunt-then-reclaim; a buyside sweep is the bearish
    mirror -- feature_engine/features/ict.py's own state machine."""
    sweep = features.get("last_liquidity_sweep")
    wants = "buyside" if bearish else "sellside"
    return LIQUIDITY_SWEEP_POINTS if sweep == wants else 0.0


def smc_structure_score(features: dict[str, Any], bearish: bool) -> tuple[float, float | None]:
    """0-22: Order Block/FVG proximity (0-15, soft-decaying from full
    credit at 0.5% down to a 5-point floor at 5%+ -- never a hard zero
    while a real zone exists) + confirmed liquidity sweep (0-7).
    Returns (points, distance_pct) -- reused by the CHoCH trigger check
    and by the frontend's LATE_ENTRY warning tag, so every consumer
    agrees on the same number."""
    distance = nearest_ob_or_fvg_distance_pct(features, bearish)
    ob_points = 0.0
    if distance is not None:
        if distance <= OB_FVG_FULL_CREDIT_PCT:
            ob_points = OB_FVG_MAX_POINTS
        elif distance >= OB_FVG_DECAY_TO_PCT:
            ob_points = OB_FVG_FLOOR_POINTS
        else:
            span = OB_FVG_DECAY_TO_PCT - OB_FVG_FULL_CREDIT_PCT
            decay_frac = (OB_FVG_DECAY_TO_PCT - distance) / span
            ob_points = OB_FVG_FLOOR_POINTS + (OB_FVG_MAX_POINTS - OB_FVG_FLOOR_POINTS) * decay_frac
    return ob_points + _liquidity_sweep_score(features, bearish), distance


def htf_momentum_score(mtf_data: dict[str, Any] | None, bearish: bool) -> float:
    """0 or 28: a Marubozu or direction-aligned Engulfing candle on the
    1H or Daily timeframe -- api/routes/mtf.py's own per-timeframe
    _candle_pattern() read (already computed for every timeframe it
    scores, Marubozu detection added there alongside this rewrite).
    Absence (no mtf cache, or neither HTF candle qualifies) scores 0,
    not a guess."""
    if not mtf_data:
        return 0.0
    timeframes = mtf_data.get("timeframes") or {}
    wants = "bearish" if bearish else "bullish"
    for tf in ("1H", "1D"):
        candle = str((timeframes.get(tf) or {}).get("candle") or "")
        if _MARUBOZU.get(candle) == wants or _ENGULFING.get(candle) == wants:
            return HTF_MOMENTUM_POINTS
    return 0.0


def order_flow_divergence_score(features: dict[str, Any], bearish: bool) -> float:
    """0 or 10: see infusion_models.smc.order_flow_divergence for the
    honest book-imbalance-vs-its-own-EMA proxy this uses in place of
    true CVD. Still requires price compression -- this specific signal
    measures pre-move accumulation, which by definition only means
    something while the range is still tight."""
    return ORDER_FLOW_DIVERGENCE_POINTS if order_flow_divergence(features, bearish) else 0.0


def oi_buildup_score(futures_data: dict[str, Any] | None, bearish: bool) -> float:
    """0 or 20: direction-matched LONG_BUILDUP (bullish) / SHORT_BUILDUP
    (bearish) OI. No longer gated on a live squeeze (the probabilistic
    revision's own point: a continuation setup has already left its
    coiling phase by definition, so requiring one here would zero out
    exactly the setups this component is meant to validate). futures_data
    is infusion:futures:{symbol}'s own hash -- absent when that sweep
    hasn't reached this symbol yet this cycle; scores 0, not a guess."""
    if not futures_data:
        return 0.0
    oi_buildup = str(futures_data.get("oi_buildup") or "NEUTRAL")
    wants = "SHORT_BUILDUP" if bearish else "LONG_BUILDUP"
    return OI_BUILDUP_POINTS if oi_buildup == wants else 0.0


def htf_alignment_score(mtf_data: dict[str, Any] | None, bearish: bool) -> float:
    """0, 6, or 12: how many of 1H/1D agree with this direction.
    Absence scores 0 deliberately, not neutral credit -- see module
    docstring."""
    if not mtf_data:
        return 0.0
    timeframes = mtf_data.get("timeframes") or {}
    wants = "BEAR" if bearish else "BULL"
    agree = sum(1 for tf in ("1H", "1D") if (timeframes.get(tf) or {}).get("state") == wants)
    if agree >= 2:
        return HTF_ALIGNMENT_FULL_POINTS
    if agree == 1:
        return HTF_ALIGNMENT_PARTIAL_POINTS
    return 0.0


def choch_trigger_score(
    features: dict[str, Any], bearish: bool, ob_distance_pct: float | None
) -> float:
    """0 or 8: an immediate CHoCH firing while price is still inside/
    near the Order Block or FVG zone."""
    direction = "bearish" if bearish else "bullish"
    event = str(features.get("last_event_label") or "")
    if event != _CHOCH_ALIGNED[direction]:
        return 0.0
    if ob_distance_pct is None or ob_distance_pct > CHOCH_MAX_OB_DISTANCE_PCT:
        return 0.0
    return CHOCH_TRIGGER_POINTS


def compute_conviction(
    features: dict[str, Any],
    mtf_data: dict[str, Any] | None = None,
    futures_data: dict[str, Any] | None = None,
) -> tuple[float, dict[str, float]]:
    """Compute the win-probability score from features -- SMC Inception
    model, probabilistic revision.

    Args:
        features: feature snapshot dict from SignalCandidate
            (feature_engine's own per-tick ml_features).
        mtf_data: infusion:mtf:{symbol}'s cached payload, or None.
        futures_data: infusion:futures:{symbol}'s hash, or None.

    Returns:
        (total_score, sub_scores_dict)

    Contract: deterministic — same inputs → same score.
    """
    direction = str(features.get("direction") or "bullish").lower()
    bearish = direction == "bearish"
    sub_scores: dict[str, float] = {}

    smc_points, ob_distance_pct = smc_structure_score(features, bearish)
    sub_scores["smc_structure"] = round(smc_points, 2)
    if ob_distance_pct is not None:
        sub_scores["ob_fvg_distance_pct"] = round(ob_distance_pct, 3)

    htf_momentum_points = htf_momentum_score(mtf_data, bearish)
    sub_scores["htf_momentum"] = round(htf_momentum_points, 2)

    order_flow_points = order_flow_divergence_score(features, bearish)
    oi_points = oi_buildup_score(futures_data, bearish)
    sub_scores["order_flow_divergence"] = round(order_flow_points, 2)
    sub_scores["oi_buildup"] = round(oi_points, 2)

    htf_points = htf_alignment_score(mtf_data, bearish)
    choch_points = choch_trigger_score(features, bearish, ob_distance_pct)
    sub_scores["htf_alignment"] = round(htf_points, 2)
    sub_scores["choch_trigger"] = round(choch_points, 2)

    total = (
        smc_points + htf_momentum_points + order_flow_points + oi_points + htf_points + choch_points
    )
    sub_scores["technical_component"] = round(total, 2)

    # ── Risk overlays -- orthogonal to the scoring MODEL above,
    # unchanged by this revision: these penalize/cap on separate risk
    # grounds (anti-chase location/risk rules, accumulated rejection
    # reasons), not on "does the setup look institutional." ──
    if features.get("anti_chase_ok") is False:
        total -= 8.0
        sub_scores["anti_chase_penalty"] = -8.0
    rejection_reasons = features.get("rejection_reasons") or []
    if isinstance(rejection_reasons, list) and rejection_reasons:
        penalty = min(10.0, len(rejection_reasons) * 3.0)
        total -= penalty
        sub_scores["rejection_penalty"] = -penalty
    total = min(max(total, 0.0), 100.0)

    if features.get("chaseable") is False and total > CHASEABLE_GRADE_CAP:
        sub_scores["chaseable_cap_applied"] = round(CHASEABLE_GRADE_CAP - total, 2)
        total = CHASEABLE_GRADE_CAP

    return total, sub_scores


def grade_conviction(score: float) -> str:
    """Map conviction score to letter grade.

    Deterministic: same score → same grade.
    """
    if score >= 85:
        return "A+"
    elif score >= 70:
        return "A"
    elif score >= 55:
        return "B"
    elif score >= 40:
        return "C"
    else:
        return "D"


def compute_risk_reward(entry: float, invalidation: float, target: float) -> float:
    """Compute risk/reward ratio. Returns 0.0 if invalid."""
    if target < entry and invalidation > entry:
        risk = invalidation - entry
        reward = entry - target
    else:
        risk = entry - invalidation
        reward = target - entry
    if risk <= 0:
        return 0.0
    if reward <= 0:
        return 0.0
    return round(reward / risk, 2)


__all__ = [
    "CHASEABLE_GRADE_CAP",
    "choch_trigger_score",
    "compute_conviction",
    "compute_risk_reward",
    "grade_conviction",
    "htf_alignment_score",
    "htf_momentum_score",
    "is_price_compressed",
    "nearest_ob_or_fvg_distance_pct",
    "oi_buildup_score",
    "order_flow_divergence_score",
    "smc_structure_score",
]
