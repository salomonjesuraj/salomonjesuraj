/**
 * Signal Cockpit — the ONE thing visible by default.
 *
 * Score-first, per direct feedback: the score should be convincing on its
 * own, not a wall of text. A card shows only: symbol + CE/PE direction,
 * a big conviction score/grade, a chaseable flag, the MTF colour-dot row,
 * and entry/SL/T1/T2/T3. No prose reasoning on the card itself — click
 * through (Stock Detail tab in "More") for the full explanation.
 */
import { api } from './api.js';
import { formatPrice, escapeHtml } from './utils.js';

const DASH = '—';
const MTF_ORDER = ['1M', '5M', '15M', '1H', '4H', '1D'];
const MTF_CLASS = { G: 'g', R: 'r', Y: 'y' };

function ageStr(createdUs) {
  if (!createdUs) return '';
  const ageSec = Math.max(0, Math.floor((Date.now() - Number(createdUs) / 1000) / 1000));
  if (ageSec < 60) return `${ageSec}s ago`;
  const ageMin = Math.floor(ageSec / 60);
  if (ageMin < 60) return `${ageMin}m ago`;
  return `${Math.floor(ageMin / 60)}h ago`;
}

function gradeClass(grade) {
  return 'grade-' + String(grade || '').toLowerCase().replace('+', 'plus');
}

export class CockpitPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._signals = [];
    this._unsubs = [];
  }

  init() {
    if (!this._el) return;
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
        <div class="panel-empty cockpit-empty">
          <span style="font-size:22px;opacity:.4">🎯</span>
          <span style="font-size:13px;font-weight:600;color:var(--text-secondary)">No active signals right now</span>
          <span style="font-size:10px;color:var(--text-disabled)">Nothing meets the conviction bar — that's the point. Check "More" for the full screener.</span>
        </div>`;
      return;
    }

    const ranked = [...this._signals].sort(
      (a, b) => Number(b.conviction_score || 0) - Number(a.conviction_score || 0)
    );

    this._el.innerHTML = ranked.map((sig) => this._renderCard(sig)).join('');

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
    return `<div class="cockpit-mtf-row">${MTF_ORDER.map((tf) => {
      const state = String(dots[tf] || 'Y');
      const cls = MTF_CLASS[state] || 'y';
      return `<div class="cockpit-mtf-dot ${cls}"><span class="dot"></span><span class="tf-label">${tf}</span></div>`;
    }).join('')}</div>`;
  }

  _renderCard(sig) {
    const score = Math.round(Number(sig.conviction_score || 0));
    const grade = sig.conviction_grade || DASH;
    const gc = gradeClass(grade);
    const entry = Number(sig.entry_price || 0);
    const stop = Number(sig.invalidation_price || 0);
    const target = Number(sig.target_price || 0);
    const isBull = (sig.signal_type || 'bullish') === 'bullish';
    const label = sig.option_bias || (isBull ? 'BUY CE' : 'BUY PE');
    const badgeBg = isBull ? 'var(--green-dim)' : 'var(--red-dim)';
    const badgeColor = isBull ? 'var(--green)' : 'var(--red)';
    const fs = sig.features_snapshot && typeof sig.features_snapshot === 'object' ? sig.features_snapshot : {};
    const target2 = Number(fs.t2_price || 0);
    const target3 = Number(fs.t3_price || 0);
    const chaseable = !!fs.chaseable;

    return `
    <div class="signal-card cockpit-card" data-cockpit-sym="${escapeHtml(sig.symbol)}">
      <div class="sig-header">
        <div class="sig-sym-block">
          <span class="sig-symbol">${escapeHtml(sig.symbol)}</span>
        </div>
        <div class="sig-header-right">
          <span class="sig-type-badge" style="background:${badgeBg};color:${badgeColor}">${escapeHtml(label)}</span>
          <span class="sig-age">${ageStr(sig.created_at_us)}</span>
        </div>
      </div>

      <div class="cockpit-score-row">
        <span class="cockpit-score ${gc}">${score}</span>
        <span class="cockpit-grade-label">${escapeHtml(grade)}</span>
        <span class="cockpit-chase-badge ${chaseable ? 'go' : 'wait'}">${chaseable ? '🚀 Chaseable' : '⏳ Wait'}</span>
      </div>

      ${this._renderMtfRow(fs)}

      <div class="sig-price-grid cockpit-price-grid t3">
        <div class="sig-price-item">
          <span class="sig-price-label">Entry</span>
          <span class="sig-price-val entry-price">${formatPrice(entry)}</span>
        </div>
        <div class="sig-price-item">
          <span class="sig-price-label">SL</span>
          <span class="sig-price-val stop-price">${formatPrice(stop)}</span>
        </div>
        <div class="sig-price-item">
          <span class="sig-price-label">T1</span>
          <span class="sig-price-val target-price">${formatPrice(target)}</span>
        </div>
        <div class="sig-price-item">
          <span class="sig-price-label">T2</span>
          <span class="sig-price-val">${target2 > 0 ? formatPrice(target2) : DASH}</span>
        </div>
        <div class="sig-price-item">
          <span class="sig-price-label">T3</span>
          <span class="sig-price-val">${target3 > 0 ? formatPrice(target3) : DASH}</span>
        </div>
      </div>
    </div>`;
  }

  destroy() {
    this._unsubs.forEach((fn) => fn());
  }
}
