import { X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { ActionCard } from '../components/ActionCard'
import { LiveCandlestickChart } from '../components/LiveCandlestickChart'
import { OrderBookLadder } from '../components/OrderBookLadder'
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
  // Charting sprint (2026-08-27): clicking an Action Card opens a live
  // candlestick chart for that symbol right here, in flow, rather than
  // sending the trader to TradingView. A dedicated pane between Zone 2
  // and Zone 3 rather than a modal -- it stays visible alongside the
  // rest of the HUD's market awareness instead of covering it, matching
  // this whole page's own "never hide the rest of the screen" ethos.
  const [activeSymbol, setActiveSymbol] = useState<string | null>(null)

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
                isActive={candidate.symbol === activeSymbol}
                onSelect={(symbol) =>
                  setActiveSymbol((current) => (current === symbol ? null : symbol))
                }
              />
            ))}
          </div>
        )}
      </section>

      {/* Candlestick pane -- only mounted while a symbol is selected, so
          idle Sniper HUD sessions never pay for a chart nobody asked
          for. */}
      {activeSymbol && (
        <section className="rounded-xl border border-hud-border bg-hud-panel p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-xs font-bold uppercase tracking-wide text-hud-muted">
              {activeSymbol} · 1-Min Chart
            </h2>
            <button
              type="button"
              onClick={() => setActiveSymbol(null)}
              aria-label="Close chart"
              className="rounded p-1 text-hud-muted transition-colors hover:bg-hud-panel-hover hover:text-hud-text"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-4">
            <div className="lg:col-span-3">
              <LiveCandlestickChart symbol={activeSymbol} />
            </div>
            <div className="lg:col-span-1">
              <OrderBookLadder symbol={activeSymbol} />
            </div>
          </div>
        </section>
      )}

      {/* Zone 3 */}
      <SmartMoneyRadar />

      {/* Zone 4 */}
      <PreBreakoutWatchlist />
    </div>
  )
}
