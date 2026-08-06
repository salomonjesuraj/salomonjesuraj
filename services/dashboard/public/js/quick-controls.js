/**
 * Quick Controls Bar — toggle modules ON/OFF instantly
 */
import { ws } from './ws.js';

const CONTROLS = [
  { key: 'live',      label: 'Live Data',  icon: '●' },
  { key: 'scanner',   label: 'Scanner',    icon: '📊' },
  { key: 'signals',   label: 'Signals',    icon: '🎯' },
  { key: 'watchlist', label: 'Watchlist',   icon: '👁' },
  { key: 'alerts',    label: 'Alerts',      icon: '📡' },
  { key: 'telegram',  label: 'Telegram',    icon: '✈' },
];

export class QuickControls {
  constructor(containerEl) {
    this._el = containerEl;
    this._state = {};
    this._layoutMode = localStorage.getItem('infusion:layoutMode') || 'balanced';
  }

  init() {
    // Load persisted state
    try {
      const saved = localStorage.getItem('infusion:qc');
      if (saved) this._state = JSON.parse(saved);
    } catch (e) { /* ignore */ }

    // Default: all ON except telegram
    for (const c of CONTROLS) {
      if (this._state[c.key] == null) {
        this._state[c.key] = c.key !== 'telegram';
      }
    }

    this._applyLayout();
    this._render();

    // Live data reflects WS connection
    ws.onStatus((status) => {
      this._state.live = status === 'connected';
      this._updateBtn('live');
    });
  }

  _render() {
    this._el.innerHTML = '';
    for (const c of CONTROLS) {
      const btn = document.createElement('button');
      btn.className = 'qc-btn' + (this._state[c.key] ? ' active' : '');
      btn.dataset.key = c.key;
      btn.innerHTML = `<span class="qc-dot"></span>${c.label}`;
      btn.addEventListener('click', () => this._toggle(c.key, btn));
      this._el.appendChild(btn);
    }

    const group = document.createElement('div');
    group.className = 'layout-mode-group';
    group.innerHTML = '<span class="layout-mode-label">Breakout Height</span>';
    const modes = [
      ['balanced', 'Balanced'],
      ['tall', 'Tall'],
      ['max', 'Max'],
    ];
    for (const [mode, label] of modes) {
      const btn = document.createElement('button');
      btn.className = 'layout-mode-btn' + (this._layoutMode === mode ? ' active' : '');
      btn.dataset.layoutMode = mode;
      btn.textContent = label;
      btn.addEventListener('click', () => this._setLayout(mode));
      group.appendChild(btn);
    }
    this._el.appendChild(group);
  }

  _toggle(key, btn) {
    this._state[key] = !this._state[key];
    btn.classList.toggle('active', this._state[key]);
    this._persist();
    document.dispatchEvent(new CustomEvent('qc:toggle', { detail: { key, active: this._state[key] } }));
  }

  _updateBtn(key) {
    const btn = this._el.querySelector(`[data-key="${key}"]`);
    if (btn) btn.classList.toggle('active', this._state[key]);
  }

  _persist() {
    try { localStorage.setItem('infusion:qc', JSON.stringify(this._state)); } catch (e) { /* ignore */ }
  }

  _setLayout(mode) {
    this._layoutMode = mode;
    try { localStorage.setItem('infusion:layoutMode', mode); } catch (e) { /* ignore */ }
    this._applyLayout();
    for (const btn of this._el.querySelectorAll('[data-layout-mode]')) {
      btn.classList.toggle('active', btn.dataset.layoutMode === mode);
    }
  }

  _applyLayout() {
    document.body.classList.remove('layout-balanced', 'layout-breakout-tall', 'layout-breakout-max');
    const cls = this._layoutMode === 'max'
      ? 'layout-breakout-max'
      : this._layoutMode === 'tall'
        ? 'layout-breakout-tall'
        : 'layout-balanced';
    document.body.classList.add(cls);
  }

  destroy() {}
}
