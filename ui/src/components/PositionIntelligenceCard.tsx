import { AlertTriangle } from 'lucide-react'
import { LiveCandlestickChart } from './LiveCandlestickChart'
import { DASH } from './MetricCard'
import { usePolling } from '../hooks/usePolling'
import { fetchTradeBlueprint } from '../lib/api'
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
 * see PositionIntelligence's own type docstring for exactly which.
 *
 * "Visual Tracking & Lifecycle" sprint (2026-08-27): embeds a
 * miniature LiveCandlestickChart (real 1-min candles + the same real
 * MTF Support/Resistance/Channel overlay Sniper HUD's own chart already
 * draws -- see LiveCandlestickChart.tsx's Effect 4) and replaced the
 * "How Far Can It Go"/"Where Will It Turn" prose with an explicit data
 * grid. T1/T2/T3 reuse the real, already-computed Fibonacci targets
 * GET /api/trade-blueprint/{symbol} produces (the same endpoint
 * ActionCard.tsx's own t1/t2/t3 fallback chain already trusts) rather
 * than inventing a second Fibonacci calculation here -- MTF
 * Support/Resistance stay sourced from `intel.structure`, already on
 * this exact payload, no second fetch needed for those two. */
export function PositionIntelligenceCard({ position }: { position: BrokerPosition }) {
  const symbol = position.trading_symbol || position.tradingsymbol || '—'
  const intel = position.intelligence
  const pnl = position.pnl
  const pnlPositive = pnl >= 0
  const thetaClass = THETA_CLASS[intel.theta_risk] || THETA_CLASS['N/A']
  const thetaFillPct = THETA_FILL_PCT[intel.theta_risk] ?? 0
  const horizonClass = HORIZON_CLASS[intel.holding_horizon] || 'bg-hud-muted/15 text-hud-muted ring-1 ring-hud-muted/30'
  // "Omnipresent Alert Engine" sprint (2026-08-27): a red pulse/glow the
  // moment this real position is telling the trader to exit -- derived
  // straight from the same real intel object already on the card, no
  // separate flag needed. Persistent (not one-shot) for the same reason
  // ActionCard's green pulse is persistent: a glow left on while the
  // condition holds isn't spam the way a repeating sound would be.
  const isExitImmediate = intel.holding_horizon === 'EXIT IMMEDIATELY'

  // 10s cadence: a position's own Fibonacci geometry moves far slower
  // than a live-scanning Sniper HUD candidate's does, so this doesn't
  // need ActionCard/LiveCandlestickChart's own 5s cadence for the
  // identical endpoint.
  const { data: blueprint } = usePolling(
    () => fetchTradeBlueprint(intel.underlying),
    10000,
    [intel.underlying],
  )
  const t1 = blueprint?.target_1_fib || intel.target_primary
  const t2 = blueprint?.target_2_fib || intel.target_secondary
  const t3 = blueprint?.target_3_fib || t2

  const ltp = position.last_price
  const stop = intel.invalidation_level
  // "Current Live Risk:Reward Ratio (based on LTP vs Stop/Target)" --
  // read literally: reward/risk from where price actually is right
  // now, against T1 (the nearest, soonest-reachable target) and the
  // real invalidation level, not the original entry's own planned R:R.
  // Honestly DASH (never a fabricated ratio) when either level is
  // unavailable or LTP already sits exactly on the stop.
  const liveRiskReward =
    stop !== null && t1 !== null && Math.abs(ltp - stop) > 0
      ? Math.abs(t1 - ltp) / Math.abs(ltp - stop)
      : null

  // Same real same-day-buy quirk broker_sync.py's own verification
  // disclosure documents: average_price reads as a bare 0 for a
  // position bought and still held intraday, never carried overnight --
  // day_buy_price is the real fill for that case. See BrokerPosition's
  // own type comment.
  const chartEntry = position.average_price || position.day_buy_price || null

  return (
    <article
      className={
        'min-w-0 rounded-xl border bg-hud-panel p-4 shadow-lg shadow-black/30 ' +
        (isExitImmediate
          ? 'animate-pulse border-bear ring-4 ring-bear/60 shadow-[0_0_20px_rgba(255,61,94,0.35)]'
          : 'border-hud-border')
      }
    >
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

      <div className="mt-3">
        <LiveCandlestickChart
          symbol={intel.underlying}
          heightClassName="h-48"
          brokerPosition={{ entry: chartEntry, stop, target1: t1 }}
        />
      </div>

      {/* Institutional data grid ("Visual Tracking & Lifecycle" sprint,
          2026-08-27) -- replaces the earlier "How Far Can It
          Go"/"Where Will It Turn" prose with explicit numbers. */}
      <div className="mt-2 rounded-lg border border-hud-border bg-hud-bg/60 p-3 text-[11px]">
        <div className="grid grid-cols-2 gap-2">
          <div>
            <div className="text-hud-muted">MTF Support</div>
            <div className="tnum font-mono text-bull">{fmt(intel.structure.support)}</div>
          </div>
          <div>
            <div className="text-hud-muted">MTF Resistance</div>
            <div className="tnum font-mono text-bear">{fmt(intel.structure.resistance)}</div>
          </div>
        </div>
        {intel.nearest_ob_fvg_level !== null && (
          <div className="mt-1.5 text-hud-muted">
            Nearest OB/FVG <span className="tnum font-mono text-hud-text">{fmt(intel.nearest_ob_fvg_level)}</span>
          </div>
        )}
        <div className="mt-2 flex items-center justify-between border-t border-hud-border pt-2">
          <span className="text-hud-muted">Live R:R (LTP-based)</span>
          <span className="tnum font-mono font-bold text-hud-text">
            {liveRiskReward !== null ? `1:${liveRiskReward.toFixed(2)}` : DASH}
          </span>
        </div>
        <div className="mt-2 grid grid-cols-3 gap-2 border-t border-hud-border pt-2">
          <div>
            <div className="text-hud-muted">T1</div>
            <div className="tnum font-mono text-bull">{fmt(t1)}</div>
          </div>
          <div>
            <div className="text-hud-muted">T2</div>
            <div className="tnum font-mono text-bull">{fmt(t2)}</div>
          </div>
          <div>
            <div className="text-hud-muted">T3</div>
            <div className="tnum font-mono text-bull">{fmt(t3)}</div>
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
