import type { LucideIcon } from 'lucide-react'

export interface MetricPlaceholder {
  label: string
}

interface ToolPageShellProps {
  icon: LucideIcon
  title: string
  subtitle: string
  metrics: MetricPlaceholder[]
  emptyStateTitle: string
  emptyStateBody: string
}

/**
 * Shared skeleton for the four not-yet-wired Command Center tool routes
 * (Options Analytics, The Lab, The Ledger, Safety & Logs) -- 2026-08-27
 * restructure. Built once rather than duplicated four times since all
 * four ship in the same commit with an identical shape: a header, a row
 * of metric placeholders, and one empty-state card describing what lands
 * here next sprint. Every metric value is a literal "—" on purpose --
 * never a fabricated number standing in for real backend data that
 * doesn't exist yet. No spreadsheet-style tables here, per this
 * restructure's own Phase 4 rule; a real data grid (if one is ever
 * needed) is a decision for whoever wires up the real backend route.
 */
export function ToolPageShell({
  icon: Icon,
  title,
  subtitle,
  metrics,
  emptyStateTitle,
  emptyStateBody,
}: ToolPageShellProps) {
  return (
    <section className="flex flex-1 flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-hud-panel ring-1 ring-hud-border">
          <Icon className="h-5 w-5 text-hud-muted" />
        </div>
        <div>
          <h1 className="text-sm font-bold uppercase tracking-wide text-hud-text">{title}</h1>
          <p className="text-xs text-hud-muted">{subtitle}</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {metrics.map(({ label }) => (
          <div key={label} className="rounded-xl border border-hud-border bg-hud-panel p-4">
            <div className="text-[10px] font-bold uppercase tracking-wide text-hud-muted">
              {label}
            </div>
            <div className="tnum mt-2 font-mono text-2xl font-bold text-hud-muted">—</div>
          </div>
        ))}
      </div>

      <div className="flex flex-1 flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-hud-border bg-hud-panel/40 px-6 py-16 text-center">
        <Icon className="h-8 w-8 text-hud-muted" />
        <h2 className="text-sm font-bold text-hud-text">{emptyStateTitle}</h2>
        <p className="max-w-md text-xs text-hud-muted">{emptyStateBody}</p>
      </div>
    </section>
  )
}
