"""Normalizer service — resolve, dedup, throttle, transform, publish.

Consumes:  infusion:stream:tick:raw
Publishes: infusion:stream:tick:normalized
Hot state: infusion:tick:{symbol}
"""

import asyncio

import structlog
from redis.asyncio import Redis

from normalizer.config import NormalizerSettings
from normalizer.resolver import SymbolResolver
from normalizer.throttler import TierThrottler
from normalizer.dedup import TickDedup
from normalizer.transformer import transform
from infusion_common.logging import setup_logging
from infusion_common.lifecycle import ServiceLifecycle
from infusion_common.health import HealthReporter
from infusion_models.events import EventType
from infusion_streams.consumer import StreamConsumer
from infusion_streams.producer import StreamProducer
from infusion_streams.constants import (
    STREAM_TICK_RAW, STREAM_TICK_NORMALIZED,
    CG_NORMALIZER, MAXLEN_TICK_NORMALIZED,
    KEY_TICK_PREFIX,
)

logger = structlog.get_logger()


async def main():
    config = NormalizerSettings()
    setup_logging(config.service_name, config.log_level, config.log_format)
    logger.info("normalizer_starting")

    redis = Redis.from_url(config.redis_url, decode_responses=False)
    await redis.ping()
    logger.info("redis_connected")

    lifecycle = ServiceLifecycle(config.service_name)

    # Components
    resolver = SymbolResolver()
    await resolver.load(redis)

    throttler = TierThrottler(
        tier2_ms=config.tier2_min_interval_ms,
        tier3_ms=config.tier3_min_interval_ms,
    )
    dedup = TickDedup(ring_size=config.dedup_ring_size)

    # Stream I/O
    consumer = StreamConsumer(
        redis, STREAM_TICK_RAW, CG_NORMALIZER, "normalizer-1",
        batch_size=config.batch_size, block_ms=config.block_ms,
    )
    await consumer.ensure_group()

    producer = StreamProducer(redis, STREAM_TICK_NORMALIZED, MAXLEN_TICK_NORMALIZED)

    # Health
    health = HealthReporter(redis, config.service_name)
    health.set_details_fn(lambda: {
        "symbols_loaded": resolver.count,
        "throttled_dropped": throttler.dropped_count,
        "duplicates_dropped": dedup.duplicate_count,
        "consumed": consumer.stats,
        "published": producer.published_count,
    })
    await health.start()
    lifecycle.register_cleanup(health.stop)
    lifecycle.register_cleanup(redis.aclose)

    # Main processing loop
    logger.info("normalizer_consuming", stream=STREAM_TICK_RAW)
    resolve_misses = 0

    async for event_type, version, rx_us, payload, ack in consumer.consume():
        if not lifecycle.should_run:
            break

        # 1. Resolve symbol
        instrument_key = payload.get("instrument_key", "")
        info = resolver.resolve(instrument_key)
        if info is None:
            resolve_misses += 1
            if resolve_misses % 100 == 1:
                logger.warning("symbol_resolve_miss", key=instrument_key, total=resolve_misses)
            await ack()
            continue

        # 2. Dedup
        if dedup.is_duplicate(info.symbol, payload.get("exchange_timestamp_ms", 0)):
            await ack()
            continue

        # 3. Throttle
        if not throttler.should_forward(info.symbol, info.tier):
            await ack()
            continue

        # 4. Transform
        normalized = transform(payload, info)

        # 5. Publish to stream
        await producer.publish(
            event_type=EventType.NORMALIZED_TICK,
            payload=normalized.model_dump(),
            received_at_us=rx_us,
        )

        # 6. Update hot state (latest tick per symbol)
        await redis.hset(
            f"{KEY_TICK_PREFIX}{info.symbol}",
            mapping={
                "ltp": str(normalized.ltp),
                "volume": str(normalized.volume),
                "change_pct": str(
                    round((normalized.ltp - normalized.close) / normalized.close * 100, 2)
                    if normalized.close > 0 else 0
                ),
                "exchange_ts": str(normalized.exchange_timestamp_ms),
                "updated_at": str(normalized.normalized_at_us),
            },
        )

        await ack()

    await lifecycle.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
