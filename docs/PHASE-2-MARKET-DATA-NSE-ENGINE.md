# PHASE 2 — MARKET DATA & NSE ENGINE

> Deep architecture for market data acquisition, normalization, sector classification, and breadth computation.
> All decisions conform to [Global Architecture Constraints](./GLOBAL-ARCHITECTURE-CONSTRAINTS.md).

---

## 1. NSE Scraper Architecture

### 1.1 The NSE Problem

NSE's website (`nseindia.com`) is the only free source for critical data not available via broker APIs: bhavcopy, delivery percentages, FII/DII flows, bulk/block deals, option chain snapshots, corporate actions, and index constituents. There is no official API — these are internal JSON endpoints that the website's frontend calls. NSE aggressively blocks automated access.

### 1.2 NSE's Defense Layers

```
Layer 1: Cookie Gate
─────────────────────
NSE requires a valid session cookie obtained by loading the homepage first.
API endpoints return 401 or redirect to homepage without this cookie.
Cookie name: nsit, nseappid, bm_sv (varies)
Cookie lifetime: ~5 minutes (short-lived by design)

Layer 2: Header Fingerprinting
──────────────────────────────
NSE checks User-Agent, Referer, Accept-Language, Accept-Encoding headers.
Missing or inconsistent headers → 403.
The Referer must be a plausible NSE page URL.

Layer 3: Rate Limiting
──────────────────────
Aggressive per-IP rate limiting.
Observed thresholds (empirically determined):
  - > 5 requests in 2 seconds → temporary block (30-60s)
  - > 30 requests in 1 minute → longer block (5-10 min)
  - > 100 requests in 10 minutes → IP block (30 min to hours)
These thresholds fluctuate. Treat them as soft upper bounds.

Layer 4: TLS Fingerprinting (suspected)
────────────────────────────────────────
Some users report blocks even with correct cookies/headers.
NSE may fingerprint the TLS ClientHello (JA3 hash).
Python's default ssl module has a distinct TLS fingerprint vs browsers.
Mitigation: use curl_cffi or tls_client for browser-like TLS.

Layer 5: Behavioral Analysis (suspected)
──────────────────────────────────────────
Perfectly periodic requests (exactly every N seconds) may trigger blocks.
Mitigation: add random jitter to all request intervals.
```

### 1.3 Session Manager Design

```
┌──────────────────────────────────────────────────────┐
│                  NSE Session Manager                  │
│                                                       │
│  ┌───────────────┐    ┌───────────────┐              │
│  │ Cookie Warmer │    │  Header Pool  │              │
│  │               │    │               │              │
│  │ GET / every   │    │ 20 real       │              │
│  │ 3 min to      │    │ browser UAs   │              │
│  │ refresh       │    │ rotated per   │              │
│  │ cookies       │    │ session cycle │              │
│  └───────┬───────┘    └───────┬───────┘              │
│          │                    │                       │
│          ▼                    ▼                       │
│  ┌────────────────────────────────────┐               │
│  │        Request Executor            │               │
│  │                                    │               │
│  │  • Attaches current cookies        │               │
│  │  • Attaches rotated headers        │               │
│  │  • Enforces min 350ms gap          │               │
│  │  • Adds ±100ms random jitter       │               │
│  │  • 3 retries with backoff          │               │
│  │  • Detects block → re-warm session │               │
│  └────────────────────────────────────┘               │
│                                                       │
│  Session States:                                     │
│    COLD → WARMING → WARM → STALE → WARMING           │
│                       │                               │
│                       └── every 3 min auto-refresh    │
└──────────────────────────────────────────────────────┘
```

### 1.4 Request Executor — Detailed Flow

```
scrape(endpoint) called:
│
├── 1. Check session state
│   └── if COLD or STALE:
│       ├── GET https://www.nseindia.com/  (homepage)
│       ├── Extract Set-Cookie headers
│       ├── Store cookies in session jar
│       ├── State → WARM
│       └── Wait 500ms before proceeding (appear human)
│
├── 2. Enforce rate limit
│   └── time_since_last_request = now - last_request_time
│       if time_since_last_request < 350ms:
│           sleep(350ms - time_since_last_request + random(0, 100ms))
│
├── 3. Build request
│   ├── URL: BASE_URL + endpoint
│   ├── Headers:
│   │   User-Agent: (rotated from pool)
│   │   Accept: application/json
│   │   Accept-Language: en-US,en;q=0.9
│   │   Accept-Encoding: gzip, deflate, br
│   │   Referer: https://www.nseindia.com/market-data/live-equity-market
│   │   X-Requested-With: XMLHttpRequest
│   │   Connection: keep-alive
│   ├── Cookies: (from session jar)
│   └── Timeout: 10s
│
├── 4. Execute request
│   └── async with aiohttp.ClientSession.get(url, headers, cookies)
│
├── 5. Handle response
│   ├── 200 OK → parse JSON → return data
│   ├── 401/403 → session STALE → re-warm → retry (max 2)
│   ├── 429 Too Many Requests → backoff 30s → retry
│   ├── 5xx → backoff (1s, 2s, 4s) → retry (max 3)
│   └── Timeout → backoff 2s → retry (max 3)
│
└── 6. Update state
    ├── last_request_time = now
    └── log: endpoint, status, latency, attempt_count
```

### 1.5 TLS Fingerprint Mitigation

```
Problem:
  Python's ssl module sends a TLS ClientHello with JA3 hash that
  differs from Chrome/Firefox. Some CDNs (Cloudflare, Akamai) 
  fingerprint this and block non-browser clients.

Solution hierarchy (try in order):
  1. curl_cffi library (pip install curl_cffi)
     - Uses libcurl with browser-impersonation TLS settings
     - Supports chrome110, chrome120, firefox profiles
     - Drop-in replacement for requests/aiohttp for this use case
     - Minimal dependency, ~5MB
  
  2. If curl_cffi fails: playwright headless browser (heavy, last resort)
     - Only for endpoints that actively detect automation
     - Run headless Chromium, extract API responses from network tab
     - High resource cost — use only if option 1 is blocked

Decision: Start with curl_cffi. It handles NSE reliably in practice.
           Playwright is the nuclear option — flag for future if needed.
```

---

## 2. Endpoint Classification

### 2.1 Complete Endpoint Map

Every NSE data endpoint we consume, classified by criticality, schedule, and format.

```
CRITICAL — Required for core functionality
═══════════════════════════════════════════

Endpoint                                          Format   Schedule           Staleness OK
─────────────────────────────────────────────────────────────────────────────────────────
/api/market-data-pre-open?key=NIFTY              JSON     09:00-09:15        never (realtime)
  → Pre-open session prices, indicated open prices
  → Used for: gap analysis, opening sentiment

/api/equity-stockIndices?index=NIFTY%2050         JSON     every 60s (mkt)    60s
  → Constituent list + LTP + change% + volume
  → Used for: sector mapping, market breadth, fallback prices

/api/option-chain-indices?symbol=NIFTY            JSON     every 3min (mkt)   3min
  → Full option chain: strikes, OI, volume, IV, Greeks
  → Used for: OI analysis, PCR, max pain, smart money detection

/api/option-chain-equities?symbol=RELIANCE        JSON     every 5min (mkt)   5min
  → Per-stock option chain (for F&O stocks only)
  → Used for: stock-level OI buildup, put-call skew


IMPORTANT — Required for conviction scoring
═══════════════════════════════════════════

Endpoint                                          Format   Schedule           Staleness OK
─────────────────────────────────────────────────────────────────────────────────────────
/api/fiidiiTradeReact                             JSON     18:30 daily        24h
  → FII/DII buy/sell/net values
  → Used for: institutional flow direction

/api/corporates-corporateActions?...              JSON     06:00 daily        24h
  → Upcoming splits, bonuses, dividends
  → Used for: price adjustment, event filtering

/api/live-analysis-variations?index=gainers       JSON     every 60s (mkt)    60s
  → Top gainers/losers with volume
  → Used for: momentum detection, sector screening

/api/merged-daily-reports-capital                 JSON     19:00 daily        24h
  → Bulk deals, block deals
  → Used for: institutional accumulation signals


SUPPORTING — Nice-to-have, enrichment data
═══════════════════════════════════════════

Endpoint                                          Format   Schedule           Staleness OK
─────────────────────────────────────────────────────────────────────────────────────────
/api/reports?archives=...&type=equities            CSV.gz  18:00 daily        24h
  → Full bhavcopy (all securities traded today)
  → Used for: EOD OHLCV, delivery data backfill

/api/equity-stockIndices?index=SECURITIES%20...   JSON     weekly             7d
  → Complete security list by index
  → Used for: symbol master refresh

/api/chart-databyindex?index=NIFTY%2050           JSON     every 5min (mkt)   5min
  → Index intraday chart data
  → Used for: index overlay on dashboard
```

### 2.2 Endpoint Priority During Rate Limit Pressure

When approaching rate limits, endpoints are prioritized in this order:

```
Priority 1 (never skip):     option-chain-indices (NIFTY, BANKNIFTY)
Priority 2 (reduce freq):    equity-stockIndices (extend to 120s interval)
Priority 3 (defer):          option-chain-equities (individual stocks)
Priority 4 (skip):           live-analysis-variations, chart-databyindex
Priority 5 (already EOD):    bhavcopy, fiidii, bulk-block (fixed schedule, no pressure)
```

### 2.3 Response Size Estimates

| Endpoint | Response Size | Parse Time | Parse Method |
|---|---|---|---|
| `equity-stockIndices` (NIFTY 50) | ~80KB | 2ms | `orjson.loads()` |
| `option-chain-indices` (NIFTY) | ~400KB | 8ms | `orjson.loads()` + flatten |
| `option-chain-equities` (single stock) | ~150KB | 4ms | `orjson.loads()` + flatten |
| `fiidiiTradeReact` | ~2KB | <1ms | `orjson.loads()` |
| Bhavcopy CSV.gz | ~1.5MB compressed | 50ms | `polars.read_csv()` |

Use `orjson` (3-10x faster than stdlib `json`) for all JSON parsing. Use Polars `read_csv` for bhavcopy (not pandas).

---

## 3. Rate-Limit Handling

### 3.1 Rate Limit Budget

```
NSE observed safe threshold: ~3 requests / second sustained

Our request budget during market hours (09:15 – 15:30 = 375 min):

  Fixed schedule requests:
    equity-stockIndices (5 indices × every 60s)  = 5/min
    option-chain-indices (2 × every 3min)        = 0.67/min
    option-chain-equities (20 stocks × every 5min) = 4/min
    live-analysis-variations (every 60s)          = 1/min
    chart-databyindex (every 5min)                = 0.2/min
    ──────────────────────────────────────────────
    Total: ~11 requests/min = 0.18 requests/sec

  Session warm-up:
    Homepage GET every 3 min                      = 0.33/min
    ──────────────────────────────────────────────
    Total including warmup: ~11.3 requests/min = 0.19 requests/sec

  Headroom: 3.0 - 0.19 = 2.81 req/sec unused (massive safety margin)
```

### 3.2 Adaptive Rate Limiter

```
┌────────────────────────────────────────────────────┐
│              Adaptive Rate Limiter                  │
│                                                     │
│  Sliding window: last 60 seconds                   │
│  Token bucket: refills at configured rate           │
│                                                     │
│  States:                                           │
│    NORMAL    → max 3 req/sec, min gap 350ms        │
│    CAUTIOUS  → max 1 req/sec, min gap 1000ms       │
│    BACKOFF   → no requests, wait for cooldown      │
│                                                     │
│  Transitions:                                      │
│    NORMAL → CAUTIOUS: on first 429 or 403          │
│    CAUTIOUS → BACKOFF: on second consecutive error │
│    BACKOFF → CAUTIOUS: after 60s cooldown          │
│    CAUTIOUS → NORMAL: after 5 successful requests  │
│                                                     │
│  Jitter: all intervals ±15% randomized             │
└────────────────────────────────────────────────────┘
```

### 3.3 Per-Endpoint Rate Tracking

Each endpoint gets its own rate tracker because NSE may rate-limit per-path, not just per-IP.

```
Tracked per endpoint:
  - last_request_time: epoch_ms
  - consecutive_errors: int
  - total_requests_last_60s: int
  - avg_response_time_ms: float (rolling 10-request average)
  - state: NORMAL | CAUTIOUS | BACKOFF

If avg_response_time spikes (>3x baseline), treat as soft rate-limit signal.
NSE sometimes doesn't 429 but instead slows responses from 200ms to 3s.
```

### 3.4 Block Detection and Recovery

```
Signal: HTTP 401 / 403 / 429 / connection reset / timeout after 10s

Response:
  1. Log: endpoint, status, response body (if any)
  2. Mark session as STALE
  3. Wait: 30s (first block), 120s (second), 300s (third)
  4. Re-warm session (fresh homepage GET)
  5. Test with lowest-priority endpoint first
  6. If test succeeds → resume normal schedule
  7. If test fails → extend wait, alert via Telegram

Hard block (>3 consecutive failures across all endpoints):
  → Switch to degraded mode
  → Stop all NSE scraping
  → Alert operator: "NSE block detected, manual intervention may be needed"
  → Continue operating on broker WS data only (no delivery%, no FII/DII, no OI)
  → Retry session warm-up every 10 minutes
```

---

## 4. Cache Strategy

### 4.1 Cache Layers

```
Layer 1: In-Memory (process-local)
──────────────────────────────────
  What: Parsed, structured response objects
  Where: Python dict in nse-scraper service
  TTL: Matches scrape interval (e.g., 60s for equity-stockIndices)
  Eviction: Overwrite on next successful scrape
  Purpose: Avoid re-parsing same response if multiple consumers ask

Layer 2: Redis (shared hot state)
─────────────────────────────────
  What: Latest scraped data in consumable format
  Where: Redis HASH keys
  TTL: 2x scrape interval (e.g., 120s for 60s-interval data)
  Purpose: Other services (sector-intel, conviction) read cached data
           without hitting NSE themselves

Layer 3: PostgreSQL (permanent store)
─────────────────────────────────────
  What: Historical scraped data
  Where: Dedicated tables
  TTL: Per retention policy (see Phase 1, section 9.5)
  Purpose: Backtesting, model training, trend analysis
```

### 4.2 Redis Cache Keys (NSE Data)

```
infusion:nse:index:{index_name}              HASH     TTL: 120s
  Fields: constituents (msgpack list), last_updated, source_url
  Written by: nse-scraper on each successful scrape
  Read by: sector-intel, breadth engine

infusion:nse:oi:index:{symbol}               HASH     TTL: 360s
  Fields: pcr, max_pain, total_ce_oi, total_pe_oi, iv_skew, chain (msgpack)
  Written by: nse-scraper every 3min
  Read by: scanner (OI strategies), conviction scorer

infusion:nse:oi:equity:{symbol}              HASH     TTL: 600s
  Fields: same as index OI
  Written by: nse-scraper every 5min
  Read by: scanner, conviction

infusion:nse:fii_dii                         HASH     TTL: 86400s (24h)
  Fields: fii_buy, fii_sell, fii_net, dii_buy, dii_sell, dii_net, date
  Written by: nse-scraper at 18:30
  Read by: conviction scorer, sector-intel

infusion:nse:delivery:{symbol}               HASH     TTL: 86400s
  Fields: delivery_qty, delivery_pct, avg_delivery_pct_20d
  Written by: nse-scraper post-market
  Read by: feature-engine (next day warmup)
```

### 4.3 Cache Invalidation Rules

```
Rule 1: TTL-based expiry (primary mechanism)
  Every cached key has a TTL = 2x expected refresh interval.
  If scraper fails to refresh, key expires. Consumers detect absence
  and operate with degraded data (last known value from PostgreSQL).

Rule 2: Explicit overwrite (normal operation)
  Each successful scrape overwrites the Redis key. The TTL is reset.
  No explicit invalidation needed — staleness is bounded by TTL.

Rule 3: Staleness flag
  Each cache key includes a `last_updated` epoch field.
  Consumers check: if (now - last_updated) > expected_interval * 3:
      log.warning("stale data", symbol=symbol, age_sec=age)
      use_data_but_flag_as_stale()
  This is a soft warning, not a hard failure.

Rule 4: Market state awareness
  Pre-market (before 09:15): option chain data is stale by definition.
  Don't scrape option chain before 09:20 (exchange data not yet populated).
  Post-market (after 15:30): stop refreshing realtime endpoints.
  Transition to EOD scrape schedule.
```

### 4.4 Freshness Guarantees

| Data | Freshness Target | Acceptable Staleness | Degraded Behavior |
|---|---|---|---|
| Index constituents (LTP) | 60s | 5 min | Use broker WS tick data instead |
| Option chain (index) | 3 min | 10 min | Use last snapshot, flag stale |
| Option chain (equity) | 5 min | 15 min | Use last snapshot, flag stale |
| FII/DII flows | Once daily at 18:30 | 24h | Use previous day's value |
| Delivery data | Once daily post-market | 24h | Skip delivery% feature |
| Bhavcopy | Once daily at 18:00 | 24h | Use broker's EOD data |

---

## 5. Upstox WebSocket Architecture

### 5.1 Auth Flow (Practical)

```
INITIAL SETUP (one-time, manual):
  1. Register app on Upstox developer portal
  2. Get api_key and api_secret
  3. Open browser: https://api.upstox.com/v2/login/authorization/dialog
     ?client_id={api_key}&redirect_uri={redirect_uri}&response_type=code
  4. User logs in with Upstox credentials
  5. Upstox redirects to: {redirect_uri}?code={authorization_code}
  6. Store authorization_code

TOKEN EXCHANGE (daily at 08:30, automated):
  POST https://api.upstox.com/v2/login/authorization/token
  Body: {
    code: {authorization_code},     // from step 6
    client_id: {api_key},
    client_secret: {api_secret},
    redirect_uri: {redirect_uri},
    grant_type: "authorization_code"
  }
  Response: { access_token: "...", expires_in: 86400 }

  Store access_token in Redis: infusion:auth:upstox (TTL: 82800s = 23h)

LIMITATION:
  Upstox access_token is valid for 1 day.
  authorization_code is single-use.
  After token expires, user must re-login manually.
  
  Practical workaround:
    - Store credentials in encrypted .env
    - At 08:30, scheduler triggers auth job
    - If auth fails → Telegram alert: "Manual Upstox re-login required"
    - Build a simple local Flask route that handles the OAuth redirect
      so re-login is a single browser click, not a manual copy-paste
```

### 5.2 WebSocket Connection

```
Endpoint: wss://api.upstox.com/v2/feed/market-data-feed

Connection setup:
  1. GET https://api.upstox.com/v2/feed/market-data-feed/authorize
     Headers: Authorization: Bearer {access_token}
     Response: { authorizedRedirectUri: "wss://..." }
  
  2. Connect to the authorizedRedirectUri via websocket
     Library: aiohttp.ClientSession.ws_connect()
  
  3. Connection established. Server sends binary protobuf frames.

Subscription:
  Send JSON message:
  {
    "guid": "unique-id",
    "method": "sub",
    "data": {
      "mode": "full",          // "full" | "ltpc" | "option_greeks"
      "instrumentKeys": [
        "NSE_EQ|INE002A01018",  // RELIANCE
        "NSE_EQ|INE009A01021",  // INFY
        ...
      ]
    }
  }

  Max instruments per message: 100
  Max instruments per connection: unspecified (empirically ~1500 works)
  Subscribe in batches of 100 with 100ms delay between batches.
```

### 5.3 Protobuf Decode

```
Upstox sends MarketDataFeed protobuf messages.

Proto schema (reconstructed from Upstox SDK):

message FeedResponse {
  map<string, Feed> feeds = 1;      // key = instrumentKey
}

message Feed {
  FullFeed ff = 1;                   // when mode=full
  LTPC ltpc = 2;                     // when mode=ltpc
  OptionGreeks optionGreeks = 3;
}

message FullFeed {
  MarketFullFeed marketFF = 1;
}

message MarketFullFeed {
  LTPC ltpc = 1;
  MarketOHLC ohlc = 2;              // has list of OHLC by interval
  double lastTradedQty = 3;
  double avgTradedPrice = 4;
  double volumeTraded = 5;
  double totalBuyQty = 6;
  double totalSellQty = 7;
  OHLC ohlcDay = 8;                  // today's OHLC
  MarketDepth depth = 9;             // best 5 bids/asks
  double oi = 10;
  int64 lastTradedTimestamp = 11;
  double upperCircuit = 12;
  double lowerCircuit = 13;
}

Our decode strategy:
  1. Receive binary frame from WS
  2. Decompress (Upstox uses gzip compression on WS frames)
  3. protobuf decode using compiled Python proto module
  4. Extract fields into RawTick dataclass
  5. XADD to tick:raw

Decode latency: ~0.1ms per tick (protobuf is fast)
The bottleneck is network, not decode.
```

### 5.4 Subscription Management

```
INSTRUMENT KEY FORMAT:
  {exchange}_{segment}|{ISIN}
  
  Examples:
    NSE_EQ|INE002A01018       → RELIANCE equity
    NSE_FO|INE002A01018       → RELIANCE futures/options
    NSE_INDEX|Nifty 50         → NIFTY 50 index
    NSE_INDEX|Nifty Bank       → BANK NIFTY index

SUBSCRIPTION LIFECYCLE:

  Pre-market (09:10):
    1. Load symbol master from Redis (infusion:symbols)
    2. Build instrument key list by tier:
       Tier 1: NIFTY 50 EQ + top 100 F&O + indices → mode: "full"
       Tier 2: NIFTY NEXT 50 + MIDCAP 100           → mode: "full" (throttled in normalizer)
       Tier 3: Remaining NIFTY 500                   → mode: "ltpc" (LTP only, saves bandwidth)
    3. Subscribe in batches of 100, 100ms apart
    4. Log: total instruments subscribed, per-tier counts

  During market:
    Dynamic re-subscription NOT needed. Our universe is fixed for the day.
    If a stock enters/exits a tier mid-day, we handle it next day.

  Post-market (15:35):
    1. Unsubscribe all instruments
    2. Close WS connection gracefully
    3. Service enters idle mode (scheduler will re-trigger at 09:10 next day)
```

### 5.5 Connection Health Monitoring

```
Health signals:
  1. Last tick received timestamp
     → if now - last_tick > 5s during market hours → UNHEALTHY
     → NIFTY 50 stocks always have ticks. >5s silence = problem.
  
  2. Tick rate
     → Track ticks/sec over 10s sliding window
     → Expected: 5,000-30,000 ticks/sec
     → If < 1,000 during active market hours → DEGRADED
  
  3. WebSocket ping/pong
     → Upstox sends WS ping frames
     → aiohttp auto-responds with pong
     → If no ping received in 45s → connection may be dead

Health check response:
  {
    status: "HEALTHY" | "DEGRADED" | "UNHEALTHY",
    ws_connected: true,
    last_tick_age_ms: 234,
    ticks_per_sec: 18432,
    uptime_sec: 21600,
    reconnect_count: 0
  }
```

---

## 6. Broker Adapter Abstraction

### 6.1 Why Abstract

Different brokers send tick data in completely different formats:
- Upstox: protobuf over compressed WebSocket
- Kite: custom binary packet format over WebSocket
- Future brokers: unknown format

The normalizer shouldn't care which broker produced the tick. The adapter translates broker-specific wire format into our internal `RawTick`.

### 6.2 Adapter Interface

```
BrokerAdapter (Abstract Base)
├── Properties
│   ├── name: str                          # "upstox" | "kite"
│   ├── state: ConnectionState             # INIT | AUTH | CONNECTING | STREAMING | etc.
│   └── stats: AdapterStats               # ticks_received, reconnects, errors
│
├── Lifecycle
│   ├── authenticate() → access_token      # Broker-specific OAuth/API key flow
│   ├── connect() → None                   # Establish WS connection
│   ├── subscribe(instruments) → None      # Subscribe to instrument list
│   ├── disconnect() → None                # Graceful close
│   └── health() → HealthStatus            # Current connection health
│
├── Data
│   ├── on_tick: Callable[[RawTick], Awaitable[None]]  
│   │   # Callback invoked for each decoded tick
│   └── decode(frame: bytes) → list[RawTick]
│       # Broker-specific binary → RawTick translation
│
└── Error Handling
    ├── on_disconnect: Callable → trigger reconnect
    ├── on_error: Callable → log + metrics
    └── on_auth_expired: Callable → re-authenticate
```

### 6.3 RawTick — The Adapter Output Contract

```
RawTick (what every adapter produces):
  broker: str              # "upstox" | "kite" (source tag)
  instrument_key: str      # Broker-specific instrument identifier
  exchange: str            # "NSE" | "BSE"
  segment: str             # "EQ" | "FO" | "INDEX"
  ltp: float
  open: float
  high: float
  low: float
  close: float             # Previous day close
  volume: int
  oi: int                  # 0 for non-F&O
  total_buy_qty: int
  total_sell_qty: int
  best_bid: float
  best_ask: float
  best_bid_qty: int
  best_ask_qty: int
  exchange_timestamp_ms: int   # Exchange epoch milliseconds
  received_at_us: int          # Local receipt epoch microseconds (for latency tracking)
```

### 6.4 Adapter Selection at Runtime

```yaml
# .env or config
BROKER_PRIMARY=upstox
BROKER_SECONDARY=             # empty = no secondary

# Adapter factory (in ingestion/main.py)
# Reads BROKER_PRIMARY, instantiates the correct adapter class.
# Only one adapter runs at a time (single-user system).
# Secondary is a future option for failover, not load balancing.
```

**Design decision: no multi-broker aggregation.** We don't merge ticks from Upstox and Kite simultaneously. That would create ordering and deduplication complexity for zero benefit in a single-user system. One broker is primary. Switch brokers by changing a config value and restarting.

---

## 7. Tick Normalization Pipeline

### 7.1 Normalizer Responsibilities

```
Input:  tick:raw (broker-specific RawTick, msgpack)
Output: tick:normalized (universal NormalizedTick, msgpack)

Step 1: Deserialize
  msgpack.unpackb(raw_message) → RawTick fields

Step 2: Symbol Resolution
  instrument_key → (symbol, isin, sector_id, is_fno, lot_size)
  
  Lookup: in-memory dict (loaded from Redis infusion:symbols at startup)
  Miss handling: 
    If instrument_key not in map → skip tick, log warning
    This can happen if symbol master is stale (new listing, re-listing)
    Resolution: next symbol master refresh (daily at 06:00) will fix it

Step 3: Tier-Based Throttling
  For Tier 2 symbols (NIFTY NEXT 50, MIDCAP 100):
    Maintain per-symbol last_forwarded_time
    If time since last forward < 500ms → drop tick (use latest values)
    Else → forward tick, update last_forwarded_time
  
  For Tier 3 symbols (remaining NIFTY 500):
    Same logic, but threshold = 2000ms

  Tier 1 symbols: forward every tick (no throttling)
  
  This happens HERE (normalizer) not in ingestion, because:
    - Ingestion should be as thin as possible (minimize crash risk)
    - Throttling logic is domain logic, not broker logic
    - Normalizer already touches every tick, so zero extra overhead

Step 4: Deduplication
  Key: (symbol, exchange_timestamp_ms)
  Check against per-symbol ring buffer of last 20 timestamps
  If duplicate → drop
  
  Why: Broker WS can occasionally deliver duplicate ticks on reconnect
  (re-delivery of messages in flight during disconnect). Rare but real.

Step 5: Build NormalizedTick
  Map RawTick fields to NormalizedTick, adding:
    - symbol (resolved from instrument_key)
    - sector_id (from symbol master)
    - is_fno (from symbol master)
    - tier (1/2/3)
    - normalized_at_us: current epoch microseconds

Step 6: Publish
  XADD infusion:stream:tick:normalized MAXLEN ~ 100000 * {msgpack(NormalizedTick)}
  
  Also: HSET infusion:tick:{symbol} with latest tick fields
  (this is the "hot state" cache — dashboard reads this for current price)
```

### 7.2 Symbol Resolution Performance

```
Symbol master size: ~2000 entries (NIFTY 500 + F&O instruments + indices)
Lookup structure: Python dict (O(1) hash lookup)
Memory: ~2000 entries × 200B = ~400KB
Lookup time: ~0.05µs per lookup

Reload trigger:
  - On startup: load from Redis HASH infusion:symbols
  - On config version change: reload from Redis
  - Frequency: effectively once per day (symbol master updated at 06:00)
```

### 7.3 Normalizer Throughput

```
Per-tick processing cost:
  msgpack decode:     0.5µs
  symbol lookup:      0.05µs
  throttle check:     0.02µs
  dedup check:        0.05µs
  NormalizedTick build: 0.1µs
  msgpack encode:     0.4µs
  ──────────────────────
  Total per tick:     ~1.1µs

At 30,000 raw ticks/sec:
  CPU time: 30,000 × 1.1µs = 33ms per second = 3.3% CPU
  After throttling: ~20,000 normalized ticks/sec (Tier 2/3 dropped)
  
Normalizer is NOT a bottleneck. Single instance is more than sufficient.
```

---

## 8. Historical Data Storage Design

### 8.1 Data Sources for Historical Backfill

```
Source 1: NSE Bhavcopy (primary for daily OHLCV)
  Available: T+0 after 18:00 IST
  Coverage: All traded securities on NSE
  Fields: symbol, series, open, high, low, close, last, prevclose,
          tottrdqty, tottrdval, timestamp, totaltrades, isin
  Format: CSV (gzipped)
  History available: ~2 years from NSE website directly
  Beyond 2 years: third-party providers or manual archive

Source 2: Broker API Historical Candles (for intraday backfill)
  Upstox: GET /v2/historical-candle/{instrumentKey}/{interval}/{to_date}/{from_date}
  Intervals: 1min, 5min, 15min, 30min, day
  Limit: 1000 candles per request
  Rate limit: 1 req/sec
  History: ~1 year for intraday, longer for daily

Source 3: Live capture (for intraday going forward)
  Feature engine persists 1m/5m/15m bars to PostgreSQL post-market
  This is the primary source for intraday data going forward
```

### 8.2 Backfill Strategy

```
Phase A — Daily OHLCV backfill (run once at initial setup):
  1. Download bhavcopy archives from NSE for last 2 years
     (or use pre-collected archive if available)
  2. Parse each CSV with Polars: read_csv() → filter(series == "EQ")
  3. Batch INSERT into ohlcv_daily (1000 rows per INSERT)
  4. Create monthly partitions as needed during insert
  5. Expected volume: ~500 trading days × 2000 symbols = 1M rows
  6. Expected time: ~10 minutes
  7. Script: scripts/backfill_ohlcv.py

Phase B — Intraday backfill (run once, optional):
  1. For each symbol in NIFTY 500:
     GET /v2/historical-candle/{key}/1minute/{today}/{90_days_ago}
  2. Rate-limited: 1 req/sec = 500 symbols × 1 req = ~8 minutes
  3. May need multiple requests per symbol (1000 candle limit)
  4. Parse and INSERT into ohlcv_intraday
  5. Expected volume: 500 × 390 bars/day × 90 days = 17.5M rows
  6. Expected time: ~30 minutes (rate-limited by broker API)

Phase C — Ongoing daily capture (automated):
  Post-market job (15:45):
    Flush intraday bars from Redis sorted sets → PostgreSQL ohlcv_intraday
  EOD job (18:00):
    Download today's bhavcopy → PostgreSQL ohlcv_daily
```

### 8.3 Partition Management

```sql
-- Automated partition creation (run by scheduler at 06:00)

-- Daily partitions for ohlcv_intraday (high-volume)
-- Create 7 days ahead to prevent insert failures
CREATE TABLE IF NOT EXISTS ohlcv_intraday_20260527
  PARTITION OF ohlcv_intraday
  FOR VALUES FROM ('2026-05-27') TO ('2026-05-28');

-- Monthly partitions for ohlcv_daily (lower-volume)
-- Create 2 months ahead
CREATE TABLE IF NOT EXISTS ohlcv_daily_2026_06
  PARTITION OF ohlcv_daily
  FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

-- Retention: drop old partitions
-- ohlcv_intraday: keep 90 days
DROP TABLE IF EXISTS ohlcv_intraday_20260227;  -- today minus 90

-- ohlcv_daily: keep 10 years (rarely drop)
```

### 8.4 Corporate Action Adjustments

```
Problem:
  A stock splits 2:1. Pre-split price was ₹2000, post-split ₹1000.
  If we don't adjust, features like "52-week high" and "200 EMA" break.

Solution:
  1. corporate_actions table tracks splits/bonuses with adjustment_factor
  2. When a corporate action is detected (from NSE scraper):
     a. Calculate adjustment_factor (e.g., 2:1 split → factor = 0.5)
     b. INSERT into corporate_actions
     c. Run adjustment query:
        UPDATE ohlcv_daily
        SET open = open * 0.5, high = high * 0.5, low = low * 0.5,
            close = close * 0.5, prev_close = prev_close * 0.5,
            volume = volume * 2
        WHERE symbol = 'STOCKNAME' AND trade_date < ex_date;
     d. Invalidate Redis feature cache for this symbol
     e. Feature engine re-warms from adjusted PostgreSQL data

  3. Adjustment is idempotent: corporate_actions.id prevents double-apply
  
  4. Timing: Run adjustment in EOD job, after bhavcopy confirms the action
```

---

## 9. Sector Classification Engine

### 9.1 Sector Hierarchy

```
Level 0: MARKET (NIFTY 50 / NIFTY 500)
  └── Level 1: SECTOR (12 NSE sectoral indices)
        └── Level 2: INDUSTRY (finer grouping within sector)

We operate primarily at Level 1 (sector) for:
  - Breadth computation
  - Rotation analysis
  - Money flow aggregation
  - Conviction context scoring

Level 2 (industry) is a future enhancement, not required for v1.
```

### 9.2 Sector Definitions

```yaml
# config/sectors.yaml
# Source of truth for sector classification.
# Updated weekly by scheduler from NSE index constituents.

sectors:
  NIFTY_BANK:
    display_name: "Bank Nifty"
    nse_index: "NIFTY BANK"
    nse_endpoint_key: "NIFTY%20BANK"
    constituents:
      - HDFCBANK
      - ICICIBANK
      - KOTAKBANK
      - SBIN
      - AXISBANK
      - INDUSINDBK
      - BANKBARODA
      - PNB
      - FEDERALBNK
      - BANDHANBNK
      - IDFCFIRSTB
      - AUBANK
    weight_method: free_float_mcap    # or equal_weight

  NIFTY_IT:
    display_name: "Nifty IT"
    nse_index: "NIFTY IT"
    nse_endpoint_key: "NIFTY%20IT"
    constituents:
      - TCS
      - INFY
      - WIPRO
      - HCLTECH
      - TECHM
      - LTIM
      - MPHASIS
      - COFORGE
      - PERSISTENT
      - LTTS
    weight_method: free_float_mcap

  NIFTY_PHARMA:
    display_name: "Nifty Pharma"
    nse_index: "NIFTY PHARMA"
    nse_endpoint_key: "NIFTY%20PHARMA"
    constituents: [...]
    weight_method: free_float_mcap

  NIFTY_AUTO:
    display_name: "Nifty Auto"
    # ...

  NIFTY_FMCG:
    display_name: "Nifty FMCG"
    # ...

  NIFTY_METAL:
    display_name: "Nifty Metal"
    # ...

  NIFTY_REALTY:
    display_name: "Nifty Realty"
    # ...

  NIFTY_ENERGY:
    display_name: "Nifty Energy"
    # ...

  NIFTY_INFRA:
    display_name: "Nifty Infra"
    # ...

  NIFTY_PSU_BANK:
    display_name: "Nifty PSU Bank"
    # ...

  NIFTY_MEDIA:
    display_name: "Nifty Media"
    # ...

  NIFTY_FIN_SERVICE:
    display_name: "Nifty Financial Services"
    # ...

# Stocks not in any sector index:
# Assigned sector_id = "UNCATEGORIZED"
# These are excluded from sector analysis but included in scanner
```

### 9.3 Sector Classification Refresh

```
When: Weekly (Sunday 18:00 via scheduler)
How:
  1. For each sector in config:
     GET /api/equity-stockIndices?index={nse_endpoint_key}
  2. Parse response → extract list of symbols
  3. Compare against current sectors.yaml constituents
  4. If changed:
     a. Update sectors.yaml (atomic file write)
     b. Update Redis: HSET infusion:sectors:{sector_id} constituents {msgpack(list)}
     c. Bump infusion:config:version → triggers reload in sector-intel service
     d. Log diff: "NIFTY_BANK: added IDBI, removed BANDHANBNK"
  5. Update symbols table: SET sector_id for all affected symbols

Edge cases:
  - Stock in multiple sector indices: assign to the most specific one
    (e.g., HDFCBANK is in NIFTY_BANK and NIFTY_FIN_SERVICE → assign NIFTY_BANK)
  - New listing enters an index mid-week: caught at next weekly refresh
  - Not a hot-path operation. Staleness of 7 days is acceptable.
```

### 9.4 Stock-to-Sector Lookup (Runtime)

```
Data structure: Python dict[str, str]
  key: symbol
  value: sector_id

Built from: sectors.yaml at service startup
Memory: ~2000 entries × 50B = ~100KB
Lookup: O(1), ~0.05µs

Loaded by:
  - normalizer (tags each tick with sector_id)
  - feature-engine (for sector-relative features)
  - sector-intel (for aggregation)
  - conviction (for context scoring)

Refresh: on config version bump (checked every 60s)
```

---

## 10. Market Breadth Engine

### 10.1 Purpose

Market breadth measures the **internal health** of a move. An index can rise on 3 heavyweight stocks while 47 others decline — breadth reveals this. This is core to conviction scoring (Global Constraint #4: sector intelligence is core).

### 10.2 Breadth Metrics Computed

```
PER-SECTOR BREADTH (computed every 1 second during market hours)
═══════════════════════════════════════════════════════════════

Metric                    Formula                              Range
────────────────────────  ───────────────────────────────────  ──────
advance_decline_ratio     advancing / (advancing + declining)  0.0-1.0
pct_above_vwap           count(ltp > vwap) / total            0.0-1.0
pct_above_prev_close     count(ltp > prev_close) / total      0.0-1.0
pct_above_ema_20         count(ltp > ema_20) / total          0.0-1.0
up_volume_ratio          sum(vol where chg>0) / sum(all vol)  0.0-1.0
net_new_highs            count(ltp == day_high) - count(ltp == day_low)  -N to +N
breadth_thrust            see section 10.4                    0.0-1.0


MARKET-WIDE BREADTH (aggregated across all NIFTY 500 stocks)
═══════════════════════════════════════════════════════════════

Same metrics as above, but computed on full NIFTY 500 universe.
Additionally:

mcclellan_oscillator     EMA_19(AD) - EMA_39(AD)             signed float
  where AD = advances - declines (daily cumulative)
  → Requires daily history. Pre-computed at market open from PostgreSQL.
  → Updated intraday using realtime A/D data.
```

### 10.3 Computation Model

```
┌──────────────────────────────────────────────────┐
│              Breadth Engine                       │
│         (inside sector-intel service)             │
│                                                   │
│  Input: feature:computed stream                   │
│         (receives FeatureVector with ltp, vwap,   │
│          prev_close, ema_20, volume, change%)     │
│                                                   │
│  State (in-memory per sector):                   │
│    sector_state[sector_id] = {                   │
│      constituents: {                              │
│        "RELIANCE": {ltp, vwap, prev_close, ...}, │
│        "TCS": {ltp, vwap, prev_close, ...},      │
│        ...                                        │
│      },                                          │
│      last_computed: epoch_us,                    │
│    }                                              │
│                                                   │
│  On each feature:computed message:               │
│    1. Update constituent state for that symbol    │
│    2. If time since last_computed >= 1000ms:      │
│       a. Recompute all breadth metrics            │
│       b. Publish to sector:state stream           │
│       c. HSET infusion:sector:{id} with metrics  │
│       d. Reset timer                              │
│                                                   │
│  Why 1-second batching?                          │
│    At 20K ticks/sec, recomputing breadth on      │
│    every tick would mean 20K recomputes/sec.     │
│    Breadth doesn't change meaningfully faster     │
│    than 1/sec. Batching saves 99.995% of CPU.    │
└──────────────────────────────────────────────────┘
```

### 10.4 Breadth Thrust Detection

A breadth thrust is a rare, powerful signal: when breadth rapidly moves from oversold to overbought, it signals the start of a strong rally. This is an institutional-quality signal.

```
BREADTH THRUST DEFINITION:
  advance_decline_ratio moves from < 0.40 to > 0.615
  within 10 trading sessions (or equivalent intraday period).

INTRADAY ADAPTATION:
  We compute a "micro breadth thrust" on 5-minute windows:
  
  1. Track advance_decline_ratio on 5-min snapshots (78 snapshots/day)
  2. If ratio was < 0.35 at any point in last 2 hours
     AND ratio now > 0.65
     → breadth_thrust = true for that sector
  
  3. This fires as a sector-level signal, not stock-level.
     It feeds into conviction scorer as a context boost:
     "Sector is in breadth thrust → all breakout signals in this sector
      get +10 conviction points"

STORAGE:
  sector:state stream message includes:
    breadth_thrust: bool
    breadth_thrust_age_min: int  (minutes since thrust fired, 0 if not active)
```

### 10.5 Breadth for Conviction Context

How breadth integrates with the conviction scorer (detailed in Phase 3):

```
SECTOR BREADTH CONTEXT RULES:
══════════════════════════════

If stock triggers bullish scanner signal:
  
  Breadth > 0.70 (strong sector participation):
    → conviction_boost: +8 points
    → rationale: "Broad sector support"
  
  Breadth 0.50-0.70 (moderate):
    → conviction_boost: 0 points (neutral)
  
  Breadth 0.30-0.50 (weak):
    → conviction_penalty: -5 points
    → rationale: "Weak sector breadth"
  
  Breadth < 0.30 (sector under pressure):
    → conviction_penalty: -12 points
    → rationale: "Moving against sector"
    → If conviction drops below B grade threshold → suppress signal entirely

  Breadth thrust active:
    → conviction_boost: +10 points
    → rationale: "Sector breadth thrust"
    → This overrides the breadth penalty rules above

These weights are configurable in config/conviction_weights.yaml.
```

### 10.6 Resource-Aware Computation (Global Constraint #7)

```
Market hours (09:15 – 15:30):
  Breadth computed every 1 second
  All sectors active
  sector:state stream published continuously

Pre-market (09:00 – 09:15):
  Pre-open session data available
  Compute indicative breadth from pre-open prices
  Reduced frequency: every 5 seconds

Post-market (15:30 – 16:00):
  Final breadth snapshot persisted to sector_daily table
  Stream publishing stops
  Service holds state in memory for API queries

Overnight (16:00 – 09:00):
  Sector-intel service enters IDLE mode
  No computation
  Responds only to health checks
  Memory released (constituent state dicts cleared)
  Re-initialized from Redis at 08:30 by scheduler
```

---

## Phase 2 Boundary

This completes the market data and NSE engine layer. Key decisions made:

| Decision | Rationale |
|---|---|
| `curl_cffi` for NSE TLS fingerprinting | Lightweight, browser-impersonation without headless browser overhead |
| Adaptive rate limiter with 3 states | Reacts to NSE behavior, avoids hardcoded limits that break when NSE changes |
| Throttling in normalizer, not ingestion | Keeps ingestion thin (less crash surface), throttling is domain logic |
| Single broker at a time, no aggregation | Simplicity-first. Multi-broker adds dedup complexity for zero benefit (Constraint #1) |
| 1-second breadth batching | 99.995% CPU savings vs per-tick computation, no meaningful accuracy loss |
| Sector classification from NSE indices | Official source, refreshed weekly, configurable override in YAML |
| Breadth thrust as conviction booster | Institutional-quality signal directly integrated into scoring pipeline |

**Awaiting approval to proceed to Phase 3 — Scanner & Conviction Engine.**
