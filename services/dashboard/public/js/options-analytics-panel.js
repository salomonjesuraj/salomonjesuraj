/**
 * Options Analytics — PCR, OI-based support/resistance, Max Pain.
 * Phase D of the UI overhaul: surfaces /api/options/chain-analytics
 * (built + live-verified in Phase 11/12 of the engine work this session,
 * never had a UI until now). Follows the same symbol-selection model as
 * scanner-insight.js (listens for chart:load).
 */
import { api } from './api.js';
import { formatPrice, escapeHtml } from './utils.js';

function sentimentTone(sentiment) {
  // .ifx-badge--bull/--bear/--neutral carry their own background+color pair
  // (see theme.css) -- .ifx-tone-* alone is text-color-only and left the
  // pill with no visible background when paired with .ifx-badge here.
  const s = String(sentiment || '').toLowerCase();
  if (s.includes('bullish')) return 'ifx-badge--bull';
  if (s.includes('bearish')) return 'ifx-badge--bear';
  return 'ifx-badge--neutral';
}

export class OptionsAnalyticsPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._symbol = '';
    this._unsub = null;
  }

  init() {
    if (!this._el) return;
    this._el.classList.add('ifx-oa');
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
    this._unsub = api.subscribe(`/api/options/chain-analytics?symbol=${encodeURIComponent(symbol)}`, (resp) => {
      this._render(resp);
    }, 15000);
  }

  _renderEmpty() {
    this._el.innerHTML = `
      <div class="ifx-oa-empty">
        <span class="ifx-oa-empty-icon">⌘</span>
        <span class="ifx-oa-empty-title">No symbol selected</span>
        <span class="ifx-oa-empty-sub">Click a symbol anywhere in the dashboard (cockpit card, scanner row, sector tile) to see its Put-Call Ratio, OI-based support/resistance, and Max Pain here.</span>
      </div>`;
  }

  _renderLoading() {
    this._el.innerHTML = `<div class="ifx-oa-loading">Loading ${escapeHtml(this._symbol)} option chain…</div>`;
  }

  _render(resp) {
    if (!resp || !resp.ready) {
      this._el.innerHTML = `
        <div class="ifx-oa-unavailable">
          <span class="ifx-oa-symbol">${escapeHtml(this._symbol)}</span>
          <span class="ifx-badge ifx-badge--warn">Chain unavailable</span>
          <p>${escapeHtml(resp?.reason || 'Option chain data is not ready yet for this symbol.')}</p>
        </div>`;
      return;
    }

    const pcr = resp.pcr || {};
    const sr = resp.oi_support_resistance || {};
    const mp = resp.max_pain || {};

    this._el.innerHTML = `
      <div class="ifx-oa-head">
        <span class="ifx-oa-symbol">${escapeHtml(this._symbol)}</span>
        <span class="ifx-badge ifx-badge--neutral">${resp.strikes_in_chain || 0} strikes · expiry ${escapeHtml(resp.expiry || '—')}</span>
        ${resp.cached ? '<span class="ifx-badge ifx-badge--neutral">cached</span>' : ''}
      </div>
      <div class="ifx-oa-grid">
        <div class="ifx-oa-card">
          <span class="ifx-oa-card-label">Put-Call Ratio</span>
          <span class="ifx-oa-card-value ifx-mono">${pcr.pcr != null ? pcr.pcr : '—'}</span>
          <span class="ifx-badge ${sentimentTone(pcr.sentiment)}">${escapeHtml((pcr.sentiment || 'no data').replace(/_/g, ' '))}</span>
          <span class="ifx-oa-card-note">Call OI ${pcr.total_call_oi != null ? pcr.total_call_oi.toLocaleString('en-IN') : '—'} · Put OI ${pcr.total_put_oi != null ? pcr.total_put_oi.toLocaleString('en-IN') : '—'}</span>
        </div>
        <div class="ifx-oa-card">
          <span class="ifx-oa-card-label">OI-Based Levels</span>
          <div class="ifx-oa-sr-row"><span class="ifx-tone-bad">Resistance</span><b class="ifx-mono">${sr.resistance != null ? formatPrice(sr.resistance) : '—'}</b></div>
          <div class="ifx-oa-sr-row"><span class="ifx-tone-good">Support</span><b class="ifx-mono">${sr.support != null ? formatPrice(sr.support) : '—'}</b></div>
          <span class="ifx-oa-card-note">Highest Call-OI / Put-OI strikes</span>
        </div>
        <div class="ifx-oa-card">
          <span class="ifx-oa-card-label">Max Pain</span>
          <span class="ifx-oa-card-value ifx-mono">${mp.max_pain_strike != null ? formatPrice(mp.max_pain_strike) : '—'}</span>
          <span class="ifx-oa-card-note">Strike where option-writer payout is minimized · ${mp.candidates_evaluated || 0} strikes evaluated</span>
        </div>
      </div>
      <p class="ifx-oa-footnote">Formulas sourced from Upstox's own learning-center content (the broker this data comes from). Informational only — not a directional recommendation on its own.</p>
    `;
  }

  destroy() {
    if (this._unsub) this._unsub();
  }
}
