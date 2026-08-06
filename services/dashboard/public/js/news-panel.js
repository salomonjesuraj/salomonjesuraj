import { api } from './api.js';
import { escapeHtml } from './utils.js';

function sourceTone(item) {
  const headlineScore = Number(item?.headline_score);
  if (Number.isFinite(headlineScore)) {
    if (headlineScore > 0.5) return 'positive';
    if (headlineScore < -0.5) return 'negative';
  }
  const tone = Number(item?.tone);
  if (!Number.isFinite(tone)) return 'neutral';
  if (tone > 1.5) return 'positive';
  if (tone < -1.5) return 'negative';
  return 'neutral';
}

function edgeTone(edge) {
  const stance = String(edge?.stance || '').toUpperCase();
  if (stance === 'BULLISH') return 'positive';
  if (stance === 'BEARISH') return 'negative';
  if (stance === 'EVENT_RISK') return 'risk';
  return 'neutral';
}

function pills(items, cls = '') {
  if (!Array.isArray(items) || !items.length) return '';
  return `<div class="news-pills">${items.slice(0, 4).map(x => `<span class="${cls}">${escapeHtml(String(x))}</span>`).join('')}</div>`;
}

export class NewsPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._symbol = '';
    this._data = null;
    this._loading = false;
    this._click = null;
  }

  init() {
    if (!this._el) return;
    this._render();
    document.addEventListener('chart:load', (e) => {
      const sym = e.detail?.symbol;
      if (sym) this.load(sym);
    });
    this._click = (e) => {
      const row = e.target.closest('[data-symbol],[data-key]');
      const symbol = row?.dataset?.symbol || row?.dataset?.key;
      if (symbol) this.load(String(symbol).trim().toUpperCase());
    };
    document.addEventListener('click', this._click);
  }

  async load(symbol) {
    if (!symbol || (this._symbol === symbol && this._data)) return;
    this._symbol = symbol;
    this._loading = true;
    this._render();
    const resp = await api.fetch(`/api/news/market?symbol=${encodeURIComponent(symbol)}`);
    this._data = resp || { ok: false, items: [], note: 'News unavailable' };
    this._loading = false;
    this._render();
  }

  _render() {
    if (!this._el) return;
    if (!this._symbol) {
      this._el.innerHTML = `<div class="panel-empty">Select a scanner row to load stock-specific news context.</div>`;
      return;
    }
    if (this._loading) {
      this._el.innerHTML = `<div class="panel-empty">Loading public news context for ${escapeHtml(this._symbol)}...</div>`;
      return;
    }
    const items = Array.isArray(this._data?.items) ? this._data.items : [];
    const edge = this._data?.edge || {};
    const tone = edgeTone(edge);
    this._el.innerHTML = `
      <div class="news-head">
        <div><span>Selected</span><b>${escapeHtml(this._symbol)}</b></div>
        <div><span>Source</span><b>${escapeHtml(this._data?.source || 'Free public feed')}</b></div>
        <small>${escapeHtml(this._data?.note || 'News is context only; scanner evidence remains primary.')}</small>
      </div>
      <div class="news-edge ${tone}">
        <div><span>News edge</span><b>${escapeHtml(edge.stance || 'NO_NEWS')}</b></div>
        <div><span>Score</span><b>${Number.isFinite(Number(edge.score)) ? Number(edge.score).toFixed(2) : '-'}</b></div>
        <div><span>Confidence</span><b>${escapeHtml(edge.confidence || 'LOW')}</b></div>
        <p>${escapeHtml(edge.action || 'News is context only; scanner evidence remains primary.')}</p>
        ${pills(edge.risks, 'risk')}
        ${pills(edge.boosters, 'positive')}
        ${pills(edge.blockers, 'negative')}
      </div>
      <div class="news-list">
        ${items.length ? items.map(item => `
          <a class="news-item ${sourceTone(item)}" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">
            <b>${escapeHtml(item.title)}</b>
            <span>${escapeHtml(item.source || 'source')} · ${escapeHtml(item.published || '')}</span>
            ${pills(item.tags, 'neutral')}
            ${pills(item.event_risks, 'risk')}
          </a>
        `).join('') : `<div class="panel-empty">No fresh public articles found for this symbol.</div>`}
      </div>
    `;
  }

  destroy() {
    if (this._click) document.removeEventListener('click', this._click);
  }
}
