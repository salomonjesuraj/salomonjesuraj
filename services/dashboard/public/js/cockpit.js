/**
 * Signal Cockpit — the ONE thing visible by default. v2 design system.
 *
 * A card is a "trade ticket": symbol + CE/PE direction, a dominant
 * conviction display (arc + big mono number + grade), the MTF colour-dot
 * row, a chaseable flag, and entry/SL/T1/T2/T3 — all frozen at signal
 * time (see api.js / scanner/engine.py; verified during the Phase A/B
 * work that these values never mutate after the signal fires). No prose
 * reasoning on the card — click through to Stock Detail in "More" for
 * the full explanation.
 */
import { api } from './api.js';
import { formatPrice, escapeHtml } from './utils.js';

const DASH = '—';
const MTF_ORDER = ['1M', '5M', '15M', '1H', '4H', '1D'];
const MTF_CLASS = { G: 'ifx-mtf-dot--bull', R: 'ifx-mtf-dot--bear', Y: 'ifx-mtf-dot--flat' };

// Single source of truth for grade → color, matching --ifx-grade-* tokens.
const GRADE_COLOR_VAR = {
  'A+': '--ifx-grade-aplus',
  A: '--ifx-grade-a',
  'B+': '--ifx-grade-bplus',
  B: '--ifx-grade-b',
};

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

/** Compact SVG arc gauge for the conviction score — the card's dominant
 * visual element, per the "easily visually convictioned" brief: readable
 * as a precise number AND as an at-a-glance shape/color. */
function scoreArc(score, colorVar) {
  const clamped = Math.max(0, Math.min(100, score));
  const r = 26;
  const circumference = 2 * Math.PI * r;
  const offset = circumference * (1 - clamped / 100);
  return `
    <svg class="ifx-score-arc" viewBox="0 0 64 64" width="64" height="64" role="img" aria-label="Conviction score ${Math.round(score)} of 100">
      <circle cx="32" cy="32" r="${r}" fill="none" stroke="var(--ifx-content-border)" stroke-width="5"/>
      <circle cx="32" cy="32" r="${r}" fill="none" stroke="var(${colorVar})" stroke-width="5"
        stroke-linecap="round" stroke-dasharray="${circumference.toFixed(1)}" stroke-dashoffset="${offset.toFixed(1)}"
        transform="rotate(-90 32 32)"/>
    </svg>`;
}

function gradeColorVar(grade) {
  return GRADE_COLOR_VAR[grade] || '--ifx-grade-c';
}

export class CockpitPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._signals = [];
    this._unsubs = [];
  }

  init() {
    if (!this._el) return;
    this._el.classList.add('ifx-shell', 'ifx-cockpit');
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
        <div class="ifx-cockpit-empty">
          <span class="ifx-cockpit-empty-icon">◎</span>
          <span class="ifx-cockpit-empty-title">No active signals right now</span>
          <span class="ifx-cockpit-empty-sub">Nothing meets the conviction bar — that's the point. Check "More" for the full screener.</span>
        </div>`;
      return;
    }

    const ranked = [...this._signals].sort(
      (a, b) => Number(b.conviction_score || 0) - Number(a.conviction_score || 0)
    );

    this._el.innerHTML = `<div class="ifx-cockpit-grid">${ranked.map((sig) => this._renderCard(sig)).join('')}</div>`;

    this._el.querySelectorAll('[data-cockpit-sym]').forEach((card) => {
      card.addEventListener('click', () => {
        const sym = card.dataset.cockpitSym;
        const sig = ranked.find((s) => s.symbol === sym);
        if (!sig) return;
        document.dispatchEvent(new CustomEvent('signal:select', { detail: sig }));
        document.dispatchEvent(new CustomEvent('chart:load', { detail: { symbol: sym, signal: sig } }));
      });
    });
  }

  _renderMtfRow(fs) {
    const dots = fs.mtf_dots && typeof fs.mtf_dots === 'object' ? fs.mtf_dots : {};
    return `<div class="ifx-mtf-row">${MTF_ORDER.map((tf) => {
      const state = String(dots[tf] || 'Y');
      const cls = MTF_CLASS[state] || MTF_CLASS.Y;
      return `<div class="ifx-mtf-dot ${cls}"><span class="ifx-mtf-dot-mark"></span><span class="ifx-mtf-dot-label">${tf}</span></div>`;
    }).join('')}</div>`;
  }

  _renderCard(sig) {
    const score = Math.round(Number(sig.conviction_score || 0));
    const grade = sig.conviction_grade || DASH;
    const colorVar = gradeColorVar(grade);
    const entry = Number(sig.entry_price || 0);
    const stop = Number(sig.invalidation_price || 0);
    const target = Number(sig.target_price || 0);
    const isBull = (sig.signal_type || 'bullish') === 'bullish';
    const label = sig.option_bias || (isBull ? 'BUY CE' : 'BUY PE');
    const fs = sig.features_snapshot && typeof sig.features_snapshot === 'object' ? sig.features_snapshot : {};
    const target2 = Number(fs.t2_price || 0);
    const target3 = Number(fs.t3_price || 0);
    const chaseable = !!fs.chaseable;

    return `
    <div class="ifx-ticket ifx-ticket--${isBull ? 'bull' : 'bear'}" data-cockpit-sym="${escapeHtml(sig.symbol)}" tabindex="0">
      <div class="ifx-ticket-head">
        <div class="ifx-ticket-symbol-block">
          <span class="ifx-ticket-symbol">${escapeHtml(sig.symbol)}</span>
          <span class="ifx-badge ${isBull ? 'ifx-badge--bull' : 'ifx-badge--bear'}">${escapeHtml(label)}</span>
        </div>
        <div class="ifx-ticket-head-right">
          <span class="ifx-ticket-locked" title="Frozen at signal time — never recalculated as price moves">🔒 ${signalTime(sig.created_at_us)}</span>
          <span class="ifx-ticket-age">${ageStr(sig.created_at_us)}</span>
        </div>
      </div>

      <div class="ifx-ticket-conviction">
        <div class="ifx-score-arc-wrap">
          ${scoreArc(score, colorVar)}
          <span class="ifx-score-arc-value ifx-mono" style="color:var(${colorVar})">${score}</span>
        </div>
        <div class="ifx-ticket-conviction-meta">
          <span class="ifx-ticket-grade" style="color:var(${colorVar})">${escapeHtml(grade)}</span>
          <span class="ifx-badge ${chaseable ? 'ifx-badge--bull' : 'ifx-badge--neutral'}">${chaseable ? '⚡ Chaseable' : '⏳ Wait'}</span>
        </div>
      </div>

      ${this._renderMtfRow(fs)}

      <div class="ifx-ticket-prices">
        <div class="ifx-ticket-price ifx-ticket-price--entry">
          <span class="ifx-ticket-price-label">Entry</span>
          <span class="ifx-ticket-price-val ifx-mono">${formatPrice(entry)}</span>
        </div>
        <div class="ifx-ticket-price ifx-ticket-price--sl">
          <span class="ifx-ticket-price-label">SL</span>
          <span class="ifx-ticket-price-val ifx-mono">${formatPrice(stop)}</span>
        </div>
        <div class="ifx-ticket-price ifx-ticket-price--t1">
          <span class="ifx-ticket-price-label">T1</span>
          <span class="ifx-ticket-price-val ifx-mono">${formatPrice(target)}</span>
        </div>
        <div class="ifx-ticket-price">
          <span class="ifx-ticket-price-label">T2</span>
          <span class="ifx-ticket-price-val ifx-mono">${target2 > 0 ? formatPrice(target2) : DASH}</span>
        </div>
        <div class="ifx-ticket-price">
          <span class="ifx-ticket-price-label">T3</span>
          <span class="ifx-ticket-price-val ifx-mono">${target3 > 0 ? formatPrice(target3) : DASH}</span>
        </div>
      </div>
    </div>`;
  }

  destroy() {
    this._unsubs.forEach((fn) => fn());
  }
}
