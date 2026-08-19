"""EBIE EB-1 -- canonical early-breakout state machine, run in SHADOW mode.

Same in-process asyncio sweep-loop architecture as radar_alert_queue.py
(Phase R9) -- reads _build_ticks() in-process (no HTTP round-trip, no
second copy of that pipeline), compares each symbol/direction's current
mapped EBIE state against what it was last sweep via a small TTL-bound
Redis cache, and logs every genuine transition to Postgres. Deliberately
never influences live scanner/alert behavior -- see docs/EBIE-
IMPLEMENTATION-ANSWERS.md's Section 38 "Shadow Mode": a new model/feature
enters shadow before it can alter user-facing verdicts, and stays there
until EB-13's shadow-validation gate is actually passed.

State mapping (v1) is deliberately built ONLY from evidence that already
exists today -- stock_breakout_tier/breakout_type (api/routes/ticks.py,
Phases R1/R2/R8), PreBreakoutTracker's raw state (services/scanner/src/
scanner/pre_breakout.py), and whether a real signal is currently active
(infusion:signal:{symbol}). EB-1's job is the STATE MACHINE STRUCTURE and
the shared EpisodeManager (see episode_manager.py on the scanner side) --
genuinely new evidence families (accumulation, microstructure, futures/
options positioning, sentiment) are EB-2 through EB-7's job, not this
one's. This mapping is disclosed as a starting point, not a finished
model -- it will visibly improve as those phases land.

Canonical states (docs/EBIE-BLUEPRINT.md Section 5):
    IDLE -> DEVELOPING -> PRE_BREAKOUT/PRE_BREAKDOWN -> READY -> ARMED
    -> TRIGGERED -> CONFIRMED, or FAILED at any point along the way
    (also covers TRAP for this v1 -- EB-9 builds a dedicated trap-
    probability model later; a nuanced TRAP sub-classification isn't
    real evidence yet, just a terminal bucket a setup can fall into).

Per Q3.1's authorized migration plan, this does NOT replace
PreBreakoutTracker/stock_breakout_tier/radar_alerts -- they keep running
exactly as before, feeding this as inputs, persisted alongside it for
comparison (legacy_tier/legacy_pb_state columns on every transition row).
Promotion to canonical authority, and deprecating the old outputs, is a
separate, later, explicitly-gated step (EB-13/EB-14) -- not this phase.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timezone

import structlog

from api.routes.ticks import _build_ticks
from infusion_streams.constants import KEY_EBIE_STATE_PREFIX, KEY_SIGNAL_PREFIX

logger = structlog.get_logger()

EBIE_STATE_TTL_SEC = 24 * 3600  # same TTL-bound principle as R2/R8/R9's own transitional caches
SWEEP_INTERVAL_SEC = 60
STATUS_KEY = "infusion:ebie-state-queue:status"

CANONICAL_STATES = (
    "IDLE", "DEVELOPING", "PRE_BREAKOUT", "PRE_BREAKDOWN",
    "READY", "ARMED", "TRIGGERED", "CONFIRMED", "FAILED",
)


def map_ebie_state(entry: dict, has_active_signal: bool) -> tuple[str, str, str]:
    """Derive (state, direction, reason) for one /api/ticks row.

    Pure function, no I/O -- easy to unit-test and to keep in sync with
    what's actually being read from `entry` below. `entry` is one row
    from _build_ticks(); `has_active_signal` is a separate, cheap check
    of whether infusion:signal:{symbol} currently exists (a real,
    scanner-fired signal, not this shadow state machine's own read).
    """
    tier = str(entry.get("stock_breakout_tier") or "NO_CHASE").upper()
    pb_state = str(entry.get("setup_state") or "IDLE").upper()
    chase_quality = str(entry.get("chase_quality") or "").upper()
    direction = "BEARISH" if entry.get("trend_bias") == "SELL" else "BULLISH"

    if has_active_signal:
        return "CONFIRMED", direction, "a real scanner-fired signal is currently active"

    if pb_state == "TRIGGERED":
        return "TRIGGERED", direction, "pre-breakout tracker just triggered"

    if tier == "OPTION_READY" or (
        tier == "BREAKOUT_NOW" and chase_quality in {"HIGHLY_CHASEABLE", "CLEAN"}
    ):
        return "ARMED", direction, f"tier={tier} chase_quality={chase_quality or 'n/a'}"

    if tier in {"RETEST_ENTRY", "BREAKOUT_NOW"}:
        return "READY", direction, f"tier={tier}"

    if tier == "EARLY_WATCH" or pb_state == "COILED":
        state = "PRE_BREAKDOWN" if direction == "BEARISH" else "PRE_BREAKOUT"
        return state, direction, f"tier={tier} pb_state={pb_state}"

    if pb_state in {"COMPRESSING", "ACCUMULATING"}:
        return "DEVELOPING", direction, f"pb_state={pb_state}"

    if pb_state == "EXPIRED":
        return "FAILED", direction, "pre-breakout setup expired without ever breaking out"

    return "IDLE", direction, "no active evidence"


async def _read_prev_states(redis, keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    values = await redis.mget([f"{KEY_EBIE_STATE_PREFIX}{k}" for k in keys])
    result: dict[str, str] = {}
    for key, raw in zip(keys, values):
        if raw:
            result[key] = raw.decode() if isinstance(raw, bytes) else raw
    return result


async def _write_states(redis, updates: dict[str, str]) -> None:
    if not updates:
        return
    pipe = redis.pipeline(transaction=False)
    for key, state in updates.items():
        pipe.set(f"{KEY_EBIE_STATE_PREFIX}{key}", state, ex=EBIE_STATE_TTL_SEC)
    await pipe.execute()


async def sweep_once(app) -> dict:
    """One shadow-state-machine pass. Called every SWEEP_INTERVAL_SEC by
    ebie_state_loop(); also directly callable (e.g. from a verification
    script) since it only needs `app`'s redis/pg_pool.
    """
    redis = app.get("redis")
    pool = app.get("pg_pool")
    if not redis or not pool:
        return {"available": False, "reason": "Redis or Postgres pool not available."}

    ticks = await _build_ticks(redis)
    symbols = [t["symbol"] for t in ticks if t.get("symbol")]

    # Bulk-check which symbols currently have a real, scanner-fired
    # active signal (infusion:signal:{symbol} -- see engine.py's
    # legacy_signal_key). Existence alone is enough; the key is TTL'd by
    # the signal's own ttl_sec, so "exists" already means "currently
    # active," no extra freshness check needed.
    active_pipe = redis.pipeline(transaction=False)
    for symbol in symbols:
        active_pipe.exists(f"{KEY_SIGNAL_PREFIX}{symbol}")
    active_results = await active_pipe.execute() if symbols else []
    has_signal = {s: bool(r) for s, r in zip(symbols, active_results)}

    mapped: dict[str, tuple[str, str, str]] = {}
    for entry in ticks:
        symbol = entry.get("symbol")
        if not symbol:
            continue
        state, direction, reason = map_ebie_state(entry, has_signal.get(symbol, False))
        mapped[symbol] = (state, direction, reason)

    cache_keys = [f"{s}:{d}" for s, (_, d, _) in mapped.items()]
    prev_states = await _read_prev_states(redis, cache_keys)

    transitions = 0
    state_updates: dict[str, str] = {}
    by_entry = {e.get("symbol"): e for e in ticks if e.get("symbol")}

    async with pool.acquire() as conn:
        for symbol, (state, direction, reason) in mapped.items():
            cache_key = f"{symbol}:{direction}"
            prev_state = prev_states.get(cache_key)
            state_updates[cache_key] = state

            if prev_state == state:
                continue  # no transition -- exactly the point of a cache, not a snapshot

            entry = by_entry.get(symbol, {})
            await conn.execute(
                """
                INSERT INTO ebie_state_transitions
                    (symbol, direction, sector_id, state, prev_state, reason,
                     legacy_tier, legacy_pb_state, score, ltp)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                symbol, direction, entry.get("sector_id"), state, prev_state, reason,
                entry.get("stock_breakout_tier"), entry.get("setup_state"),
                entry.get("stock_breakout_score"), entry.get("ltp"),
            )
            transitions += 1

    await _write_states(redis, state_updates)

    status = {
        "available": True,
        "swept": len(ticks),
        "transitions": transitions,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    await redis.set(STATUS_KEY, json.dumps(status, separators=(",", ":")), ex=600)
    return status


async def ebie_state_loop(app) -> None:
    redis = app.get("redis")
    pool = app.get("pg_pool")
    if not redis or not pool:
        logger.info("ebie_state_queue_skipped", reason="redis_or_pg_pool_unavailable")
        return
    logger.info("ebie_state_queue_started", interval=SWEEP_INTERVAL_SEC)
    while True:
        with contextlib.suppress(Exception):
            status = await sweep_once(app)
            logger.info("ebie_state_sweep", **{k: v for k, v in status.items() if k != "checked_at"})
        await asyncio.sleep(SWEEP_INTERVAL_SEC)
