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

import contextlib
import gzip
import json
import time
from typing import Any, cast

import aiohttp
import structlog
from infusion_models.oi_buildup import OIBuildupType

logger = structlog.get_logger()

# Mathematical audit fix (2026-08-25, §3.1): +/- band on BOTH price and OI
# change before either axis counts as genuinely "up" or "down" -- same
# "don't force a noisy reading into a hard classification" discipline as
# verdict_engine.py's MARKET_CONTEXT_NEUTRAL_BAND and options_analytics_v2.py's
# WALL_CHANGE_THRESHOLD. This is Infusion's own calibration, not a cited
# convention -- a single-sweep-interval (60s) price/OI move under this size
# is noise, not a real buildup/unwinding signal.
OI_BUILDUP_DEADBAND_PCT = 0.05

INSTRUMENTS_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
UPSTOX_QUOTE_URL = "https://api.upstox.com/v2/market-quote/quotes"
QUOTE_BATCH_SIZE = 500  # Upstox's own documented cap per Full Market Quote call

MASTER_CACHE_KEY = "infusion:futures:master"
MASTER_CACHE_TTL_SEC = 20 * 3600  # contract listings/rollovers don't change intraday
FUTURES_STATE_PREFIX = "infusion:futures:"  # + {symbol} -> HASH
Payload = dict[str, Any]


async def fetch_futures_master(
    session: aiohttp.ClientSession, redis: Any
) -> dict[str, list[Payload]]:
    """Download + parse the NSE instruments master file, filtered to
    futures contracts, grouped by underlying_key and sorted by expiry
    (nearest first). Cached in Redis (20h TTL) since this is a large
    file (the whole NSE derivatives+equity universe) that only changes
    with new contract listings, not intraday.
    """
    cached = await redis.get(MASTER_CACHE_KEY)
    if cached:
        try:
            cached_payload = json.loads(cached.decode() if isinstance(cached, bytes) else cached)
            return cast(dict[str, list[Payload]], cached_payload)
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
    if not isinstance(records, list):
        logger.warning("futures_master_parse_failed", error="records_not_list")
        return {}

    by_underlying: dict[str, list[Payload]] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if rec.get("segment") != "NSE_FO" or rec.get("instrument_type") != "FUT":
            continue
        underlying_key = rec.get("underlying_key") or ""
        if not underlying_key:
            continue
        by_underlying.setdefault(underlying_key, []).append(
            {
                "instrument_key": rec.get("instrument_key"),
                "trading_symbol": rec.get("trading_symbol"),
                "expiry": rec.get("expiry"),
                "lot_size": rec.get("lot_size"),
            }
        )
    for contracts in by_underlying.values():
        contracts.sort(key=lambda c: c.get("expiry") or "")

    with contextlib.suppress(Exception):
        await redis.setex(MASTER_CACHE_KEY, MASTER_CACHE_TTL_SEC, json.dumps(by_underlying))

    logger.info("futures_master_loaded", underlyings=len(by_underlying), total_records=len(records))
    return by_underlying


def current_month_contract(contracts: list[Payload]) -> Payload | None:
    """Nearest, non-expired contract -- "current month" per the
    authorized subscription policy. Contracts list is already
    expiry-sorted ascending by fetch_futures_master().

    Live-session bug found and fixed 2026-08-26 (day after Aug expiry):
    this used to just return contracts[0] on the documented assumption
    that "Upstox stops listing an expired contract the next trading
    day." That assumption is false -- verified live against the real
    instruments master and the real Full Market Quote endpoint the
    morning after expiry: the just-expired "BANKNIFTY FUT 25 AUG 26"
    contract was still contracts[0] in the cached master, and Upstox's
    quote endpoint correctly returned `{"data": {}}` for it (an expired
    contract simply isn't quotable), which meant fetch_quotes() got
    nothing for any of the 208 F&O underlyings and oi_buildup silently
    never populated for the entire session. Filtering out already-
    expired entries here, rather than trusting the master file to have
    dropped them, is the actual fix -- confirmed live that the next
    contract (BANKNIFTY FUT 29 SEP 26) returns real OI/LTP/volume.
    """
    now_ms = int(time.time() * 1000)
    live = [c for c in contracts if isinstance(c.get("expiry"), int | float) and c["expiry"] > now_ms]
    if live:
        return live[0]
    # Nothing unexpired in the cached list (e.g. a brand-new expiry day
    # before the master cache has refreshed) -- fall back to the
    # nearest entry rather than returning None, same as before.
    return contracts[0] if contracts else None


def compute_basis(spot_ltp: float, futures_ltp: float) -> Payload:
    """basis = futures - spot, and basis% -- immediate, no history needed."""
    if spot_ltp <= 0 or futures_ltp <= 0:
        return {"basis": None, "basis_pct": None}
    basis = futures_ltp - spot_ltp
    return {"basis": round(basis, 2), "basis_pct": round(basis / spot_ltp * 100, 3)}


def compute_oi_delta(current_oi: int, prev_oi: int | None) -> Payload:
    """Single-sweep-interval OI change. prev_oi=None (first sweep since
    restart, or a symbol seen for the first time) -> None, not a
    fabricated 0 that would misread as "no change."
    """
    if prev_oi is None:
        return {"oi_change": None, "oi_change_pct": None}
    delta = current_oi - prev_oi
    pct = (delta / prev_oi * 100) if prev_oi > 0 else None
    return {"oi_change": delta, "oi_change_pct": round(pct, 2) if pct is not None else None}


def compute_futures_price_change(current_ltp: float, prev_ltp: float | None) -> Payload:
    """Single-sweep-interval futures LTP change -- same shape and same
    "prev=None -> None, never a fabricated 0" rule as compute_oi_delta()
    above. Deliberately the FUTURES contract's own price move, not
    compute_basis()'s spot-vs-futures divergence -- classify_oi_buildup()
    below needs the classic definition (the instrument that actually
    carries the OI moving), which is a different question from basis."""
    if prev_ltp is None or prev_ltp <= 0 or current_ltp <= 0:
        return {"futures_price_change_pct": None}
    pct = (current_ltp - prev_ltp) / prev_ltp * 100
    return {"futures_price_change_pct": round(pct, 3)}


def classify_oi_buildup(
    price_change_pct: float | None, oi_change_pct: float | None
) -> OIBuildupType:
    """The classic 4-quadrant OI buildup matrix -- mathematical audit
    fix for §3.1 ("genuinely not implemented anywhere"). Both axes must
    clear OI_BUILDUP_DEADBAND_PCT before counting as directional; a miss
    on either axis (data not yet available, or a genuinely flat sweep)
    returns NEUTRAL rather than forcing a guess."""
    if price_change_pct is None or oi_change_pct is None:
        return OIBuildupType.NEUTRAL
    price_up = price_change_pct > OI_BUILDUP_DEADBAND_PCT
    price_down = price_change_pct < -OI_BUILDUP_DEADBAND_PCT
    oi_up = oi_change_pct > OI_BUILDUP_DEADBAND_PCT
    oi_down = oi_change_pct < -OI_BUILDUP_DEADBAND_PCT
    if price_up and oi_up:
        return OIBuildupType.LONG_BUILDUP
    if price_up and oi_down:
        return OIBuildupType.SHORT_COVERING
    if price_down and oi_up:
        return OIBuildupType.SHORT_BUILDUP
    if price_down and oi_down:
        return OIBuildupType.LONG_UNWINDING
    return OIBuildupType.NEUTRAL


async def fetch_quotes(
    session: aiohttp.ClientSession, headers: dict[str, str], instrument_keys: list[str]
) -> dict[str, Payload]:
    """Batched Full Market Quote fetch, chunked to Upstox's documented
    500-key cap (208 F&O symbols today fits in one call, but this stays
    correct if the universe grows past 500).
    """
    result: dict[str, Payload] = {}
    for i in range(0, len(instrument_keys), QUOTE_BATCH_SIZE):
        batch = instrument_keys[i : i + QUOTE_BATCH_SIZE]
        try:
            async with session.get(
                UPSTOX_QUOTE_URL,
                headers=headers,
                params={"instrument_key": ",".join(batch)},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if resp.status != 200 or data.get("status") != "success":
                    logger.warning(
                        "futures_quote_fetch_failed", status=resp.status, batch_size=len(batch)
                    )
                    continue
                quote_data = data.get("data") or {}
                if isinstance(quote_data, dict):
                    result.update(cast(dict[str, Payload], quote_data))
        except Exception as exc:
            logger.warning("futures_quote_fetch_error", error=str(exc))
    return result


async def compute_oi_buildup_map(redis: Any) -> dict[str, str]:
    """Sniper HUD Zone 3 (2026-08-27): every symbol's current
    OIBuildupType in one call, for the Smart Money Direction Radar's
    bull/bear filter -- a single SCAN + pipelined HMGET, not a fan-out
    of one request per symbol (the futures_queue sweep already wrote
    these into infusion:futures:{symbol} hashes; this only reads them).
    Symbols with no futures row yet (sweep hasn't reached them, or the
    hash's 300s TTL lapsed) are simply absent from the returned map --
    the caller treats "missing" the same as NEUTRAL, never a guess.
    """
    out: dict[str, str] = {}
    master_key = f"{FUTURES_STATE_PREFIX}master"
    cursor = 0
    keys: list[Any] = []
    while True:
        cursor, batch = await redis.scan(cursor, match=f"{FUTURES_STATE_PREFIX}*", count=500)
        keys.extend(k for k in batch if (k.decode() if isinstance(k, bytes) else k) != master_key)
        if cursor == 0:
            break
    if not keys:
        return out

    pipe = redis.pipeline(transaction=False)
    for key in keys:
        pipe.hget(key, "oi_buildup")
    values = await pipe.execute()

    prefix_len = len(FUTURES_STATE_PREFIX)
    for key, value in zip(keys, values, strict=True):
        if not value:
            continue
        key_str = key.decode() if isinstance(key, bytes) else key
        symbol = key_str[prefix_len:]
        out[symbol] = value.decode() if isinstance(value, bytes) else value
    return out
