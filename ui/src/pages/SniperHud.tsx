import { useMemo } from 'react'
import { ActionCard } from '../components/ActionCard'
import { PreBreakoutWatchlist } from '../components/PreBreakoutWatchlist'
import { RadarScanningStrip } from '../components/RadarScanningStrip'
import { SmartMoneyRadar } from '../components/SmartMoneyRadar'
import { useSignals } from '../hooks/useSignals'
import { useSuppressedSignals } from '../hooks/useSuppressedSignals'
import { mergeCandidates } from '../lib/candidates'

/**
 * Sniper HUD (`/`) -- the original 4-Zone Trading Command Screen, now
 * one route among five in the unified Command Center rather than the
 * whole app (2026-08-27 restructure). Zone 1 (sticky Live Index Pulse)
 * moved up into Layout.tsx since it has to stay visible across every
 * route; Zones 2-4 (Live Action Breakout, Smart Money Radar,
 * Pre-Breakout Watchlist) are this page's own content, unchanged from
 * the standalone-app version.
 */
export function SniperHud() {
  const { data: signals } = useSignals()
  const { data: suppressed } = useSuppressedSignals()

  // "Probabilistic Grading and Warning Tags" (2026-08-27): shows every
  // candidate >= DISPLAY_FLOOR (65), not just the ones that cleared the
  // real 80.0 publish floor -- see lib/candidates.ts. Depends on
  // `signals`/`suppressed` directly rather than a `?? []`-derived local
  // (a fresh array every render) so the memo actually memoizes.
  const candidates = useMemo(
    () => mergeCandidates(signals ?? [], suppressed ?? []),
    [signals, suppressed],
  )

  return (
    <div className="flex flex-col gap-8">
      {/* Zone 2 */}
      <section>
        <h2 className="mb-3 text-xs font-bold uppercase tracking-wide text-hud-muted">
          Live Action Breakout
        </h2>
        {candidates.length === 0 ? (
          <RadarScanningStrip />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {candidates.map((candidate) => (
              <ActionCard
                key={`${candidate.symbol}:${candidate.strategyId}`}
                candidate={candidate}
              />
            ))}
          </div>
        )}
      </section>

      {/* Zone 3 */}
      <SmartMoneyRadar />

      {/* Zone 4 */}
      <PreBreakoutWatchlist />
    </div>
  )
}
