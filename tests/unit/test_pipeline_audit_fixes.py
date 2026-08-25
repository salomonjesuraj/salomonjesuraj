"""Unit tests for pipeline-audit fixes C1 (exchange ATP / VWAP drift) and
C3 (wall-clock force-close of stale bars), 2026-08-25.
"""

from __future__ import annotations

import struct

from feature_engine.bar_builder import FORCE_CLOSE_GRACE_MS, force_close_stale_bars
from feature_engine.features.price import get_vwap_drift, update_price_features
from feature_engine.state import OHLCBar, SymbolState
from ingestion.adapters.upstox_codec import _parse_market_full_feed


def _proto_double_field(field_no: int, value: float) -> bytes:
    """Hand-encode one protobuf double field (wire_type 1, fixed64) --
    tag = (field_no << 3) | 1, followed by 8 little-endian bytes."""
    tag = (field_no << 3) | 1
    assert tag < 128, "test helper only supports single-byte tags"
    return bytes([tag]) + struct.pack("<d", value)


def _proto_varint_field(field_no: int, value: int) -> bytes:
    tag = (field_no << 3) | 0
    assert tag < 128
    out = bytearray([tag])
    v = value
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


# ── C1: codec parses atp (field 5) and iv (field 8) ─────────────────────


def test_parse_market_full_feed_extracts_atp_and_iv() -> None:
    data = _proto_double_field(5, 1234.56) + _proto_double_field(8, 42.5)
    out = _parse_market_full_feed(data)
    assert out["atp"] == 1234.56
    assert out["iv"] == 42.5


def test_parse_market_full_feed_still_extracts_volume_and_oi_alongside_atp() -> None:
    """Regression guard: adding fields 5/8 must not disturb the existing
    field 6 (volume, varint) / field 7 (oi, double) parsing."""
    data = (
        _proto_double_field(5, 100.0)
        + _proto_varint_field(6, 5000)
        + _proto_double_field(7, 250.0)
        + _proto_double_field(8, 30.0)
    )
    out = _parse_market_full_feed(data)
    assert out["atp"] == 100.0
    assert out["volume"] == 5000
    assert out["oi"] == 250
    assert out["iv"] == 30.0


def test_parse_market_full_feed_without_atp_field_has_no_atp_key() -> None:
    """A feed type that never carries atp (e.g. what index ticks send
    through this same parser path) must not fabricate a value."""
    data = _proto_varint_field(6, 100)
    out = _parse_market_full_feed(data)
    assert "atp" not in out


# ── C1: VWAP drift surfaces reliability, never fabricates a read ───────


def test_vwap_drift_unavailable_before_any_exchange_atp_seen() -> None:
    state = SymbolState(symbol="TEST")
    update_price_features(state, ltp=100.0, volume=10)  # no exchange_atp passed
    drift = get_vwap_drift(state)
    assert drift == {
        "available": False,
        "exchange_atp": None,
        "local_vwap": None,
        "drift_pct": None,
    }


def test_vwap_drift_computed_once_exchange_atp_is_present() -> None:
    state = SymbolState(symbol="TEST")
    update_price_features(state, ltp=101.0, volume=100, exchange_atp=100.0)
    drift = get_vwap_drift(state)
    assert drift["available"] is True
    assert drift["exchange_atp"] == 100.0
    assert drift["local_vwap"] == 101.0
    assert drift["drift_pct"] == 1.0  # local is 1% above the exchange's own ATP


def test_vwap_drift_keeps_last_known_good_atp_when_a_tick_omits_it() -> None:
    """A single tick missing atp (e.g. an index tick, or a transient gap)
    must not blow away the previously observed exchange_atp and make the
    drift read flicker to unavailable."""
    state = SymbolState(symbol="TEST")
    update_price_features(state, ltp=100.0, volume=50, exchange_atp=99.0)
    update_price_features(state, ltp=102.0, volume=80, exchange_atp=0.0)  # missing this tick
    drift = get_vwap_drift(state)
    assert drift["available"] is True
    assert drift["exchange_atp"] == 99.0  # unchanged, not zeroed


# ── C3: wall-clock force-close of stale bars ────────────────────────────


BAR_START_MS = 1_700_000_000_000  # a plausible real exchange timestamp -- 0 is
# reserved by update_bars()/OHLCBar's own convention as "not yet initialized"
# (see bar_builder.py's `if bar.bar_start_ms == 0: bar.bar_start_ms = bar_start`),
# so tests must never use 0 as a "real" bar start.


def _stale_bar(bar_start_ms: int = BAR_START_MS) -> OHLCBar:
    return OHLCBar(
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=500,
        tick_count=3,
        bar_start_ms=bar_start_ms,
    )


def test_force_close_does_nothing_before_the_bar_window_plus_grace_elapses() -> None:
    state = SymbolState(symbol="TEST")
    state.bar_1m = _stale_bar()
    bar_end_ms = BAR_START_MS + 60_000  # 1-minute bar
    still_within_grace = bar_end_ms + FORCE_CLOSE_GRACE_MS - 1
    completed = force_close_stale_bars(state, now_ms=still_within_grace)
    assert completed == []
    assert state.bar_1m.tick_count == 3  # untouched


def test_force_close_emits_once_the_window_and_grace_have_fully_elapsed() -> None:
    state = SymbolState(symbol="TEST")
    state.bar_1m = _stale_bar()
    bar_end_ms = BAR_START_MS + 60_000
    now_ms = bar_end_ms + FORCE_CLOSE_GRACE_MS + 1
    completed = force_close_stale_bars(state, now_ms=now_ms)
    assert len(completed) == 1
    tf, bar = completed[0]
    assert tf == 1
    assert bar.close == 101.0
    # Marked spent so a later real tick's own update_bars() call doesn't
    # re-emit the same bar a second time.
    assert state.bar_1m.tick_count == 0


def test_force_close_is_idempotent_and_never_double_emits() -> None:
    """Calling the timer twice in a row (as the real periodic loop does)
    must only ever emit a given stale bar once."""
    state = SymbolState(symbol="TEST")
    state.bar_1m = _stale_bar()
    now_ms = BAR_START_MS + 60_000 + FORCE_CLOSE_GRACE_MS + 1
    first = force_close_stale_bars(state, now_ms=now_ms)
    second = force_close_stale_bars(state, now_ms=now_ms + 5_000)
    assert len(first) == 1
    assert second == []  # tick_count already 0 -- nothing left to close


def test_force_close_ignores_a_bar_with_no_ticks_yet() -> None:
    """A genuinely empty bar (tick_count == 0, e.g. a symbol that never
    ticked at all this period) must not be force-emitted as a fabricated
    flat candle."""
    state = SymbolState(symbol="TEST")
    state.bar_1m = OHLCBar(bar_start_ms=BAR_START_MS, tick_count=0)
    now_ms = BAR_START_MS + 60_000 + FORCE_CLOSE_GRACE_MS + 1
    assert force_close_stale_bars(state, now_ms=now_ms) == []
