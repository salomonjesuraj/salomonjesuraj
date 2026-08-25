"""GET /api/trade-blueprint/{symbol} -- the unified TradeBlueprint
payload. See api/trade_blueprint.py's own module docstring for exactly
which field comes from which existing source; this route is a thin
HTTP wrapper, no business logic of its own.
"""

from __future__ import annotations

from aiohttp import web

from api.trade_blueprint import build_trade_blueprint

routes = web.RouteTableDef()


@routes.get("/api/trade-blueprint/{symbol}")
async def get_trade_blueprint(request: web.Request) -> web.Response:
    redis = request.app.get("redis")
    if not redis:
        return web.json_response({"available": False, "reason": "Redis not available."})
    symbol = request.match_info["symbol"].upper()
    blueprint = await build_trade_blueprint(redis, symbol)
    return web.json_response(blueprint.model_dump())
