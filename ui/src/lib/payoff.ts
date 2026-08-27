import type { StrategyLeg } from '../types'

export interface PayoffPoint {
  price: number
  pnl: number
}

const STEPS = 60

/** Real payoff-at-expiration math over a ranked strategy's own legs
 * (strike/premium/action/type, exactly as api/options_strategies.py
 * returned them) -- max(0, S-K)/max(0, K-S) intrinsic value per leg,
 * summed, net of premium paid/received. Per-unit rupee terms, same
 * convention the backend's own max_profit/max_loss/net_debit already
 * use (no lot-size multiplier) -- this is a deterministic transform of
 * real numbers already on the page, not a new data source. Pure
 * function, kept out of PayoffChart.tsx so that component file can stay
 * fast-refresh-clean (react/only-export-components) and so this is
 * independently unit-testable. */
export function computePayoffCurve(legs: StrategyLeg[], spot: number): PayoffPoint[] {
  if (legs.length === 0) return []
  const strikes = legs.map((l) => l.strike)
  const lo = Math.min(spot, ...strikes) * 0.85
  const hi = Math.max(spot, ...strikes) * 1.15
  const points: PayoffPoint[] = []
  for (let i = 0; i <= STEPS; i++) {
    const price = lo + ((hi - lo) * i) / STEPS
    let pnl = 0
    for (const leg of legs) {
      const intrinsic = leg.type === 'CE' ? Math.max(0, price - leg.strike) : Math.max(0, leg.strike - price)
      pnl += leg.action === 'BUY' ? intrinsic - leg.premium : leg.premium - intrinsic
    }
    points.push({ price: Math.round(price * 100) / 100, pnl: Math.round(pnl * 100) / 100 })
  }
  return points
}
