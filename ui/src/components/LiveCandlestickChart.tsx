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
  const entryLineRef = useRef<IPriceLine | null>(null)
  const slLineRef = useRef<IPriceLine | null>(null)
  const targetLineRef = useRef<IPriceLine | null>(null)
  const supportLineRef = useRef<IPriceLine | null>(null)
  const resistanceLineRef = useRef<IPriceLine | null>(null)
  const channelUpperLineRef = useRef<IPriceLine | null>(null)
  const channelLowerLineRef = useRef<IPriceLine | null>(null)

  const { bars, loading, error } = useHistoricalData(symbol)
  const activePosition = useActivePosition(symbol)
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
  // 2026-08-27) -- dashed white ENTRY, solid red STOP LOSS, solid green
  // TARGET, read straight off the real active journal position for this
  // symbol (useActivePosition). create-once + applyOptions() on later
  // changes (a position's own entry/SL/target rarely move once staged,
  // but this stays correct if a journal row is ever edited); removed
  // outright the moment there's no active position for this symbol, not
  // left stale on screen.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current
    if (!candleSeries) return

    if (!activePosition) {
      if (entryLineRef.current) candleSeries.removePriceLine(entryLineRef.current)
      if (slLineRef.current) candleSeries.removePriceLine(slLineRef.current)
      if (targetLineRef.current) candleSeries.removePriceLine(targetLineRef.current)
      entryLineRef.current = null
      slLineRef.current = null
      targetLineRef.current = null
      return
    }

    const entryOpts = {
      price: activePosition.entry,
      color: ENTRY_LINE_COLOR,
      lineWidth: 1 as const,
      lineStyle: LineStyle.Dashed,
      title: 'ENTRY',
    }
    const slOpts = {
      price: activePosition.stop,
      color: CHART_BEAR,
      lineWidth: 2 as const,
      lineStyle: LineStyle.Solid,
      title: 'STOP LOSS',
    }
    const targetOpts = {
      price: activePosition.target1,
      color: CHART_BULL,
      lineWidth: 2 as const,
      lineStyle: LineStyle.Solid,
      title: 'TARGET',
    }

    if (entryLineRef.current) entryLineRef.current.applyOptions(entryOpts)
    else entryLineRef.current = candleSeries.createPriceLine(entryOpts)

    if (slLineRef.current) slLineRef.current.applyOptions(slOpts)
    else slLineRef.current = candleSeries.createPriceLine(slOpts)

    if (targetLineRef.current) targetLineRef.current.applyOptions(targetOpts)
    else targetLineRef.current = candleSeries.createPriceLine(targetOpts)
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

    const syncLine = (
      ref: MutableRefObject<IPriceLine | null>,
      price: number | null | undefined,
      opts: { color: string; lineWidth: 1 | 2; lineStyle: LineStyle; title: string },
    ) => {
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

    syncLine(supportLineRef, structure?.support, {
      color: CHART_BULL,
      lineWidth: 1,
      lineStyle: LineStyle.Solid,
      title: 'HTF SUPPORT',
    })
    syncLine(resistanceLineRef, structure?.resistance, {
      color: CHART_BEAR,
      lineWidth: 1,
      lineStyle: LineStyle.Solid,
      title: 'HTF RESISTANCE',
    })
    syncLine(channelUpperLineRef, structure?.channel_upper, {
      color: CHANNEL_LINE_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: 'CHANNEL UPPER',
    })
    syncLine(channelLowerLineRef, structure?.channel_lower, {
      color: CHANNEL_LINE_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: 'CHANNEL LOWER',
    })
  }, [structure])

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
