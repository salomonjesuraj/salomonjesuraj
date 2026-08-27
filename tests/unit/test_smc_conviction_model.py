"""Unit tests for the SMC Inception win-probability model (scanner/
scoring.py, probabilistic revision, 2026-08-27). Covers the pure
detection/scoring functions plus compute_conviction()'s full assembly.
See scoring.py's own module docstring for the two real data-
availability findings (1m-only OB/FVG, book-imbalance as the honest
CVD substitute) and the "hard suppression -> probabilistic grading"
philosophy pivot this revision made.
"""

from __future__ import annotations

import pytest
from scanner.scoring import (
    CHASEABLE_GRADE_CAP,
    choch_trigger_score,
    compute_conviction,
    htf_alignment_score,
    htf_momentum_score,
    nearest_ob_or_fvg_distance_pct,
    oi_buildup_score,
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


# ── nearest_ob_or_fvg_distance_pct (now in infusion_models.smc, re-exported) ──


def test_bullish_distance_measured_to_validated_ob_top() -> None:
    features = {"ltp": 100.0, **_bull_ob(low=97.0, high=99.5)}
    distance = nearest_ob_or_fvg_distance_pct(features, bearish=False)
    assert distance == pytest.approx(0.5)


def test_unvalidated_ob_does_not_count_as_a_zone() -> None:
    features = {"ltp": 100.5, **_bull_ob(low=98.0, high=100.0, validated=False)}
    assert nearest_ob_or_fvg_distance_pct(features, bearish=False) is None


def test_no_zone_at_all_returns_none_not_a_fabricated_zero() -> None:
    assert nearest_ob_or_fvg_distance_pct({"ltp": 100.0}, bearish=False) is None


# ── smc_structure_score (OB/FVG proximity 0-20 soft decay + sweep 0-10) ──


def test_full_credit_within_half_a_percent() -> None:
    features = {"ltp": 100.0, **_bull_ob(low=98.0, high=99.6)}  # 0.4% away
    points, distance = smc_structure_score(features, bearish=False)
    assert points == 15.0
    assert distance is not None and distance < 0.5


def test_soft_floor_not_zero_far_beyond_the_decay_range() -> None:
    """The whole point of the probabilistic revision: distance alone
    no longer zeroes out a setup -- a real, far-away zone still keeps
    a 5-point floor."""
    features = {"ltp": 100.0, **_bull_ob(low=50.0, high=90.0)}  # 10% away
    points, _ = smc_structure_score(features, bearish=False)
    assert points == 5.0


def test_linear_decay_at_the_midpoint_of_the_band() -> None:
    """0.5%-5.0% is the decay band; at its exact midpoint (2.75%)
    credit should land exactly midway between the 15-point max and
    5-point floor."""
    features = {"ltp": 100.0, **_bull_ob(low=94.0, high=97.25)}  # 2.75% away
    points, distance = smc_structure_score(features, bearish=False)
    assert distance == pytest.approx(2.75)
    assert points == pytest.approx(10.0)


def test_sellside_sweep_adds_bullish_points() -> None:
    features = {"ltp": 100.0, "last_liquidity_sweep": "sellside"}
    points, _ = smc_structure_score(features, bearish=False)
    assert points == 7.0


def test_buyside_sweep_does_not_count_for_a_bullish_signal() -> None:
    features = {"ltp": 100.0, "last_liquidity_sweep": "buyside"}
    points, _ = smc_structure_score(features, bearish=False)
    assert points == 0.0


def test_ob_proximity_and_sweep_both_contribute_to_the_full_bucket() -> None:
    features = {
        "ltp": 100.0,
        **_bull_ob(low=98.0, high=99.8),  # well within 0.5% -> 15 pts
        "last_liquidity_sweep": "sellside",  # +7 pts
    }
    points, _ = smc_structure_score(features, bearish=False)
    assert points == 22.0  # the full SMC Structure bucket


# ── htf_momentum_score (NEW -- 1H/Daily Marubozu/Engulfing) ─────────────


def test_bullish_marubozu_on_1h_scores_full() -> None:
    mtf_data = {"timeframes": {"1H": {"candle": "Bullish Marubozu"}}}
    assert htf_momentum_score(mtf_data, bearish=False) == 28.0


def test_bearish_engulfing_on_daily_scores_full() -> None:
    mtf_data = {"timeframes": {"1D": {"candle": "Bearish Engulfing"}}}
    assert htf_momentum_score(mtf_data, bearish=True) == 28.0


def test_wrong_direction_candle_scores_zero() -> None:
    mtf_data = {"timeframes": {"1H": {"candle": "Bearish Marubozu"}}}
    assert htf_momentum_score(mtf_data, bearish=False) == 0.0


def test_non_momentum_candle_scores_zero() -> None:
    mtf_data = {"timeframes": {"1H": {"candle": "Doji"}, "1D": {"candle": "Inside Bar"}}}
    assert htf_momentum_score(mtf_data, bearish=False) == 0.0


def test_missing_mtf_cache_scores_zero_not_a_guess() -> None:
    assert htf_momentum_score(None, bearish=False) == 0.0


# ── order_flow_divergence_score (honest book-imbalance proxy for CVD) ──


def test_scores_zero_without_compression() -> None:
    features = {"book_imbalance": 0.4, "book_imbalance_ema": 0.1, "bb_width": 0.05}
    assert order_flow_divergence_score(features, bearish=False) == 0.0


def test_scores_full_when_bullish_pressure_builds_during_a_squeeze() -> None:
    features = {"book_imbalance": 0.4, "book_imbalance_ema": 0.1, "squeeze_state": "COILED"}
    assert order_flow_divergence_score(features, bearish=False) == 10.0


def test_missing_book_imbalance_scores_zero_not_a_guess() -> None:
    assert order_flow_divergence_score({"squeeze_state": "COILED"}, bearish=False) == 0.0


# ── oi_buildup_score (probabilistic revision: no longer squeeze-gated) ──


def test_long_buildup_scores_full_for_bullish_even_without_a_squeeze() -> None:
    """The probabilistic revision's own point: a continuation setup has
    already left its coiling phase by definition, so this component
    must not require one."""
    futures_data = {"oi_buildup": "LONG_BUILDUP"}
    assert oi_buildup_score(futures_data, bearish=False) == 20.0


def test_no_futures_data_scores_zero_not_neutral() -> None:
    assert oi_buildup_score(None, bearish=False) == 0.0


def test_wrong_direction_buildup_scores_zero() -> None:
    futures_data = {"oi_buildup": "SHORT_BUILDUP"}
    assert oi_buildup_score(futures_data, bearish=False) == 0.0


# ── htf_alignment_score ───────────────────────────────────────────────


def test_both_1h_and_1d_bull_scores_full() -> None:
    mtf_data = {"timeframes": {"1H": {"state": "BULL"}, "1D": {"state": "BULL"}}}
    assert htf_alignment_score(mtf_data, bearish=False) == 12.0


def test_only_one_timeframe_aligned_scores_partial() -> None:
    mtf_data = {"timeframes": {"1H": {"state": "BULL"}, "1D": {"state": "RANGE"}}}
    assert htf_alignment_score(mtf_data, bearish=False) == 6.0


def test_missing_mtf_cache_scores_zero() -> None:
    assert htf_alignment_score(None, bearish=False) == 0.0


# ── choch_trigger_score ───────────────────────────────────────────────


def test_choch_inside_the_zone_scores_full() -> None:
    features = {"last_event_label": "Bullish CHOCH"}
    assert choch_trigger_score(features, bearish=False, ob_distance_pct=0.3) == 8.0


def test_choch_far_from_any_zone_scores_zero() -> None:
    features = {"last_event_label": "Bullish CHOCH"}
    assert choch_trigger_score(features, bearish=False, ob_distance_pct=3.0) == 0.0


def test_choch_with_no_zone_at_all_scores_zero() -> None:
    features = {"last_event_label": "Bullish CHOCH"}
    assert choch_trigger_score(features, bearish=False, ob_distance_pct=None) == 0.0


def test_wrong_direction_choch_scores_zero() -> None:
    features = {"last_event_label": "Bearish CHOCH"}
    assert choch_trigger_score(features, bearish=False, ob_distance_pct=0.3) == 0.0


# ── compute_conviction (full assembly) ────────────────────────────────


def test_a_textbook_inception_setup_scores_the_full_100() -> None:
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
    mtf_data = {
        "timeframes": {
            "1H": {"state": "BULL", "candle": "Bullish Marubozu"},
            "1D": {"state": "BULL"},
        }
    }
    futures_data = {"oi_buildup": "LONG_BUILDUP"}
    total, sub_scores = compute_conviction(features, mtf_data=mtf_data, futures_data=futures_data)
    assert total == pytest.approx(100.0)
    assert sub_scores["smc_structure"] == 22.0
    assert sub_scores["htf_momentum"] == 28.0
    assert sub_scores["order_flow_divergence"] == 10.0
    assert sub_scores["oi_buildup"] == 20.0
    assert sub_scores["htf_alignment"] == 12.0
    assert sub_scores["choch_trigger"] == 8.0


def test_a_slightly_extended_but_well_backed_setup_lands_in_the_70_to_85_band() -> None:
    """The exact case this revision was built for: OB/FVG proximity is
    weak (2% away, mid-decay), but a strong HTF candle + real OI
    buildup + HTF alignment carry the score up regardless -- no longer
    zeroed out just for being extended."""
    features = {
        "direction": "bullish",
        "ltp": 100.0,
        **_bull_ob(low=94.0, high=98.0),  # 2% away -- mid-decay, not full credit
        "squeeze_state": "",
        "bb_width": 0.05,
    }
    mtf_data = {
        "timeframes": {
            "1H": {"state": "BULL", "candle": "Bullish Marubozu"},
            "1D": {"state": "BULL"},
        }
    }
    futures_data = {"oi_buildup": "LONG_BUILDUP"}
    total, sub_scores = compute_conviction(features, mtf_data=mtf_data, futures_data=futures_data)
    assert 70.0 <= total <= 85.0
    assert sub_scores["smc_structure"] < 15.0  # weaker than full credit, not zero


def test_a_setup_with_no_smc_footprint_at_all_scores_at_the_floor() -> None:
    """No OB/FVG, no sweep, no order-flow divergence, no OI, no HTF/
    CHoCH confirmation, no HTF momentum -- correctly near-zero, this
    isn't a setup the model has anything to reward."""
    features = {"direction": "bullish", "ltp": 100.0}
    total, sub_scores = compute_conviction(features)
    assert total == 0.0
    assert sub_scores["smc_structure"] == 0.0


def test_anti_chase_penalty_still_applies_on_top_of_the_new_model() -> None:
    """Orthogonal risk overlays, deliberately untouched by either
    revision of this model."""
    features = {
        "direction": "bullish",
        "ltp": 100.0,
        **_bull_ob(low=98.0, high=99.8),
        "anti_chase_ok": False,
    }
    total, sub_scores = compute_conviction(features)
    assert total == pytest.approx(7.0)  # 15 - 8
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
    mtf_data = {
        "timeframes": {
            "1H": {"state": "BULL", "candle": "Bullish Marubozu"},
            "1D": {"state": "BULL"},
        }
    }
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
    mtf_data = {
        "timeframes": {
            "1H": {"state": "BEAR", "candle": "Bearish Marubozu"},
            "1D": {"state": "BEAR"},
        }
    }
    futures_data = {"oi_buildup": "SHORT_BUILDUP"}
    total, sub_scores = compute_conviction(features, mtf_data=mtf_data, futures_data=futures_data)
    assert total == pytest.approx(100.0)
    assert sub_scores["smc_structure"] == 22.0
