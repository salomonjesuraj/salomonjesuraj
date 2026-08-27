"""Unit tests for api.trade_blueprint.build_trade_blueprint — mathematical
audit follow-up Task 4 (unified TradeBlueprint payload), 2026-08-25.

_load_bars and _fetch_full_option_chain are real I/O (Redis multi-key
fetch, live Upstox HTTP call respectively) -- monkeypatched at the
api.trade_blueprint module level, the standard way to test an
orchestration function without hitting either.
"""

from __future__ import annotations

import json
from typing import Any

import api.trade_blueprint as tb
import pytest
from infusion_models.oi_buildup import OIBuildupType


class _FakeRedis:
    def __init__(
        self,
        hashes: dict[str, dict[str, Any]] | None = None,
        lists: dict[str, list[str]] | None = None,
    ) -> None:
        self._hashes = hashes or {}
        self._lists = lists or {}

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        row = self._hashes.get(key) or {}
        return {k.encode(): str(v).encode() for k, v in row.items()}

    async def lrange(self, key: str, start: int, end: int) -> list[bytes]:
        # Real redis-py semantics: end is inclusive, -1 means "to the end".
        items = self._lists.get(key) or []
        stop = len(items) if end == -1 else end + 1
        return [item.encode() for item in items[start:stop]]


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


# ── SMC Inception Conviction Model presentation (2026-08-27) ────────────
# Same formulas as scanner/scoring.py's own (thoroughly tested)
# nearest_ob_or_fvg_distance_pct/order_flow_divergence_score --
# duplicated per this codebase's established cross-service pattern (see
# api/trade_blueprint.py's own comment), so these tests just confirm
# the duplication and the Redis-hash-string-typing are correct, not a
# full re-derivation of every edge case already covered there.


def test_ob_distance_reads_string_typed_redis_hash_values() -> None:
    """Redis hashes decode to plain strings, unlike scanner's own
    already-typed features_snapshot -- infusion_models.smc's coercion
    must handle that regardless of which service calls it."""
    feature_row = {
        "ltp": "100.0",
        "order_block_bullish_validated": "True",
        "order_block_bullish_high": "99.5",
    }
    assert tb.nearest_ob_or_fvg_distance_pct(feature_row, bearish=False) == pytest.approx(0.5)


def test_ob_distance_none_with_no_zone() -> None:
    assert tb.nearest_ob_or_fvg_distance_pct({"ltp": "100.0"}, bearish=False) is None


def test_order_flow_divergence_true_when_pressure_builds_during_a_squeeze() -> None:
    feature_row = {
        "book_imbalance": "0.4",
        "book_imbalance_ema": "0.1",
        "squeeze_state": "COILED",
    }
    assert tb.order_flow_divergence(feature_row, bearish=False) is True


def test_order_flow_divergence_false_without_compression() -> None:
    feature_row = {"book_imbalance": "0.4", "book_imbalance_ema": "0.1", "bb_width": "0.05"}
    assert tb.order_flow_divergence(feature_row, bearish=False) is False


async def test_blueprint_surfaces_ob_distance_and_liquidity_sweep() -> None:
    redis = _FakeRedis(
        {
            "infusion:feature:RELIANCE": {
                "ltp": "100.0",
                "retest_status": "NO_BREAKOUT",
                "order_block_bullish_validated": "True",
                "order_block_bullish_high": "99.5",
                "last_liquidity_sweep": "sellside",
            }
        }
    )
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.ob_fvg_distance_pct == pytest.approx(0.5)
    assert blueprint.liquidity_sweep == "sellside"
    assert "ob_fvg_distance_pct" in blueprint.available_fields


# ── "Probabilistic Grading and Warning Tags" (2026-08-27) ───────────────
# LATE_ENTRY/R:R warning_tags replaced the old hard REJECTED_CHASING_OB
# suppression gate -- see infusion_models.smc.compute_warning_tags and
# scanner/suppression.py's own removal comment for the full context.


async def test_late_entry_tag_fires_beyond_the_old_gates_threshold() -> None:
    redis = _FakeRedis(
        {
            "infusion:signal:RELIANCE": {
                "signal_type": "bullish",
                "entry_price": "100.0",
                "invalidation_price": "98.0",
                "target_price": "104.0",
            },
            "infusion:feature:RELIANCE": {
                "ltp": "100.0",
                "retest_status": "NO_BREAKOUT",
                "order_block_bullish_validated": "True",
                "order_block_bullish_high": "98.5",  # 1.52% away -- past the 0.75% line
            },
        }
    )
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert "LATE_ENTRY" in blueprint.warning_tags


async def test_poor_rr_tag_fires_below_1_point_5() -> None:
    redis = _FakeRedis(
        {
            "infusion:signal:RELIANCE": {
                "signal_type": "bullish",
                "entry_price": "100.0",
                "invalidation_price": "99.0",  # risk 1.0
                "target_price": "101.0",  # reward 1.0 -> R:R 1.0, below 1.5
            },
            "infusion:feature:RELIANCE": {"ltp": "100.0", "retest_status": "NO_BREAKOUT"},
        }
    )
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert "R:R < 1:1.5" in blueprint.warning_tags


async def test_no_warning_tags_for_a_clean_close_setup() -> None:
    redis = _FakeRedis(
        {
            "infusion:signal:RELIANCE": {
                "signal_type": "bullish",
                "entry_price": "100.0",
                "invalidation_price": "99.0",  # risk 1.0
                "target_price": "102.0",  # reward 2.0 -> R:R 2.0
            },
            "infusion:feature:RELIANCE": {
                "ltp": "100.0",
                "retest_status": "NO_BREAKOUT",
                "order_block_bullish_validated": "True",
                "order_block_bullish_high": "99.7",  # 0.3% away -- inside the line
            },
        }
    )
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.warning_tags == []


async def test_no_active_signal_means_no_rr_tag_but_still_checks_late_entry() -> None:
    """A cold symbol has no entry/SL/T1 to compute R:R from -- that
    must not crash or fabricate a tag, it should just skip the R:R
    check while still evaluating LATE_ENTRY from feature_row alone."""
    redis = _FakeRedis(
        {
            "infusion:feature:RELIANCE": {
                "ltp": "100.0",
                "retest_status": "NO_BREAKOUT",
                "order_block_bullish_validated": "True",
                "order_block_bullish_high": "98.5",  # 1.52% away
            }
        }
    )
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert "LATE_ENTRY" in blueprint.warning_tags
    assert "R:R < 1:1.5" not in blueprint.warning_tags


# ── "Terminal Edge & Analyst" sprint (2026-08-27) ─────────────────────


def _mtf_with_structure(
    *,
    blocker_up: float | None,
    blocker_down: float | None,
    donch_high: float | None,
    donch_low: float | None,
):
    async def _fake_compute_mtf(redis, symbol, store=False):
        return {
            "timeframes": {},
            "blocker_up_level": blocker_up,
            "blocker_down_level": blocker_down,
            "donchian": {"high": donch_high, "low": donch_low},
        }

    return _fake_compute_mtf


async def test_structure_populates_from_mtf_blocker_and_donchian(monkeypatch) -> None:
    monkeypatch.setattr(
        tb,
        "compute_mtf",
        _mtf_with_structure(blocker_up=110.0, blocker_down=95.0, donch_high=115.0, donch_low=90.0),
    )
    redis = _FakeRedis(
        {"infusion:feature:RELIANCE": {"ltp": "100.0", "trend_text": "UPTREND (HH/HL)"}}
    )
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.structure is not None
    assert blueprint.structure.resistance == 110.0
    assert blueprint.structure.support == 95.0
    assert blueprint.structure.channel_upper == 115.0
    assert blueprint.structure.channel_lower == 90.0
    assert blueprint.structure.trend == "UPTREND (HH/HL)"
    assert "structure" in blueprint.available_fields


async def test_structure_is_honestly_unavailable_with_no_mtf_data() -> None:
    """Default stub (_load_bars returns nothing) -> compute_mtf runs for
    real against empty bars -> every structure field is None, never a
    fabricated level."""
    redis = _FakeRedis({"infusion:feature:RELIANCE": {"ltp": "100.0"}})
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert blueprint.structure is not None
    assert blueprint.structure.support is None
    assert blueprint.structure.resistance is None
    assert "structure" in blueprint.unavailable_fields


def test_trade_rationale_mentions_bos_and_oi_buildup() -> None:
    structure = tb.TradeStructure(resistance=110.0, support=95.0, trend="UPTREND (HH/HL)")
    rationale = tb._build_trade_rationale(
        direction="BULL",
        trend_text="UPTREND (HH/HL)",
        last_event_label="Bullish BOS",
        oi_buildup="LONG_BUILDUP",
        structure=structure,
    )
    assert "resistance" in rationale
    assert "110.00" in rationale
    assert "long buildup" in rationale.lower()
    assert "uptrend" in rationale.lower()


def test_trade_rationale_is_honest_with_nothing_to_report() -> None:
    structure = tb.TradeStructure()
    rationale = tb._build_trade_rationale(
        direction="BULL",
        trend_text="RANGE / UNDEFINED",
        last_event_label="",
        oi_buildup="NEUTRAL",
        structure=structure,
    )
    assert rationale == "No structural or order-flow evidence yet." or "range" in rationale.lower()


async def test_fast_exit_tag_fires_when_active_long_position_breaks_support(monkeypatch) -> None:
    monkeypatch.setattr(
        tb,
        "compute_mtf",
        _mtf_with_structure(blocker_up=110.0, blocker_down=95.0, donch_high=115.0, donch_low=90.0),
    )
    active_trade = {"symbol": "RELIANCE", "status": "WATCH", "decision": "BUY CE"}
    redis = _FakeRedis(
        hashes={"infusion:feature:RELIANCE": {"ltp": "94.0"}},  # below support (95.0)
        lists={"infusion:journal:paper_trades": [json.dumps(active_trade)]},
    )
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert "FAST_EXIT" in blueprint.warning_tags
    assert "STRUCTURAL_BREAK" not in blueprint.warning_tags  # 94.0 is still above channel_lower (90.0)


async def test_structural_break_tag_fires_when_channel_bound_gives_way(monkeypatch) -> None:
    monkeypatch.setattr(
        tb,
        "compute_mtf",
        _mtf_with_structure(blocker_up=110.0, blocker_down=95.0, donch_high=115.0, donch_low=90.0),
    )
    active_trade = {"symbol": "RELIANCE", "status": "PLANNED", "decision": "BUY CE"}
    redis = _FakeRedis(
        hashes={"infusion:feature:RELIANCE": {"ltp": "88.0"}},  # below channel_lower (90.0) too
        lists={"infusion:journal:paper_trades": [json.dumps(active_trade)]},
    )
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert "FAST_EXIT" in blueprint.warning_tags
    assert "STRUCTURAL_BREAK" in blueprint.warning_tags


async def test_no_fast_exit_tags_without_an_active_journal_position() -> None:
    redis = _FakeRedis({"infusion:feature:RELIANCE": {"ltp": "50.0"}})
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert "FAST_EXIT" not in blueprint.warning_tags
    assert "STRUCTURAL_BREAK" not in blueprint.warning_tags


async def test_closed_journal_rows_are_not_treated_as_active_positions(monkeypatch) -> None:
    monkeypatch.setattr(
        tb,
        "compute_mtf",
        _mtf_with_structure(blocker_up=110.0, blocker_down=95.0, donch_high=115.0, donch_low=90.0),
    )
    closed_trade = {"symbol": "RELIANCE", "status": "CLOSED", "decision": "BUY CE"}
    redis = _FakeRedis(
        hashes={"infusion:feature:RELIANCE": {"ltp": "50.0"}},  # well below every bound
        lists={"infusion:journal:paper_trades": [json.dumps(closed_trade)]},
    )
    blueprint = await tb.build_trade_blueprint(redis, "RELIANCE")
    assert "FAST_EXIT" not in blueprint.warning_tags
    assert "STRUCTURAL_BREAK" not in blueprint.warning_tags
