/**
 * F&O Screener v2 — New shell's screener table. Started as the approved
 * mockup's 14-column spec (Symbol, Sector, LTP, Chg(₹+%), Strength,
 * Conviction, MTF, Bias, Chain Favour, Entry(CE/PE), T1, T2, T3, SL);
 * gained F&O (ban gate, Phase 13.13) and VCP (Phase 13.12) as columns
 * 15-16 on explicit request, mirroring the same two additions to
 * Classic's scanner.js -- same /api/ticks fields (fo_banned/
 * fo_ban_trade_date/vcp), same real ban-list and daily-bar-mtf-cache
 * sources, just rendered through this file's own badge/meter components.
 *

 * Data + ranking logic is NOT reimplemented -- smartRank/deriveDirectionZone
 * are imported straight from scanner.js (exported there for exactly this
 * reuse) so New's ranking can never quietly drift from Classic's already-
 * tuned heuristics. Same /api/ticks + /api/signals sources, same
 * live-vs-frozen distinction (a signal's entry/SL/target are frozen once
 * fired; everything else is a live, continuously-recomputed *_hint
 * projection) as Phase B's trust fix in Classic.
 *
 * Chain Favour (PCR-derived sentiment) is deliberately NOT bulk-fetched
 * for every row: a cold /api/options/chain-analytics call measured ~6s
 * per symbol live this session (fast once cached, slow on first hit) --
 * fetching that for 200+ rows on table load would make the table itself
 * feel broken. It populates on-demand for whichever row is selected,
 * same single-symbol-focus pattern options-analytics-panel.js already
 * uses elsewhere in this app.
 *
 * Rendering uses the real VirtualScroll (virtual-scroll.js) contract:
 * renderRow/keyFn/onRowClick are wired once in the constructor, and
 * _renderRow() is a pure function of (item, this._selectedSymbol,
 * this._chainFavourCache) -- selection/favour state changes call
 * vscroll.refresh() rather than hand-patching DOM nodes VirtualScroll
 * may replace wholesale on the next scroll-triggered render.
 */
import { VirtualScroll } from './virtual-scroll.js';
import { formatPrice, escapeHtml } from './utils.js';
import { api } from './api.js';
import { smartRank, deriveDirectionZone } from './scanner.js?v=8.0.0-new-shell';

const DASH = '—';

function mtfDotsHtml(dots) {
  const data = dots && typeof dots === 'object' ? dots : {};
  const cls = { G: 'ifx-mtf-dot--bull', R: 'ifx-mtf-dot--bear', Y: 'ifx-mtf-dot--flat' };
  return ['1M', '5M', '15M', '1H', '4H', '1D'].map((tf) => {
    const v = String(data[tf] || 'Y').toUpperCase();
    return `<i class="ifx-scr-mtf-dot ${cls[v] || cls.Y}" title="${tf}"></i>`;
  }).join('');
}

// F&O ban gate (Phase 13.13), same real infusion:nse:fo_ban:symbols read
// as Classic's scanner.js -- both tables share one /api/ticks response
// (see api/routes/ticks.py's _apply_fo_ban_context()), just rendered
// through New's own badge component here instead of Classic's chip.
function foBanHtml(item) {
  if (!item.fo_banned) return `<span class="ifx-scr-dash">${DASH}</span>`;
  const date = item.fo_ban_trade_date ? ` as of ${item.fo_ban_trade_date}` : '';
  return `<span class="ifx-badge ifx-badge--bear" title="NSE F&amp;O ban list (MWPL≥95%)${escapeHtml(date)} — no new F&amp;O positions allowed">BANNED</span>`;
}

// VCP / Minervini Stage-2 composite (Phase 13.12), see api/vcp.py. Same
// meter language New's Strength/Conviction columns already use. Honest
// dash (not a 0-value meter) when this row's daily-bar mtf cache hasn't
// been computed yet -- same cache-miss condition every other
// _decode_mtf_cache field already has.
function vcpHtml(item) {
  const vcp = item.vcp && typeof item.vcp === 'object' ? item.vcp : {};
  if (vcp.score == null) return `<span class="ifx-scr-dash">${DASH}</span>`;
  const score = Math.round(Number(vcp.score));
  const gradeLabel = vcp.grade === 'tight_vcp' ? 'Tight VCP'
    : vcp.grade === 'developing_base' ? 'Developing base'
    : 'No clear base';
  const color = score >= 80 ? 'var(--ifx-bull)' : score >= 55 ? 'var(--ifx-warn)' : 'var(--ifx-bear)';
  return `<div class="ifx-scr-meter" title="${escapeHtml(gradeLabel)}${vcp.reliable === false ? ' (partial read)' : ''}">
    <span class="ifx-mono">${score}</span>
    <div class="ifx-scr-meter-track"><i style="width:${Math.min(100, score)}%;background:${color}"></i></div>
  </div>`;
}

export class ScannerV2Panel {
  constructor(containerEl) {
    this._el = containerEl;
    this._data = new Map();       // symbol -> merged row
    this._signals = new Map();    // symbol -> signal payload
    this._biasLocks = new Map();  // symbol -> {bias, since} for anti-flip, mirrors scanner.js
    this._unsubs = [];
    this._sortKey = 'smart_rank';
    this._sortDir = -1;
    this._search = '';
    this._selectedSymbol = null;
    this._chainFavourCache = new Map(); // symbol -> {label, tone} | 'loading' | 'error'
    this._vscroll = null;
  }

  init() {
    if (!this._el) return;
    this._el.classList.add('ifx-scr-v2');
    this._el.innerHTML = `
      <div class="ifx-scr-toolbar">
        <div class="ifx-scr-search">🔍 <input type="text" placeholder="Search symbol…" id="scrV2Search" /></div>
        <span class="ifx-scr-count" id="scrV2Count"></span>
      </div>
      <div class="ifx-scr-table-wrap">
        <div class="ifx-scr-head" id="scrV2Head"></div>
        <div class="ifx-scr-body" id="scrV2Body"></div>
      </div>
    `;
    this._buildHead();

    this._vscroll = new VirtualScroll(this._el.querySelector('#scrV2Body'), {
      rowHeight: 40,
      overscan: 12,
      renderRow: (item) => this._renderRow(item),
      keyFn: (item) => item.symbol,
      onRowClick: (item) => this._select(item.symbol),
    });

    this._el.querySelector('#scrV2Search').addEventListener('input', (e) => {
      this._search = e.target.value.trim().toUpperCase();
      this._renderRows();
    });

    this._unsubs.push(api.subscribe('/api/ticks', (resp) => {
      if (!resp?.ticks) return;
      for (const t of resp.ticks) {
        const sym = t.symbol || t.sym;
        if (sym) this._mergeTick(sym, t);
      }
      this._renderRows();
    }, 5000));

    this._unsubs.push(api.subscribe('/api/signals', (resp) => {
      this._signals.clear();
      if (resp?.signals) for (const s of resp.signals) this._signals.set(s.symbol, s);
      this._applySignals();
      this._renderRows();
    }, 2000));

    document.addEventListener('signal:select', (e) => {
      const sym = e.detail?.symbol;
      if (sym) this._select(String(sym).toUpperCase());
    });
  }

  _mergeTick(symbol, data) {
    const existing = this._data.get(symbol) || { symbol };
    Object.assign(existing, data);
    existing.symbol = symbol;
    const zone = deriveDirectionZone(existing, this._biasLocks.get(symbol));
    existing.direction_bias = zone.bias;
    existing.direction_tone = zone.tone;
    if (zone.bias !== 'WAIT') {
      const prev = this._biasLocks.get(symbol);
      const since = prev?.bias === zone.bias ? prev.since : zone.updatedAt;
      this._biasLocks.set(symbol, { bias: zone.bias, since });
    }
    existing.signal_active = existing.signal_active || false;
    existing.smart_rank = smartRank(existing);
    this._data.set(symbol, existing);
  }

  _applySignals() {
    for (const [sym, sig] of this._signals) {
      const d = this._data.get(sym);
      if (!d) continue;
      const fs = sig.features_snapshot && typeof sig.features_snapshot === 'object' ? sig.features_snapshot : {};
      d.signal_active = true;
      d.signal_type = sig.signal_type || 'bullish';
      d.option_bias = sig.option_bias || '';
      d.entry_price = Number(sig.entry_price || 0);
      d.stop_price = Number(sig.invalidation_price || 0);
      d.target_price = Number(sig.target_price || 0);
      // t2/t3 for a fired signal: same fields cockpit.js already reads off
      // features_snapshot -- reused here, not re-derived, so a frozen row's
      // T2/T3 in this table can never disagree with what the cockpit shows
      // for the same signal.
      d.target2_price = Number(fs.t2_price || 0);
      d.target3_price = Number(fs.t3_price || 0);
      d.risk_reward_ratio = Number(sig.risk_reward_ratio || 0);
      d.conviction_score = Number(sig.conviction_score || 0);
      d.conviction_grade = sig.conviction_grade || '';
      d.smart_rank = smartRank(d);
    }
    for (const [sym, d] of this._data) {
      if (!this._signals.has(sym) && d.signal_active) {
        d.signal_active = false;
        d.smart_rank = smartRank(d);
      }
    }
  }

  _buildHead() {
    const cols = [
      ['symbol', 'Symbol'], ['sector_id', 'Sector'], ['ltp', 'LTP'], ['change_pct', 'Chg'],
      ['setup_strength', 'Strength'], ['option_readiness', 'Conviction'], ['mtf', 'MTF'],
      ['direction_bias', 'Bias'], ['favour', 'Chain Favour'], ['entry', 'Entry'],
      ['t1', 'T1'], ['t2', 'T2'], ['t3', 'T3'], ['sl', 'SL'],
      ['fo_banned', 'F&O'], ['vcp_score', 'VCP'],
    ];
    const head = this._el.querySelector('#scrV2Head');
    head.innerHTML = cols.map(([key, label]) =>
      `<div class="ifx-scr-th${key === this._sortKey ? ' sorted' : ''}" data-sort="${key}">${escapeHtml(label)}${key === this._sortKey ? `<span class="ifx-scr-arrow">${this._sortDir === -1 ? '▾' : '▴'}</span>` : ''}</div>`
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

  _sortValue(item, key) {
    switch (key) {
      case 'symbol': case 'sector_id': case 'direction_bias': return String(item[key] || '');
      case 'ltp': return Number(item.ltp || 0);
      case 'change_pct': return Number(item.change_pct || 0);
      case 'setup_strength': return Number(item.setup_strength || 0);
      case 'option_readiness': return Number(item.option_readiness || 0);
      default: return Number(item.smart_rank || 0);
    }
  }

  _renderRows() {
    let items = [...this._data.values()];
    if (this._search) items = items.filter((it) => String(it.symbol || '').includes(this._search));
    items.sort((a, b) => {
      const av = this._sortValue(a, this._sortKey), bv = this._sortValue(b, this._sortKey);
      if (typeof av === 'string') return av.localeCompare(bv) * this._sortDir;
      return (av - bv) * this._sortDir;
    });
    const count = this._el.querySelector('#scrV2Count');
    if (count) count.textContent = `${items.length} symbols`;
    this._vscroll.setData(items);
  }

  _select(symbol) {
    this._selectedSymbol = symbol;
    if (!this._chainFavourCache.has(symbol)) this._loadChainFavour(symbol);
    this._vscroll.refresh();
  }

  async _loadChainFavour(symbol) {
    this._chainFavourCache.set(symbol, 'loading');
    this._vscroll.refresh();
    try {
      const resp = await api.fetch(`/api/options/chain-analytics?symbol=${encodeURIComponent(symbol)}`);
      if (!resp?.ready) {
        this._chainFavourCache.set(symbol, { label: 'No data', tone: 'flat' });
      } else {
        const sentiment = String(resp.pcr?.sentiment || '').toLowerCase();
        let label, tone;
        if (sentiment.includes('bullish')) { label = 'Bullish'; tone = 'bull'; }
        else if (sentiment.includes('bearish')) { label = 'Bearish'; tone = 'bear'; }
        else if (sentiment.includes('neutral')) { label = 'Flatish'; tone = 'flat'; }
        else { label = 'Risk'; tone = 'risk'; }
        this._chainFavourCache.set(symbol, { label, tone });
      }
    } catch (_) {
      this._chainFavourCache.set(symbol, 'error');
    }
    // Only visually matters if this symbol is still the selection (the
    // trader may have already clicked elsewhere by the time this resolves)
    // -- refresh() is cheap and correct either way since it just re-runs
    // renderRow for whatever's currently visible.
    if (this._selectedSymbol === symbol) this._vscroll.refresh();
  }

  _favourCellHtml(symbol, isSelected) {
    if (!isSelected) return `<span class="ifx-scr-dash">${DASH}</span>`;
    const state = this._chainFavourCache.get(symbol);
    if (state === 'loading') return `<span class="ifx-scr-favour-loading">…</span>`;
    if (state === 'error' || !state) return `<span class="ifx-scr-dash">${DASH}</span>`;
    return `<span class="ifx-badge ifx-badge--${state.tone}">${escapeHtml(state.label)}</span>`;
  }

  _renderRow(item) {
    const sym = item.symbol;
    const isSelected = sym === this._selectedSymbol;
    const chg = Number(item.change_pct || 0);
    const chgAbs = Number(item.ltp || 0) * chg / 100;
    const chgCls = chg >= 0 ? 'ifx-tone-good' : 'ifx-tone-bad';
    const bias = String(item.direction_bias || 'WAIT').toUpperCase();
    const biasCls = bias.includes('CE') ? 'ce' : bias.includes('PE') ? 'pe' : 'wait';
    const strength = Number(item.setup_strength || 0);
    const conviction = Number(item.option_readiness || item.conviction_score || 0);
    const isFrozen = Boolean(item.signal_active);

    function lvl(px, cls) {
      if (!(px > 0)) return `<span class="ifx-scr-dash">${DASH}</span>`;
      return `<span class="ifx-scr-level ${isFrozen ? 'frozen' : 'live'} ${cls || ''}">${isFrozen ? '🔒 ' : ''}${formatPrice(px)}</span>`;
    }

    const entryPx = isFrozen ? item.entry_price : item.entry_price_hint;
    const t1Px = isFrozen ? item.target_price : item.target_1_hint;
    const t2Px = isFrozen ? item.target2_price : item.target_2_hint;
    const t3Px = isFrozen ? item.target3_price : item.target_3_hint;
    const slPx = isFrozen ? item.stop_price : item.stop_loss_hint;
    const entrySide = isFrozen ? (item.signal_type === 'bearish' ? 'pe' : 'ce') : (biasCls !== 'wait' ? biasCls : '');

    return `
    <div class="ifx-scr-row${isSelected ? ' selected' : ''}" data-scr-sym="${escapeHtml(sym)}">
      <div class="ifx-scr-td left"><b class="ifx-mono">${escapeHtml(sym)}</b></div>
      <div class="ifx-scr-td left"><small>${escapeHtml(String(item.sector_id || DASH).replace(/_/g, ' '))}</small></div>
      <div class="ifx-scr-td ifx-mono">${formatPrice(item.ltp)}</div>
      <div class="ifx-scr-td"><div class="ifx-scr-chg ${chgCls}"><span class="ifx-mono">${chgAbs >= 0 ? '+' : ''}${chgAbs.toFixed(2)}</span><small class="ifx-mono">${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%</small></div></div>
      <div class="ifx-scr-td"><div class="ifx-scr-meter"><span class="ifx-mono">${strength ? Math.round(strength) : DASH}</span><div class="ifx-scr-meter-track"><i style="width:${Math.min(100, strength)}%;background:${strength >= 60 ? 'var(--ifx-bull)' : strength >= 45 ? 'var(--ifx-warn)' : 'var(--ifx-bear)'}"></i></div></div></div>
      <div class="ifx-scr-td"><div class="ifx-scr-meter"><span class="ifx-mono">${conviction ? Math.round(conviction) : DASH}</span><div class="ifx-scr-meter-track"><i style="width:${Math.min(100, conviction)}%;background:${conviction >= 55 ? 'var(--ifx-bull)' : conviction >= 45 ? 'var(--ifx-warn)' : 'var(--ifx-bear)'}"></i></div></div></div>
      <div class="ifx-scr-td"><div class="ifx-scr-mtf">${mtfDotsHtml(item.mtf_dots)}</div></div>
      <div class="ifx-scr-td"><span class="ifx-badge ifx-badge--${biasCls === 'ce' ? 'bull' : biasCls === 'pe' ? 'bear' : 'warn'}">${escapeHtml(bias)}</span></div>
      <div class="ifx-scr-td">${this._favourCellHtml(sym, isSelected)}</div>
      <div class="ifx-scr-td">${entryPx > 0 ? `<span class="ifx-scr-entry">${entrySide ? `<em class="ifx-scr-side ${entrySide}">${entrySide.toUpperCase()}</em>` : ''}${lvl(entryPx)}</span>` : `<span class="ifx-scr-dash">${DASH}</span>`}</div>
      <div class="ifx-scr-td">${lvl(t1Px, 'ifx-tone-good')}</div>
      <div class="ifx-scr-td">${lvl(t2Px, 'ifx-tone-good')}</div>
      <div class="ifx-scr-td">${lvl(t3Px, 'ifx-tone-good')}</div>
      <div class="ifx-scr-td">${lvl(slPx, 'ifx-tone-bad')}</div>
      <div class="ifx-scr-td">${foBanHtml(item)}</div>
      <div class="ifx-scr-td">${vcpHtml(item)}</div>
    </div>`;
  }

  destroy() {
    this._unsubs.forEach((fn) => fn());
  }
}
