import type { JournalTrade } from '../types'

const WIN_OUTCOMES = new Set(['WIN', 'TARGET', 'T1', 'T2'])
const LOSS_OUTCOMES = new Set(['LOSS', 'STOP', 'SL'])

export interface EquityPoint {
  index: number
  cumulativeR: number
  symbol: string
  outcome: string
  r: number
  closedAt: string
}

/** Builds a cumulative R-multiple equity curve from real closed journal
 * trades -- a simplified reconstruction of the same win/loss R-multiple
 * convention /api/journal/expectancy computes server-side (WIN outcomes
 * contribute their planned rr1, LOSS outcomes contribute -1), but
 * WITHOUT that endpoint's cost-drag adjustment (per-trade option
 * total_costs aren't part of the JournalTrade rows already fetched for
 * the Trade Card grid below, and re-fetching just for this chart isn't
 * worth a second round trip). The authoritative expectancy_r metric
 * card above this chart still comes from the real backend endpoint;
 * this curve is for shape/trend, not a second source of truth for the
 * headline number. Pure function, kept out of EquityCurveChart.tsx so
 * that component file can stay fast-refresh-clean (react/only-export-
 * components) and so this is independently unit-testable. */
export function buildEquityCurve(trades: JournalTrade[]): EquityPoint[] {
  const closed = trades
    .filter((t) => t.status === 'CLOSED' && t.outcome)
    .slice()
    .reverse() // fetched newest-first; chronological (oldest -> newest) for a left-to-right curve

  let cumulative = 0
  const points: EquityPoint[] = [
    { index: 0, cumulativeR: 0, symbol: 'Start', outcome: '', r: 0, closedAt: '' },
  ]
  for (const trade of closed) {
    const outcome = (trade.outcome || '').toUpperCase()
    let r: number | null = null
    if (WIN_OUTCOMES.has(outcome)) r = trade.rr1 > 0 ? trade.rr1 : 1
    else if (LOSS_OUTCOMES.has(outcome)) r = -1
    if (r === null) continue
    cumulative = Math.round((cumulative + r) * 1000) / 1000
    points.push({
      index: points.length,
      cumulativeR: cumulative,
      symbol: trade.symbol,
      outcome,
      r,
      closedAt: trade.closed_at_ist || '',
    })
  }
  return points
}

/** Parses journal.py's `_now_ist()` format ("YYYY-MM-DD HH:MM:SS IST")
 * into a Date. Display-only (chart tick/tooltip labels), so the IST
 * offset itself isn't reconstructed -- only relative ordering and a
 * readable label matter here, and the trades array is already in the
 * correct chronological order once reversed. */
export function parseIstTimestamp(value: string): Date | null {
  if (!value) return null
  const iso = value.replace(' IST', '').replace(' ', 'T')
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d
}
