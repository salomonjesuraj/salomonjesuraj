-- ═══════════════════════════════════════════════════
-- BACKTEST SUMMARY PERFORMANCE -- scoped follow-up to Phase N8
-- ═══════════════════════════════════════════════════
-- Run: docker compose exec postgres psql -U infusion -d infusion -f /migrations/004_backtest_summary_perf.sql
-- Or:  psql -U infusion -d infusion -f migrations/004_backtest_summary_perf.sql
--
-- Confirmed live (not assumed) before writing this: /api/backtest/summary
-- ?days=7 measured 23s and a separate check hit nginx's 30s gateway
-- timeout outright, against 269,560 total signal rows in a 7-day window
-- (269,479 of them suppressed). The existing idx_signals_created_at
-- (created_at DESC) locates the date-range boundary fine; the real cost
-- is that every one of the route's aggregation queries then reads the
-- suppressed column (and several others) off the heap for all ~270k rows
-- in range, 4-5 separate times (one scan per query). This index lets
-- Postgres answer created_at + suppressed filtering straight from the
-- index for the total/active/suppressed counts without a heap fetch,
-- and the API route change in this same follow-up collapses the
-- previously-separate 5 queries (overview, by_grade, by_session,
-- by_sector, target_levels) into one GROUPING SETS query so the
-- expensive part -- reading the row set once -- happens once, not 5x.

BEGIN;

CREATE INDEX IF NOT EXISTS idx_signals_created_at_suppressed
    ON signals (created_at DESC, suppressed);

COMMIT;
