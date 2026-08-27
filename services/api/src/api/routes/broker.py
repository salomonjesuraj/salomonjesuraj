"""Read-only broker routes -- "Broker Sync & Active Position
Intelligence" master sprint (2026-08-27).

STRICT ARCHITECTURAL RULE: every route below is a GET. There is no
POST/PUT/DELETE route in this file, and none is planned -- trade
execution stays 100% manual, on the broker's own native platform. See
api/broker_sync.py's own module docstring for the full disclosure on
what's verified vs. defensively-coded in the underlying Upstox calls.
"""

from __future__ import annotations

from aiohttp import web

from api.broker_sync import fetch_holdings, fetch_orders, fetch_positions_with_intelligence

routes = web.RouteTableDef()


@routes.get("/api/broker/positions")
async def get_broker_positions(request: web.Request) -> web.Response:
    """Live active intraday + overnight F&O/equity positions, each
    carrying a real-time Position Decision & Horizon Engine read
    (DTE/theta, structural targets/invalidation, holding-horizon tag) --
    see broker_sync.fetch_positions_with_intelligence."""
    redis = request.app["redis"]
    result = await fetch_positions_with_intelligence(redis)
    return web.json_response(result)


@routes.get("/api/broker/holdings")
async def get_broker_holdings(request: web.Request) -> web.Response:
    """Long-term delivery equity holdings, straight from Upstox."""
    redis = request.app["redis"]
    result = await fetch_holdings(redis)
    return web.json_response(result)


@routes.get("/api/broker/orders")
async def get_broker_orders(request: web.Request) -> web.Response:
    """Today's real order book -- Upstox's own status strings passed
    through as-is, not remapped into an invented taxonomy."""
    redis = request.app["redis"]
    result = await fetch_orders(redis)
    return web.json_response(result)
