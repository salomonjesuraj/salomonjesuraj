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

// EBIE EB-15 Phase 1 item 3 -- matches the literal P6 policy/labels
// (ebie_candidates.py's _dq_status()); shown as a badge, never as a
// filter that hides the row -- the directive is explicit that a
// data-quality problem must stay VISIBLE on the setup, not conceal it.
const DQ_TONE = { READY: 'flat', DEGRADED: 'warn', DATA_UNRELIABLE: 'bad', UNKNOWN: 'muted' };
const DQ_LABEL = { READY: 'DQ OK', DEGRADED: 'DQ DEGRADED', DATA_UNRELIABLE: 'DQ UNRELIABLE', UNKNOWN: 'DQ —' };

// EBIE EB-15 Phase 3 -- tone for the directive's own lightweight-verdict
// label set (compute_lightweight_verdict(), api/ebie_state_queue.py).
// Deliberately a separate map from VERDICT_TONE above -- the full
// candidate-level verdict (EB-8) and this universe-wide lightweight one
// share no label strings, so there's nothing to reuse.
const LIGHTWEIGHT_VERDICT_TONE = {
  BREAKOUT_ARMED: 'good', BREAKDOWN_ARMED: 'good',
  LONG_READY: 'good', SHORT_READY: 'good',
  WATCH_LONG: 'warn', WATCH_SHORT: 'warn',
  LONG_DEVELOPING: 'flat', SHORT_DEVELOPING: 'flat',
  AVOID_TRAP_RISK: 'bad', DATA_UNRELIABLE: 'bad', NO_TRADE: 'muted',
};
const CONFIDENCE_TONE = { VERY_HIGH: 'good', HIGH: 'flat', MEDIUM: 'warn', LOW: 'muted' };

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
    this._verdicts = [];
    this._isUniverse = false;
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
      // EBIE EB-15 Phase 3 -- the universe-wide LIGHTWEIGHT verdict, a
      // fundamentally different data source (/api/ebie/lightweight-
      // verdicts) from the three pills above (all of which read /api/
      // ebie/candidates, i.e. only symbols that already produced a
      // SignalCandidate). This is the directive's own "every developing/
      // pre-breakout/pre-breakdown symbol has a visible verdict BEFORE a
      // strategy candidate ever fires" requirement.
      { key: 'universe', label: 'Universe (Lightweight)' },
    ];
    const pillsEl = this._el.querySelector('#ebieFilterPills');
    pillsEl.innerHTML = pills.map((p) =>
      `<button type="button" class="ifx-btn ifx-ebie-pill${p.key === this._filter ? ' on' : ''}" data-filter="${p.key}">${p.label}</button>`
    ).join('');
    pillsEl.querySelectorAll('[data-filter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        this._filter = btn.dataset.filter;
        // Row indices are reused across candidate-mode and universe-mode
        // arrays -- clear expand state on a mode switch so a stale index
        // from one array's rows can't misapply to the other's.
        this._expanded.clear();
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
    const requestGen = ++this._loadGen;
    const isUniverse = this._filter === 'universe';
    const data = await api.fetch(
      isUniverse
        ? '/api/ebie/lightweight-verdicts'
        : `/api/ebie/candidates?limit=40&suppressed=${this._filter === 'all' ? 'all' : this._filter}`
    );
    if (requestGen !== this._loadGen) return; // a newer request has since started -- discard this stale one
    if (!data || data.available === false) {
      listEl.innerHTML = `<div class="ifx-ebie-empty">${esc(data?.reason || 'Request failed.')}</div>`;
      return;
    }
    this._isUniverse = isUniverse;
    if (isUniverse) {
      this._verdicts = data.verdicts || [];
    } else {
      this._candidates = data.candidates || [];
    }
    this._render();
  }

  _render() {
    const listEl = this._el.querySelector('#ebieList');
    const rows = this._isUniverse ? this._verdicts : this._candidates;
    if (!rows.length) {
      listEl.innerHTML = `<div class="ifx-ebie-empty">${this._isUniverse ? 'No actionable lightweight verdicts right now (try Columns/include_no_trade for the full universe).' : 'No candidates in this window.'}</div>`;
      return;
    }
    listEl.innerHTML = rows.map((c, i) => this._isUniverse ? this._liteRowHtml(c, i) : this._rowHtml(c, i)).join('');
    listEl.querySelectorAll('[data-ebie-row]').forEach((row) => {
      row.addEventListener('click', () => {
        const idx = row.dataset.ebieRow;
        if (this._expanded.has(idx)) this._expanded.delete(idx); else this._expanded.add(idx);
        this._render();
      });
    });
  }

  _liteRowHtml(v, i) {
    // EBIE EB-15 Phase 3 -- universe-wide lightweight verdict row. A
    // deliberately different shape from _rowHtml() above: no bull/bear/
    // trap/portfolio scores exist for a symbol that never became a
    // candidate -- only what compute_lightweight_verdict() actually
    // computes (state, verdict, confidence band, reasons, invalidation,
    // DQ).
    const tone = LIGHTWEIGHT_VERDICT_TONE[v.verdict] || 'flat';
    const dirTone = v.direction === 'BULLISH' ? 'good' : v.direction === 'BEARISH' ? 'bad' : 'flat';
    const confTone = CONFIDENCE_TONE[v.confidence_band] || 'muted';
    const dqTone = DQ_TONE[v.data_quality_status] || 'muted';
    const expanded = this._expanded.has(String(i));

    const header = `
      <div class="ifx-ebie-row-lite${expanded ? ' expanded' : ''}" data-ebie-row="${i}">
        <span class="ifx-ebie-symbol">${esc(v.symbol)}</span>
        <span class="ifx-ebie-metric flat">${esc(v.sector_id || '—')}</span>
        <span class="ifx-ebie-dir ${dirTone}">${esc(v.direction || '—')}</span>
        <span class="ifx-ebie-metric flat">${esc((v.ebie_state || '—').replace(/_/g, ' '))}</span>
        <span class="ifx-ebie-verdict ${tone}">${esc((v.verdict || '—').replace(/_/g, ' '))}</span>
        <span class="ifx-ebie-metric ${confTone}">${esc(v.confidence_band || '—')}</span>
        <span class="ifx-ebie-metric ${dqTone}" title="Data quality score: ${v.data_quality_score == null ? 'unavailable' : v.data_quality_score}">${DQ_LABEL[v.data_quality_status] || v.data_quality_status || 'DQ —'}</span>
        <span class="ifx-ebie-chevron">${expanded ? '▾' : '▸'}</span>
      </div>`;

    if (!expanded) return header;
    return header + `
      <div class="ifx-ebie-detail">
        <div class="ifx-ebie-detail-cols">
          <div class="ifx-ebie-detail-block">
            <h5>Reasons</h5>
            ${v.reasons && v.reasons.length ? `<ul>${v.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : '<p class="ifx-ebie-none">None</p>'}
          </div>
          <div class="ifx-ebie-detail-block">
            <h5>Invalidation</h5>
            <p>${esc(v.invalidation_reason || '—')}</p>
          </div>
        </div>
        <p class="ifx-ebie-none">Lightweight, universe-wide verdict (Phase 3) -- computed for every symbol every sweep, before any strategy candidate exists. No bull/bear evidence-family breakdown, trap risk, or portfolio fit yet -- those are the FULL verdict's job (EB-8), which only runs once this setup is promoted to a real candidate.</p>
      </div>`;
  }

  _rowHtml(c, i) {
    const tone = VERDICT_TONE[c.verdict] || 'flat';
    const dirTone = c.direction === 'bullish' ? 'good' : c.direction === 'bearish' ? 'bad' : 'flat';
    const expanded = this._expanded.has(String(i));
    const trapTone = c.trap_risk_score == null ? 'muted' : c.trap_risk_score >= 50 ? 'risk' : 'flat';
    const pfTone = c.portfolio_fit_score == null ? 'muted' : c.portfolio_fit_score < 60 ? 'risk' : 'flat';
    const dq = c.data_quality || {};
    const dqStatus = dq.status || 'UNKNOWN';
    const dqTone = DQ_TONE[dqStatus] || 'muted';

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
        <span class="ifx-ebie-metric ${dqTone}" title="Data quality score: ${dq.score == null ? 'unavailable' : dq.score}">${DQ_LABEL[dqStatus] || dqStatus}</span>
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
    const dq = c.data_quality || {};

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
          <div class="ifx-ebie-detail-block">
            <h5>Data Quality</h5>
            ${kv('Status', dq.status || 'UNKNOWN')}
            ${kv('Score', dq.score)}
          </div>
        </div>
        ${listBlock('Data Quality Reasons', dq.reasons)}
        ${listBlock('Evidence Families Unavailable', dq.unavailable_evidence_families)}
        ${listBlock('Portfolio Correlation', (c.risk || {}).correlated_symbols)}
      </div>`;
  }

  destroy() {
    if (this._poll) clearInterval(this._poll);
  }
}
