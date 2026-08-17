/**
 * Market Pulse — tabbed sector/stock heatmap (v2 design system).
 * Inspired by market-pulse products: sector strength, stock heat, gainers/losers.
 */
import { api } from './api.js';
import { escapeHtml, formatPct, formatPrice, formatRelVol } from './utils.js';

function sectorTone(score) {
  const s = Number(score || 0);
  if (s >= 75) return 'var(--ifx-bull-strong)';
  if (s >= 60) return 'var(--ifx-bull)';
  if (s >= 45) return 'var(--ifx-warn)';
  if (s >= 30) return 'var(--ifx-accent-strong)';
  return 'var(--ifx-bear)';
}

function stockTone(item) {
  const decision = String(item.trade_decision || '').toUpperCase();
  const chg = Number(item.change_pct || 0);
  if (decision.includes('BUY CE') || chg >= 1.25) return 'var(--ifx-bull-strong)';
  if (decision.includes('BUY PE') || chg <= -1.25) return 'var(--ifx-bear-strong)';
  if (chg > 0) return 'var(--ifx-bull)';
  if (chg < 0) return 'var(--ifx-bear)';
  return 'var(--ifx-content-text-faint)';
}

function heatSize(value, min = 68, max = 156) {
  const n = Math.max(0, Math.min(100, Number(value || 0)));
  return Math.round(min + (max - min) * (n / 100));
}

const COMPONENT_LABELS = {
  advance_decline: 'Adv/Dec', momentum: 'Momentum', volume_weighted: 'Volume-wtd',
  moving_average: 'Above 50/200-SMA', week52_range: '52W range',
};

function breadthTooltip(breadth) {
  if (!breadth || !breadth.available) return 'Market breadth unavailable';
  const lines = [`F&O universe breadth (${breadth.universe_size} symbols) — informational only, not wired into any signal`];
  for (const [key, label] of Object.entries(COMPONENT_LABELS)) {
    const c = breadth.components?.[key];
    if (!c) continue;
    lines.push(c.available ? `${label}: ${c.score}%` : `${label}: n/a (${c.reason || 'not enough coverage'})`);
  }
  return lines.join('\n');
}

function breadthTone(regime) {
  if (regime === 'healthy') return 'var(--ifx-bull-strong)';
  if (regime === 'weak') return 'var(--ifx-bear)';
  return 'var(--ifx-content-text-faint)';
}

export class SectorRibbon {
  constructor(containerEl) {
    this._el = containerEl;
    this._sectors = [];
    this._ticks = [];
    this._active = localStorage.getItem('infusion:pulseTab') || 'sectors';
    this._breadth = null;
    this._unsubs = [];
  }

  init() {
    if (!this._el) return;
    this._el.classList.add('ifx-pulse');
    this._render();
    this._unsubs.push(api.subscribe('/api/sectors', (resp) => {
      this._sectors = Array.isArray(resp?.sectors) ? resp.sectors : [];
      this._render();
    }, 10000));
    this._unsubs.push(api.subscribe('/api/ticks', (resp) => {
      this._ticks = Array.isArray(resp?.ticks) ? resp.ticks : [];
      this._render();
    }, 7000));
    // 5-component breadth score across the whole tracked F&O universe --
    // see api/market_breadth.py. Cheap (~0.1s, pure Redis); its own cache
    // TTL is 15 min so a 30s poll is plenty of headroom.
    this._unsubs.push(api.subscribe('/api/market/breadth-health', (resp) => {
      this._breadth = resp;
      this._render();
    }, 30000));
  }

  _breadthBadge() {
    const b = this._breadth;
    if (!b || !b.available) return '';
    return `<span class="ifx-pulse-breadth" style="--ifx-heat:${breadthTone(b.regime)}" title="${escapeHtml(breadthTooltip(b))}">
      <span class="ifx-pulse-breadth-label">BREADTH</span>
      <strong class="ifx-mono">${Math.round(b.health_score)}</strong>
      <em>${escapeHtml(b.regime)}</em>
    </span>`;
  }

  _setTab(tab) {
    this._active = tab;
    try { localStorage.setItem('infusion:pulseTab', tab); } catch (_) {}
    this._render();
  }

  _renderTabs() {
    const tabs = [
      ['sectors', 'Sectors'],
      ['stocks', 'Stocks'],
      ['gainers', 'Gainers'],
      ['losers', 'Losers'],
    ];
    return `<div class="ifx-pulse-tabs">
      ${tabs.map(([key, label]) => `<button type="button" class="ifx-pulse-tab${this._active === key ? ' ifx-pulse-tab--active' : ''}" data-pulse-tab="${key}">${label}</button>`).join('')}
    </div>`;
  }

  _sectorHeatmap() {
    const list = [...this._sectors].sort((a, b) => Number(b.strength_score || 0) - Number(a.strength_score || 0));
    if (!list.length) return '<div class="ifx-pulse-empty">Loading sector heatmap…</div>';
    return `<div class="ifx-pulse-grid ifx-pulse-grid--sectors">
      ${list.map(s => {
        const name = String(s.sector_id || '—');
        const score = Math.round(Number(s.strength_score || 0));
        const advancing = Math.round(Number(s.advancing || 0));
        const declining = Math.round(Number(s.declining || 0));
        return `<button type="button" class="ifx-pulse-card" data-sector="${escapeHtml(name)}" title="${escapeHtml(name)} strength ${score} — ${advancing} advancing / ${declining} declining" style="--ifx-heat:${sectorTone(score)}">
          <span class="ifx-pulse-card-top"><b>${escapeHtml(name.replace(/_/g, ' '))}</b><strong class="ifx-mono">${score}</strong></span>
          <span class="ifx-pulse-card-ad"><i class="ifx-tone-good">${advancing}▲</i><i class="ifx-tone-bad">${declining}▼</i></span>
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
    if (!list.length) return '<div class="ifx-pulse-empty">Loading stock heatmap…</div>';
    return `<div class="ifx-pulse-grid ifx-pulse-grid--stocks">
      ${list.map(item => {
        const conv = Math.round(Number(item.option_readiness || item.conviction_score || 0));
        const size = heatSize(mode === 'stocks' ? conv : Math.min(100, Math.abs(Number(item.change_pct || 0)) * 22));
        return `<button type="button" class="ifx-pulse-tile" data-symbol="${escapeHtml(item.symbol || '')}" style="--ifx-heat:${stockTone(item)};--ifx-tile:${size}px" title="${escapeHtml(item.symbol || '')} ${formatPct(item.change_pct)} · Conv ${conv}">
          <b>${escapeHtml(item.symbol || '—')}</b>
          <span class="ifx-mono">${formatPrice(item.ltp)} · ${formatPct(item.change_pct)}</span>
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
      <div class="ifx-pulse-head">
        <span class="ifx-pulse-title"><span class="ifx-drag-handle" title="Drag section">⋮⋮</span>Market Pulse</span>
        ${this._renderTabs()}
        ${this._breadthBadge()}
        <span class="ifx-section-size-controls">
          <button type="button" data-section-action="expand" title="Expand section">+</button>
          <button type="button" data-section-action="collapse" title="Minimize section">−</button>
        </span>
      </div>
      <div class="ifx-pulse-body">${body}</div>
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
