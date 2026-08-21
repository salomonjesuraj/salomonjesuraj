"""Stock event-calendar routes for option safety gates."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from aiohttp import web

from api.event_calendar import EVENT_KEY_PREFIX, get_event_risk

routes = web.RouteTableDef()
IST = timezone(timedelta(hours=5, minutes=30))


@routes.get("/api/events/stock")
async def stock_event(request):
    redis = request.app["redis"]
    symbol = str(request.query.get("symbol") or "").upper().strip()
    return web.json_response(await get_event_risk(redis, symbol))


@routes.get("/api/events/stocks")
async def stock_events(request):
    redis = request.app["redis"]
    limit = int(request.query.get("limit", "300") or 300)
    limit = max(1, min(limit, 1000))
    cursor = 0
    rows: list[dict] = []
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match=f"{EVENT_KEY_PREFIX}*", count=200)
        for key in keys:
            if len(rows) >= limit:
                break
            raw = await redis.get(key)
            if not raw:
                continue
            try:
                text = raw.decode() if isinstance(raw, bytes) else raw
                item = json.loads(text)
                symbol = str(item.get("symbol") or "").upper().strip()
                if symbol:
                    rows.append(await get_event_risk(redis, symbol))
            except Exception:
                continue
        if cursor == 0 or len(rows) >= limit:
            break
    rows.sort(
        key=lambda x: (str(x.get("next_event_date") or "9999-99-99"), str(x.get("symbol") or ""))
    )
    return web.json_response({"ok": True, "count": len(rows), "events": rows})


@routes.post("/api/events/stock")
async def save_stock_event(request):
    redis = request.app["redis"]
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    symbol = str(payload.get("symbol") or "").upper().strip()
    if not symbol:
        return web.json_response({"ok": False, "error": "symbol_required"}, status=400)
    event_date = str(payload.get("next_event_date") or payload.get("event_date") or "")[:10]
    event_type = str(payload.get("event_type") or "EVENT").upper().strip()
    if not event_date or not event_type:
        return web.json_response({"ok": False, "error": "event_date_and_type_required"}, status=400)
    data = {
        "symbol": symbol,
        "next_event_date": event_date,
        "event_type": event_type,
        "source": str(payload.get("source") or "dashboard_manual"),
        "note": str(payload.get("note") or ""),
        "updated_at_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
    }
    await redis.set(
        f"{EVENT_KEY_PREFIX}{symbol}", json.dumps(data, separators=(",", ":")), ex=86400 * 370
    )
    return web.json_response({"ok": True, "event": await get_event_risk(redis, symbol)})


@routes.delete("/api/events/stock/{symbol}")
async def delete_stock_event(request):
    redis = request.app["redis"]
    symbol = str(request.match_info.get("symbol") or "").upper().strip()
    if not symbol:
        return web.json_response({"ok": False, "error": "symbol_required"}, status=400)
    removed = await redis.delete(f"{EVENT_KEY_PREFIX}{symbol}")
    return web.json_response({"ok": True, "symbol": symbol, "removed": bool(removed)})
