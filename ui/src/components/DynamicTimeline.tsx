import { useMemo } from 'react'
import type { Direction } from '../types'

interface DynamicTimelineProps {
  direction: Direction
  invalidationSl: number
  entryPrice: number
  target1: number
  target2: number
  target3: number
  /** Live LTP from useTickStore -- undefined until the first tick
   * batch arrives, in which case the marker simply doesn't render
   * rather than sitting at a fabricated position. */
  ltp: number | undefined
}

interface Level {
  key: string
  label: string
  price: number
  tone: 'risk' | 'entry' | 'reward'
}

// "Retest dips into the entry zone" band half-width, as a fraction of
// the whole SL-to-T3 span. A rendering heuristic (this component has
// no ATR of its own to size the band against precisely), not a value
// read from the backend -- disclosed here rather than presented as
// data-driven precision.
const ENTRY_ZONE_HALF_WIDTH_FRACTION = 0.06

// Live-data bug found and fixed (2026-08-27): every level label is
// centered under its tick via translateX(-50%), which is fine for a
// label sitting mid-track but pushes half the label's own width past
// the container's edge for whichever level lands at/near 0% or 100%
// (SL and T3, always -- see the pct() comment below for why they're
// always the extremes). A wide value (a >=6-digit price, or a long
// decimal) made this a real, reproducible card overflow, not just a
// theoretical one. Fix: map positions into an inset range instead of
// the full 0-100%, so even a fully-centered edge label has margin to
// sit inside. 9% comfortably fits a "123456.79"-width label at this
// font-size in a card as narrow as the mobile grid column gets.
const EDGE_INSET_PCT = 9

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export function DynamicTimeline({
  direction,
  invalidationSl,
  entryPrice,
  target1,
  target2,
  target3,
  ltp,
}: DynamicTimelineProps) {
  const bullish = direction === 'BULL'

  const hasLevels =
    invalidationSl > 0 && entryPrice > 0 && target1 > 0 && target2 > 0 && target3 > 0

  const { levels, pct, zoneHalfWidthPct, entryPct } = useMemo(() => {
    // Signed scalar so BULL and BEAR both read left(risk) -> right
    // (reward) on screen, even though BEAR's real prices descend.
    const scalar = (p: number) => (bullish ? p : -p)

    const built: Level[] = [
      { key: 'sl', label: 'SL', price: invalidationSl, tone: 'risk' },
      { key: 'entry', label: 'Entry', price: entryPrice, tone: 'entry' },
      { key: 't1', label: 'T1', price: target1, tone: 'reward' },
      { key: 't2', label: 'T2', price: target2, tone: 'reward' },
      { key: 't3', label: 'T3', price: target3, tone: 'reward' },
    ]

    const values = built.map((l) => scalar(l.price))
    const min = Math.min(...values)
    const max = Math.max(...values)
    const span = max - min || 1

    // Raw 0-100 first, then compressed into [EDGE_INSET_PCT, 100 -
    // EDGE_INSET_PCT] -- see EDGE_INSET_PCT's own comment. SL and T3
    // are always the raw min/max by construction (scalar() makes the
    // riskiest level the smallest value and the furthest target the
    // largest, for both directions), so they're always the ones that
    // actually need the inset; middle levels get compressed too, but
    // proportionally, so their relative spacing is preserved.
    const insetSpan = 100 - EDGE_INSET_PCT * 2
    const pctFn = (price: number) => {
      const raw = clamp(((scalar(price) - min) / span) * 100, 0, 100)
      return EDGE_INSET_PCT + (raw / 100) * insetSpan
    }

    return {
      levels: built,
      pct: pctFn,
      zoneHalfWidthPct: ENTRY_ZONE_HALF_WIDTH_FRACTION * 100,
      entryPct: pctFn(entryPrice),
    }
  }, [bullish, invalidationSl, entryPrice, target1, target2, target3])

  if (!hasLevels) {
    return (
      <div className="flex h-16 items-center justify-center rounded-lg border border-dashed border-hud-border text-xs text-hud-muted">
        No entry/SL/target levels available for this setup
      </div>
    )
  }

  const ltpPct = ltp !== undefined && ltp > 0 ? pct(ltp) : null
  const inEntryZone = ltpPct !== null && Math.abs(ltpPct - entryPct) <= zoneHalfWidthPct

  return (
    <div className="pt-6 pb-8">
      <div className="relative h-1.5 rounded-full bg-hud-border">
        {/* Risk (bear) -> reward (bull) gradient track, direction-agnostic
            since positions are already normalized to risk-left/reward-right. */}
        <div
          className="absolute inset-0 rounded-full opacity-70"
          style={{
            background: 'linear-gradient(90deg, var(--color-bear) 0%, var(--color-hud-border) 35%, var(--color-bull) 100%)',
          }}
        />

        {/* Entry-zone band */}
        <div
          className="absolute top-1/2 h-4 -translate-y-1/2 rounded-sm bg-hud-text/10 ring-1 ring-hud-text/20"
          style={{
            left: `${clamp(entryPct - zoneHalfWidthPct, 0, 100)}%`,
            width: `${clamp(zoneHalfWidthPct * 2, 0, 100)}%`,
          }}
        />

        {/* Level ticks */}
        {levels.map((level) => (
          <div
            key={level.key}
            className="absolute top-1/2 flex -translate-y-1/2 flex-col items-center"
            style={{ left: `${pct(level.price)}%` }}
          >
            <div
              className={
                'h-3 w-0.5 -translate-x-1/2 ' +
                (level.tone === 'risk'
                  ? 'bg-bear'
                  : level.tone === 'reward'
                    ? 'bg-bull'
                    : 'bg-hud-text')
              }
            />
          </div>
        ))}

        {/* Live LTP marker -- absent until the first real tick arrives. */}
        {ltpPct !== null && (
          <div
            className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 transition-[left] duration-300 ease-out"
            style={{ left: `${ltpPct}%` }}
          >
            <div
              className={
                'h-3.5 w-3.5 rounded-full shadow-[0_0_10px_2px] ' +
                (inEntryZone
                  ? 'bg-horizon-btst shadow-horizon-btst animate-pulse'
                  : bullish
                    ? 'bg-bull shadow-bull'
                    : 'bg-bear shadow-bear')
              }
            />
          </div>
        )}
      </div>

      {/* Labels beneath each tick */}
      <div className="relative mt-2 h-8 text-[10px] font-mono text-hud-muted">
        {levels.map((level) => (
          <div
            key={level.key}
            className="absolute flex -translate-x-1/2 flex-col items-center"
            style={{ left: `${pct(level.price)}%` }}
          >
            <span className="uppercase tracking-wide">{level.label}</span>
            <span className="tnum text-hud-text">{level.price.toFixed(2)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
