import { ShieldAlert } from 'lucide-react'
import { ToolPageShell } from '../components/ToolPageShell'

/** `/safety` -- system alerts, feed health, rejection audit trail.
 * Skeleton only; see ToolPageShell's own note. */
export function SafetyLogs() {
  return (
    <ToolPageShell
      icon={ShieldAlert}
      title="Safety & Logs"
      subtitle="System alerts and integrity checks — not yet wired to the backend"
      metrics={[
        { label: 'Active Alerts' },
        { label: 'Feed Health' },
        { label: 'Last Heartbeat' },
        { label: 'Error Rate' },
      ]}
      emptyStateTitle="Safety Console Ready"
      emptyStateBody="This view will surface feed-staleness warnings, the rejected-signal audit trail, and system health checks once the safety backend routes are wired up."
    />
  )
}
