import type { ReactNode } from 'react'
import { fetchBreadth, fetchIndices } from '../lib/api'
import { usePolling } from '../hooks/usePolling'
import type { IndexTick } from '../types'

const DASH = '—'

function fmtPct(v: number | undefined): string {
  if (v === undefined) return DASH
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function IndexStat({ label, tick }: { label: string; tick: IndexTick | undefined }) {
  const up = (tick?.change_pct ?? 0) >= 0
  const stale = !tick || tick.error !== undefined
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[10px] font-bold uppercase tracking-wide text-hud-muted">{label}</span>
      <span className="tnum font-mono text-base font-bold text-hud-text">
        {stale ? DASH : tick.ltp?.toFixed(2)}
      </span>
      <span
        className={
          'tnum text-xs font-bold ' + (stale ? 'text-hud-muted' : up ? 'text-bull' : 'text-bear')
        }
      >
        {stale ? 'no live feed' : fmtPct(tick.change_pct)}
      </span>
    </div>
  )
}

/** Zone 1: Sticky Live Index Pulse.
 *
 * A real gap found while building this (2026-08-27, fixed the same
 * day -- see the api.futures/api.routes.market commit): the real
 * Upstox ingestion adapter never subscribes to any NSE_INDEX
 * instrument key, so there is no WebSocket tick feed for NIFTY/
 * BANKNIFTY to subscribe to -- only ingestion's mock adapter ever
 * touched those Redis keys, which is why they'd been stuck on a
 * three-week-old value. /api/market/indices' own Upstox REST fallback
 * is the actual live source (same architecture choice this codebase
 * already made for futures OI in api/futures_queue.py's own docstring:
 * a REST poll instead of a new hot-path WS subscription). Polled every
 * 2s here -- indistinguishable from tick-driven at a glance, honest
 * about not being one.
 */
export function IndexPulseHeader({ statusBadge }: { statusBadge: ReactNode }) {
  const { data: indices } = usePolling(fetchIndices, 2000, [])
  const { data: breadth } = usePolling(fetchBreadth, 10000, [])

  const nifty = indices?.find((i) => i.symbol === 'NIFTY50')
  const bankNifty = indices?.find((i) => i.symbol === 'NIFTYBANK' || i.symbol === 'BANKNIFTY')

  const advancing = breadth?.advancing
  const declining = breadth?.declining

  return (
    <header className="sticky top-0 z-20 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-hud-border bg-hud-bg/95 px-6 py-3 backdrop-blur">
      <div className="flex items-baseline gap-2">
        <h1 className="font-mono text-sm font-bold uppercase tracking-[0.2em] text-hud-text">
          Sniper HUD
        </h1>
      </div>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <IndexStat label="NIFTY" tick={nifty} />
        <IndexStat label="BANKNIFTY" tick={bankNifty} />

        <div className="flex items-baseline gap-2">
          <span className="text-[10px] font-bold uppercase tracking-wide text-hud-muted">
            Breadth
          </span>
          {advancing !== undefined && declining !== undefined ? (
            <span className="tnum font-mono text-sm font-bold">
              <span className="text-bull">▲{advancing}</span>
              <span className="mx-1 text-hud-muted">/</span>
              <span className="text-bear">▼{declining}</span>
            </span>
          ) : (
            <span className="text-sm text-hud-muted">{DASH}</span>
          )}
        </div>

        {statusBadge}
      </div>
    </header>
  )
}
