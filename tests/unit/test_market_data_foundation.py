from types import SimpleNamespace

from feature_engine.bar_builder import update_bars
from feature_engine.engine import FeatureEngine
from feature_engine.features.volume import get_relative_volume
from feature_engine.state import SymbolState
from api.routes.charts import _aggregate, _merge_bars


def config():
    return SimpleNamespace(
        batch_max_ticks=200, batch_timer_ms=5, rsi_period=14,
        macd_fast=12, macd_slow=26, macd_signal=9, atr_period=14,
        bb_period=20, cci_period=20,
    )


def tick(ts_ms, price, cumulative_volume):
    return {
        "symbol": "TEST", "ltp": price, "open": 100, "high": 102,
        "low": 99, "close": 100, "volume": cumulative_volume,
        "exchange_timestamp_ms": ts_ms,
    }


def test_bar_builder_uses_incremental_not_cumulative_volume():
    state = SymbolState("TEST")
    start = 1_700_000_000_000
    assert update_bars(state, 100, 0, start) == []
    update_bars(state, 101, 25, start + 10_000)
    completed = update_bars(state, 102, 10, start + 60_000)
    one_minute = next(bar for timeframe, bar in completed if timeframe == 1)
    assert one_minute.volume == 25
    assert (one_minute.open, one_minute.high, one_minute.low, one_minute.close) == (100, 101, 100, 101)


def test_indicators_advance_only_when_one_minute_bar_closes():
    engine = FeatureEngine(config())
    state = SymbolState("TEST")
    start = 1_700_000_000_000
    first, _ = engine._compute(state, tick(start, 100, 1_000))
    second, _ = engine._compute(state, tick(start + 10_000, 101, 1_025))
    assert not first.bar_closed_1m
    assert not second.bar_closed_1m
    assert state.completed_1m_bars == 0
    closed, _ = engine._compute(state, tick(start + 60_000, 102, 1_035))
    assert closed.bar_closed_1m
    assert state.completed_1m_bars == 1
    assert len(state.bb_prices) == 1
    assert list(state.volume_history) == [25]


def test_relative_volume_requires_real_same_time_profile():
    state = SymbolState("TEST")
    state.last_tick_exchange_ms = 1_700_000_000_000
    state.session_cumulative_volume = 200
    assert get_relative_volume(state) == 0.0
    minute_of_day = ((state.last_tick_exchange_ms // 60000) + 330) % 1440
    session_minute = max(0, minute_of_day - 555)
    state.volume_profile = {session_minute: 100.0}
    state.volume_profile_ready = True
    assert get_relative_volume(state) == 2.0


def test_chart_merge_deduplicates_and_aggregates_real_bars():
    history = [{"time": 60, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 5}]
    live = [
        {"time": 60, "open": 10, "high": 13, "low": 9, "close": 12, "volume": 7},
        {"time": 120, "open": 12, "high": 14, "low": 11, "close": 13, "volume": 8},
    ]
    merged = _merge_bars(history, live)
    assert len(merged) == 2
    aggregated = _aggregate(merged, 5)
    assert aggregated == [{"time": 0, "open": 10, "high": 14, "low": 9, "close": 13, "volume": 15}]
