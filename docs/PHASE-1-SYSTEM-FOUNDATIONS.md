# PHASE 1 — SYSTEM FOUNDATIONS

> Infusion AI Screener — Institutional-grade realtime NSE market intelligence platform.

---

## 1. High-Level Architecture Diagram

```
                          ┌──────────────────────────────────────────────────────────────┐
                          │                     INFUSION AI SCREENER                      │
                          └──────────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────┐
    │        INGESTION LAYER          │      Tick rate: ~15,000–30,000/sec peak
    │                                 │
    │  ┌──────────┐   ┌──────────┐   │      Protocol: Binary WS (protobuf / custom)
    │  │ Upstox   │   │  Kite    │   │      Auth: OAuth2 token auto-refresh
    │  │ Adapter  │   │ Adapter  │   │      Reconnect: exponential backoff
    │  └────┬─────┘   └────┬─────┘   │
    │       └──────┬───────┘         │
    │              ▼                 │
    │      ┌──────────────┐          │
    │      │ Tick Mux      │         │      Deduplication + sequencing
    │      └──────┬───────┘          │
    └─────────────┼──────────────────┘
                  │ XADD tick:raw (msgpack, ~2µs per write)
                  ▼
    ┌─────────────────────────────────┐
    │         EVENT BUS               │      Redis 7.x Streams
    │                                 │      6 streams, ~220MB footprint
    │   tick:raw ──► tick:norm ──►    │      Consumer groups per service
    │   feature:computed ──►          │      XREADGROUP BLOCK 0 (no polling)
    │   scan:signals ──►              │      MAXLEN ~ trim (O(1) approximate)
    │   sector:state ──►              │      msgpack codec (40% smaller than JSON)
    │   conviction:ranked ──►         │
    │   alert:outbound                │
    └─────────────┬──────────────────┘
                  │
      ┌───────────┼───────────┬──────────────┬──────────────┐
      ▼           ▼           ▼              ▼              ▼
┌──────────┐┌──────────┐┌──────────┐  ┌──────────┐  ┌──────────┐
│Normalizer││ Feature  ││ Scanner  │  │Conviction│  │ Alerter  │
│          ││ Engine   ││ Engine   │  │ Scorer   │  │          │
│ Token→   ││ Polars   ││ Strategy │  │ Multi-   │  │ Telegram │
│ Symbol   ││ Rolling  ││ Plugin   │  │ factor   │  │ WS Push  │
│ resolve  ││ windows  ││ system   │  │ AI boost │  │ Throttle │
└──────────┘└──────────┘└──────────┘  └──────────┘  └──────────┘
                  │                         │              │
      ┌───────────┴─────────────────────────┘              │
      ▼                                                    ▼
┌──────────────────────────┐              ┌────────────────────────┐
│    PERSISTENCE LAYER     │              │    DELIVERY LAYER      │
│                          │              │                        │
│  Redis (hot state)       │              │  WS Gateway ──► Next.js│
│  • Latest ticks/features │              │  REST API   ──► Client │
│  • Cooldowns (TTL keys)  │              │  Telegram Bot          │
│  • Session state         │              │                        │
│                          │              └────────────────────────┘
│  PostgreSQL (cold store) │
│  • OHLCV daily/intraday  │
│  • Signal history        │
│  • Sector snapshots      │
│  • Institutional flows   │
└──────────────────────────┘

    ┌─────────────────────────────────┐
    │     OFFLINE / SCHEDULED         │
    │                                 │
    │  NSE Scraper ──► PostgreSQL     │      Bhavcopy, FII/DII, delivery,
    │  Scheduler   ──► All services   │      option chain, corporate actions
    │  EOD Jobs    ──► PostgreSQL     │      Pre-market prep, post-market persist
    └─────────────────────────────────┘
```

### Architecture Class

This is a **stream-processing pipeline**, not a request-response system. Every component is a stateless consumer of an append-only log. This gives us:

- **Decoupling**: producers don't know consumers exist. Add a new scanner without touching ingestion.
- **Replay**: Redis Streams retain messages (up to MAXLEN). New consumers can catch up from last ACK'd position.
- **Backpressure**: if a consumer falls behind, its lag grows in the consumer group — we monitor this and alert, not drop data.
- **Ordering guarantee**: within a single stream, messages are strictly ordered by ID (timestamp-based).

### What This Is NOT

| Anti-pattern | Why we avoid it |
|---|---|
| Request-response microservices | Adds HTTP latency per hop. We need < 50ms tick-to-signal. |
| Message queue (RabbitMQ/Kafka) | Kafka is overkill for single-node. RabbitMQ lacks stream semantics. Redis Streams gives us both pub/sub speed and persistence. |
| Monolith | Can't isolate the feature engine's memory from the scanner's CPU. Process isolation is mandatory. |
| Shared database polling | Polling PostgreSQL for new ticks would add 10-100ms latency and kill throughput. |

---

## 2. Service Map

```
┌─────────────────────────────────────────────────────────────────────┐
│                         SERVICE TOPOLOGY                             │
│                                                                      │
│   HOT PATH (latency-critical, always-on during market hours)        │
│   ────────────────────────────────────────────────────────          │
│   ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────┐    │
│   │ ingestion  │→ │ normalizer │→ │  feature   │→ │ scanner  │    │
│   │            │  │            │  │  engine    │  │          │    │
│   │ 1 per      │  │ 1 instance │  │ 1-N inst.  │  │ 1-N inst.│    │
│   │ broker     │  │            │  │ (scale by  │  │          │    │
│   └────────────┘  └────────────┘  │  symbol    │  └────┬─────┘    │
│                                   │  partition)│       │          │
│                                   └────────────┘       │          │
│                                                        ▼          │
│                                   ┌────────────┐  ┌──────────┐    │
│                                   │  sector    │→ │conviction│    │
│                                   │  intel     │  │ scorer   │    │
│                                   │ 1 instance │  │ 1 inst.  │    │
│                                   └────────────┘  └────┬─────┘    │
│                                                        │          │
│   WARM PATH (near-realtime, tolerates 100ms+ latency)             │
│   ───────────────────────────────────────────────────             │
│                                                        ▼          │
│   ┌────────────┐  ┌────────────┐  ┌────────────────────────┐     │
│   │ ws-gateway │  │  alerter   │  │    REST API (FastAPI)  │     │
│   │            │  │            │  │                         │     │
│   │ fans out   │  │ throttle + │  │ /scanner, /sector,     │     │
│   │ to browser │  │ dispatch   │  │ /conviction, /symbols  │     │
│   └────────────┘  └──────┬─────┘  └────────────────────────┘     │
│                          │                                        │
│                          ▼                                        │
│                   ┌────────────┐                                  │
│                   │ telegram   │                                  │
│                   │ bot        │                                  │
│                   └────────────┘                                  │
│                                                                   │
│   COLD PATH (scheduled, non-latency-critical)                    │
│   ──────────────────────────────────────────                     │
│   ┌────────────┐  ┌────────────┐                                 │
│   │ nse-scraper│  │ scheduler  │                                 │
│   │            │  │ (APSched)  │                                 │
│   └────────────┘  └────────────┘                                 │
│                                                                   │
└─────────────────────────────────────────────────────────────────────┘
```

### Service Catalog

| Service | Instances | Memory | CPU Profile | Restart Policy | Health Check |
|---|---|---|---|---|---|
| `ingestion` | 1 per broker | 50MB | Low (I/O bound) | Always restart, 2s delay | WS connected + last tick < 5s ago |
| `normalizer` | 1 | 30MB | Low | Always restart | Consumer lag < 100 |
| `feature-engine` | 1 (scale to N) | 500MB–1GB | High (Polars compute) | Always restart | Consumer lag < 50 |
| `scanner` | 1 (scale to N) | 100MB | Medium (pattern matching) | Always restart | Consumer lag < 50 |
| `sector-intel` | 1 | 80MB | Low-medium | Always restart | Consumer lag < 200 |
| `conviction` | 1 | 200MB (with ML model) | Medium | Always restart | Consumer lag < 100 |
| `alerter` | 1 | 30MB | Low | Always restart | Last dispatch check < 60s |
| `ws-gateway` | 1 | 50MB | Low (fan-out) | Always restart | Active WS connections ≥ 0 |
| `api` | 1 | 100MB | Low (request-driven) | Always restart | HTTP 200 on `/health` |
| `nse-scraper` | 1 | 80MB | Low (scheduled I/O) | Always restart | Last successful scrape < 24h |
| `telegram-bot` | 1 | 30MB | Low | Always restart | Bot polling active |
| `scheduler` | 1 | 30MB | Low | Always restart | Next job scheduled |
| **Total** | 12 processes | **~1.3GB** | | | |

### Inter-Service Communication Matrix

```
              ing  norm  feat  scan  sect  conv  alert  wsgw  api   scrp  tg   sched
ingestion      —    S→    —     —     —     —     —      —     —     —     —     —
normalizer     —    —     S→    —     —     —     —      S→    —     —     —     —
feature-eng    —    —     —     S→    S→    S→    —      S→    —     —     —     —
scanner        —    —     —     —     —     S→    —      —     —     —     —     —
sector-intel   —    —     —     —     —     S→    —      S→    —     —     —     —
conviction     —    —     —     —     —     —     S→     S→    —     —     —     —
alerter        —    —     —     —     —     —     —      —     —     —     S→    —
ws-gateway     —    —     —     —     —     —     —      —     —     —     —     —
api            —    —     —     —     —     —     —      —     —     —     —     —
nse-scraper    —    —     —     —     —     —     —      —     —     —     —     —
scheduler      —    —     —     —     —     —     —      —     —     R→    —     —

S→ = Redis Streams     R→ = Redis Pub/Sub (job trigger)
```

**Key insight**: Every arrow is a Redis Stream. No service calls another service directly. This means any service can crash and restart without affecting others — they just resume from their last ACK'd stream position.

---

## 3. Core Event Pipeline

### 3.1 Stream Topology

```
INGESTION                COMPUTE                    DELIVERY
─────────               ─────────                  ──────────

tick:raw ────► tick:normalized ────► feature:computed ────┬──► scan:signals
                    │                      │              │         │
                    │                      ▼              │         ▼
                    │               sector:state ─────────┤   conviction:ranked
                    │                                     │         │
                    ▼                                     │         ▼
              [ws-gateway]                                │   alert:outbound
              (live prices)                               │         │
                                                          │    ┌────┴────┐
                                                          │    ▼         ▼
                                                          │  telegram  ws-gateway
                                                          │            (signals)
                                                          │
                                                          └──► [ws-gateway]
                                                               (features for
                                                                screener table)
```

### 3.2 Stream Definitions

| Stream | Message Content | Avg Size | Rate (peak) | Producers | Consumer Groups |
|---|---|---|---|---|---|
| `tick:raw` | Raw broker tick (binary-decoded, not yet resolved) | 180B | 30,000/sec | ingestion | `normalizer-cg` |
| `tick:normalized` | Resolved symbol, unified schema, timestamps | 200B | 30,000/sec | normalizer | `feature-cg`, `dashboard-cg` |
| `feature:computed` | Symbol + 30-field feature vector | 600B | 6,000/sec (deduplicated per 5ms batch per symbol) | feature-engine | `scanner-cg`, `sector-cg`, `conviction-cg`, `dashboard-cg` |
| `scan:signals` | Strategy name + symbol + conditions met + metadata | 400B | 5–50/min (sparse by design) | scanner | `conviction-cg`, `audit-cg` |
| `sector:state` | Sector ID + breadth + money flow + rotation score | 300B | 20/sec (20 sectors × 1/sec) | sector-intel | `conviction-cg`, `dashboard-cg` |
| `conviction:ranked` | Symbol + score + grade + factor breakdown | 500B | 5–50/min (mirrors scan:signals) | conviction | `alert-cg`, `dashboard-cg` |

### 3.3 Consumer Group Design

Each consumer group provides:
- **At-least-once delivery**: consumer ACKs after processing. If it crashes mid-process, the message is re-delivered on restart via pending entries list (PEL).
- **Partitioned parallelism**: for `feature-cg`, we can run N consumers in the group. Redis distributes messages round-robin across consumers. For symbol-affinity (all ticks for RELIANCE go to the same consumer), we'd partition by symbol hash — but at our scale (700 symbols), a single feature-engine instance is sufficient.

```
                          Consumer Group: feature-cg
                          ┌─────────────────────────────┐
tick:normalized ─────────►│  consumer-1  │  consumer-2  │
                          │  (symbols    │  (symbols    │
                          │   A-M)       │   N-Z)       │
                          └─────────────────────────────┘
                          
                          Only needed if single instance
                          can't keep up. Start with 1.
```

### 3.4 Message Lifecycle

```
1. Producer: XADD stream MAXLEN ~ 50000 * field1 value1 ...
   └── Returns stream ID: "1716789012345-0" (timestamp-sequence)

2. Consumer: XREADGROUP GROUP cg consumer-1 BLOCK 0 COUNT 100 STREAMS stream >
   └── Returns batch of up to 100 new messages
   └── BLOCK 0 = block indefinitely until data arrives (no polling, no CPU waste)

3. Consumer processes batch

4. Consumer: XACK stream cg message-id-1 message-id-2 ...
   └── Marks messages as processed

5. Stream auto-trims: MAXLEN ~ keeps approximately N messages
   └── Approximate trim is O(1), exact trim is O(N) — always use approximate
```

### 3.5 Failure Semantics

| Failure | Behavior | Recovery |
|---|---|---|
| Consumer crash mid-batch | Messages stay in PEL (pending entries list) | On restart, claim pending messages via `XAUTOCLAIM` before reading new ones |
| Producer crash | No data on stream | Downstream consumers idle, health check fires lag alert |
| Redis crash | All streams lost | Services reconnect, rebuild state from broker WS. Historical data is in PostgreSQL. |
| Slow consumer (lag > threshold) | Messages accumulate, stream grows | Health monitor alerts. Scale consumer or tune batch size. |

---

## 4. Realtime Data Flow

### 4.1 Market Hours Pipeline (09:15 – 15:30 IST)

```
TIME     EVENT                           LATENCY BUDGET
─────    ─────                           ──────────────
t+0      Broker WS delivers binary tick
t+0.1ms  ingestion: decode binary frame
t+0.3ms  ingestion: XADD tick:raw          0.3ms
         ─── Redis ───
t+0.5ms  normalizer: XREADGROUP wakes up
t+1.5ms  normalizer: token→symbol resolve
         normalizer: schema transform
t+2.0ms  normalizer: XADD tick:normalized  1.7ms
         ─── Redis ───
t+2.2ms  feature-engine: XREADGROUP wakes
         feature-engine: buffer tick
t+7.0ms  feature-engine: 5ms batch fires
         feature-engine: Polars rolling
         window recompute (vectorized)
t+10.0ms feature-engine: XADD feature:computed  7.8ms
         ─── Redis ───
t+10.5ms scanner: XREADGROUP wakes
         scanner: dispatch to all strategies
t+14.0ms scanner: strategy evaluates conditions
         scanner: cooldown check (Redis GET)
t+15.0ms scanner: XADD scan:signals (if match)  4.5ms
         ─── Redis ───
t+15.5ms conviction: XREADGROUP wakes
         conviction: assemble feature vector
         conviction: technical/volume/context scores
t+18.0ms conviction: LightGBM inference (if loaded)
t+20.0ms conviction: XADD conviction:ranked      5.0ms
         ─── Redis ───
t+20.5ms alerter: XREADGROUP wakes
         alerter: throttle check
         alerter: format message
t+25.0ms alerter: Telegram API call              5.0ms
         (network-bound, async fire-and-forget)

         ─── Parallel path (from t+2.2ms) ───
t+2.5ms  ws-gateway: XREADGROUP tick:normalized
t+3.0ms  ws-gateway: buffer for 100ms batch window
t+103ms  ws-gateway: flush batch to connected browsers

TOTAL tick-to-scanner-signal:  ~15ms
TOTAL tick-to-conviction:      ~20ms
TOTAL tick-to-telegram:        ~25ms + network
TOTAL tick-to-dashboard-price: ~103ms (intentional batching)
TOTAL tick-to-dashboard-signal: ~20ms (signals sent immediately, not batched)
```

### 4.2 Pre-Market Flow (06:00 – 09:15 IST)

```
06:00   scheduler triggers: pre_market_prep
        │
        ├──► nse-scraper: download T-1 bhavcopy
        │    ├── parse CSV → Polars DataFrame
        │    ├── compute: prev_close, prev_volume, prev_delivery_pct
        │    └── INSERT INTO ohlcv_daily
        │
        ├──► nse-scraper: download FII/DII data
        │    └── INSERT INTO institutional_flows
        │
        ├──► nse-scraper: download delivery data
        │    └── UPDATE ohlcv_daily SET delivery columns
        │
        └──► symbol master refresh
             ├── fetch index constituents from NSE
             ├── fetch F&O lot sizes
             └── HSET infusion:session:symbols (token→symbol map)

08:30   scheduler triggers: market_ready
        │
        ├──► feature-engine: warm rolling windows
        │    ├── load last 50 days OHLCV from PostgreSQL
        │    ├── pre-compute: 20/50/200 EMA, ATR(14), Bollinger
        │    └── cache in Redis: infusion:feature:{symbol}
        │
        ├──► scanner: load strategy configs from YAML
        │    └── validate feature dependencies
        │
        ├──► sector-intel: load sector→constituent map
        │    └── compute baseline sector metrics
        │
        └──► conviction: load ML model weights (if available)

09:10   scheduler triggers: connect_brokers
        │
        └──► ingestion: authenticate + establish WS connections
             ├── subscribe tier-1 instruments (full tick)
             ├── subscribe tier-2 instruments (sampled)
             └── subscribe tier-3 instruments (snapshot)

09:15   Market opens. Pipeline is hot.
```

### 4.3 Post-Market Flow (15:30 – 18:00 IST)

```
15:30   Market closes. Broker WS sends close notification.
        │
        ├──► ingestion: graceful WS disconnect
        ├──► feature-engine: flush final feature snapshots
        │    └── XADD feature:snapshot (for archival)
        └──► scanner: emit EOD summary signal

16:00   scheduler triggers: post_market_persist
        │
        ├──► persist all intraday bars (1m, 5m, 15m) to PostgreSQL
        │    └── batch INSERT from Redis OHLC sorted sets
        │
        ├──► persist signal history with price-at-signal
        │    └── INSERT INTO signals (features_json snapshot)
        │
        ├──► nse-scraper: fetch bulk/block deals
        │    └── INSERT INTO corporate_events
        │
        └──► Redis cleanup
             ├── DEL infusion:ohlc:* (intraday bars)
             ├── DEL infusion:cooldown:* (scanner cooldowns)
             └── stream trimming (XTRIM all streams to 0)

18:00   scheduler triggers: eod_analysis
        │
        ├──► nse-scraper: download bhavcopy (T+0, now available)
        ├──► outcome tracking: update signals table
        │    └── fill price_1d for T-1 signals
        │    └── fill price_3d for T-3 signals
        │    └── fill price_5d for T-5 signals
        │
        ├──► EOD screener batch run (PKScreener-style)
        │    └── NR7, consolidation, inside bar scans
        │
        └──► Telegram: send EOD summary message
```

---

## 5. Repo / Folder Structure

```
infusion-core-architecture/
│
├── docker-compose.yml              # Production stack
├── docker-compose.dev.yml          # Dev: hot reload, debug ports, local Redis/PG
├── .env.example                    # All env vars documented
├── Makefile                        # up, down, logs, migrate, seed, benchmark
├── README.md
│
├── libs/                           # Shared Python packages (editable installs)
│   │
│   ├── infusion-models/            # Data contracts (the system's lingua franca)
│   │   ├── pyproject.toml          # Dependencies: pydantic, msgpack
│   │   └── src/infusion_models/
│   │       ├── __init__.py
│   │       ├── tick.py             # RawTick, NormalizedTick
│   │       ├── feature.py          # FeatureVector (30+ fields)
│   │       ├── signal.py           # ScanSignal, ConvictionScore
│   │       ├── sector.py           # SectorState, SectorBreadth
│   │       ├── alert.py            # AlertPayload, AlertChannel
│   │       └── enums.py            # Exchange, SignalType, Timeframe, Grade
│   │
│   ├── infusion-streams/           # Redis Streams abstraction layer
│   │   ├── pyproject.toml          # Dependencies: redis[hiredis], msgpack
│   │   └── src/infusion_streams/
│   │       ├── __init__.py
│   │       ├── producer.py         # async XADD with MAXLEN, batching
│   │       ├── consumer.py         # XREADGROUP + XACK + XAUTOCLAIM wrapper
│   │       ├── codec.py            # msgpack encode/decode for all model types
│   │       └── health.py           # Consumer lag monitoring, PEL size check
│   │
│   └── infusion-common/            # Cross-cutting utilities
│       ├── pyproject.toml          # Dependencies: structlog, pydantic-settings
│       └── src/infusion_common/
│           ├── __init__.py
│           ├── config.py           # BaseSettings subclass, envvar-driven
│           ├── logging.py          # structlog JSON config, correlation IDs
│           ├── timing.py           # @measure_latency decorator, histogram
│           └── symbols.py          # SymbolMaster: token↔symbol↔ISIN resolver
│
├── services/                       # Each service = 1 Docker container = 1 asyncio loop
│   │
│   ├── ingestion/                  # Broker WebSocket connections
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── __init__.py
│   │       ├── main.py             # asyncio.run(main()) entrypoint
│   │       ├── supervisor.py       # Manages adapter lifecycle, restarts
│   │       ├── adapters/
│   │       │   ├── __init__.py
│   │       │   ├── base.py         # BrokerAdapter ABC
│   │       │   ├── upstox.py       # Upstox WS: protobuf decode, auth
│   │       │   └── kite.py         # Kite WS: binary decode, auth
│   │       └── reconnect.py        # ExponentialBackoff state machine
│   │
│   ├── normalizer/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── __init__.py
│   │       ├── main.py
│   │       ├── resolver.py         # In-memory token→symbol lookup (from Redis)
│   │       └── transformer.py      # RawTick → NormalizedTick field mapping
│   │
│   ├── feature-engine/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── __init__.py
│   │       ├── main.py
│   │       ├── engine.py           # Orchestrates tick batching + feature publish
│   │       ├── windows.py          # Per-symbol ring buffer for ticks + OHLC bars
│   │       ├── bar_builder.py      # Tick → 1m/5m/15m bar aggregation
│   │       └── features/
│   │           ├── __init__.py
│   │           ├── price.py        # VWAP, gap%, ATR, Bollinger, EMA stack
│   │           ├── volume.py       # RelVol, OBV, volume profile, delivery%
│   │           ├── momentum.py     # RSI, MACD, Stochastic, CCI
│   │           ├── volatility.py   # BB width, Keltner, IV percentile
│   │           └── microstructure.py # Bid-ask spread, order imbalance
│   │
│   ├── scanner/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── __init__.py
│   │       ├── main.py
│   │       ├── engine.py           # StrategyRouter: dispatch + cooldown
│   │       ├── cooldown.py         # Redis-backed per-(strategy, symbol) TTL
│   │       └── strategies/
│   │           ├── __init__.py
│   │           ├── base.py         # ScanStrategy ABC
│   │           ├── breakout.py     # Range breakout detection
│   │           ├── volume_surge.py # Unusual volume
│   │           ├── momentum.py     # RSI divergence, MACD cross
│   │           ├── oi_buildup.py   # OI + price analysis
│   │           └── pre_breakout.py # Compression + accumulation detection
│   │
│   ├── sector-intel/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── __init__.py
│   │       ├── main.py
│   │       ├── aggregator.py       # Per-sector weighted metric computation
│   │       ├── breadth.py          # Advance/decline, %above VWAP
│   │       ├── rotation.py         # RS momentum, quadrant classification
│   │       └── money_flow.py       # Directional volume × price scoring
│   │
│   ├── conviction/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── __init__.py
│   │       ├── main.py
│   │       ├── scorer.py           # Multi-factor weighted scoring
│   │       ├── ranker.py           # Cross-stock ranking, grade assignment
│   │       └── models/
│   │           ├── __init__.py
│   │           ├── rule_engine.py  # Deterministic scoring (cold start)
│   │           └── lgbm.py         # LightGBM model inference
│   │
│   ├── alerter/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── __init__.py
│   │       ├── main.py
│   │       ├── dispatcher.py       # Route signal → channels
│   │       ├── throttle.py         # Per-symbol cooldown, global rate limit
│   │       └── channels/
│   │           ├── __init__.py
│   │           ├── telegram.py     # Async Telegram API client
│   │           └── websocket.py    # Push to ws-gateway
│   │
│   ├── nse-scraper/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── __init__.py
│   │       ├── main.py
│   │       ├── session.py          # NSE cookie/header session manager
│   │       ├── rate_limiter.py     # Per-endpoint rate limiting
│   │       └── scrapers/
│   │           ├── __init__.py
│   │           ├── bhavcopy.py
│   │           ├── fii_dii.py
│   │           ├── option_chain.py
│   │           ├── delivery.py
│   │           ├── bulk_block.py
│   │           └── corporate_actions.py
│   │
│   ├── api/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── __init__.py
│   │       ├── main.py             # FastAPI app factory
│   │       ├── deps.py             # Dependency injection: Redis pool, PG pool
│   │       ├── middleware.py       # CORS, request timing, error handling
│   │       └── routers/
│   │           ├── __init__.py
│   │           ├── scanner.py      # GET /signals, GET /signals/{id}
│   │           ├── sector.py       # GET /sectors, GET /sectors/{id}
│   │           ├── conviction.py   # GET /rankings, GET /score/{symbol}
│   │           ├── symbols.py      # GET /symbols, GET /symbols/search
│   │           ├── ohlcv.py        # GET /ohlcv/{symbol}/{tf}
│   │           └── health.py       # GET /health (system-wide)
│   │
│   ├── ws-gateway/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── src/
│   │       ├── __init__.py
│   │       ├── main.py
│   │       ├── gateway.py          # Starlette WebSocket endpoint
│   │       ├── subscriptions.py    # Per-client channel subscription state
│   │       └── fan_out.py          # Redis consumer → broadcast to clients
│   │
│   └── telegram-bot/
│       ├── Dockerfile
│       ├── pyproject.toml
│       └── src/
│           ├── __init__.py
│           ├── main.py
│           ├── handlers.py         # /top, /sector, /scan, /status commands
│           └── formatters.py       # Rich message card templates
│
├── scheduler/
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── src/
│       ├── __init__.py
│       ├── main.py                 # APScheduler async setup
│       └── jobs/
│           ├── __init__.py
│           ├── pre_market.py       # 06:00 — scrape + warm caches
│           ├── market_ready.py     # 08:30 — warm features, load models
│           ├── connect_brokers.py  # 09:10 — authenticate + connect WS
│           ├── post_market.py      # 15:45 — persist + cleanup
│           └── eod_analysis.py     # 18:00 — outcome tracking + EOD scans
│
├── migrations/                     # Alembic database migrations
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
│
├── config/                         # YAML configuration (runtime-loaded)
│   ├── scanners.yaml               # Strategy params, enable/disable
│   ├── sectors.yaml                # Sector → constituent mappings
│   ├── conviction_weights.yaml     # Scoring factor weights
│   ├── alerts.yaml                 # Throttle rules, channel config
│   └── instruments.yaml            # Subscription tiers, lot sizes
│
├── scripts/
│   ├── seed_symbols.py             # One-time symbol master population
│   ├── backfill_ohlcv.py           # Historical data backfill from bhavcopy
│   ├── benchmark_latency.py        # Synthetic tick injection, measure p99
│   └── validate_streams.py         # Check all streams exist, groups created
│
├── frontend/                       # Next.js 14+ (App Router)
│   ├── package.json
│   ├── next.config.js
│   ├── tsconfig.json
│   └── src/
│       ├── app/
│       │   ├── layout.tsx
│       │   ├── page.tsx            # Main screener dashboard
│       │   ├── sectors/
│       │   │   └── page.tsx        # Sector heatmap view
│       │   └── watchlist/
│       │       └── page.tsx        # Personal watchlist
│       ├── components/
│       ├── hooks/
│       ├── lib/
│       └── styles/
│
└── tests/
    ├── unit/                       # Per-service unit tests
    ├── integration/                # Redis + PG integration tests
    └── load/                       # Pipeline latency benchmarks
```

### Dependency Graph (libs)

```
infusion-models  ◄─── every service imports this
       │
       ▼
infusion-streams ◄─── every service that reads/writes Redis Streams
       │
       ▼
infusion-common  ◄─── every service imports this
```

All three libs are installed as **editable packages** (`pip install -e libs/infusion-models`) in each service's Docker build. In dev, they're mounted as volumes for hot reload.

### Why This Structure

| Decision | Rationale |
|---|---|
| `libs/` for shared code | Avoids code duplication. Models change in one place, all services pick it up. |
| One `pyproject.toml` per service | Each service has its own dependency set. Feature engine needs Polars; telegram-bot does not. |
| `config/` as YAML | Strategy parameters change without code deploys. Mounted as Docker volume. |
| `strategies/` as plugin dir | New scan strategies are a single file. No wiring code needed — engine auto-discovers. |
| `features/` split by domain | Price, volume, momentum, volatility — each is a Polars expression module. Easy to add/remove. |

---

## 6. Technology Stack Reasoning

### Core Runtime

| Technology | Version | Why This, Not That |
|---|---|---|
| **Python 3.12+** | 3.12 | Fastest CPython ever (5-15% speedup from 3.11). `slots=True` dataclasses for memory. Task groups in asyncio. **Not Rust/Go** because: ecosystem depth for financial indicators, ML libraries, broker SDKs are all Python-first. |
| **asyncio** | stdlib | Native async. No gevent monkeypatching. No Celery overhead. Direct event loop control. |
| **FastAPI** | 0.110+ | Fastest Python web framework. Native async. Pydantic validation. OpenAPI docs auto-generated. **Not Django** — we don't need ORM/admin. **Not Flask** — no native async. |
| **uvicorn** | 0.29+ | ASGI server. Uses `uvloop` on Linux for 2-4x event loop speedup over default asyncio. |

### Data Processing

| Technology | Why This, Not That |
|---|---|
| **Polars** | 5-30x faster than Pandas for rolling window operations. Lazy evaluation = compute only what's needed. Rust-backed = no GIL contention. Arrow-native = zero-copy interop. **Not Pandas** — too slow for realtime. **Not NumPy raw** — Polars expressions are more maintainable than raw array indexing. |
| **msgpack** | Binary serialization for Redis messages. 40% smaller than JSON, 3x faster to encode/decode. Schema-less (flexible as our models evolve). **Not protobuf** — needs schema compilation step, overkill for internal streams. **Not JSON** — too slow and too large. |

### Data Stores

| Technology | Role | Why This, Not That |
|---|---|---|
| **Redis 7.x** | Hot state + event bus | Redis Streams = persistent pub/sub with consumer groups. Sub-millisecond operations. Single binary, trivial to operate. **Not Kafka** — Kafka needs ZooKeeper/KRaft, 3+ brokers for reliability, JVM heap tuning. Massive operational overhead for a single-user system. We'd use Kafka at >500k ticks/sec; we're at ~30k. **Not RabbitMQ** — no stream semantics, no consumer group replay. |
| **PostgreSQL 16** | Cold store + analytics | Mature, reliable, partitioning support, JSONB for flexible feature storage. **Not ClickHouse** — overkill for our data volume (<10GB/year of OHLCV). **Not TimescaleDB** — adds extension complexity; native PG partitioning is sufficient. |
| **hiredis** | Redis client C parser | 10x faster Redis response parsing. Drop-in with `redis[hiredis]`. |

### Frontend

| Technology | Why This, Not That |
|---|---|
| **Next.js 14+ (App Router)** | React with SSR for initial load speed. App Router for server components (reduce client JS bundle). **Not Vite+React** — we want SSR for the initial screener table load (700 rows). **Not plain HTML** — the interactivity level (realtime table, charts, heatmaps) demands a component framework. |
| **TradingView Lightweight Charts** | Free, MIT-licensed. Sufficient for candlestick + overlay rendering. No license fee. **Not TradingView Advanced Charts** — requires commercial license. We can upgrade later if needed. |
| **Zustand** | 2KB state manager. No Redux boilerplate. Direct store access in components. Perfect for WebSocket state. |
| **@tanstack/react-table** | Virtualized table for 700-row screener. Column sorting, filtering, pinning. |

### Infrastructure

| Technology | Why This, Not That |
|---|---|
| **Docker Compose** | Single-host orchestration. 12 services, all defined declaratively. Dev and prod from same compose file (with override). **Not Kubernetes** — K8s is for multi-node scaling. We're single-host. K8s would add latency (kube-proxy, CNI) and operational complexity. |
| **APScheduler** | Python-native async scheduler. Cron expressions. Persistence to Redis. **Not Celery** — Celery needs a broker (another Redis or RabbitMQ), workers, beat. APScheduler runs in-process, zero extra infra. |
| **structlog** | Structured JSON logging. Correlation IDs across services. Machine-parseable for later Grafana/Loki integration. |

### What We Explicitly Avoid

| Technology | Why Not |
|---|---|
| Celery | Extra broker dependency, worker processes, beat scheduler. We have Redis Streams. |
| GraphQL | Over-engineered for a single-user dashboard. REST is simpler to cache and debug. |
| gRPC | Adds proto compilation step. Services communicate via Redis Streams, not RPC. |
| MongoDB | PostgreSQL is strictly superior for our structured analytical queries. |
| Elasticsearch | No full-text search requirement. PostgreSQL `ILIKE` or trigram index suffices for symbol search. |
| WebSocket libraries (socket.io) | Starlette has native WebSocket support. socket.io adds polling fallback we don't need. |

---

## 7. Async Processing Strategy

### 7.1 Event Loop Architecture

Every service runs a single `asyncio` event loop on a single thread. No thread pools, no multiprocessing within a service.

```python
# Every service entry point follows this pattern
async def main():
    config = Settings()  # pydantic-settings from env
    redis = await create_redis_pool(config)  # hiredis-backed pool

    consumer = StreamConsumer(
        redis=redis,
        stream="tick:normalized",
        group="feature-cg",
        consumer_name=f"feature-{config.instance_id}",
    )
    producer = StreamProducer(redis=redis, stream="feature:computed")

    engine = FeatureEngine(config)

    # Claim any pending messages from previous crash
    await consumer.recover_pending()

    # Main loop — BLOCK 0 means zero CPU when idle
    async for batch in consumer.read(count=100, block_ms=0):
        results = engine.process(batch)
        await producer.write_batch(results)
        await consumer.ack(batch)


if __name__ == "__main__":
    asyncio.run(main())
```

### 7.2 Why Not Threads / Multiprocessing

| Approach | Problem |
|---|---|
| **Threading** | GIL prevents true parallel CPU work. Adds lock contention. asyncio's single-threaded model is faster for I/O-bound work. |
| **Multiprocessing** | Adds IPC overhead (pickle serialization). Memory duplication. For CPU-bound work (Polars), Polars already uses multiple threads internally via Rust's rayon — we get parallelism without managing processes. |
| **Thread pool for blocking I/O** | Only used for unavoidable blocking calls (DNS resolution, file system). All Redis and PG operations use native async drivers. |

### 7.3 Concurrency Patterns

| Pattern | Where Used | Implementation |
|---|---|---|
| **Stream consumer loop** | All stream-consuming services | `async for batch in consumer.read()` — single coroutine, blocks on Redis XREADGROUP |
| **Micro-batching** | Feature engine | 5ms timer + tick buffer. Timer fires, processes accumulated ticks as a batch. Amortizes Polars overhead. |
| **Fire-and-forget** | Telegram alerter | `asyncio.create_task(send_telegram(msg))` — don't await Telegram API response in hot path. Log failures from task exception handler. |
| **Parallel fan-out** | Scanner strategies | `asyncio.gather(*[strategy.evaluate(features) for strategy in active_strategies])` — all strategies evaluate in parallel. Since they're pure compute (no I/O), this interleaves with other coroutines at yield points. |
| **Connection supervisor** | Ingestion service | Persistent coroutine that monitors WS health, triggers reconnect. Uses `asyncio.Event` to signal adapter state changes. |
| **Graceful shutdown** | All services | `signal.signal(SIGTERM, handler)` sets shutdown event. Main loop checks event, drains in-flight work, then exits. Docker sends SIGTERM on `docker compose down`. |

### 7.4 Micro-Batch Timer (Feature Engine)

```
Tick arrives ──► buffer.append(tick)
                     │
                     ├── if buffer age ≥ 5ms:
                     │       process_batch(buffer)
                     │       buffer.clear()
                     │       reset timer
                     │
                     └── if buffer size ≥ 200:
                             process_batch(buffer)  ← flush even if < 5ms
                             buffer.clear()
                             reset timer

Why 5ms?
  - At 30,000 ticks/sec → ~150 ticks per 5ms batch
  - Polars processes 150 ticks in ~3ms (vectorized)
  - Amortization: 3ms compute / 150 ticks = 0.02ms per tick
  - Without batching: Polars overhead per-tick would be ~0.5ms (50x worse)
```

### 7.5 Backpressure Handling

```
If consumer falls behind (lag > 1000 messages):

  1. Health monitor detects: XINFO GROUPS stream → lag field
  2. Alert fired to ops channel
  3. Automatic response options:
     a. Increase COUNT in XREADGROUP (process bigger batches)
     b. Skip intermediate ticks (for feature engine: only latest tick per symbol matters)
     c. Scale: add another consumer to the group (Redis auto-distributes)

  We NEVER drop messages from the stream. Backpressure is always consumer-side.
```

---

## 8. Redis Usage Map

### 8.1 Redis Instances

Single Redis 7.x instance. No cluster, no sentinel.

**Justification**: Our total memory footprint is ~220MB. A single Redis instance handles 100K+ ops/sec. We're at ~60K ops/sec peak (30K writes + 30K reads). Single instance eliminates cluster-mode latency overhead (cross-slot redirects, MOVED errors).

### 8.2 Complete Key Namespace

```
STREAMS (event bus)
════════════════════════════════════════════════════
infusion:stream:tick:raw                  STREAM    ~50K entries     Trim: MAXLEN ~ 50000
infusion:stream:tick:normalized           STREAM    ~100K entries    Trim: MAXLEN ~ 100000
infusion:stream:feature:computed          STREAM    ~200K entries    Trim: MAXLEN ~ 200000
infusion:stream:scan:signals              STREAM    ~10K entries     Trim: MAXLEN ~ 10000
infusion:stream:sector:state              STREAM    ~5K entries      Trim: MAXLEN ~ 5000
infusion:stream:conviction:ranked         STREAM    ~5K entries      Trim: MAXLEN ~ 5000

HOT STATE (latest values, overwritten on each update)
════════════════════════════════════════════════════
infusion:tick:{symbol}                    HASH      700 keys × 256B = 175KB
  Fields: ltp, open, high, low, close, volume, oi, bid, ask, ts

infusion:feature:{symbol}                HASH      700 keys × 1KB = 700KB
  Fields: rsi_14, macd, macd_signal, atr_14, bb_width, rel_vol, vwap,
          ema_5, ema_20, ema_50, obv, delivery_pct, spread_bps, ...

infusion:sector:{sector_id}              HASH      20 keys × 512B = 10KB
  Fields: breadth, pct_above_vwap, weighted_return, money_flow,
          rotation_score, rotation_quadrant, advance, decline

ROLLING BARS (sorted sets, trimmed to N bars)
════════════════════════════════════════════════════
infusion:ohlc:{symbol}:1m                ZSET      700 × 390 bars × 64B = 17MB
infusion:ohlc:{symbol}:5m                ZSET      700 × 78 bars × 64B = 3.4MB
infusion:ohlc:{symbol}:15m               ZSET      700 × 26 bars × 64B = 1.1MB
  Score: bar_timestamp (epoch seconds)
  Value: msgpack({o, h, l, c, v, vwap})

SCANNER STATE (cooldowns and active signals)
════════════════════════════════════════════════════
infusion:cooldown:{strategy}:{symbol}    STRING    ~500 keys × 16B = 8KB
  Value: signal_id
  TTL: 300s (5 min default, configurable per strategy)

infusion:signal:active                   ZSET      ~50 entries
  Score: conviction_score
  Value: signal_id
  Used by dashboard to show "top picks" sorted by conviction

SESSION STATE
════════════════════════════════════════════════════
infusion:symbols                         HASH      ~2000 entries × 128B = 250KB
  Field: instrument_token
  Value: msgpack({symbol, exchange, segment, lot_size, sector})

infusion:symbols:reverse                 HASH      ~2000 entries × 64B = 125KB
  Field: symbol
  Value: instrument_token

HEALTH MONITORING
════════════════════════════════════════════════════
infusion:health:{service_name}           STRING    12 keys × 32B
  Value: last_heartbeat_epoch_ms
  TTL: 30s (if key expires, service is considered dead)

infusion:health:lag:{service_name}       STRING    12 keys × 16B
  Value: consumer_group_lag_count
  Written by each service every 5s

CONFIG (hot-reloadable)
════════════════════════════════════════════════════
infusion:config:scanners                 STRING    ~2KB
  Value: YAML string (loaded from config/scanners.yaml, pushed on change)

infusion:config:alerts                   STRING    ~1KB
  Value: YAML string

infusion:config:version                  STRING    ~8B
  Value: monotonic version counter
  Services watch this key — on change, re-read their config keys
```

### 8.3 Memory Budget Summary

| Category | Keys | Memory |
|---|---|---|
| Streams (6) | — | ~150MB (with message bodies) |
| Hot state (tick + feature + sector) | ~1,420 | ~900KB |
| Rolling OHLC bars | ~2,100 ZSETs | ~22MB |
| Scanner cooldowns | ~500 | ~8KB |
| Session/symbols | ~4,000 | ~375KB |
| Health + config | ~30 | ~5KB |
| **Total** | | **~175MB typical, ~250MB peak** |

### 8.4 Redis Configuration

```
maxmemory 512mb
maxmemory-policy noeviction

# We never want Redis to evict keys — if we approach 512MB, 
# something is wrong (stream not trimming, leak). noeviction 
# makes XADD fail loudly rather than silently dropping data.

# Streams are our most critical data structure.
# Approximate MAXLEN trimming keeps memory bounded.

# Persistence: RDB only as crash recovery safety net
save 300 100
appendonly no

# Performance
tcp-keepalive 60
timeout 0

# hiredis client-side: no config needed, it's a client library
```

### 8.5 Why Not Redis Pub/Sub

| Feature | Redis Pub/Sub | Redis Streams |
|---|---|---|
| Persistence | ❌ Fire-and-forget | ✅ Messages persist until trimmed |
| Consumer groups | ❌ | ✅ Built-in |
| At-least-once delivery | ❌ | ✅ Via ACK + PEL |
| Replay / catch-up | ❌ | ✅ Read from any ID |
| Backpressure visibility | ❌ | ✅ Lag metric per consumer |
| Message ordering | ✅ | ✅ |

Pub/Sub loses messages if a consumer is disconnected. Streams don't. For a trading intelligence system, losing a breakout signal because the alerter restarted is unacceptable.

**Exception**: We use Pub/Sub for exactly one thing — config reload notifications (`infusion:config:changed` channel). This is fire-and-forget by design (if a service misses it, it picks up the new config on next restart).

---

## 9. PostgreSQL Schema Overview

### 9.1 Schema Design Philosophy

- **Partitioned by time**: All time-series tables use `RANGE` partitioning on date/timestamp columns. This makes partition pruning trivial (query for today's data → only scan today's partition) and enables easy data retention (drop partition = instant delete).
- **Denormalized where it matters**: `ohlcv_daily` includes delivery data directly, not in a separate table. Fewer JOINs = faster dashboard queries.
- **JSONB for flexibility**: Feature vectors are stored as JSONB in the `signals` table. Schema-free — when we add new features, old rows are unaffected.
- **No ORM overhead**: We use `asyncpg` directly with raw SQL. SQLAlchemy ORM adds 2-5x query overhead for bulk inserts. Alembic handles migrations independently.

### 9.2 Table Map

```
┌─────────────────────────────────────────────────────────┐
│                    PostgreSQL Schema                     │
│                                                          │
│  TIME-SERIES DATA (partitioned)                         │
│  ──────────────────────────────                         │
│  ┌──────────────┐  ┌──────────────────┐                 │
│  │ ohlcv_daily  │  │ ohlcv_intraday   │                 │
│  │              │  │                   │                 │
│  │ Part: MONTH  │  │ Part: DAY         │                 │
│  │ Retain: 10yr │  │ Retain: 90 days   │                 │
│  └──────────────┘  └──────────────────┘                 │
│                                                          │
│  INTELLIGENCE DATA                                      │
│  ─────────────────                                      │
│  ┌──────────────┐  ┌──────────────────┐                 │
│  │   signals    │  │  sector_daily     │                 │
│  │              │  │                   │                 │
│  │ Signal log + │  │ EOD sector        │                 │
│  │ outcome      │  │ snapshots         │                 │
│  │ tracking     │  │                   │                 │
│  │ Retain: 2yr  │  │ Retain: 5yr       │                 │
│  └──────────────┘  └──────────────────┘                 │
│                                                          │
│  REFERENCE DATA                                         │
│  ──────────────                                         │
│  ┌──────────────┐  ┌──────────────────┐                 │
│  │   symbols    │  │ corporate_actions │                 │
│  │              │  │                   │                 │
│  │ Symbol       │  │ Splits, bonuses,  │                 │
│  │ master       │  │ dividends         │                 │
│  └──────────────┘  └──────────────────┘                 │
│                                                          │
│  OPERATIONAL DATA                                       │
│  ────────────────                                       │
│  ┌──────────────┐  ┌──────────────────┐                 │
│  │institutional │  │   alert_log      │                 │
│  │   _flows     │  │                  │                 │
│  │              │  │ Audit trail      │                 │
│  │ FII/DII      │  │ Retain: 1yr      │                 │
│  └──────────────┘  └──────────────────┘                 │
└─────────────────────────────────────────────────────────┘
```

### 9.3 Core Tables

#### `ohlcv_daily` — Daily market data

```sql
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
    turnover_cr         NUMERIC(12,2),   -- in crores
    trades              INTEGER,
    open_interest       BIGINT,           -- for F&O underlyings
    PRIMARY KEY (symbol, trade_date)
) PARTITION BY RANGE (trade_date);

-- Monthly partitions: auto-created by scheduler
-- CREATE TABLE ohlcv_daily_2026_01 PARTITION OF ohlcv_daily
--     FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
```

#### `ohlcv_intraday` — Intraday bars (for backtesting)

```sql
CREATE TABLE ohlcv_intraday (
    symbol          TEXT            NOT NULL,
    timeframe       TEXT            NOT NULL,    -- '1m', '5m', '15m'
    bar_time        TIMESTAMPTZ     NOT NULL,
    open            NUMERIC(12,2),
    high            NUMERIC(12,2),
    low             NUMERIC(12,2),
    close           NUMERIC(12,2),
    volume          BIGINT,
    vwap            NUMERIC(12,2),
    PRIMARY KEY (symbol, timeframe, bar_time)
) PARTITION BY RANGE (bar_time);

-- Daily partitions (intraday data is high-volume)
-- Auto-created by scheduler at 06:00 daily
```

#### `signals` — Signal history + outcome tracking

```sql
CREATE TABLE signals (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at          TIMESTAMPTZ DEFAULT now() NOT NULL,
    symbol              TEXT        NOT NULL,
    strategy            TEXT        NOT NULL,       -- 'breakout', 'volume_surge', etc.
    signal_type         TEXT        NOT NULL,       -- 'BULLISH', 'BEARISH'
    conviction_score    NUMERIC(5,1),
    conviction_grade    TEXT,                        -- 'A+', 'A', 'B+', 'B', 'C'
    price_at_signal     NUMERIC(12,2)   NOT NULL,
    volume_at_signal    BIGINT,
    features            JSONB       NOT NULL,       -- full feature vector snapshot

    -- Outcome tracking (filled asynchronously by EOD job)
    price_1d            NUMERIC(12,2),
    price_3d            NUMERIC(12,2),
    price_5d            NUMERIC(12,2),
    return_1d_pct       NUMERIC(6,2),
    return_3d_pct       NUMERIC(6,2),
    return_5d_pct       NUMERIC(6,2),
    outcome_label       TEXT                         -- 'WIN', 'LOSS', 'FLAT' (>3% threshold)
);
```

#### `sector_daily` — Sector snapshots

```sql
CREATE TABLE sector_daily (
    sector_id           TEXT    NOT NULL,
    trade_date          DATE    NOT NULL,
    breadth             NUMERIC(5,2),       -- 0.00 to 1.00
    pct_above_vwap      NUMERIC(5,2),
    weighted_return_pct NUMERIC(6,2),
    money_flow_score    NUMERIC(8,2),
    rotation_score      NUMERIC(6,2),
    rotation_quadrant   TEXT,                -- 'LEADING', 'WEAKENING', 'LAGGING', 'IMPROVING'
    advance_count       INTEGER,
    decline_count       INTEGER,
    fii_net_cr          NUMERIC(12,2),
    dii_net_cr          NUMERIC(12,2),
    PRIMARY KEY (sector_id, trade_date)
);
```

#### `symbols` — Instrument master

```sql
CREATE TABLE symbols (
    symbol              TEXT    PRIMARY KEY,
    isin                TEXT    UNIQUE,
    instrument_token    INTEGER,
    exchange            TEXT    DEFAULT 'NSE',
    segment             TEXT,                -- 'EQ', 'FO', 'INDEX'
    series              TEXT    DEFAULT 'EQ', -- 'EQ', 'BE', 'BZ'
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
```

#### `institutional_flows` — FII/DII daily data

```sql
CREATE TABLE institutional_flows (
    trade_date      DATE    PRIMARY KEY,
    fii_buy_cr      NUMERIC(14,2),
    fii_sell_cr     NUMERIC(14,2),
    fii_net_cr      NUMERIC(14,2),
    dii_buy_cr      NUMERIC(14,2),
    dii_sell_cr     NUMERIC(14,2),
    dii_net_cr      NUMERIC(14,2)
);
```

#### `corporate_actions` — Splits, bonuses, dividends

```sql
CREATE TABLE corporate_actions (
    id                  UUID    DEFAULT gen_random_uuid() PRIMARY KEY,
    symbol              TEXT    NOT NULL,
    action_type         TEXT    NOT NULL,     -- 'SPLIT', 'BONUS', 'DIVIDEND', 'RIGHTS'
    ex_date             DATE,
    record_date         DATE,
    details             TEXT,                 -- "2:1 split", "1:1 bonus", etc.
    adjustment_factor   NUMERIC(10,6),        -- price adjustment multiplier
    created_at          TIMESTAMPTZ DEFAULT now()
);
```

#### `alert_log` — Delivery audit trail

```sql
CREATE TABLE alert_log (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    signal_id       UUID        REFERENCES signals(id),
    channel         TEXT        NOT NULL,     -- 'telegram', 'websocket'
    delivered_at    TIMESTAMPTZ DEFAULT now(),
    message_hash    TEXT,                      -- for deduplication
    status          TEXT        DEFAULT 'SENT' -- 'SENT', 'FAILED', 'THROTTLED'
);
```

### 9.4 Indexing Strategy

```sql
-- Primary access patterns and their indexes:

-- "Show me today's OHLCV for all symbols" (pre-market dashboard load)
-- → Partition pruning on trade_date handles this. No extra index needed.

-- "Show me last 50 days for RELIANCE" (chart data, feature warmup)
CREATE INDEX idx_ohlcv_daily_sym_date ON ohlcv_daily (symbol, trade_date DESC);

-- "Show me intraday 5m bars for RELIANCE today" (chart data)
-- → Partition pruning on bar_time + PK covers this.

-- "Show me latest A+ and A signals" (dashboard top picks)
CREATE INDEX idx_signals_grade_time ON signals (conviction_grade, created_at DESC)
    WHERE conviction_grade IN ('A+', 'A');

-- "Show me all signals for RELIANCE" (symbol detail page)
CREATE INDEX idx_signals_symbol_time ON signals (symbol, created_at DESC);

-- "Find signals with WIN outcomes for model training"
CREATE INDEX idx_signals_outcome ON signals (outcome_label, created_at DESC)
    WHERE outcome_label IS NOT NULL;

-- "Show me sector rotation for last 20 days"
CREATE INDEX idx_sector_daily_date ON sector_daily (trade_date DESC);

-- "Find corporate actions for RELIANCE around ex-date"
CREATE INDEX idx_corp_actions_sym_date ON corporate_actions (symbol, ex_date DESC);
```

### 9.5 Data Volume Estimates

| Table | Rows/Day | Rows/Year | Avg Row Size | Annual Size |
|---|---|---|---|---|
| `ohlcv_daily` | 2,000 (Nifty 500 + F&O) | ~500K | 150B | ~75MB |
| `ohlcv_intraday` | 700 × 3 TF × 78 bars = ~164K | ~40M | 80B | ~3.2GB |
| `signals` | 50–200 | ~50K | 2KB (with JSONB) | ~100MB |
| `sector_daily` | 20 | ~5K | 120B | ~600KB |
| `institutional_flows` | 1 | ~250 | 80B | ~20KB |
| **Total annual** | | | | **~3.5GB** |

PostgreSQL is comfortable with this volume. No sharding needed. Partition management is the only maintenance task (auto-created by scheduler).

### 9.6 Connection Pooling

```
asyncpg pool configuration:
  min_size: 2           # Keep 2 connections warm at all times
  max_size: 10          # Max 10 concurrent connections
  max_inactive_connection_lifetime: 300s

Services that need PG:
  - api (query serving)
  - nse-scraper (data loading)
  - scheduler/jobs (persistence, outcome tracking)
  - feature-engine (warmup reads at 08:30)

Services that DON'T need PG:
  - ingestion, normalizer, scanner, conviction, alerter, ws-gateway, telegram-bot
  (these are Redis-only in the hot path)
```

---

## 10. WebSocket Ingestion Architecture

### 10.1 Adapter Abstraction

```
                    ┌─────────────────────────┐
                    │   Connection Supervisor  │
                    │                          │
                    │   • Manages adapter      │
                    │     lifecycle            │
                    │   • Monitors heartbeat   │
                    │   • Triggers reconnect   │
                    │   • Rotates auth tokens  │
                    └────────┬────────────────┘
                             │
                    ┌────────▼────────────────┐
                    │     BrokerAdapter ABC    │
                    │                          │
                    │  connect()               │
                    │  subscribe(instruments)  │
                    │  on_tick(callback)       │
                    │  disconnect()            │
                    │  health_check() → bool   │
                    └────────┬────────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
    ┌─────────▼────┐  ┌─────▼──────┐  ┌────▼───────────┐
    │UpstoxAdapter │  │KiteAdapter │  │FutureAdapter   │
    │              │  │            │  │(placeholder)   │
    │• protobuf    │  │• binary    │  │                │
    │  decode      │  │  packet    │  │• implement     │
    │• OAuth2 PKCE │  │  decode    │  │  BrokerAdapter │
    │• market data │  │• API key + │  │  ABC           │
    │  WS endpoint │  │  access    │  │                │
    │              │  │  token     │  │                │
    └──────────────┘  └────────────┘  └────────────────┘
```

### 10.2 Connection State Machine

```
                         ┌──────────┐
                         │   INIT   │
                         └────┬─────┘
                              │ start()
                              ▼
                    ┌──────────────────┐
               ┌───►│ AUTHENTICATING   │
               │    └────────┬─────────┘
               │             │ token obtained
               │             ▼
               │    ┌──────────────────┐
               │    │   CONNECTING     │
               │    └────────┬─────────┘
               │             │ WS handshake complete
               │             ▼
               │    ┌──────────────────┐
               │    │  SUBSCRIBING     │
               │    └────────┬─────────┘
               │             │ subscription ACK received
               │             ▼
               │    ┌──────────────────┐
               │    │   STREAMING      │◄─── normal state
               │    └────────┬─────────┘     ticks flowing
               │             │
               │             │ heartbeat timeout / WS close / error
               │             ▼
               │    ┌──────────────────┐
               └────┤  RECONNECTING    │
                    │                  │
                    │  backoff: 1s,    │
                    │  2s, 4s, 8s,    │
                    │  16s, max 30s   │
                    └──────────────────┘
```

### 10.3 Upstox Adapter Specifics

```
Authentication:
  1. OAuth2 authorization code flow (one-time, manual login)
  2. Store refresh_token in encrypted env var
  3. On startup: exchange refresh_token → access_token
  4. access_token expires in 24h → pre-emptive refresh at 23h mark

WebSocket:
  Endpoint: wss://api.upstox.com/v2/feed/market-data-feed
  Protocol: protobuf (MarketDataFeed.proto)
  Subscription: send JSON subscribe message with instrument keys
  Heartbeat: server sends ping every 30s → respond with pong
  Max instruments per connection: 500 (Upstox limit)
  If >500 instruments: open multiple WS connections

Tick decode:
  protobuf binary → MarketDataFeed message → extract:
    - instrument_token (int)
    - ltp (float)
    - open, high, low, close (floats)
    - volume (int)
    - oi (int)
    - best_bid, best_ask (floats)
    - best_bid_qty, best_ask_qty (ints)
    - exchange_timestamp (epoch ms)

Output: RawTick dataclass → msgpack → XADD tick:raw
```

### 10.4 Kite (Zerodha) Adapter Specifics

```
Authentication:
  1. request_token from login redirect (one-time)
  2. Exchange for access_token via API
  3. access_token valid for 1 trading day (until 06:00 next day)
  4. Re-authenticate daily at 08:30 via scheduler job

WebSocket:
  Endpoint: wss://ws.kite.trade
  Protocol: Custom binary packet format
  Subscription modes: 'ltp', 'quote', 'full'
  We use 'full' mode for top 100, 'quote' for rest
  Max instruments: 3000 per connection
  Heartbeat: server sends 1-byte ping every ~2.5s

Binary packet decode:
  - First 2 bytes: number of packets
  - Each packet: 2 bytes length + payload
  - Payload layout depends on mode (ltp=8B, quote=44B, full=184B)
  - All values are big-endian integers/floats
```

### 10.5 Subscription Tiers

```
┌────────────────────────────────────────────────────────────────┐
│                    INSTRUMENT TIERS                              │
│                                                                  │
│  TIER 1 — Full Tick (every trade, no sampling)                  │
│  ─────────────────────────────────────────                      │
│  • NIFTY 50 constituents                              50 inst  │
│  • Top F&O underlyings by OI (refreshed weekly)      100 inst  │
│  • NIFTY, BANKNIFTY, FINNIFTY indices                  3 inst  │
│  ─────────────────────────────────────────────────────────────  │
│  Total: ~153 instruments @ full tick                            │
│  Expected rate: ~20,000 ticks/sec peak                          │
│                                                                  │
│  TIER 2 — Throttled (latest tick per 500ms window)              │
│  ─────────────────────────────────────────                      │
│  • NIFTY NEXT 50 constituents                         50 inst  │
│  • NIFTY MIDCAP 100 constituents                     100 inst  │
│  ─────────────────────────────────────────────────────────────  │
│  Total: ~150 instruments @ 2 ticks/sec/symbol = 300 ticks/sec  │
│                                                                  │
│  TIER 3 — Snapshot (latest tick per 2s window)                  │
│  ─────────────────────────────────────────                      │
│  • Remaining NIFTY 500 constituents                  ~300 inst │
│  ─────────────────────────────────────────────────────────────  │
│  Total: ~300 instruments @ 0.5 ticks/sec/symbol = 150 ticks/sec│
│                                                                  │
│  AGGREGATE: ~20,450 ticks/sec peak → tick:raw stream            │
└────────────────────────────────────────────────────────────────┘
```

**Why tier?** Upstox/Kite send ticks for all subscribed instruments at full speed. We can't control the broker's tick rate. Tiering happens **inside the ingestion service** — we subscribe to all instruments at full speed, but for Tier 2/3, we drop intermediate ticks and only forward the latest to `tick:raw`. This reduces downstream load without losing any information (we only need the latest price for mid/small caps).

### 10.6 Reconnect Strategy

```python
class ExponentialBackoff:
    """Reconnect with exponential backoff + jitter."""

    initial_delay: float = 1.0  # seconds
    max_delay: float = 30.0  # seconds
    multiplier: float = 2.0
    jitter_pct: float = 0.2  # ±20% randomization

    # Sequence: 1.0s → 2.0s → 4.0s → 8.0s → 16.0s → 30.0s → 30.0s → ...
    # With jitter: 0.8-1.2s → 1.6-2.4s → 3.2-4.8s → ...

    # Reset to initial_delay after successful connection + first tick received.
    # NOT on connect success — only on first tick. This prevents
    # rapid reconnect loops when broker accepts WS but sends no data.
```

### 10.7 Fault Tolerance Matrix

| Failure | Detection | Response | Recovery Time |
|---|---|---|---|
| WS disconnect (clean) | `on_close` event | Immediate reconnect | 1-2s |
| WS disconnect (dirty) | Heartbeat timeout (35s) | Reconnect with backoff | 5-35s |
| Auth token expired | 401 from WS or REST | Re-authenticate, then reconnect | 3-5s |
| Broker API down | Connection refused / timeout | Backoff up to 30s, alert on 3rd failure | 30s-5min |
| Network partition | No heartbeat + no close event | Heartbeat timeout → reconnect | 35s + backoff |
| Redis down | XADD raises ConnectionError | Buffer ticks in-memory (max 10K), retry Redis every 1s | 1-10s |
| Ingestion service crash | Docker restart policy | Docker restarts container, service re-authenticates | 5-10s |

### 10.8 Data Integrity Guarantees

| Guarantee | How |
|---|---|
| **No duplicate ticks** | Each tick carries `exchange_timestamp` + `instrument_token`. Normalizer deduplicates by this composite key (in-memory set, last 1000 IDs per symbol). |
| **Ordering within symbol** | Broker WS delivers ticks in exchange order per instrument. Single-writer-per-stream preserves this. |
| **No cross-symbol ordering** | We don't guarantee ordering across symbols. Each symbol's tick stream is independent. This is fine — features are computed per-symbol. |
| **Gap detection** | Normalizer tracks expected sequence numbers (where available). Gaps trigger a warning log + metric, not a retry (market data gaps are normal during halt/circuit breaker). |

---

## Phase 1 Boundary

This completes the system foundations. All subsequent phases build on top of these primitives:

- **Phase 2** (Market Data & NSE Engine) will detail the NSE scraper internals, broker adapter protocol specifics, and sector classification — all of which produce data into the Redis Streams and PostgreSQL tables defined here.
- **Phase 3** (Scanner & Conviction Engine) will detail the strategy implementations and scoring models — all of which consume from `feature:computed` and publish to `scan:signals` and `conviction:ranked` streams defined here.
- **Phase 4** (Alerting & AI) will detail the alert pipeline — consuming from `conviction:ranked` defined here.
- **Phase 5** (Dashboard & UI) will detail the Next.js frontend — consuming from the WS Gateway defined here.
- **Phase 6** (Performance) will optimize the latency budgets and memory estimates calculated here.

**Awaiting approval to proceed to Phase 2.**
