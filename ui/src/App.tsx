import { useEffect } from 'react'
import { ActionCard } from './components/ActionCard'
import { IndexPulseHeader } from './components/IndexPulseHeader'
import { PreBreakoutWatchlist } from './components/PreBreakoutWatchlist'
import { RadarScanningStrip } from './components/RadarScanningStrip'
import { SmartMoneyRadar } from './components/SmartMoneyRadar'
import { useSignals } from './hooks/useSignals'
import { isDemoMode, startDemoTicker, stopDemoTicker } from './lib/demo'
import { connectTickSocket, useConnStatus } from './store/useTickStore'

const STATUS_LABEL: Record<string, string> = {
  connecting: 'Connecting…',
  connected: 'Live',
  disconnected: 'Reconnecting…',
}

const STATUS_CLASS: Record<string, string> = {
  connecting: 'bg-horizon-btst/15 text-horizon-btst',
  connected: 'bg-bull/15 text-bull',
  disconnected: 'bg-bear/15 text-bear',
}

function DemoBanner() {
  return (
    <div className="border-b border-horizon-btst/40 bg-horizon-btst/10 px-6 py-2 text-center text-xs font-bold uppercase tracking-wide text-horizon-btst">
      Demo Mode — every card below is simulated data, not a real signal
    </div>
  )
}

/**
 * 4-Zone Trading Command Screen (2026-08-27 rebuild).
 *
 * The old version rendered a single empty screen whenever nothing
 * cleared the 80.0 conviction floor -- which per this project's own
 * live audits is most of most trading days. Zone 2 alone used to BE
 * the whole page; it's now one section of four, and the only one that
 * ever goes empty (replaced by RadarScanningStrip when it does) --
 * Zones 1, 3, and 4 are built from data sources that are populated
 * essentially always (index prices, the 208-symbol tick universe,
 * pre-breakout watchlist), so the screen has structured market
 * awareness on it at every moment regardless of whether a real signal
 * exists.
 */
function App() {
  const demo = isDemoMode()
  const connStatus = useConnStatus()
  const { data: signals } = useSignals()

  useEffect(() => {
    if (demo) {
      startDemoTicker()
      return () => stopDemoTicker()
    }
    connectTickSocket()
  }, [demo])

  const activeSignals = signals ?? []

  const statusBadge = demo ? (
    <span className="rounded-full bg-horizon-btst/15 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-horizon-btst">
      Demo
    </span>
  ) : (
    <span
      className={
        'rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ' +
        STATUS_CLASS[connStatus]
      }
    >
      {STATUS_LABEL[connStatus]}
    </span>
  )

  return (
    <div className="flex min-h-screen flex-col bg-hud-bg">
      {demo && <DemoBanner />}

      {/* Zone 1 */}
      <IndexPulseHeader statusBadge={statusBadge} />

      <main className="flex flex-1 flex-col gap-8 px-6 py-6">
        {/* Zone 2 */}
        <section>
          <h2 className="mb-3 text-xs font-bold uppercase tracking-wide text-hud-muted">
            Live Action Breakout
          </h2>
          {activeSignals.length === 0 ? (
            <RadarScanningStrip />
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {activeSignals.map((signal) => (
                <ActionCard key={`${signal.symbol}:${signal.strategy_id}`} signal={signal} />
              ))}
            </div>
          )}
        </section>

        {/* Zone 3 */}
        <SmartMoneyRadar />

        {/* Zone 4 */}
        <PreBreakoutWatchlist />
      </main>
    </div>
  )
}

export default App
