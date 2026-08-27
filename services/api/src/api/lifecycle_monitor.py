"""Trade Lifecycle Monitor -- "Visual Tracking & Lifecycle" sprint
(2026-08-27).

The Ledger's journal (routes/journal.py) is a real Redis-backed record
of paper trades, but until this sprint nothing ever advanced a row past
WATCH/PLANNED on its own -- the only way `status` became CLOSED was a
manual POST to /api/journal/trades/{id}/outcome, which nothing in this
app's own frontend ever calls. In practice every journal row sat open
forever, and TheLedger's Cumulative Equity chart (lib/equityCurve.ts's
own buildEquityCurve(), which only reads `status === 'CLOSED'` rows)
never had anything to plot. This module is the missing writer.

This is a DIFFERENT system from services/archiver/src/archiver/tracker.py's
OutcomeTracker, not a rebuild of it -- that one resolves the Postgres
`signals` table (every scanner-published signal, tracked continuously
in a separate service) against target_price/invalidation_price. This
one resolves the Ledger's own Redis journal rows (paper trades a trader
actually reviewed/logged, tracked here in the `api` service since that's
where routes/journal.py itself already lives) against entry/stop/
target1/target2/target3. Both independently reuse the exact same real
1-minute OHLC source -- the infusion:ohlc:{symbol}:1m zset feature-engine
writes on every bar close -- and the same bar-resolution philosophy
(walk real completed-bar high/low chronologically, deterministic
nearest-to-open tie-break when one bar spans multiple levels) that
tracker.py's own docstring already established and disclosed as a
cross-service duplication precedent (archiver and api have no shared
lib import path for this). decode_bar below is that same duplication,
not an oversight.

Outcome vocabulary (deliberately different from OutcomeTracker's own
TARGET_HIT/STOP_HIT/EXPIRED, matching what was actually asked for here):
  - LOSS      -- stop touched before any target was ever reached
  - WIN_T1/T2/T3 -- the deepest configured target level reached. A stop
    touch on a LATER bar, after a target was already banked, does NOT
    downgrade the outcome -- a real trader who saw T1 print would be
    managing the remainder, not still risking the original stop
    unchanged. This is a disclosed simplification (no partial-exit or
    breakeven-trail modeling exists anywhere in this codebase), not a
    guarantee about what a live position would have actually done.
  - MISSED    -- neither stop nor any target was reached within
    LIFECYCLE_TTL_MIN minutes of entry (one full NSE session, 09:15-
    15:30 IST = 375 minutes) -- the setup never resolved either way
    within a trading day's own window, so continuing to track it stops
    being informative.

A WATCH row hasn't necessarily been entered yet (routes/journal.py's own
_normalise_trade sets WATCH when the option chain was still
WAIT_CONTRACT or the decision was itself "WAIT") -- resolving one
describes what would have happened had the plan been taken, not a real
fill. Tracked anyway because that's what was explicitly asked for
("every signal in the WATCH or ACTIVE state"); this journal's own status
machine has no literal ACTIVE state, so PLANNED (a committed, currently
open paper position) is treated as that state's closest real analog.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import datetime
from typing import Any, cast

import structlog

from api.routes.journal import IST, MAX_JOURNAL_ROWS, load_rows, save_rows

logger = structlog.get_logger()

Payload = dict[str, Any]

SWEEP_INTERVAL_SEC = 30
OPEN_STATUSES = {"WATCH", "PLANNED"}
LIFECYCLE_TTL_MIN = 375  # one full NSE session: 09:15-15:30 IST
_LEVEL_RANK = {"T1": 1, "T2": 2, "T3": 3}


def _num(value: object) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return 0.0


def _ist_str(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=IST).strftime("%Y-%m-%d %H:%M:%S IST")


def _format_duration(total_minutes: float) -> str:
    total_minutes = max(0, round(total_minutes))
    hours, minutes = divmod(total_minutes, 60)
    if hours <= 0:
        return f"{minutes}m"
    return f"{hours}h{minutes}m" if minutes else f"{hours}h"


def decode_bar(raw: Any) -> Payload | None:
    """One infusion:ohlc:{symbol}:1m zset member -- see this module's
    own docstring for why this duplicates archiver/tracker.py's
    (and api/routes/mtf.py's) identical helper rather than importing
    across a service boundary that has no shared-lib path for it."""
    try:
        val = raw.decode() if isinstance(raw, bytes) else raw
        obj = json.loads(val)
        return {
            "t": int(obj.get("t") or 0),
            "o": float(obj.get("o", 0)),
            "h": float(obj.get("h", 0)),
            "l": float(obj.get("l", 0)),
            "c": float(obj.get("c", 0)),
        }
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return None


def configured_targets(row: Payload) -> list[tuple[str, float]]:
    """T1/T2/T3 in order, only the ones this row actually has a real
    (>0) price for -- a row logged before target2/target3 existed, or
    one where the trader only ever set a single target, simply has
    fewer levels to resolve against, not a fabricated one."""
    out: list[tuple[str, float]] = []
    for name, key in (("T1", "target1"), ("T2", "target2"), ("T3", "target3")):
        price = _num(row.get(key))
        if price > 0:
            out.append((name, price))
    return out


def is_bearish(row: Payload, entry: float, target1: float) -> bool:
    decision = str(row.get("decision") or "").upper()
    if "PE" in decision or "SELL" in decision or "BEAR" in decision:
        return True
    if "CE" in decision or "BUY" in decision:
        return False
    # Same directionless fallback archiver/tracker.py's own bearish
    # heuristic uses when the decision text itself is ambiguous.
    return target1 < entry


def resolve_bar_touch(
    bar: Payload, stop: float, targets: list[tuple[str, float]], bearish: bool
) -> str | None:
    """Which single level ("STOP", or the deepest of T1/T2/T3) this
    bar's high/low range crosses. Among several targets touched in the
    same bar, the DEEPEST one wins outright (reaching T3 necessarily
    means T1/T2 were also crossed on the way there -- no tie-break
    needed there). Only target-vs-stop needs the nearest-to-open
    tie-break, generalizing archiver/tracker.py's own first_touch() from
    its one-target-one-stop case to N targets plus one stop with the
    exact same reasoning: under a continuous price path starting at the
    bar's open, whichever boundary's price is closer to that open is
    necessarily crossed first."""

    bar_low = float(bar["l"])
    bar_high = float(bar["h"])

    def touches(price: float, is_stop: bool) -> bool:
        if not bearish:
            return (bar_low <= price) if is_stop else (bar_high >= price)
        return (bar_high >= price) if is_stop else (bar_low <= price)

    stop_touched = touches(stop, True)
    deepest_target: tuple[str, float] | None = None
    for name, price in targets:
        if touches(price, False):
            deepest_target = (name, price)  # targets is ordered T1->T3

    if stop_touched and deepest_target is not None:
        bar_open = bar["o"]
        if abs(stop - bar_open) < abs(deepest_target[1] - bar_open):
            return "STOP"
        return deepest_target[0]
    if stop_touched:
        return "STOP"
    if deepest_target is not None:
        return deepest_target[0]
    return None


def walk_trade(
    bars: list[Payload],
    stop: float,
    targets: list[tuple[str, float]],
    bearish: bool,
) -> tuple[str | None, Payload | None]:
    """Walks real completed bars in chronological order. Returns
    ("STOP" or "T1"/"T2"/"T3", resolving_bar), or (None, None) if
    unresolved by the given bars. See module docstring for why a target
    reached earlier survives a stop touched on a later bar."""
    highest_configured = targets[-1][0] if targets else None
    best: tuple[str, Payload] | None = None
    for bar in bars:
        touch = resolve_bar_touch(bar, stop, targets, bearish)
        if touch is None:
            continue
        if touch == "STOP":
            if best is None:
                return "STOP", bar
            return best[0], best[1]
        if best is None or _LEVEL_RANK[touch] > _LEVEL_RANK[best[0]]:
            best = (touch, bar)
        if touch == highest_configured:
            return best[0], best[1]
    return (best[0], best[1]) if best else (None, None)


def resolve_trade(row: Payload, bars: list[Payload], now_epoch: float) -> Payload | None:
    """One row's real outcome given the real bars available so far, or
    None if it's genuinely still unresolved (not yet stopped, targeted,
    or timed out) -- callers must leave the row untouched in that case,
    never write a placeholder."""
    entry = _num(row.get("entry"))
    stop = _num(row.get("stop"))
    target1 = _num(row.get("target1"))
    if entry <= 0 or stop <= 0 or target1 <= 0:
        return None
    created_epoch = row.get("created_at_epoch")
    if not isinstance(created_epoch, (int, float)) or created_epoch <= 0:
        return None

    targets = configured_targets(row)
    bearish = is_bearish(row, entry, target1)
    relevant_bars = [b for b in bars if b["t"] >= created_epoch]

    touch, resolving_bar = walk_trade(relevant_bars, stop, targets, bearish)
    if touch is not None and resolving_bar is not None:
        elapsed_min = (resolving_bar["t"] - created_epoch) / 60.0
        outcome = "LOSS" if touch == "STOP" else f"WIN_{touch}"
        return {
            "outcome": outcome,
            "status": "CLOSED",
            "exit_price": resolving_bar["c"],
            "duration": _format_duration(elapsed_min),
            "resolved_at_epoch": resolving_bar["t"],
            "closed_at_ist": _ist_str(resolving_bar["t"]),
        }

    elapsed_min = (now_epoch - created_epoch) / 60.0
    if elapsed_min >= LIFECYCLE_TTL_MIN:
        return {
            "outcome": "MISSED",
            "status": "CLOSED",
            "duration": _format_duration(elapsed_min),
            "resolved_at_epoch": now_epoch,
            "closed_at_ist": _ist_str(now_epoch),
        }
    return None


async def _bars_since(redis: Any, symbol: str, since_epoch: int) -> list[Payload]:
    key = f"infusion:ohlc:{symbol}:1m"
    raw = await redis.zrangebyscore(key, since_epoch, "+inf")
    bars = [b for b in (decode_bar(r) for r in raw) if b is not None]
    bars.sort(key=lambda b: b["t"])
    return bars


async def sweep_once(app: Any) -> Payload:
    redis = app.get("redis")
    if not redis:
        return {"available": False, "reason": "Redis not available."}

    rows = await load_rows(redis, MAX_JOURNAL_ROWS)
    open_indices = [i for i, r in enumerate(rows) if r.get("status") in OPEN_STATUSES]
    if not open_indices:
        return {"available": True, "open": 0, "resolved": 0}

    by_symbol: dict[str, list[int]] = {}
    for i in open_indices:
        by_symbol.setdefault(str(rows[i].get("symbol")), []).append(i)

    bars_by_symbol: dict[str, list[Payload]] = {}
    for symbol, indices in by_symbol.items():
        epochs = [
            rows[i]["created_at_epoch"]
            for i in indices
            if isinstance(rows[i].get("created_at_epoch"), (int, float))
        ]
        if not epochs:
            continue
        bars_by_symbol[symbol] = await _bars_since(redis, symbol, int(min(epochs)))

    now_epoch = time.time()
    resolved = 0
    skipped_no_epoch = 0
    for i in open_indices:
        row = rows[i]
        if not isinstance(row.get("created_at_epoch"), (int, float)):
            skipped_no_epoch += 1
            continue
        bars = bars_by_symbol.get(str(row.get("symbol")), [])
        update = resolve_trade(row, bars, now_epoch)
        if update is not None:
            rows[i] = {**row, **update}
            resolved += 1

    if resolved:
        await save_rows(redis, rows)

    return {
        "available": True,
        "open": len(open_indices),
        "resolved": resolved,
        "skipped_no_epoch": skipped_no_epoch,
    }


async def lifecycle_monitor_loop(app: Any) -> None:
    """Background loop -- same in-process asyncio shape as every other
    queue in this service (e.g. portfolio_risk_queue.py), started
    alongside them in main.py. Not gated to market hours the way
    archiver's OutcomeTracker is: this service's own sibling loops all
    run unconditionally, and an off-hours cycle costs only a handful of
    idle Redis reads (no new bars exist to find outside market hours
    anyway, since feature-engine itself stops writing them)."""
    redis = app.get("redis")
    if not redis:
        logger.info("lifecycle_monitor_skipped", reason="redis_unavailable")
        return
    logger.info("lifecycle_monitor_started", interval=SWEEP_INTERVAL_SEC)
    while True:
        with contextlib.suppress(Exception):
            status = await sweep_once(app)
            if status.get("resolved"):
                logger.info("lifecycle_sweep", **status)
        await asyncio.sleep(SWEEP_INTERVAL_SEC)
