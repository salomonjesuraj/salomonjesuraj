"""Stock futures positioning — EBIE EB-4.

Genuinely new ground, confirmed before writing this: the live subscribed
universe (infusion:symbols) holds only NSE_EQ (spot) instrument keys --
zero NSE_FO keys anywhere (checked directly via Redis: `HKEYS
infusion:symbols | grep NSE_FO` returns nothing). No futures LTP/OI has
ever flowed through this pipeline. Per docs/EBIE-IMPLEMENTATION-
ANSWERS.md Q2.5: "Treat futures positioning as new infrastructure...
For each F&O underlying, maintain at least: spot instrument, current/
near stock-futures contract."

Architecture choice, and why: rather than adding new live WebSocket
subscriptions to ingestion (a bigger, riskier change touching the hot
tick path), this reuses the SAME REST-poll + Redis-cache pattern already
proven for options data (api/routes/market.py's
_fetch_full_option_chain, api/option_chain_queue.py) -- futures OI/basis
doesn't need tick-by-tick freshness the way price does, and a periodic
sweep is the lower-risk, faster-to-verify choice for a first increment.

Two real Upstox API surfaces verified live via their own documentation
before writing any code (not guessed, not hardcoded from memory):
  - Instruments master file: https://assets.upstox.com/market-quote/
    instruments/exchange/NSE.json.gz -- gzipped JSON, one record per
    tradeable NSE instrument. Futures contracts have segment="NSE_FO",
    instrument_type="FUT", and an underlying_key field that references
    the underlying's own NSE_EQ instrument_key -- the exact link needed
    to resolve "which futures contract belongs to this stock."
  - Full Market Quote API: GET https://api.upstox.com/v2/market-quote/
    quotes?instrument_key=<comma-separated, up to 500> -- returns OI,
    OHLC, volume, LTP for F&O instruments in one batched call. With 208
    F&O symbols, every sweep needs exactly ONE such call.

Scope disclosed honestly: this increment computes basis (immediate, no
history needed) and a single-sweep-interval dOI (this sweep's OI minus
last sweep's). The blueprint's fuller multi-window OI velocity/
acceleration (dOI_1m/5m/15m, d2OI/dt2) and rollover-window next-contract
tracking are deferred to a follow-up increment -- this ships the
foundation (real instrument resolution + real OI/basis data flowing for
the first time) rather than a fully-realized version of every field at
once.
"""

from __future__ import annotations

import gzip
import json
import time

import aiohttp
import structlog

logger = structlog.get_logger()

INSTRUMENTS_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
UPSTOX_QUOTE_URL = "https://api.upstox.com/v2/market-quote/quotes"
QUOTE_BATCH_SIZE = 500   # Upstox's own documented cap per Full Market Quote call

MASTER_CACHE_KEY = "infusion:futures:master"
MASTER_CACHE_TTL_SEC = 20 * 3600   # contract listings/rollovers don't change intraday
FUTURES_STATE_PREFIX = "infusion:futures:"   # + {symbol} -> HASH


async def fetch_futures_master(session: aiohttp.ClientSession, redis) -> dict[str, list[dict]]:
    """Download + parse the NSE instruments master file, filtered to
    futures contracts, grouped by underlying_key and sorted by expiry
    (nearest first). Cached in Redis (20h TTL) since this is a large
    file (the whole NSE derivatives+equity universe) that only changes
    with new contract listings, not intraday.
    """
    cached = await redis.get(MASTER_CACHE_KEY)
    if cached:
        try:
            return json.loads(cached.decode() if isinstance(cached, bytes) else cached)
        except Exception:
            pass

    async with session.get(INSTRUMENTS_MASTER_URL, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status != 200:
            logger.warning("futures_master_fetch_failed", status=resp.status)
            return {}
        raw = await resp.read()

    try:
        records = json.loads(gzip.decompress(raw))
    except Exception as exc:
        logger.warning("futures_master_parse_failed", error=str(exc))
        return {}

    by_underlying: dict[str, list[dict]] = {}
    for rec in records:
        if rec.get("segment") != "NSE_FO" or rec.get("instrument_type") != "FUT":
            continue
        underlying_key = rec.get("underlying_key") or ""
        if not underlying_key:
            continue
        by_underlying.setdefault(underlying_key, []).append({
            "instrument_key": rec.get("instrument_key"),
            "trading_symbol": rec.get("trading_symbol"),
            "expiry": rec.get("expiry"),
            "lot_size": rec.get("lot_size"),
        })
    for contracts in by_underlying.values():
        contracts.sort(key=lambda c: c.get("expiry") or "")

    try:
        await redis.setex(MASTER_CACHE_KEY, MASTER_CACHE_TTL_SEC, json.dumps(by_underlying))
    except Exception:
        pass

    logger.info("futures_master_loaded", underlyings=len(by_underlying), total_records=len(records))
    return by_underlying


def current_month_contract(contracts: list[dict]) -> dict | None:
    """Nearest, non-expired contract -- "current month" per the
    authorized subscription policy. Contracts list is already
    expiry-sorted ascending by fetch_futures_master(); the day-boundary
    handling (an already-expired contract still in the list on expiry
    day itself) is left to the exchange's own listing -- Upstox stops
    listing an expired contract the next trading day, which is precise
    enough for this use.
    """
    return contracts[0] if contracts else None


def compute_basis(spot_ltp: float, futures_ltp: float) -> dict:
    """basis = futures - spot, and basis% -- immediate, no history needed."""
    if spot_ltp <= 0 or futures_ltp <= 0:
        return {"basis": None, "basis_pct": None}
    basis = futures_ltp - spot_ltp
    return {"basis": round(basis, 2), "basis_pct": round(basis / spot_ltp * 100, 3)}


def compute_oi_delta(current_oi: int, prev_oi: int | None) -> dict:
    """Single-sweep-interval OI change. prev_oi=None (first sweep since
    restart, or a symbol seen for the first time) -> None, not a
    fabricated 0 that would misread as "no change."
    """
    if prev_oi is None:
        return {"oi_change": None, "oi_change_pct": None}
    delta = current_oi - prev_oi
    pct = (delta / prev_oi * 100) if prev_oi > 0 else None
    return {"oi_change": delta, "oi_change_pct": round(pct, 2) if pct is not None else None}


async def fetch_quotes(session: aiohttp.ClientSession, headers: dict, instrument_keys: list[str]) -> dict[str, dict]:
    """Batched Full Market Quote fetch, chunked to Upstox's documented
    500-key cap (208 F&O symbols today fits in one call, but this stays
    correct if the universe grows past 500).
    """
    result: dict[str, dict] = {}
    for i in range(0, len(instrument_keys), QUOTE_BATCH_SIZE):
        batch = instrument_keys[i:i + QUOTE_BATCH_SIZE]
        try:
            async with session.get(
                UPSTOX_QUOTE_URL,
                headers=headers,
                params={"instrument_key": ",".join(batch)},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if resp.status != 200 or data.get("status") != "success":
                    logger.warning("futures_quote_fetch_failed", status=resp.status, batch_size=len(batch))
                    continue
                result.update(data.get("data") or {})
        except Exception as exc:
            logger.warning("futures_quote_fetch_error", error=str(exc))
    return result
