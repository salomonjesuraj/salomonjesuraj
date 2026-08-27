import { BookOpen } from 'lucide-react'
import { EquityCurveChart } from '../components/EquityCurveChart'
import { DASH, MetricCard } from '../components/MetricCard'
import { PageHeader } from '../components/PageHeader'
import { useLedger } from '../hooks/useLedger'
import type { JournalTrade } from '../types'

function fmt(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined || Number.isNaN(value) ? DASH : value.toFixed(digits)
}

const STATUS_CLASS: Record<string, string> = {
  PLANNED: 'bg-horizon-intraday/15 text-horizon-intraday',
  WATCH: 'bg-horizon-btst/15 text-horizon-btst',
  BLOCKED: 'bg-bear/15 text-bear',
  CLOSED: 'bg-hud-muted/15 text-hud-muted',
}

const WIN_OUTCOMES = new Set(['WIN', 'TARGET', 'T1', 'T2'])
const LOSS_OUTCOMES = new Set(['LOSS', 'STOP', 'SL'])

function TradeCard({ trade }: { trade: JournalTrade }) {
  const outcomeTone = trade.outcome
    ? WIN_OUTCOMES.has(trade.outcome)
      ? 'text-bull'
      : LOSS_OUTCOMES.has(trade.outcome)
        ? 'text-bear'
        : 'text-hud-muted'
    : 'text-hud-muted'
  return (
    <article className="rounded-xl border border-hud-border bg-hud-panel p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="font-mono text-sm font-bold text-hud-text">{trade.symbol}</h3>
          <span className="text-[10px] uppercase tracking-wide text-hud-muted">
            {trade.decision}
          </span>
        </div>
        <span
          className={
            'shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ' +
            (STATUS_CLASS[trade.status] || 'bg-hud-muted/15 text-hud-muted')
          }
        >
          {trade.status}
        </span>
      </div>

      <div className="tnum mt-3 grid grid-cols-4 gap-2 text-[11px]">
        <div>
          Entry
          <div className="font-mono text-hud-text">{fmt(trade.entry)}</div>
        </div>
        <div>
          Stop
          <div className="font-mono text-bear">{fmt(trade.stop)}</div>
        </div>
        <div>
          T1
          <div className="font-mono text-bull">{fmt(trade.target1)}</div>
        </div>
        <div>
          R:R
          <div className="font-mono text-hud-text">1:{fmt(trade.rr1)}</div>
        </div>
      </div>

      {trade.outcome && (
        <div className={'tnum mt-2 text-xs font-bold ' + outcomeTone}>
          {trade.outcome}
          {trade.exit_price ? ` @ ${fmt(trade.exit_price)}` : ''}
        </div>
      )}

      {(trade.strength_reasons?.length || trade.rejection_reasons?.length) ? (
        <div className="mt-3 flex flex-wrap gap-1.5 border-t border-hud-border pt-2">
          {trade.strength_reasons?.slice(0, 3).map((tag) => (
            <span key={tag} className="rounded bg-bull/10 px-1.5 py-0.5 text-[10px] text-bull">
              {tag}
            </span>
          ))}
          {trade.rejection_reasons?.slice(0, 3).map((tag) => (
            <span
              key={tag}
              className="rounded bg-horizon-btst/15 px-1.5 py-0.5 text-[10px] text-horizon-btst"
            >
              ⚠ {tag}
            </span>
          ))}
        </div>
      ) : null}
    </article>
  )
}

/** `/journal` -- The Ledger, wired to real backend routes (2026-08-27
 * data-wiring sprint). `/api/journal/trades` is a real paper-trade
 * journal, not a live broker execution log (this backend never places
 * live orders -- see api/routes/execution.py's own module docstring),
 * and no route anywhere aggregates realized rupee P&L across trades, so
 * "Realized P&L" is replaced with the real headline number this backend
 * actually produces: cost-aware expectancy in R-multiples. */
export function TheLedger() {
  const { data } = useLedger()
  const stats = data?.stats
  const expectancy = data?.expectancy
  const trades = data?.trades ?? []
  const staged = data?.staged
  const openCount = stats ? stats.planned + stats.watch : null

  return (
    <div className="flex flex-1 flex-col gap-6">
      <PageHeader
        icon={BookOpen}
        title="The Ledger"
        subtitle="Paper-trade journal and execution staging -- no live orders are ever placed"
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard label="Open / Watch" value={openCount?.toString() ?? DASH} />
        <MetricCard label="Closed Today" value={stats?.closed?.toString() ?? DASH} />
        <MetricCard
          label="Expectancy (R)"
          value={fmt(expectancy?.expectancy_r, 3)}
          tone={
            expectancy?.expectancy_r === null || expectancy?.expectancy_r === undefined
              ? 'neutral'
              : expectancy.expectancy_r > 0
                ? 'good'
                : 'bad'
          }
          sublabel={
            expectancy ? `${expectancy.sample.closed} closed of ${expectancy.sample.total}` : undefined
          }
        />
        <MetricCard label="Entries Today" value={stats?.total_today?.toString() ?? DASH} />
      </div>

      <div className="rounded-xl border border-hud-border bg-hud-panel p-4">
        <h2 className="mb-2 text-xs font-bold uppercase tracking-wide text-hud-muted">
          Cumulative Equity (R-Multiples)
        </h2>
        <EquityCurveChart trades={trades} />
      </div>

      {staged && staged.count > 0 && (
        <div className="flex items-center gap-2 text-xs text-hud-muted">
          <span className="font-bold text-hud-text">Execution Staging:</span>
          <span className="text-bull">{staged.ready} ready</span>
          <span>·</span>
          <span className="text-bear">{staged.blocked} blocked</span>
          <span>·</span>
          <span>{staged.count} staged tickets total</span>
        </div>
      )}

      {trades.length > 0 ? (
        <div>
          <h2 className="mb-3 text-xs font-bold uppercase tracking-wide text-hud-muted">
            Most Recent {trades.length} Entries
          </h2>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {trades.map((trade) => (
              <TradeCard key={trade.id} trade={trade} />
            ))}
          </div>
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-hud-border bg-hud-panel/40 px-6 py-16 text-center">
          <BookOpen className="h-8 w-8 text-hud-muted" />
          <h2 className="text-sm font-bold text-hud-text">Execution Log Ready</h2>
          <p className="max-w-md text-xs text-hud-muted">
            No paper trades have been journaled yet. Entries appear here as setups are logged from
            Sniper HUD or auto-logged from confirmed signals.
          </p>
        </div>
      )}
    </div>
  )
}
