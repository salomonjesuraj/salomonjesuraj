import {
  fetchOptionChain,
  fetchOptionSummary,
  fetchOptionsChainAnalytics,
  fetchStrategySelector,
} from '../lib/api'
import { DEMO_BULL_SYMBOL, isDemoMode } from '../lib/demo'
import type { OptionChainResponse, OptionsChainAnalytics, OptionSummary, StrategySelectorResult } from '../types'
import { usePolling } from './usePolling'

export interface OptionsAnalyticsData {
  chainAnalytics: OptionsChainAnalytics
  strategySelector: StrategySelectorResult
  summary: OptionSummary
  chain: OptionChainResponse
}

/** Options Analytics (`/analytics`) data source -- 15s poll.
 *
 * "Unified Screener & Deep-Dive Interactivity" sprint (2026-08-28):
 * now takes an optional `symbol` -- when the page has one (a trader's
 * own selection, or a `?symbol=` deep link from the Screener/Watchlist/
 * Sniper HUD), every call is pinned to it. Omitted (undefined), every
 * call still omits `symbol` too, so the backend keeps applying its own
 * original default-symbol fallback (most recent active signal, else
 * best pre-breakout candidate) exactly as before this prop existed --
 * this widening is additive, not a behavior change for a visitor who
 * hasn't picked anything yet. */
export function useOptionsAnalytics(symbol?: string) {
  return usePolling<OptionsAnalyticsData>(
    async () => {
      const [chainAnalytics, strategySelector, summary, chain] = await Promise.all([
        fetchOptionsChainAnalytics(symbol),
        fetchStrategySelector(symbol),
        fetchOptionSummary(symbol || (isDemoMode() ? DEMO_BULL_SYMBOL : '')),
        fetchOptionChain(symbol),
      ])
      return { chainAnalytics, strategySelector, summary, chain }
    },
    15000,
    [symbol],
  )
}
