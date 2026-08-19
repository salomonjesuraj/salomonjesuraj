# Infusion EBIE — Final Answers to AI Engineering Open Questions
## Project-Owner / Architecture Decisions

**Date:** 2026-08-19  
**Applies to:** `INFUSION-ADVANCED-EARLY-BREAKOUT-SCANNER-BLUEPRINT.md`  
**Responds to:** `EBIE Implementation — Open Questions for the AI Engineering Team`  
**Status:** **AUTHORIZED FOR IMPLEMENTATION WITH PHASE GATES**  
**Execution policy:** Advisory / paper-first only. No live auto-execution.

---

# 0. Executive Decision

The **complete EBIE roadmap (EB-0 through EB-14) is authorized**.

However, authorization for the full roadmap does **not** mean “build all 15 phases in one uncontrolled run.” The existing Infusion engineering discipline must remain:

```text
implement
→ test
→ replay/backtest where applicable
→ verify with live/paper data
→ inspect negative findings
→ commit
→ continue to next phase
```

The goal is not an MVP that permanently stops at EB-3. The goal is the **full advanced scanner**, delivered through measurable milestones.

The AI engineering team may proceed without asking again for architectural approval on every phase **as long as it stays within the decisions in this document**. Stop and raise a new decision only if:

- an external provider capability materially differs from the verified contract,
- a phase requires live order execution,
- a paid dependency is required and no free/approved fallback exists,
- a database migration would destroy or rewrite historical outcome data,
- a proposed change violates paper-first/no-repainting/no-leakage rules,
- or real validation proves a blueprint assumption is materially wrong.

---

# 1. Scope

## Q1.1 — Full 15-phase build or MVP subset first?

### Decision: **Full 15-phase roadmap is authorized, implemented through gated milestones.**

Do **not** stop permanently after EB-3.

Use four implementation milestones:

### Milestone A — Foundation
```text
EB-0 Data Integrity
EB-1 Canonical State Machine
EB-2 Accumulation / Compression / AVWAP
EB-3 Relative Strength + Market/Sector Context
```

**Checkpoint:** The scanner must identify developing/pre-breakout states reproducibly without ML, derivatives, sentiment, or D30 depth.

### Milestone B — Advanced Evidence
```text
EB-4 Futures Positioning
EB-5 Dynamic Option Chain
EB-6 Microstructure
EB-7 Sentiment / Catalyst
```

**Checkpoint:** Evidence families must be independently testable and must not double-count correlated information.

### Milestone C — Decision Intelligence
```text
EB-8 Unified Verdict
EB-9 Trap / False-Break Model
EB-10 ML Meta-Classifier + Calibration
```

**Checkpoint:** The engine must rank, explain, reject, and track early signals using unseen outcomes.

### Milestone D — Product & Governance
```text
EB-11 Portfolio Risk
EB-12 New-Shell Decision UX
EB-13 Shadow Validation
EB-14 Champion / Challenger Promotion
```

**Checkpoint:** EBIE becomes the promoted canonical scanner only after shadow acceptance criteria are met.

### Instruction

Each phase remains independently committed and reversible. Do not batch multiple unverified phases into one opaque code change.

---

## Q1.2 — Must EBIE eventually use a transformer / sequence model?

### Decision: **No. There is no transformer mandate.**

The model ladder is evidence-based:

```text
Logistic regression
        ↓ only if demonstrably better
Boosted trees
        ↓ only if demonstrably better
Sequence model
```

The simplest model that produces the best **walk-forward, net-of-cost, calibrated, unseen performance** remains champion.

A sequence model or transformer must **not** be built merely to make EBIE appear more advanced.

It may be researched only if:

- the dataset is large enough for sequence learning,
- timestamp integrity is proven,
- simpler models have plateaued,
- sequence structure adds measurable incremental information,
- PR-AUC / precision@K / expectancy improve,
- calibration does not materially worsen,
- inference latency remains acceptable,
- and the result survives regime-sliced validation.

If boosted trees fail to beat logistic regression meaningfully, **stop at logistic regression**.

---

## Q1.3 — One implementer or parallel engineering?

### Decision: **One architecture owner; controlled parallelization only after shared contracts are frozen.**

Do not let multiple AI engineers independently create competing schemas or definitions.

Required ownership model:

```text
Architecture Owner
      │
      ├── Data / Ingestion
      ├── Feature Families
      ├── Derivatives
      ├── Sentiment
      ├── Modeling
      └── Dashboard
```

### Strictly sequential dependencies

The following must be completed in order:

```text
EB-0 → EB-1 → common feature snapshot contract
```

After that, parallelization is allowed for independent modules such as:

```text
EB-2 accumulation
EB-3 relative strength/context
EB-4 futures
EB-5 options
EB-7 sentiment
```

EB-6 microstructure can proceed when the upgraded ingestion/depth contract is ready.

EB-8 must not be finalized until the evidence-family interfaces from EB-2 through EB-7 are stable.

### Rule

Parallelize **implementation**, not architecture.

Shared contracts for:

- timestamps,
- feature snapshots,
- evidence states,
- symbol/contract identity,
- freshness,
- episode IDs,
- score schemas,
- and persistence

must have one canonical definition.

---

# 2. Current Capability Gaps

## Q2.1 — Multi-level market depth / Upstox entitlement

### Decision: **D5 is the required baseline. D30 is a Tier-3 enhancement and must not block EBIE.**

Current official Upstox V3 documentation uses these mode names:

```text
ltpc
option_greeks
full
full_d30
```

Important correction:

> Do not implement a provider mode named `full_d5` unless an actual current SDK contract uses that literal value. In current Upstox V3 documentation, **`full` contains 5 market levels**.

Current documented public capability:

- Normal V3 account:
  - 2 WebSocket connections per user
  - `full` mode with 5 market levels
  - `option_greeks`
- Upstox Plus:
  - up to 5 WebSocket connections
  - `full_d30`
  - D30 is restricted compared with normal feed modes
- `change_mode` is supported, so an already subscribed instrument can be promoted from a cheaper mode to `full` / `full_d30`.

### Account entitlement

The exact subscription status of the project owner's Upstox account is **not knowable from the repository**.

Therefore EB-0 must implement a capability/configuration record such as:

```json
{
  "provider": "upstox",
  "ws_connections_available": 2,
  "supports_full_d5": true,
  "supports_full_d30": false,
  "supports_option_greeks": true,
  "supports_news": true
}
```

Use the internal semantic field `supports_full_d5`, but map it to provider mode `full`.

### Implementation decision

1. Build EBIE microstructure first against **5-level `full` data**.
2. Make all D30 features capability-aware.
3. If Plus is available, dynamically promote only Tier-3 candidates to D30.
4. If Plus is not available, EBIE remains fully operational in D5 mode.
5. Never fabricate or approximate missing D30 levels.

### Cost authorization

Do **not** purchase or assume a paid plan automatically.

If the account does not have Plus and Plus is required for a desired D30 experiment:

```text
fetch/show exact current Upstox commercial terms
→ obtain owner approval
→ enable D30
```

D30 is not a prerequisite for EB-0 through EB-5.

---

## Q2.2 — Sentiment pipeline: self-host or hosted API?

### Decision: **Self-host the first production sentiment model. CPU first. No paid hosted NLP dependency initially.**

Use:

```text
Upstox News API
      ↓
dedupe
      ↓
entity/event classifier
      ↓
finance-domain sentiment model
      ↓
relevance / novelty / severity / decay
```

Recommended baseline:

- FinBERT or an equivalent finance-domain classifier
- CPU inference initially
- batched headline + summary inference
- cached by article fingerprint
- no GPU requirement until measured latency proves CPU inadequate

### Why

Self-hosting gives:

- reproducibility,
- no per-request NLP cost,
- version pinning,
- stable research replay,
- no external sentiment-provider drift,
- and control over model upgrades.

### GPU policy

Do not add GPU infrastructure initially.

Benchmark first.

A GPU becomes justified only if:

- candidate/news throughput exceeds CPU SLA,
- inference queues become material,
- or a later model genuinely needs acceleration.

---

## Q2.3 — Boosted-tree dependency and training architecture

### Decision: **Boosted-tree dependencies are approved, but training must not occur inside the API request process.**

The existing hand-written logistic model remains the baseline.

For Level 2 research, adding one mature boosted-tree library is acceptable.

Preferred design:

```text
scheduler / research training job
          ↓
versioned model artifact
          ↓
lightweight inference runtime
          ↓
scanner / verdict engine
          ↓
API only exposes result
```

### Rules

- `api` must not train models on HTTP request paths.
- Training should run as a scheduled/offline job.
- Model artifacts must be immutable and versioned.
- Every prediction stores `model_version`.
- The model must be loadable independently of training.
- Do not install every ML library. Select one primary boosted-tree stack first.
- Do not create an always-on ML microservice unless measured serving load justifies it.

### Suggested order

```text
existing logistic
→ sklearn-compatible calibrated logistic if needed
→ LightGBM/XGBoost/CatBoost experiment
→ choose one champion
```

Dependency additions are accepted when isolated and pinned.

---

## Q2.4 — Portfolio risk priority while paper-only

### Decision: **EB-11 remains authorized but is not allowed to delay the core early-detection engine.**

Order remains:

```text
early detection
→ derivatives
→ verdict
→ trap rejection
→ ML/calibration
→ portfolio risk
```

Portfolio risk is important because correlated paper signals can make the scanner appear more diversified than it is.

However, while Infusion remains paper-only:

- portfolio risk is **informational/advisory**,
- it does not suppress the underlying setup from research,
- it records both raw setup quality and portfolio-adjusted actionability.

Before any future live-capital mode, portfolio hard blocking becomes mandatory.

---

## Q2.5 — Futures LTP/OI/basis subscriptions

### Decision: **Treat futures positioning as new infrastructure and add explicit stock-futures subscriptions in EB-4.**

Do not assume the current options pipeline already provides sufficient futures positioning.

For each F&O underlying, maintain at least:

```text
spot instrument
current/near stock-futures contract
```

Near expiry, optionally add the next contract for rollover analysis.

Upstox V3 `full` feed exposes fields including:

```text
LTP
volume
OI
market depth
```

so EB-4 can derive:

```text
futures_oi
dOI
OI velocity
OI acceleration
basis = futures_ltp - spot_ltp
basis %
basis velocity
volume z-score
```

### Subscription policy

For the normal universe:

- current-month futures: subscribe continuously where capacity allows
- next-month futures: add only inside rollover window
- far futures: not required initially

### Rollover window

Make configurable; do not hard-code business logic permanently.

Example starting research window:

```text
last 5 trading sessions before expiry
```

### Verification

Before EB-4 code starts, perform a runtime audit of the current subscription list.

If stock-futures LTP/OI is already present, reuse it.

If not, add the instruments deliberately.

---

## Q2.6 — Probability calibration

### Decision: **Yes. Calibration is a standalone deliverable and verification gate.**

Early EBIE must **not** label an uncalibrated score as a probability.

Before calibration:

```text
EBIE Score: 82/100
Model Score: 0.78
```

Allowed.

Not allowed:

```text
78% breakout probability
```

unless calibration is validated.

### Calibration deliverable must include

- Platt scaling and/or isotonic comparison
- Brier score
- reliability curve
- expected calibration error
- probability bucket outcome table
- bullish and bearish calibration separately where necessary
- intraday and swing calibration separately
- out-of-sample calibration verification

### Promotion rule

Only after this gate can the dashboard show:

```text
Breakout Probability: 71%
```

If there is insufficient unseen sample for honest calibration, continue showing `score`, not fake probability.

---

# 3. Overlap With Existing Systems

## Q3.1 — EBIE state machine vs three existing early-state trackers

### Decision: **EBIE becomes the single canonical early-state machine. Do not create a permanent fourth competing state system.**

Migration approach:

```text
Existing systems
     ↓
compatibility / feature inputs
     ↓
EBIE shadow state machine
     ↓
compare outcomes
     ↓
EBIE promotion
     ↓
legacy state outputs deprecated
```

### Existing components

#### `PreBreakoutTracker`

Do **not** delete its useful logic immediately.

Refactor useful compression/accumulation observations into EBIE feature inputs.

Its state names must not remain a parallel user-facing authority after EBIE promotion.

#### `stock_breakout_tier`

During shadow:

```text
existing tier
vs
EBIE state
```

must be persisted for comparison.

After EBIE promotion, EBIE becomes the authoritative state.

The old tier may remain temporarily as a compatibility field, then be deprecated.

#### `radar_alerts`

Preserve its historical records.

Do not rewrite old outcomes.

New alerts should eventually use the EBIE episode/state lifecycle.

### Canonical EBIE states

```text
IDLE
DEVELOPING
PRE_BREAKOUT / PRE_BREAKDOWN
READY
ARMED
TRIGGERED
CONFIRMED
CONTINUATION
FAILED / TRAP
```

The final architecture must have **one state authority per symbol/direction/horizon/episode**.

---

## Q3.2 — Episode freezing

### Decision: **Extend and generalize the existing proven freeze mechanism. Do not build a second independent freeze system.**

Refactor the existing mechanism into a reusable shared component, conceptually:

```text
EpisodeManager
```

It should own:

```text
episode_id
direction
horizon
setup_start
reference_range
trigger
invalidation
targets
baseline_feature_snapshot_id
initial_option_contract
created_at
state
resolution
```

Use the same mechanism for:

- current watch strategies,
- EBIE READY,
- EBIE ARMED,
- triggered setups,
- option contract references.

### Rule

Live evidence may evolve.

The original signal ladder must not drift.

A materially changed setup creates a **new episode**, not a modified history.

---

## Q3.3 — Existing VCP vs EB-2 compression

### Decision: **Reuse VCP for swing. Build separate intraday compression logic. Unify only the interface, not the raw algorithm.**

Architecture:

```text
CompressionEvidence
    │
    ├── intraday implementation
    └── swing implementation
```

### Short swing

Reuse existing VCP as a major input:

- Stage-2 structure
- contraction quality
- volume dry-up
- pivot proximity
- existing RS

Do not rewrite a working daily VCP system merely for consistency.

### Intraday

Add genuinely new evidence:

- ATR/BB width compression
- CLV
- RVOL_TOD
- pullback-volume dry-up
- AVWAP holds
- range contraction
- intraday absorption proxy

### Output

Both should normalize to a common contract such as:

```json
{
  "horizon": "intraday",
  "compression_score": 78,
  "state": "COILING",
  "components": {}
}
```

---

## Q3.4 — Existing static PCR / Max Pain / OI S-R

### Decision: **Upgrade the existing options analytics boundary, shadow the dynamic version, then remove static reads from verdict voting.**

Do not maintain two permanent option analytics systems.

Recommended migration:

```text
options_analytics current
      ↓
options_analytics_v2 / dynamic engine
      ↓
shadow comparison
      ↓
verified cutover
```

### Keep for display/research

- raw PCR
- Max Pain
- absolute OI
- largest OI strikes

### Remove from primary verdict logic

- fixed PCR bullish/bearish thresholds
- “spot below Max Pain therefore bullish”
- “highest call OI equals hard resistance”
- “highest put OI equals hard support”
- OI-only writer/buyer labels

### New verdict inputs

- ΔOI
- OI velocity
- wall strength
- wall weakening
- wall migration
- near-ATM weighted PCR
- PCR velocity
- premium movement
- IV movement
- volume
- bid/ask quality
- distance from spot
- expiry

After EB-5 acceptance, static outputs remain informational only.

---

## Q3.5 — Signal Alignment Gate vs Unified Verdict

### Decision: **EB-8 subsumes the existing alignment gate. Do not keep two independent permanent veto systems.**

During EB-8 shadow:

```text
old alignment decision
new evidence-family decision
```

must both be logged.

After EBIE promotion:

- evidence-family independence becomes part of Unified Verdict,
- anti-double-counting is implemented there,
- the old alignment gate is retired as an independent blocker.

Useful alignment metrics may remain as features.

### Why

Two independent alignment blockers would create:

```text
good setup
→ old gate reject
→ new engine approve
```

without one authoritative explanation.

There must be one final decision contract.

---

## Q3.6 — Multi-anchor AVWAP sequencing

### Decision: **Multi-anchor AVWAP belongs in EB-2. Do not defer it.**

Initial anchors:

```text
session open
previous day high
previous day low
previous day close
week open
latest significant swing high
latest significant swing low
current consolidation/base origin
```

Phase-dependent anchors:

```text
event/news candle → add with EB-7
major gap event → add when gap classifier is stable
month open → include for swing if useful
```

### Why EB-2

AVWAP is part of the accumulation/absorption story and is useful to both intraday and swing evidence.

---

# 4. Architecture

## Q4.1 — New shell only or dual-shell parity?

### Decision: **All new EBIE UI is New-shell only. Classic is frozen to bug-fix/security/stability maintenance.**

Starting with EBIE:

```text
New shell = active product
Classic = legacy fallback
```

Do not spend engineering time implementing:

- EBIE evidence timeline,
- dynamic option positioning,
- sentiment,
- state-machine UX,
- trap probability,
- advanced verdict cards

twice.

### Classic retirement criterion

Do not retire Classic based on an arbitrary calendar date.

Classic can move to archived/fallback status after the New shell completes:

1. all non-EBIE critical operational functions,
2. EBIE production/shadow dashboard,
3. at least **20 consecutive trading sessions** without a Severity-1/Severity-2 shell regression,
4. acceptable memory/CPU/browser performance,
5. no unresolved live-data rendering mismatch,
6. successful dark/light theme verification,
7. successful user acceptance on the New shell.

Then:

- remove Classic from default navigation,
- preserve code/history for rollback for a defined release window,
- stop feature development permanently.

---

## Q4.2 — Sentiment as separate service or in-process module?

### Decision: **Build `sentiment-engine` as a separate service boundary from EB-7, CPU first.**

This is the one service split approved in advance.

Reason:

- Transformers/PyTorch dependencies are heavier than the existing API runtime.
- Model initialization has a different lifecycle.
- NLP failure should not destabilize price/scanner APIs.
- News can be cached and processed asynchronously relative to ticks.
- Future GPU use can be isolated.

### Contract

```text
sentiment-engine
  input:
    article_id
    heading
    summary
    published_time
    mapped_symbols

  output:
    event_type
    direction
    confidence
    severity
    relevance
    novelty
    source_quality
    impact
    model_version
```

If sentiment service fails:

```text
sentiment = UNKNOWN
```

not neutral, not zero, and not a scanner crash.

---

## Q4.3 — Redis/Postgres retention and capacity

### Decision: **Partition from day one. Do not persist every raw tick/depth state for all 208 symbols forever.**

Also note: the question calls this “12 new tables” but enumerates **13 proposed table names**. Do not create all 13 automatically just because they were listed; normalize where sensible.

### Storage policy

#### Redis — ephemeral/hot

Keep:

- latest quote
- latest depth
- rolling microstructure windows
- current feature state
- current candidate tier
- current option chain
- current sentiment
- active episode

No long-term research dependency may rely only on Redis.

#### Postgres — durable/reproducible

Persist:

- state transitions
- candidate feature snapshots
- verdict snapshots
- derivative snapshots used by a verdict
- option-chain snapshot references
- sentiment/event metadata
- model outputs
- episodes/outcomes
- model/calibration/experiment versions

### Retention classes

#### Permanent / indefinite

Keep indefinitely:

```text
signal episodes
state transitions
verdict at alert time
outcomes
MFE/MAE
model versions
feature versions
calibration metadata
experiment runs
news event IDs/metadata used by signals
portfolio risk decision at signal time
```

#### Hot research snapshots

Keep detailed candidate snapshots in Postgres for approximately:

```text
180 calendar days
```

then archive/compress older detailed snapshots if storage volume warrants it.

#### Tier-1 universe snapshots

Do not snapshot option chain/depth for every symbol on every tick.

Use aggregated/sampled features.

Initial target:

```text
1-minute normalized Tier-1 feature snapshot
90–120 trading days hot
```

#### Raw/deep depth

Do not permanently store every D30 event across the full universe.

For Tier-3/ARMED candidates:

- retain a rolling in-memory window,
- persist sampled/aggregated depth features,
- capture higher-resolution depth around the episode for replay,
- archive only when it materially supports research.

### Database design

Use time/date partitioning from the first EBIE migration for high-volume snapshot tables.

Index by:

```text
symbol
timestamp
episode_id
horizon
model_version
```

Do not JSON-dump everything into one unindexed mega-table.

---

## Q4.4 — Tiered scanning and dynamic subscriptions

### Decision: **EB-0 must make ingestion dynamically controllable, but full Tier-2/Tier-3 promotion goes live with EB-6.**

Milestone A can still scan all ~208 symbols uniformly at Tier-1 depth.

### EB-0 must add infrastructure for

```text
subscribe
unsubscribe
change_mode
subscription registry
provider capacity tracking
promotion/demotion request
staleness after mode change
```

But do not make early EB-0 correctness depend on constant runtime subscription churn.

### Initial behavior

```text
all F&O universe → Tier 1
```

### When EB-6 arrives

Activate:

```text
Tier 1
  ↓ candidates
Tier 2 = richer derivative/context analysis
  ↓ strongest candidates
Tier 3 = full/deep microstructure
```

If D30 exists:

```text
Tier 3 → full_d30
```

Otherwise:

```text
Tier 3 → full (5 levels)
```

### Why

This avoids a large ingestion rewrite and microstructure rewrite happening simultaneously while still designing the correct interface from EB-0.

---

# 5. Modeling & Evaluation

## Q5.1 — Use example breakout labels or calibrate first?

### Decision: **Run a label-sensitivity/calibration study first. Keep the blueprint thresholds only as baseline comparators.**

Baseline comparators:

### Intraday
```text
horizon = 45 minutes
success = +0.75 ATR beyond trigger
failure = -0.50 ATR invalidation first
```

### Swing
```text
horizon = 3 sessions
success = +1.50 ATR
failure = -0.75 ATR first
```

Do not immediately declare these the production truth.

### First study

Use the archived outcomes and replayable market history to compare a **small pre-declared grid**, for example:

#### Intraday horizons
```text
30m
45m
60m
```

#### Favorable excursion
```text
0.50 ATR
0.75 ATR
1.00 ATR
```

#### Adverse invalidation
```text
0.35 ATR
0.50 ATR
0.75 ATR
```

#### Swing horizons
```text
1 session
2 sessions
3 sessions
```

### Selection principle

Choose a label that represents a meaningful tradable breakout and remains stable across:

- symbols,
- liquidity buckets,
- volatility regimes,
- bull/bear directions.

Do **not** choose the definition merely because it maximizes backtest performance. That would turn the target definition itself into an overfitting knob.

### Archived 12,000+ outcomes

Use them where their timestamps and outcome definitions are compatible.

Do not force old outcomes into new EBIE labels if required pre-trigger features were never recorded.

---

## Q5.2 — Shadow promotion criteria

### Decision: **Require both historical/replay pre-validation and real unseen shadow validation. Replay does not replace live shadow.**

### Gate A — Offline/replay

Before user-facing influence:

- no leakage
- no repainting
- deterministic replay
- sensible feature distributions
- stable performance across regime slices
- preferably at least **2,000 labeled candidate episodes** if the available history supports it

If only fewer valid episodes exist, report the limitation instead of manufacturing data.

### Gate B — Live shadow

Before EBIE replaces the current live alert authority:

```text
minimum 300 unique EBIE episodes
AND
minimum 25 trading sessions
```

whichever takes longer.

Also require:

- both bullish and bearish examples,
- more than one market regime,
- no single symbol dominating the sample,
- no unresolved data-quality defect,
- no state-machine drift/repaint defect.

### Performance requirements

Promotion requires:

- positive net-of-cost paper expectancy,
- false-break behavior no worse than current system and preferably materially better,
- precision@K no worse than current system,
- useful median early lead time,
- stable drawdown behavior,
- reproducible explanations,
- calibrated probability if percentage probability is shown.

### Timeline

Yes, several weeks of shadow validation is acceptable.

An advanced scanner should not be promoted because the project is impatient.

Synthetic data may be used for software tests, **not as a substitute for real unseen market outcomes**.

---

## Q5.3 — Champion/challenger cadence

### Decision: **Evaluate weekly; promote manually at first.**

Scheduler:

```text
weekly after the final trading session of the week
```

Review:

- rolling 30/60/120-trade results
- 20/60-trading-day results
- calibration drift
- regime slices
- false-break rate
- net-of-cost expectancy
- feature drift

### Important

The scheduler may produce:

```text
CHALLENGER_ELIGIBLE_FOR_REVIEW
```

It must **not automatically replace the champion** in the initial EBIE generation.

Promotion requires:

```text
review
→ documented comparison
→ version bump
→ commit
→ deployment
```

Daily automatic champion switching is prohibited.

---

# 6. Risk & Product Policy

## Q6.1 — Portfolio risk as paper hard gate?

### Decision: **No. Portfolio risk is not a hard blocker of the raw paper setup verdict.**

Separate two concepts:

```text
SETUP VERDICT
```

and

```text
PORTFOLIO ACTIONABILITY
```

Example:

```text
RELIANCE
Setup Verdict: A / LONG READY
Portfolio Fit: REJECT — already overexposed to Energy
```

The setup remains in research and outcome tracking.

### Hard gates now

These can hard-block an actionable paper verdict:

- F&O ban
- market/session closed
- invalid/stale underlying
- invalid/stale derivative contract
- data-quality hard failure
- invalid symbol mapping
- missing trigger
- missing invalidation
- invalid option quote
- extreme/invalid option spread for an option recommendation
- insufficient option liquidity for a specific contract recommendation

### Portfolio policy while paper-only

```text
warn / mark rejected for portfolio
but preserve raw setup
```

If live-capital mode is ever introduced, portfolio exposure limits become hard gates.

---

## Q6.2 — Data Quality Score threshold

### Decision: **Start conservative from day one, but combine score thresholds with explicit hard failures.**

Do not rely only on one composite number.

### Provisional EBIE v1 policy

```text
DQ < 80
→ DATA UNRELIABLE
→ NO TRADE

DQ 80–89
→ DEGRADED
→ may display DEVELOPING / PRE-BREAKOUT
→ cannot become final OPTION_READY / highest-confidence actionable state

DQ >= 90
→ fully verdict-eligible
```

### Hard failure overrides score

Regardless of DQ score:

```text
stale spot
invalid timestamp order
missing trigger data
stale selected option
broken instrument mapping
known feed gap in required window
```

must block actionability.

### Calibration

Log DQ distributions from day one.

After the first **10 trading sessions**, review:

- percentage of universe in each bucket
- false blocks
- missed data faults
- relationship between DQ and signal failure

Threshold changes must be versioned and justified.

Do not silently tune DQ to increase the number of signals.

---

## Q6.3 — Verdict grade thresholds

### Decision: **Use the blueprint bands only as temporary shadow instrumentation. Calibrate before production.**

Temporary EB-1/EB-8 shadow bands:

```text
<55      NO EDGE
55–64    DEVELOPING
65–74    PRE-BREAKOUT WATCH
75–84    READY
85+      ARMED CANDIDATE
```

Important:

`85+` does not automatically mean ARMED.

ARMED also requires:

- trigger proximity,
- data quality,
- liquidity,
- no hard gate,
- correct horizon,
- stable setup/episode,
- sufficient evidence-family agreement.

Before production, derive v1 thresholds from historical + shadow score distributions.

Store:

```text
threshold_version
horizon
direction
regime applicability
effective_date
```

Do not hard-code threshold numbers in multiple files.

---

# 7. External Dependency Verification

## Q7.1 — Upstox depth, Greeks, News, connection/subscription limits

### Decision: **Public capabilities are verified; exact account entitlement still requires runtime/account verification.**

Verified from current official Upstox documentation on 2026-08-19:

### Market Data Feed V3

Normal documented limits include:

```text
Connections: 2 per user

LTPC:
  individual category limit up to 5000 keys

Option Greeks:
  individual category limit up to 3000 keys

Full:
  individual category limit up to 2000 keys

Mixed-category combined limits are lower.
```

`full` contains **5 market levels**.

Upstox Plus currently documents:

```text
Connections: up to 5 per user
Full D30: available under Plus
```

The Plus announcement states D30 supports up to **50 instruments per WebSocket connection**.

### Dynamic mode control

Market Data Feed V3 currently documents:

```text
sub
change_mode
unsub
```

This directly supports EBIE candidate promotion.

### Option Greeks

Current official Option Greeks API supports up to **50 instrument keys per REST request**.

The option-chain API returns strike-wise market data including OI and Greeks.

### News

News API is currently documented.

### Exact account

EB-0 must perform an entitlement/config validation in the actual project environment.

Do not infer Plus subscription from source code.

---

## Q7.2 — News API cost/rate/latency

### Decision: **Use Upstox News API as the first news source; batch and cache it. Do not design EB-7 around per-symbol polling.**

Current official News API documentation states:

```text
maximum 30 instrument keys per request
page size up to 100
published_time included
```

Current general Upstox standard API limits document:

```text
50 requests / second
500 requests / minute
2000 requests / 30 minutes
```

The News page does not state a separate latency SLA.

The public Upstox API material currently describes trading/data APIs as free, but exact account-specific commercial terms must still be checked before assuming any premium entitlement.

### EB-7 polling design

For ~208 symbols:

```text
208 / 30
≈ 7 batched requests per universe sweep
```

Recommended starting cadence:

```text
full universe news refresh: every 60 seconds
Tier-2/Tier-3 candidates: optional 30-second refresh
```

Use:

- article-link fingerprint
- published timestamp
- heading hash
- symbol mapping
- Redis cache

to prevent duplicate inference.

### Latency measurement

Record:

```text
published_time
api_fetch_time
first_seen_time
sentiment_completed_time
```

Then measure actual provider publication lag.

Do not assume the News API is suitable for sub-second event trading.

Sentiment is a catalyst/context input, not a high-frequency trigger.

---

## Q7.3 — NSE delivery and derivatives report health

### Decision: **Current official report surfaces still exist. Keep NSE EOD data as validation/research input, not a critical live dependency.**

Current official NSE pages still expose:

- Security-wise equity archives / price-volume-delivery information
- derivative contract-wise price/volume/OI information
- CSV/report download surfaces

However, this does **not** prove the existing scraper's HTML/schema remains unchanged.

Therefore, before EB-4 / delivery-dependent work:

### Add an automated source health check

Verify:

```text
HTTP success
expected columns
row count sanity
date sanity
symbol normalization
OI field presence
delivery field presence
file hash / format version
```

### Parser policy

- prefer official downloadable report data over brittle DOM scraping,
- version the parser,
- retain a sample fixture for tests,
- fail closed if schema changes,
- never write malformed rows into durable research tables,
- make ingestion idempotent.

### Operational rule

An NSE report failure may reduce EOD enrichment but must not crash the live scanner.

---

# 8. Four Decisions Required to Unblock EB-0 — Final Answer

The engineering team's “minimum decisions” are now resolved.

## 1. Scope

**FULL EB-0 → EB-14 BUILD AUTHORIZED.**

Implementation remains phased and verified.

## 2. Depth data

**D5 (`full`) is the mandatory baseline. D30 is optional Tier-3 enhancement.**

Do not block EBIE waiting for Plus.

Build capability detection and dynamic promotion.

## 3. State-machine ownership

**EBIE becomes the canonical state machine.**

Existing trackers run during migration/shadow and are then deprecated as competing state authorities.

## 4. Shell target

**New shell only for new EBIE UI.**

Classic becomes bug-fix/fallback only and is retired from active feature development.

---

# 9. Revised EBIE Implementation Sequence

Based on the decisions above, implement in this order:

```text
EB-0A
Event-time integrity
Data freshness
Provider capability registry
Dynamic subscription control interface
Data Quality v1

EB-0B
Snapshot schema
Partitioning
Episode persistence contract

EB-1
Canonical EBIE state machine
Legacy-state adapters
Shared EpisodeManager

EB-2
Intraday accumulation
Compression
CLV
RVOL_TOD
Multi-anchor AVWAP
Reuse VCP for swing

EB-3
Relative strength
Sector mapping
Market/sector directional context

CHECKPOINT A

EB-4
Current-month futures subscriptions
OI velocity/acceleration
Basis/basis velocity
Rollover awareness

EB-5
Dynamic option analytics
Wall strength/migration
PCR velocity
IV/skew
Option tradeability

EB-6
5-level microstructure baseline
Tiered promotion activation
D30 enhancement if entitlement exists

EB-7
Upstox News ingestion
Separate sentiment-engine
FinBERT CPU baseline
Event taxonomy
Novelty/relevance/decay

CHECKPOINT B

EB-8
Unified bull/bear verdict
Evidence-family anti-double-counting
Hard gates
Why/Why Not

EB-9
False-break / trap model

EB-10A
Label study
Logistic baseline
Calibration framework

EB-10B
Boosted-tree challenger if justified

CHECKPOINT C

EB-11
Portfolio risk
Informational while paper-only

EB-12
New-shell EBIE UX
Timeline
Direct verdict
Rejection reasons
Probability only if calibrated

EB-13
Offline gate
Live shadow ≥300 episodes and ≥25 trading sessions

EB-14
Weekly champion/challenger evaluation
Manual model promotion

CHECKPOINT D
EBIE becomes canonical production scanner
```

---

# 10. Non-Negotiable Engineering Rules

The AI team must treat these as architecture constraints.

1. **No repainting.**
2. **No future leakage.**
3. **No drifting entry/SL/target inside one episode.**
4. **No permanent fourth breakout-state system.**
5. **No OI-only “writer/buyer” assumption.**
6. **No static PCR / Max Pain / highest-OI strike as standalone directional verdict.**
7. **No raw score shown as probability.**
8. **No two independent final verdict gates after EBIE promotion.**
9. **No model training in an API request path.**
10. **No transformer requirement.**
11. **No news sentiment treated as neutral when the service/data is missing — use UNKNOWN.**
12. **No D30 dependency for core EBIE operation.**
13. **No option recommendation without liquidity/tradeability checks.**
14. **No duplicate strategy alerts for the same underlying episode.**
15. **No full-history raw D30 storage for the complete universe.**
16. **No live-capital auto execution.**
17. **No Classic-shell EBIE feature parity requirement.**
18. **No silent model/threshold promotion.**
19. **No feature added to production without ablation/walk-forward evidence.**
20. **No schema or provider parsing failure allowed to poison historical research data.**

---

# 11. Definition of the Final Product

EBIE is successful when the system evolves from:

```text
Breakout happened
→ indicators agree
→ BUY
```

to:

```text
Stock begins accumulating/distributing
        ↓
range contracts
        ↓
relative strength changes
        ↓
volume participation changes
        ↓
futures OI/basis changes
        ↓
option walls migrate
        ↓
microstructure pressure develops
        ↓
market/sector context agrees
        ↓
sentiment/catalyst checked
        ↓
trap risk checked
        ↓
EBIE identifies PRE-BREAKOUT / PRE-BREAKDOWN
        ↓
READY
        ↓
ARMED
        ↓
trigger
        ↓
acceptance confirmation
        ↓
direct verdict
```

The dashboard must ultimately answer:

```text
WHAT is likely?
HOW EARLY are we?
WHY does EBIE believe it?
WHAT contradicts it?
WHERE is the trigger?
WHERE is invalidation?
HOW likely is a trap?
IS the option contract tradeable?
IS the sector/market helping?
IS the data trustworthy?
SHOULD this setup be acted on or rejected?
```

---

# 12. Example Final EBIE Verdict

```text
RELIANCE — BREAKOUT ARMED

Horizon                  INTRADAY
Direction                BULLISH
State                    ARMED
EBIE Score               86/100
Calibrated Probability   74%
False-Break Risk         13%
Data Quality             96/100

Accumulation             STRONG
Compression              STRONG
Relative Strength        LEADER
Volume Pressure          RISING
Microstructure           BULLISH
Futures Positioning      SUPPORTIVE
Options Positioning      SUPPORTIVE
Sector Context           SUPPORTIVE
Market Context           HEALTHY
Sentiment                NEUTRAL
Option Tradeability      PASS

Trigger                   ₹3,012.50
Invalidation              ₹2,988.00
Trigger Distance          0.18 ATR
Clean Air                 1.21 ATR

WHY EARLY
1. multi-stage contraction
2. pullback volume drying
3. CLV remains positive
4. swing/base AVWAP repeatedly defended
5. relative strength accelerating before price breakout
6. futures price + OI support long-buildup interpretation
7. near-ATM call wall weakening
8. put support migrating upward
9. time-normalized RVOL accelerating
10. sector breadth supportive

RISKS
1. weekly supply 1.2 ATR above
2. sentiment provides no additional catalyst

DIRECT VERDICT

LONG READY.
DO NOT CHASE.
WAIT FOR ₹3,012.50 TRIGGER AND ACCEPTANCE.
```

That is the target product behavior.

---

# 13. External Technical Verification Performed

The following current primary/official documentation was reviewed on **2026-08-19** to resolve the provider questions:

### Upstox Developer API
- Market Data Feed V3
- WebSocket Plus / Full D30 announcement
- Put/Call Option Chain
- Option Greek Fields
- News API
- Rate Limits

### NSE
- Security-wise Archives (Equities)
- Historical Contract-wise Price Volume Data / derivatives reports

### Verified design consequences

- Use Upstox `full` as the 5-level baseline.
- Treat `full_d30` as a Plus/capability-gated enhancement.
- Use dynamic `change_mode` for candidate promotion.
- Batch News API requests.
- Do not infer the project's actual Plus entitlement from public documentation.
- Use NSE reports primarily for EOD/research validation and keep their parser health-checked/versioned.

---

# 14. Final Instruction to the AI Engineering Team

**Proceed with EB-0.**

Do not return another broad requirements questionnaire for decisions already answered here.

When a later phase reveals a genuinely new blocker:

1. state the observed fact,
2. show the exact code/provider evidence,
3. explain why existing decisions do not cover it,
4. propose a default solution,
5. continue with all unaffected work.

The target is the **full Infusion EBIE advanced early-breakout / early-breakdown scanner**, implemented safely, transparently, and phase by phase.
