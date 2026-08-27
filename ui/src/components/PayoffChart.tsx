import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CHART_AXIS_COLOR, CHART_BEAR, CHART_BULL, CHART_GRID_OPACITY, CHART_GRID_STROKE } from '../lib/chartTheme'
import { computePayoffCurve, type PayoffPoint } from '../lib/payoff'
import type { StrategyLeg } from '../types'
import { ChartTooltip } from './ChartTooltip'

function PayoffTooltip({ active, payload }: { active?: boolean; payload?: { payload: PayoffPoint }[] }) {
  const point = payload?.[0]?.payload
  if (!point) return null
  return (
    <ChartTooltip active={active}>
      <div className="tnum text-hud-text">
        Underlying <span className="font-bold">{point.price.toFixed(2)}</span>
      </div>
      <div className={'tnum font-bold ' + (point.pnl >= 0 ? 'text-bull' : 'text-bear')}>
        PnL {point.pnl >= 0 ? '+' : ''}
        ₹{point.pnl.toFixed(2)}
      </div>
    </ChartTooltip>
  )
}

/** Options Analytics' payoff-at-expiration diagram (2026-08-27 Data
 * Studio overhaul) -- an AreaChart (a line with its underside filled),
 * the correct recharts composition for "a curve, shaded green above
 * zero / red below" in one pass rather than layering a separate
 * LineChart and Area. Gradient split point is computed from this
 * curve's own zero-crossing, exactly like EquityCurveChart's. */
export function PayoffChart({ legs, spot }: { legs: StrategyLeg[]; spot: number }) {
  const points = computePayoffCurve(legs, spot)
  if (points.length === 0) {
    return (
      <div className="flex h-56 items-center justify-center text-xs text-hud-muted">
        No legs to plot a payoff for.
      </div>
    )
  }

  const values = points.map((p) => p.pnl)
  const max = Math.max(...values, 0)
  const min = Math.min(...values, 0)
  const span = max - min || 1
  const zeroOffsetPct = Math.min(100, Math.max(0, (max / span) * 100))
  const spotPoint = points.reduce((best, p) => (Math.abs(p.price - spot) < Math.abs(best.price - spot) ? p : best), points[0])

  return (
    <ResponsiveContainer width="100%" height={224}>
      <AreaChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="payoffFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CHART_BULL} stopOpacity={0.5} />
            <stop offset={`${zeroOffsetPct}%`} stopColor={CHART_BULL} stopOpacity={0.05} />
            <stop offset={`${zeroOffsetPct}%`} stopColor={CHART_BEAR} stopOpacity={0.05} />
            <stop offset="100%" stopColor={CHART_BEAR} stopOpacity={0.5} />
          </linearGradient>
          <linearGradient id="payoffStroke" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CHART_BULL} />
            <stop offset={`${zeroOffsetPct}%`} stopColor={CHART_BULL} />
            <stop offset={`${zeroOffsetPct}%`} stopColor={CHART_BEAR} />
            <stop offset="100%" stopColor={CHART_BEAR} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="price"
          tick={{ fill: CHART_AXIS_COLOR, fontSize: 10 }}
          axisLine={{ stroke: CHART_GRID_STROKE, strokeOpacity: CHART_GRID_OPACITY }}
          tickLine={false}
          tickFormatter={(v: number) => v.toFixed(0)}
        />
        <YAxis
          width={44}
          tick={{ fill: CHART_AXIS_COLOR, fontSize: 10 }}
          axisLine={false}
          tickLine={false}
        />
        <ReferenceLine y={0} stroke={CHART_GRID_STROKE} strokeOpacity={CHART_GRID_OPACITY} />
        <ReferenceLine
          x={spotPoint.price}
          stroke={CHART_AXIS_COLOR}
          strokeDasharray="3 3"
          label={{ value: 'Spot', position: 'insideTopRight', fill: CHART_AXIS_COLOR, fontSize: 10 }}
        />
        <Tooltip content={<PayoffTooltip />} />
        <Area
          type="monotone"
          dataKey="pnl"
          stroke="url(#payoffStroke)"
          strokeWidth={2}
          fill="url(#payoffFill)"
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
