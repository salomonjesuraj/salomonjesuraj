"""Archiver service — persists signals to Postgres, tracks outcomes.

Pipeline:
  1. Backfill existing stream history (if enabled, first startup)
  2. Consume STREAM_SCAN_SIGNALS via CG_ARCHIVER → write to Postgres
  3. Consume STREAM_SCAN_SUPPRESSED via CG_ARCHIVER_SUP → write suppressed signals
  4. Run OutcomeTracker background loop (30s sampling)
  5. Daily recap at 15:35 IST → publish to signal stream
  6. Health reporter with archiver + tracker stats

Architecture:
  - Two concurrent consumer tasks (signals + suppressed)
  - Shared writer with batched inserts (thread-safe via single event loop)
  - Fully async: consumer → batch writer → Postgres
  - Isolated from scanner: backpressure does not affect signal pipeline
  - Idempotent: UPSERT on signal_id for safe replay/restart
  - Bounded: batch writes, capped lookback, market-hours-only tracking
"""

import asyncio
from datetime import datetime, timezone, timedelta

import asyncpg
import redis.asyncio as aioredis
import structlog

from archiver.config import ArchiverSettings
from archiver.writer import SignalWriter
from archiver.tracker import OutcomeTracker
from archiver.analytics import SignalAnalytics
from archiver.recap import generate_and_publish_recap

from infusion_common.logging import setup_logging
from infusion_common.health import HealthReporter
from infusion_common.lifecycle import ServiceLifecycle
from infusion_streams.consumer import StreamConsumer
from infusion_streams.constants import (
    STREAM_SCAN_SIGNALS,
    STREAM_SCAN_SUPPRESSED,
    CG_ARCHIVER,
    CG_ARCHIVER_SUP,
    KEY_ARCHIVER_CHECKPOINT,
)
from infusion_streams.codec import decode_event

logger = structlog.get_logger()


async def _backfill(
    r: aioredis.Redis,
    writer: SignalWriter,
    stream: str,
    settings: ArchiverSettings,
) -> int:
    """Backfill existing stream history on first startup.

    Reads from stream start (or last checkpoint) up to current,
    writes all signals to Postgres via the writer.
    """
    checkpoint = await r.hget(KEY_ARCHIVER_CHECKPOINT, stream)
    start_id = "0-0"
    if checkpoint:
        start_id = checkpoint.decode() if isinstance(checkpoint, bytes) else checkpoint
        logger.info("backfill_resuming", stream=stream, from_id=start_id)
    else:
        logger.info("backfill_starting", stream=stream, from_id=start_id)

    total = 0
    last_id = start_id
    while True:
        msgs = await r.xrange(
            stream, min=f"({last_id}", count=settings.backfill_batch_size
        )
        if not msgs:
            break

        for msg_id, fields in msgs:
            mid = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
            raw_data = fields.get(b"data")
            if not raw_data:
                continue
            try:
                _, _, _, _, payload = decode_event(raw_data)
                should_flush = writer.add(payload)
                if should_flush:
                    await writer.flush()
            except Exception as e:
                logger.warning("backfill_decode_error", msg_id=mid, error=str(e))
            last_id = mid

        total += len(msgs)

    # Final flush
    await writer.flush()

    # Save checkpoint
    if last_id != start_id:
        await r.hset(KEY_ARCHIVER_CHECKPOINT, stream, last_id)
        logger.info("backfill_complete", stream=stream, total=total, last_id=last_id)

    return total


async def _consume_stream(
    consumer: StreamConsumer,
    writer: SignalWriter,
    lifecycle: ServiceLifecycle,
    stream_label: str,
) -> None:
    """Consume a single stream and write signals to Postgres."""
    async for event_type, version, rx_us, payload, ack in consumer.consume():
        if lifecycle.shutdown_event.is_set():
            break
        try:
            should_flush = writer.add(payload)
            if should_flush:
                await writer.flush()
        except Exception as e:
            logger.error(
                "archive_error",
                stream=stream_label,
                symbol=payload.get("symbol", "?"),
                error=str(e),
            )
        await ack()

        # Periodic stats
        if writer._total_written > 0 and writer._total_written % 50 == 0:
            logger.info("archiver_stats", **writer.stats)


async def _periodic_flush(writer: SignalWriter, lifecycle: ServiceLifecycle) -> None:
    """Periodic time-based flush for the writer."""
    while not lifecycle.shutdown_event.is_set():
        await asyncio.sleep(2.0)
        if writer.should_flush():
            await writer.flush()


_IST = timezone(timedelta(hours=5, minutes=30))
_RECAP_HOUR = 15
_RECAP_MINUTE = 35


async def _recap_scheduler(
    analytics: SignalAnalytics,
    redis: aioredis.Redis,
    lifecycle: ServiceLifecycle,
) -> None:
    """Background task that triggers daily recap at 15:35 IST.

    Checks every 30 seconds whether the recap time has been reached.
    Publishes at most once per trading day.
    """
    last_recap_date = None

    while not lifecycle.shutdown_event.is_set():
        now_ist = datetime.now(_IST)
        today = now_ist.date()
        target_time = now_ist.replace(
            hour=_RECAP_HOUR, minute=_RECAP_MINUTE, second=0, microsecond=0
        )

        if (
            now_ist >= target_time
            and last_recap_date != today
        ):
            try:
                text = await generate_and_publish_recap(analytics, redis, today)
                last_recap_date = today
                logger.info(
                    "recap_scheduled_complete",
                    trade_date=today.isoformat(),
                    text_length=len(text),
                )
            except Exception as e:
                logger.error("recap_scheduler_error", error=str(e))

        await asyncio.sleep(30)


async def run() -> None:
    settings = ArchiverSettings()
    setup_logging(settings.service_name, settings.log_level, settings.log_format)

    logger.info("archiver_starting")

    # Connect Redis
    r = aioredis.from_url(settings.redis_url, decode_responses=False)
    await r.ping()
    logger.info("redis_connected", url=settings.redis_url)

    # Connect Postgres
    pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=5,
        command_timeout=30,
    )
    logger.info("postgres_connected", url=settings.database_url.split("@")[-1])

    # Initialize writer (shared across both consumers — safe on single event loop)
    writer = SignalWriter(pool, settings)

    # Backfill on first startup
    if settings.backfill_on_startup:
        bf_signals = await _backfill(r, writer, STREAM_SCAN_SIGNALS, settings)
        bf_suppressed = await _backfill(r, writer, STREAM_SCAN_SUPPRESSED, settings)
        logger.info(
            "backfill_summary",
            signals=bf_signals,
            suppressed=bf_suppressed,
        )

    # Create consumers
    consumer_signals = StreamConsumer(
        redis=r,
        stream=STREAM_SCAN_SIGNALS,
        group=CG_ARCHIVER,
        consumer_name=settings.archiver_consumer_name,
        batch_size=settings.archiver_batch_size,
        block_ms=settings.archiver_block_ms,
    )
    await consumer_signals.ensure_group()

    consumer_suppressed = StreamConsumer(
        redis=r,
        stream=STREAM_SCAN_SUPPRESSED,
        group=CG_ARCHIVER_SUP,
        consumer_name=f"{settings.archiver_consumer_name}-sup",
        batch_size=settings.archiver_batch_size,
        block_ms=settings.archiver_block_ms,
    )
    await consumer_suppressed.ensure_group()

    logger.info(
        "archiver_consuming",
        signals_stream=STREAM_SCAN_SIGNALS,
        suppressed_stream=STREAM_SCAN_SUPPRESSED,
    )

    # Outcome tracker
    tracker = OutcomeTracker(pool, r, settings)
    await tracker.start()

    # Analytics engine
    analytics = SignalAnalytics(pool)
    logger.info("analytics_engine_ready")

    # Health reporter
    def _combined_stats():
        return {**writer.stats, **tracker.stats}

    health = HealthReporter(r, settings.service_name)
    health.set_details_fn(_combined_stats)
    await health.start()

    # Lifecycle
    lifecycle = ServiceLifecycle(settings.service_name)
    lifecycle.on_shutdown(tracker.stop)
    lifecycle.on_shutdown(health.stop)
    lifecycle.on_shutdown(pool.close)
    lifecycle.on_shutdown(r.aclose)

    # Main loop — consumers + flush + recap scheduler concurrently
    async def main_loop():
        await asyncio.gather(
            _consume_stream(consumer_signals, writer, lifecycle, "signals"),
            _consume_stream(consumer_suppressed, writer, lifecycle, "suppressed"),
            _periodic_flush(writer, lifecycle),
            _recap_scheduler(analytics, r, lifecycle),
        )

    await lifecycle.run_until_shutdown(main_loop)


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
