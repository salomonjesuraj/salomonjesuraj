"""Tick data models — raw and normalized.

RawTickV1: broker-specific tick as received from ingestion adapter.
NormalizedTickV1: universal tick after symbol resolution, dedup, throttling.
"""

from pydantic import BaseModel


class RawTickV1(BaseModel, frozen=True):
    """Broker-specific tick. Output of ingestion adapter."""

    broker: str  # "upstox" | "kite" | "mock"
    instrument_key: str  # Broker-specific identifier
    exchange: str  # "NSE" | "BSE"
    segment: str  # "EQ" | "FO" | "INDEX"
    ltp: float
    open: float
    high: float
    low: float
    close: float  # Previous day close
    volume: int
    oi: int = 0  # 0 for non-F&O
    total_buy_qty: int = 0
    total_sell_qty: int = 0
    best_bid: float = 0.0
    best_ask: float = 0.0
    best_bid_qty: int = 0
    best_ask_qty: int = 0
    exchange_timestamp_ms: int  # UTC epoch milliseconds (authoritative)
    received_at_us: int  # Local receipt epoch microseconds
    # EBIE EB-6: up to 5 {bidP,bidQ,askP,askQ} levels, best-first --
    # real data, not a placeholder: Upstox's "full" subscription mode
    # (the only mode this adapter has ever used) already sends 5 levels
    # per tick; a codec bug (upstox_codec.py's _parse_market_level)
    # previously discarded levels 2-5 by returning on the first repeated
    # protobuf field instead of collecting all of them. Empty list for
    # feed types that only ever carry one level (index feeds).
    depth_levels: list = []


class NormalizedTickV1(BaseModel, frozen=True):
    """Universal tick. Output of normalizer."""

    symbol: str  # "RELIANCE", "INFY"
    sector_id: str  # "NIFTY_BANK", "NIFTY_IT", "UNCATEGORIZED"
    is_fno: bool
    tier: int  # 1, 2, or 3
    ltp: float
    open: float
    high: float
    low: float
    close: float  # Previous day close
    volume: int
    oi: int = 0
    best_bid: float = 0.0
    best_ask: float = 0.0
    best_bid_qty: int = 0
    best_ask_qty: int = 0
    # EBIE EB-15 Phase 1 item 2: real exchange-wide aggregate quantities
    # (Upstox's MarketFullFeed tbq/tsq protobuf fields -- distinct from
    # best_bid_qty/best_ask_qty above, which are level-1 depth only).
    # RawTickV1 has always computed these correctly; this model just never
    # carried them through before now -- see transformer.py's transform().
    total_buy_qty: int = 0
    total_sell_qty: int = 0
    exchange_timestamp_ms: int
    received_at_us: int
    normalized_at_us: int
    # EBIE EB-6: real 5-level depth, carried through unchanged from
    # RawTickV1 -- see that model's own field comment for the full story.
    depth_levels: list = []
    # EBIE EB-0 event-time integrity: True when this tick's
    # exchange_timestamp_ms is older than the newest one already seen for
    # this symbol (see normalizer/ordering.py). Never dropped for this --
    # only flagged, so Data Quality scoring downstream can account for it.
    is_out_of_order: bool = False
