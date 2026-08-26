import type { OptionSummary, SignalRow, TradeBlueprint } from '../types'

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`)
  return (await res.json()) as T
}

export async function fetchSignals(): Promise<SignalRow[]> {
  const body = await getJson<{ count: number; signals: SignalRow[] }>('/api/signals')
  return body.signals || []
}

export async function fetchTradeBlueprint(symbol: string): Promise<TradeBlueprint> {
  return getJson<TradeBlueprint>(`/api/trade-blueprint/${encodeURIComponent(symbol)}`)
}

export async function fetchOptionSummary(symbol: string): Promise<OptionSummary> {
  return getJson<OptionSummary>(`/api/options/summary?symbol=${encodeURIComponent(symbol)}`)
}
