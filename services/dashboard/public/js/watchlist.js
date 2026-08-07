/**
 * Pre-Breakout Watchlist — identify the next breakout before it fires.
 */
import { api } from './api.js';
import { formatPrice, escapeHtml } from './utils.js';

const DASH = '—';

// Light-theme colours (the rest of the dashboard moved to a light chrome
// tonight; these were still the old dark-navy-card palette, which is what
// read as "wrong color" sitting inside an otherwise light page).
const STATE_META = {
  coiled: { label: 'COILED', color: '#15803d', bg: '#dcfce7', icon: '🔥', priority: 4 },
  accumulating: { label: 'ACCUM', color: '#1d4ed8', bg: '#dbeafe', icon: '📈', priority: 3 },
  compressing: { label: 'COMP', color: '#a16207', bg: '#fef9c3', icon: '🗜️', priority: 2 },
  triggered: { label: 'TRIGGERED', color: '#a21caf', bg: '#fae8ff', icon: '⚡', priority: 5 },
  idle: { label: 'IDLE', color: '#475569', bg: '#f1f5f9', icon: '', priority: 1 },
};

function convClass(score) {
  if (score >= 95) return 'conv-95';
  if (score >= 85) return 'conv-85';
  if (score >= 75) return 'conv-75';
  if (score >= 65) return 'conv-65';
  return '';
}

function durationStr(sec) {
  if (!sec || Number(sec) <= 0) return DASH;
  const s = Math.round(Number(sec));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function compressionPct(bbWidth) {
  if (!bbWidth || bbWidth <= 0) return 0;
  const maxWidth = 0.012;
  const comp = Math.max(0, (1 - bbWidth / maxWidth)) * 100;
  return Math.min(Math.round(comp), 100);
}

function prettyReason(reason) {
  const text = String(reason || '');

  let m = text.match(/^extreme_compression_bb_([\d.]+)_vol_([\d.]+x)_rsi_([\d.]+)$/);
  if (m) return `Extreme compression · BB ${m[1]} · RelVol ${m[2]} · RSI ${m[3]}`;

  m = text.match(/^vol_rising_([\d.]+x)_while_compressed_([\d.]+)$/);
  if (m) return `Volume rising ${m[1]} while compressed · BB ${m[2]}`;

  m = text.match(/^bb_declining_(\d+)_ticks_width_([\d.]+)$/);
  if (m) return `Bollinger width declining ${m[1]} candles · Width ${m[2]}`;

  if (text === 'breakout_signal_fired') return 'Breakout signal fired';

  return text
    .replace(/_/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/\bbb\b/gi, 'BB')
    .replace(/\brsi\b/gi, 'RSI')
    .trim();
}

export class WatchlistPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._items = [];
    this._sectorStrength = new Map();
    this._unsubs = [];
  }

  init() {
    this._unsubs.push(api.subscribe('/api/sectors', (resp) => {
      this._sectorStrength.clear();
      if (resp && resp.sectors) {
        for (const s of resp.sectors) {
          if (s.sector_id) this._sectorStrength.set(s.sector_id, s.strength_score || 0);
        }
      }
    }, 10000));

    this._unsubs.push(api.subscribe('/api/prebreakout', (resp) => {
      this._items = resp?.watchlist || [];
      this._render();
      document.dispatchEvent(new CustomEvent('watchlist:count', { detail: this._items.length }));
    }, 3000));

    this._render();
  }

  _render() {
    const active = this._items
      .filter(w => w.state && w.state !== 'idle' && w.state !== 'expired')
      .sort((a, b) => {
        const pa = (STATE_META[a.state] || STATE_META.idle).priority;
        const pb = (STATE_META[b.state] || STATE_META.idle).priority;
        if (pa !== pb) return pb - pa;
        return (b.readiness_score || 0) - (a.readiness_score || 0);
      });

    if (active.length === 0) {
      this._el.innerHTML = `
        <div class="panel-empty" style="flex-direction:column;gap:6px;padding:16px">
          <span style="font-size:20px;opacity:.4">👁️</span>
          <span>No pre-breakout setups</span>
        </div>`;
      return;
    }

    this._el.innerHTML = active.map(w => this._renderCard(w)).join('');

    this._el.querySelectorAll('.watchlist-row').forEach(card => {
      card.addEventListener('click', () => {
        const sym = card.dataset.sym;
        document.dispatchEvent(new CustomEvent('chart:load', { detail: { symbol: sym } }));
      });
    });
  }

  _renderCard(w) {
    const state = (w.state || 'idle').toLowerCase();
    const meta = STATE_META[state] || STATE_META.idle;
    const readiness = Math.round(Number(w.readiness_score || 0));
    const conv = Math.round(Number(w.conviction_score || 0));
    const rvol = Number(w.rel_vol || 0);
    const rsi = Number(w.rsi || 0);
    const bbWidth = Number(w.bb_width || 0);
    const comp = compressionPct(bbWidth);
    const sector = w.sector_id || DASH;
    const dur = durationStr(w.duration_sec);
    const hasSignal = Boolean(w.has_signal);

    const secStrength = this._sectorStrength.get(sector) || 0;
    const secStrColor = secStrength >= 70 ? '#15803d' : secStrength >= 50 ? '#a16207' : '#b91c1c';
    const rColor = readiness >= 75 ? '#15803d' : readiness >= 50 ? '#a16207' : '#c2410c';
    const cc = convClass(conv);
    const triggerPrice = hasSignal && w.entry_price > 0 ? formatPrice(w.entry_price) : DASH;
    const reasonText = w.transition_reason ? prettyReason(w.transition_reason) : '';

    const gradeHtml = w.conviction_grade
      ? `<span class="grade-chip grade-${(w.conviction_grade || '').toLowerCase().replace('+', 'plus')}">${escapeHtml(w.conviction_grade)}</span>`
      : '';

    // Single row per stock -- everything visible at a glance, no
    // stacked/wrapped card. Full reasoning still available on hover.
    return `
    <div class="watchlist-row ${cc}" data-sym="${escapeHtml(w.symbol)}" title="${escapeHtml(reasonText)}">
      <span class="wl-state-badge" style="background:${meta.bg};color:${meta.color}">${meta.icon} ${meta.label}</span>
      <span class="wl-symbol">${escapeHtml(w.symbol)}</span>
      ${gradeHtml}
      <span class="wl-cell"><label>Sector</label><b>${escapeHtml(sector)}</b></span>
      <span class="wl-cell"><label>Ready</label><b style="color:${rColor}">${readiness}</b></span>
      <span class="wl-cell"><label>Conv</label><b class="${cc}">${conv > 0 ? conv : DASH}</b></span>
      <span class="wl-cell"><label>Trigger</label><b class="${hasSignal ? 'positive' : ''}">${triggerPrice}</b></span>
      <span class="wl-cell"><label>RelVol</label><b class="${rvol > 1.5 ? 'positive' : ''}">${rvol > 0 ? rvol.toFixed(1) + 'x' : DASH}</b></span>
      <span class="wl-cell"><label>Compress</label><b class="${comp > 70 ? 'positive' : ''}">${comp}%</b></span>
      <span class="wl-cell"><label>RSI</label><b>${rsi > 0 ? rsi.toFixed(1) : DASH}</b></span>
      <span class="wl-cell"><label>Time</label><b>${dur}</b></span>
      ${secStrength > 0 ? `<span class="wl-cell"><label>Sector Str</label><b style="color:${secStrColor}">${Math.round(secStrength)}</b></span>` : ''}
    </div>`;
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
  }
}
