"""Unit tests for archiver.tracker's bar-resolution outcome logic.

Covers the pipeline-audit fix (2026-08-25, finding C2): outcome
resolution moved from a 30-second LTP point-sample poll to walking real
completed 1-minute bar high/low, with an explicit deterministic
tie-break when a single bar's range spans both target and stop.
"""

from __future__ import annotations

import json
from typing import Any

from archiver.tracker import decode_bar, first_touch


def _bar(o: float, h: float, low: float, c: float, t: int = 1_700_000_000) -> dict[str, Any]:
    return {"t": t, "o": o, "h": h, "l": low, "c": c}


def test_decode_bar_reads_the_real_on_bar_json_shape() -> None:
    """Matches feature_engine/main.py's on_bar() payload exactly:
    {"t": ..., "o": ..., "h": ..., "l": ..., "c": ..., "v": ...}."""
    raw = json.dumps({"t": 1700000060, "o": 100.0, "h": 101.5, "l": 99.5, "c": 101.0, "v": 500})
    bar = decode_bar(raw.encode())
    assert bar == {"t": 1700000060, "o": 100.0, "h": 101.5, "l": 99.5, "c": 101.0}


def test_decode_bar_rejects_garbage_without_raising() -> None:
    assert decode_bar(b"not json") is None
    assert decode_bar(None) is None


def test_bullish_target_hit_when_high_clears_target() -> None:
    bar = _bar(o=100, h=106, low=99, c=105)
    assert first_touch(bar, target=105, stop=95, bearish=False) == "TARGET_HIT"


def test_bullish_stop_hit_when_low_breaches_stop() -> None:
    bar = _bar(o=100, h=101, low=94, c=95)
    assert first_touch(bar, target=110, stop=95, bearish=False) == "STOP_HIT"


def test_bullish_no_touch_stays_none() -> None:
    bar = _bar(o=100, h=103, low=98, c=102)
    assert first_touch(bar, target=110, stop=90, bearish=False) is None


def test_bearish_target_hit_when_low_clears_target_downward() -> None:
    bar = _bar(o=100, h=101, low=93, c=94)
    assert first_touch(bar, target=95, stop=105, bearish=True) == "TARGET_HIT"


def test_bearish_stop_hit_when_high_breaches_stop_upward() -> None:
    bar = _bar(o=100, h=106, low=99, c=105)
    assert first_touch(bar, target=90, stop=105, bearish=True) == "STOP_HIT"


def test_both_boundaries_touched_ties_to_the_one_nearer_the_open() -> None:
    """The exact scenario audit finding C2 called out: a single bar
    (or, in the old design, a single 30s poll gap) spans both target
    and stop. The nearer-to-open level must win, deterministically --
    not whichever the code happens to check first."""
    # Open at 100, target 102 (2 away), stop 90 (10 away) -- target is
    # nearer, so under any continuous path from the open it must be
    # reached before stop even though the bar's full range covers both.
    bar = _bar(o=100, h=103, low=89, c=95)
    assert first_touch(bar, target=102, stop=90, bearish=False) == "TARGET_HIT"

    # Same bar, but stop is now the nearer level (target far away).
    bar2 = _bar(o=100, h=103, low=89, c=95)
    assert first_touch(bar2, target=150, stop=99, bearish=False) == "STOP_HIT"


def test_both_boundaries_touched_tie_break_is_symmetric_for_bearish() -> None:
    bar = _bar(o=100, h=111, low=97, c=105)
    # bearish: target below, stop above. Target (98, 2 away) nearer than
    # stop (110, 10 away).
    assert first_touch(bar, target=98, stop=110, bearish=True) == "TARGET_HIT"
