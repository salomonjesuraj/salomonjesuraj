import { api } from './api.js';
import { formatPrice } from './utils.js';

function money(v) {
  return formatPrice(Number(v || 0));
}

const SIGNAL_MODES = [
  ['precision', 'Precision'],
  ['opportunity', 'Opportunity'],
  ['aggressive_paper', 'Aggressive Paper'],
];

function modeLabel(mode) {
  return SIGNAL_MODES.find(([key]) => key === mode)?.[1] || 'Precision';
}

export class RiskConsole {
  constructor(containerEl) {
    this._el = containerEl;
    this._settings = null;
    this._saving = false;
  }

  init() {
    if (!this._el) return;
    this._render();
    this._load();
  }

  async _load() {
    const resp = await api.fetch('/api/risk/settings');
    this._settings = resp?.settings || null;
    this._emitSettings();
    this._render();
  }

  async _save() {
    if (!this._el || this._saving) return;
    const capital = Number(this._el.querySelector('#riskCapital')?.value || 50000);
    const maxDailyLoss = Number(this._el.querySelector('#riskDailyLoss')?.value || 2500);
    const riskPerTrade = Number(this._el.querySelector('#riskPerTrade')?.value || 1);
    const tradingSignalMode = this._el.querySelector('#tradingSignalMode')?.value || 'precision';
    this._saving = true;
    this._render();
    const resp = await api.post('/api/risk/settings', {
      capital,
      max_daily_loss: maxDailyLoss,
      risk_per_trade_pct: riskPerTrade,
      trading_signal_mode: tradingSignalMode,
    });
    this._settings = resp?.settings || this._settings;
    this._saving = false;
    this._emitSettings();
    this._render();
  }

  _emitSettings() {
    document.dispatchEvent(new CustomEvent('risk:settings-updated', {
      detail: this._settings || {},
    }));
  }

  _render() {
    const s = this._settings || {
      capital: 50000,
      max_daily_loss: 2500,
      max_daily_loss_pct: 5,
      risk_per_trade_pct: 1,
      risk_per_trade_amount: 500,
      high_conviction_risk_amount: 750,
      loss_day_max_trades: 2,
      green_day_max_trades: 5,
      execution_mode: 'paper_first',
      trading_signal_mode: 'precision',
    };
    const mode = String(s.trading_signal_mode || 'precision');
    const modeOptions = SIGNAL_MODES.map(([key, label]) => (
      `<option value="${key}" ${mode === key ? 'selected' : ''}>${label}</option>`
    )).join('');
    this._el.innerHTML = `
      <div class="risk-console-title">
        <span class="section-drag-handle" title="Drag section">⋮⋮</span>
        <b>Risk Console</b>
        <small>Capital guard + trade budget</small>
      </div>
      <div class="risk-console-left">
        <span class="risk-label">Capital</span>
        <input id="riskCapital" type="number" min="1000" step="500" value="${Number(s.capital || 0)}" />
        <span class="risk-label">Daily max loss</span>
        <input id="riskDailyLoss" type="number" min="0" step="100" value="${Number(s.max_daily_loss || 0)}" />
        <span class="risk-label">Risk/trade %</span>
        <input id="riskPerTrade" type="number" min="0.1" max="5" step="0.1" value="${Number(s.risk_per_trade_pct || 1)}" />
        <span class="risk-label">Signal mode</span>
        <select id="tradingSignalMode" title="Controls dashboard opportunity visibility. Live Telegram alerts remain strict.">
          ${modeOptions}
        </select>
        <button id="riskSave" ${this._saving ? 'disabled' : ''}>${this._saving ? 'Saving…' : 'Update risk'}</button>
      </div>
      <div class="risk-console-right">
        <div><small>Daily guard</small><b class="risk-red">${money(s.max_daily_loss)} / ${Number(s.max_daily_loss_pct || 0).toFixed(1)}%</b></div>
        <div><small>Normal trade</small><b>${money(s.risk_per_trade_amount)}</b></div>
        <div><small>High conviction</small><b class="risk-green">${money(s.high_conviction_risk_amount)}</b></div>
        <div><small>Loss day cap</small><b>${s.loss_day_max_trades || 2} trades</b></div>
        <div><small>Execution</small><b class="risk-yellow">${String(s.execution_mode || 'paper_first').replace('_', ' ')}</b></div>
        <div><small>Signal mode</small><b class="risk-yellow">${modeLabel(mode)}</b></div>
      </div>
      <span class="section-size-controls risk-section-controls">
        <button type="button" data-section-action="expand" title="Expand section">+</button>
        <button type="button" data-section-action="collapse" title="Minimize section">−</button>
      </span>
    `;
    this._el.querySelector('#riskSave')?.addEventListener('click', () => this._save());
  }

  destroy() {}
}
