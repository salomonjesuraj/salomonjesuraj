"""Market + sector directional context — EBIE EB-3.

Per docs/EBIE-BLUEPRINT.md Section 4.11: expand the existing whole-
universe market-breadth health score (api/market_breadth.py,
`compute_market_breadth()`) into a genuine DIRECTIONAL read for a
*specific* stock's setup — is the broader market/sector actually helping
or fighting this particular bias, not just "is breadth healthy in the
abstract."

Deliberate naming deviation from the blueprint's literal
market_context_score_bull/market_context_score_bear/sector_context_
score_bull/sector_context_score_bear (4 separate fields): a stock only
ever has one active bias at a time in this codebase's own data model
(trend_bias is either BUY/SELL, never both at once for the same row), so
computing all 4 unconditionally would mean 2 of the 4 numbers are always
irrelevant noise. This instead returns ONE direction-aware
market_sector_context_score (already read against the row's own bias),
matching how anti_chase_reasons/rejection_reasons already work in this
same file -- direction-aware, not double-computed. Same real information,
half the redundant fields.
"""

from __future__ import annotations

# Per-signal weight caps, tuned so no single input can swing the score
# more than its share -- NIFTY direction matters most (it's the whole
# market), sector second, breadth health third (a slower-moving, more
# diffuse signal).
NIFTY_WEIGHT = 20.0
NIFTY_PCT_SCALE = 8.0
SECTOR_WEIGHT = 15.0
SECTOR_PCT_SCALE = 6.0
BREADTH_WEIGHT = 0.3   # applied to (health_component - 50), so max +/-15


def compute_directional_context(
    *,
    is_sell_bias: bool,
    nifty_change_pct: float | None,
    sector_avg_change_pct: float | None,
    market_health_score: float | None,
) -> dict:
    """All three inputs are already computed elsewhere in this codebase
    (nifty_change_pct/sector_avg_change_pct from ticks.py's own R8
    second pass, market_health_score from market_breadth.py's whole-
    universe read, called once per request, not per row). This is pure
    synthesis -- no new I/O.

    Returns {"score": 0-100 (50 = neutral), "reasons": [...]} -- reasons
    always explicit, matching this file's own "never a silent number"
    convention. Missing inputs are skipped, not treated as neutral 0
    contributions with a fabricated reason.
    """
    score = 50.0
    reasons: list[str] = []

    if nifty_change_pct is not None:
        supportive = (nifty_change_pct >= 0) if not is_sell_bias else (nifty_change_pct <= 0)
        delta = min(abs(nifty_change_pct) * NIFTY_PCT_SCALE, NIFTY_WEIGHT)
        if supportive:
            score += delta
            reasons.append("NIFTY moving with this direction")
        else:
            score -= delta
            reasons.append("NIFTY moving against this direction")

    if sector_avg_change_pct is not None:
        supportive = (sector_avg_change_pct >= 0) if not is_sell_bias else (sector_avg_change_pct <= 0)
        delta = min(abs(sector_avg_change_pct) * SECTOR_PCT_SCALE, SECTOR_WEIGHT)
        if supportive:
            score += delta
            reasons.append("Sector moving with this direction")
        else:
            score -= delta
            reasons.append("Sector moving against this direction")

    if market_health_score is not None:
        # market_health_score is already a 0-100 bullish-leaning breadth
        # read (see market_breadth.py's own header) -- for a sell-biased
        # row, a HIGH health score is actually a headwind, so it's
        # mirrored around 50 first.
        health_component = market_health_score if not is_sell_bias else (100.0 - market_health_score)
        score += (health_component - 50.0) * BREADTH_WEIGHT
        reasons.append(
            f"Market breadth {'supportive' if health_component >= 50 else 'weak'} for this direction"
        )

    return {
        "score": round(max(0.0, min(100.0, score)), 1),
        "reasons": reasons[:3],
    }
