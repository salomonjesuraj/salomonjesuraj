import { useOrderBookDepth } from '../hooks/useOrderBookDepth'
import { CHART_BEAR, CHART_BULL } from '../lib/chartTheme'
import type { DepthLevel } from '../types'

function fmtPrice(v: number): string {
  return v.toFixed(2)
}

function fmtQty(v: number): string {
  return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)
}

interface RowProps {
  price: number
  qty: number
  maxQty: number
  side: 'bid' | 'ask'
}

/** One ladder row -- a background bar scaled to this level's share of
 * the visible book's largest quantity (across both sides, so a lopsided
 * book visibly shows which side has the "liquidity wall"), price and
 * quantity as plain text on top of it. */
function DepthRow({ price, qty, maxQty, side }: RowProps) {
  const color = side === 'bid' ? CHART_BULL : CHART_BEAR
  const fillPct = maxQty > 0 ? Math.max(4, (qty / maxQty) * 100) : 0
  return (
    <div className="relative flex h-6 items-center justify-between overflow-hidden rounded-sm px-2 text-[11px]">
      <div
        className="absolute inset-y-0 right-0"
        style={{ width: `${fillPct}%`, backgroundColor: color, opacity: 0.22 }}
      />
      <span className="tnum relative z-10 font-mono font-bold" style={{ color }}>
        {fmtPrice(price)}
      </span>
      <span className="tnum relative z-10 font-mono text-hud-muted">{fmtQty(qty)}</span>
    </div>
  )
}

/** Level 2 DOM ladder ("Terminal Edge" sprint, 2026-08-27) -- up to 5
 * real bid/ask levels from GET /api/market/depth/{symbol}. Asks render
 * above the spread (best ask closest to center, deepest at the top),
 * bids below (best bid closest to center, deepest at the bottom) --
 * the standard DOM-ladder reading order, not an arbitrary list. */
export function OrderBookLadder({ symbol }: { symbol: string }) {
  const { levels, available, reason } = useOrderBookDepth(symbol)

  if (!available || levels.length === 0) {
    return (
      <div className="flex h-full min-h-[220px] flex-col items-center justify-center gap-1 rounded-lg border border-hud-border bg-hud-bg/60 p-3 text-center text-[11px] text-hud-muted">
        <span>{reason || `No live depth for ${symbol} yet.`}</span>
      </div>
    )
  }

  const maxQty = Math.max(...levels.flatMap((l: DepthLevel) => [l.bidQ, l.askQ]), 1)
  const asks = [...levels].reverse() // deepest ask first, best ask (level 0) last -> closest to spread
  const spread = levels[0].askP - levels[0].bidP

  return (
    <div className="flex h-full flex-col gap-2 rounded-lg border border-hud-border bg-hud-bg/60 p-3">
      <div className="mb-1 text-[10px] font-bold uppercase tracking-wide text-hud-muted">
        Order Book -- {symbol}
      </div>
      <div className="flex flex-col gap-0.5">
        {asks.map((level, i) => (
          <DepthRow key={`ask-${i}`} price={level.askP} qty={level.askQ} maxQty={maxQty} side="ask" />
        ))}
      </div>
      <div className="tnum flex items-center justify-center gap-2 border-y border-hud-border py-1 text-[10px] text-hud-muted">
        Spread <span className="font-bold text-hud-text">{fmtPrice(spread)}</span>
      </div>
      <div className="flex flex-col gap-0.5">
        {levels.map((level, i) => (
          <DepthRow key={`bid-${i}`} price={level.bidP} qty={level.bidQ} maxQty={maxQty} side="bid" />
        ))}
      </div>
    </div>
  )
}
