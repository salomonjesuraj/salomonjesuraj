/**
 * Dashboard-mode controller — Classic / New shell toggle.
 *
 * Applies `data-dashboard-mode="classic"|"new"` on <html>, persisted to
 * localStorage. Defaults to "classic" on first visit (never surprise a
 * daily-use live tool with a different layout on its own) -- "new" is an
 * explicit opt-in via the header toggle, kept that way until the New shell
 * has proven itself against real market conditions. Mirrors theme.js's
 * pattern exactly: a tiny module, no color/layout values live here, this
 * only ever flips the attribute that theme.css's [data-dashboard-mode]
 * blocks key off.
 */

const STORAGE_KEY = 'infusion:dashboard-mode';

function initialMode() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'classic' || saved === 'new') return saved;
  } catch (e) { /* ignore */ }
  return 'classic';
}

export const dashboardMode = {
  current: initialMode(),

  apply(mode) {
    this.current = mode;
    document.documentElement.setAttribute('data-dashboard-mode', mode);
    try { localStorage.setItem(STORAGE_KEY, mode); } catch (e) { /* ignore */ }
    document.dispatchEvent(new CustomEvent('dashboard-mode:change', { detail: { mode } }));
  },

  toggle() {
    this.apply(this.current === 'classic' ? 'new' : 'classic');
  },

  init() {
    this.apply(this.current);
  },
};
