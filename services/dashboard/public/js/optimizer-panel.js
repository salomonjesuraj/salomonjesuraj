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
    this._icBusy = false;
    this._render();

    this._unsubs.push(api.subscribe('/api/backtest/walkforward?days=120&target=80', (resp) => {
      this._renderWalkforward(resp);
    }, 60000));

    this._unsubs.push(api.subscribe('/api/backtest/optimizer-proposal/latest', (resp) => {
      this._renderProposal(resp);
    }, 60000));

    this._unsubs.push(api.subscribe('/api/backtest/kelly-sizing?days=180', (resp) => {
      this._renderKelly(resp);
    }, 120000));

    this._unsubs.push(api.subscribe('/api/backtest/ml-classifier/latest', (resp) => {
      this._renderMlClassifier(resp);
    }, 120000));
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
        <div class="ifx-opt-section-title">Position Sizing — Half-Kelly <span class="ifx-tone-faint">(from real archived win/loss outcomes, informational only)</span></div>
        <div id="optKelly" class="ifx-opt-body">Loading…</div>
      </div>
      <div class="ifx-opt-section">
        <div class="ifx-opt-section-title">ML Classifier <span class="ifx-tone-faint">(trained on real archived outcomes, benchmarked against the existing conviction score)</span></div>
        <div id="optMlClassifier" class="ifx-opt-body">Loading…</div>
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
      <div class="ifx-opt-section">
        <div class="ifx-opt-section-title">Feature Information Coefficient <span class="ifx-tone-faint">(correlation of every informational field against real R-multiple outcomes)</span></div>
        <div class="ifx-opt-ablation-controls">
          <button type="button" class="ifx-btn ifx-btn--on-paper" id="optIcRun">Load Feature IC</button>
        </div>
        <div id="optIc" class="ifx-opt-body">Click "Load Feature IC" — nothing runs automatically (this queries live archived-outcome data on demand, decoding every archived signal's full snapshot — can take ~20-30s on a large archive).</div>
      </div>
    `;

    this._el.querySelector('#optAblationRun')?.addEventListener('click', () => this._runAblation());
    this._el.querySelector('#optIcRun')?.addEventListener('click', () => this._runFeatureIc());
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
      ${this._renderDsr(resp.dsr)}
    `;
  }

  _renderDsr(dsr) {
    if (!dsr) return '';
    if (!dsr.available) {
      return `<div class="ifx-opt-dsr">
        <div class="ifx-opt-section-subtitle">Deflated Sharpe Ratio <span class="ifx-tone-faint">(corrects for testing ${dsr.n_trials || 0} profile variants)</span></div>
        <div class="ifx-opt-unavailable">${escapeHtml(dsr.reason || 'Not enough data to compute.')}</div>
      </div>`;
    }
    const pct = dsr.deflated_sharpe_ratio != null ? Math.round(dsr.deflated_sharpe_ratio * 100) : null;
    const tone = pct == null ? '' : pct >= 80 ? 'ifx-tone-good' : pct >= 50 ? '' : 'ifx-tone-bad';
    return `<div class="ifx-opt-dsr">
      <div class="ifx-opt-section-subtitle">Deflated Sharpe Ratio <span class="ifx-tone-faint">(corrects for testing ${dsr.n_trials || 0} profile variants)</span></div>
      <div class="ifx-opt-metric-grid">
        <div class="ifx-opt-metric"><label>DSR</label><b class="ifx-mono ${tone}">${pct != null ? pct + '%' : '—'}</b></div>
        <div class="ifx-opt-metric"><label>Recommended Sharpe</label><b class="ifx-mono">${dsr.recommended_sharpe != null ? dsr.recommended_sharpe : '—'}</b></div>
        <div class="ifx-opt-metric"><label>Chance benchmark</label><b class="ifx-mono">${dsr.benchmark_sharpe != null ? dsr.benchmark_sharpe : '—'}</b></div>
        <div class="ifx-opt-metric"><label>Test trades</label><b class="ifx-mono">${dsr.recommended_n_trades || 0}</b></div>
      </div>
      <p class="ifx-opt-note">${escapeHtml(dsr.note || '')}</p>
    </div>`;
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

  _renderKelly(resp) {
    const el = this._el.querySelector('#optKelly');
    if (!el) return;
    if (!resp || !resp.available) {
      el.innerHTML = `<div class="ifx-opt-unavailable">${escapeHtml(resp?.reason || 'Unavailable')}</div>`;
      return;
    }
    const strategies = resp.strategies || {};
    const rows = Object.entries(strategies);
    if (!rows.length) {
      el.innerHTML = `<div class="ifx-opt-unavailable">No archived outcomes yet.</div>`;
      return;
    }
    el.innerHTML = `
      <div class="ifx-opt-metric-grid">
        ${rows.map(([strategyId, s]) => `
          <div class="ifx-opt-metric">
            <label>${escapeHtml(strategyId)}</label>
            <b class="ifx-mono ${s.reliable ? (Number(s.half_kelly_pct) >= 0 ? 'ifx-tone-good' : 'ifx-tone-bad') : ''}">
              ${s.reliable ? s.half_kelly_pct + '%' : 'not enough sample'}
            </b>
            <small>${s.win_rate_pct != null ? s.win_rate_pct + '% win rate' : '—'} · ${s.decided || 0} decided</small>
          </div>
        `).join('')}
      </div>
      <p class="ifx-opt-note">${escapeHtml(resp.note || '')}</p>
    `;
  }

  _renderMlClassifier(resp) {
    const el = this._el.querySelector('#optMlClassifier');
    if (!el) return;
    if (!resp || !resp.available) {
      el.innerHTML = `<div class="ifx-opt-unavailable">${escapeHtml(resp?.reason || 'Unavailable')}</div>`;
      return;
    }
    if (resp.reason && !resp.test_metrics) {
      // available:true but training itself declined (too few rows either
      // side of the purge/embargo split) -- same shape backtest.py's
      // walkforward LOW_SAMPLE case uses.
      el.innerHTML = `<div class="ifx-opt-unavailable">${escapeHtml(resp.reason)}</div>`;
      return;
    }
    const m = resp.test_metrics || {};
    const lift = resp.lift_over_score_auc;
    const liftTone = lift == null ? '' : lift >= 0.05 ? 'ifx-tone-good' : lift >= 0 ? '' : 'ifx-tone-bad';
    const trainedAgo = resp.trained_at ? this._relativeTime(resp.trained_at) : '—';
    el.innerHTML = `
      <div class="ifx-opt-stat-row">
        <span class="ifx-badge ${resp.reliable ? 'ifx-badge--bull' : 'ifx-badge--neutral'}">${resp.reliable ? 'reliable sample' : 'building sample'}</span>
        <span class="ifx-tone-faint">${resp.n_train || 0} train / ${resp.n_test || 0} test · ${resp.n_active_features || 0} active features · trained ${trainedAgo}</span>
      </div>
      <div class="ifx-opt-metric-grid">
        <div class="ifx-opt-metric"><label>Model test AUC</label><b class="ifx-mono">${m.auc != null ? m.auc : '—'}</b></div>
        <div class="ifx-opt-metric"><label>Raw score AUC (baseline)</label><b class="ifx-mono">${resp.baseline_score_auc != null ? resp.baseline_score_auc : '—'}</b></div>
        <div class="ifx-opt-metric"><label>Lift over score</label><b class="ifx-mono ${liftTone}">${lift != null ? (lift >= 0 ? '+' : '') + lift : '—'}</b></div>
        <div class="ifx-opt-metric"><label>Test accuracy</label><b class="ifx-mono">${m.accuracy != null ? Math.round(m.accuracy * 100) + '%' : '—'}</b></div>
      </div>
      <p class="ifx-opt-note">${escapeHtml(resp.interpretation || '')}</p>
      <p class="ifx-opt-note">Informational only — never wired into suppression, position sizing, or the live conviction score. Surfaces as <code>sub_scores.ml_classifier.ml_probability</code> on newly fired signals; see Stock Detail's Signal-time evidence for a specific signal's own reading.</p>
    `;
  }

  _relativeTime(iso) {
    try {
      const ms = Date.now() - new Date(iso).getTime();
      if (ms < 0) return 'just now';
      const mins = Math.round(ms / 60000);
      if (mins < 60) return `${mins}m ago`;
      const hours = Math.round(mins / 60);
      if (hours < 48) return `${hours}h ago`;
      return `${Math.round(hours / 24)}d ago`;
    } catch (_) {
      return '—';
    }
  }

  async _runFeatureIc() {
    if (this._icBusy) return;
    const out = this._el.querySelector('#optIc');
    const btn = this._el.querySelector('#optIcRun');
    if (!out) return;
    this._icBusy = true;
    if (btn) btn.disabled = true;
    out.innerHTML = 'Loading — decoding the full archive, this can take ~20-30s…';
    try {
      const resp = await api.fetch('/api/backtest/feature-ic?days=90');
      if (!resp || !resp.available) {
        out.innerHTML = `<div class="ifx-opt-unavailable">${escapeHtml(resp?.reason || 'Unavailable')}</div>`;
      } else {
        const fields = resp.fields || [];
        out.innerHTML = `
          <table class="ifx-opt-ic-table">
            <thead><tr><th>Field</th><th>IC</th><th>Present</th><th>Absent</th><th>Reliable</th></tr></thead>
            <tbody>
              ${fields.map(f => `
                <tr>
                  <td>${escapeHtml(f.field)}</td>
                  <td class="ifx-mono ${f.ic != null && Math.abs(f.ic) >= 0.1 ? (f.ic > 0 ? 'ifx-tone-good' : 'ifx-tone-bad') : ''}">${f.ic != null ? f.ic : '—'}</td>
                  <td class="ifx-mono">${f.n_present}</td>
                  <td class="ifx-mono">${f.n_absent}</td>
                  <td>${f.reliable ? '<span class="ifx-badge ifx-badge--bull">yes</span>' : '<span class="ifx-badge ifx-badge--neutral">no</span>'}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
          <p class="ifx-opt-note">${escapeHtml(resp.note || '')}</p>
        `;
      }
    } catch (err) {
      out.innerHTML = `<div class="ifx-opt-unavailable">Request failed: ${escapeHtml(String(err))}</div>`;
    }
    this._icBusy = false;
    if (btn) btn.disabled = false;
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
