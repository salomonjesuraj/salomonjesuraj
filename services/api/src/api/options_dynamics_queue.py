"""Dynamic option-chain sweep loop — EBIE EB-5.

Same shape as every other queue loop in this service (radar_alert_
queue.py/futures_queue.py/mtf_queue.py): in-process asyncio task,
periodic sweep, writes a per-symbol Redis hash other routes can read
cheaply. See api/options_analytics_v2.py's own header for the pure
functions this wraps.

Candidate selection deliberately reuses option_chain_queue.py's
build_candidates() rather than sweeping the full 208-symbol universe --
full option-chain fetches are "heavier" (that module's own docstring)
than the near-ATM context fetch it drives, so this stays scoped to the
same already-proven priority set (active signals, pre-breakout leaders,
high-momentum rows) rather than adding a second, independent heavy
sweep across everything.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

import structlog

from api.option_chain_queue import build_candidates
from api.options_analytics_v2 import (
    compute_pcr_acceleration,
    compute_pcr_velocity,
    compute_wall_dynamics,
    compute_weighted_pcr,
)
from api.routes.market import _fetch_full_option_chain

logger = structlog.get_logger()

SWEEP_INTERVAL_SEC = 60
CANDIDATE_LIMIT = 20
STATUS_KEY = "infusion:options-dynamics-queue:status"
STATE_PREFIX = "infusion:options-dynamics:"  # + {symbol} -> STRING (JSON)
STATE_TTL_SEC = 300
# EBIE-KNOWN-GAPS.md §1.7 -- same "distinguish never-cached from cached-
# but-stale" marker as mtf.py's MTF_LAST_SEEN_PREFIX. This sweep only ever
# covers CANDIDATE_LIMIT=20 priority symbols per cycle out of 208, so most
# symbols' STATE_PREFIX entry is routinely absent -- this longer-lived,
# payload-free marker is the only way api/routes/ebie_candidates.py can
# tell "never computed" apart from "computed a while ago, since expired."
LAST_SEEN_PREFIX = "infusion:options-dynamics-last-seen:"
LAST_SEEN_TTL_SEC = 7 * 24 * 3600
Payload = dict[str, Any]


async def sweep_once(app: Any) -> Payload:
    redis = app.get("redis")
    if not redis:
        return {"available": False, "reason": "Redis not available."}

    candidates = await build_candidates(redis, CANDIDATE_LIMIT)
    swept = 0
    failed = 0

    for row in candidates:
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        try:
            chain = await _fetch_full_option_chain(redis, symbol)
            if not chain.get("ready"):
                continue
            rows = chain.get("rows") or []
            spot = float(chain.get("spot") or 0)

            prev_raw = await redis.get(f"{STATE_PREFIX}{symbol}")
            prev = json.loads(prev_raw) if prev_raw else None
            prev_wall = (prev or {}).get("wall")
            prev_pcr = (prev or {}).get("weighted_pcr", {}).get("weighted_pcr") if prev else None
            prev_velocity = (prev or {}).get("pcr_velocity") if prev else None

            weighted = compute_weighted_pcr(rows, spot)
            wall = compute_wall_dynamics(rows, prev_wall)
            velocity = compute_pcr_velocity(
                weighted.get("weighted_pcr") if weighted else None, prev_pcr
            )
            # EBIE EB-15 Phase 5 item 8 -- OI acceleration, the second
            # derivative. Needs the PREVIOUS sweep's own velocity value
            # (not recomputed from a 2-sweeps-back PCR), same "state
            # carries forward one field at a time" shape velocity itself
            # already uses one line up.
            acceleration = compute_pcr_acceleration(velocity, prev_velocity)

            state = {
                "symbol": symbol,
                "spot": spot,
                "expiry": chain.get("expiry"),
                "weighted_pcr": weighted,
                "pcr_velocity": velocity,
                "pcr_acceleration": acceleration,
                "wall": wall,
                "updated_at": int(time.time()),
            }
            await redis.setex(
                f"{STATE_PREFIX}{symbol}", STATE_TTL_SEC, json.dumps(state, default=str)
            )
            await redis.setex(
                f"{LAST_SEEN_PREFIX}{symbol}", LAST_SEEN_TTL_SEC, str(int(time.time()))
            )
            swept += 1
        except Exception as exc:
            failed += 1
            logger.warning("options_dynamics_sweep_symbol_failed", symbol=symbol, error=str(exc))

    status = {
        "available": True,
        "candidates": len(candidates),
        "swept": swept,
        "failed": failed,
        "checked_at": int(time.time()),
    }
    await redis.set(STATUS_KEY, json.dumps(status), ex=600)
    return status


async def options_dynamics_loop(app: Any) -> None:
    redis = app.get("redis")
    if not redis:
        logger.info("options_dynamics_queue_skipped", reason="redis_unavailable")
        return
    logger.info(
        "options_dynamics_queue_started", interval=SWEEP_INTERVAL_SEC, limit=CANDIDATE_LIMIT
    )
    while True:
        with contextlib.suppress(Exception):
            status = await sweep_once(app)
            logger.info(
                "options_dynamics_sweep", **{k: v for k, v in status.items() if k != "checked_at"}
            )
        await asyncio.sleep(SWEEP_INTERVAL_SEC)
