-- ═══════════════════════════════════════════════════
-- PIPELINE AUDIT FIX B5: UNIFIED REJECTION CODE TAXONOMY
-- ═══════════════════════════════════════════════════
-- Run: docker compose exec postgres psql -U infusion -d infusion -f /migrations/011_rejection_code_taxonomy.sql
-- Or:  psql -U infusion -d infusion -f migrations/011_rejection_code_taxonomy.sql
--
-- Adds a stable, sliceable rejection-code column alongside the existing
-- free-text suppression_reason (added by 002_phase4_outcome_tracking.sql).
-- suppression_reason is untouched and keeps carrying the exact human-
-- readable detail it always has -- this is purely additive. See
-- libs/infusion-models/src/infusion_models/rejection.py for the
-- RejectionCode enum whose values populate this column; '' (not every
-- suppression gate maps to a taxonomy member yet) is a disclosed gap,
-- not a NULL/missing-data ambiguity.

BEGIN;

ALTER TABLE signals ADD COLUMN IF NOT EXISTS suppression_code TEXT DEFAULT '';

-- Matches idx_signals_created_at_suppressed's own reasoning
-- (004_backtest_summary_perf.sql): the dashboard's "why no trade" board
-- slices by code within a recent time window, so a composite index
-- avoids a heap fetch per row for that exact query shape.
CREATE INDEX IF NOT EXISTS idx_signals_created_at_suppression_code
    ON signals (created_at DESC, suppression_code)
    WHERE suppression_code <> '';

COMMIT;
