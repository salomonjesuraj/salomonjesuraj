# Infusion — Project Abstract

**Last updated:** 2026-08-19

## What it is

Infusion is a live, paper-trading F&O (Futures & Options) intelligence system for the Indian NSE market. It scans a universe of roughly 208 F&O-eligible symbols in real time, generates scored trade signals across multiple independent strategies, tracks the real outcome of every signal it fires, self-validates its own edge with walk-forward statistics, and surfaces all of it in a live dashboard.

The system is explicitly **advisory-only**. Trades are staged, never placed live — every order path runs in `mode: paper_first_no_live_orders`. Every "propose" surface (strategy selection, position sizing, kill-switch triggers, the ML classifier, the AI advisor) is designed to stop short of auto-acting; a human always makes the final call.

## Architecture

Microservices, Docker Compose–orchestrated:

| Service | Responsibility |
|---|---|
| **ingestion** | Upstox WebSocket/REST market data intake |
| **feature-engine** | Computes the technical/structural feature set per symbol per tick |
| **scanner** | Signal-detection engine — strategies evaluate features into candidate signals, gated by dedupe/cooldown/suppression logic |
| **api** | FastAPI backend — routes for ticks, signals, backtest/analytics, options chain, market breadth, radar alerts, etc. |
| **archiver** | Persists fired signals and tracks their outcomes (target/stop/expiry) over time |
| **scheduler** | Periodic jobs — optimizer retrains, premium capture, VIX/Kelly sizing sweeps |
| **dashboard** | Vanilla JS/CSS frontend, no framework. Two parallel shells — **Classic** (the original, ~7,600-line accretion) and **New** (a from-scratch rebuild) — toggleable, both reading the same live data |
| **postgres / redis** | Durable outcome storage / hot cache + pub-sub |

## Capability layers, in build order

### Engine core (Phases 1–10)
Market structure (swing pivots, BOS/CHoCH), a full candlestick pattern library, ADX/Supertrend regime detection, multi-timeframe (MTF) confirmation, ATR/Turtle-style position sizing, Fibonacci confluence, classic pivots/CPR, MA-stack regime, chart-pattern geometry classification, ICT concepts (Fair Value Gaps, order blocks, liquidity sweeps), partial Wyckoff structural signals, cross-index (Dow-style) confirmation, and Volman-style entry timing.

### Options layer
PCR, OI-based support/resistance, Max Pain, IV Rank, and a 6-strategy multi-leg options catalog (bull/bear spreads, iron condor, straddle, strangle, covered call) with advisory-only strategy selection ranked against live chain data.

### Validation & trust infrastructure
A self-improving walk-forward optimizer; purged cross-validation with embargo gaps to close a leakage vector; net-of-cost precision (capturing real option premiums so "precision" reflects money made, not just target/stop hits); a trained logistic-regression classifier over 12,000+ archived real outcomes; per-feature information-coefficient and feature-ablation tooling; Deflated Sharpe Ratio reporting.

### Risk & sizing
Half-Kelly position sizing derived from real historical win-rate/R-multiple; VIX-tiered size multipliers; an F&O ban-list gate (hard NSE/SEBI legal constraint, checked before any signal publishes); a 5-component market breadth health score across the whole F&O universe.

### Dashboard evolution
- **Phase A–E** — design-system token consolidation; the "live vs. frozen" trust fix (the original driving complaint: entry/SL/target values visibly changing as price moved); a conviction-first visual redesign; backend feature surfacing; an accessibility pass.
- **Phase N1–N8 ("New shell")** — a second, parallel dashboard built clean rather than patched: Command Center, a redesigned screener table, a left rail, a Track Record strip, a Signal Integrity tab, and real T1/T2/T3 outcome tracking (not just binary target/stop).
- **Phase O.1–O.5** — progressive-disclosure noise reduction: collapsible evidence sections with derived one-line summaries, price-level column clustering, tab grouping — applied to both shells.
- **Phase R1–R9 (Stock Breakout Radar)** — restructured the dashboard to be stock-first rather than options-first: a real 0–100 breakout score, 8 breakout-type classifications (volume surge, day-high break, VWAP reclaim, opening-range break, sector leadership, relative strength vs. index, etc.), a dedicated Radar panel in both shells, and dashboard-only early alerts with their own tracked outcome ledger (graduated / faded / expired).
- **Phase W** — fixed a real engine bug: watch-tier Telegram alerts were re-firing with a drifting entry/SL/target ladder as price moved before the setup actually triggered. Alerts now freeze their price ladder per "episode" and only re-fire on genuine new information.
- **Color refresh** — Classic's shell had no real light/dark theme support at all (a structural class unconditionally forced light tokens regardless of the theme attribute). Rebuilt real, verified light and dark theming across both shells from the token layer up.

### Research follow-through
Two rounds of external GitHub-repo research (27 repos total across two passes) were triaged against the live codebase and turned into scoped, shipped work: Deflated Sharpe Ratio, signal-alignment gating, half-Kelly sizing, RSI divergence, VCP (Volatility Contraction Pattern / Minervini Stage-2) composite scoring, an F&O ban gate, and a hand-implemented candlestick-pattern fill (Harami, Tweezer, Dragonfly/Gravestone Doji, Pin Bar).

## Design discipline

Every phase above followed the same loop: **implement → rebuild → verify live against real data (not assumptions) → commit**, one phase at a time, never batching unverified work. Negative findings and gaps are disclosed directly rather than hidden — several phases explicitly recorded "verified except X" rather than claiming full completion. A handful of real bugs were caught and fixed *during* verification passes rather than assumed away (a duplicate-alert risk, a stale-cache render gap, a database-poisoning idempotency bug, and — most recently — a genuine CSS specificity conflict between the two dashboard shells' styling systems).

## Current state

All phases listed above are implemented, live-verified, and committed. Nothing is currently in progress. Remaining open items are explicitly deferred and unscheduled, not forgotten:
- Net-of-cost precision dashboard UI (backend is wired; waiting on enough real captured-premium trades to have something to show)
- Portfolio concentration guardrails (never scoped in detail)
- VCP-adjacent lower-priority research candidates already reviewed and closed out without being built

## Repository layout (top level)

```
services/
  ingestion/       Upstox market data intake
  feature-engine/  Technical/structural feature computation
  scanner/         Signal detection + strategies
  api/             FastAPI backend, routes, analytics
  archiver/        Outcome tracking
  scheduler/       Periodic jobs
  dashboard/       Frontend (Classic + New shells)
migrations/        Postgres schema migrations, applied in order
docs/              Audits, review docs, this abstract
```
