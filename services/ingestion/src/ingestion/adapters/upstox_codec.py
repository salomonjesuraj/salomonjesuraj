"""Minimal decoder for Upstox Market Data Feed V3 protobuf messages.

Upstox publishes the official ``MarketDataFeed.proto`` file, but keeping a
small local decoder avoids a generated-code build step in this service.  This
module only decodes the fields Infusion needs for live ticks: LTPC, first
depth, OHLC, volume, OI and top-level feed instrument keys.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


class ProtoDecodeError(ValueError):
    """Raised when a protobuf frame cannot be decoded."""


@dataclass(slots=True)
class DecodedFeed:
    instrument_key: str
    ltp: float = 0.0
    close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: int = 0
    oi: int = 0
    total_buy_qty: int = 0
    total_sell_qty: int = 0
    best_bid: float = 0.0
    best_ask: float = 0.0
    best_bid_qty: int = 0
    best_ask_qty: int = 0
    exchange_timestamp_ms: int = 0
    # EBIE EB-6: up to 5 {bidP,bidQ,askP,askQ} levels, best-first. Empty
    # for feed types that only ever carry one level (index feeds, the
    # FirstLevelWithGreeks options message type) -- never fabricated.
    depth_levels: list | None = None


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift >= 70:
            break
    raise ProtoDecodeError("invalid_varint")


def _fields(data: bytes):
    pos = 0
    size = len(data)
    while pos < size:
        tag, pos = _read_varint(data, pos)
        field_no = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:
            value, pos = _read_varint(data, pos)
            yield field_no, wire_type, value
        elif wire_type == 1:
            if pos + 8 > size:
                raise ProtoDecodeError("truncated_fixed64")
            yield field_no, wire_type, data[pos:pos + 8]
            pos += 8
        elif wire_type == 2:
            length, pos = _read_varint(data, pos)
            end = pos + length
            if end > size:
                raise ProtoDecodeError("truncated_length_delimited")
            yield field_no, wire_type, data[pos:end]
            pos = end
        elif wire_type == 5:
            if pos + 4 > size:
                raise ProtoDecodeError("truncated_fixed32")
            yield field_no, wire_type, data[pos:pos + 4]
            pos += 4
        else:
            raise ProtoDecodeError(f"unsupported_wire_type_{wire_type}")


def _double(value) -> float:
    return float(struct.unpack("<d", value)[0]) if isinstance(value, bytes) else 0.0


def _text(value) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else ""


def _parse_ltpc(data: bytes) -> dict:
    out = {"ltp": 0.0, "ltt": 0, "ltq": 0, "cp": 0.0}
    for field_no, _, value in _fields(data):
        if field_no == 1:
            out["ltp"] = _double(value)
        elif field_no == 2:
            out["ltt"] = int(value)
        elif field_no == 3:
            out["ltq"] = int(value)
        elif field_no == 4:
            out["cp"] = _double(value)
    return out


def _parse_quote(data: bytes) -> dict:
    out = {"bidQ": 0, "bidP": 0.0, "askQ": 0, "askP": 0.0}
    for field_no, _, value in _fields(data):
        if field_no == 1:
            out["bidQ"] = int(value)
        elif field_no == 2:
            out["bidP"] = _double(value)
        elif field_no == 3:
            out["askQ"] = int(value)
        elif field_no == 4:
            out["askP"] = _double(value)
    return out


def _parse_market_level(data: bytes) -> list[dict]:
    """Parse EVERY depth level in a MarketLevel message.

    EBIE EB-6 fix: Upstox's real MarketLevel message has a REPEATED
    Quote field (field_no==1 occurs once per depth level -- up to 5 for
    the "full" subscription mode ingestion has used since it first
    connected). The original version of this function returned on the
    FIRST occurrence only, silently discarding levels 2-5 on every
    single tick -- real 5-level depth has been arriving over the wire
    the whole time, not something that needed a new subscription mode
    or Upstox Plus entitlement to unlock. See docs/EBIE-BLUEPRINT.md
    Section 4.6 / the authorized D5-baseline decision.
    """
    levels: list[dict] = []
    for field_no, _, value in _fields(data):
        if field_no == 1 and isinstance(value, bytes):
            levels.append(_parse_quote(value))
    return levels


def _parse_ohlc(data: bytes) -> dict:
    out = {"interval": "", "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "vol": 0, "ts": 0}
    for field_no, _, value in _fields(data):
        if field_no == 1:
            out["interval"] = _text(value)
        elif field_no == 2:
            out["open"] = _double(value)
        elif field_no == 3:
            out["high"] = _double(value)
        elif field_no == 4:
            out["low"] = _double(value)
        elif field_no == 5:
            out["close"] = _double(value)
        elif field_no == 6:
            out["vol"] = int(value)
        elif field_no == 7:
            out["ts"] = int(value)
    return out


def _parse_market_ohlc(data: bytes) -> dict:
    latest = {}
    daily = {}
    for field_no, _, value in _fields(data):
        if field_no != 1 or not isinstance(value, bytes):
            continue
        bar = _parse_ohlc(value)
        interval = str(bar.get("interval", "")).lower()
        if interval in {"1d", "d1"}:
            daily = bar
        elif interval in {"i1", "1m", "minute"}:
            latest = bar
    return daily or latest


def _parse_market_full_feed(data: bytes) -> dict:
    out: dict = {}
    for field_no, _, value in _fields(data):
        if field_no == 1 and isinstance(value, bytes):
            out["ltpc"] = _parse_ltpc(value)
        elif field_no == 2 and isinstance(value, bytes):
            # EBIE EB-6: depth_levels is the full (up to 5) parsed list;
            # first_depth stays level[0] exactly as before this fix, so
            # every existing best_bid/best_ask consumer is unaffected.
            levels = _parse_market_level(value)
            out["depth_levels"] = levels
            out["first_depth"] = levels[0] if levels else {}
        elif field_no == 4 and isinstance(value, bytes):
            out["ohlc"] = _parse_market_ohlc(value)
        elif field_no == 6:
            out["volume"] = int(value)
        elif field_no == 7:
            out["oi"] = int(_double(value))
        elif field_no == 9:
            out["tbq"] = int(_double(value))
        elif field_no == 10:
            out["tsq"] = int(_double(value))
    return out


def _parse_index_full_feed(data: bytes) -> dict:
    out: dict = {}
    for field_no, _, value in _fields(data):
        if field_no == 1 and isinstance(value, bytes):
            out["ltpc"] = _parse_ltpc(value)
        elif field_no == 2 and isinstance(value, bytes):
            out["ohlc"] = _parse_market_ohlc(value)
    return out


def _parse_full_feed(data: bytes) -> dict:
    for field_no, _, value in _fields(data):
        if field_no == 1 and isinstance(value, bytes):
            return _parse_market_full_feed(value)
        if field_no == 2 and isinstance(value, bytes):
            return _parse_index_full_feed(value)
    return {}


def _parse_first_level_with_greeks(data: bytes) -> dict:
    out: dict = {}
    for field_no, _, value in _fields(data):
        if field_no == 1 and isinstance(value, bytes):
            out["ltpc"] = _parse_ltpc(value)
        elif field_no == 2 and isinstance(value, bytes):
            out["first_depth"] = _parse_quote(value)
        elif field_no == 4:
            out["volume"] = int(value)
        elif field_no == 5:
            out["oi"] = int(_double(value))
    return out


def _parse_feed(data: bytes) -> dict:
    for field_no, _, value in _fields(data):
        if field_no == 1 and isinstance(value, bytes):
            return {"ltpc": _parse_ltpc(value)}
        if field_no == 2 and isinstance(value, bytes):
            return _parse_full_feed(value)
        if field_no == 3 and isinstance(value, bytes):
            return _parse_first_level_with_greeks(value)
    return {}


def _parse_feed_map_entry(data: bytes) -> tuple[str, dict] | None:
    key = ""
    feed = {}
    for field_no, _, value in _fields(data):
        if field_no == 1:
            key = _text(value)
        elif field_no == 2 and isinstance(value, bytes):
            feed = _parse_feed(value)
    if not key or not feed:
        return None
    return key, feed


def _to_decoded_feed(instrument_key: str, feed: dict, current_ts: int) -> DecodedFeed | None:
    ltpc = feed.get("ltpc") or {}
    ltp = float(ltpc.get("ltp") or 0)
    if ltp <= 0:
        return None

    ohlc = feed.get("ohlc") or {}
    depth = feed.get("first_depth") or {}
    close = float(ltpc.get("cp") or ohlc.get("close") or ltp)
    return DecodedFeed(
        instrument_key=instrument_key,
        ltp=ltp,
        close=close,
        open=float(ohlc.get("open") or ltp),
        high=float(ohlc.get("high") or ltp),
        low=float(ohlc.get("low") or ltp),
        volume=int(feed.get("volume") or ohlc.get("vol") or ltpc.get("ltq") or 0),
        oi=int(feed.get("oi") or 0),
        total_buy_qty=int(feed.get("tbq") or 0),
        total_sell_qty=int(feed.get("tsq") or 0),
        best_bid=float(depth.get("bidP") or 0),
        best_ask=float(depth.get("askP") or 0),
        best_bid_qty=int(depth.get("bidQ") or 0),
        best_ask_qty=int(depth.get("askQ") or 0),
        exchange_timestamp_ms=int(ltpc.get("ltt") or current_ts or 0),
        depth_levels=feed.get("depth_levels") or [],
    )


def decode_feed_response(data: bytes) -> list[DecodedFeed]:
    """Decode a Market Data Feed V3 ``FeedResponse`` frame."""
    current_ts = 0
    raw_feeds: list[tuple[str, dict]] = []
    for field_no, _, value in _fields(data):
        if field_no == 2 and isinstance(value, bytes):
            entry = _parse_feed_map_entry(value)
            if entry:
                raw_feeds.append(entry)
        elif field_no == 3:
            current_ts = int(value)

    decoded = []
    for instrument_key, feed in raw_feeds:
        item = _to_decoded_feed(instrument_key, feed, current_ts)
        if item:
            decoded.append(item)
    return decoded
