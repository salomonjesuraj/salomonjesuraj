"""Transformer — RawTick payload + SymbolInfo -> NormalizedTickV1."""

from infusion_common.timing import now_us
from infusion_models.tick import NormalizedTickV1

from normalizer.resolver import SymbolInfo


def transform(
    raw_payload: dict, info: SymbolInfo, is_out_of_order: bool = False
) -> NormalizedTickV1:
    """Transform raw tick payload + symbol info into NormalizedTick."""
    return NormalizedTickV1(
        symbol=info.symbol,
        sector_id=info.sector_id,
        is_fno=info.is_fno,
        tier=info.tier,
        ltp=raw_payload["ltp"],
        open=raw_payload["open"],
        high=raw_payload["high"],
        low=raw_payload["low"],
        close=raw_payload["close"],
        volume=raw_payload["volume"],
        oi=raw_payload.get("oi", 0),
        best_bid=raw_payload.get("best_bid", 0.0),
        best_ask=raw_payload.get("best_ask", 0.0),
        best_bid_qty=raw_payload.get("best_bid_qty", 0),
        best_ask_qty=raw_payload.get("best_ask_qty", 0),
        # EBIE EB-15 Phase 1 item 2: real exchange-wide tbq/tsq, previously
        # silently dropped here -- RawTickV1 has always had them (see that
        # model's own field comment).
        total_buy_qty=raw_payload.get("total_buy_qty", 0),
        total_sell_qty=raw_payload.get("total_sell_qty", 0),
        exchange_timestamp_ms=raw_payload["exchange_timestamp_ms"],
        received_at_us=raw_payload["received_at_us"],
        normalized_at_us=now_us(),
        is_out_of_order=is_out_of_order,
        depth_levels=raw_payload.get("depth_levels") or [],
    )
