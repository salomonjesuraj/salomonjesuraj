import { LineChart } from 'lucide-react'
import { DASH, MetricCard, type MetricTone } from '../components/MetricCard'
import { PageHeader } from '../components/PageHeader'
import { PayoffChart } from '../components/PayoffChart'
import { useOptionsAnalytics } from '../hooks/useOptionsAnalytics'
import type { RankedStrategy } from '../types'

function fmt(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined || Number.isNaN(value) ? DASH : value.toFixed(digits)
}

function titleCase(value: string | null | undefined): string {
  if (!value) return DASH
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function sentimentTone(sentiment: string | null | undefined): MetricTone {
  if (!sentiment) return 'neutral'
  if (sentiment.includes('bullish')) return 'good'
  if (sentiment.includes('bearish')) return 'bad'
  return 'neutral'
}

function StrategyCard({ strategy }: { strategy: RankedStrategy }) {
  const netKind = strategy.net_debit !== undefined ? 'Net Debit' : 'Net Credit'
  const netValue = strategy.net_debit ?? strategy.net_credit
  return (
    <div className="rounded-xl border border-hud-border bg-hud-panel p-4">
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-mono text-sm font-bold text-hud-text">{titleCase(strategy.strategy)}</h3>
        <span className="tnum shrink-0 rounded bg-bull/10 px-1.5 py-0.5 text-xs font-bold text-bull">
          {fmt(strategy.fit_score, 0)}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
        {strategy.legs?.map((leg, i) => (
          <span
            key={i}
            className={
              'rounded px-1.5 py-0.5 font-mono ' +
              (leg.action === 'BUY' ? 'bg-bull/10 text-bull' : 'bg-bear/10 text-bear')
            }
          >
            {leg.action} {leg.type} {fmt(leg.strike, 0)}
          </span>
        ))}
      </div>
      <div className="tnum mt-3 grid grid-cols-3 gap-2 text-[11px] text-hud-muted">
        <div>
          Max P&L
          <div className="text-hud-text">
            +₹{fmt(strategy.max_profit)} / -₹{fmt(strategy.max_loss)}
          </div>
        </div>
        <div>
          Breakeven
          <div className="text-hud-text">
            {strategy.breakeven?.map((b) => fmt(b, 1)).join(', ') || DASH}
          </div>
        </div>
        <div>
          {netKind}
          <div className="text-hud-text">{netValue !== undefined ? `₹${fmt(netValue)}` : DASH}</div>
        </div>
      </div>
      <ul className="mt-3 space-y-1 border-t border-hud-border pt-2 text-[11px] text-hud-muted">
        <li>{strategy.components.directional.reason}</li>
        <li>{strategy.components.pcr.reason}</li>
        <li>{strategy.components.max_pain.reason}</li>
      </ul>
    </div>
  )
}

/** `/analytics` -- Options Analytics, wired to real backend routes
 * (2026-08-27 data-wiring sprint). Honest gap, disclosed rather than
 * papered over: this backend scores exactly one live option Greek
 * (Delta) anywhere in its code -- api/routes/market.py's
 * _score_option_leg reads `iv` and `delta` off Upstox's option_greeks
 * payload and nothing else. Gamma/Theta/Vega are never read out of that
 * same payload by any route, so there is no real number to show for
 * them; this page says so instead of inventing one. */
export function OptionsAnalytics() {
  const { data } = useOptionsAnalytics()
  const chain = data?.chainAnalytics
  const strategies = data?.strategySelector
  const summary = data?.summary
  const delta = summary?.upstox_option?.metrics?.delta
  const topStrategy = strategies?.ready ? strategies.ranked_strategies?.[0] : undefined

  return (
    <div className="flex flex-1 flex-col gap-6">
      <PageHeader
        icon={LineChart}
        title="Options Analytics"
        subtitle={
          summary?.symbol
            ? `Live chain read for ${summary.symbol} · ${summary.bias ?? 'WAIT'}`
            : 'Waiting for a symbol context (active signal or pre-breakout candidate)'
        }
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard
          label="Delta"
          value={fmt(delta, 3)}
          tone={delta === undefined || delta === null ? 'neutral' : delta > 0 ? 'good' : 'bad'}
          sublabel={summary?.suggested_contract || undefined}
        />
        <MetricCard
          label="PCR Sentiment"
          value={titleCase(chain?.pcr?.sentiment)}
          tone={sentimentTone(chain?.pcr?.sentiment)}
          sublabel={chain?.pcr ? `PCR ${fmt(chain.pcr.pcr, 2)}` : undefined}
        />
        <MetricCard
          label="Max Pain"
          value={fmt(chain?.max_pain?.max_pain_strike, 0)}
          sublabel={chain?.spot ? `Spot ${fmt(chain.spot, 1)}` : undefined}
        />
        <MetricCard label="IV Rank" value={fmt(strategies?.iv_rank, 1)} />
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <div className="rounded-xl border border-hud-border bg-hud-panel p-4 lg:col-span-1">
          <h2 className="text-xs font-bold uppercase tracking-wide text-hud-muted">
            Greeks Exposure
          </h2>
          <div className="tnum mt-3 text-3xl font-bold text-hud-text">{fmt(delta, 3)}</div>
          <div className="text-[10px] uppercase tracking-wide text-hud-muted">Delta</div>
          <div className="mt-4 grid grid-cols-3 gap-2 text-center">
            {(['Gamma', 'Theta', 'Vega'] as const).map((label) => (
              <div key={label}>
                <div className="tnum font-mono text-lg font-bold text-hud-muted">{DASH}</div>
                <div className="text-[10px] uppercase tracking-wide text-hud-muted">{label}</div>
              </div>
            ))}
          </div>
          <p className="mt-4 text-[11px] leading-relaxed text-hud-muted">
            Gamma/Theta/Vega are not computed anywhere in this backend yet -- only Delta is
            scored. Disclosed gap, not a fabricated reading.
          </p>
        </div>

        <div className="rounded-xl border border-hud-border bg-hud-panel p-4 lg:col-span-2">
          <h2 className="text-xs font-bold uppercase tracking-wide text-hud-muted">
            OI Support / Resistance
          </h2>
          {chain?.oi_support_resistance ? (
            <div className="tnum mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <div>
                <div className="text-lg font-bold text-bull">
                  {fmt(chain.oi_support_resistance.support, 0)}
                </div>
                <div className="text-[10px] uppercase tracking-wide text-hud-muted">Support</div>
              </div>
              <div>
                <div className="text-lg font-bold text-bear">
                  {fmt(chain.oi_support_resistance.resistance, 0)}
                </div>
                <div className="text-[10px] uppercase tracking-wide text-hud-muted">
                  Resistance
                </div>
              </div>
              <div>
                <div className="text-lg font-bold text-hud-text">
                  {fmt(chain?.max_pain?.max_pain_strike, 0)}
                </div>
                <div className="text-[10px] uppercase tracking-wide text-hud-muted">Max Pain</div>
              </div>
              <div>
                <div className="text-lg font-bold text-hud-text">
                  {chain?.strikes_in_chain ?? DASH}
                </div>
                <div className="text-[10px] uppercase tracking-wide text-hud-muted">
                  Strikes in Chain
                </div>
              </div>
            </div>
          ) : (
            <p className="mt-3 text-xs text-hud-muted">
              {chain?.reason || 'No option chain read yet.'}
            </p>
          )}
        </div>
      </div>

      {topStrategy?.legs?.length && chain?.spot ? (
        <div className="rounded-xl border border-hud-border bg-hud-panel p-4">
          <h2 className="text-xs font-bold uppercase tracking-wide text-hud-muted">
            Payoff at Expiration -- {titleCase(topStrategy.strategy)}
          </h2>
          <p className="mt-1 text-[11px] text-hud-muted">
            Top-ranked strategy's real legs (strike/premium/action), profit zone shaded green,
            max-loss zone shaded red.
          </p>
          <PayoffChart legs={topStrategy.legs} spot={chain.spot} />
        </div>
      ) : null}

      <div>
        <h2 className="mb-3 text-xs font-bold uppercase tracking-wide text-hud-muted">
          Strategy Selector
        </h2>
        {strategies?.ready && strategies.ranked_strategies?.length ? (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {strategies.ranked_strategies.map((s) => (
              <StrategyCard key={s.strategy} strategy={s} />
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-hud-border bg-hud-panel/40 px-6 py-12 text-center">
            <LineChart className="h-6 w-6 text-hud-muted" />
            <p className="max-w-md text-xs text-hud-muted">
              {strategies?.reason || 'No ranked strategy shortlist available yet.'}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
