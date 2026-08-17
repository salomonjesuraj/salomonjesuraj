/**
 * Market Health Widget — regime, breadth, index trends, sector leader/laggard
 */
import { api } from './api.js';

// Component key -> display label, matching market-health-top.js's mapping.
const COMPONENT_LABELS = {
  advance_decline: 'Adv/Dec', momentum: 'Momentum', volume_weighted: 'Volume-wtd',
  moving_average: 'Above 50/200-SMA', week52_range: '52W range',
};

function breadthTooltip(breadth) {
  if (!breadth || !breadth.available) return 'Market breadth unavailable';
  const lines = [`F&O universe breadth (${breadth.universe_size} symbols) -- informational only`];
  for (const [key, label] of Object.entries(COMPONENT_LABELS)) {
    const c = breadth.components?.[key];
    if (!c) continue;
    lines.push(c.available ? `${label}: ${c.score}%` : `${label}: n/a (${c.reason || 'not enough coverage'})`);
  }
  return lines.join('\n');
}

export class MarketHealth {
  constructor(containerEl) {
    this._el = containerEl;
    this._regime = null;
    this._sectors = [];
    this._diagnostics = null;
    this._breadth = null;
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

    // Sectors for leader/laggard
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

    // Real 5-component breadth score across the whole tracked F&O
    // universe -- see api/market_breadth.py. Cheap (~0.1s, pure Redis),
    // and its own cache TTL is 15 min, so a 30s poll is already more than
    // enough headroom rather than a cost concern.
    this._unsubs.push(api.subscribe('/api/market/breadth-health', (resp) => {
      this._breadth = resp;
      this._render();
    }, 30000));

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

    // Count advancing/declining across all sectors -- fallback only, used
    // when /api/market/breadth-health hasn't responded yet.
    let totalAdv = 0, totalDec = 0;
    for (const s of this._sectors) {
      totalAdv += parseInt(s.advancing || 0);
      totalDec += parseInt(s.declining || 0);
    }

    // Real 5-component score across the whole tracked universe, preferred
    // over the client-side sector sum above (which only reflects whatever
    // sectors happened to have refreshed recently, and used total loaded
    // symbols rather than decided ones as its denominator).
    const breadth = this._breadth;
    const adComponent = breadth?.available ? breadth.components?.advance_decline : null;
    const adv = adComponent?.advancing ?? totalAdv;
    const dec = adComponent?.declining ?? totalDec;
    const breadthPct = breadth?.available ? breadth.health_score : null;
    const breadthRegime = breadth?.available ? breadth.regime : null;
    const breadthColor = breadthRegime === 'healthy' ? 'var(--green)' : breadthRegime === 'weak' ? 'var(--red)' : 'var(--text-secondary)';
    const breadthDisplay = breadthPct != null ? `${Math.round(breadthPct)}%` : '—';

    this._el.innerHTML = `
      <div class="market-health">
        <div class="mh-item" style="grid-column:span 2;justify-content:center">
          <span class="regime-badge ${regimeClass}">${regimeVal.replace('_', ' ')}</span>
        </div>
        <div class="mh-item">
          <span class="mh-label">A/D</span>
          <span class="mh-value"><span class="positive">▲${adv}</span> <span class="negative">▼${dec}</span></span>
        </div>
        <div class="mh-item" title="${breadth ? breadthTooltip(breadth).replace(/"/g, '&quot;') : 'Market breadth unavailable'}">
          <span class="mh-label">Breadth${breadthRegime ? ` (${breadthRegime})` : ''}</span>
          <span class="mh-value" style="color:${breadthColor}">${breadthDisplay}</span>
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
