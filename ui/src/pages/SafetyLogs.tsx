import { ShieldAlert } from 'lucide-react'
import { DASH, MetricCard } from '../components/MetricCard'
import { PageHeader } from '../components/PageHeader'
import { useSafetyMetrics } from '../hooks/useSafetyMetrics'
import { useUiEngineStore, type ChartEngineStatus } from '../store/useUiEngineStore'
import type { AlertLogEntry, HealthStatus, SafetyGate, SafetyStatus, ServiceHealth } from '../types'

type LogLevel = 'ok' | 'warn' | 'error'

interface LogLine {
  key: string
  level: LogLevel
  text: string
}

const LEVEL_CLASS: Record<LogLevel, string> = {
  ok: 'text-bull',
  warn: 'text-horizon-btst',
  error: 'text-bear',
}

const LEVEL_MARK: Record<LogLevel, string> = { ok: 'OK', warn: 'WARN', error: 'FAIL' }

function gateLine(gate: SafetyGate): LogLine {
  const level: LogLevel = gate.state === 'pass' ? 'ok' : gate.state === 'warn' ? 'warn' : 'error'
  return { key: `gate:${gate.key}`, level, text: `[GATE] ${gate.label} -- ${gate.detail}` }
}

function healthLine(service: string, health: ServiceHealth): LogLine {
  const level: LogLevel = health.status === 'healthy' ? 'ok' : 'error'
  const reason = health.reason ? ` -- ${health.reason}` : ''
  return { key: `health:${service}`, level, text: `[HEALTH] ${service}: ${health.status}${reason}` }
}

// Exactly 3 real outcome values, per services/alerter/src/alerter/
// engine.py's own _log_delivery call sites -- "delivered" (sent),
// "blocked" (a gate rejected it: cooldown, rate limit, quality), or
// "failed" (Telegram send itself errored). Anything else is a future
// value this page doesn't know about yet -- treated as 'warn', never
// silently painted green.
const ALERT_LEVEL: Record<string, LogLevel> = { delivered: 'ok', blocked: 'warn', failed: 'error' }

function alertLine(entry: AlertLogEntry, i: number): LogLine {
  const level: LogLevel = ALERT_LEVEL[String(entry.outcome || '')] ?? 'warn'
  const ts = entry.ts ? `${entry.ts} ` : ''
  return {
    key: `alert:${i}:${entry.signal_id ?? entry.symbol ?? i}`,
    level,
    text: `[ALERT] ${ts}${entry.symbol ?? '?'} (${entry.grade ?? '-'}) -- ${entry.outcome ?? 'unknown'}${
      entry.reason ? `: ${entry.reason}` : ''
    }`,
  }
}

// ── "Terminal Edge & Analyst" sprint (2026-08-27) -- Admin Terminal ──
// Every module below maps to a REAL signal this backend already
// produces; none is a guess dressed up as telemetry. See each mapping
// function's own comment for exactly which real field it reads.

type ModuleStatus = 'green' | 'amber' | 'red'

const MODULE_LABEL: Record<ModuleStatus, string> = {
  green: 'ACTIVE',
  amber: 'DEGRADED',
  red: 'CRIPPLED',
}
const MODULE_BADGE_CLASS: Record<ModuleStatus, string> = {
  green: 'bg-bull/15 text-bull ring-bull/30',
  amber: 'bg-horizon-btst/15 text-horizon-btst ring-horizon-btst/30',
  red: 'bg-bear/15 text-bear ring-bear/30',
}
const MODULE_DOT_CLASS: Record<ModuleStatus, string> = {
  green: 'bg-bull',
  amber: 'bg-horizon-btst',
  red: 'bg-bear',
}

interface ModuleRow {
  key: string
  label: string
  status: ModuleStatus
  detail: string
}

/** MTF Scanner + Signal & Scoring Engine both map to a real service's
 * own heartbeat from GET /api/health (infusion_common.health's
 * HealthReporter, the same mechanism every service in this stack
 * already uses) -- MTF's own compute path lives inside the api service
 * but depends entirely on feature-engine's live bar_builder for fresh
 * OHLC, so feature-engine's heartbeat is the honest proxy for "is MTF
 * structure data actually fresh right now." No heartbeat at all reads
 * as DEGRADED (amber), not CRIPPLED -- "never checked in yet" and
 * "confirmed broken" are different real states. */
function serviceModuleStatus(health: HealthStatus | undefined, service: string, label: string): ModuleRow {
  const entry = health?.services[service]
  if (!entry) return { key: service, label, status: 'amber', detail: 'No heartbeat received yet' }
  if (entry.status === 'healthy') return { key: service, label, status: 'green', detail: 'Heartbeat OK' }
  return { key: service, label, status: 'red', detail: entry.reason || 'No heartbeat' }
}

/** Risk & Execution Module -- GET /api/safety/status's own real verdict
 * (the same safety cockpit gate checklist Safety & Logs already reads
 * below), not a re-derived guess. */
function riskModuleStatus(status: SafetyStatus | undefined): ModuleRow {
  const label = 'Risk & Execution Module'
  if (!status) return { key: 'risk', label, status: 'amber', detail: 'Waiting for the safety cockpit' }
  if (status.verdict === 'PAPER_READY') return { key: 'risk', label, status: 'green', detail: status.next_action }
  if (status.verdict === 'WATCH_READY') return { key: 'risk', label, status: 'amber', detail: status.next_action }
  return { key: 'risk', label, status: 'red', detail: status.next_action }
}

/** TradingView UI Engine -- the one module with no legitimate backend
 * signal (whether a client-side charting library is rendering correctly
 * is inherently a client fact). Sourced from useUiEngineStore, which
 * LiveCandlestickChart.tsx reports into from its own real chart-
 * creation try/catch. "unknown" (amber, not green) until a chart has
 * actually been opened this session -- reporting green by default would
 * be a fabricated all-clear. */
function uiEngineModuleStatus(chartEngineStatus: ChartEngineStatus, chartEngineError: string | null): ModuleRow {
  const label = 'TradingView UI Engine'
  if (chartEngineStatus === 'ok') return { key: 'ui', label, status: 'green', detail: 'Chart engine initialized OK' }
  if (chartEngineStatus === 'error') {
    return { key: 'ui', label, status: 'red', detail: chartEngineError || 'Chart engine threw on init' }
  }
  return { key: 'ui', label, status: 'amber', detail: 'Not measured yet -- open a chart on Sniper HUD' }
}

/** `/safety` -- Safety & Logs, upgraded into the "God-Mode" Admin
 * Terminal (2026-08-27 "Terminal Edge & Analyst" sprint). The System
 * Health readout is a real aggregate over the four module rows below
 * it, never a hardcoded "100% PERFECT" -- it reads OPERATIONAL only
 * when all four real signals agree, and says exactly how many don't
 * otherwise. */
export function SafetyLogs() {
  const { data } = useSafetyMetrics()
  const chartEngineStatus = useUiEngineStore((s) => s.chartEngineStatus)
  const chartEngineError = useUiEngineStore((s) => s.chartEngineError)
  const status = data?.status
  const health = data?.health
  const alertLog = data?.alertLog ?? []

  const modules: ModuleRow[] = [
    serviceModuleStatus(health, 'feature-engine', 'MTF Scanner'),
    serviceModuleStatus(health, 'scanner', 'Signal & Scoring Engine'),
    uiEngineModuleStatus(chartEngineStatus, chartEngineError),
    riskModuleStatus(status),
  ]
  const greenCount = modules.filter((m) => m.status === 'green').length
  const hasRed = modules.some((m) => m.status === 'red')
  const telemetryReady = Boolean(health || status)

  const systemHealthLabel = !telemetryReady
    ? 'AWAITING TELEMETRY'
    : hasRed
      ? `DEGRADED · ${greenCount}/4 OPERATIONAL`
      : greenCount === modules.length
        ? '100% OPERATIONAL'
        : `PARTIAL · ${greenCount}/4 OPERATIONAL`
  const systemHealthTone: 'good' | 'warn' | 'bad' | 'neutral' = !telemetryReady
    ? 'neutral'
    : hasRed
      ? 'bad'
      : greenCount === modules.length
        ? 'good'
        : 'warn'
  const systemHealthClass = {
    good: 'border-bull/40 bg-bull/10 text-bull',
    warn: 'border-horizon-btst/40 bg-horizon-btst/10 text-horizon-btst',
    bad: 'border-bear/40 bg-bear/10 text-bear',
    neutral: 'border-hud-border bg-hud-panel text-hud-muted',
  }[systemHealthTone]

  const blockedGates = status?.gates.filter((g) => g.state === 'block').length
  const serviceEntries = health ? Object.entries(health.services) : []
  const healthyCount = serviceEntries.filter(([, h]) => h.status === 'healthy').length

  const lines: LogLine[] = [
    ...(status?.gates.map(gateLine) ?? []),
    ...serviceEntries.map(([name, h]) => healthLine(name, h)),
    ...alertLog.map(alertLine),
  ]

  const verdictTone =
    status?.verdict === 'PAPER_READY' ? 'good' : status?.verdict === 'WATCH_READY' ? 'warn' : 'bad'

  return (
    <div className="flex flex-1 flex-col gap-6">
      <PageHeader
        icon={ShieldAlert}
        title="Safety & Logs"
        subtitle={status ? `${status.session} · ${status.next_action}` : 'Waiting for the safety cockpit.'}
      />

      {/* System Health -- the master readout, a real aggregate over the
          module grid below, never a hardcoded claim. */}
      <div className={'rounded-xl border p-4 text-center ' + systemHealthClass}>
        <div className="text-[10px] font-bold uppercase tracking-wide text-hud-muted">System Health</div>
        <div className="tnum mt-1 font-mono text-2xl font-bold">{systemHealthLabel}</div>
      </div>

      {/* Core Engine Status */}
      <div>
        <h2 className="mb-3 text-xs font-bold uppercase tracking-wide text-hud-muted">Core Engine Status</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {modules.map((m) => (
            <div key={m.key} className="rounded-xl border border-hud-border bg-hud-panel p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px] font-bold uppercase tracking-wide text-hud-muted">{m.label}</span>
                <span className={'h-2 w-2 shrink-0 rounded-full ' + MODULE_DOT_CLASS[m.status]} />
              </div>
              <div
                className={
                  'mt-2 inline-block rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ring-1 ' +
                  MODULE_BADGE_CLASS[m.status]
                }
              >
                {MODULE_LABEL[m.status]}
              </div>
              <p className="mt-2 text-[11px] text-hud-muted">{m.detail}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard label="Verdict" value={status?.verdict ?? DASH} tone={status ? verdictTone : 'neutral'} />
        <MetricCard
          label="Blocked Gates"
          value={blockedGates?.toString() ?? DASH}
          tone={blockedGates === undefined ? 'neutral' : blockedGates > 0 ? 'bad' : 'good'}
        />
        <MetricCard
          label="Services Healthy"
          value={health ? `${healthyCount}/${serviceEntries.length}` : DASH}
          tone={!health ? 'neutral' : healthyCount === serviceEntries.length ? 'good' : 'warn'}
        />
        <MetricCard label="Alerts Logged" value={alertLog.length > 0 ? alertLog.length.toString() : DASH} />
      </div>

      <div className="flex flex-1 flex-col rounded-xl border border-hud-border bg-hud-bg">
        <div className="border-b border-hud-border px-4 py-2 text-[10px] font-bold uppercase tracking-wide text-hud-muted">
          System Log
        </div>
        {lines.length > 0 ? (
          <div className="max-h-[28rem] overflow-y-auto px-4 py-3 font-mono text-[11px] leading-relaxed">
            {lines.map((line) => (
              <div key={line.key} className="flex gap-2">
                <span className={'w-10 shrink-0 font-bold ' + LEVEL_CLASS[line.level]}>
                  {LEVEL_MARK[line.level]}
                </span>
                <span className="text-hud-text/90">{line.text}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 py-16 text-center">
            <ShieldAlert className="h-8 w-8 text-hud-muted" />
            <h2 className="text-sm font-bold text-hud-text">Safety Console Ready</h2>
            <p className="max-w-md text-xs text-hud-muted">
              No gate checks, health heartbeats, or alert deliveries to show yet.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
