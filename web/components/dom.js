// Tiny DOM helpers. All text goes through textContent / createTextNode, so
// user- and LLM-authored strings are never interpreted as HTML.

/**
 * h('div.card#id', {onclick, class, style, ...}, ...children)
 * - tag may include .class and #id shorthands.
 * - attrs: on* → event listeners; class/className; style (string|object);
 *   dataset (object); boolean true → empty attribute; false/null → omitted;
 *   'value', 'checked', 'disabled', 'selected', 'hidden' set as properties.
 * - children: strings/numbers (text), Nodes, arrays (flattened), null/false
 * (skipped).
 */
export function h(tag, attrs, ...children) {
  if (attrs == null || typeof attrs !== 'object' || attrs instanceof Node ||
      Array.isArray(attrs)) {
    if (attrs != null && attrs !== false) children.unshift(attrs);
    attrs = {};
  }
  let name = tag;
  let id = null;
  const classes = [];
  const m = tag.match(/^([a-zA-Z0-9-]*)((?:[.#][\w-]+)*)$/);
  if (m) {
    name = m[1] || 'div';
    (m[2].match(/[.#][\w-]+/g) || []).forEach((t) => {
      if (t[0] === '.')
        classes.push(t.slice(1));
      else
        id = t.slice(1);
    });
  }
  const svg = name === 'svg' || name === 'path' || name === 'circle' ||
      name === 'rect' || name === 'line' || name === 'polyline';
  const el = svg ?
      document.createElementNS('http://www.w3.org/2000/svg', name) :
      document.createElement(name);
  if (id) el.id = id;
  if (classes.length) el.setAttribute('class', classes.join(' '));
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (k === 'class' || k === 'className') {
      const extra = Array.isArray(v) ? v.filter(Boolean).join(' ') : String(v);
      if (extra)
        el.setAttribute(
            'class',
            [el.getAttribute('class'), extra].filter(Boolean).join(' '));
    } else if (k === 'style') {
      if (typeof v === 'string')
        el.setAttribute('style', v);
      else
        Object.entries(v).forEach(([sk, sv]) => {
          if (sv == null) return;
          if (sk.startsWith('--'))
            el.style.setProperty(sk, sv);
          else
            el.style[sk] = sv;
        });
    } else if (k === 'dataset') {
      Object.entries(v).forEach(([dk, dv]) => {
        if (dv != null) el.dataset[dk] = dv;
      });
    } else if ([
                 'value', 'checked', 'disabled', 'selected', 'hidden',
                 'indeterminate', 'multiple'
               ].includes(k)) {
      el[k] = v;
    } else if (k === 'ref' && typeof v === 'function') {
      v(el);
    } else if (v === true) {
      el.setAttribute(k, '');
    } else {
      el.setAttribute(k, String(v));
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false || c === true) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/** Replace all children of el. */
export function mount(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

export function icon(name, cls = '') {
  return h('span.ms', {class: cls, 'aria-hidden': 'true'}, name);
}

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
                                               '&': '&amp;',
                                               '<': '&lt;',
                                               '>': '&gt;',
                                               '"': '&quot;',
                                               '\'': '&#39;'
                                             }[c]));
}

export function relTime(iso) {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (isNaN(t)) return iso;
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 45) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 14) return `${Math.round(s / 86400)} d ago`;
  return new Date(t).toLocaleDateString(
      undefined, {month: 'short', day: 'numeric', year: 'numeric'});
}

export function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString(
      undefined,
      {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
}

export function fmtBytes(n) {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function debounce(fn, ms = 250) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

/** Simple spinner element. */
export function spinner(label) {
  return h(
      'span.spinner-wrap', h('span.spinner'), label ? h('span', label) : null);
}

export function emptyState(title, body, ...actions) {
  return h(
      'div.empty', h('div.empty-title', title),
      body ? h('p.empty-body', body) : null,
      actions.length ? h('div.empty-actions', actions) : null);
}

export function download(url) {
  const a = h('a', {href: url, download: ''});
  document.body.appendChild(a);
  a.click();
  a.remove();
}
