"""Chart-pattern geometry classifier — Bulkowski-style multi-swing patterns
(Double/Triple Top/Bottom, Rectangle) computed on the DAILY bar series.

Scope note, important: this operates on daily closes/highs/lows (the same
`daily` bars api/routes/mtf.py already loads for pivots/CPR and the MA
regime), NOT the feature-engine's 1-minute intraday swing_points used by
features/fibonacci.py. Bulkowski's own hit-rate statistics (cited in each
detector below, from his 1991-2011 US-equity database) describe patterns
that form over WEEKS on a daily chart. Applying them to fast intraday
swings would be a real methodological mismatch — a "double top" that forms
over 20 minutes has no relationship to the phenomenon his numbers describe.
Keeping this on the daily timeframe is what makes the confidence numbers
below still mean something.

NOT implemented this pass — deferred, not silently skipped:
  - Head & Shoulders: needs neckline-slope geometry beyond simple
    peak/valley height comparison (a sloped line through two "armpits",
    not just "are these two points near the same price").
  - Triangles (ascending/descending/symmetrical): need real trendline
    slope-fitting across multiple points, not point-to-point comparison.
  - Flags/Pennants: need pole detection (a sharp directional impulse)
    ahead of the consolidation, a separate precursor step.
  - Busted-pattern reversal detection: a natural follow-up once these base
    detectors exist and can be checked for failure.

Known overlap, accepted rather than engineered around: a genuine triple
top's last three points are themselves a valid double top by definition,
so both can fire simultaneously on the same swing sequence. Each result is
independently correct under its own definition; callers wanting a single
"best" answer should prefer the one with more confirming points (triple
over double) or the higher Bulkowski hit rate.
"""

from __future__ import annotations

from typing import Any

PEAK_SIMILARITY_PCT = 5.0  # Bulkowski's own stated tolerance for double-bottom valleys; extended here to peaks and triple variants for consistency
PATTERN_LOOKBACK_PIVOTS = (
    8  # how many recent pivots are worth checking for a live, still-relevant pattern
)


def _similar(a: float, b: float, tolerance_pct: float = PEAK_SIMILARITY_PCT) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) * 100.0 <= tolerance_pct


def fractal_pivots_indexed(
    bars: list[dict[str, Any]], left: int = 2, right: int = 2
) -> list[tuple[float, str, int]]:
    """Chronological (price, 'high'|'low', bar_index) pivot list — the same
    fractal left/right rule as api.routes.mtf._fractal_pivots, but indexed
    and interleaved (one combined, ordered sequence) so pattern detectors
    can tell which pivot came before which, not just that both exist
    somewhere in the array.
    """
    out: list[tuple[float, str, int]] = []
    window = left + right + 1
    if len(bars) < window:
        return out
    for i in range(left, len(bars) - right):
        segment = bars[i - left : i + right + 1]
        cand_high = bars[i]["high"]
        cand_low = bars[i]["low"]
        seg_highs = [b["high"] for b in segment]
        seg_lows = [b["low"] for b in segment]
        if cand_high == max(seg_highs) and seg_highs.count(cand_high) == 1:
            out.append((cand_high, "high", i))
        if cand_low == min(seg_lows) and seg_lows.count(cand_low) == 1:
            out.append((cand_low, "low", i))
    out.sort(key=lambda t: t[2])
    return out


def _recent(
    pivots: list[tuple[float, str, int]], n: int = PATTERN_LOOKBACK_PIVOTS
) -> list[tuple[float, str, int]]:
    return pivots[-n:] if len(pivots) > n else pivots


def detect_double_top(
    pivots: list[tuple[float, str, int]], current_price: float
) -> dict[str, Any] | None:
    """high -> low -> high, the two highs similar. Confirmed on a close
    below the intervening low (Bulkowski's confirmation rule). Target uses
    HALF the pattern height off the confirmation line: Bulkowski found the
    full-height target hits only 44% of the time, "so poor" a result that
    half-height (73%) became his recommended rule instead.
    """
    recent = _recent(pivots)
    for i in range(len(recent) - 3, -1, -1):
        p1, k1, _ = recent[i]
        p2, k2, _ = recent[i + 1]
        p3, k3, _ = recent[i + 2]
        if k1 == "high" and k2 == "low" and k3 == "high" and _similar(p1, p3):
            valley = p2
            peak = max(p1, p3)
            height = peak - valley
            if height <= 0:
                continue
            return {
                "pattern": "double_top",
                "bias": "bearish",
                "peak_1": round(p1, 2),
                "valley": round(valley, 2),
                "peak_2": round(p3, 2),
                "height": round(height, 2),
                "confirmation_line": round(valley, 2),
                "confirmed": current_price < valley,
                "target": round(valley - height * 0.5, 2),
                "bulkowski_hit_rate_pct": 73,
                "method": "Half-height target off the confirmation line (full height hits only 44%)",
            }
    return None


def detect_double_bottom(
    pivots: list[tuple[float, str, int]], current_price: float
) -> dict[str, Any] | None:
    """Mirror of detect_double_top: low -> high -> low, the two lows
    similar. Confirmed on a close above the intervening peak."""
    recent = _recent(pivots)
    for i in range(len(recent) - 3, -1, -1):
        p1, k1, _ = recent[i]
        p2, k2, _ = recent[i + 1]
        p3, k3, _ = recent[i + 2]
        if k1 == "low" and k2 == "high" and k3 == "low" and _similar(p1, p3):
            peak = p2
            valley = min(p1, p3)
            height = peak - valley
            if height <= 0:
                continue
            return {
                "pattern": "double_bottom",
                "bias": "bullish",
                "valley_1": round(p1, 2),
                "peak": round(peak, 2),
                "valley_2": round(p3, 2),
                "height": round(height, 2),
                "confirmation_line": round(peak, 2),
                "confirmed": current_price > peak,
                "target": round(peak + height * 0.5, 2),
                "bulkowski_hit_rate_pct": 73,
                "method": "Half-height target off the confirmation line (full height hits only 44%)",
            }
    return None


def detect_triple_top(
    pivots: list[tuple[float, str, int]], current_price: float
) -> dict[str, Any] | None:
    """high -> low -> high -> low -> high, all three highs similar.
    Confirmed on a close below the lower of the two intervening valleys.
    Target uses the FULL pattern height (Bulkowski gives no half-height
    correction for triples the way he does for doubles)."""
    recent = _recent(pivots)
    for i in range(len(recent) - 5, -1, -1):
        p1, k1, _ = recent[i]
        p2, k2, _ = recent[i + 1]
        p3, k3, _ = recent[i + 2]
        p4, k4, _ = recent[i + 3]
        p5, k5, _ = recent[i + 4]
        if (
            (k1, k2, k3, k4, k5) == ("high", "low", "high", "low", "high")
            and _similar(p1, p3)
            and _similar(p3, p5)
        ):
            peak = max(p1, p3, p5)
            valley = min(p2, p4)
            height = peak - valley
            if height <= 0:
                continue
            return {
                "pattern": "triple_top",
                "bias": "bearish",
                "peaks": [round(p1, 2), round(p3, 2), round(p5, 2)],
                "confirmation_line": round(valley, 2),
                "height": round(height, 2),
                "confirmed": current_price < valley,
                "target": round(valley - height, 2),
                "bulkowski_hit_rate_pct": 48,
                "method": "Full height off the confirmation line (lowest intervening valley)",
            }
    return None


def detect_triple_bottom(
    pivots: list[tuple[float, str, int]], current_price: float
) -> dict[str, Any] | None:
    """Mirror of detect_triple_top: low -> high -> low -> high -> low, all
    three lows similar. Confirmed on a close above the higher of the two
    intervening peaks."""
    recent = _recent(pivots)
    for i in range(len(recent) - 5, -1, -1):
        p1, k1, _ = recent[i]
        p2, k2, _ = recent[i + 1]
        p3, k3, _ = recent[i + 2]
        p4, k4, _ = recent[i + 3]
        p5, k5, _ = recent[i + 4]
        if (
            (k1, k2, k3, k4, k5) == ("low", "high", "low", "high", "low")
            and _similar(p1, p3)
            and _similar(p3, p5)
        ):
            valley = min(p1, p3, p5)
            peak = max(p2, p4)
            height = peak - valley
            if height <= 0:
                continue
            return {
                "pattern": "triple_bottom",
                "bias": "bullish",
                "valleys": [round(p1, 2), round(p3, 2), round(p5, 2)],
                "confirmation_line": round(peak, 2),
                "height": round(height, 2),
                "confirmed": current_price > peak,
                "target": round(peak + height, 2),
                "bulkowski_hit_rate_pct": 48,
                "method": "Full height off the confirmation line (highest intervening peak)",
            }
    return None


def detect_rectangle(
    pivots: list[tuple[float, str, int]], current_price: float
) -> dict[str, Any] | None:
    """>= 2 highs clustering near one level AND >= 2 lows clustering near
    another, over the same recent window — a horizontal trading channel.
    Target is the full channel height applied from whichever line breaks.
    """
    recent = _recent(pivots)
    highs = [p for p, k, _ in recent if k == "high"]
    lows = [p for p, k, _ in recent if k == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return None

    top_ref = max(highs)
    top_cluster = [h for h in highs if _similar(h, top_ref)]
    bottom_ref = min(lows)
    bottom_cluster = [low for low in lows if _similar(low, bottom_ref)]
    if len(top_cluster) < 2 or len(bottom_cluster) < 2:
        return None

    top = sum(top_cluster) / len(top_cluster)
    bottom = sum(bottom_cluster) / len(bottom_cluster)
    height = top - bottom
    if height <= 0:
        return None

    if current_price > top:
        breakout, target = "up", top + height
    elif current_price < bottom:
        breakout, target = "down", bottom - height
    else:
        breakout, target = "inside", None

    return {
        "pattern": "rectangle",
        "bias": "breakout_direction_dependent",
        "top": round(top, 2),
        "bottom": round(bottom, 2),
        "top_touches": len(top_cluster),
        "bottom_touches": len(bottom_cluster),
        "height": round(height, 2),
        "breakout": breakout,
        "target": round(target, 2) if target is not None else None,
        "bulkowski_hit_rate_pct": 55,
        "method": "Full height applied from the broken trendline",
    }


PATTERN_DETECTORS = (
    detect_double_top,
    detect_double_bottom,
    detect_triple_top,
    detect_triple_bottom,
    detect_rectangle,
)


def detect_chart_patterns(
    pivots: list[tuple[float, str, int]], current_price: float
) -> list[dict[str, Any]]:
    """Run every implemented detector, return whichever currently match.
    See the module docstring for the known double/triple overlap."""
    results: list[dict[str, Any]] = []
    for detector in PATTERN_DETECTORS:
        match = detector(pivots, current_price)
        if match:
            results.append(match)
    return results
