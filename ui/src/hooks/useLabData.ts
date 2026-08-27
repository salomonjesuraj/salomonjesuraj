import { fetchBacktestSummary, fetchOptimizerProposal } from '../lib/api'
import type { BacktestSummary, OptimizerProposal } from '../types'
import { usePolling } from './usePolling'

export interface LabData {
  summary: BacktestSummary
  optimizerProposal: OptimizerProposal
}

/** The Lab (`/optimizer`) data source -- 30s poll. `/api/backtest/summary`
 * is already server-cached for 90s and `/api/backtest/optimizer-proposal/
 * latest` only reads the last-written record (no fresh walk-forward
 * sweep triggered from here), so 30s is just a client refresh cadence
 * against cheap reads, not new backend load. */
export function useLabData() {
  return usePolling<LabData>(
    async () => {
      const [summary, optimizerProposal] = await Promise.all([
        fetchBacktestSummary(60),
        fetchOptimizerProposal(),
      ])
      return { summary, optimizerProposal }
    },
    30000,
    [],
  )
}
