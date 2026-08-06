import { api } from './api.js';
import { escapeHtml } from './utils.js';

function gateClass(state) {
  if (state === 'pass') return 'pass';
  if (state === 'warn') return 'warn';
  return 'block';
}

export class SafetyPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._unsub = null;
  }

  init() {
    if (!this._el) return;
    this._el.innerHTML = `<div class="panel-empty">Loading safety cockpit...</div>`;
    this._unsub = api.subscribe('/api/safety/status', data => this._render(data), 5000);
  }

  async refresh() {
    const data = await api.fetch('/api/safety/status');
    if (data) this._render(data);
  }

  async _setKill(enabled) {
    const reason = enabled ? 'Manual dashboard kill-switch enabled' : '';
    const res = await api.post('/api/safety/kill-switch', { enabled, reason });
    if (res?.ok) this.refresh();
  }

  _render(data) {
    const gates = Array.isArray(data?.gates) ? data.gates : [];
    const verdict = String(data?.verdict || 'BLOCKED');
    const verdictClass = verdict === 'PAPER_READY' ? 'ready' : verdict === 'WATCH_READY' ? 'watch' : 'blocked';
    const counts = data?.counts || {};
    const killOn = Boolean(data?.kill_switch?.enabled);
    this._el.innerHTML = `
      <div class="safety-hero ${verdictClass}">
        <div>
          <span>Safety Cockpit</span>
          <b>${escapeHtml(verdict.replace('_', ' '))}</b>
          <small>${escapeHtml(data?.next_action || 'Check blockers first.')} | ${escapeHtml(data?.timestamp_ist || '-')}</small>
        </div>
        <div class="safety-actions">
          <button type="button" class="kill ${killOn ? 'active' : ''}" data-kill="on">Kill Switch ON</button>
          <button type="button" class="clear" data-kill="off">Clear Kill Switch</button>
        </div>
      </div>
      <div class="safety-counts">
        <span><em>Session</em><b>${escapeHtml(data?.session || '-')}</b></span>
        <span><em>Signals</em><b>${Number(counts.active_signals || 0)}</b></span>
        <span><em>Journal Today</em><b>${Number(counts.journal_today || 0)}</b></span>
        <span><em>Staged</em><b>${Number(counts.staged_today || 0)}</b></span>
        <span><em>Ready Tickets</em><b>${Number(counts.ready_tickets || 0)}</b></span>
        <span><em>Blocked Tickets</em><b>${Number(counts.blocked_tickets || 0)}</b></span>
      </div>
      <div class="safety-gates">
        ${gates.map(g => `
          <article class="safety-gate ${gateClass(g.state)}">
            <em>${escapeHtml(String(g.state || 'block').toUpperCase())}</em>
            <b>${escapeHtml(g.label || '-')}</b>
            <span>${escapeHtml(g.detail || '-')}</span>
          </article>
        `).join('')}
      </div>
      <div class="safety-runbook">
        <b>Trading runbook</b>
        <ol>
          <li>Update capital and daily max loss before market open.</li>
          <li>Use scanner auto-rank; log only setups with clean MTF, VWAP, volume, and no chase blocker.</li>
          <li>Stage ticket from Journal; if ticket is blocked, do not override casually.</li>
          <li>Open TradingView for visual confirmation before any real-world action.</li>
          <li>After trade, mark Win/Loss/Skip so the engine learns from realistic outcomes.</li>
        </ol>
      </div>
    `;
    this._el.querySelector('[data-kill="on"]')?.addEventListener('click', () => this._setKill(true));
    this._el.querySelector('[data-kill="off"]')?.addEventListener('click', () => this._setKill(false));
  }

  destroy() {
    if (this._unsub) this._unsub();
  }
}
