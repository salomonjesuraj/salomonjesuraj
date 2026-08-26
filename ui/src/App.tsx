import { useEffect } from 'react'
import { ActionCard } from './components/ActionCard'
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

function EmptyState() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 py-24 text-center">
      <div className="text-5xl">◎</div>
      <h2 className="text-lg font-semibold text-hud-text">Awaiting High Conviction Setups</h2>
      <p className="max-w-md text-sm text-hud-muted">
        No active signal currently clears the conviction bar. That's the anti-noise design working
        as intended, not a loading state — the HUD only ever shows a setup worth acting on.
      </p>
    </div>
  )
}

function DemoBanner() {
  return (
    <div className="border-b border-horizon-btst/40 bg-horizon-btst/10 px-6 py-2 text-center text-xs font-bold uppercase tracking-wide text-horizon-btst">
      Demo Mode — every card below is simulated data, not a real signal
    </div>
  )
}

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

  return (
    <div className="flex min-h-screen flex-col bg-hud-bg">
      {demo && <DemoBanner />}

      <header className="flex items-center justify-between border-b border-hud-border px-6 py-4">
        <div className="flex items-baseline gap-2">
          <h1 className="font-mono text-sm font-bold uppercase tracking-[0.2em] text-hud-text">
            Sniper HUD
          </h1>
          <span className="text-xs text-hud-muted">Pre-Breakout Decision Support</span>
        </div>
        {demo ? (
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
        )}
      </header>

      <main className="flex flex-1 flex-col px-6 py-6">
        {activeSignals.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {activeSignals.map((signal) => (
              <ActionCard key={`${signal.symbol}:${signal.strategy_id}`} signal={signal} />
            ))}
          </div>
        )}
      </main>
    </div>
  )
}

export default App
