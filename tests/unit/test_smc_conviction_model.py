"""Unit tests for the SMC Inception Conviction Model (scanner/scoring.py,
2026-08-27 rewrite). Covers the pure detection/scoring functions plus
compute_conviction()'s full assembly -- see scoring.py's own module
docstring for the two real data-availability findings (1m-only OB/FVG,
book-imbalance as the honest CVD substitute) these tests are written
against.
"""

from __future__ import annotations

import pytest
from scanner.scoring import (
    CHASEABLE_GRADE_CAP,
    choch_trigger_score,
    compute_conviction,
    htf_alignment_score,
    nearest_ob_or_fvg_distance_pct,
    oi_squeeze_score,
    order_flow_divergence_score,
    smc_structure_score,
)


def _bull_ob(low: float, high: float, validated: bool = True) -> dict[str, object]:
    return {
        "order_block_bullish_low": low,
        "order_block_bullish_high": high,
        "order_block_bullish_validated": validated,
    }


def _bear_ob(low: float, high: float, validated: bool = True) -> dict[str, object]:
    return {
        "order_block_bearish_low": low,
        "order_block_bearish_high": high,
        "order_block_bearish_validated": validated,
    }


# ── nearest_ob_or_fvg_distance_pct ───────────────────────────────────


def test_bullish_distance_measured_to_validated_ob_top() -> None:
    features = {"ltp": 100.0, **_bull_ob(low=97.0, high=99.5)}
    distance = nearest_ob_or_fvg_distance_pct(features, bearish=False)
    assert distance == pytest.approx(0.5)


def test_unvalidated_ob_does_not_count_as_a_zone() -> None:
    """An OB candidate that hasn't yet closed beyond its own high/low
    is not real yet, per ict.py's own validation rule -- scoring must
    not credit a zone that isn't confirmed."""
    features = {"ltp": 100.5, **_bull_ob(low=98.0, high=100.0, validated=False)}
    assert nearest_ob_or_fvg_distance_pct(features, bearish=False) is None


def test_bearish_distance_measured_to_validated_ob_bottom() -> None:
    features = {"ltp": 199.0, **_bear_ob(low=200.0, high=205.0)}
    distance = nearest_ob_or_fvg_distance_pct(features, bearish=True)
    assert distance == pytest.approx(0.5025125628140703)


def test_fvg_counts_as_a_zone_even_without_an_order_block() -> None:
    features = {"ltp": 100.0, "fvg_bullish_top": 99.0}
    distance = nearest_ob_or_fvg_distance_pct(features, bearish=False)
    assert distance == pytest.approx(1.0)


def test_nearest_of_two_candidate_zones_wins() -> None:
    features = {
        "ltp": 100.0,
        **_bull_ob(low=90.0, high=95.0),  # 5.0 away
        "fvg_bullish_top": 99.0,  # 1.0 away -- nearer
    }
    distance = nearest_ob_or_fvg_distance_pct(features, bearish=False)
    assert distance == pytest.approx(1.0)


def test_no_zone_at_all_returns_none_not_a_fabricated_zero() -> None:
    assert nearest_ob_or_fvg_distance_pct({"ltp": 100.0}, bearish=False) is None


def test_zero_or_missing_ltp_returns_none() -> None:
    assert nearest_ob_or_fvg_distance_pct({"ltp": 0.0, **_bull_ob(98, 100)}, bearish=False) is None
    assert nearest_ob_or_fvg_distance_pct({**_bull_ob(98, 100)}, bearish=False) is None


# ── smc_structure_score (OB/FVG proximity 0-20 + liquidity sweep 0-15) ──


def test_full_credit_within_half_a_percent() -> None:
    features = {"ltp": 100.0, **_bull_ob(low=98.0, high=99.6)}  # 0.4% away
    points, distance = smc_structure_score(features, bearish=False)
    assert points == 20.0
    assert distance is not None and distance < 0.5


def test_zero_credit_at_or_beyond_one_point_five_percent() -> None:
    features = {"ltp": 100.0, **_bull_ob(low=90.0, high=98.5)}  # 1.5% away
    points, _ = smc_structure_score(features, bearish=False)
    assert points == 0.0


def test_linear_decay_at_the_midpoint_of_the_band() -> None:
    """At exactly 1.0% (the midpoint of 0.5%-1.5%), credit should be
    roughly half of the 20-point maximum."""
    features = {"ltp": 100.0, **_bull_ob(low=95.0, high=99.0)}  # 1.0% away
    points, distance = smc_structure_score(features, bearish=False)
    assert distance == pytest.approx(1.0)
    assert 9.0 < points < 11.0


def test_sellside_sweep_adds_bullish_points() -> None:
    features = {"ltp": 100.0, "last_liquidity_sweep": "sellside"}
    points, _ = smc_structure_score(features, bearish=False)
    assert points == 15.0


def test_buyside_sweep_does_not_count_for_a_bullish_signal() -> None:
    features = {"ltp": 100.0, "last_liquidity_sweep": "buyside"}
    points, _ = smc_structure_score(features, bearish=False)
    assert points == 0.0


def test_ob_proximity_and_sweep_both_contribute() -> None:
    features = {
        "ltp": 100.0,
        **_bull_ob(low=98.0, high=99.8),  # well within 0.5% -> 20 pts
        "last_liquidity_sweep": "sellside",  # +15 pts
    }
    points, _ = smc_structure_score(features, bearish=False)
    assert points == 35.0  # the full SMC Structure bucket


# ── order_flow_divergence_score (honest book-imbalance proxy for CVD) ──


def test_scores_zero_without_compression_even_if_pressure_is_building() -> None:
    """The whole point is INCEPTION -- pressure building while price is
    still flat. Pressure building during an already-expanded range
    doesn't qualify."""
    features = {"book_imbalance": 0.4, "book_imbalance_ema": 0.1, "bb_width": 0.05}
    assert order_flow_divergence_score(features, bearish=False) == 0.0


def test_scores_full_when_bullish_pressure_builds_during_a_squeeze() -> None:
    features = {
        "book_imbalance": 0.4,
        "book_imbalance_ema": 0.1,
        "squeeze_state": "COILED",
    }
    assert order_flow_divergence_score(features, bearish=False) == 20.0


def test_scores_zero_when_pressure_is_building_the_wrong_direction() -> None:
    features = {
        "book_imbalance": -0.4,
        "book_imbalance_ema": -0.1,
        "squeeze_state": "COILED",
    }
    assert order_flow_divergence_score(features, bearish=False) == 0.0


def test_requires_the_dominant_side_not_just_a_slight_uptick_off_a_deep_negative() -> None:
    """Rising off a very negative base while still net-negative isn't
    genuine bullish dominance yet."""
    features = {
        "book_imbalance": -0.3,
        "book_imbalance_ema": -0.6,  # rising, but still net-negative
        "squeeze_state": "COILED",
    }
    assert order_flow_divergence_score(features, bearish=False) == 0.0


def test_missing_book_imbalance_scores_zero_not_a_guess() -> None:
    assert order_flow_divergence_score({"squeeze_state": "COILED"}, bearish=False) == 0.0


def test_bb_width_compression_counts_the_same_as_squeeze_state() -> None:
    features = {"book_imbalance": 0.4, "book_imbalance_ema": 0.1, "bb_width": 0.01}
    assert order_flow_divergence_score(features, bearish=False) == 20.0


# ── oi_squeeze_score ──────────────────────────────────────────────────


def test_long_buildup_during_squeeze_scores_full_for_bullish() -> None:
    features = {"squeeze_state": "COILED"}
    futures_data = {"oi_buildup": "LONG_BUILDUP"}
    assert oi_squeeze_score(features, futures_data, bearish=False) == 15.0


def test_long_buildup_without_a_squeeze_scores_zero() -> None:
    """Buildup already accompanying an expanded range isn't the
    pre-breakout footprint this component rewards."""
    features = {"squeeze_state": "", "bb_width": 0.05}
    futures_data = {"oi_buildup": "LONG_BUILDUP"}
    assert oi_squeeze_score(features, futures_data, bearish=False) == 0.0


def test_no_futures_data_scores_zero_not_neutral() -> None:
    assert oi_squeeze_score({"squeeze_state": "COILED"}, None, bearish=False) == 0.0


def test_wrong_direction_buildup_scores_zero() -> None:
    features = {"squeeze_state": "COILED"}
    futures_data = {"oi_buildup": "SHORT_BUILDUP"}
    assert oi_squeeze_score(features, futures_data, bearish=False) == 0.0


# ── htf_alignment_score ───────────────────────────────────────────────


def test_both_1h_and_1d_bull_scores_full() -> None:
    mtf_data = {"timeframes": {"1H": {"state": "BULL"}, "1D": {"state": "BULL"}}}
    assert htf_alignment_score(mtf_data, bearish=False) == 15.0


def test_only_one_timeframe_aligned_scores_partial() -> None:
    mtf_data = {"timeframes": {"1H": {"state": "BULL"}, "1D": {"state": "RANGE"}}}
    assert htf_alignment_score(mtf_data, bearish=False) == 8.0


def test_neither_aligned_scores_zero() -> None:
    mtf_data = {"timeframes": {"1H": {"state": "BEAR"}, "1D": {"state": "BEAR"}}}
    assert htf_alignment_score(mtf_data, bearish=False) == 0.0


def test_missing_mtf_cache_scores_zero_deliberately_not_neutral_credit() -> None:
    assert htf_alignment_score(None, bearish=False) == 0.0


# ── choch_trigger_score ───────────────────────────────────────────────


def test_choch_inside_the_zone_scores_full() -> None:
    features = {"last_event_label": "Bullish CHOCH"}
    assert choch_trigger_score(features, bearish=False, ob_distance_pct=0.3) == 15.0


def test_choch_far_from_any_zone_scores_zero() -> None:
    features = {"last_event_label": "Bullish CHOCH"}
    assert choch_trigger_score(features, bearish=False, ob_distance_pct=3.0) == 0.0


def test_choch_with_no_zone_at_all_scores_zero() -> None:
    features = {"last_event_label": "Bullish CHOCH"}
    assert choch_trigger_score(features, bearish=False, ob_distance_pct=None) == 0.0


def test_wrong_direction_choch_scores_zero() -> None:
    features = {"last_event_label": "Bearish CHOCH"}
    assert choch_trigger_score(features, bearish=False, ob_distance_pct=0.3) == 0.0


def test_bos_is_not_a_choch_and_does_not_count() -> None:
    features = {"last_event_label": "Bullish BOS"}
    assert choch_trigger_score(features, bearish=False, ob_distance_pct=0.3) == 0.0


# ── compute_conviction (full assembly) ────────────────────────────────


def test_a_textbook_inception_setup_scores_near_the_top() -> None:
    """Every bucket maxed: OB proximity + sweep + order-flow divergence
    + OI squeeze + HTF alignment + CHoCH -- should land at/near 100."""
    features = {
        "direction": "bullish",
        "ltp": 100.0,
        **_bull_ob(low=98.0, high=99.8),
        "last_liquidity_sweep": "sellside",
        "book_imbalance": 0.4,
        "book_imbalance_ema": 0.1,
        "squeeze_state": "COILED",
        "last_event_label": "Bullish CHOCH",
    }
    mtf_data = {"timeframes": {"1H": {"state": "BULL"}, "1D": {"state": "BULL"}}}
    futures_data = {"oi_buildup": "LONG_BUILDUP"}
    total, sub_scores = compute_conviction(features, mtf_data=mtf_data, futures_data=futures_data)
    assert total == pytest.approx(100.0)
    assert sub_scores["smc_structure"] == 35.0
    assert sub_scores["order_flow_divergence"] == 20.0
    assert sub_scores["oi_squeeze"] == 15.0
    assert sub_scores["htf_alignment"] == 15.0
    assert sub_scores["choch_trigger"] == 15.0


def test_a_lagging_momentum_setup_with_no_smc_footprint_scores_low() -> None:
    """The exact case this rewrite exists to stop scoring high: no OB/
    FVG, no sweep, no order-flow divergence, no OI/squeeze, no HTF or
    CHoCH confirmation -- just direction and a price. Old model would
    have scored this on VWAP/RSI/volume; new model correctly has
    nothing to reward."""
    features = {"direction": "bullish", "ltp": 100.0}
    total, sub_scores = compute_conviction(features)
    assert total == 0.0
    assert sub_scores["smc_structure"] == 0.0


def test_anti_chase_penalty_still_applies_on_top_of_the_new_model() -> None:
    """The anti-chase/rejection-reason penalties are orthogonal risk
    overlays this rewrite deliberately left in place -- not part of
    the 'lagging indicator weights' being replaced."""
    features = {
        "direction": "bullish",
        "ltp": 100.0,
        **_bull_ob(low=98.0, high=99.8),
        "anti_chase_ok": False,
    }
    total, sub_scores = compute_conviction(features)
    assert total == pytest.approx(12.0)  # 20 - 8
    assert sub_scores["anti_chase_penalty"] == -8.0


def test_chaseable_hard_cap_still_applies() -> None:
    features = {
        "direction": "bullish",
        "ltp": 100.0,
        **_bull_ob(low=98.0, high=99.8),
        "last_liquidity_sweep": "sellside",
        "book_imbalance": 0.4,
        "book_imbalance_ema": 0.1,
        "squeeze_state": "COILED",
        "last_event_label": "Bullish CHOCH",
        "chaseable": False,
    }
    mtf_data = {"timeframes": {"1H": {"state": "BULL"}, "1D": {"state": "BULL"}}}
    futures_data = {"oi_buildup": "LONG_BUILDUP"}
    total, _ = compute_conviction(features, mtf_data=mtf_data, futures_data=futures_data)
    assert total == CHASEABLE_GRADE_CAP


def test_bearish_direction_mirrors_the_bullish_case() -> None:
    features = {
        "direction": "bearish",
        "ltp": 100.0,
        **_bear_ob(low=100.2, high=102.0),
        "last_liquidity_sweep": "buyside",
        "book_imbalance": -0.4,
        "book_imbalance_ema": -0.1,
        "squeeze_state": "COILED",
        "last_event_label": "Bearish CHOCH",
    }
    mtf_data = {"timeframes": {"1H": {"state": "BEAR"}, "1D": {"state": "BEAR"}}}
    futures_data = {"oi_buildup": "SHORT_BUILDUP"}
    total, sub_scores = compute_conviction(features, mtf_data=mtf_data, futures_data=futures_data)
    assert total == pytest.approx(100.0)
    assert sub_scores["smc_structure"] == 35.0
