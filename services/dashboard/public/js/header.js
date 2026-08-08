/**
 * Header Bar — regime, session, WS status, clock, health dots, theme toggle.
 * v2 design system (ifx-* classes, theme.css) — see theme.js for the toggle.
 */
import { istClock, currentSession, escapeHtml } from './utils.js';
import { api } from './api.js';
import { ws } from './ws.js';
import { theme } from './theme.js';

const SESSION_LABELS = {
  pre_market: 'Pre-Market',
  opening: 'Opening',
  mid_morning: 'Mid-Morning',
  midday: 'Midday',
  closing: 'Closing',
  cas_auction: 'Closing Auction',
  post_market: 'Post-Market',
};

const SERVICES = ['ingestion', 'normalizer', 'feature-engine', 'scanner', 'alerter', 'archiver'];

function regimeBadgeClass(regime) {
  const r = String(regime || '').toLowerCase();
  if (r.includes('bull') || r.includes('risk-on')) return 'ifx-badge--bull';
  if (r.includes('bear') || r.includes('risk-off')) return 'ifx-badge--bear';
  return 'ifx-badge--neutral';
}

export class Header {
  constructor(containerEl) {
    this._el = containerEl;
    this._clockInterval = null;
    this._unsubs = [];
  }

  init() {
    this._el.classList.add('ifx-shell', 'ifx-header');
    this._el.innerHTML = `
      <div class="ifx-header-brand">
        <span class="ifx-brand-mark">IF</span>
        <span class="ifx-brand-word">INFUSION</span>
      </div>
      <div class="ifx-header-center">
        <span class="ifx-badge ${regimeBadgeClass('neutral')}" id="regimeBadge">
          <span class="ifx-dot"></span><span id="regimeText">NEUTRAL</span>
        </span>
        <span class="ifx-badge ifx-badge--info" id="sessionBadge">${escapeHtml(SESSION_LABELS[currentSession()] || currentSession())}</span>
        <span class="ifx-ws-indicator" id="wsIndicator" title="Live data connection">
          <span class="ifx-dot ifx-dot--pulse" id="wsDot"></span>
          <span id="wsLabel">Connecting</span>
        </span>
      </div>
      <div class="ifx-header-right">
        <span class="ifx-badge ifx-badge--neutral" id="safetyChip" title="Paper-first lock and kill-switch status">
          <span class="ifx-dot"></span><span id="safetyChipText">Safety --</span>
        </span>
        <span class="ifx-header-clock ifx-mono" id="clockDisplay">${istClock()}</span>
        <span class="ifx-health-dots" id="healthDots" title="Service Health">
          ${SERVICES.map(s => `<span class="ifx-health-dot" data-svc="${s}" title="${escapeHtml(s)}"></span>`).join('')}
        </span>
        <button type="button" class="ifx-theme-toggle" id="themeToggle" aria-label="Toggle light/dark theme" title="Toggle light/dark theme">
          <span class="ifx-theme-toggle-icon">${theme.current === 'dark' ? '☀' : '☽'}</span>
        </button>
      </div>
    `;

    // Clock update
    this._clockInterval = setInterval(() => {
      const clockEl = document.getElementById('clockDisplay');
      const sessEl = document.getElementById('sessionBadge');
      if (clockEl) clockEl.textContent = istClock();
      if (sessEl) sessEl.textContent = SESSION_LABELS[currentSession()] || currentSession();
    }, 1000);

    // Theme toggle
    const toggleBtn = document.getElementById('themeToggle');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', () => {
        theme.toggle();
        const icon = toggleBtn.querySelector('.ifx-theme-toggle-icon');
        if (icon) icon.textContent = theme.current === 'dark' ? '☀' : '☽';
      });
    }

    // WS status
    ws.onStatus((status) => {
      const dot = document.getElementById('wsDot');
      const label = document.getElementById('wsLabel');
      if (dot) {
        dot.className = 'ifx-dot' + (status === 'connected' ? '' : ' ifx-dot--pulse');
        dot.style.color = status === 'connected' ? 'var(--ifx-bull)' : status === 'connecting' ? 'var(--ifx-warn)' : 'var(--ifx-bear)';
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
        badge.className = 'ifx-badge ' + regimeBadgeClass(regime);
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
          dot.classList.toggle('ifx-health-dot--healthy', info.status === 'healthy');
          dot.classList.toggle('ifx-health-dot--unhealthy', info.status !== 'healthy');
        }
      }
    }, 10000));

    // Safety chip — always-visible discipline reminder (paper-first lock +
    // kill switch) now that the full Safety Cockpit lives in the "More"
    // drawer rather than the default view. Same /api/safety/status
    // endpoint safety-panel.js already uses.
    this._unsubs.push(api.subscribe('/api/safety/status', (resp) => {
      const chip = document.getElementById('safetyChip');
      const text = document.getElementById('safetyChipText');
      if (!resp || !chip || !text) return;
      const killed = !!resp.kill_switch?.enabled;
      const verdict = resp.verdict || 'BLOCKED';
      let variant = 'ifx-badge--bear';
      let label = 'Blocked';
      if (killed) {
        variant = 'ifx-badge--bear';
        label = 'Kill switch ON';
      } else if (verdict === 'PAPER_READY') {
        variant = 'ifx-badge--bull';
        label = 'Paper-first ready';
      } else if (verdict === 'WATCH_READY') {
        variant = 'ifx-badge--warn';
        label = 'Paper-first (check)';
      }
      chip.className = 'ifx-badge ' + variant;
      text.textContent = label;
    }, 5000));
  }

  destroy() {
    if (this._clockInterval) clearInterval(this._clockInterval);
    this._unsubs.forEach(fn => fn());
  }
}
