import { useMemo } from 'react'
import { fetchAllTicks, fetchOiBuildupMap } from '../lib/api'
import { usePolling } from '../hooks/usePolling'
import { BEAR_OI, BULL_OI, OI_LABEL } from '../lib/oiBuildup'
import type { OIBuildupType, TickRow } from '../types'

const DASH = '—'
const MAX_ROWS = 8

interface Row {
  tick: TickRow
  oi: OIBuildupType
}

function RadarColumn({ title, tone, rows }: { title: string; tone: 'bull' | 'bear'; rows: Row[] }) {
  const toneClass = tone === 'bull' ? 'text-bull' : 'text-bear'
  const dot = tone === 'bull' ? '🟢' : '🔴'
  return (
    <div className="flex-1 rounded-xl border border-hud-border bg-hud-panel p-4">
      <h3 className={'mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-wide ' + toneClass}>
        <span>{dot}</span>
        {title}
      </h3>
      {rows.length === 0 ? (
        <p className="text-sm text-hud-muted">No qualifying structure right now.</p>
      ) : (
        <div className="flex flex-col divide-y divide-hud-border">
          {rows.map(({ tick, oi }) => (
            <div key={tick.symbol} className="grid grid-cols-[1fr_auto] items-center gap-2 py-2">
              <div className="min-w-0">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="truncate font-mono text-sm font-bold text-hud-text">
                    {tick.symbol}
                  </span>
                  <span className="tnum font-mono text-xs text-hud-muted">
                    {tick.ltp?.toFixed(2) ?? DASH}
                  </span>
                </div>
                <p className="truncate text-[11px] text-hud-muted">
                  {tick.command_center?.why ?? tick.strength_reasons?.[0] ?? tick.weakness_reasons?.[0] ?? DASH}
                </p>
              </div>
              <div className="flex flex-col items-end gap-1">
                <span
                  className={
                    'rounded px-1.5 py-0.5 text-[9px] font-bold ' +
                    (tone === 'bull' ? 'bg-bull/15 text-bull' : 'bg-bear/15 text-bear')
                  }
                >
                  {OI_LABEL[oi]}
                </span>
                <span className="text-[9px] font-bold uppercase tracking-wide text-hud-muted">
                  {tick.trade_horizon_label ?? DASH}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Zone 3: Smart Money Direction Radar.
 *
 * "SMC Tag" is command_center.why / strength_reasons[0] -- a real,
 * already-generated string ("EMA 5/9/20 bullish stack"), not fabricated
 * BOS/CHOCH jargon this pipeline doesn't actually compute per-symbol
 * yet. mtf_dots' green-dot count stands in for "multi-timeframe
 * bullish structure" the same honest way. Trade Horizon here is the
 * always-on per-tick heuristic (trade_horizon_label), not
 * TradeBlueprint's stricter signal-gated classifier -- see TickRow's
 * own note in types.ts for why they're deliberately different fields.
 */
export function SmartMoneyRadar() {
  const { data: ticks } = usePolling(fetchAllTicks, 5000, [])
  const { data: oiMap } = usePolling(fetchOiBuildupMap, 5000, [])

  const { bulls, bears } = useMemo(() => {
    if (!ticks || !oiMap) return { bulls: [] as Row[], bears: [] as Row[] }

    const withOi: Row[] = ticks
      .map((tick) => ({ tick, oi: oiMap[tick.symbol] ?? ('NEUTRAL' as OIBuildupType) }))
      .filter((r) => r.oi !== 'NEUTRAL')

    const greenDots = (t: TickRow) =>
      Object.values(t.mtf_dots ?? {}).filter((d) => d === 'G').length

    const bullRows = withOi
      .filter((r) => r.tick.trend_bias === 'BUY' && BULL_OI.includes(r.oi))
      .sort(
        (a, b) =>
          (b.tick.stock_breakout_score ?? 0) +
          greenDots(b.tick) * 5 -
          ((a.tick.stock_breakout_score ?? 0) + greenDots(a.tick) * 5),
      )
      .slice(0, MAX_ROWS)

    const bearRows = withOi
      .filter((r) => r.tick.trend_bias === 'SELL' && BEAR_OI.includes(r.oi))
      .sort(
        (a, b) =>
          (b.tick.stock_breakout_score ?? 0) +
          greenDots(b.tick) * 5 -
          ((a.tick.stock_breakout_score ?? 0) + greenDots(a.tick) * 5),
      )
      .slice(0, MAX_ROWS)

    return { bulls: bullRows, bears: bearRows }
  }, [ticks, oiMap])

  return (
    <section>
      <h2 className="mb-3 text-xs font-bold uppercase tracking-wide text-hud-muted">
        Smart Money Direction Radar
      </h2>
      <div className="flex flex-col gap-4 md:flex-row">
        <RadarColumn title="Bullish Smart Money Flow" tone="bull" rows={bulls} />
        <RadarColumn title="Bearish Smart Money Flow" tone="bear" rows={bears} />
      </div>
    </section>
  )
}
