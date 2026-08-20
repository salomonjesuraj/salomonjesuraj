"""Portfolio-risk routes -- EBIE EB-11 (increment 2).

Read-only views into api/portfolio_risk_queue.py's sweep loop, same
"writes happen only in the sweep loop" convention as futures.py/
upstox_news.py/sentiment.py.
"""

from __future__ import annotations

import json

from aiohttp import web

routes = web.RouteTableDef()


@routes.get("/api/portfolio-risk/status")
async def portfolio_risk_status(request):
    redis = request.app.get("redis")
    if not redis:
        return web.json_response({"available": False})
    raw = await redis.get("infusion:portfolio-risk-queue:status")
    if not raw:
        return web.json_response({"available": False, "reason": "No sweep has completed yet."})
    return web.json_response(json.loads(raw.decode() if isinstance(raw, bytes) else raw))


@routes.get("/api/portfolio-risk/daily-loss")
async def portfolio_risk_daily_loss(request):
    redis = request.app.get("redis")
    if not redis:
        return web.json_response({"available": False})
    raw = await redis.get("infusion:portfolio-risk:daily-loss")
    if not raw:
        return web.json_response({"available": False, "reason": "No sweep has completed yet."})
    return web.json_response(json.loads(raw.decode() if isinstance(raw, bytes) else raw))


@routes.get("/api/portfolio-risk/consecutive-losses")
async def portfolio_risk_consecutive_losses(request):
    redis = request.app.get("redis")
    if not redis:
        return web.json_response({"available": False})
    raw = await redis.get("infusion:portfolio-risk:consecutive-losses")
    if not raw:
        return web.json_response({"available": False, "reason": "No sweep has completed yet."})
    return web.json_response(json.loads(raw.decode() if isinstance(raw, bytes) else raw))
