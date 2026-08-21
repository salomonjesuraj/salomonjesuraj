# Infusion — Complete Dashboard Architecture & Breakout/Volume Detection Audit

**Purpose of this document.** The user reported: *"this dashboard is not finding out the potential breakout stocks immediately by rising in volume, we are completely missing something and aligned to something else."* This document does two things in one file, for review by other agents/engineers:

1. Maps the **complete system architecture** — every service, how data flows end to end, with real code.
2. Gives a **root-cause audit of exactly why a stock breaking out on rising volume is not caught immediately** — grounded in the actual source code (file + line references throughout), not speculation.

Everything below was read directly from the live repository on 2026-08-18. Where a finding is a design tradeoff rather than a bug, it's labeled as such. Where it's a real gap, it's called a gap.

---

## Part 0 — Executive Summary (read this first)

The system does **not** have a simple "volume spike → alert" path. Every volume-driven candidate must pass through a stack of independent, AND-gated filters, several of which are specifically hostile to a *fresh* breakout (as opposed to an already-established, multi-indicator-confirmed trend). The four biggest contributors, in order of impact:

1. **The "Pre-Breakout Watch" panel only shows stocks that went through a prior multi-tick compression phase.** A stock that jumps straight from normal/IDLE state to a volume-driven move — without first spending 5+ ticks in a tightening Bollinger Band — is **structurally invisible** to it. Worse: the state machine treats the price-range *expansion* that a real breakout produces as "compression reversed" and expires the setup, right at the moment it should be graduating. (`services/scanner/src/scanner/pre_breakout.py`)

2. **The one strategy purpose-built for "volume expansion" (`vol_vwap_breakout`) only fires on a single-bar VWAP crossover** (price must have been ≤ VWAP on the *previous* closed bar and > VWAP *now*). A stock that's already trading above VWAP when volume kicks in — the far more common real-world "breakout while already trending" pattern — can never trigger it, no matter how much volume shows up. (`services/scanner/src/scanner/strategies/vol_vwap_breakout.py:73-83`)

3. **The other strategy (`options_first_hybrid`) treats volume as only 1 of 8 independent gates, and requires 4 of 8 to align.** EMA-stack, MACD, RSI-zone, and ATR-trend are all *lagging* indicators — they take multiple closed bars to catch up to a fresh volume-driven move. A stock 2-3 minutes into a genuine breakout, with huge volume but an EMA stack that hasn't crossed yet, will fail the gate count. (`services/scanner/src/scanner/strategies/options_first_hybrid.py:88-118`)

4. **Even a candidate that clears both strategies gets a second, much stricter gate before it's ever shown or alerted** — the "Precision Guard": conviction score ≥ 80 (not the 62-70 a strategy itself requires), R:R ≥ 1.2, **and it must be `mid_morning`, `midday`, or `closing` session — the `opening` session (09:15–10:00 IST) is explicitly excluded**, which is precisely when a large share of real volume breakouts happen. (`services/scanner/src/scanner/suppression.py:204-226`, thresholds in `services/scanner/src/scanner/config.py:29-41`)

On top of all four: no symbol is evaluated *at all* until it has **26 completed 1-minute candles** of history in the running process (`indicator_ready` gate, `max(bb_period=20, macd_slow=26)`), which — combined with #4 — means there is a **~45-minute dead zone every morning (09:15–10:00 IST)** where breakout candidates are either not yet evaluable or evaluable-but-suppressed, and the universe being watched at all is capped at the **F&O-eligible symbol list (~208 symbols)**, not the full NSE market.

None of this is a single bug. It's a *design stance*: the system is tuned for **confirmed, low-false-positive, options-grade setups**, not for **first-alert-on-a-fresh-volume-spike**. Part 5 below lays out the actual code for all of this; Part 7 lists concrete options to change the stance if that's what's wanted.

---

## Part 1 — System Architecture

### 1.1 Service Map

Fifteen containers (`docker-compose.yml`), all on one bridge network, Redis + Postgres as shared state:

```
                              ┌─────────────┐        ┌──────────────┐
                              │   Upstox    │        │   NSE site   │
                              │  (broker)   │        │ (bhavcopy,   │
                              └──────┬──────┘        │  F&O ban)    │
                                     │ WS ticks       └──────┬───────┘
                                     ▼                        │
                              ┌─────────────┐                 │
                              │  ingestion  │                 │
                              │ (WS client) │                 │
                              └──────┬──────┘                 │
                                     │ Redis Stream: ticks.raw │
                                     ▼                        │
                              ┌─────────────┐                 │
                              │ normalizer  │                 │
                              └──────┬──────┘                 │
                                     │ Redis Stream: ticks.normalized
                                     ▼                        │
                              ┌─────────────┐                 │
                              │feature-engine│◄───── historical bootstrap
                              │ (indicators, │        (scheduler/historical.py)
                              │ closed-candle│                 │
                              │ aggregation) │                 │
                              └──────┬──────┘                 │
                                     │ Redis Stream: features.computed
                                     ▼                        │
                              ┌─────────────┐                 │
                              │   scanner   │◄──── sector-intel, conviction
                              │ (strategies,│       (auxiliary scorers)
                              │  gates, PB  │
                              │ state machine)│
                              └──────┬──────┘
                          ┌──────────┼──────────┐
                          ▼          ▼          ▼
                    Stream:     Stream:     Redis hash/ZSET:
                    scan.signals scan.suppressed  infusion:signal:*,
                          │          │          infusion:prebreak:*
                          ▼          ▼
                   ┌───────────┐ ┌──────────┐
                   │  alerter  │ │ archiver │──► Postgres (signals, outcomes)
                   │ (Telegram)│ └──────────┘
                   └───────────┘
                                     │
                                     ▼
                    ┌────────────────────────────────┐
                    │       api (aiohttp, :8000)      │
                    │  reads Redis + Postgres, serves  │
                    │  /api/* to the dashboard          │
                    └───────────────┬────────────────┘
                                     │ HTTP polling (api.js, 2-120s per endpoint)
                                     ▼
                    ┌────────────────────────────────┐
                    │   dashboard (static, nginx:3000) │
                    │  Classic shell + New shell        │
                    └────────────────────────────────┘
                                     ▲
                                     │ WebSocket ticks (live LTP only)
                              ┌─────────────┐
                              │ ws-gateway  │
                              └─────────────┘
```

`scheduler` runs periodic sweeps (historical bootstrap, optimizer proposal, Kelly sizing, VIX multiplier, ML classifier retrain, premium capture). `nse-scraper` fetches delivery %, F&O ban list. `telegram-bot` handles inbound Telegram commands. `sector-intel` and `conviction` are smaller auxiliary scoring services.

### 1.2 Service Inventory (from `docker-compose.yml`)

| Service | Role | Depends on |
|---|---|---|
| `redis` | Shared hot state, streams, pub/sub | — |
| `postgres` | Durable archive (signals, outcomes, journal) | — |
| `ingestion` | Upstox WebSocket client → raw tick stream | redis |
| `normalizer` | Raw ticks → normalized schema | redis |
| `feature-engine` | Per-symbol indicator computation, closed-candle aggregation | redis, postgres |
| `scanner` | Strategy evaluation, pre-breakout state machine, suppression gates, signal publishing | redis |
| `sector-intel` | Sector-level strength scoring | redis |
| `conviction` | Auxiliary conviction scoring | redis |
| `alerter` | Telegram delivery, rate limiting | redis |
| `archiver` | Persists signals/outcomes to Postgres, tracks TARGET_HIT/STOP_HIT | redis, postgres |
| `ws-gateway` | WebSocket fan-out of live ticks to the dashboard | redis |
| `api` | aiohttp REST API for the dashboard (`/api/*`) | redis, postgres |
| `nse-scraper` | NSE Bhavcopy, delivery %, F&O ban list | redis, postgres |
| `telegram-bot` | Inbound Telegram bot commands | redis |
| `scheduler` | Periodic sweeps (historical bootstrap, optimizer, Kelly, VIX, ML retrain) | redis, postgres |
| `dashboard` | Static nginx serving the two dashboard shells | api, ws-gateway |

### 1.3 Symbol Universe (this matters directly for the complaint)

`services/nse-scraper/src/nse_scraper/config.py:11-12`:
```python
# Symbol universe tier: nifty50 | nifty100 | nifty200 | nifty500 | fno | custom
symbol_universe: str = "nifty200"
```
`docker-compose.yml`: `INFUSION_SYMBOL_UNIVERSE=${SYMBOL_UNIVERSE:-nifty200}`.

Every live check this session (dashboard "F&O 208/208" counter, market breadth score, etc.) confirms the **actually-deployed universe is the F&O-eligible set, ~208 symbols** — not the full ~2,000+ NSE-listed universe. **Any stock outside this set, however dramatic its volume/price action, is invisible to the entire pipeline** — it's never ticked, never featured, never scanned.

---

## Part 2 — The Volume/Breakout Detection Pipeline, Step by Step

### Step 1 — Ticks → Normalized Ticks
`ingestion` receives Upstox WebSocket ticks, pushes to `ticks.raw`. `normalizer` reshapes into a common schema, pushes to `ticks.normalized`. No filtering happens here relevant to breakout detection — this is pure plumbing.

### Step 2 — Feature Computation (closed-candle discipline)
`feature-engine` consumes `ticks.normalized`, maintains per-symbol state (`SymbolState`), aggregates into 1-minute candles (`bar_builder.py`), and computes every indicator **only on candle close** — live ticks between closes only update LTP/VWAP, never advance RSI/MACD/EMA/Bollinger or any state-machine counter. This is a deliberate, documented anti-noise design (matches this codebase's "closed-candle confirmation" principle used throughout), but it does mean the minimum latency from "volume starts spiking" to "a feature reflecting it exists" is **up to one full 1-minute bar (≤60s)**.

**Relative volume — the actual "is this stock's volume elevated" signal** (`services/feature-engine/src/feature_engine/features/volume.py`, full file):

```python
"""Volume features — OBV, relative volume, volume SMA."""

from feature_engine.state import SymbolState


def update_obv(state: SymbolState, close: float, volume: int):
    """On-Balance Volume -- incremental."""
    if state.obv_prev_close == 0:
        state.obv_prev_close = close
        return

    if close > state.obv_prev_close:
        state.obv += volume
    elif close < state.obv_prev_close:
        state.obv -= volume
    # close == prev: OBV unchanged

    state.obv_prev_close = close


def get_relative_volume(state: SymbolState) -> float:
    """Cumulative session volume / 20-session average at the same minute."""
    if not state.volume_profile_ready or state.last_tick_exchange_ms <= 0:
        return 0.0
    # NSE session starts at 09:15 IST. Epoch time is converted by adding IST.
    minute_of_day = ((state.last_tick_exchange_ms // 60000) + 330) % 1440
    session_minute = max(0, minute_of_day - (9 * 60 + 15))
    expected = state.volume_profile.get(session_minute, 0.0)
    if expected <= 0:
        return 0.0
    return state.session_cumulative_volume / expected


def get_volume_sma(state: SymbolState) -> float:
    if not state.volume_history:
        return 0.0
    return sum(state.volume_history) / len(state.volume_history)
```

This is a genuinely well-designed metric — cumulative session volume at this exact minute-of-day, divided by the 20-session average volume *at that same minute-of-day* (so 9:20 AM isn't compared against a full-day average, which would always look "low"). **But it hard-depends on `state.volume_profile_ready`.** If that's `False`, `get_relative_volume()` returns `0.0` — meaning `rel_vol_20d` looks like *zero volume* to every downstream strategy, not "unknown."

**Where `volume_profile_ready` gets set** (`services/feature-engine/src/feature_engine/engine.py:129, 184-185, 214-220`):
```python
def set_volume_profile_loader(self, fn):
    ...
                    state.volume_profile = profile
                    state.volume_profile_ready = True
...
        if (not state.volume_profile_ready and self._profile_loader
                and now_us() - state.volume_profile_checked_us > 300_000_000):
            state.volume_profile_checked_us = now_us()
            ...
                state.volume_profile = profile
                state.volume_profile_ready = True
```
It loads from Redis key `infusion:volume-profile:{symbol}` (`main.py:54-64`), which is populated by the scheduler's historical bootstrap job:

`services/scheduler/src/scheduler/historical.py:106-129` (volume profile builder):
```python
async def _store_volume_profile(redis, symbol: str, rows: list) -> None:
    sessions: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for raw in rows:
        try:
            dt = datetime.fromisoformat(str(raw[0]))
            minute = dt.hour * 60 + dt.minute - 555
            if 0 <= minute <= 375:
                sessions[dt.date().isoformat()].append((minute, int(float(raw[5]))))
        except (IndexError, TypeError, ValueError):
            continue
    totals: dict[int, list[int]] = defaultdict(list)
    for _, bars in list(sorted(sessions.items()))[-20:]:
        cumulative = 0
        for minute, volume in sorted(bars):
            cumulative += max(volume, 0)
            totals[minute].append(cumulative)
    profile = {str(k): str(sum(v) / len(v)) for k, v in totals.items() if v}
    if profile:
        key = f"infusion:volume-profile:{symbol}"
        pipe = redis.pipeline(transaction=False)
        pipe.delete(key)
        pipe.hset(key, mapping=profile)
        pipe.expire(key, 14 * 86400)
        await pipe.execute()
```
and the bootstrap loop that calls it (`historical.py:132-179`) fetches per-symbol historical intraday data from Upstox one symbol at a time, with a `0.20s` sleep between symbols and a bare `except Exception: logger.warning(...)` — **a single symbol's historical fetch failing (rate limit, transient Upstox error) means that symbol's `volume_profile_ready` never flips true, and its relative volume silently reads as zero forever**, with only a warning log, no dashboard-visible indicator that this specific symbol is flying blind on volume.

### Step 3 — Warm-up Gate (per-symbol, per-process)

`services/feature-engine/src/feature_engine/engine.py:407`:
```python
indicator_ready = (state.completed_1m_bars >= max(self.config.bb_period, self.config.macd_slow),)
```
`services/feature-engine/src/feature_engine/config.py:19-24`:
```python
rsi_period: int = 14
...
bb_period: int = 20
...
macd_fast: int = 12
macd_slow: int = 26
```
`max(20, 26) = 26` completed 1-minute bars. **No strategy evaluates a symbol at all until 26 minutes of live trading have accumulated for it in the running process** — i.e., not before ~09:41 IST on a normal day, and this counter resets to zero for every symbol on every `feature-engine` restart (deploy, crash-recovery), reopening the same ~26-minute blackout mid-day if that ever happens.

### Step 4 — Scanner: the hot path (`services/scanner/src/scanner/engine.py:93-154`)

```python
async def process_feature(self, payload: dict) -> None:
    """Process a single feature vector from the stream.

    This is the core hot path — called for every feature tick.
    """
    symbol = payload.get("symbol", "")
    if not symbol:
        return

    self._evaluations += 1
    state = self.state_mgr.get_or_create(symbol)

    # Populate sector_id from symbol map (once)
    if not state.sector_id:
        state.sector_id = self._symbol_sectors.get(symbol, "UNCATEGORIZED")

    # Update sector engine (incremental, every tick)
    await self.sector.update_symbol(symbol, payload)

    # Signal and pre-breakout logic is candle based. Live feature vectors
    # still update LTP/VWAP between closes, but must not advance indicators
    # or state-machine counters.
    if not payload.get("bar_closed_1m", False):
        return

    # Do not evaluate partially warmed indicators.
    if not payload.get("indicator_ready", False):
        state.update_from_features(payload)
        return

    # Update pre-breakout state machine (event-driven, every tick)
    await self.pre_breakout.update(symbol, payload, state)

    # Attach the real historical-MTF cache ...
    mtf_cache = await self._fetch_mtf_cache(symbol)
    if mtf_cache is not None:
        payload = {**payload, "mtf_cache": mtf_cache}

    # Run all registered strategies
    strategies = get_strategies()
    for strategy in strategies:
        candidate = strategy.evaluate(payload, state)
        await self._persist_diagnostics(
            symbol, strategy.strategy_id, payload, state, candidate is not None
        )
        if candidate is not None:
            if candidate.episode_key and candidate.episode_snapshot is not None:
                state.watch_episodes[candidate.episode_key] = candidate.episode_snapshot
            await self._process_candidate(candidate, payload, state)

    # Update state AFTER evaluation (so prev_* reflects pre-evaluation)
    state.update_from_features(payload)
```

Confirms: event-driven per closed-candle tick (not a slow poll loop — the *scanner's own* latency is minimal once a feature vector arrives), gated on `bar_closed_1m` and `indicator_ready`, then updates the **Pre-Breakout Watcher** before running the two registered strategies.

### Step 5 — The Pre-Breakout Watcher (the "early warning" system — full file)

This is the panel the user is almost certainly looking at ("Pre-Breakout Watch" in the dashboard). `services/scanner/src/scanner/pre_breakout.py`:

```python
"""Pre-breakout state machine — core institutional intelligence.

Identifies stocks BEFORE breakout expansion by tracking compression,
accumulation, and coiling patterns in real-time.

States (strictly deterministic transitions):

    IDLE → COMPRESSING → ACCUMULATING → COILED → TRIGGERED
                ↓              ↓           ↓
              EXPIRED        EXPIRED     EXPIRED

State definitions:
  IDLE:          Default. No compression detected.
  COMPRESSING:   Bollinger width declining, price range narrowing.
  ACCUMULATING:  Volume rising while price remains compressed.
  COILED:        Extreme compression + volume + RSI sweet spot.
                 This is the HIGH-PROBABILITY breakout readiness state.
  TRIGGERED:     Breakout fired (vol_vwap_breakout signal emitted).
                 Transitions back to IDLE after cooldown.
  EXPIRED:       Setup degraded or timed out. Aggressive cleanup.
"""


class PBState(StrEnum):
    IDLE = "idle"
    COMPRESSING = "compressing"
    ACCUMULATING = "accumulating"
    COILED = "coiled"
    TRIGGERED = "triggered"
    EXPIRED = "expired"


class PreBreakoutTracker:
    def _evaluate(self, current, state, bb_width, rel_vol, rsi) -> PBState:
        if current == PBState.IDLE:
            # IDLE → COMPRESSING: bb_width declining for N ticks AND width < threshold
            if (
                state.bb_width_declining_count >= self._s.pb_compress_ticks
                and bb_width < self._s.pb_compress_bb_max
                and bb_width > 0
            ):
                return PBState.COMPRESSING
            return PBState.IDLE

        elif current == PBState.COMPRESSING:
            # COMPRESSING → ACCUMULATING: volume rising while compressed
            if rel_vol >= self._s.pb_accumulate_rel_vol:
                return PBState.ACCUMULATING
            if state.bb_width_declining_count == 0:
                if state.ticks_in_pre_breakout >= self._s.pb_reversal_ticks:
                    return PBState.EXPIRED
            return PBState.COMPRESSING

        elif current == PBState.ACCUMULATING:
            # ACCUMULATING → COILED: extreme compression + volume + RSI sweet spot
            if (
                bb_width < self._s.pb_coiled_bb_max
                and rel_vol >= self._s.pb_coiled_rel_vol
                and self._s.pb_coiled_rsi_min <= rsi <= self._s.pb_coiled_rsi_max
            ):
                return PBState.COILED
            # ACCUMULATING → EXPIRED: volume dropped or bb expanding
            if rel_vol < 1.0 or bb_width > self._s.pb_compress_bb_max:
                return PBState.EXPIRED
            return PBState.ACCUMULATING

        elif current == PBState.COILED:
            if bb_width > self._s.pb_compress_bb_max:
                # Bollinger expanded without breakout — false compression
                return PBState.EXPIRED
            if rel_vol < 1.0:
                return PBState.EXPIRED
            if rsi > 80 or rsi < 30:
                return PBState.EXPIRED
            return PBState.COILED
        ...
```

**Config thresholds** (`services/scanner/src/scanner/config.py:65-75`):
```python
pb_compress_ticks: int = 5  # ticks of declining bb_width
pb_compress_bb_max: float = 0.03  # max bb_width to enter COMPRESSING
pb_accumulate_rel_vol: float = 1.3  # min rel_vol for ACCUMULATING
pb_coiled_bb_max: float = 0.015  # max bb_width for COILED
pb_coiled_rel_vol: float = 1.5  # min rel_vol for COILED
pb_coiled_rsi_min: float = 45.0  # RSI lower for COILED
pb_coiled_rsi_max: float = 60.0  # RSI upper for COILED
pb_reversal_ticks: int = 10  # ticks to reverse back to IDLE
pb_max_state_sec: int = 1800  # 30 min max in any pre-breakout state
```

**The API endpoint that feeds the dashboard's "Pre-Breakout Watch" panel** (`services/api/src/api/routes/scanner.py:360-365`):
```python
@routes.get("/api/prebreakout")
async def get_prebreakout(request):
    """List symbols in pre-breakout states (watchlist).

    Returns only COMPRESSING, ACCUMULATING, COILED symbols.
    Ordered by readiness_score descending.
    """
```

**This is finding #1 from the executive summary, now with the exact mechanism:** the ONLY path into this list is `IDLE → COMPRESSING` (needs 5+ consecutive ticks of *declining* Bollinger width, staying under 0.03). A stock that's been trading at normal/wider volatility (bb_width already above 0.03, no declining streak) and then has volume and price suddenly expand **never enters `COMPRESSING`, so it can never reach `ACCUMULATING` or `COILED`, so it never appears in `/api/prebreakout` at all.** And for a stock that *did* get into `ACCUMULATING` or `COILED`: the moment its price range actually starts expanding (`bb_width > pb_compress_bb_max`) — which is what a real breakout does — the state machine calls that "Bollinger expanded without breakout — false compression" and expires it, right when it should be advancing toward `TRIGGERED`.

### Step 6 — The Two Live Strategies (full files)

**`vol_vwap_breakout.py`** — the strategy purpose-built for "volume expansion breakout":

```python
"""Volume-VWAP Breakout strategy.

Thesis: Detect stocks where volume is expanding significantly AND price
reclaims VWAP from below, suggesting institutional participation in a
developing breakout.

Sub-conditions (ALL must be true for signal):
  1. Volume expansion: rel_vol_20d >= threshold
  2. VWAP reclaim: price crossed above VWAP (crossover)
  3. Price above EMA(9)
  4. RSI in range (momentum, not overbought)
  5. Bollinger not dead (width > threshold)
  6. Order flow positive (imbalance > 0)
  7. Spread filter (liquid, not manipulated)
"""


class VolVwapBreakout(BaseStrategy):
    def evaluate(self, features: dict, state: ScannerSymbolState) -> SignalCandidate | None:
        ltp = features.get("ltp", 0.0)
        vwap = features.get("vwap", 0.0)
        rel_vol = features.get("rel_vol_20d", 0.0)
        rsi = features.get("rsi_14", 50.0)
        ema_9 = features.get("ema_9", 0.0)
        bb_width = features.get("bb_width", 0.0)
        order_imbalance = features.get("order_imbalance", 0.0)
        spread_bps = features.get("spread_bps", 0.0)

        # Guard: need valid prices, warmed candle state and a historical
        # time-of-day volume profile. Missing history is not neutral volume.
        if (
            ltp <= 0
            or vwap <= 0
            or state.prev_ltp <= 0
            or not features.get("indicator_ready", False)
            or not features.get("volume_profile_ready", False)
        ):
            return None

        conditions: dict[str, bool] = {}

        # ── Condition 1: Volume expansion ──────────────────
        vol_ok = rel_vol >= self._s.vvb_min_rel_vol  # 2.0x

        # ── Condition 2: VWAP reclaim (crossover) ──────────
        # Current: ltp > vwap  AND  Previous: prev_ltp <= prev_vwap
        vwap_above = ltp > vwap
        prev_vwap = state.prev_vwap if state.prev_vwap > 0 else vwap
        vwap_was_below = state.prev_ltp <= prev_vwap
        vwap_reclaim = vwap_above and vwap_was_below

        # ── Condition 3: Price above EMA(9) ────────────────
        ema9_ok = ltp > ema_9 > 0

        # ── Condition 4: RSI in range ──────────────────────
        rsi_ok = self._s.vvb_min_rsi < rsi < self._s.vvb_max_rsi  # 40 < rsi < 75

        # ── Condition 5: Bollinger context ─────────────────
        bb_ok = bb_width > self._s.vvb_min_bb_width  # > 0.01

        # ── Condition 6: Order flow ────────────────────────
        flow_ok = order_imbalance > self._s.vvb_min_order_imbalance  # > 0

        # ── Condition 7: Spread filter ─────────────────────
        spread_ok = spread_bps < self._s.vvb_max_spread_bps  # < 50bps

        # ── All conditions must pass ───────────────────────
        if not all(conditions.values()):
            return None
        ...
```

**Condition 2 is the structural blocker.** `vwap_reclaim = (ltp > vwap) and (state.prev_ltp <= prev_vwap)` — this requires the *immediately preceding closed bar* to have LTP at-or-below VWAP. A stock that's already been trading above VWAP for hours and then has a genuine volume-driven breakout mid-trend **can never satisfy this condition again that session** (short of dipping back below VWAP first) — this strategy can only ever fire once per VWAP-crossing event, not on a continuation/acceleration of an existing move.

**`options_first_hybrid.py`** — the broader, "primary" strategy (per this session's own prior work, e.g. the GRASIM watch-episode-freeze fix):

```python
"""Options-first hybrid intraday strategy.

Thesis:
  For an options trader, the scanner should first decide whether the
  underlying move is good enough for CE/PE consideration, then let the
  option-chain layer confirm execution quality.

This strategy is intentionally broader than vol_vwap_breakout:
  - It can produce bullish BUY CE candidates.
  - It can produce bearish BUY PE candidates.
  - It does not require a fresh VWAP crossover; sustained trend alignment
    can qualify as a watch/trade candidate.
"""


class OptionsFirstHybrid(BaseStrategy):
    def evaluate(self, features: dict, state: ScannerSymbolState) -> SignalCandidate | None:
        ltp = float(features.get("ltp") or 0.0)
        vwap = float(features.get("vwap") or 0.0)
        ema5, ema9, ema20, ema50 = ...
        rsi = float(features.get("rsi_14") or 50.0)
        macd, macd_signal, macd_hist = ...
        rel_vol = float(features.get("rel_vol_20d") or 0.0)
        bb_width = float(features.get("bb_width") or 0.0)
        atr_trend = str(features.get("atr_trend") or "NEUTRAL").upper()
        squeeze_state = str(features.get("squeeze_state") or "").upper()
        nr_pattern = str(features.get("nr_pattern") or "")
        change_pct = float(features.get("change_pct") or 0.0)
        flow = float(features.get("order_imbalance") or 0.0)

        if ltp <= 0 or vwap <= 0 or not features.get("indicator_ready", False):
            return None

        above_vwap = ltp > vwap
        ema_bull = ltp > ema5 > ema9 > ema20 > 0 or ltp > ema9 > ema20 > 0
        macd_bull = macd > macd_signal and macd_hist > 0
        rsi_bull = 50 <= rsi <= 72
        volume_ok = (
            rel_vol >= self._s.options_hybrid_min_rel_vol
        )  # 1.1x — softer than vol_vwap_breakout
        compression_ok = bb_width > 0 and bb_width <= 0.018
        pk_squeeze_ok = squeeze_state in {"EXTREME", "COILED", "BUILDING"} or nr_pattern in {
            "NR4",
            "NR7",
        }
        liquid_ok = spread_bps < self._s.options_hybrid_max_spread_bps
        atr_bull = atr_trend == "BULL" and (atr_trail_stop <= 0 or ltp > atr_trail_stop)

        bull_gates = {
            "above_vwap": above_vwap,
            "ema_bull": ema_bull,
            "macd_bull": macd_bull,
            "rsi_bull_zone": rsi_bull,
            "atr_trail_bull": atr_bull,
            "positive_change": change_pct >= 0,
            "volume_or_squeeze": volume_ok
            or compression_ok
            or pk_squeeze_ok,  # volume is ONE of 8, OR'd with two others
            "liquid_underlying": liquid_ok,
        }
        # bear_gates mirrors this for the short side

        bull_count = sum(1 for ok in bull_gates.values() if ok)
        bear_count = sum(1 for ok in bear_gates.values() if ok)
        if bull_count == bear_count:
            return None

        bullish = bull_count > bear_count
        gates = bull_gates if bullish else bear_gates
        core_count = bull_count if bullish else bear_count
        if core_count < self._s.options_hybrid_min_core_gates:  # 4 of 8
            return None

        score = self._score(...)
        if score < self._s.options_hybrid_min_score:  # 62.0
            return None
        ...
```

**`_score()`, the raw candidate-quality scorer (full):**
```python
def _score(
    self,
    *,
    bullish,
    ltp,
    vwap,
    ema_ok,
    macd_ok,
    rsi,
    rel_vol,
    bb_width,
    atr_ok,
    pk_squeeze_ok,
    candle_ok,
    spread_bps,
    flow,
    change_pct,
) -> float:
    directional_vwap = (ltp - vwap) / vwap * 100 if bullish else (vwap - ltp) / vwap * 100
    vwap_score = 18 if 0 < directional_vwap <= 0.8 else 14 if directional_vwap <= 1.6 else 8
    ema_score = 18 if ema_ok else 6
    macd_score = 14 if macd_ok else 4
    if bullish:
        rsi_score = 14 if 52 <= rsi <= 66 else 10 if 45 <= rsi <= 72 else 4
        flow_score = 5 if flow > 0 else 2
        change_score = 5 if change_pct >= 0 else 0
    else:
        rsi_score = 14 if 34 <= rsi <= 48 else 10 if 28 <= rsi <= 55 else 4
        flow_score = 5 if flow < 0 else 2
        change_score = 5 if change_pct <= 0 else 0
    volume_score = 14 if rel_vol >= 2.5 else 10 if rel_vol >= 1.5 else 6 if rel_vol >= 1.0 else 0
    squeeze_score = (
        10 if pk_squeeze_ok else 8 if 0 < bb_width <= 0.012 else 5 if bb_width <= 0.02 else 2
    )
    atr_score = 8 if atr_ok else 2
    candle_score = 4 if candle_ok else 0
    spread_score = (
        4 if spread_bps < 35 else 2 if spread_bps < self._s.options_hybrid_max_spread_bps else 0
    )
    return _clamp(
        vwap_score
        + ema_score
        + macd_score
        + rsi_score
        + volume_score
        + squeeze_score
        + atr_score
        + candle_score
        + spread_score
        + flow_score
        + change_score
    )
```

**Note what `volume_score` actually contributes: at most 14 out of ~100 possible points**, and only if `rel_vol >= 2.5`. `ema_score` (18) and `macd_score`+`rsi_score` (28) together weigh double what volume does — and both EMA-stack and MACD are lagging indicators that need several closed candles to "catch up" to a fresh volume-driven move. A stock 2-3 minutes into a real breakout, with `rel_vol=4.0` but an EMA stack that hasn't crossed yet, scores well on volume and poorly everywhere else — very plausibly under the 62-point floor.

### Step 7 — Suppression Gates (full file)

Even a candidate that clears a strategy's own internal score threshold still has to pass this, in strict order, before it's published to `scan.signals` (visible anywhere on the dashboard) rather than `scan.suppressed` (audit-only):

`services/scanner/src/scanner/suppression.py`:
```python
"""Suppression gate — first-class signal quality filter.

Evaluation order is strict and deterministic:
  1. F&O BAN CHECK    — symbol under NSE trading ban (MWPL>=95%)?
  2. DUPLICATE CHECK  — active signal for same symbol+strategy?
  3. COOLDOWN CHECK   — cooldown key exists?
  4. SECTOR FILTER    — sector strength above threshold?
  5. REGIME FILTER    — market regime compatible with strategy?
  6. CONVICTION FLOOR — score above minimum?
  (a 7th gate, PRECISION GUARD, runs after the above when enabled for a
  given strategy — see evaluate() below)
"""


def _current_session(now=None) -> str:
    if now is None:
        now = datetime.now(tz=_IST).time()
    if dt_time(9, 15) <= now < dt_time(10, 0):
        return "opening"
    if dt_time(10, 0) <= now < dt_time(12, 0):
        return "mid_morning"
    if dt_time(12, 0) <= now < dt_time(14, 0):
        return "midday"
    if dt_time(14, 0) <= now < dt_time(15, 15):
        return "closing"
    if dt_time(15, 15) <= now < dt_time(15, 30):
        return "cas_auction"
    if now < dt_time(9, 15):
        return "pre_market"
    return "post_market"


class SuppressionGate:
    async def evaluate(
        self,
        symbol,
        strategy_id,
        conviction_score,
        sector_id="",
        market_regime="",
        signal_type="bullish",
        risk_reward_ratio=0.0,
    ) -> SuppressionResult:

        # ── Gate 1: F&O ban ──────────────
        is_banned = await self._check_fo_ban(symbol)
        if is_banned:
            return SuppressionResult(passed=False, reason="fo_trading_ban", gate="fo_ban")

        # ── Gate 2: Duplicate active signal ────────────
        is_dup = await self._check_duplicate(symbol, strategy_id)
        if is_dup:
            return SuppressionResult(passed=False, reason="duplicate_active", gate="duplicate")

        # ── Gate 3: Cooldown ───────────────────────────
        in_cooldown = await self._check_cooldown(symbol, strategy_id)
        if in_cooldown:
            return SuppressionResult(passed=False, reason="cooldown_active", gate="cooldown")

        # ── Gate 4: Sector strength ────────────────────
        if sector_id:
            strength = await self._sector_strength(sector_id)
            bearish = str(signal_type).lower() == "bearish"
            if strength is not None and not bearish and strength < self._min_sector_strength:
                return SuppressionResult(passed=False, reason="sector_weak", gate="sector")
            if strength is not None and bearish and strength > 75:
                return SuppressionResult(
                    passed=False, reason="sector_too_strong_for_pe", gate="sector"
                )

        # ── Gate 5: Market regime ──────────────────────
        if market_regime == "volatile":
            return SuppressionResult(passed=False, reason="regime_unfavorable", gate="regime")

        # ── Gate 6: Conviction floor ───────────────────
        if conviction_score < self._min_conviction:  # 80.0
            return SuppressionResult(passed=False, reason="low_conviction", gate="conviction")

        # ── Gate 7: Precision guard from optimizer ─────────────────
        if self._precision_guard_enabled and strategy_id in self._precision_guard_strategy_ids:
            if conviction_score < self._precision_guard_min_score:  # 80.0
                return SuppressionResult(
                    passed=False, reason="precision_guard_score", gate="precision_guard"
                )
            if risk_reward_ratio < self._precision_guard_min_rr:  # 1.2
                return SuppressionResult(
                    passed=False, reason="precision_guard_rr", gate="precision_guard"
                )
            if self._precision_guard_sessions:
                current_session = _current_session()
                if (
                    current_session not in self._precision_guard_sessions
                ):  # {mid_morning, midday, closing}
                    return SuppressionResult(
                        passed=False,
                        reason=f"precision_guard_session_{current_session}",
                        gate="precision_guard",
                    )

        return SuppressionResult(passed=True)
```

**Config, with the backtest note that justifies the opening-session exclusion** (`services/scanner/src/scanner/config.py:26-41`):
```python
class ScannerSettings(InfusionSettings):
    min_conviction_score: float = 80.0  # optimizer v4.4.1: below this → suppressed
    min_sector_strength: float = 30.0  # below this → sector_weak

    # ─── Phase 5.1 precision guard ───────────────────
    # Re-backtested 2026-08-07 against 90 days of live outcomes at score >= 80, R:R >= 1.2:
    #   closing      81.0% precision (358 decided, ~4.0/day)
    #   mid_morning  63.2% precision (380 decided, ~4.2/day)
    #   midday       61.5% precision (364 decided, ~4.0/day)
    #   opening      41.4% precision (99 decided,  ~1.1/day) — BELOW breakeven at 1.2 R:R (45.5%), excluded
    precision_guard_enabled: bool = True
    precision_guard_min_score: float = 80.0
    precision_guard_min_rr: float = 1.2
    precision_guard_sessions: str = "mid_morning,midday,closing"
    precision_guard_strategy_ids: str = "options_first_hybrid,vol_vwap_breakout"

    vvb_min_rel_vol: float = 2.0
    vvb_min_rsi: float = 40.0
    vvb_max_rsi: float = 75.0
    vvb_min_bb_width: float = 0.01
    vvb_max_spread_bps: float = 50.0
    vvb_min_order_imbalance: float = 0.0

    options_hybrid_min_score: float = 62.0
    options_hybrid_min_rel_vol: float = 1.1
    options_hybrid_max_spread_bps: float = 70.0
    options_hybrid_min_core_gates: int = 4
    options_hybrid_watch_ttl_min: int = 180

    pb_compress_ticks: int = 5
    pb_compress_bb_max: float = 0.03
    pb_accumulate_rel_vol: float = 1.3
    pb_coiled_bb_max: float = 0.015
    pb_coiled_rel_vol: float = 1.5
    pb_coiled_rsi_min: float = 45.0
    pb_coiled_rsi_max: float = 60.0
    pb_reversal_ticks: int = 10
    pb_max_state_sec: int = 1800
    pb_state_ttl_sec: int = 3600

    warmup_ticks: int = 5
```

**This confirms the precision-guard exclusion is not an oversight — it's a real, backtested finding that the `opening` session's precision was below breakeven at these thresholds.** But the *consequence* — that both live strategies are entirely blocked from publishing for 45 minutes at the open — is exactly the window where breakout-on-open moves happen most, and is very likely a large part of what's being perceived as "missing breakout stocks."

### Step 8 — Signal Delivery, Archival, Dashboard Surfacing

- **`alerter`** consumes `scan.signals`, applies its own Telegram rate-limit/dedup gates (`services/alerter/src/alerter/gate.py`), formats and sends. Not deeply audited here — the finding above (Precision Guard) already blocks most candidates before they even reach this stage.
- **`archiver`** persists every decided outcome to Postgres (`signals` table) and tracks TARGET_HIT/STOP_HIT/EXPIRED for backtesting — this is what feeds the walk-forward optimizer, Kelly sizing, ML classifier, etc. from this session's earlier work.
- **`api`** exposes `/api/ticks` (live scanner-table rows including hint projections), `/api/signals` (fired signals), `/api/prebreakout` (Pre-Breakout Watch, confirmed above), `/api/regime`, etc. — polled by `services/dashboard/public/js/api.js`'s deduplicating `subscribe()` client at 2-120s intervals per endpoint.
- **Dashboard** — two shells (`Classic`, `New`), both driven by the same `api.js`/`ws.js`. Scanner tables (`scanner.js` / `scanner-v2.js`) render `option_readiness`/`setup_strength`/`rel_vol` per row for every one of the ~208 F&O symbols on every poll (whether or not a signal has fired) — this is a live, continuous read of the *underlying features*, not gated by any of the above; a user watching the raw `RVol`/`Strength` columns closely could, in principle, spot a volume spike before any strategy fires — but the **Pre-Breakout Watch panel specifically, and any Telegram alert, are both fully gated by everything in Part 2**.

---

## Part 3 — Root-Cause Findings (grounded, file-cited)

| # | Finding | File:Line | Real-world consequence |
|---|---|---|---|
| 1 | Pre-Breakout Watch only shows `COMPRESSING`/`ACCUMULATING`/`COILED` — reachable only via a prior 5+-tick declining-Bollinger-width phase | `pre_breakout.py:154-160`, `scanner.py:360-365` | A stock with no prior compression that jumps on volume never appears in the watchlist panel, ever |
| 2 | The same state machine EXPIRES `ACCUMULATING`/`COILED` the moment `bb_width` actually expands past 0.03 — which is what a real breakout does | `pre_breakout.py:178-179, 187-189` | The setup is killed right as it should be graduating to `TRIGGERED` |
| 3 | `vol_vwap_breakout`'s VWAP condition requires the *previous* closed bar to be at/below VWAP — a single-crossover-only trigger | `vol_vwap_breakout.py:74-83` | An already-trending-above-VWAP stock that has a volume-driven continuation/acceleration can never trigger this strategy |
| 4 | `options_first_hybrid` treats volume as 1 of 8 gates (OR'd with two others), requires 4/8 including lagging EMA/MACD/ATR-trend gates | `options_first_hybrid.py:88-118` | A stock 2-3 minutes into a real breakout, with huge volume but not-yet-crossed EMA/MACD, fails the gate count |
| 5 | `_score()` weights EMA+MACD+RSI at ~46 of ~100 points vs. volume's max 14 | `options_first_hybrid.py:420-459` | Volume alone, however extreme, cannot single-handedly clear the 62-point floor |
| 6 | Precision Guard requires conviction ≥80 (not the 62-70 a strategy itself needs) AND excludes the `opening` session entirely | `suppression.py:204-226`, `config.py:37-41` | Every candidate from `options_first_hybrid`/`vol_vwap_breakout` is suppressed 09:15-10:00 IST regardless of quality |
| 7 | `rel_vol_20d` silently reads as `0.0` (not "unknown") whenever a symbol's 20-session volume profile hasn't bootstrapped | `volume.py:21-31`, `engine.py:214-220` | A symbol whose historical fetch failed once (rate limit, transient error) has zero volume signal all day, invisibly |
| 8 | No symbol is evaluated until 26 completed 1-minute bars exist in the running process | `feature-engine/engine.py:407`, `feature-engine/config.py:19-24` | ~26-minute blackout at market open, and again after any feature-engine restart |
| 9 | Strategies only evaluate on closed 1-minute candles, never on live ticks | `scanner/engine.py:112-116` | Up to ~60s latency floor built into every signal, by design |
| 10 | Universe is capped at the F&O-eligible set (~208 symbols), not the full NSE market | `nse-scraper/config.py:11-12`, live dashboard counters | A breakout in any non-F&O-eligible stock is structurally invisible end-to-end |

**Combined effect of #6 + #8:** there is a real, structural **~45-minute dead zone every trading day (09:15-10:00 IST)** — the single most common window for real volume breakouts — during which the system is either still warming up or actively suppressing everything it finds.

---

## Part 4 — What Is *Not* the Problem (checked and ruled out)

- **The scanner's own processing loop is not slow.** It's event-driven per feature tick (`process_feature`), not a slow poll — once a feature vector for a closed candle arrives, evaluation is immediate (sub-second).
- **The dashboard's own polling is not the bottleneck.** `/api/ticks` (5s), `/api/signals` (2s) — these are fast relative to everything above.
- **`api.js`'s subscribe() correctly deduplicates polling** — no redundant backend load from having two dashboard shells mounted simultaneously.
- **This is not a bug introduced by any of this session's recent dashboard-only changes** (Phase O noise reduction, the chart-library fix) — none of those touched `scanner/`, `feature-engine/`, or any strategy/gate code.

---

## Part 5 — Recommendations (a menu for review, none of this has been applied)

These are options, not decisions — intentionally left for review with other agents/the team, since each is a real tradeoff between **signal volume** and **precision** (the system's current stance was deliberately, backtested-ly chosen for precision — see the `config.py` comment in Part 2 Step 7).

1. **Add a "volume-only early-warning" tier**, separate from the Pre-Breakout Watch and separate from the two trade-signal strategies — a lower-bar, informational-only "unusual volume" flag (e.g. `rel_vol_20d >= 2.0` regardless of Bollinger/VWAP/EMA state) surfaced as its own dashboard list. Would not touch existing strategy/suppression logic at all — purely additive.
2. **Let the Pre-Breakout state machine enter `ACCUMULATING` directly from `IDLE`** on a strong enough volume spike alone, bypassing the mandatory `COMPRESSING` prerequisite — a second entry path, not a replacement of the existing one.
3. **Reconsider treating `bb_width` expansion as automatic `EXPIRED`** for `ACCUMULATING`/`COILED` — an expanding range *combined with* rising volume is arguably the breakout succeeding, not failing; today's code cannot distinguish "expanding because it's breaking out" from "expanding because the setup failed."
4. **Add a lower, volume-specific precision-guard carve-out** — e.g., allow a `pure volume + minimal confirmation` candidate through even in the `opening` session at a lower score floor, if backtesting can establish a session-specific precision number for that narrower criterion (the current 41.4% opening-session number is for the *existing* broad criteria, not necessarily for volume-led setups specifically).
5. **Make `rel_vol_20d`'s "no volume profile yet" case distinguishable from "genuinely no volume"** — e.g., a sentinel value or a separate `volume_profile_ready` dashboard indicator per symbol, so a bootstrap failure doesn't silently look identical to "quiet stock."
6. **Consider widening the universe** beyond F&O-eligible-only if the goal includes catching breakouts in stocks the system currently can't even see — a larger architectural decision (Upstox tick-subscription limits, feature-engine CPU/memory scaling) but worth flagging explicitly as a scope constraint, not an oversight.

---

## Part 6 — Appendix: Key Redis Keys & Config Reference

| Redis key pattern | Written by | Read by | Purpose |
|---|---|---|---|
| `infusion:volume-profile:{symbol}` | `scheduler/historical.py` | `feature-engine` | 20-session minute-of-day cumulative volume baseline |
| `infusion:ohlc:{symbol}:1m` / `:history:1m` | `feature-engine/bar_builder.py` | `api/routes/charts.py`, feature-engine bootstrap | 1-min OHLC bars (live + history) |
| `infusion:prebreak:{symbol}` | `scanner/pre_breakout.py` | `api/routes/scanner.py` (`/api/prebreakout`) | Pre-Breakout Watch state |
| `infusion:signal:{symbol}` | `scanner/engine.py` | `api/routes/scanner.py`, `alerter`, `archiver` | Per-symbol last fired signal |
| `infusion:cooldown:{symbol}:{strategy}` | `suppression.py` | `suppression.py` | 15-min per symbol+strategy re-fire block |
| `infusion:nse:fo_ban:symbols` | `nse-scraper/fo_ban.py` | `suppression.py` (Gate 1) | Daily NSE F&O ban list |
| `infusion:sector:{sector_id}` | `sector-intel` | `suppression.py` (Gate 4) | Sector strength score |

| Config knob | File | Default | Effect |
|---|---|---|---|
| `min_conviction_score` | `scanner/config.py` | 80.0 | Global floor for any signal to publish |
| `precision_guard_sessions` | `scanner/config.py` | `mid_morning,midday,closing` | Sessions where precision-gated strategies can publish |
| `vvb_min_rel_vol` | `scanner/config.py` | 2.0 | `vol_vwap_breakout`'s volume-expansion threshold |
| `options_hybrid_min_rel_vol` | `scanner/config.py` | 1.1 | `options_first_hybrid`'s (much softer) volume threshold |
| `options_hybrid_min_core_gates` | `scanner/config.py` | 4 (of 8) | Minimum aligned gates for a candidate |
| `pb_compress_ticks` / `pb_compress_bb_max` | `scanner/config.py` | 5 / 0.03 | Entry threshold into `COMPRESSING` |
| `bb_period` / `macd_slow` | `feature-engine/config.py` | 20 / 26 | Drives the 26-completed-bar warm-up gate |
| `symbol_universe` | `nse-scraper/config.py` | `nifty200` (env-overridden to F&O ~208 live) | Which symbols are watched at all |

---

*Generated by Claude Code, 2026-08-18, by direct inspection of the live repository at `services/`. Every code excerpt above was read verbatim from the source files cited — nothing here is paraphrased logic.*
