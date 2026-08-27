"""Unit tests for api.lifecycle_monitor's bar-resolution outcome logic
-- "Visual Tracking & Lifecycle" sprint (2026-08-27). Same "test the
pure math, not the live HTTP/Redis" posture as
tests/unit/test_outcome_tracker.py (archiver's own, analogous tracker)
and tests/unit/test_broker_sync.py.
"""

from __future__ import annotations

from typing import Any

from api.lifecycle_monitor import (
    configured_targets,
    decode_bar,
    is_bearish,
    resolve_bar_touch,
    resolve_trade,
    walk_trade,
)


def _bar(o: float, h: float, low: float, c: float, t: int = 1_700_000_000) -> dict[str, Any]:
    return {"t": t, "o": o, "h": h, "l": low, "c": c}


def test_decode_bar_reads_the_real_on_bar_json_shape() -> None:
    import json

    raw = json.dumps({"t": 1700000060, "o": 100.0, "h": 101.5, "l": 99.5, "c": 101.0, "v": 500})
    assert decode_bar(raw.encode()) == {"t": 1700000060, "o": 100.0, "h": 101.5, "l": 99.5, "c": 101.0}


def test_decode_bar_rejects_garbage_without_raising() -> None:
    assert decode_bar(b"not json") is None
    assert decode_bar(None) is None


def test_configured_targets_omits_unset_levels() -> None:
    assert configured_targets({"target1": 105, "target2": 0, "target3": 0}) == [("T1", 105.0)]
    assert configured_targets({"target1": 105, "target2": 110, "target3": 120}) == [
        ("T1", 105.0),
        ("T2", 110.0),
        ("T3", 120.0),
    ]


def test_is_bearish_reads_the_decision_text_first() -> None:
    assert is_bearish({"decision": "BUY PE"}, entry=100, target1=95) is True
    assert is_bearish({"decision": "BUY CE"}, entry=100, target1=105) is False


def test_is_bearish_falls_back_to_target_vs_entry_when_decision_is_ambiguous() -> None:
    assert is_bearish({"decision": "WAIT"}, entry=100, target1=90) is True
    assert is_bearish({"decision": "WAIT"}, entry=100, target1=110) is False


def test_resolve_bar_touch_picks_the_deepest_target_reached() -> None:
    bar = _bar(o=100, h=125, low=99, c=120)
    targets = [("T1", 105.0), ("T2", 115.0), ("T3", 120.0)]
    assert resolve_bar_touch(bar, stop=90, targets=targets, bearish=False) == "T3"


def test_resolve_bar_touch_ties_to_whichever_boundary_is_nearer_the_open() -> None:
    # Stop (90, 10 away) is farther than T1 (102, 2 away) -- target wins.
    bar = _bar(o=100, h=103, low=89, c=95)
    assert resolve_bar_touch(bar, stop=90, targets=[("T1", 102.0)], bearish=False) == "T1"

    # Now stop (99, 1 away) is nearer than the target (150, 50 away).
    bar2 = _bar(o=100, h=151, low=98, c=100)
    assert resolve_bar_touch(bar2, stop=99, targets=[("T1", 150.0)], bearish=False) == "STOP"


def test_resolve_bar_touch_no_touch_is_none() -> None:
    bar = _bar(o=100, h=103, low=98, c=102)
    assert resolve_bar_touch(bar, stop=90, targets=[("T1", 110.0)], bearish=False) is None


def test_walk_trade_stops_out_immediately_when_no_target_was_ever_reached() -> None:
    bars = [_bar(o=100, h=101, low=94, c=95, t=1)]
    outcome, resolving_bar = walk_trade(bars, stop=95, targets=[("T1", 110.0)], bearish=False)
    assert outcome == "STOP"
    assert resolving_bar is not None and resolving_bar["t"] == 1


def test_walk_trade_a_stop_after_a_banked_target_does_not_downgrade_the_win() -> None:
    bars = [
        _bar(o=100, h=111, low=99, c=110, t=1),  # T1 (110) reached first
        _bar(o=109, h=109, low=94, c=95, t=2),  # stop (95) reached later
    ]
    outcome, resolving_bar = walk_trade(bars, stop=95, targets=[("T1", 110.0)], bearish=False)
    assert outcome == "T1"
    assert resolving_bar is not None and resolving_bar["t"] == 1


def test_walk_trade_escalates_across_bars_to_the_deepest_target_reached() -> None:
    bars = [
        _bar(o=100, h=111, low=99, c=110, t=1),  # T1
        _bar(o=110, h=121, low=109, c=120, t=2),  # T2
    ]
    outcome, resolving_bar = walk_trade(
        bars, stop=90, targets=[("T1", 110.0), ("T2", 120.0)], bearish=False
    )
    assert outcome == "T2"
    assert resolving_bar is not None and resolving_bar["t"] == 2


def test_walk_trade_unresolved_when_nothing_touched_yet() -> None:
    bars = [_bar(o=100, h=103, low=98, c=102, t=1)]
    outcome, resolving_bar = walk_trade(bars, stop=90, targets=[("T1", 110.0)], bearish=False)
    assert outcome is None
    assert resolving_bar is None


def test_resolve_trade_reports_a_real_win_with_duration_and_exit_price() -> None:
    row = {
        "entry": 100.0,
        "stop": 90.0,
        "target1": 110.0,
        "target2": 0.0,
        "target3": 0.0,
        "decision": "BUY CE",
        "created_at_epoch": 1_700_000_000,
    }
    bars = [_bar(o=100, h=111, low=99, c=110, t=1_700_000_000 + 300)]  # 5 min later
    update = resolve_trade(row, bars, now_epoch=1_700_000_000 + 600)
    assert update is not None
    assert update["outcome"] == "WIN_T1"
    assert update["status"] == "CLOSED"
    assert update["duration"] == "5m"
    assert update["exit_price"] == 110.0


def test_resolve_trade_reports_a_loss() -> None:
    row = {
        "entry": 100.0,
        "stop": 90.0,
        "target1": 110.0,
        "decision": "BUY CE",
        "created_at_epoch": 1_700_000_000,
    }
    bars = [_bar(o=100, h=101, low=89, c=90, t=1_700_000_000 + 120)]
    update = resolve_trade(row, bars, now_epoch=1_700_000_000 + 600)
    assert update is not None
    assert update["outcome"] == "LOSS"


def test_resolve_trade_is_unresolved_before_the_ttl_with_no_touch() -> None:
    row = {
        "entry": 100.0,
        "stop": 90.0,
        "target1": 110.0,
        "decision": "BUY CE",
        "created_at_epoch": 1_700_000_000,
    }
    bars = [_bar(o=100, h=103, low=98, c=102, t=1_700_000_000 + 60)]
    update = resolve_trade(row, bars, now_epoch=1_700_000_000 + 60 * 60)  # 1h later, well under TTL
    assert update is None


def test_resolve_trade_is_missed_once_the_ttl_elapses_with_no_touch() -> None:
    row = {
        "entry": 100.0,
        "stop": 90.0,
        "target1": 110.0,
        "decision": "BUY CE",
        "created_at_epoch": 1_700_000_000,
    }
    bars = [_bar(o=100, h=103, low=98, c=102, t=1_700_000_000 + 60)]
    update = resolve_trade(row, bars, now_epoch=1_700_000_000 + 380 * 60)  # past 375-min TTL
    assert update is not None
    assert update["outcome"] == "MISSED"
    assert update["status"] == "CLOSED"


def test_resolve_trade_is_none_for_a_row_missing_created_at_epoch() -> None:
    # Pre-sprint rows never got the new field -- must be skipped, never
    # guessed at, per this module's own documented honesty rule.
    row = {"entry": 100.0, "stop": 90.0, "target1": 110.0, "decision": "BUY CE"}
    bars = [_bar(o=100, h=111, low=99, c=110, t=1)]
    assert resolve_trade(row, bars, now_epoch=1_700_000_000) is None


def test_resolve_trade_is_none_for_a_row_with_no_real_levels() -> None:
    row = {
        "entry": 0.0,
        "stop": 0.0,
        "target1": 0.0,
        "decision": "WAIT",
        "created_at_epoch": 1_700_000_000,
    }
    assert resolve_trade(row, [], now_epoch=1_700_000_000 + 600) is None
