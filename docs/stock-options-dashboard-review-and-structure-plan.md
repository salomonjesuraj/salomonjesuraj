# Infusion Stock-Options Dashboard Review and Structure Plan

Date: 2026-08-18

Scope: Review the attached breakout/volume audit, the live repository dashboard/API/scanner architecture, and current market practice for stock-options scanning. The attached document was used as context only; its instructions were not treated as user instructions.

## Executive View

The dashboard is trying to solve two different jobs with one visual language:

1. Find stocks that are breaking out or about to break out.
2. Decide whether a stock option contract is executable.

Right now the screen is biased toward job 2. That makes sense for risk control, but it is the wrong first layer if the goal is to catch stock breakouts quickly. A stock-options dashboard should first rank the underlying stock movement, then open the option-chain evidence only for shortlisted names.

The dashboard should therefore be restructured into a stock-first workflow:

1. Market and sector context.
2. Live stock breakout tape.
3. Volume surge and price-location evidence.
4. Selected-stock detail.
5. Option contract confirmation.
6. Alerts and paper/live execution gates.

This preserves option discipline without letting option-chain readiness hide fresh stock opportunities.

## Main Findings

### 1. The current system is options-first, not stock-breakout-first

Evidence:

- `services/dashboard/public/js/scanner.js:19` defines the main table, but its default visible columns include `Conviction`, `Bias`, `Chain`, and `F&O` before relative volume.
- `services/dashboard/public/js/scanner.js:45` makes `RVol` hidden by default.
- `services/dashboard/public/js/scanner-v2.js:56` has a similar table, and `scanner-v2.js:62` uses `option_readiness` as `Conviction`.
- `services/api/src/api/routes/ticks.py:1274-1291` overwrites the stock-derived `option_readiness` with option-chain execution score when option-chain cache is available.

Consequence: a user sees contract readiness and CE/PE language before the screen has made the stock breakout obvious.

### 2. The best breakout signal, relative volume, is under-surfaced

Evidence:

- `services/feature-engine/src/feature_engine/features/volume.py:21-31` computes relative volume against a same-minute 20-session volume profile, which is a strong design.
- `services/api/src/api/routes/ticks.py:356`, `423`, `436`, and `848` use/pass `rel_vol`.
- But `services/dashboard/public/js/scanner.js:45` hides `RVol` by default.

Consequence: the dashboard can have the data needed for volume-led detection, but the user has to dig for it.

### 3. Published signals are structurally late for fresh breakouts

Evidence:

- `services/scanner/src/scanner/engine.py:113-119` evaluates strategy logic only on closed 1-minute bars and only after indicators are ready.
- `services/feature-engine/src/feature_engine/engine.py:407` requires completed 1-minute bars >= `max(bb_period, macd_slow)`.
- `services/scanner/src/scanner/config.py:26` sets minimum conviction score to 80.
- `services/scanner/src/scanner/config.py:40` excludes the opening session from precision-guard sessions.
- `services/scanner/src/scanner/suppression.py:218-223` suppresses precision-guarded strategies outside the configured sessions.

Consequence: alerts may remain strict, but the dashboard should not depend on alert publication to show early stock opportunity.

### 4. The Volume-VWAP breakout strategy is too narrow for stock options

Evidence:

- `services/scanner/src/scanner/strategies/vol_vwap_breakout.py:73-82` requires a VWAP reclaim/crossover.
- `services/scanner/src/scanner/strategies/vol_vwap_breakout.py:136-148` confirms the strategy is intentionally crossover-adjacent.

Consequence: a stock already above VWAP that accelerates on volume can be missed by the strategy even if it is exactly the kind of stock-options opportunity the user wants.

### 5. The hybrid strategy treats volume as one supporting gate

Evidence:

- `services/scanner/src/scanner/strategies/options_first_hybrid.py:79` creates `volume_ok`.
- `options_first_hybrid.py:95` and `105` combine volume with squeeze/compression as only one gate.
- `options_first_hybrid.py:116-117` requires enough core gates.
- `options_first_hybrid.py:450` gives volume a maximum score of 14.

Consequence: fresh high-volume moves can be ranked below slower, cleaner, already-confirmed option setups.

### 6. The universe is partly stock-options aware, but the UI language is still index/options mixed

Evidence:

- `services/nse-scraper/src/nse_scraper/loader.py:71-75` keeps the `fno` universe stocks-only.
- `loader.py:257` says instrument keys returned for scanner are EQ segment keys, not index keys.
- `services/nse-scraper/src/nse_scraper/config.py:11-12` supports `fno`, `nifty500`, etc.

Consequence: the data layer can support stock-option underlyings, but the dashboard still needs clearer separation between stock candidates and index context.

## External Market Practice Notes

The dashboard should borrow three global patterns:

1. Use the underlying stock as the primary scanner key, then inspect option contract quality after selection.
2. Show unusual activity as a ratio, not just raw volume.
3. Separate liquidity/flow metrics from directional conviction.

References used:

- NSE states that equity derivatives include options on individual securities (`OPTSTK`) as distinct from index options (`OPTIDX`): https://www.nseindia.com/static/products-services/equity-derivatives-contract-specifications
- NSE describes its derivatives offering as futures/options on 6 major indices and more than 200 securities: https://www.nseindia.com/static/products-services/about-equity-derivatives
- Cboe’s open-close volume dataset highlights intraday option-flow categorization by participant/action/position, showing how mature options tooling separates flow analysis from simple price signals: https://datashop.cboe.com/cboe-options-open-close-volume-summary
- Options volume and open interest are commonly used together to judge liquidity and participation: https://www.investopedia.com/trading/options-trading-volume-and-open-interest/
- Cboe reported that single-stock option flow has grown strongly, reinforcing the need for stock-specific option workflows rather than index-first design: https://www.cboe.com/insights/posts/the-state-of-the-options-industry-quarter-three-2025/

## Recommended New Dashboard Structure

### A. Top Band: Market Context, Not Trade Direction

Purpose: answer whether the market is helping or fighting stock breakouts.

Show:

- NIFTY, BANKNIFTY, GIFT NIFTY change.
- Market breadth across tracked F&O stocks.
- Sector leaders and laggards.
- Opening/midday/closing session label.
- System readiness: ticks live, features live, volume-profile coverage.

Do not make this a trade signal. It is context only.

### B. Primary Panel: Stock Breakout Radar

This should be the first and largest panel.

Default columns:

- Symbol
- Sector
- LTP
- Change %
- Relative Volume
- Volume Profile Ready
- Breakout Type
- Price Location
- Day High / Day Low Distance
- VWAP State
- 1M / 5M / 15M Alignment
- Freshness
- Stock Breakout Score
- Alert Tier

Breakout types:

- Volume Surge
- Day High Break
- VWAP Reclaim
- Above VWAP Continuation
- Opening Range Break
- Sector Leader
- Relative Strength vs Index
- Failed Breakout / No Chase

Important change: `RVol` must be visible by default, ideally immediately after `Change %`.

### C. Secondary Panel: Opportunity Queue

Purpose: split early stock detection from trade execution.

Tiers:

- `EARLY WATCH`: RVol spike, price moving, but confirmation incomplete.
- `BREAKOUT NOW`: stock has broken key level with volume.
- `RETEST ENTRY`: breakout happened, waiting for safer pullback/reclaim.
- `OPTION READY`: stock signal plus contract quality is acceptable.
- `NO CHASE`: move too extended or option contract too poor.

This panel should include suppressed/early candidates. It should not wait for Telegram-grade signal publication.

### D. Selected Stock Detail

When the user clicks a stock, show:

- 1-minute and 5-minute stock chart.
- RVol timeline.
- Day high/low, previous close, VWAP, opening range.
- Sector rank and index-relative strength.
- Why it is ranked.
- What invalidates it.
- What must happen next.

This is the core decision view for stock-options trading.

### E. Option Contract Confirmation

Only after a stock is selected, show:

- Suggested CE/PE contract.
- Bid/ask spread.
- Volume.
- Open interest.
- OI change.
- IV and IV rank, if available.
- Delta.
- Expiry days.
- Physical settlement/event risk.
- Contract status: `TRADE_READY`, `WAIT_CONTRACT`, `AVOID_CONTRACT`.

Rename `Conviction` into two fields:

- `Stock Score`
- `Contract Score`

This removes the current confusion where `option_readiness` sometimes means underlying proxy and sometimes means option-chain execution score.

### F. Alerts

Use two alert layers:

- Dashboard-only early alerts: loose, stock-first, visible on screen.
- Telegram/live trade alerts: strict, option-confirmed, precision guarded.

This lets the dashboard catch fast movers without degrading the serious alert channel.

## Backend/API Changes Needed

### 1. Add stock-first fields to `/api/ticks`

Recommended fields:

- `stock_breakout_score`
- `stock_breakout_tier`
- `breakout_type`
- `breakout_freshness_sec`
- `rvol_rank`
- `rvol_zscore`
- `volume_profile_ready`
- `opening_range_high`
- `opening_range_low`
- `opening_range_state`
- `day_high_distance_pct`
- `day_low_distance_pct`
- `relative_strength_vs_nifty`
- `relative_strength_vs_sector`
- `stock_no_chase_reason`

### 2. Preserve separate option fields

Recommended fields:

- `contract_score`
- `contract_status`
- `contract_blockers`
- `contract_spread_pct`
- `contract_oi`
- `contract_volume`
- `contract_iv`
- `contract_delta`

Avoid overwriting `option_readiness` with chain score. Keep backward compatibility temporarily, but introduce explicit names.

### 3. Add an early-watch API

Endpoint:

- `/api/stock-breakouts`

Purpose:

- Return top stock candidates, including those that are not published signals.
- Include early, active, retest, and no-chase states.

This can be derived initially from hot Redis features, without changing the strict scanner alert path.

### 4. Make volume-profile readiness visible

The feature engine already emits `volume_profile_ready` at `feature_engine/engine.py:405`. It should be passed through and shown in the dashboard. A stock with no volume profile should read `VOL BASELINE MISSING`, not `0.0x`.

## Frontend Changes Needed

### Phase 1: Fast UI Reorientation

- Rename main tab from options-first wording to `Stock Breakout Radar`.
- Move `RVol` into the default visible column set.
- Rename `Conviction` to `Stock Score` where it is stock-derived.
- Add separate `Contract` column instead of blending it into conviction.
- Hide `Chain`, `F&O`, and option-specific fields behind selected-stock detail unless the row is in `OPTION READY`.

### Phase 2: New Stock Breakout Radar Panel

- Add a new stock-first table using `/api/stock-breakouts` or enriched `/api/ticks`.
- Default sort: `stock_breakout_score`, then `rvol_rank`, then `freshness`.
- Add filters: `RVol >= 2`, `near day high`, `above VWAP`, `sector top 3`, `opening range break`, `no chase excluded`.

### Phase 3: Selected Stock Workflow

- Replace the current selected trade plan emphasis with:
  - Stock move evidence.
  - Trigger/invalidation.
  - Retest/entry zone.
  - Contract confirmation.

### Phase 4: Alert Separation

- Add dashboard-only early alerts for stock movement.
- Keep Telegram alerts strict.
- Track early-alert outcomes separately so the team can backtest whether opening-session stock breakouts have edge without polluting live precision statistics.

## Suggested Scoring Model

Stock Breakout Score, 100 points:

- Relative volume: 25
- Price location: 20
- Fresh breakout/fresh high: 15
- VWAP/EMA acceptance: 15
- Sector and index relative strength: 10
- Multi-timeframe alignment: 10
- No-chase quality: 5

Contract Score, 100 points:

- Spread/liquidity: 25
- OI/volume participation: 20
- IV/IV rank suitability: 15
- Delta/strike quality: 15
- Expiry suitability: 10
- Event/physical-settlement risk: 10
- Cost/slippage realism: 5

Decision rule:

- Show stock candidates when `Stock Breakout Score >= 55` or `RVol >= 2.0`.
- Mark `BREAKOUT NOW` around `Stock Breakout Score >= 70`.
- Mark `OPTION READY` only when stock and contract scores both pass.

## Implementation Order

1. Expose explicit stock-vs-contract fields in `/api/ticks`.
2. Make `RVol` and volume-profile readiness visible by default.
3. Add `stock_breakout_score` and `stock_breakout_tier`.
4. Add `/api/stock-breakouts` if the enriched tick route becomes too crowded.
5. Restructure the main dashboard layout around stock candidates.
6. Move option-chain details into selected-stock detail.
7. Add dashboard-only early alerts.
8. Backtest early-alert outcomes separately.

## Bottom Line

Do not remove stock-options logic. Reorder it.

The correct mental model is:

Stock first. Option contract second. Live alert last.

The dashboard should detect the stock move immediately, explain why it matters, and only then decide whether the option contract is good enough to trade.
