"""Scheduler service: broker-history bootstrap and periodic refresh."""

import asyncio
import contextlib
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

# EBIE EB-14: weekly promotion-review snapshot, per docs/EBIE-
# IMPLEMENTATION-ANSWERS.md Q5.3 ("Evaluate weekly; promote manually at
# first"). This ONLY persists a durable record of EB-13's shadow-
# validation report (api/promotion_review.py) -- it never changes which
# model drives a live signal; see that module's own docstring for the
# full, deliberately-scoped rationale.
PROMOTION_REVIEW_INTERVAL_SEC = 7 * 24 * 3600
PROMOTION_REVIEW_RETRY_SEC = 600

# Phase 13.4: option-premium capture for net-of-cost walk-forward
# validation. Short interval on purpose -- the endpoint's own LIMIT 20
# per pass bounds each call, and live daily published-signal volume is
# low (confirmed ~11-15/day from the real archive checked this session),
# so running every 60s keeps entry premium captured close to fire-time
# without meaningfully loading the api/Upstox side.
PREMIUM_CAPTURE_INTERVAL_SEC = 60

# VIX-tiered position-size multiplier. Short interval (unlike Kelly/
# optimizer-proposal's daily cadence) since India VIX genuinely moves
# during a session, unlike a win-rate averaged over months -- 5 min keeps
# the cached tier reasonably current without hammering Upstox (the
# underlying REST call is also already de-duped by _upstox_index_quotes'
# own 5s in-process cache in api).
VIX_MULTIPLIER_INTERVAL_SEC = 5 * 60
VIX_MULTIPLIER_RETRY_SEC = 60

# Phase 13.10: half-Kelly position-sizing stat per strategy. Daily cadence
# matches optimizer_proposal_loop's -- the underlying win-rate/avg-win-R
# numbers move slowly (they're averaged over 180 days of archived
# outcomes), so there's nothing to gain from checking more often.
KELLY_SIZING_INTERVAL_SEC = 24 * 3600
KELLY_SIZING_RETRY_SEC = 120

# ML classifier retrain. Daily cadence, same reasoning as Kelly/optimizer-
# proposal -- the underlying archive grows by ~11-15 decided signals/day
# (confirmed this session), nowhere near enough in a single day to move a
# model trained on 8,000+ rows meaningfully. The route itself takes
# ~15-20s (real, measured against the live archive) since it's a plain-
# Python logistic-regression training pass -- api/ml_classifier.py runs
# that inside asyncio.to_thread so it never blocks api's event loop, and a
# generous 90s timeout here gives it headroom over aiohttp/network
# overhead on top of that real cost.
ML_CLASSIFIER_INTERVAL_SEC = 24 * 3600
ML_CLASSIFIER_RETRY_SEC = 120


async def run_optimizer_proposal_sweep() -> dict:
    """Calls the api service's walk-forward-vs-live-config comparison. This
    only ever causes api to WRITE A PROPOSAL to Redis (infusion:optimizer:
    proposal) for human review -- never changes scanner's actual config.
    See compute_optimizer_proposal() in api/routes/backtest.py for the full
    "propose only, never auto-apply" rationale.
    """
    url = f"{API_BASE_URL}/api/backtest/optimizer-proposal"
    async with (
        aiohttp.ClientSession() as session,
        session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp,
    ):
        resp.raise_for_status()
        return await resp.json()


async def run_promotion_review_sweep() -> dict:
    """Calls the api service's EB-14 promotion-review endpoint. This only
    ever causes api to compute the current EB-13 shadow-validation
    report and INSERT one durable snapshot row into
    ebie_promotion_reviews -- never changes which model (scanner's
    existing conviction_score/suppression gate, still champion) drives
    a live signal. See api/promotion_review.py for the full rationale.
    """
    url = f"{API_BASE_URL}/api/ebie/promotion-review"
    async with (
        aiohttp.ClientSession() as session,
        session.post(url, timeout=aiohttp.ClientTimeout(total=60)) as resp,
    ):
        resp.raise_for_status()
        return await resp.json()


async def run_kelly_sizing_sweep() -> dict:
    """Calls the api service's half-Kelly sizing computation. This only
    ever writes informational stats to Redis (infusion:kelly:{strategy_id})
    for scanner/engine.py's _recommended_lots() to read alongside the
    existing ATR-scaled sizing -- never changes scanner's actual live
    config or auto-applies a size. See compute_kelly_sizing() in
    api/routes/backtest.py."""
    url = f"{API_BASE_URL}/api/backtest/kelly-sizing"
    async with (
        aiohttp.ClientSession() as session,
        session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp,
    ):
        resp.raise_for_status()
        return await resp.json()


async def run_ml_classifier_sweep() -> dict:
    """Calls the api service's ML classifier (re)train. This only ever
    writes the trained weights + held-out test metrics to Redis
    (infusion:ml-classifier:model) for scanner/ml_score.py to read and
    api/routes/backtest.py's /latest route to serve to the dashboard --
    never changes scanner's live config, suppression, or the conviction
    score itself. See api/ml_classifier.py's module docstring."""
    url = f"{API_BASE_URL}/api/backtest/ml-classifier"
    async with (
        aiohttp.ClientSession() as session,
        session.get(url, timeout=aiohttp.ClientTimeout(total=90)) as resp,
    ):
        resp.raise_for_status()
        return await resp.json()


async def run_vix_multiplier_sweep() -> dict:
    """Calls the api service's India VIX-tiered position-size read. Only
    ever writes an informational tier/multiplier to Redis
    (infusion:vix:multiplier) for scanner/engine.py's _recommended_lots()
    to read alongside Kelly and the ATR-scaled sizing -- never changes
    scanner's live config or auto-applies a size. See
    api/vix_sizing.py and compute_vix_multiplier() in api/routes/market.py."""
    url = f"{API_BASE_URL}/api/market/vix-multiplier"
    async with (
        aiohttp.ClientSession() as session,
        session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp,
    ):
        resp.raise_for_status()
        return await resp.json()


async def run_premium_capture() -> dict:
    """Calls the api service's option-premium capture pass for signals
    that fired/resolved recently and don't have real premium data yet.
    See capture_missing_premiums() in api/routes/backtest.py -- this
    only ever fills in entry_premium_ask/entry_premium_bid/
    exit_premium_bid/option_instrument_key on the signals table, never
    touches scanner's live config or any trading decision."""
    url = f"{API_BASE_URL}/api/backtest/capture-premiums"
    async with (
        aiohttp.ClientSession() as session,
        session.post(url, timeout=aiohttp.ClientTimeout(total=60)) as resp,
    ):
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
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(lifecycle.shutdown_event.wait(), timeout=delay)

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
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    lifecycle.shutdown_event.wait(),
                    timeout=delay,
                )

    # EBIE EB-14: weekly promotion-review snapshot -- independent of
    # every loop above. Runs once shortly after startup too (same
    # reasoning as optimizer_proposal_loop above) so a first review
    # snapshot exists immediately rather than waiting a full week.
    async def promotion_review_loop():
        while not lifecycle.shutdown_event.is_set():
            delay = PROMOTION_REVIEW_INTERVAL_SEC
            try:
                result = await run_promotion_review_sweep()
                logger.info(
                    "promotion_review_sweep",
                    readiness=result.get("readiness"),
                    review_id=result.get("review_id"),
                )
            except Exception as exc:
                logger.warning("promotion_review_sweep_failed", error=str(exc))
                delay = PROMOTION_REVIEW_RETRY_SEC
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    lifecycle.shutdown_event.wait(),
                    timeout=delay,
                )

    # Phase 13.4: short-interval premium capture, independent of both
    # loops above. Failures just mean "try again in 60s" -- no retry
    # backoff needed since the interval is already short and each pass
    # is cheap/bounded (LIMIT 20 per query inside capture_missing_premiums()).
    async def premium_capture_loop():
        while not lifecycle.shutdown_event.is_set():
            try:
                result = await run_premium_capture()
                logger.info(
                    "premium_capture_sweep",
                    entry_captured=result.get("entry_captured"),
                    entry_attempted=result.get("entry_attempted"),
                    exit_captured=result.get("exit_captured"),
                    exit_attempted=result.get("exit_attempted"),
                )
            except Exception as exc:
                logger.warning("premium_capture_sweep_failed", error=str(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    lifecycle.shutdown_event.wait(),
                    timeout=PREMIUM_CAPTURE_INTERVAL_SEC,
                )

    # Phase 13.10: once-daily half-Kelly sizing sweep, same shape as
    # optimizer_proposal_loop. Runs once at startup too so the Redis cache
    # is populated without waiting a full day on a fresh deploy.
    async def kelly_sizing_loop():
        while not lifecycle.shutdown_event.is_set():
            delay = KELLY_SIZING_INTERVAL_SEC
            try:
                result = await run_kelly_sizing_sweep()
                logger.info(
                    "kelly_sizing_sweep",
                    available=result.get("available"),
                    strategies=list((result.get("strategies") or {}).keys()),
                )
            except Exception as exc:
                logger.warning("kelly_sizing_sweep_failed", error=str(exc))
                delay = KELLY_SIZING_RETRY_SEC
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    lifecycle.shutdown_event.wait(),
                    timeout=delay,
                )

    # ML classifier: once-daily retrain, same shape as kelly_sizing_loop.
    # Runs once at startup too so the Redis cache is populated without
    # waiting a full day on a fresh deploy.
    async def ml_classifier_loop():
        while not lifecycle.shutdown_event.is_set():
            delay = ML_CLASSIFIER_INTERVAL_SEC
            try:
                result = await run_ml_classifier_sweep()
                logger.info(
                    "ml_classifier_sweep",
                    available=result.get("available"),
                    reliable=result.get("reliable"),
                    n_train=result.get("n_train"),
                    n_test=result.get("n_test"),
                    test_auc=(result.get("test_metrics") or {}).get("auc"),
                    lift_over_score_auc=result.get("lift_over_score_auc"),
                )
            except Exception as exc:
                logger.warning("ml_classifier_sweep_failed", error=str(exc))
                delay = ML_CLASSIFIER_RETRY_SEC
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    lifecycle.shutdown_event.wait(),
                    timeout=delay,
                )

    # VIX-tiered position-size multiplier: frequent (5 min) sweep, same
    # shape as kelly_sizing_loop otherwise. Runs once at startup too.
    async def vix_multiplier_loop():
        while not lifecycle.shutdown_event.is_set():
            delay = VIX_MULTIPLIER_INTERVAL_SEC
            try:
                result = await run_vix_multiplier_sweep()
                logger.info(
                    "vix_multiplier_sweep",
                    available=result.get("available"),
                    vix_level=result.get("vix_level"),
                    vix_tier=result.get("vix_tier"),
                )
            except Exception as exc:
                logger.warning("vix_multiplier_sweep_failed", error=str(exc))
                delay = VIX_MULTIPLIER_RETRY_SEC
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    lifecycle.shutdown_event.wait(),
                    timeout=delay,
                )

    async def combined_loop():
        await asyncio.gather(
            main_loop(),
            optimizer_proposal_loop(),
            premium_capture_loop(),
            kelly_sizing_loop(),
            ml_classifier_loop(),
            vix_multiplier_loop(),
            promotion_review_loop(),
        )

    await lifecycle.run_until_shutdown(combined_loop)


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
