import { Crosshair, FlaskConical } from 'lucide-react'
import { useEffect, useState } from 'react'
import { LiveCandlestickChart } from '../components/LiveCandlestickChart'
import { DASH, MetricCard, type MetricTone } from '../components/MetricCard'
import { PageHeader } from '../components/PageHeader'
import { SymbolSelector } from '../components/SymbolSelector'
import { usePolling } from '../hooks/usePolling'
import {
  fetchStructureBacktestRun,
  fetchStructureOptimize,
  fetchStructureOptimizeProgress,
  fetchStructureSignal,
  postStructureBacktestRun,
} from '../lib/api'
import type { ChartInterval } from '../lib/api'
import type {
  StructureBacktestRun,
  StructureOptimizedProfile,
  StructureOptimizeProgress,
  StructureOptimizeResult,
  StructureSignal,
} from '../types'

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

/** `/structure` -- "Structure & Breakout Suite," the dashboard half of
 * the NSE Pro Smart Structure & Breakout Suite PineScript port. Phase
 * 1/2 (2026-08-29) built the signal engine and this page; Phase 4
 * (2026-08-29) added the real on-canvas chart drawing (trigger/entry/
 * SL/TP1-3 lines, momentum/breakout markers) via LiveCandlestickChart's
 * own new `structureSignal` prop -- see that component's own Effect 6
 * for exactly what's drawn and why it doesn't collide with Effect 3's
 * separate broker-position lines or Effect 5's own SMC markers. The
 * status chips below the chart stay as real, live TEXT confirmation of
 * the same state (bias/quality/readiness/travel status aren't points on
 * a price series, so they were never candidates for on-canvas drawing
 * in the first place) -- reuses MetricCard/PageHeader/SymbolSelector
 * every other Command Center tool route already uses.
 *
 * This is a decision-support read, not a promise -- every value shown
 * here traces back to GET /api/structure/signal's own real computed
 * response, including its own `disclaimer` field, rendered verbatim
 * rather than paraphrased. */
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
          <LiveCandlestickChart
            symbol={symbol}
            interval={CHART_INTERVAL_FOR[timeframe]}
            structureSignal={signal}
          />
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

      <BacktestOptimizerPanel symbol={symbol} timeframe={timeframe} />
    </div>
  )
}

const CONFIDENCE_TONE: Record<StructureOptimizedProfile['confidence'], MetricTone> = {
  HIGH: 'good',
  MEDIUM: 'warn',
  LOW: 'bad',
}

function pct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined || Number.isNaN(v) ? DASH : `${v.toFixed(digits)}%`
}

function num(v: number | null | undefined, digits = 2): string {
  return v === null || v === undefined || Number.isNaN(v) ? DASH : v.toFixed(digits)
}

/** One symbol's params_used-style table -- reused for both the params
 * grid and, doubled up, for train-vs-test metric comparison below. */
function TrainTestRow({ label, train, test }: { label: string; train: string; test: string }) {
  return (
    <div className="grid grid-cols-3 gap-2 border-t border-hud-border py-1.5 text-xs first:border-t-0">
      <span className="text-hud-muted">{label}</span>
      <span className="tnum font-mono text-hud-text">{train}</span>
      <span className="tnum font-mono text-hud-text">{test}</span>
    </div>
  )
}

/** "Structure & Breakout Suite" Phase 3/4 (2026-08-29) -- the real
 * historical replay backtester and optimizer, added to the same page
 * the live signal renders on. Runs a fresh 1-year BOTH-sides backtest
 * for the CURRENT symbol/timeframe on demand (Phase 3's own real ~1
 * year Redis daily-bar depth, see StructureBreakout's own module
 * docstring), then reads back the Phase 4 optimizer's real, sampled
 * grid search over it. Every number here traces to a real backend
 * response -- "statistically stronger setup candidate," never "perfect"
 * or "guaranteed," per this whole suite's own established wording. */
function BacktestOptimizerPanel({ symbol, timeframe }: { symbol: string; timeframe: Timeframe }) {
  const [runId, setRunId] = useState<string | null>(null)
  const [launching, setLaunching] = useState(false)
  const [launchError, setLaunchError] = useState<string | null>(null)
  const [optimize, setOptimize] = useState<StructureOptimizeResult | null>(null)
  const [optimizing, setOptimizing] = useState(false)

  // Reset whenever the caller switches symbol/timeframe -- a run_id from
  // a different instrument has nothing to say about this one.
  const [prevKey, setPrevKey] = useState(`${symbol}:${timeframe}`)
  const key = `${symbol}:${timeframe}`
  if (key !== prevKey) {
    setPrevKey(key)
    setRunId(null)
    setOptimize(null)
    setLaunchError(null)
  }

  const { data: run } = usePolling<StructureBacktestRun | null>(
    async () => (runId ? fetchStructureBacktestRun(runId) : null),
    3000,
    [runId],
  )

  // Task 3's own "return progress information so the UI does not feel
  // stuck" ask -- polled from a SEPARATE request while the optimize
  // fetch below may still be blocked on a fresh search; the backend's
  // own periodic `asyncio.sleep(0)` yields keep it able to answer this
  // even mid-search. Only polls while `optimizing` is true, and stops
  // the moment it is (see the cleanup below).
  const { data: progress } = usePolling<StructureOptimizeProgress | null>(
    async () => (optimizing && runId ? fetchStructureOptimizeProgress(runId) : null),
    1500,
    [optimizing, runId],
  )

  // One-shot optimize fetch the moment the backtest itself finishes --
  // not on every 3s poll tick (the optimizer's own real search, or even
  // its cached read, is real work not worth repeating every poll).
  useEffect(() => {
    if (run?.status === 'DONE' && run.run_id && !optimize && !optimizing) {
      setOptimizing(true)
      void fetchStructureOptimize(run.run_id)
        .then(setOptimize)
        .finally(() => setOptimizing(false))
    }
  }, [run, optimize, optimizing])

  async function handleRunBacktest() {
    setLaunching(true)
    setLaunchError(null)
    setOptimize(null)
    const end = new Date()
    const start = new Date(end)
    start.setDate(start.getDate() - 365)
    const result = await postStructureBacktestRun({
      symbols: [symbol],
      timeframes: [timeframe],
      start_date: start.toISOString().slice(0, 10),
      end_date: end.toISOString().slice(0, 10),
      side: 'BOTH',
    })
    setLaunching(false)
    if (result.available && result.run_id) setRunId(result.run_id)
    else setLaunchError(result.reason ?? 'Could not start the backtest.')
  }

  const recommended = optimize?.recommended ?? null

  return (
    <div className="rounded-xl border border-hud-border bg-hud-panel p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <FlaskConical className="h-4 w-4 text-hud-muted" />
          <span className="text-[10px] font-bold uppercase tracking-wide text-hud-muted">
            Backtest &amp; Optimizer -- {symbol} · {timeframe.toUpperCase()} · last 365 days
          </span>
        </div>
        <button
          type="button"
          onClick={() => void handleRunBacktest()}
          disabled={launching || run?.status === 'RUNNING'}
          className="rounded-lg bg-bull/15 px-3 py-1.5 text-[11px] font-bold uppercase tracking-wide text-bull ring-1 ring-bull/50 transition-colors hover:bg-bull/25 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {launching || run?.status === 'RUNNING' ? 'Running…' : 'Run Backtest'}
        </button>
      </div>

      {launchError && <p className="mt-3 text-xs text-bear">{launchError}</p>}

      {run && (
        <div className="mt-3 text-xs text-hud-muted">
          Run <span className="font-mono text-hud-text">{run.run_id.slice(0, 8)}</span> --{' '}
          <span className={run.status === 'FAILED' ? 'text-bear' : 'text-hud-text'}>
            {run.status}
          </span>
          {run.status === 'DONE' && run.metrics && (
            <span>
              {' '}
              -- {run.metrics.overview.trade_count} real trade(s), net{' '}
              {num(run.metrics.overview.net_pnl_r)}R, win rate{' '}
              {pct(run.metrics.overview.win_rate_pct)}
            </span>
          )}
        </div>
      )}

      {optimizing && (
        <p className="mt-3 text-xs text-hud-muted">
          {progress?.available && progress.phase === 'precomputing_features'
            ? `Precomputing features -- ${progress.pairs_done ?? 0}/${progress.pairs_total ?? '?'} symbol/timeframe pair(s)…`
            : progress?.available && progress.phase === 'evaluating_combos'
              ? `Evaluating combinations -- ${progress.combos_done ?? 0}/${progress.combos_total ?? '?'} (${progress.elapsed_sec ?? 0}s elapsed)…`
              : 'Searching real settings…'}
        </p>
      )}

      {optimize && (
        <div className="mt-4 flex flex-col gap-4 border-t border-hud-border pt-4">
          <p className="text-xs text-hud-muted">{optimize.note}</p>

          {optimize.runtime?.is_intraday && (
            <div
              className={
                'rounded-lg px-3 py-2 text-xs ' +
                (optimize.runtime.runtime_guard_triggered
                  ? 'bg-amber-400/10 text-amber-400'
                  : 'bg-hud-panel-hover text-hud-muted')
              }
            >
              Intraday optimizer run -- {optimize.runtime.elapsed_sec}s elapsed,{' '}
              {optimize.runtime.max_combinations_applied} combination(s) capped
              {optimize.runtime.max_runtime_sec_guard != null &&
                ` (${optimize.runtime.max_runtime_sec_guard}s hard runtime guard)`}
              .
              {optimize.runtime.runtime_guard_triggered &&
                ' The runtime guard stopped this run early -- results reflect only the combinations actually evaluated.'}
            </div>
          )}

          {!!optimize.trigger_source_breakdown &&
            Object.keys(optimize.trigger_source_breakdown).length > 0 && (
              <div>
                <h3 className="mb-1 text-[10px] font-bold uppercase tracking-wide text-hud-muted">
                  Trigger-Source Breakdown
                </h3>
                <div className="grid grid-cols-4 gap-2 text-[10px] font-bold uppercase text-hud-muted">
                  <span>Source</span>
                  <span>Combos Tested</span>
                  <span>Test Trades</span>
                  <span>Survivors</span>
                </div>
                {Object.entries(optimize.trigger_source_breakdown).map(([source, row]) => (
                  <div
                    key={source}
                    className="grid grid-cols-4 gap-2 border-t border-hud-border py-1.5 text-xs first:border-t-0"
                  >
                    <span className="font-mono text-hud-text">{source}</span>
                    <span className="tnum font-mono text-hud-text">{row.combos_tested}</span>
                    <span className="tnum font-mono text-hud-text">{row.total_test_trades}</span>
                    <span className="tnum font-mono text-hud-text">{row.survivors}</span>
                  </div>
                ))}
              </div>
            )}

          {!recommended ? (
            <div className="flex flex-col gap-2">
              <div className="rounded-lg bg-bear/10 px-3 py-2 text-xs font-medium text-bear">
                No acceptable profile yet. Do not treat any tested candidate as a statistically
                stronger setup candidate -- widen the search or collect more real history.
              </div>
              {optimize.candidates.length > 0 && (
                <div>
                  <h3 className="mb-1 text-[10px] font-bold uppercase tracking-wide text-hud-muted">
                    Why profiles were rejected (top {Math.min(3, optimize.candidates.length)} by
                    robustness score)
                  </h3>
                  <div className="flex flex-col gap-2">
                    {optimize.candidates.slice(0, 3).map((c, i) => (
                      <div
                        key={i}
                        className="rounded-lg border border-hud-border px-3 py-2 text-[11px]"
                      >
                        <div className="font-mono text-hud-muted">
                          {c.params.trigger_source} · quality≥{c.params.min_setup_quality} ·{' '}
                          {c.params.trade_mode}
                        </div>
                        <ul className="mt-1 list-inside list-disc text-hud-muted">
                          {c.rejection_reasons.map((reason, j) => (
                            <li key={j}>{reason}</li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <span
                  className={
                    'rounded-full px-3 py-1 text-[11px] font-bold uppercase tracking-wide ring-1 ' +
                    (CONFIDENCE_TONE[recommended.confidence] === 'good'
                      ? 'bg-bull/15 text-bull ring-bull/50'
                      : CONFIDENCE_TONE[recommended.confidence] === 'warn'
                        ? 'bg-amber-400/15 text-amber-400 ring-amber-400/40'
                        : 'bg-bear/15 text-bear ring-bear/50')
                  }
                >
                  Confidence: {recommended.confidence}
                </span>
                <span className="text-[11px] text-hud-muted">
                  Statistically stronger setup candidate -- rank #{recommended.rank ?? 1} of{' '}
                  {optimize.survivor_count ?? 0} survivor(s), {optimize.sampled_combinations ?? 0}{' '}
                  of {optimize.full_grid_size ?? 0} combinations sampled
                </span>
              </div>

              {recommended.overfit_gap_r !== null && recommended.overfit_gap_r > 0.3 && (
                <div className="rounded-lg bg-amber-400/10 px-3 py-2 text-xs text-amber-400">
                  Overfit warning: train performed {num(recommended.overfit_gap_r)}R/trade better
                  than test. Treat this profile cautiously even though it survived.
                </div>
              )}

              <div>
                <h3 className="mb-1 text-[10px] font-bold uppercase tracking-wide text-hud-muted">
                  Train vs. Test
                </h3>
                <div className="grid grid-cols-3 gap-2 text-[10px] font-bold uppercase text-hud-muted">
                  <span />
                  <span>Train</span>
                  <span>Test (out-of-sample)</span>
                </div>
                <TrainTestRow
                  label="Trade count"
                  train={String(recommended.train_metrics.trade_count)}
                  test={String(recommended.test_metrics.trade_count)}
                />
                <TrainTestRow
                  label="Win rate"
                  train={pct(recommended.train_metrics.win_rate_pct)}
                  test={pct(recommended.test_metrics.win_rate_pct)}
                />
                <TrainTestRow
                  label="Profit factor"
                  train={num(recommended.train_metrics.profit_factor)}
                  test={num(recommended.test_metrics.profit_factor)}
                />
                <TrainTestRow
                  label="Expectancy (R)"
                  train={num(recommended.train_metrics.expectancy_r)}
                  test={num(recommended.test_metrics.expectancy_r)}
                />
                <TrainTestRow
                  label="Max drawdown (R)"
                  train={num(recommended.train_metrics.max_drawdown_r)}
                  test={num(recommended.test_metrics.max_drawdown_r)}
                />
                <TrainTestRow
                  label="Sharpe"
                  train={num(recommended.train_metrics.sharpe?.sharpe)}
                  test={num(recommended.test_metrics.sharpe?.sharpe)}
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <MetricCard
                  label="Consistency (symbols)"
                  value={pct(recommended.consistency_symbols * 100, 0)}
                />
                <MetricCard
                  label="Consistency (timeframes)"
                  value={pct(recommended.consistency_timeframes * 100, 0)}
                />
              </div>

              <div>
                <h3 className="mb-1 text-[10px] font-bold uppercase tracking-wide text-hud-muted">
                  Recommended Settings
                </h3>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
                  <span className="text-hud-muted">
                    Min Setup Quality: <span className="text-hud-text">{recommended.params.min_setup_quality}</span>
                  </span>
                  <span className="text-hud-muted">
                    Min Bias Edge: <span className="text-hud-text">{recommended.params.min_bias_edge}</span>
                  </span>
                  <span className="text-hud-muted">
                    Fast Trigger Lookback: <span className="text-hud-text">{recommended.params.fast_trigger_lookback}</span>
                  </span>
                  <span className="text-hud-muted">
                    ATR Breakout Buffer: <span className="text-hud-text">{recommended.params.atr_breakout_buffer}</span>
                  </span>
                  <span className="text-hud-muted">
                    Strict Stop Max ATR: <span className="text-hud-text">{recommended.params.strict_stop_max_atr}</span>
                  </span>
                  <span className="text-hud-muted">
                    TP1 / TP2 / TP3: <span className="text-hud-text">{recommended.params.tp1_r}R / {recommended.params.tp2_r}R / {recommended.params.tp3_r}R</span>
                  </span>
                  <span className="text-hud-muted">
                    Trade Mode: <span className="text-hud-text">{recommended.params.trade_mode}</span>
                  </span>
                  <span className="text-hud-muted">
                    Trigger Source: <span className="text-hud-text">{recommended.params.trigger_source}</span>
                  </span>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
