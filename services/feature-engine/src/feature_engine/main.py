"""Feature engine service — consumes normalized ticks, computes features.

Consumes:  infusion:stream:tick:normalized
Publishes: infusion:stream:feature:computed
Hot state: infusion:feature:{symbol}
"""

import asyncio
import json
from typing import Any

import structlog
from infusion_common.health import HealthReporter
from infusion_common.lifecycle import ServiceLifecycle
from infusion_common.logging import setup_logging
from infusion_models.events import EventType
from infusion_streams.constants import (
    CG_FEATURE,
    KEY_FEATURE_PREFIX,
    MAXLEN_FEATURE_COMPUTED,
    STREAM_FEATURE_COMPUTED,
    STREAM_TICK_NORMALIZED,
)
from infusion_streams.consumer import StreamConsumer
from infusion_streams.producer import StreamProducer
from redis.asyncio import Redis

from feature_engine.config import FeatureEngineSettings
from feature_engine.engine import FeatureEngine
from feature_engine.state import OHLCBar

logger = structlog.get_logger()


async def main() -> None:
    config = FeatureEngineSettings()
    setup_logging(config.service_name, config.log_level, config.log_format)
    logger.info("feature_engine_starting")

    redis = Redis.from_url(config.redis_url, decode_responses=False)
    await redis.ping()
    logger.info("redis_connected")

    lifecycle = ServiceLifecycle(config.service_name)

    # Stream I/O
    consumer = StreamConsumer(
        redis,
        STREAM_TICK_NORMALIZED,
        CG_FEATURE,
        "feature-engine-1",
        batch_size=config.consumer_batch_size,
        block_ms=config.consumer_block_ms,
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

    async def load_history(symbol: str) -> list[dict[str, Any]]:
        raw = await redis.zrange(f"infusion:ohlc:{symbol}:history:1m", -60, -1)
        bars: list[dict[str, Any]] = []
        for value in raw:
            try:
                if isinstance(value, bytes | bytearray):
                    decoded = value.decode()
                elif isinstance(value, str):
                    decoded = value
                else:
                    continue
                loaded = json.loads(decoded)
                if isinstance(loaded, dict):
                    bars.append(loaded)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return bars

    engine.set_history_loader(load_history)

    async def load_delivery(symbol: str) -> dict[str, Any] | None:
        raw = await redis.hgetall(f"infusion:nse:delivery:{symbol}")
        if not raw:
            return None
        out: dict[str, str] = {}
        for key, value in raw.items():
            k = key.decode() if isinstance(key, bytes) else key
            v = value.decode() if isinstance(value, bytes) else value
            out[str(k)] = str(v)
        try:
            return {
                "delivery_pct": float(out.get("delivery_pct") or 0.0),
                "avg_delivery_pct_20d": float(out["avg_delivery_pct_20d"])
                if out.get("avg_delivery_pct_20d")
                else None,
                "avg_days": int(out.get("avg_days") or 0),
                "trade_date": out.get("trade_date", ""),
            }
        except ValueError:
            return None

    engine.set_delivery_loader(load_delivery)

    async def on_bar(symbol: str, timeframe: int, bar: OHLCBar) -> None:
        """Persist every completed bar for charting and restart-safe history."""
        key = f"infusion:ohlc:{symbol}:{timeframe}m"
        ts = int(bar.bar_start_ms / 1000)
        payload = json.dumps(
            {
                "t": ts,
                "o": bar.open,
                "h": bar.high,
                "l": bar.low,
                "c": bar.close,
                "v": bar.volume,
            },
            separators=(",", ":"),
        )
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

    # ml_features is free-form and excluded from the hot-state hash below
    # wholesale (it can carry list/dict values a flat str-mapping hash
    # can't represent cleanly), but a small whitelist of Phase 13.5/13.7
    # scalar fields is worth surfacing live in the dashboard's per-symbol
    # feature hash -- api/routes/ticks.py reads this same hash for the
    # live scanner table and Stock Detail panel.
    HOT_STATE_ML_WHITELIST = (
        "vwap_stdev",
        "vwap_sd1_upper",
        "vwap_sd1_lower",
        "vwap_sd2_upper",
        "vwap_sd2_lower",
        "vwap_sd_ready",
        "ha_trend",
        "ha_trend_streak",
        "ha_doji",
        "ha_color_flip",
        "delivery_pct_avg_20d",
        "delivery_avg_days",
        "delivery_trade_date",
        # EBIE EB-2: CLV accumulation/distribution evidence -- see
        # feature_engine/features/accumulation.py.
        "clv_ema",
        "clv_volume_weighted",
        "clv_upper_quartile_rate",
        "clv_lower_quartile_rate",
        "clv_ready",
    )

    async def on_feature(fv: Any) -> None:
        """Callback: publish feature vector to stream + hot state."""
        payload = fv.model_dump()
        await producer.publish(
            event_type=EventType.FEATURE_COMPUTED,
            payload=payload,
            received_at_us=fv.timestamp_us,
        )
        # Update hot state
        mapping = {k: str(v) for k, v in payload.items() if k != "ml_features"}
        ml_features = payload.get("ml_features") or {}
        for key in HOT_STATE_ML_WHITELIST:
            if key in ml_features:
                mapping[key] = str(ml_features[key])
        await redis.hset(f"{KEY_FEATURE_PREFIX}{fv.symbol}", mapping=mapping)

    engine.set_callback(on_feature)
    engine.set_bar_callback(on_bar)

    # Health
    health = HealthReporter(redis, config.service_name)
    health.set_details_fn(
        lambda: {
            **engine.stats,
            "consumed": consumer.stats,
            "published": producer.published_count,
        }
    )
    await health.start()
    lifecycle.register_cleanup(health.stop)
    lifecycle.register_cleanup(redis.aclose)

    # Start flush timer
    timer_task = asyncio.create_task(engine.flush_timer())
    # Pipeline audit fix C3: separate wall-clock timer that force-closes
    # stale bars for quiet/illiquid symbols -- see
    # FeatureEngine.bar_flush_timer()'s own docstring.
    bar_timer_task = asyncio.create_task(engine.bar_flush_timer())

    # Main loop
    logger.info("feature_engine_consuming", stream=STREAM_TICK_NORMALIZED)

    async for _event_type, _version, _rx_us, payload, ack in consumer.consume():
        if not lifecycle.should_run:
            break
        await engine.ingest(payload)
        await ack()

    timer_task.cancel()
    bar_timer_task.cancel()
    await lifecycle.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
