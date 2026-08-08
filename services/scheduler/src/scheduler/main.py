"""Scheduler service: broker-history bootstrap and periodic refresh."""

import asyncio
import os

import aiohttp
import redis.asyncio as aioredis
import structlog

from infusion_common.config import InfusionSettings
from infusion_common.health import HealthReporter
from infusion_common.lifecycle import ServiceLifecycle
from infusion_common.logging import setup_logging
from scheduler.historical import bootstrap_historical

logger = structlog.get_logger()

# Phase 11: daily nightly-sweep trigger for the self-improving optimizer.
# Not part of InfusionSettings (shared base config for every service) --
# scoped here since only the scheduler needs it, same precedent as api's
# _fetch_full_option_chain being kept separate from the function it's
# adjacent to rather than risking a shared one. Plain env var, no
# INFUSION_ prefix requirement since this isn't an InfusionSettings field.
API_BASE_URL = os.environ.get("INFUSION_API_BASE_URL", "http://api:8000")
OPTIMIZER_PROPOSAL_INTERVAL_SEC = 24 * 3600
OPTIMIZER_PROPOSAL_RETRY_SEC = 120  # short retry on failure (e.g. api not up
                                     # yet on a fresh cold start) instead of
                                     # stranding the sweep for a full day


async def run_optimizer_proposal_sweep() -> dict:
    """Calls the api service's walk-forward-vs-live-config comparison. This
    only ever causes api to WRITE A PROPOSAL to Redis (infusion:optimizer:
    proposal) for human review -- never changes scanner's actual config.
    See compute_optimizer_proposal() in api/routes/backtest.py for the full
    "propose only, never auto-apply" rationale.
    """
    url = f"{API_BASE_URL}/api/backtest/optimizer-proposal"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            resp.raise_for_status()
            return await resp.json()


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

    # Phase 11: once-daily optimizer-proposal sweep, independent of the
    # historical-bootstrap cadence above (different purpose, different
    # timing). Runs once shortly after startup too so a proposal exists
    # without waiting a full day on a fresh deploy.
    async def optimizer_proposal_loop():
        while not lifecycle.shutdown_event.is_set():
            delay = OPTIMIZER_PROPOSAL_INTERVAL_SEC
            try:
                result = await run_optimizer_proposal_sweep()
                logger.info(
                    "optimizer_proposal_sweep",
                    status=result.get("status"),
                    available=result.get("available"),
                )
            except Exception as exc:
                logger.warning("optimizer_proposal_sweep_failed", error=str(exc))
                delay = OPTIMIZER_PROPOSAL_RETRY_SEC
            try:
                await asyncio.wait_for(
                    lifecycle.shutdown_event.wait(),
                    timeout=delay,
                )
            except asyncio.TimeoutError:
                pass

    async def combined_loop():
        await asyncio.gather(main_loop(), optimizer_proposal_loop())

    await lifecycle.run_until_shutdown(combined_loop)


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
