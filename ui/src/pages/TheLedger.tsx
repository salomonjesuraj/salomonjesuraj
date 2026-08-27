import { BookOpen } from 'lucide-react'
import { ToolPageShell } from '../components/ToolPageShell'

/** `/journal` -- execution log + trade journal. Skeleton only; see
 * ToolPageShell's own note. */
export function TheLedger() {
  return (
    <ToolPageShell
      icon={BookOpen}
      title="The Ledger"
      subtitle="Execution log and trade journal — not yet wired to the backend"
      metrics={[
        { label: 'Open Positions' },
        { label: 'Closed Today' },
        { label: 'Realized P&L' },
        { label: 'Journal Entries' },
      ]}
      emptyStateTitle="Execution Log Ready"
      emptyStateBody="This view will list every executed trade alongside the trader's own journal notes once the execution and journal backend routes are wired up."
    />
  )
}
