import { fetchSuppressedSignals } from '../lib/api'
import { usePolling } from '../hooks/usePolling'

const DASH = '—'

/** Zone 2's empty-state replacement: when nothing has cleared the real
 * 80.0 conviction floor, show the single closest candidate instead of
 * a void. Sourced from /api/signals/suppressed (the SAME score space
 * as the 80.0 floor itself, via routes/scanner.py's suppression
 * pipeline) -- not /api/ticks' own separate composite-score layer,
 * which answers a different question and would misrepresent "how
 * close is the real gate to opening" if used here instead. */
export function RadarScanningStrip() {
  const { data: suppressed } = usePolling(fetchSuppressedSignals, 5000, [])

  const best = (suppressed ?? [])
    .filter((s) => s.score !== undefined)
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))[0]

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-dashed border-hud-border bg-hud-panel/50 px-5 py-4">
      <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-wide text-hud-muted">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-horizon-scalp" />
        Radar Scanning F&O Universe
      </div>
      {!best ? (
        <p className="text-sm text-hud-muted">No setup in flight right now.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
          <span className="font-mono text-base font-bold text-hud-text">{best.symbol}</span>
          <span className="text-xs text-hud-muted">
            {best.side ?? '—'} · closest near-miss of the session
          </span>
          <span className="tnum font-mono text-sm font-bold text-horizon-btst">
            {best.score?.toFixed(1) ?? DASH}
            <span className="ml-1 text-[10px] font-normal text-hud-muted">/ 80.0 needed</span>
          </span>
          {best.grade && (
            <span className="rounded bg-hud-muted/10 px-1.5 py-0.5 text-[10px] font-bold text-hud-muted">
              {best.grade}
            </span>
          )}
          <span className="text-xs text-hud-muted">{best.reason?.replace(/_/g, ' ') ?? DASH}</span>
        </div>
      )}
    </div>
  )
}
