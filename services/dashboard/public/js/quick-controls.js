/**
 * Quick Controls Bar — toggle data modules ON/OFF instantly.
 * v2 design system (ifx-* classes, theme.css).
 *
 * The old "Breakout Height" balanced/tall/max control was dropped in the
 * v2 rebuild: it drove --signals-flex/--watchlist-flex/--alerts-flex, CSS
 * variables that were declared but never consumed by any var() anywhere
 * in the stylesheet (confirmed by grep before removing) — a dead control
 * left over from the pre-Signal-Cockpit 3-column layout.
 */
import { ws } from './ws.js';

const CONTROLS = [
  { key: 'live',      label: 'Live Data',  icon: '●' },
  { key: 'scanner',   label: 'Scanner',    icon: '▤' },
  { key: 'signals',   label: 'Signals',    icon: '◎' },
  { key: 'watchlist', label: 'Watchlist',  icon: '◑' },
  { key: 'alerts',    label: 'Alerts',     icon: '◈' },
  { key: 'telegram',  label: 'Telegram',   icon: '➤' },
];

export class QuickControls {
  constructor(containerEl) {
    this._el = containerEl;
    this._state = {};
  }

  init() {
    this._el.classList.add('ifx-shell', 'ifx-nav');
    try {
      const saved = localStorage.getItem('infusion:qc');
      if (saved) this._state = JSON.parse(saved);
    } catch (e) { /* ignore */ }

    for (const c of CONTROLS) {
      if (this._state[c.key] == null) {
        this._state[c.key] = c.key !== 'telegram';
      }
    }

    this._render();

    ws.onStatus((status) => {
      this._state.live = status === 'connected';
      this._updateBtn('live');
    });
  }

  _render() {
    this._el.innerHTML = CONTROLS.map(c => `
      <button type="button" class="ifx-nav-btn${this._state[c.key] ? ' ifx-nav-btn--active' : ''}" data-key="${c.key}">
        <span class="ifx-nav-btn-icon">${c.icon}</span>${c.label}
      </button>
    `).join('');
    this._el.querySelectorAll('[data-key]').forEach(btn => {
      btn.addEventListener('click', () => this._toggle(btn.dataset.key, btn));
    });
  }

  _toggle(key, btn) {
    this._state[key] = !this._state[key];
    btn.classList.toggle('ifx-nav-btn--active', this._state[key]);
    this._persist();
    document.dispatchEvent(new CustomEvent('qc:toggle', { detail: { key, active: this._state[key] } }));
  }

  _updateBtn(key) {
    const btn = this._el.querySelector(`[data-key="${key}"]`);
    if (btn) btn.classList.toggle('ifx-nav-btn--active', this._state[key]);
  }

  _persist() {
    try { localStorage.setItem('infusion:qc', JSON.stringify(this._state)); } catch (e) { /* ignore */ }
  }

  destroy() {}
}
