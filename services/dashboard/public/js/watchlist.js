/**
 * Pre-Breakout Watchlist — identify the next breakout before it fires.
 */
import { api } from './api.js';
import { formatPrice, escapeHtml } from './utils.js';

const DASH = '—';

const STATE_META = {
  coiled: { label: 'COILED', color: '#4ade80', bg: '#14532d', icon: '🔥', priority: 4 },
  accumulating: { label: 'ACCUM', color: '#60a5fa', bg: '#1e3a5f', icon: '📈', priority: 3 },
  compressing: { label: 'COMP', color: '#facc15', bg: '#2d2a14', icon: '🗜️', priority: 2 },
  triggered: { label: 'TRIGGERED', color: '#e879f9', bg: '#3b0764', icon: '⚡', priority: 5 },
  idle: { label: 'IDLE', color: '#94a3b8', bg: '#1e293b', icon: '', priority: 1 },
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

    this._el.querySelectorAll('.watchlist-card').forEach(card => {
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
    const secStrColor = secStrength >= 70 ? '#4ade80' : secStrength >= 50 ? '#fbbf24' : '#f87171';
    const rColor = readiness >= 75 ? '#4ade80' : readiness >= 50 ? '#facc15' : '#f97316';
    const cc = convClass(conv);
    const triggerPrice = hasSignal && w.entry_price > 0 ? formatPrice(w.entry_price) : DASH;

    const gradeHtml = w.conviction_grade
      ? `<span class="grade-chip grade-${(w.conviction_grade || '').toLowerCase().replace('+', 'plus')}">${escapeHtml(w.conviction_grade)}</span>`
      : '';

    return `
    <div class="watchlist-card ${cc}" data-sym="${escapeHtml(w.symbol)}" style="cursor:pointer">
      <div class="wl-header">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <span class="wl-state-badge" style="background:${meta.bg};color:${meta.color}">${meta.icon} ${meta.label}</span>
          <span class="wl-symbol">${escapeHtml(w.symbol)}</span>
          ${gradeHtml}
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:2px">
          <span class="wl-sector">${escapeHtml(sector)}</span>
          ${secStrength > 0 ? `<span style="font-size:9px;color:${secStrColor};font-weight:600">Str ${Math.round(secStrength)}</span>` : ''}
        </div>
      </div>

      <div class="wl-readiness">
        <div class="wl-readiness-track">
          <div class="wl-readiness-fill" style="width:${readiness}%;background:${rColor}"></div>
        </div>
        <span class="wl-readiness-val" style="color:${rColor}">${readiness}</span>
      </div>

      <div class="wl-stats">
        <div class="wl-stat">
          <span class="wl-stat-label">Conviction</span>
          <span class="wl-stat-val ${cc}">${conv > 0 ? conv : DASH}</span>
        </div>
        <div class="wl-stat">
          <span class="wl-stat-label">Trigger</span>
          <span class="wl-stat-val ${hasSignal ? 'positive' : ''}">${triggerPrice}</span>
        </div>
        <div class="wl-stat">
          <span class="wl-stat-label">RelVol</span>
          <span class="wl-stat-val ${rvol > 1.5 ? 'positive' : ''}">${rvol > 0 ? rvol.toFixed(1) + 'x' : DASH}</span>
        </div>
        <div class="wl-stat">
          <span class="wl-stat-label">Compress</span>
          <span class="wl-stat-val ${comp > 70 ? 'positive' : ''}">${comp}%</span>
        </div>
        <div class="wl-stat">
          <span class="wl-stat-label">RSI</span>
          <span class="wl-stat-val">${rsi > 0 ? rsi.toFixed(1) : DASH}</span>
        </div>
        <div class="wl-stat">
          <span class="wl-stat-label">Time</span>
          <span class="wl-stat-val">${dur}</span>
        </div>
      </div>

      ${w.transition_reason ? `<div class="wl-reason" title="${escapeHtml(prettyReason(w.transition_reason))}">${escapeHtml(prettyReason(w.transition_reason))}</div>` : ''}
    </div>`;
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
  }
}
