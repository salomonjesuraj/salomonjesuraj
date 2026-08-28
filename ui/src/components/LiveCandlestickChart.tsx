import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  LineStyle,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
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
import type { ChartBar } from '../types'

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

/** Shared by Effects 3 and 4 below (previously a local closure only
 * Effect 4 had) -- a null/undefined price removes that specific line
 * rather than drawing a fabricated one at 0, the same honesty rule both
 * the position overlay and the MTF structure overlay need. */
function syncPriceLine(
  candleSeries: ISeriesApi<'Candlestick'>,
  ref: MutableRefObject<IPriceLine | null>,
  price: number | null | undefined,
  opts: { color: string; lineWidth: 1 | 2; lineStyle: LineStyle; title: string },
): void {
  if (price === null || price === undefined) {
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
  // fresher data.
  const { data: smc } = usePolling(() => fetchSmcGeometry(symbol), 20000, [symbol])

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
        timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
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
      chartRef.current?.remove()
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
    }
    // The old symbol's BOS/CHOCH/sweep markers likewise belong to a
    // different instrument -- clear immediately rather than leaving
    // stale markers up until the new symbol's own SMC poll lands.
    markersPluginRef.current?.setMarkers([])
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
  }, [symbol, interval])

  // Effect 2: bind fetched bars into the series.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current
    const volumeSeries = volumeSeriesRef.current
    if (!candleSeries || !volumeSeries || bars.length === 0) return

    if (isFirstLoadRef.current) {
      candleSeries.setData(bars.map(toCandle))
      volumeSeries.setData(bars.map(toVolume))
      chartRef.current?.timeScale().fitContent()
      isFirstLoadRef.current = false
    } else {
      const since = lastBarTimeRef.current ?? -Infinity
      for (const bar of bars) {
        if (bar.time < since) continue
        candleSeries.update(toCandle(bar))
        volumeSeries.update(toVolume(bar))
      }
    }
    lastBarTimeRef.current = bars[bars.length - 1].time
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
      markersPlugin.setMarkers([])
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
      markersPlugin.setMarkers(markers)
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
  }, [smc])

  return (
    <div
      className={`relative w-full overflow-hidden rounded-lg ${heightClassName}`}
      style={{ background: CHART_BACKGROUND }}
    >
      <div ref={containerRef} className="h-full w-full" />
      {bars.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs text-hud-muted">
          {error
            ? `Chart data unavailable: ${error.message}`
            : loading
              ? 'Loading 1-min bars…'
              : `No intraday bars cached yet for ${symbol}.`}
        </div>
      )}
    </div>
  )
}
