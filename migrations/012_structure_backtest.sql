-- ═══════════════════════════════════════════════════
-- STRUCTURE & BREAKOUT SUITE -- PHASE 3 HISTORICAL REPLAY BACKTESTER
-- ═══════════════════════════════════════════════════
-- Run: docker compose exec postgres psql -U infusion -d infusion -f /migrations/012_structure_backtest.sql
-- Or:  psql -U infusion -d infusion -f migrations/012_structure_backtest.sql
--
-- New tables, not an extension of the existing `signals` table:
-- `signals` holds real, live-fired production candidates (one row per
-- actual scanner decision); a replay backtest run generates a much
-- larger volume of hypothetical, bar-by-bar simulated trades across a
-- parameter/symbol/timeframe grid that were never live signals at all.
-- Overloading `signals`' row shape and volume expectations with that
-- would conflate two genuinely different concepts -- see
-- api/structure_backtest.py's own module docstring for the full
-- reasoning (matches the approved architecture's own Section 8 design).

BEGIN;

CREATE TABLE IF NOT EXISTS structure_backtest_runs (
    run_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    requested_at        TIMESTAMPTZ DEFAULT now() NOT NULL,
    completed_at        TIMESTAMPTZ,
    symbols             TEXT[]      NOT NULL,
    timeframes          TEXT[]      NOT NULL,
    start_date          DATE        NOT NULL,
    end_date            DATE        NOT NULL,
    side                TEXT        NOT NULL DEFAULT 'BOTH',   -- LONG_ONLY | SHORT_ONLY | BOTH
    cost_assumptions    JSONB       NOT NULL DEFAULT '{}',     -- brokerage/slippage rates used
    config_used         JSONB       NOT NULL DEFAULT '{}',     -- the exact StructureSignalConfig for this run
    status              TEXT        NOT NULL DEFAULT 'RUNNING', -- RUNNING | DONE | FAILED
    error               TEXT,
    metrics             JSONB                                  -- populated on DONE: see structure_backtest.py's BacktestMetrics
);

CREATE TABLE IF NOT EXISTS structure_backtest_trades (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  UUID        NOT NULL REFERENCES structure_backtest_runs(run_id) ON DELETE CASCADE,
    symbol                  TEXT        NOT NULL,
    timeframe               TEXT        NOT NULL,
    direction               TEXT        NOT NULL,   -- LONG | SHORT
    entry_time              TIMESTAMPTZ NOT NULL,
    entry_price             NUMERIC(14,4) NOT NULL,
    sl_price                NUMERIC(14,4) NOT NULL,
    tp1_price               NUMERIC(14,4),
    tp2_price               NUMERIC(14,4),
    tp3_price               NUMERIC(14,4),
    exit_time               TIMESTAMPTZ,
    exit_price              NUMERIC(14,4),
    exit_reason             TEXT,        -- SL_HIT | TP1 | TP2 | TP3 | SESSION_CLOSE
    r_multiple              NUMERIC(8,4),
    pnl_per_share           NUMERIC(14,4),
    setup_quality_at_entry  SMALLINT,
    market_phase_at_entry   TEXT,
    params_used             JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sbt_run ON structure_backtest_trades (run_id);
CREATE INDEX IF NOT EXISTS idx_sbt_run_symbol_tf ON structure_backtest_trades (run_id, symbol, timeframe);
CREATE INDEX IF NOT EXISTS idx_sbr_status_requested ON structure_backtest_runs (status, requested_at DESC);

COMMIT;
