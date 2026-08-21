"""Phase R9 -- dashboard-only early alerts, and their outcome tracking.

Answers a question the Stock Breakout Radar (R1-R8) never tried to
answer on its own: "the Radar shows me a live tier snapshot every poll,
but did I actually get notified the FIRST moment a symbol became
interesting, and did that early notice tend to pay off?"

Two, deliberately separate, things:

1. Detection -- every sweep, compare each symbol's current
   stock_breakout_tier (services/api/src/api/routes/ticks.py's
   _build_ticks(), the exact same computation the dashboard's own
   /api/ticks poll uses -- called in-process here, no HTTP round-trip,
   no second copy of that ~250-line pipeline to keep in sync) against
   the tier it had last sweep (infusion:radar-alert-tier:{symbol}, a
   small TTL-bound transitional cache -- same "cheap bulk MGET, TTL so
   it ages out rather than growing forever" shape as R2's vwap-state and
   R8's opening-range cache). The FIRST time a symbol crosses from
   NO_CHASE into EARLY_WATCH-or-higher, that's a new alert -- a row gets
   written to Postgres (`radar_alerts`, migration 006). Repeat sweeps
   while it stays at EARLY_WATCH+ don't re-fire; that's the whole point
   of using a transition, not a snapshot (same reasoning R2's VWAP
   reclaim/rejection classification already established).

2. Outcome tracking -- every sweep also revisits every still-OPEN
   (outcome_label = 'PENDING') alert and checks what its symbol's tier
   has done since: reached BREAKOUT_NOW/OPTION_READY -> GRADUATED.
   Dropped back to NO_CHASE -> FADED (no grace period -- the specific
   evidence that earned this alert is genuinely gone; a symbol that
   comes back later gets a fresh alert, which is the more honest read
   than papering over a real round-trip with one row). Still open past
   RADAR_ALERT_STALE_HOURS with neither outcome -> EXPIRED, a catch-all
   so a row from a quiet, low-volatility day doesn't stay PENDING
   forever.

Deliberately never touches `signals`, scanner's cooldown/suppression
gates, or Telegram -- this is a strictly dashboard-side notification and
its own, separate ledger, one step earlier and softer than a real fired
signal. Runs in-process inside the api service (asyncio.create_task in
main.py, same pattern as option_chain_queue_loop/mtf_queue_loop) since
it needs both Redis (for the tier computation) and the Postgres pool
(for the ledger) that are already sitting on `app`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta

import structlog

from api.routes.ticks import _build_ticks

logger = structlog.get_logger()

# Ordered weakest -> strongest, mirrors ticks.py's own tier vocabulary.
TIER_RANK = {
    "NO_CHASE": 0,
    "EARLY_WATCH": 1,
    "RETEST_ENTRY": 2,
    "BREAKOUT_NOW": 3,
    "OPTION_READY": 4,
}
ALERT_THRESHOLD_RANK = TIER_RANK["EARLY_WATCH"]  # user's own scoping answer
GRADUATION_THRESHOLD_RANK = TIER_RANK["BREAKOUT_NOW"]
RADAR_ALERT_STALE_HOURS = 6  # comfortably spans a full session (09:15-15:30 IST)

TIER_STATE_TTL_SEC = 24 * 3600  # same TTL-bound principle as R2/R8's own transitional caches
TIER_STATE_PREFIX = "infusion:radar-alert-tier:"

SWEEP_INTERVAL_SEC = 60
STATUS_KEY = "infusion:radar-alert-queue:status"


def _rank(tier: str | None) -> int:
    return TIER_RANK.get(str(tier or "").upper(), -1)


async def _read_tier_state(redis, symbols: list[str]) -> dict[str, str]:
    if not symbols:
        return {}
    keys = [f"{TIER_STATE_PREFIX}{s}" for s in symbols]
    values = await redis.mget(keys)
    result: dict[str, str] = {}
    for symbol, raw in zip(symbols, values, strict=False):
        if raw:
            result[symbol] = raw.decode() if isinstance(raw, bytes) else raw
    return result


async def _write_tier_state(redis, updates: dict[str, str]) -> None:
    if not updates:
        return
    pipe = redis.pipeline(transaction=False)
    for symbol, tier in updates.items():
        pipe.set(f"{TIER_STATE_PREFIX}{symbol}", tier, ex=TIER_STATE_TTL_SEC)
    await pipe.execute()


async def sweep_once(app) -> dict:
    """One detection + outcome-tracking pass. Called every
    SWEEP_INTERVAL_SEC by radar_alert_loop(); also callable directly
    (e.g. from a test script) since it only needs `app`'s redis/pg_pool.
    """
    redis = app.get("redis")
    pool = app.get("pg_pool")
    if not redis or not pool:
        return {"available": False, "reason": "Redis or Postgres pool not available."}

    ticks = await _build_ticks(redis)
    symbols = [t["symbol"] for t in ticks if t.get("symbol")]
    prev_tiers = await _read_tier_state(redis, symbols)

    new_alerts = 0
    graduated = 0
    faded = 0
    tier_updates: dict[str, str] = {}

    async with pool.acquire() as conn:
        for entry in ticks:
            symbol = entry.get("symbol")
            if not symbol:
                continue
            current_tier = str(entry.get("stock_breakout_tier") or "NO_CHASE").upper()
            current_rank = _rank(current_tier)
            prev_rank = _rank(prev_tiers.get(symbol))
            tier_updates[symbol] = current_tier

            if prev_rank < ALERT_THRESHOLD_RANK and current_rank >= ALERT_THRESHOLD_RANK:
                # Fresh crossing -- a genuinely new alert. Any previously
                # open row for this symbol has already been resolved
                # (FADED/GRADUATED/EXPIRED) by the time it could drop
                # back below the threshold and cross up again -- the
                # tier-state cache and the PENDING-row lifecycle move in
                # lockstep by construction, not by an extra guard here.
                await conn.execute(
                    """
                    INSERT INTO radar_alerts
                        (symbol, sector_id, tier_at_fire, score_at_fire,
                         breakout_type_at_fire, rel_vol_at_fire, ltp_at_fire,
                         best_tier_reached, best_tier_reached_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $3, now())
                    """,
                    symbol,
                    entry.get("sector_id"),
                    current_tier,
                    entry.get("stock_breakout_score"),
                    entry.get("breakout_type"),
                    entry.get("rel_vol"),
                    entry.get("ltp"),
                )
                new_alerts += 1
                continue

            if prev_rank < ALERT_THRESHOLD_RANK:
                # Was, and still is, below the alert threshold -- nothing
                # open for this symbol, nothing to track.
                continue

            # Was already alert-worthy last sweep -- update the open
            # row's outcome, if it has one.
            row = await conn.fetchrow(
                """
                SELECT id, best_tier_reached FROM radar_alerts
                WHERE symbol = $1 AND outcome_label = 'PENDING'
                ORDER BY fired_at DESC LIMIT 1
                """,
                symbol,
            )
            if not row:
                # Tier-state cache says "already alerted" but there's no
                # open row (e.g. it already resolved this same sweep on
                # a prior symbol iteration is impossible, but a manual
                # DB edit or a restart racing the cache isn't) -- treat
                # as a fresh alert rather than silently losing the event.
                await conn.execute(
                    """
                    INSERT INTO radar_alerts
                        (symbol, sector_id, tier_at_fire, score_at_fire,
                         breakout_type_at_fire, rel_vol_at_fire, ltp_at_fire,
                         best_tier_reached, best_tier_reached_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $3, now())
                    """,
                    symbol,
                    entry.get("sector_id"),
                    current_tier,
                    entry.get("stock_breakout_score"),
                    entry.get("breakout_type"),
                    entry.get("rel_vol"),
                    entry.get("ltp"),
                )
                new_alerts += 1
                continue

            if current_rank > _rank(row["best_tier_reached"]):
                await conn.execute(
                    "UPDATE radar_alerts SET best_tier_reached = $1, best_tier_reached_at = now(), last_checked_at = now() WHERE id = $2",
                    current_tier,
                    row["id"],
                )
            else:
                await conn.execute(
                    "UPDATE radar_alerts SET last_checked_at = now() WHERE id = $1",
                    row["id"],
                )

            if current_rank >= GRADUATION_THRESHOLD_RANK:
                await conn.execute(
                    "UPDATE radar_alerts SET outcome_label = 'GRADUATED', resolved_at = now() WHERE id = $1",
                    row["id"],
                )
                graduated += 1
            elif current_rank < ALERT_THRESHOLD_RANK:
                await conn.execute(
                    "UPDATE radar_alerts SET outcome_label = 'FADED', resolved_at = now() WHERE id = $1",
                    row["id"],
                )
                faded += 1

        # Catch-all: anything still PENDING well past a normal session
        # (a symbol that hovered at EARLY_WATCH without ever clearing
        # BREAKOUT_NOW or dropping back to NO_CHASE) gets marked EXPIRED
        # rather than staying open forever.
        stale_cutoff = datetime.now(UTC) - timedelta(hours=RADAR_ALERT_STALE_HOURS)
        expired_rows = await conn.fetch(
            "UPDATE radar_alerts SET outcome_label = 'EXPIRED', resolved_at = now() WHERE outcome_label = 'PENDING' AND fired_at < $1 RETURNING id",
            stale_cutoff,
        )
        expired = len(expired_rows)

    await _write_tier_state(redis, tier_updates)

    status = {
        "available": True,
        "swept": len(ticks),
        "new_alerts": new_alerts,
        "graduated": graduated,
        "faded": faded,
        "expired": expired,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    await redis.set(STATUS_KEY, json.dumps(status, separators=(",", ":")), ex=600)
    return status


async def radar_alert_loop(app) -> None:
    redis = app.get("redis")
    pool = app.get("pg_pool")
    if not redis or not pool:
        logger.info("radar_alert_queue_skipped", reason="redis_or_pg_pool_unavailable")
        return
    logger.info("radar_alert_queue_started", interval=SWEEP_INTERVAL_SEC)
    while True:
        with contextlib.suppress(Exception):
            status = await sweep_once(app)
            logger.info(
                "radar_alert_sweep", **{k: v for k, v in status.items() if k != "checked_at"}
            )
        await asyncio.sleep(SWEEP_INTERVAL_SEC)
