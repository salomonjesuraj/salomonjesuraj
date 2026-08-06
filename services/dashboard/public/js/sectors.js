/**
 * Sector Panel — sector rankings with strength bars and breadth
 * Priority 4: Sector intelligence dashboard
 */
import { escapeHtml, clamp } from './utils.js';
import { api } from './api.js';

/**
 * Return the strength-bar colour based on 5-tier thresholds.
 *   80+  = #10b981 (green)
 *   60-80 = #34d399 (light green)
 *   40-60 = #f59e0b (amber)
 *   20-40 = #f97316 (orange)
 *   <20  = #ef4444 (red)
 */
function strengthColor(val) {
  if (val >= 80) return '#10b981';
  if (val >= 60) return '#34d399';
  if (val >= 40) return '#f59e0b';
  if (val >= 20) return '#f97316';
  return '#ef4444';
}

export class SectorPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._unsubs = [];
    this._selected = '';
  }

  init() {
    this._unsubs.push(api.subscribe('/api/sectors', (resp) => {
      if (resp) this._render(resp.sectors || []);
    }, 5000));
  }

  _render(sectors) {
    if (!sectors.length) {
      this._el.innerHTML = '<div class="panel-empty">No sector data</div>';
      return;
    }

    // Sort by rank ascending
    sectors.sort((a, b) => (a.rank || 999) - (b.rank || 999));

    this._el.innerHTML = sectors.map((s, i) => {
      const name = s.sector_id || s.name || `Sector ${i + 1}`;
      const strength = Math.round(s.strength_score != null ? s.strength_score : (s.strength || 0));
      const advancing = s.advancing || 0;
      const declining = s.declining || 0;
      const barColor = strengthColor(strength);
      const active = this._selected === name ? 'active' : '';

      return `<div class="sector-row ${active}" data-sector="${escapeHtml(name)}">
        <span class="sector-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
        <div class="strength-bar"><div class="strength-bar-fill" style="width:${clamp(strength, 0, 100)}%;background:${barColor}"></div></div>
        <span class="sector-strength" style="color:${barColor}">${strength}</span>
        <span class="sector-ad">
          <span class="positive">▲${advancing}</span>
          <span class="negative">▼${declining}</span>
        </span>
      </div>`;
    }).join('');

    // Click handlers for sector filtering
    this._el.querySelectorAll('.sector-row').forEach(row => {
      row.addEventListener('click', () => {
        const sector = row.dataset.sector;
        if (this._selected === sector) {
          this._selected = '';
        } else {
          this._selected = sector;
        }
        // Update active styling
        this._el.querySelectorAll('.sector-row').forEach(r => r.classList.remove('active'));
        if (this._selected) row.classList.add('active');
        // Notify scanner to filter
        document.dispatchEvent(new CustomEvent('sector:select', { detail: this._selected }));
      });
    });
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
  }
}
