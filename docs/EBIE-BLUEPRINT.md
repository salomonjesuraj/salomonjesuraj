# Infusion Advanced Early Breakout / Breakdown Intelligence Scanner
## AI-Team Architecture & Implementation Blueprint

**Document purpose:** Convert Infusion from a highly capable confirmation/scoring scanner into an **anticipatory market-intelligence engine** that identifies the probability of a breakout or breakdown *before* the price level is crossed, explains why, rejects low-quality setups, and gives one direct human-readable verdict.

**Target market:** NSE F&O universe  
**Execution policy:** Advisory / paper-trading first; no live auto-execution  
**Primary design principle:** Predict the *developing state* of a breakout/breakdown, not merely the breakout candle.

---

# 1. Executive Mandate

Infusion already has a strong technical foundation: market structure, BOS/CHoCH, MTF confirmation, ATR sizing, Fibonacci, pivots/CPR, MA regimes, chart-pattern geometry, ICT concepts, Wyckoff signals, cross-index confirmation, VCP scoring, options analytics, walk-forward validation, purged CV, ML classification, Half-Kelly sizing, breadth scoring, and tracked real outcomes.

The next stage must **not** be another large indicator expansion.

The goal now is to transform the scanner into an **Early Breakout Intelligence Engine (EBIE)** that continuously answers:

> **Is this stock being accumulated or distributed? Is pressure building? Are derivatives positioning and option-chain behavior supporting the move? Is the sector/market helping? Is there a catalyst? How close is the setup to activation? Is this likely to be a genuine breakout, a breakdown, or a trap?**

The scanner must provide separate bullish and bearish probabilities and a direct verdict such as:

- `EARLY LONG BUILDUP`
- `LONG READY`
- `BREAKOUT ARMED`
- `BREAKOUT CONFIRMED`
- `EARLY SHORT BUILDUP`
- `SHORT READY`
- `BREAKDOWN ARMED`
- `BREAKDOWN CONFIRMED`
- `TRAP RISK`
- `NO TRADE`
- `DATA UNRELIABLE`

The system should prioritize **precision, lead time, calibration, and rejection quality** over the raw number of alerts.

---

# 2. Current Infusion Baseline — Keep This Foundation

The current architecture should remain the backbone:

```text
Upstox / Exchange Data
        ↓
ingestion
        ↓
feature-engine
        ↓
scanner
        ↓
api
        ↓
dashboard / Telegram
        ↓
archiver
        ↓
walk-forward / optimizer / analytics
```

Keep the following existing strengths:

- Event-driven market ingestion
- Feature-engine separation
- Scanner-level dedupe/cooldown/suppression
- Frozen entry/SL/target per signal episode
- Real signal outcome tracking
- T1/T2/T3 outcome states
- Walk-forward validation
- Purged cross-validation with embargo
- Net-of-cost option-premium tracking
- Feature information coefficient
- Feature ablation
- Deflated Sharpe Ratio
- F&O ban hard gate
- Market breadth
- Half-Kelly research/sizing
- VIX-aware sizing
- ML as advisory probability, not autonomous execution
- Stock-first radar direction

These are valuable and should be extended rather than replaced.

---

# 3. Fundamental Design Change

## Old mental model

```text
Indicator conditions
      ↓
Breakout candle occurs
      ↓
Confirm with volume / RSI / Supertrend / MTF
      ↓
Create signal
```

This is inherently late.

## New mental model

```text
ACCUMULATION / DISTRIBUTION
            ↓
VOLATILITY COMPRESSION
            ↓
PARTICIPATION CHANGE
            ↓
ORDER-FLOW / DEPTH PRESSURE
            ↓
FUTURES + OPTIONS POSITIONING
            ↓
RELATIVE STRENGTH / SECTOR SUPPORT
            ↓
NEWS / EVENT SENTIMENT
            ↓
LEVEL PROXIMITY + LIQUIDITY MAP
            ↓
PRE-BREAKOUT PROBABILITY
            ↓
ARMED STATE
            ↓
PRICE TRIGGER
            ↓
BREAKOUT QUALITY CHECK
            ↓
CONFIRMED / FAILED / TRAP
```

A breakout must become a **state machine**, not a one-candle event.

---

# 4. What Must Be Added

# 4.1 Event-Time and Market-Data Integrity Layer — Highest Priority

Before increasing model complexity, build a strict event-time system.

For every tick, quote, candle, option-chain snapshot, news item and derived feature, record:

```text
exchange_timestamp
provider_timestamp
receive_timestamp
processing_timestamp
feature_timestamp
signal_timestamp
persisted_timestamp
```

## Required checks

- out-of-order ticks
- duplicate ticks
- stale ticks
- WebSocket reconnect gaps
- missing candles
- partial candle handling
- duplicate candle reconstruction
- option-chain snapshot age
- quote age
- news publication age
- exchange/session status
- pre-open vs normal session
- closing-session handling
- holiday/special-session handling
- symbol/contract rollover
- expiry mapping
- corporate-action adjustments where applicable

## Add a Data Quality Score

Every symbol must have:

```text
data_quality_score: 0–100
```

Example penalties:

- stale spot quote
- stale futures quote
- stale option chain
- missing depth
- missing 1m candles
- unexplained gap
- WebSocket reconnect inside feature window
- contradictory instrument mapping

### Hard rule

If `data_quality_score < threshold`, verdict must become:

```text
DATA UNRELIABLE — NO TRADE
```

### Justification

An advanced model operating on bad timestamps simply produces more sophisticated wrong answers.

---

# 4.2 Multi-Speed Data Architecture

Do not process all 208 symbols at maximum depth continuously.

Create three scanning tiers.

## Tier 1 — Universe Scan

All F&O symbols.

Use:

- LTP / OHLC
- cumulative volume
- 1m bars
- basic depth where available
- index/sector mapping
- futures price/OI
- lightweight option-chain snapshots
- basic news flags

Purpose:

> Cheaply detect which stocks are developing unusual behavior.

## Tier 2 — Candidate Promotion

Promote approximately the strongest developing candidates based on:

- unusual relative volume
- compression
- range proximity
- relative strength
- OI acceleration
- directional imbalance
- catalyst/news

Run heavier analysis:

- full strike neighborhood
- option Greeks
- OI velocity
- IV skew
- richer depth metrics
- microstructure
- anchored VWAP families
- accumulation/distribution model

## Tier 3 — Armed / Active Setups

Only the most actionable candidates.

Use the highest data cadence and all available microstructure/depth.

### Upstox-specific design note

Upstox Market Data Feed V3 supports multiple feed modes including LTPC, option Greeks, full depth and a 30-level depth mode. The current API documentation also imposes subscription/connection limits, and 30-level depth is more restricted than normal feeds. Therefore the architecture should use **dynamic subscription promotion**, not try to keep maximum depth on the entire universe.

### Justification

This gives lower latency and better API-budget utilization while reserving expensive features for candidates where they can actually change a decision.

---

# 4.3 Accumulation / Distribution Intelligence Engine

This is one of the most important additions.

Do **not** define accumulation as “price sideways + volume high.”

Use multiple independent evidence families.

## 4.3.1 Compression Features

Calculate:

- ATR percentile over 20/60/120 sessions
- Bollinger Band Width percentile
- normalized true-range compression
- rolling standard deviation contraction
- high-low range contraction
- NR4 / NR7-like compression state
- VCP contraction sequence
- inside-bar cluster density
- distance between successive swing highs/lows
- compression duration
- compression quality
- range symmetry/asymmetry

### Bullish interpretation

Price volatility contracts while:

- lows rise,
- price spends more time in upper range,
- down-volume weakens,
- relative strength improves.

### Bearish interpretation

Price volatility contracts while:

- highs fall,
- price spends more time in lower range,
- up-volume weakens,
- relative strength deteriorates.

---

## 4.3.2 Close-Location and Volume Pressure

For every bar:

```text
CLV = ((Close - Low) - (High - Close)) / max(High - Low, epsilon)
```

Track:

- volume-weighted CLV
- CLV trend
- positive-volume CLV
- negative-volume CLV
- closing-in-upper-quartile frequency
- closing-in-lower-quartile frequency

This helps identify persistent absorption/pressure that a simple volume spike misses.

---

## 4.3.3 Up-Volume / Down-Volume Imbalance

Maintain windows such as:

```text
5m
15m
30m
60m
1D
5D
20D
```

Features:

- up-volume / down-volume ratio
- signed volume proxy
- volume on expansion bars
- volume on pullbacks
- volume dry-up during contraction
- participation expansion near trigger
- volume acceleration
- relative volume vs time-of-day baseline

### Important

Intraday volume must be compared to the **same time-of-day historical profile**.

Example:

11:05 AM cumulative volume should not be compared directly with a full-day average.

Create:

```text
RVOL_TOD =
current cumulative volume at T
/
median cumulative volume at T over historical sessions
```

---

# 4.4 Anchored VWAP Intelligence

Add multiple AVWAP anchors:

- current session open
- previous day high
- previous day low
- previous day close
- week open
- month open
- last major swing high
- last major swing low
- latest high-volume pivot
- breakout base start
- major gap day
- earnings/event candle when relevant

Derive:

- price above/below AVWAP
- AVWAP slope
- distance to AVWAP in ATR
- number of AVWAPs clustering
- reclaim/rejection count
- time spent above/below
- successful retest quality

### Why

A stock repeatedly absorbing supply above a meaningful anchored VWAP gives stronger accumulation evidence than a generic EMA crossover.

---

# 4.5 Relative Strength Engine — Upgrade Existing Logic

Relative strength must become a core feature, not a side confirmation.

Calculate stock performance relative to:

```text
NIFTY 50
relevant sector index
F&O universe
peer basket
```

Across:

```text
5m
15m
1h
1D
5D
20D
60D
```

Features:

- RS slope
- RS acceleration
- percentile rank
- RS new-high before price new-high
- RS new-low before price new-low
- sector-relative strength
- intraday beta-adjusted residual move
- strength during index pullback
- weakness during index rally

## Powerful early-breakout pattern

```text
Index flat/down
Stock holds near highs
Volume does not collapse
RS rises
Sector breadth improves
Stock remains above VWAP/AVWAP
```

This should score heavily bullish even before the absolute breakout.

---

# 4.6 Order-Book / Microstructure Pressure Engine

This is a major advanced-layer addition.

Use available market depth to derive:

## 4.6.1 Book Imbalance

For top N levels:

```text
BidImbalance =
sum(weight_i * bid_qty_i)
/
(sum(weight_i * bid_qty_i) + sum(weight_i * ask_qty_i))
```

Use larger weights near the best bid/ask.

Track:

- instantaneous imbalance
- EMA imbalance
- imbalance persistence
- imbalance acceleration
- bid replenishment
- ask replenishment
- liquidity pull
- spread widening/narrowing
- depth concentration
- top-level fragility

## 4.6.2 Absorption Proxy

Bullish absorption example:

```text
Repeated selling pressure
+ support price does not break
+ bid depth replenishes
+ traded volume increases
+ close remains above support
```

Bearish absorption is inverse.

## 4.6.3 Sweep / Liquidity Consumption Proxy

Detect:

- rapid depletion across ask levels
- rapid depletion across bid levels
- spread jump
- price impact per unit volume
- immediate replenishment after depletion

### Critical limitation

Without full exchange order-by-order data, do not claim exact institutional order flow.

Name the feature:

```text
microstructure_pressure
```

not:

```text
institutional_buying
```

unless the evidence actually supports that label.

---

# 4.7 Futures Positioning Engine

For stock futures, track:

- price
- OI
- ΔOI
- OI velocity
- OI acceleration
- volume
- basis
- basis change
- near/far contract relationship where useful
- rollover behavior near expiry

Use the classical price/OI matrix only as a **starting interpretation**:

| Price | OI | Initial interpretation |
|---|---:|---|
| Up | Up | Long buildup candidate |
| Down | Up | Short buildup candidate |
| Up | Down | Short covering candidate |
| Down | Down | Long unwinding candidate |

Then validate with:

- volume
- basis
- spot move
- sector move
- time persistence
- expiry proximity

## Add OI velocity

Static OI is weak.

Use:

```text
dOI_1m
dOI_5m
dOI_15m
dOI_zscore
d2OI_dt2
```

The **change in positioning** is often more useful than the absolute OI value.

---

# 4.8 Advanced Option-Chain Positioning Engine

Current PCR/OI support-resistance/Max Pain should be upgraded into a dynamic positioning model.

## 4.8.1 Never infer writer/buyer from OI alone

Incorrect:

```text
Call OI increased → call writing → bearish
```

Correct:

Use joint state:

```text
option premium
Δpremium
OI
ΔOI
volume
IV
ΔIV
bid/ask
underlying direction
distance from spot
time to expiry
```

Only then assign a probabilistic positioning label.

---

## 4.8.2 Strike Neighborhood

For each stock, dynamically analyze strikes around spot:

```text
ATM ± configurable strike count
```

Weight nearer strikes more heavily.

Separate:

- nearest expiry
- next expiry
- monthly expiry where relevant

Do not blindly combine all expiries.

---

## 4.8.3 OI Wall Strength

For each strike:

```text
call_wall_score
put_wall_score
```

based on:

- absolute OI percentile
- ΔOI
- OI velocity
- volume/OI
- distance from spot
- persistence
- premium behavior
- IV behavior

Track whether the wall is:

- strengthening
- weakening
- migrating
- being consumed
- abandoned

---

## 4.8.4 Wall Migration

Example bullish evidence:

```text
Call resistance at 1000 weakens
New call OI shifts to 1020/1040
Put OI strengthens at 980/990
Spot holds above 995
```

This is more valuable than a static “max call OI = resistance” label.

---

## 4.8.5 Dynamic PCR

Keep PCR, but replace raw PCR as a primary signal.

Compute:

```text
PCR_OI
PCR_volume
ΔPCR_5m
ΔPCR_15m
PCR_zscore
near_ATM_PCR
weighted_PCR
expiry_specific_PCR
```

The direction and rate of change matter more than a fixed threshold such as 0.7 or 1.3.

---

## 4.8.6 IV Intelligence

Add:

- ATM IV
- IV percentile/rank
- call IV vs put IV
- skew slope
- skew change
- term structure
- IV acceleration
- IV expansion before price break
- IV crush risk
- IV divergence vs realized volatility

Use IV as:

- uncertainty/catalyst information,
- option-tradeability information,
- trap-risk information.

Do not use rising IV as automatically bullish or bearish.

---

## 4.8.7 Greeks Pressure

Use:

- delta
- gamma
- theta
- vega

for option selection and risk.

Possible advanced research feature:

```text
delta_weighted_OI
gamma_weighted_OI_proxy
```

### Important

Call it a **proxy**, not true dealer gamma exposure, unless dealer position direction is actually known.

Public OI does not reveal which side is held by dealers.

---

# 4.9 Option Tradeability Gate

The underlying setup and the option contract must be scored separately.

Pipeline:

```text
Underlying bullish/bearish verdict
          ↓
Option contract candidates
          ↓
Option Tradeability Score
          ↓
Contract accepted / rejected
```

## Option Tradeability Score inputs

- bid/ask spread %
- depth
- traded volume
- OI
- volume/OI
- delta
- gamma
- theta
- IV
- IV rank
- premium
- premium ATR
- distance from ATM
- expiry proximity
- estimated slippage
- expected underlying move
- expected premium sensitivity
- strike-level resistance/support
- stale quote detection

## Hard rejection examples

- spread too wide
- stale contract
- insufficient volume
- insufficient OI
- extreme theta relative to expected hold time
- option price disconnected from underlying
- poor reward after estimated spread/slippage

### Why

A correct directional stock call can still produce a bad option trade.

---

# 4.10 Sentiment and Catalyst Engine

Upstox currently exposes a News API for instrument-specific market news. Use it as one source, but design the system with a provider abstraction so additional trusted sources can be added later.

## Pipeline

```text
news/event source
      ↓
deduplicate
      ↓
entity mapping
      ↓
event classification
      ↓
sentiment model
      ↓
relevance
      ↓
novelty
      ↓
credibility
      ↓
time decay
      ↓
sentiment impact score
```

## Do not use generic positive/negative keywords

Financial language is contextual.

Use:

- FinBERT or equivalent finance-domain classifier
- optional LLM event classifier
- deterministic event taxonomy

## Event taxonomy

Examples:

- earnings beat/miss
- guidance increase/decrease
- order win
- regulatory approval
- regulatory investigation
- acquisition
- stake sale/purchase
- promoter pledge
- debt refinancing
- credit-rating change
- management change
- lawsuit
- plant shutdown
- production increase
- government policy
- sector policy
- commodity/input-price shock
- bulk/block activity if reliable source available

## Sentiment output

```text
direction: bullish / bearish / neutral / ambiguous
confidence: 0–1
event_severity: 0–1
stock_relevance: 0–1
novelty: 0–1
source_quality: 0–1
age_decay: 0–1
```

Composite:

```text
sentiment_impact =
direction *
confidence *
event_severity *
stock_relevance *
novelty *
source_quality *
age_decay
```

### Important

News should influence the verdict but should rarely override broken market structure or poor liquidity by itself.

---

# 4.11 Market + Sector Context Engine

Expand the existing breadth score into a directional context engine.

For each stock determine:

- NIFTY regime
- relevant sector regime
- sector breadth
- F&O breadth
- advance/decline
- % above VWAP
- % above key EMAs
- breakout/breakdown count
- new intraday highs/lows
- index futures positioning
- volatility regime
- correlation regime

Output:

```text
market_context_score_bull
market_context_score_bear
sector_context_score_bull
sector_context_score_bear
```

Example:

A bullish stock setup receives a penalty if:

```text
NIFTY breakdown
+ sector breakdown
+ sector breadth weak
+ stock beta high
```

But a very strong relative-strength leader can still survive with a lower penalty.

---

# 4.12 Regime Engine

Every feature must behave differently by regime.

Classify:

```text
TREND_UP
TREND_DOWN
RANGE
HIGH_VOLATILITY
LOW_VOLATILITY_COMPRESSION
EVENT_SHOCK
OPENING_DISCOVERY
EXPIRY_DISTORTION
```

Strategies/features receive regime-dependent weights.

Example:

- VCP is valuable in compression.
- breakout chasing is dangerous during event shock.
- Max Pain may be more relevant near expiry but should never be a standalone directional trigger.
- mean-reversion evidence should not veto a strong expansion regime too early.

---

# 4.13 Liquidity Map

Create a unified map of important price zones:

- previous day high/low
- week high/low
- month high/low
- recent swing highs/lows
- equal highs/lows
- VWAP/AVWAP clusters
- high-volume pivots
- CPR/pivots
- option OI walls
- futures high-volume areas
- gap edges
- breakout-base boundaries
- round numbers

For every candidate, output:

```text
nearest_upside_liquidity
nearest_downside_liquidity
distance_to_breakout
distance_to_invalidation
room_to_next_resistance
room_to_next_support
```

### Why

A breakout with only 0.2 ATR of room before the next major resistance is inferior to one with 1.5 ATR of clean air.

---

# 5. Early Breakout / Breakdown State Machine

Implement one state machine per symbol and direction.

```text
IDLE
  ↓
DEVELOPING
  ↓
PRE_BREAKOUT / PRE_BREAKDOWN
  ↓
READY
  ↓
ARMED
  ↓
TRIGGERED
  ↓
CONFIRMED
  ├── CONTINUATION
  └── FAILED / TRAP
```

## IDLE

No meaningful setup.

## DEVELOPING

Early evidence exists:

- compression
- accumulation/distribution
- RS change
- unusual OI behavior

but not enough confluence.

## PRE_BREAKOUT / PRE_BREAKDOWN

Multiple independent evidence families agree.

Price has **not** broken the trigger.

This is the key new state.

## READY

High probability, good context, trigger reasonably close.

## ARMED

Strong setup and price within a small normalized distance from trigger.

Example normalized metric:

```text
trigger_distance_atr =
abs(trigger_price - spot) / ATR
```

## TRIGGERED

Price crosses trigger.

Do not instantly call it confirmed.

## CONFIRMED

Require quality conditions such as:

- price acceptance beyond trigger
- sufficient participation
- spread remains healthy
- option-chain positioning has not sharply reversed
- no immediate liquidity rejection
- market/sector context remains acceptable

## FAILED / TRAP

Examples:

- breakout crosses trigger then closes back inside range
- volume spike without follow-through
- OI wall rapidly rebuilds against direction
- bid/ask deteriorates
- sector reverses
- price loses AVWAP/VWAP immediately

Track trap outcome explicitly.

---

# 6. New Scoring Architecture

Do not maintain one generic “conviction” number assembled from dozens of equal votes.

Compute **bullish and bearish scores independently**.

```text
bull_score: 0–100
bear_score: 0–100
```

Recommended initial component weights:

| Component | Weight |
|---|---:|
| Structure + trigger proximity | 12 |
| Accumulation / distribution | 18 |
| Volume / participation | 12 |
| Microstructure pressure | 10 |
| Futures + options positioning | 20 |
| Compression / expansion readiness | 8 |
| Relative strength + sector/market | 10 |
| Sentiment / catalyst | 10 |
| **Total** | **100** |

Then apply penalties separately.

## Penalty examples

Up to configurable negative points for:

- stale data
- poor liquidity
- spread expansion
- contradictory market regime
- event uncertainty
- gap exhaustion
- proximity to major opposing liquidity
- abnormal expiry behavior
- signal crowding/correlation
- recent repeated false breaks
- insufficient sample size

---

# 7. Score Is Not Probability

Do **not** display:

```text
score 84 = 84% breakout probability
```

unless calibration proves this.

Use the score as a ranking input.

Then map features/model output to calibrated probability:

```text
P(breakout within horizon)
P(breakdown within horizon)
P(false breakout)
P(target before invalidation)
```

Use:

- Platt scaling
- isotonic regression
- calibration curves

Track:

- Brier score
- expected calibration error
- reliability diagram

A displayed probability must have empirical meaning.

---

# 8. Direct Verdict Engine

The user should not need to interpret 40 indicators.

Every candidate should end with a concise decision contract.

Example:

```json
{
  "symbol": "RELIANCE",
  "horizon": "INTRADAY",
  "direction": "BULLISH",
  "state": "PRE_BREAKOUT",
  "verdict": "LONG READY",
  "score": 82,
  "prob_breakout": 0.71,
  "prob_false_breakout": 0.18,
  "trigger": 3012.5,
  "invalidation": 2988.0,
  "trigger_distance_atr": 0.28,
  "market_context": "SUPPORTIVE",
  "sector_context": "STRONG",
  "accumulation": "STRONG",
  "derivatives": "BULLISH_CONFIRMING",
  "sentiment": "MILDLY_POSITIVE",
  "option_tradeability": "PASS",
  "top_reasons": [
    "3-stage volatility contraction",
    "relative strength making a new 20-day high",
    "put wall strengthening below spot",
    "near-ATM call wall weakening",
    "5m relative volume accelerating",
    "price holding above anchored VWAP"
  ],
  "risks": [
    "major weekly resistance 0.9 ATR above"
  ]
}
```

Dashboard text:

```text
RELIANCE — LONG READY
Breakout probability: 71%
Trigger: ₹3,012.50
Invalidation: ₹2,988

WHY:
✓ accumulation strong
✓ 3-stage compression
✓ RS leadership
✓ put support strengthening
✓ call resistance weakening
✓ volume accelerating
✓ sector supportive

RISK:
! weekly resistance 0.9 ATR above
```

---

# 9. Separate Intraday and Short-Swing Models

Do not use the same thresholds/model for every holding period.

## Intraday model

Primary information:

```text
1m / 3m / 5m / 15m
order book
time-of-day RVOL
VWAP
intraday AVWAP
futures ΔOI
option-chain velocity
sector/index intraday breadth
news shock
```

Example prediction horizon:

```text
break within next 15–60 minutes
```

## Short-swing model

Primary information:

```text
15m / 1h / 1D
multi-day compression
daily/weekly AVWAP
delivery history
futures positioning
multi-session OI
RS 5D/20D
sector leadership
event sentiment
```

Example prediction horizon:

```text
break within next 1–3 sessions
```

Separate models prevent high-frequency noise from contaminating swing signals.

---

# 10. Delivery-Based Accumulation — Use Correctly

NSE publishes delivery-related security-wise information for equities.

Use delivery as an **EOD / multi-day accumulation feature**, not an intraday real-time feature unless a valid licensed real-time source exists.

Features:

```text
delivery_percentile_20d
delivery_percentile_60d
delivery_value_zscore
price_up_delivery_up
price_flat_delivery_up
multi_day_delivery_accumulation
```

### Do not claim

“High delivery = institutional accumulation.”

It can support an accumulation hypothesis, but it does not identify participant type by itself.

---

# 11. Features to Demote or Remove From Primary Voting

The goal is not to delete useful information. The goal is to remove **duplicate influence**.

# 11.1 Raw RSI Threshold Voting — Demote

Do not give a strong standalone point because:

```text
RSI > 60
```

RSI can remain as a momentum descriptor/divergence feature.

### Why

RSI often duplicates price momentum already captured by structure, RS, MA slope and breakout proximity.

---

# 11.2 MACD Crossover — Demote

Keep for descriptive trend/momentum features if ablation proves value.

Do not let:

```text
MACD bullish crossover = +X conviction
```

### Why

It is lagging and highly correlated with other trend measures.

---

# 11.3 Supertrend as Direct Signal — Demote

Use Supertrend primarily for regime/trailing context.

### Why

It often confirms after a move is already underway.

---

# 11.4 Large Candlestick Pattern Library — Demote

Keep only patterns with proven incremental information coefficient after controlling for:

- trend
- volatility
- location
- volume
- regime

A candle pattern at random chart location should contribute almost nothing.

### Better representation

```text
pattern
× location quality
× volume quality
× regime
× follow-through history
```

---

# 11.5 Static PCR Threshold — Remove as Standalone Signal

Remove:

```text
PCR > X = bullish
PCR < Y = bearish
```

Replace with:

- PCR change
- weighted near-ATM PCR
- expiry-specific PCR
- PCR z-score
- strike migration

---

# 11.6 Static Max Pain Direction — Remove as Standalone Signal

Max Pain can remain as:

- expiry context
- magnet/risk feature
- distance feature
- intraday drift feature

Do not use:

```text
spot below max pain → must rise
```

or inverse.

---

# 11.7 “Highest Call OI = Resistance” as Hard Truth — Remove

Replace with dynamic wall state:

```text
strengthening
weakening
migrating
consumed
abandoned
```

---

# 11.8 Equal-Weight Indicator Voting — Remove

Do not do:

```text
RSI bullish +1
MACD bullish +1
Supertrend bullish +1
EMA bullish +1
BOS bullish +1
```

These signals are correlated and create false confidence.

Use feature groups and incremental validation.

---

# 11.9 Duplicate Strategy Alerts — Merge

Multiple strategies detecting the same underlying event must merge into one **signal episode**.

Example:

```text
VCP breakout
VWAP reclaim
BOS
volume breakout
range breakout
```

may all describe the same price event.

Output one episode with evidence, not five trades.

---

# 11.10 Classic Dashboard Active Feature Parity — Eventually Remove

Maintain Classic for stability temporarily.

New intelligence features should eventually target the New shell first.

### Why

Building and validating every advanced visualization twice will slow development and create CSS/state divergence.

---

# 12. Features to Keep But Reposition

| Feature | New role |
|---|---|
| BOS / CHoCH | Structural state |
| FVG / order blocks | Liquidity/location evidence |
| Wyckoff | Accumulation/distribution evidence |
| VCP | Compression/expansion readiness |
| VWAP | Intraday fair-value anchor |
| EMAs | Regime / trend state |
| RSI | Momentum/divergence descriptor |
| MACD | Secondary momentum descriptor |
| Supertrend | Regime/trailing context |
| CPR/pivots | Liquidity/location map |
| Fibonacci | Secondary confluence only |
| Candlestick patterns | Contextual micro-pattern evidence |
| Max Pain | Expiry context |
| PCR | Dynamic derivatives feature |
| OI support/resistance | Dynamic wall model |
| ML classifier | Meta-probability / calibration |
| Half-Kelly | Risk sizing after signal quality |
| Breadth | Market/sector context |

---

# 13. Breakout Target Definition for Machine Learning

The model must learn a precise target.

Bad label:

```text
did stock break resistance?
```

Better label:

```text
Given information available at timestamp T,
did price cross the defined trigger within H
and achieve MFE >= X ATR
before MAE >= Y ATR,
without a false-break condition?
```

Example intraday research label:

```text
horizon = 45 minutes
trigger = pre-existing resistance
success = +0.75 ATR beyond trigger before -0.50 ATR invalidation
```

Example short-swing research label:

```text
horizon = 3 sessions
success = +1.50 ATR before -0.75 ATR
```

These numbers are **research starting points**, not permanent thresholds.

Calibrate them by symbol liquidity, regime and realized volatility.

---

# 14. ML Architecture

Do not immediately replace logistic regression with a black box.

Use a model ladder:

## Level 1

Logistic regression baseline.

Purpose:

- interpretability
- calibration baseline
- leakage detection

## Level 2

Gradient boosted trees:

- LightGBM
- XGBoost
- CatBoost

Purpose:

- nonlinear interactions
- missing-value robustness
- feature importance

## Level 3

Sequence models only if proven necessary.

Possible research:

- temporal convolution
- LSTM/GRU
- compact transformer

Do not deploy because they are “advanced.”

Deploy only if walk-forward performance improves after costs and remains calibrated.

## Meta-model

Instead of predicting direction from raw prices alone, train the meta-model to answer:

> “Should this already-detected setup be trusted?”

This generally makes debugging easier.

---

# 15. Training Dataset Requirements

Every prediction row must be reproducible.

Store:

```text
symbol
timestamp
horizon
setup_id
episode_id
feature_version
strategy_version
model_version
market_regime
sector_regime
spot_snapshot
futures_snapshot
option_chain_snapshot_id
news_snapshot_id
feature_vector
trigger
invalidation
score_components
raw_model_probability
calibrated_probability
outcome
MFE
MAE
time_to_trigger
time_to_MFE
false_break
net_option_return
estimated_cost
```

No future data may enter the feature vector.

---

# 16. Leakage Controls

Maintain and strengthen:

- purged CV
- embargo
- walk-forward splits

Also enforce:

## Snapshot immutability

The feature snapshot used at signal time must never be recalculated later using revised information.

## News-time integrity

Use actual publication/availability timestamp, not a later database timestamp.

## Option snapshot integrity

Persist the exact chain state used for the verdict.

## Universe integrity

Avoid survivorship bias when testing historical F&O universes.

---

# 17. Evaluation Metrics

Accuracy is not enough.

Track:

## Prediction quality

- precision
- recall
- precision@K
- ROC-AUC
- PR-AUC
- Brier score
- calibration error

## Trading quality

- target-first rate
- stop-first rate
- false-break rate
- MFE
- MAE
- expectancy in R
- net-of-cost expectancy
- option-premium expectancy
- slippage-adjusted expectancy

## Early-detection quality

This must become a first-class metric:

```text
lead_time_to_breakout
```

Measure:

- median warning lead time
- 25th/75th percentile
- lead time by score bucket
- lead time vs false-positive rate

A model that detects a breakout 20 minutes early at 65% precision may be more useful than one that detects it 30 seconds early at 75%.

## Ranking quality

Because the scanner may find many candidates:

```text
Precision@1
Precision@3
Precision@5
Precision@10
```

This matters more operationally than global accuracy.

---

# 18. False-Breakout / Trap Model

Build a dedicated trap probability model.

Features:

- trigger distance vs ATR
- first-cross volume quality
- close acceptance
- wick rejection
- depth reversal
- spread widening
- OI wall rebuilding
- negative RS divergence
- sector divergence
- breakout after extended move
- breakout directly into higher-timeframe resistance
- news-driven gap exhaustion
- option IV spike without spot follow-through

Output:

```text
P(false_breakout)
P(false_breakdown)
```

A setup can have:

```text
P(breakout) = 0.74
P(false_breakout) = 0.38
```

That should not receive an A+ verdict.

---

# 19. Divergence Engine

Add structured divergences across evidence families.

Examples:

## Bullish hidden accumulation

```text
Price flat
Volume elevated
RS rising
put support strengthens
call wall weakens
VWAP reclaimed repeatedly
downside attempts fail
```

## Bearish hidden distribution

```text
Price flat
up-volume weakens
RS deteriorates
call wall strengthens
put support weakens
VWAP repeatedly rejected
rallies show poor follow-through
```

## Trap divergence

```text
Price new high
RS not new high
volume below expected
futures OI weak
call IV jumps
book imbalance turns negative
```

These divergences should be displayed as explicit reasons.

---

# 20. Feature Correlation and Redundancy Control

Create feature clusters.

Example:

```text
momentum_cluster
trend_cluster
volatility_cluster
volume_cluster
structure_cluster
derivatives_cluster
sentiment_cluster
microstructure_cluster
```

Within each cluster:

- calculate correlation
- mutual information
- information coefficient
- feature ablation
- SHAP stability where appropriate

Avoid allowing 10 correlated trend features to dominate one independent option-chain warning.

---

# 21. Dynamic Weighting

Do not freeze one weight system forever.

Weights can depend on:

```text
regime
time of day
expiry distance
symbol liquidity
intraday vs swing
event state
```

Example:

Opening 15 minutes:

```text
higher weight:
volume surprise
order book
gap context
opening range
news

lower weight:
slow indicators
```

Mid-session compression:

```text
higher weight:
compression
RS
OI migration
AVWAP
volume dry-up
```

Near expiry:

```text
higher caution:
gamma sensitivity
IV distortions
rapid OI shifts
```

---

# 22. Time-of-Day Model

Build explicit session regimes:

```text
PRE_OPEN
OPEN_DISCOVERY
MORNING_TREND
MIDDAY
AFTERNOON_EXPANSION
CLOSING
```

Why:

A 3× volume spike at 09:18 means something different from the same metric at 13:10.

Normalize:

- volume
- spread
- volatility
- depth
- price impact

against time-of-day historical distributions.

---

# 23. Candidate Ranking

The dashboard should not primarily sort by latest signal time.

Default rank should be:

```text
expected_opportunity_score
```

Possible formula:

```text
ranking_score =
calibrated_probability
× expected_reward_quality
× liquidity_quality
× regime_fit
× data_quality
× (1 - trap_probability)
```

Use normalized components.

Provide filters:

```text
Intraday
Short Swing
Bullish
Bearish
Pre-breakout only
Armed only
Confirmed only
A+/A only
Option tradeable only
Fresh catalyst only
```

---

# 24. Verdict Grades

Suggested initial representation:

```text
A+  = elite setup
A   = strong
B   = watch
C   = weak / informational
REJECT = no trade
```

Do not hard-code final thresholds until calibration is complete.

Example starting state thresholds:

```text
<55      NO EDGE
55–64    DEVELOPING
65–74    PRE-BREAKOUT WATCH
75–84    READY
85+      ARMED candidate
```

But ARMED must also satisfy:

- trigger proximity
- liquidity
- data quality
- no hard-risk gate

Score alone cannot arm a trade.

---

# 25. Explanation Engine

Every verdict must be explainable using the same feature snapshot used by the model.

Return:

```text
top_3_positive_reasons
top_3_negative_reasons
hard_gates
confidence
data_freshness
```

Do not allow an LLM to invent the explanation.

The LLM/AI advisor may convert structured evidence into plain language, but the facts must come from deterministic feature outputs.

---

# 26. Portfolio-Level Guardrails — Mandatory Before Capital Scaling

Build this now rather than later.

Track:

- total open risk
- total directional delta
- sector concentration
- correlated positions
- index beta
- single-stock exposure
- expiry concentration
- option gamma exposure
- daily loss budget
- consecutive losses
- strategy concentration

Example:

```text
HDFCBANK CE
ICICIBANK CE
AXISBANK CE
BANKNIFTY CE
```

should not be treated as four unrelated trades.

Create:

```text
portfolio_fit_score
```

and allow:

```text
GOOD SETUP — REJECTED DUE TO PORTFOLIO CORRELATION
```

---

# 27. Alert Design

Replace noisy strategy alerts with state-transition alerts.

Examples:

```text
RELIANCE
DEVELOPING → PRE-BREAKOUT
Bull score 68 → 76
Reason: RS acceleration + call wall weakening
```

```text
RELIANCE
PRE-BREAKOUT → ARMED
Spot now 0.21 ATR below trigger
Volume participation increasing
```

```text
RELIANCE
ARMED → TRIGGERED
₹3,012.50 crossed
Awaiting acceptance
```

```text
RELIANCE
TRIGGERED → CONFIRMED
Volume +38% vs TOD baseline
Call wall consumed
Sector remains strong
```

```text
RELIANCE
TRIGGERED → FAILED
Price returned below base
Trap probability 67%
```

This is much more useful than repeated BUY alerts.

---

# 28. Episode Freezing

Retain the current frozen signal principle and extend it.

At transition to `READY` or `ARMED`, freeze:

```text
episode_id
setup_start
reference_range
trigger
invalidation
initial_targets
baseline_feature_snapshot
initial_option_contract
```

Live metrics may update, but historical episode values must remain visible.

If the setup materially changes, close the episode and create a new one.

---

# 29. Proposed Service / Module Changes

Avoid unnecessary microservice explosion.

Recommended architecture:

```text
services/
  ingestion/
  feature-engine/
      price_structure/
      accumulation/
      volatility/
      relative_strength/
      microstructure/
      derivatives/
      context/
  sentiment-engine/       # new, isolated because NLP/news lifecycle differs
  scanner/
      state_machine/
      candidate_ranker/
      trap_model/
      option_tradeability/
      verdict_engine/
  api/
  archiver/
  scheduler/
  dashboard/
```

Possible future extraction:

```text
derivatives-engine/
```

only if load/latency independently demands it.

### Why

Start as modules within existing bounded services. Split only when operational evidence requires independent scaling.

---

# 30. Redis / Postgres Responsibilities

## Redis

Use for hot state:

- latest quote
- latest depth
- current candles
- current option-chain snapshot
- candidate state
- score components
- active episode
- latest sentiment impact
- market/sector context
- freshness flags

## Postgres

Use for durable research:

- feature snapshots
- signal episodes
- state transitions
- option snapshots
- news/event records
- model predictions
- calibrated probabilities
- outcomes
- training labels
- experiment versions

---

# 31. Suggested New Tables

```text
market_feature_snapshots
derivative_snapshots
option_strike_snapshots
news_events
sentiment_scores
scanner_episode_states
verdict_snapshots
trap_predictions
model_predictions
model_calibrations
feature_versions
experiment_runs
portfolio_risk_snapshots
```

## Key rule

Every verdict must be reproducible from persisted snapshot IDs.

---

# 32. Dashboard Redesign

The advanced scanner should become **decision-first**, not indicator-first.

## Main table

Recommended columns:

```text
Symbol
Mode
Verdict
State
Direction
Probability
Trap Risk
Score
Trigger Distance
Accumulation
Derivatives
RS
Volume
Sentiment
Sector
Option Quality
Data Quality
Age
```

Do not show 30 indicator columns by default.

## Expand row

Sections:

### Why Now?
- top evidence

### Price Structure
- base
- trigger
- invalidation
- clean-air distance

### Accumulation
- compression
- CLV
- RVOL
- AVWAP
- absorption

### Derivatives
- futures state
- OI velocity
- call wall
- put wall
- PCR change
- IV/skew

### Sentiment
- latest event
- impact
- freshness

### Risk
- trap probability
- portfolio correlation
- liquidity warning

### Option
- selected contract
- tradeability
- spread
- delta
- theta
- IV

---

# 33. Candidate Timeline UI

Add an evidence timeline:

```text
10:05 Accumulation detected
10:18 RS improved to top 10%
10:24 Put support strengthened
10:31 Call wall weakened
10:36 RVOL accelerated
10:39 PRE-BREAKOUT
10:44 ARMED
10:47 Trigger crossed
10:49 CONFIRMED
```

This will make the scanner far easier to trust and debug.

---

# 34. “Why Not?” Rejection UI

For rejected setups show the exact reason.

Example:

```text
TATAMOTORS — NO TRADE

Bull evidence: 78
Rejected because:
- option spread too wide
- sector breadth weak
- major resistance only 0.24 ATR above trigger
- trap risk 48%
```

A mature scanner should explain **why it refused a trade**, not only why it likes one.

---

# 35. Backtest / Replay Infrastructure Upgrade

Add event replay using stored tick/candle/derivative snapshots.

The same production feature code should be used in replay.

Avoid separate “backtest indicator logic” and “live indicator logic.”

Replay must support:

```text
market time progression
option snapshot progression
news arrival time
state transitions
candidate promotion
alert generation
episode freezing
```

---

# 36. Walk-Forward Validation Matrix

Evaluate by:

```text
market regime
sector
symbol liquidity
time of day
expiry distance
intraday vs swing
bull vs bear
news vs no-news
high/low volatility
score bucket
```

Do not accept a strategy merely because aggregate performance is good.

---

# 37. Feature Ablation Gate

Before a new feature enters production:

1. implement
2. calculate historical snapshots
3. verify no leakage
4. walk-forward test
5. calculate incremental precision/expectancy
6. ablate it
7. inspect regime stability
8. verify latency/cost
9. ship only if incremental value exists

Remove features that repeatedly contribute no incremental predictive value.

---

# 38. Shadow Mode

Every new model/feature should enter:

```text
SHADOW
```

before it can alter user-facing verdicts.

Shadow mode records:

- score
- proposed state
- probability
- expected action

but does not influence live verdict.

Promote only after sufficient real unseen outcomes.

---

# 39. Champion / Challenger Models

Maintain:

```text
champion_model
challenger_model
```

Both evaluate the same live candidates.

Only champion drives the verdict.

Challenger is promoted if it shows:

- better net-of-cost expectancy
- acceptable precision
- better calibration
- equal/better drawdown
- stable regime performance

---

# 40. Strategy Decay Monitoring

A previously good feature/model can stop working.

Track rolling:

```text
30-trade
60-trade
120-trade
20-day
60-day
```

performance.

Detect:

- precision decay
- expectancy decay
- calibration drift
- feature distribution shift
- increased false-break rate

Verdict engine should reduce model weight or quarantine a strategy when decay is statistically meaningful.

Do not automatically retrain purely because recent P&L is negative.

---

# 41. Data Drift Monitoring

For important features compare live distributions with training distributions.

Examples:

- PSI
- KS statistic
- z-score drift
- missingness drift

Trigger:

```text
MODEL DEGRADED
```

when live conditions move substantially outside known distributions.

---

# 42. Hard Gates

Some conditions should not be “minus five points.”

They should block a trade.

Examples:

```text
F&O ban
market closed
stale underlying quote
stale option quote
invalid contract mapping
insufficient option liquidity
extreme spread
missing trigger/invalidation
data-quality failure
portfolio risk limit exceeded
```

---

# 43. Do Not Build These as “Advanced Features”

Avoid misleading sophistication.

Do not add:

- dozens of more retail indicators
- astrology/time-cycle predictions
- unsupported “smart money” labels
- dealer gamma claims without dealer-side data
- “AI predicted institutional buy” labels
- sentiment based only on social-media keyword counts
- a huge deep-learning model before labels are proven
- automatic parameter optimization directly into production
- one universal breakout threshold for all stocks

---

# 44. Verified Data Capabilities Relevant to the Design

As of the document preparation date, Upstox documentation supports the following capabilities relevant to this architecture:

- Market Data Feed V3 with real-time WebSocket data
- multiple feed modes including richer market depth
- option Greeks
- full market quotes with bid/ask depth
- put/call option-chain retrieval
- historical and intraday candle APIs
- expired-contract historical candles
- OI data
- Max Pain data
- instrument-specific market news
- market/exchange status

NSE also publishes:

- option-chain information
- derivatives reports
- change in OI / contract-wise data
- equity security-wise price/volume and delivery information

### Engineering instruction

API contracts and limits can change. Keep provider adapters versioned and never hard-code undocumented behavior.

---

# 45. Data Source Priority

Suggested source hierarchy:

## Real-time market data
1. Upstox WebSocket
2. REST snapshot fallback

## Options
1. Upstox option chain / Greeks / market feed
2. NSE official reports for validation/EOD research where permitted

## News
1. Upstox News API
2. additional licensed trusted providers behind adapter

## Delivery/EOD
1. NSE official reports

---

# 46. Sentiment Model Research Note

FinBERT is suitable as a baseline finance-domain sentiment model because it is specifically trained/fine-tuned for financial language.

However:

- sentiment alone does not predict price reliably,
- event classification matters,
- freshness matters,
- source credibility matters,
- duplicate articles must not multiply evidence.

Use sentiment as one component of the larger evidence graph.

---

# 47. Proposed Evidence Graph

Instead of a flat indicator vector, internally represent evidence like:

```text
BULLISH BREAKOUT HYPOTHESIS
│
├── Accumulation
│   ├── rising CLV
│   ├── pullback volume drying
│   └── AVWAP holding
│
├── Compression
│   ├── ATR percentile low
│   ├── VCP sequence
│   └── range tightening
│
├── Participation
│   ├── RVOL acceleration
│   └── positive volume imbalance
│
├── Derivatives
│   ├── futures long-build candidate
│   ├── put support strengthening
│   └── call wall weakening
│
├── Context
│   ├── sector strong
│   └── RS leadership
│
└── Catalyst
    └── positive high-relevance news
```

Contradicting evidence sits in the same graph.

This makes explanation and debugging easier.

---

# 48. Pre-Breakout Feature Snapshot

At each candidate evaluation persist:

```text
{
  "spot": {},
  "structure": {},
  "accumulation": {},
  "compression": {},
  "volume": {},
  "microstructure": {},
  "futures": {},
  "options": {},
  "relative_strength": {},
  "sector": {},
  "market": {},
  "sentiment": {},
  "liquidity_map": {},
  "risk": {},
  "data_quality": {}
}
```

This becomes the single source of truth for:

- rule engine
- ML
- explanation
- replay
- dashboard
- audit

---

# 49. Suggested Initial Derived Features

## Structure

```text
distance_to_range_high_atr
distance_to_range_low_atr
higher_low_count
lower_high_count
swing_compression_ratio
bos_age
choch_age
base_duration
base_depth_atr
clean_air_up_atr
clean_air_down_atr
```

## Volume

```text
rvol_tod
volume_zscore
up_down_volume_ratio
volume_acceleration
pullback_volume_ratio
breakout_prevolume_ratio
```

## Accumulation

```text
clv_ema
clv_volume_weighted
upper_quartile_close_rate
lower_quartile_close_rate
avwap_hold_count
support_absorption_score
distribution_score
```

## Compression

```text
atr_percentile
bbw_percentile
range_contraction_score
vcp_stage_count
inside_cluster_score
```

## Microstructure

```text
book_imbalance_5
book_imbalance_30
imbalance_persistence
spread_bps
spread_percentile
bid_replenishment
ask_replenishment
liquidity_pull_score
```

## Futures

```text
fut_oi_change
fut_oi_velocity
fut_oi_acceleration
basis
basis_change
fut_volume_zscore
fut_position_state
```

## Options

```text
near_atm_pcr
pcr_velocity
call_wall_score
put_wall_score
call_wall_velocity
put_wall_velocity
wall_migration_score
atm_iv
iv_velocity
skew
skew_velocity
option_volume_zscore
```

## Context

```text
rs_index
rs_sector
rs_acceleration
sector_breadth
market_breadth
index_regime
sector_regime
```

## Sentiment

```text
sentiment_direction
sentiment_confidence
event_severity
novelty
source_quality
news_age
sentiment_impact
```

---

# 50. Anti-Double-Counting Rules

Examples:

- EMA stack + MACD + Supertrend should not count as three independent trend confirmations.
- Raw OI + PCR + max call OI may derive from the same chain and should not count as three independent derivatives confirmations.
- BOS + range breakout + day-high breakout can describe the same structural event.
- volume spike + RVOL + volume z-score are related measures.

Score by **evidence family**, not raw feature count.

---

# 51. Contradiction Handling

A mature scanner must tolerate mixed evidence.

Example:

```text
Technical bullish
Derivatives neutral
Sector bullish
Sentiment negative
```

Do not force every feature into bullish/bearish.

Allow:

```text
BULLISH
BEARISH
NEUTRAL
UNKNOWN
STALE
```

for each evidence family.

Unknown must not be treated as neutral.

---

# 52. Confidence vs Strength

Keep these separate.

## Strength

How strongly current evidence supports the hypothesis.

## Confidence

How reliable the estimate is given:

- data quality
- sample size
- model calibration
- regime familiarity
- feature completeness

Example:

```text
Strength: 90
Confidence: 58
```

should not be displayed as an elite trade.

---

# 53. Minimum Sample Rules

Do not display aggressive probability claims from tiny samples.

Track sample counts by:

```text
model
regime
symbol bucket
setup family
direction
horizon
```

Use uncertainty intervals where useful.

---

# 54. Option Contract Selection

After bullish verdict:

rank CE contracts.

After bearish verdict:

rank PE contracts.

Optimize for:

```text
liquidity
delta suitability
spread
theta burden
IV
expected move
premium risk
```

Do not default blindly to ATM.

Possible profiles:

```text
INTRADAY_FAST
INTRADAY_BALANCED
SWING_BALANCED
```

Each can prefer a different delta/expiry combination.

---

# 55. Reward/Risk Must Use Underlying and Premium

Display two layers:

```text
Underlying:
Entry
Invalidation
T1/T2/T3

Option:
Premium reference
Premium invalidation
Estimated premium targets
Spread/slippage
```

Option-premium targets should be recomputed from actual contract behavior models, not a simple fixed multiplier.

---

# 56. Outcome Tracking Expansion

For every early alert track:

```text
did_trigger
time_to_trigger
did_confirm
false_break
MFE_before_trigger
MAE_before_trigger
MFE_after_trigger
MAE_after_trigger
T1/T2/T3
stop
expiry
option_premium_MFE
option_premium_MAE
net_cost_return
```

This lets the system answer:

> Was the early warning useful even if the user did not enter until trigger?

---

# 57. Performance Dashboard

Add:

## Early Detection

- alerts
- trigger rate
- confirmation rate
- false-break rate
- median lead time
- precision@5

## By Verdict

```text
DEVELOPING
PRE-BREAKOUT
READY
ARMED
CONFIRMED
```

## By Evidence

- high accumulation
- high derivatives
- positive catalyst
- high RS
- strong sector

## By Regime

- trend
- range
- compression
- high-volatility

---

# 58. Development Priorities

## Phase EB-0 — Data Integrity

Build:

- event timestamps
- staleness
- gap detection
- data quality score
- snapshot reproducibility

**Do not continue if this is unreliable.**

---

## Phase EB-1 — Candidate State Machine

Build:

- developing
- pre-breakout
- ready
- armed
- triggered
- confirmed
- failed

No ML required initially.

Acceptance:

- state history reproducible
- no drifting triggers
- no duplicate episodes

---

## Phase EB-2 — Accumulation + Compression

Build:

- CLV
- RVOL TOD
- volume imbalance
- AVWAP
- VCP/compression
- absorption proxies

Acceptance:

- feature snapshots
- ablation results
- replay verification

---

## Phase EB-3 — Relative Strength + Context

Build:

- stock vs index
- stock vs sector
- sector breadth
- market breadth directional context

---

## Phase EB-4 — Futures Positioning

Build:

- price/OI state
- OI velocity
- OI acceleration
- basis behavior

---

## Phase EB-5 — Dynamic Option Chain

Build:

- strike wall scores
- wall migration
- weighted PCR
- IV/skew
- chain freshness
- option tradeability

---

## Phase EB-6 — Microstructure

Build:

- book imbalance
- spread state
- replenishment
- absorption proxy
- liquidity consumption

Use tiered subscriptions.

---

## Phase EB-7 — Sentiment

Build:

- Upstox News ingestion
- dedupe
- entity mapping
- event taxonomy
- FinBERT baseline
- relevance/novelty/decay

---

## Phase EB-8 — Unified Verdict

Build:

- bull score
- bear score
- penalties
- hard gates
- top reasons
- direct verdict

---

## Phase EB-9 — Trap Model

Build:

- false-break labels
- trap probability
- reversal state

---

## Phase EB-10 — ML Meta-Classifier

Start logistic baseline.

Then evaluate boosted trees.

Use calibrated probabilities.

---

## Phase EB-11 — Portfolio Risk

Build:

- correlation
- sector exposure
- directional delta
- risk budget
- portfolio fit

---

## Phase EB-12 — Dashboard Decision UX

Build:

- ranked candidate list
- why now
- why not
- evidence timeline
- state transitions
- direct verdict

---

## Phase EB-13 — Shadow Validation

Run new system without changing production alerts.

Collect unseen outcomes.

---

## Phase EB-14 — Champion Promotion

Promote only after walk-forward + live shadow evidence.

---

# 59. Acceptance Criteria for “Advanced Scanner”

Do not call the upgrade complete because the UI looks advanced.

The scanner is ready only if it demonstrates:

1. measurable pre-trigger lead time
2. acceptable false-positive rate
3. calibrated breakout probabilities
4. lower false-break rate than current scanner
5. improved precision@K
6. positive net-of-cost expectancy in tracked paper outcomes
7. stable performance across multiple market regimes
8. reproducible feature snapshots
9. no timestamp leakage
10. direct verdict explanations match underlying deterministic data
11. option contract rejection prevents poor-liquidity trades
12. state transitions do not repaint

---

# 60. What Success Should Look Like

Instead of:

```text
ABC
BUY
Conviction 86
RSI 68
MACD bullish
Supertrend bullish
Volume high
```

Infusion should produce:

```text
ABC — BREAKOUT ARMED

Bull Probability        76%
False-Break Risk        14%
Setup Grade             A
Trigger Distance        0.19 ATR
Expected Horizon        Intraday

ACCUMULATION            STRONG
COMPRESSION             STRONG
VOLUME PRESSURE         RISING
MICROSTRUCTURE          BULLISH
FUTURES POSITIONING     SUPPORTIVE
OPTION POSITIONING      SUPPORTIVE
RELATIVE STRENGTH       LEADER
SECTOR                   SUPPORTIVE
SENTIMENT                NEUTRAL
OPTION TRADEABILITY      PASS
DATA QUALITY             98/100

Trigger                  ₹512.40
Invalidation             ₹503.80
Next Liquidity           ₹526.00

WHY EARLY:
1. price compressed for 9 sessions with rising lows
2. 5m time-normalized relative volume accelerating
3. stock RS improving while sector is flat
4. near-ATM call wall weakening
5. put support migrating upward
6. futures OI + price behavior supports long buildup
7. price repeatedly defended above swing AVWAP

RISK:
- higher-timeframe supply at ₹526

VERDICT:
LONG READY — WAIT FOR ₹512.40 TRIGGER
```

That is the target behavior.

---

# 61. Final Engineering Direction

The next generation of Infusion should optimize for:

```text
EARLY EVIDENCE
    +
INDEPENDENT CONFIRMATION
    +
DERIVATIVES POSITIONING
    +
MARKET CONTEXT
    +
CATALYST AWARENESS
    +
LIQUIDITY / EXECUTION QUALITY
    +
FALSE-BREAK REJECTION
    +
CALIBRATED PROBABILITY
    =
DIRECT VERDICT
```

The project should **not** aim to predict every breakout.

It should aim to identify a **small ranked set of setups where multiple independent evidence families are building before the move**, provide enough lead time to prepare, and reject setups where the evidence is correlated, stale, illiquid, contradictory, or statistically weak.

The most valuable future improvement is not another indicator.

It is:

> **better evidence fusion, earlier state recognition, option-chain dynamics, accumulation/distribution modeling, microstructure pressure, false-break rejection, probability calibration, and disciplined trade rejection.**

---

# 62. Mandatory Principles for the AI Engineering Team

1. **No repainting.**
2. **No future leakage.**
3. **No OI-only “writer” assumptions.**
4. **No raw score presented as probability.**
5. **No equal-weight indicator voting.**
6. **No duplicate alerts for the same market episode.**
7. **No unsupported “institutional/smart-money” claims.**
8. **No option recommendation without tradeability validation.**
9. **No signal when data freshness fails.**
10. **No new production feature without ablation and walk-forward evidence.**
11. **No model promotion without shadow-mode unseen outcomes.**
12. **No live auto-execution; preserve advisory/paper-first architecture unless the project policy is explicitly changed later.**

---

# 63. Reference Basis

This blueprint extends the current **Infusion Project Abstract (updated 2026-08-19)**.

External capabilities were checked against current primary/official documentation during preparation, including:

- Upstox Developer API — Market Data Feed V3
- Upstox Developer API — Put/Call Option Chain
- Upstox Developer API — Option Greek Fields
- Upstox Developer API — Full Market Quotes
- Upstox Developer API — Historical / Intraday Candle V3
- Upstox Developer API — Open Interest Data
- Upstox Developer API — Max Pain
- Upstox Developer API — News API
- Upstox Developer API — Rate Limits
- NSE — Equity Derivatives Option Chain / OI reports
- NSE — Security-wise price, volume and delivery reports
- FinBERT research / finance-domain sentiment modeling

**Important:** Provider schemas, entitlements, subscription limits and exchange rules can change. The implementation team must verify current API documentation at integration time and keep adapters versioned.

---

# 64. Recommended Project Name for This Upgrade

```text
INFUSION EBIE
Early Breakout Intelligence Engine
```

Internal module family:

```text
EBIE-DQ     Data Quality
EBIE-ACC    Accumulation/Distribution
EBIE-VOL    Participation/Volume
EBIE-MICRO  Microstructure
EBIE-DERIV  Futures/Options Positioning
EBIE-RS     Relative Strength
EBIE-SENT   Sentiment/Catalyst
EBIE-CTX    Market/Sector Context
EBIE-TRAP   False-Break Detection
EBIE-OPT    Option Tradeability
EBIE-VERDICT Unified Decision Engine
```

This naming makes the upgrade separable, measurable and easy to implement phase by phase without destabilizing the existing Infusion core.
