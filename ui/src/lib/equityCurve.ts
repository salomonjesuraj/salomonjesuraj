import type { JournalTrade } from '../types'

// "Visual Tracking & Lifecycle" sprint (2026-08-27): WIN_T1/T2/T3 are
// api/lifecycle_monitor.py's own real outcome vocabulary, written once
// its background sweep actually resolves a trade against real 1-min
// bars -- WIN/TARGET/T1/T2 stay for rows closed the old manual way
// (still a live code path, see journal.py's /outcome route). MISSED is
// deliberately absent from both sets: it's neither a win nor a loss,
// just a setup that never resolved either way -- the existing `r ===
// null` skip below already handles it correctly with no code change.
const WIN_OUTCOMES = new Set(['WIN_T1', 'WIN_T2', 'WIN_T3', 'WIN', 'TARGET', 'T1', 'T2'])
const LOSS_OUTCOMES = new Set(['LOSS', 'STOP', 'SL'])

const TARGET_FIELD: Record<string, keyof JournalTrade> = {
  WIN_T1: 'target1',
  WIN_T2: 'target2',
  WIN_T3: 'target3',
}

/** The real R-multiple a WIN_T1/T2/T3 outcome actually banked --
 * (target reached - entry) / (entry - stop), using the SAME real
 * target/entry/stop prices already on the row, rather than a synthetic
 * "T2 = 1.5x, T3 = 2x" multiplier on top of rr1 (which is only ever
 * T1's own risk:reward). Falls back to rr1 (or 1) when the specific
 * target field is missing/zero -- the same honest-fallback shape the
 * rest of this file already uses, not a fabricated number. */
function realizedR(trade: JournalTrade, outcome: string): number {
  const field = TARGET_FIELD[outcome]
  const risk = Math.abs(trade.entry - trade.stop)
  const targetPrice = field ? (trade[field] as number | undefined) : undefined
  if (field && targetPrice && risk > 0) {
    return Math.abs(targetPrice - trade.entry) / risk
  }
  return trade.rr1 > 0 ? trade.rr1 : 1
}

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
    if (WIN_OUTCOMES.has(outcome)) r = realizedR(trade, outcome)
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
