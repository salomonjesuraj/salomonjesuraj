"""EBIE EB-1 routes -- read-only views into the canonical state machine
(shadow mode). Writes happen only in ebie_state_queue.py's sweep loop --
these routes mirror radar_alerts.py's own "writes happen only in the
sweep loop" convention exactly.

Shadow mode: nothing here drives any live scanner/alert behavior. These
endpoints exist so the new state machine's real output can be inspected
and compared against the existing tier/pre-breakout systems it's shadowing
-- per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q3.1's explicit requirement
that "existing tier vs EBIE state must be persisted for comparison."
"""

from __future__ import annotations

import json

from aiohttp import web

routes = web.RouteTableDef()


@routes.get("/api/ebie/status")
async def ebie_status(request):
    """Shadow sweep loop's own last-run status."""
    redis = request.app["redis"]
    raw = await redis.get("infusion:ebie-state-queue:status")
    if not raw:
        return web.json_response({"available": False, "reason": "No sweep has completed yet."})
    return web.json_response(json.loads(raw))


@routes.get("/api/ebie/transitions/recent")
async def ebie_transitions_recent(request):
    """Most recently logged state transitions (default 50, capped 200)."""
    pool = request.app.get("pg_pool")
    if not pool:
        return web.json_response({"available": False, "reason": "Postgres not configured."})

    try:
        limit = min(int(request.query.get("limit", 50)), 200)
    except ValueError:
        limit = 50

    symbol = request.query.get("symbol")
    async with pool.acquire() as conn:
        if symbol:
            rows = await conn.fetch(
                """
                SELECT symbol, direction, sector_id, state, prev_state, reason,
                       legacy_tier, legacy_pb_state, score, ltp, transitioned_at
                FROM ebie_state_transitions
                WHERE symbol = $1
                ORDER BY transitioned_at DESC LIMIT $2
                """,
                symbol.upper(), limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT symbol, direction, sector_id, state, prev_state, reason,
                       legacy_tier, legacy_pb_state, score, ltp, transitioned_at
                FROM ebie_state_transitions
                ORDER BY transitioned_at DESC LIMIT $1
                """,
                limit,
            )

    return web.json_response({
        "available": True,
        "count": len(rows),
        "transitions": [
            {
                "symbol": r["symbol"],
                "direction": r["direction"],
                "sector_id": r["sector_id"],
                "state": r["state"],
                "prev_state": r["prev_state"],
                "reason": r["reason"],
                "legacy_tier": r["legacy_tier"],
                "legacy_pb_state": r["legacy_pb_state"],
                "score": float(r["score"]) if r["score"] is not None else None,
                "ltp": float(r["ltp"]) if r["ltp"] is not None else None,
                "transitioned_at": r["transitioned_at"].isoformat(),
            }
            for r in rows
        ],
    })


@routes.get("/api/ebie/comparison")
async def ebie_comparison(request):
    """Shadow-comparison view: how the new EBIE state distribution lines
    up against the legacy tier it was derived from, over a real window
    (default last 24h, capped 30 days). This is the honesty check Q3.1
    asks for -- not a claim that EBIE is right, just a real look at where
    the two systems currently agree or diverge.
    """
    pool = request.app.get("pg_pool")
    if not pool:
        return web.json_response({"available": False, "reason": "Postgres not configured."})

    try:
        hours = min(max(int(request.query.get("hours", 24)), 1), 30 * 24)
    except ValueError:
        hours = 24

    async with pool.acquire() as conn:
        state_counts = await conn.fetch(
            """
            SELECT state, count(*) AS n
            FROM ebie_state_transitions
            WHERE transitioned_at >= now() - ($1 || ' hours')::interval
            GROUP BY state ORDER BY n DESC
            """,
            str(hours),
        )
        cross_tab = await conn.fetch(
            """
            SELECT state, legacy_tier, count(*) AS n
            FROM ebie_state_transitions
            WHERE transitioned_at >= now() - ($1 || ' hours')::interval
            GROUP BY state, legacy_tier ORDER BY state, n DESC
            """,
            str(hours),
        )
        total = await conn.fetchval(
            "SELECT count(*) FROM ebie_state_transitions WHERE transitioned_at >= now() - ($1 || ' hours')::interval",
            str(hours),
        )

    return web.json_response({
        "available": True,
        "window_hours": hours,
        "total_transitions": total,
        "state_distribution": [{"state": r["state"], "count": r["n"]} for r in state_counts],
        "state_vs_legacy_tier": [
            {"state": r["state"], "legacy_tier": r["legacy_tier"], "count": r["n"]}
            for r in cross_tab
        ],
    })
