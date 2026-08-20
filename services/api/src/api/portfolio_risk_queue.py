"""EBIE EB-11 (increment 2) -- sweep loop caching daily loss budget +
consecutive-losses to Redis, same in-process asyncio shape as every
other queue in this service (futures_queue.py/sentiment_queue.py).
Scanner reads the cached result cheaply (same pattern as VIX
multiplier/Kelly sizing), never queries Postgres directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import structlog

from api.portfolio_risk_daily import compute_daily_loss_budget, compute_consecutive_losses

logger = structlog.get_logger()

SWEEP_INTERVAL_SEC = 60
STATUS_KEY = "infusion:portfolio-risk-queue:status"
DAILY_LOSS_KEY = "infusion:portfolio-risk:daily-loss"
CONSECUTIVE_LOSSES_KEY = "infusion:portfolio-risk:consecutive-losses"
CACHE_TTL_SEC = 10 * 60


async def sweep_once(app) -> dict:
    redis = app.get("redis")
    pool = app.get("pg_pool")
    if not redis or not pool:
        return {"available": False, "reason": "Redis or Postgres pool not available."}

    daily_loss = await compute_daily_loss_budget(pool, redis)
    consecutive = await compute_consecutive_losses(pool)

    await redis.set(DAILY_LOSS_KEY, json.dumps(daily_loss, separators=(",", ":")), ex=CACHE_TTL_SEC)
    await redis.set(CONSECUTIVE_LOSSES_KEY, json.dumps(consecutive, separators=(",", ":")), ex=CACHE_TTL_SEC)

    status = {
        "available": True,
        "daily_loss_available": daily_loss.get("available", False),
        "consecutive_losses_available": consecutive.get("available", False),
    }
    await redis.set(STATUS_KEY, json.dumps(status, separators=(",", ":")), ex=600)
    return status


async def portfolio_risk_loop(app) -> None:
    redis = app.get("redis")
    pool = app.get("pg_pool")
    if not redis or not pool:
        logger.info("portfolio_risk_queue_skipped", reason="redis_or_pg_pool_unavailable")
        return
    logger.info("portfolio_risk_queue_started", interval=SWEEP_INTERVAL_SEC)
    while True:
        with contextlib.suppress(Exception):
            status = await sweep_once(app)
            logger.info("portfolio_risk_sweep", **status)
        await asyncio.sleep(SWEEP_INTERVAL_SEC)
