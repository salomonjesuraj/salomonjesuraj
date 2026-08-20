/**
 * EBIE Verdict — Phase EB-12 (increment 2). New-shell only, per
 * docs/EBIE-IMPLEMENTATION-ANSWERS.md Q4.1 (all new EBIE UI is New-shell
 * only; Classic stays frozen). Ranked candidate list + expand-row detail,
 * per docs/EBIE-BLUEPRINT.md Section 32 ("Main table... do not show 30
 * indicator columns by default... Expand row: Why Now / Price Structure /
 * Accumulation / Derivatives / Sentiment / Risk") and Section 34 ("Why
 * Not? Rejection UI... explain why it refused a trade, not only why it
 * likes one").
 *
 * Pure consumer of GET /api/ebie/candidates (EB-12 increment 1) -- every
 * number/reason here is already computed server-side (EB-8 verdict, EB-9
 * trap risk, EB-11 portfolio fit); this file only ranks, filters, and
 * renders. No calibrated probability is ever shown as a percentage here
 * (Non-Negotiable Rule #7) -- the raw evidence-agreement score and
 * verdict band are shown as what they are, not dressed up as "% chance".
 */
import { api } from './api.js';

const VERDICT_TONE = {
  ARMED_CANDIDATE: 'good', READY: 'good', PRE_BREAKOUT_WATCH: 'warn',
  DEVELOPING: 'flat', NO_EDGE: 'bad', HARD_BLOCKED: 'bad',
};

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function ageLabel(iso) {
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.floor(ms / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

function scorePill(label, value, tone) {
  if (value == null) return `<span class="ifx-ebie-metric muted">${label} —</span>`;
  return `<span class="ifx-ebie-metric ${tone}">${label} ${Number(value).toFixed(0)}</span>`;
}

export class EbieVerdictPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._filter = 'all';
    this._expanded = new Set();
    this._candidates = [];
    this._loadGen = 0;
  }

  init() {
    if (!this._el) return;
    this._el.innerHTML = `
      <div class="ifx-ebie-toolbar">
        <div class="ifx-ebie-pills" id="ebieFilterPills"></div>
        <button type="button" class="ifx-btn" id="ebieRefreshBtn">Refresh</button>
      </div>
      <div class="ifx-ebie-list" id="ebieList"></div>
    `;
    const pills = [
      { key: 'all', label: 'All' },
      { key: 'false', label: 'Fired' },
      { key: 'true', label: 'Rejected' },
    ];
    const pillsEl = this._el.querySelector('#ebieFilterPills');
    pillsEl.innerHTML = pills.map((p) =>
      `<button type="button" class="ifx-btn ifx-ebie-pill${p.key === this._filter ? ' on' : ''}" data-filter="${p.key}">${p.label}</button>`
    ).join('');
    pillsEl.querySelectorAll('[data-filter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        this._filter = btn.dataset.filter;
        pillsEl.querySelectorAll('.ifx-ebie-pill').forEach((b) => b.classList.toggle('on', b === btn));
        this._load();
      });
    });
    this._el.querySelector('#ebieRefreshBtn').addEventListener('click', () => this._load());

    this._load();
    this._poll = setInterval(() => this._load(), 30000);
  }

  async _load() {
    // Real bug caught during EB-12's own live verification (not
    // synthetic): rapid filter-pill clicks plus the 30s poll can leave
    // multiple _load() calls in flight at once; without a guard, an
    // OLDER request (e.g. still-resolving "all") completing AFTER a
    // NEWER one (e.g. "Fired" just clicked) silently overwrites the
    // correct, more-recent render with stale data -- reproduced live:
    // clicking "Fired" sent the right suppressed=false request (visible
    // in the network log) but the table kept showing "rejected" rows
    // because an in-flight "all" response landed last. Fixed with a
    // monotonic request generation counter -- a response only renders
    // if it's still the most recent request issued.
    const listEl = this._el.querySelector('#ebieList');
    const suppressedParam = this._filter === 'all' ? 'all' : this._filter;
    const requestGen = ++this._loadGen;
    const data = await api.fetch(`/api/ebie/candidates?limit=40&suppressed=${suppressedParam}`);
    if (requestGen !== this._loadGen) return; // a newer request has since started -- discard this stale one
    if (!data || data.available === false) {
      listEl.innerHTML = `<div class="ifx-ebie-empty">${esc(data?.reason || 'Request failed.')}</div>`;
      return;
    }
    this._candidates = data.candidates || [];
    this._render();
  }

  _render() {
    const listEl = this._el.querySelector('#ebieList');
    if (!this._candidates.length) {
      listEl.innerHTML = `<div class="ifx-ebie-empty">No candidates in this window.</div>`;
      return;
    }
    listEl.innerHTML = this._candidates.map((c, i) => this._rowHtml(c, i)).join('');
    listEl.querySelectorAll('[data-ebie-row]').forEach((row) => {
      row.addEventListener('click', () => {
        const idx = row.dataset.ebieRow;
        if (this._expanded.has(idx)) this._expanded.delete(idx); else this._expanded.add(idx);
        this._render();
      });
    });
  }

  _rowHtml(c, i) {
    const tone = VERDICT_TONE[c.verdict] || 'flat';
    const dirTone = c.direction === 'bullish' ? 'good' : c.direction === 'bearish' ? 'bad' : 'flat';
    const expanded = this._expanded.has(String(i));
    const trapTone = c.trap_risk_score == null ? 'muted' : c.trap_risk_score >= 50 ? 'risk' : 'flat';
    const pfTone = c.portfolio_fit_score == null ? 'muted' : c.portfolio_fit_score < 60 ? 'risk' : 'flat';

    const header = `
      <div class="ifx-ebie-row${expanded ? ' expanded' : ''}" data-ebie-row="${i}">
        <span class="ifx-ebie-symbol">${esc(c.symbol)}</span>
        <span class="ifx-ebie-dir ${dirTone}">${esc(c.direction || '—')}</span>
        <span class="ifx-ebie-verdict ${tone}">${esc((c.verdict || '—').replace(/_/g, ' '))}</span>
        ${scorePill('Score', c.score, 'flat')}
        ${scorePill('Bull', c.bull_score, 'good')}
        ${scorePill('Bear', c.bear_score, 'bad')}
        ${scorePill('Trap', c.trap_risk_score, trapTone)}
        ${scorePill('Portfolio', c.portfolio_fit_score, pfTone)}
        <span class="ifx-ebie-suppressed">${c.suppressed ? 'rejected' : 'fired'}</span>
        <span class="ifx-ebie-age">${ageLabel(c.created_at)}</span>
        <span class="ifx-ebie-chevron">${expanded ? '▾' : '▸'}</span>
      </div>`;

    if (!expanded) return header;
    return header + this._detailHtml(c);
  }

  _detailHtml(c) {
    const listBlock = (title, items) => `
      <div class="ifx-ebie-detail-block">
        <h5>${title}</h5>
        ${items && items.length ? `<ul>${items.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : '<p class="ifx-ebie-none">None</p>'}
      </div>`;
    const kv = (label, value, suffix = '') => `<div class="ifx-ebie-kv"><label>${label}</label><span>${value == null ? '—' : esc(value) + suffix}</span></div>`;

    const ps = c.price_structure || {};
    const acc = c.accumulation || {};
    const der = c.derivatives || {};
    const sent = c.sentiment || {};

    return `
      <div class="ifx-ebie-detail">
        <div class="ifx-ebie-detail-cols">
          ${listBlock('Why Now', c.why_now)}
          ${listBlock('Why Not', c.why_not)}
        </div>
        <div class="ifx-ebie-detail-grid">
          <div class="ifx-ebie-detail-block">
            <h5>Price Structure</h5>
            ${kv('Entry', ps.entry_price != null ? '₹' + Number(ps.entry_price).toFixed(2) : null)}
            ${kv('Invalidation', ps.invalidation_price != null ? '₹' + Number(ps.invalidation_price).toFixed(2) : null)}
            ${kv('Target', ps.target_price != null ? '₹' + Number(ps.target_price).toFixed(2) : null)}
            ${kv('Risk:Reward', c.risk_reward_ratio != null ? Number(c.risk_reward_ratio).toFixed(2) : null)}
          </div>
          <div class="ifx-ebie-detail-block">
            <h5>Accumulation</h5>
            ${kv('CLV (EMA)', acc.clv_ema != null ? Number(acc.clv_ema).toFixed(2) : null)}
            ${kv('VCP', acc.vcp_score != null ? Number(acc.vcp_score).toFixed(0) : null, acc.vcp_grade ? ` (${acc.vcp_grade})` : '')}
            ${kv('Rel. Volume', acc.rel_vol_20d != null ? Number(acc.rel_vol_20d).toFixed(2) + 'x' : null)}
            ${kv('Book Imbalance', acc.microstructure_book_imbalance != null ? Number(acc.microstructure_book_imbalance).toFixed(2) : null)}
          </div>
          <div class="ifx-ebie-detail-block">
            <h5>Derivatives</h5>
            ${kv('Futures Basis', der.futures_basis_pct != null ? Number(der.futures_basis_pct).toFixed(2) + '%' : null)}
            ${kv('Futures OI Δ', der.futures_oi_change_pct != null ? Number(der.futures_oi_change_pct).toFixed(2) + '%' : null)}
            ${kv('Weighted PCR', der.weighted_pcr != null ? Number(der.weighted_pcr).toFixed(2) : null)}
            ${kv('Call Wall', der.call_wall_state)}
            ${kv('Put Wall', der.put_wall_state)}
          </div>
          <div class="ifx-ebie-detail-block">
            <h5>Sentiment</h5>
            ${kv('News', sent.news_sentiment)}
            ${kv('Impact', sent.news_sentiment_impact != null ? Number(sent.news_sentiment_impact).toFixed(3) : null)}
            ${kv('Articles', sent.news_article_count)}
          </div>
        </div>
        ${listBlock('Portfolio Correlation', (c.risk || {}).correlated_symbols)}
      </div>`;
  }

  destroy() {
    if (this._poll) clearInterval(this._poll);
  }
}
