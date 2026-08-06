/**
 * Footer Diagnostics Bar - pipeline health + data freshness.
 */
import { ws } from './ws.js';
import { api } from './api.js';
import {
  DASHBOARD_VERSION,
  DASHBOARD_UPDATED_AT_IST,
  DASHBOARD_PHASE,
  DASHBOARD_PENDING_PHASES,
  DASHBOARD_PHASE_LABEL,
} from './version.js?v=6.4.0-index-pine-drag';

function istTime() {
  const now = new Date(Date.now() + 5.5 * 3600000);
  const hh = String(now.getUTCHours()).padStart(2, '0');
  const mm = String(now.getUTCMinutes()).padStart(2, '0');
  const ss = String(now.getUTCSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss} IST`;
}

function marketSession() {
  const now = new Date(Date.now() + 5.5 * 3600000);
  const total = now.getUTCHours() * 60 + now.getUTCMinutes();
  if (total >= 9 * 60 + 15 && total < 15 * 60 + 30) return 'OPEN';
  if (total >= 9 * 60 && total < 9 * 60 + 15) return 'PRE';
  if (total >= 15 * 60 + 30 && total < 16 * 60) return 'POST';
  return 'CLOSED';
}

function ageLabel(ageMs) {
  if (ageMs < 0) return '-';
  const s = Math.floor(ageMs / 1000);
  if (s < 5) return 'Live';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  const h = Math.floor(s / 3600);
  if (h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export class Footer {
  constructor(containerEl) {
    this._el = containerEl;
    this._clockInterval = null;
    this._unsubs = [];
    this._diag = {};
    this._health = {};
    this._optionQueue = {};
    this._lastAuthPromptKey = '';
  }

  init() {
    this._render();

    this._unsubs.push(api.subscribe('/api/diagnostics', (resp) => {
      if (resp) {
        this._diag = resp;
        this._updateStats();
      }
    }, 5000));

    this._unsubs.push(api.subscribe('/api/health', (resp) => {
      if (resp?.services) {
        this._health = resp.services;
        this._updateHealth();
      }
    }, 5000));

    this._unsubs.push(api.subscribe('/api/options/queue/status', (resp) => {
      if (resp) {
        this._optionQueue = resp;
        this._updateOptionQueue();
      }
    }, 10000));

    document.addEventListener('scanner:count', (e) => this._setVal('ftSymbols', e.detail));
    document.addEventListener('signals:count', (e) => this._setVal('ftSignals', e.detail));
    document.addEventListener('watchlist:count', (e) => this._setVal('ftWatch', e.detail));

    this._clockInterval = setInterval(() => {
      this._setVal('ftClock', istTime());
      const stats = ws.stats;
      const wsDot = document.getElementById('ftWsDot');
      if (wsDot) wsDot.className = 'health-dot ' + (stats.connected ? 'healthy' : 'unhealthy');
    }, 1000);
  }

  _render() {
    const session = marketSession();
    const sessColor = session === 'OPEN' ? 'var(--green)' : session === 'PRE' ? 'var(--gold)' : 'var(--text-disabled)';

    this._el.innerHTML = `
      <div class="footer-stat" title="Current IST time">
        <span class="value" id="ftClock" style="font-family:var(--font-mono)">${istTime()}</span>
      </div>
      <div class="footer-stat" title="NSE market session">
        <span style="color:${sessColor};font-weight:600;font-size:10px" id="ftSession">${session}</span>
      </div>
      <div class="footer-stat" title="Upstox WebSocket: is live data flowing?">
        Upstox <span class="health-dot unknown" id="ftKiteDot"></span>
        <span class="value" id="ftKiteState" style="font-size:9px">-</span>
      </div>
      <div class="footer-stat" title="Age of most recent feature data in Redis">
        Data <span class="value" id="ftDataAge" style="font-size:9px">-</span>
      </div>
      <div class="footer-stat">Syms <span class="value" id="ftSymbols">-</span></div>
      <div class="footer-stat">Sectors <span class="value" id="ftSectors">-</span></div>
      <div class="footer-stat">Signals <span class="value" id="ftSignals">0</span></div>
      <div class="footer-stat">Watch <span class="value" id="ftWatch">-</span></div>
      <div class="footer-stat" title="Smart option-chain queue: refreshed / candidates / failures">ChainQ <span class="value" id="ftChainQ">-</span></div>
      <div class="footer-stat">WS <span class="health-dot unknown" id="ftWsDot"></span></div>
      <div class="footer-stat">API <span class="health-dot unknown" id="ftApiDot"></span></div>
      <div class="footer-stat">Feature <span class="health-dot unknown" id="ftFeatureDot"></span></div>
      <div class="footer-stat footer-phase" title="${DASHBOARD_PHASE_LABEL}">
        ${DASHBOARD_PHASE} - Pending <span class="value">${DASHBOARD_PENDING_PHASES}</span>
      </div>
      <div class="footer-stat footer-version" title="Dashboard build/version">
        ${DASHBOARD_VERSION} - Updated ${DASHBOARD_UPDATED_AT_IST}
      </div>
    `;
  }

  _setVal(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  _updateStats() {
    const d = this._diag;
    this._setVal('ftSectors', d.sectors_loaded || 0);
    const apiDot = document.getElementById('ftApiDot');
    if (apiDot) apiDot.className = 'health-dot healthy';
  }

  _updateOptionQueue() {
    const q = this._optionQueue || {};
    const el = document.getElementById('ftChainQ');
    if (!el) return;
    if (q.enabled === false) {
      el.textContent = 'OFF';
      el.style.color = 'var(--red)';
      return;
    }
    const refreshed = Number(q.refreshed_count || 0);
    const candidates = Number(q.candidate_count || 0);
    const failed = Number(q.failed_count || 0);
    el.textContent = `${refreshed}/${candidates}${failed ? ` !${failed}` : ''}`;
    el.style.color = failed ? 'var(--gold)' : refreshed ? 'var(--green)' : 'var(--text-muted)';
  }

  _updateHealth() {
    const h = this._health;

    const featDot = document.getElementById('ftFeatureDot');
    if (featDot && h['feature-engine']) {
      featDot.className = 'health-dot ' + (h['feature-engine'].status === 'healthy' ? 'healthy' : 'unhealthy');
    }

    const kiteDot = document.getElementById('ftKiteDot');
    const kiteState = document.getElementById('ftKiteState');
    const ing = h.ingestion;
    if (ing) {
      const isLive = ing.state === 'streaming' && Number(ing.last_tick_age_ms || 999999) < 10000;
      const isStale = ing.state === 'disconnected';
      if (kiteDot) kiteDot.className = 'health-dot ' + (isLive ? 'healthy' : isStale ? 'unhealthy' : 'unknown');
      if (kiteState) {
        const auth = ing.auth_error ? String(ing.auth_error).replace(/_/g, ' ') : '';
        kiteState.textContent = isLive
          ? `${(ing.tick_count || 0).toLocaleString()} ticks`
          : auth ? auth.toUpperCase()
          : ing.state ? String(ing.state).toUpperCase()
          : 'No token';
        kiteState.style.color = isLive ? 'var(--green)' : 'var(--red)';
        if (ing.token_expiry_ist) {
          kiteState.title = `Token source: ${ing.token_source || '-'} - Expires: ${ing.token_expiry_ist}`;
        }
      }

      const authError = String(ing.auth_error || '');
      const needsToken = ['expired_token', 'invalid_token', 'missing_token'].includes(authError);
      if (needsToken) {
        const promptKey = `${authError}:${ing.token_expiry_ts || ''}:${ing.state || ''}`;
        if (promptKey !== this._lastAuthPromptKey) {
          this._lastAuthPromptKey = promptKey;
          document.dispatchEvent(new CustomEvent('auth:upstox-needs-token', {
            detail: {
              token_state: authError.replace('_token', ''),
              auth_error: authError,
              expiry_ts: ing.token_expiry_ts || 0,
              expiry_ist: ing.token_expiry_ist || '',
              source: ing.token_source || '',
              ingestion_state: ing.state || '',
            },
          }));
        }
      }
    }

    const ageMs = h.ingestion?.last_tick_age_ms;
    const ageEl = document.getElementById('ftDataAge');
    if (ageEl && ageMs !== undefined) {
      const label = ageMs < 0 ? 'Stale / re-login' : ageLabel(ageMs);
      ageEl.textContent = label;
      ageEl.style.color = ageMs < 0 ? 'var(--red)' :
        ageMs < 5000 ? 'var(--green)' :
        ageMs < 60000 ? 'var(--gold)' : 'var(--red)';
    }

    const session = marketSession();
    const sessEl = document.getElementById('ftSession');
    if (sessEl) {
      sessEl.textContent = session;
      sessEl.style.color = session === 'OPEN' ? 'var(--green)' :
        session === 'PRE' ? 'var(--gold)' : 'var(--text-disabled)';
    }
  }

  destroy() {
    if (this._clockInterval) clearInterval(this._clockInterval);
    this._unsubs.forEach(fn => fn());
  }
}
