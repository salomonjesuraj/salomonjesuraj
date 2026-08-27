import { LineChart } from 'lucide-react'
import { ToolPageShell } from '../components/ToolPageShell'

/** `/analytics` -- Greeks exposure + strategy selector. Skeleton only;
 * see ToolPageShell's own note. */
export function OptionsAnalytics() {
  return (
    <ToolPageShell
      icon={LineChart}
      title="Options Analytics"
      subtitle="Greeks exposure and strategy selection — not yet wired to the backend"
      metrics={[
        { label: 'Net Delta' },
        { label: 'Net Gamma' },
        { label: 'Net Theta' },
        { label: 'Net Vega' },
      ]}
      emptyStateTitle="Strategy Selector Ready"
      emptyStateBody="This view will surface live Greeks exposure per open position and a payoff-diagram strategy selector once the options-analytics backend route is wired up."
    />
  )
}
