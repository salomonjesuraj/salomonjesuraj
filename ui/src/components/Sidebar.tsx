import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  FlaskConical,
  LineChart,
  Radar,
  ShieldAlert,
  Wallet,
  type LucideIcon,
} from 'lucide-react'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Sniper HUD', icon: Radar },
  { to: '/positions', label: 'Active Cockpit', icon: Wallet },
  { to: '/analytics', label: 'Options Analytics', icon: LineChart },
  { to: '/optimizer', label: 'The Lab', icon: FlaskConical },
  { to: '/journal', label: 'The Ledger', icon: BookOpen },
  { to: '/safety', label: 'Safety & Logs', icon: ShieldAlert },
]

const STORAGE_KEY = 'command-center-sidebar-collapsed'

function readStoredCollapsed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    // Private browsing / storage disabled -- default to expanded, never crash.
    return false
  }
}

/**
 * Persistent left navigation for the unified Command Center (2026-08-27
 * restructure -- replaces the legacy vanilla-JS dashboard's own nav rail).
 * `sticky top-0 h-screen` on the <aside> keeps it pinned through page
 * scroll without lifting it out of flow, so it still pushes the content
 * column over rather than overlapping it. Collapse state persists in
 * localStorage (per-viewer convenience, not shared state) so a trader who
 * collapses it once doesn't have to redo that every reload.
 */
export function Sidebar() {
  const [collapsed, setCollapsed] = useState(readStoredCollapsed)

  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev
      try {
        localStorage.setItem(STORAGE_KEY, next ? '1' : '0')
      } catch {
        /* nothing to do -- collapse state just won't survive a reload */
      }
      return next
    })
  }

  return (
    <aside
      className={
        'sticky top-0 flex h-screen shrink-0 flex-col border-r border-hud-border bg-hud-panel transition-[width] duration-150 ' +
        (collapsed ? 'w-16' : 'w-56')
      }
    >
      <div className="flex items-center gap-2 border-b border-hud-border px-4 py-4">
        <Radar className="h-5 w-5 shrink-0 text-bull" />
        {!collapsed && (
          <span className="truncate font-mono text-xs font-bold uppercase tracking-[0.2em] text-hud-text">
            Command Center
          </span>
        )}
      </div>

      <nav className="flex flex-1 flex-col gap-1 p-2">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            title={collapsed ? label : undefined}
            className={({ isActive }) =>
              'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ' +
              (isActive
                ? 'bg-bull/10 text-bull'
                : 'text-hud-muted hover:bg-hud-panel-hover hover:text-hud-text')
            }
          >
            <Icon className="h-4 w-4 shrink-0" />
            {!collapsed && <span className="truncate">{label}</span>}
          </NavLink>
        ))}
      </nav>

      <button
        type="button"
        onClick={toggle}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        className="flex items-center justify-center gap-2 border-t border-hud-border px-3 py-3 text-hud-muted transition-colors hover:bg-hud-panel-hover hover:text-hud-text"
      >
        {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        {!collapsed && (
          <span className="text-xs font-bold uppercase tracking-wide">Collapse</span>
        )}
      </button>
    </aside>
  )
}
