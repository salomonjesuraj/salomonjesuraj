/**
 * Left rail — Phase N5. A flat, 1:1 restyle of the existing 16-tab "More"
 * drawer (workbench-tabs.js's data-tab/data-pane mechanism), not a rebuild:
 * same 15 destinations (screener excluded -- it's already the primary New
 * shell view, not tucked away), same panel classes underneath, just a
 * vertical list instead of a horizontal tab bar and a full-width swap
 * instead of a bottom drawer. Grouping the 15 rows into fewer, categorized
 * rows is a deliberately separate, later cosmetic pass -- see the plan's
 * "ship flat first" decision.
 */
const ROWS = [
  { key: 'watchlist', icon: '◑', label: 'Watchlist' },
  { key: 'option-basis', icon: '⌘', label: 'Option Basis' },
  { key: 'risk', icon: '◈', label: 'Risk' },
  { key: 'stock-detail', icon: '◎', label: 'Stock Detail' },
  { key: 'triggers', icon: '▲', label: 'Triggers' },
  { key: 'news', icon: '📰', label: 'News' },
  { key: 'events', icon: '📅', label: 'Events' },
  { key: 'journal', icon: '▤', label: 'Journal' },
  { key: 'execution', icon: '⚙', label: 'Execution' },
  { key: 'safety', icon: '🛡', label: 'Safety' },
  { key: 'alerts', icon: '🔔', label: 'Alert Log' },
  { key: 'diagnostics', icon: '📊', label: 'Diagnostics' },
  { key: 'options-analytics', icon: '⌗', label: 'Options Analytics' },
  { key: 'optimizer', icon: '↻', label: 'Optimizer' },
  { key: 'ask-infusion', icon: '✦', label: 'Ask Infusion' },
  { key: 'signal-integrity', icon: '✓', label: 'Signal Integrity' },
];

export function initRailV2() {
  const rail = document.getElementById('railV2');
  const primary = document.getElementById('primaryViewV2');
  const paneView = document.getElementById('paneViewV2');
  const paneTitle = document.getElementById('paneTitleV2');
  const backBtn = document.getElementById('backToPrimaryV2');
  if (!rail || !primary || !paneView) return;

  rail.innerHTML = ROWS.map((r) =>
    `<button type="button" class="ifx-rail-item" data-rail-key="${r.key}">
      <span class="ifx-rail-icon">${r.icon}</span><span class="ifx-rail-item-label">${r.label}</span>
      <span class="ifx-rail-chevron">→</span>
    </button>`
  ).join('');

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
