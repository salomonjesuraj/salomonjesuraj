"""Scheduler service: broker-history bootstrap and periodic refresh."""

import asyncio

import redis.asyncio as aioredis
import structlog

from infusion_common.config import InfusionSettings
from infusion_common.health import HealthReporter
from infusion_common.lifecycle import ServiceLifecycle
from infusion_common.logging import setup_logging
from scheduler.historical import bootstrap_historical

logger = structlog.get_logger()


async def run() -> None:
    settings = InfusionSettings(service_name="scheduler")
    setup_logging(settings.service_name, settings.log_level, settings.log_format)
    redis = aioredis.from_url(settings.redis_url, decode_responses=False)
    await redis.ping()
    logger.info("redis_connected", url=settings.redis_url)
    health = HealthReporter(redis, settings.service_name)
    await health.start()
    lifecycle = ServiceLifecycle(settings.service_name)
    lifecycle.on_shutdown(health.stop)
    lifecycle.on_shutdown(redis.aclose)

    async def main_loop():
        while not lifecycle.shutdown_event.is_set():
            try:
                result = await bootstrap_historical(redis)
                logger.info("historical_bootstrap", **result)
            except Exception as exc:
                logger.warning("historical_bootstrap_failed", error=str(exc))
            delay = 6 * 3600 if await redis.exists("infusion:symbols") else 300
            try:
                await asyncio.wait_for(lifecycle.shutdown_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    await lifecycle.run_until_shutdown(main_loop)


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
