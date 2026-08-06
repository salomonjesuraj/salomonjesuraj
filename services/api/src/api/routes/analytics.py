"""Analytics API routes — signal precision, sector, session, regime stats.

Endpoints:
  GET /api/analytics/precision          - overall signal precision
  GET /api/analytics/precision/grade    - precision by conviction grade
  GET /api/analytics/precision/sector   - precision by sector
  GET /api/analytics/precision/session  - precision by session hour
  GET /api/analytics/precision/regime   - precision by market regime
  GET /api/analytics/suppression        - suppression effectiveness stats
  GET /api/analytics/outcomes           - recent signal outcomes
  GET /api/analytics/recap              - daily recap data (JSON)

Optional query param: date=YYYY-MM-DD (defaults to today)

All responses are deterministic JSON — no AI interpretation.
"""

from datetime import date, datetime

from aiohttp import web

routes = web.RouteTableDef()


def _parse_date(request) -> date | None:
    """Extract optional date query param."""
    raw = request.query.get("date")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


@routes.get("/api/analytics/precision")
async def analytics_precision(request):
    analytics = request.app["analytics"]
    td = _parse_date(request)
    data = await analytics.precision(td)
    return web.json_response(data)


@routes.get("/api/analytics/precision/grade")
async def analytics_by_grade(request):
    analytics = request.app["analytics"]
    td = _parse_date(request)
    data = await analytics.precision_by_grade(td)
    return web.json_response(data)


@routes.get("/api/analytics/precision/sector")
async def analytics_by_sector(request):
    analytics = request.app["analytics"]
    td = _parse_date(request)
    data = await analytics.precision_by_sector(td)
    return web.json_response(data)


@routes.get("/api/analytics/precision/session")
async def analytics_by_session(request):
    analytics = request.app["analytics"]
    td = _parse_date(request)
    data = await analytics.precision_by_session(td)
    return web.json_response(data)


@routes.get("/api/analytics/precision/regime")
async def analytics_by_regime(request):
    analytics = request.app["analytics"]
    td = _parse_date(request)
    data = await analytics.precision_by_regime(td)
    return web.json_response(data)


@routes.get("/api/analytics/suppression")
async def analytics_suppression(request):
    analytics = request.app["analytics"]
    td = _parse_date(request)
    data = await analytics.suppression_stats(td)
    return web.json_response(data)


@routes.get("/api/analytics/outcomes")
async def analytics_outcomes(request):
    analytics = request.app["analytics"]
    limit = int(request.query.get("limit", "20"))
    limit = min(max(limit, 1), 100)  # bounded 1–100
    data = await analytics.recent_outcomes(limit)
    return web.json_response(data)


@routes.get("/api/analytics/recap")
async def analytics_recap(request):
    analytics = request.app["analytics"]
    td = _parse_date(request)
    data = await analytics.daily_recap_data(td)
    return web.json_response(data)
