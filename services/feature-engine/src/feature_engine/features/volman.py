"""Volman-style entry timing — buildup detection, signal-bar qualification,
and a 1-tick breakout trigger, layered ON TOP of Infusion's existing zones
(supply/demand from features/zones.py, or Order Blocks from features/ict.py)
as a TIMING filter for when to actually enter — not a new zone detector of
its own. Matches "Understanding Price Action" (Bob Volman), 5-minute
EUR/USD scalping — explicitly intraday, so Infusion's 1-minute bars are
the right timeframe here (unlike Phase 5's daily-only chart patterns).

The book's core insight: a genuine breakout needs visible BUILDUP (a tight
cluster of small-range bars right at the boundary, "pre-breakout tension")
before it breaks. Three tiers, cited directly from the source:
  - "False break" — no buildup at all before the boundary gave way. Widest,
    least-justifiable stop. Don't trade it.
  - "Tease break" — some buildup, but it happened mid-range, not right at
    the boundary. Tends to fail first, then re-attempt.
  - "Proper break" — tight buildup directly AT the boundary. Tightest,
    best-justified stop; the only "first-instance" setup worth taking
    directly.

Stop/target sizing note: Volman's fixed 10/20-pip bracket is calibrated
for 5-min EUR/USD and doesn't transfer directly to Infusion's F&O
underlyings — Infusion already has ATR-based target sizing
(pine_confidence.practical_option_targets) for that. This module only
answers the TIMING question (is this break worth acting on right now,
and how well-justified is it), not target sizing.
"""

from __future__ import annotations

from typing import Any

from feature_engine.state import SymbolState

BUILDUP_TIGHT_RANGE_ATR_FRAC = 0.5  # a bar counts as "tight" if its range < this fraction of ATR -- Infusion's own calibration, source gives no exact number
BUILDUP_MIN_BARS = 3  # minimum consecutive tight bars to call it a genuine buildup
BUILDUP_AT_BOUNDARY_ATR_FRAC = (
    0.3  # how close the buildup cluster must sit to the boundary to count as "proper" vs "tease"
)


def _bar_range(bar: dict[str, Any]) -> float:
    return max(0.0, float(bar["h"]) - float(bar["l"]))


def _is_tight(bar: dict[str, Any], atr: float) -> bool:
    if atr <= 0:
        return False
    return _bar_range(bar) < atr * BUILDUP_TIGHT_RANGE_ATR_FRAC


def detect_buildup(
    bars: list[dict[str, Any]], boundary: float, atr: float, bullish: bool
) -> dict[str, Any] | None:
    """Looks for a tight cluster in the bars BEFORE the most recent one
    (the cluster is the setup; the most recent bar is checked separately
    by check_entry_trigger as the potential break). Returns the buildup
    quality + the signal bar (the cluster's last bar) — or None when
    there's no genuine buildup at all (a "false break" candidate, per
    Volman's own rule: don't trade it).
    """
    if atr <= 0 or len(bars) < BUILDUP_MIN_BARS + 1:
        return None

    cluster = bars[
        -(BUILDUP_MIN_BARS + 1) : -1
    ]  # excludes the newest bar, which is the potential trigger
    if not all(_is_tight(b, atr) for b in cluster):
        return None

    cluster_high = max(b["h"] for b in cluster)
    cluster_low = min(b["l"] for b in cluster)
    cluster_mid = (cluster_high + cluster_low) / 2.0
    distance_to_boundary = abs(cluster_mid - boundary)
    quality = "proper" if distance_to_boundary <= atr * BUILDUP_AT_BOUNDARY_ATR_FRAC else "tease"

    signal_bar = cluster[-1]
    signal_range = _bar_range(signal_bar)
    if signal_range <= 0:
        return None
    close_position = (signal_bar["c"] - signal_bar["l"]) / signal_range

    # Volman's signal-bar filter: its own close must sit in the
    # anticipated breakout direction (upper part for bullish, lower for
    # bearish) -- never short below a strong bullish bar or go long above
    # a strong bearish bar.
    if bullish and close_position < 0.5:
        return None
    if not bullish and close_position > 0.5:
        return None

    return {
        "quality": quality,
        "signal_bar_high": signal_bar["h"],
        "signal_bar_low": signal_bar["l"],
        "cluster_high": round(cluster_high, 4),
        "cluster_low": round(cluster_low, 4),
        "close_position_pct": round(close_position * 100, 1),
    }


def check_entry_trigger(
    bars: list[dict[str, Any]], buildup: dict[str, Any] | None, bullish: bool
) -> bool:
    """Has the MOST RECENT bar broken the signal bar's extreme in the
    anticipated direction — Volman's actual entry trigger (a 1-tick break,
    acted on at market, not waiting for the breaking bar's own close)."""
    if not buildup or not bars:
        return False
    current_bar = bars[-1]
    if bullish:
        return bool(current_bar["h"] > buildup["signal_bar_high"])
    return bool(current_bar["l"] < buildup["signal_bar_low"])


def volman_snapshot(state: SymbolState) -> dict[str, Any]:
    """Checks Volman's buildup/signal-bar/trigger sequence against
    whichever of Infusion's existing zones are currently active — supply/
    demand (zones.py) and validated Order Blocks (ict.py). Returns the
    first genuine buildup+trigger read found; empty fields if none of the
    active zones currently show one (which is the common case — buildup
    is a specific, comparatively rare precondition, not something that
    should fire on most bars).
    """
    bars = list(state.recent_1m_bars)
    atr = state.atr
    candidates: list[tuple[float, bool, str]] = []

    if state.demand_zone is not None:
        top, _bottom, _ = state.demand_zone
        candidates.append((top, True, "demand_zone"))
    if state.supply_zone is not None:
        _top, bottom, _ = state.supply_zone
        candidates.append((bottom, False, "supply_zone"))
    if state.order_block_bullish is not None:
        _low, high, _bar, validated = state.order_block_bullish
        if validated:
            candidates.append((high, True, "order_block_bullish"))
    if state.order_block_bearish is not None:
        low, _high, _bar, validated = state.order_block_bearish
        if validated:
            candidates.append((low, False, "order_block_bearish"))

    for boundary, bullish, source in candidates:
        buildup = detect_buildup(bars, boundary, atr, bullish)
        if buildup is None:
            continue
        triggered = check_entry_trigger(bars, buildup, bullish)
        return {
            "volman_source": source,
            "volman_bullish": bullish,
            "volman_quality": buildup["quality"],
            "volman_close_position_pct": buildup["close_position_pct"],
            "volman_entry_triggered": triggered,
        }

    return {
        "volman_source": None,
        "volman_bullish": None,
        "volman_quality": None,
        "volman_close_position_pct": None,
        "volman_entry_triggered": False,
    }
