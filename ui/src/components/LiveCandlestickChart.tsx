import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  TickMarkType,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LineData,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef, type MutableRefObject } from 'react'
import { useActivePosition } from '../hooks/useActivePosition'
import { useHistoricalData } from '../hooks/useHistoricalData'
import { usePolling } from '../hooks/usePolling'
import { fetchSmcGeometry, fetchTradeBlueprint, type ChartInterval } from '../lib/api'
import { CHART_BEAR, CHART_BULL } from '../lib/chartTheme'
import { useUiEngineStore } from '../store/useUiEngineStore'
import type { ChartBar, StructureSignal } from '../types'

// "TradingView Parity" sprint (2026-08-29): the NSE session (09:15-
// 15:30) is defined in IST -- this chart must read in IST regardless of
// the viewer's own browser/OS timezone (this app's own backend already
// treats IST as the one true clock everywhere, e.g. trade_blueprint.py's
// `_IST = ZoneInfo("Asia/Kolkata")`; the frontend axis had never
// actually enforced the same thing, just inherited whatever timezone
// the browser happened to be in). lightweight-charts' own `Time` union
// includes string/BusinessDay variants this chart never actually
// produces -- every bar this app ever sets is a plain Unix-seconds
// UTCTimestamp, so this narrows to that one real case rather than
// handling all three.
function istDate(time: Time): Date | null {
  return typeof time === 'number' ? new Date(time * 1000) : null
}

const IST_TIME_ZONE = 'Asia/Kolkata'
const IST_HHMM = new Intl.DateTimeFormat('en-GB', {
  timeZone: IST_TIME_ZONE,
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})
const IST_DAY_MONTH = new Intl.DateTimeFormat('en-GB', {
  timeZone: IST_TIME_ZONE,
  day: '2-digit',
  month: 'short',
})
const IST_MONTH_YEAR = new Intl.DateTimeFormat('en-GB', {
  timeZone: IST_TIME_ZONE,
  month: 'short',
  year: '2-digit',
})
const IST_YEAR = new Intl.DateTimeFormat('en-GB', {
  timeZone: IST_TIME_ZONE,
  year: 'numeric',
})
const IST_CROSSHAIR = new Intl.DateTimeFormat('en-GB', {
  timeZone: IST_TIME_ZONE,
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

// The actual VISIBLE x-axis ticks -- lightweight-charts' own
// `localization.timeFormatter` (below) only overrides the crosshair
// tooltip, not the axis itself; this app's ask ("the x-axis always
// aligns with IST") needs `timeScale.tickMarkFormatter` too, or the
// tooltip would read IST while the axis underneath it kept reading
// browser-local time.
function istTickMarkFormatter(time: Time, tickMarkType: TickMarkType): string | null {
  const date = istDate(time)
  if (!date) return null
  switch (tickMarkType) {
    case TickMarkType.Year:
      return IST_YEAR.format(date)
    case TickMarkType.Month:
      return IST_MONTH_YEAR.format(date)
    case TickMarkType.DayOfMonth:
      return IST_DAY_MONTH.format(date)
    default:
      return IST_HHMM.format(date)
  }
}

function istCrosshairTimeFormatter(time: Time): string {
  const date = istDate(time)
  return date ? IST_CROSSHAIR.format(date) : ''
}

// "Institutional Chart Overlay" sprint (2026-08-28) -- distinct from
// CHART_BULL/CHART_BEAR (the candle body colors) so a liquidity-sweep
// marker never reads as "just another candle color." Amber for a
// liquidity sweep specifically (neither a clean bull nor bear signal --
// it's a stop-run, direction TBD until price actually breaks
// structure) matches this app's own established amber-for-caution use
// (Screener.tsx's IV Rank "elevated" tier).
const SWEEP_MARKER_COLOR = '#f59e0b' // Tailwind amber-400 -- see IV Rank badge, Screener.tsx
const OB_BULLISH_COLOR = CHART_BULL
const OB_BEARISH_COLOR = CHART_BEAR
const TARGET_ZONE_COLOR = '#fbbf24' // matches --color-horizon-btst
// "TradingView Parity" sprint (2026-08-29) -- a subtler hue than the
// solid CHART_BULL/CHART_BEAR candle colors ("subtle green/red," per
// this sprint's own ask): the trendline is a projection, not a real
// price level the way an Order Block or target zone is, so it reads as
// a lighter-weight, deliberately less assertive line on the chart.
const TRENDLINE_BULLISH_COLOR = '#4ade80' // Tailwind green-400
const TRENDLINE_BEARISH_COLOR = '#f87171' // Tailwind red-400

// "Structure & Breakout Suite" Phase 4 (2026-08-29) -- the structure
// signal's own trigger/entry/SL/TP1-3 lines are a DIFFERENT real
// position than whatever useActivePosition/brokerPosition resolves
// (Effect 3 below): a symbol can have both a real open broker position
// AND a live structure-signal reading at once, and conflating their
// lines would misrepresent one as the other. Amber for the breakout
// trigger (matches SWEEP_MARKER_COLOR's own "not yet resolved either
// way" amber convention); entry/SL/TP1-3 reuse the same white/red/
// bull-or-bear-by-direction convention Effect 3's own broker-position
// lines already establish for the same real concepts.
const STRUCT_TRIGGER_LINE_COLOR = '#f59e0b' // Tailwind amber-400, matches SWEEP_MARKER_COLOR

const ENTRY_LINE_COLOR = '#FFFFFF'
const CHANNEL_LINE_COLOR = '#6b7684' // matches --color-hud-muted -- deliberately
// muted/neutral so the Donchian channel bounds read as a wider, softer
// context band, not competing visually with the sharper green/red
// support & resistance lines or the white/red/green position lines.

// This sprint's own strict instruction -- gray-900, not this app's
// usual --color-hud-bg/--color-hud-panel tokens. Scoped to this one
// chart, same "the user's explicit words win for the new visual work,
// existing UI stays on its own tokens" reasoning the Data Studio
// overhaul's chartTheme.ts already used for CHART_BULL/CHART_BEAR.
const CHART_BACKGROUND = '#111827'
const AXIS_TEXT_COLOR = '#6b7684' // matches --color-hud-muted

function toCandle(bar: ChartBar): CandlestickData<UTCTimestamp> {
  return {
    time: bar.time as UTCTimestamp,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
  }
}

/** "Black Screen Crash" fix (2026-08-29): a bar with any non-finite
 * OHLC/time field (NaN, +/-Infinity -- e.g. a genuinely malformed
 * upstream reading, or a symbol switch resolving an in-flight fetch
 * for a stale request) would make `candleSeries.setData()`/`update()`
 * throw synchronously; that throw, uncaught inside this render effect,
 * used to unmount the whole page before this sprint's own
 * ErrorBoundary existed. Dropping the bad bar here is the honest fix
 * either way -- a fabricated candle isn't the alternative to a crash. */
function isFiniteBar(bar: ChartBar): boolean {
  return (
    Number.isFinite(bar.time) &&
    Number.isFinite(bar.open) &&
    Number.isFinite(bar.high) &&
    Number.isFinite(bar.low) &&
    Number.isFinite(bar.close) &&
    Number.isFinite(bar.volume)
  )
}

/** Shared by Effects 3-5 below (previously a local closure only Effect
 * 4 had) -- a null/undefined price removes that specific line rather
 * than drawing a fabricated one at 0, the same honesty rule both the
 * position overlay and the MTF structure overlay need. "Black Screen
 * Crash" fix (2026-08-29): also treats a non-finite number (NaN,
 * +/-Infinity) as "remove the line" -- lightweight-charts' own
 * `createPriceLine`/`applyOptions` throw on a non-finite price, and an
 * uncaught throw inside a render effect (before this sprint's own
 * ErrorBoundary) unmounted the whole page. `Number.isFinite` narrows
 * `number` on its own, no separate null check needed for that branch. */
function syncPriceLine(
  candleSeries: ISeriesApi<'Candlestick'>,
  ref: MutableRefObject<IPriceLine | null>,
  price: number | null | undefined,
  opts: { color: string; lineWidth: 1 | 2; lineStyle: LineStyle; title: string },
): void {
  if (price === null || price === undefined || !Number.isFinite(price)) {
    if (ref.current) {
      candleSeries.removePriceLine(ref.current)
      ref.current = null
    }
    return
  }
  if (ref.current) ref.current.applyOptions({ price, ...opts })
  else ref.current = candleSeries.createPriceLine({ price, ...opts })
}

function toVolume(bar: ChartBar): HistogramData<UTCTimestamp> {
  return {
    time: bar.time as UTCTimestamp,
    value: bar.volume,
    color: bar.close >= bar.open ? `${CHART_BULL}80` : `${CHART_BEAR}80`,
  }
}

/** Real entry/stop/target1 to overlay -- each independently nullable,
 * since a real broker position's own invalidation_level/target_primary
 * can genuinely be unavailable (no fabricated 0 line gets drawn for a
 * level the backend never computed). "Visual Tracking & Lifecycle"
 * sprint (2026-08-27), embedding this chart into the Active Cockpit's
 * PositionIntelligenceCard. */
export interface PositionOverlay {
  entry: number | null
  stop: number | null
  target1: number | null
}

interface LiveCandlestickChartProps {
  symbol: string
  // Miniature instance inside a grid card (PositionIntelligenceCard)
  // vs. the original full-size Sniper HUD pane -- same component,
  // different footprint. Defaults to the original h-80 so every
  // existing caller is pixel-identical to before this prop existed.
  heightClassName?: string
  // When provided, overrides useActivePosition's own journal lookup for
  // the ENTRY/STOP LOSS/TARGET overlay lines -- a real open broker
  // position (Active Cockpit) is ground truth for itself and shouldn't
  // wait on a matching (or possibly absent/stale) journal row to know
  // its own entry/stop/target. Sniper HUD's own usage never passes
  // this, so it keeps resolving those lines from the journal exactly
  // as it did before this prop existed.
  brokerPosition?: PositionOverlay | null
  // "Unified Screener & Deep-Dive Interactivity" sprint (2026-08-28) --
  // controlled, not internal state: the caller owns which timeframe is
  // selected (and renders whatever toggle UI makes sense for its own
  // layout, e.g. the fullscreen chart modal's own top bar) rather than
  // this component inventing its own toggle chrome for every context it
  // might be embedded in. Defaults to '1m', matching every caller from
  // before this prop existed.
  interval?: ChartInterval
  // "Structure & Breakout Suite" Phase 4 (2026-08-29) -- when provided,
  // draws the real structure signal's own BUY ABOVE/SELL BELOW trigger
  // line, entry/SL/TP1-3 (once its own candle has confirmed), and its
  // momentum/breakout markers directly on this chart. Optional and
  // independent of brokerPosition/activePosition above -- a symbol can
  // have both a real open position AND a live structure-signal reading
  // showing at once. null/undefined draws none of it, same as every
  // other optional overlay this component already has.
  structureSignal?: StructureSignal | null
}

/** Live 1-min candlestick chart for the Sniper HUD (2026-08-27 charting
 * sprint) -- lightweight-charts v5's `chart.addSeries(SeriesDefinition,
 * options)` API (v3/v4's `addCandlestickSeries()` method was removed in
 * v5), real OHLCV from the already-shipped GET /api/chart/{symbol}/
 * intraday (no new backend route needed for this).
 *
 * Two effects, deliberately separate:
 * 1. Chart/series creation -- runs once per mount, `chart.remove()` on
 *    unmount is the actual cleanup lightweight-charts' own docs specify
 *    to avoid leaking the chart's internal canvas/RAF loop; `autoSize`
 *    handles container resize without a manually-managed
 *    ResizeObserver.
 * 2. Data binding -- runs per `bars` change. First load for a symbol
 *    uses `setData()` (and fits the visible range); every poll after
 *    that calls `update()` only for bars at/after the last-seen
 *    timestamp (the forming current-minute candle plus any bar that
 *    completed since the last poll), which is what actually makes the
 *    chart feel "live" without resetting the user's zoom/pan on every
 *    10s refresh the way a repeated setData() would.
 */
export function LiveCandlestickChart({
  symbol,
  heightClassName = 'h-80',
  brokerPosition,
  interval = '1m',
  structureSignal,
}: LiveCandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const isFirstLoadRef = useRef(true)
  const lastBarTimeRef = useRef<number | null>(null)
  const entryLineRef = useRef<IPriceLine | null>(null)
  const slLineRef = useRef<IPriceLine | null>(null)
  const targetLineRef = useRef<IPriceLine | null>(null)
  const supportLineRef = useRef<IPriceLine | null>(null)
  const resistanceLineRef = useRef<IPriceLine | null>(null)
  const channelUpperLineRef = useRef<IPriceLine | null>(null)
  const channelLowerLineRef = useRef<IPriceLine | null>(null)
  // "Institutional Chart Overlay" sprint (2026-08-28) -- lightweight-
  // charts v5 moved markers out of the series itself: there's no
  // `series.setMarkers()` anymore, only a `createSeriesMarkers(series)`
  // plugin object that owns its own `.setMarkers()` (verified against
  // this app's actual installed v5.2.1 typings, not assumed from
  // memory of the older v3/v4 API the sprint's own instructions named).
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const obBullishLineRef = useRef<IPriceLine | null>(null)
  const obBearishLineRef = useRef<IPriceLine | null>(null)
  const targetT2LineRef = useRef<IPriceLine | null>(null)
  // "TradingView Parity" sprint (2026-08-29) -- v5's real line-series
  // API is `chart.addSeries(LineSeries, options)`, matching this file's
  // own already-established CandlestickSeries/HistogramSeries pattern
  // (NOT `chart.addLineSeries()`, the removed v3/v4 method the sprint's
  // own instructions named -- same situation as createSeriesMarkers
  // above). Created lazily (on first real trendline data), since unlike
  // the candle/volume series this one doesn't always exist.
  const trendlineSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  // "Structure & Breakout Suite" Phase 4 (2026-08-29) -- the structure
  // signal's own 6 price lines, independent of Effect 3's broker-
  // position lines (see structureSignal's own prop comment above).
  const structTriggerLineRef = useRef<IPriceLine | null>(null)
  const structEntryLineRef = useRef<IPriceLine | null>(null)
  const structSlLineRef = useRef<IPriceLine | null>(null)
  const structTp1LineRef = useRef<IPriceLine | null>(null)
  const structTp2LineRef = useRef<IPriceLine | null>(null)
  const structTp3LineRef = useRef<IPriceLine | null>(null)
  // Effect 5's own SMC markers (BOS/CHOCH/sweep) and Effect 6's new
  // structure-signal markers (momentum/breakout) both draw onto the ONE
  // real markers plugin lightweight-charts allows per series -- each
  // effect owns its own half of the combined set in its own ref and
  // calls applyMarkers() (below) to merge+redraw both together, so
  // neither effect's own update ever silently erases the other's.
  const smcMarkersRef = useRef<SeriesMarker<Time>[]>([])
  const structMarkersRef = useRef<SeriesMarker<Time>[]>([])
  // Perfect-fit, once per symbol/interval: fitContent() already runs
  // once when bars first load (Effect 2 below); this fires it ONE more
  // time after the SMC markers/trendline first arrive for this same
  // symbol/interval, in case that poll resolves after the bars did and
  // a trendline's own projected value sits outside the candles' own
  // price range. Deliberately NOT on every subsequent 20s SMC poll --
  // that would yank the user's own zoom/pan back to fit-all on every
  // routine refresh, undoing Effect 2's own careful update()-not-
  // setData() UX.
  const hasFitSmcRef = useRef(false)

  // Merges smcMarkersRef + structMarkersRef and redraws the one real
  // marker set the plugin owns -- see structMarkersRef's own comment
  // above for why neither ref's owning effect calls setMarkers()
  // directly. lightweight-charts requires markers sorted ascending by
  // time, same requirement Effect 5 already honored on its own before
  // this merge existed.
  function applyMarkers(): void {
    const merged = [...smcMarkersRef.current, ...structMarkersRef.current].sort(
      (a, b) => (a.time as number) - (b.time as number),
    )
    markersPluginRef.current?.setMarkers(merged)
  }

  const { bars, loading, error } = useHistoricalData(symbol, interval)
  const journalPosition = useActivePosition(symbol)
  // A real broker position (when passed) is ground truth for its own
  // entry/stop/target and takes precedence over the journal lookup --
  // see PositionOverlay's own docstring above.
  const activePosition: PositionOverlay | null = brokerPosition ?? journalPosition
  // Same 5s cadence ActionCard already polls TradeBlueprint at -- this
  // chart fetches its own copy rather than requiring the parent to plumb
  // it through as a prop, matching useHistoricalData/useActivePosition's
  // own "self-contained" shape.
  const { data: blueprint } = usePolling(() => fetchTradeBlueprint(symbol), 5000, [symbol])
  const structure = blueprint?.structure ?? null
  // "Institutional Chart Overlay" sprint (2026-08-28) -- real SMC
  // geometry, its own fetch keyed on symbol (refetches whenever the
  // symbol prop changes, same shape as `blueprint` above). 20s, not 5s:
  // this is a batch replay over ~3000 real 1-minute bars per call (see
  // api/smc_geometry.py's own docstring) -- real BOS/CHOCH/OB state
  // only ever changes once a full bar closes, so polling it as fast as
  // the position blueprint would be pure added backend load for no
  // fresher data. "TradingView Parity" sprint (2026-08-29): now also
  // keyed on `interval`, matching `bars` above -- the backend aggregates
  // to that same timeframe before recomputing geometry (see
  // fetchSmcGeometry's own comment), so switching the timeframe toggle
  // refetches BOTH the candles and a genuinely different structural
  // read, trendlines included.
  const { data: smc } = usePolling(() => fetchSmcGeometry(symbol, interval), 20000, [
    symbol,
    interval,
  ])

  // Effect 1: create once, tear down on unmount. Wrapped in try/catch
  // purely to feed useUiEngineStore's real chart-health probe ("Terminal
  // Edge & Analyst" sprint's Admin Terminal) -- lightweight-charts
  // itself doesn't normally throw here, but if container sizing or a
  // future options change ever does, the Admin Terminal should see a
  // real failure instead of staying silent about it.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    try {
      const chart = createChart(container, {
        autoSize: true,
        layout: {
          background: { type: ColorType.Solid, color: CHART_BACKGROUND },
          textColor: AXIS_TEXT_COLOR,
        },
        grid: {
          vertLines: { visible: false },
          horzLines: { visible: false },
        },
        crosshair: { mode: CrosshairMode.Normal },
        rightPriceScale: { borderVisible: false },
        // "TradingView Parity" sprint (2026-08-29): strictly IST,
        // regardless of the viewer's own browser/OS timezone -- see
        // istTickMarkFormatter's own comment for why BOTH of these are
        // needed (timeFormatter alone only overrides the crosshair
        // tooltip, not the visible axis ticks underneath it).
        localization: { timeFormatter: istCrosshairTimeFormatter },
        timeScale: {
          borderVisible: false,
          timeVisible: true,
          secondsVisible: false,
          tickMarkFormatter: istTickMarkFormatter,
        },
      })

      const candleSeries = chart.addSeries(CandlestickSeries, {
        upColor: CHART_BULL,
        downColor: CHART_BEAR,
        borderUpColor: CHART_BULL,
        borderDownColor: CHART_BEAR,
        wickUpColor: CHART_BULL,
        wickDownColor: CHART_BEAR,
      })

      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
      })
      // Overlay the volume series into the bottom ~20% of the same pane
      // rather than a separate chart pane -- "a histogramSeries at the
      // bottom of the chart pane," read literally.
      chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })

      // "Institutional Chart Overlay" sprint (2026-08-28) -- v5's
      // markers plugin, created once alongside the series it annotates
      // (see markersPluginRef's own comment above for why this isn't
      // `candleSeries.setMarkers()`).
      markersPluginRef.current = createSeriesMarkers(candleSeries, [])

      chartRef.current = chart
      candleSeriesRef.current = candleSeries
      volumeSeriesRef.current = volumeSeries
      useUiEngineStore.getState().reportChartEngine('ok')
    } catch (err) {
      useUiEngineStore
        .getState()
        .reportChartEngine('error', err instanceof Error ? err.message : String(err))
    }

    return () => {
      try {
        chartRef.current?.remove()
      } catch (err) {
        // Real, live bug (2026-08-31): switching symbols on
        // /analytics?symbol=OBEROIRLTY threw "Failed to execute
        // 'removeChild' on 'Node': the node to be removed is not a
        // child of this node" out of THIS cleanup -- with `autoSize:
        // true`, lightweight-charts attaches its own ResizeObserver to
        // `container`; the parent Chart ErrorBoundary above is keyed on
        // `activeSymbol` (see OptionsAnalytics.tsx), so a symbol switch
        // unmounts this whole subtree, and that observer can still fire
        // (or lightweight-charts' own internal teardown can still run)
        // in the brief window after React has already detached
        // `container` from the document but before this cleanup's own
        // chart.remove() call finishes -- a benign DOM-timing race, not
        // a real data/render bug. Because it's thrown from a cleanup
        // running as PART of this keyed subtree's own unmount, the
        // nearest boundary that can catch it is the page-level "Options
        // Analytics" one, not the "Chart" one built specifically to
        // contain exactly this kind of failure -- taking the ENTIRE
        // page down over a chart that was already being thrown away.
        // Swallowed here (never silently ignored -- logged) rather than
        // letting a teardown-only race crash a page that has nothing
        // left to actually render wrong.
        console.warn('[LiveCandlestickChart] chart.remove() threw during unmount', err)
      }
      chartRef.current = null
      candleSeriesRef.current = null
      volumeSeriesRef.current = null
      // chart.remove() already tears down every price line attached to
      // it -- these refs just shouldn't outlive the series they point
      // into.
      entryLineRef.current = null
      slLineRef.current = null
      targetLineRef.current = null
      supportLineRef.current = null
      resistanceLineRef.current = null
      channelUpperLineRef.current = null
      channelLowerLineRef.current = null
      markersPluginRef.current = null
      obBullishLineRef.current = null
      obBearishLineRef.current = null
      targetT2LineRef.current = null
      trendlineSeriesRef.current = null
      structTriggerLineRef.current = null
      structEntryLineRef.current = null
      structSlLineRef.current = null
      structTp1LineRef.current = null
      structTp2LineRef.current = null
      structTp3LineRef.current = null
      smcMarkersRef.current = []
      structMarkersRef.current = []
    }
  }, [])

  // Reset the incremental-update bookkeeping whenever the symbol OR the
  // selected timeframe changes -- either one means the next `bars`
  // payload is a different dataset (different instrument, or the same
  // instrument at a different candle width) that must fully replace
  // what's drawn, never partially patch onto it.
  useEffect(() => {
    isFirstLoadRef.current = true
    lastBarTimeRef.current = null
    candleSeriesRef.current?.setData([])
    volumeSeriesRef.current?.setData([])

    // The old symbol's price lines (if any) belong to a different
    // instrument's entry/SL/target -- drop them immediately rather than
    // leaving them on screen until Effect 3 catches up with the new
    // symbol's own active-position data.
    const candleSeries = candleSeriesRef.current
    if (candleSeries) {
      if (entryLineRef.current) candleSeries.removePriceLine(entryLineRef.current)
      if (slLineRef.current) candleSeries.removePriceLine(slLineRef.current)
      if (targetLineRef.current) candleSeries.removePriceLine(targetLineRef.current)
      if (supportLineRef.current) candleSeries.removePriceLine(supportLineRef.current)
      if (resistanceLineRef.current) candleSeries.removePriceLine(resistanceLineRef.current)
      if (channelUpperLineRef.current) candleSeries.removePriceLine(channelUpperLineRef.current)
      if (channelLowerLineRef.current) candleSeries.removePriceLine(channelLowerLineRef.current)
      if (obBullishLineRef.current) candleSeries.removePriceLine(obBullishLineRef.current)
      if (obBearishLineRef.current) candleSeries.removePriceLine(obBearishLineRef.current)
      if (targetT2LineRef.current) candleSeries.removePriceLine(targetT2LineRef.current)
      if (structTriggerLineRef.current) candleSeries.removePriceLine(structTriggerLineRef.current)
      if (structEntryLineRef.current) candleSeries.removePriceLine(structEntryLineRef.current)
      if (structSlLineRef.current) candleSeries.removePriceLine(structSlLineRef.current)
      if (structTp1LineRef.current) candleSeries.removePriceLine(structTp1LineRef.current)
      if (structTp2LineRef.current) candleSeries.removePriceLine(structTp2LineRef.current)
      if (structTp3LineRef.current) candleSeries.removePriceLine(structTp3LineRef.current)
    }
    // The old symbol's BOS/CHOCH/sweep/structure-signal markers and
    // trendline likewise belong to a different instrument/timeframe --
    // clear immediately rather than leaving stale ones up until the
    // next SMC/structure-signal poll lands.
    smcMarkersRef.current = []
    structMarkersRef.current = []
    markersPluginRef.current?.setMarkers([])
    trendlineSeriesRef.current?.setData([])
    hasFitSmcRef.current = false
    entryLineRef.current = null
    slLineRef.current = null
    targetLineRef.current = null
    supportLineRef.current = null
    resistanceLineRef.current = null
    channelUpperLineRef.current = null
    channelLowerLineRef.current = null
    obBullishLineRef.current = null
    obBearishLineRef.current = null
    targetT2LineRef.current = null
    structTriggerLineRef.current = null
    structEntryLineRef.current = null
    structSlLineRef.current = null
    structTp1LineRef.current = null
    structTp2LineRef.current = null
    structTp3LineRef.current = null
  }, [symbol, interval])

  // Effect 2: bind fetched bars into the series. "Black Screen Crash"
  // fix (2026-08-29): filtered through isFiniteBar first -- see that
  // function's own comment -- so a malformed bar is silently dropped
  // rather than thrown at lightweight-charts raw.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current
    const volumeSeries = volumeSeriesRef.current
    const validBars = bars.filter(isFiniteBar)
    if (!candleSeries || !volumeSeries || validBars.length === 0) return

    if (isFirstLoadRef.current) {
      candleSeries.setData(validBars.map(toCandle))
      volumeSeries.setData(validBars.map(toVolume))
      chartRef.current?.timeScale().fitContent()
      isFirstLoadRef.current = false
    } else {
      const since = lastBarTimeRef.current ?? -Infinity
      for (const bar of validBars) {
        if (bar.time < since) continue
        candleSeries.update(toCandle(bar))
        volumeSeries.update(toVolume(bar))
      }
    }
    lastBarTimeRef.current = validBars[validBars.length - 1].time
  }, [bars])

  // Effect 3: Active Trade Chart Overlay ("Terminal Edge" sprint,
  // 2026-08-27; widened "Visual Tracking & Lifecycle" sprint, 2026-08-27,
  // to accept a real broker position in place of the journal lookup and
  // to drop each line independently null-safe rather than all-or-
  // nothing) -- dashed white ENTRY, solid red STOP LOSS, solid green
  // TARGET. create-once + applyOptions() on later changes (a position's
  // own entry/SL/target rarely move once staged, but this stays correct
  // if a journal row is ever edited); each line is removed the moment
  // its own price goes missing, not left stale on screen.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current
    if (!candleSeries) return

    syncPriceLine(candleSeries, entryLineRef, activePosition?.entry, {
      color: ENTRY_LINE_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: 'ENTRY',
    })
    syncPriceLine(candleSeries, slLineRef, activePosition?.stop, {
      color: CHART_BEAR,
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      title: 'STOP LOSS',
    })
    syncPriceLine(candleSeries, targetLineRef, activePosition?.target1, {
      color: CHART_BULL,
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      title: 'TARGET',
    })
  }, [activePosition])

  // Effect 4: "Terminal Edge & Analyst" sprint (2026-08-27) -- HTF
  // Support (green) / Resistance (red) from the real fractal-pivot
  // "Major Blocker" read, Channel upper/lower (muted, dashed) from the
  // real Donchian channel -- see TradeStructure's own docstring for the
  // exact backend source of each. A null field (upstream has no data
  // yet) removes that specific line rather than drawing a fabricated
  // one at 0.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current
    if (!candleSeries) return

    syncPriceLine(candleSeries, supportLineRef, structure?.support, {
      color: CHART_BULL,
      lineWidth: 1,
      lineStyle: LineStyle.Solid,
      title: 'HTF SUPPORT',
    })
    syncPriceLine(candleSeries, resistanceLineRef, structure?.resistance, {
      color: CHART_BEAR,
      lineWidth: 1,
      lineStyle: LineStyle.Solid,
      title: 'HTF RESISTANCE',
    })
    syncPriceLine(candleSeries, channelUpperLineRef, structure?.channel_upper, {
      color: CHANNEL_LINE_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: 'CHANNEL UPPER',
    })
    syncPriceLine(candleSeries, channelLowerLineRef, structure?.channel_lower, {
      color: CHANNEL_LINE_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: 'CHANNEL LOWER',
    })
  }, [structure])

  // Effect 5: "Institutional Chart Overlay" sprint (2026-08-28), de-
  // cluttered by "UI Cleanup, Symbol Sync & SMC Clutter Filtering"
  // (2026-08-28) -- real SMC geometry from GET /api/chart/smc (see
  // SmcGeometry's own type comment and api/smc_geometry.py's module
  // docstring for exactly what's real vs. disclosed-absent here).
  // `smc?.ready` false (not enough closed bars yet, or no symbol
  // resolvable) clears every marker/line from this effect rather than
  // leaving a stale prior symbol's geometry on screen. The backend
  // itself now caps bos_choch_events/liquidity_sweeps at 8 (down from
  // 50 -- see MAX_EVENTS's own comment in smc_geometry.py); this effect
  // no longer needs its own separate cap on top of that.
  //
  // Markers: compact abbreviations (BOS/CH/SW), not full sentences --
  // "Bullish CHOCH" as marker text overlapped neighboring markers on a
  // real 1-minute chart with several events close together. Direction
  // still reads from shape + color (arrowUp/arrowDown, this app's own
  // established CHART_BULL/CHART_BEAR pair) without needing the word
  // spelled out; liquidity sweeps keep their own amber circle so they
  // never read as a third BOS/CHOCH color. lightweight-charts requires
  // markers sorted ascending by time -- sorted here on the already-
  // numeric source `time` field to avoid an unsound cast on
  // lightweight-charts' own wider `Time` union (which includes string/
  // BusinessDay variants this chart never actually uses).
  //
  // Price lines: cut from 6 to 3 -- FVG bullish/bearish and the T3
  // target dropped entirely (not in this sprint's own explicit
  // whitelist: nearest active Order Block per direction, HTF Support/
  // Resistance -- Effect 4's own, untouched -- and one Target Zone).
  // The single target line keeps T2 (1.618), the nearer and more
  // commonly-referenced Fibonacci target, over T3's further 2.618
  // stretch target. Order Blocks keep the SAME proximal-edge convention
  // infusion_models.smc's own nearest_ob_or_fvg_level() already
  // established elsewhere in this app (bullish zones quote their HIGH,
  // bearish their LOW).
  useEffect(() => {
    const candleSeries = candleSeriesRef.current
    const markersPlugin = markersPluginRef.current
    if (!candleSeries || !markersPlugin) return

    if (!smc?.ready) {
      smcMarkersRef.current = []
      applyMarkers()
    } else {
      type RawEvent =
        | { kind: 'bos'; time: number; direction: 'bullish' | 'bearish'; label: string }
        | { kind: 'sweep'; time: number; side: 'buyside' | 'sellside' }
      const raw: RawEvent[] = [
        ...(smc.bos_choch_events ?? []).map(
          (e): RawEvent => ({ kind: 'bos', time: e.time, direction: e.direction, label: e.label }),
        ),
        ...(smc.liquidity_sweeps ?? []).map(
          (s): RawEvent => ({ kind: 'sweep', time: s.time, side: s.side }),
        ),
      ].sort((a, b) => a.time - b.time)

      const markers: SeriesMarker<Time>[] = raw.map((e) =>
        e.kind === 'bos'
          ? {
              time: e.time as UTCTimestamp,
              position: e.direction === 'bullish' ? 'belowBar' : 'aboveBar',
              color: e.direction === 'bullish' ? CHART_BULL : CHART_BEAR,
              shape: e.direction === 'bullish' ? 'arrowUp' : 'arrowDown',
              // "BOS" or "CH" (CHOCH) -- e.label is always "Bullish/
              // Bearish BOS" or "Bullish/Bearish CHOCH" (see
              // SmcBosChochEvent's own type), so the second word alone
              // is the compact abbreviation; direction is already
              // carried by the arrow + color above.
              text: e.label.endsWith('BOS') ? 'BOS' : 'CH',
            }
          : {
              time: e.time as UTCTimestamp,
              position: e.side === 'sellside' ? 'belowBar' : 'aboveBar',
              color: SWEEP_MARKER_COLOR,
              shape: 'circle',
              text: 'SW',
            },
      )
      smcMarkersRef.current = markers
      applyMarkers()
    }

    syncPriceLine(candleSeries, obBullishLineRef, smc?.ready ? smc.order_block_bullish?.high : null, {
      color: OB_BULLISH_COLOR,
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      title: 'Bullish OB High',
    })
    syncPriceLine(candleSeries, obBearishLineRef, smc?.ready ? smc.order_block_bearish?.low : null, {
      color: OB_BEARISH_COLOR,
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      title: 'Bearish OB Low',
    })
    syncPriceLine(candleSeries, targetT2LineRef, smc?.ready ? smc.target_zones?.t2 : null, {
      color: TARGET_ZONE_COLOR,
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      title: 'T2 Target Zone',
    })

    // "TradingView Parity" sprint (2026-08-29): the trendline itself --
    // real API is `chart.addSeries(LineSeries, options)` (see
    // trendlineSeriesRef's own comment for why this isn't
    // `addLineSeries()`), created lazily the first time a real
    // trendline exists rather than up front in Effect 1, since unlike
    // the candle/volume series a symbol in RANGE state has none at all.
    // At most one trendline ever comes back (see SmcTrendline's own
    // type comment) -- its color is re-applied on every update since
    // the SAME series is reused across a bullish<->bearish flip, not
    // recreated.
    // "Black Screen Crash" fix (2026-08-29): also requires both points'
    // own time/value to be finite -- same reasoning as isFiniteBar's
    // own comment above, applied to the one other place this component
    // feeds raw numeric coordinates to lightweight-charts.
    const rawTrendline = smc?.ready ? smc.trendlines?.[0] : undefined
    const trendline =
      rawTrendline && rawTrendline.points.every((p) => Number.isFinite(p.time) && Number.isFinite(p.value))
        ? rawTrendline
        : undefined
    if (!trendline) {
      trendlineSeriesRef.current?.setData([])
    } else {
      if (!trendlineSeriesRef.current) {
        trendlineSeriesRef.current = chartRef.current?.addSeries(LineSeries, {
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          lastValueVisible: false,
          priceLineVisible: false,
        }) ?? null
      }
      const trendlineSeries = trendlineSeriesRef.current
      if (trendlineSeries) {
        trendlineSeries.applyOptions({
          color: trendline.direction === 'bullish' ? TRENDLINE_BULLISH_COLOR : TRENDLINE_BEARISH_COLOR,
        })
        const data: LineData<Time>[] = trendline.points.map((p) => ({
          time: p.time as UTCTimestamp,
          value: p.value,
        }))
        trendlineSeries.setData(data)
      }
    }

    // Perfect-fit, once per symbol/interval -- see hasFitSmcRef's own
    // comment for why this doesn't run on every subsequent poll.
    if (smc?.ready && !hasFitSmcRef.current) {
      chartRef.current?.timeScale().fitContent()
      hasFitSmcRef.current = true
    }
  }, [smc])

  // Effect 6: "Structure & Breakout Suite" Phase 4 (2026-08-29) -- the
  // real on-canvas drawing this phase's own review found missing
  // (Phase 1/2 only ever rendered status chips beside the chart). Draws
  // directly from the `structureSignal` prop, independent of Effect 3's
  // broker-position lines and Effect 5's own SMC markers -- see
  // structureSignal's own prop comment and structMarkersRef's own
  // comment above for why these don't overwrite either.
  //
  // BUY ABOVE/SELL BELOW is the one line always shown once a real
  // trigger price exists; entry/SL/TP1-3 only draw once the signal's
  // own breakout candle has actually confirmed (trade_readiness ===
  // BUY_ARMED/SELL_ARMED) -- a real, computed level, not the trigger
  // guessed forward. Momentum uses a circle marker with a diamond glyph
  // (lightweight-charts v5 has no native diamond shape -- same "shape +
  // text glyph" approach Effect 5's own BOS/CHOCH markers already use
  // for their own text) at the LAST closed bar; breakout uses the real
  // directional arrow shape (arrowUp/arrowDown -- already literally
  // triangle-shaped) with a triangle glyph, matching direction + color
  // the same way Effect 5's own BOS markers do.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current
    if (!candleSeries) return

    const bullish = structureSignal?.dominant_bias === 'BULLISH'
    const tpColor = bullish ? CHART_BULL : CHART_BEAR

    syncPriceLine(candleSeries, structTriggerLineRef, structureSignal?.trigger_price, {
      color: STRUCT_TRIGGER_LINE_COLOR,
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      title: structureSignal?.trigger_side === 'SELL_BELOW' ? 'SELL BELOW' : 'BUY ABOVE',
    })

    const confirmed =
      structureSignal?.candle_confirmed &&
      (structureSignal.trade_readiness === 'BUY_ARMED' ||
        structureSignal.trade_readiness === 'SELL_ARMED')
    syncPriceLine(candleSeries, structEntryLineRef, confirmed ? structureSignal?.entry : null, {
      color: ENTRY_LINE_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: 'STRUCT ENTRY',
    })
    syncPriceLine(candleSeries, structSlLineRef, confirmed ? structureSignal?.sl : null, {
      color: CHART_BEAR,
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      title: 'STRUCT SL',
    })
    syncPriceLine(candleSeries, structTp1LineRef, confirmed ? structureSignal?.tp1 : null, {
      color: tpColor,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: 'STRUCT TP1',
    })
    syncPriceLine(candleSeries, structTp2LineRef, confirmed ? structureSignal?.tp2 : null, {
      color: tpColor,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: 'STRUCT TP2',
    })
    syncPriceLine(candleSeries, structTp3LineRef, confirmed ? structureSignal?.tp3 : null, {
      color: tpColor,
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      title: 'STRUCT TP3',
    })

    // Markers anchor to the last real closed bar this chart actually has
    // -- the structure signal is a "right now" read, not tied to a
    // specific historical event the way a BOS/CHOCH break is.
    const lastBarTime = lastBarTimeRef.current
    const markers: SeriesMarker<Time>[] = []
    if (lastBarTime !== null && structureSignal) {
      if (structureSignal.visual_markers.momentum_diamond) {
        const diamondBullish = structureSignal.visual_markers.momentum_diamond === 'GREEN'
        markers.push({
          time: lastBarTime as UTCTimestamp,
          position: diamondBullish ? 'belowBar' : 'aboveBar',
          color: diamondBullish ? CHART_BULL : CHART_BEAR,
          shape: 'circle',
          text: '◆',
        })
      }
      if (structureSignal.visual_markers.breakout_triangle) {
        const triangleBullish = structureSignal.visual_markers.breakout_triangle === 'GREEN'
        markers.push({
          time: lastBarTime as UTCTimestamp,
          position: triangleBullish ? 'belowBar' : 'aboveBar',
          color: triangleBullish ? CHART_BULL : CHART_BEAR,
          shape: triangleBullish ? 'arrowUp' : 'arrowDown',
          text: triangleBullish ? '▲' : '▼',
        })
      }
    }
    structMarkersRef.current = markers
    applyMarkers()
  }, [structureSignal])

  // "Black Screen Crash" fix (2026-08-29): an empty/blank symbol prop
  // (belt-and-suspenders -- every real call site already guards this
  // before rendering the component at all) gets its own honest message
  // rather than silently falling through to "No intraday bars cached
  // yet for ." A real symbol still fetching its first bars gets a
  // pulsing skeleton bar, not a plain static sentence, so "the new
  // symbol's data is in flight" reads as a loading state at a glance.
  return (
    <div
      className={`relative w-full overflow-hidden rounded-lg ${heightClassName}`}
      style={{ background: CHART_BACKGROUND }}
    >
      <div ref={containerRef} className="h-full w-full" />
      {bars.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-3 text-xs text-hud-muted">
          {!symbol ? (
            'No symbol selected.'
          ) : error ? (
            `Chart data unavailable: ${error.message}`
          ) : loading ? (
            <>
              <div className="h-2 w-40 animate-pulse rounded-full bg-hud-panel-hover" />
              <div className="h-2 w-28 animate-pulse rounded-full bg-hud-panel-hover" />
              <span>Loading 1-min bars for {symbol}…</span>
            </>
          ) : (
            `No intraday bars cached yet for ${symbol}.`
          )}
        </div>
      )}
    </div>
  )
}
