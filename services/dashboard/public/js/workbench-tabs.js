/**
 * Tiny tab controller for merged breakout/option workbench.
 */
export class WorkbenchTabs {
  constructor(containerEl) {
    this._el = containerEl;
  }

  init() {
    if (!this._el) return;
    this._el.querySelectorAll('[data-tab]').forEach(btn => {
      btn.addEventListener('click', () => this._activate(btn.dataset.tab));
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

  destroy() {}
}
