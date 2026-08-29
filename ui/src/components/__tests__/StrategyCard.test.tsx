import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StrategyCard } from '../StrategyCard'
import type { RankedStrategy } from '../../types'

/** A real, fully-populated bull_call_spread shape -- every field
 * StrategyCard actually reads, matching what GET /api/options/
 * strategy-selector really returns for a ready strategy (see
 * RankedStrategy's own type comment), not a partial/guessed fixture. */
function makeStrategy(overrides: Partial<RankedStrategy> = {}): RankedStrategy {
  return {
    strategy: 'bull_call_spread',
    ready: true,
    fit_score: 78,
    components: {
      directional: { score: 40, reason: 'matches the bullish bias' },
      iv_rank: { score: 20, reason: 'IV rank favors buying premium' },
      pcr: { score: 10, reason: 'PCR neutral' },
      max_pain: { score: 8, reason: 'max pain is above spot' },
    },
    legs: [
      { action: 'BUY', type: 'CE', strike: 1300, premium: 22.65, iv: 16.9, delta: 0.47 },
      { action: 'SELL', type: 'CE', strike: 1330, premium: 11.8, iv: 16.9, delta: 0.3 },
    ],
    max_profit: 19.15,
    max_loss: 10.85,
    net_debit: 10.85,
    breakeven: [1310.85],
    ...overrides,
  }
}

describe('StrategyCard', () => {
  it('renders the strategy name, fit score, legs, Max P&L, and Breakeven (standard state)', () => {
    render(<StrategyCard strategy={makeStrategy()} />)

    expect(screen.getByText('Bull Call Spread')).toBeInTheDocument()
    expect(screen.getByText('78')).toBeInTheDocument()
    expect(screen.getByText('BUY CE 1300')).toBeInTheDocument()
    expect(screen.getByText('SELL CE 1330')).toBeInTheDocument()
    expect(screen.getByText('+₹19.15 / -₹10.85')).toBeInTheDocument()
    // (1310.85).toFixed(1) -- verified directly, not assumed: floating-
    // point rounds this DOWN to "1310.8", not the naively-expected
    // "1310.9".
    expect(screen.getByText('1310.8')).toBeInTheDocument()
    expect(screen.getByText('Net Debit')).toBeInTheDocument()
    expect(screen.getByText('₹10.85')).toBeInTheDocument()
    expect(screen.getByText('matches the bullish bias')).toBeInTheDocument()
  })

  it('shows Net Credit instead of Net Debit when the strategy is a credit structure', () => {
    render(
      <StrategyCard
        strategy={makeStrategy({
          strategy: 'iron_condor',
          net_debit: undefined,
          net_credit: 12.2,
        })}
      />,
    )

    expect(screen.getByText('Net Credit')).toBeInTheDocument()
    expect(screen.queryByText('Net Debit')).not.toBeInTheDocument()
    expect(screen.getByText('₹12.20')).toBeInTheDocument()
  })

  it('renders no "· SMC" badge and no accent ring when smcAligned is omitted', () => {
    const { container } = render(<StrategyCard strategy={makeStrategy()} />)

    expect(screen.queryByText('· SMC')).not.toBeInTheDocument()
    const card = container.firstElementChild as HTMLElement
    expect(card.className).toContain('border-hud-border')
    expect(card.className).not.toContain('ring-bull')
  })

  it('renders no "· SMC" badge and no accent ring when smcAligned is explicitly false', () => {
    const { container } = render(<StrategyCard strategy={makeStrategy()} smcAligned={false} />)

    expect(screen.queryByText('· SMC')).not.toBeInTheDocument()
    const card = container.firstElementChild as HTMLElement
    expect(card.className).not.toContain('ring-bull')
  })

  it('renders the "· SMC" badge and the bull accent ring when smcAligned is true', () => {
    const { container } = render(<StrategyCard strategy={makeStrategy()} smcAligned />)

    expect(screen.getByText('· SMC')).toBeInTheDocument()
    const card = container.firstElementChild as HTMLElement
    expect(card.className).toContain('border-bull/50')
    expect(card.className).toContain('ring-1')
    expect(card.className).toContain('ring-bull/30')
  })

  it('formats Max P&L, Breakeven, and Net values with an honest DASH when data is missing', () => {
    render(
      <StrategyCard
        strategy={makeStrategy({
          max_profit: undefined,
          max_loss: undefined,
          breakeven: undefined,
          net_debit: undefined,
          net_credit: undefined,
        })}
      />,
    )

    // max_profit/max_loss individually missing still renders as
    // "+₹— / -₹—" -- fmt()'s own honest-DASH fallback per missing
    // number, not a blank string or a fabricated 0.
    expect(screen.getByText('+₹— / -₹—')).toBeInTheDocument()
    // No real breakeven array at all -- the `|| DASH` fallback on the
    // whole joined string, not an empty list silently rendered.
    //
    // getByText('Breakeven') matches the OUTER stat-block <div> (RTL's
    // default text matching considers only a candidate's OWN direct
    // text-node children, ignoring nested elements' text -- verified
    // directly, not assumed: an earlier `.nextElementSibling` version
    // of this assertion looked like it passed but was actually reading
    // the NEXT stat block over, since the real value div is a CHILD of
    // the matched element, not a sibling of it). `.text-hud-text` is
    // the value div's own real className, one query away from any
    // assumption about DOM ordering.
    const breakevenBlock = screen.getByText('Breakeven')
    expect(breakevenBlock.querySelector('.text-hud-text')).toHaveTextContent('—')
    // Neither net_debit nor net_credit present: the component's own
    // ternary (`net_debit !== undefined ? 'Net Debit' : 'Net Credit'`)
    // falls to "Net Credit" whenever net_debit itself is absent,
    // REGARDLESS of whether net_credit is also present -- verified
    // against the real rendered output, not assumed from the label
    // name alone. Value honestly dashed either way.
    const netBlock = screen.getByText('Net Credit')
    expect(netBlock.querySelector('.text-hud-text')).toHaveTextContent('—')
  })

  it('joins multiple breakeven levels with a comma (a real multi-leg shape, e.g. Iron Condor)', () => {
    render(<StrategyCard strategy={makeStrategy({ breakeven: [1257.8, 1322.2] })} />)

    expect(screen.getByText('1257.8, 1322.2')).toBeInTheDocument()
  })
})
