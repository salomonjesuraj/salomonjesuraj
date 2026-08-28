"""Unit tests for api.routes.market's per-strike option chain helpers --
"Unified Screener & Deep-Dive Interactivity" sprint (2026-08-28). Pure
field-extraction functions only; the real Upstox HTTP fetch
(_fetch_full_option_chain) is out of scope for a unit test, same "test
the pure math, not the live HTTP" posture as this codebase's other
broker-facing test files.
"""

from __future__ import annotations

from api.routes.market import _leg_snapshot, _num_or_zero


def test_num_or_zero_reads_a_real_value() -> None:
    assert _num_or_zero({"delta": 0.42}, "delta") == 0.42


def test_num_or_zero_is_zero_for_missing_or_null() -> None:
    assert _num_or_zero({}, "delta") == 0.0
    assert _num_or_zero({"delta": None}, "delta") == 0.0


def test_leg_snapshot_reads_the_real_upstox_market_data_and_greeks_shape() -> None:
    row = {
        "call_options": {
            "market_data": {"ltp": 12.5, "oi": 450000, "volume": 12000},
            "option_greeks": {"iv": 24.5, "delta": 0.55, "gamma": 0.002, "theta": -3.1, "vega": 1.2},
        }
    }
    snapshot = _leg_snapshot(row, "call_options")
    assert snapshot == {
        "ltp": 12.5,
        "oi": 450000.0,
        "volume": 12000.0,
        "iv": 24.5,
        "delta": 0.55,
        "gamma": 0.002,
        "theta": -3.1,
        "vega": 1.2,
    }


def test_leg_snapshot_is_all_zero_for_a_missing_leg_not_a_crash() -> None:
    # A deep OTM/ITM strike can genuinely have no put (or call) leg quoted
    # at all -- must not raise, and must not fabricate a non-zero number.
    assert _leg_snapshot({}, "put_options") == {
        "ltp": 0.0,
        "oi": 0.0,
        "volume": 0.0,
        "iv": 0.0,
        "delta": 0.0,
        "gamma": 0.0,
        "theta": 0.0,
        "vega": 0.0,
    }
