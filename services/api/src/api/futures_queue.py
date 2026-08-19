"""Futures positioning sweep loop — EBIE EB-4.

Same shape as api/radar_alert_queue.py/option_chain_queue.py/mtf_queue.py:
an in-process asyncio task inside `api`, sweeping on a fixed interval,
reading Redis for instrument resolution and writing a per-symbol Redis
hash other routes/rows can read cheaply. See api/futures.py's own
module docstring for the real Upstox API surfaces this is built on and
the honest scope disclosure (basis + single-sweep dOI only, fuller
multi-window OI velocity deferred).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import aiohttp
import msgpack
import structlog

from api.futures import (
    FUTURES_STATE_PREFIX,
    compute_basis,
    compute_oi_delta,
    current_month_contract,
    fetch_futures_master,
    fetch_quotes,
)
from api.routes.market import _upstox_access_token

logger = structlog.get_logger()

SWEEP_INTERVAL_SEC = 60
STATUS_KEY = "infusion:futures-queue:status"
STATE_TTL_SEC = 300   # a few sweep intervals of grace, same order as other queues' caches


async def _load_underlyings(redis) -> dict[str, str]:
    """instrument_key (NSE_EQ|...) -> symbol, from the live universe."""
    all_symbols = await redis.hgetall("infusion:symbols")
    result: dict[str, str] = {}
    for inst_key_raw, meta_raw in all_symbols.items():
        inst_key = inst_key_raw.decode() if isinstance(inst_key_raw, bytes) else inst_key_raw
        try:
            meta = msgpack.unpackb(meta_raw, raw=False) if isinstance(meta_raw, bytes) else meta_raw
            symbol = meta.get("symbol", "")
        except Exception:
            continue
        if symbol:
            result[inst_key] = symbol
    return result


async def sweep_once(app) -> dict:
    redis = app.get("redis")
    if not redis:
        return {"available": False, "reason": "Redis not available."}

    access_token = await _upstox_access_token(redis)
    if not access_token:
        return {"available": False, "reason": "Upstox auth token missing."}

    underlyings = await _load_underlyings(redis)
    if not underlyings:
        return {"available": False, "reason": "No symbols loaded yet."}

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    resolved = 0
    unresolved = 0
    quoted = 0
    async with aiohttp.ClientSession() as session:
        master = await fetch_futures_master(session, redis)
        if not master:
            return {"available": False, "reason": "Futures instrument master unavailable."}

        symbol_to_contract: dict[str, dict] = {}
        contract_keys: list[str] = []
        for underlying_key, symbol in underlyings.items():
            contracts = master.get(underlying_key)
            contract = current_month_contract(contracts) if contracts else None
            if contract and contract.get("instrument_key"):
                symbol_to_contract[symbol] = contract
                contract_keys.append(contract["instrument_key"])
                resolved += 1
            else:
                unresolved += 1

        quotes = await fetch_quotes(session, headers, contract_keys) if contract_keys else {}

    pipe = redis.pipeline(transaction=False)
    now = int(time.time())
    for symbol, contract in symbol_to_contract.items():
        instrument_key = contract["instrument_key"]
        # Upstox's Full Market Quote response keys its `data` dict by a
        # "EXCHANGE:TRADING_SYMBOL"-style composite, not the raw
        # instrument_key -- match defensively on either the instrument_key
        # itself or the trading_symbol substring rather than assume one
        # exact key format, since this hasn't been exercised against a
        # real response shape until this sweep's own first live run.
        quote = quotes.get(instrument_key)
        if quote is None:
            for k, v in quotes.items():
                if instrument_key in str(v.get("instrument_token", "")) or contract.get("trading_symbol", "") in k:
                    quote = v
                    break
        if quote is None:
            continue
        quoted += 1

        futures_ltp = float(quote.get("last_price") or 0)
        oi = int(quote.get("oi") or 0)
        volume = int(quote.get("volume") or 0)

        state_key = f"{FUTURES_STATE_PREFIX}{symbol}"
        prev_raw = await redis.hgetall(state_key)
        prev_oi = int(prev_raw[b"oi"]) if prev_raw and b"oi" in prev_raw else None

        spot_raw = await redis.hget(f"infusion:tick:{symbol}", "ltp")
        spot_ltp = float(spot_raw) if spot_raw else 0.0

        basis = compute_basis(spot_ltp, futures_ltp)
        oi_delta = compute_oi_delta(oi, prev_oi)

        mapping = {
            "instrument_key": instrument_key,
            "trading_symbol": contract.get("trading_symbol") or "",
            "expiry": contract.get("expiry") or "",
            "futures_ltp": futures_ltp,
            "oi": oi,
            "volume": volume,
            "updated_at": now,
            **{k: ("" if v is None else v) for k, v in basis.items()},
            **{k: ("" if v is None else v) for k, v in oi_delta.items()},
        }
        pipe.hset(state_key, mapping=mapping)
        pipe.expire(state_key, STATE_TTL_SEC)
    await pipe.execute()

    status = {
        "available": True,
        "underlyings": len(underlyings),
        "resolved": resolved,
        "unresolved": unresolved,
        "quoted": quoted,
        "checked_at": now,
    }
    await redis.set(STATUS_KEY, json.dumps(status), ex=600)
    return status


async def futures_queue_loop(app) -> None:
    redis = app.get("redis")
    if not redis:
        logger.info("futures_queue_skipped", reason="redis_unavailable")
        return
    logger.info("futures_queue_started", interval=SWEEP_INTERVAL_SEC)
    while True:
        with contextlib.suppress(Exception):
            status = await sweep_once(app)
            logger.info("futures_sweep", **{k: v for k, v in status.items() if k != "checked_at"})
        await asyncio.sleep(SWEEP_INTERVAL_SEC)
