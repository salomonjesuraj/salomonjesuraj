/**
 * Compact top-row market health strip — regime, breadth, A/D, leader/laggard.
 * v2 design system (ifx-* classes, theme.css).
 */
import { api } from './api.js';

function fmtPct(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${Math.round(n)}%` : '—';
}

function regimeBadgeClass(value) {
  const text = String(value || '').toLowerCase();
  if (text.includes('risk-on') || text.includes('bull')) return 'ifx-badge--bull';
  if (text.includes('risk-off') || text.includes('bear')) return 'ifx-badge--bear';
  return 'ifx-badge--neutral';
}

export class MarketHealthTop {
  constructor(containerEl) {
    this._el = containerEl;
    this._timer = null;
  }

  init() {
    this._el.classList.add('ifx-shell', 'ifx-market-health');
    this._renderLoading();
    this._refresh();
    this._timer = setInterval(() => this._refresh(), 15000);
  }

  async _refresh() {
    if (!this._el) return;
    try {
      const [regime, sectors] = await Promise.all([
        api.fetch('/api/regime').catch(() => ({})),
        api.fetch('/api/sectors').catch(() => ({ sectors: [] })),
      ]);

      const list = Array.isArray(sectors?.sectors) ? sectors.sectors : [];
      const sorted = [...list].sort(
        (a, b) => Number(b.strength_score ?? b.score ?? 0) - Number(a.strength_score ?? a.score ?? 0)
      );
      const leader = sorted[0]?.sector_id || sorted[0]?.sector || regime?.leader || '—';
      const laggard = sorted[sorted.length - 1]?.sector_id || sorted[sorted.length - 1]?.sector || regime?.laggard || '—';
      const totalAdv = list.reduce((sum, s) => sum + Number(s.advancing || 0), 0);
      const totalDec = list.reduce((sum, s) => sum + Number(s.declining || 0), 0);
      const breadth = regime?.breadth_pct ?? regime?.breadth ?? regime?.market_breadth
        ?? (totalAdv + totalDec > 0 ? (totalAdv / (totalAdv + totalDec)) * 100 : null);
      const adv = regime?.advancers ?? regime?.advance ?? regime?.advancing ?? (totalAdv || '—');
      const dec = regime?.decliners ?? regime?.decline ?? regime?.declining ?? (totalDec || '—');
      const status = String(regime?.regime || regime?.status || 'NEUTRAL').toUpperCase();

      this._el.innerHTML = `
        <span class="ifx-badge ${regimeBadgeClass(status)}">${status}</span>
        <span class="ifx-mh-stat"><label>A/D</label><b class="ifx-mono ifx-tone-good">▲${adv}</b><b class="ifx-mono ifx-tone-bad">▼${dec}</b></span>
        <span class="ifx-mh-stat"><label>BREADTH</label><b class="ifx-mono">${fmtPct(breadth)}</b></span>
        <span class="ifx-mh-stat"><label>LEADER</label><b class="ifx-tone-good">${leader}</b></span>
        <span class="ifx-mh-stat"><label>LAGGARD</label><b class="ifx-tone-bad">${laggard}</b></span>
      `;
    } catch (err) {
      this._el.innerHTML = '<div class="ifx-mh-muted">Market health unavailable</div>';
    }
  }

  _renderLoading() {
    if (this._el) this._el.innerHTML = '<div class="ifx-mh-muted">Loading market health…</div>';
  }

  destroy() {
    if (this._timer) clearInterval(this._timer);
  }
}
