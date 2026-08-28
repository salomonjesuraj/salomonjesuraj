import { demoBlueprint, demoOptionSummary, DEMO_SIGNALS, isDemoMode } from './demo'
import type {
  AlertLogEntry,
  BacktestSummary,
  BrokerHoldingsResponse,
  BrokerOrdersResponse,
  BrokerPositionsResponse,
  ChartBar,
  DepthResponse,
  ExecutionTicket,
  HealthStatus,
  IndexTick,
  IntradayChartResponse,
  JournalExpectancy,
  JournalStats,
  JournalTrade,
  MarketBreadth,
  OiBuildupMap,
  OptimizerProposal,
  OptionChainResponse,
  OptionsChainAnalytics,
  OptionSummary,
  PrebreakoutRow,
  RiskSettings,
  SafetyStatus,
  ScreenerOptionsSummaryMap,
  ScreenerStructureMap,
  SignalRow,
  SmcGeometry,
  StagedTicketsResponse,
  StrategySelectorResult,
  SuppressedSignalRow,
  SymbolMeta,
  TickRow,
  TradeBlueprint,
  UpstoxAuthStatus,
  UpstoxTokenSaveResult,
  WalkforwardResult,
} from '../types'

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`)
  return (await res.json()) as T
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`)
  return (await res.json()) as T
}

export async function fetchSignals(): Promise<SignalRow[]> {
  if (isDemoMode()) return DEMO_SIGNALS
  const body = await getJson<{ count: number; signals: SignalRow[] }>('/api/signals')
  return body.signals || []
}

export async function fetchTradeBlueprint(symbol: string): Promise<TradeBlueprint> {
  if (isDemoMode()) return demoBlueprint(symbol)
  return getJson<TradeBlueprint>(`/api/trade-blueprint/${encodeURIComponent(symbol)}`)
}

export async function fetchOptionSummary(symbol: string): Promise<OptionSummary> {
  if (isDemoMode()) return demoOptionSummary(symbol)
  return getJson<OptionSummary>(`/api/options/summary?symbol=${encodeURIComponent(symbol)}`)
}

// ── 4-Zone Trading Command Screen ────────────────────────────────────

export async function fetchIndices(): Promise<IndexTick[]> {
  const body = await getJson<{ count: number; indices: IndexTick[] }>('/api/market/indices')
  return body.indices || []
}

export async function fetchBreadth(): Promise<MarketBreadth> {
  const body = await getJson<{
    available: boolean
    components?: { advance_decline?: { advancing?: number; declining?: number } }
  }>('/api/market/breadth-health')
  return {
    available: body.available,
    advancing: body.components?.advance_decline?.advancing,
    declining: body.components?.advance_decline?.declining,
  }
}

export async function fetchSuppressedSignals(): Promise<SuppressedSignalRow[]> {
  // Demo mode's two mock signals come through fetchSignals() (active)
  // only -- returning real suppressed data alongside them here would
  // mix real symbols into an otherwise fully-simulated screen.
  if (isDemoMode()) return []
  // 200 (the route's own max) rather than 50 -- Zone 2's probabilistic
  // display now needs to find every candidate scoring >= 65 in the
  // recent window, not just the single closest near-miss the old
  // RadarScanningStrip needed.
  const body = await getJson<{ count: number; suppressed: SuppressedSignalRow[] }>(
    '/api/signals/suppressed?limit=200',
  )
  return body.suppressed || []
}

export async function fetchAllTicks(): Promise<TickRow[]> {
  const body = await getJson<{ count: number; ticks: TickRow[] }>('/api/ticks')
  return body.ticks || []
}

export async function fetchPrebreakout(): Promise<PrebreakoutRow[]> {
  const body = await getJson<{ count: number; watchlist: PrebreakoutRow[] }>('/api/prebreakout')
  return body.watchlist || []
}

export async function fetchOiBuildupMap(): Promise<OiBuildupMap> {
  const body = await getJson<{ count: number; oi_buildup: OiBuildupMap }>(
    '/api/futures/oi-buildup-map',
  )
  return body.oi_buildup || {}
}

// ── Command Center data-wiring sprint (2026-08-27) ───────────────────
// All four hit real, already-shipped backend routes -- no new API
// surface was added for this. Each honestly reports `available`/`ready`
// false (never a fabricated zero) when its own data source -- Postgres,
// the Upstox option chain, a sweep loop -- isn't there yet.

// Demo mode has no Upstox option chain to point these two at (they hit
// the real broker chain, unlike fetchOptionSummary's own symbol-keyed
// mock map) -- same "never mix real backend reads into the simulated
// screen" rule fetchSuppressedSignals follows, honestly reported the
// same way the real backend itself reports a thin/unavailable chain.
const DEMO_UNAVAILABLE = { ready: false, reason: 'Not available in demo mode.' } as const

/** GET /api/options/chain-analytics -- omit `symbol` to let the backend
 * pick its own default (most recent active signal, else best pre-
 * breakout candidate), same fallback every options route already uses. */
export async function fetchOptionsChainAnalytics(symbol?: string): Promise<OptionsChainAnalytics> {
  if (isDemoMode()) return { ...DEMO_UNAVAILABLE, pcr: null, oi_support_resistance: null, max_pain: null }
  const qs = symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''
  return getJson<OptionsChainAnalytics>(`/api/options/chain-analytics${qs}`)
}

export async function fetchStrategySelector(symbol?: string): Promise<StrategySelectorResult> {
  if (isDemoMode()) return { ...DEMO_UNAVAILABLE }
  const qs = symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''
  return getJson<StrategySelectorResult>(`/api/options/strategy-selector${qs}`)
}

// ── "Unified Screener & Deep-Dive Interactivity" sprint (2026-08-28) ──

/** GET /api/symbols -- the real F&O universe this pipeline actually
 * tracks (infusion:symbols in Redis), used for the searchable symbol
 * selector and the F&O Screener alike, not a second hand-maintained
 * list. */
export async function fetchSymbols(): Promise<SymbolMeta[]> {
  const body = await getJson<{ count: number; symbols: SymbolMeta[] }>('/api/symbols')
  return body.symbols || []
}

/** GET /api/options/chain -- full per-strike chain (real strikes, PCR,
 * Max Pain, and Greeks straight off Upstox's own real payload). Reuses
 * the exact same full-chain fetch fetchOptionsChainAnalytics's summary
 * numbers already come from -- see api/routes/market.py's own
 * options_chain() docstring. */
export async function fetchOptionChain(symbol?: string): Promise<OptionChainResponse> {
  if (isDemoMode()) {
    return { ready: false, reason: 'Not available in demo mode.', pcr: null, max_pain: null, oi_support_resistance: null }
  }
  const qs = symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''
  return getJson<OptionChainResponse>(`/api/options/chain${qs}`)
}

/** GET /api/screener/structure -- real bulk Order Block/FVG proximity
 * for the whole F&O universe in one Redis round-trip. */
export async function fetchScreenerStructure(): Promise<ScreenerStructureMap> {
  if (isDemoMode()) return {}
  const body = await getJson<{ count: number; structure: ScreenerStructureMap }>(
    '/api/screener/structure',
  )
  return body.structure || {}
}

/** GET /api/screener/options-summary -- real PCR/Max Pain for whichever
 * symbols the background option-chain queue has actually refreshed
 * recently, never a live per-request fetch for the whole universe --
 * see api/routes/screener.py's own module docstring for why. */
export async function fetchScreenerOptionsSummary(): Promise<ScreenerOptionsSummaryMap> {
  if (isDemoMode()) return {}
  const body = await getJson<{ count: number; summary: ScreenerOptionsSummaryMap }>(
    '/api/screener/options-summary',
  )
  return body.summary || {}
}

/** GET /api/backtest/summary -- server-cached 90s, so client polling
 * faster than that just re-reads the same cached row. */
export async function fetchBacktestSummary(days = 60): Promise<BacktestSummary> {
  return getJson<BacktestSummary>(`/api/backtest/summary?days=${days}`)
}

/** GET /api/backtest/optimizer-proposal/latest -- reads the last written
 * proposal, does NOT trigger a fresh walk-forward sweep (that's a
 * separate, expensive endpoint this page deliberately never calls). */
export async function fetchOptimizerProposal(): Promise<OptimizerProposal> {
  return getJson<OptimizerProposal>('/api/backtest/optimizer-proposal/latest')
}

/** GET /api/backtest/walkforward -- a real ~1,575-profile in-memory grid
 * search, NOT cached server-side (unlike /api/backtest/summary). Call
 * this sparingly -- see useLabScatter's own note on why it's fetched
 * once per page visit rather than on the Lab's shared 30s poll. days=60
 * matches the same window /api/backtest/summary already uses elsewhere
 * on this page, smaller than the route's own 120-day default, to keep
 * the row count (and therefore the grid search's real cost) down. */
export async function fetchWalkforward(days = 60): Promise<WalkforwardResult> {
  return getJson<WalkforwardResult>(`/api/backtest/walkforward?days=${days}`)
}

export async function fetchJournalTrades(limit = 40): Promise<JournalTrade[]> {
  const body = await getJson<{ ok: boolean; count: number; trades: JournalTrade[] }>(
    `/api/journal/trades?limit=${limit}`,
  )
  return body.trades || []
}

export async function fetchJournalStats(): Promise<JournalStats | null> {
  const body = await getJson<{ ok: boolean; stats: JournalStats }>('/api/journal/stats')
  return body.stats ?? null
}

export async function fetchJournalExpectancy(): Promise<JournalExpectancy> {
  return getJson<JournalExpectancy>('/api/journal/expectancy')
}

export async function fetchStagedTickets(): Promise<StagedTicketsResponse> {
  return getJson<StagedTicketsResponse>('/api/execution/staged?limit=40')
}

export async function fetchSafetyStatus(): Promise<SafetyStatus> {
  return getJson<SafetyStatus>('/api/safety/status')
}

export async function fetchHealth(): Promise<HealthStatus> {
  return getJson<HealthStatus>('/api/health')
}

export async function fetchAlertLog(): Promise<AlertLogEntry[]> {
  const body = await getJson<{ count: number; log: AlertLogEntry[] }>('/api/alerts/log')
  return body.log || []
}

// ── Sniper HUD candlestick chart (2026-08-27 charting sprint) ────────
// /api/chart/{symbol}/intraday already existed, real and shipped
// (api/routes/charts.py) -- 1-min OHLC bars from feature-engine's own
// bar_builder, keyed off infusion:ohlc:{symbol}:1m. No new backend
// route was needed for this sprint.
//
// "Unified Screener & Deep-Dive Interactivity" sprint (2026-08-28):
// the route already aggregates real 1m bars into 5m/15m/1h/4h
// (charts.py's own `_aggregate()`) -- it was just never given an
// `interval` param by any caller until now. Real aggregation of real
// bars, not a second data source.
export type ChartInterval = '1m' | '5m' | '15m' | '1h' | '4h'

export async function fetchIntradayChart(
  symbol: string,
  interval: ChartInterval = '1m',
): Promise<ChartBar[]> {
  const body = await getJson<IntradayChartResponse>(
    `/api/chart/${encodeURIComponent(symbol)}/intraday?interval=${interval}`,
  )
  if (body.error) throw new Error(body.error)
  return body.bars || []
}

// "Institutional Chart Overlay" sprint (2026-08-28) -- see
// SmcGeometry's own type comment for what this really is (a batch
// replay of feature-engine's real structure/ICT rules, not a new
// algorithm and not the live per-symbol hot-state hash).
export async function fetchSmcGeometry(symbol: string): Promise<SmcGeometry> {
  if (isDemoMode()) return { symbol, ready: false, reason: 'Demo mode has no real bar history.' }
  return getJson<SmcGeometry>(`/api/chart/smc?symbol=${encodeURIComponent(symbol)}`)
}

// ── Level 2 DOM ladder (2026-08-27 "Terminal Edge" sprint) ────────────
// GET /api/market/depth/{symbol} is new this sprint -- feature-engine's
// own raw 5-level order-book capture (upstox_codec.py's real MarketLevel
// depth-codec) wasn't previously exposed past its derived aggregate
// (book_imbalance/depth_concentration); it now also gets a dedicated
// Redis key + this route. See services/api/src/api/routes/depth.py.

export async function fetchDepth(symbol: string): Promise<DepthResponse> {
  return getJson<DepthResponse>(`/api/market/depth/${encodeURIComponent(symbol)}`)
}

// ── 1-Click paper execution (2026-08-27 "Terminal Edge" sprint) ──────
// Deliberately wired to the REAL, already-shipped, paper-only
// api/routes/execution.py -- NOT a new live-order-placing endpoint. See
// useExecution.ts's own note for why: this backend's execution
// architecture is paper-first end to end (execution.py's own module
// docstring, safety.py's paper_first gate, journal.py's "intentionally
// paper-only"), and building a real Upstox order-placement path is a
// financial-trade-execution capability this session does not add.

export async function fetchRiskSettings(): Promise<RiskSettings> {
  const body = await getJson<{ ok: boolean; settings: RiskSettings }>('/api/risk/settings')
  return body.settings
}

export async function stageExecutionTicket(trade: Record<string, unknown>): Promise<ExecutionTicket> {
  const body = await postJson<{ ok: boolean; ticket: ExecutionTicket }>(
    '/api/execution/stage',
    { trade },
  )
  return body.ticket
}

/** Logs a staged paper ticket into the real journal (/journal, The
 * Ledger) so Phase 3's chart overlay has a real active position to
 * draw -- the same POST /api/journal/trades the dashboard's own manual
 * journal entry already uses, not a new endpoint. Best-effort: a
 * failure here doesn't undo the staged ticket the trader already saw,
 * so callers should catch and ignore, not surface this as the
 * execution having failed. */
export async function logJournalTrade(payload: Record<string, unknown>): Promise<void> {
  await postJson('/api/journal/trades', payload)
}

// ── "Broker Sync & Active Position Intelligence" master sprint
// (2026-08-27) -- all three are real GETs against api/routes/broker.py.
// STRICT READ-ONLY: there is no order-placement function anywhere in
// this file, and none is planned.

export async function fetchBrokerPositions(): Promise<BrokerPositionsResponse> {
  return getJson<BrokerPositionsResponse>('/api/broker/positions')
}

export async function fetchBrokerHoldings(): Promise<BrokerHoldingsResponse> {
  return getJson<BrokerHoldingsResponse>('/api/broker/holdings')
}

export async function fetchBrokerOrders(): Promise<BrokerOrdersResponse> {
  return getJson<BrokerOrdersResponse>('/api/broker/orders')
}

// ── "Telegram Redesign & Token Modal" sprint (2026-08-27) -- the real,
// already-shipped api/routes/auth.py token-recovery pair. See
// UpstoxAuthStatus's own type comment for why the modal watches this
// purpose-built status endpoint rather than sniffing 401s off every
// broker fetch above.

export async function fetchUpstoxAuthStatus(): Promise<UpstoxAuthStatus> {
  return getJson<UpstoxAuthStatus>('/api/auth/upstox/status')
}

/** Deliberately NOT built on postJson: that helper throws away the
 * response body on a non-2xx status, and the real failure text this
 * route returns on its 400 (e.g. "Invalid Upstox Token") is exactly
 * what the modal needs to show the trader -- so this reads the JSON
 * body first and only then decides what to return, regardless of
 * status code. */
export async function saveUpstoxToken(accessToken: string): Promise<UpstoxTokenSaveResult> {
  const res = await fetch('/api/auth/upstox/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ access_token: accessToken }),
  })
  const body = (await res.json().catch(() => null)) as UpstoxTokenSaveResult | null
  if (body) return body
  return { ok: false, status: 'error', message: `Unexpected response (HTTP ${res.status}).` }
}
