/**
 * Signal-fire alert — Phase N6, the user's pick from the suggestion list
 * (#3: "a soft audio/visual chime when a Pre-Breakout Watcher graduates
 * into a confirmed Command Center signal"). Watches the same /api/signals
 * poll cockpit-v2.js already subscribes to (deduped by api.js -- this
 * doesn't add a second poll) and diffs the symbol set: any symbol that
 * newly appears gets a toast + a synthesized two-tone Web Audio chime (no
 * audio file) + cockpit-v2.js's existing .just-fired glow class on its
 * card. Mute state persists to localStorage. New shell only -- Classic
 * has no equivalent and none of this touches it.
 *
 * Phase R9 -- extended to also watch /api/radar-alerts/recent (a
 * genuinely different, softer event than a real fired signal: the first
 * moment a symbol crosses into EARLY_WATCH-or-higher on the Breakout
 * Radar, see api/radar_alert_queue.py). Deliberately reuses this same
 * toast stack + chime plumbing rather than building a second mechanism,
 * but with its own icon/id-diffing so a "just entered early watch" toast
 * never gets confused with "a real signal just fired" -- the whole point
 * of R6's Contract Confirmation relabel and the radar/execution split
 * this session has kept consistent throughout.
 */
import { api } from './api.js';

const MUTE_KEY = 'infusion:new-shell:muted';

function loadMuted() {
  try { return localStorage.getItem(MUTE_KEY) === '1'; } catch (e) { return false; }
}

export class SignalAlertV2 {
  constructor() {
    this._known = new Set();
    this._first = true;
    this._muted = loadMuted();
    this._unsub = null;
    // Phase R9 -- separate diff-state for radar alerts, keyed by row id
    // (not symbol -- a symbol can fire, resolve, and fire again the same
    // day, and each is its own real ledger row worth its own toast).
    this._knownRadarAlertIds = new Set();
    this._firstRadar = true;
    this._unsubRadar = null;
  }

  init() {
    this._wireMuteButton();
    this._unsub = api.subscribe('/api/signals', (resp) => this._onSignals(resp), 2000);
    this._unsubRadar = api.subscribe('/api/radar-alerts/recent?limit=20', (resp) => this._onRadarAlerts(resp), 5000);
  }

  _wireMuteButton() {
    const btn = document.getElementById('soundToggleV2');
    if (!btn) return;
    this._paintMuteButton(btn);
    btn.addEventListener('click', () => {
      this._muted = !this._muted;
      try { localStorage.setItem(MUTE_KEY, this._muted ? '1' : '0'); } catch (e) { /* ignore */ }
      this._paintMuteButton(btn);
    });
  }

  _paintMuteButton(btn) {
    btn.textContent = this._muted ? '🔇' : '🔊';
    btn.title = this._muted ? 'Unmute signal-fire alerts' : 'Mute signal-fire alerts';
  }

  _onSignals(resp) {
    const signals = resp?.signals || [];
    const currentSymbols = new Set(signals.map((s) => s.symbol));

    if (this._first) {
      // Don't alert for signals that were already live before this page
      // loaded -- only genuinely NEW ones from here on.
      this._known = currentSymbols;
      this._first = false;
      return;
    }

    for (const sig of signals) {
      if (!this._known.has(sig.symbol)) {
        this._fire(sig);
      }
    }
    this._known = currentSymbols;
  }

  _onRadarAlerts(resp) {
    const alerts = Array.isArray(resp?.alerts) ? resp.alerts : [];
    const currentIds = new Set(alerts.map((a) => a.id));

    if (this._firstRadar) {
      // Same "don't alert for what was already sitting there before this
      // page loaded" rule as _onSignals.
      this._knownRadarAlertIds = currentIds;
      this._firstRadar = false;
      return;
    }

    for (const a of alerts) {
      if (!this._knownRadarAlertIds.has(a.id)) {
        this._fireRadarAlert(a);
      }
    }
    this._knownRadarAlertIds = currentIds;
  }

  _fireRadarAlert(alert) {
    this._toastRadar(alert);
    if (!this._muted) this._chime();
  }

  _toastRadar(alert) {
    const stack = document.getElementById('toastStackV2');
    if (!stack) return;
    const el = document.createElement('div');
    el.className = 'ifx-toast ifx-toast--radar';
    el.innerHTML = `<span class="ifx-toast-icon">📡</span><div class="ifx-toast-body"><b>${alert.symbol} entered ${(alert.tier_at_fire || '').replace(/_/g, ' ')}</b><span>Radar early alert · not a fired signal · see Breakout Radar</span></div>`;
    stack.appendChild(el);
    setTimeout(() => {
      el.classList.add('out');
      setTimeout(() => el.remove(), 220);
    }, 4200);
  }

  _fire(sig) {
    this._toast(sig);
    if (!this._muted) this._chime();
    const card = document.querySelector(`.ifx-deck-card[data-deck-sym="${CSS.escape(sig.symbol)}"]`);
    if (card) {
      card.classList.remove('just-fired');
      // Force reflow so re-adding the class restarts the animation even
      // if this symbol fired, expired, and fired again within one poll.
      void card.offsetWidth;
      card.classList.add('just-fired');
    }
  }

  _toast(sig) {
    const stack = document.getElementById('toastStackV2');
    if (!stack) return;
    const isBull = (sig.signal_type || 'bullish') === 'bullish';
    const label = sig.option_bias || (isBull ? 'BUY CE' : 'BUY PE');
    const el = document.createElement('div');
    el.className = 'ifx-toast';
    el.innerHTML = `<span class="ifx-toast-icon">🔔</span><div class="ifx-toast-body"><b>${sig.symbol} just triggered</b><span>${label} · Grade ${sig.conviction_grade || '-'}</span></div>`;
    stack.appendChild(el);
    setTimeout(() => {
      el.classList.add('out');
      setTimeout(() => el.remove(), 220);
    }, 4200);
  }

  _chime() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.type = 'sine';
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(1320, ctx.currentTime + 0.15);
      gain.gain.setValueAtTime(0.001, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.15, ctx.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.5);
      osc.start();
      osc.stop(ctx.currentTime + 0.5);
      osc.onended = () => ctx.close();
    } catch (e) { /* ignore -- silence is an acceptable degrade, not a crash */ }
  }

  destroy() {
    if (this._unsub) this._unsub();
    if (this._unsubRadar) this._unsubRadar();
  }
}
