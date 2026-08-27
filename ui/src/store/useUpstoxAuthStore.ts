import { create } from 'zustand'

interface UpstoxAuthModalState {
  isTokenModalOpen: boolean
  openTokenModal: () => void
  closeTokenModal: () => void
}

/**
 * "Telegram Redesign & Token Modal" sprint (2026-08-27) -- the global
 * switch UpstoxTokenModal.tsx renders from and useUpstoxAuthWatcher.ts
 * (polling the real GET /api/auth/upstox/status) flips, so the modal
 * can auto-open from any route the same way useUiEngineStore's own
 * cross-route session state already works in this app. Session-local,
 * same as that store -- there's nothing to persist here, the next poll
 * always re-derives the real truth from the backend.
 */
export const useUpstoxAuthStore = create<UpstoxAuthModalState>((set) => ({
  isTokenModalOpen: false,
  openTokenModal: () => set({ isTokenModalOpen: true }),
  closeTokenModal: () => set({ isTokenModalOpen: false }),
}))
