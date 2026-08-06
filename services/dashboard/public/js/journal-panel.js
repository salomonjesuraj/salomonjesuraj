import { api } from './api.js';
import { formatPrice, formatPct, escapeHtml } from './utils.js';

function statusClass(value) {
  const v = String(value || '').toUpperCase();
  if (v === 'PLANNED') return 'planned';
  if (v === 'WATCH') return 'watch';
  if (v === 'BLOCKED') return 'blocked';
  if (v === 'CLOSED') return 'closed';
  return 'watch';
}

function decisionClass(value) {
  const v = String(value || '').toUpperCase();
  if (v.includes('CE') || v === 'BUY') return 'buy';
  if (v.includes('PE') || v === 'SELL') return 'sell';
  if (v.includes('WAIT') || v.includes('HOLD')) return 'hold';
  return 'avoid';
}

function compactList(items, empty = 'No blockers') {
  if (!Array.isArray(items) || !items.length) return escapeHtml(empty);
  return items.slice(0, 3).map(x => escapeHtml(x)).join(' | ');
}

export class JournalPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._unsub = null;
  }

  init() {
    if (!this._el) return;
    this._el.innerHTML = `<div class="panel-empty">Loading paper journal...</div>`;
    this._unsub = api.subscribe('/api/journal/trades?limit=60', (data) => this._render(data), 7000);
    document.addEventListener('journal:refresh', () => this.refresh());
  }

  async refresh() {
    const data = await api.fetch('/api/journal/trades?limit=60');
    if (data) this._render(data);
  }

  async _markOutcome(id, outcome) {
    if (!id) return;
    const res = await api.post(`/api/journal/trades/${encodeURIComponent(id)}/outcome`, { outcome });
    if (res?.ok) this.refresh();
  }

  _render(data) {
    const trades = Array.isArray(data?.trades) ? data.trades : [];
    if (!trades.length) {
      this._el.innerHTML = `
        <div class="journal-empty">
          <b>No paper trades logged yet</b>
          <span>Select a stock in the screener and press Log Paper Trade.</span>
        </div>`;
      return;
    }
    const stats = this._stats(trades);
    this._el.innerHTML = `
      <div class="journal-toolbar">
        <div>
          <b>Paper Trade Journal</b>
          <span>Evidence captured before execution. No live orders.</span>
        </div>
        <div class="journal-stat-grid">
          <span><em>Today</em><b>${stats.today}</b></span>
          <span><em>Planned</em><b>${stats.planned}</b></span>
          <span><em>Watch</em><b>${stats.watch}</b></span>
          <span><em>Blocked</em><b>${stats.blocked}</b></span>
        </div>
      </div>
      <div class="journal-list">
        ${trades.map(t => this._card(t)).join('')}
      </div>
    `;
    this._el.querySelectorAll('[data-journal-outcome]').forEach(btn => {
      btn.addEventListener('click', () => this._markOutcome(btn.dataset.tradeId, btn.dataset.journalOutcome));
    });
    this._el.querySelectorAll('[data-stage-ticket]').forEach(btn => {
      btn.addEventListener('click', () => this._stageTicket(btn.dataset.stageTicket, btn));
    });
    this._el.querySelectorAll('[data-journal-discretion]').forEach(btn => {
      btn.addEventListener('click', () => this._markDiscretion(btn.dataset.tradeId, btn.dataset.journalDiscretion));
    });
  }

  _stats(trades) {
    const todayKey = new Date().toISOString().slice(0, 10);
    const todayTrades = trades.filter(t => String(t.created_at_ist || '').slice(0, 10) === todayKey);
    return {
      today: todayTrades.length,
      planned: todayTrades.filter(t => t.status === 'PLANNED').length,
      watch: todayTrades.filter(t => t.status === 'WATCH').length,
      blocked: todayTrades.filter(t => t.status === 'BLOCKED').length,
      taken: todayTrades.filter(t => String(t.discretionary_action || '').toUpperCase() === 'TAKEN').length,
      skipped: todayTrades.filter(t => String(t.discretionary_action || '').toUpperCase() === 'SKIPPED').length,
      notReviewed: todayTrades.filter(t => String(t.discretionary_action || 'NOT_REVIEWED').toUpperCase() === 'NOT_REVIEWED').length,
    };
  }

  _card(t) {
    const option = t.option || {};
    const event = option.event_calendar || {};
    const news = t.news_edge || {};
    const status = statusClass(t.status);
    const decision = decisionClass(t.decision);
    return `
      <article class="journal-card ${status}">
        <div class="journal-card-head">
          <div>
            <b>${escapeHtml(t.symbol || '-')}</b>
            <span>${escapeHtml(t.sector || '-')} | ${escapeHtml(t.created_at_ist || '-')}</span>
          </div>
          <div class="journal-pills">
            <em class="${decision}">${escapeHtml(t.decision || '-')}</em>
            <em class="${status}">${escapeHtml(t.status || 'WATCH')}</em>
            <em>${escapeHtml(t.discretionary_action || 'NOT_REVIEWED')}</em>
            <em>${escapeHtml(option.quality_grade || '-')}</em>
          </div>
        </div>
        <div class="journal-levels">
          <span><label>LTP</label><b>${formatPrice(t.ltp)}</b><small>${formatPct(t.change_pct)}</small></span>
          <span><label>Entry</label><b>${formatPrice(t.entry)}</b><small>R:R ${Number(t.rr1 || 0).toFixed(1)}:1</small></span>
          <span><label>SL</label><b class="negative">${formatPrice(t.stop)}</b><small>Risk ${formatPrice(t.risk_amount)}</small></span>
          <span><label>T1 / T2</label><b class="positive">${formatPrice(t.target1)}</b><small>${formatPrice(t.target2)}</small></span>
          <span><label>Scores</label><b>${Math.round(Number(t.option_readiness || 0))} / ${Math.round(Number(t.setup_strength || 0))}</b><small>Conv / Str</small></span>
        </div>
        <div class="journal-context">
          <div><label>Option contract</label><b>${escapeHtml(option.suggested_contract || '-')}</b><small>${escapeHtml(option.execution_status || 'WAIT_CONTRACT')} | Spread ${Number(option.spread_pct || 0).toFixed(2)}% | OI ${Math.round(Number(option.oi || 0))}</small></div>
          <div><label>Reality</label><b>Delta ${Number(option.delta || 0).toFixed(2)} | IVR ${option.iv_rank == null || Number(option.iv_rank) < 0 ? 'PENDING' : Number(option.iv_rank).toFixed(0)}</b><small>BE ${formatPrice(option.breakeven_underlying)} | Req ${Number(option.required_move_pct || 0).toFixed(2)}% / Exp ${Number(option.expected_move_pct || 0).toFixed(2)}%</small></div>
          <div><label>Costs</label><b>Net flat ${formatPrice(option.net_pnl)}</b><small>Costs ${formatPrice(option.total_costs)} | ${Number(option.cost_as_pct_of_premium || 0).toFixed(2)}% premium</small></div>
          <div><label>Safety</label><b>Liq ${option.liquidity_whitelist_pass === true ? 'OK' : option.liquidity_whitelist_pass === false ? 'NO' : 'WAIT'} | Physical ${option.physical_settlement_block ? 'BLOCK' : 'OK'}</b><small>Event ${escapeHtml(option.next_event_date || event.next_event_date || 'clear')}</small></div>
          <div><label>News edge</label><b>${escapeHtml(news.stance || 'NO_NEWS')} ${Number(news.score || 0).toFixed(2)}</b><small>${escapeHtml(news.action || 'Use price confirmation first')}</small></div>
          <div><label>Why</label><b>${compactList(t.strength_reasons, 'Evidence building')}</b><small>${escapeHtml(t.mtf_text || 'MTF building')}</small></div>
          <div><label>Blockers</label><b>${compactList([...(t.rejection_reasons || []), ...(option.hard_blockers || []), ...(option.blockers || [])])}</b><small>${compactList(news.risks, 'No news risk flagged')}</small></div>
        </div>
        ${t.status === 'CLOSED' ? `<div class="journal-outcome">Closed: ${escapeHtml(t.outcome || 'REVIEW')} @ ${formatPrice(t.exit_price)}</div>` : `
          <div class="journal-actions">
            <button type="button" data-stage-ticket="${escapeHtml(t.id)}">Stage Ticket</button>
            <button type="button" data-trade-id="${escapeHtml(t.id)}" data-journal-discretion="TAKEN">Taken</button>
            <button type="button" data-trade-id="${escapeHtml(t.id)}" data-journal-discretion="SKIPPED">Skipped</button>
            <button type="button" data-trade-id="${escapeHtml(t.id)}" data-journal-outcome="WIN">Mark Win</button>
            <button type="button" data-trade-id="${escapeHtml(t.id)}" data-journal-outcome="LOSS">Mark Loss</button>
            <button type="button" data-trade-id="${escapeHtml(t.id)}" data-journal-outcome="SKIP">Skip</button>
          </div>`}
      </article>
    `;
  }

  async _stageTicket(id, buttonEl) {
    const data = await api.fetch('/api/journal/trades?limit=200');
    const trade = (data?.trades || []).find(x => x.id === id);
    if (!trade) return;
    const original = buttonEl.textContent;
    buttonEl.disabled = true;
    buttonEl.textContent = 'Staging...';
    const res = await api.post('/api/execution/stage', { trade });
    if (res?.ok) {
      buttonEl.textContent = res.ticket?.status === 'READY_TO_STAGE' ? 'Ticket Ready' : 'Ticket Blocked';
      document.dispatchEvent(new CustomEvent('execution:refresh', { detail: res.ticket }));
      setTimeout(() => {
        buttonEl.disabled = false;
        buttonEl.textContent = original;
      }, 1800);
      return;
    }
    buttonEl.textContent = 'Stage failed';
    setTimeout(() => {
      buttonEl.disabled = false;
      buttonEl.textContent = original;
    }, 1800);
  }

  async _markDiscretion(id, action) {
    if (!id || !action) return;
    const payload = { discretionary_action: action };
    if (action === 'SKIPPED') payload.skip_reason = 'Skipped from dashboard review';
    const res = await api.post(`/api/journal/trades/${encodeURIComponent(id)}/discretion`, payload);
    if (res?.ok) this.refresh();
  }

  destroy() {
    if (this._unsub) this._unsub();
  }
}
