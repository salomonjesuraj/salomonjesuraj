/**
 * ============================================================================
 *  Infusion Trading Command Center — WebSocket Manager
 * ============================================================================
 *
 *  Maintains a persistent WebSocket connection to the ws-gateway (proxied
 *  through Nginx at /ws → ws-gateway:8001).  Provides:
 *
 *    • Auto-reconnect with exponential backoff (1s → 2s → 4s … max 30s)
 *    • Typed listener registration: onTick, onStatus, onSignal
 *    • Connection statistics: latency, message count, reconnect count
 *    • Heartbeat detection — marks connection as stale after 10s of silence
 *
 *  Message Protocol (expected from ws-gateway):
 *
 *    tick_batch:
 *    {
 *      "type": "tick_batch",
 *      "ts": 1717567200000,          // server timestamp (ms)
 *      "data": {
 *        "RELIANCE": { "ltp": 2845.5, "volume": 1234567, ... },
 *        "INFY":     { "ltp": 1490.2, ... }
 *      }
 *    }
 *
 *    signal:
 *    {
 *      "type": "signal",
 *      "data": { ... }
 *    }
 *
 *  Usage:
 *    import { ws } from './ws.js';
 *
 *    ws.onTick((symbol, data) => {
 *      console.log(symbol, data.ltp);
 *    });
 *
 *    ws.onStatus((status) => {
 *      // 'connected' | 'connecting' | 'disconnected'
 *    });
 *
 * ============================================================================
 */

/* ── Constants ───────────────────────────────────────────────────────────── */

/** Base reconnect delay in milliseconds */
const RECONNECT_BASE_MS = 1000;

/** Maximum reconnect delay */
const RECONNECT_MAX_MS = 30_000;

/** After this many ms without a message, we consider the connection stale */
const STALE_TIMEOUT_MS = 10_000;

/* ────────────────────────────────────────────────────────────────────────── */
/*  WSManager                                                               */
/* ────────────────────────────────────────────────────────────────────────── */

class WSManager {
  /**
   * @param {string} url — WebSocket URL.  Defaults to '/ws' which Nginx
   *   upgrades and proxies to ws-gateway:8001.
   */
  constructor(url = '/ws') {
    /** @type {string} */
    this._url = url;

    /** @type {WebSocket|null} */
    this._socket = null;

    /** @type {'connected'|'connecting'|'disconnected'} */
    this._status = 'disconnected';

    /* ── Listener registries ─────────────────────────────────────────── */

    /** @type {Set<(symbol: string, data: object) => void>} */
    this._tickListeners = new Set();

    /** @type {Set<(status: string) => void>} */
    this._statusListeners = new Set();

    /** @type {Set<(signal: object) => void>} */
    this._signalListeners = new Set();

    /* ── Stats ────────────────────────────────────────────────────────── */

    /** @type {number} */
    this._reconnectCount = 0;

    /** @type {number} */
    this._messagesReceived = 0;

    /** @type {number|null} — last message timestamp (local ms) */
    this._lastMessageAt = null;

    /** @type {number} — most recent round-trip/processing latency (ms) */
    this._latencyMs = 0;

    /** @type {Set<string>} — unique symbols seen */
    this._symbolsSeen = new Set();

    /* ── Reconnect plumbing ───────────────────────────────────────────── */

    /** @type {number|null} */
    this._reconnectTimer = null;

    /** @type {number} — current backoff delay */
    this._reconnectDelay = RECONNECT_BASE_MS;

    /** @type {boolean} — if true, don't attempt reconnect (explicit close) */
    this._closed = false;

    /** @type {number|null} — stale-check timer */
    this._staleTimer = null;

    // Auto-connect on construction
    this.connect();
  }

  /* ── Public API ────────────────────────────────────────────────────────── */

  /**
   * Initiate (or re-initiate) the WebSocket connection.
   */
  connect() {
    // Don't double-connect
    if (this._socket &&
        (this._socket.readyState === WebSocket.CONNECTING ||
         this._socket.readyState === WebSocket.OPEN)) {
      return;
    }

    this._closed = false;
    this._setStatus('connecting');

    try {
      // Build absolute WS URL from relative path
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const absUrl = this._url.startsWith('ws')
        ? this._url
        : `${proto}//${location.host}${this._url}`;

      this._socket = new WebSocket(absUrl);
      this._socket.binaryType = 'arraybuffer';

      this._socket.onopen    = () => this._handleOpen();
      this._socket.onmessage = (ev) => this._handleMessage(ev);
      this._socket.onerror   = (ev) => this._handleError(ev);
      this._socket.onclose   = (ev) => this._handleClose(ev);
    } catch (err) {
      console.error('[ws] connection error:', err);
      this._scheduleReconnect();
    }
  }

  /**
   * Register a tick listener.
   *
   * Called for each symbol in every `tick_batch` message with:
   *   callback(symbol, { ltp, volume, high, low, change_pct, ... })
   *
   * @param {(symbol: string, data: object) => void} callback
   * @returns {Function} unsubscribe
   */
  onTick(callback) {
    this._tickListeners.add(callback);
    return () => this._tickListeners.delete(callback);
  }

  /**
   * Register a connection-status listener.
   *
   * Called with 'connected', 'connecting', or 'disconnected'.
   *
   * @param {(status: string) => void} callback
   * @returns {Function} unsubscribe
   */
  onStatus(callback) {
    this._statusListeners.add(callback);
    // Immediately deliver current status
    try { callback(this._status); } catch (_) { /* ignore */ }
    return () => this._statusListeners.delete(callback);
  }

  /**
   * Register a signal listener (for future WS-based signal forwarding).
   *
   * @param {(signal: object) => void} callback
   * @returns {Function} unsubscribe
   */
  onSignal(callback) {
    this._signalListeners.add(callback);
    return () => this._signalListeners.delete(callback);
  }

  /**
   * Connection statistics snapshot.
   *
   * @returns {{ connected: boolean, reconnects: number,
   *             messagesReceived: number, latencyMs: number }}
   */
  get stats() {
    return {
      connected:        this._status === 'connected',
      reconnects:       this._reconnectCount,
      messagesReceived: this._messagesReceived,
      latencyMs:        this._latencyMs,
      symbolCount:      this._symbolsSeen.size,
    };
  }

  /**
   * Gracefully close the connection.  Does NOT auto-reconnect.
   */
  close() {
    this._closed = true;
    this._clearReconnectTimer();
    this._clearStaleTimer();

    if (this._socket) {
      this._socket.onclose = null;   // prevent reconnect handler
      this._socket.close();
      this._socket = null;
    }

    this._setStatus('disconnected');
  }

  /* ── Internal: Socket Handlers ─────────────────────────────────────────── */

  /**
   * @private
   */
  _handleOpen() {
    console.info('[ws] connected');
    this._setStatus('connected');
    this._reconnectDelay = RECONNECT_BASE_MS;  // reset backoff
    this._resetStaleTimer();
  }

  /**
   * Parse and dispatch incoming messages.
   *
   * @private
   * @param {MessageEvent} ev
   */
  _handleMessage(ev) {
    this._messagesReceived++;
    this._lastMessageAt = Date.now();
    this._resetStaleTimer();

    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (err) {
      console.warn('[ws] malformed JSON:', err.message);
      return;
    }

    // Calculate latency from server timestamp (if present)
    if (msg.ts) {
      this._latencyMs = Math.max(0, Date.now() - msg.ts);
    }

    // Dispatch by message type
    switch (msg.type) {
      case 'tick_batch':
        this._dispatchTicks(msg.data);
        break;

      case 'signal':
        this._dispatchSignal(msg.data);
        break;

      default:
        // Unknown types are silently ignored — forward-compatible
        break;
    }
  }

  /**
   * @private
   * @param {Event} ev
   */
  _handleError(ev) {
    // The browser doesn't expose much error detail for WebSocket.
    // The close event that follows will trigger reconnect.
    console.warn('[ws] error event');
  }

  /**
   * @private
   * @param {CloseEvent} ev
   */
  _handleClose(ev) {
    console.info(`[ws] closed (code=${ev.code}, reason=${ev.reason || 'n/a'})`);
    this._socket = null;
    this._setStatus('disconnected');
    this._clearStaleTimer();

    if (!this._closed) {
      this._scheduleReconnect();
    }
  }

  /* ── Internal: Dispatch ────────────────────────────────────────────────── */

  /**
   * Fan-out tick data to all registered tick listeners.
   *
   * @private
   * @param {Record<string, object>} tickData — symbol → tick fields
   */
  _dispatchTicks(tickData) {
    if (!tickData || typeof tickData !== 'object') return;

    for (const [symbol, data] of Object.entries(tickData)) {
      this._symbolsSeen.add(symbol);
      for (const cb of this._tickListeners) {
        try {
          cb(symbol, data);
        } catch (err) {
          console.error(`[ws] tick listener error for ${symbol}:`, err);
        }
      }
    }
  }

  /**
   * Fan-out signal data to all registered signal listeners.
   *
   * @private
   * @param {object} signalData
   */
  _dispatchSignal(signalData) {
    for (const cb of this._signalListeners) {
      try {
        cb(signalData);
      } catch (err) {
        console.error('[ws] signal listener error:', err);
      }
    }
  }

  /* ── Internal: Status ──────────────────────────────────────────────────── */

  /**
   * Update internal status and notify all status listeners.
   *
   * @private
   * @param {'connected'|'connecting'|'disconnected'} status
   */
  _setStatus(status) {
    if (this._status === status) return;
    this._status = status;

    for (const cb of this._statusListeners) {
      try {
        cb(status);
      } catch (err) {
        console.error('[ws] status listener error:', err);
      }
    }
  }

  /* ── Internal: Reconnect ───────────────────────────────────────────────── */

  /**
   * Schedule a reconnect with exponential backoff.
   *
   * Delay sequence: 1s → 2s → 4s → 8s → 16s → 30s (capped).
   *
   * @private
   */
  _scheduleReconnect() {
    this._clearReconnectTimer();

    const delay = this._reconnectDelay;
    console.info(`[ws] reconnecting in ${delay}ms (attempt #${this._reconnectCount + 1})`);

    this._reconnectTimer = setTimeout(() => {
      this._reconnectCount++;
      this.connect();
    }, delay);

    // Exponential backoff
    this._reconnectDelay = Math.min(
      this._reconnectDelay * 2,
      RECONNECT_MAX_MS
    );
  }

  /**
   * @private
   */
  _clearReconnectTimer() {
    if (this._reconnectTimer !== null) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
  }

  /* ── Internal: Stale Detection ─────────────────────────────────────────── */

  /**
   * Reset the stale-message timer.  If we don't receive any message
   * within STALE_TIMEOUT_MS, the connection is considered unhealthy.
   *
   * @private
   */
  _resetStaleTimer() {
    this._clearStaleTimer();
    this._staleTimer = setTimeout(() => {
      if (this._status === 'connected') {
        console.warn('[ws] connection appears stale (no messages for 10s)');
        // Force reconnect — the TCP connection may still be alive but
        // the server may have stopped sending data.
        if (this._socket) {
          this._socket.close(4000, 'stale');
        }
      }
    }, STALE_TIMEOUT_MS);
  }

  /**
   * @private
   */
  _clearStaleTimer() {
    if (this._staleTimer !== null) {
      clearTimeout(this._staleTimer);
      this._staleTimer = null;
    }
  }
}

/* ────────────────────────────────────────────────────────────────────────── */
/*  Singleton Export                                                        */
/* ────────────────────────────────────────────────────────────────────────── */

/** Global WebSocket manager shared across all dashboard modules. */
export const ws = new WSManager();
