-- ═══════════════════════════════════════════════════
-- STRUCTURE & BREAKOUT SUITE -- PHASE 4 OPTIMIZER PERSISTENCE
-- ═══════════════════════════════════════════════════
-- Run: docker compose exec postgres psql -U infusion -d infusion -f /migrations/013_structure_optimized_profiles.sql
-- Or:  psql -U infusion -d infusion -f migrations/013_structure_optimized_profiles.sql

BEGIN;

CREATE TABLE IF NOT EXISTS structure_optimized_profiles (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  UUID        NOT NULL REFERENCES structure_backtest_runs(run_id) ON DELETE CASCADE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    params                  JSONB       NOT NULL,
    train_metrics           JSONB       NOT NULL,
    test_metrics            JSONB       NOT NULL,
    consistency_symbols     NUMERIC(4,3),
    consistency_timeframes  NUMERIC(4,3),
    overfit_gap_r           NUMERIC(10,4),
    robustness_score        NUMERIC(10,3),
    confidence              TEXT,        -- LOW | MEDIUM | HIGH
    rejected                BOOLEAN     NOT NULL DEFAULT false,
    rejection_reasons       TEXT[]      NOT NULL DEFAULT '{}',
    rank                    SMALLINT    -- 1 = recommended; NULL for rejected/unranked candidates
);

CREATE INDEX IF NOT EXISTS idx_sop_run_rank ON structure_optimized_profiles (run_id, rank);

COMMIT;
