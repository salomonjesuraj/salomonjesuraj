import { api } from './api.js';
import { escapeHtml } from './utils.js';

function tone(item) {
  if (!item?.entry_allowed) return 'block';
  if (item?.days_to_event != null && Number(item.days_to_event) <= 5) return 'warn';
  return 'pass';
}

function todayIso() {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

export class EventsPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._events = [];
    this._selectedSymbol = '';
    this._unsub = null;
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
    this._unsub = api.subscribe('/api/events/stocks?limit=500', resp => {
      this._events = Array.isArray(resp?.events) ? resp.events : [];
      this._render();
    }, 10000);
    this._render();
  }

  async _save() {
    const symbol = this._el.querySelector('#eventSymbol')?.value?.trim().toUpperCase();
    const nextEventDate = this._el.querySelector('#eventDate')?.value;
    const eventType = this._el.querySelector('#eventType')?.value || 'RESULTS';
    const note = this._el.querySelector('#eventNote')?.value?.trim() || '';
    if (!symbol || !nextEventDate) return;
    const res = await api.post('/api/events/stock', {
      symbol,
      next_event_date: nextEventDate,
      event_type: eventType,
      note,
      source: 'dashboard_manual',
    });
    if (res?.ok) {
      this._selectedSymbol = symbol;
      await this.refresh();
    }
  }

  async _delete(symbol) {
    await globalThis.fetch(`/api/events/stock/${encodeURIComponent(symbol)}`, { method: 'DELETE' });
    await this.refresh();
  }

  async refresh() {
    const resp = await api.fetch('/api/events/stocks?limit=500');
    this._events = Array.isArray(resp?.events) ? resp.events : [];
    this._render();
  }

  _render() {
    if (!this._el) return;
    const rows = this._events.map(e => `
      <article class="event-card ${tone(e)}">
        <div>
          <b>${escapeHtml(e.symbol || '-')}</b>
          <span>${escapeHtml(e.event_type || 'EVENT')} · ${escapeHtml(e.next_event_date || '-')}</span>
        </div>
        <em>${e.entry_allowed ? 'ALLOW' : 'BLOCK'}</em>
        <small>${escapeHtml(e.block_reason || (e.days_to_event == null ? 'No active block' : `${e.days_to_event} day(s) to event`))}</small>
        <button type="button" data-delete-event="${escapeHtml(e.symbol || '')}">Remove</button>
      </article>
    `).join('') || `<div class="panel-empty">No stock events saved yet. Add results/board-event dates here so option entries are blocked automatically around T-2 to T+1.</div>`;

    this._el.innerHTML = `
      <div class="event-console">
        <div class="event-form">
          <input id="eventSymbol" placeholder="SYMBOL" value="${escapeHtml(this._selectedSymbol)}" />
          <input id="eventDate" type="date" min="${todayIso()}" />
          <select id="eventType">
            <option value="RESULTS">Results</option>
            <option value="BOARD_MEETING">Board Meeting</option>
            <option value="DIVIDEND">Dividend</option>
            <option value="CORPORATE_ACTION">Corporate Action</option>
            <option value="EVENT">Other Event</option>
          </select>
          <input id="eventNote" placeholder="Note/source optional" />
          <button id="eventSave" type="button">Save Event Gate</button>
        </div>
        <div class="event-help">
          <b>How this is used</b>
          <span>Stock-option buying is blocked from T-2 to T+1 around the saved event. Index options are not treated as physical-settlement stock options.</span>
        </div>
        <div class="event-list">${rows}</div>
      </div>
    `;
    this._el.querySelector('#eventSave')?.addEventListener('click', () => this._save());
    this._el.querySelectorAll('[data-delete-event]').forEach(btn => {
      btn.addEventListener('click', () => this._delete(btn.dataset.deleteEvent));
    });
  }

  destroy() {
    if (this._unsub) this._unsub();
  }
}
