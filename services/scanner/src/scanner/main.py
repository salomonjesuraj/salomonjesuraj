"""Scanner service — consumes feature stream, produces signals.

Pipeline:
  feature:computed → scanner engine → scan:signals / scan:suppressed

Startup sequence:
  1. Load config
  2. Connect Redis
  3. Load symbol→sector map from infusion:symbols
  4. Register strategies
  5. Create consumer group
  6. Start health reporter
  7. Start cleanup timer
  8. Consume feature stream and evaluate
"""

import asyncio

import msgpack
import redis.asyncio as aioredis
import structlog

from scanner.config import ScannerSettings
from scanner.engine import ScannerEngine
from scanner.strategies import register_strategy
from scanner.strategies.options_first_hybrid import OptionsFirstHybrid
from scanner.strategies.vol_vwap_breakout import VolVwapBreakout

from infusion_common.logging import setup_logging
from infusion_common.health import HealthReporter
from infusion_common.lifecycle import ServiceLifecycle
from infusion_streams.consumer import StreamConsumer
from infusion_streams.constants import (
    STREAM_FEATURE_COMPUTED,
    CG_SCANNER,
    KEY_SYMBOLS,
)

logger = structlog.get_logger()


async def load_symbol_sectors(redis: aioredis.Redis) -> dict[str, str]:
    """Load symbol→sector_id mapping from infusion:symbols hash."""
    symbol_sectors: dict[str, str] = {}
    raw = await redis.hgetall(KEY_SYMBOLS)
    for _instrument_key, meta_raw in raw.items():
        try:
            if isinstance(meta_raw, bytes):
                meta = msgpack.unpackb(meta_raw, raw=False)
            else:
                meta = meta_raw
            symbol = meta.get("symbol", "")
            sector = meta.get("sector_id", meta.get("sector", "UNCATEGORIZED"))
            if symbol:
                symbol_sectors[symbol] = sector
        except Exception:
            continue
    return symbol_sectors


async def load_symbol_lot_sizes(redis: aioredis.Redis) -> dict[str, int]:
    """Load symbol→lot_size mapping from infusion:symbols hash, for the
    signal-time position-sizing estimate (see scanner/engine.py
    _recommended_lots). Same hash/scan as load_symbol_sectors, kept as a
    separate pass so a sector-map-only caller isn't forced to pay for it."""
    symbol_lot_sizes: dict[str, int] = {}
    raw = await redis.hgetall(KEY_SYMBOLS)
    for _instrument_key, meta_raw in raw.items():
        try:
            if isinstance(meta_raw, bytes):
                meta = msgpack.unpackb(meta_raw, raw=False)
            else:
                meta = meta_raw
            symbol = meta.get("symbol", "")
            lot_size = int(meta.get("lot_size") or 1)
            if symbol and lot_size > 0:
                symbol_lot_sizes[symbol] = lot_size
        except Exception:
            continue
    return symbol_lot_sizes


async def run() -> None:
    settings = ScannerSettings()
    setup_logging(settings.service_name, settings.log_level, settings.log_format)

    logger.info("scanner_starting")

    # Connect Redis (decode_responses=False for binary stream data)
    r = aioredis.from_url(settings.redis_url, decode_responses=False)
    await r.ping()
    logger.info("redis_connected", url=settings.redis_url)

    # Load symbol→sector mapping
    symbol_sectors = await load_symbol_sectors(r)
    logger.info("symbols_loaded", count=len(symbol_sectors), sectors=list(set(symbol_sectors.values())))

    # Load symbol→lot_size mapping (for signal-time position-size estimate)
    symbol_lot_sizes = await load_symbol_lot_sizes(r)
    logger.info("symbol_lot_sizes_loaded", count=len(symbol_lot_sizes))

    # Register strategies
    register_strategy(VolVwapBreakout(settings))
    register_strategy(OptionsFirstHybrid(settings))
    logger.info(
        "strategies_registered",
        count=2,
        strategies=["vol_vwap_breakout", "options_first_hybrid"],
    )

    # Create scanner engine
    engine = ScannerEngine(r, settings, symbol_sectors, symbol_lot_sizes)
    await engine.sector.startup()

    # Create consumer
    consumer = StreamConsumer(
        redis=r,
        stream=STREAM_FEATURE_COMPUTED,
        group=CG_SCANNER,
        consumer_name=settings.scanner_consumer_name,
        batch_size=settings.scanner_batch_size,
        block_ms=settings.scanner_block_ms,
    )
    await consumer.ensure_group()
    logger.info(
        "scanner_consuming",
        stream=STREAM_FEATURE_COMPUTED,
        group=CG_SCANNER,
    )

    # Health reporter
    health = HealthReporter(r, settings.service_name)
    await health.start()

    # Lifecycle
    lifecycle = ServiceLifecycle(settings.service_name)
    lifecycle.on_shutdown(health.stop)
    lifecycle.on_shutdown(r.aclose)

    # Periodic cleanup of expired active signals
    async def cleanup_loop():
        while not lifecycle.shutdown_event.is_set():
            await asyncio.sleep(60)
            try:
                await engine.cleanup_expired()
            except Exception as e:
                logger.warning("cleanup_error", error=str(e))

    # Periodic sector persistence (independent of market updates)
    async def sector_persist_loop():
        while not lifecycle.shutdown_event.is_set():
            await asyncio.sleep(60)
            try:
                engine.sector._recalculate_rankings()
                await engine.sector._persist_all()
            except Exception as e:
                logger.warning("sector_persist_error", error=str(e))

    # Main consume loop
    async def main_loop():
        cleanup_task = asyncio.create_task(cleanup_loop())
        sector_task = asyncio.create_task(sector_persist_loop())
        try:
            async for event_type, version, rx_us, payload, ack in consumer.consume():
                if lifecycle.shutdown_event.is_set():
                    break
                try:
                    await engine.process_feature(payload)
                except Exception as e:
                    logger.error(
                        "feature_processing_error",
                        symbol=payload.get("symbol", "?"),
                        error=str(e),
                    )
                await ack()

                # Log stats periodically
                if engine._evaluations % 500 == 0 and engine._evaluations > 0:
                    logger.info("scanner_stats", **engine.stats)
        finally:
            cleanup_task.cancel()
            sector_task.cancel()
            for t in [cleanup_task, sector_task]:
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    await lifecycle.run_until_shutdown(main_loop)


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
