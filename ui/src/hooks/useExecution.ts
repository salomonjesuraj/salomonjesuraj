import { useState } from 'react'
import { fetchRiskSettings, logJournalTrade, stageExecutionTicket } from '../lib/api'
import type { ExecutionTicket } from '../types'

export type ExecutionStatus = 'idle' | 'staging' | 'staged' | 'error'

export interface StageParams {
  symbol: string
  decision: string
  entry: number
  stop: number
  target1: number
  option?: Record<string, unknown>
}

export interface UseExecutionResult {
  status: ExecutionStatus
  ticket: ExecutionTicket | null
  error: string | null
  stage: (params: StageParams) => Promise<void>
  reset: () => void
}

/**
 * 1-click execution hook ("Terminal Edge" sprint, 2026-08-27) -- stages
 * a PAPER order ticket via the real, already-shipped
 * POST /api/execution/stage. Deliberately does NOT call a live order-
 * placement endpoint: this backend's entire execution architecture is
 * paper-first by explicit design (api/routes/execution.py's own module
 * docstring -- "builds a broker-style order ticket... but does not
 * place orders"; api/routes/safety.py's paper_first gate; journal.py's
 * "intentionally paper-only in this phase"). A real live-broker
 * "execute this trade" capability is a financial-trade-execution
 * feature this pass does not add -- see the sprint's own summary for
 * why. Fetches risk settings at click time (not polled) purely to size
 * the ticket with a real risk_amount rather than leaving it at 0,
 * which would otherwise make every ticket come back BLOCKED on
 * "Risk amount is not set" regardless of setup quality.
 */
export function useExecution(): UseExecutionResult {
  const [status, setStatus] = useState<ExecutionStatus>('idle')
  const [ticket, setTicket] = useState<ExecutionTicket | null>(null)
  const [error, setError] = useState<string | null>(null)

  const stage = async (params: StageParams) => {
    setStatus('staging')
    setError(null)
    try {
      const risk = await fetchRiskSettings()
      const result = await stageExecutionTicket({
        symbol: params.symbol,
        decision: params.decision,
        entry: params.entry,
        stop: params.stop,
        target1: params.target1,
        risk_amount: risk.risk_per_trade_amount,
        option: params.option ?? {},
      })
      setTicket(result)
      setStatus('staged')

      // Phase 3 wiring: only log genuinely ready tickets to the journal
      // (The Ledger) -- a BLOCKED attempt was never a real position to
      // draw an overlay for. Best-effort: the staged ticket above is
      // already the source of truth for what the trader just saw, so a
      // journal-logging failure is swallowed, not surfaced as the
      // execution itself failing.
      if (result.status === 'READY_TO_STAGE') {
        try {
          await logJournalTrade({
            symbol: params.symbol,
            decision: params.decision,
            entry: params.entry,
            stop: params.stop,
            target1: params.target1,
            source: 'sniper_hud_1click',
            selected_option: params.option ?? {},
            risk: { riskAmount: risk.risk_per_trade_amount },
          })
        } catch {
          // Non-fatal -- see docstring above.
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setStatus('error')
    }
  }

  const reset = () => {
    setStatus('idle')
    setTicket(null)
    setError(null)
  }

  return { status, ticket, error, stage, reset }
}
