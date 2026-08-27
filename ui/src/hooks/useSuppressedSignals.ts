import { fetchSuppressedSignals } from '../lib/api'
import { usePolling } from './usePolling'

/** Polls the suppressed-signal list -- "Probabilistic Grading and Warning
 * Tags" (2026-08-27): Zone 2 now merges this with useSignals()'s active
 * list (see lib/candidates.ts's mergeCandidates) so a candidate scoring
 * >= 65 but below the real 80.0 publish floor still reaches the trader,
 * carrying its own warning_tags rather than being hidden outright. Same
 * 3s cadence as useSignals -- both lists back the identical Zone 2 grid,
 * so they should never visibly lag one another. */
export function useSuppressedSignals() {
  return usePolling(fetchSuppressedSignals, 3000, [])
}
