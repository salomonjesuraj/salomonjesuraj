"""EBIE EB-9 (increment 1) -- real-time trap-risk heuristic (shadow only).

Per docs/EBIE-BLUEPRINT.md Section 18 ("False-Breakout / Trap Model"):
a setup can have a high breakout score AND a high false-breakout risk
at the same time -- "that should not receive an A+ verdict." This
module computes a deterministic, rule-based trap_risk_score at signal-
candidate time from real-time evidence already available in this
codebase (no new I/O), covering the subset of the blueprint's own
feature list that's actually computable without live option-chain data
(IV-spike-without-follow-through needs a live Greeks fetch scanner has
no access to -- same architectural constraint already disclosed for
EB-8's option-liquidity hard gates; not built here, not guessed at).

This is a genuinely different, complementary signal from the Unified
Verdict Engine (EB-8): the verdict asks "how much evidence supports
this direction," this asks "how likely is this specific trigger to be
a fake-out even if the broader evidence is right." A candidate can
score well on both engines' bull_score AND still carry real trap risk
(e.g. genuinely strong multi-family evidence, but THIS EXACT crossing
happened on weak first-cross volume with a rejection wick) -- exactly
the blueprint's own point.

Per Non-Negotiable Rule #7 ("No raw score shown as probability") and
this whole phase's shadow-mode governance: trap_risk_score is an
evidence-signal COUNT percentage (how many of the checkable trap
indicators actually fired), not a calibrated P(false_breakout). A real
probability needs the false-break-labeled outcome history EB-9
increment 2 starts capturing -- not yet enough real labeled examples to
train or calibrate anything here. Informational only, never blocks a
signal, matching every other EBIE evidence family's own governance.
"""

from __future__ import annotations

from typing import Any

# Candlestick patterns that specifically signal a wick REJECTION at the
# current level -- reused from scanner/alignment.py's own vocabulary
# (feature-engine/features/candles.py), not re-invented.
REJECTION_CANDLES_BEARISH = {
    "Bearish Engulfing",
    "Shooting Star",
    "Bearish Marubozu",
    "Bearish Harami",
    "Bearish Pin Bar",
    "Three Black Crows",
    "Dark Cloud Cover",
    "Evening Star",
    "Gravestone Doji",
    "Tweezer Top",
}
REJECTION_CANDLES_BULLISH = {
    "Bullish Engulfing",
    "Hammer",
    "Bullish Marubozu",
    "Bullish Harami",
    "Bullish Pin Bar",
    "Three White Soldiers",
    "Piercing Line",
    "Morning Star",
    "Dragonfly Doji",
    "Tweezer Bottom",
}

WEAK_VOLUME_THRESHOLD = 1.2  # rel_vol_20d below this on the trigger itself is weak participation
WIDE_SPREAD_BPS = 15.0
EXTENDED_MOVE_PCT = (
    3.0  # |change_pct| beyond this, in the SAME direction as the candidate, reads as chasing
)
SECTOR_SUPPORTIVE = 55.0
SECTOR_CONTRARY = 45.0


def _check(name: str, fired: bool | None, reasons: list[str], indicator_name: str) -> bool | None:
    if fired is None:
        return None
    if fired:
        reasons.append(indicator_name)
    return fired


def compute_trap_risk(
    *,
    bullish: bool,
    anti_chase_ok: bool | None,
    rel_vol_20d: float | None,
    candle_pattern: str,
    change_pct: float | None,
    spread_bps: float | None,
    call_wall_state: str | None,
    put_wall_state: str | None,
    rs_slope_20d: float | None,
    sector_strength: float | None,
) -> dict[str, Any]:
    """Each indicator votes True (trap-risk sign present), False (no
    sign of that specific risk), or is left out of the count entirely
    when the underlying data isn't available -- an absent indicator is
    not evidence of safety, it's simply unchecked (same "never fabricate"
    convention as every prior EBIE evidence family)."""
    reasons: list[str] = []
    votes: dict[str, bool | None] = {}

    # 1. Already-chasing an extended move (Phase R's own no-chase gate).
    votes["chase_risk"] = _check(
        "chase_risk",
        None if anti_chase_ok is None else (anti_chase_ok is False),
        reasons,
        "anti-chase gate already flagged this as a chase",
    )

    # 2. Weak first-cross participation.
    votes["weak_volume"] = _check(
        "weak_volume",
        None if rel_vol_20d is None else (rel_vol_20d < WEAK_VOLUME_THRESHOLD),
        reasons,
        f"relative volume {rel_vol_20d} below {WEAK_VOLUME_THRESHOLD}x on the trigger",
    )

    # 3. A rejection-wick candle against the candidate's own direction.
    rejection_set = REJECTION_CANDLES_BEARISH if bullish else REJECTION_CANDLES_BULLISH
    votes["wick_rejection"] = _check(
        "wick_rejection",
        candle_pattern in rejection_set if candle_pattern else None,
        reasons,
        f"{candle_pattern} rejection candle against the setup's own direction",
    )

    # 4. OI wall rebuilding against direction (EB-5) -- resistance
    # strengthening for a bullish setup, or support eroding (and the
    # mirror for a bearish setup). None (unchecked) unless at least one
    # wall's state is actually known.
    wall_signal = None
    if call_wall_state is not None or put_wall_state is not None:
        resistance_state = call_wall_state if bullish else put_wall_state
        support_state = put_wall_state if bullish else call_wall_state
        wall_signal = (resistance_state == "strengthening") or (support_state == "weakening")
    votes["wall_against_direction"] = _check(
        "wall_against_direction",
        wall_signal,
        reasons,
        "option-chain wall rebuilding against direction",
    )

    # 5. Negative RS divergence -- price breaking out but relative
    # strength fading in the same window (EB-3).
    rs_divergent = None
    if rs_slope_20d is not None:
        rs_divergent = (rs_slope_20d < 0) if bullish else (rs_slope_20d > 0)
    votes["rs_divergence"] = _check(
        "rs_divergence", rs_divergent, reasons, "relative strength diverging from price direction"
    )

    # 6. Sector context contradicting the setup's own direction.
    sector_divergent = None
    if sector_strength is not None:
        sector_divergent = (
            (sector_strength < SECTOR_CONTRARY)
            if bullish
            else (sector_strength > SECTOR_SUPPORTIVE)
        )
    votes["sector_divergence"] = _check(
        "sector_divergence",
        sector_divergent,
        reasons,
        "sector context contradicting the setup's direction",
    )

    # 7. Chasing an already-extended move in the same direction.
    extended = None
    if change_pct is not None:
        extended = (
            (change_pct > EXTENDED_MOVE_PCT) if bullish else (change_pct < -EXTENDED_MOVE_PCT)
        )
    votes["extended_move"] = _check(
        "extended_move",
        extended,
        reasons,
        f"already moved {change_pct}% today in the same direction",
    )

    # 8. Wide spread -- a real, if coarse, liquidity-deterioration proxy
    # (no historical spread trend available in this stateless context,
    # so this checks the absolute level only, disclosed as a
    # simplification vs the blueprint's own "spread widening" language).
    votes["wide_spread"] = _check(
        "wide_spread",
        None if spread_bps is None else (spread_bps > WIDE_SPREAD_BPS),
        reasons,
        f"spread {spread_bps}bps wider than {WIDE_SPREAD_BPS}bps",
    )

    checked = {k: v for k, v in votes.items() if v is not None}
    fired = [k for k, v in checked.items() if v]
    total_checked = len(checked)
    trap_risk_score = round(100 * len(fired) / total_checked, 1) if total_checked else None

    return {
        "trap_risk_score": trap_risk_score,
        "trap_indicators_fired": fired,
        "trap_indicators_checked": total_checked,
        "trap_indicators_total": len(votes),
        "trap_reasons": reasons,
        # Per Rule #7 -- an evidence-count percentage, not a calibrated
        # probability. Real P(false_breakout) needs EB-9 increment 2's
        # false-break-labeled outcome history, which doesn't exist yet.
        "trap_probability": None,
        "trap_probability_reason": "Not yet calibrated -- awaiting false-break-labeled outcome history (EB-9 increment 2).",
    }
