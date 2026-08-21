"""VIX-tiered position-size multiplier -- from hopit-ai/india-trade-cli's
research-cited tiering (100% / 85% / 65% / 50% bands by India VIX level),
one of the 11-link GitHub review's lower-confidence candidates, built now
on explicit request.

Layered ALONGSIDE the existing per-contract IV Rank
(api/option_reality.py's iv_rank_gate), not a replacement for it -- the
two measure different things entirely. IV Rank asks "is THIS option's own
premium cheap or expensive relative to its own recent history?" (a
single-contract, relative read). India VIX asks "how much fear/
complacency is priced into the WHOLE market right now?" (a broad-market,
absolute read). A contract can have a low IV Rank (cheap versus its own
history) while the broad market VIX is elevated (everything is
expensive right now, this one just less so) -- both readings matter, for
different questions.

VIX LEVEL thresholds below are Infusion's own calibration, not lifted
from the same source: hopit-ai's README specifies the four multiplier
tiers (100/85/65/50%) but not the exact India VIX cutoffs between them.
These follow the commonly-cited NSE/brokerage convention for India VIX
regimes -- low/complacent below ~15, a normal-to-elevated band 15-25 split
at 20, and a high/panic regime above 25 -- not literally sourced from
hopit-ai's own repo.

Informational only -- same governance as every other Phase 13 sizing
overlay (half-Kelly, the ATR volatility cap): reported alongside, never
multiplied into, the actual quantity/lot_count compute_position_size()
already returns. A human (or a future, separately-reviewed change) decides
whether to actually size down in a high-VIX regime.
"""

from __future__ import annotations

# (ceiling_exclusive, size_multiplier, tier_label) -- first tier whose
# ceiling the level falls under wins. The last tier's ceiling is +inf so
# every level matches something.
VIX_TIERS: list[tuple[float, float, str]] = [
    (15.0, 1.00, "low"),
    (20.0, 0.85, "elevated"),
    (25.0, 0.65, "high"),
    (float("inf"), 0.50, "extreme"),
]


def vix_position_multiplier(vix_level: float | None) -> dict:
    """Pure function: India VIX level -> size-multiplier tier. No I/O."""
    if vix_level is None or vix_level <= 0:
        return {"available": False, "reason": "No India VIX level available."}
    for ceiling, multiplier, tier in VIX_TIERS:
        if vix_level < ceiling:
            return {
                "available": True,
                "vix_level": round(vix_level, 2),
                "vix_tier": tier,
                "vix_size_multiplier_pct": round(multiplier * 100, 0),
            }
    return {  # unreachable given the last tier's ceiling is +inf, kept as a safe fallback
        "available": True,
        "vix_level": round(vix_level, 2),
        "vix_tier": "extreme",
        "vix_size_multiplier_pct": 50.0,
    }
