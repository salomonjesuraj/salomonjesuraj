/**
 * Options Strategy Selector — Phase 13.6 dashboard surfacing.
 * Renders GET /api/options/strategy-selector's ranked multi-leg shortlist
 * (Bull Call Spread, Bear Put Spread, Iron Condor, Long Straddle, Long
 * Strangle, Covered Call), each scored on directional/IV-Rank/PCR/Max-Pain
 * fit against real chain data. Advisory only -- this ranks a shortlist for
 * a human to review, it never picks or executes anything, same "propose
 * only" framing as options-analytics-panel.js and every other advisory
 * surface in this dashboard. Follows that panel's exact lifecycle/
 * selection pattern (chart:load / signal:select, api.subscribe polling).
 */
import { api } from './api.js';
import { formatPrice, escapeHtml } from './utils.js';

const STRATEGY_LABELS = {
  bull_call_spread: 'Bull Call Spread',
  bear_put_spread: 'Bear Put Spread',
  iron_condor: 'Iron Condor',
  long_straddle: 'Long Straddle',
  long_strangle: 'Long Strangle',
  covered_call: 'Covered Call',
};

function fitTone(score) {
  if (score >= 70) return 'ifx-badge--bull';
  if (score >= 45) return 'ifx-badge--neutral';
  return 'ifx-badge--bear';
}

function biasTone(bias) {
  if (bias === 'BUY CE') return 'ifx-badge--bull';
  if (bias === 'BUY PE') return 'ifx-badge--bear';
  return 'ifx-badge--neutral';
}

function legRow(leg) {
  const dirCls = leg.action === 'BUY' ? 'positive' : leg.action === 'SELL' ? 'negative' : '';
  const strikeText = leg.strike != null ? formatPrice(leg.strike) : 'Spot';
  return `
    <tr>
      <td class="${dirCls}">${escapeHtml(leg.action)}</td>
      <td>${escapeHtml(leg.type)}</td>
      <td class="ifx-mono">${strikeText}</td>
      <td class="ifx-mono">${formatPrice(leg.premium)}</td>
      <td class="ifx-mono">${leg.iv != null ? leg.iv.toFixed(1) + '%' : '—'}</td>
      <td class="ifx-mono">${leg.delta != null ? leg.delta.toFixed(3) : '—'}</td>
    </tr>`;
}

function strategyCard(s) {
  const label = STRATEGY_LABELS[s.strategy] || s.strategy;
  const netLine = s.net_debit != null
    ? `Net debit ${formatPrice(s.net_debit)}`
    : s.net_credit != null ? `Net credit ${formatPrice(s.net_credit)}` : '';
  const breakevens = Array.isArray(s.breakeven) ? s.breakeven.map(b => formatPrice(b)).join(' / ') : '—';
  const comps = s.components || {};
  const compRows = ['directional', 'iv_rank', 'pcr', 'max_pain'].map((key) => {
    const c = comps[key];
    if (!c) return '';
    const label2 = { directional: 'Directional', iv_rank: 'IV Rank', pcr: 'PCR', max_pain: 'Max Pain' }[key];
    return `<div class="ifx-ss-comp"><span>${label2}</span><b>${c.score}</b><small>${escapeHtml(c.reason)}</small></div>`;
  }).join('');

  return `
    <div class="ifx-ss-card">
      <div class="ifx-ss-card-head">
        <span class="ifx-ss-card-title">${escapeHtml(label)}</span>
        <span class="ifx-badge ${fitTone(s.fit_score)}">Fit ${s.fit_score}/100</span>
      </div>
      <div class="ifx-ss-legs">
        <table>
          <thead><tr><th>Action</th><th>Type</th><th>Strike</th><th>Premium</th><th>IV</th><th>Delta</th></tr></thead>
          <tbody>${(s.legs || []).map(legRow).join('')}</tbody>
        </table>
      </div>
      <div class="ifx-ss-metrics">
        <div><span>${escapeHtml(netLine)}</span></div>
        <div><span>Max profit</span><b class="positive">${s.max_profit != null ? formatPrice(s.max_profit) : 'Unlimited'}</b></div>
        <div><span>Max loss</span><b class="negative">${formatPrice(s.max_loss)}</b></div>
        <div><span>Breakeven</span><b>${breakevens}</b></div>
      </div>
      <div class="ifx-ss-comps">${compRows}</div>
    </div>`;
}

export class StrategySelectorPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._symbol = '';
    this._unsub = null;
  }

  init() {
    if (!this._el) return;
    this._el.classList.add('ifx-ss');
    this._renderEmpty();

    document.addEventListener('chart:load', (e) => {
      const sym = e.detail?.symbol;
      if (sym) this._select(String(sym).toUpperCase());
    });
    document.addEventListener('signal:select', (e) => {
      const sym = e.detail?.symbol;
      if (sym) this._select(String(sym).toUpperCase());
    });
  }

  _select(symbol) {
    if (this._unsub) { this._unsub(); this._unsub = null; }
    this._symbol = symbol;
    this._renderLoading();
    this._unsub = api.subscribe(`/api/options/strategy-selector?symbol=${encodeURIComponent(symbol)}`, (resp) => {
      this._render(resp);
    }, 20000);
  }

  _renderEmpty() {
    this._el.innerHTML = `
      <div class="ifx-oa-empty">
        <span class="ifx-oa-empty-icon">⌗</span>
        <span class="ifx-oa-empty-title">No symbol selected</span>
        <span class="ifx-oa-empty-sub">Click a symbol anywhere in the dashboard to see a ranked multi-leg strategy shortlist here — Bull Call Spread, Bear Put Spread, Iron Condor, Long Straddle, Long Strangle, Covered Call.</span>
      </div>`;
  }

  _renderLoading() {
    this._el.innerHTML = `<div class="ifx-oa-loading">Ranking ${escapeHtml(this._symbol)} strategies…</div>`;
  }

  _render(resp) {
    if (!resp || !resp.ready) {
      this._el.innerHTML = `
        <div class="ifx-oa-unavailable">
          <span class="ifx-oa-symbol">${escapeHtml(this._symbol)}</span>
          <span class="ifx-badge ifx-badge--warn">Not available</span>
          <p>${escapeHtml(resp?.reason || 'Strategy selector data is not ready yet for this symbol.')}</p>
        </div>`;
      return;
    }

    const ranked = Array.isArray(resp.ranked_strategies) ? resp.ranked_strategies : [];
    this._el.innerHTML = `
      <div class="ifx-oa-head">
        <span class="ifx-oa-symbol">${escapeHtml(resp.symbol)}</span>
        <span class="ifx-badge ifx-badge--neutral">Spot ${formatPrice(resp.spot)} · expiry ${escapeHtml(resp.expiry || '—')}</span>
        <span class="ifx-badge ${biasTone(resp.trade_bias)}">${escapeHtml(resp.trade_bias || 'HOLD')}</span>
      </div>
      <div class="ifx-ss-context">
        <span>MTF: ${escapeHtml(resp.mtf_alignment || '—')}</span>
        <span>IV Rank: ${resp.iv_rank != null ? resp.iv_rank.toFixed(0) : 'building history'}</span>
        <span>PCR: ${escapeHtml((resp.pcr_sentiment || '—').replace(/_/g, ' '))}</span>
        <span>Max Pain: ${resp.max_pain_strike != null ? formatPrice(resp.max_pain_strike) : '—'}</span>
      </div>
      <div class="ifx-ss-list">
        ${ranked.length ? ranked.map(strategyCard).join('') : '<p class="ifx-oa-card-note">No strategy could be built from the current chain.</p>'}
      </div>
      <p class="ifx-oa-footnote">Ranked shortlist, advisory only — never auto-selected or auto-executed. Every score is a fit read against current IV Rank/PCR/Max Pain/directional bias, not a profit guarantee.</p>
    `;
  }

  destroy() {
    if (this._unsub) this._unsub();
  }
}
