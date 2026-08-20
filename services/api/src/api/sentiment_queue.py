"""EBIE EB-7 (increment 3) -- sentiment cache sweep: recomputes each
covered symbol's live sentiment_impact summary (real decay applied
against `now()`, not a stale baked-in value) and caches it into Redis
for scanner/dashboard to read cheaply, same "api computes + caches,
consumers do a cheap Redis read" pattern as VIX multiplier/Kelly
sizing/F&O ban.

Only sweeps symbols with real classified news in the recency window
(a plain SELECT DISTINCT against sentiment_scores/news_events) rather
than looping the full 208-symbol universe every cycle -- most symbols
have no news at all on a given day (confirmed: 88/208 in EB-7 increment
1's own live verification), so this is real work only where there's
real data.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import structlog

from api.sentiment import summarize_symbol_sentiment, RECENCY_WINDOW_MS

logger = structlog.get_logger()

SWEEP_INTERVAL_SEC = 60
STATUS_KEY = "infusion:sentiment-queue:status"
CACHE_PREFIX = "infusion:sentiment:"
CACHE_TTL_SEC = 10 * 60   # a few sweep intervals of grace, same order as other queue caches
MAX_ARTICLES_PER_SYMBOL = 50


async def _covered_symbols(pool, cutoff_ms: int) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ss.symbol
            FROM sentiment_scores ss
            JOIN news_events ne ON ne.id = ss.news_event_id
            WHERE ne.published_time_ms IS NULL OR ne.published_time_ms >= $1
            """,
            cutoff_ms,
        )
    return [r["symbol"] for r in rows]


async def _symbol_rows(pool, symbol: str, cutoff_ms: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ss.direction, ss.confidence, ss.severity, ss.relevance,
                   ss.novelty, ss.source_quality, ss.event_type,
                   ne.heading, ne.published_time_ms
            FROM sentiment_scores ss
            JOIN news_events ne ON ne.id = ss.news_event_id
            WHERE ss.symbol = $1 AND (ne.published_time_ms IS NULL OR ne.published_time_ms >= $2)
            ORDER BY ne.published_time_ms DESC NULLS LAST
            LIMIT $3
            """,
            symbol, cutoff_ms, MAX_ARTICLES_PER_SYMBOL,
        )
    return [dict(r) for r in rows]


async def sweep_once(app) -> dict:
    redis = app.get("redis")
    pool = app.get("pg_pool")
    if not redis or not pool:
        return {"available": False, "reason": "Redis or Postgres pool not available."}

    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - RECENCY_WINDOW_MS

    symbols = await _covered_symbols(pool, cutoff_ms)
    updated = 0
    for symbol in symbols:
        rows = await _symbol_rows(pool, symbol, cutoff_ms)
        summary = summarize_symbol_sentiment(rows, now_ms)
        await redis.set(f"{CACHE_PREFIX}{symbol}", json.dumps(summary, separators=(",", ":")), ex=CACHE_TTL_SEC)
        updated += 1

    status = {
        "available": True,
        "symbols_covered": updated,
        "checked_at": now_ms,
    }
    await redis.set(STATUS_KEY, json.dumps(status, separators=(",", ":")), ex=600)
    return status


async def sentiment_cache_loop(app) -> None:
    redis = app.get("redis")
    pool = app.get("pg_pool")
    if not redis or not pool:
        logger.info("sentiment_cache_queue_skipped", reason="redis_or_pg_pool_unavailable")
        return
    logger.info("sentiment_cache_queue_started", interval=SWEEP_INTERVAL_SEC)
    while True:
        with contextlib.suppress(Exception):
            status = await sweep_once(app)
            logger.info("sentiment_cache_sweep", **{k: v for k, v in status.items() if k != "checked_at"})
        await asyncio.sleep(SWEEP_INTERVAL_SEC)
