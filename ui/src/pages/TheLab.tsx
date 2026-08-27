import { FlaskConical } from 'lucide-react'
import { ToolPageShell } from '../components/ToolPageShell'

/** `/optimizer` -- backtest engine + conviction-model optimizer.
 * Skeleton only; see ToolPageShell's own note. */
export function TheLab() {
  return (
    <ToolPageShell
      icon={FlaskConical}
      title="The Lab"
      subtitle="Backtesting and conviction-model optimizer — not yet wired to the backend"
      metrics={[
        { label: 'Backtests Run' },
        { label: 'Win Rate' },
        { label: 'Avg R:R' },
        { label: 'Sharpe' },
      ]}
      emptyStateTitle="Backtest Engine Ready"
      emptyStateBody="This view will run historical calibration against the live scoring model -- the 2026-08-27 Probabilistic Grading pivot's own disclosed gap, no backtest has validated the current weights yet -- and surface optimizer sweep results here."
    />
  )
}
