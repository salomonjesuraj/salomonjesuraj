"""Futures positioning routes — EBIE EB-4.

Read-only views into api/futures_queue.py's sweep loop, same "writes
happen only in the sweep loop" convention as radar_alerts.py.
"""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

routes = web.RouteTableDef()
Payload = dict[str, Any]


@routes.get("/api/futures/status")
async def futures_status(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    raw = await redis.get("infusion:futures-queue:status")
    if not raw:
        return web.json_response({"available": False, "reason": "No sweep has completed yet."})
    text = raw.decode() if isinstance(raw, bytes) else raw
    return web.json_response(json.loads(text))


@routes.get("/api/futures/{symbol}")
async def futures_for_symbol(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    symbol = request.match_info["symbol"].upper()
    data = await redis.hgetall(f"infusion:futures:{symbol}")
    if not data:
        return web.json_response(
            {"available": False, "reason": f"No futures data for {symbol} yet."}, status=404
        )
    result: Payload = {"symbol": symbol, "available": True}
    for k, v in data.items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        if val == "":
            result[key] = None
            continue
        try:
            result[str(key)] = (
                float(val) if "." in val or key in ("basis_pct", "oi_change_pct") else int(val)
            )
        except (ValueError, TypeError):
            result[str(key)] = val
    return web.json_response(result)
