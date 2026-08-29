-- ═══════════════════════════════════════════════════
-- STRUCTURE & BREAKOUT SUITE -- POST-PHASE-4 REVIEW FIX (2026-08-29)
-- ═══════════════════════════════════════════════════
-- Run: docker compose exec postgres psql -U infusion -d infusion -f /migrations/014_structure_optimize_diagnostics.sql
-- Or:  psql -U infusion -d infusion -f migrations/014_structure_optimize_diagnostics.sql
--
-- Real gap found while wiring up Task 2/4's own diagnostics/UI ask: the
-- ORIGINAL Phase 4 get_cached_optimize_result() already dropped
-- full_grid_size/sampled_combinations/dsr on a cache-hit read (they were
-- only ever present on the first, freshly-computed response) -- adding
-- per-profile trigger_diagnostics and run-level trigger_source_breakdown/
-- runtime without persisting them would repeat that exact gap for the
-- new fields too. Persisted here instead of silently dropped on refresh.

BEGIN;

ALTER TABLE structure_optimized_profiles
    ADD COLUMN IF NOT EXISTS trigger_diagnostics JSONB;

CREATE TABLE IF NOT EXISTS structure_optimize_runs_meta (
    run_id                      UUID        PRIMARY KEY REFERENCES structure_backtest_runs(run_id) ON DELETE CASCADE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    full_grid_size              INTEGER,
    sampled_combinations        INTEGER,
    combos_evaluated            INTEGER,
    feature_precompute_pairs    INTEGER,
    trigger_source_breakdown    JSONB,
    runtime                     JSONB,
    dsr                         JSONB
);

COMMIT;
