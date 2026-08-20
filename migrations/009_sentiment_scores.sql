-- ═══════════════════════════════════════════════════
-- EBIE EB-7 (increment 2): SENTIMENT-ENGINE CLASSIFICATION OUTPUT
-- ═══════════════════════════════════════════════════
-- Run: docker compose exec postgres psql -U infusion -d infusion -f /migrations/009_sentiment_scores.sql
-- Or:  psql -U infusion -d infusion -f migrations/009_sentiment_scores.sql
--
-- Separate table from news_events (not an ALTER), matching the
-- authorized suggested-table list in docs/EBIE-BLUEPRINT.md Section 31
-- (news_events and sentiment_scores are listed as distinct tables) --
-- news_events is purely about the raw ingested article (EB-7 increment
-- 1's concern), sentiment_scores is purely about a model's
-- classification of it (this increment's concern). Keeping them
-- separate means a future re-classification (new model_version) or a
-- second classifier can add rows without ever touching the ingestion
-- table.
--
-- Deliberate, disclosed deviation from a literal reading of
-- docs/EBIE-IMPLEMENTATION-ANSWERS.md Q4.2's output contract: that list
-- includes "impact" as one of sentiment-engine's own output fields.
-- This table does NOT persist a baked-in impact number. The blueprint's
-- own pipeline (Section 4.10) runs relevance -> novelty -> credibility
-- -> TIME DECAY -> sentiment impact score -- decay is a function of how
-- long ago the article was published relative to *now*, not a static
-- property of the article at classification time. Baking a decayed
-- "impact" value in here would make it silently stale the moment
-- enough time passes without a re-read. Instead this table persists the
-- decay-independent components (direction, confidence, severity,
-- relevance, novelty, source_quality) and EB-7 increment 3's read path
-- computes the live, correctly-decayed sentiment_impact composite at
-- request time from published_time_ms (on the joined news_events row)
-- vs now().
--
-- 1:1 with news_events for v1 (UNIQUE news_event_id) -- a later
-- re-classification under a new model_version is a deliberately
-- deferred design question (upsert-latest vs. append-and-keep-history),
-- not decided here.

BEGIN;

CREATE TABLE IF NOT EXISTS sentiment_scores (
    id                  BIGSERIAL PRIMARY KEY,
    news_event_id       BIGINT NOT NULL REFERENCES news_events(id) ON DELETE CASCADE,
    symbol              TEXT NOT NULL,
    -- Deterministic event taxonomy (sentiment_engine/event_taxonomy.py)
    -- -- 'other' when nothing in the fixed taxonomy matches, never a
    -- fabricated specific category.
    event_type          TEXT NOT NULL,
    -- 'bullish' | 'bearish' | 'neutral' | 'ambiguous' (FinBERT's 3-class
    -- output, 'ambiguous' when the top two class probabilities are too
    -- close to call -- see sentiment_engine/classifier.py)
    direction            TEXT NOT NULL,
    confidence            NUMERIC(5,4) NOT NULL,   -- FinBERT's own top-class probability, 0-1
    severity              NUMERIC(5,4) NOT NULL,   -- static per-event_type heuristic, v1 (see event_taxonomy.py)
    relevance             NUMERIC(5,4) NOT NULL,   -- does the article actually concern this symbol vs. generic market news
    novelty               NUMERIC(5,4) NOT NULL,   -- vs this symbol's own recent article history (near-duplicate/re-syndication detection)
    source_quality        NUMERIC(5,4) NOT NULL,   -- static per-publisher-domain heuristic, v1 (see source_quality.py)
    model_version          TEXT NOT NULL,
    classified_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (news_event_id)
);

CREATE INDEX IF NOT EXISTS idx_sentiment_scores_symbol_time
    ON sentiment_scores (symbol, classified_at DESC);

COMMIT;
