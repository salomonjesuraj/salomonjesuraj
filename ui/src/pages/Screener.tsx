import { ArrowDown, ArrowUp, Filter, Radar } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { fetchAllTicks, fetchOiBuildupMap, fetchPrebreakout, fetchSymbols } from '../lib/api'
import { BULL_OI, OI_LABEL } from '../lib/oiBuildup'
import { usePolling } from '../hooks/usePolling'
import type { OIBuildupType, PrebreakoutRow, SymbolMeta, TickRow } from '../types'

const DASH = '—'

const SQUEEZE_STATE_LABEL: Record<string, string> = {
  coiled: 'SQUEEZE',
  accumulating: 'ACCUMULATION',
  compressing: 'COILING',
  triggered: 'TRIGGERED',
}

type SortKey = 'symbol' | 'rvol' | 'readiness'
type QuickFilter = 'high_rvol' | 'squeeze' | 'bullish_buildup'

interface ScreenerRow {
  symbol: string
  sector: string
  buildup: OIBuildupType | null
  rvol: number | null
  squeezeState: string | null
  readiness: number | null
}

function fmt(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined || Number.isNaN(v) ? DASH : v.toFixed(digits)
}

/** `/screener` -- Unified F&O Screener, "Unified Screener & Deep-Dive
 * Interactivity" sprint (2026-08-28). Every column reuses a real,
 * already-shipped bulk endpoint, joined client-side by symbol -- the
 * exact same "fetch a couple of cheap bulk routes and join by symbol"
 * shape PreBreakoutWatchlist.tsx and SmartMoneyRadar.tsx already use,
 * not a new backend aggregation endpoint:
 *   - Symbol & Sector: GET /api/symbols (the real infusion:symbols
 *     universe this pipeline tracks, 200+ real F&O names)
 *   - Smart Money Flow: GET /api/futures/oi-buildup-map
 *   - Relative Volume: GET /api/ticks' own rel_vol
 *   - Squeeze Readiness: GET /api/prebreakout's own state + readiness_score
 *
 * Deliberately honest gap, disclosed rather than papered over: PCR
 * Sentiment and IV Rank are NOT columns here. Real PCR/IV-Rank needs a
 * live per-symbol Upstox option-chain fetch (api/routes/market.py's own
 * _fetch_full_option_chain) -- there is no cheap bulk source for it,
 * and this codebase's own architecture deliberately avoids fetching the
 * full chain for the whole universe at once (option_chain_queue.py only
 * ever refreshes a small rotating "candidate" subset, precisely to
 * avoid hammering the real broker API). Showing a column of 200+ mostly-
 * stale-or-fabricated PCR numbers would be worse than not showing one;
 * clicking any row instead deep-links to Options Analytics, which
 * fetches that ONE symbol's real chain on demand. */
export function Screener() {
  const { data: symbols } = usePolling(fetchSymbols, 60000, [])
  const { data: ticks } = usePolling(fetchAllTicks, 5000, [])
  const { data: buildupMap } = usePolling(fetchOiBuildupMap, 5000, [])
  const { data: prebreakout } = usePolling(fetchPrebreakout, 5000, [])
  const navigate = useNavigate()

  const [sortKey, setSortKey] = useState<SortKey>('rvol')
  const [sortDesc, setSortDesc] = useState(true)
  const [activeFilters, setActiveFilters] = useState<Set<QuickFilter>>(new Set())

  const rows = useMemo<ScreenerRow[]>(() => {
    const tickBySymbol = new Map<string, TickRow>((ticks ?? []).map((t) => [t.symbol, t]))
    const prebreakBySymbol = new Map<string, PrebreakoutRow>(
      (prebreakout ?? []).map((p) => [p.symbol, p]),
    )
    const buildup = buildupMap ?? {}

    return (symbols ?? []).map((s: SymbolMeta) => {
      const tick = tickBySymbol.get(s.symbol)
      const pb = prebreakBySymbol.get(s.symbol)
      return {
        symbol: s.symbol,
        sector: s.sector_id || DASH,
        buildup: buildup[s.symbol] ?? null,
        rvol: tick?.rel_vol ?? null,
        squeezeState: pb?.state ?? null,
        readiness: pb?.readiness_score ?? null,
      }
    })
  }, [symbols, ticks, buildupMap, prebreakout])

  const filtered = useMemo(() => {
    let out = rows
    if (activeFilters.has('high_rvol')) out = out.filter((r) => (r.rvol ?? 0) > 3)
    if (activeFilters.has('squeeze')) out = out.filter((r) => r.squeezeState === 'coiled')
    if (activeFilters.has('bullish_buildup')) {
      out = out.filter((r) => r.buildup !== null && BULL_OI.includes(r.buildup))
    }

    const sorted = [...out].sort((a, b) => {
      if (sortKey === 'symbol') return a.symbol.localeCompare(b.symbol)
      if (sortKey === 'rvol') return (a.rvol ?? -1) - (b.rvol ?? -1)
      return (a.readiness ?? -1) - (b.readiness ?? -1)
    })
    return sortDesc ? sorted.reverse() : sorted
  }, [rows, activeFilters, sortKey, sortDesc])

  const toggleFilter = (f: QuickFilter) => {
    setActiveFilters((prev) => {
      const next = new Set(prev)
      if (next.has(f)) next.delete(f)
      else next.add(f)
      return next
    })
  }

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) setSortDesc((d) => !d)
    else {
      setSortKey(key)
      setSortDesc(true)
    }
  }

  const SortIcon = sortDesc ? ArrowDown : ArrowUp

  const FILTER_LABEL: Record<QuickFilter, string> = {
    high_rvol: 'High RVOL (>3x)',
    squeeze: 'Squeeze Coiling',
    bullish_buildup: 'Bullish Buildup',
  }

  return (
    <div className="flex flex-1 flex-col gap-6">
      <PageHeader
        icon={Radar}
        title="F&O Screener"
        subtitle={`${rows.length} real F&O symbols tracked -- click any row for its full deep dive`}
      />

      <div className="flex flex-wrap items-center gap-2">
        <Filter className="h-3.5 w-3.5 text-hud-muted" />
        {(Object.keys(FILTER_LABEL) as QuickFilter[]).map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => toggleFilter(f)}
            className={
              'rounded-full px-3 py-1.5 text-[11px] font-bold uppercase tracking-wide ring-1 transition-colors ' +
              (activeFilters.has(f)
                ? 'bg-bull/15 text-bull ring-bull/50'
                : 'text-hud-muted ring-hud-border hover:bg-hud-panel-hover hover:text-hud-text')
            }
          >
            {FILTER_LABEL[f]}
          </button>
        ))}
        {activeFilters.size > 0 && (
          <button
            type="button"
            onClick={() => setActiveFilters(new Set())}
            className="text-[11px] text-hud-muted underline hover:text-hud-text"
          >
            Clear filters
          </button>
        )}
        <span className="ml-auto text-[11px] text-hud-muted">
          {filtered.length} of {rows.length} shown
        </span>
      </div>

      <div className="overflow-x-auto rounded-xl border border-hud-border bg-hud-panel">
        <table className="w-full min-w-[820px] text-left text-xs">
          <thead>
            <tr className="border-b border-hud-border text-[10px] uppercase tracking-wide text-hud-muted">
              <th className="cursor-pointer select-none px-3 py-2 font-bold" onClick={() => toggleSort('symbol')}>
                <span className="inline-flex items-center gap-1">
                  Symbol / Sector {sortKey === 'symbol' && <SortIcon className="h-3 w-3" />}
                </span>
              </th>
              <th className="px-3 py-2 font-bold">Smart Money Flow</th>
              <th className="cursor-pointer select-none px-3 py-2 font-bold" onClick={() => toggleSort('rvol')}>
                <span className="inline-flex items-center gap-1">
                  RVOL {sortKey === 'rvol' && <SortIcon className="h-3 w-3" />}
                </span>
              </th>
              <th
                className="cursor-pointer select-none px-3 py-2 font-bold"
                onClick={() => toggleSort('readiness')}
              >
                <span className="inline-flex items-center gap-1">
                  Squeeze Readiness {sortKey === 'readiness' && <SortIcon className="h-3 w-3" />}
                </span>
              </th>
              <th className="px-3 py-2 font-bold" title="Real per-symbol Upstox chain fetch, not a bulk-cheap number -- computed on demand when you open a symbol's deep dive.">
                PCR / IV Rank
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hud-border">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-hud-muted">
                  {rows.length === 0 ? 'Loading symbol universe…' : 'No symbols match these filters.'}
                </td>
              </tr>
            ) : (
              filtered.map((r) => (
                <tr
                  key={r.symbol}
                  onClick={() => navigate(`/analytics?symbol=${encodeURIComponent(r.symbol)}`)}
                  className="cursor-pointer transition-colors hover:bg-hud-panel-hover"
                >
                  <td className="px-3 py-2">
                    <Link
                      to={`/analytics?symbol=${encodeURIComponent(r.symbol)}`}
                      onClick={(e) => e.stopPropagation()}
                      className="font-mono font-bold text-hud-text hover:text-bull hover:underline"
                    >
                      {r.symbol}
                    </Link>
                    <div className="text-[10px] uppercase tracking-wide text-hud-muted">{r.sector}</div>
                  </td>
                  <td className="px-3 py-2">
                    {r.buildup ? (
                      <span
                        className={
                          'rounded px-1.5 py-0.5 text-[10px] font-bold ' +
                          (BULL_OI.includes(r.buildup)
                            ? 'bg-bull/15 text-bull'
                            : r.buildup === 'NEUTRAL'
                              ? 'bg-hud-muted/15 text-hud-muted'
                              : 'bg-bear/15 text-bear')
                        }
                      >
                        {OI_LABEL[r.buildup]}
                      </span>
                    ) : (
                      <span className="text-hud-muted">{DASH}</span>
                    )}
                  </td>
                  <td className="tnum px-3 py-2 font-mono text-hud-text">
                    {r.rvol !== null ? `${fmt(r.rvol)}x` : DASH}
                  </td>
                  <td className="px-3 py-2">
                    {r.squeezeState ? (
                      <span className="tnum font-mono text-hud-text">
                        {SQUEEZE_STATE_LABEL[r.squeezeState] ?? r.squeezeState.toUpperCase()}
                        {r.readiness !== null && (
                          <span className="ml-1.5 text-hud-muted">{fmt(r.readiness, 0)}</span>
                        )}
                      </span>
                    ) : (
                      <span className="text-hud-muted">{DASH}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-hud-muted">Open deep dive →</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
