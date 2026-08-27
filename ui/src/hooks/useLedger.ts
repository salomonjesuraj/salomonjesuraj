import {
  fetchJournalExpectancy,
  fetchJournalStats,
  fetchJournalTrades,
  fetchStagedTickets,
} from '../lib/api'
import type { JournalExpectancy, JournalStats, JournalTrade, StagedTicketsResponse } from '../types'
import { usePolling } from './usePolling'

export interface LedgerData {
  trades: JournalTrade[]
  stats: JournalStats | null
  expectancy: JournalExpectancy
  staged: StagedTicketsResponse
}

/** The Ledger (`/journal`) data source -- 1min poll, matching how
 * infrequently a paper trade's own state actually changes compared to
 * live price/signal data elsewhere in this app. */
export function useLedger() {
  return usePolling<LedgerData>(
    async () => {
      const [trades, stats, expectancy, staged] = await Promise.all([
        fetchJournalTrades(40),
        fetchJournalStats(),
        fetchJournalExpectancy(),
        fetchStagedTickets(),
      ])
      return { trades, stats, expectancy, staged }
    },
    60000,
    [],
  )
}
