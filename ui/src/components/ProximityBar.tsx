// Matches infusion_models.smc.LATE_ENTRY_MAX_PCT (services/scanner +
// libs/infusion-models) -- the same real threshold that fires the
// LATE_ENTRY warning tag server-side, reused here as the bar's own
// green/amber split rather than an independently invented number.
const LATE_ENTRY_MAX_PCT = 0.75
// Display ceiling -- beyond this the bar just reads "far", matching the
// ~2% "moderately extended but still scoreable" band this project's own
// Probabilistic Grading pivot was calibrated against.
const DISPLAY_CAP_PCT = 2.0

/** Sniper HUD Action Card micro-visual (2026-08-27 Data Studio overhaul)
 * -- a tiny horizontal proximity bar for ob_fvg_distance_pct: full/green
 * when price sits tight against the nearest validated order block or
 * FVG (the same real distance nearest_ob_or_fvg_distance_pct computes
 * server-side, already on the Candidate), draining and amber as price
 * extends away from it, past LATE_ENTRY_MAX_PCT. `null` means no OB/FVG
 * was found at all -- rendered as an explicit empty/hollow state, never
 * as a fabricated 0% (which would misread as "right at the base"). */
export function ProximityBar({ distancePct }: { distancePct: number | null }) {
  if (distancePct === null) {
    return (
      <div className="flex items-center gap-2">
        <div className="h-1.5 flex-1 rounded-full bg-hud-border" />
        <span className="shrink-0 text-[10px] text-hud-muted">no OB/FVG</span>
      </div>
    )
  }

  const clamped = Math.min(distancePct, DISPLAY_CAP_PCT)
  const fillPct = Math.max(0, (1 - clamped / DISPLAY_CAP_PCT) * 100)
  const tight = distancePct <= LATE_ENTRY_MAX_PCT

  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-hud-border">
        <div
          className={'h-full rounded-full ' + (tight ? 'bg-bull' : 'bg-horizon-btst')}
          style={{ width: `${fillPct}%` }}
        />
      </div>
      <span className={'tnum shrink-0 text-[10px] ' + (tight ? 'text-bull' : 'text-horizon-btst')}>
        {distancePct.toFixed(2)}%
      </span>
    </div>
  )
}
