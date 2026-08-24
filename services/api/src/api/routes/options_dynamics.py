"""Dynamic option-chain routes — EBIE EB-5.

Read-only views into api/options_dynamics_queue.py's sweep loop, same
"writes happen only in the sweep loop" convention as radar_alerts.py/
futures.py.
"""

from __future__ import annotations

import json

from aiohttp import web

routes = web.RouteTableDef()


@routes.get("/api/options-dynamics/status")
async def options_dynamics_status(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    raw = await redis.get("infusion:options-dynamics-queue:status")
    if not raw:
        return web.json_response({"available": False, "reason": "No sweep has completed yet."})
    return web.json_response(json.loads(raw))


@routes.get("/api/options-dynamics/{symbol}")
async def options_dynamics_for_symbol(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    symbol = request.match_info["symbol"].upper()
    raw = await redis.get(f"infusion:options-dynamics:{symbol}")
    if not raw:
        return web.json_response(
            {
                "available": False,
                "reason": f"No dynamic option data for {symbol} yet -- not in the current sweep candidate set, or its chain isn't ready.",
            },
            status=404,
        )
    return web.json_response(json.loads(raw))
