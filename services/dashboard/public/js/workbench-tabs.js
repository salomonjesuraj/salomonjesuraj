/**
 * Tiny tab controller for the "More" drawer (formerly the merged
 * breakout/option workbench).
 */
const SECTION_LAYOUT_KEY = 'infusion:section-layout:v1'; // same key section-controls.js uses

export class WorkbenchTabs {
  constructor(containerEl) {
    this._el = containerEl;
  }

  init() {
    if (!this._el) return;
    this._el.querySelectorAll('[data-tab]').forEach(btn => {
      btn.addEventListener('click', () => {
        this._activate(btn.dataset.tab);
        this._ensureExpanded(btn);
      });
    });
  }

  _activate(tab) {
    document.querySelectorAll('.workbench-tab').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    document.querySelectorAll('.workbench-pane').forEach(pane => {
      pane.classList.toggle('active', pane.dataset.pane === tab);
    });
  }

  // Picking a tab while the drawer is collapsed should actually open it —
  // otherwise the click looks like it did nothing. Mirrors
  // section-controls.js's collapse/expand persistence so state stays in
  // sync regardless of which control the user clicked.
  _ensureExpanded(btn) {
    const section = btn.closest('[data-collapsible-section]');
    if (!section || !section.classList.contains('section-collapsed')) return;
    section.classList.remove('section-collapsed');
    section.classList.add('section-expanded');
    const key = section.dataset.collapsibleSection;
    if (!key) return;
    try {
      const state = JSON.parse(localStorage.getItem(SECTION_LAYOUT_KEY) || '{}') || {};
      state[key] = 'expanded';
      localStorage.setItem(SECTION_LAYOUT_KEY, JSON.stringify(state));
    } catch (_) {}
  }

  destroy() {}
}
