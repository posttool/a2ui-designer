// Toasts, modal dialogs, drawers, confirm/prompt helpers.
import {recordToast} from './diagnostics.js';
import {h, icon} from './dom.js';
import {emit} from './store.js';

let toastHost;
export function toast(message, kind = 'info', ms = 4200, opts = {}) {
  recordToast({message, kind});
  if (!toastHost) {
    toastHost = h('div.toasts', {role: 'status', 'aria-live': 'polite'});
    document.body.appendChild(toastHost);
  }
  const ic = {
    bad: 'error',
    good: 'check_circle',
    warn: 'warning',
    info: 'info'
  }[kind] ||
      'info';
  const reportBtn = (kind === 'bad' && !opts.noReport) ?
      h('button.toast-report', {
        type: 'button',
        title: 'Report a bug with this error',
        onclick: () => {
          el.remove();
          emit('open-bug-report', {
            prefill: {
              title: `Error: ${String(message || '').slice(0, 80)}`,
              what_happened: String(message || ''),
            },
          });
        },
      },
        'Report') :
      null;
  const el = h(
      'div.toast', {class: `toast-${kind}`}, icon(ic), h('span', message),
      reportBtn,
      h('button.toast-x', {'aria-label': 'Dismiss', onclick: () => el.remove()},
        icon('close')));
  toastHost.appendChild(el);
  setTimeout(() => el.classList.add('out'), ms);
  setTimeout(() => el.remove(), ms + 400);
  return el;
}

/**
 * Open a modal. Returns {el, close, body, setBusy}.
 * opts: {title, body (Node|Node[]), actions: [{label, id,
 * kind:'primary'|'ghost'|'danger', onClick(close) → false keeps open}], wide,
 * onClose}
 */
export function modal(opts) {
  const prevFocus = document.activeElement;
  const overlay = h('div.overlay', {
    onmousedown: (e) => {
      if (e.target === overlay) close();
    }
  });
  const body = h('div.modal-body', opts.body);
  const footer = h('div.modal-foot');
  const box =
      h('div.modal', {
        class: opts.wide ? 'modal-wide' : '',
        role: 'dialog',
        'aria-modal': 'true',
        'aria-label': opts.title || 'Dialog',
        id: opts.id || null
      },
        h('div.modal-head', h('h2', opts.title || ''),
          h('button.icon-btn', {'aria-label': 'Close', onclick: () => close()},
            icon('close'))),
        body, footer);
  overlay.appendChild(box);
  let closed = false;
  function close(result) {
    if (closed) return;
    closed = true;
    document.removeEventListener('keydown', onKey, true);
    overlay.remove();
    opts.onClose && opts.onClose(result);
    if (prevFocus && prevFocus.focus) prevFocus.focus();
  }
  function onKey(e) {
    if (e.key === 'Escape') {
      e.stopPropagation();
      close();
    }
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      const primary = footer.querySelector('.btn-primary');
      if (primary && !primary.disabled) {
        e.preventDefault();
        primary.click();
      }
    }
  }
  const buttons = [];
  for (const a of opts.actions || []) {
    const b =
        h('button.btn', {
          class: `btn-${a.kind || 'ghost'}`,
          id: a.id || null,
          type: 'button',
          onclick: async () => {
            if (!a.onClick) return close();
            setBusy(true, b);
            try {
              const r = await a.onClick(close);
              if (r !== false) close(r);
            } catch (e) {
              console.error(e);
            } finally {
              if (!closed) setBusy(false, b);
            }
          }
        },
          a.label);
    buttons.push(b);
    footer.appendChild(b);
  }
  function setBusy(busy, which) {
    buttons.forEach((b) => {
      b.disabled = busy;
    });
    if (which) which.classList.toggle('busy', busy);
  }
  document.addEventListener('keydown', onKey, true);
  document.body.appendChild(overlay);
  setTimeout(() => {
    const f = box.querySelector('[autofocus]') ||
        box.querySelector('input,textarea,select');
    if (f) f.focus();
  }, 0);
  return {el: box, body, close, setBusy};
}

export function confirmDialog(
    title, message, {confirmLabel = 'Confirm', danger = false} = {}) {
  return new Promise((resolve) => {
    let ok = false;
    modal({
      title,
      body: h('p.muted', message),
      actions: [
        {label: 'Cancel', kind: 'ghost'},
        {
          label: confirmLabel,
          kind: danger ? 'danger' : 'primary',
          id: 'confirm-ok',
          onClick: () => {
            ok = true;
          }
        },
      ],
      onClose: () => resolve(ok),
    });
  });
}

export function promptDialog(title, {
  label = '',
  value = '',
  placeholder = '',
  confirmLabel = 'OK',
  multiline = false,
  help = ''
} = {}) {
  return new Promise((resolve) => {
    let result = null;
    const input = multiline ?
        h('textarea.input',
          {id: 'prompt-input', rows: 4, placeholder, autofocus: true}, value) :
        h('input.input',
          {id: 'prompt-input', value, placeholder, autofocus: true});
    modal({
      title,
      body: [
        h('label.field', label ? h('span.field-label', label) : null, input),
        help ? h('p.help', help) : null
      ],
      actions: [
        {label: 'Cancel'},
        {
          label: confirmLabel,
          kind: 'primary',
          id: 'prompt-ok',
          onClick: () => {
            result = input.value;
          }
        },
      ],
      onClose: () => resolve(result),
    });
    if (!multiline)
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          document.getElementById('prompt-ok')?.click();
        }
      });
  });
}

/**
 * Right-side drawer. Returns {el, body, close, setTitle}. Only one at a time.
 */
let openDrawer = null;
export function drawer({title, body, wide = false, onClose, actions, id}) {
  if (openDrawer) openDrawer.close();
  const titleEl = h('h2', title || '');
  const bodyEl = h('div.drawer-body', body);
  const head =
      h('div.drawer-head', titleEl,
        h('div.drawer-head-actions', actions || null,
          h('button.icon-btn', {
            'aria-label': 'Close drawer',
            id: 'drawer-close',
            onclick: () => close()
          },
            icon('close'))));
  const el = h(
      'aside.drawer',
      {class: wide ? 'drawer-wide' : '', id: id || null, role: 'complementary'},
      head, bodyEl);
  const scrim = h('div.scrim', {onclick: () => close()});
  document.body.append(scrim, el);
  requestAnimationFrame(() => {
    el.classList.add('open');
    scrim.classList.add('open');
  });
  function onKey(e) {
    if (e.key === 'Escape' && !document.querySelector('.overlay')) close();
  }
  document.addEventListener('keydown', onKey);
  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    document.removeEventListener('keydown', onKey);
    el.classList.remove('open');
    scrim.classList.remove('open');
    setTimeout(() => {
      el.remove();
      scrim.remove();
    }, 220);
    if (openDrawer && openDrawer.el === el) openDrawer = null;
    onClose && onClose();
  }
  openDrawer = {
    el,
    body: bodyEl,
    close,
    setTitle: (t) => {
      titleEl.textContent = t;
    }
  };
  return openDrawer;
}
export function closeDrawer() {
  if (openDrawer) openDrawer.close();
}

/** Progress bar. value 0..1 or segments [{value, cls}] */
export function progressBar(value, {segments, label} = {}) {
  const bar = h('div.progress', {
    role: 'progressbar',
    'aria-valuemin': 0,
    'aria-valuemax': 100,
    'aria-valuenow': Math.round((value || 0) * 100)
  });
  if (segments) {
    segments.forEach((s) => bar.appendChild(h('div.progress-seg', {
      class: s.cls || '',
      style: {width: `${Math.max(0, s.value) * 100}%`},
      title: s.title || ''
    })));
  } else {
    bar.appendChild(
        h('div.progress-seg.fill',
          {style: {width: `${Math.min(1, Math.max(0, value || 0)) * 100}%`}}));
  }
  return label ? h('div.progress-wrap', bar, h('span.progress-label', label)) :
                 bar;
}

/** Segmented control: options [{value, label, id, count}] */
export function segmented(options, value, onChange, {id} = {}) {
  const el = h('div.seg', {role: 'tablist', id: id || null});
  let cur = value;
  const counts = new Map();
  const render = (v) => {
    cur = v;
    el.replaceChildren(...options.map((o) => {
      const cnt = counts.has(o.value) ? counts.get(o.value) : o.count;
      return h(
          'button.seg-btn', {
            type: 'button',
            role: 'tab',
            id: o.id || null,
            'aria-selected': String(o.value === v),
            class: o.value === v ? 'on' : '',
            title: o.title || null,
            onclick: () => {
              render(o.value);
              onChange(o.value);
            },
          },
          o.icon ? icon(o.icon) : null, o.label,
          cnt != null && cnt > 0 ? h('span.count.seg-count', ` ${cnt}`) : null);
    }));
  };
  render(value);
  el.set = render;
  el.setCounts = (next = {}) => {
    for (const [k, n] of Object.entries(next)) counts.set(k, n);
    render(cur);
  };
  return el;
}

export function toggle({id, checked, onChange, label, help}) {
  const input = h('input', {
    type: 'checkbox',
    id,
    checked: !!checked,
    onchange: (e) => onChange && onChange(e.target.checked)
  });
  return h(
      'label.toggle',
      {for: id
      },
      input, h('span.toggle-track', h('span.toggle-thumb')),
      h('span.toggle-text', h('span', label),
        help ? h('span.help', help) : null));
}
