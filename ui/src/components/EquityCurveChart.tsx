import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CHART_AXIS_COLOR, CHART_BEAR, CHART_BULL, CHART_GRID_OPACITY, CHART_GRID_STROKE } from '../lib/chartTheme'
import { buildEquityCurve, parseIstTimestamp, type EquityPoint } from '../lib/equityCurve'
import type { JournalTrade } from '../types'
import { ChartTooltip } from './ChartTooltip'

function EquityTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: { payload: EquityPoint }[]
}) {
  const point = payload?.[0]?.payload
  if (!point || point.index === 0) return null
  const date = parseIstTimestamp(point.closedAt)
  return (
    <ChartTooltip active={active}>
      <div className="font-mono font-bold text-hud-text">{point.symbol}</div>
      <div className="mt-1 flex items-center gap-2">
        <span className={point.r >= 0 ? 'text-bull' : 'text-bear'}>
          {point.outcome} ({point.r >= 0 ? '+' : ''}
          {point.r.toFixed(2)}R)
        </span>
      </div>
      <div className="tnum mt-1 text-hud-text">
        Cumulative: <span className="font-bold">{point.cumulativeR.toFixed(2)}R</span>
      </div>
      {date && (
        <div className="mt-1 text-hud-muted">
          {date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
        </div>
      )}
    </ChartTooltip>
  )
}

/** The Ledger's real cumulative-expectancy equity curve (2026-08-27
 * Data Studio overhaul). Gradient fill splits at the zero line -- green
 * above (net ahead of breakeven), red below (underwater relative to
 * breakeven) -- computed from the data's own min/max rather than a
 * fixed assumption, so it's correct whether the curve is all-profit,
 * all-drawdown, or crosses zero multiple times. This is the zero-
 * relative reading of "underwater," not a rolling peak-relative
 * drawdown band (that needs per-segment clip-paths past what one
 * linearGradient can express) -- a disclosed scope choice, not a bug. */
export function EquityCurveChart({ trades }: { trades: JournalTrade[] }) {
  const points = buildEquityCurve(trades)
  if (points.length < 2) {
    return (
      <div className="flex h-56 flex-col items-center justify-center gap-1 text-center text-xs text-hud-muted">
        No closed trades yet -- the equity curve fills in as journal entries resolve.
      </div>
    )
  }

  const values = points.map((p) => p.cumulativeR)
  const max = Math.max(...values, 0)
  const min = Math.min(...values, 0)
  const span = max - min || 1
  const zeroOffsetPct = Math.min(100, Math.max(0, (max / span) * 100))

  return (
    <ResponsiveContainer width="100%" height={224}>
      <AreaChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CHART_BULL} stopOpacity={0.55} />
            <stop offset={`${zeroOffsetPct}%`} stopColor={CHART_BULL} stopOpacity={0.05} />
            <stop offset={`${zeroOffsetPct}%`} stopColor={CHART_BEAR} stopOpacity={0.05} />
            <stop offset="100%" stopColor={CHART_BEAR} stopOpacity={0.55} />
          </linearGradient>
          <linearGradient id="equityStroke" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CHART_BULL} />
            <stop offset={`${zeroOffsetPct}%`} stopColor={CHART_BULL} />
            <stop offset={`${zeroOffsetPct}%`} stopColor={CHART_BEAR} />
            <stop offset="100%" stopColor={CHART_BEAR} />
          </linearGradient>
        </defs>
        <XAxis dataKey="index" hide />
        <YAxis
          width={36}
          tick={{ fill: CHART_AXIS_COLOR, fontSize: 10 }}
          axisLine={false}
          tickLine={false}
        />
        <ReferenceLine y={0} stroke={CHART_GRID_STROKE} strokeOpacity={CHART_GRID_OPACITY} />
        <Tooltip content={<EquityTooltip />} />
        <Area
          type="monotone"
          dataKey="cumulativeR"
          stroke="url(#equityStroke)"
          strokeWidth={2}
          fill="url(#equityFill)"
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
