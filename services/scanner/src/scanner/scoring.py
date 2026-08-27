"""Conviction scoring engine -- SMC Inception Conviction Model
(2026-08-27 rewrite).

The prior model (VWAP-distance/RSI/volume-spike/EMA-alignment, blended
with Pine confidence and a Strength Meter) is a LAGGING model: by the
time those inputs agree, the move has typically already run 3-4%,
turning every entry into a retail-style chase with poor risk/reward.
This tears that weighting out and replaces it with an INCEPTION model:
score the setup at its base -- proximity to an unmitigated Order Block
or Fair Value Gap, a confirmed liquidity sweep, order-flow pressure
building while price is still compressed, OI buildup during a squeeze,
and higher-timeframe/structural confirmation -- so a signal fires
where an entry can actually be taken, not after the fact.

Two real data-availability findings from building this, both disclosed
here rather than silently worked around:

1. Order Block / FVG / liquidity-sweep detection already exists
   (feature_engine/features/ict.py, wired into ml_features today) --
   but only on 1-MINUTE bars. There is no 15m/1H equivalent: that would
   need the swing-pivot detection in feature_engine/features/structure.py
   duplicated at each additional timeframe (state.swing_high_1/
   swing_low_1 are themselves 1m-only), a materially larger, separate
   piece of infrastructure than this scoring rewrite. This model scores
   against the real 1m zones -- still a genuine, close-confirmed
   institutional footprint, just a lower timeframe than originally
   asked for.
2. True Cumulative Volume Delta (buyer-initiated minus seller-initiated
   EXECUTED volume) is not something this pipeline can compute: checked
   ingestion/adapters/upstox_codec.py directly -- the real Upstox feed
   carries tbq/tsq (exchange-wide RESTING order quantity totals) and
   5-level market depth, never a per-trade buy/sell side tag. The
   honest analogue used here is book_imbalance / book_imbalance_ema
   (feature_engine/features/microstructure.py's own weighted depth-
   imbalance + its EMA, already computed, previously never wired into
   scoring) -- real order-BOOK pressure, not executed-trade delta.
   Named "order-flow divergence" throughout, deliberately not "CVD",
   so this distinction can't be lost in a later refactor.

HTF (1H/1D) trend alignment and OI-buildup context live in OTHER
services (api/routes/mtf.py's infusion:mtf:{symbol} cache;
api/futures_queue.py's infusion:futures:{symbol} hash) that
feature-engine's own per-tick ml_features never touches -- engine.py's
_process_candidate() now reads both (two cheap Redis GETs, the same
"read what's already computed" pattern used everywhere else in this
codebase) and passes them in as the optional mtf_data/futures_data
arguments below. Both are commonly absent for any given symbol at any
given moment (mtf_queue.py only keeps ~50/208 symbols warm at once,
per EBIE-KNOWN-GAPS.md 1.7) -- absence scores 0 for that component,
deliberately not neutral credit, since these points are meant to be
EARNED higher-timeframe confirmation, not assumed in the absence of
data.

Grade mapping (unchanged):
  85-100 → A+  (exceptional)
  70-84  → A   (strong)
  55-69  → B   (moderate)
  40-54  → C   (marginal)
  0-39   → D   (weak — typically suppressed)
"""

from __future__ import annotations

from typing import Any

CHASEABLE_GRADE_CAP = 69.0  # top of B — below the 70-point A threshold

# ── SMC Structure (35 pts) ───────────────────────────────────────────
OB_FVG_MAX_POINTS = 20.0
OB_FVG_FULL_CREDIT_PCT = 0.5  # within this %, full credit
OB_FVG_ZERO_CREDIT_PCT = 1.5  # at/beyond this %, zero credit -- this IS the anti-chase line
LIQUIDITY_SWEEP_POINTS = 15.0

# ── Order Flow & Derivatives (35 pts) ───────────────────────────────
ORDER_FLOW_DIVERGENCE_POINTS = 20.0
ORDER_FLOW_DOMINANCE_THRESHOLD = 0.05  # book_imbalance must actually lean this way, not just tick off zero
SQUEEZE_STATES = {"EXTREME", "COILED", "BUILDING"}
BB_WIDTH_COMPRESSED_MAX = 0.02
OI_SQUEEZE_POINTS = 15.0

# ── MTF Alignment & LTF Trigger (30 pts) ────────────────────────────
HTF_ALIGNMENT_FULL_POINTS = 15.0
HTF_ALIGNMENT_PARTIAL_POINTS = 8.0
CHOCH_TRIGGER_POINTS = 15.0
# CHoCH only counts as an LTF trigger "inside the HTF Order Block" if
# price is still within this distance of the zone -- reuses the same
# line the SMC Structure component itself decays to zero at, so a
# CHoCH that fires nowhere near a zone can't still earn LTF-trigger
# credit.
CHOCH_MAX_OB_DISTANCE_PCT = OB_FVG_ZERO_CREDIT_PCT

_CHOCH_ALIGNED = {"bullish": "Bullish CHOCH", "bearish": "Bearish CHOCH"}


def nearest_ob_or_fvg_distance_pct(features: dict[str, Any], bearish: bool) -> float | None:
    """% distance from LTP to the nearest validated, direction-aligned
    Order Block or non-rebalanced Fair Value Gap's proximal line (the
    edge price would touch first on a pullback into the zone). None
    when no such zone currently exists -- never a fabricated 0.0 that
    would misread as "sitting right at the zone."
    """
    ltp = features.get("ltp") or 0.0
    if ltp <= 0:
        return None

    candidates: list[float] = []
    if not bearish:
        if (
            features.get("order_block_bullish_validated")
            and features.get("order_block_bullish_high") is not None
        ):
            candidates.append(float(features["order_block_bullish_high"]))
        if features.get("fvg_bullish_top") is not None:
            candidates.append(float(features["fvg_bullish_top"]))
    else:
        if (
            features.get("order_block_bearish_validated")
            and features.get("order_block_bearish_low") is not None
        ):
            candidates.append(float(features["order_block_bearish_low"]))
        if features.get("fvg_bearish_bottom") is not None:
            candidates.append(float(features["fvg_bearish_bottom"]))

    if not candidates:
        return None
    nearest = min(candidates, key=lambda z: abs(z - ltp))
    return abs(nearest - ltp) / ltp * 100.0


def _liquidity_sweep_score(features: dict[str, Any], bearish: bool) -> float:
    """A sellside sweep (wick below a swing low, close back above) is a
    bullish stop-hunt-then-reclaim; a buyside sweep is the bearish
    mirror -- feature_engine/features/ict.py's own state machine."""
    sweep = features.get("last_liquidity_sweep")
    wants = "buyside" if bearish else "sellside"
    return LIQUIDITY_SWEEP_POINTS if sweep == wants else 0.0


def smc_structure_score(
    features: dict[str, Any], bearish: bool
) -> tuple[float, float | None]:
    """0-35: Order Block/FVG proximity (0-20, linearly decaying from
    full credit at 0.5% to zero at 1.5%) + confirmed liquidity sweep
    (0-15). Returns (points, distance_pct) -- the distance is reused by
    both the CHoCH trigger check below and the anti-chase suppression
    gate, so all three always agree on the same number."""
    distance = nearest_ob_or_fvg_distance_pct(features, bearish)
    ob_points = 0.0
    if distance is not None:
        if distance <= OB_FVG_FULL_CREDIT_PCT:
            ob_points = OB_FVG_MAX_POINTS
        elif distance < OB_FVG_ZERO_CREDIT_PCT:
            span = OB_FVG_ZERO_CREDIT_PCT - OB_FVG_FULL_CREDIT_PCT
            ob_points = OB_FVG_MAX_POINTS * (OB_FVG_ZERO_CREDIT_PCT - distance) / span
    return ob_points + _liquidity_sweep_score(features, bearish), distance


def _is_price_compressed(features: dict[str, Any]) -> bool:
    squeeze_state = str(features.get("squeeze_state") or "").upper()
    bb_width = features.get("bb_width")
    compressed_by_bb = bb_width is not None and 0 < float(bb_width) <= BB_WIDTH_COMPRESSED_MAX
    return squeeze_state in SQUEEZE_STATES or compressed_by_bb


def order_flow_divergence_score(features: dict[str, Any], bearish: bool) -> float:
    """0 or 20: order-BOOK pressure (see module docstring for why this
    isn't executed-trade CVD) building in the signal's direction while
    price is still compressed -- book_imbalance pulling away from its
    own slower EMA (the raw reading leading its trend) while a squeeze/
    tight-Bollinger state means price itself hasn't expanded yet. This
    IS "hidden accumulation": pressure building, price still flat.
    """
    imbalance = features.get("book_imbalance")
    imbalance_ema = features.get("book_imbalance_ema")
    if imbalance is None or imbalance_ema is None:
        return 0.0
    if not _is_price_compressed(features):
        return 0.0

    imbalance = float(imbalance)
    imbalance_ema = float(imbalance_ema)
    pressure_building = (imbalance - imbalance_ema) > 0 if not bearish else (
        imbalance - imbalance_ema
    ) < 0
    side_dominant = (
        imbalance > ORDER_FLOW_DOMINANCE_THRESHOLD
        if not bearish
        else imbalance < -ORDER_FLOW_DOMINANCE_THRESHOLD
    )
    return ORDER_FLOW_DIVERGENCE_POINTS if pressure_building and side_dominant else 0.0


def oi_squeeze_score(features: dict[str, Any], futures_data: dict[str, Any] | None, bearish: bool) -> float:
    """0 or 15: pre-breakout LONG_BUILDUP (bullish) / SHORT_BUILDUP
    (bearish) OI, specifically during a volatility squeeze -- OI
    conviction building while the range is still tight, not a buildup
    already accompanying an expanded move. futures_data is
    infusion:futures:{symbol}'s own hash (api/futures_queue.py) --
    absent when that sweep hasn't reached this symbol yet this cycle;
    scores 0, not a guess."""
    if not futures_data or not _is_price_compressed(features):
        return 0.0
    oi_buildup = str(futures_data.get("oi_buildup") or "NEUTRAL")
    wants = "SHORT_BUILDUP" if bearish else "LONG_BUILDUP"
    return OI_SQUEEZE_POINTS if oi_buildup == wants else 0.0


def htf_alignment_score(mtf_data: dict[str, Any] | None, bearish: bool) -> float:
    """0, 8, or 15: how many of 1H/1D agree with this direction.
    mtf_data is infusion:mtf:{symbol}'s cached payload (api/routes/
    mtf.py) -- absent for most symbols most of the time (see module
    docstring); absence scores 0 deliberately, not neutral credit."""
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


def choch_trigger_score(features: dict[str, Any], bearish: bool, ob_distance_pct: float | None) -> float:
    """0 or 15: an immediate CHoCH (feature_engine/features/structure.py's
    own BOS/CHOCH state machine, 1m) firing while price is still inside/
    near the Order Block or FVG zone -- a CHoCH far from any zone is a
    reversal signal on its own, but not the "reaction right at the
    base" this points bucket is meant to reward."""
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
    """Compute conviction score from features -- SMC Inception model.

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

    order_flow_points = order_flow_divergence_score(features, bearish)
    oi_points = oi_squeeze_score(features, futures_data, bearish)
    sub_scores["order_flow_divergence"] = round(order_flow_points, 2)
    sub_scores["oi_squeeze"] = round(oi_points, 2)

    htf_points = htf_alignment_score(mtf_data, bearish)
    choch_points = choch_trigger_score(features, bearish, ob_distance_pct)
    sub_scores["htf_alignment"] = round(htf_points, 2)
    sub_scores["choch_trigger"] = round(choch_points, 2)

    total = smc_points + order_flow_points + oi_points + htf_points + choch_points
    sub_scores["technical_component"] = round(total, 2)

    # ── Risk overlays -- orthogonal to the conviction MODEL above,
    # unchanged by this rewrite: these penalize/cap on separate risk
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
