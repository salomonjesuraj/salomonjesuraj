/**
 * Pre-Breakout Watchers strip — New shell's compact companion to Command
 * Center, per the approved mockup (quieter than a confirmed deck card:
 * dashed border, amber "state" tag, no bull/bear glow, since nothing here
 * is a confirmed call yet). Same /api/prebreakout + /api/sectors sources
 * watchlist.js already polls -- this only changes the rendering, not the
 * data or the state taxonomy (coiled/accumulating/compressing/triggered).
 */
import { api } from './api.js';
import { formatPrice, escapeHtml } from './utils.js';

const DASH = '—';
const STATE_LABEL = {
  coiled: 'COILED', accumulating: 'ACCUM', compressing: 'COMPRESSING', triggered: 'TRIGGERED',
};
const STATE_PRIORITY = { triggered: 5, coiled: 4, accumulating: 3, compressing: 2, idle: 1 };

export class WatchStripV2Panel {
  constructor(containerEl) {
    this._el = containerEl;
    this._items = [];
    this._unsubs = [];
  }

  init() {
    if (!this._el) return;
    this._el.classList.add('ifx-watch-strip-wrap');
    this._unsubs.push(api.subscribe('/api/prebreakout', (resp) => {
      this._items = resp?.watchlist || [];
      this._render();
    }, 3000));
    this._render();
  }

  _render() {
    const active = this._items
      .filter((w) => w.state && w.state !== 'idle' && w.state !== 'expired')
      .sort((a, b) => {
        const pa = STATE_PRIORITY[a.state] || 0, pb = STATE_PRIORITY[b.state] || 0;
        if (pa !== pb) return pb - pa;
        return (b.readiness_score || 0) - (a.readiness_score || 0);
      });

    const label = document.getElementById('watchCountV2');
    if (label) label.textContent = `${active.length} watching`;

    if (active.length === 0) {
      this._el.innerHTML = `<div class="ifx-watch-empty">No pre-breakout setups building right now.</div>`;
      return;
    }

    this._el.innerHTML = `<div class="ifx-watch-strip">${active.slice(0, 8).map((w) => this._renderCard(w)).join('')}</div>`;
    this._el.querySelectorAll('[data-watch-sym]').forEach((card) => {
      card.addEventListener('click', () => {
        document.dispatchEvent(new CustomEvent('chart:load', { detail: { symbol: card.dataset.watchSym } }));
      });
    });
  }

  _renderCard(w) {
    const state = (w.state || 'idle').toLowerCase();
    const readiness = Math.max(0, Math.min(100, Math.round(Number(w.readiness_score || 0))));
    const sector = w.sector_id || DASH;
    const hasSignal = Boolean(w.has_signal);
    const triggerText = hasSignal && w.entry_price > 0 ? `Signal live at ${formatPrice(w.entry_price)}` : `${readiness}% ready`;
    return `
    <div class="ifx-watch-card" data-watch-sym="${escapeHtml(w.symbol)}">
      <div class="ifx-watch-top"><span class="ifx-watch-symbol">${escapeHtml(w.symbol)}</span>
        <span class="ifx-badge ifx-badge--warn">${STATE_LABEL[state] || state.toUpperCase()}</span></div>
      <div class="ifx-watch-progress"><i style="width:${readiness}%"></i></div>
      <div class="ifx-watch-foot"><span>${escapeHtml(sector.replace(/_/g, ' '))}</span><b>${escapeHtml(triggerText)}</b></div>
    </div>`;
  }

  destroy() {
    this._unsubs.forEach((fn) => fn());
  }
}
