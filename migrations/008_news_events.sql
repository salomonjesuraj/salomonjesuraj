-- ═══════════════════════════════════════════════════
-- EBIE EB-7 (increment 1): NEWS INGESTION -- durable article store
-- ═══════════════════════════════════════════════════
-- Run: docker compose exec postgres psql -U infusion -d infusion -f /migrations/008_news_events.sql
-- Or:  psql -U infusion -d infusion -f migrations/008_news_events.sql
--
-- Per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q4.3's storage policy: raw
-- news/event records are durable-research data ("news/event records" is
-- explicitly listed under Postgres, not Redis -- only "current
-- sentiment" is hot state). This is the fetch/dedupe/entity-mapping
-- stage only (see api/news_ingestion.py + api/news_queue.py) -- pure
-- Upstox News API ingestion, no classification/sentiment yet. The
-- sentiment-engine service (EB-7 increment 2) will read unclassified
-- rows from here and write its output back via ALTER (event_type,
-- direction, confidence, etc. columns land in a follow-up migration
-- once that contract is implemented and verified against real output
-- shapes, not guessed at now).
--
-- One real article can legitimately map to several symbols (Upstox's
-- response is keyed by instrument_key, and the same underlying article
-- can be tagged against more than one instrument) -- each symbol's
-- exposure to that article is its own row, own relevance evidence, so
-- the dedup key is (symbol, article_fingerprint), not article_fingerprint
-- alone. article_fingerprint is a sha256 of article_link when present
-- (the closest thing to a stable article identity Upstox's response
-- offers), falling back to heading+published_time when article_link is
-- missing/empty (observed to happen for some real articles during
-- verification).

BEGIN;

CREATE TABLE IF NOT EXISTS news_events (
    id                      BIGSERIAL PRIMARY KEY,
    symbol                  TEXT NOT NULL,
    instrument_key          TEXT NOT NULL,
    article_fingerprint     TEXT NOT NULL,
    heading                 TEXT NOT NULL,
    summary                 TEXT,
    article_link            TEXT,
    thumbnail               TEXT,
    published_time_ms       BIGINT,             -- Upstox's own unix-ms published_time, NULL if absent
    api_fetch_time          TIMESTAMPTZ NOT NULL DEFAULT now(),   -- when THIS sweep's request returned it
    first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT now(),   -- when this (symbol, article) pair was first persisted
    -- Filled by EB-7 increment 2 (sentiment-engine) -- NULL until then,
    -- and NULL forever for an article the service genuinely never
    -- reaches (never silently defaulted to a fake "processed" state).
    sentiment_completed_at  TIMESTAMPTZ,
    UNIQUE (symbol, article_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_news_events_symbol_published
    ON news_events (symbol, published_time_ms DESC);

CREATE INDEX IF NOT EXISTS idx_news_events_pending_sentiment
    ON news_events (id)
    WHERE sentiment_completed_at IS NULL;

COMMIT;
