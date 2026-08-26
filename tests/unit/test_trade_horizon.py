"""Unit tests for api.trade_blueprint.classify_trade_horizon — "Sniper
HUD" Phase 1 (2026-08-26). Pure function, no Redis/async involved --
see test_trade_blueprint.py for the orchestration-level wiring test.
"""

from __future__ import annotations

from api.trade_blueprint import classify_trade_horizon
from infusion_models.trade_horizon import TradeHorizon


def _base_kwargs(**overrides: object) -> dict[str, object]:
    """A deliberately UNCLASSIFIED baseline (no active signal) --
    every test overrides exactly the fields its scenario needs, so a
    passing test proves those specific fields drove the result."""
    defaults: dict[str, object] = {
        "has_active_signal": True,
        "direction": "BULL",
        "retest_status": "NO_BREAKOUT",
        "oi_buildup": "NEUTRAL",
        "wall_distance_pct": None,
        "rel_vol_20d": 1.0,
        "ltp": 100.0,
        "day_high": 101.0,
        "day_low": 99.0,
        "atr_14": 1.0,
        "mtf_5m": None,
        "mtf_15m": None,
        "mtf_1h": None,
        "mtf_1d": None,
        "accumulation_base": False,
        "vah_20d": None,
        "val_20d": None,
        "signal_hour_ist": None,
    }
    defaults.update(overrides)
    return defaults


def test_no_active_signal_is_unclassified_regardless_of_everything_else() -> None:
    kwargs = _base_kwargs(has_active_signal=False, rel_vol_20d=10.0, oi_buildup="SHORT_COVERING")
    assert classify_trade_horizon(**kwargs) == TradeHorizon.UNCLASSIFIED


def test_scalp_bull_short_covering_near_wall_high_rvol() -> None:
    kwargs = _base_kwargs(
        direction="BULL",
        rel_vol_20d=3.0,
        oi_buildup="SHORT_COVERING",
        wall_distance_pct=0.5,
        mtf_5m="BULL",
    )
    assert classify_trade_horizon(**kwargs) == TradeHorizon.SCALP


def test_scalp_bear_long_unwinding_mirrors_the_bull_case() -> None:
    kwargs = _base_kwargs(
        direction="BEAR",
        rel_vol_20d=3.0,
        oi_buildup="LONG_UNWINDING",
        wall_distance_pct=0.5,
        mtf_5m="BEAR",
    )
    assert classify_trade_horizon(**kwargs) == TradeHorizon.SCALP


def test_scalp_requires_rvol_above_threshold_not_just_at_it() -> None:
    kwargs = _base_kwargs(
        direction="BULL",
        rel_vol_20d=2.5,  # exactly at the spec's floor, not above it
        oi_buildup="SHORT_COVERING",
        wall_distance_pct=0.5,
        mtf_5m="BULL",
    )
    assert classify_trade_horizon(**kwargs) != TradeHorizon.SCALP


def test_scalp_requires_the_wall_to_actually_be_close() -> None:
    kwargs = _base_kwargs(
        direction="BULL",
        rel_vol_20d=3.0,
        oi_buildup="SHORT_COVERING",
        wall_distance_pct=2.0,  # spec says < 1%
        mtf_5m="BULL",
    )
    assert classify_trade_horizon(**kwargs) != TradeHorizon.SCALP


def test_intraday_breakout_in_flight_1h_and_15m_aligned_atr_used() -> None:
    kwargs = _base_kwargs(
        direction="BULL",
        retest_status="RETEST_HELD",
        oi_buildup="LONG_BUILDUP",
        mtf_15m="BULL",
        mtf_1h="BULL",
        day_high=110.0,
        day_low=100.0,
        atr_14=10.0,  # range (10) >= atr*0.8 (8)
    )
    assert classify_trade_horizon(**kwargs) == TradeHorizon.INTRADAY


def test_intraday_does_not_fire_on_no_breakout_even_if_everything_else_matches() -> None:
    kwargs = _base_kwargs(
        direction="BULL",
        retest_status="NO_BREAKOUT",
        oi_buildup="LONG_BUILDUP",
        mtf_15m="BULL",
        mtf_1h="BULL",
        day_high=110.0,
        day_low=100.0,
        atr_14=10.0,
    )
    assert classify_trade_horizon(**kwargs) == TradeHorizon.UNCLASSIFIED


def test_btst_late_session_near_day_high_room_to_wall() -> None:
    kwargs = _base_kwargs(
        direction="BULL",
        ltp=109.5,
        day_high=110.0,
        day_low=100.0,  # (110-109.5)/10 = 0.05 <= 0.15 fraction
        wall_distance_pct=2.0,  # spec says > 1.5%
        signal_hour_ist=14.5,
    )
    assert classify_trade_horizon(**kwargs) == TradeHorizon.BTST


def test_btst_does_not_fire_before_the_cutoff_hour() -> None:
    kwargs = _base_kwargs(
        direction="BULL",
        ltp=109.5,
        day_high=110.0,
        day_low=100.0,
        wall_distance_pct=2.0,
        signal_hour_ist=13.99,
    )
    assert classify_trade_horizon(**kwargs) != TradeHorizon.BTST


def test_swing_bull_clears_20d_vah_with_buildup_and_daily_alignment() -> None:
    kwargs = _base_kwargs(
        direction="BULL",
        accumulation_base=True,
        vah_20d=95.0,
        ltp=100.0,  # clears the 20d VAH
        oi_buildup="LONG_BUILDUP",
        mtf_1d="BULL",
    )
    assert classify_trade_horizon(**kwargs) == TradeHorizon.SWING


def test_swing_bear_clears_20d_val_with_short_buildup_and_daily_alignment() -> None:
    kwargs = _base_kwargs(
        direction="BEAR",
        accumulation_base=True,
        val_20d=105.0,
        ltp=100.0,  # clears (below) the 20d VAL
        oi_buildup="SHORT_BUILDUP",
        mtf_1d="BEAR",
    )
    assert classify_trade_horizon(**kwargs) == TradeHorizon.SWING


def test_swing_requires_accumulation_base_not_just_a_value_area_break() -> None:
    kwargs = _base_kwargs(
        direction="BULL",
        accumulation_base=False,
        vah_20d=95.0,
        ltp=100.0,
        oi_buildup="LONG_BUILDUP",
        mtf_1d="BULL",
    )
    assert classify_trade_horizon(**kwargs) != TradeHorizon.SWING


def test_swing_takes_priority_over_a_simultaneously_matching_intraday_case() -> None:
    """SWING is checked first -- a setup that happens to also clear
    INTRADAY's bar should still be reported as the more structurally
    significant SWING call."""
    kwargs = _base_kwargs(
        direction="BULL",
        accumulation_base=True,
        vah_20d=95.0,
        ltp=100.0,
        oi_buildup="LONG_BUILDUP",
        mtf_1d="BULL",
        retest_status="RETEST_HELD",
        mtf_15m="BULL",
        mtf_1h="BULL",
        day_high=110.0,
        day_low=100.0,
        atr_14=10.0,
    )
    assert classify_trade_horizon(**kwargs) == TradeHorizon.SWING


def test_nothing_matches_falls_back_to_unclassified_not_a_guess() -> None:
    kwargs = _base_kwargs(direction="BULL", oi_buildup="NEUTRAL")
    assert classify_trade_horizon(**kwargs) == TradeHorizon.UNCLASSIFIED
