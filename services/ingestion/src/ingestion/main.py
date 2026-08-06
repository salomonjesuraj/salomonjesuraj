"""Ingestion service — connects to Upstox WS, publishes raw ticks to Redis stream.

Supported brokers:
  INFUSION_BROKER_PRIMARY=mock    → MockAdapter (development only)
  INFUSION_BROKER_PRIMARY=upstox  → UpstoxAdapter

Usage:
    INFUSION_BROKER_PRIMARY=upstox python -m ingestion.main
"""

import asyncio

import structlog
from redis.asyncio import Redis

from ingestion.config import IngestionSettings
from ingestion.supervisor import ConnectionSupervisor
from ingestion.publisher import TickPublisher
from ingestion.adapters.mock import MockAdapter
from ingestion.adapters.upstox import UpstoxAdapter
from infusion_common.logging import setup_logging
from infusion_common.lifecycle import ServiceLifecycle
from infusion_common.health import HealthReporter

logger = structlog.get_logger()


def create_adapter(config: IngestionSettings, redis: Redis):
    """Create broker adapter based on configuration.

    Upstox receives Redis to read access tokens stored by the OAuth callback server.
    """
    match config.broker_primary:
        case "upstox":
            adapter = UpstoxAdapter(config)
            adapter.set_redis(redis)
            return adapter
        case "mock":
            return MockAdapter(config)
        case other:
            raise ValueError(f"Unknown broker: {other}")


async def main():
    config = IngestionSettings()
    setup_logging(config.service_name, config.log_level, config.log_format)

    logger.info(
        "ingestion_starting",
        broker=config.broker_primary,
        mock_rate_hz=config.mock_tick_rate_hz,
    )

    redis = Redis.from_url(config.redis_url, decode_responses=False)
    await redis.ping()
    logger.info("redis_connected", url=config.redis_url)

    lifecycle = ServiceLifecycle(config.service_name)

    # Health reporter
    health = HealthReporter(redis, config.service_name)

    # Publisher
    publisher = TickPublisher(redis)

    # Adapter (pass redis for token lookup)
    adapter = create_adapter(config, redis)

    # Load instrument keys from Redis (populated by nse-scraper)
    instruments = []
    if config.broker_primary == "mock":
        from ingestion.adapters.mock import MOCK_SYMBOLS
        instruments = [s[0] for s in MOCK_SYMBOLS]
    else:
        # Read instrument keys from infusion:symbols hash
        # (populated by nse-scraper service on startup)
        symbol_data = await redis.hgetall("infusion:symbols")
        instruments = [k.decode() for k in symbol_data.keys()] if symbol_data else []

        if not instruments:
            logger.warning(
                "no_instruments_configured",
                hint="Ensure nse-scraper service has started and populated infusion:symbols",
            )
            # Wait and retry — nse-scraper may still be starting
            for attempt in range(5):
                await asyncio.sleep(3)
                symbol_data = await redis.hgetall("infusion:symbols")
                instruments = [k.decode() for k in symbol_data.keys()] if symbol_data else []
                if instruments:
                    logger.info("instruments_found_on_retry", attempt=attempt + 1, count=len(instruments))
                    break

    logger.info(
        "instruments_loaded",
        count=len(instruments),
        broker=config.broker_primary,
    )

    # Supervisor
    supervisor = ConnectionSupervisor(
        adapter=adapter,
        instruments=instruments,
        on_tick=publisher.publish,
        redis=redis,
        reconnect_base=config.reconnect_base_sec,
        reconnect_max=config.reconnect_max_sec,
        jitter_pct=config.reconnect_jitter_pct,
    )

    # Set health details
    health.set_details_fn(lambda: {
        **adapter.health(),
        **publisher.stats,
    })

    # Cleanup
    lifecycle.register_cleanup(health.stop)
    lifecycle.register_cleanup(redis.aclose)

    # Start
    await health.start()

    try:
        await supervisor.run()
    except KeyboardInterrupt:
        pass
    finally:
        supervisor.stop()
        await lifecycle.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
