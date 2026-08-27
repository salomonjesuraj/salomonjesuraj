import { ShieldAlert } from 'lucide-react'
import { DASH, MetricCard } from '../components/MetricCard'
import { PageHeader } from '../components/PageHeader'
import { useSafetyMetrics } from '../hooks/useSafetyMetrics'
import type { AlertLogEntry, SafetyGate, ServiceHealth } from '../types'

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

/** `/safety` -- Safety & Logs, wired to real backend routes (2026-08-27
 * data-wiring sprint). `/api/safety/status`'s own gate checklist is the
 * real "active gates" data source this route asked for; `/api/health`
 * supplies per-service heartbeat, `/api/alerts/log` the real Telegram
 * delivery log -- all three merged into one scrolling terminal-style
 * feed below, newest-relevant first. No generic "error rate" is
 * computed anywhere in this backend, so that metric card is replaced
 * with the real number this data actually supports: how many of the
 * safety gates are currently blocking. */
export function SafetyLogs() {
  const { data } = useSafetyMetrics()
  const status = data?.status
  const health = data?.health
  const alertLog = data?.alertLog ?? []

  const blockedGates = status?.gates.filter((g) => g.state === 'block').length
  const serviceEntries = health ? Object.entries(health.services) : []
  const healthyCount = serviceEntries.filter(([, h]) => h.status === 'healthy').length

  const lines: LogLine[] = [
    ...(status?.gates.map(gateLine) ?? []),
    ...serviceEntries.map(([name, h]) => healthLine(name, h)),
    ...alertLog.map(alertLine),
  ]

  const verdictTone = status?.verdict === 'PAPER_READY' ? 'good' : status?.verdict === 'WATCH_READY' ? 'warn' : 'bad'

  return (
    <div className="flex flex-1 flex-col gap-6">
      <PageHeader
        icon={ShieldAlert}
        title="Safety & Logs"
        subtitle={status ? `${status.session} · ${status.next_action}` : 'Waiting for the safety cockpit.'}
      />

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
          tone={
            !health ? 'neutral' : healthyCount === serviceEntries.length ? 'good' : 'warn'
          }
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
