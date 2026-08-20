/**
 * Command Center v2 — New shell's Signal Cockpit, rebuilt to the approved
 * mockup's deck-card visual (gauge arc, sparkline, verdict word, level
 * grid). Same data source as the Classic cockpit (cockpit.js) --
 * /api/signals, unchanged fields, same frozen-at-signal-time guarantee --
 * this file only changes how it's drawn, not what it shows. Mark Taken
 * reuses the exact lookup+POST sequence scanner.js's _logPaperTrade()
 * already uses (/api/journal/trades -> find NOT_REVIEWED row for the
 * symbol -> POST .../discretion), not a second, possibly-diverging path.
 */
import { api } from './api.js';
import { formatPrice, escapeHtml } from './utils.js';

const DASH = '—';
const GRADE_TONE = { 'A+': 'var(--ifx-bull)', A: 'var(--ifx-info)', 'B+': 'var(--ifx-warn)', B: 'var(--ifx-shell-text-muted)' };

function ageStr(createdUs) {
  if (!createdUs) return '';
  const ageSec = Math.max(0, Math.floor((Date.now() - Number(createdUs) / 1000) / 1000));
  if (ageSec < 60) return `${ageSec}s ago`;
  const ageMin = Math.floor(ageSec / 60);
  if (ageMin < 60) return `${ageMin}m ago`;
  return `${Math.floor(ageMin / 60)}h ago`;
}

function signalTime(createdUs) {
  if (!createdUs) return '';
  const d = new Date(Number(createdUs) / 1000);
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
}

function sparkPath(vals, w, h) {
  if (!vals || vals.length < 2) return '';
  const min = Math.min(...vals), max = Math.max(...vals);
  return vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * w;
    const y = h - ((v - min) / ((max - min) || 1)) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
}

// Clean Sweep LC-2: the score was previously a circular gauge (SVG ring
// + centered number) -- a legitimate design element, but the reference
// screenshot's whole visual language is number-forward and reads faster
// at a glance than a dial does; a beginner shouldn't need to parse a
// ring fill to know "how good is this." Replaced with a plain, bold,
// tone-colored score badge -- same information, less to visually parse.
function scoreBadge(score, glowVar) {
  return `
    <div class="ifx-deck-score">
      <b style="color:${glowVar}">${Math.round(score)}</b><span>score</span>
    </div>`;
}

export class CockpitV2Panel {
  constructor(containerEl) {
    this._el = containerEl;
    this._signals = [];
    this._sparkCache = new Map(); // symbol -> closes[], one-shot per symbol per session
    this._unsubs = [];
  }

  init() {
    if (!this._el) return;
    this._el.classList.add('ifx-deck-wrap');
    this._unsubs.push(api.subscribe('/api/signals', (resp) => {
      this._signals = resp?.signals || [];
      this._render();
    }, 2000));
    this._render();
  }

  _render() {
    if (!this._el) return;
    if (this._signals.length === 0) {
      this._el.innerHTML = `
        <div class="ifx-deck-empty">
          <span class="ifx-deck-empty-icon">◎</span>
          <span class="ifx-deck-empty-title">No active signals right now</span>
          <span class="ifx-deck-empty-sub">Nothing meets the conviction bar — that's the point.</span>
        </div>`;
      return;
    }
    const ranked = [...this._signals].sort((a, b) => Number(b.conviction_score || 0) - Number(a.conviction_score || 0));
    this._el.innerHTML = `<div class="ifx-deck">${ranked.map((s, i) => this._renderCard(s, i + 1)).join('')}</div>`;

    this._el.querySelectorAll('[data-deck-sym]').forEach((card) => {
      const symbol = card.dataset.deckSym;
      const sig = ranked.find((s) => s.symbol === symbol);
      card.querySelector('[data-deck-chart]')?.addEventListener('click', () => {
        if (!sig) return;
        document.dispatchEvent(new CustomEvent('signal:select', { detail: sig }));
        document.dispatchEvent(new CustomEvent('chart:load', { detail: { symbol, signal: sig } }));
      });
      card.querySelector('[data-deck-mark]')?.addEventListener('click', (e) => this._markTaken(symbol, e.currentTarget));
      this._loadSpark(symbol, card.querySelector('.ifx-deck-spark'));
    });
  }

  async _loadSpark(symbol, container) {
    if (!container) return;
    if (this._sparkCache.has(symbol)) {
      this._paintSpark(container, this._sparkCache.get(symbol));
      return;
    }
    try {
      const resp = await api.fetch(`/api/chart/${encodeURIComponent(symbol)}/intraday?interval=15m`);
      const closes = (resp?.bars || []).slice(-16).map((b) => Number(b.close)).filter((n) => !Number.isNaN(n));
      this._sparkCache.set(symbol, closes);
      this._paintSpark(container, closes);
    } catch (_) {
      // No sparkline is fine -- the card still works without it.
    }
  }

  _paintSpark(container, closes) {
    if (!container || closes.length < 2) return;
    const w = 300, h = 34;
    const up = closes[closes.length - 1] >= closes[0];
    container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <polyline points="${sparkPath(closes, w, h)}" fill="none" stroke="${up ? 'var(--ifx-bull)' : 'var(--ifx-bear)'}" stroke-width="2"/>
    </svg>`;
  }

  async _markTaken(symbol, btn) {
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Marking…';
    try {
      const data = await api.fetch('/api/journal/trades?limit=200');
      const rows = Array.isArray(data?.trades) ? data.trades : [];
      const row = rows.find((x) =>
        String(x.symbol || '').toUpperCase() === symbol.toUpperCase() &&
        String(x.discretionary_action || 'NOT_REVIEWED').toUpperCase() === 'NOT_REVIEWED'
      );
      if (!row?.id) {
        btn.textContent = 'No auto row';
        setTimeout(() => { btn.disabled = false; btn.textContent = original; }, 1800);
        return;
      }
      const res = await api.post(`/api/journal/trades/${encodeURIComponent(row.id)}/discretion`, { discretionary_action: 'TAKEN' });
      if (res?.ok) {
        btn.textContent = 'Marked Taken';
        document.dispatchEvent(new CustomEvent('journal:refresh', { detail: res.trade }));
        setTimeout(() => { btn.disabled = false; btn.textContent = original; }, 1600);
      } else {
        btn.textContent = 'Log failed';
        setTimeout(() => { btn.disabled = false; btn.textContent = original; }, 1800);
      }
    } catch (_) {
      btn.textContent = 'Log failed';
      setTimeout(() => { btn.disabled = false; btn.textContent = original; }, 1800);
    }
  }

  _renderCard(sig, rank) {
    const score = Number(sig.conviction_score || 0);
    const grade = sig.conviction_grade || DASH;
    const glow = GRADE_TONE[grade] || 'var(--ifx-shell-text-muted)';
    const entry = Number(sig.entry_price || 0);
    const stop = Number(sig.invalidation_price || 0);
    const target = Number(sig.target_price || 0);
    const isBull = (sig.signal_type || 'bullish') === 'bullish';
    const side = isBull ? 'bull' : 'bear';
    const label = sig.option_bias || (isBull ? 'BUY CE' : 'BUY PE');
    const fs = sig.features_snapshot && typeof sig.features_snapshot === 'object' ? sig.features_snapshot : {};
    const t2 = Number(fs.t2_price || 0);
    const rr = Number(sig.risk_reward_ratio || 0);
    const sector = sig.sector_id || DASH;

    return `
    <div class="ifx-deck-card ifx-deck-card--${side}" data-deck-sym="${escapeHtml(sig.symbol)}">
      <div class="ifx-deck-top">
        <div><div class="ifx-deck-symbol">${escapeHtml(sig.symbol)}</div><div class="ifx-deck-sector">${escapeHtml(sector.replace(/_/g, ' '))}</div></div>
        <div class="ifx-deck-lock" title="Frozen at signal time — never recalculated as price moves">🔒 as of ${signalTime(sig.created_at_us)} · ${ageStr(sig.created_at_us)}</div>
      </div>
      <div class="ifx-deck-mid">
        ${scoreBadge(score, glow)}
        <div class="ifx-deck-verdict"><div class="ifx-deck-verdict-word ifx-deck-verdict-word--${side}">${escapeHtml(label)}</div>
          <div class="ifx-deck-verdict-reason">Grade ${escapeHtml(grade)} · Rank #${rank}${rr ? ` · R:R ${rr.toFixed(1)}:1` : ''}</div></div>
      </div>
      <div class="ifx-deck-levels">
        <div class="ifx-deck-lvl"><label>Entry</label><b>${formatPrice(entry)}</b></div>
        <div class="ifx-deck-lvl"><label>Stop</label><b class="ifx-tone-bad">${formatPrice(stop)}</b></div>
        <div class="ifx-deck-lvl"><label>T1</label><b class="ifx-tone-good">${formatPrice(target)}</b></div>
        <div class="ifx-deck-lvl"><label>T2</label><b class="ifx-tone-good">${t2 > 0 ? formatPrice(t2) : DASH}</b></div>
      </div>
      <div class="ifx-deck-spark"></div>
      <div class="ifx-deck-foot">
        <span class="ifx-deck-foot-note">${escapeHtml(sig.pre_breakout_state || sig.market_regime || '')}</span>
        <div class="ifx-deck-actions">
          <button type="button" class="ifx-btn ifx-btn--on-paper" data-deck-chart>Chart</button>
          <button type="button" class="ifx-btn ifx-deck-mark-btn" data-deck-mark>Mark Taken</button>
        </div>
      </div>
    </div>`;
  }

  destroy() {
    this._unsubs.forEach((fn) => fn());
  }
}
