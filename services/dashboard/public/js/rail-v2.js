/**
 * Left rail — Clean Sweep LC-1 regroups this from 5 loosely-named groups
 * (19 rows) into 4 workflow-named groups (~15 top-level rows), per the
 * plan file's "Clean Sweep" section. Reasoning per group:
 *   - Trade: detection through contract decision (the moment-to-moment
 *     trading surfaces). Options Analytics / Strategy Selector stay
 *     their own rows here for now, even though the plan's end state
 *     folds them into Option Basis as internal sections -- that fold is
 *     LC-3's job (needs options.js itself restructured); removing their
 *     rail entry before that UI exists would strand real functionality
 *     with no way to reach it. Deliberate sequencing, not an oversight.
 *   - Manage: capital + paper-trade lifecycle.
 *   - Performance: Optimizer, Diagnostics, Signal Integrity, and Alert
 *     Log collapsed into ONE rail row with an internal sub-tab bar
 *     (see PERFORMANCE_TABS/showPane below) -- all four are "is this
 *     actually working" evidence, not trading-decision surfaces, and
 *     genuinely overlapped in purpose (walk-forward/ML/Kelly/ablation,
 *     precision proof, outcome ledger, delivery audit are all flavors
 *     of the same question). Their underlying panel classes/containers
 *     in index.html/app.js are UNCHANGED -- only the rail entry point
 *     collapses, not the panels themselves.
 *   - Context: unchanged, already coherent (News/Events/Ask Infusion).
 * Watchlist's rail row is retired outright (not moved) -- its content
 * ("Pre-Breakout Watch") is fully covered by the primary view's own
 * Watch Strip plus the Breakout Radar and EBIE Verdict panes (confirmed
 * duplication: same /api/prebreakout source). Its underlying
 * WatchlistPanel/#watchlistBodyV2 mount is left completely untouched in
 * index.html/app.js (Classic still uses the same class) -- it simply
 * has no rail button pointing at it anymore, the same "mounted but
 * unreached" trade-off this codebase already accepts elsewhere (every
 * panel mounts and polls regardless of which one is currently visible).
 */
// Note: 'Performance' is deliberately NOT a real group here -- it has no
// ROWS entries of its own (see PERFORMANCE_TABS below); its single rail
// button is injected directly after 'Manage' in initRailV2() instead.
const GROUPS = ['Trade', 'Manage', 'Context'];
const ROWS = [
  // Phase R3 -- Stock Breakout Radar, first per the reference plan's
  // "first and largest panel" framing.
  { key: 'breakout-radar', icon: '📡', label: 'Breakout Radar', group: 'Trade' },
  { key: 'ebie-verdict', icon: '⚡', label: 'EBIE Verdict', group: 'Trade' },
  { key: 'stock-detail', icon: '◎', label: 'Stock Detail', group: 'Trade' },
  // Clean Sweep LC-3: Options Analytics / Strategy Selector no longer
  // have their own rail rows -- they're now internal collapsible
  // sections inside Option Basis itself (the reference screenshot's own
  // "one option-chain-centric panel" shape). Their panel classes/mount
  // containers are unchanged, just relocated in index.html.
  { key: 'option-basis', icon: '⌘', label: 'Option Basis', group: 'Trade' },
  { key: 'triggers', icon: '▲', label: 'Triggers', group: 'Trade' },
  { key: 'risk', icon: '◈', label: 'Risk', group: 'Manage' },
  { key: 'execution', icon: '⚙', label: 'Execution', group: 'Manage' },
  { key: 'journal', icon: '▤', label: 'Journal', group: 'Manage' },
  { key: 'safety', icon: '🛡', label: 'Safety', group: 'Manage' },
  { key: 'news', icon: '📰', label: 'News', group: 'Context' },
  { key: 'events', icon: '📅', label: 'Events', group: 'Context' },
  { key: 'ask-infusion', icon: '✦', label: 'Ask Infusion', group: 'Context' },
];

// The 4 panes merged under the single "Performance" rail row. Each
// entry's `key` still matches its real, untouched data-pane-v2 value in
// index.html -- only the rail navigation collapses, not the panes.
const PERFORMANCE_TABS = [
  { key: 'optimizer', label: 'Optimizer' },
  { key: 'diagnostics', label: 'Diagnostics' },
  { key: 'signal-integrity', label: 'Signal Integrity' },
  { key: 'alerts', label: 'Alert Log' },
];

const PERFORMANCE_KEYS = new Set(PERFORMANCE_TABS.map((t) => t.key));

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
    ${g === 'Manage' ? `
    <div class="ifx-rail-group">
      <span class="ifx-rail-group-label">Performance</span>
      <button type="button" class="ifx-rail-item" data-rail-key="performance">
        <span class="ifx-rail-icon">📊</span><span class="ifx-rail-item-label">Performance</span>
        <span class="ifx-rail-chevron">→</span>
      </button>
    </div>` : ''}
  `).join('');

  // Sub-tab bar for the merged Performance pane -- built once, inserted
  // right after the "← Back" row, hidden unless the Performance cluster
  // is the active pane. Reuses .ifx-btn (the existing shared component)
  // for the tab buttons rather than inventing a second tab-bar system.
  const perfBar = document.createElement('div');
  perfBar.id = 'performanceSubTabsV2';
  perfBar.className = 'ifx-perf-subtabs';
  perfBar.style.display = 'none';
  perfBar.innerHTML = PERFORMANCE_TABS.map((t, i) =>
    `<button type="button" class="ifx-btn ifx-btn--on-paper" data-perf-key="${t.key}">${t.label}</button>`
  ).join('');
  const sectionLabel = paneView.querySelector('.ifx-section-label');
  sectionLabel?.insertAdjacentElement('afterend', perfBar);

  function showPane(key) {
    if (key === 'breakout-radar') {
      showPrimary();
      document.getElementById('breakoutRadarV2')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }

    const isPerf = PERFORMANCE_KEYS.has(key) || key === 'performance';
    const activeKey = isPerf && key === 'performance' ? PERFORMANCE_TABS[0].key : key;
    const row = isPerf
      ? { label: 'Performance' }
      : ROWS.find((r) => r.key === key);
    if (!row) return;

    primary.style.display = 'none';
    paneView.style.display = 'block';
    paneTitle.textContent = row.label;
    paneView.querySelectorAll('[data-pane-v2]').forEach((el) => {
      el.style.display = el.dataset.paneV2 === activeKey ? 'block' : 'none';
    });
    rail.querySelectorAll('.ifx-rail-item').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.railKey === (isPerf ? 'performance' : key));
    });

    perfBar.style.display = isPerf ? 'flex' : 'none';
    if (isPerf) {
      perfBar.querySelectorAll('[data-perf-key]').forEach((btn) => {
        btn.classList.toggle('ifx-btn--active', btn.dataset.perfKey === activeKey);
      });
    }
  }

  function showPrimary() {
    paneView.style.display = 'none';
    primary.style.display = 'block';
    perfBar.style.display = 'none';
    rail.querySelectorAll('.ifx-rail-item').forEach((btn) => btn.classList.remove('active'));
  }

  rail.querySelectorAll('[data-rail-key]').forEach((btn) => {
    btn.addEventListener('click', () => showPane(btn.dataset.railKey));
  });
  perfBar.querySelectorAll('[data-perf-key]').forEach((btn) => {
    btn.addEventListener('click', () => showPane(btn.dataset.perfKey));
  });
  backBtn?.addEventListener('click', showPrimary);

  return { showPane, showPrimary };
}
