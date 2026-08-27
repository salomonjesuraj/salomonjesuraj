"""Unit tests for infusion_models.smc's structural helpers --
nearest_ob_or_fvg_level (refactored out of nearest_ob_or_fvg_distance_pct,
"Broker Sync & Active Position Intelligence" sprint, 2026-08-27) and
structural_invalidation (promoted from api/trade_blueprint.py's own
inline Fast Exit Logic the same sprint, now shared with
api/broker_sync.py's Reversal & Invalidation Watch).
"""

from __future__ import annotations

from infusion_models.smc import (
    nearest_ob_or_fvg_distance_pct,
    nearest_ob_or_fvg_level,
    structural_invalidation,
)


def test_nearest_ob_or_fvg_level_picks_the_validated_bullish_ob() -> None:
    features = {
        "ltp": 100.0,
        "order_block_bullish_validated": "True",
        "order_block_bullish_high": 98.0,
        "fvg_bullish_top": 95.0,
    }
    assert nearest_ob_or_fvg_level(features, bearish=False) == 98.0


def test_nearest_ob_or_fvg_level_falls_back_to_fvg_when_ob_not_validated() -> None:
    features = {"ltp": 100.0, "order_block_bullish_validated": "False", "fvg_bullish_top": 95.0}
    assert nearest_ob_or_fvg_level(features, bearish=False) == 95.0


def test_nearest_ob_or_fvg_level_is_none_with_no_zone_at_all() -> None:
    assert nearest_ob_or_fvg_level({"ltp": 100.0}, bearish=False) is None


def test_distance_pct_is_derived_from_the_same_level_function() -> None:
    features = {
        "ltp": 100.0,
        "order_block_bullish_validated": "True",
        "order_block_bullish_high": 99.0,
    }
    assert nearest_ob_or_fvg_distance_pct(features, bearish=False) == 1.0


def test_structural_invalidation_fires_fast_exit_when_bull_support_breaks() -> None:
    tags = structural_invalidation(
        bullish=True,
        ltp=94.0,
        support=95.0,
        resistance=110.0,
        channel_lower=90.0,
        channel_upper=115.0,
    )
    assert tags == ["FAST_EXIT"]


def test_structural_invalidation_fires_both_tags_when_channel_also_breaks() -> None:
    tags = structural_invalidation(
        bullish=True,
        ltp=88.0,
        support=95.0,
        resistance=110.0,
        channel_lower=90.0,
        channel_upper=115.0,
    )
    assert set(tags) == {"STRUCTURAL_BREAK", "FAST_EXIT"}


def test_structural_invalidation_mirrors_for_a_bearish_position() -> None:
    tags = structural_invalidation(
        bullish=False,
        ltp=120.0,
        support=90.0,
        resistance=110.0,
        channel_lower=85.0,
        channel_upper=115.0,
    )
    assert set(tags) == {"STRUCTURAL_BREAK", "FAST_EXIT"}


def test_structural_invalidation_is_silent_when_nothing_has_broken() -> None:
    tags = structural_invalidation(
        bullish=True,
        ltp=100.0,
        support=95.0,
        resistance=110.0,
        channel_lower=90.0,
        channel_upper=115.0,
    )
    assert tags == []


def test_structural_invalidation_skips_unknown_bounds_rather_than_treating_them_as_broken() -> None:
    tags = structural_invalidation(
        bullish=True,
        ltp=50.0,
        support=None,
        resistance=None,
        channel_lower=None,
        channel_upper=None,
    )
    assert tags == []
