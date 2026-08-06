/**
 * ============================================================================
 *  Infusion Trading Command Center — Virtual Scroll Engine
 * ============================================================================
 *
 *  Renders large data tables (500-1000+ rows) by only materialising DOM
 *  nodes for the visible viewport plus a small overscan buffer.
 *
 *  Architecture:
 *
 *    ┌────────────────────────────────────────────┐  ← container (overflow-y: auto)
 *    │ ┌────────────────────────────────────────┐ │
 *    │ │ vscroll-spacer                         │ │  ← total height = items.length × rowHeight
 *    │ │                                        │ │     (gives scrollbar correct proportions)
 *    │ │   ┌──────────────────────────────────┐ │ │
 *    │ │   │ vscroll-content                  │ │ │  ← translateY to match scroll offset
 *    │ │   │  ┌── row 14 ──────────────────┐  │ │ │     only contains visible + overscan rows
 *    │ │   │  ├── row 15 ──────────────────┤  │ │ │
 *    │ │   │  ├── row 16 ──────────────────┤  │ │ │
 *    │ │   │  └── row 17 ──────────────────┘  │ │ │
 *    │ │   └──────────────────────────────────┘ │ │
 *    │ └────────────────────────────────────────┘ │
 *    └────────────────────────────────────────────┘
 *
 *  Performance targets:
 *    • 1000 rows: initial render < 16ms
 *    • Scroll:    re-render      < 8ms
 *    • Memory:    ~40-50 DOM row nodes in the tree at any time
 *
 *  Features:
 *    • Dirty tracking per key — only re-renders rows whose data changed
 *    • Flash animation class ('vscroll-flash') auto-removed after 300ms
 *    • Row click events via event delegation (single listener)
 *    • Scroll debouncing via requestAnimationFrame
 *
 *  Usage:
 *    import { VirtualScroll } from './virtual-scroll.js';
 *
 *    const vs = new VirtualScroll(document.getElementById('signal-list'), {
 *      rowHeight: 28,
 *      overscan: 10,
 *      renderRow: (item, idx) => `<div class="row">${item.symbol}</div>`,
 *      keyFn: (item) => item.symbol,
 *    });
 *
 *    vs.setData(signals);   // full replacement
 *    vs.updateItem('INFY', newData);  // surgical update
 *
 * ============================================================================
 */

/* ── Constants ───────────────────────────────────────────────────────────── */

/** Duration (ms) the flash highlight class remains on a row */
const FLASH_DURATION_MS = 300;

/** Minimum scroll delta (px) before we bother re-rendering */
const SCROLL_THRESHOLD_PX = 4;

/* ────────────────────────────────────────────────────────────────────────── */
/*  VirtualScroll                                                           */
/* ────────────────────────────────────────────────────────────────────────── */

export class VirtualScroll {
  /**
   * @param {HTMLElement} container — the scrollable viewport element.
   *   Must be a block element with a defined height (CSS) and overflow-y: auto.
   *
   * @param {object} options
   * @param {number}   options.rowHeight  — fixed pixel height per row (default 28)
   * @param {number}   options.overscan   — extra rows rendered above/below viewport (default 10)
   * @param {(item: any, index: number) => string} options.renderRow
   *   — function that returns the HTML string for a single row
   * @param {(item: any) => string} options.keyFn
   *   — function that returns a unique key for each item (used for dirty tracking)
   * @param {((item: any, index: number) => void)|null} [options.onRowClick]
   *   — optional click handler; receives the item and its index
   */
  constructor(container, {
    rowHeight = 28,
    overscan  = 10,
    renderRow,
    keyFn,
    onRowClick = null,
  }) {
    if (!container) throw new Error('[VirtualScroll] container is required');
    if (!renderRow)  throw new Error('[VirtualScroll] renderRow is required');
    if (!keyFn)      throw new Error('[VirtualScroll] keyFn is required');

    /** @type {HTMLElement} */
    this._container = container;

    /** @type {number} */
    this._rowHeight = rowHeight;

    /** @type {number} */
    this._overscan = overscan;

    /** @type {(item: any, index: number) => string} */
    this._renderRow = renderRow;

    /** @type {(item: any) => string} */
    this._keyFn = keyFn;

    /** @type {((item: any, index: number) => void)|null} */
    this._onRowClick = onRowClick;

    /* ── Data ─────────────────────────────────────────────────────────── */

    /** Full data array (reference — not cloned for performance) */
    this._items = [];

    /** Map of key → item for O(1) lookups */
    this._keyMap = new Map();

    /** Set of keys whose data has changed since last render */
    this._dirtyKeys = new Set();

    /* ── Render state ─────────────────────────────────────────────────── */

    /** Currently rendered range: [startIdx, endIdx) */
    this._rangeStart = 0;
    this._rangeEnd   = 0;

    /** Last known scrollTop — used to detect meaningful scroll deltas */
    this._lastScrollTop = 0;

    /** Whether a rAF render is already scheduled */
    this._rafPending = false;

    /** Flash timers: key → timeoutId */
    this._flashTimers = new Map();

    /* ── DOM scaffold ─────────────────────────────────────────────────── */

    this._buildDOM();

    /* ── Event bindings ───────────────────────────────────────────────── */

    this._onScrollBound = this._onScroll.bind(this);
    this._container.addEventListener('scroll', this._onScrollBound, { passive: true });

    // Row click delegation — single listener on the content div
    if (this._onRowClick) {
      this._onClickBound = this._onClick.bind(this);
      this._contentEl.addEventListener('click', this._onClickBound);
    }
  }

  /* ── Public API ────────────────────────────────────────────────────────── */

  /**
   * Replace the entire data set.  Triggers a full re-render of the
   * visible portion.
   *
   * @param {any[]} items
   */
  setData(items) {
    this._items = items || [];
    this._rebuildKeyMap();

    // Update spacer height for scrollbar proportions
    this._spacerEl.style.height = `${this._items.length * this._rowHeight}px`;

    // Force full re-render
    this._rangeStart = -1;
    this._rangeEnd   = -1;
    this._scheduleRender();
  }

  /**
   * Update a single item by key.  If the item is currently visible it
   * will be re-rendered with a flash animation.  If not visible the
   * data is updated silently.
   *
   * @param {string} key
   * @param {any}    newData — the complete updated item
   */
  updateItem(key, newData) {
    const idx = this._items.findIndex(item => this._keyFn(item) === key);
    if (idx === -1) return;

    this._items[idx] = newData;
    this._keyMap.set(key, newData);
    this._dirtyKeys.add(key);

    // Only re-render if the row is in the visible range
    if (idx >= this._rangeStart && idx < this._rangeEnd) {
      this._scheduleRender();
    }
  }

  /**
   * Force a full re-render of the visible rows.
   */
  refresh() {
    this._rangeStart = -1;
    this._rangeEnd   = -1;
    this._scheduleRender();
  }

  /**
   * Change the fixed per-row height (e.g. a density toggle) and re-render.
   * The spacer's total height must be recalculated against the new value —
   * a stale rowHeight here desyncs scroll-position math from actual pixel
   * geometry, which silently breaks "which rows are visible" calculations.
   *
   * @param {number} newHeight
   */
  setRowHeight(newHeight) {
    if (!newHeight || newHeight === this._rowHeight) return;
    this._rowHeight = newHeight;
    this._spacerEl.style.height = `${this._items.length * this._rowHeight}px`;
    this.refresh();
  }

  /**
   * Get the indices of the currently visible row range.
   *
   * @returns {{ start: number, end: number, total: number }}
   */
  getVisibleRange() {
    return {
      start: this._rangeStart,
      end:   this._rangeEnd,
      total: this._items.length,
    };
  }

  /**
   * Tear down: remove event listeners, clear timers, empty DOM.
   */
  destroy() {
    this._container.removeEventListener('scroll', this._onScrollBound);
    if (this._onClickBound) {
      this._contentEl.removeEventListener('click', this._onClickBound);
    }

    // Clear flash timers
    for (const timerId of this._flashTimers.values()) {
      clearTimeout(timerId);
    }
    this._flashTimers.clear();

    // Clear DOM
    this._container.innerHTML = '';

    this._items   = [];
    this._keyMap.clear();
    this._dirtyKeys.clear();
  }

  /* ── Internal: DOM scaffold ────────────────────────────────────────────── */

  /**
   * Build the internal DOM structure inside the container.
   *
   * @private
   */
  _buildDOM() {
    // Ensure container has proper scroll styles
    this._container.classList.add('vscroll-viewport');

    // Spacer: sets total scrollable height
    this._spacerEl = document.createElement('div');
    this._spacerEl.className = 'vscroll-spacer';
    this._spacerEl.style.position = 'relative';
    this._spacerEl.style.width    = '100%';
    this._spacerEl.style.height   = '0px';

    // Content: contains visible rows, positioned via translateY
    this._contentEl = document.createElement('div');
    this._contentEl.className = 'vscroll-content';
    this._contentEl.style.position = 'absolute';
    this._contentEl.style.top      = '0';
    this._contentEl.style.left     = '0';
    this._contentEl.style.width    = '100%';

    this._spacerEl.appendChild(this._contentEl);
    this._container.appendChild(this._spacerEl);
  }

  /* ── Internal: Scroll Handling ─────────────────────────────────────────── */

  /**
   * Scroll event handler — schedules a re-render via requestAnimationFrame
   * if the scroll position has changed enough to shift the visible range.
   *
   * @private
   */
  _onScroll() {
    this._scheduleRender();
  }

  /**
   * Schedule a render on the next animation frame (deduped).
   *
   * @private
   */
  _scheduleRender() {
    if (this._rafPending) return;
    this._rafPending = true;
    requestAnimationFrame(() => {
      this._rafPending = false;
      this._render();
    });
  }

  /* ── Internal: Render ──────────────────────────────────────────────────── */

  /**
   * Core render routine.  Calculates the visible range and renders only
   * the rows that fall within it (plus the overscan buffer).
   *
   * @private
   */
  _render() {
    const totalItems   = this._items.length;
    if (totalItems === 0) {
      this._contentEl.innerHTML = '';
      this._rangeStart = 0;
      this._rangeEnd   = 0;
      return;
    }

    const scrollTop    = this._container.scrollTop;
    const viewportH    = this._container.clientHeight;
    const rowHeight    = this._rowHeight;
    const overscan     = this._overscan;

    // Calculate visible range
    const rawStart = Math.floor(scrollTop / rowHeight);
    const rawEnd   = Math.ceil((scrollTop + viewportH) / rowHeight);

    // Apply overscan buffer and clamp to data bounds
    const start = Math.max(0, rawStart - overscan);
    const end   = Math.min(totalItems, rawEnd + overscan);

    // Check if we have dirty items in the visible range
    const hasDirtyVisible = this._hasDirtyInRange(start, end);

    // Skip re-render if the range hasn't changed and no dirty items
    if (start === this._rangeStart &&
        end   === this._rangeEnd   &&
        !hasDirtyVisible) {
      return;
    }

    this._rangeStart = start;
    this._rangeEnd   = end;

    // Position the content div at the correct scroll offset
    const offsetY = start * rowHeight;
    this._contentEl.style.transform = `translateY(${offsetY}px)`;

    // Build HTML for visible rows
    const fragments = [];
    for (let i = start; i < end; i++) {
      const item = this._items[i];
      const key  = this._keyFn(item);
      const html = this._renderRow(item, i);

      // Wrap in a row container with data-key and data-index for delegation
      fragments.push(
        `<div class="vscroll-row" data-key="${key}" data-index="${i}" style="height:${rowHeight}px">${html}</div>`
      );
    }

    this._contentEl.innerHTML = fragments.join('');

    // Apply flash animation to dirty rows that are visible
    if (hasDirtyVisible) {
      this._applyFlash(start, end);
    }

    this._dirtyKeys.clear();
    this._lastScrollTop = scrollTop;
  }

  /**
   * Check if any dirty keys fall within [start, end).
   *
   * @private
   * @param {number} start
   * @param {number} end
   * @returns {boolean}
   */
  _hasDirtyInRange(start, end) {
    if (this._dirtyKeys.size === 0) return false;

    for (let i = start; i < end; i++) {
      const key = this._keyFn(this._items[i]);
      if (this._dirtyKeys.has(key)) return true;
    }
    return false;
  }

  /**
   * Apply flash highlight class to rows whose keys are dirty.
   * The class is removed after FLASH_DURATION_MS.
   *
   * @private
   * @param {number} start
   * @param {number} end
   */
  _applyFlash(start, end) {
    const rows = this._contentEl.querySelectorAll('.vscroll-row');

    for (const rowEl of rows) {
      const key = rowEl.getAttribute('data-key');
      if (key && this._dirtyKeys.has(key)) {
        rowEl.classList.add('vscroll-flash');

        // Clear any existing flash timer for this key
        const existingTimer = this._flashTimers.get(key);
        if (existingTimer) clearTimeout(existingTimer);

        // Remove flash class after duration
        const timerId = setTimeout(() => {
          rowEl.classList.remove('vscroll-flash');
          this._flashTimers.delete(key);
        }, FLASH_DURATION_MS);

        this._flashTimers.set(key, timerId);
      }
    }
  }

  /* ── Internal: Click Delegation ────────────────────────────────────────── */

  /**
   * Handle click events on the content div.  Walks up from the event
   * target to find the nearest `.vscroll-row`, reads its data-index,
   * and calls the onRowClick handler.
   *
   * @private
   * @param {MouseEvent} ev
   */
  _onClick(ev) {
    if (!this._onRowClick) return;

    // Walk up from click target to find the row container
    let el = ev.target;
    while (el && el !== this._contentEl) {
      if (el.classList.contains('vscroll-row')) {
        const idx = parseInt(el.getAttribute('data-index'), 10);
        if (!isNaN(idx) && idx >= 0 && idx < this._items.length) {
          this._onRowClick(this._items[idx], idx);
        }
        return;
      }
      el = el.parentElement;
    }
  }

  /* ── Internal: Key Map ─────────────────────────────────────────────────── */

  /**
   * Rebuild the key → item map from scratch.
   *
   * @private
   */
  _rebuildKeyMap() {
    this._keyMap.clear();
    for (const item of this._items) {
      this._keyMap.set(this._keyFn(item), item);
    }
  }
}
