"""Futures positioning routes — EBIE EB-4.

Read-only views into api/futures_queue.py's sweep loop, same "writes
happen only in the sweep loop" convention as radar_alerts.py.
"""

from __future__ import annotations

import json

from aiohttp import web

routes = web.RouteTableDef()


@routes.get("/api/futures/status")
async def futures_status(request):
    redis = request.app["redis"]
    raw = await redis.get("infusion:futures-queue:status")
    if not raw:
        return web.json_response({"available": False, "reason": "No sweep has completed yet."})
    return web.json_response(json.loads(raw))


@routes.get("/api/futures/{symbol}")
async def futures_for_symbol(request):
    redis = request.app["redis"]
    symbol = request.match_info["symbol"].upper()
    data = await redis.hgetall(f"infusion:futures:{symbol}")
    if not data:
        return web.json_response(
            {"available": False, "reason": f"No futures data for {symbol} yet."}, status=404
        )
    result = {"symbol": symbol, "available": True}
    for k, v in data.items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        if val == "":
            result[key] = None
            continue
        try:
            result[key] = float(val) if "." in val or key in ("basis_pct", "oi_change_pct") else int(val)
        except (ValueError, TypeError):
            result[key] = val
    return web.json_response(result)
