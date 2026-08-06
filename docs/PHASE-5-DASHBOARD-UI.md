# PHASE 5 — DASHBOARD & UI

> Frontend architecture for the Infusion AI Screener dashboard.
> Design philosophy: institutional information density without cognitive chaos.
> All decisions conform to [Global Architecture Constraints](./GLOBAL-ARCHITECTURE-CONSTRAINTS.md).

---

## 1. Dashboard Layout System

### 1.1 Layout Philosophy

The dashboard is a **single-page command center**, not a multi-page app. Every piece of information the user needs during market hours is visible or one click away. No page navigation during active trading.

### 1.2 Desktop Layout (≥ 1440px — primary target)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ HEADER BAR                                                            56px │
│ ┌──────────────┐ ┌─────────────────────────────────────┐ ┌────────────────┐│
│ │ INFUSION AI  │ │ NIFTY 23,456 +0.82% │ VIX 14.2 -3% │ │⚙ │ 🔔 │ AI:●  ││
│ │ SCREENER     │ │ Breadth 72% │ TRENDING UP           │ │Settings│Alerts ││
│ └──────────────┘ └─────────────────────────────────────┘ └────────────────┘│
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  LEFT COLUMN (400px fixed)              RIGHT AREA (remaining width)        │
│  ┌────────────────────────┐             ┌──────────────────────────────────┐│
│  │ SIGNAL PANEL           │             │ SCREENER TABLE                   ││
│  │                        │             │                                  ││
│  │ ┌────────────────────┐ │             │ Symbol│LTP   │%Chg│RelV│RSI│Scr ││
│  │ │ RELIANCE    A+ 84  │ │             │ ──────┼──────┼────┼────┼───┼─── ││
│  │ │ Range Breakout     │ │             │ RELI  │2847  │+2.1│4.2x│ 62│ 84 ││
│  │ │ 3.2x vol │ 12:14  │ │             │ INFY  │1523  │+1.8│2.8x│ 58│ 74 ││
│  │ └────────────────────┘ │             │ HDFC  │1672  │+1.2│1.9x│ 55│ 71 ││
│  │ ┌────────────────────┐ │             │ ...   │      │    │    │   │    ││
│  │ │ TCS         A  74  │ │             │ (700 rows, virtualized)         ││
│  │ │ Volume Surge       │ │             │                                  ││
│  │ └────────────────────┘ │             │                                  ││
│  │ ┌────────────────────┐ │             │                                  ││
│  │ │ HDFCBANK    A  71  │ │             │                                  ││
│  │ └────────────────────┘ │             │                                  ││
│  │                        │             │                                  ││
│  │ ── WATCHLIST ────────  │             ├──────────────────────────────────┤│
│  │ ┌────────────────────┐ │             │ BOTTOM PANELS (collapsible, 300px)│
│  │ │ 🎯 INFY  COILED   │ │             │                                  ││
│  │ │ 7d │ 1.2% to res. │ │             │ ┌──────────┐ ┌────────────────┐ ││
│  │ └────────────────────┘ │             │ │  CHART   │ │ SECTOR HEATMAP │ ││
│  │ ┌────────────────────┐ │             │ │(selected │ │                │ ││
│  │ │ 🎯 TATAMTRS ACCUM │ │             │ │ symbol)  │ │ IT  +1% BNK+2%│ ││
│  │ │ 4d │ OBV diverge  │ │             │ │          │ │ PHR -1% AUT+3%│ ││
│  │ └────────────────────┘ │             │ │          │ │ MTL +2% ENG+1%│ ││
│  │                        │             │ └──────────┘ └────────────────┘ ││
│  └────────────────────────┘             └──────────────────────────────────┘│
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ STATUS BAR                                                            28px │
│ WS: ● Connected │ Lag: 12ms │ Ticks: 24K/s │ Signals: 4 today │ 11:23 IST│
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.3 Layout Zones — Priority Hierarchy

```
ZONE 1: HEADER BAR (always visible, never collapsed)
  Purpose: market regime at a glance
  Content: NIFTY level + change, VIX, breadth, regime label, system health
  Update rate: 1s (from sector:state stream)
  Height: 56px fixed

ZONE 2: SIGNAL PANEL (left column, always visible)
  Purpose: highest-priority information — what to act on NOW
  Content: active signals sorted by conviction, watchlist below
  Update rate: IMMEDIATE on new signal, 1s for age/decay
  Width: 400px fixed
  Priority: #1 — this is what the user looks at most

ZONE 3: SCREENER TABLE (right area, upper)
  Purpose: full-universe view, sortable, filterable
  Content: all 700 symbols with realtime LTP, features, scores
  Update rate: 100ms batched (delta updates to changed cells only)
  Height: fills remaining vertical space
  Priority: #2 — scanning for opportunities

ZONE 4: BOTTOM PANELS (right area, lower, collapsible)
  Purpose: deeper analysis on selected items
  Content: chart (left) + sector heatmap (right)
  Height: 300px, collapsible to 0px with toggle
  Priority: #3 — supporting context

ZONE 5: STATUS BAR (bottom, always visible)
  Purpose: system health + operational awareness
  Content: WS status, latency, tick rate, signal count, clock
  Update rate: 5s
  Height: 28px fixed
```

### 1.4 Layout at 1280px (laptop)

```
Same structure but:
  Left column: 360px (compressed cards)
  Bottom panels: start collapsed (user expands as needed)
  Screener table: fewer visible columns (hide RSI, show on hover)
```

### 1.5 Layout at 1024px (small laptop / tablet landscape)

```
Switch to tab-based bottom panels:
  [Chart] [Heatmap] tabs — only one visible at a time
Left column: 320px
Screener table: 3-4 visible columns + horizontal scroll
```

---

## 2. Realtime UI Update Model

### 2.1 Update Flow

```
WebSocket message arrives
        │
        ▼
  ┌─────────────┐
  │ WS Client   │  Deserialize JSON, classify message type
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │ State Store │  Merge delta into normalized entity store (Zustand)
  │ (Zustand)   │  Only update changed fields
  └──────┬──────┘
         │
         ▼
  ┌─────────────────┐
  │ React Selectors │  Components subscribe to specific slices
  │ (fine-grained)  │  Only re-render if subscribed slice changed
  └──────┬──────────┘
         │
         ▼
  ┌──────────────┐
  │ DOM Updates  │  React reconciliation — minimal DOM patches
  └──────────────┘
```

### 2.2 Delta Update Strategy

```
TICKS (100ms batched):
  Server sends: { type: "ticks", data: [ {sym: "RELI", ltp: 2847.5, chg: 2.1, vol: 14200000}, ... ] }
  
  Client merges: for each tick in data → store.ticks[sym] = { ...old, ...tick }
  Only changed fields are sent. If RELIANCE's LTP changed but volume didn't,
  server sends: {sym: "RELI", ltp: 2847.5} (no vol field).
  
  Re-render: only table cells whose values changed get updated.
  Implementation: React.memo on cell components + shallow equality check.

SIGNALS (immediate):
  Server sends: { type: "signal", data: { id, symbol, grade, score, strategy, ... } }
  
  Client: 
    store.signals[id] = signal
    store.signalOrder = [id, ...store.signalOrder]  // prepend to top
  
  Re-render: signal panel adds new card at top.

SECTORS (1s batched):
  Server sends: { type: "sectors", data: [ {id: "NIFTY_BANK", breadth: 0.75, ret: 1.4, ...}, ... ] }
  
  Client merges: for each sector → store.sectors[id] = { ...old, ...sector }
  Re-render: heatmap cells update color intensity.

AI ENRICHMENT (async):
  Server sends: { type: "ai_enrichment", signal_id: "abc", ai: { narrative, key_insight, risk_note } }
  
  Client: store.signals["abc"].ai = ai_data
  Re-render: if signal detail panel is open for this signal → show AI narrative.
  If not open → no re-render (enrichment is stored but not displayed).
```

### 2.3 Stale State Handling

```
On WebSocket disconnect:
  1. Set store.connectionStatus = "DISCONNECTED"
  2. Show banner: "Connection lost. Reconnecting..."
  3. All prices display with dimmed opacity (stale indicator)
  4. Signal panel shows "Data may be stale" warning
  5. Disable sound notifications (prevent false alarms on reconnect)

On WebSocket reconnect:
  1. Request full snapshot: { type: "snapshot_request" }
  2. Server responds: { type: "snapshot", ticks: {...all}, signals: {...all}, sectors: {...all} }
  3. Client replaces entire store state with snapshot (authoritative sync)
  4. Resume delta updates from this point
  5. Remove stale indicators
  6. Re-enable sound notifications

Snapshot size: ~700 ticks × 100B + ~20 sectors × 200B + ~10 signals × 500B ≈ 80KB
Transfer time: < 100ms on any reasonable connection.
```

---

## 3. Signal Feed Architecture

### 3.1 Signal Card Structure

```
COMPACT CARD (default view in signal panel, ~80px height):

┌──────────────────────────────────────┐
│ ██ RELIANCE          A+  84    12:14 │  ← grade badge + score + time
│ Range Breakout + Volume Surge        │  ← strategy + confirmations
│ ₹2,847 +2.1% │ 3.2x vol │ 🏢 72%   │  ← price, rel vol, sector breadth
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░ 84/100        │  ← conviction bar
└──────────────────────────────────────┘

Visual encoding:
  Left border: conviction color (4px solid)
    A+: amber/gold (#F59E0B)
    A:  blue (#3B82F6)
    B+: slate (#64748B)
  
  Grade badge: pill with background color matching border
  Score: numeric, right-aligned
  Time: age since signal (updates every 60s: "12:14" → "5m ago" → "47m ago")
  Conviction bar: horizontal fill bar, color matches grade
  Sector breadth: 🏢 icon + percentage (sector health at a glance)
```

### 3.2 Expanded Card (on click)

```
┌──────────────────────────────────────┐
│ ██ RELIANCE          A+  84    12:14 │
│ Range Breakout + Volume Surge        │
│ ₹2,847 +2.1% │ 3.2x vol │ 🏢 72%   │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░ 84/100        │
├──────────────────────────────────────┤
│ CONVICTION FACTORS                   │
│  Technical  ████████░░  78  ×.25  20 │
│  Volume     █████████░  85  ×.20  17 │
│  Setup      ██████████  90  ×.20  18 │
│  Sector     ███████░░░  72  ×.20  14 │
│  Regime     ████████░░  80  ×.10   8 │
│  Options    ██████░░░░  65  ×.05   3 │
│  ─────────────────────── Raw: 80     │
│  Penalties: ×0.90 (lunch)  Final: 84 │
├──────────────────────────────────────┤
│ WHY:                                 │
│ • ATR 18th pctl (compressed)         │
│ • BB width 62% of avg (squeeze)      │
│ • 8 days < 1.5% range               │
│ • Broke ₹2,840 (tested 3×)          │
│ • Volume 3.2x + delivery 58%        │
├──────────────────────────────────────┤
│ ✅ EMA stack aligned │ ✅ Multi-TF   │
│ ✅ NIFTY trending up │ ✅ Smart money│
│ ⚠️ VIX +8% today                    │
├──────────────────────────────────────┤
│ 🤖 AI: "RELIANCE breaking out of    │  ← AI enrichment (loads async)
│ 8-day compression with institutional │
│ accumulation evidence..."            │
│ [Loading...] if AI pending           │
├──────────────────────────────────────┤
│ [📊 Open Chart]  [👁 Dismiss]       │
└──────────────────────────────────────┘
```

### 3.3 Signal Age Decay Visualization

```
Age → Visual treatment:

  < 5 min:    full opacity, conviction bar animated pulse (subtle)
  5-30 min:   full opacity, no animation
  30-60 min:  opacity 0.85, time label turns amber
  > 60 min:   opacity 0.65, time label turns gray, card slightly compressed

Implementation:
  CSS class based on age bucket, calculated every 60s:
    .signal-fresh    { opacity: 1; }
    .signal-aging    { opacity: 0.85; }
    .signal-stale    { opacity: 0.65; }
  
  Age bucket recalculated via setInterval(60000), not per-frame.
```

### 3.4 Signal Panel Sections

```
Signal Panel (left column) divided into 2 sections:

SECTION 1: ACTIVE SIGNALS (top)
  Title: "Signals" + count badge
  Content: scored signals, sorted by conviction DESC
  Max visible: 8 cards (scrollable if more)
  Empty state: "No active signals" (muted text)

SECTION 2: WATCHLIST (bottom, collapsible)
  Title: "Watchlist" + count badge
  Content: pre-breakout COILED and ACCUMULATING symbols
  Sorted by: COILED first, then distance to trigger ASC
  Max visible: 5 cards (scrollable if more)
  Collapse toggle: saves vertical space when not needed

Section divider: thin horizontal line with collapsible chevron.
```

---

## 4. Watchlist UI

### 4.1 Watchlist Card

```
┌──────────────────────────────────────┐
│ 🎯 INFY           COILED      7d    │  ← target icon + state + days
│ ₹1,523 │ 1.2% to ₹1,541 resistance │  ← price + distance to trigger
│ SM: ███████░░░ 72  │ Del: 52%       │  ← smart money score + delivery
│ ░░░░░░░▓▓▓▓▓▓▓▓▓▓ 82% ready       │  ← trigger readiness meter
└──────────────────────────────────────┘

State color coding:
  COILED:       left border amber (#F59E0B) — ready, watch closely
  ACCUMULATING: left border blue (#3B82F6) — building, be patient
  COMPRESSING:  left border slate (#94A3B8) — early stage, low urgency
```

### 4.2 Trigger Readiness Meter

```
Composite score showing how close to breakout trigger:

readiness = weighted_avg(
  distance_to_resistance (closer = higher readiness),  weight 0.35
  smart_money_score,                                   weight 0.25
  compression_days (more = higher readiness, up to 15), weight 0.20
  sector_alignment (sector breadth > 0.60 = bonus),    weight 0.20
)

Display: horizontal progress bar, 0-100%
  < 50%: gray fill
  50-75%: blue fill
  > 75%: amber fill (approaching trigger)
  
  The bar communicates "how ready is this setup" at a glance.
  No number overload — one bar replaces 4 metrics.
```

### 4.3 Watchlist Interactions

```
Click card → expand to show:
  - Compression history (days, ATR percentile over time)
  - Accumulation evidence (OBV divergence, delivery trend)
  - Nearest resistance level + touch count
  - Sector context (breadth, quadrant)

Double-click → open chart for this symbol (in bottom panel)

Watchlist auto-updates:
  Symbol enters COILED → card appears with subtle slide-in animation (200ms ease)
  Symbol triggers breakout → card moves to Signal Panel with highlight
  Symbol reverts to NEUTRAL → card fades out (300ms)
```

---

## 5. Sector Heatmap System

### 5.1 Heatmap Design

```
┌────────────────────────────────────────────────┐
│              SECTOR HEATMAP                     │
│                                                 │
│  ┌────────┐ ┌──────────────┐ ┌───────┐        │
│  │  BANK  │ │    AUTO      │ │ FMCG  │        │
│  │ +1.4%  │ │   +2.1%      │ │ +0.3% │        │
│  │ B:75%  │ │   B:80%      │ │ B:55% │        │
│  │ ▲ LEAD │ │   ▲ LEAD     │ │ → RNG │        │
│  ├────────┤ ├──────────────┤ ├───────┤        │
│  │  IT    │ │    PHARMA    │ │ METAL │        │
│  │ -0.3%  │ │   +0.8%      │ │ +1.8% │        │
│  │ B:35%  │ │   B:60%      │ │ B:70% │        │
│  │ ▼ LAG  │ │   ↗ IMPR     │ │ ▲ LEAD│        │
│  ├────────┤ ├──────────────┤ ├───────┤        │
│  │ REALTY │ │    ENERGY    │ │ MEDIA │        │
│  │ -1.2%  │ │   +0.5%      │ │ -0.8% │        │
│  │ B:28%  │ │   B:50%      │ │ B:30% │        │
│  │ ▼ LAG  │ │   → RNG      │ │ ↘ WEAK│        │
│  └────────┘ └──────────────┘ └───────┘        │
│                                                 │
└────────────────────────────────────────────────┘

Cell size: proportional to sector market cap weight (treemap layout).
           Larger sectors get bigger cells → naturally prioritized.

Color:
  Return-based gradient:
    > +2%:   deep green (#059669)
    +1-2%:   medium green (#10B981)
    0-1%:    light green (#6EE7B7)
    0 to -1%: light red (#FCA5A5)
    -1 to -2%: medium red (#EF4444)
    < -2%:   deep red (#DC2626)

  Opacity: modulated by breadth
    Breadth > 0.65: full opacity (broad participation, color is meaningful)
    Breadth < 0.40: 60% opacity (narrow move, less reliable signal)

Quadrant indicator: small arrow + abbreviation
  ▲ LEAD (Leading), ↗ IMPR (Improving), ↘ WEAK (Weakening), ▼ LAG (Lagging), → RNG (Range)
```

### 5.2 Heatmap Hover / Click

```
Hover → tooltip:
  "NIFTY BANK: +1.4%
   Breadth: 75% (9 of 12 advancing)
   Above VWAP: 83%
   Rotation: LEADING
   Money Flow: Strong Inflow
   FII: Buying"

Click → drilldown:
  Replace heatmap with sector detail view:
  
  ┌────────────────────────────────────┐
  │ ← Back │ NIFTY BANK │ +1.4%      │
  ├────────────────────────────────────┤
  │ HDFCBANK  +2.1%  ██████████░░ 85  │
  │ ICICIBANK +1.8%  █████████░░░ 78  │
  │ SBIN      +1.5%  ████████░░░░ 72  │
  │ KOTAKBANK +1.2%  ███████░░░░░ 65  │
  │ ...                                │
  │ BANDHANBNK -0.5% ██░░░░░░░░░░ 22  │
  ├────────────────────────────────────┤
  │ Breadth: 75% │ VWAP: 83%│ PCR:0.9│
  └────────────────────────────────────┘
  
  Constituent list sorted by return DESC.
  Bar shows relative volume (higher = brighter bar).
```

### 5.3 Compact Mode

```
When bottom panel height is constrained (< 200px), heatmap switches to compact:

┌─────────────────────────────────────────────────────┐
│ BNK +1.4 │ AUT +2.1 │ MTL +1.8 │ PHR +0.8 │ ENG +0.5 │
│ IT  -0.3 │ MED -0.8 │ RLT -1.2 │ FMCG+0.3 │ INF +0.4 │
└─────────────────────────────────────────────────────┘

Single-row strip with color coding. Space-efficient.
Expand toggle to full heatmap view.
```

---

## 6. Market Breadth Visualization

### 6.1 Header Bar Breadth Display

```
The header bar contains the most critical market metrics:

┌──────────────────────────────────────────────────────────────────────┐
│ INFUSION    NIFTY 23,456 +0.82%   VIX 14.2 ▼3%   [Breadth] [Regime]│
└──────────────────────────────────────────────────────────────────────┘

Breadth display: segmented bar in header

  Breadth bar (120px wide):
  ████████████████░░░░░░░  72%
  
  Colors:
    > 65%: green fill   (healthy)
    40-65%: amber fill   (neutral)
    < 40%: red fill      (weak)
  
  Updates every 1s from sector:state stream.

Regime badge: colored pill
  TRENDING UP:   green pill with ↗ icon
  RANGE BOUND:   amber pill with ↔ icon
  TRENDING DOWN: red pill with ↘ icon
  VOLATILE:      purple pill with ⚡ icon
  
  Changes only on regime transitions (rare, 0-3/day).
  Brief pulse animation on transition (300ms) to draw attention.
```

### 6.2 Breadth Thrust Indicator

```
When breadth thrust fires (rare, powerful):

Header bar adds temporary banner below:

┌──────────────────────────────────────────────────────┐
│ ⚡ BREADTH THRUST — NIFTY BANK — Breadth surged to   │
│   75% from 33% in 90 minutes                         │
└──────────────────────────────────────────────────────┘

Banner: amber background, subtle pulsing border, auto-dismisses after 30 min.
This is an institutional-quality signal — warrants visual prominence.
```

---

## 7. Signal Detail Panel

### 7.1 Detail Panel Trigger

```
Signal card click → expanded card in-place (within signal panel).
Only ONE card expanded at a time. Clicking another collapses the current.

For deeper analysis: [📊 Full Detail] button opens a modal overlay (640px wide)
with the complete factor breakdown, conditions, and chart.
```

### 7.2 Factor Breakdown Visualization

```
Each conviction factor displayed as a labeled horizontal bar:

  Technical  ████████░░  78/100  ×0.25 = 19.5
  
  Bar color: same color family, intensity proportional to score
    > 75: full saturation
    50-75: medium saturation
    < 50: desaturated

  Components listed below bar on expand:
    ├── Trend alignment: 40/40 (EMA5 > EMA20 > EMA50)
    ├── RSI position: 20/20 (RSI 61, healthy momentum)
    ├── Price vs VWAP: 12/20 (above VWAP, flat)
    └── MACD alignment: 6/20 (bullish but weak histogram)

Penalty section:
  ⚠️ Lunch lull: ×0.90   (-8 points)
  ⚠️ VIX elevated: -5 pts (contradiction)
  
  Displayed with amber warning icons. Makes penalties visible and understandable.
```

### 7.3 Confirmation / Contradiction Badges

```
Confirmations shown as green check badges:
  ✅ EMA stack aligned
  ✅ Multi-TF (1m+5m+15m)
  ✅ Smart money (OBV + delivery)
  ✅ Also confirmed by: volume_surge

Contradictions shown as amber warning badges:
  ⚠️ VIX elevated +8%
  ⚠️ Lunch-hour timing

Layout: horizontal badge row, wrapping as needed.
Compact enough to scan in < 2 seconds.
```

---

## 8. AI Explanation Rendering

### 8.1 Progressive Loading

```
Signal card expansion sequence:

  t+0ms:    Card expands. DETERMINISTIC content renders immediately:
            - Factor bars
            - Conditions list
            - Confirmations / contradictions
            
  t+0ms:    AI section shows: [🤖 Loading explanation...]
            Skeleton loader (gray pulsing placeholder lines)
            
  t+2-10s:  AI enrichment arrives via WebSocket
            Skeleton replaced with narrative text
            Fade-in transition (200ms)
            
  Never:    If AI fails to arrive within 15s, skeleton replaced with:
            "Explanation unavailable. See factor breakdown above."
            
  Offline:  If AI status is OFFLINE (from health bar), don't show
            skeleton at all. Show: "AI offline — deterministic view"
```

### 8.2 AI Content Layout

```
AI explanation displayed in a visually distinct section:

┌─────────────────────────────────────────┐
│ 🤖 AI Analysis                          │
│                                          │
│ "RELIANCE is breaking out of an 8-day   │
│ range compression with 3.2x volume      │
│ surge. Bollinger Bands had squeezed to  │
│ 62% of normal width, indicating a       │
│ coiled spring pattern."                  │
│                                          │
│ 💡 Multi-factor confluence with smart   │
│ money accumulation makes this a high-   │
│ quality institutional setup.             │
│                                          │
│ ⚠️ Lunch-hour timing reduces            │
│ reliability; watch for volume follow-   │
│ through in the afternoon.               │
└─────────────────────────────────────────┘

Visual distinction:
  Background: slightly different shade (1-2% lighter than card bg)
  Left border: 2px dotted line (vs. solid for deterministic sections)
  Small robot icon header: signals "this is AI-generated"
  
  This visual distinction is important: user must always know
  what's deterministic data vs. AI-generated narrative.
```

---

## 9. Chart Integration Architecture

### 9.1 Library Selection

```
TradingView Lightweight Charts (v4)
  - MIT license, free, no commercial restrictions
  - ~40KB gzipped bundle size
  - Candlestick, line, area, histogram series
  - Marker annotations (for signal events)
  - Realtime tick updates via API
  - Custom overlays (resistance levels, EMA lines)

Why NOT TradingView Advanced Charts:
  - Requires commercial license ($)
  - 1.5MB+ bundle size
  - Feature-heavy (drawing tools, indicators) — most unused
  - We compute indicators server-side; don't need client-side TA

Why NOT Chart.js / Recharts:
  - Not designed for financial candlestick data
  - Poor realtime performance with tick-by-tick updates
  - No built-in OHLC support
```

### 9.2 Chart Data Feed

```
Chart opens for symbol → two data sources:

1. HISTORICAL BARS (REST):
   GET /api/ohlcv/{symbol}/5m?limit=200
   Returns: last 200 5-minute bars
   Rendered: candlestick series
   Loaded once on chart open.

2. REALTIME TICKS (WebSocket):
   Client sends: { type: "focus_symbol", symbol: "RELIANCE" }
   Server pushes: every tick for RELIANCE (unbatched)
   Client: aggregates ticks into current bar in real-time
   On bar close (every 5 min): append completed bar to series

Chart bar aggregation logic (client-side):
  currentBar = { open, high, low, close, volume }
  On each tick:
    if tick.timestamp >= currentBar.closeTime:
      // new bar period
      series.append(currentBar)
      currentBar = { open: tick.ltp, high: tick.ltp, low: tick.ltp, close: tick.ltp, volume: 0 }
    else:
      currentBar.high = max(currentBar.high, tick.ltp)
      currentBar.low = min(currentBar.low, tick.ltp)
      currentBar.close = tick.ltp
      currentBar.volume += tick.volume
```

### 9.3 Chart Overlays

```
Overlays rendered on the chart (computed server-side, sent with historical data):

1. EMA Lines (5, 20, 50):
   Thin colored lines. Colors: 5=cyan, 20=blue, 50=purple
   Sent as part of /api/ohlcv response (pre-computed)

2. Resistance/Support Levels:
   Horizontal dashed lines at key levels
   Data source: pre-breakout state machine (nearest_resistance)
   Sent as annotations: { price: 2840, label: "R1", style: "dashed" }

3. Signal Markers:
   Triangular markers on bars where signals fired
   ▲ green (bullish) or ▼ red (bearish)
   Data source: signals table filtered by symbol + today's date
   On hover: tooltip showing strategy + conviction score

4. VWAP Line:
   Orange dotted line showing intraday VWAP
   Computed server-side, sent with bar data
```

### 9.4 Chart Sizing

```
Chart lives in bottom-left panel (alongside sector heatmap).
Default: 50% of bottom panel width.

Split: draggable divider between chart and heatmap.
Collapse: heatmap can collapse to give chart full width (for deep analysis).
Chart height: fixed at bottom panel height (300px default, resizable).

Chart is a SUPPORTING tool, not the primary interface.
User glances at chart to confirm setup visually, then returns to signal panel.
```

---

## 10. Mobile Responsiveness

### 10.1 Mobile Layout (< 768px)

```
Desktop layout is NOT rearranged for mobile. Instead, mobile gets a
PURPOSE-BUILT compact layout:

┌──────────────────────────┐
│ HEADER (compact)    40px │
│ N 23,456 +0.82%│B:72%│● │
├──────────────────────────┤
│ [Signals] [Watch] [Sect] │  ← tab bar
├──────────────────────────┤
│                          │
│ SIGNAL FEED (full width) │  ← active tab content
│                          │
│ ┌──────────────────────┐ │
│ │ RELIANCE    A+ 84  5m│ │
│ │ Range Breakout        │ │
│ │ ₹2,847 +2.1% │ 3.2x  │ │
│ └──────────────────────┘ │
│ ┌──────────────────────┐ │
│ │ TCS         A  74 12m│ │
│ │ Volume Surge          │ │
│ │ ₹1,523 +1.8% │ 2.8x  │ │
│ └──────────────────────┘ │
│                          │
└──────────────────────────┘
│ STATUS BAR          24px │
└──────────────────────────┘

Three tabs:
  Signals: signal cards (compact, full width)
  Watch: watchlist cards (compact)
  Sectors: compact heatmap strip + list view

No screener table on mobile (too dense for small screens).
No chart on mobile (user opens dedicated broker app for charting).
```

### 10.2 Mobile Signal Card (Compact)

```
Tap card → slide-in detail panel from bottom (bottom sheet)
  Factor bars, conditions, confirmations visible in bottom sheet
  Swipe down to dismiss

No expanded inline card on mobile — screen space too limited.
Bottom sheet pattern is standard mobile UX.
```

### 10.3 Bandwidth-Aware Updates

```
Mobile clients subscribe to reduced data:

  Ticks: receive only top 50 symbols by conviction relevance (not all 700)
  Batch: 500ms instead of 100ms
  Sectors: 5s instead of 1s
  
  Client signals mobile mode on WS connect:
    { type: "subscribe", channels: ["signals", "sectors"], mode: "mobile" }
  
  Server adjusts:
    - Filter tick stream to symbols in active signals + watchlist only
    - Increase batch window
    - Omit feature columns (RSI, MACD) from tick updates

  Bandwidth: ~5 KB/sec (vs. 23 KB/sec desktop). Comfortable on 4G.
```

---

## 11. Frontend State Management

### 11.1 Why Zustand

```
Zustand over Redux:
  - 1.2KB vs 7.7KB bundle
  - No boilerplate (no actions, reducers, action creators)
  - Direct mutative API (set(state => ({...})))
  - Fine-grained subscriptions (components subscribe to slices)
  - No context provider wrapping
  - TypeScript-first

Zustand over TanStack Query:
  - TanStack Query is for server-state (request/response).
  - Our primary data source is WebSocket push, not REST requests.
  - TanStack Query doesn't model streaming data well.
  - We use TanStack Query ONLY for one-off REST fetches (historical bars, precision stats).
  - Zustand manages all realtime WebSocket state.

Zustand over Jotai/Recoil:
  - Single store with slices > many scattered atoms for our use case
  - Easier to snapshot/debug full state
  - Better for normalized entity stores
```

### 11.2 Store Structure

```typescript
interface InfusionStore {
  // Connection
  connectionStatus: 'CONNECTED' | 'CONNECTING' | 'DISCONNECTED'
  lastTickTime: number             // epoch ms of last tick received
  
  // Market ticks (normalized entity map)
  ticks: Record<string, TickData>  // key: symbol
  // TickData: { ltp, open, high, low, close, volume, chg_pct, rel_vol, rsi, score, grade }
  
  // Signals (ordered + map)
  signals: Record<string, ScoredSignal>  // key: signal_id
  signalOrder: string[]                  // signal_ids sorted by conviction DESC
  
  // Watchlist
  watchlist: Record<string, WatchlistEntry>  // key: symbol
  watchlistOrder: string[]                    // sorted by state priority + readiness
  
  // Sectors
  sectors: Record<string, SectorState>  // key: sector_id
  
  // Market overview
  market: {
    niftyLevel: number
    niftyChange: number
    breadth: number
    regime: string
    vix: number
    vixChange: number
  }
  
  // UI state
  selectedSymbol: string | null        // for chart + detail view
  expandedSignalId: string | null      // which signal card is expanded
  bottomPanelMode: 'chart' | 'heatmap' | 'split'
  bottomPanelCollapsed: boolean
  
  // Health
  health: {
    wsConnected: boolean
    lagMs: number
    ticksPerSec: number
    signalsToday: number
    aiStatus: 'online' | 'degraded' | 'offline'
  }
  
  // AI enrichments (loaded async)
  aiEnrichments: Record<string, AIEnrichment>  // key: signal_id
}
```

### 11.3 Subscription Pattern

```typescript
// Components subscribe to minimal slices:

// Screener table row — only re-renders when THIS symbol's tick changes
const tick = useStore(state => state.ticks[symbol])

// Signal panel — only re-renders when signal order changes
const signalIds = useStore(state => state.signalOrder)

// Header breadth — only re-renders when market.breadth changes
const breadth = useStore(state => state.market.breadth)

// This fine-grained subscription prevents cascade re-renders.
// When RELIANCE's LTP updates, only RELIANCE's table row re-renders.
// Not the entire table. Not the signal panel. Not the heatmap.
```

### 11.4 Update Functions

```typescript
// WebSocket message handler dispatches to typed updaters:

const wsHandlers = {
  ticks: (data: TickDelta[]) => {
    useStore.setState(state => {
      const newTicks = { ...state.ticks }
      for (const delta of data) {
        newTicks[delta.sym] = { ...newTicks[delta.sym], ...delta }
      }
      return { ticks: newTicks }
    })
  },
  
  signal: (data: ScoredSignal) => {
    useStore.setState(state => ({
      signals: { ...state.signals, [data.id]: data },
      signalOrder: [data.id, ...state.signalOrder.filter(id => id !== data.id)]
    }))
  },
  
  ai_enrichment: (data: { signal_id: string, ai: AIEnrichment }) => {
    useStore.setState(state => ({
      aiEnrichments: { ...state.aiEnrichments, [data.signal_id]: data.ai }
    }))
  },
  
  // ... sectors, health, etc.
}
```

---

## 12. WebSocket Client Architecture

### 12.1 Connection Manager

```typescript
class WSManager {
  private ws: WebSocket | null = null
  private reconnectDelay = 1000       // start at 1s
  private maxReconnectDelay = 30000   // cap at 30s
  private heartbeatInterval: number | null = null
  
  connect() {
    this.ws = new WebSocket(WS_URL)
    
    this.ws.onopen = () => {
      this.reconnectDelay = 1000  // reset on success
      this.subscribe(['ticks', 'signals', 'sectors', 'health'])
      this.startHeartbeat()
      useStore.setState({ connectionStatus: 'CONNECTED' })
    }
    
    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      wsHandlers[msg.type]?.(msg.data)
    }
    
    this.ws.onclose = () => {
      useStore.setState({ connectionStatus: 'DISCONNECTED' })
      this.stopHeartbeat()
      this.scheduleReconnect()
    }
  }
  
  scheduleReconnect() {
    useStore.setState({ connectionStatus: 'CONNECTING' })
    const jitter = this.reconnectDelay * 0.2 * Math.random()
    setTimeout(() => this.connect(), this.reconnectDelay + jitter)
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay)
  }
  
  startHeartbeat() {
    this.heartbeatInterval = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, 30000)  // ping every 30s
  }
  
  requestSnapshot() {
    // Called on reconnect to sync full state
    this.ws?.send(JSON.stringify({ type: 'snapshot_request' }))
  }
}
```

### 12.2 Reconnect + State Recovery

```
Reconnect sequence:

  1. WS closes (server restart, network blip, etc.)
  2. UI shows: "Reconnecting..." banner + stale indicators on prices
  3. Exponential backoff: 1s, 2s, 4s, 8s, 16s, 30s (max)
  4. On reconnect success:
     a. Send snapshot_request
     b. Receive full state snapshot
     c. Replace store state (authoritative sync)
     d. Resume delta updates
     e. Remove stale indicators
  5. If reconnect fails for > 2 minutes:
     Show: "Connection lost. Check network." with manual retry button.
```

---

## 13. Performance Optimization

### 13.1 Performance Budgets

```
INITIAL LOAD:
  Target: < 2s First Contentful Paint, < 3s interactive
  SSR: header bar + signal panel shell rendered server-side
  Hydration: table and heatmap load client-side after hydration
  JS bundle: < 200KB gzipped (excluding chart library)
  Chart library: lazy-loaded on first chart open (~40KB)

RUNTIME (during market hours):
  Frame rate: 60fps sustained (no jank during tick updates)
  Tick update processing: < 2ms per batch (100ms window)
  Signal card render: < 5ms (including conviction bars)
  Table row update: < 0.5ms per row (delta update)
  Memory: < 100MB heap (800 symbols + state + DOM)
  
  React re-render budget per 100ms tick batch:
    Tick store update: ~0.5ms (object spread for changed symbols)
    React reconciliation: ~1ms (only changed cells)
    DOM updates: ~0.5ms (text node changes, no layout shifts)
    Total: ~2ms per batch (well within 16.6ms frame budget)
```

### 13.2 Table Virtualization

```
Screener table: 700 rows × 8+ columns.
Rendering all 700 DOM rows would be expensive and unnecessary.

Virtualization: @tanstack/react-virtual
  Only render visible rows + small overscan buffer (10 rows above/below)
  Visible rows at 32px height, 300px viewport: ~28 visible rows
  Total DOM rows: ~48 (28 visible + 20 overscan)
  
  On scroll: swap out DOM rows, recycle elements
  On tick update: only update visible row cells (invisible rows skip render)

Column virtualization: not needed (< 12 columns, all fit in viewport)
```

### 13.3 Render Optimization Techniques

```
1. React.memo on EVERY table cell component
   Cell only re-renders if its specific value changed.
   
2. Stable references for objects
   Zustand selectors return same object reference if data unchanged.
   
3. requestAnimationFrame batching
   If multiple WS messages arrive in same frame, batch state updates:
   
   let pendingUpdates: TickDelta[] = []
   let rafScheduled = false
   
   function onTickMessage(data: TickDelta[]) {
     pendingUpdates.push(...data)
     if (!rafScheduled) {
       rafScheduled = true
       requestAnimationFrame(() => {
         applyTickUpdates(pendingUpdates)
         pendingUpdates = []
         rafScheduled = false
       })
     }
   }

4. CSS-only animations
   Conviction bar fill: CSS transition (not JS animation)
   Signal card expand: CSS max-height transition
   No JavaScript animation loops during market hours.

5. Lazy loading
   Chart library: loaded on first chart open
   Sector drilldown: loaded on first heatmap click
   Historical precision stats: loaded on first analytics tab open
```

### 13.4 Memory Management

```
Tick history: NOT kept in frontend state.
  Store only latest tick per symbol (700 entries × ~200B = ~140KB)
  Historical ticks are in the chart component's internal buffer (managed by Lightweight Charts)
  
Signal history: keep last 50 signals in memory (older ones GC'd from store)
  On scroll-to-bottom in signal panel → REST fetch for more

Sector history: latest snapshot only (20 entries × ~300B = ~6KB)

Total Zustand store size: < 500KB
Total heap (including React, DOM, chart): < 100MB target
```

---

## 14. UI/UX Prioritization Logic

### 14.1 Keyboard Shortcuts

```
Market-hours keyboard shortcuts for fast operation:

  ↑/↓        Navigate signal list
  Enter      Expand/collapse selected signal card
  Escape     Collapse expanded card / close modal
  C          Toggle chart for selected signal's symbol
  H          Toggle sector heatmap
  S          Focus screener table search
  F          Cycle table sort: Score → RelVol → %Chg → Symbol
  1/2/3      Filter signals: 1=A+ only, 2=A+A, 3=All
  M          Mute alerts (toggles)
  ?          Show keyboard shortcut overlay

Implementation: global keydown listener (document.addEventListener)
Active only when no text input is focused.
Shortcuts shown in footer tooltip on hover.
```

### 14.2 Quick Filtering

```
Screener table filters (in table header):

  Sector dropdown:  [All Sectors ▾] → filter to one sector
  Min Score slider: [0 ━━━●━━━ 100] → filter by conviction score
  F&O only toggle:  [F&O ○] → show only F&O stocks
  Search:           [🔍 Search symbol...] → live filter by symbol name

  Filters are URL-persisted (query params) so they survive page refresh.
  Filters apply in < 1ms (client-side filtering of in-memory data).
```

### 14.3 Rapid Context Switching

```
Click symbol anywhere → updates:
  1. selectedSymbol in store
  2. Chart loads/switches to this symbol
  3. Screener table scrolls to and highlights this row
  4. If the symbol has an active signal, signal card highlights

This cross-linking means clicking "RELIANCE" in the heatmap,
the signal panel, OR the screener table all produce the same result.
One click = full context on that symbol across all views.
```

---

## 15. Visual Design System

### 15.1 Color Semantics

```
CONVICTION COLORS (signal grades):
  A+:  amber/gold    #F59E0B (warm, attention-grabbing, premium)
  A:   blue          #3B82F6 (confident, clear)
  B+:  slate         #64748B (neutral, informational)
  B:   muted slate   #94A3B8 (low priority)
  C:   not displayed

MARKET DIRECTION:
  Positive:  green    #10B981 (not too bright, easy on eyes)
  Negative:  red      #EF4444 (clear but not alarming)
  Neutral:   gray     #6B7280
  
  These are muted vs. the pure red/green of retail platforms.
  Institutional feel: information, not excitement.

STATUS:
  Healthy:   green    #22C55E
  Warning:   amber    #F59E0B
  Error:     red      #EF4444
  Offline:   gray     #6B7280

BACKGROUNDS (dark mode):
  Page bg:        #0F172A (deep navy, not pure black)
  Card bg:        #1E293B (slightly lighter)
  Hover bg:       #334155
  Active/selected: #1E3A5F (blue tint)
  Border:         #334155 (subtle)
```

### 15.2 Typography

```
Font stack: 'Inter', system-ui, sans-serif
  Inter: clean, professional, excellent numeric readability
  Load from Google Fonts (woff2, < 30KB for regular + medium + semibold)

Hierarchy:
  Header bar labels:    13px semibold, letter-spacing 0.02em
  Signal card title:    15px semibold
  Signal card body:     13px regular
  Table headers:        12px semibold uppercase, letter-spacing 0.05em
  Table cells:          13px regular (monospace for numbers: 'JetBrains Mono')
  Status bar:           12px regular
  
  Numeric values (prices, volumes, scores): monospace font
    Prevents layout shift when numbers change (all digits same width)
    Font: 'JetBrains Mono' or 'Fira Code', fallback: 'Courier New'

Line height: 1.4 for body text, 1.2 for compact elements (cards, table)
```

### 15.3 Spacing System

```
Base unit: 4px

  xs:  4px   (tight padding inside badges)
  sm:  8px   (padding inside compact elements)
  md:  12px  (card internal padding)
  lg:  16px  (gap between cards)
  xl:  24px  (section gaps)

Card padding: 12px
Card gap: 8px (between stacked cards)
Table row height: 32px
Table cell padding: 8px horizontal, 4px vertical
```

### 15.4 Dark Mode First

```
Dark mode is the ONLY mode. No light mode toggle.

Rationale:
  - Market screens are stared at for 6+ hours
  - Dark mode reduces eye strain
  - Better contrast for colored indicators (amber/green pop on dark bg)
  - Institutional platforms (Bloomberg, Refinitiv) are dark by default
  - One mode = less code, fewer bugs

Contrast compliance:
  All text: minimum 4.5:1 contrast ratio against background (WCAG AA)
  Primary text (#E2E8F0 on #0F172A): 11.6:1 ratio ✓
  Secondary text (#94A3B8 on #0F172A): 5.2:1 ratio ✓
  Border (#334155 on #0F172A): 2.1:1 (decorative, not informational) ✓
```

### 15.5 Animation Policy

```
ALLOWED:
  - CSS transitions on hover (150ms ease): background color, opacity
  - Card expand/collapse: max-height transition (200ms ease)
  - Signal card entry: translateY slide-in (200ms ease-out)
  - Breadth thrust banner: subtle border pulse (1s ease infinite, CSS only)
  - Conviction bar fill: width transition (300ms ease)

PROHIBITED:
  - JavaScript animation loops (requestAnimationFrame for visuals)
  - Continuous shimmer/shine effects
  - Auto-scrolling tickers
  - Particle effects
  - 3D transforms
  - Parallax
  - Any animation that runs during tick processing

Rule: if an animation runs continuously, it competes with tick processing
for CPU/GPU time. Only transition-on-change animations are acceptable.
```

---

## Frontend Technology Summary

```
FRAMEWORK & BUILD:
  Next.js 14+ (App Router)
  React 18 (concurrent features)
  TypeScript (strict mode)
  CSS Modules (scoped styles, no Tailwind)

STATE:
  Zustand (realtime WS state)
  TanStack Query (REST fetches: historical data, precision stats)

TABLE:
  @tanstack/react-table (headless table logic)
  @tanstack/react-virtual (row virtualization)

CHART:
  lightweight-charts v4 (TradingView, MIT)

FONTS:
  Inter (UI) + JetBrains Mono (numbers)
  Google Fonts, woff2 subset

WS CLIENT:
  Native WebSocket API (no socket.io)
  Custom reconnect manager

BUNDLE BUDGET:
  Framework (Next.js + React):  ~80KB gzipped
  Zustand:                       ~1KB
  TanStack Table + Virtual:      ~15KB
  Lightweight Charts:            ~40KB (lazy-loaded)
  Application code:              ~50KB
  ─────────────────────────────────
  Total:                         ~186KB gzipped (target < 200KB)
```

---

## Phase 5 Boundary

Key design decisions:

| Decision | Rationale |
|---|---|
| Single-page command center, no navigation | Zero context-switching during market hours. Everything visible or one click away. |
| Left panel = signals + watchlist (always visible) | Highest-priority information dominates layout. User's eyes go here first. |
| Dark mode only | Reduces code, matches institutional standard, reduces eye strain for 6-hour sessions. |
| Zustand over Redux / TanStack Query for WS state | Minimal boilerplate, fine-grained subscriptions, direct store access. TanStack Query only for REST. |
| Table virtualization (@tanstack/react-virtual) | 700 rows rendered as ~48 DOM nodes. Smooth scrolling at 60fps. |
| Delta updates with React.memo cells | Only changed table cells re-render. Per-tick render cost: < 2ms. |
| Lightweight Charts (TradingView, MIT) | Free, 40KB, lazy-loaded. Chart supports decisions, doesn't dominate UI. |
| Treemap heatmap with breadth-modulated opacity | Sector strength + participation confidence in one visual. Drilldown on click. |
| Two-track AI rendering (skeleton → content) | Deterministic shows instantly. AI enrichment fades in async. Never blocks. |
| Keyboard shortcuts for market-hours operation | Fast scanning without mouse. ↑↓ navigate, Enter expand, C chart, H heatmap. |
| CSS-only animations, no JS animation loops | Zero competition with tick processing for CPU time. |
| Mobile: tab-based compact layout, not responsive desktop | Purpose-built mobile UX. No chart, no table. Signal-feed-first. |
| Monospace for numeric values | Prevents layout shift when prices update. Professional readability. |
| Muted color palette (not bright red/green) | Institutional feel. Information, not excitement. |

**Awaiting approval to proceed to Phase 6 — Performance & Optimization.**
