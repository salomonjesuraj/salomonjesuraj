"""Unit tests for the 4-quadrant OI buildup matrix — mathematical audit
fix §3.1, 2026-08-25.
"""

from __future__ import annotations

from api.futures import (
    OI_BUILDUP_DEADBAND_PCT,
    classify_oi_buildup,
    compute_futures_price_change,
)
from infusion_models.oi_buildup import OIBuildupType
from scanner.verdict_engine import _futures_positioning_family


def test_long_buildup_price_up_oi_up() -> None:
    assert classify_oi_buildup(2.0, 3.0) == OIBuildupType.LONG_BUILDUP


def test_short_covering_price_up_oi_down() -> None:
    assert classify_oi_buildup(2.0, -3.0) == OIBuildupType.SHORT_COVERING


def test_short_buildup_price_down_oi_up() -> None:
    assert classify_oi_buildup(-2.0, 3.0) == OIBuildupType.SHORT_BUILDUP


def test_long_unwinding_price_down_oi_down() -> None:
    assert classify_oi_buildup(-2.0, -3.0) == OIBuildupType.LONG_UNWINDING


def test_neutral_when_either_input_missing() -> None:
    assert classify_oi_buildup(None, 3.0) == OIBuildupType.NEUTRAL
    assert classify_oi_buildup(2.0, None) == OIBuildupType.NEUTRAL
    assert classify_oi_buildup(None, None) == OIBuildupType.NEUTRAL


def test_neutral_inside_the_deadband_on_either_axis() -> None:
    # Price move clears the deadband but OI move doesn't.
    small_oi = OI_BUILDUP_DEADBAND_PCT / 2
    assert classify_oi_buildup(2.0, small_oi) == OIBuildupType.NEUTRAL
    assert classify_oi_buildup(2.0, -small_oi) == OIBuildupType.NEUTRAL
    # Neither axis clears the deadband.
    small_price = OI_BUILDUP_DEADBAND_PCT / 2
    assert classify_oi_buildup(small_price, small_oi) == OIBuildupType.NEUTRAL


def test_exactly_on_the_deadband_boundary_is_neutral_not_directional() -> None:
    """Strict inequality in classify_oi_buildup -- a move exactly AT the
    deadband must not tip into a directional read."""
    assert classify_oi_buildup(OI_BUILDUP_DEADBAND_PCT, OI_BUILDUP_DEADBAND_PCT) == (
        OIBuildupType.NEUTRAL
    )


def test_compute_futures_price_change_matches_compute_oi_deltas_own_shape() -> None:
    assert compute_futures_price_change(102.0, 100.0) == {"futures_price_change_pct": 2.0}
    assert compute_futures_price_change(100.0, None) == {"futures_price_change_pct": None}
    assert compute_futures_price_change(100.0, 0.0) == {"futures_price_change_pct": None}


# ── Wired into verdict_engine's futures-positioning family ──────────────


def test_long_buildup_and_short_covering_both_vote_bullish() -> None:
    assert _futures_positioning_family({"oi_buildup": OIBuildupType.LONG_BUILDUP.value}) is True
    assert _futures_positioning_family({"oi_buildup": OIBuildupType.SHORT_COVERING.value}) is True


def test_short_buildup_and_long_unwinding_both_vote_bearish() -> None:
    """The exact gap the audit named: these two used to be unhandled
    None returns under the old basis_pct-only, 2-quadrant read."""
    assert _futures_positioning_family({"oi_buildup": OIBuildupType.SHORT_BUILDUP.value}) is False
    assert _futures_positioning_family({"oi_buildup": OIBuildupType.LONG_UNWINDING.value}) is False


def test_neutral_or_missing_abstains() -> None:
    assert _futures_positioning_family({"oi_buildup": OIBuildupType.NEUTRAL.value}) is None
    assert _futures_positioning_family({}) is None
