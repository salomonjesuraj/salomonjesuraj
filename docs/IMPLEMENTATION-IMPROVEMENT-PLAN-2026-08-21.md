# Infusion Improvement Implementation Report

Date: 2026-08-21

## Summary

This report records the first implementation pass after the full repository review.
The completed work focuses on making local verification reproducible before larger UI,
typing, lint, and test-coverage upgrades. This is intentional: the repository already has
many services and dashboard surfaces, but the quality gates were not runnable from a
fresh checkout because test tools were configured but not installed by any project command.

## Changes Completed

### 1. Reproducible developer test setup

Added `requirements-dev.txt` with lightweight core developer tooling:

- `pytest`
- `pytest-asyncio`
- `ruff`
- `mypy`
- core runtime dependencies needed by the editable packages used in tests

The file deliberately avoids making `torch` and `transformers` part of the default test
setup. Those belong to `sentiment-engine`, which is a heavy optional service runtime and
should not be required for ordinary unit tests.

### 2. Venv-aware Makefile commands

Updated `Makefile` so quality commands use `.venv` tools instead of assuming global tools:

- `setup-dev`
- `lint`
- `format`
- `typecheck`
- `test`
- `test-all`
- `test-integration`
- `compile-check`
- `compose-check`

Because this repository is currently being worked on from Windows PowerShell where `make`
may not be installed, also added `scripts/dev.ps1` with the same task names.

The default editable install path now covers the core packages needed by the current tests:

- `libs/infusion-models`
- `libs/infusion-streams`
- `libs/infusion-common`
- `services/feature-engine`
- `services/api`
- `services/scanner`
- `services/archiver`

### 3. Modern Ruff configuration

Moved deprecated top-level Ruff lint settings into `[tool.ruff.lint]`.
This removes the Ruff deprecation warning without changing the selected lint rules.

### 4. Cache hygiene

Updated `.gitignore` to ignore local verification caches:

- `.pytest_cache/`
- `.ruff_cache/`
- `.mypy_cache/`

### 5. Backend coverage and typed shared packages

Added focused unit coverage for the stock breakout score path:

- missing 20-session volume profile no longer earns real RVol score points
- `OPTION_READY` only upgrades an already-qualified stock breakout

Also hardened the shared packages and scheduler under strict mypy:

- `libs/infusion-models`
- `libs/infusion-streams`
- `libs/infusion-common`
- `services/scheduler`

The shared packages now carry `py.typed` markers and pass mypy as a group.

### 6. Dashboard verification and UI consolidation

Added a zero-dependency dashboard JS verifier:

- `services/dashboard/package.json`
- `services/dashboard/scripts/verify-js.mjs`
- `services/dashboard/scripts/verify-shell.mjs`
- `dashboard-check` in `Makefile`
- `dashboard-check` in `scripts/dev.ps1`

The Windows runner now falls back to Codex's bundled Node runtime when system `node`
is not on PATH.

The New dashboard shell is now the default first view, while Classic remains available
as a fallback. Stock Breakout Radar has been promoted into the New shell primary flow
above the F&O screener so the UI matches the stock-first, contract-second workflow.
The rail's Breakout Radar action now returns to that primary surface instead of opening
a hidden duplicate pane.

The shell verifier checks that:

- New is the default dashboard mode unless Classic was explicitly saved.
- `breakoutRadarV2` is mounted exactly once.
- Breakout Radar appears before the F&O Screener in the New primary view.
- required New shell mount points exist exactly once.
- rail rows have matching panes, with Breakout Radar handled as a primary-view shortcut.

### 7. Additional strict typing pass

Added strict annotations and safer payload decoding for:

- `services/alerter/src/alerter`
- `services/api/src/api/main.py`
- `services/api/src/api/routes/shadow_validation.py`
- `services/scanner/src/scanner/main.py`
- scanner evidence/scoring helpers:
  - `scanner/alignment.py`
  - `scanner/episode_manager.py`
  - `scanner/ml_score.py`
  - `scanner/pine_confidence.py`
  - `scanner/portfolio_risk.py`
  - `scanner/scoring.py`
  - `scanner/strategies/base.py`
  - `scanner/suppression.py`
  - `scanner/trap_model.py`
- remaining scanner core:
  - `scanner/engine.py`
  - `scanner/pre_breakout.py`
  - `scanner/sector.py`
  - `scanner/verdict_engine.py`
  - `scanner/strategies/options_first_hybrid.py`
  - `scanner/strategies/vol_vwap_breakout.py`

The alerter package now passes mypy as a package, and the edited API/scanner entrypoints
pass targeted mypy checks. The full scanner package now passes strict mypy as a group.

### 8. Feature-engine strict typing pass

Completed strict annotations and safer boundary handling for the full feature-engine
package:

- `feature_engine/bar_builder.py`
- `feature_engine/engine.py`
- `feature_engine/main.py`
- all 16 `feature_engine/features/*` modules

This pass kept the indicator and market-structure math unchanged, but made async
callbacks, Redis loader payloads, OHLC bar aggregation, mixed feature snapshots, and
optional depth values explicit. The full `services/feature-engine/src/feature_engine`
package now passes strict mypy as a group.

### 9. API helper strict typing pass

Completed a first API helper cluster under strict mypy:

- `api/chart_patterns.py`
- `api/cost_model.py`
- `api/market_context.py`
- `api/relative_strength.py`
- `api/sentiment.py`
- `api/vix_sizing.py`
- `api/wyckoff.py`

Most changes made mixed analysis-result dictionaries explicit. One logic-adjacent
hardening was added in relative strength: slope arithmetic now uses direct `None`
guards instead of relying on a tuple membership shortcut.

### 10. Options analytics helper strict typing pass

Completed strict typing for the smaller option-readiness analytics helpers:

- `api/daily_trend_filter.py`
- `api/option_reality.py`
- `api/options_analytics.py`
- `api/options_analytics_v2.py`

This pass typed the option-chain row shapes, reality-gate result payloads, dynamic wall
snapshots, weighted PCR outputs, and daily-trend filter outputs without changing the
pricing or indicator formulas.

### 11. Multi-leg options strategy strict typing pass

Completed strict typing for `api/options_strategies.py`, including:

- option-chain row aliases
- strategy result payload aliases
- catalog builder callable typing
- missing-row tolerant leg helpers for thin chains
- explicit ready/not-ready result typing

This pass preserves the existing strategy formulas while making the thin-chain failure
path safer: missing strike rows now flow through helpers as empty market/Greek data
rather than being treated as guaranteed dictionaries.

### 12. API analysis-helper strict typing pass

Completed strict typing for another dashboard/API analysis cluster:

- `api/anchored_vwap.py`
- `api/intelligence.py`
- `api/label_study.py`
- `api/market_breadth.py`
- `api/statistics_utils.py`
- `api/vcp.py`

This pass typed mixed dashboard payloads, market-breadth Redis decodes, AVWAP anchor
maps, VCP component payloads, statistics summaries, and label-study window results.
The label-study recommendation logic now narrows timeout percentages before comparing
windows, and the intelligence layer now guards optional nested option metrics before
reading from them.

### 13. API reliability-helper strict typing pass

Completed strict typing for another reliability/reporting cluster:

- `api/event_calendar.py`
- `api/portfolio_risk_daily.py`
- `api/promotion_review.py`
- `api/signal_snapshot.py`
- `api/trap_labels.py`

This pass typed Redis/Postgres helper boundaries and result payloads. The false-break
statistics path now keeps grouped label lists as real booleans after excluding
unavailable rows, preventing optional labels from leaking into rate calculations.

### 14. API queue payload strict typing pass

Completed strict typing for the next live-data queue cluster:

- `api/sentiment_queue.py`
- `api/portfolio_risk_queue.py`
- `api/mtf_queue.py`
- `api/options_dynamics_queue.py`
- `api/futures_queue.py`
- `api/news_queue.py`

This pass typed the queue app/Redis/Postgres boundaries, JSON status payloads, futures
contract maps, MTF refresh failure payloads, and news headline cache payloads. Runtime
behavior is unchanged; the improvement is that the live queue layer now has explicit
payload contracts around the mixed Redis, API, and database data it moves into the
dashboard-facing cache.

### 15. API route payload strict typing pass

Completed strict typing for the next small route cluster:

- `api/routes/verify.py`
- `api/routes/upstox_news.py`
- `api/routes/triggers.py`

This pass typed request/response handlers, Redis hash decoders, trigger evaluation
payloads, Upstox news article rows, and manual price-trigger alert payloads. The verify
route now uses one shared Redis decode helper instead of repeating local decode closures
inside every source block, which makes the live-data consistency endpoint easier to
audit.

### 16. API external-data helper strict typing pass

Completed strict typing for the helper layer behind the news and futures queues:

- `api/news_ingestion.py`
- `api/futures.py`

This pass typed Upstox article payloads, news event mapping, futures master contracts,
quote payloads, and cached futures master decoding. It also replaced a raw integer HTTP
timeout in the news fetcher with an explicit `aiohttp.ClientTimeout`, matching the
client API used elsewhere in the service.

### 17. EBIE state queue strict typing pass

Completed strict typing for `api/ebie_state_queue.py`, including:

- lightweight verdict payloads
- canonical state mapping inputs
- Redis previous-state reads/writes
- futures context decode maps
- market-context and queue status payloads

This pass keeps the EBIE shadow state-machine behavior unchanged while making the
central queue boundary explicit: every mixed Redis/API row now flows through a shared
payload type instead of unbounded dictionaries.

### 18. API dashboard route strict typing pass

Completed strict typing for another dashboard-facing route cluster:

- `api/routes/system.py`
- `api/routes/sentiment.py`
- `api/routes/scanner.py`

This pass typed route handlers, scanner signal payloads, suppressed-candidate payloads,
pre-breakout watchlist rows, sector/regime rows, alert-test payloads, and muted
symbol/strategy decoding. `api/routes/scanner.py` now uses a single Redis hash decoder
instead of two duplicate `_decode_hash` implementations.

### 19. API ticks route strict typing pass

Completed strict typing for `api/routes/ticks.py`, the main dashboard tick/intelligence
route, including:

- index, F&O-ban, VWAP-state, and opening-range context helpers
- stock breakout tier/type helpers
- option level planning and trade-map payloads
- scanner intelligence payloads
- historical MTF, news, option-chain, and event-risk overlays
- `/api/ticks`, `/api/ticks/{symbol}`, `/api/ticks/snapshot`, and `/api/symbols`

This pass keeps the dashboard response shape intact while making the main mixed Redis
payload route strict-typed. It also narrows nullable nested MTF/relative-strength
payloads before score and percentile calculations.

### 20. API safety/risk route strict typing pass

Completed strict typing for the compact safety and risk route group:

- `api/routes/safety.py`
- `api/routes/risk.py`
- `api/routes/radar_alerts.py`
- `api/routes/portfolio_risk.py`
- `api/routes/options_dynamics.py`

This pass typed safety cockpit status payloads, kill-switch request handling, risk
setting defaults, radar alert rows, portfolio risk summaries, and options dynamics
responses. It also added explicit JSON/Redis/Postgres payload narrowing before route
handlers read mixed dashboard dictionaries.

### 21. API execution route strict typing pass

Completed strict typing for `api/routes/execution.py`, including staged execution ticket
building, nested trade/option/news/event payload decoding, staged-row loading, and current
max-lots lookup. Request JSON is now validated before ticket creation, and decoded Redis
payloads are narrowed before nested fields are read.

### 22. API news route strict typing pass

Completed strict typing for `api/routes/news.py`, including headline scoring, news-edge
classification, compact article payloads, Redis edge caching, Google News RSS fallback
items, and the `/api/news/market` route. The route now narrows external GDELT/RSS payloads
before reading titles and URLs while preserving the existing free-news response shape.

### 23. API route completion cluster

Completed strict typing for the remaining high-impact API route cluster:

- `api/routes/mtf.py`
- `api/routes/journal.py`
- `api/routes/strategy_selector.py`
- `api/routes/market.py`
- `api/routes/ai.py`

This pass typed the historical MTF engine, paper-trade journal boundaries,
multi-leg strategy selector, market/options route helpers, Upstox option-chain
payloads, AI advisory request/response payloads, and HTTP timeout usage.

### 24. Ingestion and WebSocket package strict typing pass

Completed focused strict typing for the ingestion package and WebSocket gateway:

- Upstox protobuf decoder payloads and depth rows
- broker adapter base, mock adapter, and Upstox adapter callbacks
- ingestion publisher, supervisor, subscription registry, capability registry, and main service
- websocket client manager and gateway startup handlers

Both `services/ingestion/src/ingestion` and `services/ws-gateway/src/ws_gateway`
now pass focused mypy and Ruff checks.

### 25. Archiver and normalizer package strict typing pass

Completed focused strict typing for archiver and normalizer packages, including
signal analytics result payloads, recap formatting data, batched signal writer
buffers, outcome tracker stats, archiver stream backfill decoding, normalizer
resolver loading, transformer payloads, and normalizer entrypoint typing.

Both `services/archiver/src/archiver` and `services/normalizer/src/normalizer`
now pass focused mypy and Ruff checks.

### 26. NSE scraper and lightweight service strict typing pass

Completed focused strict typing for `services/nse-scraper/src/nse_scraper` and
the small service entrypoints. This covered OAuth state decoding, Redis bytes/text
boundaries, Upstox instrument-map loading, NSE delivery capture, F&O ban capture,
instrument map storage, HTTP timeout construction, and service startup loops.

The following now pass focused mypy and Ruff checks:

- `services/nse-scraper/src/nse_scraper`
- `services/telegram-bot/src/telegram_bot/main.py`
- `services/sector-intel/src/sector_intel/main.py`
- `services/conviction/src/conviction/main.py`

### 27. API small-route and advisory strict typing pass

Completed focused strict typing for another API route cluster and the AI advisor.
The route cleanup covers health, futures, features, events, analytics, charts,
auth, and EBIE state. The AI advisor now has typed response payloads, typed OpenAI
content extraction, and safer JSON result narrowing.

The following now pass focused mypy and Ruff checks:

- `services/api/src/api/routes/health.py`
- `services/api/src/api/routes/futures.py`
- `services/api/src/api/routes/features.py`
- `services/api/src/api/routes/events.py`
- `services/api/src/api/routes/analytics.py`
- `services/api/src/api/ai_advisor.py`
- `services/api/src/api/routes/charts.py`
- `services/api/src/api/routes/auth.py`
- `services/api/src/api/routes/ebie_state.py`

### 28. Calibration, queues, and sentiment strict typing pass

Completed focused strict typing for calibration/report helpers, live queue helpers,
and the sentiment engine package. This covered reliability curves, verdict
calibration, shadow validation comparisons, radar alert tier-state Redis payloads,
option-chain candidate scoring, sentiment pool startup, and HuggingFace classifier
result narrowing.

The following now pass focused mypy and Ruff checks:

- `services/api/src/api/calibration.py`
- `services/api/src/api/verdict_calibration.py`
- `services/api/src/api/shadow_validation.py`
- `services/api/src/api/radar_alert_queue.py`
- `services/api/src/api/option_chain_queue.py`
- `services/sentiment-engine/src/sentiment_engine`

### 29. Complete walkthrough status

Implemented now:

- Local developer foundation is in place through `Makefile`, `scripts/dev.ps1`,
  dev requirements, compile checks, Compose config checks, dashboard static checks,
  and test commands.
- Ruff formatting and lint are clean across the repository.
- The dashboard New shell is the default, Classic remains as fallback, and Stock
  Breakout Radar is promoted into the first New-shell flow.
- Shared libraries, scheduler, alerter, scanner, feature-engine, ingestion,
  WebSocket gateway, archiver, normalizer, NSE scraper, sentiment engine, many API
  helper modules, many API route groups, calibration helpers, queue helpers, and
  AI advisory helpers now pass focused strict-typing checks.
- JSON, Redis, msgpack, HTTP, and third-party model boundaries have been made
  safer in the completed modules through explicit payload aliases, casts only at
  external boundaries, bytes/text decoding, nullable guards, and concrete return
  types.

Improved:

- Full strict mypy reduced from 1425 errors across 158 files to 114 errors across
  4 files.
- The remaining type surface is now concentrated in four known files instead of
  spread across the whole repo.
- The dashboard has automated static verification for JavaScript syntax and shell
  wiring.
- Basic market-data unit coverage exists for stock breakout score behavior and
  `OPTION_READY` tier behavior.
- Python formatting/lint drift is now catchable from one local command.

Still pending:

- Full strict mypy is not zero yet. The remaining files are:
  - `services/api/src/api/routes/ebie_candidates.py`
  - `services/api/src/api/ml_classifier.py`
  - `services/api/src/api/routes/backtest.py`
  - `services/api/src/api/ai_query.py`
- Backend tests are still thin: six unit tests pass, but integration and load
  folders still do not contain real coverage.
- Browser-level dashboard QA is still pending because this Windows runtime has
  Node but no `npx`/npm Playwright runner available.
- UI polish beyond the default-shell upgrade is still pending: responsive visual
  QA, mobile overlap checks, empty/loading/error states, selected-stock detail
  ergonomics, and eventual Classic shell removal.
- CI is still pending, so local quality checks are not yet enforced automatically
  on every change.

Best next execution order:

1. Finish strict typing for `services/api/src/api/ai_query.py`.
2. Finish strict typing for `services/api/src/api/ml_classifier.py`.
3. Finish strict typing for `services/api/src/api/routes/ebie_candidates.py`.
4. Finish strict typing for `services/api/src/api/routes/backtest.py`.
5. Re-run full mypy and drive the result to zero errors.
6. Add backend unit coverage for the remaining scoring, EBIE, option-chain, and
   backtest edge cases.
7. Add Docker-backed integration tests for Redis, Postgres, streams, API payloads,
   and dashboard proxy wiring.
8. Add Playwright dashboard smoke tests when npm or a Playwright runner is
   available.
9. Complete the UI upgrade pass: responsive screenshots, clearer score language,
   selected-stock detail cleanup, consistent loading/error states, and Classic
   removal after parity is proven.

## Verification Results

These were the results from the verification pass:

| Check | Result |
|---|---|
| `python -m pytest tests/ -q` | Passed: 6 tests |
| `python -m pytest tests/unit/ -q` | Passed: 6 tests |
| `python -m pytest tests/integration/ -q` | No tests found |
| `python -m compileall -q libs services scripts tests` | Passed |
| `docker compose config --quiet` | Passed |
| Dashboard JS syntax check, excluding vendor bundle | Passed for 49 files |
| Dashboard shell wiring check | Passed |
| `ruff check .` before cleanup | Failed: 478 findings |
| `ruff format --check .` before cleanup | Failed: 179 files would be reformatted |
| `ruff format .` | Completed: 179 files reformatted, then 7 post-fix files reformatted |
| `ruff check . --fix` | Completed: 294 safe fixes applied |
| `ruff check . --fix --unsafe-fixes` | Completed: 134 additional fixes applied |
| Manual Ruff cleanup | Completed: remaining 79 findings fixed |
| `ruff format --check .` after cleanup | Passed: 246 files already formatted |
| `ruff check .` after cleanup | Passed: all checks |
| `mypy libs/ services/` before shared-package hardening | Failed: 1425 errors in 158 files |
| `mypy libs/ services/` after shared-package hardening | Failed: 1280 errors in 140 files |
| `mypy libs/ services/` after scheduler hardening | Failed: 1235 errors in 138 files |
| `mypy libs/ services/` after alerter/API/scanner entrypoint hardening | Failed: 1189 errors in 131 files |
| `mypy libs/ services/` after scanner helper hardening | Failed: 1140 errors in 122 files |
| `mypy libs/ services/` after full scanner hardening | Failed: 1062 errors in 116 files |
| `mypy libs/ services/` after full feature-engine hardening | Failed: 967 errors in 98 files |
| `mypy libs/ services/` after first API helper hardening | Failed: 944 errors in 91 files |
| `mypy libs/ services/` after options analytics helper hardening | Failed: 929 errors in 87 files |
| `mypy libs/ services/` after multi-leg options strategy hardening | Failed: 871 errors in 86 files |
| `mypy libs/ services/` after API analysis-helper hardening | Failed: 813 errors in 80 files |
| `mypy libs/ services/` after API reliability-helper hardening | Failed: 782 errors in 75 files |
| `mypy libs/ services/` after API queue payload hardening | Failed: 752 errors in 69 files |
| `mypy libs/ services/` after API route payload hardening | Failed: 723 errors in 66 files |
| `mypy libs/ services/` after API external-data helper hardening | Failed: 707 errors in 64 files |
| `mypy libs/ services/` after EBIE state queue hardening | Failed: 694 errors in 63 files |
| `mypy libs/ services/` after API dashboard route hardening | Failed: 664 errors in 60 files |
| `mypy libs/ services/` after API ticks route hardening | Failed: 608 errors in 59 files |
| `mypy libs/ services/` after API safety/risk route hardening | Failed: 568 errors in 54 files |
| `mypy libs/ services/` after API execution route hardening | Failed: 521 errors in 53 files |
| `mypy libs/ services/` after API news route hardening | Failed: 512 errors in 52 files |
| `mypy libs/ services/` after API MTF/journal/market/AI route hardening | Failed: 355 errors in 47 files |
| `mypy libs/ services/` after ingestion/WebSocket hardening | Failed: 277 errors in 36 files |
| `mypy libs/ services/` after archiver/normalizer hardening | Failed: 248 errors in 28 files |
| `mypy libs/ services/` after NSE scraper hardening | Failed: 222 errors in 23 files |
| `mypy libs/ services/` after lightweight service entrypoint hardening | Failed: 213 errors in 20 files |
| `mypy libs/ services/` after small API route hardening | Failed: 194 errors in 15 files |
| `mypy libs/ services/` after AI advisor hardening | Failed: 186 errors in 14 files |
| `mypy libs/ services/` after charts/auth hardening | Failed: 175 errors in 12 files |
| `mypy libs/ services/` after EBIE state route hardening | Failed: 171 errors in 11 files |
| `mypy libs/ services/` after calibration/shadow hardening | Failed: 142 errors in 8 files |
| `mypy libs/ services/` after queue helper hardening | Failed: 127 errors in 7 files |
| `mypy libs/ services/` after sentiment engine hardening | Failed: 114 errors in 4 files |
| `mypy libs/infusion-models libs/infusion-streams libs/infusion-common` | Passed: 24 files |
| `mypy services/scheduler/src/scheduler` | Passed: 3 files |
| `mypy services/alerter/src/alerter` | Passed: 8 files |
| `mypy services/api/src/api/main.py services/api/src/api/routes/shadow_validation.py services/scanner/src/scanner/main.py` | Passed: 3 files |
| `mypy scanner helper group` | Passed: 9 files |
| `mypy services/scanner/src/scanner` | Passed: 20 files |
| `mypy services/feature-engine/src/feature_engine/features` | Passed: 16 files |
| `mypy services/feature-engine/src/feature_engine` | Passed: 22 files |
| `mypy API helper/options strategy group` | Passed: 12 files |
| `mypy API analysis/reliability helper group` | Passed: 11 files |
| `mypy API queue payload group` | Passed: 6 files |
| `mypy API route payload group` | Passed: 3 files |
| `mypy API external-data helper group` | Passed: 2 files |
| `mypy API EBIE state queue` | Passed: 1 file |
| `mypy API dashboard route group` | Passed: 3 files |
| `mypy API ticks route` | Passed: 1 file |
| `mypy API safety/risk route group` | Passed: 5 files |
| `mypy API execution route` | Passed: 1 file |
| `mypy API news route` | Passed: 1 file |
| `mypy API MTF/journal/strategy-selector/market/AI route group` | Passed: 5 files |
| `mypy services/ingestion/src/ingestion` | Passed: 12 files |
| `mypy services/ws-gateway/src/ws_gateway` | Passed: 4 files |
| `mypy services/archiver/src/archiver` | Passed: 7 files |
| `mypy services/normalizer/src/normalizer` | Passed: 8 files |
| `mypy services/nse-scraper/src/nse_scraper` | Passed: 7 files |
| `mypy lightweight service entrypoints` | Passed: 3 files |
| `mypy API small-route/advisory group` | Passed: 9 files |
| `mypy API calibration/shadow group` | Passed: 3 files |
| `mypy API queue helper group` | Passed: 2 files |
| `mypy services/sentiment-engine/src/sentiment_engine` | Passed: 5 files |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 test` | Passed: 6 tests |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 compile-check` | Passed |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 compose-check` | Passed |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 dashboard-check` | Passed: 49 dashboard JS files + shell wiring |
| Playwright browser smoke test | Not run: this Windows environment has Node but no `npx`/npm runner on PATH or in the bundled runtime |

After the config change, `ruff check pyproject.toml` passes and the previous Ruff
deprecation warning is gone. Full-repo Ruff now passes: 478 initial findings were
reduced to zero through formatting, safe fixes, scoped script ignores, unsafe-fix
review, and targeted manual cleanup.

## Files Changed In This Pass

| File | Change |
|---|---|
| `.gitignore` | Added local cache ignores for pytest, Ruff, and mypy. |
| `Makefile` | Added venv-aware setup, test, lint, typecheck, compile, and Compose check targets. |
| `pyproject.toml` | Moved Ruff lint settings to `[tool.ruff.lint]`. |
| `requirements-dev.txt` | Added lightweight developer/test dependencies. |
| `scripts/dev.ps1` | Added Windows-native dev/test runner mirroring Makefile tasks, fixed exit-code propagation, and added dashboard-check with bundled Node fallback. |
| `docs/IMPLEMENTATION-IMPROVEMENT-PLAN-2026-08-21.md` | Added this implementation report and full upgrade plan. |
| `services/dashboard/package.json` | Added dashboard check/lint scripts. |
| `services/dashboard/scripts/verify-js.mjs` | Added zero-dependency dashboard JS syntax verifier. |
| `services/dashboard/scripts/verify-shell.mjs` | Added dashboard shell/default-mode wiring verifier. |
| `services/dashboard/public/index.html` | Made New shell the default, promoted Stock Breakout Radar into the primary New shell flow, and kept Classic as fallback. |
| `services/dashboard/public/js/dashboard-mode.js` | Changed first-visit dashboard mode default to New. |
| `services/dashboard/public/js/mode-switch.js` | Updated the persistent shell switch text for New-primary/Classic-fallback rollout. |
| `services/dashboard/public/js/rail-v2.js` | Routed Breakout Radar rail action back to the primary view instead of a duplicate pane. |
| `services/alerter/src/alerter/*` | Added strict payload/helper typing across the alerter package. |
| `services/api/src/api/main.py` | Added entrypoint and health-handler annotations. |
| `services/api/src/api/routes/shadow_validation.py` | Added route handler request/response annotations. |
| `services/scanner/src/scanner/main.py` | Added safer symbol metadata decoding and startup-loop annotations. |
| `services/scanner/src/scanner/{alignment,episode_manager,ml_score,pine_confidence,portfolio_risk,scoring,suppression,trap_model}.py` | Added strict helper typing and an explicit frozen-episode invariant guard. |
| `services/scanner/src/scanner/strategies/base.py` | Typed the shared `SignalCandidate` strategy contract. |
| `services/scanner/src/scanner/{engine,pre_breakout,sector,verdict_engine}.py` | Completed strict scanner package typing, including Redis/JSON cache boundaries and mixed scoring payloads. |
| `services/scanner/src/scanner/strategies/{options_first_hybrid,vol_vwap_breakout}.py` | Typed concrete strategy feature inputs. |
| `services/feature-engine/src/feature_engine/bar_builder.py` | Typed completed-bar aggregation output. |
| `services/feature-engine/src/feature_engine/engine.py` | Typed async callbacks, loader contracts, tick payloads, completed bars, and mixed ML feature payload assembly. |
| `services/feature-engine/src/feature_engine/main.py` | Typed service entrypoint callbacks and Redis decoding helpers. |
| `services/feature-engine/src/feature_engine/features/*.py` | Completed strict typing for all feature modules, including price, momentum, volatility, microstructure, candles, ICT, Fibonacci, divergence, zones, structure, and volume-manager snapshots. |
| `services/api/src/api/{chart_patterns,cost_model,market_context,relative_strength,sentiment,vix_sizing,wyckoff}.py` | Typed mixed API analysis helper payloads and added explicit relative-strength `None` guards. |
| `services/api/src/api/{daily_trend_filter,option_reality,options_analytics,options_analytics_v2}.py` | Typed option-chain analytics, option reality gates, dynamic wall snapshots, and daily trend filter payloads. |
| `services/api/src/api/options_strategies.py` | Typed multi-leg options strategy catalog/results and made missing strike-row helpers explicit. |
| `services/api/src/api/{anchored_vwap,intelligence,label_study,market_breadth,statistics_utils,vcp}.py` | Typed API analysis helpers, Redis decode boundaries, dashboard intelligence payloads, and study/result dictionaries. |
| `services/api/src/api/{event_calendar,portfolio_risk_daily,promotion_review,signal_snapshot,trap_labels}.py` | Typed reliability/reporting helpers and kept false-break grouped labels as booleans only. |
| `services/api/src/api/{sentiment_queue,portfolio_risk_queue,mtf_queue,options_dynamics_queue,futures_queue,news_queue}.py` | Typed live queue app boundaries, JSON status payloads, Redis cache payloads, futures contract maps, and news headline caches. |
| `services/api/src/api/routes/{verify,upstox_news,triggers}.py` | Typed route handlers, Redis decode helpers, Upstox news article payloads, and manual trigger evaluation/alert payloads. |
| `services/api/src/api/{news_ingestion,futures}.py` | Typed Upstox news/futures API payloads, futures master cache decoding, quote maps, and HTTP timeout usage. |
| `services/api/src/api/ebie_state_queue.py` | Typed EBIE shadow state payloads, lightweight verdicts, Redis state boundaries, and futures/market context maps. |
| `services/api/src/api/routes/{system,sentiment,scanner}.py` | Typed dashboard route handlers and scanner/alert/watchlist payloads; consolidated scanner Redis hash decoding. |
| `services/api/src/api/routes/ticks.py` | Typed the main dashboard ticks/intelligence route, context helpers, overlays, and snapshot/symbol endpoints. |
| `services/api/src/api/routes/{safety,risk,radar_alerts,portfolio_risk,options_dynamics}.py` | Typed compact safety/risk route handlers, JSON decode boundaries, health/status payloads, risk defaults, radar rows, and dashboard summary responses. |
| `services/api/src/api/routes/execution.py` | Typed staged execution ticket creation, nested signal context payloads, staged-row loading, and risk max-lots decoding. |
| `services/api/src/api/routes/news.py` | Typed public news route payloads, headline scoring, RSS fallback rows, Redis edge caching, and GDELT article narrowing. |
| `services/api/src/api/routes/{mtf,journal,strategy_selector,market,ai}.py` | Typed the remaining high-impact API route payloads, option-chain helpers, journal Redis rows, MTF candle payloads, AI advisory payloads, and route handler signatures. |
| `services/ingestion/src/ingestion/**` | Completed focused ingestion package typing across adapters, protobuf decoder, publisher, supervisor, registries, and service entrypoint. |
| `services/ws-gateway/src/ws_gateway/**` | Completed focused WebSocket gateway typing for client manager state, websocket handlers, batch flushing, and startup loop. |
| `services/archiver/src/archiver/**` | Completed focused archiver package typing for analytics, recap, writer, tracker, and stream backfill/main service payloads. |
| `services/normalizer/src/normalizer/**` | Completed focused normalizer package typing for resolver loading, transformer payloads, and service entrypoint. |
| `services/nse-scraper/src/nse_scraper/**` | Completed focused NSE scraper typing for OAuth Redis state, Upstox instrument loading, NSE delivery capture, F&O ban capture, HTTP timeouts, and service startup. |
| `services/telegram-bot/src/telegram_bot/main.py` | Typed the service startup loop and entrypoint. |
| `services/sector-intel/src/sector_intel/main.py` | Typed the service startup loop and entrypoint. |
| `services/conviction/src/conviction/main.py` | Typed the service startup loop and entrypoint. |
| `services/api/src/api/routes/{health,futures,features,events,analytics}.py` | Typed small API route handlers, request payloads, Redis/JSON decode boundaries, futures rows, feature rows, event rows, and analytics date parsing. |
| `services/api/src/api/ai_advisor.py` | Typed AI advisory digest, query response, OpenAI text extraction, JSON narrowing, and public methods. |
| `services/api/src/api/routes/{charts,auth,ebie_state}.py` | Typed chart aggregation helpers, auth session/OAuth payloads, msgpack/JSON request bodies, EBIE state decoding, and dashboard response rows. |
| `services/api/src/api/{calibration,verdict_calibration,shadow_validation}.py` | Typed calibration curves, verdict calibration reports, shadow-validation comparisons, gate summaries, and episode fetch payloads. |
| `services/api/src/api/{radar_alert_queue,option_chain_queue}.py` | Typed queue-loop app boundaries, tier state storage, tick-row narrowing, option-chain symbol universes, candidate scoring, and Redis payload handling. |
| `services/sentiment-engine/src/sentiment_engine/**` | Completed focused sentiment engine typing for asyncpg records, classifier model/tokenizer boundaries, sentiment results, and main service startup. |
| `tests/unit/test_market_data_foundation.py` | Added stock breakout score and `OPTION_READY` tier coverage. |
| Shared package `py.typed` markers | Added PEP 561 type markers for `infusion-models`, `infusion-streams`, and `infusion-common`. |
| Python and Markdown files across the repo | Reformatted by Ruff. |
| Python files across the repo | Safe Ruff auto-fixes, reviewed unsafe fixes, and targeted manual lint cleanup applied. |

## Current Gaps

### Testing

The existing test suite is still too small for the size of the system. There are now six
passing unit tests, and the `tests/integration` and `tests/load` folders contain no real
tests yet.

### Lint and formatting

Ruff formatting and lint now pass across the repository. The cleanup included import
ordering, unused variables, unnecessary f-strings, `try/except/pass` patterns,
`zip(strict=...)`, script-specific import bootstrapping exceptions, ambiguous variable
names, and bare exception handling.

### Typing

Mypy is configured as strict, and most of the service tree now passes focused
module checks. The shared packages, scheduler, alerter package, scanner package,
feature-engine package, API helper clusters, route payload builders, external-data
helpers, EBIE state queue, multiple API route groups, ingestion, WebSocket
gateway, archiver, normalizer, NSE scraper, sentiment engine, calibration helpers,
queue helpers, and the multi-leg options strategy builder now pass targeted mypy
checks. The full `libs/ services/` result improved from 1425 errors across 158
files to 114 errors across 4 files. The remaining files are:

- `services/api/src/api/routes/ebie_candidates.py`
- `services/api/src/api/ml_classifier.py`
- `services/api/src/api/routes/backtest.py`
- `services/api/src/api/ai_query.py`

The most common remaining categories are missing generic type arguments, missing
function annotations, nullable values used without guards, untyped calculation
payloads, sortable-key type issues, and Redis/API boundary dictionaries that still
need concrete payload types.

### UI

The dashboard still carries Classic and New shells in parallel, but New is now the primary
default and Classic is the fallback. Stock-breakout discovery now appears first in the New
shell before option-contract confirmation. Remaining UI work is visual browser QA, mobile
overlap checks, and eventually removing Classic after parity has held for a release.

## Full Upgrade Plan

### Phase 1: Finish local quality foundation

1. Run `make setup-dev` from a clean checkout.
   - On Windows PowerShell, run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 setup-dev`.
2. Confirm `make test`, `make compile-check`, and `make compose-check` pass.
   - On Windows PowerShell, use `scripts/dev.ps1 test`, `scripts/dev.ps1 compile-check`, and `scripts/dev.ps1 compose-check`.
3. Keep `sentiment-engine` as an optional install path, not part of default setup.

### Phase 2: Formatting-only cleanup

Completed in this pass. Ruff formatting now reports 246 files already formatted.

### Phase 3: Ruff cleanup

Completed in this pass. Full-repo Ruff lint now passes.

1. Safe auto-fixes reduced the initial findings.
2. Reviewed unsafe fixes were applied.
3. Remaining findings were manually fixed.
4. `ruff check .` now passes.

### Phase 4: Backend test expansion

Add focused tests for:

- `/api/ticks` stock score and contract score separation
- `stock_breakout_score` - partially complete
- `stock_breakout_tier` - partially complete
- relative volume behavior when volume profile is unavailable - complete for unit path
- option-chain states: `TRADE_READY`, `WAIT_CONTRACT`, `AVOID_CONTRACT`
- EBIE hard gates
- EBIE cache freshness states: `fresh`, `stale`, `never_cached`
- scanner suppression gates
- chart merge and aggregation edge cases

### Phase 5: Integration tests

Add Docker-backed integration tests that:

1. Start Redis and Postgres.
2. Seed a small symbol universe.
3. Push mock ticks through the pipeline.
4. Assert Redis stream output.
5. Assert API endpoints return valid payloads.
6. Assert the dashboard Nginx proxy resolves `/api/` and `/ws`.

### Phase 6: Mypy strategy

Do not try to fix all mypy errors in one pass. Move strictness module-by-module:

1. `libs/infusion-models` - complete
2. `libs/infusion-streams` - complete
3. `libs/infusion-common` - complete
4. `services/scheduler` - complete
5. `services/alerter` - complete
6. selected API/scanner entrypoints - partially complete
7. `services/scanner` - complete
8. `services/feature-engine` - complete
9. API route payload builders - mostly complete
10. option strategy calculations - partially complete
11. EBIE state and calibration modules - complete for focused checks
12. `services/nse-scraper` - complete
13. `services/sentiment-engine` - complete
14. remaining API strict files:
    - `services/api/src/api/ai_query.py`
    - `services/api/src/api/ml_classifier.py`
    - `services/api/src/api/routes/ebie_candidates.py`
    - `services/api/src/api/routes/backtest.py`

### Phase 7: UI consolidation

1. Make the New shell the primary dashboard - complete
2. Freeze Classic as fallback for one release - implemented as fallback mode
3. Remove Classic after parity.
4. Keep `Breakout Radar` as the largest first screen surface - partially complete; it is now in the New primary flow above the screener.
5. Rename visible language around scores:
   - `Stock Score`
   - `Contract Score`
   - `RVol`
   - `Tier`
   - `Freshness`
   - `Next Action`
6. Move deep option-chain metrics into selected-stock detail.
7. Remove remaining user-facing ambiguity around `option_readiness` and `Conviction`.

### Phase 8: Frontend verification

1. Add a dashboard `package.json` - complete
2. Add JS lint/syntax scripts - complete
3. Add shell/default-mode wiring checks - complete
4. Add Playwright smoke tests:
   - dashboard loads
   - Classic/New toggle works while Classic exists
   - Breakout Radar renders
   - scanner renders
   - selected-stock detail opens
   - mobile viewport has no major overlap
5. Add screenshot baselines for desktop and mobile.

Playwright is currently blocked in this local environment because `npx`/npm is not
available. The bundled runtime provides Node only, which is enough for the static dashboard
checks but not enough for the Playwright CLI wrapper.

### Phase 9: Repository hygiene

1. Remove accidental nested empty folder `Infusion-Core-Architecture/` if confirmed unused.
2. Decide whether generated PDFs and `tmp/` outputs should stay in git.
3. Push the local branch after review.
4. Add CI so test/lint/type drift is visible on every change.

## Recommended Next Commit Order

1. Developer setup and config cleanup.
2. Formatting-only cleanup.
3. Ruff-safe auto-fixes.
4. Strict typing cleanup for `ai_query.py`.
5. Strict typing cleanup for `ml_classifier.py`.
6. Strict typing cleanup for `ebie_candidates.py`.
7. Strict typing cleanup for `backtest.py`.
8. Backend test expansion for scoring, EBIE, option-chain, and backtest paths.
9. Integration tests for Redis/Postgres/API/dashboard proxy flow.
10. Frontend Playwright tests once npm or a Playwright runner is available.
11. UI polish and Classic shell removal after New-shell parity is proven.
