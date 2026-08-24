"""Upstox News API ingestion routes -- EBIE EB-7 (increment 1).

Deliberately a separate module/route prefix from the pre-existing
routes/news.py (a free GDELT-based headline+keyword-sentiment feed,
`/api/news/market`, unrelated and untouched by this work) -- EB-7's
authorized design is specifically Upstox's own News API as the source
(per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q7.2), persisted durably with
real dedup, feeding the future sentiment-engine service. Conflating the
two would blur a real, load-bearing distinction: GDELT's route is a
same-request live fetch with no persistence and a crude keyword
heuristic; this one is a background-swept, deduplicated, durably
archived pipeline that a real FinBERT classifier will consume next.

Endpoints:
  GET /api/upstox-news/status     - the sweep loop's own last-run status
                                     (api/news_queue.py)
  GET /api/upstox-news/{symbol}   - recent headlines for one symbol,
                                     Redis cache first (fast, dashboard-
                                     read-speed only), falling back to
                                     Postgres (the real source of truth)
                                     if the cache is cold.

Writes happen only in news_queue.py's sweep loop -- these routes are
read-only. No sentiment/classification fields yet (EB-7 increment 2).
"""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

routes = web.RouteTableDef()
Payload = dict[str, Any]


@routes.get("/api/upstox-news/status")
async def upstox_news_status(request: web.Request) -> web.Response:
    redis = request.app.get("redis")
    if not redis:
        return web.json_response({"available": False})
    raw = await redis.get("infusion:news-queue:status")
    if not raw:
        return web.json_response({"available": False, "reason": "No sweep has completed yet."})
    payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    return web.json_response(payload)


def _row_to_article(row: Any) -> Payload:
    return {
        "heading": row["heading"],
        "summary": row["summary"],
        "article_link": row["article_link"],
        "thumbnail": row["thumbnail"],
        "published_time_ms": row["published_time_ms"],
        "article_fingerprint": row["article_fingerprint"],
    }


@routes.get("/api/upstox-news/{symbol}")
async def upstox_news_for_symbol(request: web.Request) -> web.Response:
    symbol = request.match_info["symbol"].upper()
    limit = min(max(int(request.query.get("limit", "10") or 10), 1), 50)
    redis = request.app.get("redis")

    if redis:
        cached_raw = await redis.get(f"infusion:news:{symbol}")
        if cached_raw:
            try:
                cached = json.loads(
                    cached_raw.decode() if isinstance(cached_raw, bytes) else cached_raw
                )
                if cached:
                    return web.json_response(
                        {
                            "symbol": symbol,
                            "available": True,
                            "source": "cache",
                            "articles": cached[:limit],
                        }
                    )
            except Exception:
                pass

    pool = request.app.get("pg_pool")
    if not pool:
        return web.json_response(
            {
                "symbol": symbol,
                "available": False,
                "reason": "Postgres unavailable.",
                "articles": [],
            }
        )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT heading, summary, article_link, thumbnail, published_time_ms, article_fingerprint
            FROM news_events
            WHERE symbol = $1
            ORDER BY published_time_ms DESC NULLS LAST, first_seen_at DESC
            LIMIT $2
            """,
            symbol,
            limit,
        )
    if not rows:
        return web.json_response(
            {
                "symbol": symbol,
                "available": True,
                "source": "postgres",
                "articles": [],
                "reason": "No news captured for this symbol yet.",
            }
        )
    return web.json_response(
        {
            "symbol": symbol,
            "available": True,
            "source": "postgres",
            "articles": [_row_to_article(r) for r in rows],
        }
    )
