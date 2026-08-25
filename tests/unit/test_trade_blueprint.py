"""Unit tests for api.trade_blueprint.build_trade_blueprint — mathematical
audit follow-up Task 4 (unified TradeBlueprint payload), 2026-08-25.

_load_bars and _fetch_full_option_chain are real I/O (Redis multi-key
fetch, live Upstox HTTP call respectively) -- monkeypatched at the
api.trade_blueprint module level, the standard way to test an
orchestration function without hitting either.
"""

from __future__ import annotations

from typing import Any

import api.trade_blueprint as tb
import pytest
from infusion_models.oi_buildup import OIBuildupType


class _FakeRedis:
    def __init__(self, hashes: dict[str, dict[str, Any]] | None = None) -> None:
        self._hashes = hashes or {}

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        row = self._hashes.get(key) or {}
        return {k.encode(): str(v).encode() for k, v in row.items()}


def _bar(high: float, low: float, close: float, volume: float) -> dict[str, Any]:
    return {"high": high, "low": low, "close": close, "volume": volume}


@pytest.fixture(autouse=True)
def _stub_bars_and_chain(monkeypatch):
    """Default stubs: no bar history, no option chain -- individual
    tests override via monkeypatch.setattr for the paths they exercise."""

    async def _no_bars(redis, symbol):
        return [], [], []

    async def _no_chain(redis, symbol):
        return {"ready": False, "reason": "stub"}

    monkeypatch.setattr(tb, "_load_bars", _no_bars)
    monkeypatch.setattr(tb, "_fetch_full_option_chain", _no_chain)
    yield


async def test_no_active_signal_reports_entry_fields_as_unavailable() -> None:
    redis = _FakeRedis()
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.entry_price == 0.0
    assert blueprint.setup_name == "no_active_signal"
    assert "entry_price" in blueprint.unavailable_fields
    assert "entry_price" not in blueprint.available_fields


async def test_active_signal_populates_entry_sl_and_targets() -> None:
    redis = _FakeRedis(
        {
            "infusion:signal:RELIANCE": {
                "signal_type": "bullish",
                "strategy_id": "vol_vwap_breakout",
                "entry_price": "1300.0",
                "invalidation_price": "1290.0",
                "target_price": "1320.0",
                "t2_price": "1335.0",
                "target_method": "Practical option target floor",
                "features_snapshot": '{"t3_price": 1350.0}',
            }
        }
    )
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.direction == "BULL"
    assert blueprint.setup_name == "vol_vwap_breakout"
    assert blueprint.entry_price == 1300.0
    assert blueprint.invalidation_sl == 1290.0
    assert blueprint.target_1_fib == 1320.0
    assert blueprint.target_2_fib == 1335.0
    assert blueprint.target_3_fib == 1350.0
    assert "entry_price" in blueprint.available_fields


async def test_bearish_signal_sets_direction_bear() -> None:
    redis = _FakeRedis(
        {
            "infusion:signal:RELIANCE": {
                "signal_type": "bearish",
                "strategy_id": "options_first_hybrid",
                "entry_price": "1300.0",
                "invalidation_price": "1310.0",
                "target_price": "1280.0",
                "t2_price": "1270.0",
            }
        }
    )
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.direction == "BEAR"


async def test_retest_status_read_from_the_live_feature_hash() -> None:
    redis = _FakeRedis(
        {"infusion:feature:RELIANCE": {"retest_status": "RETEST_HELD", "retest_level": "1305.5"}}
    )
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.retest_status == "RETEST_HELD"
    assert blueprint.retest_level == 1305.5
    assert "retest_status" in blueprint.available_fields


async def test_retest_status_defaults_to_no_breakout_when_feature_hash_absent() -> None:
    redis = _FakeRedis()
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.retest_status == "NO_BREAKOUT"
    assert blueprint.retest_level is None
    assert "retest_status" in blueprint.unavailable_fields


async def test_volume_profile_populated_when_bar_history_available(monkeypatch) -> None:
    bars = [_bar(100.0, 99.0, 100.0, 5000.0) for _ in range(30)]

    async def _bars(redis, symbol):
        return bars, [], []

    monkeypatch.setattr(tb, "_load_bars", _bars)
    redis = _FakeRedis()
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.poc_level is not None
    assert blueprint.vah_level is not None
    assert blueprint.val_level is not None
    assert "poc_level" in blueprint.available_fields


async def test_oi_buildup_read_from_futures_cache() -> None:
    redis = _FakeRedis({"infusion:futures:RELIANCE": {"oi_buildup": "LONG_BUILDUP"}})
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.oi_buildup == OIBuildupType.LONG_BUILDUP.value
    assert "oi_buildup" in blueprint.available_fields


async def test_oi_buildup_defaults_to_neutral_when_futures_cache_absent() -> None:
    redis = _FakeRedis()
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.oi_buildup == OIBuildupType.NEUTRAL.value
    assert "oi_buildup" in blueprint.unavailable_fields


async def test_oi_attraction_and_hurdle_from_a_real_chain(monkeypatch) -> None:
    rows = [
        {
            "strike_price": 1300.0,
            "call_options": {"market_data": {"oi": 50_000}},
            "put_options": {"market_data": {"oi": 10_000}},
        },
        {
            "strike_price": 1350.0,
            "call_options": {"market_data": {"oi": 90_000}},  # heaviest call OI -> resistance
            "put_options": {"market_data": {"oi": 5_000}},
        },
        {
            "strike_price": 1250.0,
            "call_options": {"market_data": {"oi": 5_000}},
            "put_options": {"market_data": {"oi": 80_000}},  # heaviest put OI -> support
        },
    ]

    async def _chain(redis, symbol):
        return {"ready": True, "rows": rows, "spot": 1300.0}

    monkeypatch.setattr(tb, "_fetch_full_option_chain", _chain)
    redis = _FakeRedis()  # no active signal -> defaults to BULL

    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.oi_attraction_strike is not None  # Max Pain, some strike in the chain
    # BULL direction (default with no signal) -> hurdle is the call-OI
    # resistance strike (1350.0, heaviest call OI).
    assert blueprint.oi_hurdle_strike == 1350.0
    assert "oi_attraction_strike" in blueprint.available_fields
    assert "oi_hurdle_strike" in blueprint.available_fields


async def test_oi_hurdle_uses_put_support_for_a_bear_setup(monkeypatch) -> None:
    rows = [
        {
            "strike_price": 1350.0,
            "call_options": {"market_data": {"oi": 90_000}},
            "put_options": {"market_data": {"oi": 5_000}},
        },
        {
            "strike_price": 1250.0,
            "call_options": {"market_data": {"oi": 5_000}},
            "put_options": {"market_data": {"oi": 80_000}},
        },
    ]

    async def _chain(redis, symbol):
        return {"ready": True, "rows": rows, "spot": 1300.0}

    monkeypatch.setattr(tb, "_fetch_full_option_chain", _chain)
    redis = _FakeRedis({"infusion:signal:RELIANCE": {"signal_type": "bearish"}})

    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.direction == "BEAR"
    assert blueprint.oi_hurdle_strike == 1250.0  # put-OI support, below price


async def test_never_raises_on_a_symbol_with_nothing_cached_anywhere() -> None:
    """Full "cold" symbol -- no signal, no feature row, no bars, no
    chain -- must return an honest, all-unavailable blueprint, not
    raise."""
    redis = _FakeRedis()
    blueprint = await tb.build_trade_blueprint(redis, "UNKNOWNSTOCK")
    assert blueprint.symbol == "UNKNOWNSTOCK"
    assert blueprint.entry_price == 0.0
    assert blueprint.oi_buildup == OIBuildupType.NEUTRAL.value
    assert len(blueprint.unavailable_fields) > 0
