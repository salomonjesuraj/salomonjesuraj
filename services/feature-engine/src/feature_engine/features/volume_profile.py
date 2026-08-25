"""Volume Profile — Point of Control (POC) and Value Area (VAH/VAL).

Mathematical audit fix (§1.1, 2026-08-25): confirmed genuinely absent
everywhere in the codebase before this (a repo-wide grep for
point_of_control/value_area/POC returned zero hits). "volume_profile"
already existed as a term elsewhere (feature_engine/features/volume.py's
RVol time-of-day baseline), but that is an unrelated concept -- a
minute-of-day volume average, not a price-level distribution.

Algorithm (standard, but the specific calibration choices below are
Infusion's own, not from a cited source):
  1. Bin the observed [low, high] price range into NUM_BINS_DEFAULT
     equal-width bins.
  2. Each bar's ENTIRE volume is assigned to the bin containing its
     typical price (H+L+C)/3 -- the same "typical price" convention
     api/routes/mtf.py's own _vwap() already uses. This is a real
     simplification (true tick-level volume-at-price isn't available
     from 1-minute OHLCV bars) -- disclosed, not hidden: a bar that
     ranged widely still counts its whole volume at one representative
     price, not spread across the bins it touched.
  3. POC = the bin with the most volume (reported as that bin's
     midpoint price).
  4. Value Area = the narrowest contiguous bin range around POC whose
     cumulative volume clears VALUE_AREA_PCT_DEFAULT (70%, the standard
     convention) of the total -- built by repeatedly extending the
     range to whichever side (above/below) has more volume in its next
     unclaimed bin, the standard Value Area construction algorithm.

This module is a pure function taking a plain list of (typical_price,
volume) pairs, not a SymbolState -- unlike every incremental update_*
feature in this package, a volume profile fundamentally needs a batch
of historical bars (days of session history, per the task's own
"20-day Daily and Intraday session profile" ask), which doesn't fit
this engine's per-tick incremental model. compute_volume_profile_from_bars()
is the convenience wrapper for feature-engine's own native
{"h","l","c","v"} bar shape (state.py's recent_1m_bars / _bar_dict());
api/volume_profile.py carries a self-contained duplicate of the core
histogram/POC/VA algorithm for its own {"high","low","close","volume"}
bar shape and Redis-fetched multi-day history -- same "no shared-lib
import path between services" precedent already used elsewhere in this
codebase (e.g. scanner/verdict_engine.py's market-context duplication).
"""

from __future__ import annotations

from typing import Any

NUM_BINS_DEFAULT = 50
VALUE_AREA_PCT_DEFAULT = 0.70

# Task 3.2's own spec: a Value Area narrower than this, as a fraction of
# POC, counts as a "tight" consolidation base worth flagging.
ACCUMULATION_BASE_WIDTH_PCT = 0.03  # (VAH-VAL)/POC < 3%
ACCUMULATION_MIN_RVOL = 1.5


def compute_volume_profile(prices_and_volumes: list[tuple[float, float]]) -> dict[str, Any]:
    """Core POC/Value-Area algorithm over (typical_price, volume) pairs.
    `available: False` (never a fabricated level) when there isn't
    enough real data -- no bars, or every bar had zero/negative volume.
    """
    pairs = [(p, v) for p, v in prices_and_volumes if v > 0 and p > 0]
    if not pairs:
        return {
            "available": False,
            "reason": "No priced volume in the lookback window.",
            "poc": None,
            "vah": None,
            "val": None,
            "value_area_width_pct": None,
            "total_volume": 0.0,
            "bins_used": 0,
        }

    low = min(p for p, _ in pairs)
    high = max(p for p, _ in pairs)
    if high <= low:
        # A single flat price across the whole window -- POC/VAH/VAL all
        # collapse to that one price, honestly, not an arbitrary spread.
        total = sum(v for _, v in pairs)
        return {
            "available": True,
            "poc": round(low, 2),
            "vah": round(low, 2),
            "val": round(low, 2),
            "value_area_width_pct": 0.0,
            "total_volume": round(total, 1),
            "bins_used": 1,
        }

    num_bins = NUM_BINS_DEFAULT
    bin_width = (high - low) / num_bins
    volume_by_bin: dict[int, float] = {i: 0.0 for i in range(num_bins)}
    for price, volume in pairs:
        idx = min(int((price - low) / bin_width), num_bins - 1)
        volume_by_bin[idx] += volume

    total_volume = sum(volume_by_bin.values())
    poc_bin = max(volume_by_bin, key=lambda i: volume_by_bin[i])

    # Value Area: extend outward from the POC bin, each step adding
    # whichever adjacent unclaimed bin carries more volume, until the
    # accumulated range clears VALUE_AREA_PCT_DEFAULT of total volume --
    # the standard construction algorithm.
    lo_idx = hi_idx = poc_bin
    accumulated = volume_by_bin[poc_bin]
    target = total_volume * VALUE_AREA_PCT_DEFAULT
    while accumulated < target and (lo_idx > 0 or hi_idx < num_bins - 1):
        next_lo_vol = volume_by_bin[lo_idx - 1] if lo_idx > 0 else -1.0
        next_hi_vol = volume_by_bin[hi_idx + 1] if hi_idx < num_bins - 1 else -1.0
        if next_hi_vol >= next_lo_vol:
            hi_idx += 1
            accumulated += volume_by_bin[hi_idx]
        else:
            lo_idx -= 1
            accumulated += volume_by_bin[lo_idx]

    def _bin_mid(idx: int) -> float:
        return low + bin_width * (idx + 0.5)

    poc = _bin_mid(poc_bin)
    vah = low + bin_width * (hi_idx + 1)
    val = low + bin_width * lo_idx
    width_pct = (vah - val) / poc * 100 if poc > 0 else None

    return {
        "available": True,
        "poc": round(poc, 2),
        "vah": round(vah, 2),
        "val": round(val, 2),
        "value_area_width_pct": round(width_pct, 3) if width_pct is not None else None,
        "total_volume": round(total_volume, 1),
        "bins_used": num_bins,
    }


def compute_volume_profile_from_bars(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Convenience wrapper for feature-engine's own native bar shape
    ({"h","l","c","v"} -- see state.py's recent_1m_bars / engine.py's
    _bar_dict()). Typical price = (h+l+c)/3, matching api/routes/mtf.py's
    own _vwap() convention -- see this module's own docstring."""
    pairs: list[tuple[float, float]] = []
    for bar in bars:
        h, low_p, c = float(bar.get("h", 0)), float(bar.get("l", 0)), float(bar.get("c", 0))
        v = float(bar.get("v", 0))
        if h <= 0 or low_p <= 0 or c <= 0:
            continue
        typical = (h + low_p + c) / 3.0
        pairs.append((typical, v))
    return compute_volume_profile(pairs)


def detect_macro_accumulation_breakout(
    profile: dict[str, Any],
    *,
    current_price: float,
    prev_price: float,
    rel_vol: float,
) -> bool:
    """Task 3.2's own spec: a genuine CROSS above VAH (not merely
    "currently above" -- prev_price must have been at/below VAH) out of
    a tight Value Area, confirmed by real relative-volume expansion.
    False (never fabricated True) whenever the profile itself isn't
    available or the width/RVol thresholds aren't both cleared."""
    if not profile.get("available"):
        return False
    poc = profile.get("poc")
    vah = profile.get("vah")
    val = profile.get("val")
    width_pct = profile.get("value_area_width_pct")
    if poc is None or vah is None or val is None or width_pct is None:
        return False
    tight_base = (width_pct / 100.0) < ACCUMULATION_BASE_WIDTH_PCT
    fresh_cross_above_vah = prev_price <= vah < current_price
    volume_expanding = rel_vol >= ACCUMULATION_MIN_RVOL
    return bool(tight_base and fresh_cross_above_vah and volume_expanding)
