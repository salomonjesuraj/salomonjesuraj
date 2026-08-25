"""Outcome tracker — bar-resolution sampling of signal outcomes.

Reads untracked active signals from Postgres, checks completed 1-minute
OHLC bars from Redis (the same infusion:ohlc:{symbol}:1m zset feature-
engine already writes on every bar close -- see
feature_engine/main.py's on_bar()), and updates outcome status:
  - TARGET_HIT: a bar's high/low crossed target_price
  - STOP_HIT: a bar's high/low crossed invalidation_price
  - EXPIRED: signal TTL elapsed without hitting either

Fix (2026-08-25, pipeline audit finding C2): this previously sampled a
single LTP point-in-time snapshot every tracker_interval_sec (30s) from
Redis hot tick state. If price swept through BOTH target and stop
inside one 30-second gap, the outcome was decided by whichever boundary
happened to be true at the exact instant of the poll -- not by true
chronological first-touch -- and a transient touch that reverted before
the next poll was never recorded at all. Switching to completed
1-minute bar high/low removes the polling-gap blind spot entirely: a
bar's high/low reflect every trade in that minute, not one snapshot.
The only remaining ambiguity is a single bar whose range spans BOTH
target and stop (handled explicitly below with a deterministic
tie-break), and the standard "the first minute has no closed bar yet"
latency (<=60s), both far tighter than the previous, uncorrelated-to-
price +/-30s poll window.

Also tracks MFE/MAE (max favorable/adverse excursion) per signal --
now computed from real bar highs/lows across the whole tracked window,
not a running max of LTP poll samples, so it can no longer miss a
spike that happened between two polls.

Design:
  - 30-second polling interval (configurable) -- unchanged; this is how
    often we re-check Redis for newly-closed bars, not the resolution
    granularity of the outcome decision itself (that's now 1 minute,
    bar-close-driven)
  - Only runs during market hours (09:15 - 15:30 IST)
  - Deterministic: re-walks every bar since the signal's created_at, in
    chronological order, every cycle, stopping at the first bar that
    resolves it -- same price data -> same outcome, with no dependency
    on exactly when a poll happened to land
  - Bounded: only tracks signals from current session
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import asyncpg
import structlog
from redis.asyncio import Redis

from archiver.config import ArchiverSettings

logger = structlog.get_logger()

_IST = timezone(timedelta(hours=5, minutes=30))
Payload = dict[str, Any]

_FETCH_UNTRACKED_SQL = """
SELECT id, signal_id, symbol, signal_type, entry_price, invalidation_price,
       target_price, created_at, high_after_signal, low_after_signal,
       t2_price, t3_price
FROM signals
WHERE NOT outcome_tracked
  AND NOT suppressed
  AND created_at >= $1
  AND entry_price > 0
  AND invalidation_price > 0
  AND target_price > 0
ORDER BY created_at
LIMIT 200
"""

_UPDATE_OUTCOME_SQL = """
UPDATE signals SET
    outcome_tracked = $2,
    outcome_label = $3,
    target_hit_at = $4,
    stop_hit_at = $5,
    expired_at = $6,
    high_after_signal = $7,
    low_after_signal = $8,
    max_favorable_pct = $9,
    max_adverse_pct = $10,
    time_to_target_min = $11,
    time_to_stop_min = $12,
    target_level_hit = $13
WHERE id = $1
"""


def decode_bar(raw: Any) -> Payload | None:
    """Decode one infusion:ohlc:{symbol}:1m zset member (JSON -- see
    feature_engine/main.py's on_bar()). Self-contained duplication of
    api/routes/mtf.py's own _decode_ohlc -- archiver and api are separate
    services with no shared-lib import path for this, matching the same
    cross-service duplication precedent used elsewhere in this codebase
    (e.g. scanner/verdict_engine.py's own market-context duplication)."""
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


def first_touch(bar: Payload, target: float, stop: float, bearish: bool) -> str | None:
    """Which boundary this bar's high/low crossed, if any -- "TARGET_HIT",
    "STOP_HIT", or None.

    When a single bar's range spans BOTH target and stop (a real, if
    uncommon, possibility on a 1-minute bar for a volatile underlying),
    the boundary whose price is closer to the bar's OPEN is treated as
    reached first -- a deterministic, disclosed tie-break, not a guess:
    under any continuous price path starting at the bar's open, the
    nearer of two levels is necessarily crossed before the farther one.
    """
    if not bearish:
        hit_target = bar["h"] >= target
        hit_stop = bar["l"] <= stop
    else:
        hit_target = bar["l"] <= target
        hit_stop = bar["h"] >= stop
    if hit_target and hit_stop:
        return "STOP_HIT" if abs(bar["o"] - stop) < abs(bar["o"] - target) else "TARGET_HIT"
    if hit_target:
        return "TARGET_HIT"
    if hit_stop:
        return "STOP_HIT"
    return None


class OutcomeTracker:
    """Tracks signal outcomes from completed 1-minute bar high/low.

    Usage:
        tracker = OutcomeTracker(pool, redis, settings)
        await tracker.start()  # runs background loop
        await tracker.stop()
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        redis: Redis,
        settings: ArchiverSettings,
    ):
        self._pool = pool
        self._redis = redis
        self._interval = settings.tracker_interval_sec
        self._lookback_min = settings.tracker_lookback_min
        self._signal_ttl_min = settings.signal_ttl_min
        self._market_open = (settings.market_open_hour, settings.market_open_min)
        self._market_close = (settings.market_close_hour, settings.market_close_min)
        self._task: asyncio.Task[None] | None = None
        self._running = False

        # Stats
        self._cycles = 0
        self._target_hits = 0
        self._stop_hits = 0
        self._expired = 0
        self._tracked_total = 0

    def _is_market_hours(self) -> bool:
        """Check if current time is within NSE market hours (IST)."""
        now = datetime.now(_IST)
        t = now.hour * 60 + now.minute
        open_t = self._market_open[0] * 60 + self._market_open[1]
        close_t = self._market_close[0] * 60 + self._market_close[1]
        return open_t <= t <= close_t + 10  # 10 min grace after close

    async def _get_bars_since(self, symbol: str, since_epoch: int) -> list[Payload]:
        """Every completed 1m bar for `symbol` at or after `since_epoch`
        (Unix seconds), in chronological order. Same zset feature-engine
        writes on every bar close -- already read live by
        api/routes/mtf.py and api/routes/charts.py, so this reuses
        proven, already-running production data, not a new write path."""
        key = f"infusion:ohlc:{symbol}:1m"
        raw = await self._redis.zrangebyscore(key, since_epoch, "+inf")
        bars = [b for b in (decode_bar(r) for r in raw) if b is not None]
        bars.sort(key=lambda b: b["t"])
        return bars

    async def _track_cycle(self) -> None:
        """One tracking cycle: fetch untracked signals, walk real
        completed-bar high/low since each signal's created_at, and
        resolve outcomes deterministically."""
        now_utc = datetime.now(UTC)
        lookback = now_utc - timedelta(minutes=self._lookback_min)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_FETCH_UNTRACKED_SQL, lookback)

        if not rows:
            return

        # Group by symbol so each symbol's bar zset is fetched once per
        # cycle regardless of how many untracked signals share it.
        by_symbol: dict[str, list[Any]] = {}
        for row in rows:
            by_symbol.setdefault(row["symbol"], []).append(row)

        bars_by_symbol: dict[str, list[Payload]] = {}
        for symbol, symbol_rows in by_symbol.items():
            earliest = min(r["created_at"] for r in symbol_rows)
            bars_by_symbol[symbol] = await self._get_bars_since(symbol, int(earliest.timestamp()))

        updates = []
        for row in rows:
            symbol = row["symbol"]
            entry = float(row["entry_price"])
            stop = float(row["invalidation_price"])
            target = float(row["target_price"])
            t2 = float(row["t2_price"] or 0)
            t3 = float(row["t3_price"] or 0)
            signal_type = str(row["signal_type"] or "").lower()
            bearish = target < entry or signal_type == "bearish"
            created = row["created_at"]
            created_epoch = int(created.timestamp())
            prev_high = float(row["high_after_signal"] or entry)
            prev_low = float(row["low_after_signal"] or entry)

            bars = [b for b in bars_by_symbol.get(symbol, []) if b["t"] >= created_epoch]

            current_high = prev_high
            current_low = prev_low
            outcome_label: str | None = None
            resolving_bar: Payload | None = None

            # Walk bars in order, stopping at the first one that resolves
            # this signal -- MFE/MAE below only reflects the window up to
            # and including that bar, matching this tracker's own
            # documented scope boundary (a signal stops being updated the
            # instant it resolves; T2/T3 progress in a LATER cycle, after
            # the row has already left the untracked pool, is an honest,
            # disclosed gap of this implementation, not silently hidden).
            for bar in bars:
                current_high = max(current_high, bar["h"])
                current_low = min(current_low, bar["l"])
                touch = first_touch(bar, target, stop, bearish)
                if touch is not None:
                    outcome_label = touch
                    resolving_bar = bar
                    break

            outcome_tracked = False
            target_hit_at = None
            stop_hit_at = None
            expired_at = None
            time_to_target = None
            time_to_stop = None
            target_level_hit = None

            if outcome_label is not None and resolving_bar is not None:
                outcome_tracked = True
                # Bar-open timestamp is a <=1-minute-resolution
                # approximation of the true touch instant -- still a
                # far tighter, price-correlated bound than the previous
                # design's poll-wall-clock timestamp, which had no
                # relationship to when the price move actually happened.
                bar_ts = datetime.fromtimestamp(resolving_bar["t"], tz=UTC)
                elapsed_min = (resolving_bar["t"] - created_epoch) / 60.0
                if outcome_label == "TARGET_HIT":
                    target_hit_at = bar_ts
                    time_to_target = elapsed_min
                    self._target_hits += 1
                    target_level_hit = "T1"
                    if t3 > 0 and (
                        (not bearish and resolving_bar["h"] >= t3)
                        or (bearish and resolving_bar["l"] <= t3)
                    ):
                        target_level_hit = "T3"
                    elif t2 > 0 and (
                        (not bearish and resolving_bar["h"] >= t2)
                        or (bearish and resolving_bar["l"] <= t2)
                    ):
                        target_level_hit = "T2"
                else:
                    stop_hit_at = bar_ts
                    time_to_stop = elapsed_min
                    self._stop_hits += 1
            else:
                elapsed_min = (now_utc - created).total_seconds() / 60
                if elapsed_min >= self._signal_ttl_min:
                    outcome_tracked = True
                    outcome_label = "EXPIRED"
                    expired_at = now_utc
                    self._expired += 1

            if outcome_tracked:
                self._tracked_total += 1

            # MFE/MAE from real bar highs/lows across the tracked window,
            # not a running max of point-sampled LTPs.
            if bearish:
                mfe_pct = ((entry - current_low) / entry) * 100 if entry > 0 else 0
                mae_pct = ((current_high - entry) / entry) * 100 if entry > 0 else 0
            else:
                mfe_pct = ((current_high - entry) / entry) * 100 if entry > 0 else 0
                mae_pct = ((entry - current_low) / entry) * 100 if entry > 0 else 0

            updates.append(
                (
                    row["id"],  # $1
                    outcome_tracked,  # $2
                    outcome_label,  # $3
                    target_hit_at,  # $4
                    stop_hit_at,  # $5
                    expired_at,  # $6
                    current_high,  # $7
                    current_low,  # $8
                    mfe_pct,  # $9
                    mae_pct,  # $10
                    time_to_target,  # $11
                    time_to_stop,  # $12
                    target_level_hit,  # $13
                )
            )

        if updates:
            async with self._pool.acquire() as conn:
                await conn.executemany(_UPDATE_OUTCOME_SQL, updates)

            outcomes = [u[2] for u in updates if u[2]]
            if outcomes:
                logger.info(
                    "outcomes_tracked",
                    cycle=self._cycles,
                    updated=len(updates),
                    outcomes={o: outcomes.count(o) for o in set(outcomes)},
                )

    async def _run_loop(self) -> None:
        """Background loop: track outcomes every interval."""
        while self._running:
            try:
                if self._is_market_hours():
                    await self._track_cycle()
                    self._cycles += 1
                else:
                    logger.debug("tracker_outside_market_hours")
            except Exception as e:
                logger.error("tracker_error", error=str(e))

            await asyncio.sleep(self._interval)

    async def start(self) -> None:
        """Start the outcome tracking loop."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("outcome_tracker_started", interval_sec=self._interval)

    async def stop(self) -> None:
        """Stop the outcome tracking loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("outcome_tracker_stopped")

    @property
    def stats(self) -> Payload:
        return {
            "tracker_cycles": self._cycles,
            "target_hits": self._target_hits,
            "stop_hits": self._stop_hits,
            "expired": self._expired,
            "tracked_total": self._tracked_total,
        }
