/**
 * Scanner Panel — information-dense trading intelligence table
 * Columns: Symbol | LTP | Chg% | RVol | Sector | Trend | Setup | Bias | Opt | Ready
 * Toggle-able columns: Volume, VWAP, EMA, MACD (hidden by default on narrow screens)
 */
import { VirtualScroll } from './virtual-scroll.js';
import { formatPrice, formatVolume, formatPct, formatRelVol, escapeHtml, debounce } from './utils.js';
import { api } from './api.js';
import { ws } from './ws.js';

// ── Column definitions ─────────────────────────────────────────────────────
// toggle:true = can be hidden (via the Columns button); defaultHidden:true =
// hidden on first load. Essential set per direct spec: Symbol, Sector, LTP,
// Chg%, Strength, Conviction, MTF, Bias (CE/PE), Entry, T1, T2, T3, SL,
// Chain (option contract CE/PE favor). Everything else still exists and is
// one click away in the Columns panel — nothing was deleted, just off by
// default. Column order below matches that essential order first, then
// everything hidden-by-default after.
const COLUMNS = [
  { key: 'symbol',       label: 'Symbol',      align: 'left',   width: '112px',  toggle: false },
  { key: 'sector_id',    label: 'Sector',      align: 'left',   width: '122px',  toggle: true },
  { key: 'ltp',          label: 'LTP',         align: 'right',  width: '92px',   toggle: false },
  { key: 'change_pct',   label: 'Chg%',        align: 'right',  width: '70px',   toggle: true },
  { key: 'setup_strength', label: 'Strength',  align: 'right',  width: '92px',   toggle: true },
  { key: 'option_readiness', label: 'Conviction', align: 'right', width: '104px', toggle: true },
  { key: 'mtf_dots',     label: 'MTF',         align: 'center', width: '124px',  toggle: true },
  { key: 'trade_decision', label: 'Bias',      align: 'center', width: '88px',   toggle: true },
  { key: 'entry_price_hint', label: 'Entry',   align: 'right',  width: '88px',   toggle: true },
  { key: 'target_1_hint', label: 'T1',         align: 'right',  width: '78px',   toggle: true },
  { key: 'target_2_hint', label: 'T2',         align: 'right',  width: '78px',   toggle: true },
  { key: 'target_3_hint', label: 'T3',         align: 'right',  width: '78px',   toggle: true },
  { key: 'stop_loss_hint', label: 'SL',        align: 'right',  width: '78px',   toggle: true },
  { key: 'chain_execution_status', label: 'Chain', align: 'center', width: '112px', toggle: true },

  { key: 'prev_diff',    label: 'Prev close +/-', align: 'right', width: '118px', toggle: true, defaultHidden: true },
  { key: 'rel_vol',      label: 'RVol',        align: 'right',  width: '66px',   toggle: true, defaultHidden: true },
  { key: 'smart_rank',   label: 'Rank',        align: 'right',  width: '86px',   toggle: true, defaultHidden: true },
  { key: 'mode_signal',  label: 'Mode Signal', align: 'center', width: '126px',  toggle: true, defaultHidden: true },
  { key: 'gate_score',   label: 'Gates',       align: 'center', width: '108px',  toggle: true, defaultHidden: true },
  { key: 'intelligence_score', label: 'Intel', align: 'right',  width: '92px',   toggle: true, defaultHidden: true },
  { key: 'news_confirmation', label: 'News',   align: 'center', width: '104px',  toggle: true, defaultHidden: true },
  { key: 'trade_horizon', label: 'Horizon',    align: 'center', width: '104px',  toggle: true, defaultHidden: true },
  { key: 'chase_quality', label: 'Chase',      align: 'center', width: '118px',  toggle: true, defaultHidden: true },
  { key: 'intraday_score', label: 'Intra',     align: 'right',  width: '78px',   toggle: true, defaultHidden: true },
  { key: 'swing_score',  label: 'Swing',       align: 'right',  width: '78px',   toggle: true, defaultHidden: true },
  { key: 'trend_bias',   label: 'Trend',       align: 'center', width: '78px',   toggle: true, defaultHidden: true },
  { key: 'mtf_score',    label: 'MTF Score',   align: 'right',  width: '94px',   toggle: true, defaultHidden: true },
  { key: 'mtf_source',   label: 'MTF Src',     align: 'center', width: '82px',   toggle: true, defaultHidden: true },
  { key: 'direction_zone', label: 'CE / PE Zone', align: 'left', width: '210px', toggle: true, defaultHidden: true },
  { key: 'ce_score',      label: 'CE',         align: 'right',  width: '72px',   toggle: true, defaultHidden: true },
  { key: 'pe_score',      label: 'PE',         align: 'right',  width: '72px',   toggle: true, defaultHidden: true },
  { key: 'evidence',     label: 'Why / Block', align: 'left',   width: '176px',  toggle: true, defaultHidden: true },
  { key: 'positive_above', label: 'Bull above', align: 'right', width: '92px',   toggle: true, defaultHidden: true },
  { key: 'negative_below', label: 'Bear below', align: 'right', width: '92px',   toggle: true, defaultHidden: true },
  { key: 'ai_trade_map', label: 'AI Trade Map', align: 'left',  width: '220px',  toggle: true, defaultHidden: true },
  { key: 'readiness',    label: 'Ready',       align: 'right',  width: '72px',   toggle: true, defaultHidden: true },
  { key: 'status',       label: 'Status',      align: 'center', width: '92px',   toggle: true, defaultHidden: true },
  { key: 'alignment',    label: 'Alignment',   align: 'left',   width: '104px',  toggle: true, defaultHidden: true },
  { key: 'fibo_pivot',   label: 'Fibo P',      align: 'right',  width: '78px',   toggle: true, defaultHidden: true },
  { key: 'volume',       label: 'Vol',         align: 'right',  width: '70px',   toggle: true, defaultHidden: true },
  { key: 'vwap_state',   label: 'VWAP',        align: 'center', width: '68px',   toggle: true, defaultHidden: true },
  { key: 'ema_state',    label: 'EMA',         align: 'center', width: '62px',   toggle: true, defaultHidden: true },
  { key: 'macd_state',   label: 'MACD',        align: 'center', width: '68px',   toggle: true, defaultHidden: true },
];
const STICKY_END_KEY = 'sector_id';

// ── Density presets ─────────────────────────────────────────────────────────
// Row height must match what VirtualScroll is told (see virtual-scroll.js's
// setRowHeight()) -- it uses a fixed pixel height for scroll-position math,
// so CSS row height and this value have to agree or "which rows are visible"
// silently desyncs from what's actually on screen.
const DENSITY = {
  compact:     { rowHeight: 26, label: 'Compact' },
  comfortable: { rowHeight: 34, label: 'Comfortable' },
  spacious:    { rowHeight: 44, label: 'Spacious' },
};
const DEFAULT_DENSITY = 'compact';

const STATE_COLORS = {
  coiled:       { bg: '#14532d', color: '#4ade80', label: 'COILED'  },
  accumulating: { bg: '#1e3a5f', color: '#60a5fa', label: 'ACCUM'   },
  compressing:  { bg: '#2d2a14', color: '#facc15', label: 'COMP'    },
  triggered:    { bg: '#3b0764', color: '#e879f9', label: 'TRIGRD'  },
  idle:         { bg: '',        color: '',         label: ''        },
};

const SIG_COLORS = {
  bullish: { bg: '#14532d', color: '#4ade80', label: 'BUY'  },
  bearish: { bg: '#4c0519', color: '#f87171', label: 'SELL' },
};

function convClass(score) {
  if (!score) return '';
  if (score >= 95) return 'conv-95';
  if (score >= 85) return 'conv-85';
  if (score >= 75) return 'conv-75';
  if (score >= 65) return 'conv-65';
  return '';
}

function decisionClass(value) {
  const v = String(value || '').toUpperCase();
  if (v.includes('BUY CE') || v === 'BUY') return 'buy';
  if (v.includes('BUY PE') || v === 'SELL') return 'sell';
  if (v.includes('HOLD') || v.includes('WAIT')) return 'hold';
  return 'avoid';
}

function nDiffClass(value) {
  const n = Number(value || 0);
  if (n > 0) return 'positive';
  if (n < 0) return 'negative';
  return 'text-muted';
}

function scoreColor(score) {
  const n = Number(score || 0);
  if (n >= 80) return '#22c55e';
  if (n >= 65) return '#84cc16';
  if (n >= 50) return '#facc15';
  if (n >= 35) return '#fb923c';
  return '#ef4444';
}

function scoreMeter(score) {
  const n = Math.max(0, Math.min(100, Math.round(Number(score || 0))));
  const color = scoreColor(n);
  return `<div class="scanner-score-meter">
    <div class="scanner-score-track"><div class="scanner-score-fill" style="width:${n}%;background:${color}"></div></div>
    <span style="color:${color}">${n || '—'}</span>
  </div>`;
}

function stateChip(label, value) {
  const cls = decisionClass(value || label);
  return `<span class="trade-chip ${cls}">${escapeHtml(label || '—')}</span>`;
}

function mtfDots(dots) {
  const data = dots && typeof dots === 'object' ? dots : {};
  const color = { G: 'buy', R: 'sell', Y: 'hold' };
  return ['1M', '5M', '15M', '1H', '4H', '1D'].map(tf => {
    const v = String(data[tf] || 'Y').toUpperCase();
    return `<span class="mtf-dot ${color[v] || 'hold'}" title="${tf}">${tf.replace('M','')}</span>`;
  }).join('');
}

function horizonChip(value, friendlyLabel) {
  const v = String(value || 'INTRADAY').toUpperCase();
  const cls = v.includes('SWING') ? 'swing' : v.includes('BTST') ? 'btst' : v.includes('AVOID') ? 'avoid' : 'intraday';
  // Prefer the backend's broker-card-style label (Intraday/Short Term/
  // Medium Term/Avoid) when supplied; falls back to the raw value for any
  // caller that hasn't been updated to pass it.
  const label = friendlyLabel || (v === 'BTST_1_2D' ? 'BTST 1-2D' : v.replaceAll('_', ' '));
  return `<span class="horizon-chip ${cls}">${escapeHtml(label)}</span>`;
}

function chaseChip(value) {
  const v = String(value || 'WATCH_ONLY').toUpperCase();
  const cls = v.includes('HIGHLY') ? 'hot' : v.includes('CLEAN') ? 'clean' : v.includes('RETEST') ? 'wait' : v.includes('NO') ? 'avoid' : 'watch';
  return `<span class="chase-chip ${cls}">${escapeHtml(v.replaceAll('_', ' '))}</span>`;
}

function aiTradeMapCell(item) {
  const text = item.breakout_explanation || 'Awaiting breakout map';
  return `<div class="ai-map-cell" title="${escapeHtml(text)}">
    <b>${escapeHtml(item.sustain_rule || 'Wait')}</b>
    <span>${escapeHtml(text)}</span>
  </div>`;
}

function intelCell(item) {
  const score = Math.round(Number(item.intelligence_score || item.option_readiness || 0));
  const zone = String(item.intelligence_zone || '').toUpperCase();
  const action = String(item.intelligence_action || '').replaceAll('_', ' ');
  const title = item.intelligence_summary || item.trade_map_summary || 'Infusion intelligence building';
  return `<div class="intel-score-cell" title="${escapeHtml(title)}">
    ${scoreMeter(score)}
    <small>${escapeHtml(zone || action || 'WATCH')}</small>
  </div>`;
}

function newsCell(item) {
  const news = item.news_confirmation && typeof item.news_confirmation === 'object' ? item.news_confirmation : {};
  const state = String(news.state || 'NO_NEWS').toUpperCase();
  const stance = String(news.stance || 'NO_NEWS').toUpperCase();
  const cls = state.includes('CONFIRMED') ? 'buy'
    : state.includes('CONFLICT') || stance.includes('BEARISH') ? 'sell'
    : state.includes('EVENT') ? 'hold'
    : state.includes('UNCONFIRMED') || state.includes('WATCH') ? 'hold'
    : 'avoid';
  const label = state === 'NO_NEWS' ? 'NO NEWS'
    : state === 'UNCONFIRMED' ? 'UNCONF'
    : state === 'CONFLICTING' ? 'CONFLICT'
    : state;
  const score = Number(news.score || item.news_edge_score || 0);
  const title = news.message || 'News confirmation pending; select stock to fetch public news.';
  return `<div class="news-confirm-cell" title="${escapeHtml(title)}">
    <span class="trade-chip ${cls}">${escapeHtml(label)}</span>
    <small>${escapeHtml(stance)} ${score ? Math.round(score) : ''}</small>
  </div>`;
}

// Standard 4-way OI read: does open interest agree with price direction
// (fresh positioning) or oppose it (positions closing)? Only meaningful
// once there's live Upstox chain data for this row -- see
// api/routes/ticks.py:_classify_oi_buildup(). "PROXY" (no live chain yet)
// is a deliberate scope choice, not a bug: chains are only live-fetched for
// the highest-priority candidates to stay within Upstox rate limits, not
// all 208 F&O symbols at once.
function oiSignalText(item) {
  const signal = String(item.chain_oi_signal || '').toUpperCase();
  if (!signal || signal === 'NO_DATA' || signal === 'OI FLAT') return '';
  const pct = item.chain_oi_change_pct;
  const pctText = pct != null ? ` ${Number(pct) >= 0 ? '+' : ''}${Number(pct).toFixed(1)}%` : '';
  return `${signal}${pctText}`;
}

function chainCell(item) {
  const ready = Boolean(item.option_chain_ready);
  const status = String(item.chain_execution_status || (ready ? 'WAIT_CONTRACT' : 'PROXY')).toUpperCase();
  const score = Math.round(Number(item.chain_option_score || item.option_readiness || 0));
  const raw = item.chain_raw_option_score == null ? '' : `Raw ${Math.round(Number(item.chain_raw_option_score || 0))}`;
  const grade = item.chain_quality_grade || '';
  const oiSignal = oiSignalText(item);
  const cls = status.includes('TRADE_READY') ? 'buy'
    : status.includes('AVOID') ? 'sell'
    : status.includes('WAIT') || status.includes('PENDING') ? 'hold'
    : 'avoid';
  const label = status === 'TRADE_READY' ? 'READY'
    : status === 'AVOID_CONTRACT' ? 'AVOID'
    : status === 'WAIT_CONTRACT' ? 'WAIT'
    : status === 'CHAIN_PENDING' ? 'PENDING'
    : ready ? 'CHAIN' : 'PROXY';
  const title = [
    item.chain_suggested_contract ? `Contract: ${item.chain_suggested_contract}` : 'No cached Upstox option contract yet',
    item.chain_score_cap_detail ? `Cap: ${item.chain_score_cap_detail}` : '',
    item.chain_spread_pct != null ? `Spread: ${item.chain_spread_pct}%` : '',
    item.chain_oi != null ? `OI: ${Math.round(Number(item.chain_oi || 0))}` : '',
    oiSignal ? `OI signal: ${oiSignal}` : '',
    item.chain_iv != null ? `IV: ${item.chain_iv}` : '',
    item.chain_delta != null ? `Delta: ${item.chain_delta}` : '',
  ].filter(Boolean).join('\n');
  const subText = oiSignal || `${score ? score : '-'}${grade ? ` · ${escapeHtml(String(grade))}` : ''}${raw ? ` · ${escapeHtml(raw)}` : ''}`;
  return `<div class="chain-cell" title="${escapeHtml(title)}">
    <span class="trade-chip ${cls}">${escapeHtml(label)}</span>
    <small class="${oiSignal ? (String(item.chain_oi_bias).toUpperCase() === 'BULLISH' ? 'positive' : 'negative') : ''}">${subText}</small>
  </div>`;
}

function commandCenterBlock(item) {
  const cmd = item.command_center && typeof item.command_center === 'object' ? item.command_center : {};
  const evidence = cmd.option_evidence && typeof cmd.option_evidence === 'object' ? cmd.option_evidence : {};
  const premium = evidence.premium != null ? formatPrice(evidence.premium) : '-';
  const spread = evidence.spread_pct != null ? `${Number(evidence.spread_pct).toFixed(2)}%` : '-';
  const oi = evidence.oi != null ? Math.round(Number(evidence.oi || 0)).toLocaleString('en-IN') : '-';
  const iv = evidence.iv != null ? `${Number(evidence.iv).toFixed(1)}` : '-';
  const t3 = Number(cmd.target_3 || 0);
  const t3Text = t3 > 0 ? formatPrice(t3) : '-';
  const headline = cmd.headline || item.trade_map_summary || item.breakout_explanation || 'Command center evidence building';
  const actionText = cmd.action_text || item.breakout_explanation || 'Wait for closed-candle trigger, volume, and option confirmation.';
  const failText = cmd.fail_text || 'If trigger fails, do not chase. Reassess CE/PE from the wait zone.';
  const why = cmd.why || (Array.isArray(item.strength_reasons) ? item.strength_reasons[0] : '') || item.mtf_text || 'Evidence building';
  const blocker = cmd.blocker || (Array.isArray(item.rejection_reasons) && item.rejection_reasons[0]) || 'Anti-chase clean';
  return `
    <div class="command-answer-block">
      <div class="command-answer-head">
        <span>Command Answers</span>
        <b>${escapeHtml(cmd.quality || item.intelligence_action || 'WATCH')}</b>
        <em>${escapeHtml(cmd.horizon || item.intelligence_horizon || item.trade_horizon || 'WATCH')} · Score ${Math.round(Number(cmd.score || item.intelligence_score || 0))}</em>
      </div>
      <p>${escapeHtml(headline)}</p>
      <div class="command-answer-grid">
        <span><label>CE trigger</label><b class="positive">${formatPrice(cmd.ce_above || item.ce_active_above || item.positive_above)}</b><small>bullish only above</small></span>
        <span><label>PE trigger</label><b class="negative">${formatPrice(cmd.pe_below || item.pe_active_below || item.negative_below)}</b><small>bearish only below</small></span>
        <span><label>Support</label><b>${formatPrice(cmd.support || item.fibo_s2 || item.negative_below)}</b><small>nearest defense</small></span>
        <span><label>Resistance</label><b>${formatPrice(cmd.resistance || item.fibo_r2 || item.positive_above)}</b><small>nearest blocker</small></span>
        <span><label>SL</label><b class="negative">${formatPrice(cmd.stop_loss || item.stop_loss_hint)}</b><small>invalid line</small></span>
        <span><label>T1 / T2 / T3</label><b class="positive">${formatPrice(cmd.target_1 || item.target_1_hint)} / ${formatPrice(cmd.target_2 || item.target_2_hint)} / ${t3Text}</b><small>underlying targets</small></span>
        <span><label>Dominance</label><b>${escapeHtml(cmd.dominance || 'MIXED')}</b><small>${escapeHtml(cmd.dominance_reason || 'Buyers/sellers evidence building')}</small></span>
        <span><label>Index drag</label><b>${escapeHtml(cmd.index_drag || 'CHECK')}</b><small>${escapeHtml(cmd.index_drag_note || 'Needs NIFTY/BANKNIFTY context')}</small></span>
        <span><label>Option chain</label><b>${escapeHtml(cmd.option_state || item.chain_execution_status || 'PENDING')}</b><small>${escapeHtml(cmd.option_contract || item.chain_suggested_contract || 'contract pending')}</small></span>
        <span><label>Premium/OI/IV</label><b>${premium} · OI ${oi} · IV ${iv}</b><small>Spread ${spread}</small></span>
        <span><label>News</label><b>${escapeHtml(cmd.news_state || item.news_confirmation?.state || 'NO_NEWS')}</b><small>${escapeHtml(cmd.news_message || item.news_confirmation?.message || 'No cached news edge')}</small></span>
        <span><label>Why / Block</label><b>${escapeHtml(why)}</b><small>${escapeHtml(blocker)}</small></span>
      </div>
      <div class="command-answer-next">
        <b>Action:</b> ${escapeHtml(actionText)}
        <small><b>Failure:</b> ${escapeHtml(failText)} | <b>Chase:</b> ${escapeHtml(cmd.chase_text || 'Use chart confirmation first.')}</small>
      </div>
    </div>
  `;
}

function px(width) {
  return Number(String(width || '').replace('px', '')) || 0;
}

function visibleColumns(hidden) {
  return COLUMNS.filter(c => !hidden.has(c.key));
}

// Sticky (frozen) columns keep a fixed width — their left offset is computed
// once from nominal widths (see stickyMeta), so letting them flex would
// desync that math. Every other column stretches to fill whatever space is
// left, proportional to its own minimum width -- turn a column off and the
// rest automatically get wider, no manual retuning needed.
function cellSizeStyle(col, isSticky) {
  if (isSticky) {
    return `width:${col.width};min-width:${col.width};max-width:${col.width};`;
  }
  return `flex:1 1 ${col.width};min-width:${col.width};`;
}

function stickyMeta(columns) {
  const meta = new Map();
  let left = 0;
  for (const col of columns) {
    meta.set(col.key, left);
    left += px(col.width);
    if (col.key === STICKY_END_KEY) break;
  }
  return meta;
}

function mtfSourceChip(source) {
  const src = String(source || 'proxy').toLowerCase();
  const cls = src.includes('historical') ? 'historical' : src.includes('limited') ? 'limited' : src.includes('missing') ? 'missing' : 'proxy';
  const label = cls === 'historical' ? 'HIST' : cls === 'limited' ? 'LIMIT' : cls === 'missing' ? 'MISS' : 'PROXY';
  return `<span class="mtf-source-chip ${cls}" title="MTF source: ${escapeHtml(source || 'live proxy')}">${label}</span>`;
}

function rowEvidence(item) {
  const strengths = Array.isArray(item.strength_reasons) ? item.strength_reasons : [];
  const blockers = Array.isArray(item.rejection_reasons) && item.rejection_reasons.length
    ? item.rejection_reasons
    : Array.isArray(item.weakness_reasons) ? item.weakness_reasons : [];
  const why = strengths[0] || item.mtf_text || 'Awaiting evidence';
  const block = blockers[0] || (item.anti_chase_ok ? 'Anti-chase clean' : 'No blocker data');
  const title = [
    `Why: ${strengths.slice(0, 4).join(' | ') || why}`,
    `Block: ${blockers.slice(0, 4).join(' | ') || block}`,
    `MTF: ${item.mtf_text || 'proxy'}`
  ].join('\n');
  return `
    <div class="scanner-evidence" title="${escapeHtml(title)}">
      <span class="evidence-why">${escapeHtml(why)}</span>
      <span class="evidence-block">${escapeHtml(block)}</span>
    </div>
  `;
}

function actionClass(item) {
  const decision = String(item.direction_bias || item.trade_decision || '').toUpperCase();
  if (decision.includes('BUY CE')) return 'bias-ce';
  if (decision.includes('BUY PE')) return 'bias-pe';
  if (decision.includes('HOLD') || decision.includes('WAIT') || String(item.status || '').toUpperCase().includes('WATCH')) return 'bias-wait';
  return 'bias-avoid';
}

function isCERow(item) {
  return String(item.direction_bias || item.trade_decision || '').toUpperCase().includes('BUY CE');
}

function isPERow(item) {
  return String(item.direction_bias || item.trade_decision || '').toUpperCase().includes('BUY PE');
}

function isWaitRow(item) {
  const text = `${item.direction_bias || item.trade_decision || ''} ${item.direction_state || ''} ${item.status || ''}`.toUpperCase();
  return text.includes('HOLD') || text.includes('WAIT') || text.includes('WATCH');
}

function isAvoidRow(item) {
  const text = `${item.trade_decision || ''} ${item.status || ''}`.toUpperCase();
  return text.includes('AVOID') || text.includes('NO_TRADE');
}

const SIGNAL_MODE_PROFILES = {
  precision: { label: 'Precision', watch: 70, paper: 80, minRr: 1.6, rankBoost: 0 },
  opportunity: { label: 'Opportunity', watch: 55, paper: 72, minRr: 1.4, rankBoost: 12 },
  aggressive_paper: { label: 'Aggressive Paper', watch: 45, paper: 62, minRr: 1.2, rankBoost: 20 },
};

function signalModeProfile(mode) {
  return SIGNAL_MODE_PROFILES[String(mode || 'precision')] || SIGNAL_MODE_PROFILES.precision;
}

function rrOf(item) {
  const direct = Number(item.risk_reward_ratio ?? item.rr ?? item.rr1 ?? 0);
  if (Number.isFinite(direct) && direct > 0) return direct;
  const entry = Number(item.entry_price_hint || item.entry_price || 0);
  const stop = Number(item.stop_loss_hint || item.stop_price || 0);
  const target = Number(item.target_1_hint || item.target_price || 0);
  const risk = Math.abs(entry - stop);
  const reward = Math.abs(target - entry);
  return risk > 0 && reward > 0 ? reward / risk : 0;
}

function hasHardChaseBlock(item) {
  const reasons = [
    ...(Array.isArray(item.anti_chase_reasons) ? item.anti_chase_reasons : []),
    ...(Array.isArray(item.rejection_reasons) ? item.rejection_reasons : []),
  ].map(x => String(x || '').toLowerCase());
  return item.anti_chase_ok === false || reasons.some(x =>
    x.includes('large signal candle') || x.includes('vwap stretch') || x.includes('stop too wide')
  );
}

function modeSignal(item, mode = 'precision') {
  const profile = signalModeProfile(mode);
  const option = Number(item.option_readiness || item.conviction_score || item.score || 0);
  const setup = Number(item.setup_strength || item.readiness || 0);
  const mtf = Number(item.mtf_score || item.pine_confidence || 0);
  const rr = rrOf(item);
  const decision = String(item.direction_bias || item.trade_decision || '').toUpperCase();
  const directional = decision.includes('BUY CE') || decision.includes('BUY PE');
  const hardBlock = hasHardChaseBlock(item);
  const blockers = Array.isArray(item.rejection_reasons) ? item.rejection_reasons.length : 0;
  const live = Boolean(item.signal_active);

  if (live) {
    return { key: 'live', label: 'LIVE ALERT', tone: 'live', priority: 5, reason: 'Passed strict live-alert gate.' };
  }
  if (hardBlock) {
    return { key: 'no_chase', label: 'NO CHASE', tone: 'block', priority: 1, reason: 'Anti-chase rejected location/risk.' };
  }
  if (directional && option >= profile.paper && rr >= profile.minRr && setup >= 50) {
    return { key: 'paper_ready', label: 'PAPER READY', tone: 'good', priority: 4, reason: `${profile.label}: paper review threshold passed.` };
  }
  if (directional && option >= profile.watch && rr >= profile.minRr) {
    return { key: 'watch_only', label: 'WATCH ONLY', tone: 'watch', priority: 3, reason: `${profile.label}: watch threshold passed, not live-alert grade.` };
  }
  if (mode === 'aggressive_paper' && directional && option >= 45 && setup >= 40 && mtf >= 45 && blockers <= 2) {
    return { key: 'learn', label: 'LEARN ONLY', tone: 'learn', priority: 2, reason: 'Aggressive paper learning candidate only.' };
  }
  return { key: 'avoid', label: 'AVOID', tone: 'avoid', priority: 0, reason: 'Below selected mode threshold.' };
}

function modeAwareRank(item, mode = 'precision') {
  const base = smartRank(item);
  const sig = modeSignal(item, mode);
  const profile = signalModeProfile(mode);
  const gates = checklistSummary(item, mode);
  let boost = sig.priority * 9 + profile.rankBoost;
  if (sig.key === 'no_chase') boost = -18;
  if (sig.key === 'avoid') boost = -10;
  boost += Math.min(18, gates.pass * 2) - gates.fail * 10;
  return Math.round(Math.max(0, Math.min(180, base + boost)));
}

function modeSignalChip(item, mode = 'precision') {
  const sig = modeSignal(item, mode);
  return `<span class="mode-signal-chip ${sig.tone}" title="${escapeHtml(sig.reason)}">${escapeHtml(sig.label)}</span>`;
}

function gateItem(label, state, detail) {
  const key = String(state || 'warn').toLowerCase();
  const icon = key === 'pass' ? 'OK' : key === 'fail' ? 'X' : '!';
  return `<span class="trade-gate ${key}">
    <i>${icon}</i>
    <b>${escapeHtml(label)}</b>
    <small>${escapeHtml(detail || '')}</small>
  </span>`;
}

function selectedTradeChecklist(item, mode = 'precision') {
  const profile = signalModeProfile(mode);
  const modeSig = modeSignal(item, mode);
  const decision = String(item.direction_bias || item.trade_decision || '').toUpperCase();
  const mtf = Number(item.mtf_score || item.pine_confidence || 0);
  const setup = Number(item.setup_strength || item.readiness || 0);
  const option = Number(item.option_readiness || item.conviction_score || item.score || 0);
  const rr = rrOf(item);
  const chainStatus = String(item.chain_execution_status || (item.option_chain_ready ? 'WAIT_CONTRACT' : 'PROXY')).toUpperCase();
  const news = item.news_confirmation && typeof item.news_confirmation === 'object' ? item.news_confirmation : {};
  const newsState = String(news.state || 'NO_NEWS').toUpperCase();
  const gates = [];

  gates.push(gateItem(
    'Direction',
    decision.includes('BUY CE') || decision.includes('BUY PE') ? 'pass' : 'warn',
    decision.includes('BUY CE') ? 'CE side locked' : decision.includes('BUY PE') ? 'PE side locked' : 'No stable side yet'
  ));
  gates.push(gateItem(
    'Mode',
    ['live', 'paper_ready'].includes(modeSig.key) ? 'pass' : modeSig.key === 'no_chase' || modeSig.key === 'avoid' ? 'fail' : 'warn',
    `${modeSig.label}: ${modeSig.reason}`
  ));
  gates.push(gateItem(
    'MTF',
    mtf >= 60 ? 'pass' : mtf >= 50 ? 'warn' : 'fail',
    `Score ${Math.round(mtf)} · ${item.mtf_text || 'alignment building'}`
  ));
  gates.push(gateItem(
    'Strength',
    setup >= 60 ? 'pass' : setup >= 45 ? 'warn' : 'fail',
    `Setup ${Math.round(setup)}`
  ));
  gates.push(gateItem(
    'Option',
    option >= profile.paper ? 'pass' : option >= profile.watch ? 'warn' : 'fail',
    `Conviction ${Math.round(option)} · paper ${profile.paper}+`
  ));
  gates.push(gateItem(
    'R:R',
    rr >= profile.minRr ? 'pass' : rr >= 1.2 ? 'warn' : 'fail',
    rr ? `${rr.toFixed(1)}:1 target logic` : 'R:R unavailable'
  ));
  gates.push(gateItem(
    'Anti-chase',
    hasHardChaseBlock(item) ? 'fail' : item.anti_chase_ok === false ? 'warn' : 'pass',
    hasHardChaseBlock(item) ? 'Wait for retest/cleaner candle' : 'Location acceptable'
  ));
  gates.push(gateItem(
    'Chain',
    chainStatus.includes('TRADE_READY') ? 'pass' : chainStatus.includes('AVOID') ? 'fail' : 'warn',
    `${chainStatus.replaceAll('_', ' ')}${item.chain_suggested_contract ? ` · ${item.chain_suggested_contract}` : ''}`
  ));
  gates.push(gateItem(
    'News',
    newsState.includes('CONFLICT') || newsState.includes('EVENT') ? 'fail' : newsState.includes('CONFIRMED') ? 'pass' : 'warn',
    news.message || 'No strong public-news confirmation cached'
  ));

  const pass = gates.filter(x => x.includes('trade-gate pass')).length;
  const fail = gates.filter(x => x.includes('trade-gate fail')).length;
  const verdict = fail ? 'WAIT / REJECT' : pass >= 7 ? 'GO REVIEW' : 'WAIT CONFIRM';
  const tone = fail ? 'fail' : pass >= 7 ? 'pass' : 'warn';
  return `<div class="trade-checklist ${tone}">
    <div class="trade-checklist-head">
      <span>Go / Wait Checklist</span>
      <b>${verdict}</b>
      <small>${pass}/9 pass · ${fail} hard fail</small>
    </div>
    <div class="trade-gates">${gates.join('')}</div>
  </div>`;
}

function checklistSummary(item, mode = 'precision') {
  const profile = signalModeProfile(mode);
  const modeSig = modeSignal(item, mode);
  const decision = String(item.direction_bias || item.trade_decision || '').toUpperCase();
  const mtf = Number(item.mtf_score || item.pine_confidence || 0);
  const setup = Number(item.setup_strength || item.readiness || 0);
  const option = Number(item.option_readiness || item.conviction_score || item.score || 0);
  const rr = rrOf(item);
  const chainStatus = String(item.chain_execution_status || (item.option_chain_ready ? 'WAIT_CONTRACT' : 'PROXY')).toUpperCase();
  const news = item.news_confirmation && typeof item.news_confirmation === 'object' ? item.news_confirmation : {};
  const newsState = String(news.state || 'NO_NEWS').toUpperCase();
  const states = [
    decision.includes('BUY CE') || decision.includes('BUY PE') ? 'pass' : 'warn',
    ['live', 'paper_ready'].includes(modeSig.key) ? 'pass' : ['no_chase', 'avoid'].includes(modeSig.key) ? 'fail' : 'warn',
    mtf >= 60 ? 'pass' : mtf >= 50 ? 'warn' : 'fail',
    setup >= 60 ? 'pass' : setup >= 45 ? 'warn' : 'fail',
    option >= profile.paper ? 'pass' : option >= profile.watch ? 'warn' : 'fail',
    rr >= profile.minRr ? 'pass' : rr >= 1.2 ? 'warn' : 'fail',
    hasHardChaseBlock(item) ? 'fail' : item.anti_chase_ok === false ? 'warn' : 'pass',
    chainStatus.includes('TRADE_READY') ? 'pass' : chainStatus.includes('AVOID') ? 'fail' : 'warn',
    newsState.includes('CONFLICT') || newsState.includes('EVENT') ? 'fail' : newsState.includes('CONFIRMED') ? 'pass' : 'warn',
  ];
  const pass = states.filter(x => x === 'pass').length;
  const warn = states.filter(x => x === 'warn').length;
  const fail = states.filter(x => x === 'fail').length;
  const verdict = fail ? 'REJECT' : pass >= 7 ? 'GO' : 'WAIT';
  const tone = fail ? 'fail' : pass >= 7 ? 'pass' : 'warn';
  return { pass, warn, fail, verdict, tone, score: pass * 10 + warn * 4 - fail * 12 };
}

function gateScoreCell(item, mode = 'precision') {
  const s = checklistSummary(item, mode);
  return `<span class="gate-score-cell ${s.tone}" title="${s.pass}/9 pass, ${s.warn} warn, ${s.fail} hard fail">
    <b>${escapeHtml(s.verdict)}</b>
    <small>${s.pass}/9 · F${s.fail}</small>
  </span>`;
}

function smartRank(item) {
  const option = Number(item.option_readiness || 0);
  const setup = Number(item.setup_strength || item.readiness || 0);
  const mtf = Number(item.mtf_score || item.pine_confidence || 0);
  const rvol = Number(item.rel_vol || 0);
  const chg = Number(item.change_pct || 0);
  const trend = String(item.trend_bias || '').toUpperCase();
  const decision = String(item.direction_bias || item.trade_decision || '').toUpperCase();
  const status = String(item.status || item.state || '').toUpperCase();
  const blockers = Array.isArray(item.rejection_reasons) ? item.rejection_reasons.length : 0;
  const antiChasePenalty = item.anti_chase_ok === false ? 14 : 0;
  const swing = Number(item.swing_score || 0);
  const intra = Number(item.intraday_score || 0);
  const chase = String(item.chase_quality || '').toUpperCase();

  let rank = 0;
  rank += option * 0.34;
  rank += setup * 0.28;
  rank += mtf * 0.18;
  rank += Math.min(18, Math.max(0, rvol) * 4);
  rank += Math.max(-12, Math.min(12, chg * 2.2));

  if (decision.includes('BUY CE') || decision.includes('BUY PE')) rank += 14;
  else if (decision.includes('HOLD') || decision.includes('WAIT')) rank += 4;
  else if (decision.includes('AVOID')) rank -= 12;

  if (status.includes('ARMED') || status.includes('TRIGGER')) rank += 12;
  else if (status.includes('WATCH') || status.includes('RETEST')) rank += 7;
  else if (status.includes('NO_TRADE')) rank -= 16;

  if ((decision.includes('BUY CE') && trend.includes('BUY')) || (decision.includes('BUY PE') && trend.includes('SELL'))) rank += 8;
  if (String(item.direction_state || '').includes('CONFLICT')) rank -= 10;
  if (option >= 70 && setup >= 70) rank += 10;
  if (option >= 60 && setup >= 60 && mtf >= 60) rank += 8;
  if (swing >= 72) rank += 8;
  if (intra >= 72) rank += 6;
  if (chase.includes('HIGHLY')) rank += 8;
  else if (chase.includes('CLEAN')) rank += 4;
  if (rvol >= 1.5 && Math.abs(chg) >= 0.35) rank += 5;
  if (blockers) rank -= Math.min(18, blockers * 6);
  rank -= antiChasePenalty;

  return Math.round(Math.max(0, Math.min(150, rank)));
}

function smartRankReasons(item) {
  const parts = [];
  const option = Math.round(Number(item.option_readiness || 0));
  const setup = Math.round(Number(item.setup_strength || item.readiness || 0));
  const mtf = Math.round(Number(item.mtf_score || item.pine_confidence || 0));
  const rvol = Number(item.rel_vol || 0);
  const chg = Number(item.change_pct || 0);
  const decision = String(item.direction_bias || item.trade_decision || '').toUpperCase();
  const blockers = Array.isArray(item.rejection_reasons) ? item.rejection_reasons.length : 0;
  if (option) parts.push(`Opt ${option}`);
  if (setup) parts.push(`Strength ${setup}`);
  if (mtf) parts.push(`MTF ${mtf}`);
  if (rvol >= 1.2) parts.push(`RVol ${rvol.toFixed(1)}x`);
  if (Math.abs(chg) >= 0.25) parts.push(`Move ${chg > 0 ? '+' : ''}${chg.toFixed(2)}%`);
  if (decision.includes('BUY CE') || decision.includes('BUY PE')) parts.push(decision);
  if (item.direction_state) parts.push(String(item.direction_state).replaceAll('_', ' '));
  if (item.anti_chase_ok === false) parts.push('Anti-chase reject');
  if (blockers) parts.push(`${blockers} blocker${blockers > 1 ? 's' : ''}`);
  return parts.length ? parts.join(' · ') : 'Waiting for conviction inputs';
}

function smartRankCell(item) {
  const n = Number(item.mode_rank || smartRank(item));
  const pct = Math.min(100, Math.round((n / 180) * 100));
  const color = n >= 105 ? '#16a34a' : n >= 82 ? '#65a30d' : n >= 58 ? '#d97706' : '#dc2626';
  return `
    <div class="smart-rank-cell" title="${escapeHtml(smartRankReasons(item))}">
      <div class="smart-rank-track"><i style="width:${pct}%;background:${color}"></i></div>
      <b style="color:${color}">${n}</b>
    </div>
  `;
}

function triggerDistance(item, side) {
  const ltp = Number(item.ltp || 0);
  const level = side === 'bear' ? Number(item.negative_below || 0) : Number(item.positive_above || 0);
  if (!ltp || !level) return null;
  const diffPct = ((level - ltp) / ltp) * 100;
  if (side === 'bear') {
    return { side, level, distance: diffPct, absDistance: Math.abs(diffPct), crossed: ltp <= level };
  }
  return { side, level, distance: diffPct, absDistance: Math.abs(diffPct), crossed: ltp >= level };
}

function directionalScores(item) {
  const option = Number(item.option_readiness || 0);
  const setup = Number(item.setup_strength || item.readiness || 0);
  const mtf = Number(item.mtf_score || item.pine_confidence || 0);
  const bullRaw = Number(item.bull_confidence ?? item.ce_confidence ?? item.bull_score ?? 0);
  const bearRaw = Number(item.bear_confidence ?? item.pe_confidence ?? item.bear_score ?? 0);
  const trend = String(item.trend_bias || '').toUpperCase();
  const decision = String(item.trade_decision || '').toUpperCase();
  const vwap = String(item.vwap_state || '').toUpperCase();
  const rvol = Math.min(12, Math.max(0, Number(item.rel_vol || 0)) * 3);

  let ce = bullRaw || (option * 0.38 + setup * 0.24 + mtf * 0.24 + rvol);
  let pe = bearRaw || (option * 0.38 + setup * 0.24 + mtf * 0.24 + rvol);
  if (trend.includes('BUY')) ce += 8;
  if (trend.includes('SELL')) pe += 8;
  if (decision.includes('BUY CE')) ce += 10;
  if (decision.includes('BUY PE')) pe += 10;
  if (vwap === 'ABOVE') ce += 5;
  if (vwap === 'BELOW') pe += 5;
  return {
    ce: Math.round(Math.max(0, Math.min(100, ce))),
    pe: Math.round(Math.max(0, Math.min(100, pe))),
  };
}

function deriveDirectionZone(item, previousLock) {
  const ltp = Number(item.ltp || 0);
  const ceAbove = Number(item.positive_above || item.breakout_area || item.entry_price_hint || 0);
  const peBelow = Number(item.negative_below || item.invalidation_area || item.stop_loss_hint || 0);
  const scores = directionalScores(item);
  const raw = String(item.trade_decision || 'WAIT').toUpperCase();
  const minScore = 65;
  const minGap = 10;
  const strongGap = 18;
  const ceCrossed = Boolean(ltp && ceAbove && ltp >= ceAbove);
  const peCrossed = Boolean(ltp && peBelow && ltp <= peBelow);
  const ceReady = ceCrossed && scores.ce >= minScore && scores.ce - scores.pe >= minGap;
  const peReady = peCrossed && scores.pe >= minScore && scores.pe - scores.ce >= minGap;

  let bias = 'WAIT';
  let state = 'WAIT_ZONE';
  let tone = 'wait';
  let reason = 'No trade: price is between CE trigger and PE trigger.';

  if (ceReady && !peReady) {
    bias = 'BUY CE';
    state = 'CE_ACTIVE';
    tone = 'ce';
    reason = `CE only above ${formatPrice(ceAbove)} with 5M/15M sustain.`;
  } else if (peReady && !ceReady) {
    bias = 'BUY PE';
    state = 'PE_ACTIVE';
    tone = 'pe';
    reason = `PE only below ${formatPrice(peBelow)} with 5M/15M sustain.`;
  } else if (ceReady && peReady) {
    bias = scores.ce >= scores.pe ? 'BUY CE' : 'BUY PE';
    state = 'CONFLICT';
    tone = 'conflict';
    reason = 'Conflict: both CE and PE zones are touched; wait for one closed-candle direction.';
  } else if (raw.includes('BUY CE')) {
    state = 'WAIT_CE_ABOVE';
    reason = `Raw CE bias exists, but wait until price sustains above ${formatPrice(ceAbove)}.`;
  } else if (raw.includes('BUY PE')) {
    state = 'WAIT_PE_BELOW';
    reason = `Raw PE bias exists, but wait until price sustains below ${formatPrice(peBelow)}.`;
  }

  const now = Date.now();
  const prevBias = previousLock?.bias || '';
  const prevSince = Number(previousLock?.since || 0);
  const flip = prevBias && bias !== 'WAIT' && prevBias !== bias && now - prevSince < 180000;
  const strongFlip = Math.abs(scores.ce - scores.pe) >= strongGap && (bias === 'BUY CE' ? ceCrossed : peCrossed);
  let switchNote = '';
  if (flip && !strongFlip) {
    switchNote = `Anti-flip lock: ${prevBias} not changed to ${bias}; need stronger candle/score confirmation.`;
    bias = 'WAIT';
    state = 'CONFLICT_LOCK';
    tone = 'conflict';
    reason = switchNote;
  } else if (prevBias && bias !== 'WAIT' && prevBias !== bias) {
    switchNote = `Bias switched ${prevBias} → ${bias}: trigger crossed and score gap confirmed.`;
  }

  return {
    bias,
    state,
    tone,
    reason,
    switchNote,
    ceAbove,
    peBelow,
    ceScore: scores.ce,
    peScore: scores.pe,
    waitText: ceAbove && peBelow ? `${formatPrice(peBelow)} – ${formatPrice(ceAbove)}` : 'building',
    updatedAt: now,
  };
}

function directionZoneCell(item) {
  const bias = String(item.direction_bias || 'WAIT').toUpperCase();
  const tone = item.direction_tone || (bias.includes('CE') ? 'ce' : bias.includes('PE') ? 'pe' : 'wait');
  return `
    <div class="direction-zone-cell ${tone}" title="${escapeHtml(item.direction_reason || '')}">
      <b>${escapeHtml(bias)}</b>
      <span><em>CE &gt;</em> ${formatPrice(item.ce_active_above)} <em>PE &lt;</em> ${formatPrice(item.pe_active_below)}</span>
      <small>${escapeHtml(item.direction_state || 'WAIT_ZONE')} · Wait ${escapeHtml(item.direction_wait_zone || 'building')}</small>
    </div>
  `;
}

function triggerCandidates(items) {
  const rows = [];
  for (const item of items) {
    const bull = triggerDistance(item, 'bull');
    const bear = triggerDistance(item, 'bear');
    if (bull && (bull.crossed || bull.absDistance <= 1.2)) rows.push({ item, ...bull });
    if (bear && (bear.crossed || bear.absDistance <= 1.2)) rows.push({ item, ...bear });
  }
  return rows
    .sort((a, b) => {
      if (a.crossed !== b.crossed) return a.crossed ? -1 : 1;
      if (a.absDistance !== b.absDistance) return a.absDistance - b.absDistance;
      return smartRank(b.item) - smartRank(a.item);
    })
    .slice(0, 5);
}

function tradingViewUrl(symbol) {
  return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(`NSE:${String(symbol || '').toUpperCase()}`)}`;
}

function currentRiskBudget() {
  const capital = Number(document.getElementById('riskCapital')?.value || 50000);
  const riskPct = Number(document.getElementById('riskPerTrade')?.value || 1);
  const riskAmount = Math.max(0, capital * riskPct / 100);
  return { capital, riskPct, riskAmount };
}

export class ScannerPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._data      = new Map();
    this._sorted    = [];
    this._prevLtp   = new Map();
    this._flashTimers = new Map();
    this._signals   = new Map();
    this._prebreakout = new Map();
    this._sort   = { key: 'smart_rank', dir: 'desc' };
    this._filter = { sector: '', search: '' };
    this._colFilters = {};
    this._masterFilter = '';
    this._preset = '';
    this._selectedSymbol = '';
    this._selectedOption = null;
    this._selectedOptionToken = 0;
    this._biasLocks = new Map();
    this._hidden = new Set(); // hidden column keys
    this._density = DEFAULT_DENSITY;
    this._vs = null;
    this._unsubs = [];
    this._warming = false;
    this._foTotalCount = 0;
    this._signalMode = 'precision';
    this._signalModeProfile = signalModeProfile('precision');
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  init() {
    // Load hidden columns from localStorage
    try {
      const saved = JSON.parse(localStorage.getItem('infusion:scanner:hidden:v2') || '[]');
      this._hidden = new Set(saved);
    } catch (_) {}
    // Default hidden columns
    COLUMNS.filter(c => c.defaultHidden && !this._hidden.has(c.key + ':shown'))
            .forEach(c => this._hidden.add(c.key));

    // Load density preference
    try {
      const savedDensity = localStorage.getItem('infusion:scanner:density');
      if (savedDensity && DENSITY[savedDensity]) this._density = savedDensity;
    } catch (_) {}

    this._el.classList.add('ifx-scanner');
    this._el.innerHTML = `
      <div class="filter-bar">
        <input type="text" class="filter-input" id="scannerSearch" placeholder="Search symbol..." />
        <select class="filter-select" id="scannerSector"><option value="">All Sectors</option></select>
        <select class="filter-select scanner-master-filter" id="scannerMasterFilter">
          <option value="">All Actions</option>
          <option value="paper_ready">Paper Ready</option>
          <option value="watch_only">Watch Only</option>
          <option value="no_chase">No Chase</option>
          <option value="ce_ready">CE Ready</option>
          <option value="pe_ready">PE Ready</option>
          <option value="swing_ready">Swing Ready</option>
          <option value="intraday_ready">Intraday Ready</option>
          <option value="wait">Wait / Watch</option>
          <option value="avoid">Avoid</option>
        </select>
        <div class="scanner-presets" id="scannerPresets">
          <button type="button" data-preset="strong_ce">Strong CE</button>
          <button type="button" data-preset="strong_pe">Strong PE</button>
          <button type="button" data-preset="high_volume">High Vol</button>
          <button type="button" data-preset="breakout_watch">Breakout</button>
          <button type="button" data-preset="swing_watch">Swing</button>
          <button type="button" data-preset="highly_chaseable">Chaseable</button>
          <button type="button" data-preset="reversal_watch">Reversal</button>
          <button type="button" data-preset="anti_chase_clean">Clean Chase</button>
          <button type="button" data-preset="">Clear</button>
        </div>
        <span class="filter-count" id="scannerCount">0 symbols</span>
        <div class="density-toggle" id="densityToggle">
          ${Object.entries(DENSITY).map(([key, d]) => `
            <button type="button" data-density="${key}" class="${key === this._density ? 'active' : ''}" title="${d.label} row spacing">${d.label}</button>
          `).join('')}
        </div>
        <button class="scanner-action-btn" id="mtfWarmBtn" title="Refresh historical MTF score cache for scanner rows">Warm MTF</button>
        <button class="col-toggle-btn" id="scannerColToggle" title="Toggle columns">Columns</button>
      </div>
      <div class="col-toggle-panel" id="scannerColPanel" style="display:none"></div>
      <div class="scanner-command-summary" id="scannerCommandSummary"></div>
      <div class="scanner-trade-plan" id="scannerTradePlan"></div>
      <div class="scanner-leader-strip" id="scannerLeaderStrip"></div>
      <div class="scanner-head-scroll" id="scannerHeadScroll">
        <div class="scanner-head" id="scannerHead"></div>
        <div class="scanner-filter-head" id="scannerFilterHead"></div>
      </div>
      <div class="vscroll-viewport" id="scannerViewport"></div>
    `;

    this._el.classList.add(`density-${this._density}`);
    this._buildHeader();

    const viewport = this._el.querySelector('#scannerViewport');
    const headScroll = this._el.querySelector('#scannerHeadScroll');
    this._vs = new VirtualScroll(viewport, {
      rowHeight: DENSITY[this._density].rowHeight,
      overscan: 15,
      renderRow: (item) => this._renderRow(item),
      keyFn: (item) => item.symbol,
      // Row selection is already handled by the delegated click listener
      // below (this._el, matches .scanner-row[data-symbol]) -- not wiring
      // onRowClick here too, that would fire the same selection twice.
    });

    this._el.querySelector('#densityToggle')?.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-density]');
      if (!btn) return;
      this._setDensity(btn.dataset.density);
    });

    // Header/body columns were drifting out of alignment: #scannerViewport
    // has a vertical scrollbar (208 rows), #scannerHeadScroll deliberately
    // doesn't (overflow-y:hidden) -- so the header's content area was
    // consistently wider than the body's by exactly the scrollbar's width.
    // With fixed-width columns that only showed up as slop at the far-right
    // edge; with fluid columns it spreads across every column. See
    // _syncHeaderScrollbarGutter() -- called here and after every data/
    // density change, since the scrollbar only exists once rows actually
    // overflow the viewport (measuring once at init reads 0, before any
    // data has loaded, and would never update).
    this._headScrollEl = headScroll;
    this._viewportEl = viewport;
    this._syncHeaderScrollbarGutter();
    if (window.ResizeObserver) {
      this._viewportResizeObserver = new ResizeObserver(() => this._syncHeaderScrollbarGutter());
      this._viewportResizeObserver.observe(viewport);
    }

    this._syncingScroll = false;
    viewport.addEventListener('scroll', () => {
      if (this._syncingScroll) return;
      this._syncingScroll = true;
      headScroll.scrollLeft = viewport.scrollLeft;
      this._syncingScroll = false;
    }, { passive: true });
    headScroll.addEventListener('scroll', () => {
      if (this._syncingScroll) return;
      this._syncingScroll = true;
      viewport.scrollLeft = headScroll.scrollLeft;
      this._syncingScroll = false;
    }, { passive: true });

    // Sort is delegated so rebuilt headers and browser caches cannot strand stale listeners.
    this._el.addEventListener('click', (e) => {
      const th = e.target.closest('.sh[data-col]');
      if (!th || !this._el.contains(th)) return;
      e.preventDefault();
      this._handleSort(th.dataset.col);
    });
    this._el.addEventListener('input', (e) => {
      const control = e.target.closest('[data-filter-col]');
      if (!control || !this._el.contains(control)) return;
      this._colFilters[control.dataset.filterCol] = control.value;
      this._forceScrollTop = true;
      this._applyFilterAndSort();
    });
    this._el.addEventListener('change', (e) => {
      const control = e.target.closest('[data-filter-col]');
      if (!control || !this._el.contains(control)) return;
      this._colFilters[control.dataset.filterCol] = control.value;
      this._forceScrollTop = true;
      this._applyFilterAndSort();
    });
    this._forceScrollTop = true;
    this._updateSortIndicators();

    // Search
    const searchEl = this._el.querySelector('#scannerSearch');
    const sectorEl = this._el.querySelector('#scannerSector');
    const warmBtn = this._el.querySelector('#mtfWarmBtn');
    const masterEl = this._el.querySelector('#scannerMasterFilter');
    const presetsEl = this._el.querySelector('#scannerPresets');
    searchEl.addEventListener('input', debounce(() => {
      this._filter.search = searchEl.value.toUpperCase();
      this._applyFilterAndSort();
    }, 150));
    sectorEl.addEventListener('change', () => {
      this._filter.sector = sectorEl.value;
      this._applyFilterAndSort();
      document.dispatchEvent(new CustomEvent('scanner:sector-filter', { detail: sectorEl.value }));
    });
    masterEl.addEventListener('change', () => {
      this._masterFilter = masterEl.value;
      this._forceScrollTop = true;
      this._applyFilterAndSort();
    });
    presetsEl.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-preset]');
      if (!btn) return;
      this._preset = btn.dataset.preset || '';
      presetsEl.querySelectorAll('[data-preset]').forEach(x => x.classList.toggle('active', x.dataset.preset === this._preset && this._preset !== ''));
      this._forceScrollTop = true;
      this._applyFilterAndSort();
    });
    warmBtn.addEventListener('click', () => this._warmMTFCache(warmBtn));

    this._el.addEventListener('click', (e) => {
      const summaryBtn = e.target.closest('[data-summary-filter]');
      if (summaryBtn && this._el.contains(summaryBtn)) {
        this._applySummaryFilter(summaryBtn.dataset.summaryFilter || '');
        return;
      }
      const logBtn = e.target.closest('[data-log-paper]');
      if (logBtn && this._el.contains(logBtn)) {
        e.preventDefault();
        this._logPaperTrade(logBtn);
        return;
      }
      const stageBtn = e.target.closest('[data-stage-row]');
      if (stageBtn && this._el.contains(stageBtn)) {
        e.preventDefault();
        this._stageFromRow(stageBtn.dataset.stageRow, stageBtn);
        return;
      }
      const tvBtn = e.target.closest('[data-open-tv]');
      if (tvBtn && this._el.contains(tvBtn)) {
        const symbol = tvBtn.dataset.openTv || this._selectedSymbol;
        if (symbol) window.open(tradingViewUrl(symbol), '_blank', 'noreferrer');
        return;
      }
      const leader = e.target.closest('.leader-card[data-symbol],.trigger-radar-card[data-symbol]');
      if (leader && this._el.contains(leader)) {
        const symbol = leader.dataset.symbol || '';
      if (symbol) {
        this._selectedSymbol = symbol;
        this._loadSelectedOption(symbol);
        document.dispatchEvent(new CustomEvent('chart:load', { detail: { symbol } }));
        if (this._vs) this._vs.refresh();
      }
        return;
      }
      const row = e.target.closest('.scanner-row[data-symbol]');
      if (!row || !this._el.contains(row)) return;
      this._selectSymbol(row.dataset.symbol || '');
    });

    // Keyboard navigation: ↑/↓ move the selection through the currently
    // sorted/filtered list, Enter opens the same detail a click would.
    // Scoped to the panel so typing in an unrelated input elsewhere on the
    // page (e.g. the chat) never gets hijacked.
    this._el.addEventListener('keydown', (e) => {
      if (e.target.matches('input, select, textarea')) return;
      if (!['ArrowUp', 'ArrowDown', 'Enter'].includes(e.key)) return;
      if (!this._sorted.length) return;
      e.preventDefault();
      const currentIdx = this._sorted.findIndex(
        (x) => String(x.symbol).toUpperCase() === String(this._selectedSymbol).toUpperCase()
      );
      if (e.key === 'Enter') {
        if (currentIdx >= 0) this._scrollSelectedIntoView(currentIdx);
        return;
      }
      const delta = e.key === 'ArrowDown' ? 1 : -1;
      const nextIdx = Math.max(0, Math.min(this._sorted.length - 1, (currentIdx < 0 ? 0 : currentIdx) + delta));
      this._selectSymbol(this._sorted[nextIdx].symbol);
      this._scrollSelectedIntoView(nextIdx);
    });
    this._el.tabIndex = 0; // make the panel keyboard-focusable
    document.addEventListener('chart:load', (e) => {
      const symbol = e.detail?.symbol;
      if (!symbol) return;
      this._selectedSymbol = String(symbol).toUpperCase();
      this._loadSelectedOption(this._selectedSymbol);
      this._updateMiniTradeCard();
      if (this._vs) this._vs.refresh();
    });

    // Sector filter from sector panel click
    document.addEventListener('sector:select', (e) => {
      const val = e.detail || '';
      sectorEl.value = val;
      this._filter.sector = val;
      this._applyFilterAndSort();
    });

    // Column toggle panel
    const toggleBtn = this._el.querySelector('#scannerColToggle');
    const panel = this._el.querySelector('#scannerColPanel');
    this._buildColTogglePanel(panel);
    toggleBtn.addEventListener('click', () => {
      panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    });

    // WS ticks
    ws.onTick((symbol, data) => this._onTick(symbol, data));

    // REST subscriptions
    this._unsubs.push(api.subscribe('/api/ticks', (resp) => {
      if (resp && resp.ticks) {
        const wasEmpty = this._data.size === 0;
        for (const t of resp.ticks) {
          const sym = t.symbol || t.sym;
          if (sym) this._mergeData(sym, t);
        }
        if (wasEmpty && this._data.size > 0) this._buildHeader();
        this._applyFilterAndSort();
      }
    }, 5000));

    this._unsubs.push(api.subscribe('/api/signals', (resp) => {
      this._signals.clear();
      if (resp && resp.signals) {
        for (const s of resp.signals) this._signals.set(s.symbol, s);
      }
      this._mergeSignalData();
      this._applyFilterAndSort();
    }, 2000));

    this._unsubs.push(api.subscribe('/api/prebreakout', (resp) => {
      this._prebreakout.clear();
      if (resp && resp.watchlist) {
        for (const w of resp.watchlist) this._prebreakout.set(w.symbol, w);
      }
      this._mergePrebreakoutData();
      this._applyFilterAndSort();
    }, 3000));

    this._unsubs.push(api.subscribe('/api/sectors', (resp) => {
      if (resp && resp.sectors) {
        const select = this._el.querySelector('#scannerSector');
        const current = select.value;
        const opts = ['<option value="">All Sectors</option>'];
        for (const s of resp.sectors) {
          const name = s.sector_id || '';
          if (name) opts.push(`<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`);
        }
        select.innerHTML = opts.join('');
        select.value = current;
        this._buildHeader();
      }
    }, 10000));

    this._unsubs.push(api.subscribe('/api/symbols', (resp) => {
      if (resp && Number(resp.count || 0) > 0) {
        this._foTotalCount = Number(resp.count || 0);
        this._applyFilterAndSort();
      }
    }, 30000));

    this._unsubs.push(api.subscribe('/api/risk/settings', (resp) => {
      const mode = resp?.settings?.trading_signal_mode || 'precision';
      this._setSignalMode(mode);
    }, 5000));

    const riskModeHandler = (event) => {
      this._setSignalMode(event?.detail?.trading_signal_mode || 'precision');
    };
    document.addEventListener('risk:settings-updated', riskModeHandler);
    this._unsubs.push(() => document.removeEventListener('risk:settings-updated', riskModeHandler));
  }

  // ── Column header ─────────────────────────────────────────────────────────
  _buildHeader() {
    const head = this._el.querySelector('#scannerHead');
    const filterHead = this._el.querySelector('#scannerFilterHead');
    const cols = visibleColumns(this._hidden);
    const sticky = stickyMeta(cols);
    head.innerHTML = cols
      .map(c => {
        const isSticky = sticky.has(c.key);
        const classes = ['sh', isSticky ? 'sticky-freeze' : '', c.key === 'symbol' ? 'sticky-symbol' : ''].filter(Boolean).join(' ');
        const stickyStyle = isSticky ? `left:${sticky.get(c.key)}px;` : '';
        return `<div class="${classes}" title="Click to sort by ${escapeHtml(c.label)}" style="${stickyStyle}text-align:${c.align};${cellSizeStyle(c, isSticky)}" data-col="${c.key}">${c.label} <span class="sort-arrow"></span></div>`;
      })
      .join('');
    if (filterHead) {
      filterHead.innerHTML = cols.map(c => this._renderHeaderFilter(c, sticky)).join('');
    }
    this._updateSortIndicators();
  }

  _renderHeaderFilter(c, sticky) {
    const isSticky = sticky.has(c.key);
    const stickyStyle = isSticky ? `left:${sticky.get(c.key)}px;` : '';
    const classes = ['sf', isSticky ? 'sticky-freeze' : '', c.key === 'symbol' ? 'sticky-symbol' : ''].filter(Boolean).join(' ');
    const value = this._colFilters[c.key] || '';
    const wrap = (html) => `<div class="${classes}" data-col="${c.key}" style="${stickyStyle}${cellSizeStyle(c, isSticky)}text-align:${c.align}">${html}</div>`;
    const options = (items) => items.map(([v, label]) => `<option value="${escapeHtml(v)}" ${String(value) === String(v) ? 'selected' : ''}>${escapeHtml(label)}</option>`).join('');
    if (c.key === 'symbol') return wrap(`<input data-filter-col="symbol" value="${escapeHtml(value)}" placeholder="Find" />`);
    if (c.key === 'change_pct') return wrap(`<select data-filter-col="change_pct">${options([['','All'],['positive','Green'],['negative','Red']])}</select>`);
    if (c.key === 'rel_vol') return wrap(`<select data-filter-col="rel_vol">${options([['','All'],['1.5','≥1.5x'],['2','≥2x'],['3','≥3x']])}</select>`);
    if (c.key === 'sector_id') {
      const sectors = [...new Set([...this._data.values()].map(x => x.sector_id).filter(Boolean))].sort();
      return wrap(`<select data-filter-col="sector_id"><option value="">All</option>${sectors.map(s => `<option value="${escapeHtml(s)}" ${value === s ? 'selected' : ''}>${escapeHtml(s.replace('_', ' '))}</option>`).join('')}</select>`);
    }
    if (c.key === 'smart_rank') return wrap(`<select data-filter-col="smart_rank">${options([['','All'],['60','≥60'],['80','≥80'],['100','≥100'],['115','≥115']])}</select>`);
    if (c.key === 'mode_signal') return wrap(`<select data-filter-col="mode_signal">${options([['','All'],['live','Live'],['paper_ready','Paper'],['watch_only','Watch'],['learn','Learn'],['no_chase','No Chase'],['avoid','Avoid']])}</select>`);
    if (c.key === 'gate_score') return wrap(`<select data-filter-col="gate_score">${options([['','All'],['go','GO'],['wait','WAIT'],['reject','REJECT'],['7','≥7 pass'],['6','≥6 pass']])}</select>`);
    if (c.key === 'intelligence_score') return wrap(`<select data-filter-col="intelligence_score">${options([['','All'],['50','≥50'],['60','≥60'],['70','≥70'],['80','≥80']])}</select>`);
    if (c.key === 'news_confirmation') return wrap(`<select data-filter-col="news_confirmation">${options([['','All'],['CONFIRMED','Confirmed'],['UNCONFIRMED','Unconfirmed'],['CONFLICTING','Conflict'],['EVENT_RISK','Event'],['NO_NEWS','No news']])}</select>`);
    if (c.key === 'trade_horizon') return wrap(`<select data-filter-col="trade_horizon">${options([['','All'],['INTRADAY','Intraday'],['BTST_1_2D','BTST'],['SWING','Swing'],['AVOID','Avoid']])}</select>`);
    if (c.key === 'chase_quality') return wrap(`<select data-filter-col="chase_quality">${options([['','All'],['HIGHLY_CHASEABLE','High'],['CLEAN','Clean'],['WAIT_RETEST','Retest'],['NO_CHASE','No Chase']])}</select>`);
    if (c.key === 'intraday_score') return wrap(`<select data-filter-col="intraday_score">${options([['','All'],['50','>=50'],['60','>=60'],['70','>=70'],['80','>=80']])}</select>`);
    if (c.key === 'swing_score') return wrap(`<select data-filter-col="swing_score">${options([['','All'],['50','>=50'],['60','>=60'],['70','>=70'],['80','>=80']])}</select>`);
    if (c.key === 'trend_bias') return wrap(`<select data-filter-col="trend_bias">${options([['','All'],['BUY','BUY'],['SELL','SELL'],['HOLD','HOLD']])}</select>`);
    if (c.key === 'mtf_source') return wrap(`<select data-filter-col="mtf_source">${options([['','All'],['limited','LIMIT'],['historical','HIST'],['proxy','PROXY'],['missing','MISS']])}</select>`);
    if (c.key === 'mtf_score') return wrap(`<select data-filter-col="mtf_score">${options([['','All'],['50','≥50'],['60','≥60'],['70','≥70'],['80','≥80']])}</select>`);
    if (c.key === 'setup_strength') return wrap(`<select data-filter-col="setup_strength">${options([['','All'],['50','≥50'],['60','≥60'],['70','≥70'],['80','≥80']])}</select>`);
    if (c.key === 'trade_decision') return wrap(`<select data-filter-col="trade_decision">${options([['','All'],['BUY CE','CE'],['BUY PE','PE'],['HOLD','HOLD'],['AVOID','AVOID']])}</select>`);
    if (c.key === 'direction_zone') return wrap(`<select data-filter-col="direction_zone">${options([['','All'],['CE_ACTIVE','CE active'],['PE_ACTIVE','PE active'],['WAIT_ZONE','Wait zone'],['CONFLICT','Conflict']])}</select>`);
    if (c.key === 'ce_score') return wrap(`<select data-filter-col="ce_score">${options([['','All'],['50','≥50'],['60','≥60'],['70','≥70'],['80','≥80']])}</select>`);
    if (c.key === 'pe_score') return wrap(`<select data-filter-col="pe_score">${options([['','All'],['50','≥50'],['60','≥60'],['70','≥70'],['80','≥80']])}</select>`);
    if (c.key === 'option_readiness') return wrap(`<select data-filter-col="option_readiness">${options([['','All'],['50','≥50'],['60','≥60'],['70','≥70'],['80','≥80']])}</select>`);
    if (c.key === 'chain_execution_status') return wrap(`<select data-filter-col="chain_execution_status">${options([['','All'],['TRADE_READY','Ready'],['WAIT_CONTRACT','Wait'],['CHAIN_PENDING','Pending'],['AVOID_CONTRACT','Avoid'],['PROXY','Proxy']])}</select>`);
    if (c.key === 'status') return wrap(`<select data-filter-col="status">${options([['','All'],['ARMED','ARMED'],['WATCH','WATCH'],['WAIT','WAIT'],['NO_TRADE','NO TRADE']])}</select>`);
    return wrap(`<span class="filter-empty">—</span>`);
  }

  _buildColTogglePanel(panel) {
    panel.innerHTML = COLUMNS.filter(c => c.toggle).map(c => `
      <label class="col-toggle-label">
        <input type="checkbox" data-col="${c.key}" ${this._hidden.has(c.key) ? '' : 'checked'} />
        ${c.label}
      </label>
    `).join('');
    panel.querySelectorAll('input[type=checkbox]').forEach(cb => {
      cb.addEventListener('change', () => {
        if (cb.checked) this._hidden.delete(cb.dataset.col);
        else this._hidden.add(cb.dataset.col);
        this._saveHidden();
        this._buildHeader();
        if (this._vs) this._vs.refresh();
      });
    });
  }

  _saveHidden() {
    try { localStorage.setItem('infusion:scanner:hidden:v2', JSON.stringify([...this._hidden])); } catch (_) {}
  }

  _setDensity(key) {
    if (!DENSITY[key] || key === this._density) return;
    this._el.classList.remove(`density-${this._density}`);
    this._density = key;
    this._el.classList.add(`density-${this._density}`);
    try { localStorage.setItem('infusion:scanner:density', key); } catch (_) {}
    this._el.querySelectorAll('#densityToggle [data-density]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.density === key);
    });
    if (this._vs) this._vs.setRowHeight(DENSITY[key].rowHeight);
    // Taller rows at the same row count can flip the viewport from "no
    // scrollbar" to "has one" (or vice versa) -- recheck the header gutter.
    requestAnimationFrame(() => this._syncHeaderScrollbarGutter());
  }

  // See the long comment where this is first wired up (init()). Re-run
  // whenever row count or row height could have changed the vertical
  // scrollbar's presence on #scannerViewport.
  _syncHeaderScrollbarGutter() {
    if (!this._headScrollEl || !this._viewportEl) return;
    const gutter = this._viewportEl.offsetWidth - this._viewportEl.clientWidth;
    this._headScrollEl.style.paddingRight = gutter > 0 ? `${gutter}px` : '0px';
  }

  // Single place that performs "select this symbol" -- reused by row
  // clicks and keyboard navigation so they can never drift apart.
  _selectSymbol(symbol) {
    if (!symbol) return;
    this._selectedSymbol = symbol;
    this._loadSelectedOption(symbol);
    this._updateMiniTradeCard();
    if (this._vs) this._vs.refresh();
  }

  _scrollSelectedIntoView(index) {
    if (!this._vs) return;
    const viewport = this._el.querySelector('#scannerViewport');
    if (!viewport) return;
    const rowHeight = DENSITY[this._density].rowHeight;
    const rowTop = index * rowHeight;
    const rowBottom = rowTop + rowHeight;
    if (rowTop < viewport.scrollTop) {
      viewport.scrollTop = rowTop;
    } else if (rowBottom > viewport.scrollTop + viewport.clientHeight) {
      viewport.scrollTop = rowBottom - viewport.clientHeight;
    }
  }

  async _warmMTFCache(button) {
    if (this._warming) return;
    this._warming = true;
    const previous = button.textContent;
    button.textContent = 'Warming...';
    button.disabled = true;
    try {
      const limit = Math.max(this._data.size || 208, 50);
      const resp = await api.fetch(`/api/mtf/refresh?limit=${encodeURIComponent(limit)}`);
      button.textContent = resp?.refreshed ? `MTF ${resp.refreshed} OK` : 'MTF done';
      const ticks = await api.fetch('/api/ticks');
      if (ticks?.ticks) {
        for (const t of ticks.ticks) {
          const sym = t.symbol || t.sym;
          if (sym) this._mergeData(sym, t);
        }
        this._applyFilterAndSort();
      }
      setTimeout(() => { button.textContent = previous; }, 2500);
    } catch (_) {
      button.textContent = 'MTF failed';
      setTimeout(() => { button.textContent = previous; }, 2500);
    } finally {
      this._warming = false;
      button.disabled = false;
    }
  }

  _setSignalMode(mode) {
    const requested = String(mode || 'precision');
    const next = SIGNAL_MODE_PROFILES[requested] ? requested : 'precision';
    if (next === this._signalMode) return;
    this._signalMode = next;
    this._signalModeProfile = signalModeProfile(next);
    this._forceScrollTop = true;
    this._applyFilterAndSort();
  }

  // ── Data merging ──────────────────────────────────────────────────────────
  _mergeData(symbol, data) {
    const existing = this._data.get(symbol) || { symbol };
    Object.assign(existing, data);
    existing.symbol = symbol;
    const previousLock = this._biasLocks.get(symbol);
    const zone = deriveDirectionZone(existing, previousLock);
    existing.direction_bias = zone.bias;
    existing.direction_state = zone.state;
    existing.direction_tone = zone.tone;
    existing.direction_reason = zone.reason;
    existing.direction_switch_note = zone.switchNote;
    existing.ce_active_above = zone.ceAbove;
    existing.pe_active_below = zone.peBelow;
    existing.ce_score = zone.ceScore;
    existing.pe_score = zone.peScore;
    existing.direction_wait_zone = zone.waitText;
    if (zone.bias !== 'WAIT') {
      const keepSince = previousLock?.bias === zone.bias ? previousLock.since : zone.updatedAt;
      this._biasLocks.set(symbol, { bias: zone.bias, since: keepSince, state: zone.state });
    }
    existing.smart_rank = smartRank(existing);
    this._data.set(symbol, existing);
  }

  _mergeSignalData() {
    for (const [sym, sig] of this._signals) {
      const d = this._data.get(sym);
      if (d) {
        d.score        = Number(sig.conviction_score || sig.active_score || 0);
        d.grade        = sig.conviction_grade || '';
        d.signal       = sig.signal_type || 'bullish';   // "bullish"|"bearish"
        d.signal_active = true;
        d.entry_price  = Number(sig.entry_price || 0);
        d.stop_price   = Number(sig.invalidation_price || 0);
        d.target_price = Number(sig.target_price || 0);
        d.rr           = Number(sig.risk_reward_ratio || 0);
        d.market_regime = sig.market_regime || '';
        d.sig_sector   = sig.sector_id || d.sector_id || '';
        d.smart_rank   = smartRank(d);
      }
    }
    // Clear signal for symbols no longer active
    for (const [sym, d] of this._data) {
      if (!this._signals.has(sym)) {
        d.signal_active = false;
        d.signal = '';
        d.smart_rank = smartRank(d);
      }
    }
  }

  _mergePrebreakoutData() {
    for (const [sym, pb] of this._prebreakout) {
      const d = this._data.get(sym);
      if (d) {
        d.readiness = pb.readiness_score || 0;
        d.state     = pb.state || '';
        d.rel_vol   = d.rel_vol || pb.rel_vol || 0;
        // Only set score from PB if no active signal score
        if (!d.signal_active && pb.conviction_score) {
          d.score = Number(pb.conviction_score);
          d.grade = pb.conviction_grade || '';
        }
        d.smart_rank = smartRank(d);
      }
    }
  }

  // ── WS tick handler ───────────────────────────────────────────────────────
  _onTick(symbol, data) {
    const prev = this._data.get(symbol);
    const prevLtp = prev ? prev.ltp : null;
    this._mergeData(symbol, data);
    const item = this._data.get(symbol);
    if (prevLtp != null && item.ltp != null && prevLtp !== item.ltp) {
      item._flash = item.ltp > prevLtp ? 'up' : 'down';
      const existing = this._flashTimers.get(symbol);
      if (existing) clearTimeout(existing);
      this._flashTimers.set(symbol, setTimeout(() => {
        const d = this._data.get(symbol);
        if (d) d._flash = null;
      }, 450));
    }
    if (this._vs) this._vs.updateItem(symbol, item);
    this._scheduleLiveResort();
  }

  _scheduleLiveResort() {
    if (this._sort.key !== 'smart_rank' || this._sort.dir !== 'desc') return;
    if (this._resortTimer) return;
    this._resortTimer = setTimeout(() => {
      this._resortTimer = null;
      this._applyFilterAndSort();
    }, 900);
  }

  // ── Sort & Filter ─────────────────────────────────────────────────────────
  _handleSort(key) {
    if (this._sort.key === key) {
      this._sort.dir = this._sort.dir === 'desc' ? 'asc' : 'desc';
    } else {
      this._sort.key = key;
      this._sort.dir = key === 'symbol' || key === 'sector_id' ? 'asc' : 'desc';
    }
    this._forceScrollTop = true;
    this._updateSortIndicators();
    this._applyFilterAndSort();
  }

  _updateSortIndicators() {
    this._el.querySelectorAll('.sh[data-col]').forEach(el => {
      const arrow = el.querySelector('.sort-arrow');
      if (!arrow) return;
      if (el.dataset.col === this._sort.key) {
        el.classList.add('sorted');
        arrow.textContent = this._sort.dir === 'asc' ? '▲' : '▼';
      } else {
        el.classList.remove('sorted');
        arrow.textContent = '';
      }
    });
  }

  _applyFilterAndSort() {
    let items = Array.from(this._data.values());
    if (this._filter.search) items = items.filter(d => (d.symbol || '').includes(this._filter.search));
    if (this._filter.sector) items = items.filter(d => (d.sector_id || '') === this._filter.sector);
    items = this._applyColumnFilters(items);
    items = this._applyActionFilters(items);

    const { key, dir } = this._sort;
    items.forEach(item => {
      item.smart_rank = smartRank(item);
      item.mode_rank = modeAwareRank(item, this._signalMode);
      item.mode_signal_key = modeSignal(item, this._signalMode).key;
    });

    // Priority sort (default: score desc) — trading workflow order
    if (key === 'smart_rank' && dir === 'desc') {
      const STATE_PRIORITY = { triggered: 5, coiled: 4, accumulating: 3, compressing: 2, expired: 0, idle: 0 };
      items.sort((a, b) => {
        // 0. Selected dashboard mode decides what deserves top visibility.
        const am = modeSignal(a, this._signalMode);
        const bm = modeSignal(b, this._signalMode);
        if (am.priority !== bm.priority) return bm.priority - am.priority;

        // 1. Actionable BUY/SELL ideas first, HOLD next, AVOID last.
        const DECISION_PRIORITY = { 'BUY CE': 4, 'BUY PE': 4, BUY: 4, SELL: 4, HOLD: 2, WAIT: 2, AVOID: 0 };
        const ad = DECISION_PRIORITY[String(a.direction_bias || a.trade_decision || '').toUpperCase()] || 0;
        const bd = DECISION_PRIORITY[String(b.direction_bias || b.trade_decision || '').toUpperCase()] || 0;
        if (ad !== bd) return bd - ad;

        // 2. By state priority
        const aPrio = STATE_PRIORITY[a.state || 'idle'] || 0;
        const bPrio = STATE_PRIORITY[b.state || 'idle'] || 0;
        if (aPrio !== bPrio) return bPrio - aPrio;

        // 3. By smart rank: strength + conviction + MTF + movement, minus blockers.
        return modeAwareRank(b, this._signalMode) - modeAwareRank(a, this._signalMode);
      });
    } else {
      // Manual sort by clicked column
      items.sort((a, b) => {
        let va = a[key] ?? '', vb = b[key] ?? '';
        const aBlank = va === '' || va == null;
        const bBlank = vb === '' || vb == null;
        if (aBlank && !bBlank) return 1;
        if (!aBlank && bBlank) return -1;
        const na = Number(va);
        const nb = Number(vb);
        const numeric = Number.isFinite(na) && Number.isFinite(nb);
        if (!numeric) {
          const cmp = String(va).localeCompare(String(vb));
          return dir === 'asc' ? cmp : -cmp;
        }
        return dir === 'asc' ? na - nb : nb - na;
      });
    }

    // Default view: only the top-ranked 30 (already sorted above). Searching
    // a symbol or picking a specific sector means the user wants to see
    // everything matching that, not just the top of the whole universe.
    const fullMatchCount = items.length;
    const isNarrowed = Boolean(this._filter.search || this._filter.sector);
    if (!isNarrowed && items.length > 30) {
      items = items.slice(0, 30);
    }

    this._sorted = items;
    if (!this._selectedSymbol && items.length) this._selectedSymbol = items[0].symbol || '';
    const countEl = this._el.querySelector('#scannerCount');
    if (countEl) {
      countEl.textContent = isNarrowed
        ? `${items.length}/${this._foTotalCount || fullMatchCount} F&O`
        : `Top ${items.length} of ${this._foTotalCount || fullMatchCount} F&O`;
    }
    if (this._vs) this._vs.setData(items);
    // Row count just changed -- the viewport's scrollbar may have appeared
    // or disappeared. Let VirtualScroll's DOM update land first.
    requestAnimationFrame(() => this._syncHeaderScrollbarGutter());
    if (this._forceScrollTop) {
      const viewport = this._el.querySelector('#scannerViewport');
      if (viewport) viewport.scrollTop = 0;
      this._forceScrollTop = false;
    }
    document.dispatchEvent(new CustomEvent('scanner:count', { detail: items.length }));
    this._updateMiniTradeCard();
    this._renderCommandSummary(Array.from(this._data.values()));
    this._renderLeadershipQueue(items);
  }

  _applySummaryFilter(filter) {
    const masterEl = this._el.querySelector('#scannerMasterFilter');
    const presetsEl = this._el.querySelector('#scannerPresets');
    this._masterFilter = '';
    this._preset = '';
    if (filter === 'ce_ready') this._masterFilter = 'ce_ready';
    else if (filter === 'pe_ready') this._masterFilter = 'pe_ready';
    else if (filter === 'paper_ready') this._masterFilter = 'paper_ready';
    else if (filter === 'watch_only') this._masterFilter = 'watch_only';
    else if (filter === 'no_chase') this._masterFilter = 'no_chase';
    else if (filter === 'go_review') this._masterFilter = 'go_review';
    else if (filter === 'wait_confirm') this._masterFilter = 'wait_confirm';
    else if (filter === 'reject_gate') this._masterFilter = 'reject_gate';
    else if (filter === 'swing_ready') this._masterFilter = 'swing_ready';
    else if (filter === 'intraday_ready') this._masterFilter = 'intraday_ready';
    else if (filter === 'avoid') this._masterFilter = 'avoid';
    else if (filter === 'near_bull' || filter === 'near_bear' || filter === 'high_rank' || filter === 'highly_chaseable') this._preset = filter;
    if (masterEl) masterEl.value = this._masterFilter;
    if (presetsEl) {
      presetsEl.querySelectorAll('[data-preset]').forEach(x => {
        x.classList.toggle('active', x.dataset.preset === this._preset && this._preset !== '');
      });
    }
    this._forceScrollTop = true;
    this._applyFilterAndSort();
  }

  _renderCommandSummary(allItems) {
    const el = this._el.querySelector('#scannerCommandSummary');
    if (!el) return;
    const items = allItems || [];
    const counts = {
      high_rank: items.filter(x => smartRank(x) >= 100).length,
      paper_ready: items.filter(x => ['live', 'paper_ready'].includes(modeSignal(x, this._signalMode).key)).length,
      watch_only: items.filter(x => ['watch_only', 'learn'].includes(modeSignal(x, this._signalMode).key)).length,
      no_chase: items.filter(x => modeSignal(x, this._signalMode).key === 'no_chase').length,
      go_review: items.filter(x => checklistSummary(x, this._signalMode).verdict === 'GO').length,
      wait_confirm: items.filter(x => checklistSummary(x, this._signalMode).verdict === 'WAIT').length,
      reject_gate: items.filter(x => checklistSummary(x, this._signalMode).verdict === 'REJECT').length,
      ce_ready: items.filter(x => isCERow(x) && Number(x.option_readiness || 0) >= 55).length,
      pe_ready: items.filter(x => isPERow(x) && Number(x.option_readiness || 0) >= 55).length,
      swing_ready: items.filter(x => Number(x.swing_score || 0) >= 72 && String(x.trade_horizon || '').toUpperCase().includes('SWING')).length,
      intraday_ready: items.filter(x => Number(x.intraday_score || 0) >= 68 && String(x.trade_horizon || '').toUpperCase().includes('INTRADAY')).length,
      highly_chaseable: items.filter(x => String(x.chase_quality || '').toUpperCase().includes('HIGHLY') || String(x.chase_quality || '').toUpperCase() === 'CLEAN').length,
      near_bull: items.filter(x => {
        const d = triggerDistance(x, 'bull');
        return d && (d.crossed || d.absDistance <= 1.2);
      }).length,
      near_bear: items.filter(x => {
        const d = triggerDistance(x, 'bear');
        return d && (d.crossed || d.absDistance <= 1.2);
      }).length,
      avoid: items.filter(isAvoidRow).length,
    };
    const chip = (key, label, tone, hint) => `
      <button type="button" class="summary-chip ${tone} ${this._summaryFilterActive(key) ? 'active' : ''}" data-summary-filter="${key}" title="${escapeHtml(hint)}">
        <span>${escapeHtml(label)}</span>
        <b>${counts[key] || 0}</b>
      </button>
    `;
    el.innerHTML = `
      <div class="summary-caption">Command Summary</div>
      <div class="summary-total" title="Visible rows from scanner vs total F&O universe">F&O ${items.length}/${this._foTotalCount || items.length}</div>
      <div class="summary-total mode" title="Risk Console signal visibility mode">${escapeHtml(this._signalModeProfile.label)}</div>
      ${chip('paper_ready', 'Paper Ready', 'paper', 'Selected-mode paper review candidates; live Telegram remains strict')}
      ${chip('watch_only', 'Watch Only', 'watch', 'Selected-mode opportunity watchlist candidates')}
      ${chip('no_chase', 'No Chase', 'avoid', 'Rejected because location/risk is poor')}
      ${chip('go_review', 'GO Review', 'go', 'Checklist has 7+ pass and no hard fail')}
      ${chip('wait_confirm', 'Wait Confirm', 'wait', 'Checklist needs confirmation but has no hard fail')}
      ${chip('reject_gate', 'Reject', 'reject', 'Checklist contains at least one hard fail')}
      <div class="scanner-decision-legend"><span class="go">GO = review only</span><span class="wait">WAIT = need trigger close</span><span class="reject">REJECT = do not chase</span></div>
      ${chip('high_rank', 'High Rank', 'rank', 'Smart rank >= 100')}
      ${chip('ce_ready', 'CE Ready', 'ce', 'BUY CE candidates with option conviction')}
      ${chip('pe_ready', 'PE Ready', 'pe', 'BUY PE candidates with option conviction')}
      ${chip('swing_ready', 'Swing', 'swing', 'Higher timeframe swing candidates')}
      ${chip('intraday_ready', 'Intraday', 'intraday', 'Fast same-day candidates')}
      ${chip('highly_chaseable', 'Chaseable', 'chase', 'Clean or highly chaseable setups')}
      ${chip('near_bull', 'Near Bull', 'bull', 'Within 1.2% of bull-above trigger or already crossed')}
      ${chip('near_bear', 'Near Bear', 'bear', 'Within 1.2% of bear-below trigger or already crossed')}
      ${chip('avoid', 'Avoid', 'avoid', 'Avoid / no-trade rows')}
      <button type="button" class="summary-chip clear ${!this._masterFilter && !this._preset ? 'active' : ''}" data-summary-filter="" title="Clear command filter">
        <span>All</span>
        <b>${items.length}</b>
      </button>
    `;
  }

  _summaryFilterActive(key) {
    if (!key) return !this._masterFilter && !this._preset;
    if (['ce_ready', 'pe_ready', 'paper_ready', 'watch_only', 'no_chase', 'go_review', 'wait_confirm', 'reject_gate', 'swing_ready', 'intraday_ready', 'avoid'].includes(key)) return this._masterFilter === key;
    return this._preset === key;
  }

  async _loadSelectedOption(symbol) {
    const normalized = String(symbol || '').toUpperCase();
    if (!normalized) return;
    const token = ++this._selectedOptionToken;
    this._selectedOption = null;
    this._updateMiniTradeCard();
    try {
      const resp = await api.fetch(`/api/options/summary?symbol=${encodeURIComponent(normalized)}`);
      if (token !== this._selectedOptionToken) return;
      this._selectedOption = resp || null;
      this._updateMiniTradeCard();
    } catch (_) {
      if (token !== this._selectedOptionToken) return;
      this._selectedOption = { option_chain_ready: false, reason: 'Option summary fetch failed.' };
      this._updateMiniTradeCard();
    }
  }

  _renderOptionQuickPick(item) {
    const option = this._selectedOption || {};
    const decision = String(item.trade_decision || '').toUpperCase();
    const fallbackBias = decision.includes('PE') || decision.includes('SELL') ? 'PE' : decision.includes('CE') || decision.includes('BUY') ? 'CE' : 'CE/PE';
    const bias = option.bias || fallbackBias;
    const ready = Boolean(option.option_chain_ready);
    const up = option.upstox_option || {};
    const metrics = up.metrics || {};
    const suggested = option.suggested_contract || `${item.symbol || '-'} ATM ${bias} · waiting for Upstox chain`;
    const score = ready ? Math.round(Number(option.execution_score || option.option_score || option.final_score || 0)) : Math.round(Number(item.option_readiness || 0));
    const rawScore = ready ? Math.round(Number(option.raw_option_score || metrics.raw_option_score || score || 0)) : 0;
    const capNote = option.score_cap_detail || metrics.score_cap_detail || '';
    const strike = up.strike || metrics.strike || 'ATM';
    const expiry = up.expiry || 'Monthly/nearest liquid';
    const premium = metrics.ltp ?? metrics.premium ?? '-';
    const spread = metrics.spread_pct ?? '-';
    const oi = metrics.oi != null ? Math.round(Number(metrics.oi || 0)) : '-';
    const iv = metrics.iv ?? '-';
    const reason = ready
      ? (option.reason || 'Upstox option chain confirms selected contract gates.')
      : (option.reason || 'Option chain pending. Use this only as underlying direction, not contract confirmation.');
    return `
      <div class="trade-plan-option ${ready ? 'ready' : 'pending'}">
        <label>Option Contract Quick Pick</label>
        <div class="option-pick-head">
          <span class="${String(bias).toLowerCase()}">${escapeHtml(bias)}</span>
          <b>${score || '-'}</b>
          <small>${ready ? `${escapeHtml(option.execution_status || 'CHAIN LIVE')}${rawScore && rawScore !== score ? ` · Raw ${rawScore}` : ''}` : 'CHAIN PENDING'}</small>
        </div>
        <div class="option-pick-contract" title="${escapeHtml(suggested)}">${escapeHtml(suggested)}</div>
        <div class="option-pick-grid">
          <span><em>Strike</em><b>${escapeHtml(String(strike))}</b></span>
          <span><em>Expiry</em><b>${escapeHtml(String(expiry))}</b></span>
          <span><em>Premium</em><b>${escapeHtml(String(premium))}</b></span>
          <span><em>Spread</em><b>${escapeHtml(String(spread))}${spread !== '-' ? '%' : ''}</b></span>
          <span><em>OI</em><b>${escapeHtml(String(oi))}</b></span>
          <span><em>IV</em><b>${escapeHtml(String(iv))}</b></span>
        </div>
        <small>${escapeHtml(capNote || reason)}</small>
      </div>
    `;
  }

  _renderExecutionGates(item) {
    const option = this._selectedOption || {};
    const direction = decisionClass(item.trade_decision || item.trend_bias || '');
    const trendBias = String(item.trend_bias || '').toUpperCase();
    const mtfScore = Number(item.mtf_score || item.pine_confidence || 0);
    const vwap = String(item.vwap_state || '').toUpperCase();
    const rvol = Number(item.rel_vol || 0);
    const optionReady = Boolean(option.option_chain_ready);
    const blockers = Array.isArray(item.rejection_reasons) ? item.rejection_reasons.length : 0;
    const risk = currentRiskBudget();
    const entry = Number(item.entry_price_hint || 0);
    const sl = Number(item.stop_loss_hint || 0);
    const hasRiskLine = Boolean(entry && sl && Math.abs(entry - sl) > 0);

    const trendPass = direction === 'buy'
      ? trendBias.includes('BUY')
      : direction === 'sell' ? trendBias.includes('SELL') : false;
    const vwapPass = direction === 'buy'
      ? vwap === 'ABOVE'
      : direction === 'sell' ? vwap === 'BELOW' : false;

    const gates = [
      { label: 'Trend', state: trendPass ? 'pass' : 'warn', text: trendBias || 'WAIT' },
      { label: 'MTF', state: mtfScore >= 70 ? 'pass' : mtfScore >= 55 ? 'warn' : 'block', text: mtfScore ? Math.round(mtfScore) : 'BUILD' },
      { label: 'VWAP', state: vwapPass ? 'pass' : 'warn', text: vwap || 'WAIT' },
      { label: 'Volume', state: rvol >= 1.5 ? 'pass' : rvol >= 1 ? 'warn' : 'block', text: rvol ? `${rvol.toFixed(1)}x` : '-' },
      { label: 'Chain', state: optionReady ? 'pass' : 'warn', text: optionReady ? 'LIVE' : 'PENDING' },
      { label: 'Chase', state: item.anti_chase_ok === false || blockers ? 'block' : 'pass', text: item.anti_chase_ok === false ? 'NO' : blockers ? `${blockers} BLK` : 'OK' },
      { label: 'Risk', state: hasRiskLine && risk.riskAmount > 0 ? 'pass' : 'warn', text: hasRiskLine ? formatPrice(risk.riskAmount) : 'SET' },
    ];
    const passCount = gates.filter(g => g.state === 'pass').length;
    const blockCount = gates.filter(g => g.state === 'block').length;
    const verdict = blockCount ? 'WAIT' : passCount >= 5 ? 'READY' : 'WATCH';
    return `
      <div class="trade-plan-gates ${verdict.toLowerCase()}">
        <label>Execution Gate Checklist</label>
        <div class="gate-verdict"><b>${verdict}</b><span>${passCount}/${gates.length} pass</span></div>
        <div class="gate-grid">
          ${gates.map(g => `
            <span class="${g.state}" title="${escapeHtml(g.label)}: ${escapeHtml(String(g.text))}">
              <em>${escapeHtml(g.label)}</em>
              <b>${escapeHtml(String(g.text))}</b>
            </span>
          `).join('')}
        </div>
      </div>
    `;
  }

  _tradeDecisionBanner(item) {
    const rawDecision = String(item.trade_decision || 'WAIT').toUpperCase();
    const decision = String(item.direction_bias || rawDecision || 'WAIT').toUpperCase();
    const option = this._selectedOption || {};
    const chainReady = Boolean(option.option_chain_ready);
    const rank = modeAwareRank(item, this._signalMode);
    const rawRank = smartRank(item);
    const modeSig = modeSignal(item, this._signalMode);
    const modeProfile = this._signalModeProfile || signalModeProfile(this._signalMode);
    const setup = Number(item.setup_strength || item.readiness || 0);
    const opt = Number(item.option_readiness || 0);
    const blockers = Array.isArray(item.rejection_reasons) ? item.rejection_reasons : [];
    const directionClass = decisionClass(decision);
    const triggerLabel = directionClass === 'sell' ? 'PE below' : directionClass === 'buy' ? 'CE above' : 'Wait zone';
    const trigger = directionClass === 'sell' ? item.pe_active_below : directionClass === 'buy' ? item.ce_active_above : item.ce_active_above;
    const cmd = item.command_center && typeof item.command_center === 'object' ? item.command_center : {};

    let verdict = 'WATCH';
    let next = cmd.action_text || `${triggerLabel} ${formatPrice(trigger)} must confirm before entry.`;
    let tone = 'watch';
    if (blockers.length || item.anti_chase_ok === false) {
      verdict = 'WAIT';
      tone = 'wait';
      next = cmd.chase_text || blockers[0] || 'Anti-chase rejected. Wait for cleaner pullback/retest.';
    } else if (!decision.includes('BUY')) {
      verdict = 'WAIT';
      tone = 'wait';
      next = cmd.headline || item.direction_reason || 'No clean CE/PE direction yet. Let scanner build conviction.';
    } else if (!chainReady) {
      verdict = 'WATCH';
      tone = 'watch';
      next = cmd.option_state ? `${cmd.headline || decision}: option chain ${cmd.option_state}. Confirm contract before trade.` : `${decision} bias exists, but option chain is pending. Confirm contract before trade.`;
    } else if (rank >= 100 && setup >= 65 && opt >= 65) {
      verdict = 'READY';
      tone = directionClass === 'sell' ? 'pe' : 'ce';
      next = cmd.action_text || `${decision} candidate. Confirm chart, spread, and risk before manual execution.`;
    }

    return `
      <div class="trade-plan-decision ${tone}">
        <div>
          <span>Decision</span>
          <b>${verdict}</b>
        </div>
        <p>${escapeHtml(next)}</p>
        <em>${escapeHtml(decision)} · Raw ${escapeHtml(rawDecision)} · CE ${Math.round(Number(item.ce_score || 0))} / PE ${Math.round(Number(item.pe_score || 0))} · Rank ${rank} · Strength ${Math.round(setup)} · Conviction ${Math.round(opt)}</em>
      </div>
    `;
  }

  _renderLeadershipQueue(items) {
    const el = this._el.querySelector('#scannerLeaderStrip');
    if (!el) return;
    const leaders = items
      .filter(x => smartRank(x) >= 45)
      .slice(0, 7);
    if (!leaders.length) {
      el.innerHTML = `
        <div class="leader-title">
          <b>Leadership Queue</b>
          <span>Waiting for strength + conviction</span>
        </div>
        <div class="leader-empty">No ranked candidate yet</div>
      `;
      return;
    }
    el.innerHTML = `
      <div class="leader-title">
        <b>Leadership Queue</b>
        <span>Auto-top by Rank · click to focus chart</span>
      </div>
      <div class="leader-cards">
        ${leaders.map((item, idx) => this._renderLeaderCard(item, idx)).join('')}
      </div>
      ${this._renderTriggerRadar(items)}
    `;
  }

  _renderTriggerRadar(items) {
    const candidates = triggerCandidates(items);
    const body = candidates.length
      ? candidates.map(x => this._renderTriggerCard(x)).join('')
      : '<div class="trigger-radar-empty">No stock within 1.2% of bull/bear trigger</div>';
    return `
      <div class="trigger-radar-title">
        <b>Trigger Radar</b>
        <span>Near/crossed bull above & bear below</span>
      </div>
      <div class="trigger-radar-cards">${body}</div>
    `;
  }

  _renderTriggerCard(row) {
    const item = row.item;
    const symbol = String(item.symbol || '').toUpperCase();
    const sideText = row.side === 'bear' ? 'BEAR' : 'BULL';
    const sideClass = row.side === 'bear' ? 'bear' : 'bull';
    const distanceText = row.crossed
      ? 'CROSSED'
      : `${row.distance >= 0 ? '+' : ''}${row.distance.toFixed(2)}%`;
    const title = `${symbol} ${sideText} trigger ${formatPrice(row.level)} · LTP ${formatPrice(item.ltp)} · ${smartRankReasons(item)}`;
    return `
      <button type="button" class="trigger-radar-card ${sideClass} ${row.crossed ? 'crossed' : ''}" data-symbol="${escapeHtml(symbol)}" title="${escapeHtml(title)}">
        <b>${escapeHtml(symbol)}</b>
        <span>${sideText} ${escapeHtml(distanceText)}</span>
        <small>${formatPrice(row.level)} ? Rank ${modeAwareRank(item, this._signalMode)}</small>
      </button>
    `;
  }

  _renderLeaderCard(item, idx) {
    const symbol = String(item.symbol || '').toUpperCase();
    const rank = modeAwareRank(item, this._signalMode);
    const decision = item.direction_bias || item.trade_decision || 'HOLD';
    const cls = decisionClass(decision);
    const selected = this._selectedSymbol && this._selectedSymbol === symbol;
    const reason = smartRankReasons(item);
    return `
      <button type="button" class="leader-card ${cls} ${selected ? 'active' : ''}" data-symbol="${escapeHtml(symbol)}" title="${escapeHtml(reason)}">
        <span class="leader-pos">#${idx + 1}</span>
        <b>${escapeHtml(symbol)}</b>
        <small class="${Number(item.change_pct || 0) >= 0 ? 'positive' : 'negative'}">${formatPct(item.change_pct)} · ${formatRelVol(item.rel_vol)}</small>
        <em>${escapeHtml(decision)}</em>
        ${modeSignalChip(item, this._signalMode)}
        <strong>${rank}</strong>
      </button>
    `;
  }

  _applyColumnFilters(items) {
    const f = this._colFilters || {};
    return items.filter(d => {
      if (f.symbol && !String(d.symbol || '').toUpperCase().includes(String(f.symbol).toUpperCase())) return false;
      if (f.change_pct === 'positive' && Number(d.change_pct || 0) < 0) return false;
      if (f.change_pct === 'negative' && Number(d.change_pct || 0) >= 0) return false;
      if (f.rel_vol && Number(d.rel_vol || 0) < Number(f.rel_vol)) return false;
      if (f.sector_id && String(d.sector_id || '') !== f.sector_id) return false;
      if (f.smart_rank && smartRank(d) < Number(f.smart_rank)) return false;
      if (f.mode_signal && modeSignal(d, this._signalMode).key !== f.mode_signal) return false;
      if (f.gate_score) {
        const gates = checklistSummary(d, this._signalMode);
        if (['go', 'wait', 'reject'].includes(String(f.gate_score)) && gates.verdict.toLowerCase() !== String(f.gate_score)) return false;
        if (!['go', 'wait', 'reject'].includes(String(f.gate_score)) && gates.pass < Number(f.gate_score)) return false;
      }
      if (f.intelligence_score && Number(d.intelligence_score || 0) < Number(f.intelligence_score)) return false;
      if (f.news_confirmation && String(d.news_confirmation?.state || 'NO_NEWS').toUpperCase() !== f.news_confirmation) return false;
      if (f.trade_horizon && String(d.trade_horizon || '').toUpperCase() !== f.trade_horizon) return false;
      if (f.chase_quality && String(d.chase_quality || '').toUpperCase() !== f.chase_quality) return false;
      if (f.intraday_score && Number(d.intraday_score || 0) < Number(f.intraday_score)) return false;
      if (f.swing_score && Number(d.swing_score || 0) < Number(f.swing_score)) return false;
      if (f.trend_bias && String(d.trend_bias || '').toUpperCase() !== f.trend_bias) return false;
      if (f.mtf_source && !String(d.mtf_source || 'proxy').toLowerCase().includes(String(f.mtf_source).toLowerCase())) return false;
      if (f.mtf_score && Number(d.mtf_score || 0) < Number(f.mtf_score)) return false;
      if (f.setup_strength && Number(d.setup_strength || 0) < Number(f.setup_strength)) return false;
      if (f.trade_decision && String(d.direction_bias || d.trade_decision || '').toUpperCase() !== f.trade_decision) return false;
      if (f.direction_zone && !String(d.direction_state || '').toUpperCase().includes(f.direction_zone)) return false;
      if (f.ce_score && Number(d.ce_score || 0) < Number(f.ce_score)) return false;
      if (f.pe_score && Number(d.pe_score || 0) < Number(f.pe_score)) return false;
      if (f.option_readiness && Number(d.option_readiness || 0) < Number(f.option_readiness)) return false;
      if (f.chain_execution_status) {
        const chainStatus = String(d.chain_execution_status || (d.option_chain_ready ? 'WAIT_CONTRACT' : 'PROXY')).toUpperCase();
        if (chainStatus !== f.chain_execution_status) return false;
      }
      if (f.status && !String(d.status || '').toUpperCase().includes(f.status)) return false;
      return true;
    });
  }

  _applyActionFilters(items) {
    let result = items;
    if (this._masterFilter === 'ce_ready') result = result.filter(isCERow);
    if (this._masterFilter === 'pe_ready') result = result.filter(isPERow);
    if (this._masterFilter === 'paper_ready') result = result.filter(x => modeSignal(x, this._signalMode).key === 'paper_ready' || modeSignal(x, this._signalMode).key === 'live');
    if (this._masterFilter === 'watch_only') result = result.filter(x => modeSignal(x, this._signalMode).key === 'watch_only' || modeSignal(x, this._signalMode).key === 'learn');
    if (this._masterFilter === 'no_chase') result = result.filter(x => modeSignal(x, this._signalMode).key === 'no_chase');
    if (this._masterFilter === 'go_review') result = result.filter(x => checklistSummary(x, this._signalMode).verdict === 'GO');
    if (this._masterFilter === 'wait_confirm') result = result.filter(x => checklistSummary(x, this._signalMode).verdict === 'WAIT');
    if (this._masterFilter === 'reject_gate') result = result.filter(x => checklistSummary(x, this._signalMode).verdict === 'REJECT');
    if (this._masterFilter === 'swing_ready') result = result.filter(x => Number(x.swing_score || 0) >= 72 && String(x.trade_horizon || '').toUpperCase().includes('SWING'));
    if (this._masterFilter === 'intraday_ready') result = result.filter(x => Number(x.intraday_score || 0) >= 68 && String(x.trade_horizon || '').toUpperCase().includes('INTRADAY'));
    if (this._masterFilter === 'wait') result = result.filter(isWaitRow);
    if (this._masterFilter === 'avoid') result = result.filter(isAvoidRow);

    const preset = this._preset;
    if (preset === 'strong_ce') {
      result = result.filter(x => isCERow(x) && Number(x.option_readiness || 0) >= 55 && Number(x.mtf_score || 0) >= 60);
    } else if (preset === 'strong_pe') {
      result = result.filter(x => isPERow(x) && Number(x.option_readiness || 0) >= 55 && Number(x.mtf_score || 0) >= 60);
    } else if (preset === 'high_volume') {
      result = result.filter(x => Number(x.rel_vol || 0) >= 2);
    } else if (preset === 'breakout_watch') {
      result = result.filter(x => Number(x.setup_strength || 0) >= 55 && ['ARMED', 'WATCH', 'WAIT_RETEST'].includes(String(x.status || '').toUpperCase()));
    } else if (preset === 'swing_watch') {
      result = result.filter(x => Number(x.swing_score || 0) >= 62 && ['SWING', 'BTST_1_2D'].includes(String(x.trade_horizon || '').toUpperCase()));
    } else if (preset === 'highly_chaseable') {
      result = result.filter(x => String(x.chase_quality || '').toUpperCase().includes('HIGHLY') || String(x.chase_quality || '').toUpperCase() === 'CLEAN');
    } else if (preset === 'reversal_watch') {
      result = result.filter(x => {
        const pattern = String(x.candle_pattern || '').toUpperCase();
        const rsi = Number(x.rsi_14 || 50);
        const mtf = String(x.mtf_text || '').toUpperCase();
        return pattern.includes('HAMMER') || pattern.includes('SHOOTING') || rsi <= 32 || rsi >= 68 || mtf.includes('FAST SCALP');
      });
    } else if (preset === 'anti_chase_clean') {
      result = result.filter(x => x.anti_chase_ok !== false && !(Array.isArray(x.rejection_reasons) && x.rejection_reasons.length));
    } else if (preset === 'near_bull') {
      result = result.filter(x => {
        const d = triggerDistance(x, 'bull');
        return d && (d.crossed || d.absDistance <= 1.2);
      });
    } else if (preset === 'near_bear') {
      result = result.filter(x => {
        const d = triggerDistance(x, 'bear');
        return d && (d.crossed || d.absDistance <= 1.2);
      });
    } else if (preset === 'high_rank') {
      result = result.filter(x => smartRank(x) >= 100);
    }
    return result;
  }

  _currentSelected() {
    if (this._selectedSymbol) {
      const selected = this._data.get(this._selectedSymbol) || this._sorted.find(x => x.symbol === this._selectedSymbol);
      if (selected) return selected;
    }
    return this._sorted[0] || null;
  }

  // Row Rendering
  //
  // v8.0.0 UI overhaul Phase A: this class used to define _updateMiniTradeCard()
  // twice. JS class bodies allow that silently -- the second definition
  // simply shadows the first at the prototype level, making the first one
  // entirely uncallable. Confirmed the first (deleted) definition was
  // already fully dead code even before the shadowing: it only ever
  // targeted `#scannerMiniCard`, an element id that is never rendered
  // anywhere in this file or index.html (only `#scannerTradePlan` exists,
  // which is what the surviving definition below targets).
  _updateMiniTradeCard() {
    const el = this._el.querySelector('#scannerTradePlan') || this._el.querySelector('#scannerMiniCard');
    if (!el) return;
    const item = this._currentSelected();
    if (!item) {
      el.innerHTML = `<div class="trade-plan-empty">No stock matches current filters</div>`;
      return;
    }
    const symbol = String(item.symbol || '').toUpperCase();
    const rawDecision = item.trade_decision || 'WAIT';
    const decision = item.direction_bias || rawDecision;
    const mtf = item.mtf_text || 'MTF building';
    const optionReady = Math.round(Number(item.option_readiness || 0));
    const setup = Math.round(Number(item.setup_strength || 0));
    const rank = modeAwareRank(item, this._signalMode);
    const rawRank = smartRank(item);
    const modeSig = modeSignal(item, this._signalMode);
    const modeProfile = this._signalModeProfile || signalModeProfile(this._signalMode);
    const risk = currentRiskBudget();
    const entry = Number(item.entry_price_hint || 0);
    const sl = Number(item.stop_loss_hint || 0);
    const t1 = Number(item.target_1_hint || 0);
    const t2 = Number(item.target_2_hint || 0);
    const underlyingRisk = entry && sl ? Math.abs(entry - sl) : 0;
    const rrHint = Number(item.risk_reward_ratio_hint || 0);
    const rr1 = rrHint > 0 ? rrHint.toFixed(1) : underlyingRisk && t1 ? (Math.abs(t1 - entry) / underlyingRisk).toFixed(1) : '-';
    const directionClass = decisionClass(decision);
    const trigger = directionClass === 'sell' ? item.pe_active_below : directionClass === 'buy' ? item.ce_active_above : item.ce_active_above;
    const triggerLabel = directionClass === 'sell' ? 'PE below' : directionClass === 'buy' ? 'CE above' : 'Wait zone';
    const reasons = Array.isArray(item.strength_reasons) ? item.strength_reasons.slice(0, 2).join(' · ') : '';
    const blockers = Array.isArray(item.rejection_reasons) && item.rejection_reasons.length
      ? item.rejection_reasons.slice(0, 2).join(' · ')
      : item.anti_chase_ok ? 'Anti-chase clean' : 'Check blockers';
    el.innerHTML = `
      <div class="trade-plan-card ${directionClass}">
        ${this._tradeDecisionBanner(item)}
        <div class="trade-plan-hero">
          <span>Selected Trade Plan</span>
          <b>${escapeHtml(symbol || '-')}</b>
          <small>${escapeHtml(item.sector_id || '-')} - Mode rank ${rank} - Raw ${rawRank}</small>
          <button type="button" data-open-tv="${escapeHtml(symbol)}">TradingView</button>
          <button type="button" class="trade-plan-log-btn" data-log-paper="${escapeHtml(symbol)}">Mark Taken</button>
        </div>
        <div class="trade-plan-action">
          <span>${escapeHtml(decision)}</span>
          <b>${optionReady}</b>
          <small>Option conviction</small>
        </div>
        <div class="trade-plan-mode ${escapeHtml(modeSig.tone)}">
          <label>${escapeHtml(modeProfile.label)} verdict</label>
          <b>${escapeHtml(modeSig.label)}</b>
          <small>${escapeHtml(modeSig.reason)} · Needs Opt ${modeProfile.watch}/${modeProfile.paper}+ and R:R ${modeProfile.minRr}:1+</small>
        </div>
        <div class="trade-plan-metric"><label>LTP</label><b>${formatPrice(item.ltp)}</b><small class="${Number(item.change_pct || 0) >= 0 ? 'positive' : 'negative'}">${formatPct(item.change_pct)} · ${formatRelVol(item.rel_vol)}</small></div>
        <div class="trade-plan-metric"><label>Entry</label><b>${formatPrice(entry)}</b><small>${escapeHtml(item.status || 'WATCH')}</small></div>
        <div class="trade-plan-metric"><label>SL</label><b class="negative">${formatPrice(sl)}</b><small>Risk ${underlyingRisk ? formatPrice(underlyingRisk) : '-'}</small></div>
        <div class="trade-plan-metric"><label>T1 / T2</label><b class="positive">${formatPrice(t1)}</b><small>${formatPrice(t2)} · R:R ${rr1}:1</small></div>
        <div class="trade-plan-metric"><label>Potential Upside</label><b class="positive">${formatPct(item.potential_upside_pct)}</b><small>${escapeHtml(item.trade_horizon_label || '')}</small></div>
        <div class="trade-plan-metric"><label>${escapeHtml(triggerLabel)}</label><b class="${directionClass === 'sell' ? 'negative' : 'positive'}">${formatPrice(trigger)}</b><small>activation</small></div>
        <div class="trade-plan-metric"><label>Risk budget</label><b>${formatPrice(risk.riskAmount)}</b><small>${risk.riskPct.toFixed(1)}% capital</small></div>
        <div class="trade-plan-wide"><label>MTF / Evidence</label><b>${escapeHtml(mtf)}</b><small>Strength ${setup} · ${escapeHtml(reasons || 'Evidence building')}</small></div>

        <details class="trade-plan-advanced">
          <summary>Advanced ▾</summary>
          ${commandCenterBlock(item)}
          ${selectedTradeChecklist(item, this._signalMode)}
          <div class="direction-command-panel ${escapeHtml(item.direction_tone || 'wait')}">
            <div><label>BUY CE only above</label><b class="positive">${formatPrice(item.ce_active_above)}</b><small>Need 5M/15M close + volume</small></div>
            <div><label>WAIT / No chase zone</label><b>${escapeHtml(item.direction_wait_zone || 'building')}</b><small>${escapeHtml(item.direction_state || 'WAIT_ZONE')}</small></div>
            <div><label>BUY PE only below</label><b class="negative">${formatPrice(item.pe_active_below)}</b><small>Need 5M/15M close + VWAP rejection</small></div>
            <div><label>Directional conviction</label><b>CE ${Math.round(Number(item.ce_score || 0))} / PE ${Math.round(Number(item.pe_score || 0))}</b><small>${escapeHtml(item.direction_switch_note || item.direction_reason || 'Stable direction engine active')}</small></div>
          </div>
          <div class="trade-plan-ai-map">
            <div class="ai-map-head">
              <span>AI Trade Map</span>
              ${horizonChip(item.trade_horizon, item.trade_horizon_label)}
              ${chaseChip(item.chase_quality)}
            </div>
            <p>${escapeHtml(item.breakout_explanation || 'Awaiting breakout map')}</p>
            ${item.target_method ? `<small class="target-method-note">${escapeHtml(item.target_method)}</small>` : ''}
            <div class="ai-map-levels">
              <span><label>Breakout area</label><b>${formatPrice(item.breakout_area || item.positive_above)}</b></span>
              <span><label>Sustain rule</label><b>${escapeHtml(item.sustain_rule || '5M/15M close')}</b></span>
              <span><label>Move upto</label><b class="positive">${formatPrice(item.move_to || t1)}</b></span>
              <span><label>Extended</label><b class="positive">${formatPrice(item.extended_move_to || t2)}</b></span>
              <span><label>Breakdown / invalid</label><b class="negative">${formatPrice(item.invalidation_area || item.negative_below)}</b></span>
              <span><label>Carry risk</label><b>${escapeHtml(item.carry_risk || 'MEDIUM')}</b></span>
            </div>
            <small>${escapeHtml(item.horizon_reason || 'Horizon model is building from MTF, sector, trend and option realism.')}</small>
          </div>
          ${this._renderOptionQuickPick(item)}
          ${this._renderExecutionGates(item)}
          <div class="trade-plan-wide"><label>Block / Anti-chase</label><b>${escapeHtml(blockers)}</b><small>${item.anti_chase_ok === false ? 'Wait for cleaner retest' : 'Allowed if chart confirms'}</small></div>
        </details>
      </div>
    `;
  }

  _optionJournalSnapshot() {
    const option = this._selectedOption || {};
    const up = option.upstox_option || {};
    const metrics = up.metrics || {};
    return {
      execution_status: option.execution_status || (option.option_chain_ready ? 'TRADE_READY' : 'WAIT_CONTRACT'),
      quality_grade: option.quality_grade || '-',
      trade_ready: Boolean(option.trade_ready),
      suggested_contract: option.suggested_contract || '',
      instrument_key: up.instrument_key || option.instrument_key || '',
      premium: metrics.ltp ?? metrics.premium ?? null,
      bid: metrics.bid ?? null,
      ask: metrics.ask ?? null,
      entry_fill: metrics.entry_fill ?? metrics.ask ?? metrics.ltp ?? null,
      exit_fill_reference: metrics.exit_fill_reference ?? metrics.bid ?? null,
      spread_pct: metrics.spread_pct ?? null,
      spread_per_unit: metrics.spread_per_unit ?? null,
      est_costs_per_unit: metrics.est_costs_per_unit ?? null,
      oi: metrics.oi ?? null,
      iv: metrics.iv ?? null,
      iv_rank: metrics.iv_rank ?? null,
      iv_history_count: metrics.iv_history_count ?? null,
      delta: metrics.delta ?? null,
      delta_used: metrics.delta_used ?? metrics.delta ?? null,
      premium_risk_pct: metrics.premium_risk_pct ?? null,
      premium_risk: metrics.premium_risk ?? null,
      option_sl_price: metrics.option_sl_price ?? null,
      underlying_risk: metrics.underlying_risk ?? null,
      breakeven_underlying: metrics.breakeven_underlying ?? null,
      required_move_pct: metrics.required_move_pct ?? null,
      expected_move_pct: metrics.expected_move_pct ?? null,
      target_clears_breakeven: metrics.target_clears_breakeven ?? null,
      liquidity_whitelist_pass: metrics.liquidity_whitelist_pass ?? null,
      physical_settlement_block: metrics.physical_settlement_block ?? null,
      event_calendar: metrics.event_calendar ?? option.event_calendar ?? null,
      next_event_date: metrics.event_calendar?.next_event_date ?? option.event_calendar?.next_event_date ?? '',
      expiry_days: metrics.expiry_days ?? null,
      lot_size: metrics.lot_size ?? null,
      blockers: option.blockers || [],
      hard_blockers: option.hard_blockers || [],
    };
  }

  async _logPaperTrade(buttonEl) {
    const item = this._currentSelected();
    if (!item) return;
    const symbol = String(item.symbol || '').toUpperCase();
    const originalText = buttonEl.textContent;
    buttonEl.disabled = true;
    buttonEl.textContent = 'Marking...';
    try {
      const data = await api.fetch('/api/journal/trades?limit=200');
      const rows = Array.isArray(data?.trades) ? data.trades : [];
      const row = rows.find(x =>
        String(x.symbol || '').toUpperCase() === symbol &&
        String(x.discretionary_action || 'NOT_REVIEWED').toUpperCase() === 'NOT_REVIEWED'
      );
      if (!row?.id) {
        buttonEl.textContent = 'No auto row';
        setTimeout(() => {
          buttonEl.disabled = false;
          buttonEl.textContent = originalText;
        }, 1800);
        return;
      }
      const res = await api.post(`/api/journal/trades/${encodeURIComponent(row.id)}/discretion`, {
        discretionary_action: 'TAKEN',
      });
      if (res?.ok) {
        buttonEl.textContent = 'Marked Taken';
        document.dispatchEvent(new CustomEvent('journal:refresh', { detail: res.trade }));
        setTimeout(() => {
          buttonEl.disabled = false;
          buttonEl.textContent = originalText;
        }, 1600);
      } else {
        buttonEl.textContent = 'Log failed';
        setTimeout(() => {
          buttonEl.disabled = false;
          buttonEl.textContent = originalText;
        }, 1800);
      }
    } catch (_) {
      buttonEl.textContent = 'Log failed';
      setTimeout(() => {
        buttonEl.disabled = false;
        buttonEl.textContent = originalText;
      }, 1800);
    }
  }

  // Row quick-action: stage a ticket directly from the table without the
  // click-row -> scroll -> click-Stage round trip. Same lookup pattern as
  // _logPaperTrade (find the auto-logged journal row for this symbol) and
  // the same /api/execution/stage call journal-panel.js's Stage Ticket
  // button uses -- one staging path, not two that could drift apart.
  async _stageFromRow(symbol, buttonEl) {
    const sym = String(symbol || '').toUpperCase();
    if (!sym) return;
    const originalText = buttonEl.textContent;
    buttonEl.disabled = true;
    buttonEl.textContent = '...';
    try {
      const data = await api.fetch('/api/journal/trades?limit=200');
      const rows = Array.isArray(data?.trades) ? data.trades : [];
      const trade = rows.find(x => String(x.symbol || '').toUpperCase() === sym);
      if (!trade?.id) {
        buttonEl.textContent = 'No signal yet';
        setTimeout(() => {
          buttonEl.disabled = false;
          buttonEl.textContent = originalText;
        }, 1800);
        return;
      }
      const res = await api.post('/api/execution/stage', { trade });
      if (res?.ok) {
        buttonEl.textContent = res.ticket?.status === 'READY_TO_STAGE' ? 'Staged' : 'Blocked';
        document.dispatchEvent(new CustomEvent('execution:refresh', { detail: res.ticket }));
      } else {
        buttonEl.textContent = 'Stage failed';
      }
    } catch (_) {
      buttonEl.textContent = 'Stage failed';
    } finally {
      setTimeout(() => {
        buttonEl.disabled = false;
        buttonEl.textContent = originalText;
      }, 1800);
    }
  }

  _renderRow(item) {
    const chgClass   = (item.change_pct || 0) >= 0 ? 'positive' : 'negative';
    const rvClass    = (item.rel_vol || 0) > 2 ? 'positive' : (item.rel_vol || 0) < 0.5 ? 'negative' : '';
    const flashClass = item._flash === 'up' ? 'flash-up' : item._flash === 'down' ? 'flash-down' : '';
    const score      = Math.round(item.score || 0);
    const cc         = convClass(score);
    const hasSignal  = item.signal_active;

    // Signal badge
    let signalHTML = '';
    if (hasSignal && item.signal) {
      const sc = SIG_COLORS[item.signal] || SIG_COLORS.bullish;
      signalHTML = `<span class="intel-badge" style="background:${sc.bg};color:${sc.color}">${sc.label}</span>`;
    }

    // Conviction display
    let convHTML = '';
    if (score > 0) {
      convHTML = `<span class="conv-val ${cc}">${score}</span>`;
    }

    // Grade badge
    let gradeHTML = '';
    if (item.grade) {
      const gc = item.grade === 'A+' ? 'grade-aplus' : item.grade === 'A' ? 'grade-a' : item.grade === 'B' ? 'grade-b' : 'grade-c';
      gradeHTML = `<span class="grade-chip ${gc}">${item.grade}</span>`;
    }

    // State badge
    let stateHTML = '';
    if (item.state && item.state !== 'idle' && item.state !== 'expired') {
      const st = STATE_COLORS[item.state] || {};
      stateHTML = `<span class="intel-badge" style="background:${st.bg || '#1e293b'};color:${st.color || '#94a3b8'}">${st.label || item.state.toUpperCase()}</span>`;
    }

    // Readiness bar
    let readHTML = '';
    if (item.readiness > 0) {
      const r = Math.round(item.readiness);
      const rc = r >= 75 ? '#4ade80' : r >= 50 ? '#facc15' : '#f97316';
      readHTML = `<div style="display:flex;align-items:center;gap:3px;justify-content:flex-end">
        <div style="width:28px;height:4px;background:#1e293b;border-radius:2px;overflow:hidden">
          <div style="width:${r}%;height:100%;background:${rc};border-radius:2px"></div>
        </div>
        <span style="color:${rc};font-size:10px">${r}</span>
      </div>`;
    }

    const setupHTML = scoreMeter(item.setup_strength || item.readiness || 0);
    const optionHTML = scoreMeter(item.option_readiness || 0);
    const trendHTML = stateChip(item.trend_bias || 'HOLD', item.trend_bias || 'HOLD');
    const decisionHTML = stateChip(item.direction_bias || item.trade_decision || 'HOLD', item.direction_bias || item.trade_decision || 'HOLD');
    const statusHTML = stateChip(String(item.status || 'WATCH').replace('_', ' '), item.status || 'HOLD');
    const vwapHTML = item.vwap_state ? `<span class="mini-state ${String(item.vwap_state).toLowerCase()}">${escapeHtml(item.vwap_state)}</span>` : '';
    const emaHTML = item.ema_state ? `<span class="mini-state ${String(item.ema_state).toLowerCase()}">${escapeHtml(item.ema_state)}</span>` : '';
    const macdHTML = item.macd_state ? `<span class="mini-state ${String(item.macd_state).toLowerCase()}">${escapeHtml(item.macd_state)}</span>` : '';

    // Map from column key → cell HTML content
    const contentMap = {
      symbol:     `<span class="sym-link">${escapeHtml(item.symbol)}</span><button type="button" class="row-quick-stage" data-stage-row="${escapeHtml(item.symbol)}" title="Stage this trade (paper-first)">Stage</button>`,
      ltp:        `<span style="font-weight:600">${formatPrice(item.ltp)}</span>`,
      prev_diff:  `<span class="${nDiffClass(item.points_diff)}">${formatPrice(item.prev_close || 0)}<br><small>${formatPrice(item.points_diff || 0)} (${formatPct(item.pct_diff ?? item.change_pct)})</small></span>`,
      change_pct: `<span class="${chgClass}">${formatPct(item.change_pct)}</span>`,
      volume:     `<span class="text-muted">${formatVolume(item.volume)}</span>`,
      rel_vol:    `<span class="${rvClass}">${formatRelVol(item.rel_vol)}</span>`,
      vwap_dist:  `<span>${item.vwap_dist != null ? formatPct(item.vwap_dist, 1) : '—'}</span>`,
      sector_id:  `<span style="font-size:10px;color:var(--text-muted)" title="${escapeHtml(item.sector_id || '')}">${escapeHtml((item.sector_id||'').replace('_',' '))}</span>`,
      smart_rank: smartRankCell(item),
      mode_signal: modeSignalChip(item, this._signalMode),
      gate_score: gateScoreCell(item, this._signalMode),
      intelligence_score: intelCell(item),
      news_confirmation: newsCell(item),
      trade_horizon: horizonChip(item.trade_horizon, item.trade_horizon_label),
      chase_quality: chaseChip(item.chase_quality),
      intraday_score: scoreMeter(item.intraday_score || 0),
      swing_score: scoreMeter(item.swing_score || 0),
      trend_bias: trendHTML,
      mtf_dots: mtfDots(item.mtf_dots),
      mtf_score: scoreMeter(item.mtf_score ?? item.pine_confidence ?? item.option_readiness ?? 0),
      mtf_source: mtfSourceChip(item.mtf_source),
      setup_strength: setupHTML,
      trade_decision: decisionHTML,
      direction_zone: directionZoneCell(item),
      ce_score: scoreMeter(item.ce_score || 0),
      pe_score: scoreMeter(item.pe_score || 0),
      option_readiness: optionHTML,
      chain_execution_status: chainCell(item),
      evidence: rowEvidence(item),
      entry_price_hint: `<span class="level-cell">${formatPrice(item.entry_price_hint)}</span>`,
      stop_loss_hint: `<span class="level-cell negative">${formatPrice(item.stop_loss_hint)}</span>`,
      target_1_hint: `<span class="level-cell positive">${formatPrice(item.target_1_hint)}</span>`,
      target_2_hint: `<span class="level-cell positive">${formatPrice(item.target_2_hint)}</span>`,
      target_3_hint: `<span class="level-cell positive">${formatPrice(item.target_3_hint)}</span>`,
      positive_above: `<span class="level-cell positive">${formatPrice(item.positive_above)}</span>`,
      negative_below: `<span class="level-cell negative">${formatPrice(item.negative_below)}</span>`,
      ai_trade_map: aiTradeMapCell(item),
      status: statusHTML,
      alignment: `<span class="text-muted">${escapeHtml(item.alignment || '-')}</span>`,
      fibo_pivot: `<span class="level-cell">${formatPrice(item.fibo_pivot)}</span>`,
      vwap_state: vwapHTML,
      ema_state: emaHTML,
      macd_state: macdHTML,
      signal:     signalHTML,
      score:      convHTML,
      grade:      gradeHTML,
      state:      stateHTML,
      readiness:  readHTML,
    };

    // Build each cell with correct width from COLUMNS - guarantees header/row alignment
    const cols = visibleColumns(this._hidden);
    const stickyCols = stickyMeta(cols);
    const cells = cols
      .map(c => {
        const content = contentMap[c.key] || '';
        const isSticky = stickyCols.has(c.key);
        const classes = ['sc', isSticky ? 'sticky-freeze' : '', c.key === 'symbol' ? 'sticky-symbol' : ''].filter(Boolean).join(' ');
        const stickyStyle = isSticky ? `left:${stickyCols.get(c.key)}px;` : '';
        return `<div class="${classes}" data-col="${c.key}" style="${stickyStyle}${cellSizeStyle(c, isSticky)}text-align:${c.align}">${content}</div>`;
      })
      .join('');

    // Visual hierarchy classes — eye lands on best setups
    const gradeHighlight = item.grade === 'A+' ? 'grade-highlight-aplus'
                         : item.grade === 'A'  ? 'grade-highlight-a'
                         : '';
    // Selected-row detail used to expand inline here (position:absolute
    // over the row), which kept re-breaking every time the column set
    // changed since it depended on exact sticky-width/row-height math.
    // The "Selected Trade Plan" panel (_updateMiniTradeCard) already shows
    // the same detail without that fragility, so the row itself now just
    // highlights — one source of truth for "what's selected," not two.
    const selected = this._selectedSymbol && String(this._selectedSymbol).toUpperCase() === String(item.symbol || '').toUpperCase();
    const modeRow = modeSignal(item, this._signalMode);
    const rowClass = `scanner-row ${flashClass} ${hasSignal ? 'has-signal' : ''} ${cc} ${gradeHighlight} ${actionClass(item)} mode-${modeRow.key} ${selected ? 'selected-row' : ''}`.trim();
    const stickyWidth = cols.reduce((sum, col) => stickyCols.has(col.key) ? sum + px(col.width) : sum, 0);
    return `<div class="${rowClass}" data-symbol="${escapeHtml(item.symbol)}" style="--sticky-width:${stickyWidth}px">${cells}</div>`;
  }

  destroy() {
    this._unsubs.forEach(fn => fn());
    if (this._vs) this._vs.destroy();
    if (this._viewportResizeObserver) this._viewportResizeObserver.disconnect();
    this._flashTimers.forEach(t => clearTimeout(t));
  }
}
