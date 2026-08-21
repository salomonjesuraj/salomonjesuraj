# EBIE — Known Gaps, Loose Ends, and Logical Mismatches

**Purpose:** a candid engineering review of the EB-0 → EB-14 implementation, written for
whoever picks this codebase up next (human or AI). Every item below was either disclosed
in its own commit message at the time it shipped, or is a real inconsistency found while
compiling this document. Nothing here is a crash bug — the system consistently degrades
honestly (`None`/"unavailable" rather than a fabricated number) — but several of these are
genuine logical mismatches between what different parts of the system assume, and several
are scope gaps that materially limit what EBIE can actually do today versus what
`docs/EBIE-BLUEPRINT.md` originally specified.

Cross-references: `docs/EBIE-BLUEPRINT.md` (original spec), `docs/EBIE-IMPLEMENTATION-ANSWERS.md`
(authorized decisions, cited as "Q<n>.<n>" below), and the git log (every phase's own commit
message has the original disclosure this document draws from).

**Living document, updated once (2026-08-21):** this document originally described the
state after EB-0 → EB-14. It was then handed to an AI engineering team, which wrote back
the "EBIE Consolidation, Calibration and Production Readiness" directive (EB-15,
`EBIE_IMPLEMENTATION_REPLY_TO_AI_TEAM.md`) addressing several items below directly. EB-15's
own 7-phase Implementation Sequence has since been fully implemented, verified live, and
committed (17 numbered items, commits `e7977e4` through `0310e63`). Items below are marked
**RESOLVED (EB-15 ...)**, **PARTIALLY RESOLVED (EB-15 ...)**, or left as-is where EB-15 did
not touch them. A new §7 covers gaps EB-15's own work disclosed that did not exist in the
original EB-0 → EB-14 review. Re-verified against the live running system before editing
any line below, not assumed from memory.

---

## 1. Cross-cutting / systemic issues

### 1.1 Three-plus competing "early state" trackers still coexist — Rule #4 violated in spirit
`PreBreakoutTracker`, `stock_breakout_tier` (Stock Breakout Radar, R1-R9), `radar_alerts`,
and now the EBIE state machine (EB-1) all independently compute a notion of "how early/close
is this setup." Q3.1's authorized migration plan was explicit: the old trackers feed EBIE as
inputs during a shadow period, then get **deprecated** once EBIE is promoted. That
deprecation step was never scheduled or done — all four systems still run in parallel
indefinitely. This is exactly the "No permanent fourth breakout-state system" (Rule #4)
scenario the rules warn against, currently true in practice even though each individual
phase was authorized. Revisit once (or if) Checkpoint D is ever reached.

### 1.2 `total_buy_qty`/`total_sell_qty` naming bug — RESOLVED (EB-15 Phase 1)
Discovered during EB-6: `RawTickV1` correctly computes real exchange-wide `tbq`/`tsq` from
the Upstox codec, but `normalizer/transformer.py` never carried them onto `NormalizedTickV1`
at all, and `feature-engine/engine.py`'s `state.total_buy_qty`/`total_sell_qty` were actually
fed from `best_bid_qty`/`best_ask_qty` (level-1 quantities only). Fixed directly (not via the
previously-spawned background task, which had still not landed as of this session):
`NormalizedTickV1` gained real `total_buy_qty`/`total_sell_qty` fields, `transformer.py` now
copies them from `raw_payload`, and `engine.py` now reads `tick.get("total_buy_qty", 0)`
instead of `best_bid_qty`. `microstructure.py`'s `get_order_imbalance()` is now a genuine
exchange-wide read, matching its own name. Also added a new `depth_tier` field
(`D5`/`UNAVAILABLE`/`D30_CANDIDATE_UNCONFIRMED`) so a reader can tell exchange-wide-depth-5
evidence apart from a future real Level-2/3 (D30) feed, should one ever be added.

### 1.3 NIFTY50 daily-bar history is empty — RESOLVED (EB-15 Phase 1), one caveat remains
`infusion:ohlc:NIFTY50:daily` had zero cached bars because NIFTY50 was absent from the live
symbol universe and never bootstrapped, silently degrading EB-3's multi-timeframe relative
strength (`rs_available: false` always) and VCP's relative-strength component. Fixed by
adding a `BENCHMARK_INDICES` map and `_benchmark_status()` to `scheduler/historical.py`;
`_universe()` now always `setdefault`s the benchmark index into the bootstrapped set
regardless of whether it's in the live F&O universe. Re-verified live post-fix, not just
code-read: `ZCARD infusion:ohlc:NIFTY50:daily` = 250 real bars, and `rs_available` genuinely
flips true given real overlapping stock+NIFTY history.

**Caveat found while re-verifying**: `relative_strength` still shows up in
`unavailable_evidence_families` for ~80% of live candidates sampled this session (32/40) —
not because of this bug, but because `verdict_engine.py`'s `_relative_strength_family()` reads
from `infusion:mtf:{symbol}`, which `api/mtf_queue.py`'s own warming loop only ever keeps
populated for a rolling ~50/208-symbol subset at a time (already an intentional, pre-existing
design — see the new §1.7 below, which this session found generalizes across three separate
caches, not just this one). The NIFTY50 bug itself is genuinely fixed; the family still reads
`None` most of the time for an unrelated, separate reason.

### 1.4 Alignment computation happens twice, from the same data, never reconciled
`scanner/alignment.py`'s `compute_signal_alignment()` is called **twice** per candidate on two
separate paths that never talk to each other:
1. Directly inside each strategy (`options_first_hybrid.py`/`vol_vwap_breakout.py`), spread
   into `features_snapshot` as `alignment_agree_count`/`alignment_agreeing_families`/etc.
2. Inside `verdict_engine.py`'s `compute_verdict()`, called with `bullish=True` to derive each
   family's *absolute* direction, feeding `bull_score`/`bear_score`.

These are the same underlying 8 families, computed from the same inputs, via two independent
call sites. Not a bug (results are consistent, since both call the same pure function), but a
real duplication that means a reader inspecting a signal sees two different-shaped
representations of identical evidence (`alignment_agree_count: 5` in `features_snapshot`
alongside `bull_score: 62.5` in `sub_scores.verdict`) with no explicit link between them.
Worth consolidating into one call site if this file is touched again.

### 1.5 42 commits sit unpushed to `origin/main` (was 28)
Every EBIE-phase commit, including all of EB-15, has landed locally only (local branch is
actually named `master`, tracking `origin/main` — itself a pre-existing naming mismatch, not
something either session introduced). Re-confirmed via `git status -sb`: `master...origin/main
[ahead 42]`. Still not pushed as of EB-15 Phase 7 — worth the next engineer knowing the remote
is now 42 commits behind until a human explicitly pushes it.

### 1.6 Options-dynamics v2 shadow-comparison and cutover never happened — display side
partially addressed, verdict-side unchanged
Q3.4's authorized migration path was explicit: `options_analytics` (static) →
`options_analytics_v2` (dynamic, EB-5) → **shadow comparison** → **verified cutover**, after
which static outputs become display/research-only and dynamic outputs drive the verdict.
EB-5 shipped the dynamic engine and verified it works, but the shadow-comparison step and the
actual cutover were never done. The Clean Sweep dashboard redesign (this session, alongside
EB-15) folded Options Analytics and Strategy Selector into Option Basis as collapsed-by-default,
explicitly informational sections — matching EB-15 item 8's own instruction that static PCR/Max
Pain become "informational, not standalone decision surfaces." That resolves the **display**
half of this item. It does not resolve the underlying question this item is actually about:
which engine's numbers the verdict itself should trust. `verdict_engine.py`'s
`_options_positioning_family()` still reads only `options_dynamics_cache` (the dynamic v2
engine) — static `options_analytics` was never wired into the verdict at all, so there was
never really a "which one wins" conflict to cut over from at the scoring layer, only at the
display layer. No shadow-comparison report exists comparing the two engines' outputs against
each other.

### 1.7 Three of six per-symbol evidence caches are deliberately-narrow rolling queues, not
full-universe sweeps — RESOLVED (dashboard-legibility fix, coverage itself unchanged by design)
Found while re-verifying §1.3 above, then checked across every other per-symbol cache this
session touches. As of that check, out of 208 live symbols:

| Cache | Key prefix | Live coverage | Pattern |
|---|---|---|---|
| Futures | `infusion:futures:` | 209/208 | full sweep, every symbol |
| Market context | `infusion:market-context:` | 208/208 | full sweep (EB-15 Phase 4), every symbol |
| Sentiment | `infusion:sentiment:` | 90/208 | limited by real news volume, not an artificial cap |
| Multi-timeframe RS | `infusion:mtf:` | 50/208 | rolling subset, `mtf_queue.py`, ~60/cycle |
| Options dynamics | `infusion:options-dynamics:` | 24/208 | rolling subset, `options_dynamics_queue.py` |
| Option chain | `infusion:option-chain:` | 14/208 | rolling subset, `option_chain_queue.py` |

The three rolling-subset queues are an intentional, disclosed design choice (`market_breadth.py`'s
own comment: "mtf_queue warming loop only keeps warm for a rolling subset at any given time") —
built to protect Upstox rate limits and dashboard responsiveness, not a bug, and this fix does
**not** widen any of them (correctly out of this fix's scope). What it fixes is the specific
legibility problem this section named: `relative_strength`, `options_positioning`, and the Phase
5 option-tradeability gate all silently scored `None`/absent with no way for a reader to tell
"never computed" apart from "computed recently, cache since expired."

Fixed exactly as this section's own suggestion named it: each of the three short-TTL caches
gained (or, for option-chain, had widened) a companion long-lived (7-day) "last seen" marker —
`api/routes/mtf.py`'s `MTF_LAST_SEEN_PREFIX`, `api/options_dynamics_queue.py`'s
`LAST_SEEN_PREFIX`, and `api/option_chain_queue.py`'s pre-existing `LAST_REFRESH_PREFIX` (which
already stored exactly the needed timestamp — just had a 600s TTL, too short to answer this
question given the queue only covers ~14/208 symbols per cycle; widened, its only consumer
re-confirmed unaffected). `api/routes/ebie_candidates.py` now returns a `cache_freshness` object
per candidate (`fresh`/`stale`/`never_cached` + `age_sec` for each of the three), and
`ebie-verdict-panel.js` surfaces it inline: "options_positioning — never cached for this symbol",
or "Not currently cached — last checked 3m ago." for the option-tradeability block specifically.

A real nuance preserved rather than smoothed over: a family's cache can be genuinely `fresh` and
the family can *still* be unavailable (confirmed live: HDFCLIFE's `relative_strength` — the mtf
cache was live, but the family's own separate requirement of 30+ days of the stock's own history
wasn't met). The freshness suffix correctly stays blank in that case rather than misattributing
an unrelated gap to cache coverage. Live-verified all three real states occurring in production
data before shipping (`fresh`, `stale` with real ages like 57-549s, `never_cached`).

---

## 2. Verdict Engine (EB-8) — `scanner/verdict_engine.py`

### 2.1 Market/Sector Context is completely absent from the verdict — RESOLVED (EB-15 Phase 4)
EB-3's `market_sector_context_score` was computed inline in `api/routes/ticks.py`'s
`_build_ticks()` for the dashboard, but was never cached standalone the way
`mtf_cache`/`sentiment_cache`/`futures_cache`/`options_dynamics_cache` are, so it was simply
missing from `bull_score`/`bear_score`. Fixed exactly as originally scoped: a new `api`-side
sweep in `ebie_state_queue.py` caches raw `{nifty_change_pct, sector_avg_change_pct,
market_health_score}` per symbol (JSON, `infusion:market-context:{symbol}`, matching the
existing cache-read pattern), and `scanner/verdict_engine.py` gained a real
`_market_context_family()` plus a duplicated-but-disclosed `_compute_directional_context()`
(scanner and api are separate containers with no shared import path for this function — the
same precedent as `vcp.py`/`daily_trend_filter.py`). Live-verified: this cache is now the one
genuinely full-universe sweep (208/208 symbols, see the new §1.7 above) — market/sector context
is the one evidence family that never silently goes unavailable due to queue-coverage gaps.
Surfaced in the dashboard as its own detail block, EB-15 Phase 7.

### 2.2 Evidence-family voting is unweighted — RESOLVED (EB-15 Phase 4), with a real caveat on
what "resolved" means here
Blueprint Section 6 proposes specific point weights (Accumulation/Distribution 18,
Futures+Options 20, Structure+trigger 12, Microstructure 10, Volume 12, Compression 8,
RS+sector/market 10, Sentiment 10 — summing to 100). `verdict_engine.py` previously treated
every family as one equal vote — exactly the "equal votes" anti-pattern Section 6 explicitly
argues against. Fixed: the 8 pre-existing alignment families (structure/candlestick/zone/ict/
regime/ma_regime/donchian/wyckoff) now share one capped `STRUCTURE_CLUSTER_WEIGHT = 25.0`
split evenly among however many are available per tick, and every other family gets its own
fixed `FAMILY_WEIGHTS` entry (accumulation 8, compression 6, relative_strength 10, market_context
13, microstructure 8, futures_positioning 8, options_positioning 8, sentiment 8, volume 6 —
summing to exactly 100, enforced by a module-level `assert`). **This is a real, disclosed v1
weight scheme this session invented, not a literal port of the blueprint's own Section 6
numbers** — the code's own comment says so explicitly ("the directive names the CATEGORIES
needing weights, not exact numbers"). So the "equal votes" anti-pattern is genuinely gone, but
the specific weight VALUES are themselves uncalibrated (folds into §2.7 below, unchanged).
`WEIGHTS_VERSION = "v1-2026-08-20"` is stamped onto every verdict so an archived row can always
be read against the exact scheme that produced it, if the weights are ever revised.

### 2.3 `compression` family structurally cannot vote bearish
VCP/Minervini Stage-2 is inherently a long-side methodology — `_compression_family()` can
only ever return `True` or `None`, never `False`. Disclosed at the time, but it means the
15-family list is not symmetric across directions; a bearish candidate's `bear_score` can
never benefit from a "good base" read the way a bullish candidate's `bull_score` can. No
bearish-equivalent factor (e.g. a distribution/breakdown-base pattern) was ever built to fill
the gap.

### 2.4 Hard gates cover only a subset of Q6.1's authorized list — PARTIALLY RESOLVED
(EB-15 Phase 5), coverage is real but narrow
Implemented before EB-15: F&O ban, DQ hard-fail (<80), missing trigger/invalidation, stale
underlying/feed-gap. **Missing, all disclosed as needing data scanner doesn't have at signal
time**: invalid/stale derivative contract, invalid option quote, extreme/invalid option
spread, insufficient option liquidity — a candidate could pass every gate and still be
economically untradeable on the option side.

EB-15 Phase 5 closes this the same way §1.7 closes market-context: `api`'s own per-candidate
option-chain scorer (`routes/market.py`'s `_upstox_option_context()`) already computes exactly
these checks (`hard_blockers` like "SL inside spread - noise stop", "target does not pay for
premium", "breakeven too far vs ATR", "need 60-session IV history") and caches the result at
`infusion:option-chain:{symbol}`. `scanner/engine.py` gained a `_fetch_option_chain_context_cache()`
best-effort read, and `verdict_engine.py`'s `_hard_gates()` now adds `OPTION_NOT_TRADEABLE` when
`execution_status == "AVOID_CONTRACT"`. Live-verified with a real payload (CDSL, a genuine
`AVOID_CONTRACT`): a candidate scoring `bull_score=100.0` was still correctly `HARD_BLOCKED`.

**The real caveat, found during Phase 7's own live verification and covered fully in §1.7
above**: `infusion:option-chain:*` is the narrowest of the three rolling-subset caches —
14/208 symbols (~7%) at any sampled moment. So this gate only ever fires for whichever handful
of symbols the option-chain queue happened to have refreshed recently; the other ~93% of
candidates pass through with **no option-side check performed at all**, not because their
contract was checked and found fine. The §1.7 fix at least makes this ~93% legible now (the
dashboard's Option Tradeability block says "Never checked for this symbol" rather than looking
identical to "checked, all clear") -- but the underlying gate still doesn't run for most
candidates. The four originally-missing checks are now real and wired
in — but only for a small, rotating slice of the universe, not every candidate at signal time.

### 2.5 Wall-migration flags are computed but never voted on
`options_dynamics_cache["wall"]["call_wall_migrated"]`/`put_wall_migrated` exist in the data
(EB-5) but `_options_positioning_family()` only reads `strengthening`/`weakening` state, never
the migration booleans. A #1-strike migration is real, structurally significant evidence
(explicitly called out in the blueprint as inconclusive-by-construction but worth surfacing)
that currently reaches neither `top_reasons` nor `risks`.

### 2.6 `calibrated_probability` is always `None` — PARTIALLY RESOLVED (EB-15 Phase 6):
the missing pipeline now exists, still correctly `None` pending real data
Per Rule #7 `None` here was always intentional, not a bug. The actual gap this item names —
"no calibration pipeline wired to `verdict.directional_score` specifically" (see §5.1) — is
now closed at the infrastructure level: every verdict carries a new `confidence_band`
(LOW/MEDIUM/HIGH/VERY_HIGH, thresholded off `directional_score`, matching the directive's own
explicit "use confidence bands until calibration is statistically valid" instruction), and a
new `GET /api/ebie/verdict-calibration` reuses `shadow_validation.py`'s own episode-counting
gate and, once genuinely cleared, runs EB-10's own proven Platt/isotonic `calibrate_and_validate()`
against the real archive, scoped to `directional_score` specifically. Live-checked against the
real archive at ship time: still only 1-3 decided episodes with a real verdict score — correctly
reports `NOT_READY`, not a fabricated probability. The plumbing exists now in both directions
(ML classifier per EB-10, verdict engine per this item); only real data volume is missing.

### 2.7 Thresholds are all hand-picked v1 constants, none calibrated
`CLV_THRESHOLD = 0.15`, `MICROSTRUCTURE_THRESHOLD = 0.15`, `VCP_MIN_SCORE = 60`,
`DQ_HARD_FAIL = 80`, `DQ_DEGRADED = 90`, the `VERDICT_BANDS` cutoffs (55/65/75/85) — all
disclosed as "temporary shadow instrumentation" per Q6.3's own explicit language ("Do not
hard-code final thresholds until calibration is complete"), and none have been recalibrated
against real outcome data since. This is expected/by-design at this stage, but is a real,
open item, not something quietly resolved.

---

## 3. Trap Model (EB-9) — `scanner/trap_model.py`, `api/trap_labels.py`

### 3.1 IV-spike-without-follow-through — never built
Listed explicitly in the blueprint's own trap-model feature list (Section 18). Needs a live
per-candidate Greeks fetch scanner has no access to at signal time. Same constraint as §2.4.

### 3.2 False-break label is narrow by construction
`classify_false_break()` only counts a **fast** STOP_HIT (within 15 minutes of firing) as a
false break. A slower STOP_HIT — the setup traded correctly for a while, then genuinely
failed later — is explicitly *not* counted, a disclosed scope decision, not every failed
trade. This means the real false-break rate the system reports (28.5% as of EB-9's own
verification) is a lower bound on "how often EBIE was simply wrong," not the full failure
rate.

### 3.3 MFE/MAE-based labeling is a real approximation, not a bar-level replay
Because this system doesn't archive a full tick/bar-level price path per signal, the
false-break label uses `max_favorable_pct`/`max_adverse_pct` (running peak/trough over the
signal's tracked lifetime) as a proxy for temporal ordering. A signal that touched the adverse
threshold first, recovered, then reached the favorable threshold would be misclassified as a
clean success under this approximation. Disclosed at the time; genuinely unresolvable without
new bar-level archival infrastructure.

### 3.4 `trap_risk_heuristic_check` and `precision_comparison` are both still statistically unvalidated
As of EB-13's own live check, only 1-3 real decided episodes carry the fields these checks
need. The correlation machinery is real and correct, but "does the trap-risk score actually
predict real false breaks" and "does the verdict's actionable band actually predict higher
precision" are both **open, unanswered questions today** — not yet resolved either way.

---

## 4. Portfolio Risk (EB-11) — `scanner/portfolio_risk.py`, `api/portfolio_risk_daily.py`

### 4.1 "Current open portfolio" is a proxy, not real positions
Because Infusion is paper-only with no executed-position ledger, the "portfolio" EB-11
reasons about is the set of currently-*active signals* (`KEY_SIGNAL_ACTIVE`), not real held
positions with real quantities over time. This is a reasonable, disclosed choice for today,
but if live-capital mode is ever introduced, this entire module's data model would need
rebuilding around a real position ledger — it's a structural placeholder, not an extensible
foundation.

### 4.2 Correlation detection is same-sector + same-direction only
Two symbols in genuinely macro-correlated but differently-labeled sectors (e.g. a rate-sensitive
NBFC and a real-estate name) are not flagged as correlated today, even though they may move
together in practice. `sector_id`-equality is a coarse, disclosed proxy for real correlation,
never validated against actual return-correlation data.

### 4.3 Index beta, expiry concentration, option gamma exposure — never built
All three explicitly listed in the blueprint (Section 26) and explicitly disclosed as out of
scope: no per-stock beta coefficients exist anywhere in this codebase; every current signal
trades the same near-month contract (no multi-expiry selection exists, so this metric would be
a trivial always-100%); gamma exposure needs live per-position Greeks scanner doesn't have.

---

## 5. Calibration (EB-10)

### 5.1 Only the ML classifier's raw score is calibrated — RESOLVED at the infrastructure
level (EB-15 Phase 6), see §2.6 above for the detail
`calibrate_and_validate()` was wired into `ml_classifier.py`'s training flow only;
`verdict_engine.py`'s own score had zero calibration infrastructure pointed at it. EB-15 Phase 6
built `api/verdict_calibration.py` + `GET /api/ebie/verdict-calibration` specifically for
`directional_score`, reusing EB-10's own proven calibration machinery rather than a second,
diverging implementation. Both paths now exist; both are correctly gated on real episode volume,
which remains the actual bottleneck (see §5.5 for why the ML side's own lift is separately
disappointing even where it does have data).

### 5.2 The blueprint's proposed label-sensitivity study (Q5.1) — PARTIALLY ADDRESSED
(EB-15 Phase 6), a narrower real study now exists and was actually run, the full grid was not
The real, load-bearing finding from EB-10: the archiver's `signal_ttl_min` was only 5 minutes,
meaning **zero** archived signals were ever tracked long enough to test any of the blueprint's
proposed horizons (30/45/60min intraday). The TTL was widened to 75 minutes going forward.
EB-15 Phase 6 built `api/label_study.py` + `GET /api/backtest/label-study` and **actually ran
it** against the real archive: a real design shortcut makes this possible without a full
OHLC price-path replay — `archiver/tracker.py`'s own 30-second polling loop already records
`time_to_target_min`/`time_to_stop_min` per signal, from which "what would this signal's label
have been at 30/45/60 minutes" is directly derivable (target-before-stop from two timestamps
is unambiguous). Only considers signals created at/after EB-10's TTL widen (`d7d5001`,
2026-08-20T08:37:19Z) — everything before that could only ever resolve within ~5.5 minutes
regardless of its own recorded numbers. Trap classification reuses EB-9's own already-validated
`classify_false_break()` rather than a second, unvalidated definition. Live result at ship time:
correctly reports needing 100 real post-EB-10 decided signals before naming a recommended
window, has 1 — an honest "not enough data yet," not a guess.

**What this does not do**: the blueprint's full proposed methodology is a *grid* of 9+
candidate label definitions across horizon × favorable-excursion × adverse-excursion
combinations, checked for stability across symbol/liquidity/volatility/direction cuts (Q5.1).
EB-15's study covers exactly three horizons (30/45/60 min) with the system's existing single
target/stop definition — a real, running, honestly-reported study, but a narrower one than the
blueprint originally specified. The full grid study has still never been run.

### 5.3 Swing-horizon calibration is entirely out of scope
The TTL widening only covers intraday (up to ~75 min). Swing-horizon (1-3 session) validation
would need cross-session-boundary handling in `tracker.py`/`_get_ltp()` that was explicitly
not built. EBIE currently has no calibrated notion of a multi-day swing setup at all, despite
the blueprint treating intraday and swing as two co-equal models throughout (Section 9).

### 5.4 Bullish/bearish-specific calibration deferred
Q2.6 explicitly asks for this "where necessary." Only one overall Platt/isotonic mapping
exists; it has never been checked whether bullish and bearish signals calibrate differently
(a real, plausible risk given options have asymmetric liquidity/spread behavior by side).

### 5.5 The ML classifier itself shows near-zero real lift
Disclosed honestly at the time, worth restating here: test AUC 0.556 vs. the existing
`conviction_score`'s own 0.544 on identical held-out rows — a real, measured +0.012 lift, not
meaningfully better than the score Infusion already had. This is an honest finding about
current feature coverage (most Phase 1-13/EBIE informational fields have near-zero real
presence in the training data, since most archived rows predate whichever build actually
populated each field), not a training bug — but it means the thing being calibrated in §5.1
isn't very predictive to begin with.

---

## 6. Shadow Validation & Promotion Review (EB-13, EB-14)

### 6.1 "Episode" definition excludes 12,000+ real archived signals by construction
`_fetch_episodes()` in `api/shadow_validation.py` only counts decided signals carrying
`sub_scores.verdict` — i.e., signals fired *after* EB-8 shipped. This is the technically
correct definition of "an EBIE episode," but it means the 300-episode Gate B threshold will
take real calendar time to reach purely because of *when* verdict-scoring started, not because
there's insufficient underlying trading activity — the archive already has over 12,000 decided
signals, effectively none of which count toward Gate B. Worth being explicit about this
distinction when reviewing the shadow-validation report's progress.

### 6.2 False-break-rate reuse is not sample-matched to the precision comparison
`compute_shadow_validation_report()` bundles EB-9's `overall_false_break_rate_pct` (computed
over the *whole* recent archive, `days` window, regardless of verdict-scoring) alongside the
verdict-scoped `precision_comparison` (computed only over the small verdict-scored episode
set). These two numbers are honestly labeled but drawn from different, non-overlapping sample
populations — a reviewer could reasonably misread them as directly comparable when they are
not.

### 6.3 Weekly review cadence is a raw process-uptime timer, not calendar-anchored
`PROMOTION_REVIEW_INTERVAL_SEC = 7 * 24 * 3600` in `scheduler/main.py` counts from whenever the
scheduler process last started, not from "the final trading session of the week" as Q5.3
literally specifies (i.e., a real Friday-close anchor). Functionally harmless since promotion
is manual either way, but it means the actual review timestamps will drift depending on
deploy/restart history rather than landing predictably every Friday evening.

### 6.4 No dashboard UI for promotion-review history or decision recording — still open,
not touched by EB-15 Phase 7
`GET /api/ebie/promotion-review/history` and `POST .../decision` are both still API-only.
EB-15 Phase 7 built the EBIE Verdict panel's ranking/evidence/timeline/drift-monitoring
surfaces (item 14's own checklist), but promotion-review history/decision-recording was never
named in that checklist and was not added. A reviewer today would still need to call these
endpoints directly (curl/Postman) rather than using the dashboard.

### 6.5 EB-1's universe-wide state and EB-8's candidate-only verdict are still architecturally
split — PARTIALLY RESOLVED (EB-15 Phase 3 + Phase 7): the dashboard-visibility half is fixed,
the deeper scoring split is not
EB-1's shadow state machine evaluates *every* symbol every sweep, regardless of whether a
strategy ever produces a candidate. EB-8's verdict, trap-risk, and portfolio-fit scores only
ever get computed for symbols that *do* produce a real `SignalCandidate`. The specific
complaint this item names — "a symbol in DEVELOPING/PRE_BREAKOUT state is completely invisible
to the dashboard" — is now false: EB-15 Phase 3 added `compute_lightweight_verdict()`, a real
(simpler, unweighted) verdict computed for every symbol every 60-second sweep and cached at
`infusion:ebie-verdict-lite:{symbol}`; EB-15 Phase 7 surfaced it in the dashboard as the "Universe
(Lightweight)" view, with the same long/short filtering as the full-candidate view. Every
symbol now has *some* visible verdict at all times.

**What remains genuinely split, unchanged**: the lightweight verdict is a deliberately simpler
heuristic — it does not use the weighted family engine, does not read market context, futures,
sentiment, or options data at all, only breakout-tier/VWAP/rel-vol/anti-chase signals already on
the `/api/ticks` row. It is not a "preview" of what the full EB-8 verdict would say if a candidate
existed — it's a genuinely different, second computation that can and does disagree with the full
verdict for the same symbol at the same moment. See the new §7.1 below for why this itself is now
a disclosed, real gap.

---

## 7. New items disclosed during EB-15 (this session's own work, not present in the original
EB-0 → EB-14 review)

### 7.1 Two independent, disagreeing verdict computations now exist for the same symbol
Per §6.5 above: the full weighted verdict (`verdict_engine.compute_verdict()`, EB-8 +
EB-15 Phase 4's weighting) only runs for symbols with a real `SignalCandidate`; the lightweight
verdict (`ebie_state_queue.compute_lightweight_verdict()`, EB-15 Phase 3) runs for every symbol
every sweep using a simpler, unweighted heuristic over a different, smaller input set. These are
not two views of one underlying score — they are genuinely different computations that can
disagree for the same symbol at the same instant (e.g. the full verdict could read `NO_EDGE`
off weighted evidence while the lightweight verdict simultaneously reads `LONG_READY` off
breakout-tier state alone). Both are shown side by side in the same dashboard panel (EB-15 Phase
7's "All"/"Universe (Lightweight)" pills) with no explicit reconciliation or disagreement flag
between them — a real duplication in the same category as §1.1/§1.4, newly created by this
session's own work, not inherited from before.

### 7.2 The new EB-15 Phase 7 drift-monitoring strip deliberately shows raw counts, not a
computed "agreement" metric
`GET /api/ebie/comparison` (EB-1) reports real EBIE-state-vs-legacy-tier cross-tab counts. EB-15
Phase 7 surfaced this directly rather than deriving an "agreement %," because doing so would
require a state↔legacy-tier correspondence table that doesn't exist anywhere in this codebase —
building one now would be a guess dressed up as a metric. This was the right call for this
phase, but it means the dashboard currently asks a human to eyeball a raw cross-tab table rather
than surfacing an actual drift alert. Worth building the correspondence table explicitly (with
real, reasoned pairings signed off by whoever owns EBIE) if this becomes a recurring manual task.

### 7.3 The dashboard's new `market_context`/`option_chain` fields are live snapshots, not
point-in-time-of-firing values
EB-15 Phase 7's `ebie_candidates.py` enrichment reads the *current* Redis cache for
`market_context`/`option_chain` at the moment the dashboard requests the candidate list — not
the value that was actually in effect when the candidate fired (that value was already folded
into `bull_score`/`bear_score`/the `OPTION_NOT_TRADEABLE` gate at signal time and isn't archived
separately). For a candidate viewed shortly after firing this is usually the same number; for an
older or suppressed candidate viewed later in the session, the displayed market context can have
since moved on from what the verdict actually scored against. Disclosed in the route's own code
comment; not surfaced to the dashboard reader today (no "as of" timestamp on these two fields
specifically, unlike the rest of the row which carries `created_at`).

---

## 8. Suggested priority order for whoever picks this up

Items 1-3 from the original list (NIFTY50 bootstrap, tbq/tsq naming, market/sector context
wiring) are RESOLVED by EB-15 — see §1.2, §1.3, §2.1 above. Item 1 from the re-prioritized list
below (rolling-subset queue legibility) is now also RESOLVED — see §1.7 above. Re-prioritized
again for what's actually left, roughly in order of leverage-per-effort, not urgency (nothing
here is broken or blocking):

1. **§2.4 Option-tradeability gate coverage** — now the single highest-leverage remaining item.
   §1.7's fix makes the ~93%-uncovered gap *legible* (the dashboard now says "never checked"
   rather than looking clean); it does not close the gap itself. Widening `option_chain_queue.py`'s
   own per-cycle candidate limit (or its cycle frequency) would directly reduce how often a
   hard-blockable, actually-untradeable contract slips through with zero check performed.
2. **§7.1 Reconcile the two verdict computations** — the full weighted verdict and the Phase 3
   lightweight verdict can now genuinely disagree for the same symbol with no flag surfaced when
   they do; a real, EB-15-introduced duplication worth resolving before it's load-bearing for a
   human's trust in either number.
3. **§5.2 Full label-sensitivity grid study** — EB-15 ran a narrower 30/45/60-minute version;
   the blueprint's original 9+-definition grid across horizon/excursion/stability cuts has still
   never been run. Remains the single highest-value, highest-effort item once enough post-EB-10
   episodes accumulate to make it meaningful.
4. **§6.5 / §1.1 deeper architectural consolidation** — the dashboard-visibility half of this is
   now fixed (Phase 3+7); the underlying question of whether EB-1's universe-wide state should
   ever feed EB-8's full weighted verdict for every symbol (not just fired candidates), and
   whether the legacy trackers (§1.1) can finally be deprecated, remains a bigger, riskier change
   — correctly still not attempted casually.

Everything else in this document is either a genuinely low-priority polish item or explicitly,
correctly blocked on real time passing (more archived episodes, more trading sessions) rather
than more engineering effort.
