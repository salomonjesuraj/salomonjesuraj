-- ═══════════════════════════════════════════════════
-- EBIE EB-14: PROMOTION REVIEW INFRASTRUCTURE (tracking only, no live cutover)
-- ═══════════════════════════════════════════════════
-- Run: docker compose exec postgres psql -U infusion -d infusion -f /migrations/010_ebie_promotion_reviews.sql
-- Or:  psql -U infusion -d infusion -f migrations/010_ebie_promotion_reviews.sql
--
-- Per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q5.3 ("Evaluate weekly;
-- promote manually at first") and Section 39 ("Champion / Challenger
-- Models... Only champion drives the verdict. Challenger is promoted
-- if it shows...") -- this table is a durable, weekly-appended history
-- of EB-13's shadow-validation report, not a live-behavior switch.
-- Nothing in this migration or the code that writes to this table
-- changes which model drives real signals; the existing scanner
-- remains champion, EBIE remains challenger/shadow, exactly as every
-- prior EBIE phase has run. `human_decision` stays NULL until a person
-- explicitly records one -- no automated process in this codebase ever
-- writes PROMOTED/REJECTED into it.

BEGIN;

CREATE TABLE IF NOT EXISTS ebie_promotion_reviews (
    id                      BIGSERIAL PRIMARY KEY,
    reviewed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Gate A/B raw numbers at the moment of this review (denormalized
    -- from report_snapshot for cheap trend queries without re-parsing
    -- JSONB every time).
    episode_count           INTEGER NOT NULL,
    session_count           INTEGER NOT NULL,
    gate_a_met              BOOLEAN NOT NULL,
    gate_b_met              BOOLEAN NOT NULL,
    precision_available     BOOLEAN NOT NULL,
    precision_favors_ebie   BOOLEAN,             -- NULL when precision_available is false
    false_break_reliable    BOOLEAN NOT NULL,
    calibration_reliable    BOOLEAN NOT NULL,
    -- 'NOT_READY' (either gate unmet) | 'READY_FOR_HUMAN_REVIEW' (both
    -- gates met AND the performance comparison favors EBIE) -- an
    -- automated classification of the CURRENT evidence, never itself a
    -- promotion action.
    readiness               TEXT NOT NULL,
    readiness_reasons       TEXT[] NOT NULL DEFAULT '{}',
    -- Full report at this exact moment, for complete auditability --
    -- "every verdict must be reproducible from persisted snapshot IDs"
    -- (Section 30's own rule) applies here too.
    report_snapshot         JSONB NOT NULL,
    -- Populated ONLY by an explicit human action (never by this
    -- table's own writer, never by any scheduled job) -- 'DEFERRED' |
    -- 'PROMOTED' | 'REJECTED', with a free-text note and timestamp.
    human_decision          TEXT,
    human_decision_note     TEXT,
    human_decision_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ebie_promotion_reviews_reviewed_at
    ON ebie_promotion_reviews (reviewed_at DESC);

COMMIT;
