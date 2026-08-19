-- ═══════════════════════════════════════════════════
-- EBIE EB-1: CANONICAL STATE MACHINE (SHADOW MODE)
-- ═══════════════════════════════════════════════════
-- Run: docker compose exec postgres psql -U infusion -d infusion -f /migrations/007_ebie_state_transitions.sql
-- Or:  psql -U infusion -d infusion -f migrations/007_ebie_state_transitions.sql
--
-- Per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q3.1's authorized migration
-- plan: "During shadow: existing tier vs EBIE state must be persisted
-- for comparison." This table is that comparison record -- an
-- append-only transition log (state, prev_state, timestamp, reason),
-- something confirmed to NOT exist anywhere in this codebase before now
-- (every prior mechanism -- signals, radar_alerts, infusion:prebreak:{},
-- infusion:radar-alert-tier:{} -- stores only "current or best-so-far
-- state," never a full history of every intermediate transition).
--
-- Deliberately never influences live scanner/alert behavior -- this is
-- purely observational during the shadow period (EB-1 through EB-13),
-- matching Section 38 "Shadow Mode" of docs/EBIE-BLUEPRINT.md: "Every
-- new model/feature should enter SHADOW before it can alter user-facing
-- verdicts."

BEGIN;

CREATE TABLE IF NOT EXISTS ebie_state_transitions (
    id                  BIGSERIAL PRIMARY KEY,
    symbol              TEXT NOT NULL,
    direction           TEXT NOT NULL,          -- 'BULLISH' | 'BEARISH'
    sector_id           TEXT,
    -- Canonical EBIE state (docs/EBIE-BLUEPRINT.md Section 5): IDLE,
    -- DEVELOPING, PRE_BREAKOUT, PRE_BREAKDOWN, READY, ARMED, TRIGGERED,
    -- CONFIRMED, FAILED. (TRAP folds into FAILED for this v1 -- EB-9
    -- builds a dedicated trap-probability model later; a nuanced TRAP
    -- sub-classification isn't real evidence yet, just a terminal bucket.)
    state               TEXT NOT NULL,
    prev_state          TEXT,
    reason              TEXT,
    -- Shadow-period comparison fields (Q3.1's explicit requirement) --
    -- the existing signals this v1 mapping is DERIVED FROM, captured
    -- alongside the new state so the two can be compared honestly rather
    -- than trusting the new mapping blind.
    legacy_tier         TEXT,       -- stock_breakout_tier at this moment (api/routes/ticks.py)
    legacy_pb_state     TEXT,       -- PreBreakoutTracker's raw state (services/scanner/src/scanner/pre_breakout.py)
    score               NUMERIC(6,2),
    ltp                 NUMERIC(14,4),
    transitioned_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ebie_transitions_symbol ON ebie_state_transitions (symbol, direction, transitioned_at DESC);
CREATE INDEX IF NOT EXISTS idx_ebie_transitions_state ON ebie_state_transitions (state);
CREATE INDEX IF NOT EXISTS idx_ebie_transitions_time ON ebie_state_transitions (transitioned_at DESC);

COMMIT;
