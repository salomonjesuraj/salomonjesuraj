"""EBIE EB-13/EB-14 -- shadow validation report + promotion review
infrastructure routes.

Read-only /api/ebie/shadow-validation is computed on request (not
swept/cached), since this report is checked occasionally (weekly
review per Q5.3), not polled continuously.

The EB-14 routes below are durable RECORD-KEEPING only -- none of them
change which model drives a live signal. POST /promotion-review
persists one weekly snapshot (called by the scheduler); POST
/promotion-review/{id}/decision lets a human log an explicit decision
(DEFERRED/PROMOTED/REJECTED) onto an existing review row as an audit
trail -- it does not itself execute a cutover, and nothing in this
codebase reads human_decision to change scanner behavior. See
api/promotion_review.py's own module docstring for the full,
deliberately-scoped rationale.
"""

from __future__ import annotations

from aiohttp import web

from api.shadow_validation import compute_shadow_validation_report
from api.promotion_review import record_promotion_review, fetch_promotion_review_history

routes = web.RouteTableDef()

VALID_DECISIONS = {"DEFERRED", "PROMOTED", "REJECTED"}


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


@routes.post("/api/ebie/promotion-review")
async def ebie_promotion_review_create(request):
    """POST /api/ebie/promotion-review -- computes the current shadow-
    validation report, classifies it (NOT_READY / READY_FOR_HUMAN_REVIEW),
    and persists one durable snapshot row. Called by the scheduler's
    weekly loop, per Q5.3's "evaluate weekly" cadence -- also safe to
    call manually/on-demand. Writes a record only; changes no live
    behavior."""
    pool = request.app.get("pg_pool")
    redis = request.app.get("redis")
    report = await compute_shadow_validation_report(pool, redis)
    if not report.get("available"):
        return web.json_response(report, status=503)
    result = await record_promotion_review(pool, report)
    return web.json_response(result)


@routes.get("/api/ebie/promotion-review/history")
async def ebie_promotion_review_history(request):
    """GET /api/ebie/promotion-review/history?limit=20 -- recent review
    snapshots, for a human to see the real trend over time before ever
    deciding anything."""
    pool = request.app.get("pg_pool")
    limit = min(max(int(request.query.get("limit", "20") or 20), 1), 100)
    result = await fetch_promotion_review_history(pool, limit=limit)
    return web.json_response(result)


@routes.post("/api/ebie/promotion-review/{review_id}/decision")
async def ebie_promotion_review_decision(request):
    """POST /api/ebie/promotion-review/{id}/decision -- lets a human
    record an explicit decision on an existing review row. Body:
    {"decision": "DEFERRED"|"PROMOTED"|"REJECTED", "note": "..."}.
    This is an AUDIT-TRAIL WRITE ONLY -- no code anywhere in this
    codebase reads human_decision to change what scanner does. An
    actual champion/challenger cutover, if one is ever authorized,
    would be a separate, explicitly-reviewed architectural change, not
    a side effect of calling this endpoint."""
    pool = request.app.get("pg_pool")
    if not pool:
        return web.json_response({"available": False, "reason": "Postgres analytics pool is not available."}, status=503)

    try:
        review_id = int(request.match_info["review_id"])
    except (KeyError, ValueError):
        return web.json_response({"available": False, "reason": "Invalid review id."}, status=400)

    body = await request.json()
    decision = str(body.get("decision") or "").upper()
    note = body.get("note")
    if decision not in VALID_DECISIONS:
        return web.json_response(
            {"available": False, "reason": f"decision must be one of {sorted(VALID_DECISIONS)}."}, status=400,
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE ebie_promotion_reviews
            SET human_decision = $1, human_decision_note = $2, human_decision_at = now()
            WHERE id = $3
            RETURNING id, human_decision, human_decision_note, human_decision_at
            """,
            decision, note, review_id,
        )
    if not row:
        return web.json_response({"available": False, "reason": f"No review with id {review_id}."}, status=404)

    return web.json_response({
        "available": True,
        "review_id": row["id"],
        "human_decision": row["human_decision"],
        "human_decision_note": row["human_decision_note"],
        "human_decision_at": row["human_decision_at"].isoformat(),
    })
