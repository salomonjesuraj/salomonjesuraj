import { AlertTriangle, Maximize2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { LiveCandlestickChart } from './LiveCandlestickChart'
import { DASH } from './MetricCard'
import type { ChartInterval } from '../lib/api'
import type { BrokerPosition } from '../types'

const TIMEFRAMES: ChartInterval[] = ['1m', '5m', '15m', '1h', '4h']

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
 * grid.
 *
 * "Strict Quant & Option Logic" sprint (2026-08-28): T1/T2/T3 no longer
 * poll GET /api/trade-blueprint/{symbol} -- that endpoint only has real
 * Fibonacci targets when there's an ACTIVE SCANNER SIGNAL for the
 * symbol, which a manually-held broker position typically doesn't have
 * (both real positions this card was built against, POWERGRID and
 * KAYNES, come back "no_active_signal"). Falling back through an empty
 * blueprint meant T3's own fallback chain (`target_3_fib || t2`) always
 * landed on the same value as T2 -- not a rounding coincidence, T2 and
 * T3 were structurally guaranteed to be identical. All three now come
 * straight from `intel` itself -- api/broker_sync.py's own
 * compute_position_intelligence() always computes real, distinct T2/T3
 * (1.618/2.618 Fibonacci extensions of the real Donchian swing) for
 * every position, signal or no signal. Explicitly labeled SPOT TARGETS
 * below since every one of them is a level on the underlying's own
 * chart, not the option premium. */
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

  const t1 = intel.target_primary
  const t2 = intel.target_secondary
  const t3 = intel.target_tertiary

  const ltp = position.last_price
  const stop = intel.invalidation_level
  // Real bug fix (2026-08-28): for an OPTION position, `ltp` is the
  // option's own PREMIUM (e.g. Rs 116.00), while `stop`/T1 are levels
  // on the UNDERLYING's own spot chart (e.g. Rs 4,011.50) -- the exact
  // same premium-vs-spot unit mismatch already fixed for Structural
  // Risk Est., missed here the first time even though it's the
  // identical bug class. Dividing one by the other produced a
  // coincidentally plausible-looking ratio (often near 1:1) that was
  // never a real number -- mixed-unit junk math, not a rounding
  // artifact. Never computed for an option position now; only a plain
  // equity position (where ltp/stop/T1 are all genuinely the same
  // spot-price unit) gets a real ratio here.
  const liveRiskReward =
    !intel.is_option && stop !== null && t1 !== null && Math.abs(ltp - stop) > 0
      ? Math.abs(t1 - ltp) / Math.abs(ltp - stop)
      : null

  // Real entry price -- api/broker_sync.py's own effective_entry_price
  // already applies the same day_buy_price fallback (a position bought
  // and still held intraday reports average_price as a bare 0, a real
  // Upstox quirk, not a missing value) as the backend's own Capital
  // Deployed sum, so this card and that master strip can never disagree
  // about what this position's own real entry was.
  const effectiveEntry = intel.effective_entry_price

  // Full-screen chart modal (2026-08-28) -- the embedded chart's own
  // h-48 footprint is deliberately compact for the grid layout, too
  // small for real technical analysis on demand. Escape closes it the
  // same way a click on the backdrop does; a click on the chart panel
  // itself is stopped from bubbling to the backdrop so it doesn't
  // close while the trader is actually interacting with the chart.
  const [isChartExpanded, setIsChartExpanded] = useState(false)
  const [expandedInterval, setExpandedInterval] = useState<ChartInterval>('1m')
  useEffect(() => {
    if (!isChartExpanded) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsChartExpanded(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isChartExpanded])

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
            <span>Avg {fmt(effectiveEntry)}</span>
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

      <div className="relative mt-3">
        <LiveCandlestickChart
          symbol={intel.underlying}
          heightClassName="h-48"
          brokerPosition={{ entry: effectiveEntry, stop, target1: t1 }}
        />
        <button
          type="button"
          onClick={() => setIsChartExpanded(true)}
          aria-label="Expand chart"
          title="Expand chart"
          className="absolute right-2 top-2 z-10 flex items-center justify-center rounded-md bg-hud-bg/80 p-1.5 text-hud-muted ring-1 ring-hud-border backdrop-blur transition-colors hover:bg-hud-panel-hover hover:text-hud-text"
        >
          <Maximize2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {isChartExpanded && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`${symbol} expanded chart`}
          // Fully opaque, not the previous /95 -- a real bug caught
          // live: at 95% opacity, this card's own content (still
          // mounted in normal page flow directly behind the modal) was
          // faintly visible through the backdrop, reading as a
          // cluttered "ghost" overlay behind the big chart. Solid black
          // removes any possibility of that regardless of what's
          // actually behind it.
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950 p-8"
          onClick={() => setIsChartExpanded(false)}
        >
          <div
            className="flex h-[88vh] w-full max-w-6xl flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Minimal top bar only -- symbol, real timeframe toggles
                (charts.py's own _aggregate() already turns real 1-min
                bars into 5m/15m/1h/4h; this just exposes it), and
                Close. No position data card, no PnL badge, no grid --
                the ask was an unobstructed chart. */}
            <div className="mb-3 flex items-center justify-between gap-3">
              <h3 className="font-mono text-sm font-bold uppercase tracking-wide text-hud-text">
                {symbol}
              </h3>
              <div className="flex items-center gap-1 rounded-lg bg-hud-panel p-1 ring-1 ring-hud-border">
                {TIMEFRAMES.map((tf) => (
                  <button
                    key={tf}
                    type="button"
                    onClick={() => setExpandedInterval(tf)}
                    className={
                      'rounded px-2.5 py-1 text-[11px] font-bold uppercase transition-colors ' +
                      (expandedInterval === tf
                        ? 'bg-bull/15 text-bull'
                        : 'text-hud-muted hover:bg-hud-panel-hover hover:text-hud-text')
                    }
                  >
                    {tf}
                  </button>
                ))}
              </div>
              <button
                type="button"
                onClick={() => setIsChartExpanded(false)}
                aria-label="Close"
                className="flex items-center gap-1.5 rounded-lg bg-hud-panel px-3 py-1.5 text-xs font-bold text-hud-text ring-1 ring-hud-border transition-colors hover:bg-hud-panel-hover"
              >
                <X className="h-3.5 w-3.5" />
                Close
              </button>
            </div>
            <div className="min-h-0 flex-1">
              <LiveCandlestickChart
                symbol={intel.underlying}
                heightClassName="h-full"
                brokerPosition={{ entry: effectiveEntry, stop, target1: t1 }}
                interval={expandedInterval}
              />
            </div>
          </div>
        </div>
      )}

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
          <span className="text-hud-muted">{intel.is_option ? 'Premium R:R' : 'Live R:R (LTP-based)'}</span>
          {intel.is_option ? (
            <span className="text-[10px] italic text-hud-muted">Awaiting Options Analytics</span>
          ) : (
            <span className="tnum font-mono font-bold text-hud-text">
              {liveRiskReward !== null ? `1:${liveRiskReward.toFixed(2)}` : DASH}
            </span>
          )}
        </div>
        <div className="mt-2 border-t border-hud-border pt-2">
          {/* "Strict Quant & Option Logic" sprint (2026-08-28): labeled
              explicitly so a trader holding an option never mistakes
              these for premium targets -- every one of T1/T2/T3 is a
              level on the underlying's own spot chart. */}
          <div className="mb-1.5 text-[10px] font-bold uppercase tracking-wide text-hud-muted">
            Spot Targets
          </div>
          <div className="grid grid-cols-3 gap-2">
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
        {intel.is_option && (
          <div className="mt-2 flex items-center justify-between rounded-lg border-t border-hud-border bg-horizon-btst/10 px-2 py-1.5 ring-1 ring-horizon-btst/30">
            <span className="text-[10px] font-bold uppercase tracking-wide text-horizon-btst">
              Spot Required for Breakeven
            </span>
            <span className="tnum font-mono text-sm font-black text-horizon-btst">
              {fmt(intel.spot_breakeven)}
            </span>
          </div>
        )}
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
