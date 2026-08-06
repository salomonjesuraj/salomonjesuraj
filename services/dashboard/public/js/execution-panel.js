import { api } from './api.js';
import { formatPrice, escapeHtml } from './utils.js';

function statusClass(status) {
  return String(status || '').toUpperCase() === 'READY_TO_STAGE' ? 'ready' : 'blocked';
}

export class ExecutionPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._unsub = null;
  }

  init() {
    if (!this._el) return;
    this._el.innerHTML = `<div class="panel-empty">Loading staged tickets...</div>`;
    this._unsub = api.subscribe('/api/execution/staged?limit=60', data => this._render(data), 7000);
    document.addEventListener('execution:refresh', () => this.refresh());
  }

  async refresh() {
    const data = await api.fetch('/api/execution/staged?limit=60');
    if (data) this._render(data);
  }

  _render(data) {
    const tickets = Array.isArray(data?.tickets) ? data.tickets : [];
    if (!tickets.length) {
      this._el.innerHTML = `
        <div class="execution-empty">
          <b>No staged tickets yet</b>
          <span>Open Journal and press Stage Ticket on a logged setup.</span>
          <small>Live order placement is locked off in this phase.</small>
        </div>`;
      return;
    }
    this._el.innerHTML = `
      <div class="execution-toolbar">
        <div>
          <b>Execution Staging</b>
          <span>Broker-style ticket preview. Paper-first. No live Upstox orders.</span>
        </div>
        <div class="execution-totals">
          <span><em>Total</em><b>${tickets.length}</b></span>
          <span><em>Ready</em><b>${Number(data.ready || 0)}</b></span>
          <span><em>Blocked</em><b>${Number(data.blocked || 0)}</b></span>
        </div>
      </div>
      <div class="execution-list">
        ${tickets.map(t => this._ticket(t)).join('')}
      </div>`;
  }

  _ticket(t) {
    const cls = statusClass(t.status);
    const blockers = Array.isArray(t.blockers) && t.blockers.length
      ? t.blockers.slice(0, 5).map(x => escapeHtml(x)).join(' | ')
      : 'All staging gates passed';
    return `
      <article class="execution-ticket ${cls}">
        <div class="execution-head">
          <div>
            <b>${escapeHtml(t.symbol || '-')}</b>
            <span>${escapeHtml(t.contract || '-')}</span>
          </div>
          <em class="${cls}">${escapeHtml(t.status || 'BLOCKED')}</em>
        </div>
        <div class="execution-grid">
          <span><label>Side</label><b>${escapeHtml(t.side || 'BUY')}</b><small>${escapeHtml(t.decision || '-')}</small></span>
          <span><label>Limit</label><b>${formatPrice(t.limit_price)}</b><small>Option premium</small></span>
          <span><label>Qty / Lots</label><b>${Number(t.quantity || 0)}</b><small>${Number(t.lot_count || 0)} lots x ${Number(t.lot_size || 0)}</small></span>
          <span><label>Risk</label><b class="negative">${formatPrice(t.estimated_max_loss)}</b><small>Budget ${formatPrice(t.risk_amount)}</small></span>
          <span><label>Option SL</label><b>${formatPrice(t.estimated_option_sl)}</b><small>${Number(t.option_sl_pct || 0).toFixed(1)}% premium risk | Delta ${Number(t.delta_used || 0).toFixed(2)}</small></span>
          <span><label>Trigger</label><b>${formatPrice(t.trigger_price)}</b><small>Underlying activation</small></span>
          <span><label>Flat Net P&L</label><b class="${Number(t.net_pnl_flat || 0) < 0 ? 'negative' : 'positive'}">${formatPrice(t.net_pnl_flat)}</b><small>Costs ${formatPrice(t.total_costs)} | ${Number(t.cost_as_pct_of_premium || 0).toFixed(2)}%</small></span>
        </div>
        <div class="execution-context">
          <span><label>Instrument</label><b>${escapeHtml(t.instrument_key || '-')}</b></span>
          <span><label>Scores</label><b>Conv ${Math.round(Number(t.scores?.conviction || 0))} | Str ${Math.round(Number(t.scores?.strength || 0))} | MTF ${Math.round(Number(t.scores?.mtf || 0))}</b></span>
          <span><label>Block / Warning</label><b>${blockers}</b><small>${escapeHtml(t.warning || '')}</small></span>
        </div>
      </article>`;
  }

  destroy() {
    if (this._unsub) this._unsub();
  }
}
