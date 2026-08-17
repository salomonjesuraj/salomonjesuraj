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

// Breadth regime (healthy/neutral/weak) -> tone, distinct from the index
// regime badge above (risk-on/neutral/risk-off) -- these are two different
// reads (single-NIFTY50-index vs whole-universe breadth) shown side by
// side deliberately, not merged into one number.
function breadthTone(regime) {
  const r = String(regime || '').toLowerCase();
  if (r === 'healthy') return 'ifx-tone-good';
  if (r === 'weak') return 'ifx-tone-bad';
  return 'ifx-tone-faint';
}

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
      const [regime, sectors, breadth] = await Promise.all([
        api.fetch('/api/regime').catch(() => ({})),
        api.fetch('/api/sectors').catch(() => ({ sectors: [] })),
        api.fetch('/api/market/breadth-health').catch(() => null),
      ]);

      const list = Array.isArray(sectors?.sectors) ? sectors.sectors : [];
      const sorted = [...list].sort(
        (a, b) => Number(b.strength_score ?? b.score ?? 0) - Number(a.strength_score ?? a.score ?? 0)
      );
      const leader = sorted[0]?.sector_id || sorted[0]?.sector || regime?.leader || '—';
      const laggard = sorted[sorted.length - 1]?.sector_id || sorted[sorted.length - 1]?.sector || regime?.laggard || '—';

      // Prefer the real breadth-health endpoint's own advance/decline count
      // (server-computed across the whole live-ticked universe every call)
      // over the client-side per-sector sum -- same underlying idea, this
      // one just doesn't depend on /api/sectors having refreshed recently.
      const adComponent = breadth?.available ? breadth.components?.advance_decline : null;
      const totalAdv = list.reduce((sum, s) => sum + Number(s.advancing || 0), 0);
      const totalDec = list.reduce((sum, s) => sum + Number(s.declining || 0), 0);
      const adv = adComponent?.advancing ?? (totalAdv || '—');
      const dec = adComponent?.declining ?? (totalDec || '—');
      const breadthPct = breadth?.available ? breadth.health_score : null;
      const breadthRegime = breadth?.available ? breadth.regime : null;
      const status = String(regime?.regime || regime?.status || 'NEUTRAL').toUpperCase();

      this._el.innerHTML = `
        <span class="ifx-badge ${regimeBadgeClass(status)}">${status}</span>
        <span class="ifx-mh-stat"><label>A/D</label><b class="ifx-mono ifx-tone-good">▲${adv}</b><b class="ifx-mono ifx-tone-bad">▼${dec}</b></span>
        <span class="ifx-mh-stat" title="${breadth ? breadthTooltip(breadth).replace(/"/g, '&quot;') : 'Market breadth unavailable'}">
          <label>BREADTH</label><b class="ifx-mono ${breadthTone(breadthRegime)}">${fmtPct(breadthPct)}</b>${breadthRegime ? `<i class="ifx-mh-regime">${breadthRegime}</i>` : ''}
        </span>
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
