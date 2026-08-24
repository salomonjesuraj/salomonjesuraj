"""Live sentiment_impact routes -- EBIE EB-7 (increment 3).

Read-only views into api/sentiment_queue.py's sweep loop, same
"writes happen only in the sweep loop" convention as futures.py/
upstox_news.py. Deliberately a different route family/Redis key prefix
from the pre-existing routes/news.py (GDELT keyword-sentiment feed,
`infusion:news-edge:`) and from EB-7 increment 1's routes/upstox_news.py
(raw article ingestion, `infusion:news:`) -- this is EBIE's own,
FinBERT-classified, decay-weighted sentiment_impact view specifically.
"""

from __future__ import annotations

import json

from aiohttp import web

routes = web.RouteTableDef()


@routes.get("/api/sentiment/status")
async def sentiment_status(request: web.Request) -> web.Response:
    redis = request.app.get("redis")
    if not redis:
        return web.json_response({"available": False})
    raw = await redis.get("infusion:sentiment-queue:status")
    if not raw:
        return web.json_response({"available": False, "reason": "No sweep has completed yet."})
    return web.json_response(json.loads(raw.decode() if isinstance(raw, bytes) else raw))


@routes.get("/api/sentiment/{symbol}")
async def sentiment_for_symbol(request: web.Request) -> web.Response:
    symbol = request.match_info["symbol"].upper()
    redis = request.app.get("redis")
    if not redis:
        return web.json_response({"symbol": symbol, "available": False})
    raw = await redis.get(f"infusion:sentiment:{symbol}")
    if not raw:
        return web.json_response(
            {
                "symbol": symbol,
                "available": False,
                "reason": "No recent news classified for this symbol yet.",
            }
        )
    payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    return web.json_response({"symbol": symbol, **payload})
