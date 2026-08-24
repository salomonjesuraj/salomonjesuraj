/**
 * Wires the always-visible #dashboardModeSwitch button (index.html) to
 * dashboard-mode.js. Kept separate from dashboard-mode.js itself (which
 * only ever flips the attribute/localStorage, no DOM) and separate from
 * header.js (whose DOM lives inside .dashboard and would disappear along
 * with it in New mode) -- this is the one control guaranteed visible in
 * both modes.
 */
import { dashboardMode } from './dashboard-mode.js';

function paint(btn) {
  const tryingNew = dashboardMode.current === 'classic';
  btn.textContent = tryingNew ? 'Open New Dashboard' : 'Classic Fallback';
}

export function initModeSwitch() {
  const btn = document.getElementById('dashboardModeSwitch');
  if (!btn) return;
  paint(btn);
  btn.addEventListener('click', () => {
    dashboardMode.toggle();
    paint(btn);
  });
}
