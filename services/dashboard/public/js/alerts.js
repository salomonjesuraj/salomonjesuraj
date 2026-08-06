/**
 * Alert Delivery Timeline — readable Telegram delivery, block, and test outcomes.
 */
import { escapeHtml } from './utils.js';
import { api } from './api.js';

const FILTERS = [
  ['all', 'All'],
  ['delivered', 'Delivered'],
  ['blocked', 'Blocked'],
  ['failed', 'Failed'],
  ['test', 'Tests'],
];

function n(value, fallback = 0) {
  const out = Number(value);
  return Number.isFinite(out) ? out : fallback;
}

function price(value) {
  const out = n(value);
  return out > 0 ? `₹${out.toLocaleString('en-IN', { maximumFractionDigits: 2 })}` : '—';
}

function outcomeMeta(outcome) {
  const key = String(outcome || 'unknown').toLowerCase();
  if (key === 'delivered') return { cls: 'delivered', icon: '✓', label: 'Delivered to Telegram' };
  if (key === 'failed') return { cls: 'failed', icon: '×', label: 'Failed' };
  if (key === 'blocked') return { cls: 'blocked', icon: '⊘', label: 'Blocked by delivery gate' };
  return { cls: 'blocked', icon: '•', label: key.replace(/_/g, ' ') || 'Unknown' };
}

function cleanReason(reason) {
  const text = String(reason || '')
    .replace(/_/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/:\s*$/g, '')
    .trim();
  if (!text) return '';
  const map = {
    delivery_cooldown: 'Cooldown active',
    rate_limit_hourly: 'Hourly Telegram limit reached',
    burst_limit: 'Burst limit reached',
    duplicate_delivered: 'Already delivered',
    muted_symbol: 'Symbol muted',
    muted_strategy: 'Strategy muted',
    test_alert: 'Safe Telegram test',
  };
  return map[String(reason || '').toLowerCase()] || text.charAt(0).toUpperCase() + text.slice(1);
}

function formatTime(entry) {
  const raw = entry.timestamp || entry.ts || entry.time;
  if (!raw) return '';
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return String(raw);
  return d.toLocaleTimeString('en-IN', {
    hour12: false,
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatMicroTime(us) {
  const value = n(us);
  if (!value) return '';
  const d = new Date(value / 1000);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('en-IN', {
    hour12: false,
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function sideClass(side) {
  const s = String(side || '').toUpperCase();
  if (s.includes('CE')) return 'ce';
  if (s.includes('PE')) return 'pe';
  if (s.includes('WAIT')) return 'wait';
  return 'wait';
}

function scorePill(label, value, kind = '') {
  const v = Math.round(n(value));
  const tone = v >= 75 ? 'good' : v >= 60 ? 'warn' : 'weak';
  return `<span class="alert-score ${tone} ${kind}"><label>${escapeHtml(label)}</label><b>${v || '—'}</b></span>`;
}

export class AlertLog {
  constructor(containerEl) {
    this._el = containerEl;
    this._unsubs = [];
    this._entries = [];
    this._filter = 'all';
    this._status = '';
    this._stats = null;
    this._preview = null;
    this._suppressed = null;
    this._lastSyncAt = null;
    this._error = '';
    this._loading = true;
  }

  init() {
    if (!this._el) return;
    this._render();
    this._refreshLog();

    this._unsubs.push(api.subscribe('/api/alerts/log', (resp) => {
      if (resp) {
        this._entries = resp.log || [];
        this._lastSyncAt = Date.now();
        this._error = '';
        this._loading = false;
        this._render();
      }
    }, 5000));
    this._unsubs.push(api.subscribe('/api/alerts/stats', (resp) => {
      this._stats = resp;
      this._render();
    }, 10000));
    this._unsubs.push(api.subscribe('/api/alerts/test/preview', (resp) => {
      this._preview = resp?.preview || null;
      this._render();
    }, 30000));
    this._unsubs.push(api.subscribe('/api/signals/suppressed?limit=50', (resp) => {
      this._suppressed = resp || null;
      this._render();
    }, 10000));
    this._el.addEventListener('click', async (e) => {
      const filterBtn = e.target.closest('[data-alert-filter]');
      if (filterBtn) {
        this._filter = filterBtn.dataset.alertFilter || 'all';
        this._render();
        return;
      }

      const refreshBtn = e.target.closest('[data-refresh-alert-log]');
      if (refreshBtn) {
        refreshBtn.disabled = true;
        this._status = 'Refreshing alert timeline...';
        this._render();
        await this._refreshLog();
        refreshBtn.disabled = false;
        return;
      }

      const btn = e.target.closest('[data-send-test-alert]');
      if (!btn) return;
      btn.disabled = true;
      this._status = 'Queueing Telegram test alert...';
      this._render();
      const res = await api.post('/api/alerts/test', {});
      this._status = res?.ok ? 'Test alert queued. Check Telegram and timeline.' : `Test failed: ${res?.error || 'unknown error'}`;
      btn.disabled = false;
      this._render();
    });
  }

  async _refreshLog() {
    try {
      this._loading = true;
      this._error = '';
      const [logResp, statsResp, previewResp, suppressedResp] = await Promise.all([
        api.fetch('/api/alerts/log'),
        api.fetch('/api/alerts/stats'),
        api.fetch('/api/alerts/test/preview'),
        api.fetch('/api/signals/suppressed?limit=50'),
      ]);

      if (logResp) {
        this._entries = logResp.log || [];
        this._lastSyncAt = Date.now();
      } else {
        this._error = 'Alert API did not return log data';
      }
      if (statsResp) this._stats = statsResp;
      if (previewResp) this._preview = previewResp.preview || null;
      if (suppressedResp) this._suppressed = suppressedResp;
    } catch (err) {
      this._error = err?.message || 'Alert log refresh failed';
    } finally {
      this._loading = false;
      if (!this._status || /^Refreshing/i.test(this._status)) {
        this._status = this._error ? `Alert log problem: ${this._error}` : 'Alert timeline synced.';
      }
      this._render();
    }
  }

  _filteredEntries() {
    const entries = this._entries || [];
    if (this._filter === 'all') return entries;
    if (this._filter === 'test') return entries.filter(e => String(e.reason || '').toLowerCase() === 'test_alert' || String(e.strategy_id || '').includes('test'));
    return entries.filter(e => String(e.outcome || e.status || '').toLowerCase() === this._filter);
  }

  _summary() {
    const entries = this._entries || [];
    const count = key => entries.filter(e => String(e.outcome || e.status || '').toLowerCase() === key).length;
    return {
      total: entries.length,
      delivered: count('delivered'),
      blocked: count('blocked'),
      failed: count('failed'),
      tests: entries.filter(e => String(e.reason || '').toLowerCase() === 'test_alert' || String(e.strategy_id || '').includes('test')).length,
    };
  }

  _render() {
    const entries = this._filteredEntries();
    const p = this._preview || {};
    const s = this._stats || {};
    const summary = this._summary();
    const synced = this._lastSyncAt
      ? new Date(this._lastSyncAt).toLocaleTimeString('en-IN', {
        hour12: false,
        timeZone: 'Asia/Kolkata',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
      : 'not yet';
    const stateClass = this._error ? 'failed' : this._loading ? 'blocked' : 'delivered';

    const filters = FILTERS.map(([key, label]) => {
      const count = key === 'all' ? summary.total : key === 'test' ? summary.tests : summary[key] || 0;
      return `<button class="alert-filter ${this._filter === key ? 'active' : ''}" data-alert-filter="${key}">
        ${escapeHtml(label)} <b>${count}</b>
      </button>`;
    }).join('');

    const preview = p.symbol ? `<div class="alert-test-preview">
      <span><b>${escapeHtml(p.symbol)}</b> ${escapeHtml(p.side || '')}</span>
      <span>Entry ${escapeHtml(String(p.entry ?? '-'))}</span>
      <span>SL ${escapeHtml(String(p.sl ?? '-'))}</span>
      <span>T1/T2 ${escapeHtml(String(p.t1 ?? '-'))} / ${escapeHtml(String(p.t2 ?? '-'))}</span>
      <span>${escapeHtml(p.safety || 'Test only')}</span>
    </div>` : '';

    const stats = `<div class="alert-rate-strip">
      <span>Hourly sent <b>${Number(s.hourly_sent || 0)}</b></span>
      <span>Burst sent <b>${Number(s.burst_5min_sent || 0)}</b></span>
      <span>Backend log <b>${Number(s.delivery_log_entries || this._entries.length || 0)}</b></span>
      <span class="${stateClass}">Sync <b>${escapeHtml(synced)}</b></span>
      ${this._error ? `<span class="failed">Error ${escapeHtml(this._error)}</span>` : ''}
    </div>`;

    const toolbar = `<div class="alert-console">
      <div class="alert-console-top">
        <div>
          <h3>Telegram Signal Timeline</h3>
          <p>Every signal should show whether it was delivered, blocked, failed, or test-only — with the exact reason.</p>
        </div>
        <div class="alert-actions">
          <button class="scanner-action-btn" data-refresh-alert-log>Refresh</button>
          <button class="scanner-action-btn primary" data-send-test-alert>Send Telegram Test</button>
        </div>
      </div>
      <div class="alert-filters">${filters}</div>
      ${preview}
      ${stats}
      <div class="alert-status">${escapeHtml(this._status || 'Safe test only; no order/trade is created.')}</div>
    </div>`;

    if (!entries.length) {
      const msg = this._loading
        ? 'Loading alert delivery timeline...'
        : this._error
          ? 'Alert delivery timeline could not be loaded'
          : 'No entries for this filter';
      this._el.innerHTML = `${toolbar}<div class="panel-empty">${escapeHtml(msg)}</div>${this._renderSuppressedPanel()}`;
      return;
    }

    this._el.innerHTML = toolbar + `<div class="alert-timeline">
      ${entries.slice(0, 50).map(e => this._renderEntry(e)).join('')}
    </div>${this._renderSuppressedPanel()}`;
  }

  _renderEntry(e) {
    const time = formatTime(e);
    const sym = e.symbol || '';
    const grade = e.grade || '';
    const outcome = outcomeMeta(e.outcome || e.status);
    const reason = cleanReason(e.reason);
    const stable = e.stable_bias || e.option_bias || '—';
    const cls = sideClass(stable);
    const state = e.direction_state || '';
    const directionReason = e.direction_reason || reason || outcome.label;

    return `<article class="alert-timeline-card ${outcome.cls} ${cls}">
      <div class="alert-line-dot"></div>
      <div class="alert-card-head">
        <div>
          <span class="alert-time">${escapeHtml(time)}</span>
          <b>${escapeHtml(sym)}</b>
          <span class="grade-badge ${grade === 'A+' ? 'a-plus' : grade === 'A' ? 'a' : 'b-plus'}">${escapeHtml(grade || '—')}</span>
        </div>
        <span class="alert-outcome ${outcome.cls}">${outcome.icon} ${escapeHtml(outcome.label)}</span>
      </div>
      <div class="alert-card-command">
        <span class="side-chip ${cls}">${escapeHtml(stable)}</span>
        <strong>${escapeHtml(state || reason || 'Signal processed')}</strong>
        <small>${escapeHtml(directionReason)}</small>
      </div>
      <div class="alert-card-grid">
        <span><label>CE above</label><b class="positive">${price(e.ce_above)}</b></span>
        <span><label>PE below</label><b class="negative">${price(e.pe_below)}</b></span>
        <span><label>Entry</label><b>${price(e.entry)}</b></span>
        <span><label>SL</label><b class="negative">${price(e.stop)}</b></span>
        <span><label>T1</label><b class="positive">${price(e.target)}</b></span>
        <span><label>R:R</label><b>${n(e.rr).toFixed(1)}:1</b></span>
      </div>
      <div class="alert-card-scores">
        ${scorePill('Score', e.score)}
        ${scorePill('CE', e.ce_score, 'ce')}
        ${scorePill('PE', e.pe_score, 'pe')}
        <span class="alert-strategy">${escapeHtml(e.strategy_id || 'strategy')}</span>
      </div>
      ${e.mtf_text ? `<div class="alert-mtf">${escapeHtml(e.mtf_text)}</div>` : ''}
    </article>`;
  }

  _renderSuppressedPanel() {
    const data = this._suppressed || {};
    const rows = Array.isArray(data.suppressed) ? data.suppressed : [];
    const reasons = data.reason_counts || {};
    const mode = data.mode || 'precision';
    const profile = data.profile || {};
    const topReasons = Object.entries(reasons)
      .sort((a, b) => Number(b[1]) - Number(a[1]))
      .slice(0, 6);

    const reasonHtml = topReasons.length
      ? topReasons.map(([reason, count]) => `<span>${escapeHtml(cleanReason(reason))} <b>${Number(count || 0)}</b></span>`).join('')
      : '<span>No suppressed candidate data yet</span>';

    const cards = rows.slice(0, 12).map(r => {
      const side = r.side || 'WAIT';
      const cls = sideClass(side);
      const why = cleanReason(r.reason) || 'Suppressed';
      const action = String(r.action || 'AVOID').replace(/_/g, ' ');
      const tone = r.tone || 'block';
      const actionWhy = r.why || why;
      const notes = [
        ...(Array.isArray(r.anti_chase_reasons) ? r.anti_chase_reasons : []),
        ...(Array.isArray(r.rejection_reasons) ? r.rejection_reasons : []),
        ...(Array.isArray(r.explanation) ? r.explanation.slice(0, 2) : []),
      ].filter(Boolean).slice(0, 3);

      return `<article class="suppressed-card ${cls}">
        <div class="suppressed-head">
          <span>${escapeHtml(formatMicroTime(r.created_at_us))}</span>
          <b>${escapeHtml(r.symbol || '?')}</b>
          <em class="${escapeHtml(tone)}">${escapeHtml(action)}</em>
        </div>
        <div class="suppressed-command">
          <span class="side-chip ${cls}">${escapeHtml(side)}</span>
          ${scorePill('Score', r.score)}
          ${scorePill('CE', r.bull_confidence, 'ce')}
          ${scorePill('PE', r.bear_confidence, 'pe')}
        </div>
        <div class="suppressed-levels">
          <span><label>CE above</label><b class="positive">${price(r.positive_above)}</b></span>
          <span><label>PE below</label><b class="negative">${price(r.negative_below)}</b></span>
          <span><label>R:R</label><b>${n(r.rr).toFixed(1)}:1</b></span>
          <span><label>Sector</label><b>${escapeHtml(r.sector || '?')} ${Math.round(n(r.sector_strength))}</b></span>
        </div>
        <p><b>${escapeHtml(actionWhy)}</b>${notes.length ? ` ? ${notes.map(escapeHtml).join(' ? ')}` : ''}</p>
      </article>`;
    }).join('');

    return `<section class="suppressed-panel">
      <div class="suppressed-title">
        <div>
          <h3>Suppressed Scanner Candidates</h3>
          <p>These are setups the scanner saw but did not allow as Telegram/live signals. This is the honest ?why no trade?? board.</p>
        </div>
        <span>${escapeHtml(profile.label || mode)} ? ${Number(data.count || rows.length || 0)} recent</span>
      </div>
      ${profile.note ? `<div class="suppressed-mode-note">${escapeHtml(profile.note)}</div>` : ''}
      <div class="suppressed-reasons">${reasonHtml}</div>
      ${cards ? `<div class="suppressed-grid">${cards}</div>` : '<div class="panel-empty">Waiting for suppressed scanner candidates...</div>'}
    </section>`;
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
  }
}
