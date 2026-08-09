"""Outcome tracker — 30-second sampling of signal outcomes.

Reads untracked active signals from Postgres, checks current LTP
from Redis hot state, and updates outcome status:
  - TARGET_HIT: LTP reached target_price
  - STOP_HIT: LTP reached invalidation_price
  - EXPIRED: signal TTL elapsed without hitting either

Also tracks MFE/MAE (max favorable/adverse excursion) per signal.

Design:
  - 30-second polling interval (configurable)
  - Only runs during market hours (09:15 - 15:30 IST)
  - Deterministic: same price data → same outcome classification
  - Bounded: only tracks signals from current session
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import asyncpg
import structlog
from redis.asyncio import Redis

from archiver.config import ArchiverSettings
from infusion_streams.constants import KEY_TICK_PREFIX

logger = structlog.get_logger()

_IST = timezone(timedelta(hours=5, minutes=30))

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


class OutcomeTracker:
    """Tracks signal outcomes by sampling Redis LTP every interval.

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
        self._task: asyncio.Task | None = None
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

    async def _get_ltp(self, symbol: str) -> float | None:
        """Get current LTP from Redis hot state."""
        key = f"{KEY_TICK_PREFIX}{symbol}"
        raw = await self._redis.hget(key, "ltp")
        if raw is None:
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None

    async def _track_cycle(self) -> None:
        """One tracking cycle: fetch untracked signals, sample LTP, update outcomes."""
        now_utc = datetime.now(timezone.utc)
        lookback = now_utc - timedelta(minutes=self._lookback_min)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_FETCH_UNTRACKED_SQL, lookback)

        if not rows:
            return

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
            prev_high = float(row["high_after_signal"] or entry)
            prev_low = float(row["low_after_signal"] or entry)

            ltp = await self._get_ltp(symbol)
            if ltp is None:
                continue

            # Update high/low tracking
            current_high = max(prev_high, ltp)
            current_low = min(prev_low, ltp)

            # MFE/MAE
            if bearish:
                mfe_pct = ((entry - current_low) / entry) * 100 if entry > 0 else 0
                mae_pct = ((current_high - entry) / entry) * 100 if entry > 0 else 0
            else:
                mfe_pct = ((current_high - entry) / entry) * 100 if entry > 0 else 0
                mae_pct = ((entry - current_low) / entry) * 100 if entry > 0 else 0

            # Time elapsed
            elapsed = now_utc - created
            elapsed_min = elapsed.total_seconds() / 60

            # Outcome determination
            outcome_tracked = False
            outcome_label = None
            target_hit_at = None
            stop_hit_at = None
            expired_at = None
            time_to_target = None
            time_to_stop = None
            target_level_hit = None

            if (not bearish and ltp >= target) or (bearish and ltp <= target):
                outcome_tracked = True
                outcome_label = "TARGET_HIT"
                target_hit_at = now_utc
                time_to_target = elapsed_min
                self._target_hits += 1
                # Phase N8: T1/T2/T3 level detection. Scoped to this exact
                # moment -- the highest level ALREADY reached by the same
                # ltp reading that just confirmed T1, not continued
                # monitoring afterward (this row leaves the untracked pool
                # the instant outcome_tracked flips true, same as it always
                # has -- T2/T3 progression that happens in a LATER 30s
                # cycle, after this row is no longer selected by
                # _FETCH_UNTRACKED_SQL, is an honest, documented gap of
                # this scoped implementation, not silently pretended away).
                # A genuine confluence-cluster fib target (t3 > t2 > t1
                # for bullish, t3 < t2 < t1 for bearish) is assumed since
                # that's how compute_fib_targets()/practical_option_targets()
                # both construct them; t2/t3 of 0 means "not computed for
                # this signal" and is treated as "no further level to check".
                target_level_hit = "T1"
                if t3 > 0 and ((not bearish and ltp >= t3) or (bearish and ltp <= t3)):
                    target_level_hit = "T3"
                elif t2 > 0 and ((not bearish and ltp >= t2) or (bearish and ltp <= t2)):
                    target_level_hit = "T2"
            elif (not bearish and ltp <= stop) or (bearish and ltp >= stop):
                outcome_tracked = True
                outcome_label = "STOP_HIT"
                stop_hit_at = now_utc
                time_to_stop = elapsed_min
                self._stop_hits += 1
            elif elapsed_min >= self._signal_ttl_min:
                outcome_tracked = True
                outcome_label = "EXPIRED"
                expired_at = now_utc
                self._expired += 1

            if outcome_tracked:
                self._tracked_total += 1

            updates.append((
                row["id"],              # $1
                outcome_tracked,        # $2
                outcome_label,          # $3
                target_hit_at,          # $4
                stop_hit_at,            # $5
                expired_at,             # $6
                current_high,           # $7
                current_low,            # $8
                mfe_pct,                # $9
                mae_pct,                # $10
                time_to_target,         # $11
                time_to_stop,           # $12
                target_level_hit,       # $13
            ))

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
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("outcome_tracker_stopped")

    @property
    def stats(self) -> dict:
        return {
            "tracker_cycles": self._cycles,
            "target_hits": self._target_hits,
            "stop_hits": self._stop_hits,
            "expired": self._expired,
            "tracked_total": self._tracked_total,
        }
