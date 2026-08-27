import type { SignalRow, SuppressedSignalRow } from '../types'

/**
 * "Probabilistic Grading and Warning Tags" (2026-08-27): Zone 2 now
 * shows every candidate scoring >= DISPLAY_FLOOR, not just the ones
 * that cleared the real 80.0 publish floor -- SignalRow (active) and
 * SuppressedSignalRow (merely suppressed, but possibly still worth the
 * trader's own judgment) have different field names for the identical
 * concepts (entry vs. entry_price, score vs. conviction_score), so
 * this normalizes both into one shape ActionCard renders uniformly.
 */
export const DISPLAY_FLOOR = 65.0

export interface Candidate {
  symbol: string
  strategyId: string
  signalType: 'bullish' | 'bearish' | string
  probability: number
  grade?: string
  entryPrice: number
  invalidationPrice: number
  targetPrice: number
  t2Price?: number
  riskRewardRatio?: number
  obFvgDistancePct: number | null
  warningTags: string[]
  /** Whether this is a real, officially-published signal (cleared the
   * 80.0 floor) or a merely-suppressed-but-scoring candidate the
   * probabilistic display now also surfaces. Never hidden from the
   * trader either way -- shown so the distinction itself isn't lost. */
  isOfficial: boolean
}

function fromActive(row: SignalRow): Candidate {
  return {
    symbol: row.symbol,
    strategyId: row.strategy_id,
    signalType: row.signal_type,
    probability: row.conviction_score ?? 0,
    grade: row.conviction_grade,
    entryPrice: row.entry_price ?? 0,
    invalidationPrice: row.invalidation_price ?? 0,
    targetPrice: row.target_price ?? 0,
    t2Price: row.t2_price,
    riskRewardRatio: row.risk_reward_ratio,
    obFvgDistancePct: row.ob_fvg_distance_pct ?? null,
    warningTags: row.warning_tags ?? [],
    isOfficial: true,
  }
}

function fromSuppressed(row: SuppressedSignalRow): Candidate {
  return {
    symbol: row.symbol,
    strategyId: row.strategy_id,
    signalType: row.signal_type ?? (row.side?.includes('PE') ? 'bearish' : 'bullish'),
    probability: row.score ?? 0,
    grade: row.grade,
    entryPrice: row.entry ?? 0,
    invalidationPrice: row.stop ?? 0,
    targetPrice: row.target ?? 0,
    riskRewardRatio: row.rr,
    obFvgDistancePct: row.ob_fvg_distance_pct ?? null,
    warningTags: row.warning_tags ?? [],
    isOfficial: false,
  }
}

/** Merges active + suppressed rows into one probability-sorted list,
 * filtered to DISPLAY_FLOOR and above. A symbol appearing in both
 * (shouldn't happen -- active and suppressed are mutually exclusive
 * lifecycle states -- but defensive) keeps its active/official copy. */
export function mergeCandidates(
  active: SignalRow[],
  suppressed: SuppressedSignalRow[],
): Candidate[] {
  const bySymbol = new Map<string, Candidate>()
  for (const row of suppressed) {
    const c = fromSuppressed(row)
    if (c.probability >= DISPLAY_FLOOR) bySymbol.set(c.symbol, c)
  }
  for (const row of active) {
    const c = fromActive(row)
    if (c.probability >= DISPLAY_FLOOR) bySymbol.set(c.symbol, c)
  }
  return Array.from(bySymbol.values()).sort((a, b) => b.probability - a.probability)
}
