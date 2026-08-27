import { useEffect, useState } from 'react'
import { fetchDepth } from '../lib/api'
import type { DepthLevel } from '../types'

export interface OrderBookDepthResult {
  levels: DepthLevel[]
  available: boolean
  reason: string | null
}

// 2s -- matches this app's own established precedent for "near-real-
// time via REST poll, honestly not push-driven" (IndexPulseHeader's own
// note: "indistinguishable from tick-driven at a glance, honest about
// not being one"). A genuine tick-by-tick WS depth channel would need
// ws-gateway/normalizer changes beyond this sprint's scope; feature-
// engine's own infusion:depth:{symbol} key already refreshes every real
// tick underneath this poll; a 10s TTL means anything slower than that
// reads as honestly stale, not silently frozen.
const POLL_MS = 2000

/** Same "reset on symbol change" shape as useHistoricalData, for the
 * same reason: showing one symbol's real depth ladder mislabeled under
 * a different symbol while a new fetch is in flight would be actively
 * misleading, not just stale. */
export function useOrderBookDepth(symbol: string | null): OrderBookDepthResult {
  const [prevSymbol, setPrevSymbol] = useState(symbol)
  const [levels, setLevels] = useState<DepthLevel[]>([])
  const [available, setAvailable] = useState(false)
  const [reason, setReason] = useState<string | null>(null)

  if (symbol !== prevSymbol) {
    setPrevSymbol(symbol)
    setLevels([])
    setAvailable(false)
    setReason(null)
  }

  useEffect(() => {
    if (!symbol) return
    let cancelled = false

    const tick = async () => {
      try {
        const result = await fetchDepth(symbol)
        if (cancelled) return
        setAvailable(result.available)
        setLevels(result.available ? result.levels || [] : [])
        setReason(result.available ? null : result.reason || 'Depth unavailable.')
      } catch (err) {
        if (!cancelled) {
          setAvailable(false)
          setLevels([])
          setReason(err instanceof Error ? err.message : String(err))
        }
      }
    }

    void tick()
    const timer = window.setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [symbol])

  return { levels, available, reason }
}
