"""Symbol universe loader — reads JSON config files and populates Redis.

Responsibilities:
  1. Load nifty{50,100,200,500}.json/fno.json + indices.json from config directory
  2. Filter by the configured tier (INFUSION_SYMBOL_UNIVERSE env var)
  3. Populate infusion:symbols Redis hash with msgpack-encoded metadata
  4. Build reverse mapping: instrument_key → symbol for ingestion

Design:
  - Each tier file is SELF-CONTAINED (nifty200.json has all 200 stocks)
  - Indices are included for index universes; fno is stocks-only for scanner purity
  - No dynamic web scraping — JSON files are the single source of truth
  - Files can be updated without code changes
"""

from __future__ import annotations

import json
from pathlib import Path

import aiohttp
import msgpack
import structlog
from infusion_streams.constants import KEY_SYMBOLS
from redis.asyncio import Redis

logger = structlog.get_logger()

VALID_TIERS = {"nifty50", "nifty100", "nifty200", "nifty500", "fno", "custom"}
UPSTOX_NSE_INSTRUMENTS_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
)


def load_universe(config_dir: str, tier: str) -> dict[str, dict]:
    """Load symbol universe for the specified tier.

    Args:
        config_dir: Path to directory containing nifty*.json and indices.json
        tier: One of nifty50, nifty100, nifty200, nifty500, custom

    Returns:
        Dict of symbol → metadata (merged with indices)
    """
    if tier not in VALID_TIERS:
        raise ValueError(f"Invalid symbol_universe '{tier}'. Valid: {VALID_TIERS}")

    config_path = Path(config_dir)

    # Load tier-specific file
    if tier == "custom":
        # Custom watchlist is loaded from Redis, not from file
        symbols = {}
    else:
        tier_file = config_path / f"{tier}.json"
        if not tier_file.exists():
            raise FileNotFoundError(f"Symbol universe file not found: {tier_file}")
        with open(tier_file, encoding="utf-8") as f:
            symbols = json.load(f)
        logger.info(
            "universe_loaded",
            tier=tier,
            file=str(tier_file),
            count=len(symbols),
        )

    # Load indices for index universes. Keep fno stocks-only so dashboard F&O
    # scanner count matches the current derivative stock list exactly. Top
    # market ticker fetches NIFTY/BANKNIFTY separately.
    indices_file = config_path / "indices.json"
    if tier != "fno" and indices_file.exists():
        with open(indices_file, encoding="utf-8") as f:
            indices = json.load(f)
        symbols.update(indices)
        logger.info("indices_loaded", count=len(indices))
    elif tier != "fno":
        logger.warning("indices_file_missing", path=str(indices_file))

    return symbols


async def load_custom_watchlist(redis: Redis) -> dict[str, dict]:
    """Load custom watchlist symbols from Redis set.

    The custom watchlist is stored as a Redis set at infusion:watchlist:custom.
    Each member is a symbol name. Full metadata is looked up from the
    nifty500.json master file.
    """
    members = await redis.smembers("infusion:watchlist:custom")
    if not members:
        return {}

    symbols = {m.decode() if isinstance(m, bytes) else m for m in members}
    logger.info("custom_watchlist_loaded", count=len(symbols))
    return {s: {} for s in symbols}  # metadata resolved later


async def populate_redis(redis: Redis, symbols: dict[str, dict]) -> tuple[int, dict[str, str]]:
    """Write symbol metadata to infusion:symbols hash.

    Each entry is stored as:
      key:   instrument_key (e.g., "NSE_EQ|INE002A01018")
      value: msgpack-encoded metadata dict

    Args:
        redis: Redis connection
        symbols: dict of symbol → metadata

    Returns:
        (count, reverse_map) where reverse_map is instrument_key → symbol
    """
    upstox_master = await fetch_upstox_equity_master()
    pipe = redis.pipeline()
    pipe.delete(KEY_SYMBOLS)
    reverse_map: dict[str, str] = {}
    count = 0

    for symbol, meta in symbols.items():
        instrument_key = meta.get("upstox_key", "")
        master_row = upstox_master.get(symbol)
        if master_row:
            current_key = master_row.get("instrument_key", "")
            if current_key and current_key != instrument_key:
                logger.info(
                    "upstox_key_refreshed",
                    symbol=symbol,
                    old_key=instrument_key,
                    new_key=current_key,
                )
                instrument_key = current_key
            meta = {
                **meta,
                "upstox_key": instrument_key,
                "isin": master_row.get("isin", meta.get("isin", "")),
                "exchange_token": master_row.get("exchange_token", meta.get("exchange_token", "")),
                "lot_size": int(master_row.get("lot_size") or meta.get("lot_size") or 1),
            }
        if not instrument_key:
            logger.warning("missing_upstox_key", symbol=symbol)
            continue

        # Ensure symbol is in the metadata
        entry = {**meta, "symbol": symbol}

        # Store as msgpack for compact storage
        packed = msgpack.packb(entry, use_bin_type=True)
        pipe.hset(KEY_SYMBOLS, instrument_key, packed)

        reverse_map[instrument_key] = symbol
        count += 1

    # Execute pipeline
    await pipe.execute()

    logger.info(
        "symbols_populated",
        redis_key=KEY_SYMBOLS,
        count=count,
        sectors=len({m.get("sector_id", "?") for m in symbols.values()}),
    )

    return count, reverse_map


async def fetch_upstox_equity_master() -> dict[str, dict]:
    """Fetch current Upstox NSE equity instrument keys, with live F&O lot sizes.

    Upstox refreshes the instrument files daily.  Local symbol JSON is useful
    for sectors/tier metadata, but the broker instrument_key must be current.
    If the download fails, we safely fall back to the checked-in keys.

    The single NSE.json.gz payload bundles every NSE segment (NSE_EQ, NSE_FO,
    NSE_INDEX, ...), not just equities. lot_size on an NSE_EQ row is always 1
    (cash-market shares) — it is NOT the F&O contract lot size. We separately
    scan the NSE_FO rows in the same payload for each underlying's current lot
    size (keyed by underlying_symbol, which is consistent across strikes/
    expiries for a given underlying) and stamp that onto the returned equity
    row instead, so downstream lot_size consumers (position sizing, risk)
    get the real derivatives lot size rather than the cash-market default of 1.
    """
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                UPSTOX_NSE_INSTRUMENTS_URL,
                headers={"Accept": "application/gzip, application/json"},
                timeout=30,
            ) as resp,
        ):
            if resp.status != 200:
                logger.warning("upstox_master_fetch_failed", status=resp.status)
                return {}
            payload = await resp.read()
    except Exception as exc:
        logger.warning("upstox_master_fetch_error", error=str(exc))
        return {}

    try:
        import gzip

        rows = json.loads(gzip.decompress(payload).decode("utf-8"))
    except Exception as exc:
        logger.warning("upstox_master_decode_error", error=str(exc))
        return {}

    # F&O lot size per underlying: NSE_EQ.lot_size is always 1 (cash shares),
    # so the real derivatives lot size must come from NSE_FO rows instead.
    # Pick the lot size from the nearest-expiry contract per underlying, in
    # case multiple live expiries momentarily disagree during a lot-size
    # transition window.
    fo_lot_by_underlying: dict[str, tuple[int, int]] = {}  # symbol -> (expiry, lot_size)
    for row in rows:
        if row.get("segment") != "NSE_FO":
            continue
        underlying = row.get("underlying_symbol", "")
        lot_size = row.get("lot_size")
        expiry = row.get("expiry") or 0
        if not underlying or not lot_size:
            continue
        prev = fo_lot_by_underlying.get(underlying)
        if prev is None or expiry < prev[0]:
            fo_lot_by_underlying[underlying] = (expiry, int(lot_size))

    out = {}
    for row in rows:
        if row.get("segment") != "NSE_EQ" or row.get("instrument_type") != "EQ":
            continue
        symbol = row.get("trading_symbol", "")
        instrument_key = row.get("instrument_key", "")
        if not (symbol and instrument_key):
            continue
        fo_lot = fo_lot_by_underlying.get(symbol)
        if fo_lot:
            row = {**row, "lot_size": fo_lot[1]}
        out[symbol] = row

    logger.info(
        "upstox_equity_master_loaded",
        count=len(out),
        fo_lot_sizes_matched=sum(1 for s in out if s in fo_lot_by_underlying),
    )
    return out


def get_instrument_keys(symbols: dict[str, dict]) -> list[str]:
    """Extract Upstox instrument keys for subscription.

    Returns only EQ segment keys (not INDEX — Upstox uses a different
    subscription mode for indices).
    """
    keys = []
    for _symbol, meta in symbols.items():
        key = meta.get("upstox_key", "")
        if key:
            keys.append(key)
    return keys


def get_sector_summary(symbols: dict[str, dict]) -> dict[str, int]:
    """Summarize sector distribution for logging."""
    sectors: dict[str, int] = {}
    for meta in symbols.values():
        s = meta.get("sector_id", "UNCATEGORIZED")
        sectors[s] = sectors.get(s, 0) + 1
    return dict(sorted(sectors.items(), key=lambda x: -x[1]))
