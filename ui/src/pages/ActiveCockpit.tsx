import { Wallet } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { DASH, MetricCard } from '../components/MetricCard'
import { PageHeader } from '../components/PageHeader'
import { PositionIntelligenceCard } from '../components/PositionIntelligenceCard'
import { useActivePositions, useHoldings, useOrderBook } from '../hooks/useBrokerState'
import { useAudioAlert } from '../hooks/useAudioAlert'
import type { BrokerHolding, BrokerOrder } from '../types'

const ALERTABLE_TAGS = new Set(['STRUCTURAL_BREAK', 'FAST_EXIT'])

function fmt(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined || Number.isNaN(value) ? DASH : value.toFixed(digits)
}

const ORDER_STATUS_CLASS: Record<string, string> = {
  complete: 'bg-bull/15 text-bull',
  open: 'bg-horizon-btst/15 text-horizon-btst',
  cancelled: 'bg-hud-muted/15 text-hud-muted',
  rejected: 'bg-bear/15 text-bear',
}

function orderStatusClass(status: string): string {
  return ORDER_STATUS_CLASS[status.toLowerCase()] || 'bg-hud-muted/15 text-hud-muted'
}

function OrderRow({ order }: { order: BrokerOrder }) {
  const symbol = order.trading_symbol || order.tradingsymbol || '—'
  return (
    <tr className="border-b border-hud-border last:border-0">
      <td className="whitespace-nowrap py-2 pr-3 font-mono text-hud-text">{symbol}</td>
      <td className="whitespace-nowrap py-2 pr-3">
        <span className={order.transaction_type === 'BUY' ? 'text-bull' : 'text-bear'}>
          {order.transaction_type}
        </span>
      </td>
      <td className="tnum whitespace-nowrap py-2 pr-3 text-hud-muted">{order.quantity}</td>
      <td className="tnum whitespace-nowrap py-2 pr-3 text-hud-text">{fmt(order.price)}</td>
      <td className="tnum whitespace-nowrap py-2 pr-3 text-hud-muted">{fmt(order.average_price)}</td>
      <td className="whitespace-nowrap py-2 pr-3">
        <span className={'rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ' + orderStatusClass(order.status)}>
          {order.status}
        </span>
      </td>
      <td className="whitespace-nowrap py-2 text-hud-muted">{order.order_timestamp}</td>
    </tr>
  )
}

function HoldingRow({ holding }: { holding: BrokerHolding }) {
  const symbol = holding.trading_symbol || holding.tradingsymbol || '—'
  const returnPct =
    holding.average_price > 0 ? ((holding.last_price - holding.average_price) / holding.average_price) * 100 : null
  return (
    <tr className="border-b border-hud-border last:border-0">
      <td className="whitespace-nowrap py-2 pr-3 font-mono text-hud-text">{symbol}</td>
      <td className="max-w-[220px] truncate py-2 pr-3 text-hud-muted">{holding.company_name}</td>
      <td className="tnum whitespace-nowrap py-2 pr-3 text-hud-muted">{holding.quantity}</td>
      <td className="tnum whitespace-nowrap py-2 pr-3 text-hud-text">{fmt(holding.average_price)}</td>
      <td className="tnum whitespace-nowrap py-2 pr-3 text-hud-text">{fmt(holding.last_price)}</td>
      <td className={'tnum whitespace-nowrap py-2 pr-3 font-bold ' + (holding.pnl >= 0 ? 'text-bull' : 'text-bear')}>
        {holding.pnl >= 0 ? '+' : ''}
        {fmt(holding.pnl)}
      </td>
      <td className={'tnum whitespace-nowrap py-2 font-bold ' + (returnPct !== null && returnPct >= 0 ? 'text-bull' : 'text-bear')}>
        {returnPct !== null ? `${returnPct >= 0 ? '+' : ''}${returnPct.toFixed(2)}%` : DASH}
      </td>
    </tr>
  )
}

/**
 * `/positions` -- Active Cockpit ("Broker Sync & Active Position
 * Intelligence" master sprint, 2026-08-27). STRICT READ-ONLY: every
 * number on this page comes from a real GET against Upstox's own real
 * account data (api/broker_sync.py) or a deterministic computation over
 * it -- there is no execute/order-placement control anywhere on this
 * page, and none is planned. Trade execution stays 100% manual, on the
 * broker's own platform; this page exists purely to help decide, never
 * to act.
 */
export function ActiveCockpit() {
  const { data: positionsData } = useActivePositions()
  const { data: ordersData } = useOrderBook()
  const { data: holdingsData } = useHoldings()

  const positions = positionsData?.positions ?? []
  const portfolio = positionsData?.portfolio
  const orders = ordersData?.orders ?? []
  const holdings = holdingsData?.holdings ?? []

  // "Omnipresent Alert Engine" sprint (2026-08-27) -- Warning Chime
  // fires once per position's ONSET into an alertable state (EXIT
  // IMMEDIATELY, or carrying STRUCTURAL_BREAK/FAST_EXIT), mirroring
  // SniperHud's own Success Ping onset-only logic and the backend's own
  // per-position Telegram cooldown (api/broker_sync.py) -- three
  // independent layers all converging on the same "alert on the
  // transition, not on every poll" principle, not a coincidence.
  const { playWarningChime } = useAudioAlert()
  const alertedPositionsRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    // Derives straight from `positionsData` rather than the outer
    // `positions` (`?? []`-derived, a fresh array reference every
    // render) so this effect's own dependency array can name a real,
    // stable source instead of an inline-computed local.
    const currentKeys = new Set(
      (positionsData?.positions ?? [])
        .filter(
          (p) =>
            p.intelligence.holding_horizon === 'EXIT IMMEDIATELY' ||
            p.intelligence.warning_tags.some((tag) => ALERTABLE_TAGS.has(tag)),
        )
        .map((p) => p.instrument_token),
    )
    let firedNew = false
    for (const key of currentKeys) {
      if (!alertedPositionsRef.current.has(key)) firedNew = true
    }
    if (firedNew) playWarningChime()
    alertedPositionsRef.current = currentKeys
  }, [positionsData, playWarningChime])

  return (
    <div className="flex flex-1 flex-col gap-6">
      <PageHeader
        icon={Wallet}
        title="Active Cockpit"
        subtitle="Read-only live broker sync -- trade execution stays 100% manual, on Upstox's own platform"
      />

      {/* Master Portfolio Strip */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard
          label="Total Unrealized M2M"
          value={portfolio ? fmt(portfolio.total_unrealized_pnl, 0) : DASH}
          tone={!portfolio ? 'neutral' : portfolio.total_unrealized_pnl >= 0 ? 'good' : 'bad'}
        />
        <MetricCard
          label="Realized PnL"
          value={portfolio ? fmt(portfolio.total_realized_pnl, 0) : DASH}
          tone={!portfolio ? 'neutral' : portfolio.total_realized_pnl >= 0 ? 'good' : 'bad'}
        />
        <MetricCard label="Capital Deployed" value={portfolio ? fmt(portfolio.capital_deployed, 0) : DASH} />
        <MetricCard
          label="Structural Risk Est."
          value={portfolio ? fmt(portfolio.structural_risk_estimate, 0) : DASH}
          sublabel={
            portfolio
              ? `Known for ${portfolio.structural_risk_known_for}/${portfolio.structural_risk_total_positions} positions -- distance to the nearest real invalidation level, not a stop you set`
              : undefined
          }
        />
      </div>

      {/* Position Intelligence Cards */}
      <div>
        <h2 className="mb-3 text-xs font-bold uppercase tracking-wide text-hud-muted">
          Active Positions
        </h2>
        {positionsData && !positionsData.available ? (
          <p className="rounded-xl border border-dashed border-hud-border bg-hud-panel/40 px-4 py-6 text-center text-xs text-hud-muted">
            {positionsData.reason || 'Broker positions unavailable.'}
          </p>
        ) : positions.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {positions.map((position) => (
              <PositionIntelligenceCard key={position.instrument_token} position={position} />
            ))}
          </div>
        ) : (
          <p className="rounded-xl border border-dashed border-hud-border bg-hud-panel/40 px-4 py-6 text-center text-xs text-hud-muted">
            No active broker positions right now.
          </p>
        )}
      </div>

      {/* Live Order Book Table */}
      <div>
        <h2 className="mb-3 text-xs font-bold uppercase tracking-wide text-hud-muted">
          Live Order Book
        </h2>
        <div className="overflow-x-auto rounded-xl border border-hud-border bg-hud-panel p-3">
          {ordersData && !ordersData.available ? (
            <p className="py-4 text-center text-xs text-hud-muted">{ordersData.reason || 'Order book unavailable.'}</p>
          ) : orders.length > 0 ? (
            <table className="w-full min-w-[640px] text-left text-[11px]">
              <thead>
                <tr className="border-b border-hud-border text-[10px] uppercase tracking-wide text-hud-muted">
                  <th className="pb-2 pr-3 font-bold">Symbol</th>
                  <th className="pb-2 pr-3 font-bold">Side</th>
                  <th className="pb-2 pr-3 font-bold">Qty</th>
                  <th className="pb-2 pr-3 font-bold">Price</th>
                  <th className="pb-2 pr-3 font-bold">Avg Fill</th>
                  <th className="pb-2 pr-3 font-bold">Status</th>
                  <th className="pb-2 font-bold">Time</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order, i) => (
                  <OrderRow key={`${order.trading_symbol}-${order.order_timestamp}-${i}`} order={order} />
                ))}
              </tbody>
            </table>
          ) : (
            <p className="py-4 text-center text-xs text-hud-muted">No orders placed today.</p>
          )}
        </div>
      </div>

      {/* Portfolio Holdings Grid */}
      <div>
        <h2 className="mb-3 text-xs font-bold uppercase tracking-wide text-hud-muted">
          Portfolio Holdings
        </h2>
        <div className="overflow-x-auto rounded-xl border border-hud-border bg-hud-panel p-3">
          {holdingsData && !holdingsData.available ? (
            <p className="py-4 text-center text-xs text-hud-muted">{holdingsData.reason || 'Holdings unavailable.'}</p>
          ) : holdings.length > 0 ? (
            <table className="w-full min-w-[560px] text-left text-[11px]">
              <thead>
                <tr className="border-b border-hud-border text-[10px] uppercase tracking-wide text-hud-muted">
                  <th className="pb-2 pr-3 font-bold">Symbol</th>
                  <th className="pb-2 pr-3 font-bold">Company</th>
                  <th className="pb-2 pr-3 font-bold">Qty</th>
                  <th className="pb-2 pr-3 font-bold">Avg</th>
                  <th className="pb-2 pr-3 font-bold">LTP</th>
                  <th className="pb-2 pr-3 font-bold">PnL</th>
                  <th className="pb-2 font-bold">Return</th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((holding) => (
                  <HoldingRow key={holding.trading_symbol || holding.tradingsymbol} holding={holding} />
                ))}
              </tbody>
            </table>
          ) : (
            <p className="py-4 text-center text-xs text-hud-muted">No long-term holdings.</p>
          )}
        </div>
      </div>
    </div>
  )
}
