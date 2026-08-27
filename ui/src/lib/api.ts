import { demoBlueprint, demoOptionSummary, DEMO_SIGNALS, isDemoMode } from './demo'
import type {
  AlertLogEntry,
  BacktestSummary,
  HealthStatus,
  IndexTick,
  JournalExpectancy,
  JournalStats,
  JournalTrade,
  MarketBreadth,
  OiBuildupMap,
  OptimizerProposal,
  OptionsChainAnalytics,
  OptionSummary,
  PrebreakoutRow,
  SafetyStatus,
  SignalRow,
  StagedTicketsResponse,
  StrategySelectorResult,
  SuppressedSignalRow,
  TickRow,
  TradeBlueprint,
  WalkforwardResult,
} from '../types'

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
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
