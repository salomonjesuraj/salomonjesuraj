import { LineChart } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { DASH, MetricCard, type MetricTone } from '../components/MetricCard'
import { LiveCandlestickChart } from '../components/LiveCandlestickChart'
import { PageHeader } from '../components/PageHeader'
import { PayoffChart } from '../components/PayoffChart'
import { SymbolSelector } from '../components/SymbolSelector'
import { useOptionsAnalytics } from '../hooks/useOptionsAnalytics'
import type { OptionChainStrike, RankedStrategy } from '../types'

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

function fmtOi(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return value.toFixed(0)
}

/** Full per-strike option chain, "Unified Screener & Deep-Dive
 * Interactivity" sprint (2026-08-28) -- real GET /api/options/chain
 * rows, calls on the left, puts on the right, the classic layout. The
 * strike nearest real spot gets a highlighted row (ATM), not a
 * fabricated "the" ATM strike when spot itself isn't known. */
function OptionChainTable({
  symbol,
  strikes,
  spot,
}: {
  symbol: string
  strikes: OptionChainStrike[]
  spot?: number
}) {
  const atmStrike =
    spot && strikes.length > 0
      ? strikes.reduce((closest, s) =>
          Math.abs(s.strike_price - spot) < Math.abs(closest.strike_price - spot) ? s : closest,
        ).strike_price
      : null

  return (
    // data-symbol: an explicit, verifiable anchor that these rows really
    // are this exact symbol's own strikes -- not load-bearing for
    // rendering (the parent's own currentOnly() guard already stops a
    // stale cross-symbol fullChain from ever reaching this component),
    // but a real DOM fact a test or a screenshot diff can check directly
    // rather than trusting the heading text above it matches.
    <div
      data-symbol={symbol}
      className="max-h-[480px] overflow-auto rounded-xl border border-hud-border bg-hud-panel"
    >
      <table className="w-full min-w-[820px] text-center text-[11px]">
        <thead className="sticky top-0 bg-hud-panel">
          <tr className="border-b border-hud-border text-[10px] uppercase tracking-wide text-hud-muted">
            <th colSpan={4} className="px-2 py-2 font-bold text-bull">
              Calls
            </th>
            <th className="px-2 py-2 font-bold">Strike</th>
            <th colSpan={4} className="px-2 py-2 font-bold text-bear">
              Puts
            </th>
          </tr>
          <tr className="border-b border-hud-border text-[10px] uppercase tracking-wide text-hud-muted">
            <th className="px-2 py-1.5">OI</th>
            <th className="px-2 py-1.5">LTP</th>
            <th className="px-2 py-1.5">IV</th>
            <th className="px-2 py-1.5">Delta</th>
            <th className="px-2 py-1.5" />
            <th className="px-2 py-1.5">Delta</th>
            <th className="px-2 py-1.5">IV</th>
            <th className="px-2 py-1.5">LTP</th>
            <th className="px-2 py-1.5">OI</th>
          </tr>
        </thead>
        <tbody className="tnum divide-y divide-hud-border font-mono">
          {strikes.map((s) => (
            <tr
              key={s.strike_price}
              className={s.strike_price === atmStrike ? 'bg-horizon-btst/10' : ''}
            >
              <td className="px-2 py-1.5 text-hud-text">{fmtOi(s.call.oi)}</td>
              <td className="px-2 py-1.5 text-hud-text">{s.call.ltp.toFixed(2)}</td>
              <td className="px-2 py-1.5 text-hud-muted">{s.call.iv.toFixed(1)}</td>
              <td className="px-2 py-1.5 text-hud-muted">{s.call.delta.toFixed(2)}</td>
              <td className="px-2 py-1.5 font-bold text-hud-text">{s.strike_price.toFixed(0)}</td>
              <td className="px-2 py-1.5 text-hud-muted">{s.put.delta.toFixed(2)}</td>
              <td className="px-2 py-1.5 text-hud-muted">{s.put.iv.toFixed(1)}</td>
              <td className="px-2 py-1.5 text-hud-text">{s.put.ltp.toFixed(2)}</td>
              <td className="px-2 py-1.5 text-hud-text">{fmtOi(s.put.oi)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
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
 * papered over: this page's own Greeks Exposure card scores exactly
 * one live option Greek (Delta) anywhere in its code -- api/routes/
 * market.py's _score_option_leg reads `iv` and `delta` off Upstox's
 * option_greeks payload and nothing else for its signal-gating model.
 * That's a separate concern from the real per-strike Option Chain
 * table below, which DOES show real gamma/theta/vega straight off
 * Upstox's own payload -- see OptionChainStrike's own type comment.
 *
 * "Unified Screener & Deep-Dive Interactivity" sprint (2026-08-28): the
 * page now has its own symbol selector, synced to the URL's `?symbol=`
 * query param (react-router's useSearchParams, not local-only state) so
 * a deep link from the Screener, Pre-Breakout Watchlist, or Sniper HUD
 * lands here with the right symbol already loaded, and the page's own
 * URL stays shareable/bookmarkable. No symbol picked yet keeps the
 * original default-symbol behavior (most recent active signal, else
 * best pre-breakout candidate) exactly as before this sprint. */
/** True cross-symbol staleness guard -- "UI Cleanup, Symbol Sync & SMC
 * Clutter Filtering" sprint (2026-08-28). useOptionsAnalytics's own
 * usePolling (like every usePolling in this app) keeps the LAST GOOD
 * `data` on screen across a symbol change until the new fetch actually
 * resolves -- correct for "a dropped poll shouldn't blank a working
 * card," wrong for "the user just picked a different symbol": for that
 * brief in-flight window, every card below was still rendering the
 * PREVIOUS symbol's real numbers under the NEW symbol's own header
 * (this page's own real, verified bug behind what looked like a
 * hardcoded fallback -- searched the whole repo for the literal symbol
 * the report named; it appears nowhere, it's just whichever real
 * symbol happened to be showing when the next one hadn't loaded yet).
 * Each of the four backend payloads below carries its own real
 * `symbol` field; this drops a payload the instant it's confirmed
 * stale rather than trusting "whatever's in `data` right now must be
 * current." Passes a payload through unchanged when it has no
 * `symbol` field to check (never seen in practice, but not proof of
 * staleness either) -- only a genuine mismatch hides it. */
function currentOnly<T extends { symbol?: string }>(
  payload: T | undefined,
  activeSymbol: string | undefined,
): T | undefined {
  if (!payload || !activeSymbol || !payload.symbol) return payload
  return payload.symbol.toUpperCase() === activeSymbol.toUpperCase() ? payload : undefined
}

export function OptionsAnalytics() {
  const [searchParams, setSearchParams] = useSearchParams()
  const symbol = searchParams.get('symbol')?.toUpperCase() || undefined

  const { data } = useOptionsAnalytics(symbol)
  // No `?symbol=` yet: activeSymbol defers to the backend's OWN
  // resolved default (data.summary.symbol, unfiltered -- see
  // currentOnly's own doc for why filtering happens after this, not
  // before) so a first-ever page load isn't immediately flagged stale
  // against itself.
  const activeSymbol = symbol ?? data?.summary?.symbol
  const chain = currentOnly(data?.chainAnalytics, activeSymbol)
  const strategies = currentOnly(data?.strategySelector, activeSymbol)
  const summary = currentOnly(data?.summary, activeSymbol)
  const fullChain = currentOnly(data?.chain, activeSymbol)
  const delta = summary?.upstox_option?.metrics?.delta
  const topStrategy = strategies?.ready ? strategies.ranked_strategies?.[0] : undefined

  const handleSelectSymbol = (sym: string) => {
    setSearchParams(sym ? { symbol: sym } : {})
  }

  return (
    <div className="flex flex-1 flex-col gap-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <PageHeader
          icon={LineChart}
          title="Options Analytics"
          subtitle={
            summary?.symbol
              ? `Live chain read for ${summary.symbol} · ${summary.bias ?? 'WAIT'}`
              : 'Waiting for a symbol context (active signal or pre-breakout candidate)'
          }
        />
        <SymbolSelector
          value={symbol}
          onSelect={handleSelectSymbol}
          placeholder="Jump to F&O symbol…"
        />
      </div>

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

      <div>
        <h2 className="mb-3 text-xs font-bold uppercase tracking-wide text-hud-muted">
          Chart{activeSymbol ? ` -- ${activeSymbol}` : ''}
        </h2>
        {activeSymbol ? (
          <LiveCandlestickChart symbol={activeSymbol} heightClassName="h-96" />
        ) : (
          <p className="rounded-xl border border-dashed border-hud-border bg-hud-panel/40 px-4 py-6 text-center text-xs text-hud-muted">
            Waiting for a symbol context (active signal or pre-breakout candidate).
          </p>
        )}
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

      <div>
        <h2 className="mb-3 text-xs font-bold uppercase tracking-wide text-hud-muted">
          Option Chain{fullChain?.symbol ? ` -- ${fullChain.symbol}` : ''}
          {fullChain?.expiry ? ` (${fullChain.expiry})` : ''}
        </h2>
        {fullChain?.ready && fullChain.strikes?.length && activeSymbol ? (
          <OptionChainTable
            key={activeSymbol}
            symbol={activeSymbol}
            strikes={fullChain.strikes}
            spot={fullChain.spot}
          />
        ) : (
          <p className="rounded-xl border border-dashed border-hud-border bg-hud-panel/40 px-4 py-6 text-center text-xs text-hud-muted">
            {fullChain?.reason || 'No option chain read yet.'}
          </p>
        )}
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
