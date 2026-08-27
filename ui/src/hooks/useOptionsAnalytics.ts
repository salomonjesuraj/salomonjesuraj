import { fetchOptionSummary, fetchOptionsChainAnalytics, fetchStrategySelector } from '../lib/api'
import { DEMO_BULL_SYMBOL, isDemoMode } from '../lib/demo'
import type { OptionsChainAnalytics, OptionSummary, StrategySelectorResult } from '../types'
import { usePolling } from './usePolling'

export interface OptionsAnalyticsData {
  chainAnalytics: OptionsChainAnalytics
  strategySelector: StrategySelectorResult
  summary: OptionSummary
}

/** Options Analytics (`/analytics`) data source -- 15s poll. All three
 * calls omit `symbol` so the backend applies its own default-symbol
 * fallback (most recent active signal, else best pre-breakout
 * candidate) -- same convention every other options route in this
 * codebase already uses, so this page always reflects whatever's most
 * relevant right now rather than needing its own symbol picker wired
 * up first. fetchOptionSummary keys its demo-mode mock by symbol (see
 * lib/demo.ts's DEMO_OPTIONS), so demo mode here is pointed at the same
 * DEMO_BULL_SYMBOL Sniper HUD's own demo cards use, rather than an
 * empty string that mock map doesn't have an entry for. */
export function useOptionsAnalytics() {
  return usePolling<OptionsAnalyticsData>(
    async () => {
      const [chainAnalytics, strategySelector, summary] = await Promise.all([
        fetchOptionsChainAnalytics(),
        fetchStrategySelector(),
        fetchOptionSummary(isDemoMode() ? DEMO_BULL_SYMBOL : ''),
      ])
      return { chainAnalytics, strategySelector, summary }
    },
    15000,
    [],
  )
}
