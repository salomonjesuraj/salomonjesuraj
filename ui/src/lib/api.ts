import { demoBlueprint, demoOptionSummary, DEMO_SIGNALS, isDemoMode } from './demo'
import type {
  IndexTick,
  MarketBreadth,
  OiBuildupMap,
  OptionSummary,
  PrebreakoutRow,
  SignalRow,
  SuppressedSignalRow,
  TickRow,
  TradeBlueprint,
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
