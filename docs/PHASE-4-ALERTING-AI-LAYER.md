# PHASE 4 — ALERTING & AI LAYER

> Alert delivery, AI explanation, market narrative, and user interaction architecture.
> AI is an enhancement layer — the deterministic engine remains the source of truth.
> All decisions conform to [Global Architecture Constraints](./GLOBAL-ARCHITECTURE-CONSTRAINTS.md).

---

## 1. Alert Routing Engine

### 1.1 Architecture

```
conviction:ranked ──► ALERT ROUTER
                      │
                      ├── 1. Grade Classification
                      │     A+ → TIER 1 (critical)
                      │     A  → TIER 2 (important)
                      │     B+ → TIER 3 (informational)
                      │     B/C → no routing (dashboard passive only)
                      │
                      ├── 2. Throttle Check
                      │     Per-tier rate limits
                      │     If throttled → queue for next window
                      │
                      ├── 3. Channel Dispatch
                      │     ┌─────────────────────────────────────────┐
                      │     │ TIER 1 (A+):                            │
                      │     │   → Telegram: IMMEDIATE (< 2s)         │
                      │     │   → Dashboard WS: IMMEDIATE             │
                      │     │   → AI Enrichment Queue: ASYNC          │
                      │     │                                         │
                      │     │ TIER 2 (A):                              │
                      │     │   → Telegram: BATCHED (60s window)      │
                      │     │   → Dashboard WS: IMMEDIATE             │
                      │     │   → AI Enrichment Queue: ASYNC          │
                      │     │                                         │
                      │     │ TIER 3 (B+):                             │
                      │     │   → Telegram: NONE                      │
                      │     │   → Dashboard WS: IMMEDIATE             │
                      │     │   → AI Enrichment: NONE                 │
                      │     └─────────────────────────────────────────┘
                      │
                      └── 4. Delivery Tracking
                            Log to alert_log table (channel, status, timestamp)
```

### 1.2 Routing Decision Flow

```
On new ScoredSignal:

  1. if grade in (B, C) → STOP. Signal visible in dashboard via
     conviction:ranked stream that dashboard already subscribes to.
     No active routing. No Telegram. No AI.

  2. if grade == B+ → push to ws-gateway only.
     WebSocket message: { type: "signal", tier: 3, data: signal }
     No Telegram. No AI. Minimal overhead.

  3. if grade == A:
     a. Push to ws-gateway IMMEDIATELY (< 1ms)
     b. Add to Telegram batch queue (batched_telegram_queue)
     c. Add to AI enrichment queue (ai_queue) with priority NORMAL
     d. Log: INSERT alert_log (signal_id, 'websocket', 'SENT')

  4. if grade == A+:
     a. Push to ws-gateway IMMEDIATELY
     b. Push to Telegram IMMEDIATELY (bypass batch queue)
     c. Add to AI enrichment queue with priority HIGH
     d. Log: INSERT alert_log (signal_id, 'telegram', 'PENDING')

KEY RULE: Steps (a) happen SYNCHRONOUSLY in the hot path.
Steps (b), (c) are fire-and-forget async tasks. The alert router does NOT
wait for Telegram delivery confirmation or AI generation.
```

### 1.3 Batch Queue for Tier 2

```
Tier 2 (grade A) signals are batched to reduce Telegram noise.

Batch window: 60 seconds
Max batch size: 3 signals

Logic:
  batched_telegram_queue = []
  batch_timer = None

  on_signal(signal):
    batched_telegram_queue.append(signal)
    if batch_timer is None:
      batch_timer = schedule_after(60s, flush_batch)
    if len(batched_telegram_queue) >= 3:
      flush_batch()  # don't wait for timer if 3 signals accumulated

  flush_batch():
    signals = batched_telegram_queue.copy()
    batched_telegram_queue.clear()
    batch_timer = None
    await send_telegram_batch(signals)  # single message with multiple signals

Batch message format:
  📊 3 Signals (Last 60s)
  
  1. RELIANCE — Range Breakout — A 74
  2. TCS — Volume Surge — A 71
  3. HDFCBANK — Pre-Breakout Trigger — A 69
  
  Reply /detail RELIANCE for full breakdown.
```

---

## 2. Telegram Delivery Architecture

### 2.1 Delivery Pipeline

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────┐
│ Alert Router │────►│ Telegram     │────►│ Retry Queue  │────►│ Telegram │
│              │     │ Send Queue   │     │ (on failure) │     │ API      │
└──────────────┘     └──────┬───────┘     └──────────────┘     └──────────┘
                            │
                     ┌──────▼───────┐
                     │ Rate Limiter │
                     │ (token bucket)│
                     │ 25 msg/sec   │
                     │ burst: 5     │
                     └──────────────┘
```

### 2.2 Send Queue Implementation

```
Queue: asyncio.Queue (in-process, bounded)
  Max size: 100 messages
  If full: drop lowest-priority message, log warning

  Message schema:
    TelegramTask:
      signal_id: UUID
      priority: HIGH | NORMAL | LOW
      content: str (pre-formatted markdown)
      chat_id: str
      created_at: epoch_ms
      attempt: int (default 0)
      max_attempts: int (default 3)

Worker: single asyncio task consuming from queue
  Processes messages in priority order (HIGH first)

  async def telegram_worker():
    while True:
      task = await queue.get()

      # Rate limit: token bucket (25 msg/sec, burst 5)
      await rate_limiter.acquire()

      try:
        result = await bot.send_message(
          chat_id=task.chat_id,
          text=task.content,
          parse_mode="MarkdownV2",
          disable_web_page_preview=True,
          read_timeout=10,
          write_timeout=10,
        )
        await log_delivery(task.signal_id, 'telegram', 'SENT')

      except RetryAfterError as e:
        await asyncio.sleep(e.retry_after)
        await queue.put(task)  # re-enqueue
      except TelegramError as e:
        if task.attempt < task.max_attempts:
          task.attempt += 1
          await asyncio.sleep(2 ** task.attempt)  # 2s, 4s, 8s
          await queue.put(task)
        else:
          await log_delivery(task.signal_id, 'telegram', 'FAILED')
```

### 2.3 Markdown Escaping

```
Telegram MarkdownV2 requires escaping: _ * [ ] ( ) ~ ` > # + - = | { } . !

Strategy: build messages with a safe formatter, not raw f-strings.

def tg_escape(text: str) -> str:
    special = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{c}' if c in special else c for c in str(text))

def tg_bold(text: str) -> str:
    return f"*{tg_escape(text)}*"

# Use builder pattern for all messages.
# Never construct raw MarkdownV2 strings inline.
```

### 2.4 Deduplication

```
Before enqueueing any Telegram message:

dedup_key = f"{signal_id}:{chat_id}"
if await redis.exists(f"infusion:telegram:sent:{dedup_key}"):
    return  # already sent or attempted

await redis.set(f"infusion:telegram:sent:{dedup_key}", "1", ex=3600)
await queue.put(task)

Prevents duplicate sends on:
  - Consumer restart (re-processes conviction:ranked messages)
  - Retry logic re-enqueuing an already-sent message
```

### 2.5 Quiet Hours

```yaml
# config/alerts.yaml
quiet_hours:
  enabled: true
  start: "22:00"    # IST
  end: "08:00"      # IST
  behavior: "queue"  # "queue" | "drop"

During quiet hours:
  TIER 1 (A+): STILL delivered immediately (critical signal override)
  TIER 2/3: queued or dropped per config
  Daily recap: queued for 08:00 delivery
```

### 2.6 Telegram Outage Resilience

```
If Telegram API unreachable for > 5 minutes:

  1. Stop send attempts (avoid burning retry budget)
  2. Buffer messages in Redis list: infusion:telegram:outage_buffer
     Max buffer: 50 messages (oldest dropped if exceeded)
  3. Health monitor: ping Telegram API every 60s
  4. On recovery:
     Drain buffer, delivering newest-first
     Skip signals older than 30 minutes (no longer actionable)
  5. Log outage duration and dropped message count

Scanner, conviction, and dashboard operate normally during Telegram outage.
```

---

## 3. Dashboard Realtime Delivery

### 3.1 WebSocket Push Model

```
ws-gateway subscribes to Redis Streams and fans out to connected browsers.

Stream                    → Dashboard Update          → Push Timing
────────────────────────  ──────────────────────────  ─────────────
tick:normalized           → Price table cells          → 100ms batch
feature:computed          → Feature columns (RSI etc)  → 100ms batch
sector:state              → Sector heatmap / breadth   → 1s batch
conviction:ranked         → Signal cards + ranking     → IMMEDIATE
ai enrichment updates     → Explanation panel           → IMMEDIATE

IMMEDIATE = pushed within 1 RTT of Redis delivery (~5ms)
100ms batch = accumulated into single WS frame per 100ms window
1s batch = sector data is slow-moving, no need for faster updates
```

### 3.2 Client Subscription Model

```
Browser subscribes to channels on connect:

Client → Server: { type: "subscribe", channels: ["ticks", "signals", "sectors"] }

Channels:
  "ticks":    tick:normalized (all symbols, delta-compressed, 100ms batch)
  "signals":  conviction:ranked (immediate push)
  "sectors":  sector:state (1s batch)
  "health":   system health (5s interval)

Per-symbol subscription (when chart open):
  Client → Server: { type: "focus_symbol", symbol: "RELIANCE" }
  Server pushes full-tick for RELIANCE only (unbatched, every tick)
  Unsubscribe on chart close or symbol change.
```

### 3.3 Bandwidth Estimation

```
Per connected dashboard:
  Ticks (100ms batch, delta-compressed):   ~20 KB/sec
  Signals (sparse):                        ~0.5 KB/sec
  Sectors (1s interval):                   ~2 KB/sec
  Health (5s interval):                    ~0.1 KB/sec
  ────────────────────────────────────────
  Total: ~23 KB/sec ≈ 83 MB/hour

Single-user system: trivially within bandwidth limits.
```

---

## 4. Notification Prioritization

### 4.1 Priority Tiers

```
TIER 1 — CRITICAL (A+, conviction ≥ 82)
  Telegram: immediate
  Dashboard: highlighted card + pulsing border + sound notification
  AI enrichment: HIGH priority queue
  Expected: 0-3 per day

TIER 2 — IMPORTANT (A, conviction 68-81)
  Telegram: 60s batch window
  Dashboard: visible signal card
  AI enrichment: NORMAL priority queue
  Expected: 2-8 per day

TIER 3 — INFORMATIONAL (B+, conviction 55-67)
  Telegram: none
  Dashboard: listed in signal feed (compact row)
  AI enrichment: none
  Expected: 3-10 per day

TIER 4 — PASSIVE (B/C, conviction < 55)
  Telegram: none
  Dashboard: hidden ("all signals" expandable section)
  AI enrichment: none
```

### 4.2 Escalation Logic

```
Upgrade tier (never downgrade) based on confluence:

Rule 1: Multi-strategy confluence
  If 3+ confirming strategies → upgrade one tier
  A with 3 confirmations → A+ (Tier 1)

Rule 2: Breadth thrust
  If signal's sector has active breadth thrust → upgrade one tier

Rule 3: Sector + market alignment
  If sector quadrant == LEADING AND market regime == TRENDING_UP → upgrade one tier

Escalation runs AFTER scoring, BEFORE routing.
Modifies effective_tier, not underlying score.
```

---

## 5. AI Explanation Layer

### 5.1 Core Principle

```
AI DOES NOT generate the explanation.
AI FORMATS the explanation.

The deterministic engine produces complete, accurate structured data:
  - ConditionResult list
  - ScoringFactor list
  - PenaltyRecord list
  - SuppressionRecord list
  - MarketContext + SectorContext snapshots

This data IS the explanation. AI transforms it into scannable prose.
AI adds NO new information.
```

### 5.2 Two-Track Explanation System

```
TRACK 1: DETERMINISTIC (always available, zero extra latency)
  Built by conviction engine at scoring time.
  No AI. Pure string assembly from structured data.

  Used for:
    - Immediate Telegram alerts (all tiers)
    - Dashboard signal cards
    - API responses (immediate field)

  Format: structured key-value + factor breakdown table
  Latency: 0ms additional

TRACK 2: AI-ENRICHED (async, available 2-10s later)
  Generated by AI worker from the SAME structured data.
  Adds: natural language narrative, contextual significance.

  Used for:
    - Expanded explanation panel (dashboard, on click)
    - /detail Telegram command response
    - Daily recap narratives

  Latency: 2-10s (non-blocking)
  Fallback: if AI unavailable → Track 1 is shown. Nothing breaks.
```

### 5.3 AI Enrichment Queue

```
┌────────────┐     ┌───────────────────┐     ┌─────────────┐
│Alert Router │────►│ AI Task Queue     │────►│ AI Worker   │
│             │     │ (asyncio.Queue)   │     │ (single)    │
└─────────────┘     │ Max size: 20      │     │             │
                    │ Priority ordered  │     │ Processes   │
                    │ Full → drop LOW   │     │ one task    │
                    └───────────────────┘     │ at a time   │
                                              │             │
                                              │ Writes to   │
                                              │ Redis cache │
                                              └─────────────┘

Task types:
  SIGNAL_EXPLANATION   priority HIGH (A+) or NORMAL (A)
  SECTOR_COMMENTARY    priority LOW (triggered hourly)
  MARKET_NARRATIVE     priority LOW (triggered on regime change)
  DAILY_RECAP          priority NORMAL (triggered at 16:00)
  USER_QUERY           priority HIGH (triggered by /explain)

Single worker is deliberate:
  - Prevents concurrent API stampede
  - Natural rate limiting
  - Predictable cost
  - Queue backs up → LOW tasks dropped first
```

---

## 6. LLM Orchestration Strategy

### 6.1 Model Selection

```
PRIMARY: gemini-2.0-flash (fast, cheap, structured output)
  Response time: < 2s
  Cost: ~$0.10/1M tokens
  Used for: all routine AI tasks

FALLBACK: gemini-1.5-flash
  Used when: primary rate-limited or unavailable

NO expensive models (GPT-4, Claude Opus, Gemini Pro) for routine ops.
  10-50x more expensive, 3-10s slower.
  Only considered for: future weekly deep analysis (if ever needed).

Selection logic:
  try primary → on failure → try fallback → on failure → skip AI
```

### 6.2 API Client Configuration

```
HTTP client: aiohttp with connection pooling

  timeout: 15s (hard cutoff — non-critical)
  max_retries: 1 (not 3 — AI is enhancement, not core)
  retry_delay: 2s
  connection_pool_size: 2
```

### 6.3 Structured Output Contract

All LLM calls enforce JSON schema output. LLM fills predefined fields — cannot invent structure.

```json
// Signal explanation schema
{
  "narrative": "string, max 200 words",
  "key_insight": "string, max 50 words",
  "risk_note": "string, max 50 words",
  "historical_parallel": "string or null"
}

// Market narrative schema
{
  "summary": "string, max 150 words",
  "sector_highlight": "string, max 50 words",
  "outlook_bias": "bullish | bearish | neutral"
}

Schema enforcement guarantees:
  - No invented price targets
  - No fabricated indicators
  - No trading recommendations
  - Always parseable JSON
```

---

## 7. AI Summarization Pipeline

### 7.1 Three-Stage Pipeline

Every AI generation follows: **Data Assembly → Prompt → Output**

```
STAGE 1: DETERMINISTIC DATA ASSEMBLY (no AI)
  Gather all structured data relevant to the task.
  Format into compact JSON context block.
  This is the LLM's ONLY source of truth.

STAGE 2: PROMPT CONSTRUCTION (template + grounding)
  Inject context into predefined prompt template.
  Add task-specific instructions + output schema.

STAGE 3: LLM GENERATION
  Send to LLM API.
  Receive structured JSON.
  Validate against schema.
  Cache result in Redis.
```

### 7.2 Example — Signal Explanation

```
Stage 1 (data assembly — deterministic, ~0.1ms):
  signal_data = {
    "symbol": "RELIANCE",
    "strategy": "range_breakout",
    "direction": "BULLISH",
    "conviction": 84, "grade": "A+",
    "price": 2847.50,
    "conditions": [
      {"name": "atr_compression", "value": 18.2, "threshold": 20, "passed": true},
      {"name": "bb_squeeze", "value": 0.62, "threshold": 0.75, "passed": true},
      ...
    ],
    "factors": {
      "technical": {"score": 78, "detail": "EMA stack aligned, RSI 61"},
      "volume": {"score": 85, "detail": "3.2x rel vol, delivery 58%"},
      "setup": {"score": 90, "detail": "8-day squeeze, 3 resistance tests"},
      "sector": {"score": 72, "detail": "NIFTY50 breadth 0.72, LEADING"},
      "regime": {"score": 80, "detail": "TRENDING_UP"},
      "options": {"score": 65, "detail": "PCR 0.85, long buildup"}
    },
    "penalties": [{"name": "lunch_lull", "multiplier": 0.90}],
    "sector": {"name": "NIFTY 50", "breadth": 0.72, "quadrant": "LEADING"},
    "market": {"regime": "TRENDING_UP", "nifty_return": "+0.8%"}
  }

Stage 2 (prompt construction):
  "You are a concise market analyst. Given the structured signal data below,
   write a 2-3 sentence explanation of why this signal fired and why it matters.
   Use ONLY the data provided. Do not invent prices, targets, or recommendations.

   SIGNAL DATA:
   {json.dumps(signal_data)}

   Respond in JSON: {narrative, key_insight, risk_note}"

Stage 3 (LLM output, ~2s):
  {
    "narrative": "RELIANCE is breaking out of an 8-day range compression
      with 3.2x volume surge. Bollinger Bands had squeezed to 62% of normal
      width, indicating a coiled spring pattern. Supported by broad Nifty 50
      participation at 72% breadth.",
    "key_insight": "Multi-factor confluence with smart money accumulation
      evidence makes this a high-quality institutional setup.",
    "risk_note": "Lunch-hour timing reduces reliability; watch for volume
      follow-through in the afternoon session."
  }
```

### 7.3 Grounding Rules

```
STRICT GROUNDING POLICY:

1. Every prompt includes: "Use ONLY the data provided below. Do not reference
   external knowledge, news, fundamentals, or any information not in the context."

2. LLM is NEVER given:
   - Company fundamentals (earnings, PE ratio)
   - News headlines
   - Analyst targets
   - Historical data beyond what's in the signal snapshot

3. Post-generation validation:
   - Contains price targets (₹X target)? → strip
   - Contains "buy" / "sell" / "recommend"? → strip
   - References data not in context? → flag
   Validation is simple regex, not another LLM call.

4. Length enforcement:
   narrative > 200 words → truncate at sentence boundary
   key_insight > 50 words → truncate
```

### 7.4 Narrative Cache

```
All AI outputs cached in Redis:

Key                                              TTL
──────────────────────────────────────────────  ──────
infusion:ai:signal:{signal_id}                  3600s  (immutable per signal)
infusion:ai:narrative:{trigger}:{ts_5min}       300s   (market state changes)
infusion:ai:sector:{sector_id}:{ts_5min}        300s
infusion:ai:recap:{date}                        86400s (one per day)
infusion:ai:watchlist:{ts_30min}                1800s

Cache hit rate target: > 60%
  Signal explanations: 100% after first generation (signal is immutable)
  Same-signal /explain queries: 100% cache hit
  Narratives: ~50% (regenerated on events)
```

---

## 8. Market Narrative Generation

### 8.1 Trigger Conditions

Narratives are generated on **events**, not timers:

```
Trigger 1: Market Regime Change
  RANGE_BOUND → TRENDING_UP
  Frequency: 0-3/day

Trigger 2: Sector Quadrant Change
  LAGGING → IMPROVING
  Frequency: 0-5/day

Trigger 3: Breadth Thrust
  Breadth thrust flag → true for any sector
  Frequency: 0-1/day (rare)

Trigger 4: Scheduled Checkpoints
  11:00, 13:00, 14:30 IST (3 intraday snapshots)
```

### 8.2 Narrative Data Assembly

```
MarketNarrativeContext:
  market:
    nifty_return: "+0.82%"
    nifty_level: 23456
    breadth: 0.72
    advance: 36, decline: 14
    regime: "TRENDING_UP"
    regime_changed: true
    vix: 14.2, vix_change: "-3.2%"

  sectors (sorted by return):
    - {name: "Auto", return: "+2.1%", breadth: 0.80, quadrant: "LEADING"}
    - {name: "Bank", return: "+1.4%", breadth: 0.75, quadrant: "LEADING"}
    - {name: "IT", return: "-0.3%", breadth: 0.35, quadrant: "LAGGING"}

  signals_today:
    total: 8, tier_1: 1, top_signal: "RELIANCE A+ 84"

  institutional:
    fii: "+₹1,240 Cr", dii: "+₹890 Cr"

Token cost: ~500 input + ~200 output ≈ $0.00007
```

---

## 9. Daily Recap Generation

### 9.1 EOD Recap (triggered 16:00 IST)

```
DETERMINISTIC ASSEMBLY:

  eod_data = {
    "date": "2026-05-27",
    "market": {
      "nifty_close": 23456, "change_pct": "+0.82%",
      "breadth_close": 0.68, "advance": 312, "decline": 188,
      "regime": "TRENDING_UP", "vix_close": 14.2
    },
    "sector_leaders": [
      {"name": "Auto", "return": "+2.1%", "top_stock": "M&M +4.2%"},
      {"name": "Bank", "return": "+1.4%", "top_stock": "HDFCBANK +2.1%"}
    ],
    "sector_laggards": [
      {"name": "IT", "return": "-0.3%", "bottom_stock": "WIPRO -1.8%"}
    ],
    "signals": {
      "total_fired": 12, "delivered": 4,
      "best": {"symbol": "RELIANCE", "grade": "A+", "return_since": "+1.2%"}
    },
    "watchlist_coiled": [
      {"symbol": "INFY", "days_compressed": 7},
      {"symbol": "TATAMOTORS", "days_compressed": 4}
    ],
    "precision_5d": {"signals": 4, "wins": 3, "losses": 0, "flat": 1, "rate": "75%"}
  }

AI FORMATTING:
  Prompt: "Write a concise end-of-day market recap (max 250 words).
           Structure: market → sectors → signals → watchlist.
           Tone: professional, neutral. No recommendations."

  Output cached: infusion:ai:recap:{date}
  Delivered via Telegram at 16:15 IST.
```

### 9.2 EOD Telegram Format

```
📋 *EOD Recap — 27 May 2026*

*Market*: NIFTY 23,456 \(+0\.82%\) │ Breadth 68% │ VIX 14\.2
Regime: Trending Up │ FII \+₹1,240 Cr │ DII \+₹890 Cr

*Leaders*: Auto \(+2\.1%\), Bank \(+1\.4%\)
*Laggards*: IT \(−0\.3%\)

*Signals*: 12 fired │ 4 delivered │ 1 A\+
Best: RELIANCE A\+ 84 → \+1\.2% since signal

*5d Precision*: 3W / 0L / 1F \(75%\)

*Watchlist*:
• INFY — 7d compressed, approaching resistance
• TATAMOTORS — 4d accumulating

_AI: Broad rally led by auto and banking\. RELIANCE breakout 
showing follow\-through\. Watch IT for sector rotation\._
```

### 9.3 Weekly Recap (Sunday 18:00)

Same pattern, aggregated over 5 trading days. Includes:
- Week's best/worst signals with outcomes
- Strategy precision breakdown
- Sector rotation changes
- Conviction grade distribution

Token budget: ~800 input + ~300 output. Once per week. Negligible cost.

---

## 10. Watchlist Intelligence Layer

### 10.1 Watchlist Source

```
Watchlist = all symbols NOT in NEUTRAL state in pre-breakout state machine.

Automatically maintained. No user-managed watchlists in v1.
The system's intelligence IS the watchlist.
```

### 10.2 Watchlist Summary Generation

```
Trigger: 09:00 IST (pre-market) and 14:00 IST (afternoon)

Assembly:
  watchlist_symbols = [s for s in pre_breakout_state if s.state != NEUTRAL]
  Sort by: state priority (COILED > ACCUMULATING > COMPRESSING)
           then smart_money_score DESC.

Deterministic output (no AI needed):
  🎯 Watchlist — 14:00 IST

  COILED (ready to trigger):
    INFY — 7d compressed, ₹1,523 (1.2% from resistance), SM: 72
    BAJFINANCE — 5d compressed, ₹6,842 (0.8% from resistance), SM: 68

  ACCUMULATING:
    TATAMOTORS — 4d, delivery rising, OBV divergence

  COMPRESSING (early):
    MARUTI — ATR 15th pctl, BB squeeze 0.65

AI enrichment (optional, if ≤ 5 symbols):
  1-sentence per-symbol context note.
  Token budget: ~400 input + ~150 output.
```

---

## 11. AI-Assisted Sector Commentary

### 11.1 Trigger and Frequency

```
Generated:
  1. On sector quadrant change (event-driven, 0-5/day)
  2. At 3 intraday checkpoints (11:00, 13:00, 14:30)
  3. In EOD recap (16:00)

Per generation: ~300 input + ~100 output tokens
Max daily: ~9 generations
Max daily cost: 9 × $0.00004 = $0.00036. Negligible.
```

### 11.2 Commentary Structure

```
Deterministic (always available):
  "NIFTY BANK: +1.4% | Breadth 75% | LEADING quadrant
   Top: HDFCBANK +2.1% | 83% above VWAP | FII buying"

AI-enriched (optional):
  "Banking showing broad strength with 9 of 12 above VWAP.
   Heavyweights leading. PSU banks slightly lagging private —
   watch for rotation if breadth narrows."
```

---

## 12. User Interaction / Query System

### 12.1 Telegram Commands

```
DETERMINISTIC (instant, no AI):
════════════════════════════════

/top          → Top 5 active signals (Redis ZSET, < 100ms)
/sector [name]→ Sector snapshot (Redis HASH, < 100ms)
/watchlist    → Pre-breakout watchlist (Redis cache, < 200ms)
/status       → System health (Redis keys, < 100ms)
/signals [strat] → Today's signals (PostgreSQL, < 500ms)
/precision [N]→ Win/loss stats last N days (PostgreSQL, < 500ms)
/mute [min]   → Suppress alerts N minutes (Redis TTL key)
/unmute       → Resume alerts (DEL Redis key)

AI-ENHANCED (async, 2-10s):
════════════════════════════

/explain [SYM] → AI explanation of latest signal for symbol
  Flow: fetch signal → check AI cache → generate if miss → respond
  Fallback: deterministic explanation if AI unavailable

/why [SYM]     → Why symbol was suppressed
  Flow: fetch suppression records → format (optionally AI)

/market        → Current market narrative
  Flow: check narrative cache → generate if stale (> 5min) → respond

/recap         → Today's recap (cached) or interim recap
```

### 12.2 Dashboard Query API

```
Same queries exposed as REST endpoints:

GET /api/signals                    → /top equivalent
GET /api/signals/{id}/explain       → /explain equivalent (returns immediately
                                      with deterministic data + ai_pending: true,
                                      then pushes AI enrichment via WS when ready)
GET /api/sectors                    → all sector snapshots
GET /api/sectors/{id}               → single sector detail
GET /api/watchlist                  → pre-breakout watchlist
GET /api/health                     → system health
GET /api/market/narrative           → current market narrative
GET /api/recap                      → today's recap
GET /api/precision?days=30          → rolling precision stats

Pattern: REST returns deterministic immediately.
AI enrichment pushed via WebSocket when generated.
Zero perceived latency.
```

---

## 13. Cost-Control Architecture

### 13.1 Token Budget

```
DAILY TOKEN BUDGET:

  Signal explanations:     5 × 700  =  3,500 tokens
  Market narratives:       5 × 700  =  3,500 tokens
  Sector commentary:       9 × 400  =  3,600 tokens
  Watchlist summaries:     2 × 550  =  1,100 tokens
  EOD recap:               1 × 1100 =  1,100 tokens
  User queries:           10 × 700  =  7,000 tokens
  ──────────────────────────────────────────────────
  TOTAL:                              ~19,800 tokens/day

  At gemini-2.0-flash ($0.10/1M tokens):
    Daily:   $0.002
    Monthly: $0.04
    Annual:  $0.50

  Even at 10x estimate: $5/year.
  Cost is a non-issue. Budget enforcement is a SAFETY NET.
```

### 13.2 Budget Enforcement

```
Daily hard limit: 100,000 tokens (5x expected)
  Tracked: INCRBY infusion:ai:tokens:daily:{date} {count}, TTL 86400s
  If exceeded: disable AI, continue on deterministic. Log warning.

Per-request output limit: 2,000 tokens max
  Via: max_tokens API parameter

Per-minute rate: 10 requests max
  Token bucket in AI worker
```

### 13.3 Selective AI Invocation

```
AI IS invoked for:
  ✅ A+ and A signal explanations
  ✅ Market regime change narratives
  ✅ Sector quadrant change commentary
  ✅ Breadth thrust narratives
  ✅ EOD/weekly recaps
  ✅ Explicit user queries (/explain, /market)

AI is NOT invoked for:
  ❌ B+/B/C signals
  ❌ Tick or feature updates
  ❌ Routine sector metric refreshes
  ❌ Health checks
  ❌ Price updates
  ❌ Tier 2 Telegram batches (template, not AI)
```

---

## 14. AI Failure / Degraded-Mode Handling

### 14.1 Failure Modes

```
MODE 1: API Timeout (> 15s)
  → Log warning, mark task SKIPPED, serve deterministic. Don't retry.

MODE 2: Rate Limit (429)
  → Pause worker for Retry-After seconds, drop LOW priority tasks, resume HIGH only.

MODE 3: Auth Failure (401/403)
  → Disable AI worker entirely
  → Telegram alert: "AI running in deterministic mode"
  → Retry key validation every 30 min
  → System continues fully functional without AI

MODE 4: Malformed Response
  → Discard, serve deterministic
  → Increment error counter
  → If > 10 errors/hour → disable AI worker, alert

MODE 5: Suspected Hallucination
  → Post-generation validation catches: price targets, recommendations, external refs
  → Strip offending content
  → If > 50% stripped → discard entirely → deterministic fallback
```

### 14.2 Dashboard Health Indicator

```
  AI: ● Online     (green)
  AI: ● Degraded   (yellow — partial failures)
  AI: ● Offline    (gray — deterministic only)
```

### 14.3 Degraded Feature Mapping

```
Feature                  Normal              Degraded
──────────────────────  ──────────────────  ────────────────────
Signal explanation      AI narrative + data  Structured data only
Market narrative        AI paragraph         Deterministic snapshot
Sector commentary       AI summary           Key-value stats
EOD recap               AI-formatted         Data tables
/explain command        AI narrative          Signal data dump
Dashboard explanation   AI panel              Factor breakdown table

NOTHING BREAKS. Every feature works without AI.
AI makes it pretty. Deterministic makes it functional.
```

---

## 15. Human Override & Moderation Layer

### 15.1 Manual Signal Control (Telegram)

```
/dismiss [SIGNAL_ID]
  Remove signal from active list.
  ZREM infusion:signal:active {id}

/suppress [SYMBOL] [hours]
  Block all signals for symbol for N hours.
  SET infusion:override:suppress:{symbol} 1 EX {hours*3600}
  Checked in suppression pipeline Gate 1.

/force_quiet [minutes]
  Mute ALL Telegram alerts for N minutes.
  SET infusion:telegram:muted 1 EX {min*60}

/strategy_off [name]
  Disable a scanner strategy at runtime.
  Updates Redis config → triggers scanner reload.

/strategy_on [name]
  Re-enable a scanner strategy.
```

### 15.2 AI Output Moderation

```
Automatic content filter on ALL AI outputs:

1. Schema validation: output must match JSON schema. Missing fields → deterministic fill.

2. Content filter (regex):
   Strip: price targets (₹[0-9,.]+\s*target)
   Strip: recommendations (buy|sell|hold|accumulate|book profit)
   Strip: certainty claims (will|guaranteed|definitely|must)
   Replace with neutral language or omit.

3. Length enforcement:
   narrative > 200 words → truncate at sentence boundary.

4. Manual review flag (non-blocking):
   If filter stripped content → log for weekly prompt tuning review.
   Outputs are NEVER held for human review. That would break latency.
```

### 15.3 Override Audit Trail

```sql
CREATE TABLE override_log (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    action      TEXT NOT NULL,        -- 'dismiss', 'suppress', 'mute', 'strategy_toggle'
    target      TEXT,                 -- signal_id, symbol, or strategy name
    value       TEXT,                 -- 'off', '120 min', etc.
    created_at  TIMESTAMPTZ DEFAULT now(),
    expires_at  TIMESTAMPTZ
);

Purpose: audit trail for manual interventions.
Useful for post-analysis: "I suppressed RELIANCE all day — was that a mistake?"
```

---

## System Resource Summary

```
ALERTER SERVICE (includes AI worker):
  Memory: ~50MB
  CPU: < 2% of 1 core
  Network: Telegram API + LLM API (sparse)
  AI cost: ~$0.04/month

WS-GATEWAY:
  Memory: ~50MB
  CPU: ~3% (Redis consumer + WS fan-out)
  Network: ~23 KB/sec per dashboard

TELEGRAM BOT:
  Memory: ~30MB
  CPU: < 1%

Total Phase 4 footprint:
  Memory: ~130MB
  CPU: < 6% of 1 core
  AI cost: < $1/year
```

---

## Phase 4 Boundary

Key design decisions:

| Decision | Rationale |
|---|---|
| Two-track explanation (deterministic + AI async) | Immediate response always available. AI never blocks signal flow. |
| Single AI worker, bounded queue, priority ordering | Predictable cost, natural rate limiting, HIGH tasks first. |
| gemini-2.0-flash as primary model | Fast (< 2s), cheap ($0.002/day). No expensive models for template formatting. |
| Structured JSON output on all LLM calls | Prevents hallucination. LLM fills schema, can't invent. |
| Strict grounding: LLM sees ONLY assembled data | Zero external knowledge. Can't hallucinate fundamentals. |
| Auto content filter on all AI output | Strips price targets, buy/sell language, certainty claims. |
| Every AI feature has deterministic fallback | AI offline = fully functional. Uglier text, same intelligence. |
| Tier 1 immediate, Tier 2 batched (60s) | A+ gets instant attention. A signals batched to reduce noise. |
| Token budget: 100K/day hard limit | Safety net. Actual usage ~20K/day. Annual cost < $1. |
| Human override via Telegram commands | /dismiss, /suppress, /mute, /strategy_off. Immediate control. |

**Awaiting approval to proceed to Phase 5 — Dashboard & UI.**
