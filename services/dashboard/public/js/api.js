/**
 * ============================================================================
 *  Infusion Trading Command Center — REST API Client
 * ============================================================================
 *
 *  Polling-based API client that periodically fetches data from the backend
 *  and notifies registered listeners.  Uses the browser Fetch API.
 *
 *  Architecture:
 *    ┌─────────┐   subscribe('/api/signals', cb, 2000)
 *    │ Widget A │ ──────────────────────────┐
 *    └─────────┘                            ▼
 *    ┌─────────┐              ┌──────────────────────┐     fetch()     ┌─────────┐
 *    │ Widget B │ ──────────▶ │     ApiClient         │ ─────────────▶ │  Nginx  │
 *    └─────────┘              │  listeners / polling  │ ◀───────────── │ :3000   │
 *    ┌─────────┐              └──────────────────────┘   JSON resp     └─────────┘
 *    │ Widget C │ ────────────────────────┘                              ▼ proxy
 *    └─────────┘                                                      ┌─────────┐
 *                                                                     │ Backend  │
 *                                                                     │ :8000    │
 *                                                                     └─────────┘
 *
 *  Usage:
 *    import { api } from './api.js';
 *
 *    const unsub = api.subscribe('/api/signals', (data) => {
 *      renderSignals(data);
 *    }, 2000);
 *
 *    // Later:
 *    unsub();
 *
 *  The client is a singleton — all widgets share one polling loop per
 *  endpoint, reducing duplicate network requests.
 *
 * ============================================================================
 */

/* ────────────────────────────────────────────────────────────────────────── */
/*  ApiClient                                                               */
/* ────────────────────────────────────────────────────────────────────────── */

class ApiClient {
  /**
   * @param {string} baseUrl — prepended to every endpoint.
   *   Defaults to '' so requests go to the same origin (Nginx on :3000).
   */
  constructor(baseUrl = '') {
    /** @type {string} */
    this.baseUrl = baseUrl;

    /**
     * Map of endpoint → Set<callback>.
     * Each callback receives the parsed JSON data.
     * @type {Map<string, Set<Function>>}
     */
    this._listeners = new Map();

    /**
     * Map of endpoint → { intervalId, intervalMs }.
     * Only one polling timer exists per endpoint, regardless of how many
     * subscribers are registered.
     * @type {Map<string, {intervalId: number, intervalMs: number}>}
     */
    this._polls = new Map();

    /**
     * Response cache: endpoint → { data, fetchedAt, ok }.
     * `ok` tracks whether the last fetch succeeded — useful for health UIs.
     * @type {Map<string, {data: any, fetchedAt: number, ok: boolean}>}
     */
    this._cache = new Map();

    /**
     * Per-endpoint error counts (consecutive).  Reset to 0 on success.
     * @type {Map<string, number>}
     */
    this._errorCounts = new Map();
  }

  /* ── Public API ────────────────────────────────────────────────────────── */

  /**
   * Subscribe to an endpoint with automatic polling.
   *
   * If this is the first subscriber for the endpoint a polling timer is
   * started immediately.  The callback fires once right away with any
   * cached data, then on every subsequent successful fetch.
   *
   * @param {string}   endpoint   — e.g. '/api/signals'
   * @param {Function} callback   — receives parsed JSON
   * @param {number}   intervalMs — polling interval (default 2 000 ms)
   * @returns {Function} unsubscribe — call to stop receiving updates
   */
  subscribe(endpoint, callback, intervalMs = 2000) {
    // Register listener
    if (!this._listeners.has(endpoint)) {
      this._listeners.set(endpoint, new Set());
    }
    this._listeners.get(endpoint).add(callback);

    // Deliver cached data immediately (if available) so the UI isn't blank
    const cached = this._cache.get(endpoint);
    if (cached && cached.ok) {
      try { callback(cached.data); } catch (e) {
        console.error(`[api] listener error (cached) for ${endpoint}:`, e);
      }
    }

    // Start polling if this is the first subscriber
    if (!this._polls.has(endpoint)) {
      this._startPolling(endpoint, intervalMs);
    }

    // Return an unsubscribe function
    return () => {
      const subs = this._listeners.get(endpoint);
      if (subs) {
        subs.delete(callback);
        // If no more listeners, stop polling to save bandwidth
        if (subs.size === 0) {
          this._stopPolling(endpoint);
          this._listeners.delete(endpoint);
        }
      }
    };
  }

  /**
   * One-shot fetch.  Does NOT start polling.
   *
   * Returns the parsed JSON, or `null` on error.
   *
   * @param {string} endpoint
   * @returns {Promise<any|null>}
   */
  async fetch(endpoint) {
    try {
      const url = this.baseUrl + endpoint;
      const res = await globalThis.fetch(url);

      if (!res.ok) {
        console.warn(`[api] HTTP ${res.status} for ${endpoint}`);
        this._recordError(endpoint);
        return null;
      }

      const data = await res.json();
      this._cache.set(endpoint, { data, fetchedAt: Date.now(), ok: true });
      this._errorCounts.set(endpoint, 0);
      return data;
    } catch (err) {
      console.warn(`[api] fetch failed for ${endpoint}:`, err.message);
      this._recordError(endpoint);
      return null;
    }
  }

  /**
   * One-shot JSON POST. Secrets remain server-side; browser sends only actions.
   */
  async post(endpoint, payload = {}) {
    try {
      const url = this.baseUrl + endpoint;
      const res = await globalThis.fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        console.warn(`[api] HTTP ${res.status} for POST ${endpoint}`);
        try {
          return await res.json();
        } catch (_) {
          return { ok: false, error: `HTTP ${res.status}` };
        }
      }
      return await res.json();
    } catch (err) {
      console.warn(`[api] POST failed for ${endpoint}:`, err.message);
      return null;
    }
  }

  /**
   * Get the last-known health status of an endpoint.
   *
   * @param {string} endpoint
   * @returns {{ ok: boolean, fetchedAt: number|null, errors: number }}
   */
  status(endpoint) {
    const cached = this._cache.get(endpoint);
    return {
      ok:        cached ? cached.ok : false,
      fetchedAt: cached ? cached.fetchedAt : null,
      errors:    this._errorCounts.get(endpoint) || 0,
    };
  }

  /**
   * Stop all polling and clear all listeners.
   * Call this when the dashboard is being torn down.
   */
  destroy() {
    for (const endpoint of this._polls.keys()) {
      this._stopPolling(endpoint);
    }
    this._listeners.clear();
    this._cache.clear();
    this._errorCounts.clear();
  }

  /* ── Internal ──────────────────────────────────────────────────────────── */

  /**
   * Start the polling timer for an endpoint.
   * Fires an immediate fetch, then schedules at `intervalMs`.
   *
   * @private
   * @param {string} endpoint
   * @param {number} intervalMs
   */
  _startPolling(endpoint, intervalMs) {
    // Fire immediately so the UI updates without waiting a full interval
    this._poll(endpoint);

    const intervalId = setInterval(() => this._poll(endpoint), intervalMs);
    this._polls.set(endpoint, { intervalId, intervalMs });
  }

  /**
   * Stop polling for an endpoint.
   *
   * @private
   * @param {string} endpoint
   */
  _stopPolling(endpoint) {
    const entry = this._polls.get(endpoint);
    if (entry) {
      clearInterval(entry.intervalId);
      this._polls.delete(endpoint);
    }
  }

  /**
   * Fetch data for an endpoint and notify all registered listeners.
   *
   * Errors are logged and tracked but never thrown — the dashboard must
   * keep running even when the backend is unreachable.
   *
   * @private
   * @param {string} endpoint
   */
  async _poll(endpoint) {
    const data = await this.fetch(endpoint);

    // Only notify on successful fetch
    if (data === null) return;

    const listeners = this._listeners.get(endpoint);
    if (!listeners) return;

    for (const cb of listeners) {
      try {
        cb(data);
      } catch (err) {
        console.error(`[api] listener error for ${endpoint}:`, err);
      }
    }
  }

  /**
   * Record a fetch error for an endpoint.
   * Updates the cache entry to mark `ok: false` while preserving stale data.
   *
   * @private
   * @param {string} endpoint
   */
  _recordError(endpoint) {
    const count = (this._errorCounts.get(endpoint) || 0) + 1;
    this._errorCounts.set(endpoint, count);

    // Mark cached entry as stale
    const cached = this._cache.get(endpoint);
    if (cached) {
      cached.ok = false;
    } else {
      this._cache.set(endpoint, { data: null, fetchedAt: Date.now(), ok: false });
    }

    // Log escalating warnings — avoid flooding the console
    if (count === 1 || count === 5 || count % 20 === 0) {
      console.warn(`[api] ${endpoint} — ${count} consecutive error(s)`);
    }
  }
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Singleton Export                                                        */
/* ────────────────────────────────────────────────────────────────────────── */

/** Global API client instance shared across all dashboard modules. */
export const api = new ApiClient();
