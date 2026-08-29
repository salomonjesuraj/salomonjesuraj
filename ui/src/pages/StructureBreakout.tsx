import { Crosshair } from 'lucide-react'
import { useState } from 'react'
import { LiveCandlestickChart } from '../components/LiveCandlestickChart'
import { DASH, MetricCard, type MetricTone } from '../components/MetricCard'
import { PageHeader } from '../components/PageHeader'
import { SymbolSelector } from '../components/SymbolSelector'
import { usePolling } from '../hooks/usePolling'
import { fetchStructureSignal } from '../lib/api'
import type { ChartInterval } from '../lib/api'
import type { StructureSignal } from '../types'

const TIMEFRAMES = ['3m', '5m', '15m', '1h', '4h', '1d'] as const
type Timeframe = (typeof TIMEFRAMES)[number]

// LiveCandlestickChart's own ChartInterval union doesn't cover every
// timeframe this page offers (no 3m/1d) -- reusing that component as-is
// per this sprint's own scope (not extending its props/interval union),
// so a timeframe it can't render natively falls back to its closest
// supported neighbor for the CHART DISPLAY only. The structure signal
// itself is always computed at the real, exact selected timeframe --
// only the candlestick rendering underneath it is approximated.
const CHART_INTERVAL_FOR: Record<Timeframe, ChartInterval> = {
  '3m': '1m',
  '5m': '5m',
  '15m': '15m',
  '1h': '1h',
  '4h': '4h',
  '1d': '4h',
}

const DOT_CLASS: Record<string, string> = {
  GREEN: 'bg-bull',
  RED: 'bg-bear',
  GRAY: 'bg-hud-muted',
}

const READINESS_TONE: Record<StructureSignal['trade_readiness'], MetricTone> = {
  BUY_ARMED: 'good',
  SELL_ARMED: 'bad',
  WAIT: 'warn',
  LOW_QUALITY: 'warn',
  NO_CLEAR_BIAS: 'neutral',
}

const READINESS_LABEL: Record<StructureSignal['trade_readiness'], string> = {
  BUY_ARMED: 'Buy Armed',
  SELL_ARMED: 'Sell Armed',
  WAIT: 'Wait',
  LOW_QUALITY: 'Low Quality',
  NO_CLEAR_BIAS: 'No Clear Bias',
}

const TRAVEL_LABEL: Record<StructureSignal['travel_status'], string> = {
  WAITING: 'Waiting',
  ARMED: 'Armed',
  TRAIL_AFTER_TP1: 'Trail After TP1',
  TRAIL_TO_TP3: 'Trail To TP3',
  TP3_HIT: 'TP3 Hit / Exit Full',
  EXIT_PROTECT: 'Exit / Protect',
}

function fmtPrice(v: number | null | undefined): string {
  return v === null || v === undefined || Number.isNaN(v) ? DASH : v.toFixed(2)
}

/** `/structure` -- "Structure & Breakout Suite" Phase 1/2 (2026-08-29),
 * the dashboard half of the NSE Pro Smart Structure & Breakout Suite
 * PineScript port. Reuses LiveCandlestickChart.tsx for the actual
 * candles (unmodified -- see CHART_INTERVAL_FOR's own comment for the
 * one real limitation that comes with not extending it) plus the
 * MetricCard/PageHeader/SymbolSelector components every other Command
 * Center tool route already uses.
 *
 * This is a decision-support read, not a promise -- every value shown
 * here traces back to GET /api/structure/signal's own real computed
 * response, including its own `disclaimer` field, rendered verbatim
 * rather than paraphrased. Momentum diamonds, breakout triangles, and
 * TP2/TP3 are NOT drawn on the chart canvas itself in this phase (that
 * would mean extending LiveCandlestickChart's own marker API, out of
 * this sprint's "reuse existing chart primitives, don't redesign"
 * scope) -- they render as real, live status chips beside it instead. */
export function StructureBreakout() {
  const [symbol, setSymbol] = useState('RELIANCE')
  const [timeframe, setTimeframe] = useState<Timeframe>('15m')

  const { data: signal, loading } = usePolling<StructureSignal | null>(
    async () => (symbol ? fetchStructureSignal(symbol, timeframe) : null),
    15000,
    [symbol, timeframe],
  )

  const ready = signal?.ready ?? false
  const dot = signal?.visual_markers.dot ?? 'GRAY'
  const readiness = signal?.trade_readiness ?? 'NO_CLEAR_BIAS'

  return (
    <div className="flex flex-1 flex-col gap-6">
      <PageHeader
        icon={Crosshair}
        title="Structure & Breakout Suite"
        subtitle={
          ready
            ? `${symbol} · ${timeframe.toUpperCase()} · ${signal?.disclaimer}`
            : (signal?.reason ?? 'Loading structure signal…')
        }
      />

      <div className="flex flex-wrap items-center gap-3">
        <div className="w-56">
          <SymbolSelector value={symbol} onSelect={setSymbol} placeholder="Symbol" />
        </div>
        <div className="flex gap-1 rounded-lg border border-hud-border p-1">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              type="button"
              onClick={() => setTimeframe(tf)}
              className={
                'rounded-md px-3 py-1.5 text-xs font-bold uppercase tracking-wide transition-colors ' +
                (timeframe === tf
                  ? 'bg-bull/15 text-bull'
                  : 'text-hud-muted hover:bg-hud-panel-hover hover:text-hud-text')
              }
            >
              {tf}
            </button>
          ))}
        </div>
        <span className="flex items-center gap-1.5 text-[11px] text-hud-muted">
          <span className={'h-2.5 w-2.5 rounded-full ' + (DOT_CLASS[dot] ?? 'bg-hud-muted')} />
          {signal?.htf_timeframe ? `HTF ${signal.htf_timeframe.toUpperCase()}` : DASH}
        </span>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="rounded-xl border border-hud-border bg-hud-panel p-3">
          <LiveCandlestickChart symbol={symbol} interval={CHART_INTERVAL_FOR[timeframe]} />
        </div>

        <div className="flex flex-col gap-4">
          <div className="rounded-xl border border-hud-border bg-hud-panel p-4">
            <div className="text-[10px] font-bold uppercase tracking-wide text-hud-muted">
              Interpretation
            </div>
            {loading && !signal ? (
              <div className="mt-2 text-sm text-hud-muted">Loading…</div>
            ) : (
              <div className="mt-2 flex flex-col gap-1 font-mono text-sm text-hud-text">
                {(signal?.interpretation_label ?? ['WAIT', 'No data yet', DASH, DASH]).map(
                  (line, i) => (
                    <div key={i} className={i === 0 ? 'text-base font-bold' : 'text-hud-muted'}>
                      {line}
                    </div>
                  ),
                )}
              </div>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            {signal?.visual_markers.momentum_diamond && (
              <span
                className={
                  'rounded px-2 py-1 text-[10px] font-bold uppercase ' +
                  (signal.visual_markers.momentum_diamond === 'GREEN'
                    ? 'bg-bull/15 text-bull'
                    : 'bg-bear/15 text-bear')
                }
              >
                ◆ Momentum Watch
              </span>
            )}
            {signal?.visual_markers.breakout_triangle && (
              <span
                className={
                  'rounded px-2 py-1 text-[10px] font-bold uppercase ' +
                  (signal.visual_markers.breakout_triangle === 'GREEN'
                    ? 'bg-bull/15 text-bull'
                    : 'bg-bear/15 text-bear')
                }
              >
                ▲ Breakout Confirmed
              </span>
            )}
          </div>

          <div className="rounded-xl border border-hud-border bg-hud-panel p-4">
            <div className="text-[10px] font-bold uppercase tracking-wide text-hud-muted">
              Trigger &amp; Risk
            </div>
            <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
              <div>
                <div className="text-hud-muted">
                  {signal?.trigger_side === 'SELL_BELOW' ? 'Sell Below' : 'Buy Above'}
                </div>
                <div className="tnum font-mono font-bold text-hud-text">
                  {fmtPrice(signal?.trigger_price)}
                </div>
              </div>
              <div>
                <div className="text-hud-muted">Trigger Source</div>
                <div className="font-mono text-hud-text">{signal?.trigger_source ?? DASH}</div>
              </div>
              <div>
                <div className="text-hud-muted">Entry</div>
                <div className="tnum font-mono text-hud-text">{fmtPrice(signal?.entry)}</div>
              </div>
              <div>
                <div className="text-hud-muted">Strict SL</div>
                <div className="tnum font-mono text-bear">{fmtPrice(signal?.sl)}</div>
              </div>
              <div>
                <div className="text-hud-muted">TP1 (1.5R)</div>
                <div className="tnum font-mono text-bull">{fmtPrice(signal?.tp1)}</div>
              </div>
              <div>
                <div className="text-hud-muted">TP2 (2.5R)</div>
                <div className="tnum font-mono text-bull">{fmtPrice(signal?.tp2)}</div>
              </div>
              <div>
                <div className="text-hud-muted">TP3 (3.5R)</div>
                <div className="tnum font-mono text-bull">{fmtPrice(signal?.tp3)}</div>
              </div>
              <div>
                <div className="text-hud-muted">Risk / Share</div>
                <div className="tnum font-mono text-hud-text">
                  {fmtPrice(signal?.risk_per_share)}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-6">
        <MetricCard label="Bias" value={signal?.dominant_bias ?? DASH} tone={READINESS_TONE[readiness]} />
        <MetricCard
          label="Setup Quality"
          value={ready ? `B:${signal?.bull_score}/7 S:${signal?.bear_score}/7` : DASH}
        />
        <MetricCard
          label="Trade Readiness"
          value={READINESS_LABEL[readiness]}
          tone={READINESS_TONE[readiness]}
        />
        <MetricCard
          label="Momentum Watch"
          value={signal?.momentum_watch ? 'Watching' : 'No'}
          tone={signal?.momentum_watch ? 'warn' : 'neutral'}
        />
        <MetricCard
          label="Travel Status"
          value={signal ? TRAVEL_LABEL[signal.travel_status] : DASH}
        />
        <MetricCard label="Suggested Usage" value={signal?.suggested_usage ?? DASH} />
        <MetricCard
          label="200 EMA Trend"
          value={
            signal?.indicators.ema200 != null
              ? signal.ltp > signal.indicators.ema200
                ? 'Above'
                : 'Below'
              : DASH
          }
        />
        <MetricCard
          label="Higher TF Trend"
          value={
            signal?.indicators.htf_trend_state === 1
              ? 'Bullish'
              : signal?.indicators.htf_trend_state === -1
                ? 'Bearish'
                : 'Range'
          }
        />
        <MetricCard label="RSI (14)" value={fmtPrice(signal?.indicators.rsi14)} />
        <MetricCard label="MFI (14)" value={fmtPrice(signal?.indicators.mfi)} />
        <MetricCard label="Supertrend" value={signal?.indicators.supertrend ?? DASH} />
        <MetricCard label="RVOL" value={signal?.indicators.rvol != null ? `${signal.indicators.rvol.toFixed(2)}x` : DASH} />
      </div>
    </div>
  )
}
