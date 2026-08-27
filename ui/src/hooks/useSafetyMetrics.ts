import { fetchAlertLog, fetchHealth, fetchSafetyStatus } from '../lib/api'
import type { AlertLogEntry, HealthStatus, SafetyStatus } from '../types'
import { usePolling } from './usePolling'

export interface SafetyData {
  status: SafetyStatus
  health: HealthStatus
  alertLog: AlertLogEntry[]
}

/** Safety & Logs (`/safety`) data source -- 5s poll, the fastest of the
 * four Command Center tool routes since this is the one a trader needs
 * to catch a state change on quickly (a gate flipping to `block`, a
 * service heartbeat going stale). All three reads are cheap Redis gets. */
export function useSafetyMetrics() {
  return usePolling<SafetyData>(
    async () => {
      const [status, health, alertLog] = await Promise.all([
        fetchSafetyStatus(),
        fetchHealth(),
        fetchAlertLog(),
      ])
      return { status, health, alertLog }
    },
    5000,
    [],
  )
}
