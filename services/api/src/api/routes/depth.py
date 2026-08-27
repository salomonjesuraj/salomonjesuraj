"""Order-book depth routes -- Level 2 DOM ladder ("Terminal Edge" sprint,
2026-08-27).

Read-only view into feature-engine's own per-tick 5-level depth capture
(FeatureVectorV1.depth_levels, populated from upstox_codec.py's real
MarketLevel depth-codec -- see feature_engine/engine.py and main.py's
own dedicated infusion:depth:{symbol} write, kept separate from the
generic infusion:feature:{symbol} hash since that hash's per-field
str(value) serialization can't round-trip a nested list of dicts back
into JSON). Same "writes happen only in the producing service" read-
only convention as radar_alerts.py/options_dynamics.py/portfolio_risk.py.
"""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

routes = web.RouteTableDef()
Payload = dict[str, Any]


@routes.get("/api/market/depth/{symbol}")
async def get_depth(request: web.Request) -> web.Response:
    """GET /api/market/depth/{symbol} -- up to 5 real bid/ask levels.

    `available: false` (never a fabricated empty ladder) when the key
    has expired -- either the feed genuinely has no depth for this
    symbol, or the 10s TTL lapsed because feature-engine stopped
    ticking it (market closed, symbol not currently subscribed, etc.).
    """
    symbol = request.match_info["symbol"].upper()
    redis = request.app["redis"]

    raw = await redis.get(f"infusion:depth:{symbol}")
    if not raw:
        return web.json_response(
            {
                "available": False,
                "symbol": symbol,
                "reason": f"No recent depth tick for {symbol} -- feed may be stale or this symbol isn't currently subscribed.",
            }
        )

    try:
        payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except (json.JSONDecodeError, TypeError):
        return web.json_response(
            {"available": False, "symbol": symbol, "reason": "Stored depth record is corrupt."}
        )

    payload = payload if isinstance(payload, dict) else {}
    payload["available"] = True
    return web.json_response(payload)
