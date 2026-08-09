/**
 * Track Record strip — Phase N6. Proof, not decoration (the user's own
 * framing: "convince me"). Four numbers, every one backed by a real
 * endpoint already built and verified this session, no invented figures:
 *
 *   Today          -> /api/analytics/precision (defaults to today's date,
 *                      confirmed live: date=2020-01-01 returns all-null/
 *                      zero, so the date-scoping is real, not a no-op)
 *   30-Day         -> /api/backtest/summary?days=30
 *   Walk-Forward   -> /api/backtest/walkforward?days=120&target=80
 *   Session P&L    -> sum of option.net_pnl across /api/journal/trades
 *                      rows with status === 'CLOSED' (real per-trade
 *                      cost-adjusted P&L, already computed by cost_model.py
 *                      -- reused here, not recomputed). Zero closed trades
 *                      today is a real, honestly-shown state, not an error.
 */
import { api } from './api.js';

const DASH = '—';

function tone(v, good, warn) {
  if (v == null) return 'var(--ifx-shell-text-faint)';
  return v >= good ? 'var(--ifx-bull)' : v >= warn ? 'var(--ifx-warn)' : 'var(--ifx-bear)';
}

export class TrackRecordV2Panel {
  constructor(containerEl) {
    this._el = containerEl;
    this._unsubs = [];
  }

  init() {
    if (!this._el) return;
    this._el.classList.add('ifx-tr-strip');
    this._render(null, null, null, null);

    this._unsubs.push(api.subscribe('/api/analytics/precision', (resp) => {
      this._today = resp;
      this._paint();
    }, 30000));
    this._unsubs.push(api.subscribe('/api/backtest/summary?days=30', (resp) => {
      this._thirty = resp;
      this._paint();
    }, 60000));
    this._unsubs.push(api.subscribe('/api/backtest/walkforward?days=120&target=80', (resp) => {
      this._wf = resp;
      this._paint();
    }, 60000));
    this._unsubs.push(api.subscribe('/api/journal/trades?limit=300', (resp) => {
      const rows = Array.isArray(resp?.trades) ? resp.trades : [];
      const closed = rows.filter((r) => String(r.status || '').toUpperCase() === 'CLOSED');
      const netPnl = closed.reduce((sum, r) => sum + Number(r.option?.net_pnl || 0), 0);
      this._pnl = { closedCount: closed.length, netPnl };
      this._paint();
    }, 15000));
  }

  _paint() {
    this._render(this._today, this._thirty, this._wf, this._pnl);
  }

  _render(today, thirty, wf, pnl) {
    const todayPct = today?.precision_pct;
    const todayDecided = today?.total || 0;
    const thirtyPct = thirty?.available !== false ? thirty?.precision_pct : null;
    const wfPct = wf?.available && wf?.recommended ? wf.recommended.test?.precision_pct : null;
    const wfNote = wf?.available ? (wf.status || '') : 'unavailable';
    const pnlVal = pnl?.netPnl;
    const pnlLabel = pnl == null ? '…' : pnl.closedCount === 0 ? '₹0' : `${pnlVal >= 0 ? '+' : ''}₹${pnlVal.toFixed(0)}`;
    const pnlSub = pnl == null ? 'loading' : pnl.closedCount === 0 ? 'no closed trades yet today' : `${pnl.closedCount} closed, net of costs`;

    const stats = [
      { label: 'Today', value: todayPct != null ? `${todayPct.toFixed(1)}%` : DASH, sub: `${todayDecided} decided`, color: tone(todayPct, 65, 50), src: '/api/analytics/precision -- defaults to today, real date-scoped query' },
      { label: '30-Day Precision', value: thirtyPct != null ? `${thirtyPct.toFixed(1)}%` : DASH, sub: `${thirty?.decided ?? 0} decided`, color: 'var(--ifx-shell-text)', src: '/api/backtest/summary?days=30' },
      { label: 'Walk-Forward', value: wfPct != null ? `${wfPct.toFixed(1)}%` : DASH, sub: `out-of-sample · ${wfNote.toLowerCase().replace(/_/g, ' ')}`, color: tone(wfPct, 75, 60), info: true,
        src: '/api/backtest/walkforward -- profile chosen on OLDER data only, scored on NEWER data it never saw. Not curve-fit.' },
      { label: 'Session P&L (net)', value: pnlLabel, sub: pnlSub, color: pnl?.closedCount ? tone(pnlVal, 0.01, -1e9) : 'var(--ifx-shell-text-faint)', src: 'journal.py option.net_pnl -- cost_model.py already applied per trade' },
    ];

    this._el.innerHTML = stats.map((s) =>
      `<div class="ifx-tr-stat" title="${s.src.replace(/"/g, '&quot;')}"><label>${s.label}${s.info ? ' <span class="ifx-tr-info">ⓘ</span>' : ''}</label>
        <b style="color:${s.color}">${s.value}</b><small>${s.sub}</small></div>`
    ).join('') + `<div class="ifx-tr-source">Live · hover any number for its source</div>`;
  }

  destroy() {
    this._unsubs.forEach((fn) => fn());
  }
}
