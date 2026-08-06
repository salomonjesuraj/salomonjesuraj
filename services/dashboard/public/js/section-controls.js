const STORAGE_KEY = 'infusion:section-layout:v1';
const ORDER_KEY = 'infusion:section-order:v1';
const MODE_KEY = 'infusion:section-layout-mode:v1';

function readState() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') || {};
  } catch (_) {
    return {};
  }
}

function writeState(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state || {}));
  } catch (_) {}
}

function readOrder() {
  try {
    return JSON.parse(localStorage.getItem(ORDER_KEY) || '{}') || {};
  } catch (_) {
    return {};
  }
}

function writeOrder(order) {
  try {
    localStorage.setItem(ORDER_KEY, JSON.stringify(order || {}));
  } catch (_) {}
}

function readMode() {
  try {
    return JSON.parse(localStorage.getItem(MODE_KEY) || '{}') || {};
  } catch (_) {
    return {};
  }
}

function writeMode(mode) {
  try {
    localStorage.setItem(MODE_KEY, JSON.stringify(mode || {}));
  } catch (_) {}
}

function applyState(section, mode) {
  section.classList.toggle('section-collapsed', mode === 'collapsed');
  section.classList.toggle('section-expanded', mode === 'expanded');
}

function draggableAllowedTarget(target) {
  return !target.closest('button,input,select,textarea,a,[contenteditable="true"],.workbench-tab,.pulse-tab');
}

function sectionGroup(section) {
  if (!section?.parentElement) return '';
  if (section.parentElement.classList.contains('pro-main')) return 'main';
  if (section.parentElement.classList.contains('screener-pro-v4')) return 'top';
  return '';
}

export class SectionControls {
  constructor(root = document) {
    this._root = root;
    this._click = null;
    this._dragStart = null;
    this._dragOver = null;
    this._drop = null;
    this._dragEnd = null;
    this._dragged = null;
  }

  init() {
    this._applySavedOrder();
    this._prepareDraggables();
    const state = readState();
    this._root.querySelectorAll('[data-collapsible-section]').forEach(section => {
      const key = section.dataset.collapsibleSection;
      if (key && state[key]) applyState(section, state[key]);
    });

    this._click = (e) => {
      const btn = e.target.closest('[data-section-action]');
      if (!btn) return;
      const section = btn.closest('[data-collapsible-section]');
      if (!section) return;
      e.preventDefault();
      const key = section.dataset.collapsibleSection;
      const mode = btn.dataset.sectionAction === 'collapse' ? 'collapsed' : 'expanded';
      applyState(section, mode);
      const next = readState();
      if (key) next[key] = mode;
      writeState(next);
      if (mode === 'expanded') section.scrollIntoView({ behavior: 'smooth', block: 'start' });
      window.dispatchEvent(new Event('resize'));
    };
    this._root.addEventListener('click', this._click);
    this._bindDragDrop();
  }

  _applySavedOrder() {
    const order = readOrder();
    const mode = readMode();
    if (mode.main === 'manual') {
      this._root.querySelector('.pro-main')?.classList.add('layout-manual-order');
    }
    ['top', 'main'].forEach(group => {
      const keys = Array.isArray(order[group]) ? order[group] : [];
      if (!keys.length) return;
      const sections = [...this._root.querySelectorAll('[data-draggable-section]')]
        .filter(section => sectionGroup(section) === group);
      if (!sections.length) return;
      const parent = sections[0].parentElement;
      keys.forEach(key => {
        const section = sections.find(x => x.dataset.draggableSection === key);
        if (section && section.parentElement === parent) parent.appendChild(section);
      });
    });
  }

  _prepareDraggables() {
    this._root.querySelectorAll('[data-draggable-section]').forEach(section => {
      section.draggable = true;
      section.classList.add('section-draggable');
      const header = section.querySelector('.panel-title,.section-title,.risk-console-title');
      if (header && !header.querySelector('.section-drag-handle')) {
        header.insertAdjacentHTML('afterbegin', '<span class="section-drag-handle" title="Drag section">⋮⋮</span>');
      }
    });
  }

  _saveOrderFor(group) {
    const order = readOrder();
    order[group] = [...this._root.querySelectorAll('[data-draggable-section]')]
      .filter(section => sectionGroup(section) === group)
      .map(section => section.dataset.draggableSection)
      .filter(Boolean);
    writeOrder(order);
    if (group === 'main') {
      const mode = readMode();
      mode.main = 'manual';
      writeMode(mode);
      this._root.querySelector('.pro-main')?.classList.add('layout-manual-order');
    }
  }

  _bindDragDrop() {
    this._dragStart = (e) => {
      const section = e.target.closest('[data-draggable-section]');
      if (!section || !draggableAllowedTarget(e.target)) return;
      this._dragged = section;
      section.classList.add('section-dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', section.dataset.draggableSection || '');
    };
    this._dragOver = (e) => {
      const target = e.target.closest('[data-draggable-section]');
      if (!target || !this._dragged || target === this._dragged) return;
      if (sectionGroup(target) !== sectionGroup(this._dragged)) return;
      e.preventDefault();
      target.classList.add('section-drop-target');
      const rect = target.getBoundingClientRect();
      const after = e.clientY > rect.top + rect.height / 2;
      target.parentElement.insertBefore(this._dragged, after ? target.nextSibling : target);
    };
    this._drop = (e) => {
      const target = e.target.closest('[data-draggable-section]');
      if (!target || !this._dragged) return;
      e.preventDefault();
      this._saveOrderFor(sectionGroup(this._dragged));
    };
    this._dragEnd = () => {
      this._root.querySelectorAll('.section-dragging,.section-drop-target').forEach(x => {
        x.classList.remove('section-dragging', 'section-drop-target');
      });
      if (this._dragged) this._saveOrderFor(sectionGroup(this._dragged));
      this._dragged = null;
    };
    this._root.addEventListener('dragstart', this._dragStart);
    this._root.addEventListener('dragover', this._dragOver);
    this._root.addEventListener('drop', this._drop);
    this._root.addEventListener('dragend', this._dragEnd);
  }

  destroy() {
    if (this._click) this._root.removeEventListener('click', this._click);
    if (this._dragStart) this._root.removeEventListener('dragstart', this._dragStart);
    if (this._dragOver) this._root.removeEventListener('dragover', this._dragOver);
    if (this._drop) this._root.removeEventListener('drop', this._drop);
    if (this._dragEnd) this._root.removeEventListener('dragend', this._dragEnd);
  }
}
