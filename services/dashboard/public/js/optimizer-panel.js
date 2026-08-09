/**
 * Optimizer — walk-forward status, live-vs-recommended drift proposal,
 * and on-demand feature-ablation evidence lookup.
 * Phase D of the UI overhaul: surfaces Phase 11's endpoints, never had a
 * UI until now. Every number here is evidence for human review — nothing
 * on this panel changes live scanner behavior. See backtest.py's
 * compute_walkforward/compute_optimizer_proposal/compute_feature_ablation
 * for the "propose only, never auto-apply" governance this mirrors.
 */
import { api } from './api.js';
import { escapeHtml } from './utils.js';

// Mirrors backtest.py's KNOWN_ABLATION_FIELDS / KNOWN_ABLATION_FIELDS_SUB_SCORES
// -- keep in sync if that list changes. {label, field, column}.
const ABLATION_FIELDS = [
  { label: 'Fibonacci targets', field: 'fib_targets', column: 'features_snapshot' },
  { label: 'MA regime', field: 'ma_regime', column: 'features_snapshot' },
  { label: 'MA regime cross recent', field: 'ma_regime_cross_recent', column: 'features_snapshot' },
  { label: 'Chart patterns', field: 'chart_patterns', column: 'features_snapshot' },
  { label: 'FVG bullish (CE)', field: 'fvg_bullish_ce', column: 'features_snapshot' },
  { label: 'FVG bearish (CE)', field: 'fvg_bearish_ce', column: 'features_snapshot' },
  { label: 'Last liquidity sweep', field: 'last_liquidity_sweep', column: 'features_snapshot' },
  { label: 'Order block (bullish)', field: 'order_block_bullish_validated', column: 'features_snapshot' },
  { label: 'Order block (bearish)', field: 'order_block_bearish_validated', column: 'features_snapshot' },
  { label: 'Donchian fresh high', field: 'donchian_fresh_high_breakout', column: 'features_snapshot' },
  { label: 'Donchian fresh low', field: 'donchian_fresh_low_breakout', column: 'features_snapshot' },
  { label: 'Wyckoff structural failure', field: 'wyckoff_structural_failure', column: 'features_snapshot' },
  { label: 'Wyckoff SOT', field: 'wyckoff_sot', column: 'features_snapshot' },
  { label: 'Wyckoff SOS/SOW', field: 'wyckoff_sos_sow', column: 'features_snapshot' },
  { label: 'Volman entry triggered', field: 'volman_entry_triggered', column: 'features_snapshot' },
  { label: 'Cross-index confirmation', field: 'cross_confirmation', column: 'sub_scores' },
];

// Real status vocabulary (backtest.py's _walkforward_status + compute_optimizer_proposal):
// walk-forward -> FORWARD_TARGET_MET / FORWARD_MIXED / FORWARD_FAILED / FORWARD_BUILDING /
//   NO_FORWARD_TRADES / LOW_SAMPLE / NO_PROFILE. proposal -> PROPOSED / NO_DRIFT / NO_PROPOSAL.
function statusTone(status) {
  const s = String(status || '').toUpperCase();
  if (s.includes('TARGET_MET') || s === 'NO_DRIFT') return 'ifx-badge--bull';
  if (s.includes('FAILED') || s === 'PROPOSED' || s.includes('MIXED')) return 'ifx-badge--warn';
  return 'ifx-badge--neutral';
}

export class OptimizerPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._unsubs = [];
    this._ablationBusy = false;
  }

  init() {
    if (!this._el) return;
    this._el.classList.add('ifx-opt');
    this._render();

    this._unsubs.push(api.subscribe('/api/backtest/walkforward?days=120&target=80', (resp) => {
      this._renderWalkforward(resp);
    }, 60000));

    this._unsubs.push(api.subscribe('/api/backtest/optimizer-proposal/latest', (resp) => {
      this._renderProposal(resp);
    }, 60000));
  }

  _render() {
    this._el.innerHTML = `
      <div class="ifx-opt-section">
        <div class="ifx-opt-section-title">Walk-Forward Status</div>
        <div id="optWalkforward" class="ifx-opt-body">Loading…</div>
      </div>
      <div class="ifx-opt-section">
        <div class="ifx-opt-section-title">Optimizer Proposal <span class="ifx-tone-faint">(live vs. recommended config)</span></div>
        <div id="optProposal" class="ifx-opt-body">Loading…</div>
      </div>
      <div class="ifx-opt-section">
        <div class="ifx-opt-section-title">Feature-Ablation Evidence <span class="ifx-tone-faint">(does this informational field actually predict outcomes?)</span></div>
        <div class="ifx-opt-ablation-controls">
          <select class="ifx-oa-select" id="optAblationField">
            ${ABLATION_FIELDS.map(f => `<option value="${f.field}|${f.column}">${escapeHtml(f.label)}</option>`).join('')}
          </select>
          <button type="button" class="ifx-btn ifx-btn--on-paper" id="optAblationRun">Check evidence</button>
        </div>
        <div id="optAblation" class="ifx-opt-body">Pick a field and click "Check evidence" — nothing runs automatically (this queries live archived-outcome data on demand).</div>
      </div>
    `;

    this._el.querySelector('#optAblationRun')?.addEventListener('click', () => this._runAblation());
  }

  _renderWalkforward(resp) {
    const el = this._el.querySelector('#optWalkforward');
    if (!el) return;
    if (!resp || !resp.available) {
      el.innerHTML = `<div class="ifx-opt-unavailable">${escapeHtml(resp?.reason || 'Unavailable')}</div>`;
      return;
    }
    const rec = resp.recommended || {};
    const test = rec.test || {};
    el.innerHTML = `
      <div class="ifx-opt-stat-row">
        <span class="ifx-badge ${statusTone(resp.status)}">${escapeHtml(resp.status || 'UNKNOWN')}</span>
        <span class="ifx-tone-faint">${resp.total_decided || 0} decided signals over ${resp.days || 0} days</span>
      </div>
      ${rec.label ? `
      <div class="ifx-opt-metric-grid">
        <div class="ifx-opt-metric"><label>Recommended profile</label><b>${escapeHtml(rec.label)}</b></div>
        <div class="ifx-opt-metric"><label>Out-of-sample precision</label><b class="ifx-mono">${test.precision_pct != null ? test.precision_pct + '%' : '—'}</b></div>
        <div class="ifx-opt-metric"><label>Test sample</label><b class="ifx-mono">${test.decided || 0} decided</b></div>
      </div>` : ''}
      <p class="ifx-opt-note">${escapeHtml(resp.note || '')}</p>
    `;
  }

  _renderProposal(resp) {
    const el = this._el.querySelector('#optProposal');
    if (!el) return;
    if (!resp || !resp.available) {
      el.innerHTML = `<div class="ifx-opt-unavailable">${escapeHtml(resp?.reason || 'No proposal computed yet.')}</div>`;
      return;
    }
    el.innerHTML = `
      <div class="ifx-opt-stat-row">
        <span class="ifx-badge ${statusTone(resp.status)}">${escapeHtml(resp.status || 'UNKNOWN')}</span>
        ${resp.live_config ? `<span class="ifx-tone-faint">Live: score≥${resp.live_config.precision_guard_min_score} · R:R≥${resp.live_config.precision_guard_min_rr} · ${escapeHtml(resp.live_config.precision_guard_sessions || '')}</span>` : ''}
      </div>
      <p class="ifx-opt-note">${escapeHtml(resp.note || resp.reason || '')}</p>
      ${resp.status === 'PROPOSED' ? '<p class="ifx-opt-warn">This is a proposal for human review only — nothing was changed automatically. Review before touching scanner config.</p>' : ''}
    `;
  }

  async _runAblation() {
    if (this._ablationBusy) return;
    const select = this._el.querySelector('#optAblationField');
    const out = this._el.querySelector('#optAblation');
    const btn = this._el.querySelector('#optAblationRun');
    if (!select || !out) return;
    const [field, column] = select.value.split('|');
    this._ablationBusy = true;
    if (btn) btn.disabled = true;
    out.innerHTML = `Checking "${escapeHtml(field)}"…`;
    try {
      const resp = await api.fetch(`/api/backtest/feature-ablation?field=${encodeURIComponent(field)}&column=${encodeURIComponent(column)}&days=90`);
      if (!resp || !resp.available) {
        out.innerHTML = `<div class="ifx-opt-unavailable">${escapeHtml(resp?.reason || 'Unavailable')}</div>`;
      } else {
        const present = resp.present || {};
        const absent = resp.absent || {};
        out.innerHTML = `
          <div class="ifx-opt-metric-grid">
            <div class="ifx-opt-metric"><label>Present</label><b class="ifx-mono">${present.precision_pct != null ? present.precision_pct + '%' : '—'}</b><small>${present.decided || 0} decided</small></div>
            <div class="ifx-opt-metric"><label>Absent</label><b class="ifx-mono">${absent.precision_pct != null ? absent.precision_pct + '%' : '—'}</b><small>${absent.decided || 0} decided</small></div>
            <div class="ifx-opt-metric"><label>Lift</label><b class="ifx-mono ${resp.precision_lift_pct > 0 ? 'ifx-tone-good' : resp.precision_lift_pct < 0 ? 'ifx-tone-bad' : ''}">${resp.precision_lift_pct != null ? resp.precision_lift_pct + ' pts' : '—'}</b></div>
          </div>
          <p class="ifx-opt-note">${escapeHtml(resp.note || '')}</p>
        `;
      }
    } catch (err) {
      out.innerHTML = `<div class="ifx-opt-unavailable">Request failed: ${escapeHtml(String(err))}</div>`;
    }
    this._ablationBusy = false;
    if (btn) btn.disabled = false;
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
  }
}
