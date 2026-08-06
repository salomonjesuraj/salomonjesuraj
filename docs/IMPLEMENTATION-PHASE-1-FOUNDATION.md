# IMPLEMENTATION PHASE 1 — SYSTEM FOUNDATION

> Bootstrap the monorepo, infrastructure, shared libraries, observability, and local
> development workflow. **No service logic yet.** This phase produces a skeleton
> that starts, connects, logs, health-checks, and shuts down cleanly.

---

## 1. Repo Bootstrap

### 1.1 Initial Commands

```bash
mkdir infusion-screener && cd infusion-screener
git init
git branch -M main
```

### 1.2 Root Files

| File | Purpose |
|---|---|
| `.gitignore` | Python, Node, Docker, IDE, `.env` |
| `.env.example` | Every env var documented, no secrets |
| `.env` | Local overrides — **git-ignored** |
| `Makefile` | Canonical entry point for all commands |
| `docker-compose.yml` | Full stack definition |
| `docker-compose.dev.yml` | Dev overrides (volumes, debug ports, hot reload) |
| `README.md` | Quick-start in ≤3 commands |
| `pyproject.toml` | Root-level: workspace tooling only (ruff, mypy) |

### 1.3 `.gitignore`

```gitignore
# Python
__pycache__/
*.pyc
*.pyo
.venv/
*.egg-info/
dist/
build/

# Node
node_modules/
.next/
out/

# Docker
docker-compose.override.yml

# Environment
.env
.env.local
*.pem
*.key

# IDE
.vscode/
.idea/
*.swp

# OS
.DS_Store
Thumbs.db

# Data
*.db
*.sqlite3
pgdata/
redisdata/
```

---

## 2. Monorepo Folder Structure

```
infusion-screener/
│
├── docker-compose.yml
├── docker-compose.dev.yml
├── .env.example
├── .env                          ← git-ignored
├── Makefile
├── pyproject.toml                ← root: ruff + mypy config only
├── README.md
│
├── libs/                         ← shared Python packages
│   ├── infusion-models/
│   │   ├── pyproject.toml
│   │   └── src/infusion_models/
│   │       ├── __init__.py
│   │       ├── tick.py
│   │       ├── feature.py
│   │       ├── signal.py
│   │       ├── sector.py
│   │       ├── alert.py
│   │       └── enums.py
│   │
│   ├── infusion-streams/
│   │   ├── pyproject.toml
│   │   └── src/infusion_streams/
│   │       ├── __init__.py
│   │       ├── producer.py
│   │       ├── consumer.py
│   │       ├── codec.py
│   │       └── health.py
│   │
│   └── infusion-common/
│       ├── pyproject.toml
│       └── src/infusion_common/
│           ├── __init__.py
│           ├── config.py
│           ├── logging.py
│           ├── timing.py
│           └── health.py
│
├── services/                     ← each service = 1 Docker container
│   ├── ingestion/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── __init__.py
│   │       └── main.py
│   ├── normalizer/
│   ├── feature-engine/
│   ├── scanner/
│   ├── sector-intel/
│   ├── conviction/
│   ├── alerter/
│   ├── nse-scraper/
│   ├── api/
│   ├── ws-gateway/
│   ├── telegram-bot/
│   └── scheduler/
│
├── config/                       ← YAML configs (runtime-loaded)
│   ├── scanners.yaml
│   ├── sectors.yaml
│   ├── conviction_weights.yaml
│   ├── alerts.yaml
│   └── instruments.yaml
│
├── migrations/                   ← Alembic
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
│
├── scripts/
│   ├── seed_symbols.py
│   ├── backfill_ohlcv.py
│   ├── benchmark_latency.py
│   └── validate_streams.py
│
├── frontend/                     ← Next.js 14+ (Phase 5, empty now)
│   └── .gitkeep
│
└── tests/
    ├── unit/
    ├── integration/
    └── load/
```

### 2.1 What Gets Created in Phase 1

Only the following are populated with real content:

| Path | Content |
|---|---|
| `libs/infusion-models/` | Stub `__init__.py` + empty model files (classes added in later phases) |
| `libs/infusion-streams/` | Stub `__init__.py` + empty module files |
| `libs/infusion-common/` | Full: `config.py`, `logging.py`, `timing.py`, `health.py` |
| `services/*/` | Only `Dockerfile`, `pyproject.toml`, stub `main.py` (healthcheck entrypoint only) |
| `config/` | Minimal YAML skeletons |
| `migrations/` | Alembic init + initial schema migration |
| `scripts/validate_streams.py` | Stream existence checker |

Everything else is `.gitkeep` placeholder directories.

---

## 3. Python Environment Strategy

### 3.1 Toolchain

| Tool | Role | Version |
|---|---|---|
| Python | Runtime | 3.12+ |
| `uv` | Package manager + venv creator | Latest stable |
| `ruff` | Linter + formatter | Latest stable |
| `mypy` | Static type checker | Latest stable |
| `pytest` | Test runner | Latest stable |

### 3.2 Why `uv`

- 10-100x faster than pip for dependency resolution
- Lock file support (`uv.lock`) for reproducible installs
- Built-in venv management
- Replaces pip, pip-tools, virtualenv in one tool

### 3.3 Dependency Installation Strategy

**Local development:**

```bash
# Create venv at repo root (shared for local dev)
uv venv .venv --python 3.12
source .venv/bin/activate  # Linux/Mac
# or: .venv\Scripts\activate  # Windows

# Install shared libs as editable
uv pip install -e libs/infusion-models
uv pip install -e libs/infusion-streams
uv pip install -e libs/infusion-common

# Install a specific service (e.g., ingestion)
uv pip install -e services/ingestion
```

**Docker builds:**

```dockerfile
# Each service Dockerfile:
COPY libs/ /app/libs/
COPY services/<service-name>/ /app/services/<service-name>/

RUN pip install /app/libs/infusion-models \
                /app/libs/infusion-streams \
                /app/libs/infusion-common \
                /app/services/<service-name>/
```

In Docker, libs are installed as **non-editable** (normal install for speed). In local dev, they are **editable** (`-e`) for hot iteration.

### 3.4 Shared Lib Dependency Graph

```
infusion-models    ← zero external deps beyond pydantic, msgpack
       │
       ▼
infusion-streams   ← depends on infusion-models + redis[hiredis] + msgpack
       │
       ▼
infusion-common    ← depends on infusion-models + structlog + pydantic-settings
```

Every service depends on all three libs. The libs themselves have a strict downward dependency.

### 3.5 Root `pyproject.toml` (Tooling Only)

```toml
[project]
name = "infusion-screener"
version = "0.1.0"
requires-python = ">=3.12"

[tool.ruff]
target-version = "py312"
line-length = 100
select = ["E", "F", "W", "I", "UP", "B", "SIM", "RUF"]
ignore = ["E501"]  # line length handled by formatter

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_configs = true

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

---

## 4. Docker Foundation

### 4.1 Base Image Strategy

```
python:3.12-slim  ← all Python services
redis:7-alpine    ← event bus + hot state
postgres:16-alpine ← cold store
node:20-alpine    ← frontend (Phase 5)
```

Why `slim` not `alpine` for Python: Alpine uses musl libc. Many Python C extensions (hiredis, polars, numpy) ship manylinux wheels that require glibc. Alpine builds from source = slower builds, subtle bugs.

### 4.2 Service Dockerfile Template

Every service Dockerfile follows this exact pattern:

```dockerfile
FROM python:3.12-slim AS base

WORKDIR /app

# System deps (if any service needs them, add here)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Install shared libs (non-editable for prod)
COPY libs/infusion-models/ /app/libs/infusion-models/
COPY libs/infusion-streams/ /app/libs/infusion-streams/
COPY libs/infusion-common/ /app/libs/infusion-common/

RUN pip install --no-cache-dir \
    /app/libs/infusion-models \
    /app/libs/infusion-streams \
    /app/libs/infusion-common

# Install service
COPY services/<SERVICE_NAME>/ /app/services/<SERVICE_NAME>/
RUN pip install --no-cache-dir /app/services/<SERVICE_NAME>/

# Non-root user
RUN useradd --create-home appuser
USER appuser

# Entrypoint
CMD ["python", "-m", "<service_module>.main"]
```

### 4.3 `docker-compose.yml` — Production Stack

```yaml
version: "3.9"

x-service-defaults: &service-defaults
  restart: unless-stopped
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"
  networks:
    - infusion

services:
  # ════════════════════════════════════════════
  # INFRASTRUCTURE
  # ════════════════════════════════════════════

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redisdata:/data
      - ./config/redis.conf:/usr/local/etc/redis/redis.conf:ro
    command: redis-server /usr/local/etc/redis/redis.conf
    deploy:
      resources:
        limits:
          memory: 768M
        reservations:
          memory: 256M
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    <<: *service-defaults

  postgres:
    image: postgres:16-alpine
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-infusion}
      POSTGRES_USER: ${POSTGRES_USER:-infusion}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD required}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./migrations/init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
    deploy:
      resources:
        limits:
          memory: 768M
        reservations:
          memory: 256M
    command: >
      postgres
        -c shared_buffers=256MB
        -c work_mem=8MB
        -c effective_cache_size=512MB
        -c maintenance_work_mem=64MB
        -c max_connections=50
        -c log_min_duration_statement=100
        -c log_statement=none
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-infusion}"]
      interval: 5s
      timeout: 3s
      retries: 5
    <<: *service-defaults

  # ════════════════════════════════════════════
  # SERVICES (stubs in Phase 1 — only healthcheck)
  # ════════════════════════════════════════════

  ingestion:
    build:
      context: .
      dockerfile: services/ingestion/Dockerfile
    depends_on:
      redis:
        condition: service_healthy
    environment:
      - INFUSION_SERVICE_NAME=ingestion
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_LOG_LEVEL=${LOG_LEVEL:-info}
    deploy:
      resources:
        limits:
          memory: 192M
    <<: *service-defaults

  normalizer:
    build:
      context: .
      dockerfile: services/normalizer/Dockerfile
    depends_on:
      redis:
        condition: service_healthy
    environment:
      - INFUSION_SERVICE_NAME=normalizer
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_LOG_LEVEL=${LOG_LEVEL:-info}
    deploy:
      resources:
        limits:
          memory: 192M
    <<: *service-defaults

  feature-engine:
    build:
      context: .
      dockerfile: services/feature-engine/Dockerfile
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
    environment:
      - INFUSION_SERVICE_NAME=feature-engine
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_DATABASE_URL=postgresql://${POSTGRES_USER:-infusion}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-infusion}
      - INFUSION_LOG_LEVEL=${LOG_LEVEL:-info}
    deploy:
      resources:
        limits:
          memory: 384M
    <<: *service-defaults

  scanner:
    build:
      context: .
      dockerfile: services/scanner/Dockerfile
    depends_on:
      redis:
        condition: service_healthy
    environment:
      - INFUSION_SERVICE_NAME=scanner
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_LOG_LEVEL=${LOG_LEVEL:-info}
    deploy:
      resources:
        limits:
          memory: 192M
    <<: *service-defaults

  sector-intel:
    build:
      context: .
      dockerfile: services/sector-intel/Dockerfile
    depends_on:
      redis:
        condition: service_healthy
    environment:
      - INFUSION_SERVICE_NAME=sector-intel
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_LOG_LEVEL=${LOG_LEVEL:-info}
    deploy:
      resources:
        limits:
          memory: 192M
    <<: *service-defaults

  conviction:
    build:
      context: .
      dockerfile: services/conviction/Dockerfile
    depends_on:
      redis:
        condition: service_healthy
    environment:
      - INFUSION_SERVICE_NAME=conviction
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_LOG_LEVEL=${LOG_LEVEL:-info}
    deploy:
      resources:
        limits:
          memory: 128M
    <<: *service-defaults

  alerter:
    build:
      context: .
      dockerfile: services/alerter/Dockerfile
    depends_on:
      redis:
        condition: service_healthy
    environment:
      - INFUSION_SERVICE_NAME=alerter
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_LOG_LEVEL=${LOG_LEVEL:-info}
    deploy:
      resources:
        limits:
          memory: 128M
    <<: *service-defaults

  ws-gateway:
    build:
      context: .
      dockerfile: services/ws-gateway/Dockerfile
    ports:
      - "8001:8001"
    depends_on:
      redis:
        condition: service_healthy
    environment:
      - INFUSION_SERVICE_NAME=ws-gateway
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_LOG_LEVEL=${LOG_LEVEL:-info}
    deploy:
      resources:
        limits:
          memory: 192M
    <<: *service-defaults

  api:
    build:
      context: .
      dockerfile: services/api/Dockerfile
    ports:
      - "8000:8000"
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
    environment:
      - INFUSION_SERVICE_NAME=api
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_DATABASE_URL=postgresql://${POSTGRES_USER:-infusion}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-infusion}
      - INFUSION_LOG_LEVEL=${LOG_LEVEL:-info}
    deploy:
      resources:
        limits:
          memory: 192M
    <<: *service-defaults

  nse-scraper:
    build:
      context: .
      dockerfile: services/nse-scraper/Dockerfile
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
    environment:
      - INFUSION_SERVICE_NAME=nse-scraper
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_DATABASE_URL=postgresql://${POSTGRES_USER:-infusion}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-infusion}
      - INFUSION_LOG_LEVEL=${LOG_LEVEL:-info}
    deploy:
      resources:
        limits:
          memory: 192M
    <<: *service-defaults

  telegram-bot:
    build:
      context: .
      dockerfile: services/telegram-bot/Dockerfile
    depends_on:
      redis:
        condition: service_healthy
    environment:
      - INFUSION_SERVICE_NAME=telegram-bot
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
      - INFUSION_LOG_LEVEL=${LOG_LEVEL:-info}
    deploy:
      resources:
        limits:
          memory: 128M
    <<: *service-defaults

  scheduler:
    build:
      context: .
      dockerfile: services/scheduler/Dockerfile
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
    environment:
      - INFUSION_SERVICE_NAME=scheduler
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_DATABASE_URL=postgresql://${POSTGRES_USER:-infusion}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-infusion}
      - INFUSION_LOG_LEVEL=${LOG_LEVEL:-info}
    deploy:
      resources:
        limits:
          memory: 96M
    <<: *service-defaults

volumes:
  pgdata:
  redisdata:

networks:
  infusion:
    driver: bridge
```

### 4.4 `docker-compose.dev.yml` — Dev Overrides

```yaml
version: "3.9"

services:
  redis:
    ports:
      - "6379:6379"

  postgres:
    ports:
      - "5432:5432"

  # Dev: mount source + libs as volumes for hot reload
  # Only services actively being developed need this
  ingestion:
    volumes:
      - ./libs:/app/libs:ro
      - ./services/ingestion/src:/app/services/ingestion/src:ro
      - ./config:/app/config:ro
    environment:
      - INFUSION_LOG_LEVEL=debug

  normalizer:
    volumes:
      - ./libs:/app/libs:ro
      - ./services/normalizer/src:/app/services/normalizer/src:ro
      - ./config:/app/config:ro
    environment:
      - INFUSION_LOG_LEVEL=debug
```

Usage: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up`

---

## 5. Redis Bootstrap

### 5.1 Redis Configuration File

File: `config/redis.conf`

```conf
# ═══════════════════════════════════════════════
# INFUSION REDIS CONFIGURATION
# ═══════════════════════════════════════════════

# Memory
maxmemory 512mb
maxmemory-policy noeviction

# Persistence — RDB snapshots only (crash recovery safety net)
save 300 100
appendonly no

# Performance
tcp-keepalive 60
timeout 0
tcp-backlog 511

# Logging
loglevel notice
logfile ""

# Disable unused features
notify-keyspace-events ""
```

### 5.2 Stream Initialization

Streams and consumer groups must be created before any service reads from them.
This is done by a bootstrap script run once on fresh deploy, and idempotently on every startup.

**Stream definitions (source of truth):**

| Stream | MAXLEN ~ | Consumer Groups |
|---|---|---|
| `infusion:stream:tick:raw` | 50000 | `normalizer-cg` |
| `infusion:stream:tick:normalized` | 100000 | `feature-cg`, `dashboard-cg` |
| `infusion:stream:feature:computed` | 50000 | `scanner-cg`, `sector-cg`, `conviction-cg`, `dashboard-cg` |
| `infusion:stream:scan:signals` | 10000 | `conviction-cg`, `audit-cg` |
| `infusion:stream:sector:state` | 20000 | `conviction-cg`, `dashboard-cg` |
| `infusion:stream:conviction:ranked` | 10000 | `alert-cg`, `dashboard-cg` |

**Bootstrap script logic (`scripts/validate_streams.py`):**

```python
"""
Idempotent Redis stream + consumer group bootstrap.
Run on every deployment. Safe to run multiple times.
"""

STREAM_CONFIG = {
    "infusion:stream:tick:raw": {
        "maxlen": 50000,
        "groups": ["normalizer-cg"],
    },
    "infusion:stream:tick:normalized": {
        "maxlen": 100000,
        "groups": ["feature-cg", "dashboard-cg"],
    },
    "infusion:stream:feature:computed": {
        "maxlen": 50000,
        "groups": ["scanner-cg", "sector-cg", "conviction-cg", "dashboard-cg"],
    },
    "infusion:stream:scan:signals": {
        "maxlen": 10000,
        "groups": ["conviction-cg", "audit-cg"],
    },
    "infusion:stream:sector:state": {
        "maxlen": 20000,
        "groups": ["conviction-cg", "dashboard-cg"],
    },
    "infusion:stream:conviction:ranked": {
        "maxlen": 10000,
        "groups": ["alert-cg", "dashboard-cg"],
    },
}

# For each stream:
#   1. XGROUP CREATE <stream> <group> 0 MKSTREAM
#      - MKSTREAM creates the stream if it doesn't exist
#      - If group already exists, catch BUSYGROUP error and continue
#   2. Log result: created / already exists
```

### 5.3 Redis Pub/Sub Channel

One channel exists for config reload notifications:

```
infusion:config:changed   ← PUBLISH on config update, SUBSCRIBE by all services
```

Fire-and-forget by design. If a service misses the notification, it picks up the new config on next restart or periodic check.

---

## 6. PostgreSQL Initialization

### 6.1 Initial Schema

File: `migrations/init.sql`

This SQL runs on first container start via Docker's `docker-entrypoint-initdb.d/`.

```sql
-- ═══════════════════════════════════════════════
-- INFUSION POSTGRESQL INITIALIZATION
-- ═══════════════════════════════════════════════

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- for symbol search

-- ───────────────────────────────────────────────
-- REFERENCE DATA
-- ───────────────────────────────────────────────

CREATE TABLE symbols (
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

CREATE TABLE corporate_actions (
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

CREATE TABLE ohlcv_daily (
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

CREATE TABLE ohlcv_intraday (
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

CREATE TABLE signals (
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

CREATE TABLE sector_daily (
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

CREATE TABLE institutional_flows (
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

CREATE TABLE alert_log (
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

CREATE INDEX idx_ohlcv_daily_sym_date ON ohlcv_daily (symbol, trade_date DESC);
CREATE INDEX idx_signals_grade_time ON signals (conviction_grade, created_at DESC)
    WHERE conviction_grade IN ('A+', 'A');
CREATE INDEX idx_signals_symbol_time ON signals (symbol, created_at DESC);
CREATE INDEX idx_signals_outcome ON signals (outcome_label, created_at DESC)
    WHERE outcome_label IS NOT NULL;
CREATE INDEX idx_sector_daily_date ON sector_daily (trade_date DESC);
CREATE INDEX idx_corp_actions_sym_date ON corporate_actions (symbol, ex_date DESC);
CREATE INDEX idx_symbols_search ON symbols USING gin (symbol gin_trgm_ops);

-- ───────────────────────────────────────────────
-- INITIAL PARTITIONS (current month + next month)
-- ───────────────────────────────────────────────

-- ohlcv_daily: monthly partitions
CREATE TABLE ohlcv_daily_2026_05 PARTITION OF ohlcv_daily
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE ohlcv_daily_2026_06 PARTITION OF ohlcv_daily
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

-- ohlcv_intraday: daily partitions (create a week's worth)
-- Additional partitions auto-created by scheduler job
```

### 6.2 Alembic Setup

```
migrations/
├── alembic.ini
├── env.py
└── versions/
    └── 001_initial_schema.py
```

`alembic.ini` points to `INFUSION_DATABASE_URL` env var.

The `init.sql` handles the bootstrap (Docker first run). Alembic handles all subsequent schema evolution.

### 6.3 Connection Parameters

```
PostgreSQL Docker tuning:
  shared_buffers     = 256MB     ← 50% of container memory limit
  work_mem           = 8MB       ← per-sort memory
  effective_cache_size = 512MB   ← hint to planner
  maintenance_work_mem = 64MB    ← for VACUUM, CREATE INDEX
  max_connections    = 50        ← 12 services × 2 min pool + headroom
  log_min_duration_statement = 100  ← log queries >100ms
  log_statement      = none      ← don't log every query
```

---

## 7. Configuration Management

### 7.1 Config Hierarchy (Precedence: highest → lowest)

```
1. Environment variables          ← secrets, deployment-specific
2. .env file (local dev only)     ← convenience overrides
3. YAML config files              ← runtime-tunable parameters
4. Code defaults                  ← sensible last resort
```

### 7.2 Environment Variable Namespace

All Infusion env vars use the `INFUSION_` prefix.

```
# ═══════════════════════════════════════════════
# .env.example — ALL environment variables
# ═══════════════════════════════════════════════

# ── Infrastructure ──
INFUSION_REDIS_URL=redis://localhost:6379/0
INFUSION_DATABASE_URL=postgresql://infusion:changeme@localhost:5432/infusion

# ── PostgreSQL (used by docker-compose) ──
POSTGRES_DB=infusion
POSTGRES_USER=infusion
POSTGRES_PASSWORD=changeme

# ── Service Identity ──
INFUSION_SERVICE_NAME=<set-per-service>
INFUSION_INSTANCE_ID=<auto-generated-if-empty>

# ── Logging ──
INFUSION_LOG_LEVEL=info
INFUSION_LOG_FORMAT=json
# INFUSION_LOG_FORMAT=console   ← use for local dev (human-readable)

# ── Broker Auth (secrets) ──
INFUSION_UPSTOX_API_KEY=
INFUSION_UPSTOX_API_SECRET=
INFUSION_UPSTOX_REFRESH_TOKEN=
INFUSION_KITE_API_KEY=
INFUSION_KITE_ACCESS_TOKEN=

# ── Telegram ──
INFUSION_TELEGRAM_BOT_TOKEN=
INFUSION_TELEGRAM_CHAT_ID=

# ── AI (optional) ──
INFUSION_GEMINI_API_KEY=

# ── Feature Flags ──
INFUSION_ENABLE_AI_WORKER=false
INFUSION_ENABLE_TELEGRAM=false
```

### 7.3 Pydantic Settings Base Class

Every service inherits from this. Loaded once at startup.

```python
# libs/infusion-common/src/infusion_common/config.py

from pydantic_settings import BaseSettings

class InfusionSettings(BaseSettings):
    """Base settings for all Infusion services."""

    # Infrastructure
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = ""

    # Identity
    service_name: str = "unknown"
    instance_id: str = ""  # auto-generated if empty

    # Logging
    log_level: str = "info"
    log_format: str = "json"  # "json" or "console"

    model_config = {
        "env_prefix": "INFUSION_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }
```

Services extend this with their own fields:

```python
# services/ingestion/src/ingestion/config.py

from infusion_common.config import InfusionSettings

class IngestionSettings(InfusionSettings):
    upstox_api_key: str = ""
    upstox_api_secret: str = ""
    upstox_refresh_token: str = ""
    kite_api_key: str = ""
    kite_access_token: str = ""
```

### 7.4 YAML Config Files

Runtime-tunable parameters. Mounted as Docker volumes. Reloaded on `infusion:config:changed` pub/sub notification.

```yaml
# config/scanners.yaml  — skeleton
strategies:
  breakout:
    enabled: true
    cooldown_seconds: 300
  volume_surge:
    enabled: true
    cooldown_seconds: 300
  momentum:
    enabled: true
    cooldown_seconds: 300
  pre_breakout:
    enabled: true
    cooldown_seconds: 600
  oi_buildup:
    enabled: true
    cooldown_seconds: 300
```

```yaml
# config/sectors.yaml  — skeleton
sectors:
  BANK:
    name: Banking
    constituents: []   # populated by seed script
  IT:
    name: Information Technology
    constituents: []
  # ... remaining sectors added in Phase 2
```

```yaml
# config/conviction_weights.yaml  — skeleton
weights:
  technical_score: 0.35
  volume_score: 0.25
  sector_score: 0.20
  context_score: 0.20
```

```yaml
# config/alerts.yaml  — skeleton
throttle:
  global_max_per_minute: 10
  per_symbol_cooldown_seconds: 300
channels:
  telegram:
    enabled: false
  websocket:
    enabled: true
```

```yaml
# config/instruments.yaml  — skeleton
tiers:
  tier_1:
    mode: full
    description: "NIFTY 50 + top F&O"
    symbols: []  # populated by seed script
  tier_2:
    mode: throttled
    throttle_ms: 500
    symbols: []
  tier_3:
    mode: snapshot
    throttle_ms: 2000
    symbols: []
```

---

## 8. Secrets Management

### 8.1 Strategy

| Environment | Method |
|---|---|
| Local dev | `.env` file (git-ignored) |
| Production | OS environment variables set by deployment script |
| CI | GitHub Actions secrets → env vars |

### 8.2 Secrets Inventory

| Secret | Env Var | Rotation |
|---|---|---|
| PostgreSQL password | `POSTGRES_PASSWORD` | Manual, infrequent |
| Upstox API key | `INFUSION_UPSTOX_API_KEY` | Per-app registration |
| Upstox API secret | `INFUSION_UPSTOX_API_SECRET` | Per-app registration |
| Upstox refresh token | `INFUSION_UPSTOX_REFRESH_TOKEN` | On OAuth re-auth |
| Kite API key | `INFUSION_KITE_API_KEY` | Per-app registration |
| Kite access token | `INFUSION_KITE_ACCESS_TOKEN` | Daily (auto-refreshed) |
| Telegram bot token | `INFUSION_TELEGRAM_BOT_TOKEN` | Per-bot creation |
| Gemini API key | `INFUSION_GEMINI_API_KEY` | Manual |

### 8.3 Rules

1. **Never commit secrets.** `.env` is in `.gitignore`.
2. **Never log secrets.** `structlog` processor strips any field matching `*_token`, `*_secret`, `*_password`, `*_key`.
3. **Never embed secrets in Docker images.** Always injected via environment.
4. **No vault dependency** in Phase 1. Single-user system, env vars are sufficient.

---

## 9. Shared Library Structure

### 9.1 `libs/infusion-models/pyproject.toml`

```toml
[project]
name = "infusion-models"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "msgpack>=1.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/infusion_models"]
```

### 9.2 `libs/infusion-streams/pyproject.toml`

```toml
[project]
name = "infusion-streams"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "infusion-models",
    "redis[hiredis]>=5.0",
    "msgpack>=1.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/infusion_streams"]
```

### 9.3 `libs/infusion-common/pyproject.toml`

```toml
[project]
name = "infusion-common"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "infusion-models",
    "structlog>=24.1",
    "pydantic-settings>=2.2",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/infusion_common"]
```

### 9.4 Service `pyproject.toml` Template

```toml
[project]
name = "infusion-<service-name>"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "infusion-models",
    "infusion-streams",
    "infusion-common",
    # service-specific deps added here
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/<service_module>"]
```

---

## 10. Logging & Metrics Foundation

### 10.1 Logging Architecture

```
structlog (JSON) ──► stdout ──► Docker json-file driver ──► docker logs
```

No external log aggregator in Phase 1. Logs go to stdout. Docker captures them.
Future: pipe to Loki/Grafana when needed.

### 10.2 structlog Configuration

```python
# libs/infusion-common/src/infusion_common/logging.py

import structlog
import logging
import sys

def setup_logging(service_name: str, level: str = "info", fmt: str = "json") -> None:
    """Configure structlog for all Infusion services."""

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_service_name(service_name),
        _strip_secrets,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "console":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
```

### 10.3 Log Format

Every log line contains:

```json
{
  "timestamp": "2026-05-27T09:15:00.123456Z",
  "level": "info",
  "service": "feature-engine",
  "event": "batch_processed",
  "batch_size": 150,
  "latency_ms": 3.2,
  "symbols_updated": 47
}
```

### 10.4 Correlation IDs

Pipeline-wide tracing via `correlation_id` propagated in every stream message:

```
tick:raw message includes:     correlation_id = f"{exchange_ts}-{token}"
Every downstream message copies this correlation_id from its input.
```

This allows tracing a single tick through the entire pipeline via:
```bash
docker logs ingestion 2>&1 | grep "correlation_id.*1716789012345-256265"
```

### 10.5 Latency Timing Decorator

```python
# libs/infusion-common/src/infusion_common/timing.py

import time
import structlog
from functools import wraps

logger = structlog.get_logger()

def measure_latency(operation: str):
    """Decorator that logs operation latency in microseconds."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter_ns()
            result = await func(*args, **kwargs)
            elapsed_us = (time.perf_counter_ns() - start) / 1000
            logger.debug(
                "latency",
                operation=operation,
                latency_us=round(elapsed_us, 1),
            )
            return result
        return wrapper
    return decorator
```

### 10.6 Metrics Strategy (Phase 1 — Minimal)

No Prometheus/Grafana in Phase 1. Instead:

- **Latency**: logged per-operation via `measure_latency` decorator
- **Consumer lag**: logged every 5s by each stream consumer via `XINFO GROUPS`
- **Memory**: Docker `mem_limit` enforces bounds; `docker stats` for monitoring
- **Health**: health check endpoint per service (see Section 13)

Prometheus export is a Phase 6 concern. The log-based metrics established here will be the source data for that export.

---

## 11. Local Development Workflow

### 11.1 First-Time Setup

```bash
# 1. Clone repo
git clone <repo-url> && cd infusion-screener

# 2. Copy env file
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD, optionally set broker tokens

# 3. Start infrastructure only
docker compose up -d redis postgres

# 4. Wait for healthy
docker compose exec redis redis-cli ping        # → PONG
docker compose exec postgres pg_isready         # → accepting connections

# 5. Bootstrap Redis streams
python scripts/validate_streams.py

# 6. Create Python venv (local service development)
uv venv .venv --python 3.12
source .venv/bin/activate   # or .venv\Scripts\activate on Windows

# 7. Install libs + target service
uv pip install -e libs/infusion-models
uv pip install -e libs/infusion-streams
uv pip install -e libs/infusion-common
uv pip install -e services/ingestion   # or whichever service you're working on

# 8. Run service locally (against Dockerized Redis/PG)
python -m ingestion.main
```

### 11.2 Full Stack (Docker)

```bash
# Build and start everything
make up

# View logs
make logs

# View specific service logs
make logs-service SERVICE=feature-engine

# Restart a single service
make restart SERVICE=scanner

# Stop everything
make down

# Wipe data (Redis + PG volumes)
make clean
```

### 11.3 Makefile

```makefile
.PHONY: up down logs restart clean build test lint

# ═══════════════════════════════════════════════
# INFUSION SCREENER — Makefile
# ═══════════════════════════════════════════════

COMPOSE = docker compose
COMPOSE_DEV = docker compose -f docker-compose.yml -f docker-compose.dev.yml

# ── Stack Management ──

up:
	$(COMPOSE) up -d --build

up-dev:
	$(COMPOSE_DEV) up -d --build

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down -v --remove-orphans

build:
	$(COMPOSE) build

# ── Logs ──

logs:
	$(COMPOSE) logs -f --tail=100

logs-service:
	$(COMPOSE) logs -f --tail=100 $(SERVICE)

# ── Service Management ──

restart:
	$(COMPOSE) restart $(SERVICE)

# ── Infrastructure ──

infra:
	$(COMPOSE) up -d redis postgres

bootstrap:
	python scripts/validate_streams.py

migrate:
	cd migrations && alembic upgrade head

# ── Quality ──

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .

typecheck:
	mypy libs/ services/

test:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

# ── Utility ──

redis-cli:
	$(COMPOSE) exec redis redis-cli

psql:
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-infusion} -d $${POSTGRES_DB:-infusion}

status:
	$(COMPOSE) ps
	@echo "───────────────────"
	@echo "Redis streams:"
	$(COMPOSE) exec redis redis-cli KEYS "infusion:stream:*"
	@echo "───────────────────"
	@echo "Consumer group lag:"
	$(COMPOSE) exec redis redis-cli XINFO GROUPS infusion:stream:tick:raw 2>/dev/null || true
```

---

## 12. Startup Order

### 12.1 Dependency Graph

```
LAYER 0 — Infrastructure (no dependencies)
  redis
  postgres

LAYER 1 — Bootstrap (depends on Layer 0)
  validate_streams.py   ← creates streams + consumer groups
  init.sql              ← creates tables (auto via Docker entrypoint)

LAYER 2 — Core Pipeline (depends on Layer 1)
  ingestion      ← depends_on: redis
  normalizer     ← depends_on: redis
  feature-engine ← depends_on: redis, postgres
  scanner        ← depends_on: redis
  sector-intel   ← depends_on: redis
  conviction     ← depends_on: redis

LAYER 3 — Delivery (depends on Layer 2)
  alerter        ← depends_on: redis
  ws-gateway     ← depends_on: redis
  api            ← depends_on: redis, postgres

LAYER 4 — Peripheral (depends on Layer 0)
  nse-scraper    ← depends_on: redis, postgres
  telegram-bot   ← depends_on: redis
  scheduler      ← depends_on: redis, postgres
```

### 12.2 Docker Compose Startup Behavior

Docker Compose `depends_on` with `condition: service_healthy` ensures:

1. **Redis and PostgreSQL start first** and must pass health checks
2. **All services start in parallel** after infrastructure is healthy
3. Services that can't connect to Redis block on their own retry loop (exponential backoff)
4. No ordering is enforced between services — they are all independent stream consumers

### 12.3 Service Boot Sequence (Every Service)

```
1. Load config          → InfusionSettings from env vars
2. Setup logging        → structlog JSON to stdout
3. Connect Redis        → redis.asyncio pool with hiredis
4. Connect PostgreSQL   → asyncpg pool (if service needs PG)
5. Register health      → SET infusion:health:<service> <epoch_ms> EX 30
6. Recover pending      → XAUTOCLAIM for any messages left by previous crash
7. Enter main loop      → XREADGROUP BLOCK 0
8. Heartbeat task       → every 10s: update health key + log consumer lag
9. Shutdown handler     → SIGTERM → drain in-flight → close connections → exit 0
```

---

## 13. Health Check Framework

### 13.1 Architecture

Every service writes a heartbeat to Redis and exposes health status.

```
Service heartbeat:
  Every 10 seconds:
    SET infusion:health:<service_name> <epoch_ms> EX 30
    SET infusion:health:lag:<service_name> <consumer_group_lag> EX 30

  If key expires (TTL 30s), the service is considered dead.
  Two missed heartbeats (20s) = stale. Three (30s) = dead.
```

### 13.2 Health Check Implementation

```python
# libs/infusion-common/src/infusion_common/health.py

import asyncio
import time
import structlog

logger = structlog.get_logger()

class HealthReporter:
    """Background task that reports service health to Redis."""

    def __init__(self, redis, service_name: str, interval: int = 10):
        self.redis = redis
        self.service_name = service_name
        self.interval = interval
        self._task = None
        self._consumer_lag = 0

    def update_lag(self, lag: int) -> None:
        self._consumer_lag = lag

    async def start(self) -> None:
        self._task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                now_ms = int(time.time() * 1000)
                pipe = self.redis.pipeline()
                pipe.set(
                    f"infusion:health:{self.service_name}",
                    str(now_ms),
                    ex=30,
                )
                pipe.set(
                    f"infusion:health:lag:{self.service_name}",
                    str(self._consumer_lag),
                    ex=30,
                )
                await pipe.execute()
            except Exception:
                logger.warning("health_heartbeat_failed", service=self.service_name)
            await asyncio.sleep(self.interval)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
```

### 13.3 System Health Endpoint

The `api` service exposes `GET /health` which aggregates all service health:

```json
{
  "status": "healthy",
  "timestamp": "2026-05-27T09:15:30Z",
  "services": {
    "ingestion": {"status": "healthy", "lag": 0, "last_heartbeat_ms": 1716789030000},
    "normalizer": {"status": "healthy", "lag": 12, "last_heartbeat_ms": 1716789028000},
    "feature-engine": {"status": "healthy", "lag": 5, "last_heartbeat_ms": 1716789029000}
  },
  "redis": {"status": "connected", "memory_used_mb": 130},
  "postgres": {"status": "connected", "active_connections": 8}
}
```

Service status resolution:

| Condition | Status |
|---|---|
| Heartbeat < 10s old, lag < 100 | `healthy` |
| Heartbeat 10-20s old, or lag 100-500 | `degraded` |
| Heartbeat > 20s old, or lag > 500 | `unhealthy` |
| No heartbeat key exists | `dead` |

---

## 14. Initial CI Workflow

### 14.1 GitHub Actions — `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install ruff
      - run: ruff check .
      - run: ruff format --check .

  typecheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install mypy pydantic pydantic-settings structlog msgpack redis
      - run: pip install -e libs/infusion-models -e libs/infusion-streams -e libs/infusion-common
      - run: mypy libs/

  test:
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
        options: --health-cmd "redis-cli ping" --health-interval 5s
      postgres:
        image: postgres:16-alpine
        ports: ["5432:5432"]
        env:
          POSTGRES_DB: infusion_test
          POSTGRES_USER: infusion
          POSTGRES_PASSWORD: testpass
        options: --health-cmd "pg_isready -U infusion" --health-interval 5s
    env:
      INFUSION_REDIS_URL: redis://localhost:6379/0
      INFUSION_DATABASE_URL: postgresql://infusion:testpass@localhost:5432/infusion_test
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e libs/infusion-models -e libs/infusion-streams -e libs/infusion-common
      - run: pip install pytest pytest-asyncio
      - run: pytest tests/unit/ -v

  docker-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose build
```

---

## 15. Smoke-Test Procedure

### 15.1 Phase 1 Smoke Test Checklist

Run after every fresh deployment or significant change.

```
PHASE 1 SMOKE TEST
══════════════════════════════════════════════════

INFRASTRUCTURE
  [ ] docker compose up -d redis postgres
  [ ] redis-cli PING → PONG
  [ ] pg_isready → accepting connections
  [ ] psql → \dt shows all tables created
  [ ] Redis streams exist: KEYS infusion:stream:*  → 6 streams
  [ ] Consumer groups exist: XINFO GROUPS infusion:stream:tick:raw → normalizer-cg

SERVICES
  [ ] docker compose up -d  → all 14 containers start
  [ ] docker compose ps → all show "Up" / "healthy"
  [ ] No OOMKilled containers (docker inspect --format='{{.State.OOMKilled}}')
  [ ] Logs show structured JSON (docker logs ingestion --tail=5)
  [ ] Each service writes health key:
      redis-cli KEYS "infusion:health:*" → 12 keys

HEALTH ENDPOINT
  [ ] curl http://localhost:8000/health → 200 + JSON with all services

CONNECTIVITY
  [ ] Services connect to Redis (check logs for "redis_connected")
  [ ] Services connect to PostgreSQL where needed (check logs)
  [ ] No connection refused errors in any service logs

GRACEFUL SHUTDOWN
  [ ] docker compose down → all services exit 0 (no SIGKILL)
  [ ] Shutdown completes within 10 seconds
  [ ] No data loss warnings in logs during shutdown

RESOURCE USAGE
  [ ] docker stats → no service exceeds 50% of its memory limit at idle
  [ ] Redis memory: redis-cli INFO memory → used_memory < 50MB at idle
  [ ] PostgreSQL: no idle-in-transaction connections
```

### 15.2 Automated Smoke Test Script

```bash
#!/bin/bash
# scripts/smoke_test_phase1.sh

set -e

echo "═══ PHASE 1 SMOKE TEST ═══"

echo "── Infrastructure ──"
docker compose exec redis redis-cli PING | grep -q PONG && echo "✓ Redis PING" || echo "✗ Redis PING"
docker compose exec postgres pg_isready -U infusion && echo "✓ PostgreSQL ready" || echo "✗ PostgreSQL not ready"

echo "── Streams ──"
STREAM_COUNT=$(docker compose exec redis redis-cli KEYS "infusion:stream:*" | wc -l)
[ "$STREAM_COUNT" -ge 6 ] && echo "✓ $STREAM_COUNT streams exist" || echo "✗ Only $STREAM_COUNT streams (expected ≥6)"

echo "── Services ──"
RUNNING=$(docker compose ps --services --filter status=running | wc -l)
echo "  $RUNNING services running"

echo "── Health Keys ──"
HEALTH_KEYS=$(docker compose exec redis redis-cli KEYS "infusion:health:*" | grep -v lag | wc -l)
echo "  $HEALTH_KEYS health heartbeats"

echo "── Memory ──"
REDIS_MEM=$(docker compose exec redis redis-cli INFO memory | grep used_memory_human | cut -d: -f2 | tr -d '\r')
echo "  Redis memory: $REDIS_MEM"

echo "── Health Endpoint ──"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo "000")
[ "$HTTP_CODE" = "200" ] && echo "✓ /health → 200" || echo "✗ /health → $HTTP_CODE"

echo "═══ SMOKE TEST COMPLETE ═══"
```

---

## Phase 1 Boundary

This document defines everything needed to:

1. ✅ Clone the repo and have a working structure
2. ✅ `docker compose up` and have all infrastructure + service stubs running
3. ✅ Redis streams and consumer groups bootstrapped
4. ✅ PostgreSQL schema initialized with all tables
5. ✅ Structured JSON logging from every service
6. ✅ Health heartbeats from every service
7. ✅ System health endpoint at `/health`
8. ✅ CI pipeline running lint, typecheck, and tests
9. ✅ Smoke test validating the entire foundation

**No service logic exists yet.** Every service `main.py` is a stub that:
- Loads config
- Connects to Redis
- Starts health reporter
- Enters an idle loop (XREADGROUP BLOCK 0 on its input stream)
- Handles SIGTERM gracefully

**Next:** `IMPLEMENTATION-PHASE-2-SIGNAL-PIPELINE.md` — First vertical slice: ingestion → normalizer → feature engine producing real data through the pipeline.
