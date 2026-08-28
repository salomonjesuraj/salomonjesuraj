import { LineChart, Minus, Sparkles, TrendingDown, TrendingUp } from 'lucide-react'
import { Link } from 'react-router-dom'
import { fetchOptionSummary, fetchTradeBlueprint } from '../lib/api'
import { useExecution } from '../hooks/useExecution'
import { usePolling } from '../hooks/usePolling'
import type { Candidate } from '../lib/candidates'
import { useLtp } from '../store/useTickStore'
import type { OIBuildupType, TradeHorizon } from '../types'
import { DynamicTimeline } from './DynamicTimeline'
import { HoldToExecuteButton } from './HoldToExecuteButton'
import { ProximityBar } from './ProximityBar'

/** "Terminal Edge & Analyst" sprint (2026-08-27) -- structure.trend is a
 * free-text label (feature_engine's trend_text: "UPTREND (HH/HL)" /
 * "DOWNTREND (LH/LL)" / "RANGE / UNDEFINED"), not an enum, so this reads
 * it by substring rather than an exhaustive switch. */
function trendIcon(trend: string) {
  if (trend.includes('UPTREND')) return { Icon: TrendingUp, tone: 'text-bull' }
  if (trend.includes('DOWNTREND')) return { Icon: TrendingDown, tone: 'text-bear' }
  return { Icon: Minus, tone: 'text-hud-muted' }
}

const HORIZON_LABEL: Record<TradeHorizon, string> = {
  SCALP: 'SCALP · 15M-1H',
  INTRADAY: 'INTRADAY',
  BTST: 'BTST · OVERNIGHT',
  SWING: 'SWING · 2-5D',
  UNCLASSIFIED: 'UNCLASSIFIED',
}

const HORIZON_CLASS: Record<TradeHorizon, string> = {
  SCALP: 'bg-horizon-scalp/15 text-horizon-scalp ring-horizon-scalp/40',
  INTRADAY: 'bg-horizon-intraday/15 text-horizon-intraday ring-horizon-intraday/40',
  BTST: 'bg-horizon-btst/15 text-horizon-btst ring-horizon-btst/40',
  SWING: 'bg-horizon-swing/15 text-horizon-swing ring-horizon-swing/40',
  UNCLASSIFIED: 'bg-hud-muted/10 text-hud-muted ring-hud-muted/30',
}

const OI_LABEL: Record<OIBuildupType, string> = {
  LONG_BUILDUP: 'LONG BUILDUP',
  SHORT_COVERING: 'SHORT COVERING',
  SHORT_BUILDUP: 'SHORT BUILDUP',
  LONG_UNWINDING: 'LONG UNWINDING',
  NEUTRAL: 'OI NEUTRAL',
}

const OI_CLASS: Record<OIBuildupType, string> = {
  LONG_BUILDUP: 'bg-bull/10 text-bull ring-bull/30',
  SHORT_COVERING: 'bg-bull/10 text-bull ring-bull/30',
  SHORT_BUILDUP: 'bg-bear/10 text-bear ring-bear/30',
  LONG_UNWINDING: 'bg-bear/10 text-bear ring-bear/30',
  NEUTRAL: 'bg-hud-muted/10 text-hud-muted ring-hud-muted/30',
}

const DASH = '—'

function fmt(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined || Number.isNaN(value) ? DASH : value.toFixed(digits)
}

/** "Probabilistic Grading and Warning Tags" (2026-08-27): a tag is a risk
 * call-out, not a rejection -- the card renders regardless. R:R shortfall
 * reads as the harder of the two risk facts (a bad reward-to-risk math
 * problem) so it gets red; a late/extended entry is a softer amber
 * caution. Any future tag this taxonomy doesn't yet know about still
 * renders (falls back to amber) rather than silently vanishing. */
function warningTagClass(tag: string): string {
  return tag.startsWith('R:R')
    ? 'bg-bear/15 text-bear ring-1 ring-bear/40'
    : 'bg-horizon-btst/15 text-horizon-btst ring-1 ring-horizon-btst/40'
}

interface ActionCardProps {
  candidate: Candidate
  /** Whether this card's symbol is the one currently charted in the
   * candlestick panel below Zone 2 (2026-08-27 charting sprint) --
   * purely a selection ring, the card's own data is unaffected. */
  isActive?: boolean
  /** "Omnipresent Alert Engine" sprint (2026-08-27) -- probability >=
   * the same 85 threshold scanner/scoring.py's grade_conviction() uses
   * for grade "A+". A green pulse/glow, purely visual -- the audio
   * Success Ping (SniperHud.tsx) fires once per onset; this stays on
   * for as long as the card qualifies, the correct behavior for a
   * persistent visual indicator (unlike a sound, a glow isn't annoying
   * to leave on). */
  isHighConviction?: boolean
  onSelect?: (symbol: string) => void
}

export function ActionCard({ candidate, isActive, isHighConviction, onSelect }: ActionCardProps) {
  const symbol = candidate.symbol
  const ltp = useLtp(symbol)

  const { data: blueprint } = usePolling(() => fetchTradeBlueprint(symbol), 5000, [symbol])
  const { data: optionSummary } = usePolling(() => fetchOptionSummary(symbol), 5000, [symbol])

  const direction = blueprint?.direction ?? (candidate.signalType === 'bearish' ? 'BEAR' : 'BULL')
  const horizon = blueprint?.trade_horizon ?? 'UNCLASSIFIED'
  const oiBuildup = blueprint?.oi_buildup ?? 'NEUTRAL'
  const metrics = optionSummary?.upstox_option?.metrics

  const entryPrice = blueprint?.entry_price || candidate.entryPrice || 0
  const invalidationSl = blueprint?.invalidation_sl || candidate.invalidationPrice || 0
  const t1 = blueprint?.target_1_fib || candidate.targetPrice || 0
  const t2 = blueprint?.target_2_fib || candidate.t2Price || 0
  const t3 = blueprint?.target_3_fib || t2
  const rr = candidate.riskRewardRatio
  const warningTags = candidate.warningTags

  // "Terminal Edge" sprint (2026-08-27): 1-click execution stages a
  // PAPER ticket via the real POST /api/execution/stage -- see
  // useExecution.ts's own note for why this deliberately never calls a
  // live order-placement endpoint. The option payload carries real
  // option-chain fidelity when the chain has resolved (optionSummary
  // populated); an empty object when it hasn't -- the backend's own
  // gates correctly mark that ticket BLOCKED rather than pretending
  // it's ready, same honesty standard as every dash elsewhere on this
  // card.
  const execution = useExecution()
  const upstoxOption = optionSummary?.upstox_option
  const executionOptionPayload = upstoxOption
    ? {
        instrument_key: upstoxOption.contract,
        suggested_contract: optionSummary?.suggested_contract,
        ask: metrics?.ask,
        bid: metrics?.bid,
        entry_fill: metrics?.entry_fill,
        exit_fill_reference: metrics?.exit_fill_reference,
        delta: metrics?.delta,
        spread_pct: metrics?.spread_pct,
        lot_size: metrics?.lot_size,
        liquidity_whitelist_pass: metrics?.liquidity_whitelist_pass,
        physical_settlement_block: metrics?.physical_settlement_block,
        event_calendar: upstoxOption.event_calendar,
        hard_blockers: upstoxOption.hard_blockers,
        blockers: upstoxOption.blockers,
        quality_grade: upstoxOption.quality_grade,
        trade_ready: upstoxOption.trade_ready,
      }
    : undefined

  const handleExecute = () => {
    void execution.stage({
      symbol,
      decision: direction === 'BULL' ? 'BUY CE' : 'BUY PE',
      entry: entryPrice,
      stop: invalidationSl,
      target1: t1,
      target2: t2,
      target3: t3,
      signalGrade: candidate.grade,
      option: executionOptionPayload,
    })
  }

  return (
    <article
      onClick={() => onSelect?.(symbol)}
      className={
        'min-w-0 rounded-xl border bg-hud-panel p-4 shadow-lg shadow-black/30 transition-colors hover:bg-hud-panel-hover ' +
        (onSelect ? 'cursor-pointer ' : '') +
        (isHighConviction
          ? 'animate-pulse border-bull ring-4 ring-bull/60 shadow-[0_0_20px_rgba(57,255,138,0.35)]'
          : isActive
            ? 'border-bull ring-1 ring-bull'
            : 'border-hud-border')
      }
    >
      {/* Header. min-w-0 on the left column + truncate on the symbol
          are the actual fix for a long symbol name -- flex/grid items
          default to min-width:auto, which refuses to shrink below the
          text's own width and forces the whole row (and, uncontained,
          the page itself) to overflow horizontally. flex-wrap on the
          badge row lets the direction/horizon badges drop to their own
          line instead of fighting the symbol for space. */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate font-mono text-lg font-bold tracking-tight text-hud-text">
              {symbol}
            </h3>
            <span
              className={
                'shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ' +
                (direction === 'BULL' ? 'bg-bull/15 text-bull' : 'bg-bear/15 text-bear')
              }
            >
              {direction}
            </span>
            {!candidate.isOfficial && (
              <span className="shrink-0 rounded bg-hud-muted/15 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-hud-muted">
                Candidate
              </span>
            )}
            {/* "Unified Screener & Deep-Dive Interactivity" sprint
                (2026-08-28) -- jumps to the full Options Analytics deep
                dive for this exact symbol, distinct from the card's own
                onClick (which opens the inline candlestick pane below).
                stopPropagation so tapping this link doesn't also toggle
                that inline pane on the way to navigating away. */}
            <Link
              to={`/analytics?symbol=${encodeURIComponent(symbol)}`}
              onClick={(e) => e.stopPropagation()}
              aria-label={`Deep dive ${symbol} in Options Analytics`}
              title="Deep dive in Options Analytics"
              className="shrink-0 rounded p-1 text-hud-muted transition-colors hover:bg-hud-panel-hover hover:text-bull"
            >
              <LineChart className="h-3.5 w-3.5" />
            </Link>
          </div>
          <span
            className={
              'mt-1 inline-block rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ring-1 ' +
              HORIZON_CLASS[horizon]
            }
          >
            {HORIZON_LABEL[horizon]}
          </span>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-[10px] uppercase tracking-wide text-hud-muted">Win Probability</div>
          <div className="tnum font-mono text-xl font-bold text-hud-text">
            {fmt(candidate.probability, 0)}
            <span className="text-sm font-normal text-hud-muted">%</span>
            {candidate.grade && (
              <span className="ml-1 text-xs font-normal text-hud-muted">{candidate.grade}</span>
            )}
          </div>
          <div className="text-[10px] uppercase tracking-wide text-hud-muted">
            R:R {rr !== undefined ? `1:${fmt(rr, 2)}` : DASH}
          </div>
        </div>
      </div>

      {/* Warning chips -- never hides the card, only flags the risk the
          score already priced in (see scanner/scoring.py's soft-decay
          model + infusion_models.smc.compute_warning_tags). */}
      {warningTags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {warningTags.map((tag) => (
            <span
              key={tag}
              className={'rounded px-1.5 py-0.5 text-[10px] font-bold ' + warningTagClass(tag)}
            >
              ⚠ {tag}
            </span>
          ))}
        </div>
      )}

      {/* Institutional base proximity -- ob_fvg_distance_pct, the same
          real distance the LATE_ENTRY warning tag is computed from. */}
      <div className="mt-2">
        <ProximityBar distancePct={candidate.obFvgDistancePct} />
      </div>

      {/* Live LTP line */}
      <div className="mt-2 flex flex-wrap items-baseline gap-2">
        <span className="tnum font-mono text-2xl font-bold text-hud-text">
          {ltp !== undefined ? ltp.toFixed(2) : DASH}
        </span>
        <span
          className={
            'rounded px-1.5 py-0.5 text-[10px] font-bold ring-1 ' + OI_CLASS[oiBuildup]
          }
        >
          {OI_LABEL[oiBuildup]}
        </span>
      </div>

      {/* Dynamic Visual Timeline -- the core feature */}
      <DynamicTimeline
        direction={direction}
        invalidationSl={invalidationSl}
        entryPrice={entryPrice}
        target1={t1}
        target2={t2}
        target3={t3}
        ltp={ltp}
      />

      {/* Microstructure Pill */}
      <div className="grid grid-cols-2 gap-2 rounded-lg border border-hud-border bg-hud-bg/60 p-3 text-[11px]">
        <div>
          <div className="text-hud-muted">POC / VAH</div>
          <div className="tnum font-mono text-hud-text">
            {fmt(blueprint?.poc_level)} / {fmt(blueprint?.vah_level)}
          </div>
        </div>
        <div>
          <div className="text-hud-muted">Strike</div>
          <div className="tnum font-mono text-hud-text">
            {metrics?.strike ? fmt(metrics.strike, 1) : DASH}
          </div>
        </div>
        <div>
          <div className="text-hud-muted">Spread %</div>
          <div className="tnum font-mono text-hud-text">
            {metrics?.spread_pct !== undefined ? `${fmt(metrics.spread_pct, 2)}%` : DASH}
          </div>
        </div>
        <div>
          <div className="text-hud-muted">Delta</div>
          <div className="tnum font-mono text-hud-text">{fmt(metrics?.delta, 2)}</div>
        </div>
      </div>

      {/* AI Analysis -- "Terminal Edge & Analyst" sprint (2026-08-27).
          trade_rationale is a deterministic sentence template over real
          structure/OI/trend signals (api/trade_blueprint.py's
          _build_trade_rationale), not an LLM call -- disclosed in the
          subtitle rather than left implicit. */}
      {blueprint?.structure && (
        <div className="mt-3 rounded-lg border border-hud-border bg-hud-bg/60 p-3 text-[11px]">
          <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wide text-hud-muted">
            <Sparkles className="h-3 w-3" />
            AI Analysis
          </div>
          <p className="mt-1.5 text-hud-text">{blueprint.trade_rationale || 'No structural or order-flow evidence yet.'}</p>
          <div className="mt-2 grid grid-cols-2 gap-2 border-t border-hud-border pt-2">
            <div>
              <div className="text-hud-muted">Overall Bias</div>
              <div className={'mt-0.5 flex items-center gap-1 font-bold ' + (direction === 'BULL' ? 'text-bull' : 'text-bear')}>
                {direction === 'BULL' ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
                {direction}
              </div>
            </div>
            <div>
              <div className="text-hud-muted">Trend Direction</div>
              {(() => {
                const { Icon, tone } = trendIcon(blueprint.structure.trend)
                return (
                  <div className={'mt-0.5 flex items-center gap-1 font-bold ' + tone}>
                    <Icon className="h-3.5 w-3.5" />
                    <span className="truncate">{blueprint.structure.trend}</span>
                  </div>
                )
              })()}
            </div>
          </div>
        </div>
      )}

      {/* Execution Module -- stops propagation so pressing/holding the
          button doesn't also toggle the card's own onSelect (opening/
          closing the candlestick panel). */}
      <div className="mt-3 border-t border-hud-border pt-3" onClick={(e) => e.stopPropagation()}>
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[10px] font-bold uppercase tracking-wide text-hud-muted">
            Execution Module
          </span>
          <span className="rounded bg-horizon-btst/15 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-horizon-btst">
            Paper Only
          </span>
        </div>

        {execution.status === 'idle' || execution.status === 'error' ? (
          <>
            <HoldToExecuteButton
              label={direction === 'BULL' ? 'Stage Paper Long' : 'Stage Paper Short'}
              tone={direction === 'BULL' ? 'bull' : 'bear'}
              onConfirm={handleExecute}
            />
            {execution.status === 'error' && (
              <p className="mt-2 text-[11px] text-bear">{execution.error}</p>
            )}
          </>
        ) : execution.status === 'staging' ? (
          <div className="rounded-lg border border-hud-border py-2.5 text-center text-xs font-bold uppercase tracking-wide text-hud-muted">
            Staging ticket…
          </div>
        ) : (
          execution.ticket && (
            <div className="rounded-lg border border-hud-border bg-hud-bg/60 p-3 text-[11px]">
              <div className="flex items-center justify-between">
                <span
                  className={
                    'rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ' +
                    (execution.ticket.status === 'READY_TO_STAGE'
                      ? 'bg-bull/15 text-bull'
                      : 'bg-bear/15 text-bear')
                  }
                >
                  {execution.ticket.status.replace(/_/g, ' ')}
                </span>
                <span className="tnum text-hud-muted">
                  Qty {execution.ticket.quantity} ({execution.ticket.lot_count} lot
                  {execution.ticket.lot_count === 1 ? '' : 's'})
                </span>
              </div>
              <div className="tnum mt-2 text-hud-text">
                Est. max loss <span className="font-bold text-bear">₹{fmt(execution.ticket.estimated_max_loss)}</span>
              </div>
              {execution.ticket.blockers.length > 0 && (
                <ul className="mt-2 space-y-1 border-t border-hud-border pt-2 text-hud-muted">
                  {execution.ticket.blockers.slice(0, 4).map((b) => (
                    <li key={b}>⚠ {b}</li>
                  ))}
                </ul>
              )}
              <p className="mt-2 border-t border-hud-border pt-2 text-[10px] italic text-hud-muted">
                {execution.ticket.warning}
              </p>
              <button
                type="button"
                onClick={execution.reset}
                className="mt-2 w-full rounded border border-hud-border py-1.5 text-[10px] font-bold uppercase tracking-wide text-hud-muted hover:bg-hud-panel-hover hover:text-hud-text"
              >
                Stage Another
              </button>
            </div>
          )
        )}
      </div>
    </article>
  )
}
