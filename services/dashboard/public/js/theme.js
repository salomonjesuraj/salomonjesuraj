/**
 * Theme controller — dark/light toggle for the v2 design system (theme.css).
 *
 * Applies `data-theme="dark"|"light"` on <html>, persisted to localStorage.
 * Defaults to the OS preference (prefers-color-scheme) on first visit, then
 * whatever the user last chose. A tiny module by design — the actual color
 * values live entirely in theme.css's [data-theme] blocks, this only ever
 * flips the attribute.
 */

const STORAGE_KEY = 'infusion:theme';

function systemPrefersLight() {
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
}

function initialTheme() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'dark' || saved === 'light') return saved;
  } catch (e) { /* ignore */ }
  return systemPrefersLight() ? 'light' : 'dark';
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
