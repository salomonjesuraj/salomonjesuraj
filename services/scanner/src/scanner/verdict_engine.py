"""EBIE EB-8 -- Unified Verdict Engine (SHADOW MODE ONLY).

Per docs/EBIE-BLUEPRINT.md Section 8: synthesizes every evidence family
EBIE has built so far (EB-2 accumulation/CLV, EB-2 compression/VCP+
pullback-dry-up, EB-3 relative strength, EB-4 futures positioning, EB-5
dynamic options, EB-6 microstructure, EB-7 sentiment) into one decision
contract, instead of leaving a human to mentally combine 40+ indicator
fields.

Per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q3.5: "EB-8 subsumes the
existing alignment gate. Do not keep two independent permanent veto
systems." This module does NOT duplicate scanner/alignment.py's 8
existing families (market structure, candlestick, supply/demand zone,
ICT, ATR/Supertrend regime, MA regime, Donchian, Wyckoff SOS/SOW) --
it calls compute_signal_alignment(bullish=True, ...) ONCE to get each
family's ABSOLUTE bullish/bearish/unavailable read (agreeing-with-
bullish IS the absolute-bullish read; disagreeing IS absolute-bearish),
then adds 7 NEW families on top. Zero changes to alignment.py, zero
risk to the already-proven, currently-shipping mechanism.

Per Non-Negotiable Rules #7 ("No raw score shown as probability") and
#8 ("No two independent final verdict gates after EBIE promotion"):
this is SHADOW ONLY. bull_score/bear_score are evidence-family
AGREEMENT percentages (breadth of independent confirmation), not
probabilities -- no calibration exists yet (that's EB-10). This verdict
is computed and persisted for every candidate (published or suppressed)
but never gates, vetoes, or overrides the existing suppression pipeline
or alignment gate. Per Q3.5, both decisions are logged side by side
during shadow.

Disclosed scope boundary for this first pass: Market/Sector Context
(EB-3's market_sector_context_score) is NOT wired in here -- it's
currently only computed inline per-request inside api/routes/ticks.py's
_build_ticks(), never cached standalone the way mtf_cache/sentiment_cache/
futures_cache/options_dynamics_cache are. A real, cheap follow-up
(cache it once per sweep in `api`, same VIX-multiplier pattern), not
built in this pass to avoid unbounded prerequisite scope-creep --
surfaced honestly via this module's own "unavailable_families" list
rather than silently omitted.

Per Q3.4's explicit exclusion list ("Remove from primary verdict logic:
fixed PCR bullish/bearish thresholds... highest call/put OI as hard
resistance/support"): the options_positioning family below deliberately
does NOT use weighted_pcr's absolute LEVEL as a directional signal --
only wall STATE changes (strengthening/weakening) and migration flags,
which Q3.4 explicitly approves as new verdict inputs.

EBIE EB-15 Phase 4 (items 5+6 of the "EBIE Consolidation, Calibration
and Production Readiness" directive) -- two changes to this module's own
scoring core, done together since item 5 is naturally expressed as one
more weighted family rather than a separate multiplier mechanism:

1. Market/Sector Context (item 5) is now wired in as its own family
   (`market_context`), using RAW inputs cached by api/ebie_state_queue.py
   (see that module and KEY_MARKET_CONTEXT_PREFIX's own comments for why
   raw, not pre-biased). `_compute_directional_context()` below is a
   deliberate, self-contained duplication of api/market_context.py's own
   pure function -- scanner and api are separate services/containers
   with no shared-lib import path between them, matching the same
   cross-service duplication precedent already used elsewhere in this
   codebase (e.g. api/vcp.py's own "self-contained... to avoid a
   circular import" note).

2. Equal evidence voting is replaced with WEIGHTED evidence families
   (item 6). Previously every family counted as exactly 1 vote --
   the 8 existing alignment.py families (structure/candlestick/zone/
   ict/regime/ma_regime/donchian/wyckoff) could together contribute up
   to 8 votes to a single underlying concept (price structure), silently
   outweighing single-vote families like relative_strength or the new
   market_context even when the latter are more differentiated,
   higher-quality reads -- exactly the failure mode item 6 names
   ("equal voting lets weak or redundant indicators overpower high-
   quality evidence"). Fixed by giving the 8 alignment families a
   shared, capped cluster weight (STRUCTURE_CLUSTER_WEIGHT), split
   evenly among however many of the 8 are actually available that tick,
   and giving every other family its own fixed, capped weight
   (FAMILY_WEIGHTS) -- so no single family, and no single correlated
   cluster of families, can dominate the score regardless of how many
   sub-indicators happen to agree. Weights are this session's own
   calibration (the directive names the CATEGORIES needing weights, not
   exact numbers) -- disclosed, not hidden, and easy to retune later
   since they're one named table, not scattered magic numbers.

Also new: a `volume` family (RVOL, already computed by ticks.py/
features_snapshot, never wired into a verdict family before now).

Disclosed, NOT built in this pass: option tradeability (still no live
option-chain liquidity data reaches this module -- same gap EB-8's
original pass disclosed) and portfolio/risk constraints as a verdict
input (EB-11's portfolio_fit is computed AFTER compute_verdict() in
engine.py's own call order today; wiring it in as an advisory modifier
would need that ordering changed, scoped out of this pass to avoid an
unrelated behavior change to an already-shipped, working call sequence).
Bearish compression/distribution (item 7) is a separate, distinctly-
scoped follow-up -- see this phase's own commit message for what's
already symmetric today (CLV/accumulation, and all 8 alignment.py
families) versus what still needs new logic (VCP's genuinely bull-only-
by-construction compression read).

EBIE EB-15 Phase 5 (P4: Upgrade Option-Chain Intelligence, items 8+9):

Item 8's "demote static PCR/Max Pain" half was already true before this
phase touched anything -- verified, not assumed: grep confirmed
weighted_pcr only ever lands in features_snapshot (informational
display), never in a scoring function or candidate gate, and the
options_positioning family above has excluded PCR LEVEL/highest-OI-as-
support-resistance since EB-8 shipped (Q3.4). What item 8 actually
needed was the "required replacement" list's OI acceleration (the
second derivative of weighted PCR, api/options_analytics_v2.py's new
compute_pcr_acceleration()) -- velocity itself, and strike-wise wall-
state classification, already existed from EB-5.

Item 9 (option tradeability) closes a gap EB-8's own original pass
explicitly disclosed as out of scope ("option-quote/liquidity gates
need live option-chain data this module doesn't have"). That data
already exists -- api/routes/market.py's _upstox_option_context()
already computes a real execution_status (TRADE_READY/WAIT_CONTRACT/
CHAIN_PENDING/AVOID_CONTRACT) with real hard_blockers, already shown on
the dashboard's Option Basis panel (R6) -- it just never reached this
module's own hard-gate check. _hard_gates() now rejects a candidate
with a literal "OPTION_NOT_TRADEABLE" reason whenever real cached chain
data shows AVOID_CONTRACT, regardless of how strong the underlying
evidence looks -- directly matching the directive's own acceptance
criterion ("a strong underlying setup can still be rejected if the
option contract is untradeable"). Only acts on real, present cache data
(30s TTL, a genuine miss between sweeps is common and never treated as
a rejection) -- never fabricates tradeability from absence.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from scanner.alignment import compute_signal_alignment

# Per Q6.3's authorized temporary shadow bands -- NOT yet calibrated
# against real outcome data (that calibration work is EB-10's job).
VERDICT_BANDS = (
    (85, "ARMED_CANDIDATE"),
    (75, "READY"),
    (65, "PRE_BREAKOUT_WATCH"),
    (55, "DEVELOPING"),
    (0, "NO_EDGE"),
)

# EBIE EB-15 Phase 6 item 10's own required PRE-calibration display
# ("Use confidence bands until calibration is statistically valid"),
# same thresholds/labels as ebie_state_queue.py's own Phase 3
# lightweight-verdict confidence band -- one vocabulary across the whole
# EBIE surface, not two separately-tuned band systems for what's
# conceptually the same "how sure is this" read. This is still NOT a
# probability (Rule #7) -- see calibrated_probability below, which stays
# None until a real, sample-gated calibration exists (item 10's own
# "probability output is blocked unless calibration exists").
CONFIDENCE_BANDS = (
    (80, "VERY_HIGH"),
    (60, "HIGH"),
    (40, "MEDIUM"),
    (0, "LOW"),
)


def _confidence_band(score: float) -> str:
    for threshold, label in CONFIDENCE_BANDS:
        if score >= threshold:
            return label
    return "LOW"


# Q6.2's authorized DQ policy.
DQ_HARD_FAIL = 80
DQ_DEGRADED = 90

CLV_THRESHOLD = 0.15
MICROSTRUCTURE_THRESHOLD = 0.15
VCP_MIN_SCORE = 60
RVOL_BULL_THRESHOLD = 2.0  # matches trap_model.py's/breakout-radar's own "real volume" bar
MARKET_CONTEXT_NEUTRAL_BAND = 3.0  # +/- around the 50-neutral midpoint that counts as "no read"

# EBIE EB-15 Phase 4 item 6 -- weighted evidence families, replacing
# equal-vote counting. See this module's own docstring for the full
# reasoning. STRUCTURE_FAMILIES are alignment.py's 8 existing families,
# clustered under one capped weight; every other family gets its own
# fixed weight. Both tables sum to 100 -- "the weighted score" is a real
# 0-100 percentage of available, weighted evidence, not an arbitrary unit.
STRUCTURE_FAMILIES = frozenset(
    {
        "structure",
        "candlestick",
        "zone",
        "ict",
        "regime",
        "ma_regime",
        "donchian",
        "wyckoff",
    }
)
STRUCTURE_CLUSTER_WEIGHT = 25.0

FAMILY_WEIGHTS = {
    "accumulation": 8.0,
    "compression": 6.0,
    "relative_strength": 10.0,
    "market_context": 13.0,
    "microstructure": 8.0,
    "futures_positioning": 8.0,
    "options_positioning": 8.0,
    "sentiment": 8.0,
    "volume": 6.0,
}
# STRUCTURE_CLUSTER_WEIGHT + sum(FAMILY_WEIGHTS.values()) == 100 -- kept
# as an assertion, not just a comment, so a future edit that breaks the
# total fails loudly instead of silently shifting what "0-100" means.
assert STRUCTURE_CLUSTER_WEIGHT + sum(FAMILY_WEIGHTS.values()) == 100.0


def _direction_label(score_diff: float, threshold: float = 0.0) -> bool | None:
    if score_diff > threshold:
        return True
    if score_diff < -threshold:
        return False
    return None


def _accumulation_family(ml: dict[str, Any]) -> bool | None:
    """CLV (EB-2) -- persistent close-location pressure.

    EB-15 Phase 4 item 7 note: despite the family's name/key
    ("accumulation"), this read has ALWAYS been genuinely symmetric --
    CLV above +CLV_THRESHOLD is real accumulation (closes persistently
    near session highs), below -CLV_THRESHOLD is real DISTRIBUTION
    (closes persistently near session lows, i.e. selling pressure) --
    just never described that way. Previously mislabeled as if it only
    ever argued for a bullish thesis, which is not what the function
    itself does. FAMILY_DESCRIPTIONS below now names both directions
    explicitly, and this is the one item-7 fix landing in this same
    phase -- VCP's compression family (below) remains genuinely
    bull-only by construction and needs real new logic to mirror,
    scoped out as a separate follow-up (see this module's docstring)."""
    clv = ml.get("clv_ema")
    if clv is None:
        return None
    return _direction_label(clv, CLV_THRESHOLD)


def _compression_family(mtf_cache: dict[str, Any]) -> bool | None:
    """VCP (Phase 13.12) -- Minervini Stage-2 base quality. Inherently
    long-side by construction (a "good base" only argues FOR a bullish
    thesis, never for a bearish one) -- disclosed asymmetry: this
    family can only ever vote bullish or abstain, never bearish."""
    vcp = mtf_cache.get("vcp") or {}
    if not vcp.get("reliable"):
        return None
    score = vcp.get("score")
    if score is None:
        return None
    return True if score >= VCP_MIN_SCORE else None


def _relative_strength_family(mtf_cache: dict[str, Any]) -> bool | None:
    """Multi-timeframe RS (EB-3) -- outperformance AND accelerating,
    not just a snapshot level."""
    rs = mtf_cache.get("multi_timeframe_rs") or {}
    if not rs.get("rs_available"):
        return None
    rs20, slope = rs.get("rs_20d"), rs.get("rs_slope_20d")
    if rs20 is None or slope is None:
        return None
    if rs20 > 0 and slope > 0:
        return True
    if rs20 < 0 and slope < 0:
        return False
    return None  # outperforming-but-fading / underperforming-but-improving -- genuinely mixed


def _microstructure_family(ml: dict[str, Any]) -> bool | None:
    """Book-imbalance EMA (EB-6) -- persistent order-book pressure."""
    depth = ml.get("microstructure_depth") or {}
    imbalance = depth.get("book_imbalance_ema")
    if imbalance is None:
        return None
    return _direction_label(imbalance, MICROSTRUCTURE_THRESHOLD)


def _futures_positioning_family(futures_cache: dict[str, Any]) -> bool | None:
    """EB-4 -- rising OI + rising premium (basis) reads as long
    buildup; rising OI + falling premium reads as short buildup. Flat/
    falling OI has no clear buildup interpretation either way."""
    basis_pct = futures_cache.get("basis_pct")
    oi_change_pct = futures_cache.get("oi_change_pct")
    if basis_pct is None or oi_change_pct is None or oi_change_pct <= 0:
        return None
    if basis_pct > 0:
        return True
    if basis_pct < 0:
        return False
    return None


def _options_positioning_family(options_dynamics_cache: dict[str, Any]) -> bool | None:
    """EB-5 -- wall STATE changes and migration only, never PCR level
    or "highest OI = hard support/resistance" (both explicitly excluded
    by Q3.4). Put wall strengthening or call wall weakening reads
    bullish; the mirror reads bearish. A migrated #1 wall on either
    side is itself inconclusive (a real structural shift, but not
    directional by construction) and is left for top_reasons/risks
    text rather than voting here."""
    wall = options_dynamics_cache.get("wall") or {}
    call_wall = (wall.get("call_wall") or [{}])[0]
    put_wall = (wall.get("put_wall") or [{}])[0]
    call_state = call_wall.get("state")
    put_state = put_wall.get("state")

    bullish_votes = (call_state == "weakening") + (put_state == "strengthening")
    bearish_votes = (call_state == "strengthening") + (put_state == "weakening")
    if bullish_votes and not bearish_votes:
        return True
    if bearish_votes and not bullish_votes:
        return False
    return None


def _sentiment_family(sentiment_cache: dict[str, Any]) -> bool | None:
    """EB-7's own already-composited, decay-weighted label -- reused
    as-is, not re-derived."""
    label = sentiment_cache.get("sentiment")
    if label == "BULLISH":
        return True
    if label == "BEARISH":
        return False
    return None


def _volume_family(rel_vol_20d: Any) -> bool | None:
    """EB-15 Phase 4 item 6 -- RVOL, already computed by ticks.py/
    features_snapshot for every candidate but never wired into a
    verdict family before now. Deliberately one-sided: real volume
    expansion argues FOR whichever direction the candidate already is
    (more participation behind a move, either way), but LOW volume is
    genuinely inconclusive, not evidence against -- quiet volume doesn't
    itself argue for the opposite direction, so this abstains rather
    than voting bearish-for-bullish-candidates on thin volume alone."""
    if rel_vol_20d is None:
        return None
    return True if float(rel_vol_20d) >= RVOL_BULL_THRESHOLD else None


def _compute_directional_context(
    *,
    is_sell_bias: bool,
    nifty_change_pct: float | None,
    sector_avg_change_pct: float | None,
    market_health_score: float | None,
) -> float | None:
    """Self-contained duplication of api/market_context.py's
    compute_directional_context() -- see this module's own docstring for
    why (no shared-lib import path between the scanner and api
    services). Weights/scales kept identical to the original so the two
    can never quietly diverge in behavior, only in that this version
    returns just the score (verdict_engine.py only ever needs the
    number, not the human-readable reasons -- those are reconstructed
    separately for top_reasons/risks via FAMILY_DESCRIPTIONS like every
    other family here)."""
    if nifty_change_pct is None and sector_avg_change_pct is None and market_health_score is None:
        return None
    score = 50.0
    nifty_weight, nifty_scale = 20.0, 8.0
    sector_weight, sector_scale = 15.0, 6.0
    breadth_weight = 0.3

    if nifty_change_pct is not None:
        supportive = (nifty_change_pct >= 0) if not is_sell_bias else (nifty_change_pct <= 0)
        delta = min(abs(nifty_change_pct) * nifty_scale, nifty_weight)
        score += delta if supportive else -delta

    if sector_avg_change_pct is not None:
        supportive = (
            (sector_avg_change_pct >= 0) if not is_sell_bias else (sector_avg_change_pct <= 0)
        )
        delta = min(abs(sector_avg_change_pct) * sector_scale, sector_weight)
        score += delta if supportive else -delta

    if market_health_score is not None:
        health_component = (
            market_health_score if not is_sell_bias else (100.0 - market_health_score)
        )
        score += (health_component - 50.0) * breadth_weight

    return round(max(0.0, min(100.0, score)), 1)


def _market_context_family(market_context_cache: dict[str, Any], bullish: bool) -> bool | None:
    """EB-15 Phase 4 item 5 -- market/sector/breadth context, read
    against THIS candidate's own posited direction (bullish param),
    never a separately-cached, potentially-mismatched bias -- see
    _compute_directional_context()'s own docstring. score > 50 means
    the broader market/sector genuinely supports this candidate's own
    direction; < 50 means it's fighting it (the directive's own "a
    strong stock setup in a hostile market should be downgraded" case);
    within MARKET_CONTEXT_NEUTRAL_BAND of 50 is a genuine no-read, not
    forced to a side."""
    score = _compute_directional_context(
        is_sell_bias=not bullish,
        nifty_change_pct=market_context_cache.get("nifty_change_pct"),
        sector_avg_change_pct=market_context_cache.get("sector_avg_change_pct"),
        market_health_score=market_context_cache.get("market_health_score"),
    )
    if score is None:
        return None
    if score > 50.0 + MARKET_CONTEXT_NEUTRAL_BAND:
        return bullish
    if score < 50.0 - MARKET_CONTEXT_NEUTRAL_BAND:
        return not bullish
    return None


NEW_FAMILY_SCORERS: dict[str, Callable[[dict[str, Any]], bool | None]] = {
    "accumulation": lambda ctx: _accumulation_family(ctx["ml"]),
    "compression": lambda ctx: _compression_family(ctx["mtf_cache"]),
    "relative_strength": lambda ctx: _relative_strength_family(ctx["mtf_cache"]),
    "microstructure": lambda ctx: _microstructure_family(ctx["ml"]),
    "futures_positioning": lambda ctx: _futures_positioning_family(ctx["futures_cache"]),
    "options_positioning": lambda ctx: _options_positioning_family(ctx["options_dynamics_cache"]),
    "sentiment": lambda ctx: _sentiment_family(ctx["sentiment_cache"]),
    "volume": lambda ctx: _volume_family(ctx["rel_vol_20d"]),
    # market_context is deliberately NOT in this dict -- every other
    # scorer here computes an ABSOLUTE bullish/bearish read, independent
    # of the candidate's own direction; market_context's read is
    # inherently relative to `bullish` (see _market_context_family's own
    # docstring), so it needs that parameter threaded through explicitly
    # in compute_verdict() below rather than fitting this ctx-only shape.
}

FAMILY_DESCRIPTIONS = {
    "structure": "market structure (BOS/CHOCH)",
    "candlestick": "candlestick pattern",
    "zone": "supply/demand zone",
    "ict": "ICT (FVG/order block/liquidity sweep)",
    "regime": "ATR/Supertrend regime",
    "ma_regime": "daily MA regime",
    "donchian": "Donchian fresh breakout",
    "wyckoff": "Wyckoff SOS/SOW",
    "accumulation": "close-location accumulation/distribution pressure",
    "compression": "volatility-contraction base quality",
    "relative_strength": "relative strength vs NIFTY, accelerating",
    "microstructure": "order-book imbalance pressure",
    "futures_positioning": "futures OI/basis buildup",
    "options_positioning": "option-chain wall dynamics",
    "sentiment": "news sentiment",
    "volume": "relative volume expansion",
    "market_context": "NIFTY/sector/breadth context",
}


def _hard_gates(
    *,
    fo_banned: bool,
    data_quality_score: float | None,
    entry_price: float,
    invalidation_price: float,
    tick_lag_ms: float | None,
    session_gap_ms: float | None,
    option_chain_context: dict[str, Any] | None,
) -> list[str]:
    """Per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q6.1's authorized hard-
    gate list. EB-15 Phase 5 item 9 closes the one gap that list's own
    original comment disclosed as out of scope: option-quote/liquidity
    gates now DO reach this module, via option_chain_context (the real,
    already-computed AVOID_CONTRACT/hard_blockers read from
    api/routes/market.py's _upstox_option_context() -- see
    engine.py's _fetch_option_chain_context_cache()). Only acts when
    real chain data is actually cached -- a missing/stale cache (30s
    TTL, genuinely common between sweeps) is never treated as a
    rejection, matching this whole module's "never fabricate" rule."""
    gates: list[str] = []
    if fo_banned:
        gates.append("F&O ban in effect")
    if data_quality_score is not None and data_quality_score < DQ_HARD_FAIL:
        gates.append(f"Data quality {data_quality_score} below hard-fail threshold {DQ_HARD_FAIL}")
    if not entry_price or not invalidation_price:
        gates.append("Missing trigger or invalidation price")
    # Session-boundary-aware fields (EB-0) -- a large gap right after
    # the session opens is expected (see feature-engine's own
    # session_gap_ms gating) and is NOT itself a hard failure signal
    # here; only genuinely excessive intra-session lag/gap is.
    if tick_lag_ms is not None and tick_lag_ms > 30_000:
        gates.append(f"Stale underlying quote (tick_lag_ms={tick_lag_ms})")
    if session_gap_ms is not None and session_gap_ms > 120_000:
        gates.append(f"Feed gap detected (session_gap_ms={session_gap_ms})")
    if (option_chain_context or {}).get("execution_status") == "AVOID_CONTRACT":
        blockers = (option_chain_context or {}).get("hard_blockers") or []
        detail = f" ({'; '.join(str(b) for b in blockers[:2])})" if blockers else ""
        gates.append(f"OPTION_NOT_TRADEABLE{detail}")
    return gates


def _verdict_band(directional_score: float) -> str:
    for threshold, label in VERDICT_BANDS:
        if directional_score >= threshold:
            return label
    return "NO_EDGE"


# EB-15 Phase 4 item 6's own acceptance criterion ("weights are
# configurable and versioned") -- a plain version string, bumped
# whenever FAMILY_WEIGHTS/STRUCTURE_CLUSTER_WEIGHT actually change, so a
# shadow-comparison report (EB-13) or archived signal row can always be
# read against the exact weighting scheme that produced it.
WEIGHTS_VERSION = "v1-2026-08-20"


def _family_weights(available_families: list[str]) -> dict[str, float]:
    """Per-family weight for THIS tick's actually-available family set --
    the structure cluster's fixed total is split evenly among however
    many of the 8 alignment.py families are available right now (2 of 8
    available still only ever contributes up to STRUCTURE_CLUSTER_WEIGHT
    combined, never more), so the cluster's own sub-indicator count can
    never inflate its share of the total score."""
    structure_available = [f for f in available_families if f in STRUCTURE_FAMILIES]
    per_structure_weight = (
        STRUCTURE_CLUSTER_WEIGHT / len(structure_available) if structure_available else 0.0
    )
    weights: dict[str, float] = {}
    for name in available_families:
        if name in STRUCTURE_FAMILIES:
            weights[name] = per_structure_weight
        else:
            weights[name] = FAMILY_WEIGHTS.get(name, 0.0)
    return weights


def compute_verdict(
    *,
    bullish: bool,
    ml: dict[str, Any],
    mtf_cache: dict[str, Any],
    sentiment_cache: dict[str, Any],
    futures_cache: dict[str, Any],
    options_dynamics_cache: dict[str, Any],
    market_context_cache: dict[str, Any],
    rel_vol_20d: Any,
    option_chain_context: dict[str, Any],
    ma_regime: dict[str, Any] | None,
    donchian: dict[str, Any] | None,
    wyckoff_sos_sow: dict[str, Any] | None,
    atr_trend: str,
    candle_pattern: str,
    entry_price: float,
    invalidation_price: float,
    fo_banned: bool,
    data_quality_score: float | None,
    tick_lag_ms: float | None,
    session_gap_ms: float | None,
    chaseable: bool,
) -> dict[str, Any]:
    """The Unified Verdict for one candidate. `bullish` is the
    candidate's OWN posited direction (BUY CE vs BUY PE) -- bull_score/
    bear_score are computed independently of it (so a genuinely
    conflicted setup, evidence on both sides, is visible), while
    top_reasons/risks and the verdict band are read relative to it.

    EB-15 Phase 4 item 6: bull_score/bear_score are now WEIGHTED
    percentages of available evidence (see _family_weights()), not raw
    family counts -- see this module's own docstring for the full
    reasoning.
    """
    ctx = {
        "ml": ml,
        "mtf_cache": mtf_cache,
        "sentiment_cache": sentiment_cache,
        "futures_cache": futures_cache,
        "options_dynamics_cache": options_dynamics_cache,
        "rel_vol_20d": rel_vol_20d,
    }

    # Reuse the proven 8-family alignment mechanism unmodified -- calling
    # it with bullish=True yields each family's ABSOLUTE bullish read
    # (agreeing-with-bullish == is-bullish), not a candidate-relative one.
    old_alignment = compute_signal_alignment(
        bullish=True,
        ml=ml,
        ma_regime=ma_regime,
        donchian=donchian,
        wyckoff_sos_sow=wyckoff_sos_sow,
        atr_trend=atr_trend,
        candle_pattern=candle_pattern,
    )
    absolute: dict[str, bool | None] = {}
    for name in old_alignment["alignment_agreeing_families"]:
        absolute[name] = True
    for name in old_alignment["alignment_disagreeing_families"]:
        absolute[name] = False
    # Families the old mechanism itself tracks but had no opinion on
    # this tick aren't in either list -- they're correctly absent from
    # `absolute` too (unavailable, not neutral).

    unavailable_families: list[str] = []
    for name, scorer in NEW_FAMILY_SCORERS.items():
        result = scorer(ctx)
        if result is None:
            unavailable_families.append(name)
        else:
            absolute[name] = result

    # market_context is scored separately -- it's the one family whose
    # read is inherently relative to `bullish`, not an absolute read
    # NEW_FAMILY_SCORERS' ctx-only shape can express (see
    # _market_context_family's own docstring).
    market_context_result = _market_context_family(market_context_cache, bullish)
    if market_context_result is None:
        unavailable_families.append("market_context")
    else:
        absolute["market_context"] = market_context_result

    checked = list(absolute.keys())
    bullish_families = [k for k, v in absolute.items() if v]
    bearish_families = [k for k, v in absolute.items() if not v]

    weights = _family_weights(checked)
    total_weight = sum(weights.values())
    bull_weight = sum(weights[f] for f in bullish_families)
    bear_weight = sum(weights[f] for f in bearish_families)
    bull_score = round(100 * bull_weight / total_weight, 1) if total_weight else 0.0
    bear_score = round(100 * bear_weight / total_weight, 1) if total_weight else 0.0

    directional_score = bull_score if bullish else bear_score
    supporting = bullish_families if bullish else bearish_families
    contradicting = bearish_families if bullish else bullish_families
    # Rank reasons/risks by their own WEIGHT, not dict insertion order --
    # the directive's own "verdict output exposes TOP positive/negative
    # evidence families" means the highest-weighted ones, not merely the
    # first ones that happened to be available.
    supporting_ranked = sorted(supporting, key=lambda f: weights.get(f, 0.0), reverse=True)
    contradicting_ranked = sorted(contradicting, key=lambda f: weights.get(f, 0.0), reverse=True)

    hard_gate_reasons = _hard_gates(
        fo_banned=fo_banned,
        data_quality_score=data_quality_score,
        entry_price=entry_price,
        invalidation_price=invalidation_price,
        tick_lag_ms=tick_lag_ms,
        session_gap_ms=session_gap_ms,
        option_chain_context=option_chain_context,
    )

    if hard_gate_reasons:
        band = "HARD_BLOCKED"
    else:
        band = _verdict_band(directional_score)
        # Q6.3: 85+ alone is NOT sufficient for ARMED_CANDIDATE -- also
        # requires trigger proximity (chaseable), DQ fully eligible
        # (>=90, not just clear of the hard-fail line), and a real
        # minimum BREADTH of evidence-family agreement (family COUNT,
        # deliberately separate from the weighted score above -- per
        # item 6's own "no single static indicator can create a
        # high-confidence verdict by itself", this catches the case a
        # capped weight scheme alone doesn't: a handful of low-weight
        # families combining to clear 85% of a thin available-evidence
        # base without genuinely broad confirmation).
        if band == "ARMED_CANDIDATE" and not (
            chaseable
            and (data_quality_score is None or data_quality_score >= DQ_DEGRADED)
            and len(supporting) >= 5
        ):
            band = "READY"

    return {
        "bull_score": bull_score,
        "bear_score": bear_score,
        "directional_score": directional_score,
        "families_checked": len(checked),
        "families_total": len(absolute) + len(unavailable_families),
        "bullish_families": bullish_families,
        "bearish_families": bearish_families,
        "unavailable_families": unavailable_families,
        "weights_version": WEIGHTS_VERSION,
        # EB-15 Phase 6 item 10 -- the required PRE-calibration display
        # ("use confidence bands until calibration is statistically
        # valid"). Read off directional_score (the candidate's OWN
        # direction), not the unconditional bull_score, since that's
        # what a reader actually cares about ("how sure is THIS setup").
        "confidence_band": _confidence_band(directional_score),
        "top_reasons": [FAMILY_DESCRIPTIONS.get(f, f) for f in supporting_ranked][:6],
        "risks": [FAMILY_DESCRIPTIONS.get(f, f) for f in contradicting_ranked][:4],
        "hard_gates": hard_gate_reasons,
        "verdict": band,
        # Per Non-Negotiable Rule #7 -- no fabricated probability until a
        # real calibration exists. See api/verdict_calibration.py /
        # GET /api/ebie/verdict-calibration (EB-15 Phase 6 item 10) for
        # the real, on-demand, sample-gated check -- still NOT_READY as
        # of this phase (far fewer than the required 300 episodes/25
        # sessions), so this stays None, correctly, not a placeholder.
        "calibrated_probability": None,
        "calibrated_probability_reason": "Not yet calibrated -- see GET /api/ebie/verdict-calibration (EB-15 Phase 6).",
    }
