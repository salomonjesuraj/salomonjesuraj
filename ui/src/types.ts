/**
 * Shapes mirror the real backend payloads byte-for-byte (verified
 * against live /api/signals, /api/trade-blueprint/{symbol}, and
 * /api/options/summary responses during the 2026-08-26 live session --
 * not guessed from the Python models alone). Every optional/nullable
 * field here is genuinely optional in the wire payload; the UI must
 * render an honest "--" for a missing value, never a fabricated one.
 */

export type Direction = 'BULL' | 'BEAR'

export type RetestStatus =
  | 'NO_BREAKOUT'
  | 'PENDING_RETEST'
  | 'RETEST_HELD'
  | 'RETEST_FAILED'

export type OIBuildupType =
  | 'LONG_BUILDUP'
  | 'SHORT_COVERING'
  | 'SHORT_BUILDUP'
  | 'LONG_UNWINDING'
  | 'NEUTRAL'

export type TradeHorizon =
  | 'SCALP'
  | 'INTRADAY'
  | 'BTST'
  | 'SWING'
  | 'UNCLASSIFIED'

/** One row from GET /api/signals -- the active-signal list this HUD
 * builds its Action Cards from. Field set is the raw scanner signal
 * hash (routes/scanner.py's _decode_hash) so it carries more than we
 * use; only the fields this UI actually reads are typed here. */
export interface SignalRow {
  symbol: string
  strategy_id: string
  signal_type: 'bullish' | 'bearish' | string
  side?: string
  conviction_score?: number
  conviction_grade?: string
  entry_price?: number
  invalidation_price?: number
  target_price?: number
  t2_price?: number
  sector_id?: string
  created_at_us?: number
}

/** GET /api/trade-blueprint/{symbol} */
export interface TradeBlueprint {
  symbol: string
  direction: Direction
  setup_name: string

  entry_price: number
  invalidation_sl: number
  target_1_fib: number
  target_2_fib: number
  target_3_fib: number
  target_method: string

  retest_status: RetestStatus
  retest_level: number | null

  accumulation_base: boolean
  poc_level: number | null
  vah_level: number | null
  val_level: number | null

  oi_buildup: OIBuildupType
  oi_attraction_strike: number | null
  oi_hurdle_strike: number | null

  trade_horizon: TradeHorizon

  available_fields: string[]
  unavailable_fields: string[]
}

/** The subset of /api/options/summary this HUD's Microstructure Pill
 * reads -- see upstox_option.metrics in the real payload. */
export interface OptionSummary {
  bias?: 'CE' | 'PE'
  suggested_contract?: string
  upstox_option?: {
    ready?: boolean
    metrics?: {
      ltp?: number
      delta?: number
      spread_pct?: number
      option_sl_price?: number
      strike?: number
    }
  }
}

/** ws-gateway's tick_batch message, per services/dashboard/public/js/ws.js's
 * own documented protocol. */
export interface TickBatchMessage {
  type: 'tick_batch'
  ts: number
  data: Record<string, { ltp?: number; volume?: number; change_pct?: number }>
}
