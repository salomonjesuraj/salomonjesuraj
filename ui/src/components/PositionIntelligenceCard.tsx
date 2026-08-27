import { AlertTriangle } from 'lucide-react'
import { DASH } from './MetricCard'
import type { BrokerPosition } from '../types'

function fmt(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined || Number.isNaN(value) ? DASH : value.toFixed(digits)
}

const THETA_CLASS: Record<string, string> = {
  LOW: 'bg-bull/15 text-bull',
  ACCELERATING: 'bg-horizon-btst/15 text-horizon-btst',
  SEVERE: 'bg-bear/15 text-bear',
  'N/A': 'bg-hud-muted/15 text-hud-muted',
}

const THETA_FILL_PCT: Record<string, number> = {
  LOW: 25,
  ACCELERATING: 65,
  SEVERE: 100,
  'N/A': 0,
}

const HORIZON_CLASS: Record<string, string> = {
  'HOLD (2-3 DAYS)': 'bg-bull/15 text-bull ring-1 ring-bull/40',
  'RUNNER (INTRADAY ONLY)': 'bg-horizon-scalp/15 text-horizon-scalp ring-1 ring-horizon-scalp/40',
  'TIGHTEN STOP': 'bg-horizon-btst/15 text-horizon-btst ring-1 ring-horizon-btst/40',
  'EXIT IMMEDIATELY': 'bg-bear/15 text-bear ring-1 ring-bear/40',
}

/** One real broker position + its real Position Decision & Horizon
 * Engine read ("Broker Sync & Active Position Intelligence" sprint,
 * 2026-08-27). Every number here is either a raw Upstox field or a
 * value api/broker_sync.py computed from real structure/DTE data --
 * see PositionIntelligence's own type docstring for exactly which. */
export function PositionIntelligenceCard({ position }: { position: BrokerPosition }) {
  const symbol = position.trading_symbol || position.tradingsymbol || '—'
  const intel = position.intelligence
  const pnl = position.pnl
  const pnlPositive = pnl >= 0
  const thetaClass = THETA_CLASS[intel.theta_risk] || THETA_CLASS['N/A']
  const thetaFillPct = THETA_FILL_PCT[intel.theta_risk] ?? 0
  const horizonClass = HORIZON_CLASS[intel.holding_horizon] || 'bg-hud-muted/15 text-hud-muted ring-1 ring-hud-muted/30'

  return (
    <article className="min-w-0 rounded-xl border border-hud-border bg-hud-panel p-4 shadow-lg shadow-black/30">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate font-mono text-sm font-bold tracking-tight text-hud-text">{symbol}</h3>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-hud-muted">
            <span
              className={
                'rounded px-1.5 py-0.5 text-[10px] font-bold ' +
                (intel.direction === 'BULL' ? 'bg-bull/15 text-bull' : 'bg-bear/15 text-bear')
              }
            >
              {intel.direction}
            </span>
            <span>Qty {position.quantity}</span>
            <span>Avg {fmt(position.average_price)}</span>
          </div>
        </div>
        {/* Live PnL -- a glowing badge, colored + a soft outer ring so it
            reads at a glance from across the grid. */}
        <div
          className={
            'shrink-0 rounded-lg px-3 py-1.5 text-right ring-1 ' +
            (pnlPositive ? 'bg-bull/10 text-bull ring-bull/40 shadow-[0_0_12px_rgba(16,185,129,0.25)]' : 'bg-bear/10 text-bear ring-bear/40 shadow-[0_0_12px_rgba(239,68,68,0.25)]')
          }
        >
          <div className="tnum font-mono text-sm font-bold">
            {pnlPositive ? '+' : ''}
            {fmt(pnl, 0)}
          </div>
          <div className="text-[9px] uppercase tracking-wide opacity-80">LTP {fmt(position.last_price)}</div>
        </div>
      </div>

      {intel.warning_tags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {intel.warning_tags.map((tag) => (
            <span
              key={tag}
              className="flex items-center gap-1 rounded bg-bear/15 px-1.5 py-0.5 text-[10px] font-bold text-bear ring-1 ring-bear/40"
            >
              <AlertTriangle className="h-2.5 w-2.5" />
              {tag}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 grid grid-cols-2 gap-2 rounded-lg border border-hud-border bg-hud-bg/60 p-3 text-[11px]">
        <div>
          <div className="text-hud-muted">How Far Can It Go</div>
          <div className="tnum font-mono text-hud-text">
            <span className="text-bull">{fmt(intel.target_primary)}</span>
            {intel.target_secondary !== null && (
              <span className="text-hud-muted"> / {fmt(intel.target_secondary)}</span>
            )}
          </div>
        </div>
        <div>
          <div className="text-hud-muted">Where Will It Turn</div>
          <div className="tnum font-mono text-hud-text">
            <span className="text-bear">{fmt(intel.invalidation_level)}</span>
            {intel.nearest_ob_fvg_level !== null && (
              <span className="text-hud-muted"> · OB/FVG {fmt(intel.nearest_ob_fvg_level)}</span>
            )}
          </div>
        </div>
      </div>

      {/* DTE & Theta Meter */}
      <div className="mt-2 rounded-lg border border-hud-border bg-hud-bg/60 p-3 text-[11px]">
        <div className="flex items-center justify-between">
          <span className="text-hud-muted">
            DTE {intel.dte_trading_days !== null ? `${intel.dte_trading_days}d` : DASH}
          </span>
          <span className={'rounded px-1.5 py-0.5 text-[10px] font-bold ' + thetaClass}>
            {intel.theta_risk}
          </span>
        </div>
        <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-hud-border">
          <div
            className={'h-full rounded-full ' + (intel.theta_risk === 'SEVERE' ? 'bg-bear' : intel.theta_risk === 'ACCELERATING' ? 'bg-horizon-btst' : 'bg-bull')}
            style={{ width: `${thetaFillPct}%` }}
          />
        </div>
      </div>

      {/* Decision Tag -- large, scannable */}
      <div className={'mt-3 rounded-lg px-3 py-2 text-center text-xs font-bold uppercase tracking-wide ' + horizonClass}>
        {intel.holding_horizon}
      </div>
    </article>
  )
}
