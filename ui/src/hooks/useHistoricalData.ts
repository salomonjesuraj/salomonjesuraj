import { useEffect, useState } from 'react'
import { fetchIntradayChart, type ChartInterval } from '../lib/api'
import type { ChartBar } from '../types'

export interface HistoricalDataResult {
  bars: ChartBar[]
  loading: boolean
  error: Error | null
}

// 10s, not usePolling's usual per-hook cadence choice elsewhere in this
// app -- fast enough that the current, still-forming 1-min bar visibly
// updates as feature-engine's bar_builder keeps writing to
// infusion:ohlc:{symbol}:1m intrabar (this is what makes the chart feel
// "live" between minute boundaries, not just a static historical
// snapshot), without re-fetching thousands of bars every couple seconds.
const POLL_MS = 10000

/** Sniper HUD's candlestick data source. Deliberately its OWN hook shape
 * rather than a usePolling() wrapper: usePolling keeps stale data on
 * screen through a fetch error or a dependency change (a deliberate
 * feature there -- "never blanks a working card"), but that's wrong
 * here specifically -- switching `symbol` MUST clear the previous
 * symbol's bars immediately, or the chart would keep rendering one
 * symbol's real candles under a different symbol's label while the new
 * fetch is in flight, which is actively misleading, not just stale. */
export function useHistoricalData(
  symbol: string | null,
  interval: ChartInterval = '1m',
): HistoricalDataResult {
  // React's own documented pattern for resetting state when a prop
  // changes ("Adjusting state when a prop changes", react.dev): setState
  // during render, not inside an effect, so switching symbols (or,
  // "Unified Screener & Deep-Dive Interactivity" sprint, switching
  // timeframe) clears stale bars before this hook's caller ever paints
  // them under the wrong label/candle width. Every setState below the
  // effect boundary only ever runs from inside tick()'s async
  // continuation, never synchronously in the effect body, matching
  // usePolling's own established shape.
  const [prevKey, setPrevKey] = useState(`${symbol ?? ''}:${interval}`)
  const [bars, setBars] = useState<ChartBar[]>([])
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(false)

  const key = `${symbol ?? ''}:${interval}`
  if (key !== prevKey) {
    setPrevKey(key)
    setBars([])
    setError(null)
    setLoading(Boolean(symbol))
  }

  useEffect(() => {
    if (!symbol) return
    let cancelled = false

    const tick = async () => {
      try {
        const result = await fetchIntradayChart(symbol, interval)
        if (!cancelled) {
          setBars(result)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err : new Error(String(err)))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void tick()
    const timer = window.setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [symbol, interval])

  return { bars, loading, error }
}
