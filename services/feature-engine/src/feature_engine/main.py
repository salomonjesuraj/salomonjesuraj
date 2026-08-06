"""Feature engine service — consumes normalized ticks, computes features.

Consumes:  infusion:stream:tick:normalized
Publishes: infusion:stream:feature:computed
Hot state: infusion:feature:{symbol}
"""

import asyncio
import json

import structlog
from redis.asyncio import Redis

from feature_engine.config import FeatureEngineSettings
from feature_engine.engine import FeatureEngine
from infusion_common.logging import setup_logging
from infusion_common.lifecycle import ServiceLifecycle
from infusion_common.health import HealthReporter
from infusion_models.events import EventType
from infusion_streams.consumer import StreamConsumer
from infusion_streams.producer import StreamProducer
from infusion_streams.constants import (
    STREAM_TICK_NORMALIZED, STREAM_FEATURE_COMPUTED,
    CG_FEATURE, MAXLEN_FEATURE_COMPUTED,
    KEY_FEATURE_PREFIX,
)

logger = structlog.get_logger()


async def main():
    config = FeatureEngineSettings()
    setup_logging(config.service_name, config.log_level, config.log_format)
    logger.info("feature_engine_starting")

    redis = Redis.from_url(config.redis_url, decode_responses=False)
    await redis.ping()
    logger.info("redis_connected")

    lifecycle = ServiceLifecycle(config.service_name)

    # Stream I/O
    consumer = StreamConsumer(
        redis, STREAM_TICK_NORMALIZED, CG_FEATURE, "feature-engine-1",
        batch_size=config.consumer_batch_size, block_ms=config.consumer_block_ms,
    )
    await consumer.ensure_group()

    producer = StreamProducer(redis, STREAM_FEATURE_COMPUTED, MAXLEN_FEATURE_COMPUTED)

    # Feature engine
    engine = FeatureEngine(config)

    async def load_volume_profile(symbol: str) -> dict[int, float]:
        raw = await redis.hgetall(f"infusion:volume-profile:{symbol}")
        profile = {}
        for key, value in raw.items():
            try:
                k = key.decode() if isinstance(key, bytes) else key
                v = value.decode() if isinstance(value, bytes) else value
                profile[int(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return profile

    engine.set_volume_profile_loader(load_volume_profile)

    async def load_history(symbol: str) -> list[dict]:
        raw = await redis.zrange(f"infusion:ohlc:{symbol}:history:1m", -60, -1)
        bars = []
        for value in raw:
            try:
                value = value.decode() if isinstance(value, bytes) else value
                bars.append(json.loads(value))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return bars

    engine.set_history_loader(load_history)

    async def on_bar(symbol, timeframe, bar):
        """Persist every completed bar for charting and restart-safe history."""
        key = f"infusion:ohlc:{symbol}:{timeframe}m"
        ts = int(bar.bar_start_ms / 1000)
        payload = json.dumps({
            "t": ts, "o": bar.open, "h": bar.high,
            "l": bar.low, "c": bar.close, "v": bar.volume,
        }, separators=(",", ":"))
        maxlen = {
            1: config.ohlc_1m_max,
            5: config.ohlc_5m_max,
            15: config.ohlc_15m_max,
        }[timeframe]
        pipe = redis.pipeline(transaction=False)
        pipe.zadd(key, {payload: ts})
        pipe.zremrangebyrank(key, 0, -(maxlen + 1))
        pipe.expire(key, 7 * 86400)
        await pipe.execute()

    async def on_feature(fv):
        """Callback: publish feature vector to stream + hot state."""
        payload = fv.model_dump()
        await producer.publish(
            event_type=EventType.FEATURE_COMPUTED,
            payload=payload,
            received_at_us=fv.timestamp_us,
        )
        # Update hot state
        await redis.hset(
            f"{KEY_FEATURE_PREFIX}{fv.symbol}",
            mapping={k: str(v) for k, v in payload.items() if k != "ml_features"},
        )

    engine.set_callback(on_feature)
    engine.set_bar_callback(on_bar)

    # Health
    health = HealthReporter(redis, config.service_name)
    health.set_details_fn(lambda: {
        **engine.stats,
        "consumed": consumer.stats,
        "published": producer.published_count,
    })
    await health.start()
    lifecycle.register_cleanup(health.stop)
    lifecycle.register_cleanup(redis.aclose)

    # Start flush timer
    timer_task = asyncio.create_task(engine.flush_timer())

    # Main loop
    logger.info("feature_engine_consuming", stream=STREAM_TICK_NORMALIZED)

    async for event_type, version, rx_us, payload, ack in consumer.consume():
        if not lifecycle.should_run:
            break
        await engine.ingest(payload)
        await ack()

    timer_task.cancel()
    await lifecycle.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
