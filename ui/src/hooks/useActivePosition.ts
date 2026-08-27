import { fetchJournalTrades } from '../lib/api'
import type { JournalTrade } from '../types'
import { usePolling } from './usePolling'

export interface ActivePosition {
  entry: number
  stop: number
  target1: number
  decision: string
}

// PLANNED/WATCH = still open per journal.py's own status machine
// (_normalise_trade sets PLANNED/WATCH/BLOCKED at creation,
// update_journal_outcome flips to CLOSED on resolution) -- BLOCKED
// never had a real entry to draw a line at, and CLOSED is history, not
// an active position.
const ACTIVE_STATUSES = new Set(['PLANNED', 'WATCH'])

/** Active Trade Chart Overlay ("Terminal Edge" sprint, 2026-08-27) --
 * finds this symbol's most recent still-open real journal entry to draw
 * ENTRY/STOP LOSS/TARGET lines on the candlestick chart. Polls the same
 * real GET /api/journal/trades The Ledger page already uses (no new
 * backend route); fetches once regardless of `symbol` and filters
 * client-side, since switching the charted symbol shouldn't need a
 * fresh network round trip just to look at data already in hand. Only
 * searches the most recent 100 rows -- an older untouched position
 * could scroll out of that window, same "most recent N" limitation The
 * Ledger's own trade grid already has, not a new one. */
export function useActivePosition(symbol: string | null): ActivePosition | null {
  const { data: trades } = usePolling(() => fetchJournalTrades(100), 10000, [])

  const position = (trades ?? []).find(
    (t: JournalTrade) => t.symbol === symbol && ACTIVE_STATUSES.has(t.status),
  )

  if (!position) return null
  return {
    entry: position.entry,
    stop: position.stop,
    target1: position.target1,
    decision: position.decision,
  }
}
