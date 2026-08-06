-- ═══════════════════════════════════════════════
-- INFUSION POSTGRESQL INITIALIZATION
-- ═══════════════════════════════════════════════

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ───────────────────────────────────────────────
-- REFERENCE DATA
-- ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS symbols (
    symbol              TEXT    PRIMARY KEY,
    isin                TEXT    UNIQUE,
    instrument_token    INTEGER,
    exchange            TEXT    DEFAULT 'NSE',
    segment             TEXT,
    series              TEXT    DEFAULT 'EQ',
    lot_size            INTEGER DEFAULT 1,
    sector_id           TEXT,
    industry            TEXT,
    market_cap_cr       NUMERIC(14,2),
    free_float_pct      NUMERIC(5,2),
    is_fno              BOOLEAN DEFAULT false,
    is_index            BOOLEAN DEFAULT false,
    nifty_50            BOOLEAN DEFAULT false,
    nifty_500           BOOLEAN DEFAULT false,
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS corporate_actions (
    id                  UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
    symbol              TEXT    NOT NULL,
    action_type         TEXT    NOT NULL,
    ex_date             DATE,
    record_date         DATE,
    details             TEXT,
    adjustment_factor   NUMERIC(10,6),
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- ───────────────────────────────────────────────
-- TIME-SERIES DATA (partitioned)
-- ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ohlcv_daily (
    symbol              TEXT        NOT NULL,
    trade_date          DATE        NOT NULL,
    open                NUMERIC(12,2),
    high                NUMERIC(12,2),
    low                 NUMERIC(12,2),
    close               NUMERIC(12,2),
    prev_close          NUMERIC(12,2),
    volume              BIGINT,
    delivery_volume     BIGINT,
    delivery_pct        NUMERIC(5,2),
    vwap                NUMERIC(12,2),
    turnover_cr         NUMERIC(12,2),
    trades              INTEGER,
    open_interest       BIGINT,
    PRIMARY KEY (symbol, trade_date)
) PARTITION BY RANGE (trade_date);

CREATE TABLE IF NOT EXISTS ohlcv_intraday (
    symbol          TEXT            NOT NULL,
    timeframe       TEXT            NOT NULL,
    bar_time        TIMESTAMPTZ     NOT NULL,
    open            NUMERIC(12,2),
    high            NUMERIC(12,2),
    low             NUMERIC(12,2),
    close           NUMERIC(12,2),
    volume          BIGINT,
    vwap            NUMERIC(12,2),
    PRIMARY KEY (symbol, timeframe, bar_time)
) PARTITION BY RANGE (bar_time);

-- ───────────────────────────────────────────────
-- INTELLIGENCE DATA
-- ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS signals (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at          TIMESTAMPTZ DEFAULT now() NOT NULL,
    symbol              TEXT        NOT NULL,
    strategy            TEXT        NOT NULL,
    signal_type         TEXT        NOT NULL,
    conviction_score    NUMERIC(5,1),
    conviction_grade    TEXT,
    price_at_signal     NUMERIC(12,2) NOT NULL,
    volume_at_signal    BIGINT,
    features            JSONB       NOT NULL,
    price_1d            NUMERIC(12,2),
    price_3d            NUMERIC(12,2),
    price_5d            NUMERIC(12,2),
    return_1d_pct       NUMERIC(6,2),
    return_3d_pct       NUMERIC(6,2),
    return_5d_pct       NUMERIC(6,2),
    outcome_label       TEXT
);

CREATE TABLE IF NOT EXISTS sector_daily (
    sector_id           TEXT    NOT NULL,
    trade_date          DATE    NOT NULL,
    breadth             NUMERIC(5,2),
    pct_above_vwap      NUMERIC(5,2),
    weighted_return_pct NUMERIC(6,2),
    money_flow_score    NUMERIC(8,2),
    rotation_score      NUMERIC(6,2),
    rotation_quadrant   TEXT,
    advance_count       INTEGER,
    decline_count       INTEGER,
    fii_net_cr          NUMERIC(12,2),
    dii_net_cr          NUMERIC(12,2),
    PRIMARY KEY (sector_id, trade_date)
);

CREATE TABLE IF NOT EXISTS institutional_flows (
    trade_date      DATE    PRIMARY KEY,
    fii_buy_cr      NUMERIC(14,2),
    fii_sell_cr     NUMERIC(14,2),
    fii_net_cr      NUMERIC(14,2),
    dii_buy_cr      NUMERIC(14,2),
    dii_sell_cr     NUMERIC(14,2),
    dii_net_cr      NUMERIC(14,2)
);

-- ───────────────────────────────────────────────
-- OPERATIONAL DATA
-- ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS alert_log (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    signal_id       UUID        REFERENCES signals(id),
    channel         TEXT        NOT NULL,
    delivered_at    TIMESTAMPTZ DEFAULT now(),
    message_hash    TEXT,
    status          TEXT        DEFAULT 'SENT'
);

-- ───────────────────────────────────────────────
-- INDEXES
-- ───────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_ohlcv_daily_sym_date ON ohlcv_daily (symbol, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_signals_grade_time ON signals (conviction_grade, created_at DESC)
    WHERE conviction_grade IN ('A+', 'A');
CREATE INDEX IF NOT EXISTS idx_signals_symbol_time ON signals (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_outcome ON signals (outcome_label, created_at DESC)
    WHERE outcome_label IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sector_daily_date ON sector_daily (trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_corp_actions_sym_date ON corporate_actions (symbol, ex_date DESC);
CREATE INDEX IF NOT EXISTS idx_symbols_search ON symbols USING gin (symbol gin_trgm_ops);

-- ───────────────────────────────────────────────
-- INITIAL PARTITIONS
-- ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ohlcv_daily_2026_05 PARTITION OF ohlcv_daily
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS ohlcv_daily_2026_06 PARTITION OF ohlcv_daily
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
