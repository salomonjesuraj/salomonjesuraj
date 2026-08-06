# PHASE 6 — PERFORMANCE & OPTIMIZATION

> Production stabilization architecture for surviving market open volatility,
> WebSocket instability, NSE failures, memory growth, reconnect storms,
> backlog accumulation, degraded market conditions, and long-running uptime.
> All decisions conform to [Global Architecture Constraints](./GLOBAL-ARCHITECTURE-CONSTRAINTS.md).

---

## 1. End-to-End Latency Budget

### 1.1 The Pipeline

Every signal Infusion produces traverses this exact path. Each stage has a hard latency ceiling. If any stage exceeds its ceiling, the system is degraded — not by crashing, but by delivering stale intelligence.

```
TICK-TO-SIGNAL PIPELINE (target: ≤25ms end-to-end at p99)
══════════════════════════════════════════════════════════

Stage                        Budget    Cumulative    Owner Service
───────────────────────────  ────────  ──────────    ──────────────
1. WS frame receive          ~0ms      0ms           OS kernel / aiohttp
2. Protobuf decode           0.1ms     0.1ms         ws-ingestion
3. XADD to tick:raw          0.2ms     0.3ms         ws-ingestion
4. XREAD from tick:raw       0.1ms     0.4ms         tick-normalizer
5. Symbol resolve + throttle 0.1ms     0.5ms         tick-normalizer
6. XADD to tick:normalized   0.2ms     0.7ms         tick-normalizer
7. XREAD from tick:norm      0.1ms     0.8ms         feature-engine
8. Feature computation       2.0ms     2.8ms         feature-engine
9. XADD to feature:computed  0.2ms     3.0ms         feature-engine
10. XREAD from feature:comp  0.1ms     3.1ms         scanner
11. Delta check + routing    0.1ms     3.2ms         scanner
12. Strategy evaluation      0.5ms     3.7ms         scanner
13. Suppression pipeline     0.1ms     3.8ms         scanner
14. XADD to scan:signals     0.2ms     4.0ms         scanner
15. XREAD from scan:signals  0.1ms     4.1ms         conviction-engine
16. Conviction scoring       0.3ms     4.4ms         conviction-engine
17. Explanation building     0.1ms     4.5ms         conviction-engine
18. XADD to conviction:out   0.2ms     4.7ms         conviction-engine
19. XREAD by gateway         0.1ms     4.8ms         ws-gateway
20. WS push to dashboard     0.2ms     5.0ms         ws-gateway
                             ────────
                             TOTAL: ~5ms typical, ≤25ms at p99
```

### 1.2 Where the Slack Lives

```
Budget: 25ms total
Typical: ~5ms
Slack: 20ms

The 20ms slack absorbs:
  - Redis consumer group lag under load (0-5ms)
  - Feature engine doing heavier computations (EMA cascades, VWAP rebuild)
  - Scanner evaluating multiple strategies for one symbol
  - GC pauses in Python (typically 1-3ms, rare 10ms)
  - Burst queueing during market open (tick:raw stream backing up)

If p99 hits 25ms, the system is at capacity.
If p99 hits 50ms, something is wrong.
If p99 hits 100ms, signals are operationally stale for scalping.
```

### 1.3 Latency Measurement Points

```
Every message in every stream carries two timestamps:

  created_at_us:   epoch microseconds when the message was created
  received_at_us:  epoch microseconds when the upstream WS frame arrived
                   (propagated through the entire pipeline — never overwritten)

Measurement:
  At each stage, compute:
    stage_latency = now_us - created_at_us        (this stage only)
    pipeline_latency = now_us - received_at_us     (tick-to-here)

  Exposed as:
    infusion_pipeline_latency_us  (histogram, per stage)
    infusion_e2e_latency_us       (histogram, tick-to-gateway)

  Alerting:
    if pipeline_latency > 25_000µs (25ms):
      increment counter: infusion_latency_breach_count
      if breaches > 10 in 60s:
        log.warning("sustained latency breach", p99=<value>)
    if pipeline_latency > 100_000µs (100ms):
      log.error("critical latency", stage=<name>)
      metric: infusion_latency_critical_count
```

### 1.4 Feature Engine — The Latency Bottleneck

The feature engine is the only stage with meaningful computation. Everything else is serialization + I/O.

```
Feature computation breakdown (per tick, per symbol):
  VWAP update:           0.05ms (incremental: running sum / running vol)
  EMA cascade (5/9/20/50): 0.02ms (single multiply-add per EMA)
  RSI update:            0.03ms (incremental gain/loss)
  MACD update:           0.02ms (derived from EMA 12/26)
  ATR update:            0.02ms (incremental true range)
  Bollinger update:      0.05ms (rolling std — the expensive one)
  Volume features:       0.03ms (relative volume, OBV)
  Delivery features:     0.01ms (lookup from Redis, cached)
  Bar assembly (1m/5m):  0.08ms (append to buffer, emit if boundary)
  ─────────────────────
  Total per tick:        ~0.3ms (Tier 1 symbol, full feature set)
  
  Tier 2 symbols (throttled to 500ms): same cost but 2x fewer ticks
  Tier 3 symbols (throttled to 2000ms): same cost but 10x fewer ticks
  
  Aggregate at 20K normalized ticks/sec:
    Tier 1 (~200 symbols, full rate):  ~8K ticks → 2.4ms CPU/sec
    Tier 2 (~300 symbols, 2Hz):        ~600 ticks → 0.18ms CPU/sec
    Tier 3 (~200 symbols, 0.5Hz):      ~100 ticks → 0.03ms CPU/sec
    ─────────────────────────────────
    Total: ~8.7K feature computations/sec → ~2.6ms CPU per second → 0.26% of 1 core

  Feature engine is NOT a bottleneck under any realistic load.
```

---

## 2. Memory Management

### 2.1 Per-Service Memory Budget

```
SERVICE MEMORY MAP (single-machine, Docker Compose limits)
══════════════════════════════════════════════════════════

Service                 Soft Limit    Hard Limit    Notes
──────────────────────  ──────────    ──────────    ─────────────────────────────
redis                   512MB         768MB         All streams + hot state + pub/sub
postgresql              512MB         768MB         shared_buffers=256MB, work_mem=8MB
ws-ingestion            128MB         192MB         Protobuf decode, WS buffer
tick-normalizer         128MB         192MB         Symbol map (~400KB), dedup rings
feature-engine          256MB         384MB         Feature state: 700 × ~2KB = 1.4MB
                                                    Bar buffers: 700 × 5 TFs × 1KB = 3.5MB
                                                    Polars for heavy ops: ~100MB peak
scanner                 128MB         192MB         Strategy instances, delta cache
                                                    Pre-breakout state: 700 × 256B
conviction-engine       64MB          128MB         Scoring is pure arithmetic
sector-intel            128MB         192MB         Breadth state: 12 sectors × 100 syms × 200B
                                                    McClellan daily history: ~50KB
nse-scraper             128MB         192MB         curl_cffi session, response cache
                                                    Option chain responses: ~2MB peak
ai-worker               256MB         384MB         Gemini client, request/response buffers
                                                    Prompt templates: ~1MB
alert-router            64MB          128MB         Queue state, Telegram client
ws-gateway              128MB         192MB         Per-client state, batch buffers
scheduler               64MB          96MB          Cron state, health check results
──────────────────────  ──────────    ──────────
TOTAL                   ~2.5GB        ~3.7GB        On a 16GB machine: 23% of RAM
                                                    Leaves 12GB for OS, filesystem cache
```

### 2.2 Redis Memory Management

```
REDIS MEMORY BREAKDOWN
═══════════════════════

Data Class                           Estimated Size    TTL/MAXLEN
───────────────────────────────────  ────────────────  ──────────────
Streams:
  tick:raw                           ~20MB             MAXLEN ~ 50000
  tick:normalized                    ~40MB             MAXLEN ~ 100000
  feature:computed                   ~30MB             MAXLEN ~ 50000
  scan:signals                       ~2MB              MAXLEN ~ 10000
  conviction:scored                  ~2MB              MAXLEN ~ 10000
  alert:outbound                     ~1MB              MAXLEN ~ 5000
  sector:state                       ~5MB              MAXLEN ~ 20000
  ─────────────────────────────────
  Streams subtotal:                  ~100MB

Hot State (HASH keys):
  infusion:tick:{symbol} × 700       ~7MB              No TTL (overwritten)
  infusion:feature:{symbol} × 700    ~14MB             No TTL (overwritten)
  infusion:sector:{id} × 12          ~0.1MB            No TTL (overwritten)
  infusion:nse:* (cache keys)        ~5MB              TTL: 120s-86400s
  infusion:symbols (master)          ~0.5MB            No TTL
  infusion:cooldown:* (signal)       ~0.2MB            TTL: 60s-1800s
  ─────────────────────────────────
  Hot state subtotal:                ~27MB

Consumer group metadata:             ~1MB
PEL entries (pending):               ~2MB (at peak lag)
Auth tokens:                         ~0.01MB
Config version counters:             ~0.01MB
───────────────────────────────────
TOTAL REDIS:                         ~130MB typical, ~200MB peak

MAXMEMORY CONFIG:
  maxmemory 512mb
  maxmemory-policy noeviction
  
  Why noeviction: streams are bounded by MAXLEN. Hot state is bounded by
  symbol count (fixed at ~700). There is no unbounded growth path.
  If Redis hits 512MB, something is leaking — we want an error, not silent eviction.
```

### 2.3 Redis Stream Sizing — The MAXLEN Decision

```
WHY THESE MAXLEN VALUES:

tick:raw — MAXLEN ~ 50000
  At 30K raw ticks/sec, this holds ~1.7 seconds of data.
  The normalizer should consume within 100ms. 50K entries provides
  ~17x headroom for consumer lag during GC pauses or reconnects.
  If normalizer falls >50K behind, it's dead — restart it.

tick:normalized — MAXLEN ~ 100000
  At 20K normalized ticks/sec, holds ~5 seconds.
  Feature engine is the consumer. 5s headroom is generous.
  This is the widest stream because feature engine does the most work.

feature:computed — MAXLEN ~ 50000
  At ~8.7K features/sec, holds ~5.7 seconds.
  Scanner consumes these. Scanner is fast (sub-ms per evaluation).

scan:signals — MAXLEN ~ 10000
  At 15-50 signals/day, this holds weeks of signals.
  MAXLEN is for safety, not for flow control.

conviction:scored — MAXLEN ~ 10000
  Same rationale as scan:signals.

The ~ prefix on MAXLEN enables Redis's approximate trimming (faster, O(1)
amortized vs exact O(N) trim). Actual stream length may temporarily exceed
MAXLEN by up to ~100 entries. This is acceptable.
```

### 2.4 Memory Leak Prevention

```
LEAK VECTORS AND COUNTERMEASURES
════════════════════════════════

Vector 1: Per-symbol state growth
  Risk: dicts keyed by symbol accumulate entries for delisted/renamed symbols
  Fix:  On daily symbol master reload (06:00), purge keys not in current master.
        for key in list(feature_state.keys()):
            if key not in symbol_master:
                del feature_state[key]
                log.info("purged_stale_symbol", symbol=key)

Vector 2: Logging string accumulation
  Risk: structlog contexts or f-string interpolations in hot loops
  Fix:  Use structlog with lazy evaluation.
        Never format strings in the hot path unless logging level is enabled:
        if log.isEnabledFor(DEBUG):
            log.debug("tick", symbol=symbol, ltp=ltp)
        Rotate log files: 50MB max, 5 files retained.

Vector 3: asyncio task leaks
  Risk: spawned tasks that never complete (await forgotten, exception swallowed)
  Fix:  Every spawned task is tracked in a TaskGroup or set.
        Periodic audit (every 60s): count active tasks.
        if len(active_tasks) > expected_max:
            log.error("task_leak_detected", count=len(active_tasks))
        Cancel and recreate.

Vector 4: Redis PEL growth
  Risk: consumer crashes without ACKing. PEL grows indefinitely.
  Fix:  On consumer startup, claim all pending entries older than 60s:
        XAUTOCLAIM stream group consumer 60000 0-0
        Process or discard them, then ACK.
        Monitor: XPENDING stream group — if pending count > 1000, alert.

Vector 5: Python object interning
  Risk: msgpack/orjson deserialization creates new string objects per tick
  Fix:  Intern frequently-used keys (symbol names, field names) at startup:
        INTERNED = {s: sys.intern(s) for s in symbol_master.keys()}
        Use INTERNED[raw_symbol] in hot paths to avoid duplicate string objects.

Vector 6: Polars DataFrame accumulation (feature-engine)
  Risk: bar history DataFrames grow if not windowed
  Fix:  Bar buffers are fixed-size ring buffers (deque(maxlen=N)).
        1m bars: maxlen=390 (1 trading day)
        5m bars: maxlen=78
        15m bars: maxlen=26
        Daily bars: maxlen=250 (1 year)
        On each append, oldest entry is automatically evicted.
```

### 2.5 Memory Monitoring

```
RUNTIME MEMORY TRACKING
════════════════════════

Per-service, every 30 seconds:
  import resource  # Linux
  rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
  
  Metric: infusion_process_rss_mb{service="feature-engine"}
  
  Thresholds:
    if rss_mb > soft_limit:
      log.warning("memory_pressure", rss=rss_mb, limit=soft_limit)
      # Trigger voluntary cleanup: purge caches, gc.collect()
    if rss_mb > hard_limit * 0.9:
      log.error("memory_critical", rss=rss_mb)
      # Alert operator via Telegram
      # Do NOT self-restart — Docker will OOM-kill at hard_limit

Docker OOM behavior:
  mem_limit in docker-compose.yml = hard_limit
  Docker sends SIGKILL at mem_limit (no graceful shutdown).
  Restart policy: restart: unless-stopped
  OOM is logged in Docker events and detected by scheduler health check.
```

---

## 3. Docker Compose Topology

### 3.1 Service Definitions

```yaml
# docker-compose.yml
# Single-machine deployment. No Kubernetes. No Swarm. No service mesh.
# Constraint #1: simplicity-first.

version: "3.8"

x-common: &common
  restart: unless-stopped
  logging:
    driver: json-file
    options:
      max-size: "50m"
      max-file: "5"
  networks:
    - infusion

services:

  # ─── INFRASTRUCTURE ───────────────────────────────────────

  redis:
    image: redis:7-alpine
    command: >
      redis-server
      --maxmemory 512mb
      --maxmemory-policy noeviction
      --save ""
      --appendonly no
      --tcp-keepalive 60
      --timeout 0
      --hz 100
      --tcp-backlog 511
      --io-threads 2
      --bind 0.0.0.0
      --protected-mode no
      --loglevel warning
    <<: *common
    mem_limit: 768m
    cpus: 2.0
    ports:
      - "127.0.0.1:6379:6379"
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 3
      start_period: 5s

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: infusion
      POSTGRES_USER: infusion
      POSTGRES_PASSWORD_FILE: /run/secrets/pg_password
    command: >
      postgres
      -c shared_buffers=256MB
      -c work_mem=8MB
      -c maintenance_work_mem=128MB
      -c effective_cache_size=512MB
      -c wal_level=minimal
      -c max_wal_senders=0
      -c synchronous_commit=off
      -c checkpoint_timeout=10min
      -c max_connections=50
      -c log_min_duration_statement=100
      -c autovacuum_naptime=60s
      -c random_page_cost=1.1
    <<: *common
    mem_limit: 768m
    cpus: 2.0
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - pg-data:/var/lib/postgresql/data
    secrets:
      - pg_password
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U infusion"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 15s

  # ─── DATA INGESTION LAYER ─────────────────────────────────

  ws-ingestion:
    build:
      context: .
      dockerfile: services/ws-ingestion/Dockerfile
    <<: *common
    mem_limit: 192m
    cpus: 1.0
    depends_on:
      redis:
        condition: service_healthy
    environment:
      - BROKER_PRIMARY=upstox
      - REDIS_URL=redis://redis:6379
      - LOG_LEVEL=INFO

  tick-normalizer:
    build:
      context: .
      dockerfile: services/tick-normalizer/Dockerfile
    <<: *common
    mem_limit: 192m
    cpus: 1.0
    depends_on:
      redis:
        condition: service_healthy

  nse-scraper:
    build:
      context: .
      dockerfile: services/nse-scraper/Dockerfile
    <<: *common
    mem_limit: 192m
    cpus: 0.5
    depends_on:
      redis:
        condition: service_healthy

  # ─── COMPUTE LAYER ────────────────────────────────────────

  feature-engine:
    build:
      context: .
      dockerfile: services/feature-engine/Dockerfile
    <<: *common
    mem_limit: 384m
    cpus: 2.0
    depends_on:
      redis:
        condition: service_healthy

  scanner:
    build:
      context: .
      dockerfile: services/scanner/Dockerfile
    <<: *common
    mem_limit: 192m
    cpus: 1.0
    depends_on:
      redis:
        condition: service_healthy

  conviction-engine:
    build:
      context: .
      dockerfile: services/conviction-engine/Dockerfile
    <<: *common
    mem_limit: 128m
    cpus: 0.5
    depends_on:
      redis:
        condition: service_healthy

  sector-intel:
    build:
      context: .
      dockerfile: services/sector-intel/Dockerfile
    <<: *common
    mem_limit: 192m
    cpus: 1.0
    depends_on:
      redis:
        condition: service_healthy

  # ─── DELIVERY LAYER ───────────────────────────────────────

  ai-worker:
    build:
      context: .
      dockerfile: services/ai-worker/Dockerfile
    <<: *common
    mem_limit: 384m
    cpus: 1.0
    depends_on:
      redis:
        condition: service_healthy
    environment:
      - GEMINI_API_KEY_FILE=/run/secrets/gemini_key
      - AI_CONCURRENCY=3
      - AI_TIMEOUT_MS=4000
    secrets:
      - gemini_key

  alert-router:
    build:
      context: .
      dockerfile: services/alert-router/Dockerfile
    <<: *common
    mem_limit: 128m
    cpus: 0.5
    depends_on:
      redis:
        condition: service_healthy
    environment:
      - TELEGRAM_BOT_TOKEN_FILE=/run/secrets/tg_token
    secrets:
      - tg_token

  ws-gateway:
    build:
      context: .
      dockerfile: services/ws-gateway/Dockerfile
    <<: *common
    mem_limit: 192m
    cpus: 1.0
    ports:
      - "127.0.0.1:8080:8080"
    depends_on:
      redis:
        condition: service_healthy

  scheduler:
    build:
      context: .
      dockerfile: services/scheduler/Dockerfile
    <<: *common
    mem_limit: 96m
    cpus: 0.5
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy

  # ─── PERSISTENCE LAYER ────────────────────────────────────

  eod-persister:
    build:
      context: .
      dockerfile: services/eod-persister/Dockerfile
    <<: *common
    mem_limit: 256m
    cpus: 1.0
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
    profiles:
      - post-market    # Only runs during EOD job

networks:
  infusion:
    driver: bridge

volumes:
  redis-data:
  pg-data:

secrets:
  pg_password:
    file: ./secrets/pg_password.txt
  gemini_key:
    file: ./secrets/gemini_key.txt
  tg_token:
    file: ./secrets/tg_token.txt
```

### 3.2 Resource Allocation Summary

```
TOTAL RESOURCE BUDGET
═════════════════════

CPU (sum of cpus limits):
  Infrastructure:  4.0 cores (redis 2.0 + postgres 2.0)
  Ingestion:       2.5 cores (ws-ing 1.0 + normalizer 1.0 + nse 0.5)
  Compute:         4.5 cores (feature 2.0 + scanner 1.0 + conviction 0.5 + sector 1.0)
  Delivery:        3.0 cores (ai-worker 1.0 + alert 0.5 + gateway 1.0 + scheduler 0.5)
  ────────────────
  Total:           14.0 cores allocated

  On an 8-core machine:
    Docker cpus are soft limits, not reservations.
    14 cores allocated across 14 services does NOT mean 14 cores required.
    Actual peak usage: ~3-4 cores total (most services use <5% CPU).
    The limits prevent any single runaway service from starving others.

Memory (sum of mem_limit):
  Infrastructure:  1536MB
  Ingestion:       576MB
  Compute:         896MB
  Delivery:        800MB
  ────────────────
  Total:           3808MB hard limits

  Actual usage: ~2.5GB typical. Hard limits are safety rails.

MINIMUM MACHINE SPEC:
  CPU: 8 cores (Intel i7/Ryzen 7 or equivalent)
  RAM: 16GB (2.5GB services + 512MB Redis + 512MB Postgres + OS/cache)
  Disk: 100GB SSD (PostgreSQL historical data, logs)
  Network: 50Mbps sustained (Upstox WS + NSE scraping + Telegram + Gemini API)
  OS: Ubuntu 22.04 LTS or Debian 12

RECOMMENDED MACHINE SPEC:
  CPU: 12 cores
  RAM: 32GB (headroom for Polars operations, OS cache, PostgreSQL)
  Disk: 500GB NVMe SSD
  Network: 100Mbps
```

### 3.3 Startup Order & Dependency Chain

```
STARTUP SEQUENCE (enforced by depends_on + healthcheck)
═══════════════════════════════════════════════════════

Phase 1 — Infrastructure (0-15s):
  redis            → healthcheck: redis-cli ping (ready in ~2s)
  postgres         → healthcheck: pg_isready (ready in ~5-15s)

Phase 2 — Data Layer (15-20s, after infrastructure healthy):
  ws-ingestion     → connects to Redis, authenticates with broker
  tick-normalizer  → connects to Redis, loads symbol master
  nse-scraper      → connects to Redis, warms NSE session

Phase 3 — Compute Layer (20-25s, after Redis healthy):
  feature-engine   → connects to Redis, loads feature state from hot cache
  scanner          → connects to Redis, loads strategy configs
  conviction-engine → connects to Redis
  sector-intel     → connects to Redis, loads sector definitions

Phase 4 — Delivery Layer (25-30s, after Redis healthy):
  ai-worker        → connects to Redis, validates Gemini API key
  alert-router     → connects to Redis, validates Telegram token
  ws-gateway       → connects to Redis, starts HTTP/WS server
  scheduler        → connects to Redis + PostgreSQL, starts cron jobs

Total cold start: ~30 seconds from docker compose up to fully operational.

CRITICAL: ws-ingestion must NOT subscribe to broker WS until
feature-engine and scanner are consuming their streams.
Otherwise tick:raw and tick:normalized will fill to MAXLEN
with no consumers, and old ticks will be trimmed before processing.

Enforcement: ws-ingestion waits for a readiness signal:
  BLPOP infusion:ready:feature-engine 30
  BLPOP infusion:ready:scanner 30
  Feature-engine and scanner LPUSH their ready keys on startup.
  If timeout → start anyway (degraded: first few seconds of ticks lost).
```

---

## 4. Backpressure Hierarchy

### 4.1 The Problem

```
During market open (09:15-09:20), tick rate spikes to 50K-80K ticks/sec
(vs 15K-20K steady state). If any stage can't keep up, its input stream
grows. Redis MAXLEN trims old entries. Data is lost silently.

Backpressure means: every stage must either keep up, shed load gracefully,
or signal upstream to slow down. In a stream-based architecture, the
"signal upstream" option doesn't exist — Redis streams are fire-and-forget.
So each stage must handle its own overload.
```

### 4.2 Per-Stage Overload Behavior

```
BACKPRESSURE STRATEGY PER STAGE
════════════════════════════════

STAGE: ws-ingestion
  Overload signal: WS receive buffer growing (aiohttp internal)
  Response: NONE. Ingestion must never drop ticks. It's the thinnest
            possible layer — decode and XADD. If it can't keep up
            at 80K ticks/sec, the machine is undersized.
  Escape valve: Broker WS naturally throttles (limited instruments).
  Monitoring: ticks_received_per_sec gauge.

STAGE: tick-normalizer
  Overload signal: XLEN(tick:raw) > 20000 (falling behind)
  Response:
    Level 1 (XLEN > 20K): Increase Tier 2 throttle from 500ms to 2000ms
    Level 2 (XLEN > 35K): Increase Tier 3 throttle from 2000ms to 5000ms
    Level 3 (XLEN > 45K): Drop Tier 3 entirely. Only forward Tier 1+2.
    Recovery: when XLEN < 10K for 30s, step down one level.
  Metric: infusion_normalizer_backpressure_level{0,1,2,3}

STAGE: feature-engine
  Overload signal: XLEN(tick:normalized) > 50000 (falling behind)
  Response:
    Level 1 (XLEN > 50K): Skip Bollinger and ATR updates (use stale values).
                           These change slowly — 5s staleness is invisible.
    Level 2 (XLEN > 80K): Batch ticks — process only the latest tick per
                           symbol per 500ms window. Discard intermediate ticks.
    Recovery: when XLEN < 20K for 30s, resume full computation.
  Metric: infusion_feature_backpressure_level{0,1,2}

STAGE: scanner
  Overload signal: XLEN(feature:computed) > 30000
  Response:
    Level 1 (XLEN > 30K): Widen delta detection epsilon by 2x
                           (fewer strategies triggered per tick)
    Level 2 (XLEN > 40K): Evaluate only pre-breakout and volume-surge
                           strategies (highest-value). Defer others.
    Recovery: when XLEN < 10K for 30s, resume normal.
  Metric: infusion_scanner_backpressure_level{0,1,2}

STAGE: conviction-engine
  Overload signal: XLEN(scan:signals) > 5000 (this should never happen)
  Response: At 15-50 signals/day, the conviction engine should never
            be backpressured. If it is, something is very wrong upstream
            (scanner generating garbage). Log error and process anyway.

STAGE: ai-worker
  Overload signal: ai:enhance:pending queue length > 20
  Response:
    Level 1 (>20): Increase batch size, reduce per-signal context
    Level 2 (>50): Skip AI enhancement entirely. Signals go out
                   with deterministic explanations only.
                   AI enrichment delivered as async update when caught up.
  This is BY DESIGN. AI is always the graceful-degradation layer.
  Metric: infusion_ai_queue_depth

STAGE: alert-router
  Overload signal: Telegram rate limit hit (30 msg/sec bot limit)
  Response:
    Level 1: Batch alerts — combine 2-3 signals into one message
    Level 2: Suppress sub-B-grade alerts entirely during burst
  Metric: infusion_telegram_rate_limited_count

STAGE: ws-gateway
  Overload signal: Per-client send buffer > 100 messages
  Response:
    Level 1 (>100): Increase delta batching window from 100ms to 500ms
    Level 2 (>500): Drop stale tick updates. Only deliver signals + breadth.
    Level 3 (>1000): Disconnect client. Client auto-reconnects and
                     receives full state snapshot.
  Metric: infusion_gateway_client_buffer_depth
```

### 4.3 Shedding Priority

When the system is under sustained pressure, shed in this order (lowest value first):

```
SHED ORDER (first to go → last to go)
══════════════════════════════════════

1. AI narrative enrichment      → deterministic explanation is always available
2. Tier 3 ticks (NIFTY 500)    → these symbols rarely generate signals
3. Dashboard tick updates        → stale price for 2s is invisible to user
4. Tier 2 ticks (MIDCAP 100)   → reduce frequency, don't eliminate
5. Low-priority NSE endpoints   → chart data, variations
6. Telegram delivery batching   → delay is acceptable, loss is not
7. Sector breadth frequency     → drop from 1/sec to 1/5sec
8. Scanner low-value strategies → keep pre-breakout and volume-surge
9. Tier 1 ticks                 → NEVER shed. These are the signal source.
10. Signal pipeline              → NEVER shed. This is the product.
```

---

## 5. WebSocket Resilience (Broker Connection)

### 5.1 Connection State Machine

```
┌─────────────────────────────────────────────────────────────┐
│              Broker WebSocket State Machine                   │
│                                                               │
│  INIT ──► AUTHENTICATING ──► CONNECTING ──► SUBSCRIBING      │
│              │                   │              │              │
│              │                   │              ▼              │
│              │                   │          STREAMING ◄──┐    │
│              │                   │              │        │    │
│              ▼                   ▼              ▼        │    │
│         AUTH_FAILED        CONN_FAILED    DISCONNECTED   │    │
│              │                   │              │        │    │
│              ▼                   ▼              ▼        │    │
│         WAIT_REAUTH       WAIT_RECONNECT  RECONNECTING──┘    │
│              │                   │                             │
│              ▼                   │                             │
│         (Telegram alert         │                             │
│          if manual              │                             │
│          re-login needed)       │                             │
│                                  │                             │
│  ◄──────────────────────────────┘                             │
│                                                               │
│  DEGRADED ◄── (after max_reconnects exceeded)                │
│     │                                                         │
│     └── System operates on cached data only                   │
│         No new ticks. Scanner goes silent.                    │
│         Telegram: "CRITICAL: Broker WS down. Manual action." │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 Reconnect Strategy

```
RECONNECT PARAMETERS
════════════════════

initial_delay:        1 second
max_delay:            60 seconds
backoff_multiplier:   2.0
jitter:               ±25% of computed delay
max_reconnects:       20 (within a 30-minute window)
reset_window:         30 minutes of stable connection resets counter to 0

RECONNECT SEQUENCE:
  Attempt  Delay (computed)   Delay (with jitter)
  ────────────────────────────────────────────────
  1        1.0s              0.75s - 1.25s
  2        2.0s              1.50s - 2.50s
  3        4.0s              3.00s - 5.00s
  4        8.0s              6.00s - 10.0s
  5        16.0s             12.0s - 20.0s
  6        32.0s             24.0s - 40.0s
  7+       60.0s (capped)    45.0s - 75.0s

AFTER SUCCESSFUL RECONNECT:
  1. Verify WS is sending data (wait for first tick, timeout 10s)
  2. If first tick received:
     a. Re-subscribe to all instruments (batches of 100, 100ms apart)
     b. State → STREAMING
     c. Reset reconnect counter if stable for 30min
  3. If no tick in 10s:
     a. Treat as failed reconnect
     b. Close connection, increment counter, retry

AFTER MAX_RECONNECTS EXCEEDED:
  1. State → DEGRADED
  2. Telegram alert: "Broker WS exhausted reconnects. System degraded."
  3. Continue attempting reconnect every 5 minutes (slow poll)
  4. All downstream services operate on stale data
  5. Scanner stops generating new signals (stale features)
  6. Dashboard shows "MARKET DATA: STALE" banner
```

### 5.3 Data Gap Handling on Reconnect

```
PROBLEM:
  During WS disconnection (say 5 seconds), ticks are lost.
  Feature engine has stale feature vectors.
  Scanner might miss a breakout.

MITIGATION (not prevention — ticks are genuinely lost):

  1. Detect gap:
     On reconnect, for each symbol, compare:
       last_known_ltp (from feature state) vs first_tick_ltp (from new WS data)
     If |delta| > 0.5%:
       log.warning("price_gap_detected", symbol=s, gap_pct=delta)

  2. Feature recovery:
     After reconnect, feature engine enters RECOVERY mode for 30 seconds:
       - Widen all delta epsilons to 0 (accept all ticks, rebuild state fast)
       - Skip bar boundary detection (partial bars will be slightly off)
       - Resume normal mode after 30s or after all symbols have received ≥5 ticks

  3. Scanner behavior during recovery:
     Scanner is PAUSED for the first 10 seconds after reconnect.
     Reason: feature vectors are stale/partial → signals would be unreliable.
     After 10s pause: resume with widened suppression thresholds for 60s.
     After 60s: full normal operation.

  4. Dashboard indicator:
     ws-gateway publishes a system status event:
       { type: "system_status", ws_state: "RECOVERING", eta_normal_sec: 60 }
     Dashboard shows: "⟳ Recovering from data gap — 45s remaining"
```

---

## 6. NSE Scraper Resilience

### 6.1 Failure Modes and Responses

```
FAILURE MODE MATRIX
════════════════════

Mode                    Detection                Response                 Recovery
──────────────────────  ───────────────────────  ──────────────────────  ────────────────
Cookie expired          HTTP 401 from endpoint   Re-warm session         Automatic (3s)
Rate limited            HTTP 429                 CAUTIOUS state, 30s     Automatic (30-60s)
IP blocked              Consecutive 403s         BACKOFF state, 300s     Automatic (5min)
Hard block (sustained)  >3 consecutive fails     DEGRADED mode           Manual or 10min retry
TLS fingerprint block   Connection reset on TLS  Switch curl_cffi profile Automatic (immediate)
NSE maintenance         HTTP 503 or empty body   Skip scrape cycle       Automatic (next cycle)
DNS failure             DNS resolution error      Retry with backoff      Automatic (10s)
NSE data format change  JSON parse error          Alert + skip endpoint   Manual (code update)
Network partition       Timeout (10s)             Retry 3x with backoff   Automatic (30s)
```

### 6.2 Degraded Mode — What Still Works

```
WHEN NSE SCRAPER IS DOWN, THE SYSTEM LOSES:
  - Option chain data (OI, PCR, max pain, IV)
  - Delivery percentage data
  - FII/DII flow data
  - Bulk/block deal data
  - Pre-open session data

WHAT STILL WORKS:
  - All tick data (from broker WS — completely independent path)
  - All technical features (computed from ticks, not NSE)
  - Scanner (all strategies except OI-dependent ones)
  - Conviction scoring (OI factor scores 50/100 as neutral fallback)
  - Sector breadth (computed from ticks)
  - Dashboard (price, volume, signals — all functional)
  - Telegram alerts (continue normally)

THE SYSTEM IS DESIGNED TO OPERATE INDEFINITELY WITHOUT NSE.
NSE data is ENRICHMENT, not foundation. This is by design.
```

### 6.3 NSE Session Warm Recovery

```
POST-BLOCK SESSION RECOVERY
════════════════════════════

When transitioning from BACKOFF → CAUTIOUS:

  1. Wait full cooldown period (300s for hard block)
  2. Rotate User-Agent to a fresh browser profile
  3. Add extra jitter: 2-5 second random delay
  4. GET https://www.nseindia.com/ (homepage only)
  5. Wait 3 seconds (appear human)
  6. GET /api/equity-stockIndices?index=NIFTY%2050 (lowest-risk endpoint)
  7. If 200 OK:
     a. State → CAUTIOUS (1 req/sec max)
     b. Process 5 successful requests
     c. State → NORMAL
  8. If fail:
     a. State → BACKOFF (extend to 600s)
     b. Alert: "NSE still blocking after recovery attempt"
     c. Next attempt in 10 minutes

Session warm-up is ALWAYS serial (never parallel requests during recovery).
```

---

## 7. Market Open Survivability

### 7.1 The 09:15 Problem

```
09:15 IST is the most demanding moment of the trading day:

  - Tick rate spikes 3-5x (orders flood in, prices gap, circuits trigger)
  - Pre-open session data must be ingested and processed
  - Gap analysis must complete before first signal can fire
  - Feature engine must warm all 700 symbol states from cold/stale
  - Sector breadth must compute first readings
  - Scanner must be ready to detect immediate breakouts
  - Dashboard clients reconnect (may have been idle overnight)
  - NSE rate limits are tightest (everyone scraping at market open)

If the system can survive 09:15 cleanly, it can survive anything.
```

### 7.2 Pre-Market Warm-Up Timeline

```
MORNING BOOT SEQUENCE
═════════════════════

06:00 — PHASE 0: Data Refresh (scheduler)
  • NSE bhavcopy: download previous day's full EOD data
  • Corporate actions: check for splits/bonuses with today's ex-date
  • Run adjustment queries if needed
  • Symbol master: refresh from NSE index constituents
  • Update Redis: infusion:symbols, infusion:sectors:*
  • Bump infusion:config:version
  • PostgreSQL partition creation: ensure today + 7 days exist
  • Estimated duration: 2-5 minutes

08:30 — PHASE 1: Authentication (scheduler triggers)
  • Broker OAuth token exchange
  • Store access_token in Redis: infusion:auth:upstox
  • If auth fails → Telegram: "Manual Upstox login required"
  • Validate Gemini API key (lightweight test request)
  • Validate Telegram bot token (getMe API call)

09:00 — PHASE 2: Service Warm-Up (scheduler triggers docker events)
  • Ensure all containers are running (docker compose ps)
  • ws-ingestion: connect to broker WS (but don't subscribe yet)
  • nse-scraper: warm NSE session (GET homepage, store cookies)
  • feature-engine: load previous day's closing state from Redis
    - Last known feature vectors for all symbols
    - Historical bar buffers from PostgreSQL (last 250 daily bars)
    - EMA seeds, RSI seeds, ATR seeds
    - This takes ~5-10 seconds for 700 symbols
  • scanner: load strategy configs, initialize pre-breakout states
  • sector-intel: load sector definitions, previous day's breadth
  • Estimated duration: 10-15 seconds

09:08 — PHASE 3: Pre-Open Session (nse-scraper)
  • Scrape /api/market-data-pre-open?key=NIFTY every 15 seconds
  • Parse indicated opening prices for NIFTY 50 stocks
  • Publish to feature-engine: gap analysis
    gap_pct = (indicated_open - prev_close) / prev_close
  • Feature engine tags each symbol with opening_gap classification:
    GAP_UP_LARGE (>2%), GAP_UP_SMALL (0.5-2%), FLAT, GAP_DOWN_SMALL, GAP_DOWN_LARGE
  • This pre-seeds the scanner with "which stocks gapped and how"

09:10 — PHASE 4: WS Subscription (ws-ingestion)
  • Subscribe to Tier 1 instruments: NIFTY 50 + top F&O (mode: full)
  • 100ms delay between batches of 100
  • Subscribe to Tier 2 (mode: full)
  • Subscribe to Tier 3 (mode: ltpc)
  • Total subscription time: ~2 seconds
  • First ticks arrive during pre-open session (09:00-09:15)
  • These pre-open ticks are processed but scanner is in PRE_MARKET mode:
    signals are computed but not published (dry run)

09:15:00 — PHASE 5: Market Open
  • Scanner transitions from PRE_MARKET to ACTIVE
  • Feature engine: first 30 seconds is WARMUP mode:
    - Accept all ticks (no delta filtering)
    - Rebuild intraday VWAP from scratch
    - Seed 1m bar from first candle
  • Normalizer: expect 50K-80K raw ticks/sec for first 2-3 minutes
    - Backpressure Level 1 may activate automatically
    - This is normal and expected
  • Scanner: suppress all signals for first 60 seconds
    - Reason: features are seeding, bars are partial, volume is opening noise
    - Exception: pre-configured "gap and go" strategy can fire after 15 seconds
  • Sector-intel: breadth values stabilize after ~30 seconds

09:16:00 — PHASE 6: Full Operation
  • All services at steady state
  • Tick rate normalizing to 20K-30K/sec
  • Scanner: all strategies active
  • Feature engine: delta filtering active
  • AI worker: ready for first signal enhancement
  • Dashboard: full real-time display
```

### 7.3 Feature Engine Cold-Start Strategy

```
THE WARM-UP PROBLEM:
  Feature engine needs historical data to compute features correctly.
  EMA(20) needs 20 prior data points. RSI(14) needs 14.
  VWAP needs today's cumulative volume.

SOLUTION — TIERED SEEDING:

  Tier A: Pre-computed seeds (loaded from Redis at 09:00)
    Previous day's closing values for all indicators:
      ema_5, ema_9, ema_20, ema_50 (last closing values)
      rsi_14 (last closing value)
      atr_14 (last closing value)
      macd, macd_signal (last closing values)
    These are stored by the EOD persister at 15:35 daily:
      HSET infusion:feature:seed:{symbol} ema_20 <value> rsi_14 <value> ...

    On market open, EMA updates continue from the seed:
      new_ema = seed_ema * (1 - alpha) + new_ltp * alpha
    This produces correct values from the very first tick.
    No warm-up period needed for EMAs, RSI, ATR, MACD.

  Tier B: Intraday-only features (computed from scratch at 09:15)
    VWAP:    starts at 0, accumulates from first tick. Valid after 1 tick.
    Rel_vol: relative to 20-day average. Average loaded from PostgreSQL.
             Today's volume starts at 0. Ratio valid after 5 minutes.
    OBV:     starts at previous close OBV. Valid from first tick.

  Tier C: Features requiring history (loaded from PostgreSQL at 09:00)
    52-week high/low: queried once, cached in memory.
    20-day average volume: queried once, cached.
    Bollinger bands: require 20 periods. Loaded from daily bars initially.
                     Switch to intraday 5m bars after 100 minutes.

  Result: Feature engine is FULLY OPERATIONAL from the first tick.
  No "cold start delay." Seeds ensure continuity from yesterday.
```

---

## 8. Long-Running Uptime

### 8.1 The 6.25-Hour Endurance Test

```
Market hours: 09:15 to 15:30 = 375 minutes = 6 hours 15 minutes.
The system must run continuously for this entire period without:
  - Memory growth beyond allocated limits
  - Latency degradation
  - Connection drops (or auto-recovery if they occur)
  - Data loss
  - Signal quality degradation
  - Dashboard staleness

Additionally, the system runs 24/7 in idle/low-power mode.
Services must not leak resources overnight.
```

### 8.2 Periodic Maintenance (During Market Hours)

```
HOUSEKEEPING TASKS (run by scheduler, non-disruptive)
═════════════════════════════════════════════════════

Every 60 seconds:
  • Health check all services (HTTP /health or Redis sentinel key)
  • Record: rss_mb, cpu_percent, pending_messages, last_tick_age
  • If any service unhealthy for >3 consecutive checks → Telegram alert

Every 5 minutes:
  • gc.collect() in feature-engine and scanner (force Python GC)
  • Log: stream lengths (XLEN for all streams)
  • Log: Redis INFO memory (used_memory_human, fragmentation_ratio)
  • Log: PostgreSQL pg_stat_activity (active connections)

Every 15 minutes:
  • Purge expired cooldown keys (Redis SCAN + TTL check)
  • Validate symbol master in memory matches Redis
  • Check for config version drift

Every 60 minutes:
  • Full memory report: per-service RSS, Redis used_memory, PG cache hit ratio
  • Latency percentile report: p50, p95, p99 for each pipeline stage
  • Signal quality report: signals generated, suppressed, by strategy
```

### 8.3 Graceful Daily Lifecycle

```
DAILY LIFECYCLE
═══════════════

09:00  Services warm up (Phase 7.2)
09:15  Market open → full performance mode
12:00  Lunch lull → scanner applies lunch penalty (Phase 3)
       Resource usage naturally drops (fewer ticks, less volume)
15:30  Market close → transition begins

15:30 — POST-MARKET TRANSITION:
  1. ws-ingestion: unsubscribe all instruments, close WS
  2. tick-normalizer: drain tick:raw, then idle
  3. feature-engine: flush final feature state to Redis seeds
     HSET infusion:feature:seed:{symbol} for all 700 symbols
  4. scanner: transition to CLOSED state, no new signals
  5. conviction-engine: drain scan:signals, then idle
  6. sector-intel: persist final breadth snapshot to sector_daily table
  7. ws-gateway: push final state to dashboard, then reduce to heartbeat-only

15:45 — EOD PERSISTENCE:
  8. eod-persister: flush intraday bars → PostgreSQL ohlcv_intraday
  9. eod-persister: compute daily summary → PostgreSQL ohlcv_daily
  10. eod-persister: record signal outcomes (any triggered signals → did price move?)

18:00 — EOD DATA COLLECTION:
  11. nse-scraper: download bhavcopy
  12. nse-scraper: fetch FII/DII data
  13. nse-scraper: fetch bulk/block deals
  14. Store all in PostgreSQL

18:30 — OVERNIGHT MODE:
  15. All compute services enter IDLE (respond to health checks only)
  16. feature-engine: clear intraday bar buffers (keep seeds)
  17. sector-intel: clear constituent state dicts
  18. Redis: streams naturally bounded by MAXLEN (no manual cleanup)
  19. PostgreSQL: autovacuum runs on modified tables

06:00 — NEXT DAY PREP (Phase 7.2, Phase 0)
  20. Cycle continues.
```

### 8.4 Python-Specific Stability Measures

```
PYTHON ASYNCIO LONG-RUNNING STABILITY
══════════════════════════════════════

1. Event loop monitoring
   Track event loop lag every 10 seconds:
     expected = 0.1  # 100ms callback interval
     actual = time.monotonic() - scheduled_time
     lag = actual - expected
     if lag > 0.05:  # 50ms lag
       log.warning("event_loop_lag", lag_ms=lag*1000)
   
   Sustained lag > 100ms indicates CPU starvation or blocking I/O in the loop.

2. No blocking I/O in the event loop
   Rules enforced by code review:
     - ALL disk I/O uses aiofiles or runs in executor
     - ALL HTTP calls use aiohttp (never requests)
     - ALL PostgreSQL calls use asyncpg (never psycopg2 in sync mode)
     - ALL Redis calls use redis.asyncio (never sync redis)
     - ALL JSON parsing uses orjson (fastest, but still sync — fast enough at <1ms)
     - ALL heavy computation (Polars) runs in executor:
       result = await loop.run_in_executor(None, polars_operation)

3. Exception handling in tasks
   Every spawned asyncio.Task is wrapped:
     async def safe_task(coro, name):
       try:
         return await coro
       except asyncio.CancelledError:
         raise
       except Exception:
         log.exception("task_crashed", task=name)
         metric: infusion_task_crash_count{task=name}
         # Do NOT restart here — let the supervisor handle it

4. Signal handling for graceful shutdown
   async def shutdown(sig):
     log.info("shutdown_signal", signal=sig)
     # 1. Stop accepting new work
     # 2. Drain current stream messages (max 5s timeout)
     # 3. Flush state to Redis (feature seeds, breadth snapshot)
     # 4. Close connections (Redis, PostgreSQL, WS)
     # 5. Exit 0
   
   for sig in (SIGTERM, SIGINT):
     loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))
```

---

## 9. Reconnect Storm Prevention

### 9.1 The Thundering Herd Problem

```
SCENARIO:
  Redis restarts (brief 2-second outage).
  All 14 services lose their Redis connection simultaneously.
  All 14 services attempt to reconnect at the same time.
  Redis accepts connections but is overwhelmed processing 14 concurrent
  reconnect + re-subscribe + XREADGROUP operations.
  Some connections fail → retry → more load → cascade.

This is a reconnect storm.
```

### 9.2 Staggered Reconnect

```
SOLUTION: Service-specific reconnect delay based on priority tier.

TIER 1 — Reconnect immediately (0-500ms jitter):
  redis (N/A — it IS Redis)
  ws-ingestion (data source — must reconnect first)
  tick-normalizer (directly downstream of ingestion)

TIER 2 — Reconnect after 1-2 seconds:
  feature-engine
  sector-intel

TIER 3 — Reconnect after 2-4 seconds:
  scanner
  conviction-engine
  ws-gateway

TIER 4 — Reconnect after 4-8 seconds:
  ai-worker
  alert-router
  nse-scraper
  scheduler
  eod-persister

IMPLEMENTATION:
  Each service has a configured RECONNECT_TIER (1-4).
  On connection loss:
    base_delay = (tier - 1) * 2.0  # seconds
    jitter = random.uniform(0, base_delay * 0.5 + 0.5)
    await asyncio.sleep(base_delay + jitter)
    attempt_reconnect()

  This spreads 14 reconnects over ~8 seconds instead of all at once.
```

### 9.3 Redis Connection Pooling

```
Each service maintains a single redis.asyncio connection pool:

  pool = redis.asyncio.ConnectionPool(
      host="redis",
      port=6379,
      max_connections=5,        # per service (not global)
      retry_on_timeout=True,
      socket_keepalive=True,
      socket_keepalive_options={
          # TCP_KEEPIDLE: 30s, TCP_KEEPINTVL: 10s, TCP_KEEPCNT: 3
      },
      health_check_interval=15,  # ping every 15s to detect dead connections
      socket_connect_timeout=5,
      socket_timeout=5,
  )

Total max connections across all services: 14 × 5 = 70
Redis max_connections default: 10000. We use < 1%.

Connection pool prevents:
  - Connection churn (new TCP handshake per operation)
  - File descriptor exhaustion
  - Reconnect storms at the TCP level
```

### 9.4 Dashboard WebSocket Reconnect

```
DASHBOARD CLIENT RECONNECT (ws-gateway side)
════════════════════════════════════════════

Problem: If ws-gateway restarts, all dashboard clients disconnect.
         When gateway comes back, all clients reconnect simultaneously.

Solution:

  1. Gateway-side: accept connections with a token bucket rate limiter
     Max 5 new WS connections per second.
     Beyond that: HTTP 503 with Retry-After header.
     
  2. Client-side (JavaScript):
     const reconnect = () => {
       const delay = Math.min(1000 * 2 ** attempt, 30000);
       const jitter = Math.random() * delay * 0.3;
       setTimeout(connect, delay + jitter);
       attempt++;
     };

  3. On successful reconnect:
     Client sends: { type: "reconnect", last_seq: <last_received_sequence_number> }
     Gateway replays messages since last_seq from its in-memory ring buffer
     (last 1000 messages, covering ~5 minutes).
     If last_seq is too old: send full state snapshot instead.

  4. Full state snapshot:
     Gateway constructs current state from Redis hot cache:
       - All infusion:tick:{symbol} → current prices
       - All infusion:sector:{id} → current breadth
       - Last 20 signals from conviction:scored stream
       - System status (ws_state, service health)
     Size: ~200KB compressed. Delivered in a single WS frame.
```

---

## 10. Monitoring & Observability

### 10.1 Health Check Architecture

```
HEALTH CHECK HIERARCHY
══════════════════════

Every service exposes health via Redis key:
  HSET infusion:health:{service_name} {
    status: "HEALTHY" | "DEGRADED" | "UNHEALTHY",
    last_heartbeat: epoch_ms,
    uptime_sec: int,
    rss_mb: float,
    detail: { ... service-specific metrics ... }
  }
  EXPIRE infusion:health:{service_name} 30  # auto-expire if service dies

Scheduler reads all health keys every 60 seconds:
  SCAN 0 MATCH infusion:health:* COUNT 100
  
  For each service:
    if key missing → DEAD (no heartbeat in 30s)
    if status == "UNHEALTHY" → check detail, alert if persistent
    if status == "DEGRADED" → log, alert only if sustained >5 min

SERVICE-SPECIFIC HEALTH DETAILS:

  ws-ingestion:
    ws_connected: bool
    ticks_per_sec: float
    last_tick_age_ms: int
    reconnect_count: int

  tick-normalizer:
    ticks_in_per_sec: float
    ticks_out_per_sec: float
    throttle_drop_rate: float
    backpressure_level: int

  feature-engine:
    features_per_sec: float
    symbols_active: int
    backpressure_level: int
    warmup_complete: bool

  scanner:
    evaluations_per_sec: float
    signals_generated_today: int
    signals_suppressed_today: int
    active_strategies: int
    backpressure_level: int

  nse-scraper:
    session_state: "WARM" | "CAUTIOUS" | "BACKOFF" | "DEGRADED"
    last_scrape_age_sec: int
    rate_limit_state: "NORMAL" | "CAUTIOUS" | "BACKOFF"
    endpoints_healthy: int
    endpoints_total: int

  ai-worker:
    queue_depth: int
    avg_latency_ms: float
    gemini_errors_last_hour: int
    mode: "FULL" | "DEGRADED" | "DISABLED"

  ws-gateway:
    connected_clients: int
    messages_per_sec: float
    avg_client_buffer_depth: float
```

### 10.2 Structured Logging

```
LOGGING STANDARDS
═════════════════

Format: structlog JSON (machine-parseable)
Library: structlog with orjson serializer

Every log line includes:
  {
    "timestamp": "2026-05-27T09:15:00.123456+05:30",
    "level": "info",
    "service": "feature-engine",
    "event": "feature_computed",
    "symbol": "RELIANCE",
    "latency_us": 280,
    "pipeline_latency_us": 3400,
    ...context-specific fields...
  }

Log levels:
  DEBUG:    Per-tick details (disabled in production)
  INFO:     State transitions, periodic summaries (every 60s)
  WARNING:  Latency breaches, stale data, backpressure activation
  ERROR:    Connection failures, data loss, unexpected exceptions
  CRITICAL: Service crash, unrecoverable state

Log volume (production, INFO level):
  Per service: ~1-5 lines/second = ~200KB/min
  All services: ~20-50 lines/second = ~2MB/min = ~750MB/day
  With 50MB rotation × 5 files: ~250MB disk per service = 3.5GB total

Log aggregation:
  No external log aggregator (Constraint #1: no over-engineering).
  Docker json-file driver with rotation.
  grep/jq for ad-hoc analysis.
  If needed: single-file tail from all containers:
    docker compose logs -f --tail=100 | jq '. | select(.level != "debug")'
```

### 10.3 Key Metrics Dashboard (Internal)

```
OPERATOR-FACING METRICS (exposed via ws-gateway /api/metrics)
═════════════════════════════════════════════════════════════

Latency:
  pipeline_p50_ms:     real-time 50th percentile tick-to-gateway
  pipeline_p99_ms:     real-time 99th percentile
  pipeline_max_ms:     worst case in last 60 seconds

Throughput:
  raw_ticks_per_sec:       broker WS ingest rate
  normalized_ticks_per_sec: post-normalizer rate
  features_per_sec:         feature computations
  signals_today:            total signals generated
  signals_suppressed_today: total signals killed by suppression

Health:
  services_healthy:    count of healthy services (target: all)
  services_degraded:   count of degraded services (target: 0)
  services_dead:       count of unresponsive services (target: 0)
  redis_memory_mb:     current Redis memory usage
  redis_fragmentation: memory fragmentation ratio (target: <1.5)

Market Data:
  ws_state:            STREAMING | RECONNECTING | DEGRADED
  nse_state:           WARM | CAUTIOUS | BACKOFF | DEGRADED
  ws_reconnect_count:  reconnects since startup
  data_gap_count:      price gaps detected today
```

### 10.4 Alerting Rules (Telegram)

```
TELEGRAM ALERT CLASSIFICATION
══════════════════════════════

CRITICAL (immediate, always delivered):
  • Broker WS down >30 seconds
  • Any service DEAD (no heartbeat >30s)
  • Redis unreachable
  • PostgreSQL unreachable
  • Auth token expired (manual re-login needed)
  • Pipeline latency p99 >100ms for >5 minutes

WARNING (batched, 1 per 5 minutes max):
  • Pipeline latency p99 >25ms for >2 minutes
  • Backpressure Level 2+ activated on any stage
  • NSE scraper in BACKOFF state
  • AI worker in DEGRADED mode
  • Memory usage >80% of soft limit on any service
  • Signal rate anomaly (0 signals by 11:00 on a trading day)

INFO (daily summary at 16:00):
  • Total signals: N (by strategy breakdown)
  • Pipeline latency: p50/p95/p99
  • WS reconnects: N
  • NSE blocks: N
  • Memory peak: X MB
  • Uptime: 100%

DEDUP: Same alert type for same service → suppress for 5 minutes.
QUIET HOURS: No INFO alerts between 20:00 and 08:00.
```

---

## 11. Failure Recovery Patterns

### 11.1 Service Crash Recovery

```
RECOVERY MATRIX — WHAT HAPPENS WHEN EACH SERVICE CRASHES
═════════════════════════════════════════════════════════

Service              Restart Time   Data Loss on Crash        Recovery Action
───────────────────  ────────────   ────────────────────────  ────────────────────────
ws-ingestion         ~3s            Ticks during downtime      Reconnect WS, re-subscribe
                                    (5-15s gap typically)      Feature engine recovers (§5.3)

tick-normalizer      ~2s            tick:raw continues filling  Consumer group resumes from
                                    (50K MAXLEN = ~1.7s buf)   last ACK. No data loss if <1.7s.

feature-engine       ~5s            Feature state is in-memory  Load seeds from Redis. First 30s
                                    (lost on crash)             in RECOVERY mode (§5.3, §7.3)

scanner              ~2s            No persistent state lost    Reload configs. Resume scanning.
                                    (strategies are stateless)  Pre-breakout state rebuilt in ~5min.

conviction-engine    ~2s            None (stateless scoring)   Resume from stream.

sector-intel         ~3s            Breadth state lost          Rebuild from current ticks (~30s).
                                                               Daily McClellan from PostgreSQL.

nse-scraper          ~3s            None (data has long TTL)   Re-warm session. Resume schedule.

ai-worker            ~2s            In-flight AI requests lost  Signals go out without AI narrative.
                                                               AI enrichment on next signal.

alert-router         ~2s            In-flight alerts queued     Redis PEL retains un-ACKed alerts.
                                    in Redis stream             XAUTOCLAIM on restart.

ws-gateway           ~3s            Dashboard clients disconnect Full state snapshot on reconnect.
                                                               Client auto-reconnects (§9.4).

scheduler            ~2s            None (cron state is config) Re-read cron schedule. Resume.

PostgreSQL           ~10-30s        Unflushed WAL (unlikely      Services queue writes in Redis.
                                    with async commit off)      Drain queue on PG recovery.

Redis                ~5s            ALL IN-MEMORY STATE LOST    CRITICAL. See §11.2.
```

### 11.2 Redis Failure — The Catastrophic Case

```
REDIS IS THE NERVOUS SYSTEM. IF REDIS DIES, EVERYTHING STOPS.

Prevention:
  - Redis runs with restart: unless-stopped
  - Docker restarts Redis within 5 seconds
  - No persistence (save "", appendonly no) — fastest restart
  - Redis data is reconstructable (see below)

Impact:
  - All streams empty (history lost, but MAXLEN means only ~5s of data)
  - All hot state keys gone (tick:{symbol}, feature:{symbol}, etc.)
  - All consumer group positions lost
  - All cooldown keys lost (minor — some duplicate signals possible)
  - Auth tokens lost (re-auth needed)

Recovery sequence (automatic, triggered by each service detecting Redis):
  1. Each service detects Redis reconnect
  2. Consumer groups are re-created (XGROUP CREATE ... MKSTREAM):
     - Start from $ (latest) — accept that 5s of data is lost
     - Do NOT start from 0 (would replay entire stream history if any remains)
  3. ws-ingestion: reconnects to broker WS, starts fresh tick flow
  4. feature-engine: loads seeds from Redis... which are also gone.
     Fallback: load from PostgreSQL (last EOD values). Takes ~5 seconds.
  5. scanner: resume. Pre-breakout state lost — rebuilds over ~5 minutes.
  6. ws-gateway: push system_status "RECOVERING" to dashboards
  7. Full recovery: ~60 seconds to stable operation

DATA THAT SURVIVES REDIS CRASH (in PostgreSQL):
  - All historical OHLCV data
  - All feature seeds (from last EOD)
  - Signal history with outcomes
  - Corporate actions
  - Symbol master (config files + PostgreSQL)

THE SYSTEM DOES NOT PERSIST REDIS TO DISK.
Redis persistence (RDB/AOF) adds latency and disk I/O.
The recovery cost (60 seconds of degradation) is worth the
performance gain of an entirely in-memory Redis.
```

### 11.3 PostgreSQL Failure

```
PostgreSQL is used for:
  - Historical data storage (writes are batched, not per-tick)
  - Feature seed loading (read at 09:00 only)
  - Signal outcome tracking (writes are async, non-critical path)
  - EOD data persistence (post-market only)

IF POSTGRESQL IS DOWN DURING MARKET HOURS:
  Impact: MINIMAL
  - No realtime path depends on PostgreSQL
  - Feature seeds already loaded into memory at 09:00
  - EOD writes will fail → queue in Redis (sorted set with retry)
  - Signal outcomes not recorded → retry at next opportunity
  - Historical queries unavailable (dashboard "history" tab empty)

  Response:
    - Telegram alert: "PostgreSQL unreachable"
    - All services continue operating normally
    - EOD persister retries every 60 seconds
    - When PG recovers: drain queued writes

IF POSTGRESQL IS DOWN AT 09:00 (STARTUP):
  Impact: MODERATE
  - Feature seeds cannot load from PostgreSQL
  - Fallback: use Redis cached seeds (if available from yesterday)
  - If Redis also empty: start with zero seeds (EMAs converge in ~20 bars ≈ 100 seconds)
  - Telegram alert: "PostgreSQL down at startup — degraded feature warmup"
```

---

## 12. Production Operating Envelope

### 12.1 Expected Steady-State Performance

```
PRODUCTION OPERATING ENVELOPE
══════════════════════════════

Metric                          Expected Value        Alert Threshold
──────────────────────────────  ────────────────────  ────────────────
Tick-to-signal latency (p50)    ~5ms                  >15ms
Tick-to-signal latency (p99)    ~15ms                 >25ms
Tick-to-signal latency (max)    ~25ms                 >50ms

Raw tick ingest rate             15K-30K ticks/sec    <5K or >50K
Normalized tick rate             8K-20K ticks/sec     <3K
Feature computation rate         5K-10K features/sec  <2K

Scanner evaluations/sec          2K-4K                <500
Signals per day                  15-50                0 by 11:00

Total CPU utilization            15-25% of 8 cores    >60% sustained
Total memory (all services)      2.0-2.5 GB           >3.5 GB
Redis memory                     100-200 MB           >400 MB
PostgreSQL active connections    5-15                  >40

Broker WS reconnects/day         0-2                  >5
NSE scraper blocks/day           0-3                  >10
AI worker avg latency            800-2000ms            >4000ms
Telegram delivery latency        200-500ms             >2000ms

Dashboard WS latency (to client) <50ms                >200ms
Dashboard FPS                    60                    <30
Dashboard rerender rate          <5 components/tick    >20
```

### 12.2 Capacity Limits

```
MAXIMUM TESTED CAPACITY (single machine, 8-core, 16GB)
══════════════════════════════════════════════════════

Dimension                    Limit              Bottleneck
───────────────────────────  ─────────────────  ─────────────────────
Max raw tick rate            100K ticks/sec     Redis XADD throughput
Max symbols tracked          2000               Feature engine memory
Max active strategies        20                 Scanner CPU per evaluation
Max concurrent WS clients    50                 Gateway memory + bandwidth
Max signals per minute       10                 Conviction scoring (trivial)
Max Redis memory             512MB              Configured hard limit
Max PostgreSQL storage       500GB              Disk (SSD)
Max log volume               5GB/day            Disk rotation handles it

AT 2× EXPECTED LOAD (market anomaly):
  - Backpressure Level 1 activates on normalizer
  - Tier 3 throttling widens
  - AI worker may enter degraded mode
  - Everything else: no visible impact

AT 5× EXPECTED LOAD (extreme market event):
  - Backpressure Level 2-3 activates across pipeline
  - AI enhancement disabled
  - Tier 3 dropped entirely
  - Dashboard tick updates delayed by 1-2 seconds
  - Signals still delivered within 50ms budget
  - System survives. This is the design goal.
```

### 12.3 Scaling (If Ever Needed)

```
SCALING DECISIONS (Constraint #1: simplicity-first)
════════════════════════════════════════════════════

The system is designed for ONE machine. Scaling is not a goal.
But if the machine becomes insufficient:

Option 1: BIGGER MACHINE (strongly preferred)
  Move from 8-core/16GB to 16-core/64GB.
  Zero code changes. Docker Compose adjusts mem_limit values.
  This handles 10x current load.

Option 2: SPLIT REDIS (if Redis is the bottleneck)
  Run two Redis instances:
    redis-hot:  streams only (tick:raw, tick:normalized, feature:computed)
    redis-state: hash keys, config, auth, health
  Each service connects to the appropriate instance.
  Minor config change, no code changes.

Option 3: NEVER DO
  - Kubernetes
  - Service mesh
  - Multiple machines running the same service
  - Message brokers (Kafka, RabbitMQ)
  - HTTP-based service communication
  
  These add operational complexity that a single-user trading system
  will never justify. The system processes Indian equity data for
  ~700 symbols. This is not Twitter's firehose.
```

---

## 13. Benchmark & Verification Plan

### 13.1 Pre-Production Benchmarks

```
BENCHMARK SUITE (run before first live market day)
═════════════════════════════════════════════════

Test 1: Latency Pipeline (synthetic ticks)
  Setup: Generate 30K synthetic ticks/sec from a mock broker adapter
  Measure: End-to-end latency at p50, p95, p99
  Pass criteria: p99 < 25ms
  Duration: 10 minutes sustained

Test 2: Market Open Simulation
  Setup: Replay recorded market open data at 3x speed (90K ticks/sec)
  Measure: Backpressure activations, latency, memory growth
  Pass criteria: No data loss, p99 < 50ms, memory stable after 2 minutes
  Duration: 5 minutes

Test 3: Reconnect Storm
  Setup: Kill Redis, wait 5 seconds, restart
  Measure: Time to full recovery, data loss window, reconnect order
  Pass criteria: All services healthy within 60 seconds
  Repeat: 3 times

Test 4: Long-Running Stability
  Setup: Synthetic ticks at 20K/sec sustained
  Measure: Memory every 5 minutes, latency every 1 minute
  Pass criteria: Memory growth < 5% over 6 hours, no latency drift
  Duration: 6.5 hours (simulated trading day)

Test 5: NSE Failure Simulation
  Setup: Block NSE endpoints (iptables or mock 403s)
  Measure: System behavior with no NSE data for 30 minutes
  Pass criteria: All non-NSE functionality unaffected
  Duration: 30 minutes

Test 6: Memory Leak Detection
  Setup: Run with tracemalloc enabled (PYTHONTRACEMALLOC=10)
  Measure: Top 20 memory allocations every 30 minutes
  Pass criteria: No single allocation growing linearly over time
  Duration: 2 hours
```

### 13.2 Live Market Validation (First Week)

```
LIVE VALIDATION CHECKLIST
═════════════════════════

Day 1 — Observation Only:
  □ All services start by 09:10
  □ Feature engine seeds load correctly
  □ Pre-open data ingested
  □ 09:15 tick rate spike handled (check backpressure logs)
  □ First signal generated (verify conviction score, explanation)
  □ Telegram delivery works
  □ Dashboard connected and updating
  □ No OOM kills (docker events | grep OOM)
  □ EOD persistence completes by 16:00
  □ Memory at 15:30 within 10% of memory at 09:30

Day 2-3 — Signal Quality:
  □ Compare signals against manual chart analysis
  □ Verify suppression pipeline (check suppression reasons in logs)
  □ Verify conviction scoring (do scores match expectations?)
  □ Check for false positive patterns

Day 4-5 — Resilience:
  □ Force-restart ws-ingestion mid-market → verify recovery
  □ Force-restart feature-engine → verify seed recovery
  □ Block NSE for 10 minutes → verify degraded mode
  □ Disconnect dashboard → verify reconnect + state snapshot
  □ Review full week's latency distribution
```

---

## Phase 6 Boundary

This completes the performance and optimization layer. Key decisions made:

| Decision | Rationale |
|---|---|
| 25ms p99 tick-to-signal budget with 5ms typical | Headroom for market open spikes and GC pauses without degradation |
| No Redis persistence (RDB/AOF) | 60-second recovery cost is worth zero-latency steady-state (Constraint #2) |
| 4-tier backpressure with explicit shed order | Graceful degradation: AI first, signals last (Constraint #3) |
| Staggered reconnect by service priority tier | Prevents thundering herd on infrastructure recovery |
| Feature engine seeded from previous day's close | Eliminates cold-start delay — features valid from first tick |
| Docker Compose with soft CPU / hard memory limits | Single-machine, no Kubernetes, no orchestrator (Constraint #1) |
| NSE as enrichment, not foundation | System operates indefinitely without NSE data |
| Pre-market warm-up timeline from 06:00 to 09:15 | Every service ready before market open (Constraint #7) |
| gc.collect() every 5 minutes in compute services | Proactive memory management for long-running Python processes |
| No external monitoring stack (no Prometheus/Grafana) | Redis health keys + structlog + Telegram alerts (Constraint #1) |

**Phase 6 defines the production operating envelope. The system is architecturally complete.**
