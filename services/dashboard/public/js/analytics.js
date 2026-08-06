/**
 * Analytics Panel — precision gauge, grade breakdown, suppression stats
 * Priority 5: Statistical validation dashboard
 */
import { formatPct, clamp } from './utils.js';
import { api } from './api.js';

export class AnalyticsPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._precision = null;
    this._grades = null;
    this._suppression = null;
    this._backtest = null;
    this._optimizer = null;
    this._forward = null;
    this._expectancy = null;
    this._unsubs = [];
  }

  init() {
    this._unsubs.push(api.subscribe('/api/analytics/precision', (resp) => {
      this._precision = resp;
      this._render();
    }, 30000));

    this._unsubs.push(api.subscribe('/api/analytics/precision/grade', (resp) => {
      this._grades = resp;
      this._render();
    }, 30000));

    this._unsubs.push(api.subscribe('/api/analytics/suppression', (resp) => {
      this._suppression = resp;
      this._render();
    }, 30000));

    this._unsubs.push(api.subscribe('/api/journal/expectancy', (resp) => {
      this._expectancy = resp;
      this._render();
    }, 30000));

    this._unsubs.push(api.subscribe('/api/backtest/summary?days=60', (resp) => {
      this._backtest = resp;
      this._render();
    }, 30000));

    this._unsubs.push(api.subscribe('/api/backtest/walkforward?days=120&target=80&min_train=30&min_test=15', (resp) => {
      this._optimizer = resp;
      this._render();
    }, 30000));

    this._unsubs.push(api.subscribe('/api/backtest/forward?target=80&min_decided=30', (resp) => {
      this._forward = resp;
      this._render();
    }, 30000));

    // Diagnostics fallback when Postgres analytics unavailable
    this._unsubs.push(api.subscribe('/api/diagnostics', (resp) => {
      this._diagnostics = resp;
      this._render();
    }, 15000));

    this._render();
  }

  _render() {
    const p = this._precision || {};
    const pct = p.precision != null ? Math.round(p.precision * 100) : null;
    const hits = p.target_hits || 0;
    const stops = p.stop_hits || 0;
    const expired = p.expired || 0;
    const total = p.total_tracked || 0;

    this._el.innerHTML = `
      <div class="analytics-section">
        <div class="analytics-section-title">Expectancy First</div>
        ${this._renderExpectancy()}
      </div>
      <div class="analytics-section">
        <div class="analytics-section-title">Phase 5 Proof</div>
        ${this._renderBacktest()}
      </div>
      <div class="analytics-section">
        <div class="analytics-section-title">Walk-forward Proof</div>
        ${this._renderOptimizer()}
      </div>
      <div class="analytics-section">
        <div class="analytics-section-title">Precision - Secondary</div>
        ${this._renderGauge(pct)}
        <div style="display:flex;justify-content:center;gap:16px;font-size:var(--fs-xs);color:var(--text-muted);margin-top:4px">
          <span>Hits: <strong class="positive">${hits}</strong></span>
          <span>Stops: <strong class="negative">${stops}</strong></span>
          <span>Exp: <strong>${expired}</strong></span>
          <span>Total: <strong>${total}</strong></span>
        </div>
      </div>
      <div class="analytics-section">
        <div class="analytics-section-title">Forward Proof</div>
        ${this._renderForwardProof()}
      </div>
      <div class="analytics-section">
        <div class="analytics-section-title">By Grade</div>
        ${this._renderGrades()}
      </div>
      <div class="analytics-section">
        <div class="analytics-section-title">Suppression</div>
        ${this._renderSuppression()}
      </div>
    `;
  }

  _renderGauge(pct) {
    if (pct == null) {
      return `<div class="precision-gauge" style="background:conic-gradient(var(--border-default) 0deg,var(--border-default) 360deg)">
        <span class="precision-gauge-value text-muted">—</span>
      </div>
      <div class="precision-gauge-label">Awaiting data</div>`;
    }

    const angle = (pct / 100) * 360;
    const color = pct >= 60 ? 'var(--green)' : pct >= 45 ? 'var(--amber)' : 'var(--red)';
    return `<div class="precision-gauge" style="background:conic-gradient(${color} 0deg,${color} ${angle}deg,var(--bg-base) ${angle}deg,var(--bg-base) 360deg)">
      <span class="precision-gauge-value" style="color:${color}">${pct}%</span>
    </div>
    <div class="precision-gauge-label">Signal Precision</div>`;
  }

  _renderExpectancy() {
    const e = this._expectancy || {};
    if (!e.ok) {
      return '<div class="text-muted" style="font-size:var(--fs-xs);padding:4px 0">Loading journal expectancy...</div>';
    }
    const exp = e.expectancy_r == null ? '—' : Number(e.expectancy_r).toFixed(2) + 'R';
    const pf = e.profit_factor == null ? '—' : Number(e.profit_factor).toFixed(2);
    const hit = e.hit_rate == null ? '—' : Math.round(Number(e.hit_rate) * 100) + '%';
    const sample = e.sample || {};
    const tone = e.expectancy_r == null ? 'watch' : Number(e.expectancy_r) > 0 ? 'good' : 'weak';
    return `
      <div class="proof-card ${tone}">
        <div class="proof-head">
          <div><span>Expectancy</span><b>${exp}</b></div>
          <div><span>Profit Factor</span><b>${pf}</b></div>
          <div><span>Cost Drag</span><b>₹${Number(e.cost_drag || 0).toFixed(0)}</b></div>
        </div>
        <div class="proof-stats">
          <span>Taken <b>${sample.taken || 0}</b></span>
          <span>Skipped <b>${sample.skipped || 0}</b></span>
          <span>Not reviewed <b>${sample.not_reviewed || 0}</b></span>
          <span>Hit rate <b>${hit}</b></span>
          <span>Max DD <b>${e.max_drawdown_r ?? '—'}R</b></span>
        </div>
        <p>${e.note || 'Expectancy uses journal rows after reality gates.'}</p>
      </div>
    `;
  }

  _renderGrades() {
    const grades = this._grades;
    if (!grades || !grades.grades) {
      return '<div class="text-muted" style="font-size:var(--fs-xs);padding:4px 0">No grade data yet</div>';
    }

    const gradeOrder = ['A+', 'A', 'B+', 'B'];
    const gradeColors = { 'A+': 'var(--gold)', 'A': 'var(--silver)', 'B+': 'var(--blue)', 'B': 'var(--text-muted)' };

    return gradeOrder.map(g => {
      const data = grades.grades[g] || {};
      const pct = data.precision != null ? Math.round(data.precision * 100) : 0;
      const total = data.total || 0;
      const hits = data.hits || 0;
      const color = gradeColors[g] || 'var(--text-muted)';

      return `<div class="grade-row">
        <span class="grade-label" style="color:${color}">${g}</span>
        <div class="bar-container"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
        <span class="pct">${pct}%</span>
        <span class="count">${hits}/${total}</span>
      </div>`;
    }).join('');
  }

  _renderBacktest() {
    const b = this._backtest;
    if (!b) {
      return '<div class="text-muted" style="font-size:var(--fs-xs);padding:4px 0">Loading proof summary...</div>';
    }
    if (!b.available) {
      return `<div class="proof-card weak">
        <div><b>NO DATA</b><span>${b.reason || 'Backtest summary unavailable.'}</span></div>
      </div>`;
    }
    const pct = b.precision_pct == null ? '—' : `${Math.round(b.precision_pct)}%`;
    const tone = b.reliability === 'PROMISING' ? 'good'
      : ['BUILDING', 'MIXED', 'LOW_SAMPLE'].includes(b.reliability) ? 'watch'
      : 'weak';
    const rows = (b.by_sector || []).slice(0, 4).map(r => `
      <div class="proof-mini-row">
        <span>${r.label}</span>
        <b>${r.precision_pct == null ? '—' : Math.round(r.precision_pct) + '%'}</b>
        <em>${r.wins}/${r.total}</em>
      </div>
    `).join('');
    return `
      <div class="proof-card ${tone}">
        <div class="proof-head">
          <div><span>Reliability</span><b>${b.reliability || 'UNKNOWN'}</b></div>
          <div><span>Precision</span><b>${pct}</b></div>
          <div><span>Sample</span><b>${b.decided || 0}/${b.total || 0}</b></div>
        </div>
        <p>${b.note || 'Evidence is building.'}</p>
        <div class="proof-stats">
          <span>Hits <b class="positive">${b.target_hits || 0}</b></span>
          <span>Stops <b class="negative">${b.stop_hits || 0}</b></span>
          <span>Expired <b>${b.expired || 0}</b></span>
          <span>Avg R:R <b>${b.avg_rr ?? '—'}</b></span>
        </div>
        ${rows ? `<div class="proof-breakdown">${rows}</div>` : ''}
      </div>
    `;
  }

  _renderOptimizer() {
    const o = this._optimizer;
    if (!o) {
      return '<div class="text-muted" style="font-size:var(--fs-xs);padding:4px 0">Running walk-forward validation...</div>';
    }
    if (!o.available) {
      return `<div class="proof-card weak">
        <div><b>WALK-FORWARD OFF</b><span>${o.reason || 'Validation unavailable.'}</span></div>
      </div>`;
    }

    const rec = o.recommended || {};
    const train = rec.train || {};
    const test = rec.test || {};
    const tone = o.target_met ? 'good'
      : ['LOW_SAMPLE', 'FORWARD_BUILDING', 'NO_FORWARD_TRADES'].includes(o.status || rec.status) ? 'watch'
      : 'weak';
    const statusText = o.status || rec.status || 'NO_PROFILE';
    const trainPct = train.precision_pct == null ? '�' : `${Math.round(train.precision_pct)}%`;
    const testPct = test.precision_pct == null ? '�' : `${Math.round(test.precision_pct)}%`;
    const gap = rec.overfit_gap_pct == null ? '�' : `${Number(rec.overfit_gap_pct).toFixed(1)}%`;
    const candidates = (o.candidates || []).slice(0, 3).map((c, idx) => `
      <div class="proof-mini-row optimizer-row">
        <span>#${idx + 1} ${c.status}</span>
        <b>${c.test?.precision_pct == null ? '�' : Math.round(c.test.precision_pct) + '%'}</b>
        <em>${c.test?.decided || 0} fwd trades</em>
      </div>
    `).join('');

    return `
      <div class="proof-card ${tone}">
        <div class="proof-head optimizer-head">
          <div><span>Target</span><b>${Math.round(o.target_precision_pct || 80)}%</b></div>
          <div><span>Train</span><b>${trainPct}</b></div>
          <div><span>Forward</span><b>${testPct}</b></div>
        </div>
        <div class="optimizer-profile">
          <b>${statusText}</b>
          <span>${rec.label || 'No walk-forward profile found yet.'}</span>
        </div>
        <div class="proof-stats">
          <span>Train sample <b>${train.decided || 0}</b></span>
          <span>Forward sample <b>${test.decided || 0}</b></span>
          <span>Overfit gap <b>${gap}</b></span>
          <span>Freq <b>${test.trades_per_day ?? '�'}/day</b></span>
        </div>
        <p>${o.note || 'Use only out-of-sample forward proof before changing live alert gates.'}</p>
        ${candidates ? `<div class="proof-breakdown">${candidates}</div>` : ''}
      </div>
    `;
  }
  _renderForwardProof() {
    const f = this._forward;
    if (!f) {
      return '<div class="text-muted" style="font-size:var(--fs-xs);padding:4px 0">Loading forward proof...</div>';
    }
    if (!f.available) {
      return `<div class="proof-card weak">
        <div><b>FORWARD OFF</b><span>${f.reason || 'Forward validation unavailable.'}</span></div>
      </div>`;
    }

    const pct = f.precision_pct == null ? '—' : `${Math.round(f.precision_pct)}%`;
    const tone = f.status === 'TARGET_MET' ? 'good'
      : ['LOW_SAMPLE', 'BUILDING', 'NO_TRADES'].includes(f.status) ? 'watch'
      : 'weak';
    const directions = (f.by_direction || []).map(r => `
      <div class="proof-mini-row optimizer-row">
        <span>${r.label}</span>
        <b>${r.precision_pct == null ? '—' : Math.round(r.precision_pct) + '%'}</b>
        <em>${r.wins || 0}/${(r.wins || 0) + (r.losses || 0)}</em>
      </div>
    `).join('');

    return `
      <div class="proof-card ${tone}">
        <div class="proof-head optimizer-head">
          <div><span>Status</span><b>${f.status || 'UNKNOWN'}</b></div>
          <div><span>Forward Precision</span><b>${pct}</b></div>
          <div><span>Sample</span><b>${f.decided || 0}/${f.total || 0}</b></div>
        </div>
        <div class="optimizer-profile">
          <b>Corrected live guard</b>
          <span>${(f.profile && f.profile.label) || 'Score/R:R/session guard'}</span>
        </div>
        <div class="proof-stats">
          <span>Wins <b class="positive">${f.wins || 0}</b></span>
          <span>Losses <b class="negative">${f.losses || 0}</b></span>
          <span>Expired <b>${f.expired || 0}</b></span>
          <span>Open <b>${f.open || 0}</b></span>
        </div>
        <p>${f.note || 'Forward validation is building.'}</p>
        ${directions ? `<div class="proof-breakdown">${directions}</div>` : ''}
      </div>
    `;
  }

  _renderSuppression() {
    const s = this._suppression;
    if (!s) {
      // Fallback: show pipeline diagnostics when Postgres unavailable
      const d = this._diagnostics;
      if (d) {
        return `
          <div class="stat-row"><span class="label">Symbols</span><span class="value">${d.symbols_loaded || 0}</span></div>
          <div class="stat-row"><span class="label">Tick Keys</span><span class="value">${d.tick_keys || 0}</span></div>
          <div class="stat-row"><span class="label">Sectors</span><span class="value">${d.sectors_loaded || 0}</span></div>
          <div class="stat-row"><span class="label">Signals</span><span class="value">${d.active_signals || 0}</span></div>
          <div class="stat-row"><span class="label">Pre-breakout</span><span class="value">${d.prebreak_count || 0}</span></div>
          <div class="stat-row"><span class="label">WS Clients</span><span class="value">${d.websocket_clients || 0}</span></div>
        `;
      }
      return '<div class="text-muted" style="font-size:var(--fs-xs);padding:4px 0">No suppression data yet</div>';
    }

    const total = s.total_suppressed || 0;
    const rate = s.suppression_rate != null ? Math.round(s.suppression_rate * 100) : 0;
    const reasons = s.by_reason || [];

    let reasonHtml = '';
    if (Array.isArray(reasons)) {
      for (const r of reasons) {
        reasonHtml += `<div class="stat-row"><span class="label">${r.reason || 'unknown'}</span><span class="value">${r.count || 0}</span></div>`;
      }
    } else {
      for (const [reason, count] of Object.entries(reasons)) {
        const val = typeof count === 'object' ? (count.count || JSON.stringify(count)) : count;
        reasonHtml += `<div class="stat-row"><span class="label">${reason}</span><span class="value">${val}</span></div>`;
      }
    }

    return `
      <div class="stat-row"><span class="label">Suppressed</span><span class="value">${total}</span></div>
      <div class="stat-row"><span class="label">Rate</span><span class="value">${rate}%</span></div>
      ${reasonHtml}
    `;
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
  }
}
