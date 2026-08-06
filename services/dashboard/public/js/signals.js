/**
 * Signal Board — full trade setup cards.
 * Shows: symbol, timeframe, side, entry, stop, targets, R:R, conviction, grade, sector, regime, and explanation.
 */
import { api } from './api.js';
import { ws } from './ws.js';
import { formatPrice, escapeHtml } from './utils.js';

const DASH = '—';

const TF_LABEL = {
  vol_vwap_breakout: '15M',
  options_first_hybrid: 'OPT',
  vwap_reclaim: '5M',
  momentum: '1H',
};

function convClass(score) {
  if (score >= 95) return 'conv-95';
  if (score >= 85) return 'conv-85';
  if (score >= 75) return 'conv-75';
  if (score >= 65) return 'conv-65';
  return 'conv-below';
}

function ageStr(createdUs) {
  if (!createdUs) return '';
  const ageMs = Date.now() - Number(createdUs) / 1000;
  const ageSec = Math.max(0, Math.floor(ageMs / 1000));
  if (ageSec < 60) return `${ageSec}s ago`;
  const ageMin = Math.floor(ageSec / 60);
  if (ageMin < 60) return `${ageMin}m ago`;
  return `${Math.floor(ageMin / 60)}h ago`;
}

function asObject(value) {
  if (!value) return null;
  if (typeof value === 'object') return value;
  if (typeof value !== 'string') return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch (_) {
    return null;
  }
}

function cleanPhrase(text) {
  return String(text || '')
    .replace(/\s+/g, ' ')
    .replace(/\s+—\s+/g, ' — ')
    .trim();
}

function explanationItems(explanation) {
  if (!explanation) return [];
  if (Array.isArray(explanation)) return explanation.map(cleanPhrase).filter(Boolean);
  return String(explanation).split('|').map(cleanPhrase).filter(Boolean);
}

function rrBar(rr) {
  const total = 1 + Math.max(Number(rr) || 0, 0);
  const rewardPct = total > 0 ? Math.min((rr / total) * 100, 100) : 50;
  return `
    <div class="rr-bar" title="Risk/reward visual">
      <div class="rr-risk" style="width:${100 - rewardPct}%"></div>
      <div class="rr-reward" style="width:${rewardPct}%"></div>
    </div>`;
}

function conditionsHTML(conditions) {
  const parsed = asObject(conditions);
  if (!parsed) return '';
  return Object.entries(parsed).map(([k, v]) => {
    const label = k.replace(/_/g, ' ');
    return `<span class="cond-pill ${v ? 'cond-pass' : 'cond-fail'}" title="${escapeHtml(label)}">
      ${v ? '✓' : '✕'} ${escapeHtml(label)}
    </span>`;
  }).join('');
}

export class SignalBoard {
  constructor(containerEl) {
    this._el = containerEl;
    this._signals = [];
    this._filterType = '';
    this._unsubs = [];
  }

  init() {
    const filterEl = document.getElementById('signalTypeFilter');
    if (filterEl) {
      filterEl.addEventListener('change', () => {
        this._filterType = filterEl.value.toLowerCase();
        this._render();
      });
    }

    this._unsubs.push(api.subscribe('/api/signals', (resp) => {
      this._signals = resp?.signals || [];
      this._render();
      document.dispatchEvent(new CustomEvent('signals:count', { detail: this._signals.length }));
    }, 2000));

    ws.onTick((symbol, data) => {
      const card = this._el.querySelector(`[data-signal-sym="${symbol}"]`);
      if (card && data.ltp) {
        const ltpEl = card.querySelector('.sig-ltp-live');
        if (ltpEl) ltpEl.textContent = formatPrice(data.ltp);
      }
    });

    this._render();
  }

  _render() {
    const signals = this._filterType
      ? this._signals.filter(s => {
          const st = (s.signal_type || '').toLowerCase();
          const sid = (s.strategy_id || '').toLowerCase();
          return st.includes(this._filterType) || sid.includes(this._filterType);
        })
      : this._signals;

    if (signals.length === 0) {
      const nowIST = new Date(Date.now() + 5.5 * 3600000);
      const hIST = nowIST.getUTCHours();
      const mIST = nowIST.getUTCMinutes();
      const totalMin = hIST * 60 + mIST;
      const isOpen = totalMin >= 9 * 60 + 15 && totalMin < 15 * 60 + 30;
      const timeStr = `${String(hIST).padStart(2, '0')}:${String(mIST).padStart(2, '0')} IST`;
      const statusMsg = isOpen
        ? `Market open · ${timeStr} · Scanner processing live ticks`
        : `Market closed · ${timeStr} · Signals fire during 09:15–15:30 IST`;

      this._el.innerHTML = `
        <div class="panel-empty" style="flex-direction:column;gap:8px;padding:24px">
          <span style="font-size:24px;opacity:.4">🎯</span>
          <span style="font-size:13px;font-weight:600;color:var(--text-secondary)">No Active Signals</span>
          <span style="font-size:10px;color:var(--text-disabled)">${escapeHtml(statusMsg)}</span>
          <span style="font-size:10px;color:var(--text-disabled)">
            Signals require: volume expansion + VWAP reclaim + trend alignment
          </span>
        </div>`;
      return;
    }

    this._el.innerHTML = signals.map(sig => this._renderCard(sig)).join('');

    this._el.querySelectorAll('.signal-card').forEach(card => {
      card.addEventListener('click', () => {
        const sym = card.dataset.signalSym;
        const sig = signals.find(s => s.symbol === sym);
        if (!sig) return;
        document.dispatchEvent(new CustomEvent('signal:select', { detail: sig }));
        document.dispatchEvent(new CustomEvent('chart:load', { detail: { symbol: sym, signal: sig } }));
        this._el.querySelectorAll('.signal-card').forEach(c => c.classList.remove('selected'));
        card.classList.add('selected');
      });
    });
  }

  _renderCard(sig) {
    const score = Math.round(Number(sig.conviction_score || sig.active_score || 0));
    const grade = sig.conviction_grade || DASH;
    const entry = Number(sig.entry_price || 0);
    const stop = Number(sig.invalidation_price || sig.stop_price || 0);
    const target = Number(sig.target_price || 0);
    const rr = Number(sig.risk_reward_ratio || sig.rr || 0);
    const sector = sig.sector_id || DASH;
    const regime = (sig.market_regime || '').toLowerCase();
    const tf = TF_LABEL[sig.strategy_id] || '15M';
    const isBull = (sig.signal_type || 'bullish') === 'bullish';
    const sigLabel = sig.option_bias || (isBull ? 'BUY CE' : 'BUY PE');
    const sigColor = isBull ? 'var(--green)' : 'var(--red)';
    const sigBg = isBull ? 'var(--green-dim)' : 'var(--red-dim)';
    const cc = convClass(score);

    const stopPct = entry > 0 && stop > 0 ? Math.abs(((stop - entry) / entry) * 100).toFixed(2) : '';
    const targetPct = entry > 0 && target > 0 ? Math.abs(((target - entry) / entry) * 100).toFixed(2) : '';
    const tsl = stop > 0 && entry > 0 ? (isBull ? entry - (entry - stop) * 0.5 : entry + (stop - entry) * 0.5) : 0;
    const t2 = target > 0 && entry > 0 ? (isBull ? target + (target - entry) * 0.5 : target - (entry - target) * 0.5) : 0;

    const regimeBg = regime === 'risk_on' ? 'var(--green-dim)' : regime === 'risk_off' ? 'var(--red-dim)' : 'var(--gold-dim)';
    const regimeColor = regime === 'risk_on' ? 'var(--green)' : regime === 'risk_off' ? 'var(--red)' : 'var(--gold)';
    const regimeLabel = regime === 'risk_on' ? 'RISK ON' : regime === 'risk_off' ? 'RISK OFF' : 'NEUTRAL';

    const explain = explanationItems(sig.explanation);
    const explainHtml = explain.length
      ? `<div class="sig-explain">${explain.map(e => `<span class="explain-pill">${escapeHtml(e)}</span>`).join('')}</div>`
      : '';

    const condHtml = conditionsHTML(sig.conditions_met);

    return `
    <div class="signal-card ${cc}" data-signal-sym="${escapeHtml(sig.symbol)}">
      <div class="sig-header">
        <div class="sig-sym-block">
          <span class="sig-symbol">${escapeHtml(sig.symbol)}</span>
          <span class="sig-tf">${tf}</span>
        </div>
        <div class="sig-header-right">
          <span class="sig-type-badge" style="background:${sigBg};color:${sigColor}">${sigLabel}</span>
          <span class="sig-ltp-live">${formatPrice(sig.price_at_signal)}</span>
          <span class="sig-age">${ageStr(sig.created_at_us)}</span>
        </div>
      </div>

      <div class="sig-price-grid">
        <div class="sig-price-item">
          <span class="sig-price-label">Entry</span>
          <span class="sig-price-val entry-price">${formatPrice(entry)}</span>
        </div>
        <div class="sig-price-item">
          <span class="sig-price-label">Stop</span>
          <span class="sig-price-val stop-price">${formatPrice(stop)}${stopPct ? `<span class="sig-pct neg"> -${stopPct}%</span>` : ''}</span>
        </div>
        <div class="sig-price-item">
          <span class="sig-price-label">Trail</span>
          <span class="sig-price-val">${tsl > 0 ? formatPrice(tsl) : DASH}</span>
        </div>
        <div class="sig-price-item">
          <span class="sig-price-label">T1</span>
          <span class="sig-price-val target-price">${formatPrice(target)}${targetPct ? `<span class="sig-pct pos"> +${targetPct}%</span>` : ''}</span>
        </div>
        <div class="sig-price-item">
          <span class="sig-price-label">T2</span>
          <span class="sig-price-val">${t2 > 0 ? formatPrice(t2) : DASH}</span>
        </div>
        <div class="sig-price-item">
          <span class="sig-price-label">R:R</span>
          <span class="sig-price-val ${rr >= 2 ? 'positive' : ''}">${rr > 0 ? '1:' + rr.toFixed(1) : DASH}</span>
        </div>
      </div>

      ${rr > 0 ? rrBar(rr) : ''}

      <div class="sig-intel">
        <div class="sig-conv-block">
          <span class="sig-conv-val ${cc}">${score}</span>
          <span class="sig-conv-label">Conviction</span>
        </div>
        <div class="sig-grade-block">
          <span class="grade-chip grade-${(grade || '').toLowerCase().replace('+', 'plus')}">${escapeHtml(grade)}</span>
          <span class="sig-conv-label">Grade</span>
        </div>
        <div class="sig-meta">
          <span class="sig-meta-item">📊 ${escapeHtml(sector)}</span>
          <span class="sig-meta-item" style="background:${regimeBg};color:${regimeColor};border-radius:4px;padding:1px 5px">${regimeLabel}</span>
        </div>
      </div>

      ${explainHtml}
      ${condHtml ? `<div class="sig-conditions">${condHtml}</div>` : ''}
    </div>`;
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
  }
}
