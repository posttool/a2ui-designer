// Input-data tree for a teaching card: every leaf of the sample's input with
// ● bound / ◐ copied as text / ○ not shown, a "where" hint naming the
// component(s) that show it, a coverage bar, and "Not represented" with
// one-click observation shortcuts. Built from GET /api/outputs/{oid}/links.
import {h, icon} from './dom.js';

const DOT = {
  bound: 'b',
  literal: 'l',
  partial: 'l',
  none: 'n'
};

/** Splits "id|scope" into {id, scope}. */
export function splitKey(key) {
  const s = String(key || '');
  const i = s.indexOf('|');
  return i < 0 ? {id: s, scope: null} :
                 {id: s.slice(0, i), scope: s.slice(i + 1)};
}

/** True if component key `a` refers to (one of) the instances named by `b`. */
export function keyMatches(a, b) {
  if (!a || !b) return false;
  if (a === b) return true;
  const x = splitKey(a), y = splitKey(b);
  if (x.id !== y.id) return false;
  return x.scope == null || y.scope == null || x.scope === '' || y.scope === '';
}

/** Short label for a component from links: `Text #price`. */
export function compLabel(c) {
  if (!c) return '';
  return `${c.type || 'Component'} #${c.id}`;
}

function valueClass(kind) {
  if (kind === 'number' || kind === 'bool') return 'num';
  if (kind === 'text' || kind === 'url' || kind === 'image') return 's';
  return '';
}

function fmtValue(leaf) {
  const v = leaf.value_preview;
  if (v == null || v === '') return leaf.kind === 'null' ? 'null' : '';
  if (leaf.kind === 'text' || leaf.kind === 'url' || leaf.kind === 'image')
    return `"${v}"`;
  return String(v);
}

/**
 * dataTree({links, sample, onHover(leaf|null), onClick(leaf),
 *           onUnrepresented(path, 'show'|'omit')})
 * Returns an element with methods:
 *   .highlightComponent(key|null)  rows read by that component get .hov
 *   .highlightPaths(paths)          rows with those paths get .hov
 *   .setSelected(paths)             rows with those paths get .sel
 */
export function dataTree(opts) {
  const links = opts.links || {};
  const sample = opts.sample || {};
  const leaves = links.leaves || [];
  const comps = links.components || [];
  const compByKey = new Map(comps.map((c) => [c.key, c]));
  const collapsed = new Set();
  // Arrays: show the first row expanded, collapse the rest.
  for (const l of leaves) {
    const m = /^(.*)\/(\d+)$/.exec(l.path);
    if (m && Number(m[2]) > 0 && (l.kind === 'object' || l.kind === 'array'))
      collapsed.add(l.path);
  }
  // Components that read anything below a container.
  const below = new Map();
  for (const l of leaves) {
    for (const k of l.components || []) {
      const parts = l.path.split('/');
      for (let i = 1; i < parts.length; i++) {
        const p = parts.slice(0, i + 1).join('/');
        if (!below.has(p)) below.set(p, new Set());
        below.get(p).add(k);
      }
    }
  }

  const rowsByPath = new Map();
  const root = h('div.dtree');

  function compsFor(leaf) {
    if (leaf.components && leaf.components.length) return leaf.components;
    return [...(below.get(leaf.path) || [])];
  }

  function whereHint(leaf) {
    const ks = leaf.components || [];
    if (!ks.length) return '';
    const cs = ks.map((k) => compByKey.get(k)).filter(Boolean);
    const first = cs[0];
    if (leaf.kind === 'object' || leaf.kind === 'array') {
      return first ? `\u2192 ${compLabel(first)}` : '';
    }
    if (ks.length === 1) {
      return first ? compLabel(first) : ks[0];
    }
    const types = [...new Set(cs.map((c) => c.type))];
    return `${ks.length} \u00d7 ${types.join(' / ') || 'components'}`;
  }

  function hiddenByCollapse(path) {
    for (const c of collapsed) {
      if (path !== c && path.startsWith(`${c}/`)) return true;
    }
    return false;
  }

  function header() {
    const ctx = sample.context && typeof sample.context === 'object' ?
        Object.entries(sample.context)
            .filter(
                ([, v]) => v != null && typeof v !== 'object' &&
                    String(v).length <= 40)
            .slice(0, 8) :
        [];
    const cov = links.coverage || {};
    const pct = cov.total ? Math.round((cov.represented / cov.total) * 100) : 0;
    return [
      sample.title ? h('div.dt-title', sample.title) : null,
      sample.intent ?
          h('p.data-intent', h('b', 'Intent: '), String(sample.intent)) :
          null,
      ctx.length ?
          h('div.ctx-chips',
            ctx.map(
                ([k, v]) => h('span.chip.chip-quiet', {title: k}, String(v)))) :
          null,
      cov.total ?
          h('div.coverage', h('span', 'Represented'),
            h('div.bar', h('i', {style: {width: `${pct}%`}})),
            h('span.mono', `${cov.represented} / ${cov.total} fields`)) :
          null,
      h('div.legend', h('span', h('span.dot.b'), ' bound'),
        h('span', h('span.dot.l'), ' copied as text'),
        h('span', h('span.dot.n'), ' not shown'),
        h('span', {style: 'margin-left:auto'},
          'hover \u2194 card \u00b7 click = anchor')),
    ];
  }

  function row(leaf) {
    const lvl =
        Math.max(0, (leaf.depth || leaf.path.split('/').length - 1) - 1);
    const isBox = leaf.kind === 'object' || leaf.kind === 'array';
    const open = !collapsed.has(leaf.path);
    const el =
        h('div.row', {
          class: isBox && lvl === 0 ? 'grp' : '',
          style: {paddingLeft: `${lvl * 14}px`},
          dataset: {path: leaf.path},
          title: leaf.path,
          onmouseenter: () =>
              opts.onHover && opts.onHover(leaf, compsFor(leaf)),
          onmouseleave: () => opts.onHover && opts.onHover(null, []),
          onclick: (e) => {
            if (isBox && e.target.closest('.arrow')) return;
            opts.onClick && opts.onClick(leaf, compsFor(leaf));
          },
        },
          isBox ? h('span.arrow', {
            role: 'button',
            'aria-label': open ? 'Collapse' : 'Expand',
            onclick: (e) => {
              e.stopPropagation();
              if (collapsed.has(leaf.path))
                collapsed.delete(leaf.path);
              else
                collapsed.add(leaf.path);
              draw();
            }
          },
                    open ? '\u25be' : '\u25b8') :
                  h('span.dot', {class: DOT[leaf.represented] || 'n'}),
          h('span.k', leaf.key),
          isBox ? h('span.cnt',
                    leaf.kind === 'array' ? `[${leaf.count ?? ''}]` : '') :
                  h('span.val', {class: valueClass(leaf.kind)}, fmtValue(leaf)),
          whereHint(leaf) ? h('span.where', whereHint(leaf)) : null);
    rowsByPath.set(leaf.path, el);
    return el;
  }

  function unrepresented() {
    const list = links.unrepresented || [];
    if (!list.length) return null;
    return h(
        'div.unrep',
        h('h4', 'Not represented ', h('span.muted', `(${list.length})`)),
        list.map(
            (p) =>
                h('div.row', {title: p}, h('span.dot.n'), h('span.k', p),
                  h('span.acts',
                    h('button.mini-btn', {
                      type: 'button',
                      onclick: () => opts.onUnrepresented &&
                          opts.onUnrepresented(p, 'show')
                    },
                      'Should be shown'),
                    h('button.mini-btn', {
                      type: 'button',
                      onclick: () => opts.onUnrepresented &&
                          opts.onUnrepresented(p, 'omit')
                    },
                      'Fine to omit')))));
  }

  let lastHov = [];
  let lastSel = [];
  function draw() {
    rowsByPath.clear();
    const body = leaves.length ?
        h('div.dt', leaves.filter((l) => !hiddenByCollapse(l.path)).map(row)) :
        h('p.muted.small', icon('info'), ' No input data links for this card.');
    root.replaceChildren(
        ...header().filter(Boolean), body, unrepresented() || []);
    apply('hov', lastHov);
    apply('sel', lastSel);
  }

  function apply(cls, paths) {
    root.querySelectorAll(`.dt .row.${cls}`)
        .forEach((r) => r.classList.remove(cls));
    for (const p of paths) {
      const r = rowsByPath.get(p);
      if (r) r.classList.add(cls);
    }
  }

  root.highlightPaths = (paths) => {
    lastHov = paths || [];
    apply('hov', lastHov);
    const first = rowsByPath.get(lastHov[0]);
    if (first && opts.scrollIntoView !== false)
      first.scrollIntoView({block: 'nearest'});
  };
  root.highlightComponent = (key) => {
    if (!key) return root.highlightPaths([]);
    const paths = new Set();
    for (const c of comps) {
      if (!keyMatches(c.key, key)) continue;
      (c.reads || []).forEach((p) => paths.add(p));
      (c.literal || []).forEach((p) => paths.add(p));
    }
    for (const l of leaves) {
      if ((l.components || []).some((k) => keyMatches(k, key)))
        paths.add(l.path);
    }
    root.highlightPaths([...paths]);
  };
  root.setSelected = (paths) => {
    lastSel = paths || [];
    apply('sel', lastSel);
  };
  draw();
  return root;
}
