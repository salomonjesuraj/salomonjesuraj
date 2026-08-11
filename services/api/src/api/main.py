"""API service — aiohttp REST API for Infusion.

Endpoints:
  GET /api/health              - aggregated service health
  GET /api/ticks               - bulk tick data for all symbols
  GET /api/ticks/{symbol}      - latest tick for symbol
  GET /api/ticks/snapshot      - full tick + feature snapshot
  GET /api/symbols             - symbol universe listing
  GET /api/features/{sym}      - latest features for symbol
  GET /api/signals             - active scanner signals
  GET /api/signals/{sym}       - latest signal for symbol
  GET /api/prebreakout         - pre-breakout watchlist
  GET /api/sectors             - sector rankings
  GET /api/regime              - market regime state
  GET /api/chart/{sym}/intraday - intraday 1m OHLC
  GET /api/chart/{sym}/daily    - daily OHLC
  GET /api/analytics/precision - signal precision stats
  GET /api/analytics/...       - grade/sector/session/regime/suppression/outcomes/recap
  GET /api/verify/{sym}        - data integrity: proves chart/signal/scanner/watchlist all from same live source
  GET /health                  - simple liveness check
"""

import asyncio
import contextlib

import asyncpg
import aiohttp
import structlog
from aiohttp import web
from redis.asyncio import Redis

from api.config import APISettings
from api.routes.health import routes as health_routes
from api.routes.ticks import routes as ticks_routes
from api.routes.features import routes as features_routes
from api.routes.scanner import routes as scanner_routes
from api.routes.charts import routes as charts_routes
from api.routes.analytics import routes as analytics_routes
from api.routes.verify import routes as verify_routes
from api.routes.market import routes as market_routes
from api.routes.ai import routes as ai_routes
from api.routes.triggers import routes as trigger_routes
from api.routes.auth import routes as auth_routes
from api.routes.risk import routes as risk_routes
from api.routes.news import routes as news_routes
from api.routes.mtf import routes as mtf_routes
from api.routes.backtest import routes as backtest_routes
from api.routes.journal import routes as journal_routes
from api.routes.execution import routes as execution_routes
from api.routes.safety import routes as safety_routes
from api.routes.events import routes as events_routes
from api.routes.strategy_selector import routes as strategy_selector_routes
from api.ai_advisor import OpenAIAdvisor
from api.option_chain_queue import option_chain_queue_loop
from api.mtf_queue import mtf_queue_loop
from infusion_common.logging import setup_logging
from infusion_common.health import HealthReporter

logger = structlog.get_logger()


async def liveness(request):
    return web.json_response({"status": "ok"})


async def main():
    config = APISettings()
    setup_logging(config.service_name, config.log_level, config.log_format)

    redis = Redis.from_url(config.redis_url, decode_responses=False)
    await redis.ping()

    # Postgres pool for analytics
    pg_pool = None
    if config.database_url:
        pg_pool = await asyncpg.create_pool(
            config.database_url,
            min_size=2,
            max_size=5,
            command_timeout=30,
        )
        logger.info("postgres_connected", url=config.database_url.split("@")[-1])

    health = HealthReporter(redis, config.service_name)
    await health.start()

    app = web.Application()
    app["redis"] = redis
    app["config"] = config
    http_session = aiohttp.ClientSession()
    app["http_session"] = http_session
    app["openai_advisor"] = OpenAIAdvisor(
        api_key=config.openai_api_key,
        model=config.openai_model,
        timeout_sec=config.openai_timeout_sec,
        session=http_session,
    )

    # Analytics engine (if Postgres available)
    if pg_pool:
        from archiver.analytics import SignalAnalytics
        app["analytics"] = SignalAnalytics(pg_pool)
        app["pg_pool"] = pg_pool

    # Routes
    app.router.add_get("/health", liveness)
    app.router.add_routes(health_routes)
    app.router.add_routes(ticks_routes)
    app.router.add_routes(features_routes)
    app.router.add_routes(scanner_routes)
    app.router.add_routes(charts_routes)
    app.router.add_routes(verify_routes)
    app.router.add_routes(market_routes)
    app.router.add_routes(ai_routes)
    app.router.add_routes(trigger_routes)
    app.router.add_routes(auth_routes)
    app.router.add_routes(risk_routes)
    app.router.add_routes(news_routes)
    app.router.add_routes(mtf_routes)
    app.router.add_routes(backtest_routes)
    app.router.add_routes(journal_routes)
    app.router.add_routes(execution_routes)
    app.router.add_routes(safety_routes)
    app.router.add_routes(events_routes)
    app.router.add_routes(strategy_selector_routes)
    if pg_pool:
        app.router.add_routes(analytics_routes)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.api_host, config.api_port)
    await site.start()

    logger.info("api_started", host=config.api_host, port=config.api_port)
    option_queue_task = asyncio.create_task(option_chain_queue_loop(app))
    mtf_queue_task = asyncio.create_task(mtf_queue_loop(app))

    # Run forever
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        option_queue_task.cancel()
        mtf_queue_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await option_queue_task
        with contextlib.suppress(asyncio.CancelledError):
            await mtf_queue_task
        await health.stop()
        await runner.cleanup()
        if pg_pool:
            await pg_pool.close()
        await http_session.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
