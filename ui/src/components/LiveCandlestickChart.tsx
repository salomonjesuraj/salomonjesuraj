import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  HistogramSeries,
  LineStyle,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef, type MutableRefObject } from 'react'
import { useActivePosition } from '../hooks/useActivePosition'
import { useHistoricalData } from '../hooks/useHistoricalData'
import { usePolling } from '../hooks/usePolling'
import { fetchTradeBlueprint } from '../lib/api'
import { CHART_BEAR, CHART_BULL } from '../lib/chartTheme'
import { useUiEngineStore } from '../store/useUiEngineStore'
import type { ChartBar } from '../types'

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

  const { bars, loading, error } = useHistoricalData(symbol)
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
    }
  }, [])

  // Reset the incremental-update bookkeeping whenever the symbol itself
  // changes -- the next `bars` payload is for a different instrument
  // and must fully replace what's drawn, never partially patch onto it.
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
    }
    entryLineRef.current = null
    slLineRef.current = null
    targetLineRef.current = null
    supportLineRef.current = null
    resistanceLineRef.current = null
    channelUpperLineRef.current = null
    channelLowerLineRef.current = null
  }, [symbol])

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
