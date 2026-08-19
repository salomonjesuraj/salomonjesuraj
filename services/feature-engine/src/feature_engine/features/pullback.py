"""Pullback-volume dry-up — EBIE EB-2.

Per docs/EBIE-BLUEPRINT.md Section 4.3.3: "volume dry-up during
compression" / "pullback_volume_ratio" — during a trending move's
pullback (a temporary retracement against the prevailing trend), does
volume shrink (healthy — not much real selling pressure, likely to
resolve back in the trend's favor) or stay elevated (more concerning —
looks like real distribution, not just profit-taking)?

Deliberately scoped as a simple, honestly-bounded proxy rather than a
full swing-to-swing leg-volume comparison: state.py's `swing_points`
deque records WHICH bar a confirmed swing formed at
(`completed_1m_bars` count), but `recent_1m_bars` is only a 20-bar
rolling window — if a swing point is older than that, its actual bars
have already fallen out of memory, so there is no reliable way to sum
"volume from the last swing to now" precisely. Rather than approximate
that with stale/missing data, this instead compares the last few
completed bars' average volume against the already-tracked 20-bar
volume average (`volume_sma_20`, `features/volume.py`), gated on price
actually having moved against `trend_state` over that same short window
— a real, honestly-scoped reading of "is the current pullback quiet or
loud," not a claim about the whole swing leg.
"""

from feature_engine.state import SymbolState
from feature_engine.features.volume import get_volume_sma

PULLBACK_LOOKBACK_BARS = 3
DRY_UP_RATIO_THRESHOLD = 0.85   # recent volume meaningfully below the 20-bar baseline


def pullback_dryup_snapshot(state: SymbolState, trend_state: int) -> dict:
    """Informational only, same "compute it, let feature-ablation earn
    its way in" governance as every other Phase 1-13.x/EB field.
    available=False (not a fabricated reading) when there isn't a clear
    trend, or not enough bars yet.
    """
    bars = list(state.recent_1m_bars)
    if trend_state == 0:
        return {"available": False, "reason": "no trend", "is_pullback": None,
                "volume_ratio": None, "dry_up": None}
    if len(bars) < PULLBACK_LOOKBACK_BARS + 1:
        return {"available": False, "reason": "insufficient bars", "is_pullback": None,
                "volume_ratio": None, "dry_up": None}

    recent = bars[-PULLBACK_LOOKBACK_BARS:]
    recent_avg_vol = sum(float(b.get("v") or 0.0) for b in recent) / len(recent)
    baseline_vol = get_volume_sma(state)
    if baseline_vol <= 0:
        return {"available": False, "reason": "no volume baseline yet", "is_pullback": None,
                "volume_ratio": None, "dry_up": None}

    first_close = float(recent[0].get("c") or 0.0)
    last_close = float(recent[-1].get("c") or 0.0)
    is_pullback = (
        (trend_state == 1 and last_close < first_close)
        or (trend_state == -1 and last_close > first_close)
    )

    ratio = recent_avg_vol / baseline_vol
    return {
        "available": True,
        "is_pullback": is_pullback,
        "recent_avg_volume": round(recent_avg_vol, 0),
        "baseline_avg_volume": round(baseline_vol, 0),
        "volume_ratio": round(ratio, 3),
        # True = healthy/quiet pullback (matches the blueprint's own
        # bullish-pullback framing). Only meaningful when is_pullback is
        # True -- still reported either way so the ratio itself is always
        # visible, but callers should gate on is_pullback for the verdict.
        "dry_up": bool(is_pullback and ratio < DRY_UP_RATIO_THRESHOLD),
    }
