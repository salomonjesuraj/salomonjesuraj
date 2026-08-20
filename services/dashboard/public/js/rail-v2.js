/**
 * Left rail — Phase N5 shipped this as a flat, 1:1 restyle of the "More"
 * drawer's destinations (screener excluded -- it's already the primary
 * New shell view, not tucked away): same panel classes underneath, just a
 * vertical list instead of a horizontal tab bar. That phase deliberately
 * deferred grouping ("ship flat first, group only once it's live and
 * stable" -- see the plan). Phase O.5 does that grouping now, applying
 * the same 5 clusters used for Classic's More drawer (index.html's
 * .workbench-tab-group) so the two navigation systems read as the same
 * taxonomy even though they're separately maintained (this rail is its
 * own generated-from-ROWS mechanism, not literally shared code with
 * workbench-tabs.js -- confirmed by reading both before this phase,
 * correcting an earlier assumption that they were the same system).
 */
const GROUPS = ['Screener & Detail', 'Options', 'Backtest & Optimizer', 'Execution & Journal', 'Info'];
const ROWS = [
  // Phase R3 -- Stock Breakout Radar, first in its group per the reference
  // plan's "first and largest panel" framing (docs/stock-options-
  // dashboard-review-and-structure-plan.md).
  { key: 'breakout-radar', icon: '📡', label: 'Breakout Radar', group: 'Screener & Detail' },
  // EBIE EB-12 -- ranked candidate list + Why-Now/Why-Not evidence,
  // per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q4.1 (New-shell only).
  { key: 'ebie-verdict', icon: '⚡', label: 'EBIE Verdict', group: 'Screener & Detail' },
  { key: 'watchlist', icon: '◑', label: 'Watchlist', group: 'Screener & Detail' },
  { key: 'stock-detail', icon: '◎', label: 'Stock Detail', group: 'Screener & Detail' },
  { key: 'triggers', icon: '▲', label: 'Triggers', group: 'Screener & Detail' },
  { key: 'option-basis', icon: '⌘', label: 'Option Basis', group: 'Options' },
  { key: 'options-analytics', icon: '⌗', label: 'Options Analytics', group: 'Options' },
  { key: 'strategy-selector', icon: '⚖', label: 'Strategy Selector', group: 'Options' },
  { key: 'optimizer', icon: '↻', label: 'Optimizer', group: 'Backtest & Optimizer' },
  { key: 'diagnostics', icon: '📊', label: 'Diagnostics', group: 'Backtest & Optimizer' },
  { key: 'risk', icon: '◈', label: 'Risk', group: 'Backtest & Optimizer' },
  { key: 'signal-integrity', icon: '✓', label: 'Signal Integrity', group: 'Backtest & Optimizer' },
  { key: 'execution', icon: '⚙', label: 'Execution', group: 'Execution & Journal' },
  { key: 'journal', icon: '▤', label: 'Journal', group: 'Execution & Journal' },
  { key: 'safety', icon: '🛡', label: 'Safety', group: 'Execution & Journal' },
  { key: 'alerts', icon: '🔔', label: 'Alert Log', group: 'Execution & Journal' },
  { key: 'news', icon: '📰', label: 'News', group: 'Info' },
  { key: 'events', icon: '📅', label: 'Events', group: 'Info' },
  { key: 'ask-infusion', icon: '✦', label: 'Ask Infusion', group: 'Info' },
];

export function initRailV2() {
  const rail = document.getElementById('railV2');
  const primary = document.getElementById('primaryViewV2');
  const paneView = document.getElementById('paneViewV2');
  const paneTitle = document.getElementById('paneTitleV2');
  const backBtn = document.getElementById('backToPrimaryV2');
  if (!rail || !primary || !paneView) return;

  rail.innerHTML = GROUPS.map((g) => `
    <div class="ifx-rail-group">
      <span class="ifx-rail-group-label">${g}</span>
      ${ROWS.filter((r) => r.group === g).map((r) => `
        <button type="button" class="ifx-rail-item" data-rail-key="${r.key}">
          <span class="ifx-rail-icon">${r.icon}</span><span class="ifx-rail-item-label">${r.label}</span>
          <span class="ifx-rail-chevron">→</span>
        </button>
      `).join('')}
    </div>
  `).join('');

  function showPane(key) {
    const row = ROWS.find((r) => r.key === key);
    if (!row) return;
    primary.style.display = 'none';
    paneView.style.display = 'block';
    paneTitle.textContent = row.label;
    paneView.querySelectorAll('[data-pane-v2]').forEach((el) => {
      el.style.display = el.dataset.paneV2 === key ? 'block' : 'none';
    });
    rail.querySelectorAll('.ifx-rail-item').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.railKey === key);
    });
  }

  function showPrimary() {
    paneView.style.display = 'none';
    primary.style.display = 'block';
    rail.querySelectorAll('.ifx-rail-item').forEach((btn) => btn.classList.remove('active'));
  }

  rail.querySelectorAll('[data-rail-key]').forEach((btn) => {
    btn.addEventListener('click', () => showPane(btn.dataset.railKey));
  });
  backBtn?.addEventListener('click', showPrimary);

  return { showPane, showPrimary };
}
