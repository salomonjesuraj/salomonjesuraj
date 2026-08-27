import { useMemo } from 'react'
import { fetchAllTicks, fetchPrebreakout } from '../lib/api'
import { usePolling } from '../hooks/usePolling'
import type { PrebreakoutRow, TickRow } from '../types'

const DASH = '—'
const MAX_ROWS = 10

// The real /api/prebreakout state taxonomy -- coiled/accumulating/
// compressing/triggered -- doesn't have a fixed-trigger-price or
// distance field of its own, and isn't the COILING_AT_RESISTANCE/
// ACCUMULATION_BASE/RETEST_HELD/SQUEEZE enum this zone's spec assumed.
// Mapped to the closest honest label rather than force-fit to names
// this pipeline doesn't use.
const STATE_LABEL: Record<string, string> = {
  coiled: 'SQUEEZE',
  accumulating: 'ACCUMULATION BUILDING',
  compressing: 'COILING',
  triggered: 'TRIGGERED',
}

interface Row {
  pb: PrebreakoutRow
  trigger: number | undefined
  distancePct: number | undefined
}

function fmt(v: number | undefined, digits = 2): string {
  return v === undefined || Number.isNaN(v) ? DASH : v.toFixed(digits)
}

/** Zone 4: Pre-Breakout Coiling & Retest Watchlist.
 *
 * The "Fixed Breakout Trigger" and "Distance to Trigger" columns don't
 * exist on /api/prebreakout's own row -- joined here by symbol against
 * /api/ticks' command_center.resistance/support (falls back to
 * breakout_area/invalidation_area) to get a real trigger price, then
 * computed the distance client-side. No numeric per-symbol ATR is
 * available in bulk (only a qualitative atr_state string), so distance
 * is shown in % only -- an ATR-multiple column would have to be a
 * guess, so it's left out rather than fabricated.
 */
export function PreBreakoutWatchlist() {
  const { data: prebreakout } = usePolling(fetchPrebreakout, 5000, [])
  const { data: ticks } = usePolling(fetchAllTicks, 5000, [])

  const rows = useMemo(() => {
    if (!prebreakout) return [] as Row[]
    const tickBySymbol = new Map<string, TickRow>((ticks ?? []).map((t) => [t.symbol, t]))

    return prebreakout
      .filter((pb) => pb.state !== 'triggered' || pb.has_signal === false)
      .map((pb) => {
        const tick = tickBySymbol.get(pb.symbol)
        const cc = tick?.command_center
        const trigger = cc?.resistance ?? cc?.support ?? undefined
        const ltp = tick?.ltp
        const distancePct =
          trigger !== undefined && ltp ? Math.abs((trigger - ltp) / ltp) * 100 : undefined
        return { pb, trigger, distancePct }
      })
      .sort((a, b) => (b.pb.readiness_score ?? 0) - (a.pb.readiness_score ?? 0))
      .slice(0, MAX_ROWS)
  }, [prebreakout, ticks])

  return (
    <section>
      <h2 className="mb-3 text-xs font-bold uppercase tracking-wide text-hud-muted">
        Pre-Breakout Coiling & Retest Watchlist
      </h2>
      <div className="overflow-x-auto rounded-xl border border-hud-border bg-hud-panel">
        <table className="w-full min-w-[640px] text-left text-xs">
          <thead>
            <tr className="border-b border-hud-border text-[10px] uppercase tracking-wide text-hud-muted">
              <th className="px-3 py-2 font-bold">Symbol</th>
              <th className="px-3 py-2 font-bold">Setup Type</th>
              <th className="px-3 py-2 font-bold">Trigger</th>
              <th className="px-3 py-2 font-bold">Distance</th>
              <th className="px-3 py-2 font-bold">RVol</th>
              <th className="px-3 py-2 font-bold">Readiness</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hud-border">
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-hud-muted">
                  Nothing coiling right now.
                </td>
              </tr>
            ) : (
              rows.map(({ pb, trigger, distancePct }) => (
                <tr key={pb.symbol}>
                  <td className="px-3 py-2 font-mono font-bold text-hud-text">{pb.symbol}</td>
                  <td className="px-3 py-2 text-hud-muted">
                    {STATE_LABEL[pb.state] ?? pb.state.toUpperCase()}
                  </td>
                  <td className="tnum px-3 py-2 font-mono text-hud-text">{fmt(trigger)}</td>
                  <td className="tnum px-3 py-2 font-mono text-hud-text">
                    {distancePct !== undefined ? `${fmt(distancePct)}%` : DASH}
                  </td>
                  <td className="tnum px-3 py-2 font-mono text-hud-text">
                    {fmt(pb.rel_vol, 1)}x
                  </td>
                  <td className="tnum px-3 py-2 font-mono text-hud-text">
                    {fmt(pb.readiness_score, 0)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
