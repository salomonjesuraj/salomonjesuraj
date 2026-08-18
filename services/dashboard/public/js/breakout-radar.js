/**
 * Stock Breakout Radar — Phase R3 of the stock-first restructure
 * (docs/stock-options-dashboard-review-and-structure-plan.md).
 *
 * A new, additive panel: ranks the UNDERLYING stock's own breakout
 * evidence (relative volume, day-high/day-low proximity, VWAP/EMA
 * acceptance, MTF alignment) independent of option-chain readiness.
 * Deliberately does NOT show Entry/SL/Targets/Chain/F&O columns — those
 * belong to the existing scanner tables (trade execution) and to
 * Selected Stock Detail once a name is picked here; this table's whole
 * point is to answer "which stocks are actually moving" without contract
 * language crowding that question out (see the reference plan's Finding
 * #1/#6 and this session's own audit doc).
 *
 * Data source is the SAME /api/ticks response every other table already
 * polls (api.js's subscribe() dedupes the network call across all
 * subscribers) — reads stock_breakout_score/_score_max/_tier,
 * breakout_type, rel_vol, rvol_rank, volume_profile_ready, day_high/
 * day_low, vwap_state straight off each row. No new endpoint (R1/R2's
 * own design decision: extend /api/ticks, don't stand up
 * /api/stock-breakouts).
 *
 * Same VirtualScroll contract as scanner-v2.js, same
 * {key,label,width,toggle} column shape, same localStorage-toggle
 * convention (own key, doesn't collide with either scanner table's).
 */
import { VirtualScroll } from './virtual-scroll.js';
import { formatPrice, escapeHtml } from './utils.js';
import { api } from './api.js';

const DASH = '—';

const COLUMNS = [
  { key: 'symbol', label: 'Symbol', width: 100, toggle: false },
  { key: 'sector_id', label: 'Sector', width: 110, toggle: true },
  { key: 'ltp', label: 'LTP', width: 84, toggle: false },
  { key: 'change_pct', label: 'Chg', width: 84, toggle: true },
  { key: 'rel_vol', label: 'RVol', width: 76, toggle: false },
  { key: 'breakout_type', label: 'Type', width: 168, toggle: true },
  { key: 'range_position', label: 'Location', width: 118, toggle: true },
  { key: 'vwap_state', label: 'VWAP', width: 68, toggle: true },
  { key: 'stock_breakout_score', label: 'Score', width: 84, toggle: false },
  { key: 'stock_breakout_tier', label: 'Tier', width: 118, toggle: false },
  { key: 'freshness', label: 'Fresh', width: 68, toggle: true },
  // Daily Trend: a literal replica of a real, popular Chartink screener
  // ("FNO stocks bullish trend scanner", 14 daily-bar conditions) the user
  // pointed at, asking why this Radar surfaces different names -- answer:
  // different questions (daily trend REGIME vs. live intraday EVIDENCE).
  // See api/daily_trend_filter.py's own header for the full comparison.
  // Opt-in, off by default -- this is a second, complementary read, not
  // a replacement for the Radar's own live score/tier.
  { key: 'daily_trend', label: 'Daily Trend', width: 118, toggle: true, defaultHidden: true },
];

function visibleColumns(hidden) {
  return COLUMNS.filter((c) => !hidden.has(c.key));
}

// Mirrors the exact vocabulary api/routes/ticks.py's _classify_breakout_type
// emits (R2) — keep in sync if that list changes.
const BREAKOUT_TYPE_META = {
  vwap_reclaim: { label: 'VWAP Reclaim', tone: 'bull' },
  vwap_rejection: { label: 'VWAP Rejection', tone: 'bear' },
  day_high_break: { label: 'Day High Break', tone: 'bull' },
  day_low_break: { label: 'Day Low Break', tone: 'bear' },
  above_vwap_continuation: { label: 'Above VWAP', tone: 'bull' },
  below_vwap_continuation: { label: 'Below VWAP', tone: 'bear' },
  volume_surge: { label: 'Volume Surge', tone: 'warn' },
  failed_no_chase: { label: 'Failed / No-Chase', tone: 'flat' },
};

function breakoutTypeHtml(item) {
  const meta = BREAKOUT_TYPE_META[item.breakout_type];
  if (!meta) return `<span class="ifx-scr-dash">${DASH}</span>`;
  return `<span class="ifx-badge ifx-badge--${meta.tone}">${escapeHtml(meta.label)}</span>`;
}

// RVol column: the score itself already zeroes out when a symbol's
// volume-profile baseline hasn't bootstrapped (R1), but a bare "0.0x"
// here would still read as "confirmed quiet" rather than "unknown" --
// exactly the audit doc's Finding #7. Surfaced honestly instead.
function rvolHtml(item) {
  if (item.volume_profile_ready === false) {
    return `<span class="ifx-scr-dash" title="20-session volume baseline hasn't bootstrapped for this symbol yet -- relative volume is unknown, not confirmed low">VOL BASE MISSING</span>`;
  }
  const rvol = Number(item.rel_vol || 0);
  const tone = rvol >= 2.5 ? 'ifx-tone-good' : rvol >= 1.3 ? '' : 'ifx-tone-faint';
  return `<span class="ifx-mono ${tone}">${rvol.toFixed(2)}x</span>`;
}

function rangePositionHtml(item) {
  const high = Number(item.day_high || 0);
  const low = Number(item.day_low || 0);
  const ltp = Number(item.ltp || 0);
  if (!(high > 0) || !(low > 0) || high <= low) return `<span class="ifx-scr-dash">${DASH}</span>`;
  const pct = Math.min(100, Math.max(0, ((ltp - low) / (high - low)) * 100));
  const tone = pct >= 85 ? 'var(--ifx-bull)' : pct <= 15 ? 'var(--ifx-bear)' : 'var(--ifx-warn)';
  return `<div class="ifx-scr-meter" title="Day range: ${formatPrice(low)} – ${formatPrice(high)}">
    <span class="ifx-mono">${Math.round(pct)}%</span>
    <div class="ifx-scr-meter-track"><i style="width:${pct}%;background:${tone}"></i></div>
  </div>`;
}

function scoreHtml(item) {
  const score = item.stock_breakout_score;
  const max = item.stock_breakout_score_max || 90;
  if (score == null) return `<span class="ifx-scr-dash">${DASH}</span>`;
  const pct = (Number(score) / max) * 100;
  const tone = pct >= 70 ? 'ifx-tone-good' : pct >= 55 ? '' : 'ifx-tone-faint';
  // Honest "NN/90" display -- Phase R1's own amendment: never render a
  // bare number that could be misread as a completed 100-point score.
  return `<span class="ifx-mono ${tone}" title="Sector/index relative strength (10pts) not computed yet -- score is out of ${max}, not 100">${Number(score).toFixed(1)}/${max}</span>`;
}

const TIER_META = {
  BREAKOUT_NOW: { label: 'BREAKOUT NOW', tone: 'bull' },
  OPTION_READY: { label: 'OPTION READY', tone: 'bull' },
  RETEST_ENTRY: { label: 'RETEST ENTRY', tone: 'warn' },
  EARLY_WATCH: { label: 'EARLY WATCH', tone: 'warn' },
  NO_CHASE: { label: 'NO CHASE', tone: 'flat' },
};

function tierHtml(item) {
  const meta = TIER_META[item.stock_breakout_tier];
  if (!meta) return `<span class="ifx-scr-dash">${DASH}</span>`;
  return `<span class="ifx-badge ifx-badge--${meta.tone}">${escapeHtml(meta.label)}</span>`;
}

// Freshness is computed client-side from the tick's own updated_at
// (microsecond epoch, already present on every /api/ticks row) --
// no backend field needed, matches R1/R2's own "no new I/O beyond
// what's already there" discipline.
function freshnessHtml(item) {
  const updatedUs = Number(item.updated_at || 0);
  if (!(updatedUs > 0)) return `<span class="ifx-scr-dash">${DASH}</span>`;
  const ageSec = Math.max(0, Math.round((Date.now() - updatedUs / 1000) / 1000));
  const text = ageSec < 60 ? `${ageSec}s` : `${Math.round(ageSec / 60)}m`;
  const tone = ageSec < 10 ? 'ifx-tone-good' : ageSec < 60 ? '' : 'ifx-tone-faint';
  return `<span class="ifx-mono ${tone}">${text}</span>`;
}

// Daily Trend column: honest 3-state, not a binary pass/fail --
// "not covered" (fewer than 51 cached daily bars for this symbol; the mtf
// warmup queue hasn't reached it yet, or it's genuinely new to the F&O
// list) must never look like "fails the filter". See
// api/daily_trend_filter.py for the 14 conditions themselves.
function dailyTrendHtml(item) {
  const dt = item.daily_trend;
  if (!dt || !dt.available) {
    return `<span class="ifx-scr-dash" title="Daily bar history hasn't warmed up for this symbol yet -- not a fail, just not computed">${DASH}</span>`;
  }
  const { pass, pass_count: passCount, total, conditions } = dt;
  const failed = Object.entries(conditions || {})
    .filter(([, v]) => !v)
    .map(([k]) => k.replace(/_/g, ' '))
    .join(', ');
  const title = pass
    ? `Passes all ${total} of the Chartink bullish-trend conditions`
    : `Fails: ${failed || 'unknown'}`;
  const tone = pass ? 'ifx-tone-good' : passCount >= total - 2 ? '' : 'ifx-tone-faint';
  return `<span class="ifx-mono ${tone}" title="${escapeHtml(title)}">${pass ? '✓ ' : ''}${passCount}/${total}</span>`;
}

export class BreakoutRadarPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._data = new Map(); // symbol -> merged /api/ticks row
    this._unsubs = [];
    this._sortKey = 'stock_breakout_score';
    this._sortDir = -1;
    this._search = '';
    this._selectedSymbol = null;
    this._vscroll = null;
    this._hidden = new Set();
    // Filter toggles -- reference plan's filter list, minus the
    // sector-top-3 one (needs the deferred sector/index-RS work, R8).
    this._filters = { rvol2: false, nearHigh: false, aboveVwap: false, excludeNoChase: false, dailyTrend: false };
  }

  init() {
    if (!this._el) return;
    try {
      const saved = JSON.parse(localStorage.getItem('infusion:breakout-radar:hidden:v1') || '[]');
      this._hidden = new Set(saved);
    } catch (_) {}
    // defaultHidden columns start hidden on a fresh install, but a user who
    // explicitly re-enabled one shouldn't have it silently re-hidden again
    // on the next reload -- the ':shown' marker (written in the checkbox
    // handler below) records that explicit choice.
    COLUMNS.filter((c) => c.defaultHidden && !this._hidden.has(c.key + ':shown'))
      .forEach((c) => this._hidden.add(c.key));

    this._el.classList.add('ifx-scr-v2');
    this._el.innerHTML = `
      <div class="ifx-scr-toolbar">
        <div class="ifx-scr-search">🔍 <input type="text" placeholder="Search symbol…" id="radarSearch" /></div>
        <div class="ifx-radar-filters" id="radarFilters">
          <button type="button" class="ifx-radar-filter-btn" data-filter="rvol2">RVol ≥ 2</button>
          <button type="button" class="ifx-radar-filter-btn" data-filter="nearHigh">Near day high</button>
          <button type="button" class="ifx-radar-filter-btn" data-filter="aboveVwap">Above VWAP</button>
          <button type="button" class="ifx-radar-filter-btn" data-filter="excludeNoChase">Exclude no-chase</button>
          <button type="button" class="ifx-radar-filter-btn" data-filter="dailyTrend" title="Passes all 14 conditions of the real Chartink FNO bullish-trend screener -- a daily trend-regime read, separate from this table's own live intraday score">Daily Trend (Chartink)</button>
        </div>
        <span class="ifx-scr-count" id="radarCount"></span>
        <button type="button" class="ifx-scr-col-toggle-btn" id="radarColToggle" title="Show/hide columns">Columns</button>
      </div>
      <div class="ifx-scr-col-panel" id="radarColPanel" style="display:none"></div>
      <div class="ifx-scr-table-wrap">
        <div class="ifx-scr-head" id="radarHead"></div>
        <div class="ifx-scr-body" id="radarBody"></div>
      </div>
    `;
    this._buildHead();
    this._buildColTogglePanel();

    this._el.querySelector('#radarFilters').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-filter]');
      if (!btn) return;
      const key = btn.dataset.filter;
      this._filters[key] = !this._filters[key];
      btn.classList.toggle('active', this._filters[key]);
      this._renderRows();
    });

    const toggleBtn = this._el.querySelector('#radarColToggle');
    const panel = this._el.querySelector('#radarColPanel');
    toggleBtn.addEventListener('click', () => {
      panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    });

    this._vscroll = new VirtualScroll(this._el.querySelector('#radarBody'), {
      rowHeight: 38,
      overscan: 12,
      renderRow: (item) => this._renderRow(item),
      keyFn: (item) => item.symbol,
      onRowClick: (item) => this._select(item.symbol),
    });

    this._el.querySelector('#radarSearch').addEventListener('input', (e) => {
      this._search = e.target.value.trim().toUpperCase();
      this._renderRows();
    });

    this._unsubs.push(api.subscribe('/api/ticks', (resp) => {
      if (!resp?.ticks) return;
      for (const t of resp.ticks) {
        if (t.symbol) this._data.set(t.symbol, t);
      }
      this._renderRows();
    }, 5000));

    document.addEventListener('signal:select', (e) => {
      const sym = e.detail?.symbol;
      if (sym) this._select(String(sym).toUpperCase());
    });
  }

  _buildHead() {
    const cols = visibleColumns(this._hidden);
    this._el.style.setProperty('--ifx-scr-grid', cols.map((c) => `${c.width}px`).join(' '));
    const head = this._el.querySelector('#radarHead');
    head.innerHTML = cols.map((c) =>
      `<div class="ifx-scr-th${c.key === this._sortKey ? ' sorted' : ''}" data-sort="${c.key}">${escapeHtml(c.label)}${c.key === this._sortKey ? `<span class="ifx-scr-arrow">${this._sortDir === -1 ? '▾' : '▴'}</span>` : ''}</div>`
    ).join('');
    head.querySelectorAll('[data-sort]').forEach((th) => {
      th.addEventListener('click', () => {
        const key = th.dataset.sort;
        this._sortDir = this._sortKey === key ? -this._sortDir : -1;
        this._sortKey = key;
        this._buildHead();
        this._renderRows();
      });
    });
  }

  _buildColTogglePanel() {
    const panel = this._el.querySelector('#radarColPanel');
    if (!panel) return;
    panel.innerHTML = COLUMNS.filter((c) => c.toggle).map((c) => `
      <label class="ifx-scr-col-label">
        <input type="checkbox" data-col="${c.key}" ${this._hidden.has(c.key) ? '' : 'checked'} />
        ${escapeHtml(c.label)}
      </label>
    `).join('');
    panel.querySelectorAll('input[type=checkbox]').forEach((cb) => {
      cb.addEventListener('change', () => {
        const col = COLUMNS.find((c) => c.key === cb.dataset.col);
        if (cb.checked) {
          this._hidden.delete(cb.dataset.col);
          if (col && col.defaultHidden) this._hidden.add(cb.dataset.col + ':shown');
        } else {
          this._hidden.add(cb.dataset.col);
          this._hidden.delete(cb.dataset.col + ':shown');
        }
        try { localStorage.setItem('infusion:breakout-radar:hidden:v1', JSON.stringify([...this._hidden])); } catch (_) {}
        this._buildHead();
        if (this._vscroll) this._vscroll.refresh();
      });
    });
  }

  _sortValue(item, key) {
    switch (key) {
      case 'symbol': case 'sector_id': case 'breakout_type': case 'stock_breakout_tier':
        return String(item[key] || '');
      case 'ltp': return Number(item.ltp || 0);
      case 'change_pct': return Number(item.change_pct || 0);
      case 'rel_vol': return Number(item.rel_vol || 0);
      case 'vwap_state': return String(item.vwap_state || '');
      case 'daily_trend': return item.daily_trend?.available ? Number(item.daily_trend.pass_count || 0) : -1;
      default:
        // stock_breakout_score (default sort), tiebreak rvol_rank (lower
        // rank = higher volume, so invert it into the same "higher is
        // better" direction the rest of this sort comparator expects).
        return Number(item.stock_breakout_score || 0) - (Number(item.rvol_rank || 999) * 0.0001);
    }
  }

  _matchesFilters(item) {
    if (this._filters.rvol2 && !(Number(item.rel_vol || 0) >= 2.0)) return false;
    if (this._filters.nearHigh) {
      const high = Number(item.day_high || 0), low = Number(item.day_low || 0), ltp = Number(item.ltp || 0);
      const pct = (high > low) ? ((ltp - low) / (high - low)) * 100 : 0;
      if (pct < 85) return false;
    }
    if (this._filters.aboveVwap && item.vwap_state !== 'ABOVE') return false;
    if (this._filters.excludeNoChase && item.stock_breakout_tier === 'NO_CHASE') return false;
    if (this._filters.dailyTrend && !(item.daily_trend && item.daily_trend.pass)) return false;
    return true;
  }

  _renderRows() {
    let items = [...this._data.values()];
    if (this._search) items = items.filter((it) => String(it.symbol || '').includes(this._search));
    items = items.filter((it) => this._matchesFilters(it));
    items.sort((a, b) => {
      const av = this._sortValue(a, this._sortKey), bv = this._sortValue(b, this._sortKey);
      if (typeof av === 'string') return av.localeCompare(bv) * this._sortDir;
      return (av - bv) * this._sortDir;
    });
    const count = this._el.querySelector('#radarCount');
    if (count) count.textContent = `${items.length} of ${this._data.size} symbols`;
    this._vscroll.setData(items);
  }

  _select(symbol) {
    this._selectedSymbol = symbol;
    this._vscroll.refresh();
    document.dispatchEvent(new CustomEvent('chart:load', { detail: { symbol } }));
  }

  _renderRow(item) {
    const sym = item.symbol;
    const isSelected = sym === this._selectedSymbol;
    const chg = Number(item.change_pct || 0);
    const chgCls = chg >= 0 ? 'ifx-tone-good' : 'ifx-tone-bad';

    const cellMap = {
      symbol: `<b class="ifx-mono sym-link">${escapeHtml(sym)}</b>`,
      sector_id: `<small>${escapeHtml(String(item.sector_id || DASH).replace(/_/g, ' '))}</small>`,
      ltp: `<span class="ifx-mono">${formatPrice(item.ltp)}</span>`,
      change_pct: `<span class="ifx-mono ${chgCls}">${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%</span>`,
      rel_vol: rvolHtml(item),
      breakout_type: breakoutTypeHtml(item),
      range_position: rangePositionHtml(item),
      vwap_state: `<span class="ifx-mono">${item.vwap_state === 'ABOVE' ? '▲' : item.vwap_state === 'BELOW' ? '▼' : DASH}</span>`,
      stock_breakout_score: scoreHtml(item),
      stock_breakout_tier: tierHtml(item),
      freshness: freshnessHtml(item),
      daily_trend: dailyTrendHtml(item),
    };
    const leftKeys = new Set(['symbol', 'sector_id']);
    const cells = visibleColumns(this._hidden)
      .map((c) => `<div class="ifx-scr-td${leftKeys.has(c.key) ? ' left' : ''}">${cellMap[c.key] ?? DASH}</div>`)
      .join('');

    return `
    <div class="ifx-scr-row${isSelected ? ' selected' : ''}" data-scr-sym="${escapeHtml(sym)}" data-symbol="${escapeHtml(sym)}">
      ${cells}
    </div>`;
  }

  destroy() {
    this._unsubs.forEach((fn) => fn());
  }
}
