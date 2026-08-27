"""Health & diagnostics routes — service health and pipeline visibility."""

from typing import Any

import msgpack
from aiohttp import web

routes = web.RouteTableDef()
Payload = dict[str, Any]


@routes.get("/api/health")
async def health(request: web.Request) -> web.Response:
    """Aggregated health check across all services."""
    redis = request.app["redis"]

    # "Terminal Edge & Analyst" sprint (2026-08-27): added "scanner" --
    # it already reports its own real heartbeat via the same
    # HealthReporter every other service here uses (infusion_common.
    # health), this endpoint just never checked it. The Admin Terminal's
    # "Signal & Scoring Engine" status needs a real signal for the
    # service that actually runs conviction scoring, not a guess.
    services = ["ingestion", "normalizer", "feature-engine", "ws-gateway", "api", "scanner"]
    result: dict[str, Payload] = {}

    for svc in services:
        raw = await redis.get(f"infusion:health:{svc}")
        if raw:
            try:
                result[svc] = msgpack.unpackb(raw, raw=False)
            except Exception:
                result[svc] = {"status": "healthy"}
        else:
            result[svc] = {"status": "unhealthy", "reason": "no heartbeat"}

    all_healthy = all(s.get("status") == "healthy" for s in result.values())

    return web.json_response(
        {
            "status": "healthy" if all_healthy else "degraded",
            "services": result,
        }
    )


@routes.get("/api/diagnostics")
async def diagnostics(request: web.Request) -> web.Response:
    """Pipeline diagnostics — identify failing components instantly."""
    redis = request.app["redis"]

    pipe = redis.pipeline()

    # Symbol universe
    pipe.hlen("infusion:symbols")

    # Stream depths
    pipe.xlen("infusion:stream:tick:raw")
    pipe.xlen("infusion:stream:tick:normalized")
    pipe.xlen("infusion:stream:feature:computed")
    pipe.xlen("infusion:stream:scan:signals")
    pipe.xlen("infusion:stream:scan:suppressed")

    # Active signals count
    pipe.zcard("infusion:signals:active")

    results = await pipe.execute()

    symbols_loaded = results[0]
    tick_raw_depth = results[1]
    tick_norm_depth = results[2]
    feature_depth = results[3]
    signal_stream_depth = results[4]
    suppressed_depth = results[5]
    active_signals = results[6]

    # Count tick hot keys (SCAN for infusion:tick:*)
    tick_key_count = 0
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match="infusion:tick:*", count=500)
        tick_key_count += len(keys)
        if cursor == 0:
            break

    # Count sector keys
    sector_count = 0
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match="infusion:sector:*", count=100)
        sector_count += len(keys)
        if cursor == 0:
            break

    # Count pre-breakout keys
    prebreak_count = 0
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match="infusion:prebreak:*", count=100)
        prebreak_count += len(keys)
        if cursor == 0:
            break

    # WS gateway health (clients count)
    ws_raw = await redis.get("infusion:health:ws-gateway")
    ws_clients = 0
    if ws_raw:
        try:
            ws_info = msgpack.unpackb(ws_raw, raw=False)
            ws_clients = ws_info.get("details", {}).get("clients", 0)
        except Exception:
            pass

    return web.json_response(
        {
            "symbols_loaded": symbols_loaded,
            "tick_keys": tick_key_count,
            "sectors_loaded": sector_count,
            "active_signals": active_signals,
            "prebreak_count": prebreak_count,
            "websocket_clients": ws_clients,
            "streams": {
                "tick_raw": tick_raw_depth,
                "tick_normalized": tick_norm_depth,
                "feature_computed": feature_depth,
                "scan_signals": signal_stream_depth,
                "scan_suppressed": suppressed_depth,
            },
        }
    )
