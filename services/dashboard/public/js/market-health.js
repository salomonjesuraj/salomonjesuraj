/**
 * Market Health Widget — regime, breadth, index trends, sector leader/laggard
 */
import { api } from './api.js';

export class MarketHealth {
  constructor(containerEl) {
    this._el = containerEl;
    this._regime = null;
    this._sectors = [];
    this._diagnostics = null;
    this._unsubs = [];
  }

  init() {
    // Regime from /api/regime
    this._unsubs.push(api.subscribe('/api/regime', (resp) => {
      if (resp) {
        this._regime = resp;
        this._render();
      }
    }, 5000));

    // Sectors for leader/laggard and A/D
    this._unsubs.push(api.subscribe('/api/sectors', (resp) => {
      if (resp) {
        this._sectors = resp.sectors || [];
        this._render();
      }
    }, 5000));

    // Pipeline diagnostics for breadth info
    this._unsubs.push(api.subscribe('/api/diagnostics', (resp) => {
      if (resp) {
        this._diagnostics = resp;
        this._render();
      }
    }, 10000));

    this._render();
  }

  _render() {
    const regime = this._regime || {};
    const regimeVal = regime.regime || 'NEUTRAL';
    const regimeClass = regimeVal === 'RISK_ON' ? 'regime-risk-on' :
                        regimeVal === 'RISK_OFF' ? 'regime-risk-off' : 'regime-neutral';

    // Find leader/laggard from sectors
    let leader = '—', laggard = '—';
    if (this._sectors.length > 0) {
      const sorted = [...this._sectors].sort((a, b) =>
        (b.strength_score || 0) - (a.strength_score || 0)
      );
      leader = sorted[0]?.sector_id || '—';
      laggard = sorted[sorted.length - 1]?.sector_id || '—';
    }

    // Count advancing/declining across all sectors
    let totalAdv = 0, totalDec = 0;
    for (const s of this._sectors) {
      totalAdv += parseInt(s.advancing || 0);
      totalDec += parseInt(s.declining || 0);
    }

    const symCount = this._diagnostics?.symbols_loaded || 0;
    const breadthPct = symCount > 0 ? Math.round((totalAdv / symCount) * 100) : 0;

    this._el.innerHTML = `
      <div class="market-health">
        <div class="mh-item" style="grid-column:span 2;justify-content:center">
          <span class="regime-badge ${regimeClass}">${regimeVal.replace('_', ' ')}</span>
        </div>
        <div class="mh-item">
          <span class="mh-label">A/D</span>
          <span class="mh-value"><span class="positive">▲${totalAdv}</span> <span class="negative">▼${totalDec}</span></span>
        </div>
        <div class="mh-item">
          <span class="mh-label">Breadth</span>
          <span class="mh-value" style="color:${breadthPct >= 50 ? 'var(--green)' : 'var(--red)'}">${breadthPct}%</span>
        </div>
        <div class="mh-item">
          <span class="mh-label">Leader</span>
          <span class="mh-value positive">${leader}</span>
        </div>
        <div class="mh-item">
          <span class="mh-label">Laggard</span>
          <span class="mh-value negative">${laggard}</span>
        </div>
      </div>
    `;
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
  }
}
