"""Retest vs. rejection tracking for a broken fractal swing level.

Mathematical audit fix (§1.2, 2026-08-25): the only existing retest-
aware logic in this codebase was Virgin CPR's "no candle body has
closed back through this zone" rule (api/routes/mtf.py), scoped to
daily CPR zones only. Nothing tracked whether a broken INTRADAY swing
high/low (structure.py's swing_high_1/swing_low_1, BOS/CHOCH) gets
retested and holds, or gets retested and fails, versus never being
retested at all. This module closes that gap for the fractal-pivot
case specifically.

State machine (four states, matching the task's own spec exactly):
  NO_BREAKOUT     -- no tracked break in flight.
  PENDING_RETEST  -- a break just fired; price hasn't returned to the
                     level's retest band yet.
  RETEST_HELD     -- price wicked into the band, the candle BODY closed
                     back on the breakout side of the level, and volume
                     expanded versus the recent average -- a real,
                     confirmed retest-and-hold.
  RETEST_FAILED   -- price's candle BODY closed back inside the
                     PREVIOUS range (i.e. through the level, the wrong
                     way) -- the breakout is invalidated.

Pure function operating on SymbolState, same discipline as
structure.py/price.py -- no I/O, no Redis. Must run AFTER
update_structure() in the same tick, since it reads that call's fresh
structure_event/last_break_high/last_break_low output.
"""

from __future__ import annotations

from typing import Any

from feature_engine.state import SymbolState

# Retest zone half-width, in ATR -- band = [level - RETEST_BAND_ATR*atr,
# level + RETEST_BAND_ATR*atr]. Infusion's own calibration (the audit's
# own spec), not from a cited source.
RETEST_BAND_ATR = 0.25

# Volume must exceed this multiple of the recent (pre-retest) average to
# count as "expanding" -- a real, if modest, participation bar, not just
# any wick into the zone. Same "don't fabricate confirmation from noise"
# posture as the rest of this module's calibrated constants.
RETEST_VOLUME_EXPANSION_MULT = 1.2
RETEST_VOLUME_LOOKBACK = 5

# An armed-but-never-retested breakout reverts to NO_BREAKOUT after this
# many completed 1m bars -- an untested break from hours ago is a stale
# setup, not still "pending" (matches this codebase's own established
# TTL-over-indefinite-tracking pattern, e.g. scanner's
# options_hybrid_watch_ttl_min).
RETEST_TTL_BARS = 60


def _recent_volume_avg(state: SymbolState, exclude_last: bool) -> float:
    bars = list(state.recent_1m_bars)
    if exclude_last:
        bars = bars[:-1]
    window = bars[-RETEST_VOLUME_LOOKBACK:]
    if not window:
        return 0.0
    return sum(float(b.get("v", 0)) for b in window) / len(window)


def update_retest(state: SymbolState) -> None:
    """Advance the retest state machine by one completed 1m bar. Must be
    called after update_structure() in the same tick -- reads its fresh
    structure_event/last_break_high/last_break_low/trend_state output,
    plus the bar just appended to recent_1m_bars.
    """
    if not state.recent_1m_bars:
        return
    latest = state.recent_1m_bars[-1]
    close = float(latest.get("c", 0.0))
    high = float(latest.get("h", 0.0))
    low = float(latest.get("l", 0.0))

    # A fresh break this bar (re)arms tracking, regardless of whatever
    # was being tracked before -- a new break supersedes an old,
    # unresolved one rather than queuing behind it.
    if state.structure_event:
        if "Bullish" in state.last_event_label and state.last_break_high is not None:
            state.retest_status = "PENDING_RETEST"
            state.retest_level = state.last_break_high
            state.retest_direction = 1
            state.retest_armed_at_bar = state.completed_1m_bars
            return
        if "Bearish" in state.last_event_label and state.last_break_low is not None:
            state.retest_status = "PENDING_RETEST"
            state.retest_level = state.last_break_low
            state.retest_direction = -1
            state.retest_armed_at_bar = state.completed_1m_bars
            return

    if state.retest_direction == 0:
        return  # NO_BREAKOUT -- nothing being tracked

    # Staleness TTL -- an untested breakout from hours ago is a stale
    # setup, not still meaningfully "pending."
    if state.completed_1m_bars - state.retest_armed_at_bar > RETEST_TTL_BARS:
        state.retest_status = "NO_BREAKOUT"
        state.retest_level = 0.0
        state.retest_direction = 0
        return

    level = state.retest_level
    band = max(state.atr, 0.0) * RETEST_BAND_ATR

    if state.retest_direction == 1:
        # Bullish breakout: retest band sits just below/at the broken
        # swing high. A body close back BELOW the level is a failure
        # regardless of whether the band was ever touched.
        if close < level:
            state.retest_status = "RETEST_FAILED"
            state.retest_direction = 0
            return
        touched_band = low <= level + band
        if touched_band:
            avg_vol = _recent_volume_avg(state, exclude_last=True)
            expanding = avg_vol > 0 and float(latest.get("v", 0)) >= avg_vol * (
                RETEST_VOLUME_EXPANSION_MULT
            )
            if expanding:
                state.retest_status = "RETEST_HELD"
                state.retest_direction = 0
            # else: wicked in without real volume confirmation yet --
            # stay PENDING_RETEST, a later bar may still confirm it.
        return

    # Bearish breakout: mirror image.
    if close > level:
        state.retest_status = "RETEST_FAILED"
        state.retest_direction = 0
        return
    touched_band = high >= level - band
    if touched_band:
        avg_vol = _recent_volume_avg(state, exclude_last=True)
        expanding = avg_vol > 0 and float(latest.get("v", 0)) >= avg_vol * (
            RETEST_VOLUME_EXPANSION_MULT
        )
        if expanding:
            state.retest_status = "RETEST_HELD"
            state.retest_direction = 0


def retest_snapshot(state: SymbolState) -> dict[str, Any]:
    """Compact dict for FeatureVectorV1.ml_features."""
    return {
        "retest_status": state.retest_status,
        "retest_level": state.retest_level if state.retest_level > 0 else None,
        "retest_direction": state.retest_direction,
    }
