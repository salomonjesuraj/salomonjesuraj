/**
 * Shared recharts theming for the "Data Studio" visual overhaul
 * (2026-08-27). Deliberately its own small palette, not the app-wide
 * --color-bull/--color-bear tokens (index.css's neon #39ff8a/#ff3d5e) --
 * the user's own explicit, strict instruction for this overhaul was
 * "#10B981 (emerald-500) for bullish/profit... #EF4444 (red-500) for
 * bearish/loss" specifically for the new chart work. Every existing
 * non-chart badge/pill in this app (ActionCard, Sidebar, MetricCard)
 * keeps using the established bull/bear tokens untouched -- this
 * overhaul's own scope is the four charted routes, not an app-wide
 * recolor of UI that already shipped and wasn't asked to change.
 */
export const CHART_BULL = '#10B981'
export const CHART_BEAR = '#EF4444'

/** Grid lines "hidden or highly transparent" per the overhaul's own
 * design constraint -- a neutral slate stroke at 10% opacity rather
 * than removing the grid entirely, so long charts still have a faint
 * horizontal reference without visual clutter. */
export const CHART_GRID_STROKE = '#94a3b8'
export const CHART_GRID_OPACITY = 0.1

/** Axis line/tick color -- matches --color-hud-muted (index.css) so
 * axis labels read as secondary text, consistent with every other
 * muted label in this app. */
export const CHART_AXIS_COLOR = '#6b7684'

/** #RRGGBB -> {r,g,b}, for charts that interpolate between CHART_BULL/
 * CHART_BEAR (e.g. the optimizer scatter's Sharpe color scale) rather
 * than using either as a flat fill -- derived from the hex constants
 * above so the two representations can't drift apart. */
export function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const n = Number.parseInt(hex.slice(1), 16)
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 }
}
