"""Sentiment-engine main loop -- EBIE EB-7 (increment 2).

Polls Postgres for news_events rows with sentiment_completed_at IS NULL
(EB-7 increment 1's own pending-work index,
idx_news_events_pending_sentiment), classifies each in small batches
(event taxonomy, FinBERT direction/confidence, relevance, novelty,
source_quality), writes one sentiment_scores row per article, then
marks the source row's sentiment_completed_at.

A separate service boundary from `api`, per the authorized Q4.2
decision -- Transformers/PyTorch dependencies are heavier and have a
different (slow, one-time) init lifecycle than api's own runtime, and
an NLP failure here must never destabilize price/scanner APIs.
Classification itself talks only to Postgres (asyncpg) -- Redis is used
only for the standard cross-service health heartbeat (HealthReporter,
same pattern every other service in this codebase already uses), never
for classification data.

If the FinBERT model fails to load, the loop keeps running and keeps
marking rows processed, but every classification's direction/confidence
come back "unknown"/0.0 (see classifier.py's own docstring) -- an
UNKNOWN, disclosed result per Q4.2's authorized failure mode, never a
crash-loop and never a fabricated neutral.
"""

from __future__ import annotations

import asyncio

import asyncpg
import redis.asyncio as aioredis
import structlog
from infusion_common.config import InfusionSettings
from infusion_common.health import HealthReporter
from infusion_common.lifecycle import ServiceLifecycle
from infusion_common.logging import setup_logging

from sentiment_engine.classifier import MODEL_VERSION, FinbertClassifier
from sentiment_engine.event_taxonomy import classify_event_type, event_severity
from sentiment_engine.relevance import compute_novelty, compute_relevance, source_quality

logger = structlog.get_logger()

POLL_INTERVAL_SEC = 20
BATCH_SIZE = 16
NOVELTY_LOOKBACK = 20  # how many of a symbol's own recent classified headlines to compare against


async def _fetch_pending(pool, limit: int) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, symbol, heading, summary, article_link, published_time_ms
            FROM news_events
            WHERE sentiment_completed_at IS NULL
            ORDER BY id ASC
            LIMIT $1
            """,
            limit,
        )


async def _recent_headlines(pool, symbol: str, exclude_id: int) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ne.heading
            FROM sentiment_scores ss
            JOIN news_events ne ON ne.id = ss.news_event_id
            WHERE ss.symbol = $1 AND ne.id != $2
            ORDER BY ss.classified_at DESC
            LIMIT $3
            """,
            symbol,
            exclude_id,
            NOVELTY_LOOKBACK,
        )
    return [r["heading"] for r in rows]


async def _write_result(
    pool, article_id: int, symbol: str, result: dict, mark_complete: bool
) -> None:
    """Upsert (not insert-only) -- a real classification must be able
    to overwrite an earlier fallback 'unknown' row from a window where
    the model was unavailable (see mark_complete below), not be
    permanently blocked by the first row's UNIQUE constraint.

    mark_complete is only True for a genuine FinBERT classification.
    A fallback ('unknown', model unavailable) result is still persisted
    -- a real record that this article was attempted, with the honest
    UNKNOWN direction/confidence Q4.2 requires -- but sentiment_completed_at
    is deliberately left NULL, so the SAME article stays in
    _fetch_pending()'s queue and gets retried on a future sweep once the
    model recovers, instead of being stuck as 'unknown' forever. Found
    and fixed during EB-7 increment 2's own live verification, when a
    real Docker-volume permission bug (also fixed, see the Dockerfile)
    left a batch of real articles classified during the model's
    unavailable window."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
                INSERT INTO sentiment_scores
                    (news_event_id, symbol, event_type, direction, confidence,
                     severity, relevance, novelty, source_quality, model_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (news_event_id) DO UPDATE SET
                    event_type = EXCLUDED.event_type,
                    direction = EXCLUDED.direction,
                    confidence = EXCLUDED.confidence,
                    severity = EXCLUDED.severity,
                    relevance = EXCLUDED.relevance,
                    novelty = EXCLUDED.novelty,
                    source_quality = EXCLUDED.source_quality,
                    model_version = EXCLUDED.model_version,
                    classified_at = now()
                """,
            article_id,
            symbol,
            result["event_type"],
            result["direction"],
            result["confidence"],
            result["severity"],
            result["relevance"],
            result["novelty"],
            result["source_quality"],
            result["model_version"],
        )
        if mark_complete:
            await conn.execute(
                "UPDATE news_events SET sentiment_completed_at = now() WHERE id = $1",
                article_id,
            )


async def _process_batch(pool, classifier: FinbertClassifier, rows: list[asyncpg.Record]) -> int:
    if not rows:
        return 0

    # FinBERT batched inference over heading+summary (heading alone is
    # often too short for the model to have much to work with; summary,
    # when present, gives real additional context).
    texts = [f"{r['heading']}. {r['summary'] or ''}".strip() for r in rows]
    sentiments = classifier.classify_batch(texts)

    processed = 0
    for row, sentiment in zip(rows, sentiments, strict=False):
        try:
            heading = row["heading"]
            summary = row["summary"]
            symbol = row["symbol"]
            event_type = classify_event_type(heading, summary)
            recent = await _recent_headlines(pool, symbol, row["id"])
            result = {
                "event_type": event_type,
                # Unavailable model -> UNKNOWN, never a fabricated
                # neutral/zero -- per Q4.2's authorized failure mode.
                "direction": sentiment["direction"] if sentiment else "unknown",
                "confidence": sentiment["confidence"] if sentiment else 0.0,
                "severity": event_severity(event_type),
                "relevance": compute_relevance(symbol, heading, summary),
                "novelty": compute_novelty(heading, recent),
                "source_quality": source_quality(row["article_link"]),
                "model_version": MODEL_VERSION
                if sentiment
                else f"{MODEL_VERSION}+model_unavailable",
            }
            # Only a genuine classification marks the article complete
            # -- a fallback/unknown result stays retryable (see
            # _write_result's own docstring for why).
            await _write_result(
                pool, row["id"], symbol, result, mark_complete=sentiment is not None
            )
            processed += 1
        except Exception as exc:
            # One bad article must never stall the whole batch/sweep --
            # it simply stays unprocessed (sentiment_completed_at still
            # NULL) and gets retried on the next poll cycle.
            logger.error("sentiment_article_failed", article_id=row["id"], error=str(exc))
    return processed


async def sentiment_loop(pool, classifier: FinbertClassifier) -> None:
    logger.info(
        "sentiment_engine_loop_started",
        interval=POLL_INTERVAL_SEC,
        model_available=classifier.available,
    )
    while True:
        try:
            rows = await _fetch_pending(pool, BATCH_SIZE)
            if rows:
                processed = await _process_batch(pool, classifier, rows)
                logger.info("sentiment_sweep", fetched=len(rows), processed=processed)
        except Exception as exc:
            logger.error("sentiment_sweep_failed", error=str(exc))
        await asyncio.sleep(POLL_INTERVAL_SEC)


async def main() -> None:
    settings = InfusionSettings()
    setup_logging(settings.service_name, settings.log_level, settings.log_format)
    logger.info("sentiment_engine_starting")

    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=4)
    logger.info("sentiment_engine_pg_connected")

    classifier = FinbertClassifier()
    # Model load is slow (first-run downloads ~440MB from the HF hub)
    # and CPU-bound -- run it off the event loop so it doesn't block
    # asyncio's own startup/health surface.
    await asyncio.to_thread(classifier.load)

    redis = aioredis.from_url(settings.redis_url)
    health = HealthReporter(redis, settings.service_name)
    health.set_details_fn(
        lambda: {
            "model_available": classifier.available,
            "model_version": MODEL_VERSION,
        }
    )
    await health.start()

    lifecycle = ServiceLifecycle(settings.service_name)
    lifecycle.on_shutdown(health.stop)
    lifecycle.on_shutdown(redis.aclose)
    lifecycle.on_shutdown(pool.close)

    await lifecycle.run_until_shutdown(lambda: sentiment_loop(pool, classifier))


if __name__ == "__main__":
    asyncio.run(main())
