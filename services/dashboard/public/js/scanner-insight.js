/**
 * Scanner Insight Panel
 * Right-side evidence stack for the selected/best scanner row.
 */
import { api } from './api.js';
import { escapeHtml, formatPct, formatPrice, formatRelVol } from './utils.js';

function n(value, fallback = 0) {
  const num = Number(value);
  return Number.isFinite(num) ? num : fallback;
}

function scoreTone(score) {
  if (score >= 80) return 'buy';
  if (score >= 55) return 'hold';
  return 'sell';
}

function decisionTone(decision) {
  const d = String(decision || '').toUpperCase();
  if (d.includes('PE') || d === 'SELL') return 'sell';
  if (d.includes('CE') || d === 'BUY') return 'buy';
  return 'hold';
}

function gate(label, state, detail = '') {
  const normalized = String(state || '').toUpperCase();
  const pass = ['PASS', 'BULL', 'ABOVE'].includes(normalized);
  const fail = ['FAIL', 'BEAR', 'BELOW', 'WARN'].includes(normalized);
  const cls = pass ? 'pass' : fail ? 'fail' : 'wait';
  const mark = pass ? 'OK' : fail ? '!' : '..';
  return `
    <div class="insight-gate ${cls}">
      <span>${escapeHtml(mark)}</span>
      <b>${escapeHtml(label)}</b>
      <small>${escapeHtml(detail || normalized || 'Waiting')}</small>
    </div>
  `;
}

function reasonPills(items, cls) {
  if (!Array.isArray(items) || !items.length) {
    return `<span class="insight-pill muted">No reason data yet</span>`;
  }
  return items.map(x => `<span class="insight-pill ${cls}">${escapeHtml(String(x))}</span>`).join('');
}

// Phase R5 -- mirrors breakout-radar.js's BREAKOUT_TYPE_META and
// api/routes/ticks.py's _classify_breakout_type vocabulary exactly; keep
// in sync if that list changes.
const BREAKOUT_TYPE_LABELS = {
  vwap_reclaim: 'VWAP Reclaim', vwap_rejection: 'VWAP Rejection',
  day_high_break: 'Day High Break', day_low_break: 'Day Low Break',
  above_vwap_continuation: 'Above VWAP Continuation', below_vwap_continuation: 'Below VWAP Continuation',
  volume_surge: 'Volume Surge', failed_no_chase: 'Failed / No-Chase',
};

const BREAKOUT_TIER_NEXT_STEP = {
  BREAKOUT_NOW: 'Confirmed underlying evidence. Watch for a clean retest before chasing, or manage size if already in.',
  OPTION_READY: 'Stock evidence AND contract quality both confirm -- the strongest state this radar reaches.',
  RETEST_ENTRY: 'Breakout evidence is there, but chase quality is poor right now. Wait for a safer pullback/reclaim.',
  EARLY_WATCH: 'Early evidence only -- volume or price location is moving but not yet confirmed. Watch, don’t act.',
  NO_CHASE: 'Doesn’t clear the bar right now. No action.',
};

function mtfDots(dots) {
  const data = dots && typeof dots === 'object' ? dots : {};
  const tone = { G: 'good', R: 'bad', Y: 'warn' };
  return ['1M', '5M', '15M', '1H', '4H', '1D'].map(tf => {
    const v = String(data[tf] || 'Y').toUpperCase();
    return `<span class="insight-pill ${tone[v] || 'warn'}">${escapeHtml(tf)} ${v === 'G' ? 'BUY' : v === 'R' ? 'SELL' : 'MIX'}</span>`;
  }).join('');
}

export class ScannerInsight {
  constructor(containerEl) {
    this._el = containerEl;
    this._ticks = [];
    this._selectedSymbol = '';
    this._option = null;
    this._mtf = null;
    this._ai = null;
    this._aiMode = '';
    this._aiLoading = false;
    this._optionToken = 0;
    this._mtfToken = 0;
    this._aiToken = 0;
    this._unsubs = [];
    this._clickHandler = null;
    // Phase 13.9/13.10/13.11 -- alignment/Kelly/RSI-divergence are only
    // ever computed at signal-fire time (inside options_first_hybrid.py/
    // vol_vwap_breakout.py's evaluate(), not on every live tick), so
    // they're only meaningful for whichever FIRED signal was last
    // selected -- not for "whatever symbol is currently in Stock Detail"
    // in general. Kept as its own piece of state, cleared whenever the
    // selection moves to a symbol that isn't backed by a fired signal.
    this._activeSignal = null;
    // Phase O.1 -- collapse state for the supporting-evidence sub-sections
    // below (Pine/MTF, Trend analyser, Extended Signals, VCP, Signal-time
    // evidence). Held on the instance (not localStorage) since it just
    // needs to survive this panel's own periodic re-renders, not the page
    // session -- see _subsection()'s doc comment for why the existing
    // section-controls.js mechanism doesn't fit here.
    this._collapsed = {};
  }

  init() {
    if (!this._el) return;
    this._render();
    this._el.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-subsection-toggle]');
      if (!btn) return;
      const key = btn.dataset.subsectionToggle;
      this._collapsed[key] = !this._isCollapsed(key);
      this._render();
    });

    this._unsubs.push(api.subscribe('/api/ticks', (resp) => {
      this._ticks = Array.isArray(resp?.ticks) ? resp.ticks : [];
      if (!this._selectedSymbol && this._ticks.length) {
        this._selectedSymbol = this._bestSymbol();
        this._loadOption(this._selectedSymbol);
        this._loadMTF(this._selectedSymbol);
      }
      this._render();
    }, 5000));

    document.addEventListener('chart:load', (e) => {
      const sym = e.detail?.symbol;
      const sig = e.detail?.signal;
      this._activeSignal = (sig && sig.symbol === sym) ? sig : null;
      if (sym) this.select(sym);
      else this._render();
    });

    // cockpit.js dispatches this alongside chart:load with the full fired
    // signal (features_snapshot/sub_scores included) -- chart:load above
    // already captures it when present, this covers any future dispatch
    // site that sends signal:select on its own.
    document.addEventListener('signal:select', (e) => {
      const sig = e.detail;
      if (sig && sig.symbol) {
        this._activeSignal = sig;
        this._render();
      }
    });

    this._clickHandler = (e) => {
      const symLink = e.target.closest('.sym-link');
      const row = e.target.closest('[data-symbol],[data-key]');
      const symbol = row?.dataset?.symbol || row?.dataset?.key || symLink?.textContent;
      if (symbol) this.select(String(symbol).trim().toUpperCase());
    };
    document.addEventListener('click', this._clickHandler);
  }

  select(symbol) {
    if (!symbol || this._selectedSymbol === symbol) return;
    this._selectedSymbol = symbol;
    if (this._activeSignal && this._activeSignal.symbol !== symbol) this._activeSignal = null;
    this._ai = null;
    this._aiMode = '';
    this._mtf = null;
    this._loadOption(symbol);
    this._loadMTF(symbol);
    this._render();
  }

  async _loadAI(mode) {
    const symbol = this._selectedSymbol;
    if (!symbol || this._aiLoading) return;
    this._aiLoading = true;
    this._aiMode = mode;
    this._render();
    const token = ++this._aiToken;
    const resp = await api.post(
      `/api/ai/analyze/${encodeURIComponent(symbol)}`,
      { mode }
    );
    if (token === this._aiToken) {
      this._ai = resp || {
        verdict: 'WATCH',
        summary: 'AI advisory is temporarily unavailable. Deterministic scanner data remains active.',
        why_trade: [],
        blockers: ['AI response unavailable'],
        trigger: '',
        invalidation: '',
        option_view: '',
        risk_note: '',
        source: 'unavailable',
      };
      this._aiLoading = false;
      this._render();
    }
  }

  _bestSymbol() {
    const best = [...this._ticks].sort((a, b) =>
      n(b.option_readiness) - n(a.option_readiness) ||
      n(b.setup_strength) - n(a.setup_strength) ||
      n(b.rel_vol) - n(a.rel_vol)
    )[0];
    return best?.symbol || '';
  }

  async _loadOption(symbol) {
    if (!symbol) return;
    const token = ++this._optionToken;
    const resp = await api.fetch(`/api/options/summary?symbol=${encodeURIComponent(symbol)}`);
    if (token === this._optionToken) {
      this._option = resp || null;
      this._render();
    }
  }

  async _loadMTF(symbol) {
    if (!symbol) return;
    const token = ++this._mtfToken;
    const resp = await api.fetch(`/api/mtf/${encodeURIComponent(symbol)}`);
    if (token === this._mtfToken) {
      this._mtf = resp || null;
      this._render();
    }
  }

  _current() {
    const sym = this._selectedSymbol;
    return this._ticks.find(x => String(x.symbol || '').toUpperCase() === sym) ||
           this._ticks.find(x => x.symbol === this._bestSymbol()) ||
           null;
  }

  /** Phase 13.5/13.7 -- 52-week range, VWAP SD bands, delivery %,
   * Heiken-Ashi. All informational, none wired into the conviction score
   * -- same "not applied until feature-ablation earns it" governance as
   * everything else these come from. week52 rides on the same /api/mtf
   * fetch scanner-insight.js already does for MTF; the rest come straight
   * off the /api/ticks row (feature-engine's hot-state hash, see
   * feature_engine/main.py's HOT_STATE_ML_WHITELIST). */
  // Phase O.1 -- supporting-evidence sections (everything below the core
  // trade map) default to collapsed, showing a one-line derived verdict
  // instead of the full raw breakdown, per the "Main Dashboard Card"
  // discipline in the SMC/option-chain reference doc: state up front,
  // detail on demand. Not persisted to localStorage -- see constructor.
  _isCollapsed(key) {
    return this._collapsed[key] !== false;
  }

  _subsection(key, title, summary, bodyHtml) {
    const collapsed = this._isCollapsed(key);
    return `
      <div class="insight-section insight-subsection ${collapsed ? 'is-collapsed' : ''}">
        <button type="button" class="insight-subsection-head" data-subsection-toggle="${key}">
          <span class="insight-subsection-title">${title}</span>
          <span class="insight-subsection-summary">${summary}</span>
          <span class="insight-subsection-chevron" aria-hidden="true">›</span>
        </button>
        <div class="insight-subsection-body">${bodyHtml}</div>
      </div>
    `;
  }

  // Phase R5 -- Selected Stock Detail's own read of R1/R2's Stock
  // Breakout Radar fields (stock_breakout_score/_tier, breakout_type,
  // rel_vol, volume_profile_ready, day_high/day_low). Same /api/ticks
  // row every other section on this page already has -- no new fetch.
  _renderBreakoutEvidence(item) {
    const score = item.stock_breakout_score;
    const max = item.stock_breakout_score_max || 90;
    const tier = item.stock_breakout_tier;
    const typeLabel = BREAKOUT_TYPE_LABELS[item.breakout_type] || 'No qualifying evidence right now';
    const nextStep = BREAKOUT_TIER_NEXT_STEP[tier] || 'Not enough evidence to rank this setup yet.';
    const rvol = n(item.rel_vol);
    const volReady = item.volume_profile_ready !== false;
    const summary = score != null ? `${Number(score).toFixed(1)}/${max} · ${tier || '—'}` : 'no data yet';

    const high = n(item.day_high), low = n(item.day_low), ltp = n(item.ltp);
    const rangePos = (high > 0 && low > 0 && high > low)
      ? Math.round(Math.min(100, Math.max(0, ((ltp - low) / (high - low)) * 100)))
      : null;

    const body = `
        <div class="option-mini-grid">
          <div><span>Stock Score</span><b class="${scoreTone((Number(score || 0) / max) * 100)}" title="Sector/index relative strength not computed yet -- score is out of ${max}, not 100">${score != null ? Number(score).toFixed(1) : '—'}/${max}</b></div>
          <div><span>Tier</span><b>${escapeHtml(tier || '—')}</b></div>
          <div><span>Type</span><b>${escapeHtml(typeLabel)}</b></div>
        </div>
        <div class="trade-level-grid compact">
          <div><span>RVol</span><b class="${volReady ? (rvol >= 2.5 ? 'positive' : '') : ''}">${volReady ? `${rvol.toFixed(2)}x` : 'BASELINE MISSING'}</b></div>
          <div><span>Day High</span><b>${formatPrice(item.day_high)}</b></div>
          <div><span>Day Low</span><b>${formatPrice(item.day_low)}</b></div>
          <div><span>Range position</span><b>${rangePos != null ? `${rangePos}%` : '—'}</b></div>
          <div><span>VWAP</span><b class="${item.vwap_state === 'ABOVE' ? 'positive' : item.vwap_state === 'BELOW' ? 'negative' : ''}">${escapeHtml(item.vwap_state || '—')}</b></div>
        </div>
        ${!volReady ? `<div class="insight-pills"><span class="insight-pill warn">Volume baseline hasn't bootstrapped for this symbol -- RVol above is unknown, not confirmed low</span></div>` : ''}
        <p class="option-reason">${escapeHtml(nextStep)}</p>
        <p class="option-reason">Ranks the underlying stock's own breakout evidence, independent of option-chain readiness -- see the Stock Breakout Radar panel for the full, sortable list.</p>
    `;
    return this._subsection('breakout', 'Breakout Evidence', summary, body);
  }

  _renderExtendedSignals(item, trueMtf) {
    const week52 = trueMtf?.week52 || {};
    const haTrend = String(item.ha_trend || 'NA').toUpperCase();
    const haTone = haTrend === 'BULL' ? 'positive' : haTrend === 'BEAR' ? 'negative' : '';
    // Number.isFinite (not just != null) guards against "" or any other
    // non-numeric value that survives a Redis string round-trip --
    // caught live: an empty-string delivery_pct_avg_20d (the real "no
    // rolling average yet" sentinel) passed a plain != null check and
    // crashed .toFixed() on a string.
    // delivery_pct is a SymbolState default of 0.0 until nse-scraper's
    // capture actually runs for this symbol -- indistinguishable from a
    // genuine 0% delivery day unless gated on delivery_trade_date
    // actually being set (caught live: NATIONALUM showed a plausible-
    // looking "0.0%" that was really "never captured", not a real value).
    const deliveryCaptured = Boolean(item.delivery_trade_date);
    const deliveryPctRaw = Number(item.delivery_pct);
    const deliveryAvgRaw = Number(item.delivery_pct_avg_20d);
    const deliveryPct = (deliveryCaptured && Number.isFinite(deliveryPctRaw)) ? deliveryPctRaw : null;
    const deliveryAvg = (deliveryCaptured && Number.isFinite(deliveryAvgRaw)) ? deliveryAvgRaw : null;
    const deliveryDelta = (deliveryPct != null && deliveryAvg != null) ? deliveryPct - deliveryAvg : null;

    const summaryParts = [];
    if (haTrend !== 'NA') summaryParts.push(`HA ${haTrend}${n(item.ha_trend_streak) ? ' ×' + n(item.ha_trend_streak) : ''}`);
    if (week52.week52_near_high) summaryParts.push('near 52W high');
    else if (week52.week52_near_low) summaryParts.push('near 52W low');
    if (deliveryPct != null) summaryParts.push(`delivery ${deliveryPct.toFixed(1)}%`);
    const summary = summaryParts.length ? summaryParts.join(' · ') : 'warming up';

    const body = `
        <div class="trade-level-grid compact">
          <div><span>52W High</span><b>${formatPrice(week52.week52_high)}</b></div>
          <div><span>52W Low</span><b>${formatPrice(week52.week52_low)}</b></div>
          <div><span>From high</span><b class="${n(week52.week52_high_distance_pct) >= 0 ? 'positive' : ''}">${formatPct(week52.week52_high_distance_pct)}</b></div>
          <div><span>From low</span><b class="positive">${formatPct(week52.week52_low_distance_pct)}</b></div>
          <div><span>VWAP SD1</span><b>${formatPrice(item.vwap_sd1_lower)} – ${formatPrice(item.vwap_sd1_upper)}</b></div>
          <div><span>VWAP SD2</span><b>${formatPrice(item.vwap_sd2_lower)} – ${formatPrice(item.vwap_sd2_upper)}</b></div>
          <div><span>Delivery %</span><b>${deliveryPct != null ? deliveryPct.toFixed(1) + '%' : '—'}</b></div>
          <div><span>Delivery 20D avg</span><b>${deliveryAvg != null ? deliveryAvg.toFixed(1) + '%' : '—'}</b></div>
          <div><span>Heiken-Ashi</span><b class="${haTone}">${haTrend === 'NA' ? 'No streak yet' : `${haTrend} ×${n(item.ha_trend_streak)}`}</b></div>
        </div>
        <div class="insight-pills">
          ${week52.week52_near_high ? `<span class="insight-pill warn">Near 52W high</span>` : ''}
          ${week52.week52_near_low ? `<span class="insight-pill good">Near 52W low</span>` : ''}
          ${item.vwap_sd_ready === false ? `<span class="insight-pill muted">VWAP bands warming up</span>` : ''}
          ${item.ha_doji ? `<span class="insight-pill warn">Heiken-Ashi doji${item.ha_color_flip ? ' + color flip' : ''}</span>` : ''}
          ${deliveryDelta != null && Math.abs(deliveryDelta) >= 10 ? `<span class="insight-pill ${deliveryDelta > 0 ? 'good' : 'bad'}">Delivery ${deliveryDelta > 0 ? 'above' : 'below'} 20D avg by ${Math.abs(deliveryDelta).toFixed(1)}pt</span>` : ''}
        </div>
        <p class="option-reason">Informational only — not wired into the conviction score. Delivery % is T-1 (NSE has no live intraday delivery feed); VWAP bands and Heiken-Ashi need a few completed 1-minute bars before they're meaningful.</p>
    `;
    return this._subsection('extended', 'Extended signals', summary, body);
  }

  /** Phase 13.12 -- VCP (Volatility Contraction Pattern) / Minervini
   * Stage-2 composite. Daily-timeframe, same continuous-per-symbol shape
   * as Extended Signals above (recomputed inside /api/mtf's 300s-cached
   * compute_mtf(), not frozen at signal time) -- reads off trueMtf.vcp,
   * not the /api/ticks row, so it's live for whatever symbol Stock Detail
   * is showing right now, fired signal or not. See api/vcp.py. */
  _renderVcp(trueMtf) {
    const vcp = trueMtf?.vcp;
    if (!vcp || vcp.available === false) {
      return this._subsection('vcp', 'VCP / Minervini Stage-2', 'not available',
        `<p class="option-reason">${escapeHtml(vcp?.reason || 'No daily bar history cached for this symbol yet.')}</p>`);
    }

    const comp = vcp.components || {};
    const tt = comp.trend_template || {};
    const cq = comp.contraction_quality || {};
    const vd = comp.volume_dryup || {};
    const pp = comp.pivot_proximity || {};
    const rs = comp.relative_strength || {};

    const gradeMeta = {
      tight_vcp: { label: 'Tight VCP', tone: 'positive' },
      developing_base: { label: 'Developing base', tone: 'warn' },
      no_clear_base: { label: 'No clear base', tone: '' },
    }[vcp.grade] || { label: vcp.grade || '-', tone: '' };

    const compScore = (part, max) => part && part.available
      ? `${Number(part.score).toFixed(1)}/${max}`
      : 'n/a';

    const summary = `${n(vcp.score)}/100 · ${gradeMeta.label}`;
    const body = `
        <div class="option-mini-grid">
          <div><span>Composite</span><b class="${gradeMeta.tone}">${n(vcp.score)}/100</b></div>
          <div><span>Trend template</span><b>${tt.available ? `${tt.checks_passed}/${tt.checks_total}` : '—'}</b></div>
          <div><span>Contractions</span><b>${cq.available ? cq.contractions_found : '—'}</b></div>
        </div>
        <div class="trade-level-grid compact">
          <div><span>Trend template</span><b>${compScore(tt, 25)}</b></div>
          <div><span>Contraction quality</span><b>${compScore(cq, 25)}</b></div>
          <div><span>Volume dry-up</span><b>${compScore(vd, 20)}</b></div>
          <div><span>Pivot proximity</span><b>${compScore(pp, 15)}</b></div>
          <div><span>RS vs Nifty 50</span><b>${compScore(rs, 15)}</b></div>
          <div><span>Base pivot</span><b>${pp.available ? formatPrice(pp.pivot) : '—'}</b></div>
        </div>
        <div class="insight-pills">
          ${!vcp.reliable ? `<span class="insight-pill muted">Partial read — one or more components lack enough history</span>` : ''}
          ${rs.available ? `<span class="insight-pill ${rs.rs_diff_pct >= 0 ? 'good' : 'bad'}">RS ${rs.rs_diff_pct >= 0 ? '+' : ''}${rs.rs_diff_pct}% vs Nifty (${rs.lookback_days}d)</span>` : ''}
          ${vd.available ? `<span class="insight-pill ${vd.volume_ratio <= 0.7 ? 'good' : ''}">Volume ratio ${vd.volume_ratio}×</span>` : ''}
        </div>
        <p class="option-reason">Informational only — not wired into the conviction score. Daily-timeframe composite: Trend Template 25% / Contraction Quality 25% / Volume Dry-Up 20% / Pivot Proximity 15% / Relative Strength vs Nifty 50 15%.</p>
    `;
    return this._subsection('vcp', 'VCP / Minervini Stage-2', summary, body);
  }

  /** Phase 13.9/13.10/13.11 -- signal alignment, half-Kelly sizing, and
   * RSI divergence are all computed once, at signal-fire time, inside the
   * strategy that produced the trade -- not on every live tick like the
   * Extended Signals section above. So this section is scoped to
   * whichever fired signal card was last clicked in the Cockpit
   * (this._activeSignal), not to "whatever symbol Stock Detail happens to
   * be showing." An honest empty state when there's no active signal for
   * the current symbol, rather than silently showing nothing. */
  _renderSignalTimeEvidence(symbol) {
    const sig = this._activeSignal;
    if (!sig || sig.symbol !== symbol) {
      return this._subsection('evidence', 'Signal-time evidence', 'no fired signal selected',
        `<p class="option-reason">Click a card in the Signal Cockpit to see the alignment breadth, half-Kelly sizing read, and RSI divergence that were computed for that specific fired signal — these aren't live per-tick numbers.</p>`);
    }

    const fs = sig.features_snapshot && typeof sig.features_snapshot === 'object' ? sig.features_snapshot : {};
    const subScores = sig.sub_scores && typeof sig.sub_scores === 'object' ? sig.sub_scores : {};
    const sizing = subScores.position_sizing || {};
    const ml = subScores.ml_classifier || {};

    const agree = n(fs.alignment_agree_count);
    const checked = n(fs.alignment_checked_count);
    const total = n(fs.alignment_total_families, 8);
    const agreeTone = checked > 0 && agree / checked >= 0.6 ? 'positive' : checked > 0 && agree / checked <= 0.3 ? 'negative' : '';

    const kellyReliable = !!sizing.kelly_reliable;
    const kellyPct = sizing.kelly_half_pct;

    const divergencePills = [
      fs.rsi_divergence_bullish_regular ? { label: 'Regular bullish divergence', cls: 'good' } : null,
      fs.rsi_divergence_bullish_hidden ? { label: 'Hidden bullish divergence', cls: 'good' } : null,
      fs.rsi_divergence_bearish_regular ? { label: 'Regular bearish divergence', cls: 'bad' } : null,
      fs.rsi_divergence_bearish_hidden ? { label: 'Hidden bearish divergence', cls: 'bad' } : null,
    ].filter(Boolean);

    const mlPct = ml.ml_probability != null ? Math.round(ml.ml_probability * 100) : null;
    const mlTone = mlPct == null ? '' : mlPct >= 60 ? 'positive' : mlPct <= 40 ? 'negative' : '';

    const summary = checked > 0
      ? `${agree}/${checked} of ${total} aligned${mlPct != null ? ` · ML ${mlPct}%` : ''}`
      : 'no families active';
    const body = `
        <div class="trade-level-grid compact">
          <div><span>Alignment</span><b class="${agreeTone}">${checked > 0 ? `${agree}/${checked} of ${total}` : 'no families active'}</b></div>
          <div><span>Half-Kelly size</span><b>${kellyReliable ? kellyPct + '%' : 'not enough sample'}</b></div>
          <div><span>Kelly win rate</span><b>${sizing.kelly_win_rate_pct != null ? sizing.kelly_win_rate_pct + '%' : '—'}</b></div>
          <div><span>Kelly sample</span><b>${sizing.kelly_sample_size || 0} decided</b></div>
          <div><span>India VIX at signal</span><b>${sizing.vix_level != null ? sizing.vix_level : '—'}</b></div>
          <div><span>VIX size multiplier</span><b>${sizing.vix_tier ? `${escapeHtml(sizing.vix_tier)} · ${sizing.vix_size_multiplier_pct}%` : 'not scored'}</b></div>
          <div><span>ML classifier</span><b class="${mlTone}">${mlPct != null ? `${mlPct}% TARGET_HIT` : 'not scored'}</b></div>
          <div><span>ML model quality</span><b>${ml.ml_reliable ? `AUC ${ml.ml_model_auc}` : 'building sample'}</b></div>
        </div>
        ${checked > 0 ? `
        <div class="insight-pills">
          ${(fs.alignment_agreeing_families || []).map(f => `<span class="insight-pill good">${escapeHtml(f)}</span>`).join('')}
          ${(fs.alignment_disagreeing_families || []).map(f => `<span class="insight-pill bad">${escapeHtml(f)}</span>`).join('')}
        </div>` : ''}
        ${divergencePills.length ? `
        <div class="insight-pills">
          ${divergencePills.map(p => `<span class="insight-pill ${p.cls}">${escapeHtml(p.label)}</span>`).join('')}
        </div>` : '<p class="option-reason">No RSI divergence at this signal\'s swing points.</p>'}
        ${ml.ml_model_interpretation ? `<p class="option-reason">ML model: ${escapeHtml(ml.ml_model_interpretation)}</p>` : ''}
        <p class="option-reason">Informational only — none of this is wired into the conviction score or position sizing yet. Frozen at signal time, same as the trade levels above.</p>
    `;
    return this._subsection('evidence', 'Signal-time evidence', summary, body);
  }

  _render() {
    if (!this._el) return;
    const item = this._current();
    if (!item) {
      this._el.innerHTML = `<div class="scanner-insight-empty">Waiting for scanner data...</div>`;
      return;
    }

    const symbol = item.symbol || this._selectedSymbol || '-';
    const decision = item.trade_decision || 'WAIT';
    const opt = n(item.option_readiness);
    const setup = n(item.setup_strength);
    const trend = n(item.trend_score);
    const conviction = item.conviction_label || (opt >= 85 ? 'A+' : opt >= 75 ? 'A' : opt >= 60 ? 'B' : opt >= 45 ? 'C' : 'D');
    const tone = scoreTone(opt);
    const option = this._option || {};
    const trueMtf = this._mtf && String(this._mtf.symbol || '').toUpperCase() === String(symbol).toUpperCase()
      ? this._mtf
      : null;
    const mtfDotsData = trueMtf?.mtf_dots || trueMtf?.dots || item.mtf_dots;
    const mtfText = trueMtf?.mtf_text || trueMtf?.alignment || item.mtf_text || 'MTF alignment is building from scanner evidence.';
    const mtfSource = trueMtf?.source || item.mtf_source || 'live proxy';
    const mtfScore = n(trueMtf?.score ?? item.mtf_score, opt);
    const mtfWarnings = trueMtf?.warnings || item.mtf_warnings || [];
    const optionReady = Boolean(option.option_chain_ready);
    const optionTradeReady = Boolean(option.trade_ready);
    const optionStatus = option.execution_status || (optionReady ? 'WAIT_CONTRACT' : 'CHAIN_PENDING');
    const optionBlockers = Array.isArray(option.hard_blockers) && option.hard_blockers.length
      ? option.hard_blockers
      : Array.isArray(option.blockers) ? option.blockers : [];
    const bias = String(item.trend_bias || '').toUpperCase();
    const vwapGate = bias === 'SELL'
      ? (item.vwap_state === 'BELOW' ? 'PASS' : 'WARN')
      : bias === 'BUY'
        ? (item.vwap_state === 'ABOVE' ? 'PASS' : 'WARN')
        : item.vwap_state;
    const emaGate = bias === 'SELL'
      ? (item.ema_state === 'BEAR' ? 'PASS' : 'WARN')
      : bias === 'BUY'
        ? (item.ema_state === 'BULL' ? 'PASS' : 'WARN')
        : item.ema_state;
    const macdGate = bias === 'SELL'
      ? (item.macd_state === 'BEAR' ? 'PASS' : 'WARN')
      : bias === 'BUY'
        ? (item.macd_state === 'BULL' ? 'PASS' : 'WARN')
        : item.macd_state;
    const atrGate = bias === 'SELL'
      ? (item.atr_state === 'BEAR' ? 'PASS' : 'WARN')
      : bias === 'BUY'
        ? (item.atr_state === 'BULL' ? 'PASS' : 'WARN')
        : item.atr_state;
    const patternText = [item.nr_pattern, item.squeeze_state, item.candle_pattern]
      .filter(x => x && String(x).toUpperCase() !== 'NA')
      .join(' · ') || 'Waiting for setup';
    // Phase O.1 -- derived pass-count for the Trend analyser subsection's
    // collapsed-state summary. Same PASS vocabulary as the gate() helper.
    const trendGateStates = [
      vwapGate, emaGate, macdGate, atrGate,
      item.trend_stack?.rsi, item.trend_stack?.volume,
      item.trend_stack?.compression, item.trend_stack?.pattern,
    ];
    const trendGatePassCount = trendGateStates
      .filter(s => ['PASS', 'BULL', 'ABOVE'].includes(String(s || '').toUpperCase())).length;
    const actionTone = decisionTone(decision);
    const ai = this._ai;
    const aiTone = ai?.verdict === 'TRADE_READY' ? 'buy' : ai?.verdict === 'AVOID' ? 'sell' : 'hold';
    const aiSource = ai?.source === 'openai'
      ? `ChatGPT · ${ai.cached ? 'cached' : 'fresh'}`
      : ai?.source === 'deterministic_fallback' ? 'Deterministic fallback' : '';
    const tradingViewUrl = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(`NSE:${symbol}`)}`;

    this._el.innerHTML = `
      <div class="insight-hero ${actionTone}">
        <div>
          <span class="insight-kicker">Selected setup</span>
          <h3>${escapeHtml(symbol)}</h3>
          <div class="insight-price">
            <b>${formatPrice(item.ltp)}</b>
            <span class="${n(item.change_pct) >= 0 ? 'positive' : 'negative'}">${formatPct(item.change_pct)}</span>
            <em>${escapeHtml(item.sector_id || '-')}</em>
          </div>
          <a class="tv-open-btn" href="${tradingViewUrl}" target="_blank" rel="noreferrer">Open in TradingView</a>
        </div>
        <div class="insight-action ${actionTone}">
          <span>${escapeHtml(decision)}</span>
          <b>${escapeHtml(conviction)}</b>
        </div>
      </div>

      <div class="insight-score-grid">
        <div><label>Trend</label><strong class="${scoreTone(trend)}">${Math.round(trend)}</strong><i style="width:${Math.min(trend,100)}%"></i></div>
        <div><label>Setup</label><strong class="${scoreTone(setup)}">${Math.round(setup)}</strong><i style="width:${Math.min(setup,100)}%"></i></div>
        <div><label>Option</label><strong class="${tone}">${Math.round(opt)}</strong><i style="width:${Math.min(opt,100)}%"></i></div>
      </div>

      <div class="insight-section trade-levels">
        <div class="insight-title">Trade map: trigger, risk, targets</div>
        <div class="trade-level-grid">
          <div><span>Turns bullish above</span><b class="positive">${formatPrice(item.positive_above)}</b></div>
          <div><span>Turns bearish below</span><b class="negative">${formatPrice(item.negative_below)}</b></div>
          <div><span>Entry hint</span><b>${formatPrice(item.entry_price_hint)}</b></div>
          <div><span>Stop loss</span><b class="negative">${formatPrice(item.stop_loss_hint)}</b></div>
          <div><span>Target 1</span><b class="positive">${formatPrice(item.target_1_hint)}</b></div>
          <div><span>Target 2</span><b class="positive">${formatPrice(item.target_2_hint)}</b></div>
        </div>
        <div class="trade-level-grid compact">
          <div><span>Fibo pivot</span><b>${formatPrice(item.fibo_pivot)}</b></div>
          <div><span>Fibo S2</span><b>${formatPrice(item.fibo_s2)}</b></div>
          <div><span>Fibo R2</span><b>${formatPrice(item.fibo_r2)}</b></div>
          <div><span>S/R</span><b>${escapeHtml(item.sr_status || '-')}</b></div>
          <div><span>Alignment</span><b>${escapeHtml(item.alignment || '-')}</b></div>
          <div><span>Type</span><b>${escapeHtml(item.type || '-')}</b></div>
        </div>
        <p class="option-reason">Levels are Phase-1 live proxies from Upstox scanner features. Final execution still needs your visual TradingView confirmation.</p>
      </div>

      ${this._renderBreakoutEvidence(item)}

      ${this._subsection('pine', 'Pine-style confidence + MTF', `MTF ${Math.round(mtfScore)} · CE ${Math.round(n(item.bull_confidence))} / PE ${Math.round(n(item.bear_confidence))}`, `
        <div class="option-mini-grid">
          <div><span>CE</span><b class="positive">${Math.round(n(item.bull_confidence))}</b></div>
          <div><span>PE</span><b class="negative">${Math.round(n(item.bear_confidence))}</b></div>
          <div><span>MTF</span><b class="${scoreTone(mtfScore)}">${Math.round(mtfScore)}</b></div>
        </div>
        <div class="insight-pills">${mtfDots(mtfDotsData)}</div>
        <p class="option-reason">${escapeHtml(mtfText)} · Source: ${escapeHtml(mtfSource)}${trueMtf?.cached ? ' cache' : ''}</p>
        ${Array.isArray(mtfWarnings) && mtfWarnings.length ? `<div class="insight-pills">${reasonPills(mtfWarnings.slice(0, 4), 'warn')}</div>` : ''}
        <div class="option-mini-grid">
          <div><span>CE dots</span><b class="positive">${n(trueMtf?.bull_count, 0)}</b></div>
          <div><span>PE dots</span><b class="negative">${n(trueMtf?.bear_count, 0)}</b></div>
          <div><span>Chase</span><b class="${item.anti_chase_ok ? 'positive' : 'warn'}">${item.anti_chase_ok ? 'PASS' : 'WAIT'}</b></div>
        </div>
      `)}

      ${this._subsection('trend', 'Trend analyser', `${trendGatePassCount}/8 gates passing`, `
        <div class="insight-gates">
          ${gate('VWAP', vwapGate, item.vwap_state === 'ABOVE' ? 'Price above VWAP' : item.vwap_state === 'BELOW' ? 'Price below VWAP' : 'Need VWAP')}
          ${gate('EMA', emaGate, 'EMA stack / direction')}
          ${gate('MACD', macdGate, 'Momentum confirmation')}
          ${gate('ATR', atrGate, item.atr_trail_stop ? `Trail ${formatPrice(item.atr_trail_stop)}` : 'ATR trail')}
          ${gate('RSI', item.trend_stack?.rsi, `RSI ${n(item.rsi_14, 50).toFixed(1)}`)}
          ${gate('Volume', item.trend_stack?.volume, formatRelVol(item.rel_vol))}
          ${gate('Compression', item.trend_stack?.compression, `${Math.round(n(item.bb_compression))}% BB squeeze`)}
          ${gate('Pattern', item.trend_stack?.pattern, patternText)}
        </div>
      `)}

      ${this._renderExtendedSignals(item, trueMtf)}
      ${this._renderVcp(trueMtf)}
      ${this._renderSignalTimeEvidence(symbol)}

      <div class="insight-section">
        <div class="insight-title">Why it has strength</div>
        <div class="insight-pills">${reasonPills(item.strength_reasons, 'good')}</div>
      </div>

      <div class="insight-section">
        <div class="insight-title">What is lacking</div>
        <div class="insight-pills">${reasonPills(item.weakness_reasons, 'bad')}</div>
      </div>

      <div class="insight-section">
        <div class="insight-title">Anti-chase / rejection reasons</div>
        <div class="insight-pills">${reasonPills(item.rejection_reasons || item.anti_chase_reasons, item.anti_chase_ok ? 'good' : 'bad')}</div>
      </div>

      <div class="insight-section option-mini">
        <div class="insight-title">Option basis</div>
        <div class="option-mini-grid">
          <div><span>Chain</span><b class="${optionReady ? 'positive' : 'warn'}">${optionReady ? 'LIVE' : 'PENDING'}</b></div>
          <div><span>Bias</span><b>${escapeHtml(option.bias || decision)}</b></div>
          <div><span>Final</span><b class="${scoreTone(n(option.final_score, opt))}">${Math.round(n(option.final_score, opt))}</b></div>
          <div><span>Status</span><b class="${optionTradeReady ? 'positive' : 'warn'}">${escapeHtml(optionStatus)}</b></div>
        </div>
        <p>${escapeHtml(option.reason || 'Underlying scanner is active. CE/PE chain confidence improves once OI, IV, spread and strike data are available.')}</p>
        ${optionBlockers.length ? `<div class="insight-pills">${reasonPills(optionBlockers.slice(0, 4), 'bad')}</div>` : ''}
      </div>

      <div class="insight-section ai-advisor">
        <div class="ai-advisor-head">
          <div>
            <div class="insight-title">ChatGPT advisory</div>
            <small>Explanation only · scanner remains deterministic</small>
          </div>
          <div class="ai-actions">
            <button type="button" data-ai-mode="explain" ${this._aiLoading ? 'disabled' : ''}>Explain</button>
            <button type="button" data-ai-mode="risk" ${this._aiLoading ? 'disabled' : ''}>Risk Check</button>
          </div>
        </div>
        ${this._aiLoading ? `
          <div class="ai-loading">Reviewing ${escapeHtml(symbol)} evidence…</div>
        ` : ai ? `
          <div class="ai-verdict ${aiTone}">
            <b>${escapeHtml(ai.verdict || 'WATCH')}</b>
            <span>${escapeHtml(aiSource)}</span>
          </div>
          <p class="ai-summary">${escapeHtml(ai.summary || '')}</p>
          <div class="ai-columns">
            <div><label>Supports</label>${reasonPills(ai.why_trade, 'good')}</div>
            <div><label>Blockers</label>${reasonPills(ai.blockers, 'bad')}</div>
          </div>
          <div class="ai-plan">
            ${ai.trigger ? `<div><span>Trigger</span><b>${escapeHtml(ai.trigger)}</b></div>` : ''}
            ${ai.invalidation ? `<div><span>Invalidation</span><b>${escapeHtml(ai.invalidation)}</b></div>` : ''}
            ${ai.option_view ? `<div><span>Option view</span><b>${escapeHtml(ai.option_view)}</b></div>` : ''}
            ${ai.risk_note ? `<div><span>Risk</span><b>${escapeHtml(ai.risk_note)}</b></div>` : ''}
          </div>
        ` : `
          <p class="ai-empty">Choose Explain or Risk Check for a grounded review of this setup.</p>
        `}
      </div>
    `;

    this._el.querySelectorAll('[data-ai-mode]').forEach(btn => {
      btn.addEventListener('click', () => this._loadAI(btn.dataset.aiMode));
    });
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
    if (this._clickHandler) document.removeEventListener('click', this._clickHandler);
  }
}
