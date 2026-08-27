import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  HistogramSeries,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef } from 'react'
import { useHistoricalData } from '../hooks/useHistoricalData'
import { CHART_BEAR, CHART_BULL } from '../lib/chartTheme'
import type { ChartBar } from '../types'

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

function toVolume(bar: ChartBar): HistogramData<UTCTimestamp> {
  return {
    time: bar.time as UTCTimestamp,
    value: bar.volume,
    color: bar.close >= bar.open ? `${CHART_BULL}80` : `${CHART_BEAR}80`,
  }
}

interface LiveCandlestickChartProps {
  symbol: string
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
export function LiveCandlestickChart({ symbol }: LiveCandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const isFirstLoadRef = useRef(true)
  const lastBarTimeRef = useRef<number | null>(null)

  const { bars, loading, error } = useHistoricalData(symbol)

  // Effect 1: create once, tear down on unmount.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

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

    return () => {
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      volumeSeriesRef.current = null
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

  return (
    <div className="relative h-80 w-full overflow-hidden rounded-lg" style={{ background: CHART_BACKGROUND }}>
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
