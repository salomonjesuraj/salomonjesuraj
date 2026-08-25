/**
 * Command Center v2 — New shell's Signal Cockpit, rebuilt to the approved
 * mockup's deck-card visual (gauge arc, sparkline, verdict word, level
 * grid). Same data source as the Classic cockpit (cockpit.js) --
 * /api/signals, unchanged fields, same frozen-at-signal-time guarantee --
 * this file only changes how it's drawn, not what it shows. Mark Taken
 * reuses the exact lookup+POST sequence scanner.js's _logPaperTrade()
 * already uses (/api/journal/trades -> find NOT_REVIEWED row for the
 * symbol -> POST .../discretion), not a second, possibly-diverging path.
 *
 * Mathematical-audit-follow-up UI work (2026-08-25): each card now also
 * pulls GET /api/trade-blueprint/{symbol} (bundles retest state, Volume
 * Profile POC/VAH/VAL + accumulation base, and 4-quadrant OI buildup +
 * wall hurdle -- see libs/infusion-models/trade_blueprint.py's own
 * docstring for exactly which existing source each field comes from)
 * and GET /api/options/summary?symbol=X (the same endpoint the
 * existing standalone Option Basis panel already uses) for the
 * contract/delta/spread box. Both are fetched once per symbol per
 * session and cached, same pattern as the sparkline below -- the card
 * renders immediately from /api/signals data alone, then fills in the
 * blueprint sections the instant they arrive rather than blocking the
 * whole card on three extra network round trips.
 */
import { api } from './api.js';
import { formatPrice, escapeHtml } from './utils.js';

const DASH = '—';
const GRADE_TONE = { 'A+': 'var(--ifx-bull)', A: 'var(--ifx-info)', 'B+': 'var(--ifx-warn)', B: 'var(--ifx-shell-text-muted)' };

// Task 1's own spec vocabulary. NO_BREAKOUT is shown as "IMMEDIATE
// BREAKOUT" -- from the card's own point of view, a signal that fired
// with no broken level still pending a retest IS the immediate-entry
// case, not a fourth, unrelated state.
const RETEST_LABEL = {
  NO_BREAKOUT: 'IMMEDIATE BREAKOUT',
  PENDING_RETEST: 'PENDING RETEST',
  RETEST_HELD: 'RETEST HELD',
  RETEST_FAILED: 'RETEST FAILED',
};
const RETEST_TONE = {
  NO_BREAKOUT: 'good', PENDING_RETEST: 'warn', RETEST_HELD: 'good', RETEST_FAILED: 'bad',
};

const OI_BUILDUP_LABEL = {
  LONG_BUILDUP: 'LONG BUILDUP', SHORT_COVERING: 'SHORT COVERING',
  SHORT_BUILDUP: 'SHORT BUILDUP', LONG_UNWINDING: 'LONG UNWINDING', NEUTRAL: 'NEUTRAL',
};
const OI_BUILDUP_TONE = {
  LONG_BUILDUP: 'good', SHORT_COVERING: 'good', SHORT_BUILDUP: 'bad', LONG_UNWINDING: 'bad', NEUTRAL: 'faint',
};

function ageStr(createdUs) {
  if (!createdUs) return '';
  const ageSec = Math.max(0, Math.floor((Date.now() - Number(createdUs) / 1000) / 1000));
  if (ageSec < 60) return `${ageSec}s ago`;
  const ageMin = Math.floor(ageSec / 60);
  if (ageMin < 60) return `${ageMin}m ago`;
  return `${Math.floor(ageMin / 60)}h ago`;
}

function signalTime(createdUs) {
  if (!createdUs) return '';
  const d = new Date(Number(createdUs) / 1000);
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
}

function sparkPath(vals, w, h) {
  if (!vals || vals.length < 2) return '';
  const min = Math.min(...vals), max = Math.max(...vals);
  return vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * w;
    const y = h - ((v - min) / ((max - min) || 1)) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
}

// Clean Sweep LC-2: the score was previously a circular gauge (SVG ring
// + centered number) -- a legitimate design element, but the reference
// screenshot's whole visual language is number-forward and reads faster
// at a glance than a dial does; a beginner shouldn't need to parse a
// ring fill to know "how good is this." Replaced with a plain, bold,
// tone-colored score badge -- same information, less to visually parse.
function scoreBadge(score, glowVar) {
  return `
    <div class="ifx-deck-score">
      <b style="color:${glowVar}">${Math.round(score)}</b><span>score</span>
    </div>`;
}

function riskDistance(entry, stop, atr) {
  const dist = Math.abs(Number(entry || 0) - Number(stop || 0));
  if (!dist) return DASH;
  const atrMult = atr > 0 ? ` (${(dist / atr).toFixed(1)}× ATR)` : '';
  return `₹${dist.toFixed(2)}${atrMult}`;
}

export class CockpitV2Panel {
  constructor(containerEl) {
    this._el = containerEl;
    this._signals = [];
    this._sparkCache = new Map(); // symbol -> closes[], one-shot per symbol per session
    this._blueprintCache = new Map(); // symbol -> TradeBlueprint, one-shot per symbol per session
    this._optionCache = new Map(); // symbol -> /api/options/summary response
    this._unsubs = [];
  }

  init() {
    if (!this._el) return;
    this._el.classList.add('ifx-deck-wrap');
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
        <div class="ifx-deck-empty">
          <span class="ifx-deck-empty-icon">◎</span>
          <span class="ifx-deck-empty-title">No active signals right now</span>
          <span class="ifx-deck-empty-sub">Nothing meets the conviction bar — that's the point.</span>
        </div>`;
      return;
    }
    const ranked = [...this._signals].sort((a, b) => Number(b.conviction_score || 0) - Number(a.conviction_score || 0));
    this._el.innerHTML = `<div class="ifx-deck">${ranked.map((s, i) => this._renderCard(s, i + 1)).join('')}</div>`;

    this._el.querySelectorAll('[data-deck-sym]').forEach((card) => {
      const symbol = card.dataset.deckSym;
      const sig = ranked.find((s) => s.symbol === symbol);
      card.querySelector('[data-deck-chart]')?.addEventListener('click', () => {
        if (!sig) return;
        document.dispatchEvent(new CustomEvent('signal:select', { detail: sig }));
        document.dispatchEvent(new CustomEvent('chart:load', { detail: { symbol, signal: sig } }));
      });
      card.querySelector('[data-deck-mark]')?.addEventListener('click', (e) => this._markTaken(symbol, e.currentTarget));
      this._loadSpark(symbol, card.querySelector('.ifx-deck-spark'));
      this._loadBlueprint(symbol, card);
      this._loadOptionBasis(symbol, card);
    });
  }

  async _loadSpark(symbol, container) {
    if (!container) return;
    if (this._sparkCache.has(symbol)) {
      this._paintSpark(container, this._sparkCache.get(symbol));
      return;
    }
    try {
      const resp = await api.fetch(`/api/chart/${encodeURIComponent(symbol)}/intraday?interval=15m`);
      const closes = (resp?.bars || []).slice(-16).map((b) => Number(b.close)).filter((n) => !Number.isNaN(n));
      this._sparkCache.set(symbol, closes);
      this._paintSpark(container, closes);
    } catch (_) {
      // No sparkline is fine -- the card still works without it.
    }
  }

  _paintSpark(container, closes) {
    if (!container || closes.length < 2) return;
    const w = 300, h = 34;
    const up = closes[closes.length - 1] >= closes[0];
    container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <polyline points="${sparkPath(closes, w, h)}" fill="none" stroke="${up ? 'var(--ifx-bull)' : 'var(--ifx-bear)'}" stroke-width="2"/>
    </svg>`;
  }

  async _loadBlueprint(symbol, card) {
    if (this._blueprintCache.has(symbol)) {
      this._paintBlueprint(card, this._blueprintCache.get(symbol));
      return;
    }
    try {
      const bp = await api.fetch(`/api/trade-blueprint/${encodeURIComponent(symbol)}`);
      if (bp) {
        this._blueprintCache.set(symbol, bp);
        this._paintBlueprint(card, bp);
      }
    } catch (_) {
      // Blueprint sections just stay in their "loading" placeholder state.
    }
  }

  _paintBlueprint(card, bp) {
    if (!card || !bp) return;

    // Entry-zone retest badge, next to the lock/age note.
    const retestSlot = card.querySelector('[data-deck-retest]');
    if (retestSlot) {
      const status = bp.retest_status || 'NO_BREAKOUT';
      const label = RETEST_LABEL[status] || status.replace(/_/g, ' ');
      const tone = RETEST_TONE[status] || 'faint';
      const levelNote = bp.retest_level ? ` @ ${formatPrice(bp.retest_level)}` : '';
      retestSlot.innerHTML = `<span class="ifx-badge ifx-badge--${tone === 'good' ? 'bull' : tone === 'bad' ? 'bear' : 'warn'}" title="Retest status${levelNote}">${escapeHtml(label)}</span>`;
    }

    // T3 -- extends the existing Entry/Stop/T1/T2 grid to 5 columns.
    const t3Slot = card.querySelector('[data-deck-t3]');
    if (t3Slot && bp.target_3_fib > 0) {
      t3Slot.innerHTML = `<label>T3</label><b class="ifx-tone-good">${formatPrice(bp.target_3_fib)}</b>`;
      t3Slot.closest('.ifx-deck-levels')?.classList.add('ifx-deck-levels--5');
    }

    // Volume Profile / Value Area pill.
    const vpSlot = card.querySelector('[data-deck-vp]');
    if (vpSlot) {
      if (bp.poc_level != null) {
        const accum = bp.accumulation_base
          ? '<span class="ifx-badge ifx-badge--bull" title="Tight Value Area, fresh cross above VAH, real volume expansion">EXITING ACCUMULATION BASE</span>'
          : '';
        vpSlot.innerHTML = `
          <span class="ifx-deck-vp-item"><label>POC</label><b>${formatPrice(bp.poc_level)}</b></span>
          <span class="ifx-deck-vp-item"><label>VAH</label><b>${formatPrice(bp.vah_level)}</b></span>
          <span class="ifx-deck-vp-item"><label>VAL</label><b>${formatPrice(bp.val_level)}</b></span>
          ${accum}`;
      } else {
        vpSlot.innerHTML = `<span class="ifx-deck-vp-empty">Volume Profile: not enough bar history yet</span>`;
      }
    }

    // Derivatives (OI buildup) pill.
    const oiSlot = card.querySelector('[data-deck-oi]');
    if (oiSlot) {
      const buildup = bp.oi_buildup || 'NEUTRAL';
      const tone = OI_BUILDUP_TONE[buildup] || 'faint';
      const hurdle = bp.oi_hurdle_strike != null
        ? `<span class="ifx-deck-oi-hurdle">Hurdle <b>${formatPrice(bp.oi_hurdle_strike)}</b></span>`
        : '';
      const attraction = bp.oi_attraction_strike != null
        ? `<span class="ifx-deck-oi-hurdle">Max Pain <b>${formatPrice(bp.oi_attraction_strike)}</b></span>`
        : '';
      oiSlot.innerHTML = `
        <span class="ifx-badge ifx-badge--${tone === 'good' ? 'bull' : tone === 'bad' ? 'bear' : 'neutral'}">${escapeHtml(OI_BUILDUP_LABEL[buildup] || buildup)}</span>
        ${hurdle}${attraction}`;
    }
  }

  async _loadOptionBasis(symbol, card) {
    if (this._optionCache.has(symbol)) {
      this._paintOptionBasis(card, this._optionCache.get(symbol));
      return;
    }
    try {
      const resp = await api.fetch(`/api/options/summary?symbol=${encodeURIComponent(symbol)}`);
      if (resp) {
        this._optionCache.set(symbol, resp);
        this._paintOptionBasis(card, resp);
      }
    } catch (_) {
      // Option Basis box just stays in its "chain pending" placeholder.
    }
  }

  _paintOptionBasis(card, resp) {
    const slot = card?.querySelector('[data-deck-option]');
    if (!slot) return;
    const ctx = resp?.upstox_option || {};
    if (!resp?.option_chain_ready) {
      slot.innerHTML = `<span class="ifx-deck-option-empty">Option chain: ${escapeHtml(resp?.execution_status || 'CHAIN_PENDING')}</span>`;
      return;
    }
    const metrics = ctx.metrics || {};
    const delta = metrics.delta != null ? Number(metrics.delta).toFixed(2) : DASH;
    const spread = metrics.spread_pct != null ? `${Number(metrics.spread_pct).toFixed(1)}%` : DASH;
    const premium = metrics.ltp != null ? formatPrice(metrics.ltp) : DASH;
    const slPrice = metrics.option_sl_price != null ? formatPrice(metrics.option_sl_price) : DASH;
    const statusTone = resp.trade_ready ? 'bull' : resp.execution_status === 'AVOID_CONTRACT' ? 'bear' : 'warn';
    slot.innerHTML = `
      <div class="ifx-deck-option-head">
        <b>${escapeHtml(resp.suggested_contract || `${resp.symbol} ${resp.bias}`)}</b>
        <span class="ifx-badge ifx-badge--${statusTone}">${escapeHtml(resp.execution_status || '')}</span>
      </div>
      <div class="ifx-deck-option-grid">
        <span><label>Premium</label><b>${premium}</b></span>
        <span><label>Delta</label><b>${delta}</b></span>
        <span><label>Spread</label><b>${spread}</b></span>
        <span><label>Premium SL</label><b class="ifx-tone-bad">${slPrice}</b></span>
      </div>`;
  }

  async _markTaken(symbol, btn) {
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Marking…';
    try {
      const data = await api.fetch('/api/journal/trades?limit=200');
      const rows = Array.isArray(data?.trades) ? data.trades : [];
      const row = rows.find((x) =>
        String(x.symbol || '').toUpperCase() === symbol.toUpperCase() &&
        String(x.discretionary_action || 'NOT_REVIEWED').toUpperCase() === 'NOT_REVIEWED'
      );
      if (!row?.id) {
        btn.textContent = 'No auto row';
        setTimeout(() => { btn.disabled = false; btn.textContent = original; }, 1800);
        return;
      }
      const res = await api.post(`/api/journal/trades/${encodeURIComponent(row.id)}/discretion`, { discretionary_action: 'TAKEN' });
      if (res?.ok) {
        btn.textContent = 'Marked Taken';
        document.dispatchEvent(new CustomEvent('journal:refresh', { detail: res.trade }));
        setTimeout(() => { btn.disabled = false; btn.textContent = original; }, 1600);
      } else {
        btn.textContent = 'Log failed';
        setTimeout(() => { btn.disabled = false; btn.textContent = original; }, 1800);
      }
    } catch (_) {
      btn.textContent = 'Log failed';
      setTimeout(() => { btn.disabled = false; btn.textContent = original; }, 1800);
    }
  }

  _renderCard(sig, rank) {
    const score = Number(sig.conviction_score || 0);
    const grade = sig.conviction_grade || DASH;
    const glow = GRADE_TONE[grade] || 'var(--ifx-shell-text-muted)';
    const entry = Number(sig.entry_price || 0);
    const stop = Number(sig.invalidation_price || 0);
    const target = Number(sig.target_price || 0);
    const isBull = (sig.signal_type || 'bullish') === 'bullish';
    const side = isBull ? 'bull' : 'bear';
    const directionBadge = isBull ? 'BULLISH BREAKOUT' : 'BEAR BREAKOUT';
    const label = sig.option_bias || (isBull ? 'BUY CE' : 'BUY PE');
    const fs = sig.features_snapshot && typeof sig.features_snapshot === 'object' ? sig.features_snapshot : {};
    const t2 = Number(fs.t2_price || 0);
    const atr = Number(fs.atr_14 || 0);
    const rr = Number(sig.risk_reward_ratio || 0);
    const sector = sig.sector_id || DASH;

    return `
    <div class="ifx-deck-card ifx-deck-card--${side}" data-deck-sym="${escapeHtml(sig.symbol)}">
      <div class="ifx-deck-top">
        <div>
          <div class="ifx-deck-symbol">${escapeHtml(sig.symbol)}</div>
          <div class="ifx-deck-sector">${escapeHtml(sector.replace(/_/g, ' '))}</div>
        </div>
        <div class="ifx-deck-top-right">
          <span class="ifx-badge ifx-badge--${side === 'bull' ? 'bull' : 'bear'}">${directionBadge}</span>
          <div class="ifx-deck-lock" title="Frozen at signal time — never recalculated as price moves">🔒 as of ${signalTime(sig.created_at_us)} · ${ageStr(sig.created_at_us)}</div>
        </div>
      </div>
      <div class="ifx-deck-mid">
        ${scoreBadge(score, glow)}
        <div class="ifx-deck-verdict"><div class="ifx-deck-verdict-word ifx-deck-verdict-word--${side}">${escapeHtml(label)} <span class="ifx-deck-grade-badge">${escapeHtml(grade)}</span></div>
          <div class="ifx-deck-verdict-reason">Rank #${rank}${rr ? ` · R:R ${rr.toFixed(1)}:1` : ''} · <span data-deck-retest>Entry zone loading…</span></div></div>
      </div>
      <div class="ifx-deck-levels">
        <div class="ifx-deck-lvl"><label>Entry</label><b>${formatPrice(entry)}</b></div>
        <div class="ifx-deck-lvl"><label>Stop</label><b class="ifx-tone-bad">${formatPrice(stop)}</b><small>${riskDistance(entry, stop, atr)}</small></div>
        <div class="ifx-deck-lvl"><label>T1</label><b class="ifx-tone-good">${formatPrice(target)}</b></div>
        <div class="ifx-deck-lvl"><label>T2</label><b class="ifx-tone-good">${t2 > 0 ? formatPrice(t2) : DASH}</b></div>
        <div class="ifx-deck-lvl" data-deck-t3></div>
      </div>
      <div class="ifx-deck-vp-pill" data-deck-vp><span class="ifx-deck-vp-empty">Volume Profile loading…</span></div>
      <div class="ifx-deck-oi-pill" data-deck-oi><span class="ifx-deck-oi-empty">OI buildup loading…</span></div>
      <div class="ifx-deck-option-box" data-deck-option><span class="ifx-deck-option-empty">Option Basis loading…</span></div>
      <div class="ifx-deck-spark"></div>
      <div class="ifx-deck-foot">
        <span class="ifx-deck-foot-note">${escapeHtml(sig.pre_breakout_state || sig.market_regime || '')}</span>
        <div class="ifx-deck-actions">
          <button type="button" class="ifx-btn ifx-btn--on-paper" data-deck-chart>Chart</button>
          <button type="button" class="ifx-btn ifx-deck-mark-btn" data-deck-mark>Mark Taken</button>
        </div>
      </div>
    </div>`;
  }

  destroy() {
    this._unsubs.forEach((fn) => fn());
  }
}
