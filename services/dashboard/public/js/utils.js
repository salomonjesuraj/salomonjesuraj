/**
 * ============================================================================
 *  Infusion Trading Command Center — Utility Functions
 * ============================================================================
 *
 *  Pure, side-effect-free helpers used across every module in the dashboard.
 *  Categories:
 *    1. Price / Volume / Percentage formatting (Indian conventions)
 *    2. IST clock & market-session classification
 *    3. Grade & regime CSS-class mapping
 *    4. General-purpose: debounce, escapeHtml, clamp, sort comparators
 *
 *  All functions are individually exported — import only what you need:
 *    import { formatPrice, istClock } from './utils.js';
 *
 * ============================================================================
 */

/* ── IST Constants ───────────────────────────────────────────────────────── */

/** IST is UTC +05:30, i.e. +330 minutes */
const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;  // 19_800_000

/**
 * Market session boundaries (hours * 100 + minutes for fast comparison).
 *
 * CLOSING ends at 15:15, not 15:30 -- SEBI's Closing Auction Session (CAS),
 * effective 3 Aug 2026, stops continuous trading of F&O-eligible stocks'
 * cash equity at 15:15 (halt, then restricted auction order entry through
 * 15:30, single-price match 15:30-15:35). F&O contracts themselves still
 * trade normally to 15:40, but every feature this system displays is
 * derived from the underlying stock's own tick feed. Matches
 * scanner/suppression.py's _current_session() -- keep both in sync.
 */
const SESSION = {
  PRE_OPEN  :  915,   // 09:15 — market opens
  MID_MORN  : 1000,   // 10:00
  MIDDAY    : 1200,   // 12:00
  CLOSING   : 1400,   // 14:00
  CAS_START : 1515,   // 15:15 — continuous trading stops for F&O stocks (CAS)
  CLOSE     : 1530,   // 15:30 — CAS auction order entry ends
};

/* ────────────────────────────────────────────────────────────────────────── */
/*  1.  FORMATTING                                                          */
/* ────────────────────────────────────────────────────────────────────────── */

/**
 * Format a number as an Indian-style price string.
 *
 *   formatPrice(123456.7)  → "₹1,23,456.70"
 *   formatPrice(0)         → "₹0.00"
 *   formatPrice(null)      → "—"
 *
 * Indian convention: first group of 3, then groups of 2 from the right.
 *
 * @param {number|null|undefined} price
 * @returns {string}
 */
export function formatPrice(price) {
  if (price == null || isNaN(price)) return '—';

  const num = Number(price);
  const sign = num < 0 ? '-' : '';
  const abs = Math.abs(num);

  // Split into integer and decimal portions
  const [intPart, decPart = '00'] = abs.toFixed(2).split('.');

  // Indian grouping: last 3 digits, then groups of 2
  let formatted = '';
  const len = intPart.length;
  if (len <= 3) {
    formatted = intPart;
  } else {
    // Last three digits
    formatted = intPart.slice(len - 3);
    let remaining = intPart.slice(0, len - 3);
    // Groups of two from the right
    while (remaining.length > 2) {
      formatted = remaining.slice(remaining.length - 2) + ',' + formatted;
      remaining = remaining.slice(0, remaining.length - 2);
    }
    if (remaining.length > 0) {
      formatted = remaining + ',' + formatted;
    }
  }

  return `${sign}₹${formatted}.${decPart}`;
}

/**
 * Format volume into compact Indian notation.
 *
 *   formatVolume(12345678)  → "1.23Cr"
 *   formatVolume(4530000)   → "45.30L"
 *   formatVolume(8500)      → "8.50K"
 *   formatVolume(750)       → "750"
 *   formatVolume(null)      → "—"
 *
 * Thresholds:
 *   ≥ 1 Crore (1e7)  → Cr
 *   ≥ 1 Lakh  (1e5)  → L
 *   ≥ 1000           → K
 *   Below 1000       → raw number
 *
 * @param {number|null|undefined} vol
 * @returns {string}
 */
export function formatVolume(vol) {
  if (vol == null || isNaN(vol)) return '—';

  const num = Math.abs(Number(vol));

  if (num >= 1e7) return (num / 1e7).toFixed(2) + 'Cr';
  if (num >= 1e5) return (num / 1e5).toFixed(2) + 'L';
  if (num >= 1e3) return (num / 1e3).toFixed(2) + 'K';
  return String(Math.round(num));
}

/**
 * Format a percentage with explicit sign.
 *
 *   formatPct(2.456)     → "+2.46%"
 *   formatPct(-1.2, 1)   → "-1.2%"
 *   formatPct(0)          → "0.00%"
 *   formatPct(null)       → "—"
 *
 * @param {number|null|undefined} pct
 * @param {number} decimals — decimal places (default 2)
 * @returns {string}
 */
export function formatPct(pct, decimals = 2) {
  if (pct == null || isNaN(pct)) return '—';

  const num = Number(pct);
  const sign = num > 0 ? '+' : '';
  return `${sign}${num.toFixed(decimals)}%`;
}

/**
 * Format relative volume multiplier.
 *
 *   formatRelVol(2.34)  → "2.3x"
 *   formatRelVol(0.5)   → "0.5x"
 *   formatRelVol(null)  → "—"
 *
 * @param {number|null|undefined} rv
 * @returns {string}
 */
export function formatRelVol(rv) {
  if (rv == null || isNaN(rv)) return '—';
  return Number(rv).toFixed(1) + 'x';
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  2.  TIME & MARKET SESSION                                               */
/* ────────────────────────────────────────────────────────────────────────── */

/**
 * Time-ago label from a UNIX microsecond timestamp.
 *
 *   timeAgo(Date.now() * 1000 - 130_000_000)  → "2m"
 *
 * Buckets: <60s → "Xs", <60m → "Xm", <24h → "Xh", else "Xd"
 *
 * @param {number} timestampUs — UNIX timestamp in microseconds
 * @returns {string}
 */
export function timeAgo(timestampUs) {
  if (!timestampUs) return '—';

  const nowMs = Date.now();
  const thenMs = timestampUs / 1000;          // µs → ms
  const diffSec = Math.max(0, (nowMs - thenMs) / 1000);

  if (diffSec < 60)   return `${Math.floor(diffSec)}s`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h`;
  return `${Math.floor(diffSec / 86400)}d`;
}

/**
 * Get a Date object representing the current IST time.
 *
 * Works regardless of the browser's local timezone by computing IST from UTC.
 *
 * @returns {Date}
 */
export function istNow() {
  const utcMs = Date.now();
  // Create a Date whose *UTC* fields represent IST values.
  // This lets us use getUTCHours() / getUTCMinutes() to read IST components.
  return new Date(utcMs + IST_OFFSET_MS);
}

/**
 * Current IST clock string in HH:MM:SS format (24-hour).
 *
 * @returns {string}  e.g. "14:32:07"
 */
export function istClock() {
  const d = istNow();
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  const ss = String(d.getUTCSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}

/**
 * Classify the current market session based on IST time.
 *
 * Sessions:
 *   pre_market   — before 09:15
 *   opening      — 09:15 – 10:00
 *   mid_morning  — 10:00 – 12:00
 *   midday       — 12:00 – 14:00
 *   closing      — 14:00 – 15:15
 *   cas_auction  — 15:15 – 15:30 (CAS halt + auction window, F&O stocks)
 *   post_market  — after 15:30
 *
 * @returns {'pre_market'|'opening'|'mid_morning'|'midday'|'closing'|'cas_auction'|'post_market'}
 */
export function currentSession() {
  const d = istNow();
  const hhmm = d.getUTCHours() * 100 + d.getUTCMinutes();

  if (hhmm < SESSION.PRE_OPEN)  return 'pre_market';
  if (hhmm < SESSION.MID_MORN)  return 'opening';
  if (hhmm < SESSION.MIDDAY)    return 'mid_morning';
  if (hhmm < SESSION.CLOSING)   return 'midday';
  if (hhmm < SESSION.CAS_START) return 'closing';
  if (hhmm < SESSION.CLOSE)     return 'cas_auction';
  return 'post_market';
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  3.  GRADE & REGIME CSS CLASS MAPPING                                    */
/* ────────────────────────────────────────────────────────────────────────── */

/** @type {Record<string,string>} */
const GRADE_CLASS_MAP = {
  'A+': 'a-plus',
  'A' : 'a',
  'B+': 'b-plus',
  'B' : 'b',
  'C' : 'c',
  'D' : 'd',
};

/**
 * Map a quality grade to its corresponding CSS class name.
 *
 *   gradeClass('A+') → 'a-plus'
 *   gradeClass('X')  → 'unknown'
 *
 * @param {string} grade
 * @returns {string}
 */
export function gradeClass(grade) {
  return GRADE_CLASS_MAP[grade] || 'unknown';
}

/** @type {Record<string,string>} */
const REGIME_CLASS_MAP = {
  'risk-on' : 'risk-on',
  'risk_on' : 'risk-on',
  'neutral' : 'neutral',
  'risk-off': 'risk-off',
  'risk_off': 'risk-off',
};

/**
 * Map a market regime string to its CSS class.
 *
 *   regimeClass('risk_on')  → 'risk-on'
 *   regimeClass('neutral')  → 'neutral'
 *
 * Accepts both hyphenated and underscored forms.
 *
 * @param {string} regime
 * @returns {string}
 */
export function regimeClass(regime) {
  if (!regime) return 'unknown';
  return REGIME_CLASS_MAP[regime.toLowerCase()] || 'unknown';
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  4.  GENERAL-PURPOSE HELPERS                                             */
/* ────────────────────────────────────────────────────────────────────────── */

/**
 * Classic trailing-edge debounce.
 *
 * Returns a wrapper that delays invoking `fn` until `ms` milliseconds
 * have elapsed since the last invocation of the wrapper.
 *
 * @param {Function} fn
 * @param {number}   ms — delay in milliseconds
 * @returns {Function}
 */
export function debounce(fn, ms) {
  let timer = null;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), ms);
  };
}

/**
 * Escape HTML special characters to prevent XSS.
 *
 *   escapeHtml('<b>"hi"</b>')  → '&lt;b&gt;&quot;hi&quot;&lt;/b&gt;'
 *
 * @param {string} str
 * @returns {string}
 */
export function escapeHtml(str) {
  if (typeof str !== 'string') return '';
  return str
    .replace(/&/g,  '&amp;')
    .replace(/</g,  '&lt;')
    .replace(/>/g,  '&gt;')
    .replace(/"/g,  '&quot;')
    .replace(/'/g,  '&#39;');
}

/**
 * Clamp a numeric value to [min, max].
 *
 * @param {number} val
 * @param {number} min
 * @param {number} max
 * @returns {number}
 */
export function clamp(val, min, max) {
  return Math.max(min, Math.min(max, val));
}

/**
 * Sort comparator: numeric descending (largest first).
 *
 *   [3, 1, 2].sort(sortDesc)  → [3, 2, 1]
 *
 * @param {number} a
 * @param {number} b
 * @returns {number}
 */
export function sortDesc(a, b) {
  return b - a;
}

/**
 * Sort comparator: numeric ascending (smallest first).
 *
 *   [3, 1, 2].sort(sortAsc)  → [1, 2, 3]
 *
 * @param {number} a
 * @param {number} b
 * @returns {number}
 */
export function sortAsc(a, b) {
  return a - b;
}
