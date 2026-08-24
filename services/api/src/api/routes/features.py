"""Features route — latest computed features per symbol from hot state."""

from typing import Any

from aiohttp import web

routes = web.RouteTableDef()
Payload = dict[str, Any]


@routes.get("/api/features/{symbol}")
async def get_features(request: web.Request) -> web.Response:
    """Get latest computed features for a symbol."""
    symbol = request.match_info["symbol"].upper()
    redis = request.app["redis"]

    data = await redis.hgetall(f"infusion:feature:{symbol}")
    if not data:
        return web.json_response({"error": f"No feature data for {symbol}"}, status=404)

    result: Payload = {"symbol": symbol}
    for k, v in data.items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        if val in {"True", "False"}:
            result[key] = val == "True"
            continue
        try:
            result[key] = float(val)
        except (ValueError, TypeError):
            result[key] = val

    return web.json_response(result)
