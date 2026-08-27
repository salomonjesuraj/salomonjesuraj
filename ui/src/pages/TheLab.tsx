import { FlaskConical } from 'lucide-react'
import { DASH, MetricCard } from '../components/MetricCard'
import { PageHeader } from '../components/PageHeader'
import { useLabData } from '../hooks/useLabData'
import type { BacktestBreakdownRow } from '../types'

function fmt(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || Number.isNaN(value) ? DASH : value.toFixed(digits)
}

function BreakdownGrid({ title, rows }: { title: string; rows: BacktestBreakdownRow[] }) {
  return (
    <div>
      <h3 className="mb-2 text-[10px] font-bold uppercase tracking-wide text-hud-muted">
        {title}
      </h3>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {rows.map((row) => (
          <div key={row.label} className="rounded-lg border border-hud-border bg-hud-bg/60 p-3">
            <div className="font-mono text-xs font-bold text-hud-text">{row.label}</div>
            <div className="tnum mt-1 text-lg font-bold text-hud-text">
              {fmt(row.precision_pct, 1)}
              {row.precision_pct !== null && '%'}
            </div>
            <div className="text-[10px] text-hud-muted">
              {row.wins}W / {row.losses}L of {row.total}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/** `/optimizer` -- The Lab, wired to real backend routes (2026-08-27
 * data-wiring sprint). `/api/backtest/summary` (Postgres-archived
 * outcomes, server-cached 90s) and `/api/backtest/optimizer-proposal/
 * latest` (last-written config comparison, no fresh sweep triggered
 * from here) -- deliberately NOT the full grid-search walk-forward
 * endpoint, which is expensive and meant for the scheduler's nightly
 * run, not a page-load poll. */
export function TheLab() {
  const { data } = useLabData()
  const summary = data?.summary
  const proposal = data?.optimizerProposal
  const sharpe = proposal?.recommended?.test_sharpe?.sharpe

  return (
    <div className="flex flex-1 flex-col gap-6">
      <PageHeader
        icon={FlaskConical}
        title="The Lab"
        subtitle={
          summary?.available
            ? `Last ${summary.days} days · ${summary.reliability ?? 'reliability unrated'}`
            : summary?.reason || 'Waiting for the backtest archive.'
        }
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard label="Decided Trades" value={summary?.decided?.toString() ?? DASH} />
        <MetricCard
          label="Win Rate"
          value={
            summary?.precision_pct !== null && summary?.precision_pct !== undefined
              ? `${fmt(summary.precision_pct)}%`
              : DASH
          }
          tone={
            summary?.precision_pct === null || summary?.precision_pct === undefined
              ? 'neutral'
              : summary.precision_pct >= 50
                ? 'good'
                : 'bad'
          }
        />
        <MetricCard
          label="Avg R:R"
          value={summary?.avg_rr !== null && summary?.avg_rr !== undefined ? `1:${fmt(summary.avg_rr, 2)}` : DASH}
        />
        <MetricCard
          label="Sharpe"
          value={fmt(sharpe, 3)}
          tone={sharpe === null || sharpe === undefined ? 'neutral' : sharpe > 0 ? 'good' : 'bad'}
          sublabel="Per-trade, R-multiple based"
        />
      </div>

      <div className="rounded-xl border border-hud-border bg-hud-panel p-4">
        <h2 className="text-xs font-bold uppercase tracking-wide text-hud-muted">
          Optimizer Proposal
        </h2>
        {proposal?.available && proposal.status && proposal.status !== 'NO_PROPOSAL' ? (
          <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <div className="text-[10px] uppercase tracking-wide text-hud-muted">Live Config</div>
              <div className="tnum mt-1 font-mono text-sm text-hud-text">
                min_score {fmt(proposal.live_config?.precision_guard_min_score, 1)} · min_rr{' '}
                {fmt(proposal.live_config?.precision_guard_min_rr, 2)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-hud-muted">
                Recommended
              </div>
              <div className="tnum mt-1 font-mono text-sm text-hud-text">
                min_score {fmt(proposal.recommended?.min_score, 1)} · min_rr{' '}
                {fmt(proposal.recommended?.min_rr, 2)}
              </div>
            </div>
            <div className="sm:col-span-2">
              <span
                className={
                  'rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ' +
                  (proposal.status === 'PROPOSED'
                    ? 'bg-horizon-btst/15 text-horizon-btst'
                    : 'bg-bull/10 text-bull')
                }
              >
                {proposal.status}
              </span>
              <p className="mt-2 text-xs leading-relaxed text-hud-muted">{proposal.note}</p>
            </div>
          </div>
        ) : (
          <p className="mt-3 text-xs text-hud-muted">
            {proposal?.reason ||
              'No profile currently clears its out-of-sample target -- nothing to propose yet.'}
          </p>
        )}
      </div>

      {summary?.available && (summary.by_grade?.length || summary.by_session?.length) ? (
        <div className="flex flex-col gap-4">
          {summary.by_grade && summary.by_grade.length > 0 && (
            <BreakdownGrid title="By Conviction Grade" rows={summary.by_grade} />
          )}
          {summary.by_session && summary.by_session.length > 0 && (
            <BreakdownGrid title="By Session" rows={summary.by_session} />
          )}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-hud-border bg-hud-panel/40 px-6 py-12 text-center">
          <FlaskConical className="h-6 w-6 text-hud-muted" />
          <p className="max-w-md text-xs text-hud-muted">
            {summary?.reason || 'Backtest Engine Ready -- no archived outcomes in this window yet.'}
          </p>
        </div>
      )}
    </div>
  )
}
