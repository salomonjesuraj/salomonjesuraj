import { useEffect } from 'react'
import { Outlet } from 'react-router-dom'
import { useUpstoxAuthWatcher } from '../hooks/useUpstoxAuthWatcher'
import { isDemoMode, startDemoTicker, stopDemoTicker } from '../lib/demo'
import { connectTickSocket, useConnStatus } from '../store/useTickStore'
import { useUpstoxAuthStore } from '../store/useUpstoxAuthStore'
import { IndexPulseHeader } from './IndexPulseHeader'
import { Sidebar } from './Sidebar'
import { UpstoxTokenModal } from './UpstoxTokenModal'

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
 * Command Center app shell (2026-08-27 restructure).
 *
 * Sidebar + Zone 1 (sticky Live Index Pulse) live here, one level above
 * the router's per-route Outlet, so a trader never loses market
 * awareness while switching between Sniper HUD and the other tool
 * routes -- the same reasoning the original 4-Zone rebuild used for
 * keeping Zones 1/3/4 populated even when Zone 2 goes empty, just one
 * level up the tree now that there's more than one route. The demo-mode
 * ticker/socket-connect side effect and the connection-status badge are
 * global for the same reason: they describe the whole session's data
 * source, not any one page's content.
 */
export function Layout() {
  const demo = isDemoMode()
  const connStatus = useConnStatus()
  // Always called (rules-of-hooks) -- the modal itself is only rendered
  // outside demo mode below, since demo sessions don't depend on a real
  // broker connection and shouldn't be interrupted by one expiring.
  useUpstoxAuthWatcher()
  const isTokenModalOpen = useUpstoxAuthStore((s) => s.isTokenModalOpen)

  useEffect(() => {
    if (demo) {
      startDemoTicker()
      return () => stopDemoTicker()
    }
    connectTickSocket()
  }, [demo])

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
    <div className="flex min-h-screen bg-hud-bg">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        {demo && <DemoBanner />}
        <IndexPulseHeader statusBadge={statusBadge} />
        <main className="flex flex-1 flex-col gap-8 px-6 py-6">
          <Outlet />
        </main>
      </div>
      {!demo && isTokenModalOpen && <UpstoxTokenModal />}
    </div>
  )
}
