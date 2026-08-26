import { create } from 'zustand'
import type { TickBatchMessage } from '../types'

type ConnStatus = 'connecting' | 'connected' | 'disconnected'

interface TickState {
  /** symbol -> latest LTP. Every card/marker reads its OWN symbol's
   * price via a selector (see useLtp below) so a 5-ticks-per-second
   * update to RELIANCE only re-renders whatever is subscribed to
   * RELIANCE's slice of this map -- Zustand's default selector
   * equality (Object.is on the selected value) means a component that
   * selected `s.ticks['INFY']` never re-renders when this object's
   * RELIANCE key changes, even though the containing object itself is
   * a new reference every batch. */
  ticks: Record<string, number>
  changePct: Record<string, number>
  status: ConnStatus
  lastBatchAt: number
  _applyBatch: (msg: TickBatchMessage) => void
  _setStatus: (status: ConnStatus) => void
}

export const useTickStore = create<TickState>((set) => ({
  ticks: {},
  changePct: {},
  status: 'connecting',
  lastBatchAt: 0,
  _applyBatch: (msg) =>
    set((state) => {
      const ticks = { ...state.ticks }
      const changePct = { ...state.changePct }
      for (const [symbol, row] of Object.entries(msg.data)) {
        if (typeof row.ltp === 'number') ticks[symbol] = row.ltp
        if (typeof row.change_pct === 'number') changePct[symbol] = row.change_pct
      }
      return { ticks, changePct, lastBatchAt: msg.ts || Date.now() }
    }),
  _setStatus: (status) => set({ status }),
}))

/** Localized selector -- the one hook every price-displaying component
 * should use instead of reaching into the whole store. */
export function useLtp(symbol: string): number | undefined {
  return useTickStore((s) => s.ticks[symbol])
}

export function useChangePct(symbol: string): number | undefined {
  return useTickStore((s) => s.changePct[symbol])
}

export function useConnStatus(): ConnStatus {
  return useTickStore((s) => s.status)
}

let socket: WebSocket | null = null
let reconnectDelayMs = 1000
const MAX_RECONNECT_DELAY_MS = 30_000

/** Singleton WS connection, same /ws path + tick_batch protocol as the
 * legacy dashboard's ws.js (proxied to ws-gateway:8001). Call once from
 * App's top level; safe to call more than once (no-ops if already
 * open/connecting). */
export function connectTickSocket(): void {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return
  }
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${proto}//${window.location.host}/ws`
  useTickStore.getState()._setStatus('connecting')
  const ws = new WebSocket(url)
  socket = ws

  ws.onopen = () => {
    reconnectDelayMs = 1000
    useTickStore.getState()._setStatus('connected')
  }

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data)
      if (msg?.type === 'tick_batch' && msg.data) {
        useTickStore.getState()._applyBatch(msg as TickBatchMessage)
      }
    } catch {
      // A malformed frame just gets dropped -- never crash the socket
      // handler over one bad message.
    }
  }

  ws.onclose = () => {
    useTickStore.getState()._setStatus('disconnected')
    socket = null
    window.setTimeout(connectTickSocket, reconnectDelayMs)
    reconnectDelayMs = Math.min(reconnectDelayMs * 2, MAX_RECONNECT_DELAY_MS)
  }

  ws.onerror = () => {
    ws.close()
  }
}
