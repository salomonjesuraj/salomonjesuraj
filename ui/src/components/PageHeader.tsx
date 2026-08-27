import type { LucideIcon } from 'lucide-react'

interface PageHeaderProps {
  icon: LucideIcon
  title: string
  subtitle: string
}

/** Shared header for the four Command Center tool routes -- icon badge +
 * title + one-line subtitle, matching Sniper HUD's own section-heading
 * style (text-hud-muted, uppercase, tracked). */
export function PageHeader({ icon: Icon, title, subtitle }: PageHeaderProps) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-hud-panel ring-1 ring-hud-border">
        <Icon className="h-5 w-5 text-hud-muted" />
      </div>
      <div>
        <h1 className="text-sm font-bold uppercase tracking-wide text-hud-text">{title}</h1>
        <p className="text-xs text-hud-muted">{subtitle}</p>
      </div>
    </div>
  )
}
