import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'
import { CHART_AXIS_COLOR, CHART_BEAR, CHART_BULL, CHART_GRID_OPACITY, CHART_GRID_STROKE, hexToRgb } from '../lib/chartTheme'
import type { WalkforwardProfile } from '../types'
import { ChartTooltip } from './ChartTooltip'

interface ScatterPoint {
  winRate: number
  avgRr: number
  sharpe: number
  label: string
  status: string
  decided: number
}

function toPoints(profiles: WalkforwardProfile[]): ScatterPoint[] {
  return profiles
    .filter(
      (p) =>
        p.test.precision_pct !== null && p.test.avg_rr !== null && p.test_sharpe.sharpe !== null,
    )
    .map((p) => ({
      winRate: p.test.precision_pct as number,
      avgRr: p.test.avg_rr as number,
      sharpe: p.test_sharpe.sharpe as number,
      label: p.label,
      status: p.status,
      decided: p.test.decided,
    }))
}

/** Interpolates a color between bear-red (Sharpe <= 0) and bull-green
 * (Sharpe >= 1) -- 1.0 is a generous per-trade Sharpe for a 2-outcome
 * (win/loss) R-multiple series, chosen as the top of the scale rather
 * than a statistically special threshold. */
const BEAR_RGB = hexToRgb(CHART_BEAR)
const BULL_RGB = hexToRgb(CHART_BULL)

function sharpeColor(sharpe: number): string {
  const t = Math.max(0, Math.min(1, sharpe))
  const lerp = (a: number, b: number) => Math.round(a + (b - a) * t)
  return `rgb(${lerp(BEAR_RGB.r, BULL_RGB.r)}, ${lerp(BEAR_RGB.g, BULL_RGB.g)}, ${lerp(BEAR_RGB.b, BULL_RGB.b)})`
}

function sharpeRadius(sharpe: number): number {
  return 4 + Math.min(10, Math.abs(sharpe) * 6)
}

function ClusterDot(props: { cx?: number; cy?: number; payload?: ScatterPoint }) {
  const { cx, cy, payload } = props
  if (cx === undefined || cy === undefined || !payload) return null
  const r = sharpeRadius(payload.sharpe)
  const targetMet = payload.status === 'FORWARD_TARGET_MET'
  return (
    <g>
      {targetMet && (
        <circle cx={cx} cy={cy} r={r + 4} fill="none" stroke={CHART_BULL} strokeOpacity={0.5} />
      )}
      <circle cx={cx} cy={cy} r={r} fill={sharpeColor(payload.sharpe)} fillOpacity={0.75} />
    </g>
  )
}

function ClusterTooltip({ active, payload }: { active?: boolean; payload?: { payload: ScatterPoint }[] }) {
  const point = payload?.[0]?.payload
  if (!point) return null
  return (
    <ChartTooltip active={active}>
      <div className="max-w-[220px] font-mono text-hud-text">{point.label}</div>
      <div className="tnum mt-1 grid grid-cols-3 gap-x-3 text-hud-muted">
        <span>
          Win <span className="text-hud-text">{point.winRate.toFixed(1)}%</span>
        </span>
        <span>
          R:R <span className="text-hud-text">{point.avgRr.toFixed(2)}</span>
        </span>
        <span>
          Sharpe <span className="text-hud-text">{point.sharpe.toFixed(2)}</span>
        </span>
      </div>
      <div className="mt-1 text-hud-muted">{point.decided} decided · {point.status}</div>
    </ChartTooltip>
  )
}

/** The Lab's optimizer efficient-frontier scatter (2026-08-27 Data
 * Studio overhaul) -- one point per evaluated SMC parameter profile from
 * the real walk-forward grid search, X = out-of-sample win rate, Y =
 * out-of-sample avg R:R, node color+radius = per-trade Sharpe (green/
 * large = high, red/small = low or negative). Profiles that actually
 * clear their out-of-sample target get a glow ring so the genuinely
 * promising clusters -- not just the highest raw win rate -- stand out. */
export function OptimizerScatterChart({ profiles }: { profiles: WalkforwardProfile[] }) {
  const points = toPoints(profiles)
  if (points.length === 0) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-1 text-center text-xs text-hud-muted">
        No evaluated parameter profiles with a computable Sharpe yet.
      </div>
    )
  }

  const sharpeValues = points.map((p) => p.sharpe)

  return (
    <ResponsiveContainer width="100%" height={288}>
      <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeOpacity={CHART_GRID_OPACITY} />
        <XAxis
          type="number"
          dataKey="winRate"
          name="Win Rate"
          unit="%"
          tick={{ fill: CHART_AXIS_COLOR, fontSize: 10 }}
          axisLine={{ stroke: CHART_GRID_STROKE, strokeOpacity: CHART_GRID_OPACITY }}
          tickLine={false}
          domain={['dataMin - 5', 'dataMax + 5']}
        />
        <YAxis
          type="number"
          dataKey="avgRr"
          name="Avg R:R"
          tick={{ fill: CHART_AXIS_COLOR, fontSize: 10 }}
          axisLine={{ stroke: CHART_GRID_STROKE, strokeOpacity: CHART_GRID_OPACITY }}
          tickLine={false}
          width={36}
          domain={['dataMin - 0.2', 'dataMax + 0.2']}
        />
        <ZAxis dataKey="sharpe" domain={[Math.min(...sharpeValues), Math.max(...sharpeValues)]} />
        <Tooltip content={<ClusterTooltip />} cursor={{ stroke: CHART_GRID_STROKE, strokeOpacity: 0.3 }} />
        <Scatter data={points} shape={<ClusterDot />} isAnimationActive={false} />
      </ScatterChart>
    </ResponsiveContainer>
  )
}
