/**
 * Manual price trigger panel.
 * Lets trader say: "If this stock crosses this spot, show green / alert me."
 */
import { api } from './api.js';
import { escapeHtml, formatPrice } from './utils.js';

function tone(color) {
  if (color === 'green') return 'buy';
  if (color === 'red') return 'sell';
  return 'hold';
}

export class TriggerPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._triggers = [];
    this._selectedSymbol = '';
    this._unsubs = [];
  }

  init() {
    if (!this._el) return;
    document.addEventListener('chart:load', e => {
      const sym = e.detail?.symbol;
      if (sym) {
        this._selectedSymbol = String(sym).toUpperCase();
        this._render();
      }
    });
    this._unsubs.push(api.subscribe('/api/triggers', resp => {
      this._triggers = Array.isArray(resp?.triggers) ? resp.triggers : [];
      this._render();
    }, 1500));
    this._render();
  }

  async _create() {
    const symbol = this._el.querySelector('#trigSymbol')?.value?.trim().toUpperCase();
    const triggerPrice = Number(this._el.querySelector('#trigPrice')?.value || 0);
    const direction = this._el.querySelector('#trigDirection')?.value || 'above';
    const action = this._el.querySelector('#trigAction')?.value || (direction === 'above' ? 'BUY CE' : 'BUY PE');
    const telegram = this._el.querySelector('#trigTelegram')?.checked ?? true;
    if (!symbol || !triggerPrice) return;
    await api.post('/api/triggers', {
      symbol,
      trigger_price: triggerPrice,
      direction,
      action,
      telegram,
    });
    this._selectedSymbol = symbol;
    const resp = await api.fetch('/api/triggers');
    this._triggers = Array.isArray(resp?.triggers) ? resp.triggers : [];
    this._render();
  }

  async _delete(id) {
    await globalThis.fetch(`/api/triggers/${encodeURIComponent(id)}`, { method: 'DELETE' });
    const resp = await api.fetch('/api/triggers');
    this._triggers = Array.isArray(resp?.triggers) ? resp.triggers : [];
    this._render();
  }

  _render() {
    if (!this._el) return;
    const rows = this._triggers.map(t => `
      <div class="trigger-card ${tone(t.color)}">
        <div class="trigger-main">
          <div>
            <b>${escapeHtml(t.symbol)}</b>
            <span>${escapeHtml(String(t.direction || '').toUpperCase())} ${formatPrice(t.trigger_price)}</span>
          </div>
          <em>${escapeHtml(t.state || 'WAIT')}</em>
        </div>
        <div class="trigger-meta">
          <span>LTP ${formatPrice(t.ltp)}</span>
          <span>${escapeHtml(t.action || '')}</span>
          <span>${escapeHtml(t.mtf_text || 'MTF pending')}</span>
        </div>
        <div class="trigger-reason">${escapeHtml(t.reason || '')}</div>
        <button class="trigger-delete" data-delete-trigger="${escapeHtml(t.trigger_id)}">Remove</button>
      </div>
    `).join('') || `<div class="panel-empty">No manual triggers yet. Select a stock, enter spot, choose above/below.</div>`;

    this._el.innerHTML = `
      <div class="trigger-form">
        <input id="trigSymbol" placeholder="SYMBOL" value="${escapeHtml(this._selectedSymbol)}" />
        <input id="trigPrice" type="number" step="0.05" placeholder="Spot price" />
        <select id="trigDirection">
          <option value="above">Cross Above</option>
          <option value="below">Cross Below</option>
        </select>
        <select id="trigAction">
          <option value="BUY CE">BUY CE Watch</option>
          <option value="BUY PE">BUY PE Watch</option>
          <option value="ALERT ONLY">Alert Only</option>
        </select>
        <label class="trigger-check"><input type="checkbox" id="trigTelegram" checked /> Telegram</label>
        <button id="trigAdd">Add Trigger</button>
      </div>
      <div class="trigger-list">${rows}</div>
    `;
    this._el.querySelector('#trigAdd')?.addEventListener('click', () => this._create());
    this._el.querySelectorAll('[data-delete-trigger]').forEach(btn => {
      btn.addEventListener('click', () => this._delete(btn.dataset.deleteTrigger));
    });
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
  }
}
