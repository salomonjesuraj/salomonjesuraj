"""Alerter service — consumes signal stream, delivers via Telegram.

Pipeline:
  scan:signals → delivery gate → format → Telegram → delivery log

Startup sequence:
  1. Load config
  2. Setup logging
  3. Connect Redis (decode_responses=False for binary stream data)
  4. Create consumer group on STREAM_SCAN_SIGNALS / CG_ALERT
  5. Initialize DeliveryGate, MessageFormatter, TelegramClient, AlerterEngine
  6. Start health reporter
  7. Consume signal stream → engine.process_signal(payload)
  8. Log stats every 100 signals processed
  9. Graceful shutdown (close telegram client, redis)
"""

import asyncio
import logging

import redis.asyncio as aioredis
import structlog
from infusion_common.health import HealthReporter
from infusion_common.lifecycle import ServiceLifecycle
from infusion_common.logging import setup_logging
from infusion_streams.constants import CG_ALERT, STREAM_SCAN_SIGNALS
from infusion_streams.consumer import StreamConsumer

from alerter.config import AlerterSettings
from alerter.engine import AlerterEngine
from alerter.formatter import format_signal
from alerter.gate import DeliveryGate
from alerter.telegram import TelegramClient

logger = structlog.get_logger()


async def run() -> None:
    settings = AlerterSettings()
    setup_logging(settings.service_name, settings.log_level, settings.log_format)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger.info("alerter_starting")

    # Connect Redis (decode_responses=False for binary stream data)
    r = aioredis.from_url(settings.redis_url, decode_responses=False)
    await r.ping()
    logger.info("redis_connected", url=settings.redis_url)

    # Create consumer
    consumer = StreamConsumer(
        redis=r,
        stream=STREAM_SCAN_SIGNALS,
        group=CG_ALERT,
        consumer_name=settings.alerter_consumer_name,
        batch_size=settings.alerter_batch_size,
        block_ms=settings.alerter_block_ms,
    )
    await consumer.ensure_group()
    logger.info(
        "alerter_consuming",
        stream=STREAM_SCAN_SIGNALS,
        group=CG_ALERT,
    )

    # Initialize components
    gate = DeliveryGate(r, settings)
    telegram_client = TelegramClient(settings.telegram_bot_token, settings)
    engine = AlerterEngine(
        redis=r,
        settings=settings,
        gate=gate,
        formatter_fn=format_signal,
        telegram_client=telegram_client,
    )

    # Health reporter
    health = HealthReporter(r, settings.service_name)
    health.set_details_fn(lambda: engine.stats)
    await health.start()

    # Lifecycle
    lifecycle = ServiceLifecycle(settings.service_name)
    lifecycle.on_shutdown(health.stop)
    lifecycle.on_shutdown(telegram_client.close)
    lifecycle.on_shutdown(r.aclose)

    # Main consume loop
    async def main_loop() -> None:
        async for _event_type, _version, _rx_us, payload, ack in consumer.consume():
            if lifecycle.shutdown_event.is_set():
                break
            try:
                await engine.process_signal(payload)
            except Exception as e:
                logger.error(
                    "signal_processing_error",
                    symbol=payload.get("symbol", "?"),
                    error=str(e),
                )
            await ack()

            # Log stats periodically
            if engine._processed % 100 == 0 and engine._processed > 0:
                logger.info("alerter_stats", **engine.stats)

    await lifecycle.run_until_shutdown(main_loop)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
