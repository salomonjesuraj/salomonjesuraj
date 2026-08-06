/**
 * Chart Panel — TradingView Lightweight Charts integration
 * Shows price chart for selected symbol with volume overlay
 */
import { formatPrice, escapeHtml } from './utils.js';
import { api } from './api.js';
import { ws } from './ws.js';

/** IST offset in seconds (+5:30 = 19800s) */
const IST_OFFSET_S = 19800;

/** Format a UNIX timestamp (seconds) to HH:MM string.
 *  Timestamps are pre-shifted by IST offset, so use UTC methods directly. */
function formatTimeIST(ts) {
  const d = new Date((ts + IST_OFFSET_S) * 1000);
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

/** Format tick marks — dates for daily, times for intraday.
 *  Timestamps are pre-shifted by IST offset. */
function formatTickMarkIST(ts, tickMarkType) {
  const d = new Date((ts + IST_OFFSET_S) * 1000);
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  const mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getUTCMonth()];
  // For intraday show time, for daily/higher show date
  if (tickMarkType <= 2) return `${dd} ${mon}`; // Year / Month / DayOfMonth
  return `${hh}:${mm}`; // Time / TimeWithSeconds
}

/** Available timeframes for the timeframe selector */
const TIMEFRAMES = ['1D', '4H', '1H', '15M', '5M'];
const DEFAULT_TIMEFRAME = '15M';

export class ChartPanel {
  constructor(containerEl, symbolBadgeEl) {
    this._containerEl = containerEl;
    this._badgeEl = symbolBadgeEl;
    this._chart = null;
    this._chartEl = null;         // dedicated div for TradingView chart
    this._candleSeries = null;
    this._volumeSeries = null;
    this._vwapLine = null;        // VWAP horizontal line series
    this._ema20Series = null;     // EMA20 line series
    this._currentSymbol = null;
    this._tickData = new Map(); // symbol -> array of {time, open, high, low, close, volume}
    this._unsubs = [];
    this._wsUnsub = null;
    this._activeTimeframe = DEFAULT_TIMEFRAME;

    // Overlay series (signal lines)
    this._entryLine    = null;
    this._stopLine     = null;
    this._targetLine   = null;
    this._activeSignal = null;

    // Toolbar toggle states
    this._toggleState = {
      candles:   true,
      volume:    true,
      vwap:     false,
      ema20:    false,
      grid:      true,
      crosshair: true,
    };
  }

  init() {
    const popup = document.getElementById('chartPopup');
    const closeBtn = document.getElementById('chartPopupClose');
    if (closeBtn && popup) {
      closeBtn.addEventListener('click', () => this._hidePopup());
      document.addEventListener('mousedown', (e) => {
        if (!popup.classList.contains('open')) return;
        if (popup.contains(e.target)) return;
        if (e.target.closest('.sym-link,.watchlist-card,.signal-card,.scanner-row')) return;
        this._hidePopup();
      });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') this._hidePopup();
      });
    }

    // Chart:load event — from signal cards and watchlist clicks
    document.addEventListener('chart:load', (e) => {
      const { symbol, signal } = e.detail || {};
      if (symbol) {
        if (typeof symbol === 'string') {
          this.loadSymbol(symbol, signal || null);
        }
      }
    });

    // Signal select — overlay on current chart
    document.addEventListener('signal:select', (e) => {
      const sig = e.detail;
      if (!sig) return;
      if (sig.symbol && sig.symbol !== this._currentSymbol) {
        this.loadSymbol(sig.symbol, sig);
      } else {
        this._drawSignalOverlay(sig);
      }
    });

    // Click on scanner row — sym-link cell
    document.addEventListener('click', (e) => {
      const symLink = e.target.closest('.sym-link');
      if (symLink) {
        const row = symLink.closest('[data-symbol]') || symLink.closest('[data-key]');
        const symbol = row?.dataset?.symbol || row?.dataset?.key;
        if (symbol) { this.loadSymbol(symbol); return; }
      }
      const vrow = e.target.closest('.vscroll-row[data-key]');
      if (vrow) this.loadSymbol(vrow.dataset.key);
    });

    // Subscribe to ticks to update chart live
    this._wsUnsub = ws.onTick((symbol, data) => {
      if (symbol === this._currentSymbol && this._candleSeries && data.ltp) {
        this._updateLiveCandle(data);
      }
    });
  }

  loadSymbol(symbol, signal = null) {
    if (!symbol || typeof symbol !== 'string') return;
    this._currentSymbol = symbol;
    this._activeSignal  = signal;

    if (this._badgeEl) this._badgeEl.textContent = symbol;
    this._showPopup();

    this._createChart();
    this._loadHistoricalData(symbol).then(() => {
      if (signal) this._drawSignalOverlay(signal);
    });
  }

  _showPopup() {
    const popup = document.getElementById('chartPopup');
    if (popup) {
      popup.classList.add('open');
      popup.setAttribute('aria-hidden', 'false');
    }
  }

  _hidePopup() {
    const popup = document.getElementById('chartPopup');
    if (popup) {
      popup.classList.remove('open');
      popup.setAttribute('aria-hidden', 'true');
    }
  }

  _createChart() {
    // Clear existing
    this._containerEl.innerHTML = '';

    // Check if TradingView library is loaded
    if (typeof LightweightCharts === 'undefined') {
      this._containerEl.innerHTML = '<div class="panel-empty">Chart library loading...</div>';
      return;
    }

    // ── Build toolbar chrome above chart ──────────────────────
    this._buildTimeframeBar();
    this._buildToolbar();

    // ── Dedicated chart rendering div ────────────────────────
    this._chartEl = document.createElement('div');
    this._chartEl.className = 'chart-render-area';
    this._containerEl.appendChild(this._chartEl);

    this._chart = LightweightCharts.createChart(this._chartEl, {
      width: this._chartEl.clientWidth,
      height: this._chartEl.clientHeight,
      layout: {
        background: { type: 'solid', color: '#111827' },
        textColor: '#94a3b8',
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace",
      },
      localization: {
        locale: 'en-IN',
        timeFormatter: formatTimeIST,
      },
      grid: {
        vertLines: { color: 'rgba(30, 41, 59, 0.5)' },
        horzLines: { color: 'rgba(30, 41, 59, 0.5)' },
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: { color: 'rgba(59, 130, 246, 0.4)', width: 1, style: 2 },
        horzLine: { color: 'rgba(59, 130, 246, 0.4)', width: 1, style: 2 },
      },
      rightPriceScale: {
        borderColor: '#1e293b',
        scaleMargins: { top: 0.1, bottom: 0.25 },
      },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: formatTickMarkIST,
      },
      handleScroll: { vertTouchDrag: false },
    });

    this._candleSeries = this._chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    this._volumeSeries = this._chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    this._chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    // Resize observer — track the chart render area, not the outer container
    const ro = new ResizeObserver(() => {
      if (this._chart && this._chartEl) {
        this._chart.applyOptions({
          width: this._chartEl.clientWidth,
          height: this._chartEl.clientHeight,
        });
      }
    });
    ro.observe(this._chartEl);
    this._resizeObserver = ro;
  }

  // ── Timeframe selector bar ─────────────────────────────────
  _buildTimeframeBar() {
    const bar = document.createElement('div');
    bar.className = 'chart-tf-bar';
    for (const tf of TIMEFRAMES) {
      const btn = document.createElement('button');
      btn.className = 'chart-tf-btn' + (tf === this._activeTimeframe ? ' active' : '');
      btn.textContent = tf;
      btn.dataset.tf = tf;
      btn.addEventListener('click', () => this._selectTimeframe(tf, bar));
      bar.appendChild(btn);
    }
    this._containerEl.appendChild(bar);
  }

  _selectTimeframe(tf, bar) {
    this._activeTimeframe = tf;
    bar.querySelectorAll('.chart-tf-btn').forEach(b => b.classList.toggle('active', b.dataset.tf === tf));
    // Dispatch custom event for future data-loading integration
    document.dispatchEvent(new CustomEvent('chart:timeframe', { detail: tf }));
    if (this._currentSymbol) this._loadHistoricalData(this._currentSymbol);
  }

  // ── Chart toolbar with toggle buttons ──────────────────────
  _buildToolbar() {
    const bar = document.createElement('div');
    bar.className = 'chart-toolbar';

    const toggles = [
      { key: 'candles',   label: 'Candles' },
      { key: 'volume',    label: 'Volume' },
      { key: 'vwap',      label: 'VWAP' },
      { key: 'ema20',     label: 'EMA20' },
      { key: 'grid',      label: 'Grid' },
      { key: 'crosshair', label: 'Crosshair' },
    ];

    for (const { key, label } of toggles) {
      const btn = document.createElement('button');
      btn.className = 'chart-tb-btn' + (this._toggleState[key] ? ' active' : '');
      btn.textContent = label;
      btn.dataset.key = key;
      btn.addEventListener('click', () => {
        this._toggleState[key] = !this._toggleState[key];
        btn.classList.toggle('active', this._toggleState[key]);
        this._applyToggle(key);
      });
      bar.appendChild(btn);
    }

    // Reset button (non-toggle)
    const resetBtn = document.createElement('button');
    resetBtn.className = 'chart-tb-btn chart-tb-reset';
    resetBtn.textContent = '⟲ Reset';
    resetBtn.addEventListener('click', () => {
      if (this._chart) this._chart.timeScale().fitContent();
    });
    bar.appendChild(resetBtn);

    this._containerEl.appendChild(bar);
  }

  _applyToggle(key) {
    if (!this._chart) return;
    switch (key) {
      case 'candles':
        if (this._candleSeries) {
          this._candleSeries.applyOptions({ visible: this._toggleState.candles });
        }
        break;
      case 'volume':
        if (this._volumeSeries) {
          this._volumeSeries.applyOptions({ visible: this._toggleState.volume });
        }
        break;
      case 'vwap':
        this._toggleVwap();
        break;
      case 'ema20':
        this._toggleEma20();
        break;
      case 'grid': {
        const gridColor = this._toggleState.grid ? 'rgba(30, 41, 59, 0.5)' : 'transparent';
        this._chart.applyOptions({
          grid: { vertLines: { color: gridColor }, horzLines: { color: gridColor } },
        });
        break;
      }
      case 'crosshair':
        this._chart.applyOptions({
          crosshair: {
            mode: this._toggleState.crosshair
              ? LightweightCharts.CrosshairMode.Normal
              : LightweightCharts.CrosshairMode.Hidden,
          },
        });
        break;
    }
  }

  _toggleVwap() {
    if (this._toggleState.vwap) {
      // Add VWAP line from last candle data (placeholder — uses close price as proxy)
      if (!this._vwapLine && this._lastCandle) {
        this._vwapLine = this._chart.addLineSeries({
          color: '#f59e0b',
          lineWidth: 1,
          lineStyle: 2, // dashed
          priceLineVisible: false,
          crosshairMarkerVisible: false,
          title: 'VWAP',
        });
        // Draw flat line at VWAP price (approximated from mid of last candle range)
        const vwapPrice = this._lastCandle ? +((this._lastCandle.high + this._lastCandle.low + this._lastCandle.close) / 3).toFixed(2) : 0;
        if (this._lastCandleData && this._lastCandleData.length > 0) {
          this._vwapLine.setData(this._lastCandleData.map(c => ({ time: c.time, value: vwapPrice })));
        }
      }
    } else {
      if (this._vwapLine) {
        this._chart.removeSeries(this._vwapLine);
        this._vwapLine = null;
      }
    }
  }

  _toggleEma20() {
    if (this._toggleState.ema20) {
      if (!this._ema20Series && this._lastCandleData && this._lastCandleData.length >= 20) {
        this._ema20Series = this._chart.addLineSeries({
          color: '#8b5cf6',
          lineWidth: 1,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
          title: 'EMA20',
        });
        // Calculate EMA20
        const k = 2 / (20 + 1);
        const emaData = [];
        let ema = this._lastCandleData.slice(0, 20).reduce((s, c) => s + c.close, 0) / 20;
        for (let i = 19; i < this._lastCandleData.length; i++) {
          if (i > 19) ema = this._lastCandleData[i].close * k + ema * (1 - k);
          emaData.push({ time: this._lastCandleData[i].time, value: +ema.toFixed(2) });
        }
        this._ema20Series.setData(emaData);
      }
    } else {
      if (this._ema20Series) {
        this._chart.removeSeries(this._ema20Series);
        this._ema20Series = null;
      }
    }
  }

  async _loadHistoricalData(symbol) {
    const tfMap = { '5M': '5m', '15M': '15m', '1H': '1h', '4H': '4h' };
    const endpoint = this._activeTimeframe === '1D'
      ? `/api/chart/${symbol}/daily?days=90`
      : `/api/chart/${symbol}/intraday?interval=${tfMap[this._activeTimeframe] || '15m'}`;
    const response = await api.fetch(endpoint);
    const candles = [];
    const volumes = [];
    for (const bar of response?.bars || []) {
      candles.push({
        time: Number(bar.time), open: Number(bar.open), high: Number(bar.high),
        low: Number(bar.low), close: Number(bar.close),
      });
      volumes.push({
        time: Number(bar.time), value: Number(bar.volume || 0),
        color: bar.close >= bar.open ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)',
      });
    }
    if (candles.length === 0) {
      this._lastCandle = null;
      this._lastCandleData = [];
      this._showNoData(symbol);
      return;
    }
    this._containerEl.querySelectorAll('.chart-data-note').forEach(el => el.remove());
    if (this._candleSeries) this._candleSeries.setData(candles);
    if (this._volumeSeries) this._volumeSeries.setData(volumes);
    if (this._chart) this._chart.timeScale().fitContent();
    this._lastCandle = candles[candles.length - 1];
    this._lastCandleData = candles;
    this._showDataNote(symbol, this._lastCandle.close,
      `${this._activeTimeframe} broker candles · ${candles.length} bars`);
    this._clearSignalOverlay();
  }

  async _loadHistoricalDataLegacy(symbol) {
    this._showNoData(symbol);
    return;

    // Try real intraday data first, fall back to synthetic
    let candles = [];
    let volumes = [];

    try {
      // Fetch intraday 1-min bars from feature-engine bar_builder
      const intradayResp = await api.fetch(`/api/chart/${symbol}/intraday`);
      if (intradayResp && intradayResp.bars && intradayResp.bars.length > 0) {
        for (const bar of intradayResp.bars) {
          candles.push({
            time: bar.time,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
          });
          volumes.push({
            time: bar.time,
            value: bar.volume || 0,
            color: bar.close >= bar.open ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)',
          });
        }
      }
    } catch (e) {
      console.debug('Intraday chart data not available yet:', e);
    }

    // If no intraday data, try daily
    if (candles.length === 0) {
      try {
        const dailyResp = await api.fetch(`/api/chart/${symbol}/daily?days=90`);
        if (dailyResp && dailyResp.bars && dailyResp.bars.length > 0) {
          for (const bar of dailyResp.bars) {
            candles.push({
              time: bar.time,
              open: bar.open,
              high: bar.high,
              low: bar.low,
              close: bar.close,
            });
            volumes.push({
              time: bar.time,
              value: bar.volume || 0,
              color: bar.close >= bar.open ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)',
            });
          }
        }
      } catch (e) {
        console.debug('Daily chart data not available yet:', e);
      }
    }

    // Fallback: use feature vector data for price-accurate intraday approximation
    // Uses REAL price levels from live features: ltp, day_high, day_low, prev_close, vwap
    if (candles.length === 0) {
      // First try feature data (has day_high, day_low, vwap, ema prices)
      let featResp = null;
      try {
        featResp = await api.fetch(`/api/features/${symbol}`);
      } catch (_) {}

      // Fall back to tick data
      let tickResp = null;
      if (!featResp) {
        try {
          tickResp = await api.fetch(`/api/ticks/${symbol}`);
        } catch (_) {}
      }

      const src = featResp || tickResp;
      if (!src || !src.ltp) {
        // No data at all — show honest empty state
        this._showNoData(symbol);
        return;
      }

      const ltp      = Number(src.ltp);
      const dayHigh  = Number(src.day_high  || src.high  || ltp * 1.015);
      const dayLow   = Number(src.day_low   || src.low   || ltp * 0.985);
      const prevClose= Number(src.prev_close || src.open  || ltp);
      // Clamp to real range
      const high  = Math.max(ltp, dayHigh,  prevClose);
      const low   = Math.min(ltp, dayLow,   prevClose);
      const range = high - low || ltp * 0.01; // fallback 1% if flat

      const now = new Date();
      // IST display trick: store timestamps as if they are UTC hours = IST hours.
      // Market open 09:15 IST → stored as 09:15 UTC so getUTCHours()=9 → displays "09:15"
      // Date.UTC uses the calendar date in UTC; during Indian market hours, UTC date = IST date.
      const utcDate = new Date(Date.now());
      const baseTime = Math.floor(
        Date.UTC(utcDate.getUTCFullYear(), utcDate.getUTCMonth(), utcDate.getUTCDate(), 9, 15) / 1000
      );

      // Bars elapsed since 09:15 IST (= 03:45 UTC real time)
      const nowUTC       = Math.floor(Date.now() / 1000);
      const marketOpenUTC= Math.floor(
        Date.UTC(utcDate.getUTCFullYear(), utcDate.getUTCMonth(), utcDate.getUTCDate(), 3, 45) / 1000
      );
      const barsElapsed  = Math.max(1, Math.min(375, Math.floor((nowUTC - marketOpenUTC) / 60)));

      // Walk price from prev_close → ltp realistically
      const step = (ltp - prevClose) / barsElapsed;
      let price = prevClose;

      for (let i = 0; i < barsElapsed; i++) {
        const t  = baseTime + i * 60;
        const o  = +price.toFixed(2);
        // Add minor noise proportional to range, not exceeding day high/low
        const noise = 0;
        const c  = +Math.max(low, Math.min(high, price + step + noise)).toFixed(2);
        const h  = +Math.min(high, Math.max(o, c)).toFixed(2);
        const l  = +Math.max(low,  Math.min(o, c)).toFixed(2);
        const vol= 0;
        price = c;
        candles.push({ time: t, open: o, high: h, low: l, close: c });
        volumes.push({ time: t, value: vol, color: c >= o ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)' });
      }

      // Show data-source watermark
      this._showDataNote(symbol, ltp, `Live feature data · Day range: ₹${low.toFixed(0)}–₹${high.toFixed(0)}`);
    }


    if (this._candleSeries) this._candleSeries.setData(candles);
    if (this._volumeSeries) this._volumeSeries.setData(volumes);
    if (this._chart) this._chart.timeScale().fitContent();

    this._lastCandle = candles.length > 0 ? candles[candles.length - 1] : null;
    this._lastCandleData = candles;

    // Clear stale overlays on new data load
    this._clearSignalOverlay();
    if (this._vwapLine) { this._chart.removeSeries(this._vwapLine); this._vwapLine = null; }
    if (this._ema20Series) { this._chart.removeSeries(this._ema20Series); this._ema20Series = null; }
    if (this._toggleState.vwap) this._toggleVwap();
    if (this._toggleState.ema20) this._toggleEma20();
  }

  // ── Signal overlay ─────────────────────────────────────────────────────────
  _drawSignalOverlay(sig) {
    if (!this._chart || !this._lastCandleData || this._lastCandleData.length === 0) return;
    this._clearSignalOverlay();

    const candles  = this._lastCandleData;
    const entry    = Number(sig.entry_price || 0);
    const stop     = Number(sig.invalidation_price || sig.stop_price || 0);
    const target   = Number(sig.target_price || 0);
    const isBull   = (sig.signal_type || 'bullish') === 'bullish';

    const _addLine = (price, color, lineStyle, title) => {
      if (!price || price <= 0) return null;
      const series = this._chart.addLineSeries({
        color,
        lineWidth: 1,
        lineStyle,       // 0=solid, 1=dotted, 2=dashed
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        lastValueVisible: true,
        title,
      });
      series.setData(candles.map(c => ({ time: c.time, value: price })));
      return series;
    };

    // Entry — solid blue
    this._entryLine  = _addLine(entry, '#3b82f6', 0, `Entry ${entry.toFixed(2)}`);
    // Stop — dashed red
    this._stopLine   = _addLine(stop,  '#ef4444', 2, `Stop ${stop.toFixed(2)}`);
    // Target — dashed green
    this._targetLine = _addLine(target,'#10b981', 2, `Target ${target.toFixed(2)}`);

    // Signal marker on last candle
    if (this._candleSeries && candles.length > 0) {
      const markerTime = candles[candles.length - 1].time;
      this._candleSeries.setMarkers([{
        time: markerTime,
        position: isBull ? 'belowBar' : 'aboveBar',
        color: isBull ? '#10b981' : '#ef4444',
        shape: isBull ? 'arrowUp' : 'arrowDown',
        text: isBull ? 'BUY' : 'SELL',
        size: 1,
      }]);
    }
  }

  _clearSignalOverlay() {
    if (!this._chart) return;
    if (this._entryLine)  { this._chart.removeSeries(this._entryLine);  this._entryLine  = null; }
    if (this._stopLine)   { this._chart.removeSeries(this._stopLine);   this._stopLine   = null; }
    if (this._targetLine) { this._chart.removeSeries(this._targetLine); this._targetLine = null; }
    if (this._candleSeries) this._candleSeries.setMarkers([]);
  }

  _updateLiveCandle(data) {
    if (!this._lastCandle || !this._candleSeries) return;

    const ltp = Number(data.ltp);
    const widths = { '5M': 300, '15M': 900, '1H': 3600, '4H': 14400, '1D': 86400 };
    const width = widths[this._activeTimeframe] || 900;
    const now = Math.floor(Date.now() / 1000);
    const bucket = Math.floor(now / width) * width;
    if (bucket > this._lastCandle.time) {
      this._lastCandle = { time: bucket, open: ltp, high: ltp, low: ltp, close: ltp };
      this._lastCandleData.push(this._lastCandle);
    }
    const candle = this._lastCandle;

    candle.close = ltp;
    candle.high = Math.max(candle.high, ltp);
    candle.low = Math.min(candle.low, ltp);

    this._candleSeries.update(candle);
  }

  // ── Data status helpers ────────────────────────────────────────────────────
  _showNoData(symbol) {
    // Remove existing note if any
    this._containerEl.querySelectorAll('.chart-data-note').forEach(el => el.remove());
    const note = document.createElement('div');
    note.className = 'chart-data-note chart-no-data';
    note.innerHTML = `<span>No market data available for <strong>${symbol}</strong></span>`;
    this._containerEl.appendChild(note);
    if (this._candleSeries) this._candleSeries.setData([]);
  }

  _showDataNote(symbol, ltp, note) {
    // Remove existing note
    this._containerEl.querySelectorAll('.chart-data-note').forEach(el => el.remove());
    const el = document.createElement('div');
    el.className = 'chart-data-note';
    el.innerHTML = `<span class="chart-data-symbol">${symbol}</span>
      <span class="chart-data-ltp">₹${Number(ltp).toFixed(2)}</span>
      <span class="chart-data-src">${note}</span>`;
    this._containerEl.appendChild(el);
  }

  destroy() {
    if (this._resizeObserver) this._resizeObserver.disconnect();
    if (this._chart) {
      this._chart.remove();
      this._chart = null;
    }
    if (this._wsUnsub) this._wsUnsub();
    this._unsubs.forEach(fn => fn());
  }
}
