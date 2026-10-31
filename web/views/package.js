// Package page: the downloadable design package as a browsable file tree.
// Readiness + catalogId on top; every file in the zip can be opened in the
// viewer (code highlighting for HTML/CSS, a JSON tree, images, and a live
// preview of the static gallery index.html).
// Route: /p/:pid/package?file=<relative path>
import {get, put} from '../components/api.js';
import {highlightCss, highlightHtml} from '../components/code_highlight.js';
import {proposeComponentFixModal} from '../components/component_badges.js';
import {download, emptyState, fmtBytes, h, icon, mount, spinner} from '../components/dom.js';
import {jsonTree} from '../components/jsontree.js';
import {emit} from '../components/store.js';
import {segmented, toast} from '../components/ui.js';

const IMAGE_RE = /\.(png|jpe?g|gif|webp|svg)$/i;

function fileIcon(path) {
  if (IMAGE_RE.test(path)) return 'image';
  if (/\.json$/.test(path)) return 'data_object';
  if (/\.html$/.test(path)) return 'html';
  if (/\.css$/.test(path)) return 'css';
  if (/\.(kt|swift)$/.test(path)) return 'code';
  if (/\.md$/.test(path)) return 'article';
  return 'description';
}

/** Builds a nested {dirs: Map, files: []} tree from flat paths. */
function buildTree(files) {
  const root = {name: '', path: '', dirs: new Map(), files: []};
  for (const f of files) {
    const parts = f.path.split('/');
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const p = parts.slice(0, i + 1).join('/');
      if (!node.dirs.has(parts[i])) {
        node.dirs.set(
            parts[i], {name: parts[i], path: p, dirs: new Map(), files: []});
      }
      node = node.dirs.get(parts[i]);
    }
    node.files.push({...f, name: parts[parts.length - 1]});
  }
  return root;
}

export async function render(root, params, ctx) {
  const pid = params.pid;
  const url = (ctx && ctx.url) || new URL(location.href);
  let selected = url.searchParams.get('file') || '';
  let alive = true;
  let files = [];
  let rootDir = '';
  const fileUrl = (p) => `/api/projects/${pid}/package/files/${
      p.split('/').map(encodeURIComponent).join('/')}`;

  const summaryEl = h(
      'div.pkg-summary#package-summary', spinner('Checking the package\u2026'));
  const treeEl =
      h('nav.pkg-tree#package-tree', {'aria-label': 'Package files'});
  const viewerEl = h('section.pkg-viewer#package-viewer');
  const titleCount = h('span.count');

  root.appendChild(h(
      'div.page.page-package',
      h('div.page-head.page-head-tight',
        h('div.title-row', h('h1', 'Design package'), titleCount),
        h('div.page-actions',
          h('button.btn.btn-primary#download-package', {
            type: 'button',
            onclick: () => download(`/api/export/projects/${pid}/package.zip`),
          },
            icon('download'), 'Download package .zip'))),
      summaryEl, h('div.pkg-browser', treeEl, viewerEl)));

  // ------------------------------------------------------------ summary
  async function loadSummary() {
    const [info, {settings} = {}] = await Promise.all([
      get(`/api/projects/${pid}/package`).catch((e) => ({error: e.message})),
      get(`/api/projects/${pid}/settings`).catch(() => ({})),
    ]);
    if (!alive) return;
    const catalogId = info.catalog_id || '';
    const counts = info.counts || {};
    const issues = info.issues || [];
    const errors = issues.filter((i) => i.level === 'error').length;
    const idIn = h('input.input.mono#catalog-id-input', {
      value: (settings && settings.catalog && settings.catalog.catalog_id) ||
          catalogId,
      placeholder: catalogId || 'https://dcb.local/catalogs/<project>',
      spellcheck: 'false',
    });
    const idSave =
        h('button.btn.btn-ghost#catalog-id-save', {
          type: 'button',
          onclick: async () => {
            idSave.disabled = true;
            try {
              const {settings: fresh} =
                  await get(`/api/projects/${pid}/settings`);
              const next = {...(fresh || {})};
              next.catalog = {
                ...(next.catalog || {}),
                catalog_id: idIn.value.trim()
              };
              const res =
                  await put(`/api/projects/${pid}/settings`, {settings: next});
              emit(
                  'settings-changed',
                  {settings: (res && res.settings) || next, source: 'package'});
              toast('catalogId saved', 'good');
              loadSummary();
              loadFiles();
            } finally {
              idSave.disabled = false;
            }
          },
        },
          'Save');
    const stat = (n, label, cls = '') =>
        h('div.pkg-stat', {class: cls}, h('div.pkg-stat-num', String(n ?? 0)),
          h('div.pkg-stat-label', label));
    const issueList = issues.length ?
        h('ul.pkg-issues#package-issues', issues.map((i) => {
          const cid = i.component_id;
          return h(
              'li.pkg-issue', {class: `lvl-${i.level || 'warn'}`},
              icon(i.level === 'error' ? 'error' : 'warning'),
              i.component ?
                  (cid ? h('a.pkg-issue-comp',
                           {href: `/p/${pid}/design/components/${cid}`},
                           i.component) :
                         h('code.pkg-issue-comp', i.component)) :
                  null,
              h('span.grow', i.message || ''),
              cid ? h('button.btn.btn-ghost.btn-sm.pkg-issue-fix', {
                type: 'button',
                onclick: async () => {
                  const {component} = await get(`/api/components/${cid}`);
                  await proposeComponentFixModal(component, {
                    activateAfter:
                        component.status !== 'active' && i.level === 'error',
                    onSaved: () => {
                      loadSummary();
                      loadFiles();
                    },
                  });
                },
              },
                      icon('auto_awesome'), 'Fix with Gemini') :
                    null);
        })) :
        null;
    mount(
        summaryEl,
        h('section.design-section.pkg-summary-id',
          h('div.design-section-head', h('h2', 'Catalog ID'),
            h('p.help',
              'The catalog\u2019s $id. Agents and A2UI SDKs reference it in createSurface.catalogId.')),
          h('div.row-inline.pkg-id-row', idIn, idSave)),
        h('section.design-section#package-readiness',
          h('div.design-section-head', h('h2', 'Readiness'),
            h('p.help',
              errors ? `${errors} problem${
                           errors === 1 ?
                               '' :
                               's'} to fix before the package is complete.` :
                  issues.length ? 'Ready, with a few warnings.' :
                                  'Ready to download.')),
          info.error ?
              h('div.callout.callout-warn', icon('error'),
                h('span', `Could not check the package: ${info.error}`)) :
              null,
          h('div.pkg-stats', stat(counts.custom_active, 'custom active'),
            stat(counts.drafts, 'drafts pending', counts.drafts ? 'warn' : ''),
            stat(counts.basic_enabled, 'Basic enabled'),
            stat(counts.basic_disabled, 'Basic disabled')),
          issueList ?
              h('details.pkg-issues-wrap', {open: errors > 0},
                h('summary',
                  `${issues.length} issue${issues.length === 1 ? '' : 's'}`),
                issueList) :
              h('div.pkg-ok', icon('check_circle'), 'No issues found.')));
  }

  // -------------------------------------------------------------- files
  async function loadFiles() {
    mount(treeEl, spinner('Building the package\u2026'));
    let res;
    try {
      res = await get(`/api/projects/${pid}/package/files`);
    } catch (e) {
      if (!alive) return;
      mount(treeEl, h('p.muted', `Could not build the package: ${e.message}`));
      return;
    }
    if (!alive) return;
    files = res.files || [];
    rootDir = res.root || '';
    titleCount.textContent = ` ${files.length} files`;
    if (!files.some((f) => f.path === selected)) {
      selected = files.some((f) => f.path === 'README.md') ?
          'README.md' :
          (files[0] && files[0].path) || '';
    }
    renderTree();
    showFile(selected);
  }

  const openDirs = new Set();
  function renderTree() {
    const tree = buildTree(files);
    // Open the ancestors of the selection, plus top-level dirs, by default.
    if (!openDirs.size) {
      for (const d of tree.dirs.values()) openDirs.add(d.path);
      for (const d of (tree.dirs.get('catalog') || {
             dirs: new Map()
           }).dirs.values()) {
        if (d.name !== 'components') openDirs.add(d.path);
      }
      openDirs.add('catalog/components');
    }
    const parts = selected.split('/');
    for (let i = 1; i < parts.length; i++)
      openDirs.add(parts.slice(0, i).join('/'));

    const fileRow = (f, depth) =>
        h('button.pkg-tree-file', {
          type: 'button',
          class: f.path === selected ? 'on' : '',
          'data-path': f.path,
          style: {paddingLeft: `${8 + depth * 14}px`},
          title: f.path,
          onclick: () => select(f.path),
        },
          icon(fileIcon(f.path)), h('span.pkg-tree-name', f.name),
          h('span.pkg-tree-size', fmtBytes(f.size)));
    const dirNode = (d, depth) => {
      const det =
          h('details.pkg-tree-dir', {open: openDirs.has(d.path)},
            h('summary', {style: {paddingLeft: `${8 + depth * 14}px`}},
              icon('folder'), h('span.pkg-tree-name', d.name)),
            ...children(d, depth + 1));
      det.addEventListener('toggle', () => {
        if (det.open)
          openDirs.add(d.path);
        else
          openDirs.delete(d.path);
      });
      return det;
    };
    const children = (node, depth) =>
        [...[...node.dirs.values()]
             .sort((a, b) => a.name.localeCompare(b.name))
             .map((d) => dirNode(d, depth)),
         ...node.files.slice()
             .sort((a, b) => a.name.localeCompare(b.name))
             .map((f) => fileRow(f, depth)),
    ];
    mount(
        treeEl,
        h('div.pkg-tree-root', icon('inventory_2'),
          h('span.mono', rootDir.replace(/\/$/, ''))),
        ...children(tree, 0));
  }

  function select(path) {
    selected = path;
    const u = new URL(location.href);
    u.searchParams.set('file', path);
    history.replaceState(null, '', u.pathname + u.search);
    for (const b of treeEl.querySelectorAll('.pkg-tree-file'))
      b.classList.toggle('on', b.dataset.path === path);
    showFile(path);
  }

  let viewSeq = 0;
  async function showFile(path) {
    const seq = ++viewSeq;
    if (!path) {
      mount(viewerEl, emptyState('No files', 'The package has no files yet.'));
      return;
    }
    const f = files.find((x) => x.path === path) || {path, size: 0};
    const src = fileUrl(path);
    const body = h('div.pkg-viewer-body');
    const headActions = h('div.pkg-viewer-actions');
    mount(
        viewerEl,
        h('div.pkg-viewer-head', icon(fileIcon(path)),
          h('code.pkg-viewer-path', path),
          h('span.small.muted', fmtBytes(f.size)), headActions),
        body);
    const openRaw =
        h('a.btn.btn-ghost.btn-sm#package-file-raw', {
          href: src,
          target: '_blank',
          rel: 'noopener',
          title: 'Open in a new tab'
        },
          icon('open_in_new'), 'Open');

    if (IMAGE_RE.test(path)) {
      mount(headActions, openRaw);
      mount(
          body,
          h('div.pkg-image', h('img', {src, alt: path, loading: 'lazy'})));
      return;
    }
    mount(body, spinner('Loading\u2026'));
    let text = '';
    try {
      const resp = await fetch(src, {credentials: 'same-origin'});
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      text = await resp.text();
    } catch (e) {
      if (seq === viewSeq)
        mount(body, h('p.muted', `Could not load ${path}: ${e.message}`));
      return;
    }
    if (!alive || seq !== viewSeq) return;

    const copyBtn =
        h('button.btn.btn-ghost.btn-sm#package-file-copy', {
          type: 'button',
          onclick: async () => {
            try {
              await navigator.clipboard.writeText(text);
              toast('Copied', 'good');
            } catch (_) {
              toast('Could not copy', 'warn');
            }
          },
        },
          icon('content_copy'), 'Copy');

    const codeView = () => {
      if (/\.html$/.test(path))
        return h('pre.pkg-code.code-hl', highlightHtml(text));
      if (/\.css$/.test(path))
        return h('pre.pkg-code.code-hl', highlightCss(text));
      return h('pre.pkg-code', text);
    };
    const modes = [];
    if (/\.json$/.test(path)) {
      let parsed;
      try {
        parsed = JSON.parse(text);
        modes.push({
          value: 'tree',
          label: 'Tree',
          render: () => h('div.pkg-json', jsonTree(parsed, {collapseDepth: 2})),
        });
      } catch (_) { /* fall through to raw */
      }
    }
    if (path === 'index.html') {
      modes.push({
        value: 'preview',
        label: 'Preview',
        render: () => h('iframe.pkg-preview', {
          src,
          title: 'Package gallery preview',
          sandbox: 'allow-same-origin',
        }),
      });
    }
    modes.push({value: 'source', label: 'Source', render: codeView});

    let mode = modes[0].value;
    const show = () => {
      const m = modes.find((x) => x.value === mode) || modes[0];
      mount(body, m.render());
    };
    const seg = modes.length > 1 ?
        segmented(
            modes.map(({value, label}) => ({value, label})), mode,
            (v) => {
              mode = v;
              show();
            },
            {id: 'package-file-mode'}) :
        null;
    mount(headActions, seg, copyBtn, openRaw);
    show();
  }

  loadSummary();
  await loadFiles();
  return () => {
    alive = false;
  };
}
