import { create } from 'zustand'

interface BrokerRefreshState {
  /** Bumped whenever something wants every broker polling hook to
   * refetch right now instead of waiting for its own interval --
   * useBrokerState.ts's three hooks all include this in usePolling's
   * dependency array, so a bump tears down and immediately re-runs
   * their fetch effect (the exact mechanism usePolling already uses for
   * a symbol/deps change), with no separate "refetch()" API needed on
   * usePolling itself. */
  nonce: number
  triggerBrokerRefresh: () => void
}

/**
 * "Telegram Redesign & Token Modal" sprint (2026-08-27) -- lets
 * UpstoxTokenModal.tsx force an immediate broker-data refresh right
 * after a token save, rather than waiting up to useActivePositions'
 * own 3s cadence. The modal itself doesn't hold a reference to any of
 * useBrokerState.ts's three hook instances (it's mounted globally in
 * Layout.tsx, not inside ActiveCockpit) -- this store is the shared
 * signal between the two.
 */
export const useBrokerRefreshStore = create<BrokerRefreshState>((set) => ({
  nonce: 0,
  triggerBrokerRefresh: () => set((s) => ({ nonce: s.nonce + 1 })),
}))
