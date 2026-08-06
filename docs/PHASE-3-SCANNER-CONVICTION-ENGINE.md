# PHASE 3 — SCANNER & CONVICTION ENGINE

> The intelligence core of Infusion AI Screener.
> This is an institutional opportunity ranking engine, not an indicator crossover alert machine.
> All decisions conform to [Global Architecture Constraints](./GLOBAL-ARCHITECTURE-CONSTRAINTS.md).

---

## 1. Scanner Engine Architecture

### 1.1 Design Philosophy

The scanner does NOT iterate over all 700 symbols on every tick. It is **event-driven**: a symbol is evaluated only when its feature vector updates. A strategy is invoked only if the updated features overlap with that strategy's declared dependencies.

```
RETAIL SCANNER (what we avoid):
  every 1 second:
    for each symbol in universe:        ← 700 iterations
      for each strategy in active:      ← 6 strategies
        compute all indicators           ← redundant
        check conditions                 ← 4200 evaluations/sec
      
  Problem: 4200 evaluations/sec, most yielding nothing. Wasteful.

INFUSION SCANNER (what we build):
  on feature:computed message arrives:
    symbol = message.symbol
    changed_features = message.changed_features   ← delta tracking
    
    for each strategy WHERE strategy.depends_on ∩ changed_features ≠ ∅:
      evaluate(symbol, features)
    
  Result: only strategies whose input features actually changed are invoked.
  At steady state: ~2000-4000 evaluations/sec (not 4200 uniform — concentrated on active stocks).
```

### 1.2 Engine Pipeline

```
feature:computed ──► SCANNER ENGINE
                     │
                     ├── 1. Feature Delta Check
                     │      "Which features changed for this symbol?"
                     │      Compare against previous feature snapshot.
                     │      If no meaningful change (< epsilon) → skip entirely.
                     │
                     ├── 2. Strategy Router
                     │      For each active strategy:
                     │        if strategy.required_features ∩ changed_features:
                     │          schedule for evaluation
                     │
                     ├── 3. Strategy Evaluation
                     │      strategy.evaluate(symbol, full_feature_vector) → RawSignal | None
                     │
                     ├── 4. Cooldown Gate
                     │      Check Redis: infusion:cooldown:{strategy}:{symbol}
                     │      If exists → suppress (already signaled recently)
                     │
                     ├── 5. Suppression Pipeline (section 7)
                     │      8-gate suppression chain
                     │      Any gate can kill the signal
                     │
                     ├── 6. Explanation Builder (section 15)
                     │      Attach structured reasons for signal + suppressions
                     │
                     └── 7. Publish
                            XADD scan:signals {signal + explanation}
                            SET infusion:cooldown:{strategy}:{symbol} EX {ttl}
```

### 1.3 Feature Delta Detection

```
Purpose: avoid redundant strategy evaluation when features haven't meaningfully changed.

Implementation:
  Per-symbol, maintain last_features: dict[str, float] in memory.
  
  On new feature vector:
    changed = []
    for feature_name, new_value in incoming.items():
      old_value = last_features.get(feature_name, None)
      if old_value is None or abs(new_value - old_value) / max(abs(old_value), 1e-9) > epsilon:
        changed.append(feature_name)
    last_features[symbol] = incoming
    return changed

  epsilon thresholds (per feature class):
    price features (ltp, vwap):     0.001  (0.1% change)
    volume features (rel_vol, obv): 0.01   (1% change)
    momentum features (rsi, macd):  0.5    (0.5 point change)
    volatility features (atr, bbw): 0.01   (1% change)

  Effect: during quiet consolidation (exactly when we're watching for pre-breakout),
  features change very slowly → very few strategy evaluations → minimal CPU.
  During breakout: features change rapidly → strategies fire aggressively. 
  The system naturally focuses compute where action is happening.
```

### 1.4 Expected Signal Frequency

```
Target: 10–30 raw signals per trading session (before suppression)
After suppression: 3–10 high-conviction signals per day

Breakdown by strategy (estimated daily, after cooldowns):
  Pre-breakout watchlist entries:       5-15  (these are "watch" not "act")
  Range breakout confirmations:         2-5
  Volume surge:                         3-8
  Momentum shift:                       2-4
  OI buildup:                           1-3
  Sector rotation:                      1-2
  ──────────────────────────────────────────
  Total raw:                            14-37

After suppression pipeline:
  Suppressed by liquidity gate:         3-5
  Suppressed by sector context:         2-4
  Suppressed by market regime:          1-3
  Suppressed by time-of-day:            1-2
  Suppressed by contradiction:          1-2
  ──────────────────────────────────────────
  Surviving signals:                    5-15

After conviction scoring (grade B+ or higher):
  Telegram-worthy (grade A/A+):         1-5 per day
  Dashboard-visible (grade B+):         3-8 per day

This is the target operating envelope. If we're seeing >50 raw signals/day,
the strategy parameters are too loose. If <5, too tight.
Calibration happens via outcome tracking (section 14).
```

---

## 2. Strategy Plugin System

### 2.1 Strategy Interface

```
ScanStrategy (Abstract Base):

  METADATA (declared at class level):
    name: str                    # "pre_breakout", "range_breakout"
    display_name: str            # "Pre-Breakout Compression"
    signal_direction: BULLISH | BEARISH | BOTH
    required_features: set[str]  # {"atr_14", "bb_width", "rel_vol_20d", ...}
    timeframe: TICK | 1MIN | 5MIN | 15MIN
    default_cooldown_sec: int    # 600 (10 min between same-symbol signals)
    default_enabled: bool        # true/false

  CONFIGURATION (loaded from YAML, hot-reloadable):
    params: dict[str, Any]       # strategy-specific parameters
    enabled: bool                # can be toggled without restart
    cooldown_sec: int            # override default
    min_conviction_for_emit: int # only emit if raw signal conviction >= N

  METHODS:
    evaluate(symbol, features) → RawSignal | None
      Pure function. No I/O. No side effects. No Redis calls.
      Returns None if conditions not met.
      Returns RawSignal with:
        - strategy_name
        - symbol
        - direction (BULLISH/BEARISH)
        - conditions_met: list[ConditionResult]
        - raw_strength: float (0-100, strategy's own confidence)
        - metadata: dict (strategy-specific context)

    configure(params: dict) → None
      Called on startup and on YAML config reload.
      Validates parameters. Raises on invalid config.
```

### 2.2 Strategy Registration

```
Strategy discovery: explicit registration, not auto-discovery.

# config/scanners.yaml
strategies:
  pre_breakout:
    enabled: true
    cooldown_sec: 1800         # 30 min (long cooldown — this is a slow setup)
    params:
      atr_percentile_threshold: 20
      bb_squeeze_ratio: 0.75
      min_consolidation_days: 3
      min_delivery_pct: 45
      
  range_breakout:
    enabled: true
    cooldown_sec: 600
    params:
      min_rel_volume: 2.5
      min_range_days: 5
      breakout_margin_pct: 0.5
      require_close_above: true

  volume_surge:
    enabled: true
    cooldown_sec: 300
    params:
      min_rel_volume: 3.0
      min_price_change_pct: 1.0
      require_vwap_above: true

  momentum_shift:
    enabled: false              # disabled — enable when tuned
    cooldown_sec: 900
    params:
      rsi_oversold: 35
      rsi_overbought: 65
      require_macd_cross: true

  oi_buildup:
    enabled: true
    cooldown_sec: 600
    params:
      min_oi_change_pct: 5.0
      min_price_change_pct: 0.5
      
  sector_rotation:
    enabled: true
    cooldown_sec: 3600          # 1 hour — sector rotation is slow
    params:
      min_rs_sigma: 1.5
      require_sector_breadth_above: 0.55

Config reload: when infusion:config:version changes in Redis,
scanner re-reads scanners.yaml, calls strategy.configure(new_params)
for each strategy. No restart needed.
```

### 2.3 ConditionResult — Structured Evaluation

Every strategy returns not just pass/fail, but a list of conditions with individual pass/fail. This is the foundation for explainability.

```
ConditionResult:
  name: str              # "atr_compression"
  passed: bool
  value: float           # actual value observed
  threshold: float       # threshold that was tested
  operator: str          # "<", ">", ">=", "between"
  weight: float          # how much this condition matters (0-1)
  description: str       # "ATR(14) in bottom 18th percentile of 50-day range"

Example for pre_breakout strategy:
  conditions_met = [
    ConditionResult("atr_compression", True, 18.2, 20.0, "<", 0.30, "ATR(14) at 18th pctl of 50d"),
    ConditionResult("bb_squeeze", True, 0.62, 0.75, "<", 0.25, "BB width 62% of 20d avg"),
    ConditionResult("range_tight", True, 4, 3, ">=", 0.20, "4 consecutive days range < 1.5%"),
    ConditionResult("delivery_rising", True, 52.3, 45.0, ">", 0.15, "Delivery at 52.3% vs 45% threshold"),
    ConditionResult("obv_divergence", True, 0.73, 0.0, ">", 0.10, "OBV slope positive while price flat"),
  ]
  raw_strength = sum(c.weight * c.passed for c in conditions) / sum(c.weight for c in conditions) * 100
               = (0.30 + 0.25 + 0.20 + 0.15 + 0.10) / 1.0 * 100 = 100 (all passed)
```

---

## 3. Signal Lifecycle Management

### 3.1 Signal States

```
                    ┌───────────┐
                    │   RAW     │  Strategy fires, conditions met
                    └─────┬─────┘
                          │
                    ┌─────▼─────┐
                    │  GATED    │  Passing through suppression pipeline
                    └─────┬─────┘
                          │
                ┌─────────┼──────────┐
                │                    │
          ┌─────▼─────┐        ┌────▼──────┐
          │ SUPPRESSED│        │ SCORED    │  Conviction score assigned
          └───────────┘        └─────┬─────┘
                                     │
                         ┌───────────┼──────────┐
                         │                      │
                   ┌─────▼─────┐          ┌─────▼─────┐
                   │  RANKED   │          │  FILTERED │  Below min grade
                   │  (active) │          └───────────┘
                   └─────┬─────┘
                         │
                   ┌─────▼─────┐
                   │ DELIVERED │  Sent to Telegram / dashboard
                   └─────┬─────┘
                         │
                   ┌─────▼─────┐
                   │ TRACKING  │  Outcome monitoring (1d, 3d, 5d)
                   └─────┬─────┘
                         │
                   ┌─────▼─────┐
                   │ RESOLVED  │  Outcome labeled (WIN/LOSS/FLAT)
                   └───────────┘
```

### 3.2 Signal Data Model

```
RawSignal (output of strategy.evaluate):
  strategy: str
  symbol: str
  direction: BULLISH | BEARISH
  conditions: list[ConditionResult]
  raw_strength: float (0-100)
  metadata: dict
  timestamp_us: int

ScoredSignal (output of conviction engine):
  raw: RawSignal
  conviction_score: float (0-100)
  conviction_grade: A+ | A | B+ | B | C
  factors: list[ScoringFactor]
  suppressions: list[SuppressionRecord]
  explanation: SignalExplanation
  sector_context: SectorSnapshot
  market_context: MarketSnapshot
  price_at_signal: float
  volume_at_signal: int
  timestamp_us: int

TrackedSignal (persisted for outcome tracking):
  signal_id: UUID
  scored: ScoredSignal
  price_1d: float | None
  price_3d: float | None
  price_5d: float | None
  return_1d_pct: float | None
  return_3d_pct: float | None
  return_5d_pct: float | None
  max_favorable_pct: float | None   # best case within 5 days
  max_adverse_pct: float | None     # worst case within 5 days
  outcome: WIN | LOSS | FLAT | None
  resolved_at: datetime | None
```

### 3.3 Signal Expiry

```
Active signals decay over time. A breakout signal from 2 hours ago
is less actionable than one from 5 minutes ago.

Expiry rules:
  Pre-breakout (watchlist):  expires at EOD (removed from active set)
  Range breakout:            expires after 60 minutes
  Volume surge:              expires after 30 minutes
  Momentum shift:            expires after 45 minutes
  OI buildup:                expires after 120 minutes
  Sector rotation:           expires at EOD

Implementation:
  Active signals stored in Redis ZSET: infusion:signal:active
  Score = conviction_score (for ranking)
  Each signal also has TTL key: infusion:signal:expiry:{signal_id}
  When TTL key expires → Redis keyspace notification → remove from active ZSET

  Dashboard shows only non-expired active signals, sorted by conviction.
```

---

## 4. Pre-Breakout Detection Engine

This is the core edge of the platform. Pre-breakout detection is modeled as a **multi-stage state machine** per symbol, not a single-point condition check.

### 4.1 State Machine

```
                    ┌──────────┐
          ┌────────►│  NEUTRAL │◄────────────────────────────┐
          │         └────┬─────┘                             │
          │              │                                   │
          │              │ Stage 1 conditions met            │
          │              ▼                                   │
          │         ┌──────────────┐                         │
          │         │ COMPRESSING  │  Volatility contracting │
          │         └────┬─────────┘  ATR declining          │
          │              │            BB squeezing           │
          │              │                                   │
          │              │ Stage 2 conditions met            │
          │              ▼                                   │
          │         ┌──────────────┐                         │
          │         │ ACCUMULATING │  Volume drying up       │
          │         └────┬─────────┘  Delivery % rising      │
          │              │            OBV divergence          │
          │              │                                   │
          │              │ Stage 3 conditions met            │
          │              ▼                                   │
          │         ┌──────────────┐                         │
    reset │         │  COILED      │  All stages confirmed   │
    (fail)│         │  (WATCHLIST) │  Emits PRE_BREAKOUT     │
          │         └────┬─────────┘  signal to dashboard    │
          │              │                                   │
          │              │ Trigger conditions                │
          │              ▼                                   │
          │         ┌──────────────┐                         │
          │         │  TRIGGERED   │  Volume spike +         │
          │         │  (BREAKOUT)  │  price expansion        │
          │         └────┬─────────┘  Emits BREAKOUT signal  │
          │              │                                   │
          │              │ Confirmation or failure           │
          │              ▼                                   │
          │    ┌─────────────────────┐                       │
          │    │CONFIRMED │ FAILED  │                        │
          │    │(track)   │(record) │                        │
          │    └──────────┴─────────┘                        │
          │              │                                   │
          └──────────────┴───────────────────────────────────┘
                    reset to NEUTRAL after cooldown
```

### 4.2 Stage 1: Compression Detection

```
Conditions (ALL must be true simultaneously):

1. ATR Contraction
   atr_14_percentile = percentile_rank(atr_14, atr_14_history_50d)
   PASS if atr_14_percentile < 25
   
   Meaning: current ATR is in the bottom 25th percentile of its last 50-day range.
   The stock is moving less than it usually does.

2. Bollinger Squeeze
   bb_width = (upper_band - lower_band) / middle_band
   bb_width_avg_20d = SMA(bb_width, 20)
   bb_squeeze_ratio = bb_width / bb_width_avg_20d
   PASS if bb_squeeze_ratio < 0.80
   
   Meaning: Bollinger Bands are 20% tighter than usual. Price is coiling.

3. Range Contraction
   daily_range_pct = (high - low) / close * 100
   consecutive_narrow_days = count consecutive days where daily_range_pct < 1.5%
   PASS if consecutive_narrow_days >= 3
   
   Meaning: At least 3 consecutive days of narrow range.
   This is the NR (Narrow Range) pattern used by institutional traders.

4. No Premature Expansion
   max_rel_vol_5d = max(relative_volume over last 5 days)
   PASS if max_rel_vol_5d < 2.0
   
   Meaning: no volume spike in last 5 days. The stock hasn't already tried
   to break out and failed. If it has, it's not a clean compression setup.

Transition: NEUTRAL → COMPRESSING when all 4 conditions pass.
Reversion: COMPRESSING → NEUTRAL if any condition fails for 2 consecutive evaluations.
```

### 4.3 Stage 2: Accumulation Evidence

```
Conditions (requires COMPRESSING state + at least 2 of 4 must pass):

1. Volume Drying Up
   avg_vol_5d / avg_vol_20d < 0.70
   PASS if true
   
   Meaning: volume in last 5 days is 30% below 20-day average.
   Low volume during consolidation = nobody is selling. Supply is exhausted.

2. Delivery Percentage Rising
   delivery_pct > delivery_pct_avg_20d * 1.1
   PASS if true
   
   Meaning: a higher percentage of traded volume is being held (not day-traded).
   Rising delivery during consolidation = smart money accumulating.
   
   Source: NSE scraper delivery data (1-day delayed).
   Fallback: if delivery data unavailable, this condition is skipped
   (2 of remaining 3 must pass instead).

3. OBV Positive Divergence
   price_slope_10d = linear_regression_slope(close, 10 days)
   obv_slope_10d = linear_regression_slope(obv, 10 days)
   PASS if price_slope_10d is flat (abs < 0.001) AND obv_slope_10d > 0
   
   Meaning: price is going nowhere but On-Balance Volume is rising.
   Volume is flowing in even though price isn't moving. Accumulation.

4. Bid-Ask Imbalance (if available from full tick data)
   bid_ask_ratio = total_buy_qty / total_sell_qty
   PASS if bid_ask_ratio > 1.3
   
   Meaning: buy-side order book is 30% heavier than sell-side.
   Demand is quietly building. Not visible in price yet.

Transition: COMPRESSING → ACCUMULATING when 2+ conditions pass.
Reversion: ACCUMULATING → COMPRESSING if fewer than 2 conditions pass.
Reversion: ACCUMULATING → NEUTRAL if compression conditions also fail.
```

### 4.4 Stage 3: Coiled (Watchlist Signal)

```
Conditions (requires ACCUMULATING state + level proximity):

1. Level Proximity
   distance_to_resistance = (nearest_resistance - ltp) / ltp * 100
   PASS if distance_to_resistance < 2.0%
   
   Resistance identification:
     - 20-day high
     - 50-day high
     - 52-week high
     - Previous swing high (local maximum within 20 bars)
     - VWAP of high-volume day
   Use the nearest of these levels.

2. Level Strength
   touch_count = number of times price approached resistance (within 0.5%) and rejected
   PASS if touch_count >= 2
   
   Meaning: the level has been tested multiple times. Each test that doesn't
   break through absorbs more supply. Fewer sellers remain at that price.

3. Time in Compression
   days_in_compression = days since entering COMPRESSING state
   PASS if days_in_compression >= 5
   
   Meaning: compression has persisted long enough to be meaningful.
   Very short squeezes (1-2 days) have lower follow-through rates.

Action on COILED:
  Emit PRE_BREAKOUT signal (not a full BREAKOUT — this is a watchlist entry)
  Signal goes through conviction scoring but with a "watchlist" tag
  Dashboard shows it in a "Coiled / Watching" section
  No Telegram alert (too early — alert on trigger only)

  This is the core edge: we've identified the setup BEFORE the move happens.
```

### 4.5 Stage 4: Trigger Detection

```
Once a symbol is in COILED state, a parallel watcher monitors for breakout trigger.
This watcher runs on EVERY tick for coiled symbols (not batched — latency matters here).

Trigger conditions (2 of 3 must fire within the same 5-minute window):

1. Price Breakout
   ltp > nearest_resistance * (1 + breakout_margin_pct / 100)
   AND the candle body (not just wick) closes above resistance
   
   breakout_margin_pct = 0.3% (configurable)
   
   "Body close above" check:
     On 1-minute bar close: if bar_close > resistance → condition met
     We don't trigger on wicks alone. Wick breakouts have ~60% failure rate.

2. Volume Confirmation
   current_rel_volume > 2.5 (configurable)
   
   Measured on the 5-minute bar containing the price breakout.
   Relative to same time-of-day average (not full-day average).
   
   Time-of-day adjustment matters:
     09:15-09:30 naturally has 3-5x average volume (opening auction)
     12:00-13:00 has 0.5x average volume (lunch lull)
     Using raw 20d avg would generate false signals at open and miss real ones at lunch.

3. Sector Confirmation
   sector_breadth > 0.50 AND sector_return > 0.0%
   
   The stock isn't breaking out alone against its sector. The sector
   is at least neutral-to-positive.

On trigger:
  State → TRIGGERED
  Emit BREAKOUT signal with full conviction scoring
  This signal IS eligible for Telegram alert
  Attach the full pre-breakout history (time in compression, accumulation evidence)

On failure (no trigger within 3 trading sessions of entering COILED):
  State → NEUTRAL
  Record as EXPIRED in signal history
  Useful for analysis: "this setup didn't follow through — why?"
```

### 4.6 Pre-Breakout State Storage

```
In-memory dict per symbol (inside scanner service):

pre_breakout_state[symbol] = {
  state: NEUTRAL | COMPRESSING | ACCUMULATING | COILED | TRIGGERED
  entered_at: epoch_us (when current state was entered)
  compression_start: epoch_us
  compression_days: int
  accumulation_conditions: list[str]  # which conditions are passing
  nearest_resistance: float
  resistance_touch_count: int
  coiled_signal_id: UUID | None
}

Memory: ~700 symbols × 256B = ~175KB. Trivial.

Persistence: NOT persisted to Redis or PostgreSQL during market hours.
  On post-market: persist active COMPRESSING/ACCUMULATING/COILED states
  to Redis HASH: infusion:prebreakout:{symbol}
  On next market open: restore from Redis.
  
  If scanner crashes mid-day: states are lost. They rebuild within
  3-5 evaluation cycles (seconds to minutes). Acceptable tradeoff
  for zero persistence overhead in the hot path.
```

---

## 5. Conviction Scoring System

### 5.1 Scoring Architecture

```
RawSignal ──► CONVICTION ENGINE
              │
              ├── 1. Technical Factor    (0-100, weight 0.25)
              ├── 2. Volume Factor       (0-100, weight 0.20)
              ├── 3. Setup Quality       (0-100, weight 0.20)
              ├── 4. Sector Context      (0-100, weight 0.20)
              ├── 5. Market Regime       (0-100, weight 0.10)
              ├── 6. Options Positioning (0-100, weight 0.05)
              │
              ├── Weighted sum → raw_score (0-100)
              │
              ├── Penalty adjustments (section 5.4)
              │
              ├── Score normalization
              │
              └── Grade assignment → A+, A, B+, B, C
```

### 5.2 Factor Computation

#### Factor 1: Technical Score (weight 0.25)

```
Components:
  trend_alignment (0-40):
    EMA stack check: EMA5 > EMA20 > EMA50 (for bullish)
    All aligned:    40
    2 of 3 aligned: 20
    None aligned:   0

  rsi_position (0-20):
    RSI 45-65 (healthy momentum, not overbought): 20
    RSI 30-45 (oversold bounce potential):         15
    RSI 65-75 (strong but risky):                  10
    RSI > 75 or < 30:                              0

  price_vs_vwap (0-20):
    ltp > VWAP and rising:   20
    ltp > VWAP but flat:     12
    ltp < VWAP:              0

  macd_alignment (0-20):
    MACD > signal AND histogram positive AND rising: 20
    MACD > signal:                                   10
    MACD < signal:                                   0

  technical_score = trend_alignment + rsi_position + price_vs_vwap + macd_alignment
```

#### Factor 2: Volume Factor (weight 0.20)

```
Components:
  relative_volume (0-40):
    rel_vol >= 4.0:   40
    rel_vol >= 3.0:   30
    rel_vol >= 2.0:   20
    rel_vol >= 1.5:   10
    rel_vol < 1.5:    0

  volume_trend (0-25):
    vol_3bar_slope > 0 AND each bar > previous: 25
    vol_3bar_slope > 0:                         15
    flat or declining:                          0

  delivery_quality (0-20):
    delivery_pct > 60%:  20
    delivery_pct > 50%:  15
    delivery_pct > 40%:  8
    delivery_pct < 40%:  0
    data unavailable:    10  (neutral — don't penalize missing data)

  obv_direction (0-15):
    OBV rising over 10 bars: 15
    OBV flat:                5
    OBV declining:           0

  volume_score = relative_volume + volume_trend + delivery_quality + obv_direction
```

#### Factor 3: Setup Quality (weight 0.20)

```
This factor measures HOW CLEAN the setup is, not just WHETHER conditions are met.

Components:
  conditions_met_ratio (0-40):
    Based on the strategy's own ConditionResult list.
    ratio = count(passed) / count(total)
    score = ratio * 40

  setup_age (0-20):
    For pre-breakout: days_in_compression
      5-10 days: 20 (ideal — enough to build energy)
      10-20 days: 15 (long consolidation, may lack catalyst)
      3-5 days: 10 (short but valid)
      > 20 days: 5 (may be dead money, not compression)
    
    For other strategies: N/A → defaults to 15

  level_quality (0-20):
    resistance_touch_count >= 4: 20
    resistance_touch_count == 3: 15
    resistance_touch_count == 2: 10
    resistance_touch_count == 1: 5
    no identifiable level:       0

  pattern_cleanliness (0-20):
    Measures noise in the consolidation pattern.
    count_false_breakouts_in_range = bars where ltp briefly exceeded resistance then closed below
    0 false breakouts: 20 (clean pattern)
    1 false breakout:  12 (one headfake)
    2+ false breakouts: 0 (messy, unreliable level)

  setup_score = conditions_met + setup_age + level_quality + pattern_cleanliness
```

#### Factor 4: Sector Context (weight 0.20)

```
Components:
  sector_breadth (0-30):
    breadth > 0.70:  30
    breadth > 0.60:  22
    breadth > 0.50:  15
    breadth > 0.40:  5
    breadth <= 0.40: 0

  sector_rotation (0-25):
    quadrant == LEADING:   25
    quadrant == IMPROVING: 15
    quadrant == WEAKENING: 5
    quadrant == LAGGING:   0

  stock_vs_sector_rs (0-25):
    stock_return_today / sector_return_today (relative strength)
    RS > 2.0: 25 (massively outperforming sector)
    RS > 1.5: 20
    RS > 1.0: 12
    RS < 1.0: 0 (underperforming sector — not good for bullish signal)

  institutional_flow (0-20):
    fii_net > 0 AND dii_net > 0: 20 (both buying)
    fii_net > 0 OR dii_net > 0:  10 (one buying)
    both selling:                 0

  sector_score = breadth + rotation + rs + institutional_flow
```

#### Factor 5: Market Regime (weight 0.10)

```
Market regime is determined by NIFTY 50 state:

  regime = classify(nifty50_features)

  Classification logic:
    TRENDING_UP:
      nifty_ema_20 > nifty_ema_50
      AND nifty_breadth > 0.55
      AND nifty_above_20ema = true
      → score: 100

    RANGE_BOUND:
      nifty within 2% range for 5+ days
      AND nifty_breadth 0.40-0.60
      → score: 50

    TRENDING_DOWN:
      nifty_ema_20 < nifty_ema_50
      AND nifty_breadth < 0.45
      → score: 10

    VOLATILE:
      nifty_atr_14 in top 20th percentile
      AND breadth swinging > 0.15 intraday
      → score: 20

  For bullish signals: use score directly.
  For bearish signals: invert (TRENDING_DOWN = 100, TRENDING_UP = 10).
```

#### Factor 6: Options Positioning (weight 0.05)

```
Only for F&O stocks (is_fno = true). Non-F&O stocks get neutral score (50).

Components:
  pcr_signal (0-40):
    Put-Call Ratio from option chain data (NSE scraper)
    PCR 0.7-1.0:  40  (healthy bullish — moderate put writing)
    PCR 1.0-1.3:  30  (neutral-bullish)
    PCR < 0.5:    10  (excessive call buying — contrarian bearish)
    PCR > 1.5:    10  (excessive put buying — fear)
    data stale:   20  (neutral)

  oi_buildup (0-35):
    Rising OI + Rising price:   35 (long buildup — bullish)
    Rising OI + Falling price:  5  (short buildup — bearish for bullish signals)
    Falling OI + Rising price:  15 (short covering — not fresh demand)
    Falling OI + Falling price: 10 (long unwinding)

  max_pain_distance (0-25):
    ltp near max pain (within 1%):   5  (gravitational pull limits upside)
    ltp 2-5% above max pain:        25  (moved away — bullish)
    ltp > 5% above max pain:        15  (extended, may revert)
    ltp below max pain:             10

  options_score = pcr_signal + oi_buildup + max_pain_distance
```

### 5.3 Weighted Score Assembly

```
raw_score = (
    technical_score * 0.25 +
    volume_score    * 0.20 +
    setup_score     * 0.20 +
    sector_score    * 0.20 +
    regime_score    * 0.10 +
    options_score   * 0.05
)

Weights are configurable in config/conviction_weights.yaml:

weights:
  technical: 0.25
  volume: 0.20
  setup_quality: 0.20
  sector_context: 0.20
  market_regime: 0.10
  options_positioning: 0.05

Constraint: weights must sum to 1.0. Validated on config load.
```

### 5.4 Penalty Adjustments

After raw score, apply sequential penalties. Penalties are multiplicative (not additive) to prevent scores going negative.

```
PENALTY PIPELINE (applied in order):

1. Liquidity Penalty
   avg_daily_turnover = avg(volume * vwap, 20 days)
   if avg_daily_turnover < ₹5 crore:
     raw_score *= 0.50    # heavy penalty — can't trade meaningful size
     penalty_reason = "Low liquidity (turnover ₹{x}Cr vs ₹5Cr min)"
   elif avg_daily_turnover < ₹20 crore:
     raw_score *= 0.80
     penalty_reason = "Moderate liquidity"

2. Spread Penalty
   spread_bps = (best_ask - best_bid) / ltp * 10000
   if spread_bps > 30:
     raw_score *= 0.85
     penalty_reason = "Wide spread ({x} bps)"
   if spread_bps > 50:
     raw_score *= 0.70
     penalty_reason = "Very wide spread ({x} bps)"

3. Time-of-Day Penalty
   minutes_since_open = (now - 09:15) in minutes
   minutes_to_close = (15:30 - now) in minutes
   
   if minutes_since_open < 15:
     raw_score *= 0.70
     penalty_reason = "Opening noise (first 15 min)"
   elif minutes_since_open < 30:
     raw_score *= 0.85
     penalty_reason = "Early session caution"
   
   if 11:30 <= now <= 13:00:
     raw_score *= 0.90
     penalty_reason = "Lunch lull"
   
   if minutes_to_close < 15:
     raw_score *= 0.60
     penalty_reason = "Final 15 min suppression"
   elif minutes_to_close < 30:
     raw_score *= 0.80
     penalty_reason = "Late session damping"

4. Recent False Breakout Penalty
   recent_failures = count signals for this symbol in last 5 sessions where outcome == LOSS
   if recent_failures >= 2:
     raw_score *= 0.70
     penalty_reason = "Recent false breakout history ({x} failures in 5 sessions)"
   elif recent_failures == 1:
     raw_score *= 0.85
     penalty_reason = "1 recent false breakout"

5. Overexposure Penalty
   active_signals_in_sector = count active signals in same sector
   if active_signals_in_sector >= 3:
     raw_score *= 0.80
     penalty_reason = "Sector overexposure ({x} active signals in {sector})"
```

### 5.5 Grade Assignment

```
After penalties:

final_score = round(raw_score, 1)

Grade thresholds:
  A+: final_score >= 82
  A:  final_score >= 68
  B+: final_score >= 55
  B:  final_score >= 40
  C:  final_score < 40

Behavior per grade:
  A+: Telegram alert (immediate) + Dashboard highlight + sound notification
  A:  Telegram alert (if not throttled) + Dashboard visible
  B+: Dashboard visible (not highlighted)
  B:  Dashboard (collapsed section, details on click)
  C:  Suppressed entirely — not shown, not stored in active signals
      Logged to signals table for outcome tracking only.
```

---

## 6. Context-Aware Filtering

### 6.1 Context Model

Every signal is evaluated not in isolation but against a snapshot of the broader context at the moment of firing.

```
MarketContext (assembled once per signal):
  nifty_50:
    return_pct: float            # today's return
    breadth: float               # advance/decline ratio
    regime: TRENDING_UP | RANGE_BOUND | TRENDING_DOWN | VOLATILE
    above_ema_20: bool
    vix: float                   # India VIX (from NSE scraper)
    vix_change_pct: float        # VIX change vs yesterday
  
  sector:
    sector_id: str
    breadth: float
    rotation_quadrant: str
    money_flow_score: float
    weighted_return_pct: float
    breadth_thrust: bool
  
  institutional:
    fii_net_today_cr: float      # latest available (may be T-1)
    dii_net_today_cr: float
    fii_5d_trend: BUYING | SELLING | NEUTRAL
  
  time:
    minutes_since_open: int
    minutes_to_close: int
    is_lunch_lull: bool          # 11:30-13:00
    is_opening_noise: bool       # first 15 min
    is_closing_noise: bool       # last 15 min

SectorContext (specific to signal's sector):
  constituents_advancing: int
  constituents_declining: int
  pct_above_vwap: float
  sector_relative_strength_vs_nifty: float
  strongest_constituent: str
  weakest_constituent: str
```

### 6.2 Context-Driven Score Adjustments

```
ALIGNMENT BONUS (max +8 points):
  If signal direction aligns with:
    market regime (+3):  bullish signal in TRENDING_UP market
    sector trend (+3):   bullish signal in LEADING/IMPROVING sector
    institutional flow (+2): bullish signal with FII buying

CONTRADICTION PENALTY (max -15 points):
  If signal direction contradicts:
    market regime (-5):  bullish signal in TRENDING_DOWN
    sector trend (-5):   bullish signal in LAGGING sector
    VIX spike (-5):      bullish signal when VIX up > 10% today

These adjustments are ADDITIVE to the final_score (after factor weighting, 
before penalty multiplication). They can push a borderline B+ to A or 
drop an A to B+.
```

---

## 7. Signal Suppression Logic

### 7.1 Suppression Pipeline

Suppression is an ordered sequence of gates. A signal must pass ALL gates to survive. Each gate that kills a signal records WHY it was suppressed.

```
RawSignal ──► GATE 1 ──► GATE 2 ──► GATE 3 ──► GATE 4 ──► GATE 5 ──► GATE 6 ──► GATE 7 ──► GATE 8 ──► SCORED
               │          │          │          │          │          │          │          │
               ▼          ▼          ▼          ▼          ▼          ▼          ▼          ▼
            SUPPRESS   SUPPRESS   SUPPRESS   SUPPRESS   SUPPRESS   SUPPRESS   SUPPRESS   SUPPRESS
```

### 7.2 Gate Definitions

#### Gate 1: Liquidity

```
KILL if avg_daily_turnover < ₹1 crore
KILL if avg_daily_volume < 50,000 shares
KILL if spread_bps > 100 (1% spread — untradeable)

Rationale: no point alerting on stocks you can't trade at reasonable size.
This is the most aggressive filter. Eliminates ~40% of Nifty 500 from signaling.
```

#### Gate 2: Market Regime

```
KILL bullish signal if:
  nifty_breadth < 0.25 AND nifty_return < -1.5%
  (broad market selloff — even good stocks get dragged down)

KILL any signal if:
  vix > 25 AND vix_change_pct > 15%
  (panic spike — signals are unreliable in panic)

PASS otherwise. Even bearish markets have good breakout setups
in relative strength leaders. We don't blanket-suppress.
```

#### Gate 3: Sector Context

```
KILL bullish signal if:
  sector_breadth < 0.25
  (sector is in broad decline — stock is fighting its sector)

KILL bullish signal if:
  sector_rotation_quadrant == LAGGING AND stock_rs < 1.5
  (stock isn't strong enough to overcome a lagging sector)

PASS if stock_rs > 2.0 regardless of sector
  (exceptional relative strength overrides weak sector)
```

#### Gate 4: Time of Day

```
KILL any signal if:
  minutes_since_open < 5  (opening auction distortion)

SUPPRESS (don't kill, but add heavy penalty) if:
  minutes_since_open < 15  (opening volatility)
  OR minutes_to_close < 10  (closing cross distortion)
```

#### Gate 5: Cooldown

```
KILL if Redis key exists: infusion:cooldown:{strategy}:{symbol}

Cooldown durations (from config/scanners.yaml):
  pre_breakout:     1800s (30 min)
  range_breakout:   600s  (10 min)
  volume_surge:     300s  (5 min)
  momentum_shift:   900s  (15 min)
  oi_buildup:       600s  (10 min)
  sector_rotation:  3600s (60 min)
```

#### Gate 6: Contradiction

```
KILL if contradictory signals exist:
  Active BEARISH signal for same symbol while new signal is BULLISH
  (conflicting directions from different strategies → confusion, not conviction)

KILL if rapid signal reversal:
  Signal flipped direction within last 30 minutes for same symbol.
  (whipsaw — stock is choppy, not trending)
```

#### Gate 7: Noise

```
KILL if:
  price_change_pct > 8% in single session (likely news-driven gap, not clean setup)
  
KILL if:
  stock hit upper or lower circuit today (momentum is artificial, not tradeable)

KILL if:
  recent_signal_count_this_symbol > 3 in same session
  (signal-hopping — stock is generating too many signals = noisy)
```

#### Gate 8: Minimum Strength

```
KILL if:
  raw_signal.raw_strength < 60
  (the strategy itself wasn't highly confident — don't waste conviction engine time)

Configurable in scanners.yaml per strategy:
  min_conviction_for_emit: 60  (default)
```

### 7.3 Suppression Record

```
SuppressionRecord:
  gate: str              # "liquidity", "sector_context", etc.
  gate_number: int       # 1-8
  reason: str            # "Avg turnover ₹0.7Cr below ₹1Cr minimum"
  value: float           # 0.7
  threshold: float       # 1.0
  
Every signal (even suppressed ones) gets a list of SuppressionRecords.
Suppressed signals are logged to PostgreSQL signals table with 
conviction_grade = 'SUPPRESSED' for later analysis:
  "Are we suppressing too many real opportunities?"
```

---

## 8. Multi-Timeframe Confirmation

### 8.1 Timeframe Hierarchy

```
We compute features and check conditions on 3 timeframes simultaneously:

Timeframe    Bar Size    Bars Maintained    Primary Use
─────────    ────────    ───────────────    ──────────────────────────
INTRADAY     1-minute    390 (1 full day)   Trigger detection, entry timing
SWING        5-minute    78 per day, 5d     Setup quality, pattern clarity
POSITIONAL   15-minute   26 per day, 10d    Trend context, EMA alignment

Feature engine computes features for ALL 3 timeframes.
Scanner strategies DECLARE which timeframe they operate on.
Conviction scorer uses MULTI-timeframe confirmation as a scoring boost.
```

### 8.2 Multi-Timeframe Confluence Scoring

```
When a signal fires on its primary timeframe, check alignment on other timeframes:

CONFLUENCE CHECK:
  primary_tf = strategy's declared timeframe (e.g., 5MIN for range_breakout)
  
  For each secondary_tf in [1MIN, 5MIN, 15MIN] minus primary:
    check if the direction is aligned:
      aligned = (secondary_tf_trend == signal_direction)
    
    secondary_tf_trend determination:
      if ema_5 > ema_20 on that timeframe → bullish
      if ema_5 < ema_20 → bearish
      else → neutral

  confluence_count:
    0 aligned: confluence_score = 0
    1 aligned: confluence_score = 8
    2 aligned: confluence_score = 15

This is added as a bonus to the conviction score (after factor weighting).
A breakout that's aligned on 1-min, 5-min, AND 15-min is much stronger
than one aligned only on 5-min.
```

### 8.3 Implementation Note

```
Multi-timeframe does NOT mean running the scanner 3 times.
The feature engine already maintains 1m/5m/15m bar buffers.
Features are computed per timeframe.

The scanner runs ONCE per feature update on the strategy's primary timeframe.
Confluence is checked by READING the other timeframe features (already computed).
No extra computation needed. This is a O(1) lookup per signal, not O(N).
```

---

## 9. Option Chain Intelligence Integration

### 9.1 Data Source

Option chain data comes from the NSE scraper (Phase 2, section 2.1):
- Index chains: every 3 minutes (NIFTY, BANKNIFTY)
- Equity chains: every 5 minutes (top F&O stocks)

This data is cached in Redis: `infusion:nse:oi:index:{symbol}` and `infusion:nse:oi:equity:{symbol}`

### 9.2 Derived Metrics

```
From raw option chain, compute:

1. Put-Call Ratio (PCR)
   pcr = total_put_oi / total_call_oi
   Signal:
     PCR 0.7-1.2: bullish zone (put writers confident → support floor)
     PCR < 0.5: excessive call buying (retail exuberance → contrarian bearish)
     PCR > 1.5: excessive put buying (fear → contrarian bullish)

2. Max Pain
   max_pain = strike price where option writers have minimum loss
   Calculated as: argmin over all strikes of (total CE intrinsic + total PE intrinsic)
   
   Relevance to signal:
     Price near max pain → gravitational pull, breakout harder
     Price significantly away from max pain → move may have legs

3. OI Change Analysis
   For each strike, compare current OI vs previous snapshot (3-5 min ago):
   
   Notable OI changes:
     Heavy call writing at ATM+2 strikes → resistance being built → bearish
     Heavy put writing at ATM-2 strikes → support being built → bullish
     Unwinding (OI decreasing) → existing positions closing → trend may exhaust

4. IV Skew
   iv_skew = avg_put_iv / avg_call_iv (for ATM±3 strikes)
   
   iv_skew > 1.2: puts are expensive → market expects downside → cautious for bullish signals
   iv_skew < 0.8: calls are expensive → market expects upside → confirms bullish signals

5. Unusual OI Buildup
   For each strike:
     oi_change_pct = (current_oi - prev_day_oi) / prev_day_oi * 100
     if oi_change_pct > 20% on a single strike → flag as unusual
   
   Unusual put writing at lower strikes = institutional support
   Unusual call writing at higher strikes = institutional resistance
```

### 9.3 Integration with Conviction Scorer

```
Options data feeds into Factor 6 (Options Positioning) of conviction scorer.
Weight: 0.05 of total conviction.

Small weight is deliberate:
  - Data is 3-5 minutes stale (NSE scrape interval)
  - Option positioning is one signal among many
  - For non-F&O stocks, this factor scores neutral (50)
  - OI data can be noisy (hedging vs. directional is indistinguishable)

However, options intelligence is CRITICAL for suppression:
  If OI data strongly contradicts signal (e.g., bullish breakout signal
  but heavy call writing at resistance → smart money selling the breakout),
  the suppression pipeline Gate 6 (Contradiction) considers this.
```

---

## 10. Smart Money Detection Framework

### 10.1 What "Smart Money" Means Here

We can't directly observe institutional order flow. But we can infer it from observable market data. "Smart money" here means: large, informed participants who accumulate quietly before breakouts.

### 10.2 Observable Smart Money Footprints

```
FOOTPRINT 1: Delivery Percentage Expansion
──────────────────────────────────────────
What: Delivery % rising while price is flat or slightly down.
Why: Institutions take delivery (hold). Retailers close intraday.
     High delivery during consolidation = accumulation.

Metric:
  delivery_pct_zscore = (delivery_pct - avg_20d) / std_20d
  SMART MONEY SIGNAL if delivery_pct_zscore > 1.5 AND price_change_5d < 2%

Data: NSE scraper (1-day delayed). Feature engine incorporates in morning warmup.


FOOTPRINT 2: OBV Divergence
────────────────────────────
What: OBV rising while price is flat.
Why: Volume flowing in (accumulation) without price moving up = 
     absorption of supply at current levels.

Metric:
  obv_slope_10d = linear_regression_slope(obv, last 10 daily bars)
  price_slope_10d = linear_regression_slope(close, last 10 daily bars)
  SMART MONEY SIGNAL if obv_slope > 0 AND abs(price_slope) < 0.001


FOOTPRINT 3: Volume-at-Price Concentration
──────────────────────────────────────────
What: High volume traded at a narrow price range during consolidation.
Why: Large buyers are accumulating at a specific price level.
     They absorb all supply at that level, creating a floor.

Metric:
  Compute volume profile for last 5 days of 1-minute bars:
    For each ₹1 price bucket, sum volume traded.
    poc_volume = max(volume across buckets)  (Point of Control)
    total_volume = sum(all volume)
    concentration = poc_volume / total_volume
  SMART MONEY SIGNAL if concentration > 0.30
    (30% of all volume traded at a single price level = institutional absorption)


FOOTPRINT 4: Bid-Ask Imbalance Pattern
───────────────────────────────────────
What: Persistent bid-side depth > ask-side depth during consolidation.
Why: Large buyer is passively accumulating at bid.

Metric:
  For full-tick symbols (Tier 1):
    bid_ask_ratio = total_buy_qty / total_sell_qty
    Track 5-minute rolling average of bid_ask_ratio
    SMART MONEY SIGNAL if avg_bid_ask_ratio_5min > 1.4 consistently for 30+ minutes


FOOTPRINT 5: Block Deal / Bulk Deal Activity
─────────────────────────────────────────────
What: Large block trades during consolidation.
Why: Institutional buyers execute via block/bulk deal window.

Metric:
  From NSE scraper bulk_block data:
    If stock had a block deal in last 5 days AND price hasn't moved > 3%
    → accumulation is happening at current levels
    SMART MONEY SIGNAL if block_deal_recent AND price_range_5d < 3%


FOOTPRINT 6: Option Chain Footprint
────────────────────────────────────
What: Heavy put writing at support levels for a consolidating stock.
Why: Institutions sell puts at prices where they're willing to buy stock.
     Writing puts at ₹1000 means they're happy to own at ₹1000.

Metric:
  From option chain data:
    support_strike = nearest strike below LTP with unusually high put OI
    If put_oi_buildup > 20% at support_strike AND stock is consolidating:
    SMART MONEY SIGNAL
```

### 10.3 Smart Money Composite Score

```
smart_money_score = weighted combination of available footprints:

  delivery_expansion:    weight 0.25  (most reliable, but delayed)
  obv_divergence:        weight 0.25  (realtime, computed from ticks)
  volume_concentration:  weight 0.20  (realtime)
  bid_ask_imbalance:     weight 0.15  (realtime, but only for Tier 1)
  block_deal_activity:   weight 0.10  (sparse, high-confidence when present)
  option_chain_support:  weight 0.05  (supporting evidence, not primary)

If a footprint's data is unavailable (e.g., no delivery data today,
stock not in Tier 1), redistribute its weight proportionally to available footprints.

smart_money_score range: 0-100
  > 70: strong accumulation evidence → used as PRE-BREAKOUT Stage 2 boost
  > 50: moderate evidence → neutral
  < 30: no accumulation evidence → slight penalty for pre-breakout signals
```

### 10.4 Integration Points

```
Smart money score feeds into:
  1. Pre-breakout Stage 2 (Accumulation Evidence) — direct input
  2. Conviction Factor 3 (Setup Quality) — bonus for setup_age component
  3. Signal explanation — "Smart money footprint: OBV divergence + rising delivery"

It does NOT feed into:
  - Technical factor (that's pure TA)
  - Volume factor (that's realtime volume, not smart money inference)
  - Market regime (that's index-level)
```

---

## 11. Noise Reduction Architecture

### 11.1 Noise Sources

```
Source 1: Opening Auction (09:15-09:30)
  Problem: Opening prices gap up/down, relative volume is artificially high
           (accumulated pre-open orders execute at once).
  Mitigation: Time-of-day penalty (section 5.4, penalty 3).
              Feature engine uses time-of-day adjusted volume baselines.

Source 2: Lunch Lull (11:30-13:00)
  Problem: Low volume, thin order books, random price jitter.
           Patterns formed in low volume are unreliable.
  Mitigation: Suppression gate 4 (time-of-day) applies 0.90 multiplier.
              Volume-based signals require rel_vol adjustment for lunch hour.

Source 3: News-Driven Gaps
  Problem: Stock gaps 8% on results/news. Technical patterns are irrelevant.
  Mitigation: Suppression gate 7 kills signals if price_change_pct > 8%.

Source 4: Circuit Breakers
  Problem: Stock hits upper/lower circuit. Price action is artificial.
  Mitigation: Suppression gate 7 kills signals for circuit-hit stocks.

Source 5: Illiquid Whipsaws
  Problem: Low-float stocks move 3-4% on ₹10 lakh volume. Not real breakouts.
  Mitigation: Liquidity gate (gate 1) eliminates stocks below ₹1 Cr turnover.

Source 6: Multiple Strategy Overlap
  Problem: Same stock triggers volume_surge, momentum_shift, and breakout
           within 2 minutes. Three alerts for one event.
  Mitigation: Signal deduplication (see below).
```

### 11.2 Signal Deduplication

```
When multiple strategies fire for the same symbol within a SHORT window:

Dedup logic:
  dedup_window = 120 seconds (configurable)
  
  On new RawSignal(symbol, strategy):
    recent_signals = [s for s in active_signals 
                      if s.symbol == symbol 
                      AND s.timestamp > now - dedup_window]
    
    if len(recent_signals) > 0:
      # Another strategy already fired for this symbol recently.
      # Don't emit a separate signal. Instead:
      # 1. Merge: add this strategy's conditions to the existing signal
      # 2. Boost: increase conviction for multi-strategy confirmation
      existing = recent_signals[0]  # most recent
      existing.confirming_strategies.append(strategy_name)
      existing.raw_strength += 5  # per additional confirming strategy
      existing.explanation.add("Also confirmed by: {strategy_name}")
      
      # Don't create a new signal. Don't set a new cooldown.
      return
    
    # No recent signal → proceed normally.

Effect: one breakout event → one signal with multiple strategy confirmations,
not three separate alerts. Multi-strategy confluence BOOSTS conviction.
```

### 11.3 Noise Metric

```
Track noise ratio per session for system health monitoring:

noise_ratio = suppressed_signals / (suppressed_signals + surviving_signals)

Target: noise_ratio between 0.40 and 0.70
  < 0.40: strategies may be too conservative (missing real setups)
  > 0.70: strategies may be too loose (generating too much garbage)

Tracked in health dashboard. Logged to PostgreSQL for daily analysis.
```

---

## 12. Signal Ranking / Prioritization

### 12.1 Ranking Model

Active signals are ranked in a single ordered list. The dashboard shows this list, top to bottom. The user looks at the top 3-5, not all 15.

```
Primary sort: conviction_score DESC
Tiebreaker 1: number of confirming strategies DESC
Tiebreaker 2: sector breadth DESC (prefer stocks in strong sectors)
Tiebreaker 3: signal_age ASC (prefer fresher signals)

Implementation: Redis ZSET infusion:signal:active
  Score: conviction_score (float)
  Member: signal_id

  For tiebreakers: encode into the ZSET score as a composite:
    composite_score = conviction_score + (confirming_count * 0.01) + (freshness_bonus * 0.001)
    
    freshness_bonus = max(0, 60 - signal_age_minutes) / 60
    (1.0 for just-fired, 0.0 after 60 minutes)
```

### 12.2 Rank Stability

```
Problem: if conviction scores are recalculated frequently, the ranking jumps around.
         User sees RELIANCE at #1, refreshes 5 seconds later, now it's #3. Distracting.

Solution: conviction score is computed ONCE at signal creation and is NOT updated.
The signal score is a snapshot of the moment the signal fired.

The ranking changes only when:
  1. A new signal enters the list (higher score pushes others down)
  2. A signal expires (removed from list)
  3. A signal is manually dismissed by user (removed from list)

No recalculation. No reranking. Stability > precision for UX.
```

### 12.3 Confidence Decay for Display

```
While the SCORE doesn't change, the VISUAL DISPLAY reflects aging:

Signal age < 5 min:  bright highlight, full opacity
Signal age 5-30 min: normal display
Signal age 30-60 min: slightly faded
Signal age > 60 min: dimmed, moved to "aged" section

This is purely a frontend visual treatment. The backend ranking is unchanged.
It communicates to the user: "this signal is getting stale, act now or let it go."
```

---

## 13. Cooldown and Anti-Spam Logic

### 13.1 Cooldown Tiers

```
Per-strategy-per-symbol cooldown (Redis SET NX EX):
  Key: infusion:cooldown:{strategy}:{symbol}
  TTL: strategy-specific (from config/scanners.yaml)
  
  pre_breakout:     1800s (30 min) — slow setup, don't re-signal quickly
  range_breakout:   600s  (10 min) — can re-signal if conditions re-emerge
  volume_surge:     300s  (5 min)  — short, because volume spikes are transient
  momentum_shift:   900s  (15 min)
  oi_buildup:       600s  (10 min)
  sector_rotation:  3600s (60 min) — rotation is a slow theme

Global per-symbol cooldown (across all strategies):
  Key: infusion:cooldown:global:{symbol}
  TTL: 120s (2 min)
  
  Purpose: prevent 3 different strategies from firing for the same stock
  within 2 minutes (handled by signal dedup in section 11.2,
  but this is a safety net in case dedup logic has an edge case).
```

### 13.2 Anti-Spam Rules

```
Rule 1: Max signals per session
  If total_signals_today > 50 (across all strategies):
    Switch all strategies to CONSERVATIVE mode:
      - Increase min_conviction_for_emit to 75 (from default 60)
      - Double all cooldown TTLs
    Alert via Telegram: "Signal spam detected. Switching to conservative mode."

Rule 2: Max signals per symbol per session
  If signals_today_for_symbol > 5:
    Suppress all further signals for that symbol today.
    Something is generating noise for this stock.

Rule 3: Max signals per sector per hour
  If signals_in_sector_last_hour > 8:
    Suppress lower-conviction signals in that sector.
    Only grade A+ passes. Others suppressed with reason "sector signal saturation".

Rule 4: Telegram-specific throttle
  Max 10 Telegram alerts per hour.
  Max 3 Telegram alerts per 15-minute window.
  If exceeded: queue alerts, deliver next window.
  Never exceed 30 alerts per day.
```

---

## 14. Outcome Tracking and Feedback Loops

### 14.1 Outcome Pipeline

```
Signal fires (t=0)                      Outcome tracking begins
     │
     ├── T+1 day: fetch close price ──► compute return_1d_pct
     │                                  UPDATE signals SET price_1d, return_1d_pct
     │
     ├── T+3 days: fetch close price ──► compute return_3d_pct
     │                                   UPDATE signals SET price_3d, return_3d_pct
     │
     ├── T+5 days: fetch close price ──► compute return_5d_pct
     │                                   compute max_favorable_pct (best case in 5d window)
     │                                   compute max_adverse_pct (worst case in 5d window)
     │                                   UPDATE signals SET price_5d, return_5d_pct,
     │                                                      max_favorable_pct, max_adverse_pct,
     │                                                      outcome_label
     │
     └── T+5 outcome labeling:
           if return_5d_pct > +3%:  outcome = "WIN"
           if return_5d_pct < -3%:  outcome = "LOSS"
           else:                    outcome = "FLAT"
           
           Threshold of ±3% is configurable.
           For bullish signals: positive return = WIN.
           For bearish signals: negative return = WIN.
```

### 14.2 Analytics Queries

```sql
-- Strategy precision (win rate)
SELECT strategy,
       COUNT(*) as total,
       COUNT(*) FILTER (WHERE outcome_label = 'WIN') as wins,
       ROUND(COUNT(*) FILTER (WHERE outcome_label = 'WIN')::numeric / COUNT(*) * 100, 1) as precision_pct
FROM signals
WHERE outcome_label IS NOT NULL
  AND created_at > now() - interval '90 days'
GROUP BY strategy
ORDER BY precision_pct DESC;

-- Grade precision (do higher grades perform better?)
SELECT conviction_grade, 
       COUNT(*) as total,
       AVG(return_5d_pct) as avg_return,
       COUNT(*) FILTER (WHERE outcome_label = 'WIN') * 100.0 / COUNT(*) as win_rate
FROM signals
WHERE outcome_label IS NOT NULL
GROUP BY conviction_grade
ORDER BY conviction_grade;

-- False breakout rate by strategy
SELECT strategy,
       COUNT(*) FILTER (WHERE outcome_label = 'LOSS' AND max_adverse_pct < -5) as false_breakouts,
       COUNT(*) as total,
       ROUND(COUNT(*) FILTER (WHERE outcome_label = 'LOSS' AND max_adverse_pct < -5)::numeric / COUNT(*) * 100, 1) as false_breakout_pct
FROM signals
WHERE outcome_label IS NOT NULL
GROUP BY strategy;

-- Suppression analysis: are we killing good signals?
SELECT gate, reason, COUNT(*) as suppressed_count
FROM (
  SELECT unnest(suppressions) ->> 'gate' as gate,
         unnest(suppressions) ->> 'reason' as reason
  FROM signals
  WHERE conviction_grade = 'SUPPRESSED'
    AND outcome_label = 'WIN'  -- these WOULD have been winners
) sub
GROUP BY gate, reason
ORDER BY suppressed_count DESC;

-- Feature attribution: which features predict wins?
SELECT 
  features->>'rsi_14' as rsi_bucket,
  AVG(return_5d_pct) as avg_return
FROM signals
WHERE conviction_grade IN ('A+', 'A')
GROUP BY features->>'rsi_14'
ORDER BY avg_return DESC;
```

### 14.3 Feedback Into System

```
Outcome data drives THREE feedback loops:

Loop 1: Strategy Parameter Tuning (manual, quarterly)
  Review outcome analytics.
  If strategy precision < 40%:
    - Tighten thresholds in config/scanners.yaml
    - Increase cooldown
    - Consider disabling strategy
  If strategy precision > 70%:
    - May loosen thresholds slightly (capture more setups)
  
  This is MANUAL. Automated threshold tuning is risky for a trading system.
  Human judgment required.

Loop 2: Conviction Weight Calibration (manual, monthly)
  Run regression: which conviction factors correlate with outcomes?
  If sector_context has 0.8 correlation with WIN rate:
    → consider increasing sector weight from 0.20 to 0.25
  If options_positioning has 0.1 correlation:
    → confirm weight should stay low at 0.05

  Again, MANUAL adjustment. The outcome data tells you WHERE to look,
  but a human decides WHETHER to adjust.

Loop 3: ML Training Dataset (automated export, manual model training)
  Export: SELECT features, outcome_label, return_5d_pct FROM signals
  WHERE conviction_grade != 'SUPPRESSED' AND outcome_label IS NOT NULL
  
  This generates the labeled dataset for future LightGBM training.
  When enough data exists (500+ labeled signals), train an ML model
  that predicts return_5d_pct from features.
  
  ML model is OPTIONAL and SUPPLEMENTARY (Global Constraint #9).
  It feeds into conviction as a 0.05-weight AI boost factor.
  It never overrides the deterministic scoring pipeline.
```

---

## 15. Explainability Layer

### 15.1 Explanation Model

Every scored signal carries a structured explanation that answers the questions: WHY did this signal fire? WHY this score? WHY this rank?

```
SignalExplanation:
  
  # What fired
  trigger_summary: str
    "Range breakout on RELIANCE with 3.2x volume after 8-day compression"
  
  # Conditions (from strategy)
  conditions: list[ConditionResult]
    Each with: name, passed, value, threshold, description
  
  # Conviction breakdown
  factor_scores: list[ScoringFactor]
    ScoringFactor:
      name: str           # "technical", "volume", "setup_quality", etc.
      score: float        # 0-100
      weight: float       # 0.25
      weighted: float     # score * weight
      components: list[ScoringComponent]
        ScoringComponent:
          name: str       # "trend_alignment"
          score: float    # 40
          max: float      # 40
          reason: str     # "EMA5 > EMA20 > EMA50 — full bullish stack"
  
  # Penalties applied
  penalties: list[PenaltyRecord]
    PenaltyRecord:
      name: str           # "time_of_day"
      multiplier: float   # 0.90
      reason: str         # "Lunch lull (12:14 IST)"
  
  # Context confirmations
  confirmations: list[str]
    "Sector breadth at 0.72 (strong participation)"
    "NIFTY 50 in TRENDING_UP regime"
    "FII net buyer (₹1,240 Cr)"
    "Multi-timeframe confluence: aligned on 1m, 5m, 15m"
  
  # Context contradictions
  contradictions: list[str]
    "VIX elevated at 18.5 (+8% vs yesterday)"
  
  # Confirming strategies (if multi-strategy dedup fired)
  confirming_strategies: list[str]
    "Also confirmed by: volume_surge"
  
  # Smart money evidence (if detected)
  smart_money: list[str]
    "OBV rising +12% while price flat over 10 days"
    "Delivery % at 58% vs 42% average"
  
  # Suppression attempts (gates that DID NOT suppress but were close)
  near_suppressions: list[str]
    "Liquidity: passed (turnover ₹8.2Cr, threshold ₹1Cr)"
    "Time-of-day: penalty applied (lunch lull)"
```

### 15.2 Telegram Explanation Format

```
🔥 BREAKOUT — RELIANCE (A+ 84)

📍 Range breakout after 8-day compression + 3.2x volume

📊 Conviction Breakdown:
  Technical:  78/100 (EMA stack aligned, RSI 61)
  Volume:     85/100 (3.2x rel vol, delivery 58%)
  Setup:      90/100 (8-day squeeze, 3 resistance tests)
  Sector:     72/100 (Nifty50 breadth 0.72, LEADING)
  Regime:     80/100 (TRENDING_UP)
  Options:    65/100 (PCR 0.85, long buildup)

⚠️ Penalties: -10% lunch lull

✅ Confirmations:
  • Multi-TF aligned (1m+5m+15m)
  • Smart money: OBV divergence + rising delivery
  • Also confirmed by: volume_surge

Price: ₹2,847 | Vol: 14.2M | Delivery: 58%
⏱ 12:14 PM IST
```

### 15.3 Dashboard Explanation Panel

```
When user clicks a signal in the dashboard, expand an explanation card:

┌──────────────────────────────────────────────────────┐
│  RELIANCE — Range Breakout                    A+ 84  │
├──────────────────────────────────────────────────────┤
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │ CONVICTION FACTORS          Score    Weight      │ │
│  │ ─────────────────────────── ──────   ──────      │ │
│  │ ████████████████░░░ Tech     78      ×0.25 = 20  │ │
│  │ ██████████████████░ Volume   85      ×0.20 = 17  │ │
│  │ ████████████████████ Setup   90      ×0.20 = 18  │ │
│  │ ███████████████░░░░ Sector   72      ×0.20 = 14  │ │
│  │ ████████████████░░░ Regime   80      ×0.10 = 8   │ │
│  │ █████████████░░░░░░ Options  65      ×0.05 = 3   │ │
│  │ ─────────────────────────── ──────                │ │
│  │ Raw Score: 80 → Penalties: ×0.90 → Final: 84     │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  WHY THIS FIRED:                                     │
│  • ATR(14) in 18th percentile of 50-day range        │
│  • Bollinger width 62% of average (squeeze)          │
│  • 8 consecutive days < 1.5% range                   │
│  • Price broke ₹2,840 resistance (tested 3 times)    │
│  • Volume surged to 3.2x 20-day average              │
│                                                       │
│  PENALTIES:                                          │
│  • Lunch lull: -10% (12:14 IST)                      │
│                                                       │
│  CONFIRMATIONS:                                      │
│  ✅ EMA 5 > 20 > 50 (bullish stack)                  │
│  ✅ Sector breadth 0.72 (strong)                      │
│  ✅ NIFTY trending up                                 │
│  ✅ Multi-TF aligned (1m + 5m + 15m)                  │
│  ✅ Smart money: OBV divergence + delivery 58%        │
│  ✅ Also confirmed by: volume_surge strategy          │
│                                                       │
│  ⚠️ CONTRADICTIONS:                                  │
│  • VIX at 18.5 (+8% today)                           │
│                                                       │
└──────────────────────────────────────────────────────┘
```

---

## Resource Estimates

```
Scanner Service:
  Memory: ~100MB
    Pre-breakout state: 700 × 256B = 175KB
    Feature delta cache: 700 × 1KB = 700KB
    Strategy instances: ~2MB (with parameters)
    Signal dedup buffer: ~50KB
  CPU: ~5% of 1 core at peak (strategy evaluation is pure math)
  
Conviction Scorer:
  Memory: ~50MB (without ML model), ~200MB (with LightGBM)
  CPU: ~2% of 1 core (scoring is weighted arithmetic)
  
Combined scanner + conviction: < 10% CPU, < 300MB RAM

Expected processing time per signal:
  Strategy evaluation: 0.1-0.5ms (per symbol, per strategy)
  Suppression pipeline: 0.05ms (8 gate checks)
  Conviction scoring: 0.3ms (6 factors)
  Explanation building: 0.1ms (string assembly)
  Total per signal: < 1ms
```

---

## Phase 3 Boundary

This is the intelligence core. Key design decisions made:

| Decision | Rationale |
|---|---|
| Event-driven scanner with feature delta detection | Compute only where features actually changed (Constraint #7) |
| Multi-stage pre-breakout state machine | Detection happens progressively, not as a point-in-time check (Constraint #5) |
| Deterministic scoring with explicit weights | Explainable, auditable, tunable without code changes (Constraints #6, #8) |
| 8-gate suppression pipeline | Aggressive noise reduction. Each gate records why (Constraint #3) |
| Signal deduplication over multi-strategy spam | One event → one signal with confluence boost (Constraint #3) |
| Outcome tracking with manual feedback loops | Dataset generation for future ML, but humans decide threshold changes (Constraint #9) |
| Full structured explainability | Every signal answers "why" at every level (Constraint #5 of Phase 3) |
| Conviction score computed once, never recalculated | Ranking stability for UX. Freshness conveyed via visual decay (Constraint #6 Global) |

**Awaiting approval to proceed to Phase 4 — Alerting & AI Layer.**
