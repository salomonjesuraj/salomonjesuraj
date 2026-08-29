"""Unit tests for api.structure_optimize's pure grid/purge-embargo/
scoring/rejection logic -- "Structure & Breakout Suite" Phase 4
(2026-08-29). The actual search (optimize_structure_backtest) is
I/O-heavy (re-runs Phase 3's own real replay per sampled combination)
and exercised by this sprint's own live example instead of a unit test
here -- see this module's own docstring for the real, disclosed compute
constraint that shapes the design being tested below.
"""

from __future__ import annotations

from api.structure_backtest import SimulatedTrade
from api.structure_optimize import (
    FULL_GRID_SIZE,
    MAX_OVERFIT_GAP_R,
    MIN_TEST_TRADES,
    ParamCombo,
    _all_combos,
    _confidence,
    _consistency_fraction,
    _evaluate_combo,
    _purge_and_embargo_trades,
    _robustness_score,
    sample_param_grid,
)


def _trade(
    symbol: str = "SYM",
    timeframe: str = "1d",
    r_multiple: float = 1.0,
    entry_time: float = 0.0,
    exit_time: float = 0.0,
    trigger_source: str = "fast_range",
) -> SimulatedTrade:
    return SimulatedTrade(
        symbol=symbol,
        timeframe=timeframe,
        direction="LONG",
        entry_time=entry_time,
        entry_price=100.0,
        sl_price=95.0,
        tp1_price=105.0,
        tp2_price=110.0,
        tp3_price=115.0,
        exit_time=exit_time,
        exit_price=100.0 + r_multiple * 5,
        exit_reason="TP1",
        r_multiple=r_multiple,
        pnl_per_share=r_multiple * 5,
        setup_quality_at_entry=6,
        market_phase_at_entry="UPTREND (HH/HL)",
        params_used={"trigger_source": trigger_source},
    )


# ─────────────────────── grid ───────────────────────


def test_full_grid_size_matches_the_spec_own_literal_count() -> None:
    assert FULL_GRID_SIZE == 18_000
    assert len(_all_combos()) == 18_000


def test_sample_param_grid_is_deterministic_for_a_fixed_seed() -> None:
    first, full_a = sample_param_grid(max_combinations=50, seed=7)
    second, full_b = sample_param_grid(max_combinations=50, seed=7)
    assert full_a == full_b == 18_000
    assert first == second
    assert len(first) == 50


def test_sample_param_grid_returns_the_whole_grid_when_cap_exceeds_it() -> None:
    combos, full = sample_param_grid(max_combinations=999_999, seed=1)
    assert full == 18_000
    assert len(combos) == 18_000


def test_a_real_combo_round_trips_through_its_own_config() -> None:
    combo = ParamCombo(6, 1, 12, 0.20, 1.15, 1.5, 2.5, 3.5, "BALANCED", "swing_zone")
    config = combo.to_config()
    assert config.min_setup_quality == 6
    assert config.fast_trigger_lookback == 12
    assert config.tp1_r == 1.5


def test_replay_key_excludes_trigger_source_so_only_it_can_differ() -> None:
    a = ParamCombo(6, 1, 12, 0.20, 1.15, 1.5, 2.5, 3.5, "BALANCED", "swing_zone")
    b = ParamCombo(6, 1, 12, 0.20, 1.15, 1.5, 2.5, 3.5, "BALANCED", "trendline")
    c = ParamCombo(5, 1, 12, 0.20, 1.15, 1.5, 2.5, 3.5, "BALANCED", "swing_zone")
    assert a.replay_key() == b.replay_key()
    assert a.replay_key() != c.replay_key()


# ─────────────────────── purge/embargo ───────────────────────


def test_purge_and_embargo_drops_a_train_trade_whose_outcome_resolves_after_the_split() -> None:
    trades = [
        _trade(entry_time=0, exit_time=1),
        _trade(entry_time=1, exit_time=2),
        _trade(entry_time=2, exit_time=50),  # entered before the split, but resolves AFTER it
        _trade(entry_time=10, exit_time=11),  # real test-side trade
    ]
    train, test, purged, _embargoed = _purge_and_embargo_trades(
        trades, train_pct=0.75, embargo_sec=0.0
    )
    # split_idx = round(4*0.75) = 3 -> split_time = trades[3].entry_time = 10
    assert purged == 1  # the entry_time=2/exit_time=50 trade
    assert len(train) == 2
    assert len(test) == 1


def test_purge_and_embargo_drops_test_trades_inside_the_embargo_window() -> None:
    trades = [
        _trade(entry_time=0, exit_time=1),
        _trade(entry_time=1, exit_time=2),
        _trade(entry_time=10, exit_time=11),  # right at the split -- embargoed
        _trade(entry_time=100, exit_time=101),  # well clear of the embargo window
    ]
    _train, test, _purged, embargoed = _purge_and_embargo_trades(
        trades, train_pct=0.5, embargo_sec=50.0
    )
    assert embargoed == 1
    assert len(test) == 1
    assert test[0].entry_time == 100


def test_purge_and_embargo_is_honestly_empty_with_fewer_than_two_trades() -> None:
    _train, test, purged, embargoed = _purge_and_embargo_trades(
        [_trade()], train_pct=0.7, embargo_sec=0.0
    )
    assert test == []
    assert purged == 0 and embargoed == 0


# ─────────────────────── consistency / confidence ───────────────────────


def test_consistency_fraction_counts_real_positive_groups() -> None:
    trades = [
        _trade(symbol="A", r_multiple=1.0),
        _trade(symbol="A", r_multiple=1.0),  # A: +2 net
        _trade(symbol="B", r_multiple=-1.0),
        _trade(symbol="B", r_multiple=0.5),  # B: -0.5 net
        _trade(symbol="C", r_multiple=2.0),  # C: +2 net
    ]
    # 2 of 3 symbols (A, C) are net positive.
    assert _consistency_fraction(trades, lambda t: t.symbol) == round(2 / 3, 3)


def test_consistency_fraction_is_zero_not_none_with_no_trades() -> None:
    assert _consistency_fraction([], lambda t: t.symbol) == 0.0


def test_confidence_is_high_only_with_real_sample_and_real_cross_group_consistency() -> None:
    assert _confidence(30, 0.6, 0.6) == "HIGH"
    assert _confidence(30, 0.5, 0.6) == "MEDIUM"  # consistency just under the HIGH bar
    assert _confidence(MIN_TEST_TRADES, 1.0, 1.0) == "MEDIUM"  # good consistency, thin sample
    assert _confidence(MIN_TEST_TRADES - 1, 1.0, 1.0) == "LOW"


# ─────────────────────── robustness score ───────────────────────


def test_robustness_score_rewards_profit_factor_and_penalizes_drawdown_and_overfit() -> None:
    strong = _robustness_score(
        test_metrics={"profit_factor": 2.0, "win_rate_pct": 60.0, "max_drawdown_r": 1.0},
        consistency_symbols=0.8,
        consistency_timeframes=0.8,
        overfit_gap=0.1,
        test_trade_count=50,
    )
    weak = _robustness_score(
        test_metrics={"profit_factor": 0.5, "win_rate_pct": 30.0, "max_drawdown_r": 10.0},
        consistency_symbols=0.2,
        consistency_timeframes=0.2,
        overfit_gap=2.0,
        test_trade_count=10,
    )
    assert strong > weak


# ─────────────────────── rejection rules ───────────────────────


def test_evaluate_combo_rejects_too_few_out_of_sample_trades() -> None:
    combo = ParamCombo(6, 1, 12, 0.20, 1.15, 1.5, 2.5, 3.5, "BALANCED", "fast_range")
    trades = [
        _trade(entry_time=i, exit_time=i + 0.5) for i in range(5)
    ]  # well under MIN_TEST_TRADES
    result = _evaluate_combo(
        combo,
        trades,
        requested_symbols=["SYM"],
        requested_timeframes=["1d"],
        train_pct=0.5,
        embargo_sec=0.0,
    )
    assert result["rejected"] is True
    assert any("Too few" in r for r in result["rejection_reasons"])


def test_evaluate_combo_rejects_single_symbol_when_multiple_were_requested() -> None:
    combo = ParamCombo(6, 1, 12, 0.20, 1.15, 1.5, 2.5, 3.5, "BALANCED", "fast_range")
    # 20 trades, all on ONE symbol, split so test has plenty of trades -- but
    # two symbols were REQUESTED, so single-symbol coverage must reject.
    trades = [
        _trade(symbol="ONLY", entry_time=i, exit_time=i + 0.5, r_multiple=0.5) for i in range(20)
    ]
    result = _evaluate_combo(
        combo,
        trades,
        requested_symbols=["ONLY", "OTHER"],
        requested_timeframes=["1d"],
        train_pct=0.3,
        embargo_sec=0.0,
    )
    assert result["rejected"] is True
    assert any("only 1 symbol" in r for r in result["rejection_reasons"])


def test_evaluate_combo_rejects_negative_out_of_sample_expectancy() -> None:
    combo = ParamCombo(6, 1, 12, 0.20, 1.15, 1.5, 2.5, 3.5, "BALANCED", "fast_range")
    trades = [_trade(entry_time=i, exit_time=i + 0.5, r_multiple=-1.0) for i in range(20)]
    result = _evaluate_combo(
        combo,
        trades,
        requested_symbols=["SYM"],
        requested_timeframes=["1d"],
        train_pct=0.3,
        embargo_sec=0.0,
    )
    assert result["rejected"] is True
    assert any("Negative out-of-sample expectancy" in r for r in result["rejection_reasons"])


def test_evaluate_combo_rejects_a_large_train_test_overfit_gap() -> None:
    combo = ParamCombo(6, 1, 12, 0.20, 1.15, 1.5, 2.5, 3.5, "BALANCED", "fast_range")
    # Train trades (first 30%) are all big winners; test trades (rest) are
    # only barely positive -- a real, large expectancy gap.
    train_trades = [_trade(entry_time=i, exit_time=i + 0.5, r_multiple=3.0) for i in range(10)]
    test_trades = [
        _trade(entry_time=100 + i, exit_time=100 + i + 0.5, r_multiple=0.05) for i in range(20)
    ]
    trades = train_trades + test_trades
    result = _evaluate_combo(
        combo,
        trades,
        requested_symbols=["SYM"],
        requested_timeframes=["1d"],
        train_pct=0.3,
        embargo_sec=0.0,
    )
    assert result["overfit_gap_r"] is not None
    assert result["overfit_gap_r"] > MAX_OVERFIT_GAP_R
    assert result["rejected"] is True
    assert any("gap too large" in r for r in result["rejection_reasons"])


def test_evaluate_combo_accepts_a_genuinely_robust_profile() -> None:
    combo = ParamCombo(6, 1, 12, 0.20, 1.15, 1.5, 2.5, 3.5, "BALANCED", "fast_range")
    # Real, modest, consistent positive expectancy across two symbols in
    # both train and test -- nothing here should trip a rejection rule.
    trades = [
        _trade(symbol="A" if i % 2 == 0 else "B", entry_time=i, exit_time=i + 0.5, r_multiple=0.3)
        for i in range(40)
    ]
    result = _evaluate_combo(
        combo,
        trades,
        requested_symbols=["A", "B"],
        requested_timeframes=["1d"],
        train_pct=0.5,
        embargo_sec=0.0,
    )
    assert result["rejected"] is False
    assert result["rejection_reasons"] == []


def test_evaluate_combo_refilters_by_trigger_source_before_evaluating() -> None:
    combo = ParamCombo(6, 1, 12, 0.20, 1.15, 1.5, 2.5, 3.5, "BALANCED", "swing_zone")
    trades = [
        _trade(entry_time=i, exit_time=i + 0.5, trigger_source="fast_range") for i in range(20)
    ]
    result = _evaluate_combo(
        combo,
        trades,
        requested_symbols=["SYM"],
        requested_timeframes=["1d"],
        train_pct=0.5,
        embargo_sec=0.0,
    )
    # None of the fixture trades are swing_zone -- refiltering must leave
    # nothing to evaluate, an honest "too few trades" rejection.
    assert result["test_metrics"]["trade_count"] == 0
    assert result["rejected"] is True
