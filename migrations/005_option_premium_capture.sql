-- ═══════════════════════════════════════════════════
-- OPTION PREMIUM CAPTURE -- Phase 13.4 (net-of-cost walk-forward, part 1)
-- ═══════════════════════════════════════════════════
-- Run: docker compose exec postgres psql -U infusion -d infusion -f /migrations/005_option_premium_capture.sql
-- Or:  psql -U infusion -d infusion -f migrations/005_option_premium_capture.sql
--
-- cost_model.compute() (services/api/src/api/cost_model.py) needs the
-- option's actual premium (entry ask, exit bid) to compute a real net
-- P&L -- confirmed the `signals` table never stored this (only the
-- underlying's entry/invalidation/target prices exist here). Real
-- premium data already exists exactly one place in this codebase --
-- _upstox_option_context() in api/routes/market.py -- but only gets
-- called on-demand for a symbol a human is actively viewing, never
-- persisted per-signal. These columns give the new scheduler-driven
-- capture loop (services/scheduler/src/scheduler/main.py's
-- premium_capture_loop) somewhere to write what it fetches.
--
-- All nullable, all backfill-none: existing rows simply stay NULL until
-- a signal fires/resolves AFTER this migration and the capture loop
-- picks it up. Deliberately not backfilled for the ~12,000 pre-existing
-- signals -- there is no way to retroactively know what a since-expired
-- option contract's bid/ask was at a past moment.

BEGIN;

ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS entry_premium_ask NUMERIC(12,2),
    ADD COLUMN IF NOT EXISTS entry_premium_bid NUMERIC(12,2),
    ADD COLUMN IF NOT EXISTS exit_premium_bid NUMERIC(12,2),
    ADD COLUMN IF NOT EXISTS option_instrument_key TEXT;

COMMIT;
