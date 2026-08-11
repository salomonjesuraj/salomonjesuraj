"""NSE Scraper service — populates symbol universe and manages OAuth tokens.

Startup sequence:
  1. Load configuration (tier from INFUSION_SYMBOL_UNIVERSE env var)
  2. Connect to Redis
  3. Load symbol universe from JSON config files
  4. Populate infusion:symbols hash in Redis
  5. Start health reporter
  6. Start OAuth callback server (for Upstox token refresh)
  7. Periodic re-check of symbol state

This service does NOT stream market data — that is the ingestion service's
responsibility.  This service provides the SYMBOL REGISTRY that ingestion
and all downstream services consume.

Runtime switching:
  INFUSION_SYMBOL_UNIVERSE=nifty50   → 50 symbols
  INFUSION_SYMBOL_UNIVERSE=nifty100  → 100 symbols
  INFUSION_SYMBOL_UNIVERSE=nifty200  → 200 symbols (default)
  INFUSION_SYMBOL_UNIVERSE=nifty500  → 500 symbols
"""

import asyncio

import redis.asyncio as aioredis
import structlog

from nse_scraper.config import NseScraperSettings
from nse_scraper.delivery import run_delivery_capture
from nse_scraper.loader import (
    load_universe,
    populate_redis,
    get_instrument_keys,
    get_sector_summary,
)
from nse_scraper.oauth import start_oauth_server

from infusion_common.config import InfusionSettings
from infusion_common.logging import setup_logging
from infusion_common.health import HealthReporter
from infusion_common.lifecycle import ServiceLifecycle

logger = structlog.get_logger()


async def store_instrument_map(redis, reverse_map: dict[str, str]) -> None:
    pipe = redis.pipeline()
    pipe.delete("infusion:instrument_map")
    for inst_key, symbol in reverse_map.items():
        pipe.hset("infusion:instrument_map", inst_key, symbol)
    await pipe.execute()
    logger.info("instrument_map_stored", count=len(reverse_map))


async def run() -> None:
    settings = NseScraperSettings()
    setup_logging(settings.service_name, settings.log_level, settings.log_format)

    logger.info(
        "nse_scraper_starting",
        tier=settings.symbol_universe,
        config_dir=settings.symbols_config_dir,
    )

    # Connect Redis
    r = aioredis.from_url(settings.redis_url, decode_responses=False)
    await r.ping()
    logger.info("redis_connected", url=settings.redis_url)

    # Load symbol universe
    symbols = load_universe(
        config_dir=settings.symbols_config_dir,
        tier=settings.symbol_universe,
    )

    # Log sector distribution
    sector_summary = get_sector_summary(symbols)
    logger.info(
        "sector_distribution",
        total_symbols=len(symbols),
        sectors=len(sector_summary),
        distribution=sector_summary,
    )

    # Populate Redis
    count, reverse_map = await populate_redis(r, symbols)
    logger.info(
        "redis_populated",
        symbols=count,
        instrument_keys=len(reverse_map),
    )

    # Store reverse mapping for ingestion service
    # (ingestion reads this to resolve instrument_key → symbol)
    if reverse_map:
        await store_instrument_map(r, reverse_map)

    # Phase 13.5: NSE delivery % capture. Run once at startup (catches up
    # quickly after a redeploy instead of waiting a full interval) then on
    # DELIVERY_CAPTURE_INTERVAL_SEC -- cheap regardless of cadence since
    # run_delivery_capture() no-ops once today's session data is stored.
    try:
        result = await run_delivery_capture(r, set(symbols.keys()))
        logger.info("delivery_capture_startup", **result)
    except Exception as exc:
        logger.warning("delivery_capture_startup_failed", error=str(exc))

    # Health reporter
    health = HealthReporter(r, settings.service_name)
    health.set_details_fn(lambda: {
        "tier": settings.symbol_universe,
        "symbols_loaded": count,
        "sectors": len(sector_summary),
    })
    await health.start()

    # Lifecycle
    lifecycle = ServiceLifecycle(settings.service_name)
    lifecycle.on_shutdown(health.stop)
    lifecycle.on_shutdown(r.aclose)

    # Start OAuth callback server in background
    oauth_task = asyncio.create_task(
        start_oauth_server(r, settings)
    )

    # Main loop: periodic health heartbeat + symbol refresh + delivery capture
    DELIVERY_CAPTURE_INTERVAL_SEC = 3 * 3600  # NSE publishes once/day; this just
                                               # bounds how quickly a fresh
                                               # session's data gets picked up
    async def main_loop():
        refresh_counter = 0
        delivery_counter = 0
        while not lifecycle.shutdown_event.is_set():
            await asyncio.sleep(30)
            refresh_counter += 30
            delivery_counter += 30

            # Periodic symbol re-population (hourly by default)
            if refresh_counter >= settings.symbol_refresh_interval_sec:
                refresh_counter = 0
                try:
                    symbols_refreshed = load_universe(
                        config_dir=settings.symbols_config_dir,
                        tier=settings.symbol_universe,
                    )
                    new_count, new_reverse_map = await populate_redis(r, symbols_refreshed)
                    await store_instrument_map(r, new_reverse_map)
                    logger.info("symbols_refreshed", count=new_count)
                except Exception as e:
                    logger.warning("symbol_refresh_error", error=str(e))

            if delivery_counter >= DELIVERY_CAPTURE_INTERVAL_SEC:
                delivery_counter = 0
                try:
                    result = await run_delivery_capture(r, set(symbols.keys()))
                    logger.info("delivery_capture", **result)
                except Exception as e:
                    logger.warning("delivery_capture_error", error=str(e))

            logger.debug(
                "heartbeat",
                service=settings.service_name,
                symbols=count,
                tier=settings.symbol_universe,
            )

    try:
        await lifecycle.run_until_shutdown(main_loop)
    finally:
        oauth_task.cancel()
        try:
            await oauth_task
        except asyncio.CancelledError:
            pass


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
