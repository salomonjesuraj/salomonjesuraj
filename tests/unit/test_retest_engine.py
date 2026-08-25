"""Unit tests for feature_engine.features.retest — mathematical audit
fix §1.2 (dedicated retest-vs-rejection engine for broken fractal
swing levels), 2026-08-25.
"""

from __future__ import annotations

from feature_engine.features.retest import RETEST_TTL_BARS, retest_snapshot, update_retest
from feature_engine.state import SymbolState


def _bar(o: float, h: float, low: float, c: float, v: float = 1000.0) -> dict:
    return {"o": o, "h": h, "l": low, "c": c, "v": v}


def _fresh_state() -> SymbolState:
    return SymbolState(symbol="TEST")


def test_no_breakout_by_default() -> None:
    state = _fresh_state()
    state.recent_1m_bars.append(_bar(100, 101, 99, 100.5))
    update_retest(state)
    assert state.retest_status == "NO_BREAKOUT"
    snap = retest_snapshot(state)
    assert snap == {"retest_status": "NO_BREAKOUT", "retest_level": None, "retest_direction": 0}


def test_a_fresh_bullish_break_arms_pending_retest() -> None:
    state = _fresh_state()
    state.structure_event = True
    state.last_event_label = "Bullish BOS"
    state.last_break_high = 105.0
    state.completed_1m_bars = 10
    state.recent_1m_bars.append(_bar(104, 106, 103, 105.5))
    update_retest(state)
    assert state.retest_status == "PENDING_RETEST"
    assert state.retest_level == 105.0
    assert state.retest_direction == 1
    assert state.retest_armed_at_bar == 10


def test_a_fresh_bearish_break_arms_pending_retest() -> None:
    state = _fresh_state()
    state.structure_event = True
    state.last_event_label = "Bearish CHOCH"
    state.last_break_low = 95.0
    state.recent_1m_bars.append(_bar(96, 97, 94, 95.5))
    update_retest(state)
    assert state.retest_status == "PENDING_RETEST"
    assert state.retest_level == 95.0
    assert state.retest_direction == -1


def test_bullish_retest_held_on_wick_plus_expanding_volume() -> None:
    state = _fresh_state()
    state.atr = 1.0  # band = +/-0.25
    # Prime the recent-volume average with 5 quiet bars.
    for _ in range(5):
        state.recent_1m_bars.append(_bar(105, 106, 104, 105.5, v=1000))
    state.retest_status = "PENDING_RETEST"
    state.retest_level = 105.0
    state.retest_direction = 1
    state.retest_armed_at_bar = 0
    state.completed_1m_bars = 3

    # Wicks down into the band (low 105.1 <= 105.0+0.25), body closes
    # back ABOVE the level, and volume clearly expands.
    state.recent_1m_bars.append(_bar(105.8, 106.0, 105.1, 105.9, v=2000))
    update_retest(state)
    assert state.retest_status == "RETEST_HELD"
    assert state.retest_direction == 0  # tracking closes out once resolved


def test_bullish_retest_stays_pending_without_volume_confirmation() -> None:
    state = _fresh_state()
    state.atr = 1.0
    for _ in range(5):
        state.recent_1m_bars.append(_bar(105, 106, 104, 105.5, v=1000))
    state.retest_status = "PENDING_RETEST"
    state.retest_level = 105.0
    state.retest_direction = 1
    state.retest_armed_at_bar = 0
    state.completed_1m_bars = 3

    # Wicks into the band, body holds above the level, but volume is
    # thin (not 1.2x the recent average) -- real touch, no confirmation
    # yet.
    state.recent_1m_bars.append(_bar(105.8, 106.0, 105.1, 105.9, v=900))
    update_retest(state)
    assert state.retest_status == "PENDING_RETEST"
    assert state.retest_direction == 1  # still tracking


def test_bullish_retest_failed_when_body_closes_back_through_the_level() -> None:
    state = _fresh_state()
    state.atr = 1.0
    state.retest_status = "PENDING_RETEST"
    state.retest_level = 105.0
    state.retest_direction = 1
    state.retest_armed_at_bar = 0
    state.completed_1m_bars = 3

    state.recent_1m_bars.append(_bar(105.5, 105.6, 103.0, 104.0))  # body closes below 105
    update_retest(state)
    assert state.retest_status == "RETEST_FAILED"
    assert state.retest_direction == 0


def test_bearish_retest_held_mirrors_the_bullish_case() -> None:
    state = _fresh_state()
    state.atr = 1.0
    for _ in range(5):
        state.recent_1m_bars.append(_bar(95, 96, 94, 95.0, v=1000))
    state.retest_status = "PENDING_RETEST"
    state.retest_level = 95.0
    state.retest_direction = -1
    state.retest_armed_at_bar = 0
    state.completed_1m_bars = 3

    # Wicks up into the band (high 95.2 >= 95.0-0.25), body holds below.
    state.recent_1m_bars.append(_bar(94.2, 95.2, 94.0, 94.1, v=2000))
    update_retest(state)
    assert state.retest_status == "RETEST_HELD"


def test_bearish_retest_failed_when_body_closes_back_above_the_level() -> None:
    state = _fresh_state()
    state.atr = 1.0
    state.retest_status = "PENDING_RETEST"
    state.retest_level = 95.0
    state.retest_direction = -1
    state.retest_armed_at_bar = 0
    state.completed_1m_bars = 3

    state.recent_1m_bars.append(_bar(94.5, 97.0, 94.0, 96.0))  # body closes above 95
    update_retest(state)
    assert state.retest_status == "RETEST_FAILED"
    assert state.retest_direction == 0


def test_a_new_break_supersedes_an_unresolved_older_one() -> None:
    state = _fresh_state()
    state.retest_status = "PENDING_RETEST"
    state.retest_level = 100.0
    state.retest_direction = 1
    state.retest_armed_at_bar = 0

    state.structure_event = True
    state.last_event_label = "Bullish BOS"
    state.last_break_high = 110.0
    state.completed_1m_bars = 20
    state.recent_1m_bars.append(_bar(109, 111, 108, 110.5))
    update_retest(state)
    assert state.retest_level == 110.0  # the new level, not the stale 100.0
    assert state.retest_status == "PENDING_RETEST"


def test_an_untested_breakout_expires_after_the_ttl() -> None:
    state = _fresh_state()
    state.retest_status = "PENDING_RETEST"
    state.retest_level = 100.0
    state.retest_direction = 1
    state.retest_armed_at_bar = 5
    state.completed_1m_bars = 5 + RETEST_TTL_BARS + 1
    state.recent_1m_bars.append(_bar(103, 104, 102, 103.5))  # nowhere near the band

    update_retest(state)
    assert state.retest_status == "NO_BREAKOUT"
    assert state.retest_direction == 0


def test_no_op_with_no_bars_yet() -> None:
    state = _fresh_state()
    update_retest(state)  # must not raise
    assert state.retest_status == "NO_BREAKOUT"
