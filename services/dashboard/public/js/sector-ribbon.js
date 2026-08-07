/**
 * Tabbed market pulse heatmap for v4 screener UI.
 * Inspired by market-pulse products: sector strength, stock heat, gainers/losers.
 */
import { api } from './api.js';
import { escapeHtml, formatPct, formatPrice, formatRelVol } from './utils.js';

function sectorColor(score) {
  const s = Number(score || 0);
  if (s >= 75) return '#059669';
  if (s >= 60) return '#65a30d';
  if (s >= 45) return '#d97706';
  if (s >= 30) return '#ea580c';
  return '#dc2626';
}

function stockColor(item) {
  const decision = String(item.trade_decision || '').toUpperCase();
  const chg = Number(item.change_pct || 0);
  if (decision.includes('BUY CE') || chg >= 1.25) return '#059669';
  if (decision.includes('BUY PE') || chg <= -1.25) return '#dc2626';
  if (chg > 0) return '#65a30d';
  if (chg < 0) return '#ea580c';
  return '#94a3b8';
}

function heatSize(value, min = 70, max = 170) {
  const n = Math.max(0, Math.min(100, Number(value || 0)));
  return Math.round(min + (max - min) * (n / 100));
}

export class SectorRibbon {
  constructor(containerEl) {
    this._el = containerEl;
    this._sectors = [];
    this._ticks = [];
    this._active = localStorage.getItem('infusion:pulseTab') || 'sectors';
    this._unsubs = [];
  }

  init() {
    if (!this._el) return;
    this._render();
    this._unsubs.push(api.subscribe('/api/sectors', (resp) => {
      this._sectors = Array.isArray(resp?.sectors) ? resp.sectors : [];
      this._render();
    }, 10000));
    this._unsubs.push(api.subscribe('/api/ticks', (resp) => {
      this._ticks = Array.isArray(resp?.ticks) ? resp.ticks : [];
      this._render();
    }, 7000));
  }

  _setTab(tab) {
    this._active = tab;
    try { localStorage.setItem('infusion:pulseTab', tab); } catch (_) {}
    this._render();
  }

  _renderTabs() {
    const tabs = [
      ['sectors', 'Sector Heatmap'],
      ['stocks', 'Stock Heatmap'],
      ['gainers', 'Top Gainers'],
      ['losers', 'Top Losers'],
    ];
    return `<div class="pulse-tabs">
      ${tabs.map(([key, label]) => `<button class="pulse-tab ${this._active === key ? 'active' : ''}" data-pulse-tab="${key}">${label}</button>`).join('')}
    </div>`;
  }

  _sectorHeatmap() {
    const list = [...this._sectors].sort((a, b) => Number(b.strength_score || 0) - Number(a.strength_score || 0));
    if (!list.length) return '<div class="pulse-empty">Loading sector heatmap…</div>';
    // Compact A/D format per feedback: name + score + advancing/declining
    // count, no bar -- one glance, no wasted vertical space.
    return `<div class="sector-ribbon-grid pulse-grid sector-heatmap-grid compact">
      ${list.map(s => {
        const name = String(s.sector_id || '—');
        const score = Math.round(Number(s.strength_score || 0));
        const advancing = Math.round(Number(s.advancing || 0));
        const declining = Math.round(Number(s.declining || 0));
        const c = sectorColor(score);
        return `<button class="sector-ribbon-card pulse-heat-card" data-sector="${escapeHtml(name)}" title="${escapeHtml(name)} strength ${score} — ${advancing} advancing / ${declining} declining">
          <span class="sector-ribbon-top"><b>${escapeHtml(name.replace('_', ' '))}</b><strong style="color:${c}">${score}</strong></span>
          <span class="sector-ribbon-ad"><i class="up">${advancing}▲</i><i class="down">${declining}▼</i></span>
        </button>`;
      }).join('')}
    </div>`;
  }

  _stockHeatmap(mode = 'stocks') {
    let list = [...this._ticks];
    if (mode === 'gainers') list.sort((a, b) => Number(b.change_pct || 0) - Number(a.change_pct || 0));
    else if (mode === 'losers') list.sort((a, b) => Number(a.change_pct || 0) - Number(b.change_pct || 0));
    else list.sort((a, b) => Number(b.option_readiness || 0) - Number(a.option_readiness || 0));
    list = list.slice(0, mode === 'stocks' ? 45 : 24);
    if (!list.length) return '<div class="pulse-empty">Loading stock heatmap…</div>';
    return `<div class="stock-heatmap-grid">
      ${list.map(item => {
        const c = stockColor(item);
        const conv = Math.round(Number(item.option_readiness || item.conviction_score || 0));
        const size = heatSize(mode === 'stocks' ? conv : Math.min(100, Math.abs(Number(item.change_pct || 0)) * 22));
        return `<button class="stock-heat-tile" data-symbol="${escapeHtml(item.symbol || '')}" style="--heat:${c};--tile:${size}px" title="${escapeHtml(item.symbol || '')} ${formatPct(item.change_pct)} · Conv ${conv}">
          <b>${escapeHtml(item.symbol || '—')}</b>
          <span>${formatPrice(item.ltp)} · ${formatPct(item.change_pct)}</span>
          <small>${escapeHtml(item.sector_id || '-')} · RV ${formatRelVol(item.rel_vol)} · C${conv}</small>
        </button>`;
      }).join('')}
    </div>`;
  }

  _render() {
    if (!this._el) return;
    const body = this._active === 'sectors'
      ? this._sectorHeatmap()
      : this._stockHeatmap(this._active);
    this._el.innerHTML = `
      <div class="pulse-head">
        <span class="section-title"><span class="section-drag-handle" title="Drag section">⋮⋮</span>Market Pulse</span>
        ${this._renderTabs()}
        <span class="section-size-controls">
          <button type="button" data-section-action="expand" title="Expand section">+</button>
          <button type="button" data-section-action="collapse" title="Minimize section">−</button>
        </span>
      </div>
      <div class="pulse-body">${body}</div>
    `;
    this._el.querySelectorAll('[data-pulse-tab]').forEach(btn => {
      btn.addEventListener('click', () => this._setTab(btn.dataset.pulseTab));
    });
    this._el.querySelectorAll('[data-sector]').forEach(btn => {
      btn.addEventListener('click', () => {
        document.dispatchEvent(new CustomEvent('sector:select', { detail: btn.dataset.sector }));
      });
    });
    this._el.querySelectorAll('[data-symbol]').forEach(btn => {
      btn.addEventListener('click', () => {
        const symbol = btn.dataset.symbol;
        if (symbol) document.dispatchEvent(new CustomEvent('chart:load', { detail: { symbol } }));
      });
    });
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
  }
}
