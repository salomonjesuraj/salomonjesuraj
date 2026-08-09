-- ═══════════════════════════════════════════════════
-- PHASE N8: T1/T2/T3 TARGET-LEVEL OUTCOME TRACKING
-- ═══════════════════════════════════════════════════
-- Run: docker compose exec postgres psql -U infusion -d infusion -f /migrations/003_phase_n8_target_level_tracking.sql
-- Or:  psql -U infusion -d infusion -f migrations/003_phase_n8_target_level_tracking.sql
--
-- Confirmed gap (not assumed) before writing this: tracker.py's
-- _track_cycle() only ever compared LTP against one target_price column,
-- and outcome_label could only ever be TARGET_HIT/STOP_HIT/EXPIRED/null --
-- no notion of which fib level (T1/T2/T3) price actually reached, even
-- though options_first_hybrid.py already computes and stores t2_price/
-- t3_price inside features_snapshot at signal-creation time. This
-- migration promotes those two prices to first-class columns the tracker
-- can read without parsing JSONB per cycle, and adds a column to record
-- which level was actually reached.

BEGIN;

ALTER TABLE signals ADD COLUMN IF NOT EXISTS t2_price NUMERIC(12,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS t3_price NUMERIC(12,2);

-- NULL until a TARGET_HIT is recorded (mirrors target_hit_at's own
-- NULL-until-hit convention). 'T1'/'T2'/'T3' -- the highest level whose
-- price had already been reached at the moment target_price (T1) was
-- confirmed hit. See tracker.py's _track_cycle() docstring for the exact,
-- honestly-scoped detection method and its known limitation (a level
-- reached in a LATER 30s cycle, after the row has already left the
-- untracked pool, is not retroactively detected).
ALTER TABLE signals ADD COLUMN IF NOT EXISTS target_level_hit TEXT;

COMMIT;
