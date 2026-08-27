import type { ReactNode } from 'react'

interface ChartTooltipProps {
  active?: boolean
  children?: ReactNode
}

/** Shared dark-mode tooltip shell for every recharts chart in the Data
 * Studio overhaul -- recharts' own <Tooltip> renders whatever
 * `content` returns, so each chart supplies its own field layout as
 * `children` here and gets the same panel chrome (bg-hud-panel,
 * border-hud-border) instead of recharts' default white box. */
export function ChartTooltip({ active, children }: ChartTooltipProps) {
  if (!active) return null
  return (
    <div className="rounded-lg border border-hud-border bg-hud-panel px-3 py-2 text-[11px] shadow-lg shadow-black/40">
      {children}
    </div>
  )
}
