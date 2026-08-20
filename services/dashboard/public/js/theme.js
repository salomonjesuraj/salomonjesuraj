/**
 * Theme controller — dark/light toggle for the v2 design system (theme.css).
 *
 * Applies `data-theme="dark"|"light"` on <html>, persisted to localStorage.
 *
 * Clean Sweep (light-first): light is now the hard default for a
 * first-time visitor, not "whichever the OS prefers." Previously this
 * fell back to `prefers-color-scheme`, so a visitor on a dark-OS machine
 * landed in the dark console shell on their very first load -- the
 * opposite of the "light, clean, Upstox-reference" identity the
 * dashboard is meant to lead with now. Dark mode is NOT removed -- an
 * explicit toggle still reaches it and is still remembered exactly as
 * before; this only changes the untouched, no-preference-saved-yet case.
 */

const STORAGE_KEY = 'infusion:theme';

function initialTheme() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'dark' || saved === 'light') return saved;
  } catch (e) { /* ignore */ }
  return 'light';
}

export const theme = {
  current: initialTheme(),

  apply(name) {
    this.current = name;
    document.documentElement.setAttribute('data-theme', name);
    try { localStorage.setItem(STORAGE_KEY, name); } catch (e) { /* ignore */ }
    document.dispatchEvent(new CustomEvent('theme:change', { detail: { theme: name } }));
  },

  toggle() {
    this.apply(this.current === 'dark' ? 'light' : 'dark');
  },

  init() {
    this.apply(this.current);
  },
};
