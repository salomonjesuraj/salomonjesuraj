import { create } from 'zustand'

export type ChartEngineStatus = 'unknown' | 'ok' | 'error'

interface UiEngineState {
  /** "unknown" until a candlestick chart has actually been mounted at
   * least once this session -- an idle Sniper HUD (no symbol selected
   * yet) genuinely has no evidence either way, and reporting "ok" by
   * default would be a fabricated all-clear, not a real measurement. */
  chartEngineStatus: ChartEngineStatus
  chartEngineError: string | null
  reportChartEngine: (status: ChartEngineStatus, error?: string) => void
}

/**
 * "Terminal Edge & Analyst" sprint (2026-08-27) -- the Admin Terminal's
 * "TradingView UI Engine" row needs a real signal, and no backend route
 * can honestly answer "is the charting library rendering correctly" --
 * that's inherently a client-side fact. LiveCandlestickChart.tsx reports
 * into this store from inside its own chart-creation try/catch (a real,
 * if narrow, health probe: did lightweight-charts actually initialize
 * without throwing), and SafetyLogs.tsx reads it. Session-local, resets
 * on reload -- there's no backend persistence for a client-only signal.
 */
export const useUiEngineStore = create<UiEngineState>((set) => ({
  chartEngineStatus: 'unknown',
  chartEngineError: null,
  reportChartEngine: (status, error) => set({ chartEngineStatus: status, chartEngineError: error ?? null }),
}))
