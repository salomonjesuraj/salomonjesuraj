"""Unit tests for api.structure_backtest's pure cost/exit/metrics logic
-- "Structure & Breakout Suite" Phase 3 (2026-08-29). The actual replay
loop (_replay_symbol_timeframe) is I/O-adjacent and exercised by the
live example in this sprint's own verification notes instead of a
synthetic-bar unit test here -- see this module's own docstring for why
(reliably forcing all seven real bias subscores to align in a hand-built
fixture is fragile busywork compared to a real end-to-end check against
actual historical data).
"""

from __future__ import annotations

from api.structure_backtest import (
    CostAssumptions,
    ReplayDiagnostics,
    SimulatedTrade,
    _brokerage_per_unit,
    _check_exit,
    _entry_fill_price,
    _exit_fill_price,
    _finalize_trade,
    _is_session_close_bar,
    _max_consecutive_losses,
    _max_drawdown_r,
    _OpenPosition,
    summarize_trades,
)

COSTS = CostAssumptions(slippage_bps=10.0, brokerage_per_trade=20.0)


def _bar(time: float, high: float, low: float, close: float = 0.0) -> dict[str, float]:
    return {"time": time, "high": high, "low": low, "close": close or (high + low) / 2}


def _position(
    *,
    direction: str = "LONG",
    entry_price: float = 100.0,
    sl: float = 95.0,
    tp1: float = 105.0,
    tp2: float = 110.0,
    tp3: float = 115.0,
) -> _OpenPosition:
    return _OpenPosition(
        symbol="TEST",
        timeframe="1d",
        direction=direction,
        entry_time=0.0,
        entry_price=entry_price,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        setup_quality_at_entry=6,
        market_phase_at_entry="UPTREND (HH/HL)",
        params_used={},
    )


# ─────────────────────── cost model ───────────────────────


def test_entry_slippage_moves_the_fill_against_a_long() -> None:
    """10 bps slippage on a 100 raw price -> a long buys at 100.10, not
    a favorable 99.90."""
    assert _entry_fill_price(100.0, bullish=True, costs=COSTS) == 100.10


def test_entry_slippage_moves_the_fill_against_a_short() -> None:
    assert _entry_fill_price(100.0, bullish=False, costs=COSTS) == 99.90


def test_exit_slippage_also_moves_against_the_trader() -> None:
    """A long's exit (a sell) fills slightly LOWER than the raw target
    price -- the same real-world "you don't get the exact tick" cost a
    long's entry (a buy) already reflects."""
    assert _exit_fill_price(100.0, bullish=True, costs=COSTS) == 99.90
    assert _exit_fill_price(100.0, bullish=False, costs=COSTS) == 100.10


def test_brokerage_converts_a_flat_fee_to_a_per_unit_cost() -> None:
    """20 flat / 200 entry price = 0.10 per-unit-equivalent cost."""
    assert _brokerage_per_unit(200.0, COSTS) == 0.10


def test_brokerage_is_zero_for_a_non_positive_entry_price() -> None:
    assert _brokerage_per_unit(0.0, COSTS) == 0.0


# ─────────────────────── exit detection ───────────────────────


def test_check_exit_long_hits_take_profit_1() -> None:
    position = _position()
    bar = _bar(1, high=106.0, low=101.0)
    result = _check_exit(position, bar, COSTS)
    assert result is not None
    price, reason = result
    assert reason == "TP1"
    assert price == _exit_fill_price(105.0, bullish=True, costs=COSTS)


def test_check_exit_long_hits_stop_loss_before_target_on_the_same_bar() -> None:
    """A bar whose range spans BOTH the SL and a target -- SL is checked
    first, the real, disclosed conservative convention (see this
    module's own header): never assume the more favorable intrabar
    order actually happened."""
    position = _position()
    bar = _bar(1, high=112.0, low=90.0)  # spans SL(95), TP1(105), TP2(110)
    _, reason = _check_exit(position, bar, COSTS)
    assert reason == "SL_HIT"


def test_check_exit_long_no_exit_when_bar_stays_inside_the_channel() -> None:
    position = _position()
    bar = _bar(1, high=102.0, low=98.0)
    assert _check_exit(position, bar, COSTS) is None


def test_check_exit_short_mirrors_long() -> None:
    position = _position(
        direction="SHORT", entry_price=100.0, sl=105.0, tp1=95.0, tp2=90.0, tp3=85.0
    )
    bar = _bar(1, high=101.0, low=94.0)
    _, reason = _check_exit(position, bar, COSTS)
    assert reason == "TP1"
    sl_bar = _bar(1, high=106.0, low=99.0)
    _, sl_reason = _check_exit(position, sl_bar, COSTS)
    assert sl_reason == "SL_HIT"


def test_finalize_trade_computes_a_real_r_multiple_net_of_costs() -> None:
    """Entry filled at 100.10 (post-slippage), exits at TP1's real
    filled price. Risk is measured against the ORIGINAL sl (95) and the
    ACTUAL filled entry -- not the raw, pre-slippage entry -- since risk
    is what was really put on, slippage included."""
    position = _position(entry_price=_entry_fill_price(100.0, True, COSTS))  # 100.10
    exit_price, exit_reason = _check_exit(position, _bar(1, 106.0, 101.0), COSTS)
    trade = _finalize_trade(
        position, exit_time=1.0, exit_price=exit_price, exit_reason=exit_reason, costs=COSTS
    )
    risk = position.entry_price - position.sl  # 100.10 - 95 = 5.10
    raw_pnl = exit_price - position.entry_price
    expected_pnl = raw_pnl - _brokerage_per_unit(position.entry_price, COSTS)
    assert trade.pnl_per_share == round(expected_pnl, 4)
    assert trade.r_multiple == round(expected_pnl / risk, 4)
    assert trade.exit_reason == "TP1"


# ─────────────────────── session close ───────────────────────


def test_is_session_close_bar_true_at_or_after_1530_ist() -> None:
    # 2026-08-24 is a real Monday. Timestamp independently verified via
    # datetime.combine(date(2026,8,24), time(15,30), tzinfo=IST).timestamp()
    # before being hardcoded here, not guessed.
    ts = 1787565600.0  # 2026-08-24 15:30:00 IST
    assert _is_session_close_bar(ts, next_bar_time=ts + 60) is True


def test_is_session_close_bar_true_when_the_next_bar_is_a_new_day() -> None:
    ts = 1787565000.0  # 2026-08-24 15:20:00 IST -- not literally 15:30 yet
    next_day_ts = ts + 86400  # next real trading session, a new calendar date
    assert _is_session_close_bar(ts, next_bar_time=next_day_ts) is True


def test_is_session_close_bar_false_mid_session_with_a_same_day_next_bar() -> None:
    ts = 1787559000.0  # 2026-08-24 13:40:00 IST
    assert _is_session_close_bar(ts, next_bar_time=ts + 60) is False


# ─────────────────────── metrics ───────────────────────


def _trade(
    r_multiple: float, exit_time: float = 0.0, pnl_per_share: float | None = None
) -> SimulatedTrade:
    return SimulatedTrade(
        symbol="TEST",
        timeframe="1d",
        direction="LONG",
        entry_time=0.0,
        entry_price=100.0,
        sl_price=95.0,
        tp1_price=105.0,
        tp2_price=110.0,
        tp3_price=115.0,
        exit_time=exit_time,
        exit_price=100.0 + r_multiple * 5,
        exit_reason="TP1" if r_multiple > 0 else "SL_HIT",
        r_multiple=r_multiple,
        pnl_per_share=pnl_per_share if pnl_per_share is not None else r_multiple * 5,
        setup_quality_at_entry=6,
        market_phase_at_entry="UPTREND (HH/HL)",
    )


def test_summarize_trades_is_honest_about_an_empty_set() -> None:
    result = summarize_trades([])
    assert result["trade_count"] == 0
    assert "reason" in result


def test_summarize_trades_computes_real_win_rate_and_profit_factor() -> None:
    # 2 wins (1.5R, 2.0R), 1 loss (-1.0R) -> win rate 66.67%, PF = 3.5/1.0.
    trades = [_trade(1.5), _trade(2.0), _trade(-1.0)]
    m = summarize_trades(trades)
    assert m["trade_count"] == 3
    assert m["win_rate_pct"] == round(2 / 3 * 100, 2)
    assert m["profit_factor"] == 3.5
    assert m["avg_r"] == round((1.5 + 2.0 - 1.0) / 3, 4)
    assert m["net_pnl_r"] == round(1.5 + 2.0 - 1.0, 4)


def test_summarize_trades_profit_factor_is_none_not_infinite_with_zero_losses() -> None:
    m = summarize_trades([_trade(1.0), _trade(2.0)])
    assert m["profit_factor"] is None
    assert m["profit_factor_note"] is not None


def test_max_drawdown_r_tracks_the_real_peak_to_trough_equity_dip() -> None:
    # Equity path: +2, +1 (peak 3), -1 (2, dd=1), -2 (0, dd=3), +1 (1).
    trades = [
        _trade(2.0, exit_time=1),
        _trade(1.0, exit_time=2),
        _trade(-1.0, exit_time=3),
        _trade(-2.0, exit_time=4),
        _trade(1.0, exit_time=5),
    ]
    assert _max_drawdown_r(trades) == 3.0


def test_max_consecutive_losses_counts_the_real_longest_losing_streak() -> None:
    trades = [_trade(1.0), _trade(-1.0), _trade(-1.0), _trade(-1.0), _trade(2.0), _trade(-1.0)]
    assert _max_consecutive_losses(trades) == 3


# ─────────────────────── ReplayDiagnostics (Task 2, 2026-08-29) ───────────────────────


def test_replay_diagnostics_as_dict_reports_every_named_bucket() -> None:
    diag = ReplayDiagnostics(
        trigger_source_mode="fast_range",
        bars_evaluated=100,
        insufficient_bars=10,
        no_bias=40,
        low_quality=20,
        no_trigger=15,
        candle_not_confirmed=10,
        session_close_exits=2,
        candidate_trigger_levels=25,
        armed_setups=5,
        confirmed_trades=3,
    )
    d = diag.as_dict()
    assert d["trigger_source_mode"] == "fast_range"
    assert d["bars_evaluated"] == 100
    assert d["no_bias"] == 40
    assert d["armed_setups"] == 5
    assert d["confirmed_trades"] == 3


def test_replay_diagnostics_merge_sums_across_replays() -> None:
    a = ReplayDiagnostics(trigger_source_mode="swing_zone", bars_evaluated=50, no_bias=10)
    b = ReplayDiagnostics(trigger_source_mode="swing_zone", bars_evaluated=30, no_bias=5)
    merged = ReplayDiagnostics.merge([a, b])
    assert merged["trigger_source_mode"] == "swing_zone"
    assert merged["bars_evaluated"] == 80
    assert merged["no_bias"] == 15


def test_replay_diagnostics_merge_reports_mixed_when_modes_differ() -> None:
    a = ReplayDiagnostics(trigger_source_mode="fast_range", bars_evaluated=10)
    b = ReplayDiagnostics(trigger_source_mode="trendline", bars_evaluated=10)
    merged = ReplayDiagnostics.merge([a, b])
    assert merged["trigger_source_mode"] == "mixed"
    assert merged["bars_evaluated"] == 20


def test_replay_diagnostics_merge_of_empty_list_is_an_honest_none_mode() -> None:
    merged = ReplayDiagnostics.merge([])
    assert merged["trigger_source_mode"] == "none"
    assert merged["bars_evaluated"] == 0
