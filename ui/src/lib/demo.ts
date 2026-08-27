import { useTickStore } from '../store/useTickStore'
import type { OptionSummary, SignalRow, TradeBlueprint, TickBatchMessage } from '../types'

/**
 * Dev/preview-only mock mode -- markets are closed and, per this
 * project's own live audit, zero signals cleared the conviction floor
 * the one day this HUD has existed, so there has never been a real
 * active signal to look at. This lets anyone inspect the ActionCard
 * layout and the DynamicTimeline's live-marker animation without
 * waiting for one.
 *
 * Activated by ?demo=true in the URL. Never on by default, and
 * App.tsx renders an unmissable banner whenever it's active -- this
 * data must never be mistaken for a real signal.
 */
export function isDemoMode(): boolean {
  if (typeof window === 'undefined') return false
  return new URLSearchParams(window.location.search).get('demo') === 'true'
}

export const DEMO_BULL_SYMBOL = 'DEMO_BULLCO'
export const DEMO_BEAR_SYMBOL = 'DEMO_BEARCO'

export const DEMO_SIGNALS: SignalRow[] = [
  {
    symbol: DEMO_BULL_SYMBOL,
    strategy_id: 'options_first_hybrid',
    signal_type: 'bullish',
    conviction_score: 87,
    conviction_grade: 'A+',
    entry_price: 1000,
    invalidation_price: 985,
    target_price: 1020,
    t2_price: 1035,
    sector_id: 'DEMO',
    created_at_us: Date.now() * 1000,
  },
  {
    symbol: DEMO_BEAR_SYMBOL,
    strategy_id: 'options_first_hybrid',
    signal_type: 'bearish',
    conviction_score: 81,
    conviction_grade: 'A',
    entry_price: 500,
    invalidation_price: 512,
    target_price: 490,
    t2_price: 480,
    sector_id: 'DEMO',
    created_at_us: Date.now() * 1000,
  },
]

const DEMO_BLUEPRINTS: Record<string, TradeBlueprint> = {
  [DEMO_BULL_SYMBOL]: {
    symbol: DEMO_BULL_SYMBOL,
    direction: 'BULL',
    setup_name: 'demo_long_buildup_breakout',
    entry_price: 1000,
    invalidation_sl: 985,
    target_1_fib: 1020,
    target_2_fib: 1035,
    target_3_fib: 1055,
    target_method: 'fibonacci_confluence',
    retest_status: 'RETEST_HELD',
    retest_level: 998,
    accumulation_base: true,
    poc_level: 996,
    vah_level: 1006,
    val_level: 988,
    oi_buildup: 'LONG_BUILDUP',
    oi_attraction_strike: 1020,
    oi_hurdle_strike: 1050,
    trade_horizon: 'INTRADAY',
    structure: {
      support: 990,
      resistance: 1018,
      channel_upper: 1040,
      channel_lower: 970,
      trend: 'UPTREND (HH/HL)',
    },
    trade_rationale:
      'Price broke Bullish through HTF resistance (1018.00). Bullish long buildup detected in futures OI. Structural trend is uptrend (hh/hl).',
    available_fields: [],
    unavailable_fields: [],
  },
  [DEMO_BEAR_SYMBOL]: {
    symbol: DEMO_BEAR_SYMBOL,
    direction: 'BEAR',
    setup_name: 'demo_short_buildup_breakdown',
    entry_price: 500,
    invalidation_sl: 512,
    target_1_fib: 490,
    target_2_fib: 480,
    target_3_fib: 465,
    target_method: 'atr_practical',
    retest_status: 'PENDING_RETEST',
    retest_level: 503,
    accumulation_base: false,
    poc_level: 505,
    vah_level: 510,
    val_level: 497,
    oi_buildup: 'SHORT_BUILDUP',
    oi_attraction_strike: 490,
    oi_hurdle_strike: 470,
    trade_horizon: 'SCALP',
    structure: {
      support: 492,
      resistance: 515,
      channel_upper: 525,
      channel_lower: 480,
      trend: 'DOWNTREND (LH/LL)',
    },
    trade_rationale:
      'Price broke Bearish through HTF support (492.00). Bearish short buildup detected in futures OI. Structural trend is downtrend (lh/ll).',
    available_fields: [],
    unavailable_fields: [],
  },
}

const DEMO_OPTIONS: Record<string, OptionSummary> = {
  [DEMO_BULL_SYMBOL]: {
    bias: 'CE',
    suggested_contract: `${DEMO_BULL_SYMBOL} 1020 CE`,
    upstox_option: {
      ready: true,
      metrics: { ltp: 14.2, delta: 0.46, spread_pct: 1.4, strike: 1020, option_sl_price: 10.5 },
    },
  },
  [DEMO_BEAR_SYMBOL]: {
    bias: 'PE',
    suggested_contract: `${DEMO_BEAR_SYMBOL} 490 PE`,
    upstox_option: {
      ready: true,
      metrics: { ltp: 8.6, delta: -0.41, spread_pct: 2.1, strike: 490, option_sl_price: 6.2 },
    },
  },
}

export function demoBlueprint(symbol: string): TradeBlueprint {
  return DEMO_BLUEPRINTS[symbol]
}

export function demoOptionSummary(symbol: string): OptionSummary {
  return DEMO_OPTIONS[symbol]
}

/** Oscillating LTP for one demo symbol -- a sine wave spanning from
 * just past SL to just past T2 so it periodically dips back through
 * the entry zone (demonstrating DynamicTimeline's retest highlight)
 * and periodically pushes toward target (demonstrating the marker
 * sliding all the way across). Deliberately smooth/synthetic, not
 * meant to look like real tick noise. */
export function demoLtp(symbol: string, tMs: number): number {
  const bp = DEMO_BLUEPRINTS[symbol]
  if (!bp) return 0
  const bullish = bp.direction === 'BULL'
  const low = bullish ? bp.invalidation_sl : bp.target_2_fib
  const high = bullish ? bp.target_2_fib : bp.invalidation_sl
  const mid = (low + high) / 2
  const amplitude = (high - low) / 2
  const periodMs = 9000
  const phase = symbol === DEMO_BEAR_SYMBOL ? Math.PI : 0
  const wave = Math.sin((tMs / periodMs) * 2 * Math.PI + phase)
  return Number((mid + amplitude * wave).toFixed(2))
}

let demoTickerHandle: number | undefined

// 5 ticks/sec, matching Phase 2's own "an LTP update at 5 ticks-per-
// second" framing. Also a real fix, not just flavor: live-tested at
// 500ms, the sine wave's per-tick step (~8.7 price units near the
// steepest part of the curve) was comparable to the entry-zone band's
// own width (~10 units), so the ticker could jump clean over the zone
// between two consecutive ticks and the retest highlight would simply
// never fire that cycle. 200ms keeps the per-tick step (~3.5 units)
// comfortably smaller than the zone, so the sweep is guaranteed to
// land inside it every period.
const DEMO_TICK_INTERVAL_MS = 200

/** Feeds useTickStore synthetic tick_batch frames for both demo
 * symbols, entirely client-side -- no real WS connection involved, so
 * this works even with the backend unreachable. Safe to call more than
 * once (no-ops if already running). Call stopDemoTicker() to tear it
 * down (e.g. if demo mode is toggled off without a full page reload). */
export function startDemoTicker(): void {
  if (demoTickerHandle !== undefined) return
  const start = Date.now()
  demoTickerHandle = window.setInterval(() => {
    const elapsed = Date.now() - start
    const batch: TickBatchMessage = {
      type: 'tick_batch',
      ts: Date.now(),
      data: {
        [DEMO_BULL_SYMBOL]: { ltp: demoLtp(DEMO_BULL_SYMBOL, elapsed) },
        [DEMO_BEAR_SYMBOL]: { ltp: demoLtp(DEMO_BEAR_SYMBOL, elapsed) },
      },
    }
    useTickStore.getState()._applyBatch(batch)
  }, DEMO_TICK_INTERVAL_MS)
}

export function stopDemoTicker(): void {
  if (demoTickerHandle !== undefined) {
    window.clearInterval(demoTickerHandle)
    demoTickerHandle = undefined
  }
}
