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
        invalidation_level=10.0,
        invalidation_tags=[],
        holding_horizon="EXIT IMMEDIATELY",
    )
    payloads = _decoded_alert_payloads(redis)
    assert len(payloads) == 1
    assert payloads[0]["signal_type"] == "position_warning"
    assert payloads[0]["symbol"] == "POWERGRID"
    assert "EXIT IMMEDIATELY" in payloads[0]["message"]
    assert "Invalidation level 10.00" in payloads[0]["message"]


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
        invalidation_level=None,
        invalidation_tags=["STRUCTURAL_BREAK"],
        holding_horizon="HOLD (2-3 DAYS)",
    )
    payloads = _decoded_alert_payloads(redis)
    assert len(payloads) == 1
    assert "STRUCTURAL_BREAK" in payloads[0]["message"]
    # No invalidation level known -> the sentence fragment is omitted
    # entirely rather than printed as a fabricated "None".
    assert "Invalidation level" not in payloads[0]["message"]


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
        invalidation_level=None,
        invalidation_tags=["STRUCTURAL_BREAK"],
        holding_horizon="EXIT IMMEDIATELY",
    )
    assert len(redis.xadd_calls) == 2
