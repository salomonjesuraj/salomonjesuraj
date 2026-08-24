from types import SimpleNamespace

from api.routes.charts import _aggregate, _merge_bars
from api.routes.ticks import _finalize_stock_breakout_tier, _scanner_intel
from feature_engine.bar_builder import update_bars
from feature_engine.engine import FeatureEngine
from feature_engine.features.volume import get_relative_volume
from feature_engine.state import SymbolState


def config():
    return SimpleNamespace(
        batch_max_ticks=200,
        batch_timer_ms=5,
        rsi_period=14,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        atr_period=14,
        bb_period=20,
        cci_period=20,
    )


def tick(ts_ms, price, cumulative_volume):
    return {
        "symbol": "TEST",
        "ltp": price,
        "open": 100,
        "high": 102,
        "low": 99,
        "close": 100,
        "volume": cumulative_volume,
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
    assert (one_minute.open, one_minute.high, one_minute.low, one_minute.close) == (
        100,
        101,
        100,
        101,
    )


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


def breakout_entry():
    return {
        "ltp": 102,
        "change_pct": 1.2,
        "day_high": 102,
        "day_low": 98,
        "prev_close": 100,
    }


def breakout_features(volume_profile_ready=True):
    return {
        "ltp": 102,
        "change_pct": 1.2,
        "vwap": 100,
        "ema_5": 101.8,
        "ema_9": 101.5,
        "ema_20": 100.5,
        "ema_50": 99.5,
        "rsi_14": 58,
        "macd": 1.2,
        "macd_signal": 0.7,
        "macd_hist": 0.5,
        "rel_vol_20d": 3.0,
        "bb_width": 0.02,
        "spread_bps": 8,
        "atr_trend": "BULL",
        "atr_trail_stop": 99,
        "squeeze_state": "BUILDING",
        "candle_pattern": "Bullish Engulfing",
        "atr_14": 1.0,
        "prev_close": 100,
        "day_high": 102,
        "day_low": 98,
        "volume_profile_ready": str(volume_profile_ready),
    }


def test_stock_breakout_score_requires_real_volume_profile_for_rvol_points():
    ready = _scanner_intel(breakout_entry(), breakout_features(volume_profile_ready=True))
    missing = _scanner_intel(breakout_entry(), breakout_features(volume_profile_ready=False))
    assert ready["volume_profile_ready"] is True
    assert missing["volume_profile_ready"] is False
    assert ready["rel_vol"] == missing["rel_vol"] == 3.0
    assert ready["stock_breakout_score"] - missing["stock_breakout_score"] == 25.0


def test_option_ready_tier_only_upgrades_qualified_stock_breakouts():
    qualified = {
        "stock_breakout_score": 72,
        "anti_chase_reasons": [],
        "chase_quality": "CLEAN",
        "chain_trade_ready": True,
    }
    assert _finalize_stock_breakout_tier(qualified) == "OPTION_READY"

    early_watch = {
        "stock_breakout_score": 62,
        "anti_chase_reasons": [],
        "chase_quality": "CLEAN",
        "chain_trade_ready": True,
    }
    assert _finalize_stock_breakout_tier(early_watch) == "EARLY_WATCH"

    no_contract = {
        "stock_breakout_score": 72,
        "anti_chase_reasons": [],
        "chase_quality": "CLEAN",
        "chain_trade_ready": False,
    }
    assert _finalize_stock_breakout_tier(no_contract) == "BREAKOUT_NOW"
