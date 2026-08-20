"""EBIE EB-13 -- shadow validation report route.

Read-only view over api/shadow_validation.py's compute function --
real-time computed on request (not swept/cached), since this report is
checked occasionally (weekly review per Q5.3), not polled continuously.
"""

from __future__ import annotations

from aiohttp import web

from api.shadow_validation import compute_shadow_validation_report

routes = web.RouteTableDef()


@routes.get("/api/ebie/shadow-validation")
async def ebie_shadow_validation(request):
    """GET /api/ebie/shadow-validation?days=90 -- Gate A/Gate B status,
    EBIE-verdict-vs-baseline precision comparison, false-break rate, and
    calibration status, all from real archived data. See
    api/shadow_validation.py's own module docstring for the full
    methodology and the authorized promotion criteria this answers."""
    pool = request.app.get("pg_pool")
    redis = request.app.get("redis")
    days = request.query.get("days", "90")
    result = await compute_shadow_validation_report(pool, redis, days=int(days) if days else 90)
    return web.json_response(result)
