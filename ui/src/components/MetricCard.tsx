export const DASH = '—'

export type MetricTone = 'neutral' | 'good' | 'warn' | 'bad'

const TONE_CLASS: Record<MetricTone, string> = {
  neutral: 'text-hud-text',
  good: 'text-bull',
  warn: 'text-horizon-btst',
  bad: 'text-bear',
}

interface MetricCardProps {
  label: string
  value: string
  tone?: MetricTone
  sublabel?: string
}

/** One metric in a Command Center tool route's top strip. `value` is
 * always a pre-formatted string so a genuinely missing number renders
 * as the same honest "—" every other metric display in this app uses
 * (DynamicTimeline, ActionCard, IndexPulseHeader) -- never a fabricated
 * 0. A dash value always renders muted regardless of `tone`, since a
 * missing reading isn't a neutral *state*, it's an absence of one. */
export function MetricCard({ label, value, tone = 'neutral', sublabel }: MetricCardProps) {
  const isMissing = value === DASH
  return (
    <div className="rounded-xl border border-hud-border bg-hud-panel p-4">
      <div className="text-[10px] font-bold uppercase tracking-wide text-hud-muted">{label}</div>
      <div
        className={
          'tnum mt-2 font-mono text-2xl font-bold ' +
          (isMissing ? 'text-hud-muted' : TONE_CLASS[tone])
        }
      >
        {value}
      </div>
      {sublabel && <div className="mt-1 text-[10px] text-hud-muted">{sublabel}</div>}
    </div>
  )
}
