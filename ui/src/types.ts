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

/* ── 4-Zone Trading Command Screen (2026-08-27) ──────────────────────
 * Zone 1's index feed and Zone 3/4's per-symbol structure fields,
 * verified against the real live endpoints while building this, not
 * assumed from the requested spec -- see each interface's own note
 * for where its shape diverges from what was originally asked for. */

/** One row from GET /api/market/indices. A row with `error` set (the
 * feed had nothing fresh -- see api/routes/market.py's own staleness
 * check) has none of the price fields; render it as "--", never a
 * stale number silently passed off as live. */
export interface IndexTick {
  symbol: string
  label: string
  ltp?: number
  change_pct?: number
  error?: string
}

/** The advance_decline component of GET /api/market/breadth-health --
 * the only piece of that endpoint Zone 1 needs. */
export interface MarketBreadth {
  available: boolean
  advancing?: number
  declining?: number
}

/** GET /api/futures/oi-buildup-map -- symbol -> OIBuildupType. A
 * symbol absent from this map has no futures row yet; treat that the
 * same as NEUTRAL for filtering, never a guess at which quadrant. */
export type OiBuildupMap = Record<string, OIBuildupType>

/** One row from GET /api/signals/suppressed -- used only for Zone 2's
 * "best near-miss" strip when nothing has cleared the real conviction
 * floor. This is the SAME score space as that 80.0 floor (unlike
 * /api/ticks' own separate composite-score layer below), so "closest
 * candidate" here means the same thing the floor itself means. */
export interface SuppressedSignalRow {
  symbol: string
  strategy_id: string
  side?: string
  grade?: string
  score?: number
  reason?: string
  code?: string
  entry?: number
  stop?: number
  target?: number
  rr?: number
  ltp?: number
  why?: string
}

/** One row from GET /api/ticks -- the bulk per-symbol payload Zone 3
 * (Smart Money Radar) and Zone 4 (Pre-Breakout trigger levels) both
 * read from. This endpoint is enormous (150+ fields); only what's
 * actually used here is typed. `command_center` and `intelligence_layer`
 * are real embedded objects on every row -- there is no separate
 * `/api/command_center` endpoint, despite that name showing up
 * elsewhere; the data lives here instead. `trade_horizon` on this row
 * is a DIFFERENT, always-on per-tick heuristic (intraday_score vs.
 * swing_score vs. btst_score) from TradeBlueprint's own stricter,
 * signal-gated TradeHorizon classifier -- both are real, they just
 * answer different questions ("what does this symbol's shape look
 * like right now" vs. "what does the one real active trade look
 * like"), so this type keeps its own name for it. */
export interface TickRow {
  symbol: string
  sector_id?: string
  ltp?: number
  change_pct?: number
  day_high?: number
  day_low?: number
  trend_bias?: 'BUY' | 'SELL' | string
  trend_score?: number
  rel_vol?: number
  rvol_rank?: number
  stock_breakout_score?: number
  stock_breakout_tier?: string
  breakout_type?: string
  mtf_dots?: Record<string, 'G' | 'Y' | 'R'>
  trade_horizon_label?: string
  horizon_reason?: string
  strength_reasons?: string[]
  weakness_reasons?: string[]
  command_center?: {
    headline?: string
    why?: string
    blocker?: string
    dominance?: 'BUYERS' | 'SELLERS' | string
    dominance_reason?: string
    support?: number
    resistance?: number
    entry?: number
    stop_loss?: number
    target_1?: number
    target_2?: number
    target_3?: number
    horizon?: string
    quality?: string
    score?: number
  }
}

/** One row from GET /api/prebreakout. `state` is the REAL taxonomy
 * (coiled/accumulating/compressing/triggered) -- not the
 * COILING_AT_RESISTANCE/ACCUMULATION_BASE/RETEST_HELD/SQUEEZE enum
 * originally assumed, which this endpoint has never used. There is
 * also no fixed-trigger-price or ATR-distance field on this row (this
 * component joins TickRow's own breakout_area/day_high by symbol to
 * approximate that -- see PreBreakoutWatchlist.tsx's own note). */
export interface PrebreakoutRow {
  symbol: string
  state: 'coiled' | 'accumulating' | 'compressing' | 'triggered' | string
  prev_state?: string
  readiness_score?: number
  transition_reason?: string
  rel_vol?: number
  bb_width?: number
  has_signal?: boolean
}
