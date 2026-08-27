import { fetchOptionSummary, fetchTradeBlueprint } from '../lib/api'
import { usePolling } from '../hooks/usePolling'
import type { Candidate } from '../lib/candidates'
import { useLtp } from '../store/useTickStore'
import type { OIBuildupType, TradeHorizon } from '../types'
import { DynamicTimeline } from './DynamicTimeline'
import { ProximityBar } from './ProximityBar'

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
  onSelect?: (symbol: string) => void
}

export function ActionCard({ candidate, isActive, onSelect }: ActionCardProps) {
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

  return (
    <article
      onClick={() => onSelect?.(symbol)}
      className={
        'min-w-0 rounded-xl border bg-hud-panel p-4 shadow-lg shadow-black/30 transition-colors hover:bg-hud-panel-hover ' +
        (onSelect ? 'cursor-pointer ' : '') +
        (isActive ? 'border-bull ring-1 ring-bull' : 'border-hud-border')
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
    </article>
  )
}
