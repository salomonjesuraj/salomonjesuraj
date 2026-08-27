import { useEffect } from 'react'
import { fetchUpstoxAuthStatus } from '../lib/api'
import { useUpstoxAuthStore } from '../store/useUpstoxAuthStore'
import { usePolling } from './usePolling'

const POLL_MS = 15000

/**
 * "Telegram Redesign & Token Modal" sprint (2026-08-27) -- mounted once,
 * globally, in Layout.tsx. Polls the real, already-shipped GET
 * /api/auth/upstox/status every 15s and keeps useUpstoxAuthStore's
 * isTokenModalOpen in sync with that endpoint's own `needs_token`
 * verdict -- the literal ask was "catch 401/TOKEN_EXPIRED from broker
 * endpoints," but this backend's broker routes (api/broker_sync.py's
 * own _get_upstox()) never actually surface a real HTTP 401 or a
 * TOKEN_EXPIRED payload shape; a token problem comes back as a normal
 * 200 with `{"available": false, "reason": "...expired..."}}`, which
 * would mean pattern-matching error strings across three separate
 * fetch call sites to reconstruct what this one purpose-built status
 * endpoint already answers directly. 15s keeps the modal's reaction
 * fast without adding real load next to positions' own 3s poll.
 */
export function useUpstoxAuthWatcher(): void {
  const { data } = usePolling(fetchUpstoxAuthStatus, POLL_MS, [])
  const openTokenModal = useUpstoxAuthStore((s) => s.openTokenModal)
  const closeTokenModal = useUpstoxAuthStore((s) => s.closeTokenModal)

  useEffect(() => {
    if (!data) return
    if (data.needs_token) openTokenModal()
    else closeTokenModal()
  }, [data, openTokenModal, closeTokenModal])
}
