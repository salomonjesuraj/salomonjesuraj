/**
 * Pre-Breakout Watchers strip — New shell's compact companion to Command
 * Center, per the approved mockup (quieter than a confirmed deck card:
 * dashed border, amber "state" tag, no bull/bear glow, since nothing here
 * is a confirmed call yet). Same /api/prebreakout + /api/sectors sources
 * watchlist.js already polls -- this only changes the rendering, not the
 * data or the state taxonomy (coiled/accumulating/compressing/triggered).
 *
 * Mathematical-audit-follow-up UI work (2026-08-25): each visible card
 * (capped at 8, same as before) also pulls GET /api/trade-blueprint/
 * {symbol} for retest_status and GET /api/mtf/{symbol} for the 1M/5M/
 * 15M/1H dot row -- both fetched once per symbol per session and
 * cached, same bounded-fan-out pattern cockpit-v2.js's own per-card
 * fetches already use. Deliberately NOT done for the full 208-symbol
 * Breakout Radar table below -- that fan-out would be 208 extra
 * requests per poll cycle, a real cost this compact, <=8-card strip
 * doesn't have.
 */
import { api } from './api.js';
import { formatPrice, escapeHtml } from './utils.js';

const DASH = '—';
const STATE_LABEL = {
  coiled: 'COILED', accumulating: 'ACCUM', compressing: 'COMPRESSING', triggered: 'TRIGGERED',
};
const STATE_PRIORITY = { triggered: 5, coiled: 4, accumulating: 3, compressing: 2, idle: 1 };

const RETEST_LABEL = {
  NO_BREAKOUT: 'NO BREAKOUT', PENDING_RETEST: 'PENDING RETEST', RETEST_HELD: 'RETEST HELD', RETEST_FAILED: 'RETEST FAILED',
};
const RETEST_BADGE_TONE = {
  NO_BREAKOUT: 'neutral', PENDING_RETEST: 'warn', RETEST_HELD: 'bull', RETEST_FAILED: 'bear',
};
const MTF_DOT_TFS = ['1M', '5M', '15M', '1H'];
const DOT_COLOR = { G: 'var(--ifx-bull)', R: 'var(--ifx-bear)', Y: 'var(--ifx-warn)' };

export class WatchStripV2Panel {
  constructor(containerEl) {
    this._el = containerEl;
    this._items = [];
    this._unsubs = [];
    this._retestCache = new Map(); // symbol -> retest_status
    this._mtfCache = new Map(); // symbol -> dots
  }

  init() {
    if (!this._el) return;
    this._el.classList.add('ifx-watch-strip-wrap');
    this._unsubs.push(api.subscribe('/api/prebreakout', (resp) => {
      this._items = resp?.watchlist || [];
      this._render();
    }, 3000));
    this._render();
  }

  _render() {
    const active = this._items
      .filter((w) => w.state && w.state !== 'idle' && w.state !== 'expired')
      .sort((a, b) => {
        const pa = STATE_PRIORITY[a.state] || 0, pb = STATE_PRIORITY[b.state] || 0;
        if (pa !== pb) return pb - pa;
        return (b.readiness_score || 0) - (a.readiness_score || 0);
      });

    const label = document.getElementById('watchCountV2');
    if (label) label.textContent = `${active.length} watching`;

    if (active.length === 0) {
      this._el.innerHTML = `<div class="ifx-watch-empty">No pre-breakout setups building right now.</div>`;
      return;
    }

    const shown = active.slice(0, 8);
    this._el.innerHTML = `<div class="ifx-watch-strip">${shown.map((w) => this._renderCard(w)).join('')}</div>`;
    this._el.querySelectorAll('[data-watch-sym]').forEach((card) => {
      const symbol = card.dataset.watchSym;
      card.addEventListener('click', () => {
        document.dispatchEvent(new CustomEvent('chart:load', { detail: { symbol } }));
      });
      this._loadRetest(symbol, card);
      this._loadMtf(symbol, card);
    });
  }

  async _loadRetest(symbol, card) {
    if (this._retestCache.has(symbol)) {
      this._paintRetest(card, this._retestCache.get(symbol));
      return;
    }
    try {
      const bp = await api.fetch(`/api/trade-blueprint/${encodeURIComponent(symbol)}`);
      const status = bp?.retest_status || 'NO_BREAKOUT';
      this._retestCache.set(symbol, status);
      this._paintRetest(card, status);
    } catch (_) {
      // Retest badge just stays absent -- the rest of the card still works.
    }
  }

  _paintRetest(card, status) {
    const slot = card?.querySelector('[data-watch-retest]');
    if (!slot) return;
    const tone = RETEST_BADGE_TONE[status] || 'neutral';
    slot.innerHTML = `<span class="ifx-badge ifx-badge--${tone}">${escapeHtml(RETEST_LABEL[status] || status.replace(/_/g, ' '))}</span>`;
  }

  async _loadMtf(symbol, card) {
    if (this._mtfCache.has(symbol)) {
      this._paintMtf(card, this._mtfCache.get(symbol));
      return;
    }
    try {
      const resp = await api.fetch(`/api/mtf/${encodeURIComponent(symbol)}`);
      // Live shape is resp.timeframes[tf].dot, not a flat resp.dots map --
      // verified 2026-08-25 against a real running /api/mtf/RELIANCE
      // response after the first-draft assumption below turned out wrong.
      const tfs = resp?.timeframes || null;
      const dots = tfs
        ? Object.fromEntries(Object.entries(tfs).map(([tf, v]) => [tf, v?.dot]))
        : null;
      this._mtfCache.set(symbol, dots);
      this._paintMtf(card, dots);
    } catch (_) {
      // MTF dot row just stays absent.
    }
  }

  _paintMtf(card, dots) {
    const slot = card?.querySelector('[data-watch-mtf]');
    if (!slot || !dots) return;
    slot.innerHTML = MTF_DOT_TFS.map((tf) => {
      const dot = dots[tf] || 'Y';
      return `<span class="ifx-watch-mtf-dot" style="background:${DOT_COLOR[dot] || DOT_COLOR.Y}" title="${tf}"></span>`;
    }).join('');
  }

  _renderCard(w) {
    const state = (w.state || 'idle').toLowerCase();
    const readiness = Math.max(0, Math.min(100, Math.round(Number(w.readiness_score || 0))));
    const sector = w.sector_id || DASH;
    const hasSignal = Boolean(w.has_signal);
    const triggerText = hasSignal && w.entry_price > 0 ? `Signal live at ${formatPrice(w.entry_price)}` : `${readiness}% ready`;
    return `
    <div class="ifx-watch-card" data-watch-sym="${escapeHtml(w.symbol)}">
      <div class="ifx-watch-top"><span class="ifx-watch-symbol">${escapeHtml(w.symbol)}</span>
        <span class="ifx-badge ifx-badge--warn">${STATE_LABEL[state] || state.toUpperCase()}</span></div>
      <div class="ifx-watch-progress"><i style="width:${readiness}%"></i></div>
      <div class="ifx-watch-meta-row">
        <span data-watch-retest class="ifx-watch-retest-slot"></span>
        <span data-watch-mtf class="ifx-watch-mtf-row" title="MTF alignment: 1M · 5M · 15M · 1H"></span>
      </div>
      <div class="ifx-watch-foot"><span>${escapeHtml(sector.replace(/_/g, ' '))}</span><b>${escapeHtml(triggerText)}</b></div>
    </div>`;
  }

  destroy() {
    this._unsubs.forEach((fn) => fn());
  }
}
