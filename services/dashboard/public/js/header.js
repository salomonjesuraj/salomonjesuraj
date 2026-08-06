/**
 * Header Bar — regime, session, WS status, clock, health dots
 */
import { istClock, currentSession, regimeClass, escapeHtml } from './utils.js';
import { api } from './api.js';
import { ws } from './ws.js';

const SESSION_LABELS = {
  pre_market: 'Pre-Market',
  opening: 'Opening',
  mid_morning: 'Mid-Morning',
  midday: 'Midday',
  closing: 'Closing',
  post_market: 'Post-Market',
};

const SERVICES = ['ingestion', 'normalizer', 'feature-engine', 'scanner', 'alerter', 'archiver'];

export class Header {
  constructor(containerEl) {
    this._el = containerEl;
    this._clockInterval = null;
    this._unsubs = [];
  }

  init() {
    this._el.innerHTML = `
      <div class="header-brand">INFUSION</div>
      <div class="header-center">
        <div class="regime-badge neutral" id="regimeBadge">
          <span class="dot"></span>
          <span id="regimeText">NEUTRAL</span>
        </div>
        <div class="session-badge" id="sessionBadge">${SESSION_LABELS[currentSession()]}</div>
        <div class="ws-indicator">
          <span class="ws-dot connecting" id="wsDot"></span>
          <span id="wsLabel">Connecting</span>
        </div>
      </div>
      <div class="header-right">
        <div class="clock" id="clockDisplay">${istClock()}</div>
        <div class="health-dots" id="healthDots" title="Service Health">
          ${SERVICES.map(s => `<span class="health-dot" data-svc="${s}" title="${s}"></span>`).join('')}
        </div>
      </div>
    `;

    // Clock update
    this._clockInterval = setInterval(() => {
      const clockEl = document.getElementById('clockDisplay');
      const sessEl = document.getElementById('sessionBadge');
      if (clockEl) clockEl.textContent = istClock();
      if (sessEl) sessEl.textContent = SESSION_LABELS[currentSession()];
    }, 1000);

    // WS status
    ws.onStatus((status) => {
      const dot = document.getElementById('wsDot');
      const label = document.getElementById('wsLabel');
      if (dot) {
        dot.className = 'ws-dot ' + status;
      }
      if (label) {
        label.textContent = status === 'connected' ? 'Live' : status === 'connecting' ? 'Connecting' : 'Disconnected';
      }
    });

    // Regime
    this._unsubs.push(api.subscribe('/api/regime', (resp) => {
      if (!resp) return;
      const regime = resp.regime || 'neutral';
      const badge = document.getElementById('regimeBadge');
      const text = document.getElementById('regimeText');
      if (badge) {
        badge.className = 'regime-badge ' + regimeClass(regime);
      }
      if (text) {
        text.textContent = regime.replace(/_/g, ' ').toUpperCase();
      }
    }, 5000));

    // Health
    this._unsubs.push(api.subscribe('/api/health', (resp) => {
      if (!resp || !resp.services) return;
      for (const [svc, info] of Object.entries(resp.services)) {
        const dot = this._el.querySelector(`[data-svc="${svc}"]`);
        if (dot) {
          dot.className = 'health-dot ' + ((info.status === 'healthy') ? 'healthy' : 'unhealthy');
        }
      }
    }, 10000));
  }

  destroy() {
    if (this._clockInterval) clearInterval(this._clockInterval);
    this._unsubs.forEach(fn => fn());
  }
}
