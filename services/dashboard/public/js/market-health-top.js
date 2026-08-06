/**
 * Compact top-row market health strip.
 * Keeps the regime, breadth, A/D, leader, and laggard visible while trading options.
 */
import { api } from './api.js';

function fmtPct(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${Math.round(n)}%` : '—';
}

function cls(value) {
  const text = String(value || '').toLowerCase();
  if (text.includes('risk-on')) return 'risk-on';
  if (text.includes('risk-off')) return 'risk-off';
  return 'neutral';
}

export class MarketHealthTop {
  constructor(containerEl) {
    this._el = containerEl;
    this._timer = null;
  }

  init() {
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
      const statusClass = cls(status);

      this._el.innerHTML = `
        <div class="mht-regime ${statusClass}"><span></span>${status}</div>
        <div class="mht-stat"><label>A/D</label><b class="positive">▲${adv}</b><b class="negative">▼${dec}</b></div>
        <div class="mht-stat"><label>BREADTH</label><b>${fmtPct(breadth)}</b></div>
        <div class="mht-stat"><label>LEADER</label><b class="positive">${leader}</b></div>
        <div class="mht-stat"><label>LAGGARD</label><b class="negative">${laggard}</b></div>
      `;
    } catch (err) {
      this._el.innerHTML = '<div class="mht-muted">Market health unavailable</div>';
    }
  }

  _renderLoading() {
    if (this._el) this._el.innerHTML = '<div class="mht-muted">Loading market health…</div>';
  }

  destroy() {
    if (this._timer) clearInterval(this._timer);
  }
}
