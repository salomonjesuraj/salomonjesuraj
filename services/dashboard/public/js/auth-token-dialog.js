/**
 * Upstox Token Dialog
 * Shows a blocking-but-friendly recovery popup when the broker token expires.
 */
import { api } from './api.js';

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

export class AuthTokenDialog {
  constructor() {
    this._el = null;
    this._status = null;
    this._visible = false;
    this._unsub = null;
    this._poll = null;
  }

  init() {
    this._build();
    this._bind();
    this._pollStatus();
    this._poll = setInterval(() => this._pollStatus(), 10000);

    document.addEventListener('auth:upstox-needs-token', (e) => {
      this._status = e.detail || this._status;
      this.show();
    });
  }

  _build() {
    const root = document.createElement('div');
    root.className = 'auth-modal';
    root.id = 'upstoxAuthModal';
    root.setAttribute('aria-hidden', 'true');
    root.innerHTML = `
      <div class="auth-modal-backdrop"></div>
      <section class="auth-modal-card" role="dialog" aria-modal="true" aria-labelledby="authModalTitle">
        <div class="auth-modal-head">
          <div>
            <span class="auth-kicker">Broker recovery</span>
            <h2 id="authModalTitle">Upstox token expired</h2>
          </div>
          <button class="auth-close" type="button" data-auth-close aria-label="Close">×</button>
        </div>
        <div class="auth-state" id="authStateText">Checking token status...</div>
        <label class="auth-label" for="upstoxAccessToken">Paste fresh Upstox access token</label>
        <textarea id="upstoxAccessToken" class="auth-token-input" spellcheck="false" autocomplete="off"
          placeholder="Paste access token here..."></textarea>
        <div class="auth-actions">
          <button type="button" class="auth-primary" id="saveUpstoxToken">Save token + recheck</button>
          <button type="button" class="auth-secondary" id="recheckUpstox">Recheck now</button>
          <a class="auth-secondary auth-link" href="http://localhost:5100/auth/login" target="_blank" rel="noreferrer">Open Upstox login</a>
        </div>
        <div class="auth-help">
          After saving, ingestion wakes up immediately, reads Redis token, reconnects Upstox feed, and dashboard data should resume.
        </div>
        <div class="auth-message" id="authMessage"></div>
      </section>
    `;
    document.body.appendChild(root);
    this._el = root;
  }

  _bind() {
    this._el.querySelector('[data-auth-close]').addEventListener('click', () => this.hide());
    this._el.querySelector('.auth-modal-backdrop').addEventListener('click', () => this.hide());
    this._el.querySelector('#saveUpstoxToken').addEventListener('click', () => this._saveToken());
    this._el.querySelector('#recheckUpstox').addEventListener('click', () => this._recheck());
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this._visible) this.hide();
    });
  }

  async _pollStatus() {
    const status = await api.fetch('/api/auth/upstox/status');
    if (!status) return;
    this._status = status;
    this._renderStatus();
    if (status.needs_token) this.show();
    if (!status.needs_token && this._visible) this._setMessage('Token looks valid. Waiting for live ticks...', 'good');
  }

  _renderStatus() {
    const el = this._el?.querySelector('#authStateText');
    if (!el) return;
    const s = this._status || {};
    const expiry = s.expiry_ist ? ` · Expired/expiry: ${s.expiry_ist}` : '';
    const state = s.ingestion_state ? ` · Ingestion: ${String(s.ingestion_state).toUpperCase()}` : '';
    el.innerHTML = `
      <b>${escapeHtml(String(s.token_state || 'checking').toUpperCase())}</b>
      <span>${escapeHtml(String(s.auth_error || ''))}${escapeHtml(expiry)}${escapeHtml(state)}</span>
    `;
  }

  async _saveToken() {
    const textarea = this._el.querySelector('#upstoxAccessToken');
    const token = textarea.value.trim();
    if (!token) {
      this._setMessage('Paste the access token first.', 'bad');
      return;
    }
    this._setBusy(true);
    this._setMessage('Saving token and triggering Upstox recheck...', 'wait');
    const resp = await api.post('/api/auth/upstox/token', { access_token: token });
    this._setBusy(false);
    if (!resp?.ok) {
      this._setMessage(resp?.error || 'Token save failed. Please verify token and try again.', 'bad');
      return;
    }
    textarea.value = '';
    this._setMessage(`Saved. Token expiry: ${resp.expiry_ist || 'unknown'}. Recheck triggered.`, 'good');
    setTimeout(() => this._pollStatus(), 1500);
  }

  async _recheck() {
    this._setMessage('Requesting ingestion recheck...', 'wait');
    const resp = await api.post('/api/auth/upstox/recheck', {});
    this._setMessage(resp?.ok ? 'Recheck requested. Watch footer for Upstox live ticks.' : 'Recheck request failed.', resp?.ok ? 'good' : 'bad');
    setTimeout(() => this._pollStatus(), 1500);
  }

  _setBusy(isBusy) {
    this._el.querySelectorAll('button').forEach(btn => {
      if (!btn.matches('[data-auth-close]')) btn.disabled = isBusy;
    });
  }

  _setMessage(text, tone = 'wait') {
    const el = this._el.querySelector('#authMessage');
    el.className = `auth-message ${tone}`;
    el.textContent = text;
  }

  show() {
    if (!this._el) return;
    this._visible = true;
    this._el.classList.add('open');
    this._el.setAttribute('aria-hidden', 'false');
    this._renderStatus();
  }

  hide() {
    if (!this._el) return;
    this._visible = false;
    this._el.classList.remove('open');
    this._el.setAttribute('aria-hidden', 'true');
  }

  destroy() {
    if (this._poll) clearInterval(this._poll);
    if (this._el) this._el.remove();
  }
}
