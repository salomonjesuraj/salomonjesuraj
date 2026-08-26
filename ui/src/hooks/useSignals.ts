import { fetchSignals } from '../lib/api'
import { usePolling } from './usePolling'

/** Polls the active-signal list. 3s cadence matches the legacy
 * dashboard's own /api/signals poll interval (api.js subscribe calls). */
export function useSignals() {
  return usePolling(fetchSignals, 3000, [])
}
