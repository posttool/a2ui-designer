// Collapsible JSON tree. Nodes that look like A2UI components (have an "id"
// and "component") get data-a2ui-id so the review screen can link them with
// the live render (hover ↔ highlight).
import {h} from './dom.js';

/**
 * jsonTree(value, {collapseDepth=2, onNodeClick(a2uiId), rootLabel})
 * Returns element with .highlight(id) and .expandAll()/.collapseAll().
 */
export function jsonTree(value, opts = {}) {
  const {collapseDepth = 2, onNodeClick} = opts;
  const root = h('div.json-tree');
  root.appendChild(node(opts.rootLabel ?? null, value, 0));

  function node(key, v, depth) {
    const keyEl = key !== null ?
        h('span.jt-key', typeof key === 'number' ? `${key}` : `"${key}"`,
          h('span.jt-punc', ': ')) :
        null;
    if (v === null || typeof v !== 'object') {
      let cls = 'jt-null';
      let text = 'null';
      if (typeof v === 'string') {
        cls = 'jt-str';
        text = JSON.stringify(v);
      } else if (typeof v === 'number') {
        cls = 'jt-num';
        text = String(v);
      } else if (typeof v === 'boolean') {
        cls = 'jt-bool';
        text = String(v);
      } else if (v === undefined) {
        text = 'undefined';
      }
      return h('div.jt-row', keyEl, h('span', {class: cls}, text));
    }
    const isArr = Array.isArray(v);
    const entries = isArr ? v.map((x, i) => [i, x]) : Object.entries(v);
    const open = isArr ? '[' : '{';
    const close = isArr ? ']' : '}';
    const a2uiId =
        !isArr && typeof v.id === 'string' && (v.component !== undefined) ?
        v.id :
        null;
    const collapsed = depth >= collapseDepth && entries.length > 0;
    const summary = isArr ?
        `${entries.length} item${entries.length === 1 ? '' : 's'}` :
        (a2uiId ? `${
                      typeof v.component === 'string' ?
                          v.component :
                          'component'} · ${a2uiId}` :
                  `${entries.length} key${entries.length === 1 ? '' : 's'}`);
    const children = h('div.jt-children');
    let built = false;
    const build = () => {
      if (built) return;
      built = true;
      entries.forEach(([k, x]) => children.appendChild(node(k, x, depth + 1)));
    };
    const box = h(
        'div.jt-node',
        {class: collapsed ? 'collapsed' : '', dataset: a2uiId ? {a2uiId} : {}});
    const toggle = h('button.jt-toggle', {
      type: 'button',
      'aria-label': 'Toggle',
      onclick: (e) => {
        e.stopPropagation();
        build();
        box.classList.toggle('collapsed');
      }
    });
    const head =
        h('div.jt-row.jt-head', toggle, keyEl, h('span.jt-punc', open),
          h('span.jt-summary', summary),
          h('span.jt-punc.jt-close-inline', close));
    if (a2uiId) {
      head.classList.add('jt-a2ui');
      head.addEventListener('click', () => onNodeClick && onNodeClick(a2uiId));
    }
    if (!collapsed) build();
    box.append(
        head, children, h('div.jt-row.jt-close', h('span.jt-punc', close)));
    box._build = build;
    return box;
  }

  let selectedNodeId = null;

  function findAndReveal(id) {
    if (!id) return null;
    let target =
        root.querySelector(`.jt-node[data-a2ui-id="${CSS.escape(id)}"]`);
    if (!target) {
      root.expandAll(false);
      target = root.querySelector(`.jt-node[data-a2ui-id="${CSS.escape(id)}"]`);
    }
    if (!target) return null;
    let p = target.parentElement;
    while (p && p !== root) {
      if (p.classList.contains('jt-node')) p.classList.remove('collapsed');
      p = p.parentElement;
    }
    return target;
  }

  root.select = (id, {scroll = true} = {}) => {
    selectedNodeId = id ? String(id) : null;
    root.querySelectorAll('.jt-node.selected, .jt-node.hl')
        .forEach((n) => n.classList.remove('selected', 'hl'));
    if (!selectedNodeId) return;
    const target = findAndReveal(selectedNodeId);
    if (!target) return;
    target.classList.add('selected', 'hl');
    if (scroll) target.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  };

  root.hover = (id, {scroll = false} = {}) => {
    root.querySelectorAll('.jt-node.hover')
        .forEach((n) => n.classList.remove('hover'));
    root.querySelectorAll('.jt-node.hl:not(.selected)')
        .forEach((n) => n.classList.remove('hl'));
    if (!id) return;
    const target = findAndReveal(String(id));
    if (!target) return;
    target.classList.add('hover', 'hl');
    if (scroll) target.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  };

  root.highlight = (id, {scroll = true, select = false} = {}) => {
    if (select) {
      root.select(id, {scroll});
      return;
    }
    if (!id && selectedNodeId) {
      root.hover(null);
      return;
    }
    root.hover(id, {scroll});
  };
  root.expandAll = (uncollapse = true) => {
    for (let guard = 0; guard < 100; guard++) {
      let any = false;
      root.querySelectorAll('.jt-node').forEach((n) => {
        if (n._build && !n.__b) {
          n._build();
          n.__b = true;
          any = true;
        }
        if (uncollapse) n.classList.remove('collapsed');
      });
      if (!any) break;
    }
  };
  root.collapseAll = () => root.querySelectorAll('.jt-node').forEach((n, i) => {
    if (i > 0) n.classList.add('collapsed');
  });
  return root;
}
