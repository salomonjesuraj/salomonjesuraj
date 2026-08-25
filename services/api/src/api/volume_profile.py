"""Volume Profile — Point of Control (POC) and Value Area (VAH/VAL).

Self-contained duplicate of feature_engine/features/volume_profile.py's
core histogram/POC/Value-Area algorithm, adapted for api's own bar shape
({"high","low","close","volume"}, api/routes/mtf.py's _decode_ohlc()) and
Redis-fetched multi-day/multi-session history -- same "no shared-lib
import path between services" precedent already used elsewhere in this
codebase (e.g. scanner/verdict_engine.py's own market-context
duplication). See that module's own docstring for the full algorithm
writeup (bin count, typical-price simplification, Value Area
construction) -- kept identical here on purpose so the two never
disagree in behavior, only in which bar shape they accept.
"""

from __future__ import annotations

from typing import Any

NUM_BINS_DEFAULT = 50
VALUE_AREA_PCT_DEFAULT = 0.70
ACCUMULATION_BASE_WIDTH_PCT = 0.03
ACCUMULATION_MIN_RVOL = 1.5

Bar = dict[str, Any]


def compute_volume_profile(prices_and_volumes: list[tuple[float, float]]) -> dict[str, Any]:
    """Identical algorithm to feature_engine's compute_volume_profile() --
    see that module's docstring for the full writeup."""
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


def compute_volume_profile_from_bars(bars: list[Bar]) -> dict[str, Any]:
    """Adapter for api's own {"high","low","close","volume"} bar shape
    (api/routes/mtf.py's _decode_ohlc()/Bar type)."""
    pairs: list[tuple[float, float]] = []
    for bar in bars:
        h = float(bar.get("high", 0))
        low_p = float(bar.get("low", 0))
        c = float(bar.get("close", 0))
        v = float(bar.get("volume", 0))
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
    """Identical logic to feature_engine's own detect_macro_accumulation_
    breakout() -- see that module's docstring."""
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
