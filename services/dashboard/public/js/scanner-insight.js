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
  }

  init() {
    if (!this._el) return;
    this._render();

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
      if (sym) this.select(sym);
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

      <div class="insight-section">
        <div class="insight-title">Pine-style confidence + MTF</div>
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
      </div>

      <div class="insight-section">
        <div class="insight-title">Trend analyser</div>
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
      </div>

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
