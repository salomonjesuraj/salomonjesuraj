import { ArrowDown, ArrowUp, Filter, Radar } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { fetchAllTicks, fetchScreenerFno, fetchSymbols } from '../lib/api'
import { BULL_OI, OI_LABEL } from '../lib/oiBuildup'
import { usePolling } from '../hooks/usePolling'
import type { OIBuildupType, ScreenerFnoMap, SymbolMeta, TickRow } from '../types'

const DASH = '—'

// Squeeze Readiness (api.screener_hydrator's real TTM Squeeze ratio,
// 0-100) has no separate candle-persistence STATE the way scanner's own
// pre_breakout.py state machine does -- this sprint deliberately reuses
// the single real number rather than rebuilding that state machine (see
// screener_hydrator.py's own module docstring). These are real,
// disclosed threshold buckets of that one number, the same pattern this
// file already uses for IV_RANK_AVOID/ELEVATED and the PCR>1.0/<0.7
// color bands below -- never a fabricated distinct state.
const SQUEEZE_READY_THRESHOLD = 80
const SQUEEZE_COILING_THRESHOLD = 50

function squeezeLabel(readiness: number | null): string | null {
  if (readiness === null) return null
  if (readiness >= SQUEEZE_READY_THRESHOLD) return 'SQUEEZE'
  if (readiness >= SQUEEZE_COILING_THRESHOLD) return 'COILING'
  return null
}

type SortKey = 'symbol' | 'rvol' | 'readiness' | 'pcr' | 'iv_rank'
type QuickFilter = 'high_rvol' | 'squeeze' | 'bullish_buildup' | 'high_pcr'
type MaxPainShift = 'up' | 'down' | null

interface ScreenerRow {
  symbol: string
  sector: string
  ltp: number | null
  buildup: OIBuildupType | null
  obFvgLevel: number | null
  obFvgDistancePct: number | null
  rvol: number | null
  squeezeState: string | null
  readiness: number | null
  pcr: number | null
  pcrSentiment: string | null
  maxPainStrike: number | null
  maxPainShift: MaxPainShift
  ivRank: number | null
  ivRankHistoryCount: number
  optionsRecent: boolean
}

// Same real thresholds api/option_reality.py's own iv_rank_gate() uses
// to gate live option entries -- reused here rather than invented, so
// the Screener's badge colors mean the same thing the deep-dive's own
// gate already means: >80 is "buying into crush," 60-80 is "elevated,"
// below 60 is the real gate's own PASS band.
const IV_RANK_AVOID = 80
const IV_RANK_ELEVATED = 60

function fmt(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined || Number.isNaN(v) ? DASH : v.toFixed(digits)
}

/** `/screener` -- Unified Omni-Screener. Originally built in the
 * "Unified Omni-Screener & Deep-Dive Interactivity" sprint (2026-08-28)
 * on four separately-polled, per-request-computed endpoints; rebuilt in
 * the "Full Universe Batch Hydration Engine" sprint (2026-08-29) on ONE
 * pre-hydrated composite source (api.screener_hydrator's background
 * loop, merged server-side by GET /api/screener/fno) because the prior
 * per-request sources were confirmed genuinely degenerate: RVOL came
 * from GET /api/ticks' own rel_vol field (scanner's live-tick pipeline,
 * confirmed via live redis-cli inspection to sit at a universal 0.0 on
 * a closed/quiet market) and Squeeze Readiness came from GET
 * /api/prebreakout (scanner's own infusion:prebreak:* keys, confirmed
 * via a real SCAN to number zero at the time of that investigation --
 * short TTLs plus a wall-clock-timeout state machine with no live ticks
 * to drive it). Both are now computed fresh from this service's own
 * already-stored DAILY OHLC bars (robust to the market being closed or
 * the scanner's live-tick pipeline being idle) -- see
 * screener_hydrator.py's own module docstring for the full real
 * investigation trail:
 *   - Symbol & Sector, Current LTP (live, 5s): GET /api/symbols + GET
 *     /api/ticks
 *   - Everything else -- Smart Money Flow, OB/FVG proximity, RVOL,
 *     Squeeze Readiness, PCR Sentiment, Max Pain, IV Rank: GET
 *     /api/screener/fno, one pre-computed composite row per symbol
 *
 * Disclosed, not silent, about the real constraints that remain:
 *   - PCR/Max Pain/IV Rank are only real for whichever symbols
 *     option_chain_queue.py's own background loop has actually
 *     refreshed recently (a rotating ~28-symbol candidate subset) --
 *     never a live per-request fetch for the whole universe (this
 *     codebase's own architecture avoids that everywhere, and hitting
 *     Upstox's real rate limit while testing a prior sprint confirmed
 *     why). A symbol outside that subset shows an honest dash, not a
 *     stale or fabricated number.
 *   - IV Rank is inherently CONTRACT-scoped (api/routes/market.py's own
 *     _iv_rank() ranks one specific contract's current IV against ITS
 *     OWN stored history) -- so this column is a "blended near-term
 *     ATM" synthesis, the plain average of the nearest-strike call and
 *     put leg's own real IV Rank, not a single all-purpose number. Also
 *     null until 60 days of daily history accumulate for at least one
 *     of those two legs. Open any symbol's own deep dive for that
 *     contract's real Greeks Exposure card in the meantime.
 *   - Squeeze Readiness has no separate candle-persistence STATE the
 *     way scanner's own pre_breakout.py does -- see squeezeLabel()'s
 *     own comment above for why the SQUEEZE/COILING labels here are
 *     real threshold buckets of the one real number, not a rebuilt
 *     state machine.
 *   - "Max Pain shift" is a session-local up/down arrow against the
 *     LAST poll this page itself saw, not a persisted multi-day trend
 *     -- this pipeline doesn't store historical Max Pain snapshots, so
 *     a longer-horizon shift would be a guess dressed as a chart. */
export function Screener() {
  const { data: symbols } = usePolling(fetchSymbols, 60000, [])
  const { data: ticks } = usePolling(fetchAllTicks, 5000, [])
  const { data: fno } = usePolling(fetchScreenerFno, 15000, [])
  const navigate = useNavigate()

  // Session-local Max Pain shift tracking -- see this component's own
  // docstring for why this is deliberately not a persisted trend.
  // React's own documented "adjusting state when a prop changes"
  // idiom (react.dev), the exact same shape useHistoricalData.ts and
  // SymbolSelector.tsx already use elsewhere in this app: setState
  // synchronously during render when the upstream value has actually
  // changed, never inside a useEffect (React's own compiler-safety
  // lint flags both effect-based setState-to-derive-a-value AND
  // reading/writing a ref during render, so a plain second useState
  // "what was fno last render" is the one shape that satisfies both
  // rules at once).
  const [prevFno, setPrevFno] = useState(fno)
  const [maxPainShifts, setMaxPainShifts] = useState<Map<string, MaxPainShift>>(new Map())
  if (fno !== prevFno) {
    setPrevFno(fno)
    const shifts = new Map<string, MaxPainShift>()
    for (const [symbol, entry] of Object.entries(fno ?? {})) {
      const current = entry.max_pain?.max_pain_strike
      const before = prevFno?.[symbol]?.max_pain?.max_pain_strike
      if (
        current !== null &&
        current !== undefined &&
        before !== null &&
        before !== undefined &&
        before !== current
      ) {
        shifts.set(symbol, current > before ? 'up' : 'down')
      }
    }
    setMaxPainShifts(shifts)
  }

  const [sortKey, setSortKey] = useState<SortKey>('readiness')
  const [sortDesc, setSortDesc] = useState(true)
  const [activeFilters, setActiveFilters] = useState<Set<QuickFilter>>(new Set())

  const rows = useMemo<ScreenerRow[]>(() => {
    const tickBySymbol = new Map<string, TickRow>((ticks ?? []).map((t) => [t.symbol, t]))
    const fnoMap: ScreenerFnoMap = fno ?? {}

    return (symbols ?? []).map((s: SymbolMeta) => {
      const tick = tickBySymbol.get(s.symbol)
      const entry = fnoMap[s.symbol]
      const readiness = entry?.squeeze_readiness ?? null
      return {
        symbol: s.symbol,
        sector: s.sector_id || DASH,
        // Live 5s tick first (freshest); the hydrator's own ltp (up to
        // HYDRATE_INTERVAL_SEC=60s old) only backstops a symbol with no
        // recent live tick yet.
        ltp: tick?.ltp ?? entry?.ltp ?? null,
        buildup: entry?.oi_buildup ?? null,
        obFvgLevel: entry?.ob_fvg_level ?? null,
        obFvgDistancePct: entry?.ob_fvg_distance_pct ?? null,
        rvol: entry?.rvol ?? null,
        squeezeState: squeezeLabel(readiness),
        readiness,
        pcr: entry?.pcr?.pcr ?? null,
        pcrSentiment: entry?.pcr?.sentiment ?? null,
        maxPainStrike: entry?.max_pain?.max_pain_strike ?? null,
        maxPainShift: maxPainShifts.get(s.symbol) ?? null,
        ivRank: entry?.iv_rank ?? null,
        ivRankHistoryCount: entry?.iv_rank_history_count ?? 0,
        optionsRecent: entry?.options_updated_at !== null && entry?.options_updated_at !== undefined,
      }
    })
  }, [symbols, ticks, fno, maxPainShifts])

  const filtered = useMemo(() => {
    let out = rows
    if (activeFilters.has('high_rvol')) out = out.filter((r) => (r.rvol ?? 0) > 1.0)
    if (activeFilters.has('squeeze')) {
      out = out.filter((r) => (r.readiness ?? 0) >= SQUEEZE_COILING_THRESHOLD)
    }
    if (activeFilters.has('bullish_buildup')) {
      out = out.filter((r) => r.buildup !== null && BULL_OI.includes(r.buildup))
    }
    if (activeFilters.has('high_pcr')) out = out.filter((r) => (r.pcr ?? 0) > 1.0)

    const sorted = [...out].sort((a, b) => {
      if (sortKey === 'symbol') return a.symbol.localeCompare(b.symbol)
      if (sortKey === 'rvol') return (a.rvol ?? -1) - (b.rvol ?? -1)
      if (sortKey === 'pcr') return (a.pcr ?? -1) - (b.pcr ?? -1)
      if (sortKey === 'iv_rank') return (a.ivRank ?? -1) - (b.ivRank ?? -1)
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
    high_rvol: 'High RVOL (>1.0x)',
    squeeze: 'Squeeze Coiling',
    bullish_buildup: 'Bullish Buildup',
    high_pcr: 'High PCR (>1.0)',
  }

  const optionsRecentCount = rows.filter((r) => r.optionsRecent).length

  return (
    <div className="flex flex-1 flex-col gap-6">
      <PageHeader
        icon={Radar}
        title="F&O Omni-Screener"
        subtitle={`${rows.length} real F&O symbols · Real-Time Universe Engine Active -- Options Data live for ${optionsRecentCount} recently-refreshed candidates`}
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
        <table className="w-full min-w-[1320px] text-left text-xs">
          <thead>
            <tr className="border-b border-hud-border text-[10px] uppercase tracking-wide text-hud-muted">
              <th
                className="cursor-pointer select-none px-3 py-2 font-bold"
                onClick={() => toggleSort('symbol')}
              >
                <span className="inline-flex items-center gap-1">
                  Symbol / Sector {sortKey === 'symbol' && <SortIcon className="h-3 w-3" />}
                </span>
              </th>
              <th className="px-3 py-2 font-bold">LTP</th>
              <th className="px-3 py-2 font-bold">Smart Money Flow</th>
              <th className="px-3 py-2 font-bold">OB/FVG Proximity</th>
              <th
                className="cursor-pointer select-none px-3 py-2 font-bold"
                onClick={() => toggleSort('rvol')}
              >
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
              <th
                className="cursor-pointer select-none px-3 py-2 font-bold"
                onClick={() => toggleSort('pcr')}
                title="Real only for recently-refreshed candidates -- see this page's own module docstring."
              >
                <span className="inline-flex items-center gap-1">
                  PCR Sentiment {sortKey === 'pcr' && <SortIcon className="h-3 w-3" />}
                </span>
              </th>
              <th className="px-3 py-2 font-bold">Max Pain</th>
              <th
                className="cursor-pointer select-none px-3 py-2 font-bold"
                onClick={() => toggleSort('iv_rank')}
                title="Blended near-term ATM IV Rank (avg. of the nearest-strike call/put leg's own real 60-day rolling rank) -- only for recently-refreshed candidates, and null until 60 days of history exist. See this page's own module docstring."
              >
                <span className="inline-flex items-center gap-1">
                  IV Rank {sortKey === 'iv_rank' && <SortIcon className="h-3 w-3" />}
                </span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hud-border">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-3 py-6 text-center text-hud-muted">
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
                  <td className="tnum px-3 py-2 font-mono text-hud-text">{fmt(r.ltp, 2)}</td>
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
                    {r.obFvgLevel !== null ? (
                      <>
                        {fmt(r.obFvgLevel, 2)}
                        {r.obFvgDistancePct !== null && (
                          <span className="ml-1 text-hud-muted">
                            ({fmt(r.obFvgDistancePct, 1)}%)
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="text-hud-muted">{DASH}</span>
                    )}
                  </td>
                  <td className="tnum px-3 py-2 font-mono text-hud-text">
                    {r.rvol !== null ? `${fmt(r.rvol)}x` : DASH}
                  </td>
                  <td className="px-3 py-2">
                    {r.readiness !== null ? (
                      <span className="tnum font-mono text-hud-text">
                        {r.squeezeState && <span>{r.squeezeState}</span>}
                        {r.readiness !== null && (
                          <span className={r.squeezeState ? 'ml-1.5 text-hud-muted' : ''}>
                            {fmt(r.readiness, 0)}
                          </span>
                        )}
                      </span>
                    ) : (
                      <span className="text-hud-muted">{DASH}</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {r.pcr !== null ? (
                      <span
                        className={
                          'tnum font-mono ' +
                          (r.pcr > 1.0 ? 'text-bear' : r.pcr < 0.7 ? 'text-bull' : 'text-hud-text')
                        }
                      >
                        {r.pcr.toFixed(2)}
                        {r.pcrSentiment && (
                          <span className="ml-1.5 text-[10px] uppercase text-hud-muted">
                            {r.pcrSentiment.replace(/_/g, ' ')}
                          </span>
                        )}
                      </span>
                    ) : (
                      <span className="text-hud-muted">{DASH}</span>
                    )}
                  </td>
                  <td className="tnum px-3 py-2 font-mono text-hud-text">
                    {r.maxPainStrike !== null ? (
                      <>
                        {fmt(r.maxPainStrike, 0)}
                        {r.maxPainShift === 'up' && <span className="ml-1 text-bull">↑</span>}
                        {r.maxPainShift === 'down' && <span className="ml-1 text-bear">↓</span>}
                      </>
                    ) : (
                      <span className="text-hud-muted">{DASH}</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {r.ivRank !== null ? (
                      <span
                        title={`Blended ATM IV Rank: ${r.ivRank.toFixed(0)}% (call+put avg., ${r.ivRankHistoryCount}-session history)`}
                        className={
                          'tnum inline-block rounded px-1.5 py-0.5 font-mono font-bold ' +
                          (r.ivRank > IV_RANK_AVOID
                            ? 'bg-bear/15 text-bear'
                            : r.ivRank >= IV_RANK_ELEVATED
                              ? // No dedicated "warning" color token exists in
                                // index.css's own --color-* palette (just bull/
                                // bear) -- Tailwind's built-in amber scale for
                                // this one elevated-but-not-extreme tier rather
                                // than stretching bull/bear's binary meaning.
                                'bg-amber-400/15 text-amber-400'
                              : 'bg-bull/15 text-bull')
                        }
                      >
                        {r.ivRank.toFixed(0)}%
                      </span>
                    ) : (
                      <span
                        title={
                          r.ivRankHistoryCount > 0
                            ? `Building history: ${r.ivRankHistoryCount}/60 sessions recorded`
                            : 'No recent option-chain refresh, or history not started yet'
                        }
                        className="text-hud-muted"
                      >
                        {DASH}
                      </span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
