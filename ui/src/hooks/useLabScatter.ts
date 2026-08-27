import { fetchWalkforward } from '../lib/api'
import { usePolling } from './usePolling'

/** The Lab's scatter-plot data source -- deliberately its OWN hook,
 * separate from useLabData's 30s poll. /api/backtest/walkforward is a
 * real, uncached, in-memory ~1,575-profile grid search over every
 * archived row in the window (no Redis cache wraps it, unlike
 * /api/backtest/summary's explicit 90s TTL) -- polling that every 30s
 * would repeat real, non-trivial backend compute for a chart that
 * doesn't need to be that fresh. 10-minute cadence here is a safety-net
 * refresh for a page left open a while, not a "keep this current"
 * interval. */
export function useLabScatter() {
  return usePolling(fetchWalkforward, 600000, [])
}
