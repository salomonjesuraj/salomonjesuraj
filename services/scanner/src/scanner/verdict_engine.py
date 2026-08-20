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
"""

from __future__ import annotations

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

# Q6.2's authorized DQ policy.
DQ_HARD_FAIL = 80
DQ_DEGRADED = 90

CLV_THRESHOLD = 0.15
MICROSTRUCTURE_THRESHOLD = 0.15
VCP_MIN_SCORE = 60


def _direction_label(score_diff: float, threshold: float = 0.0) -> bool | None:
    if score_diff > threshold:
        return True
    if score_diff < -threshold:
        return False
    return None


def _accumulation_family(ml: dict) -> bool | None:
    """CLV (EB-2) -- persistent close-location pressure."""
    clv = ml.get("clv_ema")
    if clv is None:
        return None
    return _direction_label(clv, CLV_THRESHOLD)


def _compression_family(mtf_cache: dict) -> bool | None:
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


def _relative_strength_family(mtf_cache: dict) -> bool | None:
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


def _microstructure_family(ml: dict) -> bool | None:
    """Book-imbalance EMA (EB-6) -- persistent order-book pressure."""
    depth = ml.get("microstructure_depth") or {}
    imbalance = depth.get("book_imbalance_ema")
    if imbalance is None:
        return None
    return _direction_label(imbalance, MICROSTRUCTURE_THRESHOLD)


def _futures_positioning_family(futures_cache: dict) -> bool | None:
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


def _options_positioning_family(options_dynamics_cache: dict) -> bool | None:
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


def _sentiment_family(sentiment_cache: dict) -> bool | None:
    """EB-7's own already-composited, decay-weighted label -- reused
    as-is, not re-derived."""
    label = sentiment_cache.get("sentiment")
    if label == "BULLISH":
        return True
    if label == "BEARISH":
        return False
    return None


NEW_FAMILY_SCORERS = {
    "accumulation": lambda ctx: _accumulation_family(ctx["ml"]),
    "compression": lambda ctx: _compression_family(ctx["mtf_cache"]),
    "relative_strength": lambda ctx: _relative_strength_family(ctx["mtf_cache"]),
    "microstructure": lambda ctx: _microstructure_family(ctx["ml"]),
    "futures_positioning": lambda ctx: _futures_positioning_family(ctx["futures_cache"]),
    "options_positioning": lambda ctx: _options_positioning_family(ctx["options_dynamics_cache"]),
    "sentiment": lambda ctx: _sentiment_family(ctx["sentiment_cache"]),
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
    "accumulation": "close-location accumulation pressure",
    "compression": "volatility-contraction base quality",
    "relative_strength": "relative strength vs NIFTY, accelerating",
    "microstructure": "order-book imbalance pressure",
    "futures_positioning": "futures OI/basis buildup",
    "options_positioning": "option-chain wall dynamics",
    "sentiment": "news sentiment",
}


def _hard_gates(
    *, fo_banned: bool, data_quality_score, entry_price, invalidation_price,
    tick_lag_ms, session_gap_ms,
) -> list[str]:
    """Per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q6.1's authorized hard-
    gate list -- only the subset checkable from data already available
    at verdict-compute time (option-quote/liquidity gates need live
    option-chain data this module doesn't have; explicitly not built
    here, disclosed rather than guessed at)."""
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
    return gates


def _verdict_band(directional_score: float) -> str:
    for threshold, label in VERDICT_BANDS:
        if directional_score >= threshold:
            return label
    return "NO_EDGE"


def compute_verdict(
    *,
    bullish: bool,
    ml: dict,
    mtf_cache: dict,
    sentiment_cache: dict,
    futures_cache: dict,
    options_dynamics_cache: dict,
    ma_regime: dict | None,
    donchian: dict | None,
    wyckoff_sos_sow: dict | None,
    atr_trend: str,
    candle_pattern: str,
    entry_price: float,
    invalidation_price: float,
    fo_banned: bool,
    data_quality_score,
    tick_lag_ms,
    session_gap_ms,
    chaseable: bool,
) -> dict:
    """The Unified Verdict for one candidate. `bullish` is the
    candidate's OWN posited direction (BUY CE vs BUY PE) -- bull_score/
    bear_score are computed independently of it (so a genuinely
    conflicted setup, evidence on both sides, is visible), while
    top_reasons/risks and the verdict band are read relative to it.
    """
    ctx = {
        "ml": ml, "mtf_cache": mtf_cache, "sentiment_cache": sentiment_cache,
        "futures_cache": futures_cache, "options_dynamics_cache": options_dynamics_cache,
    }

    # Reuse the proven 8-family alignment mechanism unmodified -- calling
    # it with bullish=True yields each family's ABSOLUTE bullish read
    # (agreeing-with-bullish == is-bullish), not a candidate-relative one.
    old_alignment = compute_signal_alignment(
        bullish=True, ml=ml, ma_regime=ma_regime, donchian=donchian,
        wyckoff_sos_sow=wyckoff_sos_sow, atr_trend=atr_trend, candle_pattern=candle_pattern,
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

    checked = list(absolute.keys())
    bullish_families = [k for k, v in absolute.items() if v]
    bearish_families = [k for k, v in absolute.items() if not v]

    total_checked = len(checked)
    bull_score = round(100 * len(bullish_families) / total_checked, 1) if total_checked else 0.0
    bear_score = round(100 * len(bearish_families) / total_checked, 1) if total_checked else 0.0

    directional_score = bull_score if bullish else bear_score
    supporting = bullish_families if bullish else bearish_families
    contradicting = bearish_families if bullish else bullish_families

    hard_gate_reasons = _hard_gates(
        fo_banned=fo_banned, data_quality_score=data_quality_score,
        entry_price=entry_price, invalidation_price=invalidation_price,
        tick_lag_ms=tick_lag_ms, session_gap_ms=session_gap_ms,
    )

    if hard_gate_reasons:
        band = "HARD_BLOCKED"
    else:
        band = _verdict_band(directional_score)
        # Q6.3: 85+ alone is NOT sufficient for ARMED_CANDIDATE -- also
        # requires trigger proximity (chaseable), DQ fully eligible
        # (>=90, not just clear of the hard-fail line), and a real
        # minimum breadth of evidence-family agreement.
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
        "families_checked": total_checked,
        "families_total": len(absolute) + len(unavailable_families),
        "bullish_families": bullish_families,
        "bearish_families": bearish_families,
        "unavailable_families": unavailable_families,
        "top_reasons": [FAMILY_DESCRIPTIONS.get(f, f) for f in supporting][:6],
        "risks": [FAMILY_DESCRIPTIONS.get(f, f) for f in contradicting][:4],
        "hard_gates": hard_gate_reasons,
        "verdict": band,
        # Per Non-Negotiable Rule #7 -- no fabricated probability until
        # EB-10's calibration work exists.
        "calibrated_probability": None,
        "calibrated_probability_reason": "Not yet calibrated (EB-10).",
    }
