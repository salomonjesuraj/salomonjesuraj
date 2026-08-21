"""Sector Intel service — stub (Phase 1)."""

import asyncio

import redis.asyncio as aioredis
import structlog
from infusion_common.config import InfusionSettings
from infusion_common.health import HealthReporter
from infusion_common.lifecycle import ServiceLifecycle
from infusion_common.logging import setup_logging

logger = structlog.get_logger()


async def run() -> None:
    settings = InfusionSettings()
    setup_logging(settings.service_name, settings.log_level, settings.log_format)
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    await r.ping()
    logger.info("redis_connected", url=settings.redis_url)
    health = HealthReporter(r, settings.service_name)
    await health.start()
    lifecycle = ServiceLifecycle(settings.service_name)
    lifecycle.on_shutdown(health.stop)
    lifecycle.on_shutdown(r.aclose)

    async def main_loop():
        while not lifecycle.shutdown_event.is_set():
            await asyncio.sleep(5)
            logger.debug("heartbeat", service=settings.service_name)

    await lifecycle.run_until_shutdown(main_loop)


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
