"""Fibonacci retracement / extension / projection + confluence clustering.

Mirrors Carolyn Boroden's methodology (Fibonacci Trading: How to Master the
Time and Price Advantage) on top of the fractal swing-pivot history already
tracked in features/structure.py — no new detection system, pure math on
swing points Infusion already confirms.

Confluence rule (Boroden, verbatim): a price cluster requires >= 3
independent Fibonacci levels (any mix of retracement/extension/projection,
computed from different swing pairs) converging within a tight price range.
More overlapping levels = a stronger decision zone, not a higher win
probability by itself — this module only finds and ranks clusters; whether
one is worth trading is left to the caller (pine_confidence.py / scoring.py).
"""

from __future__ import annotations

from typing import Any

from feature_engine.state import SymbolState

RETRACEMENT_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)
EXTENSION_RATIOS = (1.272, 1.618, 2.618)
PROJECTION_RATIOS = (1.0, 1.618)  # 1.0 = "symmetry" / measured move

CLUSTER_MIN_HITS = 3  # Boroden's rule: >=3 independent levels = a cluster
CLUSTER_TOLERANCE_ATR_FRAC = 0.15  # levels within this fraction of 1 ATR cluster together
MAX_CLUSTERS_RETURNED = 3


def retracement_levels(swing_low: float, swing_high: float) -> dict[float, float]:
    """Support levels retracing down from a low->high swing (bullish read)."""
    span = swing_high - swing_low
    return {r: swing_high - span * r for r in RETRACEMENT_RATIOS}


def resistance_retracement_levels(swing_low: float, swing_high: float) -> dict[float, float]:
    """Resistance levels retracing up from a high->low swing (bearish read)."""
    span = swing_high - swing_low
    return {r: swing_low + span * r for r in RETRACEMENT_RATIOS}


def extension_levels(swing_low: float, swing_high: float, *, bullish: bool) -> dict[float, float]:
    """Levels beyond 100% of the swing, projected in the trade direction."""
    span = swing_high - swing_low
    base = swing_high if bullish else swing_low
    sign = 1.0 if bullish else -1.0
    return {r: base + sign * span * r for r in EXTENSION_RATIOS}


def projection_levels(point_a: float, point_b: float, point_c: float) -> dict[float, float]:
    """3-point A-B-C projection: projects the A->B swing forward from C.
    ratio=1.0 is "symmetry" (a measured move); 1.618 is the extended case."""
    leg = point_b - point_a
    return {r: point_c + leg * r for r in PROJECTION_RATIOS}


def _all_levels(swing_points: list[tuple[float, str, int]]) -> list[tuple[float, str]]:
    """Every retracement/extension level from each opposite-type swing pair,
    plus every 3-point projection from consecutive triplets in swing order."""
    levels: list[tuple[float, str]] = []

    for i in range(len(swing_points) - 1):
        p1, t1, _ = swing_points[i]
        for j in range(i + 1, len(swing_points)):
            p2, t2, _ = swing_points[j]
            if t1 == t2 or p1 == p2:
                continue  # a genuine swing needs an opposite-type pair
            lo, hi = (p1, p2) if p1 < p2 else (p2, p1)
            for r, lvl in retracement_levels(lo, hi).items():
                levels.append((lvl, f"retr_{r}"))
            for r, lvl in resistance_retracement_levels(lo, hi).items():
                levels.append((lvl, f"retr_{r}"))
            for r, lvl in extension_levels(lo, hi, bullish=True).items():
                levels.append((lvl, f"ext_{r}"))
            for r, lvl in extension_levels(lo, hi, bullish=False).items():
                levels.append((lvl, f"ext_{r}"))

    for i in range(len(swing_points) - 2):
        a, _, _ = swing_points[i]
        b, _, _ = swing_points[i + 1]
        c, _, _ = swing_points[i + 2]
        for r, lvl in projection_levels(a, b, c).items():
            levels.append((lvl, f"proj_{r}"))

    return levels


def _summarize_cluster(bucket: list[tuple[float, str]]) -> dict[str, Any]:
    prices = [p for p, _ in bucket]
    return {
        "center": round(sum(prices) / len(prices), 2),
        "low": round(min(prices), 2),
        "high": round(max(prices), 2),
        "hits": len(bucket),
        "sources": sorted({s for _, s in bucket}),
    }


def find_confluence_clusters(
    swing_points: list[tuple[float, str, int]],
    current_price: float,
    atr: float,
    tolerance_atr_frac: float = CLUSTER_TOLERANCE_ATR_FRAC,
) -> list[dict[str, Any]]:
    """Scan all pairwise/triplet Fibonacci levels from recent confirmed swings
    and group ones within `tolerance_atr_frac` of an ATR of each other.
    Returns clusters with >= CLUSTER_MIN_HITS independent levels, nearest to
    current_price first.

    Tolerance is ATR-scaled (not a fixed % of price) so the same rule works
    for a ₹50 stock and a ₹5,000 stock without retuning.
    """
    if len(swing_points) < 2 or current_price <= 0:
        return []

    tolerance = max(atr, current_price * 0.001) * tolerance_atr_frac
    if tolerance <= 0:
        return []

    levels = _all_levels(swing_points)
    if not levels:
        return []

    levels.sort(key=lambda x: x[0])

    clusters: list[dict[str, Any]] = []
    bucket = [levels[0]]
    for lvl, src in levels[1:]:
        ref = bucket[0][0]
        if abs(lvl - ref) <= tolerance:
            bucket.append((lvl, src))
        else:
            if len(bucket) >= CLUSTER_MIN_HITS:
                clusters.append(_summarize_cluster(bucket))
            bucket = [(lvl, src)]
    if len(bucket) >= CLUSTER_MIN_HITS:
        clusters.append(_summarize_cluster(bucket))

    clusters.sort(key=lambda c: abs(c["center"] - current_price))
    return clusters[:MAX_CLUSTERS_RETURNED]


def fib_snapshot(state: SymbolState, ltp: float) -> dict[str, Any]:
    """Compact dict for FeatureVectorV1.ml_features — the nearest confluence
    cluster to current price, or empty/None fields when none is found yet
    (thin swing history, or no genuine convergence)."""
    clusters = find_confluence_clusters(list(state.swing_points), ltp, state.atr)
    nearest = clusters[0] if clusters else None
    return {
        "fib_cluster_center": nearest["center"] if nearest else None,
        "fib_cluster_low": nearest["low"] if nearest else None,
        "fib_cluster_high": nearest["high"] if nearest else None,
        "fib_cluster_hits": nearest["hits"] if nearest else 0,
        "fib_cluster_sources": nearest["sources"] if nearest else [],
        "fib_swing_count": len(state.swing_points),
    }
