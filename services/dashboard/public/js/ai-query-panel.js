/**
 * Ask Infusion — natural-language query over live signals.
 * Phase D of the UI overhaul: surfaces POST /api/ai/query (built +
 * live-verified in Phase 12 of the engine work this session, never had a
 * UI until now). Deterministic answers always work with no AI key
 * configured — same "advisory only" framing as the rest of Infusion.
 */
import { escapeHtml } from './utils.js';
import { api } from './api.js';

const EXAMPLES = [
  "What's the market regime right now?",
  'Which sectors are strongest?',
  'How is RELIANCE doing?',
  'Any active PE signals?',
  'Is walk-forward hitting target?',
  'Does chart patterns actually help precision?',
];

export class AiQueryPanel {
  constructor(containerEl) {
    this._el = containerEl;
    this._history = [];
    this._busy = false;
  }

  init() {
    if (!this._el) return;
    this._el.classList.add('ifx-ask');
    this._render();
  }

  _render() {
    this._el.innerHTML = `
      <div class="ifx-ask-intro">
        <p>Ask about the current market regime, sector rankings, active signals, a specific symbol, its PCR/Max Pain, walk-forward precision, the optimizer's live-vs-recommended drift, or whether a Phase 1-10 field (e.g. "chart patterns", "wyckoff") actually shows a precision lift.</p>
        <div class="ifx-ask-examples">
          ${EXAMPLES.map(q => `<button type="button" class="ifx-ask-example" data-example="${escapeHtml(q)}">${escapeHtml(q)}</button>`).join('')}
        </div>
      </div>
      <div class="ifx-ask-history" id="askHistory"></div>
      <form class="ifx-ask-form" id="askForm">
        <input type="text" class="ifx-ask-input" id="askInput" placeholder="Ask Infusion a question…" autocomplete="off" />
        <button type="submit" class="ifx-ask-submit" id="askSubmit">Ask</button>
      </form>
    `;

    this._el.querySelectorAll('[data-example]').forEach(btn => {
      btn.addEventListener('click', () => {
        const input = this._el.querySelector('#askInput');
        if (input) { input.value = btn.dataset.example; this._submit(btn.dataset.example); }
      });
    });

    const form = this._el.querySelector('#askForm');
    form?.addEventListener('submit', (e) => {
      e.preventDefault();
      const input = this._el.querySelector('#askInput');
      const q = (input?.value || '').trim();
      if (q) this._submit(q);
    });

    this._renderHistory();
  }

  async _submit(question) {
    if (this._busy) return;
    this._busy = true;
    const submitBtn = this._el.querySelector('#askSubmit');
    const input = this._el.querySelector('#askInput');
    if (submitBtn) submitBtn.disabled = true;

    this._history.unshift({ question, answer: null, loading: true, sources: [], cached: false });
    this._renderHistory();

    try {
      const resp = await api.post('/api/ai/query', { question });
      this._history[0] = {
        question,
        answer: resp?.answer || resp?.error || 'No answer returned.',
        sources: resp?.data_sources_used || [],
        source: resp?.source || 'deterministic',
        cached: !!resp?.cached,
        loading: false,
      };
    } catch (err) {
      this._history[0] = { question, answer: `Request failed: ${err}`, sources: [], loading: false, error: true };
    }
    this._busy = false;
    if (submitBtn) submitBtn.disabled = false;
    if (input) input.value = '';
    this._renderHistory();
  }

  _renderHistory() {
    const el = this._el.querySelector('#askHistory');
    if (!el) return;
    if (!this._history.length) {
      el.innerHTML = '<div class="ifx-ask-empty">No questions asked yet — try one of the examples above.</div>';
      return;
    }
    el.innerHTML = this._history.map(h => `
      <div class="ifx-ask-turn">
        <div class="ifx-ask-question">${escapeHtml(h.question)}</div>
        ${h.loading
          ? '<div class="ifx-ask-answer ifx-ask-answer--loading">Thinking…</div>'
          : `<div class="ifx-ask-answer${h.error ? ' ifx-ask-answer--error' : ''}">${escapeHtml(h.answer)}</div>
             <div class="ifx-ask-meta">
               ${h.sources && h.sources.length ? `<span class="ifx-badge ifx-badge--info">${h.sources.map(escapeHtml).join(', ')}</span>` : ''}
               ${h.source ? `<span class="ifx-tone-faint">${escapeHtml(h.source)}${h.cached ? ' · cached' : ''}</span>` : ''}
             </div>`
        }
      </div>
    `).join('');
  }

  destroy() {}
}
