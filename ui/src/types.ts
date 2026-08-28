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
 * use; only the fields this UI actually reads are typed here.
 *
 * "Probabilistic Grading and Warning Tags" (2026-08-27): ob_fvg_distance_pct
 * and warning_tags are now on this row directly (routes/scanner.py
 * computes them from the same features_snapshot compute_conviction()
 * already scored), same fields SuppressedSignalRow below carries --
 * ActionCard.tsx merges both row types into one Candidate shape (see
 * lib/candidates.ts) so a single component renders either. */
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
  risk_reward_ratio?: number
  sector_id?: string
  created_at_us?: number
  ob_fvg_distance_pct?: number | null
  warning_tags?: string[]
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

  // "Terminal Edge & Analyst" sprint (2026-08-27) -- see
  // infusion_models.trade_blueprint.TradeStructure's own docstring for
  // exactly which already-computed real value each field passes
  // through (mtf.py's fractal-pivot "Major Blocker" for support/
  // resistance, its existing Donchian Channel for channel bounds,
  // feature-engine's real 1-minute BOS/CHOCH trend state for trend).
  // null (not 0) when the upstream source has no data yet.
  structure: {
    support: number | null
    resistance: number | null
    channel_upper: number | null
    channel_lower: number | null
    trend: string
  } | null
  // A DETERMINISTIC sentence built server-side from structure/OI/trend
  // signals (api/trade_blueprint.py's _build_trade_rationale) -- not an
  // LLM call. See that function's own docstring.
  trade_rationale: string

  available_fields: string[]
  unavailable_fields: string[]
}

/** The subset of /api/options/summary this HUD's Microstructure Pill
 * reads -- see upstox_option.metrics in the real payload. `symbol` is
 * also real (api/routes/market.py's options_summary() always includes
 * it, even in the no-symbol/no-signal fallback response), added when
 * Options Analytics needed to know which symbol the backend's own
 * default-symbol fallback had picked. */
export interface OptionSummary {
  symbol?: string
  bias?: 'CE' | 'PE'
  suggested_contract?: string
  upstox_option?: {
    ready?: boolean
    // Widened for the "Terminal Edge" sprint (2026-08-27) to carry what
    // api/routes/execution.py's _build_ticket() actually reads off a
    // trade's `option` sub-object, so ActionCard's execution module can
    // stage a real paper ticket with real option-chain fidelity instead
    // of an empty placeholder. Every field optional -- the chain may
    // still be CHAIN_PENDING/AVOID_CONTRACT, in which case most of this
    // is genuinely absent, not just untyped.
    contract?: string
    trade_ready?: boolean
    execution_status?: string
    quality_grade?: string | null
    hard_blockers?: string[]
    blockers?: string[]
    event_calendar?: Record<string, unknown>
    metrics?: {
      ltp?: number
      bid?: number
      ask?: number
      entry_fill?: number
      exit_fill_reference?: number
      delta?: number
      spread_pct?: number
      option_sl_price?: number
      lot_size?: number
      strike?: number
      liquidity_whitelist_pass?: boolean
      physical_settlement_block?: boolean
    }
  }
}

/** The subset of GET /api/risk/settings this HUD reads for sizing a
 * staged paper ticket -- see api/routes/risk.py's own DEFAULT_RISK. */
export interface RiskSettings {
  risk_per_trade_amount: number
  high_conviction_risk_amount: number
  max_lots: number
}

/** POST /api/execution/stage's response ticket -- a PAPER order ticket
 * only (api/routes/execution.py's own module docstring: "This route
 * builds a broker-style order ticket... but it does not place orders").
 * Every field this app doesn't render is left off rather than typed
 * blind; the real response carries more (see _build_ticket()'s full
 * return dict). */
export interface ExecutionTicket {
  id: string
  mode: string
  status: 'READY_TO_STAGE' | 'BLOCKED' | string
  symbol: string
  decision: string
  quantity: number
  lot_count: number
  estimated_max_loss: number
  net_pnl_flat?: number
  blockers: string[]
  warning: string
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
  signal_type?: 'bullish' | 'bearish' | string
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
  ob_fvg_distance_pct?: number | null
  warning_tags?: string[]
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

/* ── Command Center data-wiring sprint (2026-08-27) ───────────────────
 * Shapes for the four routes hydrated below -- each verified against
 * its route module's actual return statement, not guessed. A real,
 * disclosed gap found during that read: this backend computes and
 * scores exactly one live option Greek (Delta) anywhere -- Gamma/Theta/
 * Vega are never read out of Upstox's option_greeks payload by any
 * route. Options Analytics below is honest about that; see its own
 * page file. */

/** GET /api/options/chain-analytics -- PCR + OI-based support/resistance
 * + Max Pain for one symbol, off the full Upstox option chain. `pcr`/
 * `oi_support_resistance`/`max_pain` are each independently nullable
 * (api/options_analytics.py returns None per-metric on a thin/pre-market
 * chain rather than a misleading 0), not just the top-level `ready`. */
export interface OptionsChainAnalytics {
  ready: boolean
  reason?: string
  symbol?: string
  expiry?: string
  spot?: number
  strikes_in_chain?: number
  pcr: { pcr: number; sentiment: string; total_call_oi: number; total_put_oi: number } | null
  oi_support_resistance: {
    resistance: number | null
    resistance_oi: number | null
    support: number | null
    support_oi: number | null
  } | null
  max_pain: {
    max_pain_strike: number | null
    min_total_payout: number | null
    candidates_evaluated: number
  } | null
}

/** One symbol's own real fields off GET /api/symbols -- the actual
 * F&O universe tracked by this pipeline (infusion:symbols in Redis),
 * not a separate hand-maintained list. "Unified Screener & Deep-Dive
 * Interactivity" sprint (2026-08-28). */
export interface SymbolMeta {
  symbol: string
  sector_id: string
  exchange: string
  market_cap_tier: string
  index_membership: string[]
}

/** One strike's real call/put snapshot off GET /api/options/chain --
 * Upstox's own market_data + option_greeks fields, read verbatim (see
 * api/routes/market.py's own _leg_snapshot docstring for why gamma/
 * theta/vega are real here even though OptionsAnalytics' own Greeks
 * Exposure card still only has Delta -- different endpoint, different
 * scope). */
export interface OptionChainLeg {
  ltp: number
  oi: number
  volume: number
  iv: number
  delta: number
  gamma: number
  theta: number
  vega: number
}

export interface OptionChainStrike {
  strike_price: number
  call: OptionChainLeg
  put: OptionChainLeg
}

/** GET /api/options/chain?symbol=X -- the full per-strike chain,
 * "Unified Screener & Deep-Dive Interactivity" sprint (2026-08-28). */
export interface OptionChainResponse {
  ready: boolean
  reason?: string
  symbol?: string
  expiry?: string
  spot?: number
  pcr: OptionsChainAnalytics['pcr']
  max_pain: OptionsChainAnalytics['max_pain']
  oi_support_resistance: OptionsChainAnalytics['oi_support_resistance']
  strikes?: OptionChainStrike[]
}

/** GET /api/screener/structure -- "Unified Omni-Screener & Deep-Dive
 * Interactivity" sprint (2026-08-28). One entry per symbol that
 * currently has a real, validated Order Block or FVG zone; a symbol
 * with neither simply has no key here (never a fabricated 0). */
export interface ScreenerStructureEntry {
  ob_fvg_level: number
  distance_pct: number | null
}
export type ScreenerStructureMap = Record<string, ScreenerStructureEntry>

/** GET /api/screener/options-summary -- real PCR/Max Pain, but only
 * for whichever symbols api/option_chain_queue.py's own background
 * loop has actually refreshed recently (see that route's own module
 * docstring for why this is never bulk-fetched live for the whole
 * universe). A symbol not in this map just hasn't been touched by that
 * rotating candidate refresh recently -- shown as unavailable, not 0. */
export interface ScreenerOptionsSummaryEntry {
  symbol: string
  spot: number
  pcr: OptionsChainAnalytics['pcr']
  max_pain: OptionsChainAnalytics['max_pain']
  oi_support_resistance: OptionsChainAnalytics['oi_support_resistance']
  updated_at: number
}
export type ScreenerOptionsSummaryMap = Record<string, ScreenerOptionsSummaryEntry>

/** One leg inside a RankedStrategy's `legs` array
 * (api/options_strategies.py's `_leg()`). */
export interface StrategyLeg {
  action: 'BUY' | 'SELL'
  type: 'CE' | 'PE'
  strike: number
  premium: number
  iv: number
  delta: number
}

/** One entry in GET /api/options/strategy-selector's ranked_strategies --
 * field set past `components` varies per strategy (a credit spread has
 * `net_credit`, a debit spread has `net_debit`, both have `max_profit`/
 * `max_loss`/`breakeven`), so those are all optional here rather than
 * guessed into one fixed shape. */
export interface RankedStrategy {
  strategy: string
  ready: true
  fit_score: number
  components: {
    directional: { score: number; reason: string }
    iv_rank: { score: number; reason: string }
    pcr: { score: number; reason: string }
    max_pain: { score: number; reason: string }
  }
  legs?: StrategyLeg[]
  max_profit?: number
  max_loss?: number
  net_debit?: number
  net_credit?: number
  breakeven?: number[]
}

/** GET /api/options/strategy-selector */
export interface StrategySelectorResult {
  ready: boolean
  reason?: string
  symbol?: string
  spot?: number
  expiry?: string
  trade_bias?: string
  mtf_alignment?: string
  iv_rank?: number | null
  pcr_sentiment?: string | null
  max_pain_strike?: number | null
  ranked_strategies?: RankedStrategy[]
}

/** One row of GET /api/backtest/summary's by_grade/by_session/by_sector
 * breakdowns. */
export interface BacktestBreakdownRow {
  label: string
  total: number
  wins: number
  losses: number
  precision_pct: number | null
}

/** GET /api/backtest/summary -- Postgres-archived signal outcomes,
 * server-cached 90s. `available: false` means no Postgres analytics
 * pool (never a fabricated zero). */
export interface BacktestSummary {
  available: boolean
  reason?: string
  days?: number
  strategy?: string
  total?: number
  active?: number
  suppressed?: number
  target_hits?: number
  stop_hits?: number
  expired?: number
  decided?: number
  precision_pct?: number | null
  avg_score?: number | null
  avg_rr?: number | null
  avg_mfe_pct?: number | null
  avg_mae_pct?: number | null
  reliability?: string
  note?: string
  by_grade?: BacktestBreakdownRow[]
  by_session?: BacktestBreakdownRow[]
  by_sector?: BacktestBreakdownRow[]
  cached?: boolean
}

/** One evaluated parameter profile inside GET /api/backtest/walkforward's
 * `candidates` (top 10 of a real ~1,575-combination grid search over
 * min_score/min_rr/min_grade_rank/session filters, sorted best-utility-
 * first). This is the one endpoint that computes win rate, avg R:R, AND
 * Sharpe together per profile -- everywhere else in this backend has at
 * most two of those three. */
export interface WalkforwardProfile {
  min_score: number
  min_rr: number
  min_grade_rank: number
  sessions: string
  label: string
  test: {
    wins: number
    losses: number
    decided: number
    precision_pct: number | null
    avg_rr: number | null
  }
  test_sharpe: { n: number; sharpe: number | null }
  status: string
  overfit_gap_pct: number | null
  utility: number
}

/** GET /api/backtest/walkforward -- deliberately NOT part of the Lab's
 * shared 30s poll (useLabData): this is a real, uncached, in-memory grid
 * search over every archived row in the window (no Redis cache wraps it,
 * unlike /api/backtest/summary's explicit 90s TTL), so it's fetched once
 * per page visit on its own, much slower cadence instead. */
export interface WalkforwardResult {
  available: boolean
  reason?: string
  status?: string
  total_decided?: number
  candidates?: WalkforwardProfile[]
  note?: string
}

/** GET /api/backtest/optimizer-proposal/latest -- last-written comparison
 * of the scanner's live precision_guard config against the nightly
 * walk-forward recommendation. `recommended.test_sharpe.sharpe` is the
 * one real Sharpe this backend computes anywhere (per-trade, R-multiple
 * based, not annualized) -- null whenever no profile currently clears
 * its out-of-sample target, which is a legitimate outcome, not a bug. */
export interface OptimizerProposal {
  available: boolean
  reason?: string
  status?: 'PROPOSED' | 'NO_DRIFT' | 'NO_PROPOSAL'
  live_config?: {
    precision_guard_min_score?: number
    precision_guard_min_rr?: number
    precision_guard_sessions?: string
  }
  recommended?: {
    min_score?: number
    min_rr?: number
    sessions?: string
    test?: { precision_pct?: number | null; decided?: number }
    test_sharpe?: {
      n: number
      mean: number | null
      std: number | null
      sharpe: number | null
    }
  } | null
  score_diff?: number
  rr_diff?: number
  note?: string
}

/** One row from GET /api/journal/trades -- paper-trade journal, the
 * exact setup evidence visible before any live execution is allowed.
 *
 * "Visual Tracking & Lifecycle" sprint (2026-08-27): target3/
 * signal_grade/created_at_epoch are written at creation time
 * (api/routes/journal.py's _normalise_trade); duration/exit_price/
 * closed_at_ist and the WIN_T1/WIN_T2/WIN_T3/LOSS/MISSED outcome
 * vocabulary are written later by api/lifecycle_monitor.py's
 * background sweep once a real 1-min bar resolves the trade. The
 * legacy WIN/LOSS/TARGET/STOP/T1/T2/REVIEW values stay in the union
 * because rows closed manually (the old /outcome POST path, still
 * live) can still carry them -- never assume every row uses the new
 * vocabulary. */
export interface JournalTrade {
  id: string
  created_at_ist: string
  created_at_epoch?: number
  status: 'PLANNED' | 'WATCH' | 'BLOCKED' | 'CLOSED' | string
  symbol: string
  decision: string
  entry: number
  stop: number
  target1: number
  target2: number
  target3?: number
  rr1: number
  signal_grade?: string
  duration?: string | null
  option_readiness?: number
  setup_strength?: number
  strength_reasons?: string[]
  rejection_reasons?: string[]
  outcome?:
    | 'WIN_T1'
    | 'WIN_T2'
    | 'WIN_T3'
    | 'LOSS'
    | 'MISSED'
    | 'WIN'
    | 'TARGET'
    | 'STOP'
    | 'T1'
    | 'T2'
    | 'REVIEW'
    | null
  exit_price?: number
  closed_at_ist?: string
  discretionary_action?: string
}

/** GET /api/journal/stats */
export interface JournalStats {
  today: string
  total_today: number
  watch: number
  planned: number
  blocked: number
  closed: number
  wins: number
  losses: number
  win_rate: number
  risk_planned: number
}

/** GET /api/journal/expectancy -- cost-aware paper expectancy, the P1
 * headline metric this project uses instead of raw rupee P&L (no route
 * anywhere aggregates realized rupee P&L across trades; expectancy in
 * R-multiples is the real number this backend actually produces). */
export interface JournalExpectancy {
  ok: boolean
  sample: { total: number; closed: number; taken: number; skipped: number; not_reviewed: number }
  expectancy_r: number | null
  profit_factor: number | null
  hit_rate: number | null
  cost_drag: number
  max_drawdown_r: number
}

/** One row from GET /api/execution/staged -- paper order tickets. */
export interface StagedTicket {
  id: string
  created_at_ist: string
  status: 'READY_TO_STAGE' | 'BLOCKED' | string
  symbol: string
  decision: string
  quantity: number
  blockers?: string[]
}

/** GET /api/execution/staged */
export interface StagedTicketsResponse {
  ok: boolean
  count: number
  ready: number
  blocked: number
  tickets: StagedTicket[]
}

/** One gate row inside GET /api/safety/status. */
export interface SafetyGate {
  key: string
  label: string
  state: 'pass' | 'warn' | 'block'
  detail: string
}

/** GET /api/safety/status -- the safety cockpit's own gate checklist +
 * verdict, the real "active gates" data source for Safety & Logs. */
export interface SafetyStatus {
  ok: boolean
  verdict: 'PAPER_READY' | 'WATCH_READY' | 'BLOCKED' | string
  session: string
  timestamp_ist: string
  paper_first: boolean
  kill_switch: { enabled: boolean; reason: string; updated_at_ist: string }
  gates: SafetyGate[]
  counts: {
    active_signals: number
    journal_today: number
    staged_today: number
    ready_tickets: number
    blocked_tickets: number
  }
  next_action: string
}

/** One service's heartbeat inside GET /api/health. */
export interface ServiceHealth {
  status: 'healthy' | 'unhealthy' | string
  reason?: string
  [key: string]: unknown
}

/** GET /api/auth/upstox/status -- "Telegram Redesign & Token Modal"
 * sprint (2026-08-27). Purpose-built for exactly the question the
 * Upstox Token Modal needs answered ("does the trader need to paste a
 * fresh token right now"), so the modal's own watcher polls this
 * directly rather than pattern-matching on error strings from every
 * individual broker endpoint -- see useUpstoxAuthWatcher.ts's own note. */
export interface UpstoxAuthStatus {
  broker: string
  token_state: 'valid' | 'expired' | 'invalid' | 'missing' | string
  needs_token: boolean
  auth_error: string
  source: string
  expiry_ts: number
  expiry_utc: string
  expiry_ist: string
  ingestion_state: string
  last_tick_age_ms: number
  tick_count: number
}

/** Response body from POST /api/auth/upstox/token -- both the route's
 * own pre-existing `ok`/`error` fields and the sprint's own literal
 * `status`/`message` shape are present on every response (additive
 * widening, see routes/auth.py's own _rejected() docstring), so this
 * type only needs to carry the ones the modal actually reads. */
export interface UpstoxTokenSaveResult {
  ok: boolean
  status: 'success' | 'error'
  message: string
  expiry_ist?: string
}

/** GET /api/health */
export interface HealthStatus {
  status: 'healthy' | 'degraded'
  services: Record<string, ServiceHealth>
}

/** One bar from GET /api/chart/{symbol}/intraday -- real 1-min OHLCV
 * built by feature-engine's bar_builder from live tick aggregation
 * (api/routes/charts.py). `time` is already a Unix-seconds integer, the
 * exact unit lightweight-charts' own UTCTimestamp expects -- no
 * conversion needed between this wire shape and the chart's data model. */
export interface ChartBar {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

/** GET /api/chart/{symbol}/intraday */
export interface IntradayChartResponse {
  symbol: string
  interval: string
  count: number
  bars: ChartBar[]
  error?: string
}

/** One real order-book level from GET /api/market/depth/{symbol} --
 * field names match upstox_codec.py's own MarketLevel quote parsing
 * (bidP/bidQ/askP/askQ) verbatim, not renamed. Each level carries BOTH
 * sides (level 0 = best bid + best ask, level 1 = 2nd-best each, etc.)
 * -- that's how Upstox's own depth feed pairs them, not a frontend
 * reshaping. */
export interface DepthLevel {
  bidP: number
  bidQ: number
  askP: number
  askQ: number
}

/** GET /api/market/depth/{symbol} -- "Terminal Edge" sprint (2026-08-27).
 * `available: false` (never a fabricated empty ladder) when
 * feature-engine's own infusion:depth:{symbol} key has expired --
 * either genuinely no depth for this symbol, or its 10s TTL lapsed
 * because the feed stopped ticking it. */
export interface DepthResponse {
  available: boolean
  symbol?: string
  levels?: DepthLevel[]
  updated_at_us?: number
  reason?: string
}

/* ── "Broker Sync & Active Position Intelligence" master sprint
 * (2026-08-27) -- STRICT READ-ONLY: every one of these three endpoints
 * is a GET against api/broker_sync.py's own real (never fabricated)
 * Upstox reads. There is no order-placement capability anywhere in
 * this app; trade execution stays 100% manual on the broker's own
 * platform. See broker_sync.py's own module docstring for the real
 * live-verification disclosure on field-name fidelity. ──────────────── */

/** api/broker_sync.py's own real Position Decision & Horizon Engine
 * output, nested onto every row of GET /api/broker/positions. */
export interface PositionIntelligence {
  underlying: string
  direction: 'BULL' | 'BEAR'
  dte_trading_days: number | null
  theta_risk: 'LOW' | 'ACCELERATING' | 'SEVERE' | 'N/A'
  expiry: string | null
  structure: {
    support: number | null
    resistance: number | null
    channel_upper: number | null
    channel_lower: number | null
    trend: string
  }
  // SPOT TARGETS -- every one of these three is a level on the
  // underlying's own chart, never the option premium. T2/T3
  // ("Strict Quant & Option Logic" sprint, 2026-08-28) are real,
  // mathematically distinct 1.618/2.618 Fibonacci extensions of the
  // real Donchian swing (`structure.channel_upper/channel_lower`
  // above) -- they can no longer render as the same number the way an
  // earlier design's T3 (borrowed from a signal-only endpoint with
  // nothing to fall back to) sometimes did.
  target_primary: number | null
  target_secondary: number | null
  target_tertiary: number | null
  invalidation_level: number | null
  nearest_ob_fvg_level: number | null
  trend_aligned: boolean
  warning_tags: string[]
  holding_horizon: 'HOLD (2-3 DAYS)' | 'RUNNER (INTRADAY ONLY)' | 'TIGHTEN STOP' | 'EXIT IMMEDIATELY' | string
  // Real entry price (average_price, falling back to day_buy_price for
  // a same-day-only buy Upstox itself reports as a bare 0 average --
  // see api/broker_sync.py's own _effective_entry_price docstring for
  // the live-verified quirk). null only when neither is genuinely known.
  effective_entry_price: number | null
  // Option Breakeven & Spot Mapping ("Strict Quant & Option Logic"
  // sprint, 2026-08-28) -- option_strike/option_type/spot_breakeven are
  // all null for a plain equity position (is_option: false).
  is_option: boolean
  option_strike: number | null
  option_type: 'CE' | 'PE' | null
  spot_breakeven: number | null
}

/** One row from GET /api/broker/positions -- Upstox's own real fields
 * (only the ones this UI reads are typed; the real response carries
 * more, see broker_sync.py's own docstring). `trading_symbol`/
 * `tradingsymbol` are the same value duplicated by Upstox itself. */
export interface BrokerPosition {
  exchange: string
  product: string
  quantity: number
  average_price: number
  // Real Upstox quirk, verified live (2026-08-27): a position bought
  // and still held intraday (never carried overnight) reports
  // average_price as a bare 0, not the real fill -- day_buy_price is
  // the real entry for that case. See PositionIntelligenceCard.tsx's
  // own use of this for exactly why.
  day_buy_price?: number
  last_price: number
  close_price: number
  pnl: number
  unrealised: number
  realised: number
  trading_symbol?: string
  tradingsymbol?: string
  instrument_token: string
  intelligence: PositionIntelligence
}

export interface BrokerPortfolioSummary {
  total_unrealized_pnl: number
  total_realized_pnl: number
  capital_deployed: number
  // A structural-invalidation-distance proxy, NOT a literal "risk you
  // set" -- these are the trader's own manually-placed broker
  // positions with no planned stop this pipeline tracks. See
  // broker_sync.py's own comment on why this is the honest number
  // available instead of a fabricated "total risk".
  structural_risk_estimate: number
  structural_risk_known_for: number
  structural_risk_total_positions: number
}

export interface BrokerPositionsResponse {
  available: boolean
  reason?: string
  count?: number
  positions?: BrokerPosition[]
  portfolio?: BrokerPortfolioSummary
}

/** One row from GET /api/broker/holdings -- real delivery equity
 * holdings, straight from Upstox. */
export interface BrokerHolding {
  company_name: string
  trading_symbol?: string
  tradingsymbol?: string
  quantity: number
  average_price: number
  last_price: number
  close_price: number
  pnl: number
  day_change_percentage: number
  exchange: string
}

export interface BrokerHoldingsResponse {
  available: boolean
  reason?: string
  count?: number
  holdings?: BrokerHolding[]
}

/** One row from GET /api/broker/orders -- Upstox's own real order
 * status strings passed through as-is (never remapped into an
 * invented taxonomy). */
export interface BrokerOrder {
  trading_symbol?: string
  tradingsymbol?: string
  transaction_type: 'BUY' | 'SELL' | string
  order_type: string
  product: string
  quantity: number
  price: number
  average_price: number
  filled_quantity: number
  pending_quantity: number
  trigger_price: number
  status: string
  order_timestamp: string
  exchange: string
}

export interface BrokerOrdersResponse {
  available: boolean
  reason?: string
  count?: number
  orders?: BrokerOrder[]
}

/** One entry from GET /api/alerts/log -- recent Telegram delivery log
 * (services/alerter/src/alerter/engine.py's `_log_delivery`). `outcome`
 * is a free-text delivery result (e.g. "sent", "throttled", "failed"),
 * not a fixed enum on the wire, so it stays a string here. */
export interface AlertLogEntry {
  signal_id?: string
  symbol?: string
  grade?: string
  outcome?: string
  reason?: string
  ts?: string
  raw?: string
  [key: string]: unknown
}
