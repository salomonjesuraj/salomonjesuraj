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

function ageLabelSec(sec) {
  if (sec == null) return null;
  const min = Math.floor(sec / 60);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

// EBIE-KNOWN-GAPS.md §1.7 -- ebie_candidates.py's new cache_freshness
// field distinguishes "never cached" from "cached but stale" for the
// three rolling-subset-queue-backed evidence sources (relative_strength/
// mtf, options_positioning/options-dynamics, option_tradeability/
// option-chain). This turns one freshness entry into a short, honest
// suffix -- never claims a family IS available, only explains WHY it
// currently isn't.
function freshnessSuffix(freshness) {
  if (!freshness) return '';
  if (freshness.status === 'never_cached') return ' — never cached for this symbol';
  if (freshness.status === 'stale') return ` — last checked ${ageLabelSec(freshness.age_sec)}, cache since expired`;
  return '';
}

export class EbieVerdictPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._filter = 'all';
    // EBIE EB-15 Phase 7 -- long/short candidate separation, the one
    // required P7 dashboard section that was entirely missing: every
    // other section (Why Now, Why Not, DQ, ...) already existed, this
    // was a real gap. Orthogonal to `_filter` (fired/rejected/universe)
    // so it's a second, independent pill row, not folded into the first.
    this._direction = 'all';
    this._expanded = new Set();
    this._candidates = [];
    this._verdicts = [];
    this._isUniverse = false;
    this._loadGen = 0;
    // Event-timeline (Phase 7) -- lazy per-symbol cache. `/api/ebie/
    // transitions/recent?symbol=X` is real, already-archived EB-1 data
    // (never fabricated) but fetching it for every row up front would be
    // an N+1 storm against a 30-100 row list; fetched only when a row is
    // actually expanded, once per symbol per panel lifetime.
    this._timelines = new Map(); // symbol -> transitions[] | 'loading' | 'error'
  }

  init() {
    if (!this._el) return;
    this._el.innerHTML = `
      <div class="ifx-ebie-toolbar">
        <div class="ifx-ebie-pills" id="ebieFilterPills"></div>
        <div class="ifx-ebie-pills" id="ebieDirPills"></div>
        <button type="button" class="ifx-btn" id="ebieRefreshBtn">Refresh</button>
      </div>
      <div class="ifx-ebie-monitor" id="ebieMonitor"></div>
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

    const dirPills = [
      { key: 'all', label: 'Long + Short' },
      { key: 'long', label: 'Long' },
      { key: 'short', label: 'Short' },
    ];
    const dirPillsEl = this._el.querySelector('#ebieDirPills');
    dirPillsEl.innerHTML = dirPills.map((p) =>
      `<button type="button" class="ifx-btn ifx-ebie-pill${p.key === this._direction ? ' on' : ''}" data-dir="${p.key}">${p.label}</button>`
    ).join('');
    dirPillsEl.querySelectorAll('[data-dir]').forEach((btn) => {
      btn.addEventListener('click', () => {
        this._direction = btn.dataset.dir;
        this._expanded.clear();
        dirPillsEl.querySelectorAll('.ifx-ebie-pill').forEach((b) => b.classList.toggle('on', b === btn));
        this._render();
      });
    });

    this._el.querySelector('#ebieRefreshBtn').addEventListener('click', () => { this._load(); this._loadMonitor(); });

    this._load();
    this._loadMonitor();
    this._poll = setInterval(() => this._load(), 30000);
    // Drift/disagreement monitoring (Phase 7 Implementation Sequence item
    // 3) changes slowly (a 24h window) -- polled far less often than the
    // candidate list itself.
    this._monitorPoll = setInterval(() => this._loadMonitor(), 5 * 60000);
  }

  // EBIE EB-15 Phase 7 -- "add monitoring for drift, disagreement, and
  // missing evidence" (Implementation Sequence item 3). Reuses EB-1's own
  // already-built /api/ebie/comparison as-is; no new metric is invented
  // here (an "agreement %" would require a state<->legacy_tier mapping
  // that doesn't exist yet and would be a guess) -- shows the real
  // cross-tab counts so a trader can see where the two systems line up
  // or diverge, honestly, without a prejudged verdict on which is right.
  async _loadMonitor() {
    const monitorEl = this._el.querySelector('#ebieMonitor');
    if (!monitorEl) return;
    const data = await api.fetch('/api/ebie/comparison?hours=24');
    if (!data || data.available === false) {
      monitorEl.innerHTML = `<span class="ifx-ebie-none">Drift/disagreement monitoring unavailable${data?.reason ? ': ' + esc(data.reason) : ''}.</span>`;
      return;
    }
    const top = (data.state_distribution || []).slice(0, 4)
      .map((s) => `${esc((s.state || '—').replace(/_/g, ' '))} ${s.count}`).join(' · ');
    monitorEl.innerHTML = `
      <span class="ifx-ebie-monitor-label">EBIE vs legacy (last ${data.window_hours}h, ${data.total_transitions} transitions):</span>
      <span class="ifx-ebie-monitor-body">${top || 'no transitions in this window'}</span>
      <button type="button" class="ifx-btn ifx-ebie-monitor-toggle" id="ebieMonitorToggle">Cross-tab</button>
      <div class="ifx-ebie-monitor-crosstab" id="ebieMonitorCrosstab" hidden></div>
    `;
    const crosstabEl = monitorEl.querySelector('#ebieMonitorCrosstab');
    crosstabEl.innerHTML = (data.state_vs_legacy_tier || []).length
      ? `<table><thead><tr><th>EBIE state</th><th>Legacy tier</th><th>Count</th></tr></thead><tbody>${
          data.state_vs_legacy_tier.map((r) => `<tr><td>${esc((r.state || '—').replace(/_/g, ' '))}</td><td>${esc(r.legacy_tier || '—')}</td><td>${r.count}</td></tr>`).join('')
        }</tbody></table>`
      : '<p class="ifx-ebie-none">No transitions in this window.</p>';
    monitorEl.querySelector('#ebieMonitorToggle').addEventListener('click', () => {
      crosstabEl.hidden = !crosstabEl.hidden;
    });
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
    const allRows = this._isUniverse ? this._verdicts : this._candidates;
    // EBIE EB-15 Phase 7 -- long/short separation. Full candidates use
    // lowercase 'bullish'/'bearish' (ebie_candidates.py's own
    // signal_type passthrough); lightweight verdicts use uppercase
    // 'BULLISH'/'BEARISH' (compute_lightweight_verdict()) -- normalized
    // to one check rather than duplicating the filter per data source.
    const rows = this._direction === 'all' ? allRows : allRows.filter((r) => {
      const dir = String(r.direction || '').toLowerCase();
      return this._direction === 'long' ? dir === 'bullish' : dir === 'bearish';
    });
    if (!rows.length) {
      const emptyMsg = this._isUniverse ? 'No actionable lightweight verdicts right now (try Columns/include_no_trade for the full universe).' : 'No candidates in this window.';
      listEl.innerHTML = `<div class="ifx-ebie-empty">${allRows.length ? 'No candidates in this direction filter.' : emptyMsg}</div>`;
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

  // EBIE EB-15 Phase 7 -- "Event timeline per symbol", the one required
  // P7 section with no existing surface anywhere. Built entirely from
  // EB-1's already-archived, real ebie_state_transitions table (GET
  // /api/ebie/transitions/recent?symbol=X) -- no new backend computation,
  // just a chronological view over data that already exists. Lazy: only
  // fetched the first time a given symbol's row is expanded.
  _timelineHtml(symbol) {
    const cached = this._timelines.get(symbol);
    if (cached === undefined) {
      this._timelines.set(symbol, 'loading');
      this._fetchTimeline(symbol);
      return '<p class="ifx-ebie-none">Loading timeline…</p>';
    }
    if (cached === 'loading') return '<p class="ifx-ebie-none">Loading timeline…</p>';
    if (cached === 'error') return '<p class="ifx-ebie-none">Timeline unavailable.</p>';
    if (!cached.length) return '<p class="ifx-ebie-none">No recorded state transitions yet for this symbol.</p>';
    return `<ul class="ifx-ebie-timeline">${cached.map((t) => `
      <li>
        <span class="ifx-ebie-timeline-age">${ageLabel(t.transitioned_at)}</span>
        <span class="ifx-ebie-timeline-transition">${esc((t.prev_state || '—').replace(/_/g, ' '))} → ${esc((t.state || '—').replace(/_/g, ' '))}</span>
        <span class="ifx-ebie-timeline-reason">${esc(t.reason || '—')}</span>
      </li>`).join('')}</ul>`;
  }

  async _fetchTimeline(symbol) {
    const data = await api.fetch(`/api/ebie/transitions/recent?symbol=${encodeURIComponent(symbol)}&limit=8`);
    this._timelines.set(symbol, (data && data.available !== false) ? (data.transitions || []) : 'error');
    this._render();
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
        <div class="ifx-ebie-detail-block">
          <h5>Event Timeline</h5>
          ${this._timelineHtml(v.symbol)}
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
    // EBIE-KNOWN-GAPS.md §7.1 -- inline warning when this candidate's own
    // direction disagrees with the SAME symbol's current universe-wide
    // lightweight verdict (a genuinely different computation, EB-15 Phase
    // 3). No new grid column (avoids the exact "forgot to update
    // grid-template-columns" bug already caught once in this file) --
    // just a marker on the existing direction pill, with the real other
    // system's direction in the tooltip, not a fabricated score.
    const liteDisagree = c.direction_agreement === 'disagree';
    const liteDir = c.lightweight_verdict?.direction;

    const header = `
      <div class="ifx-ebie-row${expanded ? ' expanded' : ''}" data-ebie-row="${i}">
        <span class="ifx-ebie-symbol">${esc(c.symbol)}</span>
        <span class="ifx-ebie-dir ${dirTone}"${liteDisagree ? ` title="Universe-wide lightweight verdict currently reads ${esc(liteDir)} -- disagrees with this candidate's own direction"` : ''}>${esc(c.direction || '—')}${liteDisagree ? ' ⚠' : ''}</span>
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
    const mc = c.market_context || null;
    const opt = c.option_chain || null;
    const optTone = { TRADE_READY: 'good', WAIT_CONTRACT: 'warn', CHAIN_PENDING: 'flat', AVOID_CONTRACT: 'bad' }[opt?.execution_status] || 'muted';
    // EBIE-KNOWN-GAPS.md §1.7 -- per-family freshness (fresh/stale/
    // never_cached), only meaningful for the three rolling-subset-cache
    // families ebie_candidates.py actually computes it for; every other
    // key is simply absent from cacheFreshness, and freshnessSuffix()
    // returns '' for a missing entry, so this never fabricates a status
    // for a family it wasn't computed for.
    const unavailableFamilies = dq.unavailable_evidence_families || [];
    const cacheFreshness = dq.cache_freshness || {};
    const optFreshness = cacheFreshness.option_tradeability;
    const optTradeabilityEmptyMsg = optFreshness?.status === 'never_cached'
      ? 'Never checked for this symbol.'
      : optFreshness?.status === 'stale'
      ? `Not currently cached — last checked ${ageLabelSec(optFreshness.age_sec)}.`
      : 'Not cached for this symbol yet.';
    // EBIE-KNOWN-GAPS.md §7.1 -- the same symbol's current universe-wide
    // lightweight verdict (EB-15 Phase 3), a genuinely independent
    // computation from this full verdict (see the module-level comment
    // above _rowHtml()). Shown as-is, not merged into this candidate's
    // own numbers.
    const lite = c.lightweight_verdict || null;
    const AGREEMENT_LABEL = { agree: 'Agrees', disagree: 'Disagrees', unknown: '—' };
    const AGREEMENT_TONE = { agree: 'good', disagree: 'bad', unknown: 'muted' };

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
          <div class="ifx-ebie-detail-block">
            <h5>Market &amp; Sector Context</h5>
            ${mc ? `
              ${kv('NIFTY 50', mc.nifty_change_pct != null ? Number(mc.nifty_change_pct).toFixed(2) + '%' : null)}
              ${kv('Sector Avg', mc.sector_avg_change_pct != null ? Number(mc.sector_avg_change_pct).toFixed(2) + '%' : null)}
              ${kv('Market Health', mc.market_health_score != null ? Number(mc.market_health_score).toFixed(0) : null)}
            ` : '<p class="ifx-ebie-none">Not cached for this symbol yet.</p>'}
          </div>
          <div class="ifx-ebie-detail-block">
            <h5>Option Tradeability</h5>
            ${opt ? `
              <p><span class="ifx-ebie-verdict ${optTone}">${esc((opt.execution_status || '—').replace(/_/g, ' '))}</span></p>
              ${kv('Grade', opt.quality_grade)}
              ${kv('Score', opt.option_score != null ? Number(opt.option_score).toFixed(0) : null)}
              ${kv('Strike/Expiry', opt.strike != null ? `${opt.strike} / ${opt.expiry || '—'}` : null)}
            ` : `<p class="ifx-ebie-none">${esc(optTradeabilityEmptyMsg)}</p>`}
          </div>
          <div class="ifx-ebie-detail-block">
            <h5>Universe Check (Lightweight)</h5>
            ${lite ? `
              <p><span class="ifx-ebie-verdict ${AGREEMENT_TONE[c.direction_agreement] || 'muted'}">${esc(AGREEMENT_LABEL[c.direction_agreement] || '—')}</span></p>
              ${kv('Verdict', (lite.verdict || '—').replace(/_/g, ' '))}
              ${kv('Direction', lite.direction)}
              ${kv('EBIE State', (lite.ebie_state || '—').replace(/_/g, ' '))}
              ${kv('Confidence', lite.confidence_band)}
            ` : '<p class="ifx-ebie-none">No universe-wide verdict cached for this symbol right now.</p>'}
          </div>
        </div>
        ${listBlock('Data Quality Reasons', dq.reasons)}
        <div class="ifx-ebie-detail-block">
          <h5>Evidence Families Unavailable</h5>
          ${unavailableFamilies.length ? `<ul>${unavailableFamilies.map((f) =>
            `<li>${esc(f)}${esc(freshnessSuffix(cacheFreshness[f]))}</li>`
          ).join('')}</ul>` : '<p class="ifx-ebie-none">None</p>'}
        </div>
        ${opt ? listBlock('Option Tradeability Blockers', [...(opt.hard_blockers || []), ...(opt.blockers || [])]) : ''}
        ${listBlock('Portfolio Correlation', (c.risk || {}).correlated_symbols)}
        <div class="ifx-ebie-detail-block">
          <h5>Event Timeline</h5>
          ${this._timelineHtml(c.symbol)}
        </div>
      </div>`;
  }

  destroy() {
    if (this._poll) clearInterval(this._poll);
    if (this._monitorPoll) clearInterval(this._monitorPoll);
  }
}
