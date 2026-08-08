/**
 * Top market ticker strip — NIFTY, BANKNIFTY, GIFT NIFTY, selected stock.
 * v2 design system (ifx-* classes, theme.css).
 */
import { api } from './api.js';
import { formatPrice, formatPct, escapeHtml } from './utils.js';

const DEFAULT_SYMBOLS = ['NIFTY50', 'NIFTYBANK', 'GIFTNIFTY'];

function pctTone(value) {
  const n = Number(value || 0);
  return n > 0 ? 'ifx-tone-good' : n < 0 ? 'ifx-tone-bad' : 'ifx-tone-faint';
}

function renderTile(item, label = null) {
  if (!item || item.error) {
    return `<div class="ifx-ticker-tile ifx-ticker-tile--muted">
      <span class="ifx-ticker-name">${escapeHtml(label || item?.symbol || '—')}</span>
      <span class="ifx-ticker-price ifx-mono">—</span>
      <span class="ifx-ticker-pct">No data</span>
    </div>`;
  }
  const chg = Number(item.change_pct || 0);
  return `<div class="ifx-ticker-tile">
    <span class="ifx-ticker-name">${escapeHtml(label || item.label || item.symbol)}</span>
    <span class="ifx-ticker-price ifx-mono">${formatPrice(item.ltp)}</span>
    <span class="ifx-ticker-pct ifx-mono ${pctTone(chg)}">${formatPct(chg, 2)}</span>
  </div>`;
}

export class MarketTicker {
  constructor(containerEl) {
    this._el = containerEl;
    this._selectedSymbol = '';
    this._unsubs = [];
  }

  init() {
    this._el.classList.add('ifx-shell', 'ifx-ticker');
    this._renderEmpty();

    this._unsubs.push(api.subscribe('/api/market/indices', (resp) => {
      this._indices = resp?.indices || [];
      this._render();
    }, 3000));

    document.addEventListener('chart:load', (e) => {
      const sym = e.detail?.symbol;
      if (sym) {
        this._selectedSymbol = String(sym).toUpperCase();
        this._loadSelected();
      }
    });

    document.addEventListener('signal:select', (e) => {
      const sym = e.detail?.symbol;
      if (sym) {
        this._selectedSymbol = String(sym).toUpperCase();
        this._loadSelected();
      }
    });
  }

  async _loadSelected() {
    if (!this._selectedSymbol || DEFAULT_SYMBOLS.includes(this._selectedSymbol)) {
      this._selected = null;
      this._render();
      return;
    }
    try {
      this._selected = await api.fetch(`/api/ticks/${this._selectedSymbol}`);
    } catch (_) {
      this._selected = { symbol: this._selectedSymbol, error: true };
    }
    this._render();
  }

  _renderEmpty() {
    this._el.innerHTML = `
      <div class="ifx-ticker-title">MARKET</div>
      ${DEFAULT_SYMBOLS.map(s => renderTile(
        { symbol: s, error: true },
        s === 'NIFTYBANK' ? 'BANKNIFTY' : s === 'GIFTNIFTY' ? 'GIFT NIFTY' : 'NIFTY'
      )).join('')}
      <div class="ifx-ticker-spacer"></div>
      <div class="ifx-ticker-tile ifx-ticker-tile--muted"><span class="ifx-ticker-name">SELECTED</span><span class="ifx-ticker-price ifx-mono">—</span><span class="ifx-ticker-pct">Click a symbol</span></div>
    `;
  }

  _render() {
    const bySymbol = new Map((this._indices || []).map(x => [x.symbol, x]));
    const nifty = bySymbol.get('NIFTY50') || bySymbol.get('NIFTY') || { symbol: 'NIFTY50', error: true };
    const bank = bySymbol.get('NIFTYBANK') || bySymbol.get('BANKNIFTY') || { symbol: 'NIFTYBANK', error: true };
    const gift = bySymbol.get('GIFTNIFTY') || bySymbol.get('GIFT NIFTY') || { symbol: 'GIFTNIFTY', error: true };
    const selected = this._selected || null;

    this._el.innerHTML = `
      <div class="ifx-ticker-title">MARKET</div>
      ${renderTile(nifty, 'NIFTY')}
      ${renderTile(bank, 'BANKNIFTY')}
      ${renderTile(gift, 'GIFT NIFTY')}
      <div class="ifx-ticker-spacer"></div>
      ${selected ? renderTile(selected, selected.symbol) : `<div class="ifx-ticker-tile ifx-ticker-tile--muted"><span class="ifx-ticker-name">SELECTED</span><span class="ifx-ticker-price ifx-mono">—</span><span class="ifx-ticker-pct">Click a symbol</span></div>`}
      <div class="ifx-ticker-note">Options mode: CE/PE bias uses underlying + chain readiness</div>
    `;
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
  }
}
