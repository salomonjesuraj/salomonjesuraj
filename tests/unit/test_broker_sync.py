"""Unit tests for api.broker_sync's pure Position Decision & Horizon
Engine functions -- "Broker Sync & Active Position Intelligence" master
sprint (2026-08-27). The async Upstox-calling functions
(fetch_positions/fetch_holdings/fetch_orders/compute_position_intelligence)
are real I/O against a live broker API this test suite has no business
calling; only the deterministic pure functions built on top of that I/O
are covered here, same "test the pure math, not the live HTTP" posture
as tests/unit/test_trade_blueprint.py's own monkeypatch boundary.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import api.broker_sync as bs
from infusion_streams.codec import decode_event


def test_underlying_symbol_strips_option_expiry_and_strike() -> None:
    assert bs.underlying_symbol("RELIANCE28DEC2900CE") == "RELIANCE"
    assert bs.underlying_symbol("NIFTY28DEC24500PE") == "NIFTY"


def test_underlying_symbol_handles_ampersand_names() -> None:
    assert bs.underlying_symbol("M&M28DEC1500CE") == "M&M"


def test_underlying_symbol_returns_plain_equity_symbol_unchanged() -> None:
    assert bs.underlying_symbol("RELIANCE") == "RELIANCE"


def test_extract_expiry_reads_a_literal_expiry_field_first() -> None:
    row = {"expiry": "2026-12-28", "trading_symbol": "RELIANCE28DEC2900CE"}
    assert bs.extract_expiry(row) == date(2026, 12, 28)


def test_extract_expiry_parses_real_live_upstox_compact_option_symbols() -> None:
    """Regression test for a real bug caught live the first time this
    endpoint ever ran against a real account: Upstox's actual compact
    symbol format is <SYMBOL><DD><MON><STRIKE><CE|PE> with NO year at
    all -- an earlier parser version assumed a "DD MON YY" convention
    and mistook the real strike price (280, 4200) for a year, producing
    a multi-century DTE on both of these exact real live rows."""
    today = date(2026, 8, 27)
    assert bs.extract_expiry({"trading_symbol": "POWERGRID26SEP280CE"}, today=today) == date(
        2026, 9, 26
    )
    assert bs.extract_expiry({"trading_symbol": "KAYNES26SEP4200CE"}, today=today) == date(
        2026, 9, 26
    )


def test_extract_expiry_parses_a_futures_symbol() -> None:
    assert bs.extract_expiry(
        {"trading_symbol": "POWERGRID26SEPFUT"}, today=date(2026, 8, 27)
    ) == date(2026, 9, 26)


def test_extract_expiry_infers_next_year_when_the_day_month_has_already_passed() -> None:
    # Today is late December; "05JAN" must mean next January, not one
    # that already happened 11 months ago.
    today = date(2026, 12, 20)
    assert bs.extract_expiry({"trading_symbol": "NIFTY05JAN25000CE"}, today=today) == date(
        2027, 1, 5
    )


def test_extract_expiry_is_none_for_a_plain_equity_row() -> None:
    row = {"trading_symbol": "RELIANCE"}
    assert bs.extract_expiry(row) is None


def test_compute_dte_is_severe_when_expiry_has_already_passed() -> None:
    dte, risk = bs.compute_dte(date(2026, 1, 1), today=date(2026, 1, 5))
    assert dte == 0
    assert risk == "SEVERE"


def test_compute_dte_counts_only_weekday_trading_days() -> None:
    # Monday -> Thursday of the same week = 3 trading days, no weekend crossed.
    dte, risk = bs.compute_dte(date(2026, 8, 27), today=date(2026, 8, 24))
    assert dte == 3
    assert risk == "ACCELERATING"


def test_compute_dte_is_low_risk_with_plenty_of_runway() -> None:
    dte, risk = bs.compute_dte(date(2026, 9, 10), today=date(2026, 8, 24))
    assert dte is not None and dte > bs.THETA_RISK_ACCELERATING_DTE
    assert risk == "LOW"


def test_compute_dte_is_honestly_unavailable_for_an_equity_position() -> None:
    assert bs.compute_dte(None, today=date(2026, 8, 24)) == (None, "N/A")


def test_holding_horizon_exit_immediately_when_structurally_invalidated() -> None:
    horizon = bs.classify_holding_horizon(
        invalidation_tags=["FAST_EXIT"],
        theta_risk="LOW",
        dte_trading_days=10,
        product="D",
        trend_aligned=True,
    )
    assert horizon == "EXIT IMMEDIATELY"


def test_holding_horizon_tightens_stop_on_severe_theta_even_if_aligned() -> None:
    horizon = bs.classify_holding_horizon(
        invalidation_tags=[],
        theta_risk="SEVERE",
        dte_trading_days=0,
        product="D",
        trend_aligned=True,
    )
    assert horizon == "TIGHTEN STOP"


def test_holding_horizon_runner_for_an_aligned_intraday_product() -> None:
    horizon = bs.classify_holding_horizon(
        invalidation_tags=[],
        theta_risk="LOW",
        dte_trading_days=10,
        product="I",
        trend_aligned=True,
    )
    assert horizon == "RUNNER (INTRADAY ONLY)"


def test_holding_horizon_tightens_stop_for_a_misaligned_intraday_product() -> None:
    horizon = bs.classify_holding_horizon(
        invalidation_tags=[],
        theta_risk="LOW",
        dte_trading_days=10,
        product="I",
        trend_aligned=False,
    )
    assert horizon == "TIGHTEN STOP"


def test_holding_horizon_hold_2_3_days_for_an_aligned_delivery_position() -> None:
    horizon = bs.classify_holding_horizon(
        invalidation_tags=[],
        theta_risk="ACCELERATING",
        dte_trading_days=4,
        product="D",
        trend_aligned=True,
    )
    assert horizon == "HOLD (2-3 DAYS)"


def test_holding_horizon_tightens_stop_for_a_misaligned_delivery_position() -> None:
    horizon = bs.classify_holding_horizon(
        invalidation_tags=[],
        theta_risk="LOW",
        dte_trading_days=20,
        product="D",
        trend_aligned=False,
    )
    assert horizon == "TIGHTEN STOP"


# "Strict Quant & Option Logic" sprint (2026-08-28) -- regression tests
# for the real bugs the user's own audit caught live.


def test_effective_entry_price_uses_average_price_when_real() -> None:
    assert bs._effective_entry_price({"average_price": 4.4, "day_buy_price": 0.0}) == 4.4


def test_effective_entry_price_falls_back_to_day_buy_price_when_average_is_zero() -> None:
    # The exact real KAYNES26SEP4200CE shape caught live: a same-day buy
    # that Upstox reports with average_price == 0.
    assert (
        bs._effective_entry_price({"average_price": 0.0, "day_buy_price": 145.0}) == 145.0
    )


def test_effective_entry_price_is_zero_when_neither_is_known() -> None:
    assert bs._effective_entry_price({}) == 0.0


def test_extract_option_strike_reads_the_real_compact_symbol_tail() -> None:
    assert bs.extract_option_strike("POWERGRID26SEP280CE") == (280.0, "CE")
    assert bs.extract_option_strike("KAYNES26SEP4200CE") == (4200.0, "CE")
    assert bs.extract_option_strike("NIFTY28DEC24500PE") == (24500.0, "PE")


def test_extract_option_strike_is_none_for_equity_and_futures_symbols() -> None:
    assert bs.extract_option_strike("RELIANCE") is None
    assert bs.extract_option_strike("POWERGRID26SEPFUT") is None


def test_fib_extension_targets_are_distinct_for_a_bullish_position() -> None:
    # The exact bug this replaces: T2 and T3 rendering as the identical
    # number. 1.618x and 2.618x of the same real swing range must never
    # coincide unless the range itself is 0.
    t2, t3 = bs._fib_extension_targets(bullish=True, channel_upper=294.10, channel_lower=262.05)
    assert t2 is not None
    assert t3 is not None
    assert t2 != t3
    swing = 294.10 - 262.05
    assert t2 == 262.05 + swing * 1.618
    assert t3 == 262.05 + swing * 2.618
    assert t3 > t2  # T3 is the deeper extension


def test_fib_extension_targets_project_downward_for_a_bearish_position() -> None:
    t2, t3 = bs._fib_extension_targets(bullish=False, channel_upper=294.10, channel_lower=262.05)
    swing = 294.10 - 262.05
    assert t2 == 294.10 - swing * 1.618
    assert t3 == 294.10 - swing * 2.618
    assert t3 < t2  # T3 is the deeper (lower) extension


def test_option_breakeven_adds_the_premium_for_a_call() -> None:
    # A real live example: KAYNES26SEP4200CE, strike 4200, entry ~145.
    assert bs.option_breakeven(4200.0, 145.0, "CE") == 4345.0


def test_option_breakeven_subtracts_the_premium_for_a_put() -> None:
    assert bs.option_breakeven(24500.0, 120.0, "PE") == 24380.0


def test_fib_extension_targets_are_honestly_none_without_a_real_channel() -> None:
    assert bs._fib_extension_targets(bullish=True, channel_upper=None, channel_lower=262.05) == (
        None,
        None,
    )
    assert bs._fib_extension_targets(bullish=True, channel_upper=294.10, channel_lower=None) == (
        None,
        None,
    )


class _FakeRedis:
    """Minimal fake standing in for the real async Redis client --
    only the three calls `_maybe_alert_position_warning` actually makes
    (exists/set/xadd), enough to prove the cooldown gate and the
    publish-onto-the-real-stream behavior without touching a live
    Redis. "Omnipresent Alert Engine" sprint (2026-08-27)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.xadd_calls: list[tuple[str, dict[str, Any]]] = []

    async def exists(self, key: str) -> bool:
        return key in self.store

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def xadd(
        self, stream: str, fields: dict[str, Any], *, maxlen: int, approximate: bool
    ) -> None:
        self.xadd_calls.append((stream, fields))


def _decoded_alert_payloads(redis: _FakeRedis) -> list[dict[str, Any]]:
    return [decode_event(fields["data"])[4] for _stream, fields in redis.xadd_calls]


async def test_position_warning_does_not_fire_for_a_healthy_position() -> None:
    redis = _FakeRedis()
    await bs._maybe_alert_position_warning(
        redis,
        instrument_token="NSE_FO|1",
        trading_symbol="POWERGRID26SEP280CE",
        underlying="POWERGRID",
        ltp=12.5,
        pnl=-1000.0,
        invalidation_level=10.0,
        invalidation_tags=[],
        holding_horizon="HOLD (2-3 DAYS)",
    )
    assert redis.xadd_calls == []


async def test_position_warning_fires_on_exit_immediately_horizon() -> None:
    redis = _FakeRedis()
    await bs._maybe_alert_position_warning(
        redis,
        instrument_token="NSE_FO|1",
        trading_symbol="POWERGRID26SEP280CE",
        underlying="POWERGRID",
        ltp=12.5,
        pnl=-58900.0,
        invalidation_level=10.0,
        invalidation_tags=[],
        holding_horizon="EXIT IMMEDIATELY",
    )
    payloads = _decoded_alert_payloads(redis)
    assert len(payloads) == 1
    assert payloads[0]["signal_type"] == "position_warning"
    assert payloads[0]["symbol"] == "POWERGRID"
    message = payloads[0]["message"]
    # "Fast Exit Alarm" HTML template ("Blended HUD" redesign,
    # 2026-08-28) -- bold spans via <b>, no code fence at all this time.
    assert message.startswith("⚠️ <b>FAST EXIT ALARM</b> ⚠️")
    assert "<b>EXIT IMMEDIATELY</b>" in message
    assert "Broken Level: Rs 10.00" in message
    assert "PnL : Rs -58,900.00" in message


async def test_position_warning_fires_on_a_structural_break_tag_alone() -> None:
    # A position can carry an alertable tag under a non-alertable
    # horizon (e.g. a delivery position still inside "HOLD") -- the
    # tag alone is enough to warrant an urgent alert.
    redis = _FakeRedis()
    await bs._maybe_alert_position_warning(
        redis,
        instrument_token="NSE_FO|2",
        trading_symbol="KAYNES26SEP4200CE",
        underlying="KAYNES",
        ltp=55.0,
        pnl=-500.0,
        invalidation_level=None,
        invalidation_tags=["STRUCTURAL_BREAK"],
        holding_horizon="HOLD (2-3 DAYS)",
    )
    payloads = _decoded_alert_payloads(redis)
    assert len(payloads) == 1
    message = payloads[0]["message"]
    assert "STRUCTURAL_BREAK" in message
    # No invalidation level known -> an honest N/A, never a fabricated
    # price. The bottom banner also stays honest about severity: no
    # EXIT IMMEDIATELY horizon here, just a structural-break tag, so it
    # reads STRUCTURAL BREAK, not an overstated EXIT IMMEDIATELY.
    broken_lvl_line = next(
        line for line in message.split("\n") if line.startswith("• Broken Level")
    )
    assert broken_lvl_line == "• Broken Level: N/A"
    assert "ACTION: STRUCTURAL BREAK" in message


async def test_position_warning_respects_the_per_instrument_cooldown() -> None:
    # The exact spam scenario this cooldown exists to prevent: the same
    # instrument polled again (as the Active Cockpit does every 3s)
    # while still in an alertable state must NOT re-publish.
    redis = _FakeRedis()
    kwargs = dict(
        instrument_token="NSE_FO|1",
        trading_symbol="POWERGRID26SEP280CE",
        underlying="POWERGRID",
        ltp=12.5,
        pnl=-1000.0,
        invalidation_level=10.0,
        invalidation_tags=["FAST_EXIT"],
        holding_horizon="EXIT IMMEDIATELY",
    )
    await bs._maybe_alert_position_warning(redis, **kwargs)
    await bs._maybe_alert_position_warning(redis, **kwargs)
    assert len(redis.xadd_calls) == 1


async def test_position_warning_cooldown_is_scoped_per_instrument() -> None:
    # A second, distinct instrument in the same alertable state must
    # still get its own alert -- the cooldown key is per-token, not global.
    redis = _FakeRedis()
    await bs._maybe_alert_position_warning(
        redis,
        instrument_token="NSE_FO|1",
        trading_symbol="POWERGRID26SEP280CE",
        underlying="POWERGRID",
        ltp=12.5,
        pnl=-1000.0,
        invalidation_level=10.0,
        invalidation_tags=["FAST_EXIT"],
        holding_horizon="EXIT IMMEDIATELY",
    )
    await bs._maybe_alert_position_warning(
        redis,
        instrument_token="NSE_FO|2",
        trading_symbol="KAYNES26SEP4200CE",
        underlying="KAYNES",
        ltp=55.0,
        pnl=-500.0,
        invalidation_level=None,
        invalidation_tags=["STRUCTURAL_BREAK"],
        holding_horizon="EXIT IMMEDIATELY",
    )
    assert len(redis.xadd_calls) == 2
