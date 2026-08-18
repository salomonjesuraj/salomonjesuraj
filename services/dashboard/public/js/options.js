/**
 * Option Cockpit — option-trading focused readiness and chain status.
 */
import { api } from './api.js';
import { escapeHtml } from './utils.js';

function scoreClass(score) {
  if (score == null) return 'muted';
  if (score >= 85) return 'positive';
  if (score >= 70) return 'warn';
  if (score >= 55) return 'neutral';
  return 'negative';
}

function scoreText(score) {
  return score == null ? '—' : String(Math.round(score));
}

function gate(label, state, detail = '') {
  const cls = state === true ? 'pass' : state === false ? 'fail' : 'wait';
  const icon = state === true ? '✓' : state === false ? '✕' : '…';
  return `<span class="option-gate ${cls}" title="${escapeHtml(detail)}">${icon} ${escapeHtml(label)}</span>`;
}

// R6: contract confirmation is a status independent of stock breakout
// detection (that's the Radar panel's job). These four are the only
// values the backend's execution_status ever means to emit
// (market.py:821-827, _upstox_option_context) -- this map drives both
// the label text and the badge color directly off that value, instead
// of the previous ad-hoc tradeReady/optionReady/bias combination, which
// could show a green "buy-looking" badge for a CHAIN_PENDING contract
// (bias.toLowerCase() === 'ce') or the same gold as WAIT_CONTRACT for a
// hard-blocked AVOID_CONTRACT.
const EXECUTION_STATUS_META = {
  TRADE_READY: { cls: 'trade-ready', label: 'TRADE READY' },
  WAIT_CONTRACT: { cls: 'wait-contract', label: 'WAIT CONTRACT' },
  CHAIN_PENDING: { cls: 'chain-pending', label: 'CHAIN PENDING' },
  AVOID_CONTRACT: { cls: 'avoid-contract', label: 'AVOID CONTRACT' },
  NO_SYMBOL: { cls: 'chain-pending', label: 'NO SYMBOL SELECTED' },
};

function executionStatusMeta(status) {
  return EXECUTION_STATUS_META[status]
    || { cls: 'chain-pending', label: String(status || 'CHAIN PENDING').replace(/_/g, ' ') };
}

export class OptionCockpit {
  constructor(containerEl, statusEl) {
    this._el = containerEl;
    this._statusEl = statusEl;
    this._summary = null;
    this._selectedSymbol = '';
    this._unsubs = [];
  }

  init() {
    this._render();

    this._unsubs.push(api.subscribe('/api/options/summary', (resp) => {
      this._summary = resp;
      this._render();
    }, 3000));

    document.addEventListener('chart:load', (e) => {
      const sym = e.detail?.symbol;
      if (sym) this._loadSymbol(String(sym).toUpperCase());
    });
    document.addEventListener('signal:select', (e) => {
      const sym = e.detail?.symbol;
      if (sym) this._loadSymbol(String(sym).toUpperCase());
    });
  }

  async _loadSymbol(symbol) {
    this._selectedSymbol = symbol;
    try {
      this._summary = await api.fetch(`/api/options/summary?symbol=${encodeURIComponent(symbol)}`);
    } catch (_) {
      this._summary = null;
    }
    this._render();
  }

  _render() {
    const s = this._summary || {};
    const symbol = this._selectedSymbol || s.symbol || '—';
    const optionReady = Boolean(s.option_chain_ready);
    if (this._statusEl) {
      this._statusEl.textContent = optionReady ? 'CHAIN LIVE' : 'CHAIN PENDING';
      this._statusEl.style.color = optionReady ? 'var(--green)' : 'var(--gold)';
    }

    const underlying = s.underlying_score ?? null;
    const metrics = s.upstox_option?.metrics || {};
    const option = s.execution_score ?? s.option_score ?? null;
    const rawOption = s.raw_option_score ?? metrics.raw_option_score ?? null;
    const finalScore = s.final_score ?? null;
    const bias = s.bias || 'WAIT';
    const contract = s.suggested_contract || 'Waiting for option chain';
    const reason = s.reason || 'Underlying scanner is live. Option-chain scoring will activate when CE/PE OI, IV, spread and strike data are available.';
    const event = metrics.event_calendar || s.event_calendar || {};
    const executionStatus = s.execution_status || (optionReady ? 'WAIT_CONTRACT' : 'CHAIN_PENDING');
    const execMeta = executionStatusMeta(executionStatus);
    const tradeReady = Boolean(s.trade_ready);
    const qualityGrade = s.quality_grade || metrics.quality_grade || '—';
    const blockers = Array.isArray(s.hard_blockers) && s.hard_blockers.length
      ? s.hard_blockers
      : Array.isArray(s.blockers) ? s.blockers : [];

    this._el.innerHTML = `
      <div class="option-cockpit">
        <div class="option-topline">
          <div>
            <div class="option-label">Focus</div>
            <div class="option-symbol">${escapeHtml(symbol)}</div>
          </div>
          <div class="option-bias ${execMeta.cls}" title="Contract confirmation status -- separate from the stock's own breakout evidence in the Radar panel">${escapeHtml(execMeta.label)}</div>
        </div>

        <div class="option-score-grid">
          <div class="option-score-card">
            <span class="option-label">Underlying</span>
            <span class="option-score ${scoreClass(underlying)}">${scoreText(underlying)}</span>
          </div>
          <div class="option-score-card">
            <span class="option-label">Execution</span>
            <span class="option-score ${scoreClass(option)}">${scoreText(option)}</span>
            ${rawOption != null && rawOption !== option ? `<small>Raw ${scoreText(rawOption)}</small>` : ''}
          </div>
          <div class="option-score-card">
            <span class="option-label">Final</span>
            <span class="option-score ${scoreClass(finalScore)}">${scoreText(finalScore)}</span>
          </div>
          <div class="option-score-card">
            <span class="option-label">Grade</span>
            <span class="option-score ${tradeReady ? 'positive' : optionReady ? 'warn' : 'muted'}">${escapeHtml(qualityGrade)}</span>
          </div>
        </div>

        <div class="option-contract">
          <span class="option-label">Suggested contract</span>
          <span>${escapeHtml(contract)}</span>
          ${optionReady ? `<span>Ask ${escapeHtml(String(metrics.ask ?? metrics.entry_fill ?? metrics.ltp ?? '-'))} | Bid ${escapeHtml(String(metrics.bid ?? metrics.exit_fill_reference ?? '-'))} | Spread ${escapeHtml(String(metrics.spread_pct ?? '-'))}% | OI ${escapeHtml(String(Math.round(Number(metrics.oi || 0))))} | IV ${escapeHtml(String(metrics.iv ?? '-'))} | IVR ${metrics.iv_rank == null ? 'PENDING' : escapeHtml(String(metrics.iv_rank))} | Delta ${escapeHtml(String(metrics.delta ?? '-'))} | Exp ${escapeHtml(String(metrics.expiry_days ?? '-'))}d</span>` : ''}
          ${optionReady ? `<span>BE ${escapeHtml(String(metrics.breakeven_underlying ?? '-'))} | Req ${escapeHtml(String(metrics.required_move_pct ?? '-'))}% / Exp ${escapeHtml(String(metrics.expected_move_pct ?? '-'))}% | Delta SL ${escapeHtml(String(metrics.option_sl_price ?? '-'))} (${escapeHtml(String(metrics.premium_risk_pct ?? '-'))}%) | Cost/unit ${escapeHtml(String(metrics.est_costs_per_unit ?? '-'))}</span>` : ''}
          ${optionReady ? `<span>Liquidity ${metrics.liquidity_whitelist_pass === true ? 'OK' : metrics.liquidity_whitelist_pass === false ? 'NO' : 'WAIT'} | Physical ${metrics.physical_settlement_block ? 'BLOCK' : 'OK'} | Event ${escapeHtml(String(event.next_event_date || 'clear'))}</span>` : ''}
        </div>

        <div class="option-gates">
          ${gate('Trend', s.gates?.trend)}
          ${gate('Volume', s.gates?.volume)}
          ${gate('VWAP', s.gates?.vwap)}
          ${gate('OI', s.gates?.oi, 'Needs option-chain OI change')}
          ${gate('IV', s.gates?.iv, 'Needs option-chain implied volatility')}
          ${gate('IV Rank', s.gates?.iv_rank, 'Needs 60-session IV history; blocks elevated IV buying')}
          ${gate('Spread', s.gates?.spread, 'Needs option bid/ask spread')}
          ${gate('Delta', s.gates?.delta, 'Directional buying band 0.35 to 0.60')}
          ${gate('Premium SL', s.gates?.premium_sl, 'Derived from underlying SL and delta')}
          ${gate('Breakeven', s.gates?.breakeven, 'Underlying T1 must clear option breakeven')}
          ${gate('Liquidity', s.gates?.liquidity_whitelist, 'Minimum OI, volume and spread whitelist')}
          ${gate('Physical', s.gates?.physical_settlement, 'Blocks stock-option buying near expiry because of physical settlement risk')}
          ${gate('Event', s.gates?.event_calendar, 'Blocks T-2 to T+1 around results/board events')}
          ${gate('Strike', s.gates?.strike, 'Needs ATM/near-ATM strike selection')}
          ${gate('Expiry', s.gates?.expiry, 'Avoid expiry too close / too far')}
        </div>

        <div class="option-reason">${escapeHtml(reason)}</div>
        ${s.score_cap_detail || metrics.score_cap_detail ? `<div class="option-blockers"><span>Score cap: ${escapeHtml(String(s.score_cap_detail || metrics.score_cap_detail))}</span></div>` : ''}
        ${blockers.length ? `<div class="option-blockers">${blockers.slice(0, 4).map(x => `<span>${escapeHtml(String(x))}</span>`).join('')}</div>` : ''}
      </div>
    `;
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
  }
}
