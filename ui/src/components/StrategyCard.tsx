import { DASH } from './MetricCard'
import type { RankedStrategy } from '../types'

// Duplicated from OptionsAnalytics.tsx (this component's only real
// caller) rather than promoted to a shared utils module for two small,
// single-purpose helpers -- "Frontend Component Test Suite" sprint
// (2026-08-29) extracted this component into its own file specifically
// so it's independently testable/importable without also pulling in
// the page's own hooks/data-fetching, and this app's own established
// convention (see e.g. api/trade_blueprint.py's duplicated
// FIB_EXTENSION_T2 comment) is a small duplicated helper across
// modules over a shared import for something this size.
function fmt(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined || Number.isNaN(value) ? DASH : value.toFixed(digits)
}

function titleCase(value: string | null | undefined): string {
  if (!value) return DASH
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

/** One ranked option strategy from GET /api/options/strategy-selector --
 * "Option Bias Alignment" fix (2026-08-29) added the optional
 * `smcAligned` accent (a subtle green ring + inline "· SMC" badge, NOT
 * a re-sort of the shortlist's own real fit-score order from
 * api/options_strategies.py). See OptionsAnalytics.tsx's own comment
 * on STRATEGY_SMC_DIRECTION for what "aligned" means and why only two
 * of the six catalog strategies can ever carry it. */
export function StrategyCard({
  strategy,
  smcAligned,
}: {
  strategy: RankedStrategy
  smcAligned?: boolean
}) {
  const netKind = strategy.net_debit !== undefined ? 'Net Debit' : 'Net Credit'
  const netValue = strategy.net_debit ?? strategy.net_credit
  return (
    <div
      className={
        'rounded-xl border bg-hud-panel p-4 ' +
        (smcAligned ? 'border-bull/50 ring-1 ring-bull/30' : 'border-hud-border')
      }
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-mono text-sm font-bold text-hud-text">
          {titleCase(strategy.strategy)}
          {smcAligned && (
            <span
              className="ml-1.5 align-middle text-[9px] font-bold uppercase tracking-wide text-bull"
              title="This strategy's own directional class matches the chart's current SMC bias"
            >
              · SMC
            </span>
          )}
        </h3>
        <span className="tnum shrink-0 rounded bg-bull/10 px-1.5 py-0.5 text-xs font-bold text-bull">
          {fmt(strategy.fit_score, 0)}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
        {strategy.legs?.map((leg, i) => (
          <span
            key={i}
            className={
              'rounded px-1.5 py-0.5 font-mono ' +
              (leg.action === 'BUY' ? 'bg-bull/10 text-bull' : 'bg-bear/10 text-bear')
            }
          >
            {leg.action} {leg.type} {fmt(leg.strike, 0)}
          </span>
        ))}
      </div>
      <div className="tnum mt-3 grid grid-cols-3 gap-2 text-[11px] text-hud-muted">
        <div>
          Max P&L
          <div className="text-hud-text">
            +₹{fmt(strategy.max_profit)} / -₹{fmt(strategy.max_loss)}
          </div>
        </div>
        <div>
          Breakeven
          <div className="text-hud-text">
            {strategy.breakeven?.map((b) => fmt(b, 1)).join(', ') || DASH}
          </div>
        </div>
        <div>
          {netKind}
          <div className="text-hud-text">{netValue !== undefined ? `₹${fmt(netValue)}` : DASH}</div>
        </div>
      </div>
      <ul className="mt-3 space-y-1 border-t border-hud-border pt-2 text-[11px] text-hud-muted">
        <li>{strategy.components.directional.reason}</li>
        <li>{strategy.components.pcr.reason}</li>
        <li>{strategy.components.max_pain.reason}</li>
      </ul>
    </div>
  )
}
