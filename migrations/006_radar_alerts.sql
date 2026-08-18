-- ═══════════════════════════════════════════════════
-- PHASE R9: DASHBOARD-ONLY EARLY ALERTS + OUTCOME TRACKING
-- ═══════════════════════════════════════════════════
-- Run: docker compose exec postgres psql -U infusion -d infusion -f /migrations/006_radar_alerts.sql
-- Or:  psql -U infusion -d infusion -f migrations/006_radar_alerts.sql
--
-- New table, not an ALTER -- this tracks a genuinely different kind of
-- event than the `signals` table. A `signals` row is a real, scanner-
-- fired, Telegram-alerted trade candidate. A `radar_alerts` row is a
-- much softer, dashboard-only event: the first moment a symbol's
-- stock_breakout_score/tier (services/api/src/api/routes/ticks.py)
-- crosses into EARLY_WATCH or higher -- purely a UI notification, no
-- Telegram, no suppression/cooldown gate, no trade recommendation.
-- Deliberately never merged into `signals` -- conflating "we told you
-- to watch this" with "we told you to trade this" would blur the exact
-- distinction R6's Contract Confirmation relabel and the whole radar-
-- vs-execution split exists to keep honest.
--
-- User's own scoping answer (Phase R9's clarifying questions): trigger
-- on EARLY_WATCH-or-higher, full tracked ledger with real outcome
-- tracking -- comparable in shape to migration 003's T1/T2/T3 tracking,
-- just for a different, earlier kind of event.

BEGIN;

CREATE TABLE IF NOT EXISTS radar_alerts (
    id                      BIGSERIAL PRIMARY KEY,
    symbol                  TEXT NOT NULL,
    sector_id               TEXT,
    fired_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    tier_at_fire            TEXT NOT NULL,
    score_at_fire           NUMERIC(6,2),
    breakout_type_at_fire   TEXT,
    rel_vol_at_fire         NUMERIC(10,3),
    ltp_at_fire             NUMERIC(14,4),
    -- Progressively updated by the sweep as this alert's symbol keeps
    -- moving -- the highest tier reached since fired_at, and when.
    best_tier_reached       TEXT NOT NULL,
    best_tier_reached_at    TIMESTAMPTZ,
    -- PENDING (still open) | GRADUATED (reached BREAKOUT_NOW/OPTION_READY)
    -- | FADED (dropped back to NO_CHASE) | EXPIRED (stayed open past the
    -- sweep's staleness window without resolving either way). See
    -- api/radar_alert_queue.py's own module docstring for the exact
    -- resolution rules -- this migration only defines the storage shape.
    outcome_label           TEXT NOT NULL DEFAULT 'PENDING',
    resolved_at             TIMESTAMPTZ,
    last_checked_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_radar_alerts_symbol_fired ON radar_alerts (symbol, fired_at DESC);
CREATE INDEX IF NOT EXISTS idx_radar_alerts_outcome ON radar_alerts (outcome_label);
CREATE INDEX IF NOT EXISTS idx_radar_alerts_fired_at ON radar_alerts (fired_at DESC);
-- Partial index -- the sweep's every-cycle "find my open alerts" query
-- only ever filters on outcome_label = 'PENDING'; a full index on the
-- column would carry every resolved row forever for no benefit.
CREATE INDEX IF NOT EXISTS idx_radar_alerts_pending ON radar_alerts (symbol) WHERE outcome_label = 'PENDING';

COMMIT;
