-- ═══════════════════════════════════════════════════
-- PHASE 4: OUTCOME TRACKING + ANALYTICS SCHEMA
-- ═══════════════════════════════════════════════════
-- Run: docker compose exec postgres psql -U infusion -d infusion -f /migrations/002_phase4_outcome_tracking.sql
-- Or:  psql -U infusion -d infusion -f migrations/002_phase4_outcome_tracking.sql

BEGIN;

-- ───────────────────────────────────────────────────
-- EXTEND signals TABLE
-- ───────────────────────────────────────────────────

ALTER TABLE signals ADD COLUMN IF NOT EXISTS signal_id UUID;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS entry_price NUMERIC(12,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS invalidation_price NUMERIC(12,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS target_price NUMERIC(12,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS risk_reward_ratio NUMERIC(6,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS sector_id TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS sector_strength NUMERIC(5,1);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS market_regime TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS pre_breakout_state TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS suppressed BOOLEAN DEFAULT false;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS suppression_reason TEXT;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS conditions_met JSONB;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS explanation TEXT[];
ALTER TABLE signals ADD COLUMN IF NOT EXISTS sub_scores JSONB;

-- Outcome tracking
ALTER TABLE signals ADD COLUMN IF NOT EXISTS outcome_tracked BOOLEAN DEFAULT false;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS target_hit_at TIMESTAMPTZ;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS stop_hit_at TIMESTAMPTZ;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS expired_at TIMESTAMPTZ;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS high_after_signal NUMERIC(12,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS low_after_signal NUMERIC(12,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS max_favorable_pct NUMERIC(6,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS max_adverse_pct NUMERIC(6,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS time_to_target_min NUMERIC(8,1);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS time_to_stop_min NUMERIC(8,1);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS session_hour TEXT;

-- ───────────────────────────────────────────────────
-- DAILY OUTCOMES SUMMARY
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS signal_outcomes_daily (
    trade_date     DATE NOT NULL,
    total_signals  INTEGER DEFAULT 0,
    active_signals INTEGER DEFAULT 0,
    suppressed     INTEGER DEFAULT 0,
    target_hits    INTEGER DEFAULT 0,
    stop_hits      INTEGER DEFAULT 0,
    expired        INTEGER DEFAULT 0,
    precision_pct  NUMERIC(5,1),
    avg_score      NUMERIC(5,1),
    avg_rr         NUMERIC(5,2),
    regime         TEXT,
    PRIMARY KEY (trade_date)
);

-- ───────────────────────────────────────────────────
-- SESSION ANALYTICS
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS session_analytics (
    trade_date     DATE NOT NULL,
    session_hour   TEXT NOT NULL,
    signals        INTEGER DEFAULT 0,
    target_hits    INTEGER DEFAULT 0,
    stop_hits      INTEGER DEFAULT 0,
    precision_pct  NUMERIC(5,1),
    avg_score      NUMERIC(5,1),
    PRIMARY KEY (trade_date, session_hour)
);

-- ───────────────────────────────────────────────────
-- WATCHLIST CONVERSION TRACKING
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS watchlist_conversions (
    id             UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    symbol         TEXT NOT NULL,
    entered_state  TEXT NOT NULL,
    entered_at     TIMESTAMPTZ NOT NULL,
    triggered_at   TIMESTAMPTZ,
    expired_at     TIMESTAMPTZ,
    converted      BOOLEAN DEFAULT false,
    duration_min   NUMERIC(8,1),
    signal_grade   TEXT,
    trade_date     DATE NOT NULL
);

-- ───────────────────────────────────────────────────
-- SUPPRESSION ANALYTICS
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS suppression_daily (
    trade_date          DATE NOT NULL,
    reason              TEXT NOT NULL,
    count               INTEGER DEFAULT 0,
    avg_score           NUMERIC(5,1),
    would_have_won      INTEGER DEFAULT 0,
    would_have_lost     INTEGER DEFAULT 0,
    PRIMARY KEY (trade_date, reason)
);

-- ───────────────────────────────────────────────────
-- INDEXES
-- ───────────────────────────────────────────────────

ALTER TABLE signals ADD CONSTRAINT uq_signals_signal_id UNIQUE (signal_id);
CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_untracked ON signals (outcome_tracked, created_at)
    WHERE NOT outcome_tracked AND NOT suppressed;
CREATE INDEX IF NOT EXISTS idx_signals_sector_outcome ON signals (sector_id, outcome_label)
    WHERE outcome_label IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_signals_regime_outcome ON signals (market_regime, outcome_label)
    WHERE outcome_label IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_signals_grade_outcome ON signals (conviction_grade, outcome_label);
CREATE INDEX IF NOT EXISTS idx_signals_session ON signals (session_hour, outcome_label)
    WHERE session_hour IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_watchlist_conv_date ON watchlist_conversions (trade_date);
CREATE INDEX IF NOT EXISTS idx_suppression_daily_date ON suppression_daily (trade_date);

COMMIT;
