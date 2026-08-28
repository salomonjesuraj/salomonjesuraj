"""Unit tests for api.routes.screener's pure Order Block/FVG proximity
helper -- "Unified Omni-Screener & Deep-Dive Interactivity" sprint
(2026-08-28). The bulk Redis-pipeline routes themselves are real I/O
this test suite has no business calling; only the deterministic
direction-picking logic built on top of infusion_models.smc's own
already-tested nearest_ob_or_fvg_level() is covered here.
"""

from __future__ import annotations

from api.routes.screener import _nearest_ob_fvg_either_direction


def _features(**kwargs: object) -> dict[str, object]:
    return dict(kwargs)


def test_picks_the_bullish_zone_when_only_it_is_validated() -> None:
    features = _features(order_block_bullish_validated=True, order_block_bullish_high=100.0)
    assert _nearest_ob_fvg_either_direction(features, ltp=105.0) == 100.0


def test_picks_the_bearish_zone_when_only_it_is_validated() -> None:
    features = _features(order_block_bearish_validated=True, order_block_bearish_low=110.0)
    assert _nearest_ob_fvg_either_direction(features, ltp=105.0) == 110.0


def test_picks_whichever_real_zone_is_closer_to_ltp() -> None:
    features = _features(
        order_block_bullish_validated=True,
        order_block_bullish_high=95.0,  # 10 away from ltp
        order_block_bearish_validated=True,
        order_block_bearish_low=108.0,  # 3 away from ltp
    )
    assert _nearest_ob_fvg_either_direction(features, ltp=105.0) == 108.0


def test_is_honestly_none_when_neither_zone_is_validated() -> None:
    assert _nearest_ob_fvg_either_direction(_features(), ltp=105.0) is None
