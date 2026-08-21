"""Signal writer — batched asyncpg INSERT to Postgres.

Receives signal payloads from the archiver consumer loop and writes
them to the `signals` table in batches. Uses UPSERT on signal_id
for idempotent replay/backfill.

Design:
  - Batched: accumulates up to write_batch_size, then flushes
  - Time-bounded: auto-flushes after write_flush_sec even if batch not full
  - Idempotent: ON CONFLICT (signal_id) DO NOTHING for safe replay
  - Deterministic: session_hour derived from signal timestamp
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta, timezone

import asyncpg
import structlog

from archiver.config import ArchiverSettings

logger = structlog.get_logger()

_IST = timezone(timedelta(hours=5, minutes=30))

# Session hour classification (IST)
_SESSION_BOUNDARIES = [
    (9, 15, 10, 0, "opening"),  # 09:15 - 10:00
    (10, 0, 12, 0, "mid_morning"),  # 10:00 - 12:00
    (12, 0, 14, 0, "midday"),  # 12:00 - 14:00
    (14, 0, 15, 30, "closing"),  # 14:00 - 15:30
]


def _classify_session(created_at_us: int) -> str:
    """Classify signal timestamp into market session hour."""
    if created_at_us <= 0:
        return "unknown"
    dt = datetime.fromtimestamp(created_at_us / 1_000_000, tz=_IST)
    h, m = dt.hour, dt.minute
    t = h * 60 + m
    for sh, sm, eh, em, label in _SESSION_BOUNDARIES:
        if sh * 60 + sm <= t < eh * 60 + em:
            return label
    return "pre_market" if t < 9 * 60 + 15 else "post_market"


_INSERT_SQL = """
INSERT INTO signals (
    signal_id, created_at, symbol, strategy, signal_type,
    conviction_score, conviction_grade, price_at_signal, volume_at_signal,
    features, entry_price, invalidation_price, target_price,
    risk_reward_ratio, sector_id, sector_strength, market_regime,
    pre_breakout_state, suppressed, suppression_reason,
    conditions_met, explanation, sub_scores, session_hour,
    t2_price, t3_price
) VALUES (
    $1::uuid, $2, $3, $4, $5,
    $6, $7, $8, $9,
    $10::jsonb, $11, $12, $13,
    $14, $15, $16, $17,
    $18, $19, $20,
    $21::jsonb, $22::text[], $23::jsonb, $24,
    $25, $26
)
ON CONFLICT (signal_id) DO NOTHING
"""


class SignalWriter:
    """Batched signal writer to Postgres via asyncpg.

    Usage:
        writer = SignalWriter(pool, settings)
        writer.add(payload)  # accumulates
        await writer.flush()  # writes batch
        # or auto-flush when batch is full
    """

    def __init__(self, pool: asyncpg.Pool, settings: ArchiverSettings):
        self._pool = pool
        self._batch_size = settings.write_batch_size
        self._flush_sec = settings.write_flush_sec
        self._buffer: list[dict] = []
        self._last_flush = time.monotonic()
        self._total_written = 0
        self._total_dupes = 0

    def add(self, payload: dict) -> bool:
        """Add a signal payload to the write buffer.

        Returns True if the buffer is full and should be flushed.
        Skips non-archivable events (e.g. recap).
        """
        # Skip non-archivable events (recap, etc.)
        if payload.get("signal_type") in {"recap", "test_alert"}:
            return False

        self._buffer.append(payload)
        return len(self._buffer) >= self._batch_size

    def should_flush(self) -> bool:
        """Check if a time-based flush is needed."""
        if not self._buffer:
            return False
        return (time.monotonic() - self._last_flush) >= self._flush_sec

    async def flush(self) -> int:
        """Write buffered signals to Postgres. Returns count written."""
        if not self._buffer:
            return 0

        batch = self._buffer[:]
        self._buffer.clear()
        self._last_flush = time.monotonic()

        rows = []
        for p in batch:
            signal_id = p.get("signal_id")
            if not signal_id:
                continue

            created_us = p.get("created_at_us", 0)
            created_at = (
                datetime.fromtimestamp(created_us / 1_000_000, tz=UTC)
                if created_us > 0
                else datetime.now(UTC)
            )
            session = _classify_session(created_us)

            rows.append(
                (
                    signal_id,  # $1
                    created_at,  # $2
                    p.get("symbol", ""),  # $3
                    p.get("strategy_id", ""),  # $4
                    p.get("signal_type", ""),  # $5
                    p.get("conviction_score", 0.0),  # $6
                    p.get("conviction_grade", ""),  # $7
                    p.get("price_at_signal", 0.0),  # $8
                    p.get("volume_at_signal", 0),  # $9
                    json.dumps(p.get("features_snapshot", {})),  # $10
                    p.get("entry_price", 0.0),  # $11
                    p.get("invalidation_price", 0.0),  # $12
                    p.get("target_price", 0.0),  # $13
                    p.get("risk_reward_ratio", 0.0),  # $14
                    p.get("sector_id", ""),  # $15
                    p.get("sector_strength", 0.0),  # $16
                    p.get("market_regime", ""),  # $17
                    p.get("pre_breakout_state", ""),  # $18
                    p.get("suppressed", False),  # $19
                    p.get("suppression_reason", ""),  # $20
                    json.dumps(p.get("conditions_met", {})),  # $21
                    p.get("explanation", []),  # $22
                    json.dumps(p.get("sub_scores", {})),  # $23
                    session,  # $24
                    # Phase N8: t2_price/t3_price aren't top-level payload
                    # fields (unlike target_price) -- options_first_hybrid.py
                    # writes them inside features_snapshot (see its "t2_price"/
                    # "t3_price" keys), so pulled from there rather than a
                    # p.get() that would always miss.
                    (p.get("features_snapshot") or {}).get("t2_price", 0.0),  # $25
                    (p.get("features_snapshot") or {}).get("t3_price", 0.0),  # $26
                )
            )

        if not rows:
            return 0

        try:
            async with self._pool.acquire() as conn:
                await conn.executemany(_INSERT_SQL, rows)
            written = len(rows)
            self._total_written += written
            logger.info(
                "signals_archived",
                batch_size=written,
                total=self._total_written,
            )
            return written
        except Exception as e:
            logger.error("archive_write_error", error=str(e), batch_size=len(rows))
            # Re-buffer failed rows for retry on next flush
            self._buffer.extend(batch)
            return 0

    @property
    def stats(self) -> dict:
        return {
            "buffer_size": len(self._buffer),
            "total_written": self._total_written,
            "total_dupes": self._total_dupes,
        }
