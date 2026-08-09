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
  }

  init() {
    this._wireMuteButton();
    this._unsub = api.subscribe('/api/signals', (resp) => this._onSignals(resp), 2000);
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
  }
}
