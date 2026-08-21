"""Wyckoff structural concepts (partial) — structural failure (strength/
weakness), Shortening of the Thrust (SOT), and SOS/SOW trigger bars.

Computed on the DAILY bar series, matching Phase 5's chart-pattern
architecture decision: Wyckoff's methodology is fundamentally about
accumulation/distribution CAMPAIGNS spanning weeks to months, not fast
intraday noise. Unlike Bulkowski's specific hit-rate statistics (which are
empirically tied to the weeks-long daily timeframe they were measured on),
these three concepts are qualitative geometric/volume rules with no
timeframe-specific number attached — so there's less of a "stat mismatch"
risk than Phase 5 had, but the daily timeframe still matches how Wyckoff
practitioners actually read these structures, which is the more defensible
default.

Source: Wyckoff 2.0 (Villahermosa) — Volume 2 of a two-book series. It
assumes the full Phase A-E schematic (PS/SC/AR/ST/Spring/SOS/LPS) from a
Volume 1 that ISN'T in the corpus this was built from, so full phase
detection is explicitly BLOCKED — not attempted here or anywhere else in
this codebase, not faked from general knowledge and passed off as sourced.
What Volume 2 gives standalone, without needing Volume 1's event
definitions, is what's implemented below.

NOT implemented this pass, deferred with reason:
  - Volume Profile / VPOC / VAH / VAL / HVN / LVN: a true profile needs
    intrabar price-volume distribution, which daily OHLCV doesn't give
    directly. The standard workaround (distributing each bar's volume
    uniformly across its H-L range) is itself well-defined, but building a
    meaningful profile needs a substantially longer bar-retention window
    than anything currently kept for this purpose — a real architecture
    decision (how many bars, at what memory cost across 208 symbols) that
    deserves its own pass, not a bolt-on to this one.
  - Order flow / footprint (bid x ask delta): needs tick/L2 data Infusion
    doesn't ingest — confirmed infeasible in this source, not a scope
    choice on our part.
"""

from __future__ import annotations

STRUCTURAL_TOUCH_TOLERANCE_PCT = 5.0  # same convention as chart_patterns.py's peak-similarity rule
FAILURE_APPROACH_TOLERANCE_PCT = 3.0  # how close a swing must get to an extreme to count as "reaching" it -- Infusion's own calibration, source gives no exact number
SOT_MIN_LEGS = 3
SOS_SOW_RANGE_MULTIPLIER = (
    1.2  # "wide-range" -- Infusion's own calibration, source doesn't quantify
)
SOS_SOW_VOLUME_MULTIPLIER = 1.2  # "relatively high volume" -- same
SOS_SOW_LOOKBACK = 20
PATTERN_LOOKBACK_PIVOTS = 8


def _similar(a: float, b: float, tolerance_pct: float) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) * 100.0 <= tolerance_pct


def detect_structural_failure(
    pivots: list[tuple[float, str, int]], lookback: int = PATTERN_LOOKBACK_PIVOTS
) -> dict | None:
    """A validated range (>=2 touches each side, same clustering rule as
    chart_patterns.detect_rectangle) where the most recent swing clearly
    moved toward one extreme from a confirmed touch of the other, but
    failed to actually reach it before reversing.

    Failing to reach the top after validating a range = weakness (rally
    capped before resistance -- overhead selling pressure). Failing to
    reach the bottom = strength (decline arrested before support -- demand
    stepped in early).
    """
    recent = pivots[-lookback:] if len(pivots) > lookback else pivots
    highs = [(p, i) for p, k, i in recent if k == "high"]
    lows = [(p, i) for p, k, i in recent if k == "low"]
    if len(highs) < 2 or len(lows) < 2 or len(recent) < 2:
        return None

    top_ref = max(p for p, _ in highs)
    top_cluster = [p for p, _ in highs if _similar(p, top_ref, STRUCTURAL_TOUCH_TOLERANCE_PCT)]
    bottom_ref = min(p for p, _ in lows)
    bottom_cluster = [p for p, _ in lows if _similar(p, bottom_ref, STRUCTURAL_TOUCH_TOLERANCE_PCT)]
    if len(top_cluster) < 2 or len(bottom_cluster) < 2:
        return None

    top = sum(top_cluster) / len(top_cluster)
    bottom = sum(bottom_cluster) / len(bottom_cluster)
    if top <= bottom:
        return None

    approach_tol = (top - bottom) * (FAILURE_APPROACH_TOLERANCE_PCT / 100.0)
    prev_p, prev_k, _ = recent[-2]
    last_p, last_k, _ = recent[-1]

    if (
        prev_k == "low"
        and _similar(prev_p, bottom, STRUCTURAL_TOUCH_TOLERANCE_PCT)
        and last_k == "high"
        and last_p < top - approach_tol
    ):
        return {
            "type": "weakness",
            "range_top": round(top, 2),
            "range_bottom": round(bottom, 2),
            "failed_at": round(last_p, 2),
            "shortfall": round(top - last_p, 2),
            "note": "Rally from range support failed to reach range resistance -- underlying weakness",
        }
    if (
        prev_k == "high"
        and _similar(prev_p, top, STRUCTURAL_TOUCH_TOLERANCE_PCT)
        and last_k == "low"
        and last_p > bottom + approach_tol
    ):
        return {
            "type": "strength",
            "range_top": round(top, 2),
            "range_bottom": round(bottom, 2),
            "failed_at": round(last_p, 2),
            "shortfall": round(last_p - bottom, 2),
            "note": "Decline from range resistance failed to reach range support -- underlying strength",
        }
    return None


def detect_shortening_of_thrust(
    pivots: list[tuple[float, str, int]], lookback: int = PATTERN_LOOKBACK_PIVOTS
) -> dict | None:
    """>= 3 consecutive same-direction legs, each shorter than the last.
    Up-legs (low->high moves) shrinking = possible upside exhaustion;
    down-legs shrinking = possible downside exhaustion. No volume
    cross-check here (daily bar volume is noisy on its own; left as a
    caller-side confirmation using the already-available daily volume
    rather than baked into the detector's pass/fail)."""
    recent = pivots[-lookback:] if len(pivots) > lookback else pivots
    tail_len = SOT_MIN_LEGS * 2 + 1
    if len(recent) < tail_len:
        return None
    tail = recent[-tail_len:]

    legs: list[tuple[float, str]] = []
    for i in range(len(tail) - 1):
        p1, _, _ = tail[i]
        p2, k2, _ = tail[i + 1]
        legs.append((abs(p2 - p1), k2))

    up_legs = [size for size, k in legs if k == "high"]
    down_legs = [size for size, k in legs if k == "low"]

    for direction, leg_sizes, label in (
        (
            "bullish_exhaustion",
            up_legs,
            "Each successive rally is shorter than the last -- possible upside exhaustion",
        ),
        (
            "bearish_exhaustion",
            down_legs,
            "Each successive decline is shorter than the last -- possible downside exhaustion",
        ),
    ):
        if len(leg_sizes) >= SOT_MIN_LEGS:
            last_three = leg_sizes[-SOT_MIN_LEGS:]
            if all(last_three[i] > last_three[i + 1] for i in range(len(last_three) - 1)):
                return {
                    "type": direction,
                    "leg_sizes": [round(s, 2) for s in last_three],
                    "note": label,
                }
    return None


def detect_sos_sow_bar(daily_bars: list[dict], lookback: int = SOS_SOW_LOOKBACK) -> dict | None:
    """Sign of Strength / Sign of Weakness trigger bar: wide range,
    above-average volume, closing in the upper (SOS) or lower (SOW) third
    of its own range -- Wyckoff 2.0's actual entry trigger."""
    if len(daily_bars) < lookback + 1:
        return None
    window = daily_bars[-(lookback + 1) : -1]
    latest = daily_bars[-1]

    avg_range = sum(b["high"] - b["low"] for b in window) / len(window)
    avg_volume = sum(b.get("volume", 0) for b in window) / len(window)
    bar_range = latest["high"] - latest["low"]
    if avg_range <= 0 or avg_volume <= 0 or bar_range <= 0:
        return None

    close_position = (latest["close"] - latest["low"]) / bar_range
    range_ratio = bar_range / avg_range
    volume_ratio = latest.get("volume", 0) / avg_volume

    if not (range_ratio > SOS_SOW_RANGE_MULTIPLIER and volume_ratio > SOS_SOW_VOLUME_MULTIPLIER):
        return None

    if close_position >= 2.0 / 3.0:
        bar_type = "SOS"
    elif close_position <= 1.0 / 3.0:
        bar_type = "SOW"
    else:
        return None

    return {
        "type": bar_type,
        "close_position_pct": round(close_position * 100, 1),
        "range_vs_avg": round(range_ratio, 2),
        "volume_vs_avg": round(volume_ratio, 2),
    }
