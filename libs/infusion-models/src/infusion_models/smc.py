"""Shared SMC (Smart Money Concepts) pure-function helpers -- promoted
here 2026-08-27 after the third service (api/routes/scanner.py, for
the Sniper HUD's probabilistic display) needed the identical
Order-Block/FVG-distance and order-flow-divergence logic that
scanner/scoring.py and api/trade_blueprint.py had each been carrying
their own hand-duplicated copy of. Two verbatim copies matched this
codebase's own established cross-service-duplication precedent (no
shared import path between separate deployable images except libs/);
a third was the actual trigger to consolidate instead of copying
again -- libs/ IS that shared import path, so there was no reason left
not to use it.

Both functions read the SAME feature dict shape everywhere they're
called: feature_engine's own ml_features (scanner's
candidate.features_snapshot, api's infusion:feature:{symbol} hash
decoded to a dict) -- see feature_engine/features/ict.py and
microstructure.py for where order_block_*/fvg_*/book_imbalance* are
actually computed, and scanner/scoring.py's own module docstring for
why book_imbalance is the honest substitute for true CVD (this
pipeline's real Upstox feed has no per-trade buy/sell tag, only
resting tbq/tsq totals).
"""

from __future__ import annotations

from typing import Any

ORDER_FLOW_DOMINANCE_THRESHOLD = 0.05
SQUEEZE_STATES = frozenset({"EXTREME", "COILED", "BUILDING"})
BB_WIDTH_COMPRESSED_MAX = 0.02

# "Probabilistic Grading and Warning Tags" revision (2026-08-27): the
# LATE_ENTRY line replaces what used to be scanner/suppression.py's
# REJECTED_CHASING_OB hard-rejection threshold -- same number, now a
# disclosed warning instead of a hidden rejection.
LATE_ENTRY_MAX_PCT = 0.75
POOR_RR_MIN = 1.5


def _as_float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    return value in (True, "True", "true", "1", 1)


def nearest_ob_or_fvg_level(features: dict[str, Any], bearish: bool) -> float | None:
    """The nearest validated, direction-aligned Order Block or non-
    rebalanced Fair Value Gap's proximal line (the actual price level,
    not a distance) -- the edge price would touch first on a pullback
    into the zone. None when no such zone currently exists -- never a
    fabricated level.

    Timeframe disclosure: these zones are feature-engine's own
    1-minute OB/FVG state (feature_engine/features/ict.py) -- there is
    no 15m/1H equivalent in this pipeline (that would need
    structure.py's swing-pivot detection duplicated at each additional
    timeframe, a materially bigger separate piece of infrastructure).
    A 1m validated OB is still a real, close-confirmed institutional
    footprint, just a lower-timeframe one than an ideal build would use.
    """
    candidates: list[float] = []
    if not bearish:
        if _truthy(features.get("order_block_bullish_validated")):
            high = _as_float(features.get("order_block_bullish_high"))
            if high is not None:
                candidates.append(high)
        top = _as_float(features.get("fvg_bullish_top"))
        if top is not None:
            candidates.append(top)
    else:
        if _truthy(features.get("order_block_bearish_validated")):
            low = _as_float(features.get("order_block_bearish_low"))
            if low is not None:
                candidates.append(low)
        bottom = _as_float(features.get("fvg_bearish_bottom"))
        if bottom is not None:
            candidates.append(bottom)

    if not candidates:
        return None
    ltp = _as_float(features.get("ltp")) or 0.0
    if ltp <= 0:
        return candidates[0]
    return min(candidates, key=lambda z: abs(z - ltp))


def nearest_ob_or_fvg_distance_pct(features: dict[str, Any], bearish: bool) -> float | None:
    """% distance from LTP to nearest_ob_or_fvg_level's own result --
    see that function's docstring for the zone-selection rule and
    timeframe disclosure. None when there's no zone or no live LTP to
    measure the distance from, never a fabricated 0.0."""
    ltp = _as_float(features.get("ltp")) or 0.0
    if ltp <= 0:
        return None
    level = nearest_ob_or_fvg_level(features, bearish)
    if level is None:
        return None
    return abs(level - ltp) / ltp * 100.0


def is_price_compressed(features: dict[str, Any]) -> bool:
    squeeze_state = str(features.get("squeeze_state") or "").upper()
    bb_width = _as_float(features.get("bb_width"))
    compressed_by_bb = bb_width is not None and 0 < bb_width <= BB_WIDTH_COMPRESSED_MAX
    return squeeze_state in SQUEEZE_STATES or compressed_by_bb


def order_flow_divergence(features: dict[str, Any], bearish: bool) -> bool:
    """"Hidden accumulation/distribution": order-BOOK pressure
    (book_imbalance pulling away from its own slower EMA -- the raw
    reading leading its trend) building in the signal's direction
    while price is still compressed. NOT executed-trade CVD -- see
    this module's own docstring for why true CVD isn't available from
    this pipeline's real feed.
    """
    imbalance = _as_float(features.get("book_imbalance"))
    imbalance_ema = _as_float(features.get("book_imbalance_ema"))
    if imbalance is None or imbalance_ema is None:
        return False
    if not is_price_compressed(features):
        return False
    pressure_building = (imbalance - imbalance_ema) > 0 if not bearish else (
        imbalance - imbalance_ema
    ) < 0
    side_dominant = (
        imbalance > ORDER_FLOW_DOMINANCE_THRESHOLD
        if not bearish
        else imbalance < -ORDER_FLOW_DOMINANCE_THRESHOLD
    )
    return pressure_building and side_dominant


def structural_invalidation(
    *,
    bullish: bool,
    ltp: float,
    support: float | None,
    resistance: float | None,
    channel_lower: float | None,
    channel_upper: float | None,
) -> list[str]:
    """ "Terminal Edge & Analyst" sprint's Fast Exit Logic (2026-08-27),
    promoted here (2026-08-27, "Broker Sync & Active Position
    Intelligence") after a second real consumer needed the identical
    rule api/trade_blueprint.py's build_trade_blueprint() had inline --
    broker_sync.py's Reversal & Invalidation Watch checks a real broker
    position the exact same way trade_blueprint.py already checks a
    real journal position, so this is the shared, single-sourced rule
    both call now instead of a second hand-copy.

    STRUCTURAL_BREAK = the wider Donchian channel bound gave way (the
    more extreme "something bigger changed" read); FAST_EXIT = the
    nearer HTF support/resistance gave way (the more tactical, closer-
    to-price read). Both are independent checks -- either, neither, or
    both can fire. Any bound that's None (no data yet) is simply
    skipped, never treated as broken.
    """
    tags: list[str] = []
    if bullish:
        if channel_lower is not None and ltp < channel_lower:
            tags.append("STRUCTURAL_BREAK")
        if support is not None and ltp < support:
            tags.append("FAST_EXIT")
    else:
        if channel_upper is not None and ltp > channel_upper:
            tags.append("STRUCTURAL_BREAK")
        if resistance is not None and ltp > resistance:
            tags.append("FAST_EXIT")
    return tags


def compute_warning_tags(
    ob_fvg_distance_pct: float | None, risk_reward_ratio: float | None
) -> list[str]:
    """"Probabilistic Grading and Warning Tags" (2026-08-27): honest,
    human-readable flags surfaced alongside a setup instead of hard-
    suppressing it. LATE_ENTRY fires at the exact distance
    REJECTED_CHASING_OB used to hard-reject at -- same threshold, now
    disclosed rather than hidden. The R:R tag's wording matches the
    spec's own example string exactly ("R:R < 1:1.5").
    """
    tags: list[str] = []
    if ob_fvg_distance_pct is not None and ob_fvg_distance_pct > LATE_ENTRY_MAX_PCT:
        tags.append("LATE_ENTRY")
    if risk_reward_ratio is not None and 0 < risk_reward_ratio < POOR_RR_MIN:
        tags.append("R:R < 1:1.5")
    return tags
