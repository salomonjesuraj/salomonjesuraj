"""EBIE EB-7 (increment 1) -- News sweep loop: batches the live 208-
symbol universe into <=30-key Upstox News API requests (per
docs/EBIE-IMPLEMENTATION-ANSWERS.md Q7.2's authorized design -- ~7
requests/sweep, 60s cadence, well inside Upstox's 50 req/sec limit),
dedupes against news_events, persists new (symbol, article) pairs.

Same in-process asyncio-loop-inside-api shape as every other queue in
this service (option_chain_queue_loop/futures_queue_loop/
radar_alert_loop) -- needs both Redis (universe + access token) and the
Postgres pool (durable article store), both already sitting on `app`.

Redis is used only as a short-TTL read cache for the dashboard/API layer
(latest headlines per symbol, and a sweep-status blob) -- per Q4.3's
storage policy, news_events themselves are Postgres-durable, Redis is
never the source of truth here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import aiohttp
import msgpack
import structlog

from api.news_ingestion import chunk_instrument_keys, fetch_news_batch, map_articles_to_events
from api.routes.market import _upstox_access_token

logger = structlog.get_logger()

SWEEP_INTERVAL_SEC = 60
STATUS_KEY = "infusion:news-queue:status"
HEADLINES_CACHE_PREFIX = "infusion:news:"
HEADLINES_CACHE_TTL_SEC = 3 * 3600   # a few sweep intervals of grace, same order as other queue caches
HEADLINES_CACHE_MAX = 10             # most recent N headlines per symbol, dashboard-read-speed only


async def _load_universe(redis) -> dict[str, str]:
    """instrument_key (NSE_EQ|...) -> symbol, from the live universe --
    same shape/source as futures_queue.py's _load_underlyings()."""
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


async def _persist_events(pool, events: list[dict]) -> int:
    """Insert new (symbol, article) rows, ON CONFLICT DO NOTHING on the
    (symbol, article_fingerprint) unique constraint -- the actual dedupe
    mechanism. Returns how many rows were genuinely new this sweep."""
    if not events:
        return 0
    inserted = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for ev in events:
                result = await conn.execute(
                    """
                    INSERT INTO news_events
                        (symbol, instrument_key, article_fingerprint, heading,
                         summary, article_link, thumbnail, published_time_ms)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (symbol, article_fingerprint) DO NOTHING
                    """,
                    ev["symbol"], ev["instrument_key"], ev["article_fingerprint"],
                    ev["heading"], ev["summary"], ev["article_link"], ev["thumbnail"],
                    ev["published_time_ms"],
                )
                # asyncpg execute() returns e.g. "INSERT 0 1" (inserted) or
                # "INSERT 0 0" (conflict, skipped) -- the trailing count is
                # the real signal, not "did the statement run".
                if result.endswith(" 1"):
                    inserted += 1
    return inserted


async def _update_headline_cache(redis, events: list[dict]) -> None:
    """Best-effort, short-TTL per-symbol headline cache for cheap
    dashboard reads -- never the source of truth (Postgres is), so a
    failure here doesn't lose data, just read-freshness."""
    if not events:
        return
    by_symbol: dict[str, list[dict]] = {}
    for ev in events:
        by_symbol.setdefault(ev["symbol"], []).append(ev)
    pipe = redis.pipeline(transaction=False)
    for symbol, new_articles in by_symbol.items():
        key = f"{HEADLINES_CACHE_PREFIX}{symbol}"
        try:
            existing_raw = await redis.get(key)
            existing = json.loads(existing_raw) if existing_raw else []
        except Exception:
            existing = []
        combined = new_articles + existing
        # de-dupe by fingerprint while preserving order, newest-first
        seen = set()
        deduped = []
        for a in combined:
            fp = a.get("article_fingerprint")
            if fp in seen:
                continue
            seen.add(fp)
            deduped.append(a)
        pipe.set(key, json.dumps(deduped[:HEADLINES_CACHE_MAX], separators=(",", ":")), ex=HEADLINES_CACHE_TTL_SEC)
    await pipe.execute()


async def sweep_once(app) -> dict:
    """One full-universe news sweep. Called every SWEEP_INTERVAL_SEC by
    news_queue_loop(); also directly callable (e.g. from a verify
    script) since it only needs `app`'s redis/pg_pool."""
    redis = app.get("redis")
    pool = app.get("pg_pool")
    if not redis or not pool:
        return {"available": False, "reason": "Redis or Postgres pool not available."}

    access_token = await _upstox_access_token(redis)
    if not access_token:
        return {"available": False, "reason": "Upstox access token not available."}

    inst_to_symbol = await _load_universe(redis)
    if not inst_to_symbol:
        return {"available": False, "reason": "Symbol universe not loaded yet."}

    instrument_keys = list(inst_to_symbol.keys())
    batches = chunk_instrument_keys(instrument_keys)

    started = time.time()
    total_articles_seen = 0
    total_new = 0
    batches_failed = 0

    async with aiohttp.ClientSession() as session:
        for batch in batches:
            raw_data = await fetch_news_batch(session, access_token, batch)
            if not raw_data:
                batches_failed += 1
                continue
            events = map_articles_to_events(raw_data, inst_to_symbol)
            total_articles_seen += len(events)
            inserted = await _persist_events(pool, events)
            total_new += inserted
            await _update_headline_cache(redis, events)

    status = {
        "available": True,
        "universe_size": len(instrument_keys),
        "batches": len(batches),
        "batches_failed": batches_failed,
        "articles_seen": total_articles_seen,
        "articles_new": total_new,
        "duration_sec": round(time.time() - started, 2),
    }
    await redis.set(STATUS_KEY, json.dumps(status, separators=(",", ":")), ex=600)
    return status


async def news_queue_loop(app) -> None:
    redis = app.get("redis")
    pool = app.get("pg_pool")
    if not redis or not pool:
        logger.info("news_queue_skipped", reason="redis_or_pg_pool_unavailable")
        return
    logger.info("news_queue_started", interval=SWEEP_INTERVAL_SEC)
    while True:
        with contextlib.suppress(Exception):
            status = await sweep_once(app)
            logger.info("news_sweep", **status)
        await asyncio.sleep(SWEEP_INTERVAL_SEC)
