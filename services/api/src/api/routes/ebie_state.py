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
from typing import Any, cast

import msgpack
from aiohttp import web
from infusion_streams.constants import KEY_EBIE_VERDICT_LITE_PREFIX

routes = web.RouteTableDef()
Payload = dict[str, Any]

# EBIE EB-15 Phase 3 -- verdicts genuinely worth a human glancing at.
# NO_TRADE is deliberately excluded by default (it's the "nothing to see
# here" bucket for most of the 208-symbol universe most of the time,
# same reasoning as the Breakout Radar's own NO_CHASE-heavy tiering) --
# pass ?include_no_trade=true to see the full unfiltered universe.
_ACTIONABLE_LIGHTWEIGHT_VERDICTS = {
    "WATCH_LONG",
    "WATCH_SHORT",
    "LONG_DEVELOPING",
    "SHORT_DEVELOPING",
    "LONG_READY",
    "SHORT_READY",
    "BREAKOUT_ARMED",
    "BREAKDOWN_ARMED",
    "AVOID_TRAP_RISK",
    "DATA_UNRELIABLE",
}


@routes.get("/api/ebie/status")
async def ebie_status(request: web.Request) -> web.Response:
    """Shadow sweep loop's own last-run status."""
    redis = request.app["redis"]
    raw = await redis.get("infusion:ebie-state-queue:status")
    if not raw:
        return web.json_response({"available": False, "reason": "No sweep has completed yet."})
    text = raw.decode() if isinstance(raw, bytes) else raw
    return web.json_response(json.loads(text))


@routes.get("/api/ebie/transitions/recent")
async def ebie_transitions_recent(request: web.Request) -> web.Response:
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
                symbol.upper(),
                limit,
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

    return web.json_response(
        {
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
        }
    )


@routes.get("/api/ebie/comparison")
async def ebie_comparison(request: web.Request) -> web.Response:
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

    return web.json_response(
        {
            "available": True,
            "window_hours": hours,
            "total_transitions": total,
            "state_distribution": [{"state": r["state"], "count": r["n"]} for r in state_counts],
            "state_vs_legacy_tier": [
                {"state": r["state"], "legacy_tier": r["legacy_tier"], "count": r["n"]}
                for r in cross_tab
            ],
        }
    )


@routes.get("/api/ebie/lightweight-verdicts")
async def ebie_lightweight_verdicts(request: web.Request) -> web.Response:
    """GET /api/ebie/lightweight-verdicts?include_no_trade=false --
    Phase 3's universe-wide LIGHTWEIGHT verdict, one per symbol, written
    every 60s by ebie_state_queue.py's sweep (compute_lightweight_verdict()).
    This is the directive's own "every DEVELOPING/PRE_BREAKOUT/
    PRE_BREAKDOWN symbol has a visible verdict BEFORE a strategy candidate
    ever fires" requirement -- distinct from /api/ebie/candidates, which
    only ever covers symbols that already produced a SignalCandidate.
    """
    redis = request.app["redis"]
    include_no_trade = str(request.query.get("include_no_trade", "false")).lower() == "true"

    all_symbols_raw = await redis.hgetall("infusion:symbols")
    if not all_symbols_raw:
        return web.json_response(
            {"available": False, "reason": "Symbol universe not loaded yet.", "verdicts": []}
        )

    symbols: list[str] = []
    for meta_raw in all_symbols_raw.values():
        try:
            meta_raw_decoded = (
                msgpack.unpackb(meta_raw, raw=False) if isinstance(meta_raw, bytes) else meta_raw
            )
            meta = cast(Payload, meta_raw_decoded) if isinstance(meta_raw_decoded, dict) else {}
            symbol = meta.get("symbol")
            if symbol:
                symbols.append(symbol)
        except Exception:
            continue

    if not symbols:
        return web.json_response({"available": True, "count": 0, "verdicts": []})

    raw_values = await redis.mget([f"{KEY_EBIE_VERDICT_LITE_PREFIX}{s}" for s in symbols])

    verdicts: list[Payload] = []
    for raw in raw_values:
        if not raw:
            continue
        try:
            v_raw = msgpack.unpackb(raw, raw=False)
            v = cast(Payload, v_raw) if isinstance(v_raw, dict) else {}
        except Exception:
            continue
        if not include_no_trade and v.get("verdict") not in _ACTIONABLE_LIGHTWEIGHT_VERDICTS:
            continue
        verdicts.append(v)

    # Rank by confidence band (VERY_HIGH first), same descending-priority
    # feel as the Radar/Command Center panels.
    band_rank = {"VERY_HIGH": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    verdicts.sort(key=lambda v: band_rank.get(str(v.get("confidence_band") or ""), 9))

    return web.json_response(
        {
            "available": True,
            "count": len(verdicts),
            "universe_size": len(symbols),
            "verdicts": verdicts,
        }
    )
