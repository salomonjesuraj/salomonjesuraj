/**
 * Signal Integrity — Phase N7. Every signal Infusion has fired, and what
 * actually happened. Built after checking the real backend first (an
 * Explore agent verified the signals table schema, tracker.py, and every
 * candidate endpoint before any UI code was written -- see the plan's
 * "Dashboard Redesign v2" section for the full investigation):
 *
 *   Today/Yesterday -> /api/analytics/precision(/grade|/session|/sector)?date=X
 *                       + /api/analytics/outcomes?date=X (Phase N7 also
 *                       extended this endpoint to accept date=, see
 *                       archiver/analytics.py's recent_outcomes())
 *   7D/30D/90D      -> /api/backtest/summary?days=N (bundles precision +
 *                       by_grade/by_session/by_sector in one call) +
 *                       /api/analytics/outcomes (no date -- "most recent
 *                       overall" is the honest label for the ledger on
 *                       these wider windows, since /outcomes has no
 *                       rolling-window param, only exact-date or none)
 *
 * Fetched on-demand per window switch, not polled: /api/backtest/summary
 * measured ~28s live for days=7 in this environment (269k+ rows scanned
 * for a single day's synthetic signal volume) -- polling that every
 * minute while a user sits on a wide window would be real, avoidable load
 * for no benefit. A loading state says so explicitly rather than looking
 * broken during the wait.
 *
 * T1/T2/T3 level-hit tracking stays an honest, explicitly-labeled
 * placeholder until Phase N8 lands (tracker.py only ever checks one
 * target_price today -- confirmed by reading it, not assumed).
 */
import { api } from './api.js';
import { istNow, istDateStr, yesterdayIstStr } from './utils.js';

const WINDOWS = [
  { key: 'today', label: 'Today' },
  { key: 'yesterday', label: 'Yesterday' },
  { key: '7', label: 'Last 7 Days' },
  { key: '30', label: 'Last 30 Days' },
  { key: '90', label: 'Last 90 Days' },
];

export class IntegrityPanelV2 {
  constructor(containerEl) {
    this._el = containerEl;
    this._window = 'today';
    this._loading = false;
  }

  init() {
    if (!this._el) return;
    this._el.innerHTML = `
      <div class="ifx-int-toolbar" id="intWindowPills"></div>
      <div class="ifx-int-stats" id="intStats"></div>
      <div class="ifx-int-gap-note">
        <b>⚠ Not tracked yet</b>
        <p>Infusion currently records one <span class="ifx-mono">target_price</span> per signal and a binary <b>TARGET_HIT</b> / <b>STOP_HIT</b> outcome — it does not yet know which fib level (T1/T2/T3) price actually reached. The bars below are a placeholder for once the outcome tracker checks all three levels (scoped as Phase N8), not real data.</p>
        <div class="ifx-int-t123">
          <div class="ifx-int-t123-row"><label>T1</label><div class="ifx-int-t123-track"></div><span>not tracked</span></div>
          <div class="ifx-int-t123-row"><label>T2</label><div class="ifx-int-t123-track"></div><span>not tracked</span></div>
          <div class="ifx-int-t123-row"><label>T3</label><div class="ifx-int-t123-track"></div><span>not tracked</span></div>
        </div>
      </div>
      <div class="ifx-section-label" style="margin-top:16px">Breakdown by grade / session / sector<span class="ifx-section-rule"></span></div>
      <div class="ifx-int-breakdowns" id="intBreakdowns"></div>
      <div class="ifx-section-label" style="margin-top:16px">Signal Ledger<span class="ifx-section-rule"></span><span class="ifx-section-count" id="intLedgerCount"></span></div>
      <div class="ifx-int-table-wrap">
        <table class="ifx-int-table">
          <thead><tr>
            <th>Date</th><th>Symbol</th><th>Strategy</th><th>Grade</th><th>Entry</th><th>Stop</th><th>Target</th>
            <th>Outcome</th><th>Move</th><th>Time to outcome</th>
          </tr></thead>
          <tbody id="intLedgerBody"></tbody>
        </table>
      </div>
    `;

    const pillsEl = this._el.querySelector('#intWindowPills');
    pillsEl.innerHTML = WINDOWS.map((w) =>
      `<button type="button" class="ifx-btn ifx-int-pill${w.key === this._window ? ' on' : ''}" data-window="${w.key}">${w.label}</button>`
    ).join('');
    pillsEl.querySelectorAll('[data-window]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (this._loading) return;
        this._window = btn.dataset.window;
        pillsEl.querySelectorAll('.ifx-int-pill').forEach((b) => b.classList.toggle('on', b === btn));
        this._load();
      });
    });

    this._load();
  }

  async _load() {
    this._loading = true;
    const isDay = this._window === 'today' || this._window === 'yesterday';
    const dateStr = this._window === 'today' ? istDateStr(istNow()) : this._window === 'yesterday' ? yesterdayIstStr() : null;
    this._el.querySelector('#intStats').innerHTML = `<div class="ifx-int-loading">Loading${isDay ? '' : ' — wider windows can take up to ~30s against a large archive'}…</div>`;
    this._el.querySelector('#intBreakdowns').innerHTML = '';
    this._el.querySelector('#intLedgerBody').innerHTML = '';

    try {
      let stats, byGrade, bySession, bySector, decidedCount;
      if (isDay) {
        const [precision, grade, session, sector] = await Promise.all([
          api.fetch(`/api/analytics/precision?date=${dateStr}`),
          api.fetch(`/api/analytics/precision/grade?date=${dateStr}`),
          api.fetch(`/api/analytics/precision/session?date=${dateStr}`),
          api.fetch(`/api/analytics/precision/sector?date=${dateStr}`),
        ]);
        stats = precision;
        decidedCount = (precision?.target_hits || 0) + (precision?.stop_hits || 0);
        byGrade = (grade || []).map((g) => ({ label: g.grade, total: g.total, wins: g.target_hits, losses: g.stop_hits, precision_pct: g.precision_pct }));
        bySession = (session || []).map((s) => ({ label: s.session, total: s.total, wins: s.target_hits, losses: s.stop_hits, precision_pct: s.precision_pct }));
        bySector = (sector || []).map((s) => ({ label: s.sector, total: s.total, wins: s.target_hits, losses: s.stop_hits, precision_pct: s.precision_pct }));
      } else {
        // /api/backtest/summary?days=7 measured 23s live against this
        // archive's 269k+ row volume, and a separate check hit nginx's
        // 30s gateway timeout outright -- flaky right at that edge, not
        // reliably fast. api.fetch() returns null on ANY failure
        // (non-2xx, timeout, network error), so `summary` being null
        // here specifically means the request failed, not that the
        // window had zero signals -- checked explicitly below rather
        // than letting `summary?.total ?? 0` quietly render a failure
        // as an honest-looking "0 signals fired".
        const summary = await api.fetch(`/api/backtest/summary?days=${this._window}`);
        if (summary === null) {
          this._el.querySelector('#intStats').innerHTML =
            `<div class="ifx-int-loading">Request failed or timed out — this window queries a large archive and can occasionally exceed the gateway's 30s limit. Try again, or a narrower window.</div>`;
          this._loading = false;
          return;
        }
        stats = summary;
        decidedCount = summary?.decided ?? ((summary?.target_hits || 0) + (summary?.stop_hits || 0));
        byGrade = summary?.by_grade || [];
        bySession = summary?.by_session || [];
        bySector = summary?.by_sector || [];
      }

      if (isDay && stats === null) {
        this._el.querySelector('#intStats').innerHTML = `<div class="ifx-int-loading">Request failed — could not load precision data for this date.</div>`;
        this._loading = false;
        return;
      }

      const ledgerUrl = isDay ? `/api/analytics/outcomes?date=${dateStr}&limit=50` : `/api/analytics/outcomes?limit=50`;
      const ledger = await api.fetch(ledgerUrl);

      this._renderStats(stats, decidedCount);
      this._renderBreakdowns(byGrade, bySession, bySector);
      this._renderLedger(Array.isArray(ledger) ? ledger : [], !isDay);
    } catch (e) {
      this._el.querySelector('#intStats').innerHTML = `<div class="ifx-int-loading">Request failed: ${String(e)}</div>`;
    }
    this._loading = false;
  }

  _renderStats(stats, decidedCount) {
    const total = stats?.total ?? 0;
    const targetHits = stats?.target_hits ?? 0;
    const stopHits = stats?.stop_hits ?? 0;
    const precisionPct = stats?.precision_pct;
    const stopPct = decidedCount ? (stopHits / decidedCount) * 100 : null;
    const mfe = stats?.avg_mfe_pct ?? stats?.avg_mfe;
    const mae = stats?.avg_mae_pct ?? stats?.avg_mae;

    const cards = [
      { label: 'Signals Fired', value: total.toLocaleString('en-IN'), sub: `${decidedCount} decided, rest active/suppressed`, color: 'var(--ifx-shell-text)' },
      { label: 'Target Hit Rate', value: precisionPct != null ? `${precisionPct.toFixed(1)}%` : '—', sub: `${targetHits} of ${decidedCount} decided`, color: 'var(--ifx-bull)' },
      { label: 'Stop Hit Rate', value: stopPct != null ? `${stopPct.toFixed(1)}%` : '—', sub: `${stopHits} of ${decidedCount} decided`, color: 'var(--ifx-bear)' },
      { label: 'Avg Favorable Move', value: mfe != null ? `+${Number(mfe).toFixed(2)}%` : '—', sub: 'max_favorable_pct, real field', color: 'var(--ifx-bull)' },
      { label: 'Avg Adverse Move', value: mae != null ? `-${Number(mae).toFixed(2)}%` : '—', sub: 'max_adverse_pct, real field', color: 'var(--ifx-bear)' },
    ];
    this._el.querySelector('#intStats').innerHTML = cards.map((c) =>
      `<div class="ifx-int-stat"><label>${c.label}</label><b style="color:${c.color}">${c.value}</b><small>${c.sub}</small></div>`
    ).join('');
  }

  _renderBreakdowns(byGrade, bySession, bySector) {
    function card(title, rows) {
      const sorted = [...rows].filter((r) => r.total > 0).sort((a, b) => b.total - a.total).slice(0, 8);
      if (!sorted.length) return `<div class="ifx-int-bd-card"><h4>${title}</h4><div class="ifx-int-bd-empty">No decided signals in this window</div></div>`;
      return `<div class="ifx-int-bd-card"><h4>${title}</h4>` + sorted.map((r) => {
        const pct = r.precision_pct;
        const decided = (r.wins || 0) + (r.losses || 0);
        const color = pct == null ? 'var(--ifx-shell-text-faint)' : pct >= 65 ? 'var(--ifx-bull)' : pct >= 50 ? 'var(--ifx-warn)' : 'var(--ifx-bear)';
        return `<div class="ifx-int-bd-row"><span class="ifx-int-bd-label">${String(r.label || '-').replace(/_/g, ' ')}</span>
          <div class="ifx-int-bd-track"><i style="width:${pct ?? 0}%;background:${color}"></i></div>
          <span class="ifx-int-bd-val">${pct != null ? pct.toFixed(0) + '%' : '—'} (${decided})</span></div>`;
      }).join('') + '</div>';
    }
    this._el.querySelector('#intBreakdowns').innerHTML =
      card('By Grade', byGrade) + card('By Session', bySession) + card('By Sector', bySector);
  }

  _renderLedger(rows, isApprox) {
    const body = this._el.querySelector('#intLedgerBody');
    const count = this._el.querySelector('#intLedgerCount');
    count.textContent = `${rows.length} shown${isApprox ? ' (most recent overall, not window-filtered — /api/analytics/outcomes has no rolling-window param yet)' : ''}`;
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="10" class="ifx-int-ledger-empty">No decided signals in this window.</td></tr>`;
      return;
    }
    body.innerHTML = rows.map((r) => {
      const outcome = String(r.outcome || '').toUpperCase();
      const outcomeCls = outcome === 'TARGET_HIT' ? 'target' : outcome === 'STOP_HIT' ? 'stop' : 'expired';
      const move = outcome === 'TARGET_HIT' ? r.mfe_pct : outcome === 'STOP_HIT' ? -Math.abs(r.mae_pct) : (r.mfe_pct || -Math.abs(r.mae_pct || 0));
      const moveCls = move >= 0 ? 'ifx-tone-good' : 'ifx-tone-bad';
      const timeToOutcome = outcome === 'TARGET_HIT' ? r.time_to_target_min : outcome === 'STOP_HIT' ? r.time_to_stop_min : null;
      const dateStr = r.created_at ? new Date(r.created_at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }) : '—';
      return `<tr><td style="text-align:left">${dateStr}</td><td style="text-align:left"><b class="ifx-mono">${r.symbol || '—'}</b></td>
        <td style="text-align:left"><small>${r.strategy || '—'}</small></td><td>${r.grade || '—'}</td>
        <td class="ifx-mono">₹${Number(r.entry_price || 0).toFixed(2)}</td><td class="ifx-mono">₹${Number(r.stop || 0).toFixed(2)}</td><td class="ifx-mono">₹${Number(r.target || 0).toFixed(2)}</td>
        <td><span class="ifx-outcome-pill ${outcomeCls}">${outcome || '—'}</span></td>
        <td class="ifx-mono ${moveCls}">${move != null ? (move >= 0 ? '+' : '') + move.toFixed(2) + '%' : '—'}</td>
        <td class="ifx-mono">${timeToOutcome != null ? timeToOutcome.toFixed(0) + 'm' : '—'}</td></tr>`;
    }).join('');
  }

  destroy() {}
}
