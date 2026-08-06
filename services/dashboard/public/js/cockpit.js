/**
 * Signal Cockpit — the ONE thing visible by default.
 *
 * Deliberately minimal: symbol, CE/PE direction, conviction grade, a single
 * one-line reason, and entry/SL/T1/T2 ready to copy into TradingView or the
 * journal. Everything else (screener table, journal, analytics, news,
 * safety, etc.) lives behind the "More" drawer (see workbench-tabs.js /
 * section-controls.js) so this stays scannable at a glance.
 *
 * Reuses /api/signals (same endpoint signals.js already polled) and the
 * existing .signal-card / .sig-* CSS so no new visual language is needed —
 * just a trimmed layout.
 */
import { api } from './api.js';
import { formatPrice, escapeHtml } from './utils.js';

const DASH = '—';

function ageStr(createdUs) {
  if (!createdUs) return '';
  const ageSec = Math.max(0, Math.floor((Date.now() - Number(createdUs) / 1000) / 1000));
  if (ageSec < 60) return `${ageSec}s ago`;
  const ageMin = Math.floor(ageSec / 60);
  if (ageMin < 60) return `${ageMin}m ago`;
  return `${Math.floor(ageMin / 60)}h ago`;
}

function cleanPhrase(text) {
  return String(text || '').replace(/\s+/g, ' ').trim();
}

/** First explanation line, or a deterministic fallback built from the
 * fields we already have — never leave the "why" blank. */
function oneLineReason(sig) {
  const explanation = sig.explanation;
  const items = Array.isArray(explanation)
    ? explanation
    : String(explanation || '').split('|');
  const first = items.map(cleanPhrase).find(Boolean);
  if (first) return first;
  const fs = sig.features_snapshot && typeof sig.features_snapshot === 'object' ? sig.features_snapshot : {};
  const parts = [fs.trend_text, fs.last_event_label, fs.mtf_text].filter(Boolean);
  return parts.length ? parts.join(' — ') : `${sig.strategy_id || 'Scanner'} signal`;
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

  _renderCard(sig) {
    const score = Math.round(Number(sig.conviction_score || 0));
    const grade = sig.conviction_grade || DASH;
    const entry = Number(sig.entry_price || 0);
    const stop = Number(sig.invalidation_price || 0);
    const target = Number(sig.target_price || 0);
    const isBull = (sig.signal_type || 'bullish') === 'bullish';
    const label = sig.option_bias || (isBull ? 'BUY CE' : 'BUY PE');
    const badgeBg = isBull ? 'var(--green-dim)' : 'var(--red-dim)';
    const badgeColor = isBull ? 'var(--green)' : 'var(--red)';
    const fs = sig.features_snapshot && typeof sig.features_snapshot === 'object' ? sig.features_snapshot : {};
    const target2 = Number(fs.t2_price || 0);

    return `
    <div class="signal-card cockpit-card" data-cockpit-sym="${escapeHtml(sig.symbol)}">
      <div class="sig-header">
        <div class="sig-sym-block">
          <span class="sig-symbol">${escapeHtml(sig.symbol)}</span>
        </div>
        <div class="sig-header-right">
          <span class="sig-type-badge" style="background:${badgeBg};color:${badgeColor}">${escapeHtml(label)}</span>
          <span class="grade-chip ${gradeClass(grade)}">${escapeHtml(grade)} · ${score}</span>
          <span class="sig-age">${ageStr(sig.created_at_us)}</span>
        </div>
      </div>
      <div class="cockpit-reason">${escapeHtml(oneLineReason(sig))}</div>
      <div class="sig-price-grid cockpit-price-grid">
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
      </div>
    </div>`;
  }

  destroy() {
    this._unsubs.forEach((fn) => fn());
  }
}
