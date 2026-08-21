"""Phase R9 -- dashboard-only early alerts: recent list + precision stats.

Endpoints:
  GET /api/radar-alerts/recent   - most recently fired alerts (for the
                                    dashboard's own toast/chime diff --
                                    same pattern signal-alert-v2.js
                                    already uses against /api/signals)
  GET /api/radar-alerts/status   - the sweep loop's own last-run status
                                    (api/radar_alert_queue.py)
  GET /api/radar-alerts/stats    - the "did an early alert tend to pay
                                    off" precision view: GRADUATED vs
                                    FADED vs EXPIRED vs still-PENDING,
                                    overall and by tier_at_fire, over a
                                    real trailing window.

Writes happen only in radar_alert_queue.py's sweep loop -- these routes
are read-only.
"""

from __future__ import annotations

import json

from aiohttp import web

routes = web.RouteTableDef()


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("fired_at", "best_tier_reached_at", "resolved_at", "last_checked_at"):
        if d.get(key) is not None:
            d[key] = d[key].isoformat()
    for key in ("score_at_fire", "rel_vol_at_fire", "ltp_at_fire"):
        if d.get(key) is not None:
            d[key] = float(d[key])
    return d


@routes.get("/api/radar-alerts/recent")
async def radar_alerts_recent(request):
    pool = request.app.get("pg_pool")
    if not pool:
        return web.json_response({"available": False, "alerts": []})
    limit = min(max(int(request.query.get("limit", "30") or 30), 1), 100)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM radar_alerts ORDER BY fired_at DESC LIMIT $1",
            limit,
        )
    return web.json_response({"available": True, "alerts": [_row_to_dict(r) for r in rows]})


@routes.get("/api/radar-alerts/status")
async def radar_alerts_status(request):
    redis = request.app.get("redis")
    if not redis:
        return web.json_response({"available": False})
    raw = await redis.get("infusion:radar-alert-queue:status")
    if not raw:
        return web.json_response({"available": False, "reason": "Sweep hasn't run yet."})
    payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    return web.json_response(payload)


@routes.get("/api/radar-alerts/stats")
async def radar_alerts_stats(request):
    """Precision view over the last `days` days (default 7): how often a
    dashboard-only early alert actually graduated into real breakout
    evidence vs faded back vs expired unresolved -- the concrete answer
    to "did the early notice tend to pay off", broken down overall and
    by the tier it fired at.
    """
    pool = request.app.get("pg_pool")
    if not pool:
        return web.json_response({"available": False, "reason": "Postgres not available."})
    days = min(max(int(request.query.get("days", "7") or 7), 1), 90)

    async with pool.acquire() as conn:
        overall = await conn.fetch(
            """
            SELECT outcome_label, count(*) AS n
            FROM radar_alerts
            WHERE fired_at >= now() - ($1::int * interval '1 day')
            GROUP BY outcome_label
            """,
            days,
        )
        by_tier = await conn.fetch(
            """
            SELECT tier_at_fire, outcome_label, count(*) AS n
            FROM radar_alerts
            WHERE fired_at >= now() - ($1::int * interval '1 day')
            GROUP BY tier_at_fire, outcome_label
            """,
            days,
        )

    overall_counts = {r["outcome_label"]: int(r["n"]) for r in overall}
    total = sum(overall_counts.values())
    resolved = total - overall_counts.get("PENDING", 0)
    graduated = overall_counts.get("GRADUATED", 0)
    graduation_rate_pct = round(100.0 * graduated / resolved, 1) if resolved > 0 else None

    tier_breakdown: dict[str, dict[str, int]] = {}
    for r in by_tier:
        tier_breakdown.setdefault(r["tier_at_fire"], {})[r["outcome_label"]] = int(r["n"])

    return web.json_response(
        {
            "available": True,
            "days": days,
            "total": total,
            "resolved": resolved,
            "graduation_rate_pct": graduation_rate_pct,
            "counts": {
                "pending": overall_counts.get("PENDING", 0),
                "graduated": graduated,
                "faded": overall_counts.get("FADED", 0),
                "expired": overall_counts.get("EXPIRED", 0),
            },
            "by_tier_at_fire": tier_breakdown,
        }
    )
