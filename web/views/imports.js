// Imports: the record of every Figma file, Stitch project and screenshot set
// brought into a project. Each import is analyzed into extractions
// (components / templates / token reference) and only merged into tokens,
// components, templates or the constitution after the designer answers
// questions and applies a merge plan. Route: /p/:pid/imports.
// Shared import helpers (phase/kind badges, source chip) are exported for
// import_detail.js.
import {get} from '../components/api.js';
import {emptyState, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {emit} from '../components/store.js';

import {openImportDrawer} from './import_drawer.js';

export const SOURCE = {
  figma: {label: 'Figma', tint: 'tint-7', icon: 'design_services'},
  stitch: {label: 'Stitch', tint: 'tint-6', icon: 'auto_awesome_mosaic'},
  screenshots: {label: 'Screenshots', tint: 'tint-3', icon: 'image'},
};

export const KINDS = ['component', 'template', 'token_reference', 'ignore'];
export const KIND = {
  component: {
    label: 'Component',
    plural: 'Components',
    icon: 'widgets',
    noun: ['component', 'components']
  },
  template: {
    label: 'Template',
    plural: 'Templates',
    icon: 'dashboard',
    noun: ['template', 'templates']
  },
  token_reference: {
    label: 'Token reference',
    plural: 'Token reference',
    icon: 'palette',
    noun: ['token sheet', 'token sheets']
  },
  ignore: {
    label: 'Ignore',
    plural: 'Ignored',
    icon: 'block',
    noun: ['ignored', 'ignored']
  },
};

export const PHASE = {
  ingesting: {label: 'Ingesting', cls: 's-run', active: true},
  analyzing: {label: 'Analyzing', cls: 's-run', active: true},
  review: {label: 'Needs review', cls: 's-warn'},
  planning: {label: 'Planning merge', cls: 's-run', active: true},
  plan_ready: {label: 'Merge plan ready', cls: 's-warn'},
  merged: {label: 'Merged', cls: 's-done'},
  error: {label: 'Error', cls: 's-err'},
  legacy: {label: 'Legacy', cls: 's-muted'},
};
/**
 * @param {*} phase
 * @return {boolean}
 */
export const isActivePhase = (phase) => !!(PHASE[phase] && PHASE[phase].active);

/**
 * @param {*} imp
 * @return {*}
 */
export function phaseBadge(imp) {
  const p = PHASE[imp.phase] || {label: imp.phase || 'unknown', cls: ''};
  return h(
      'span.status.import-phase', {class: p.cls, dataset: {phase: imp.phase}},
      h('span.dot'), p.label);
}

/**
 * @param {*} kind
 * @param {*=} opts
 * @return {*}
 */
export function kindChip(kind, opts = {}) {
  const k = KIND[kind] || {label: kind, icon: 'help'};
  return h(
      'span.chip.kind-chip', {class: [`k-${kind}`, opts.xs ? 'chip-xs' : '']},
      icon(k.icon), k.label);
}

/** Figma / Stitch / Screenshots chip with the file name, linking out. */
/**
 * @param {*} imp
 * @return {*}
 */
export function sourceChip(imp) {
  const s = SOURCE[imp.kind] || SOURCE.screenshots;
  const src = imp.source || {};
  const name = src.file_name || src.project_title || '';
  const inner = [
    icon(s.icon), h('span.ds-kind', s.label),
    name ? h('span.ds-name', name) : null
  ];
  return src.url ? h('a.chip.design-source.import-source', {
    class: s.tint,
    href: src.url,
    target: '_blank',
    rel: 'noopener noreferrer',
    title: `${s.label}${name ? `: ${name}` : ''} \u2014 open in a new tab`,
    onclick: (e) => e.stopPropagation(),
  },
                     inner) :
                   h('span.chip.design-source.import-source',
                     {class: s.tint, title: s.label}, inner);
}

/**
 * @param {number} n
 * @param {!Array<string>} forms
 * @return {string}
 */
const plural = (n, forms) => `${n} ${n === 1 ? forms[0] : forms[1]}`;

/** "3 components · 2 templates · 1 token sheet · 2 open questions". */
/**
 * @param {*} counts
 * @return {*}
 */
export function countsLine(counts) {
  const c = counts || {};
  const parts = [];
  for (const k of ['component', 'template', 'token_reference']) {
    if (c[k])
      parts.push(
          h('span.ic-part', {class: `k-${k}`}, plural(c[k], KIND[k].noun)));
  }
  if (c.data) {
    parts.push(
        h('span.ic-part.k-data',
          plural(c.data, ['data sample', 'data samples'])));
  }
  if (c.ignore) parts.push(h('span.ic-part.muted', `${c.ignore} ignored`));
  if (c.open_questions)
    parts.push(
        h('span.ic-part.ic-open', icon('help'),
          plural(c.open_questions, ['open question', 'open questions'])));
  if (!parts.length) return null;
  const out = [];
  parts.forEach((p, i) => {
    if (i) out.push(h('span.ic-sep', '\u00b7'));
    out.push(p);
  });
  return h('div.import-counts', out);
}

/**
 * Formats elapsed time since `fromIso` as M:SS (e.g. "0:08", "1:24").
 * @param {string} fromIso
 * @param {string=} toIso
 * @return {string}
 */
export function fmtElapsed(fromIso, toIso) {
  if (!fromIso) return '';
  const start = Date.parse(fromIso);
  const end = toIso ? Date.parse(toIso) : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return '';
  const sec = Math.max(0, Math.floor((end - start) / 1000));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/**
 * Formats a completed step duration (e.g. "3s", "48s", "1m 12s").
 * @param {string} fromIso
 * @param {string} toIso
 * @return {string}
 */
export function fmtStepDuration(fromIso, toIso) {
  if (!fromIso || !toIso) return '';
  const start = Date.parse(fromIso);
  const end = Date.parse(toIso);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return '';
  const sec = Math.max(0, Math.round((end - start) / 1000));
  if (sec < 1) return '<1s';
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

/**
 * Updates all `[data-elapsed-from]` elements inside `rootEl` in place.
 * @param {!Element} rootEl
 */
export function tickElapsed(rootEl) {
  if (!rootEl) return;
  for (const el of rootEl.querySelectorAll('[data-elapsed-from]')) {
    const iso = el.dataset.elapsedFrom || '';
    const txt = fmtElapsed(iso);
    if (txt && el.textContent !== txt) el.textContent = txt;
  }
}

/**
 * Default headline when `imp.progress.label` is not yet populated.
 * @param {*} imp
 * @return {string}
 */
export function defaultProgressLabel(imp) {
  const p = imp.progress || {};
  if (p.label) return p.label;
  if (imp.phase === 'ingesting') {
    return imp.kind === 'stitch' ?
        'Fetching screens from Stitch\u2026' :
        imp.kind === 'figma' ?
        'Fetching frames and components from Figma\u2026' :
        'Storing uploads\u2026';
  }
  if (imp.phase === 'analyzing') {
    return 'Gemini is separating components, templates and token reference\u2026';
  }
  if (imp.phase === 'planning') {
    return 'Drafting the merge plan\u2026';
  }
  return '';
}

/**
 * Compact one-line live progress indicator for an active import row.
 * @param {*} imp
 * @return {*}
 */
export function progressLine(imp) {
  if (!isActivePhase(imp.phase)) return null;
  const p = imp.progress || {};
  const label = defaultProgressLabel(imp);
  const startedAt = p.started_at || imp.updated_at || imp.created_at || '';
  const total = Number(p.total) || 0;
  const done = Number(p.done) || 0;
  const pct = total > 0 ?
      Math.max(5, Math.min(100, Math.round((done / total) * 100))) :
      0;
  return h(
      'div.import-prog-line',
      h('div.import-prog-line-top', h('span.spinner.spinner-sm'),
        h('span.iprog-line-label', label),
        p.detail ? h('span.iprog-line-detail.muted', `\u00b7 ${p.detail}`) :
                   null,
        startedAt ? h('span.iprog-elapsed', {dataset: {elapsedFrom: startedAt}},
                      fmtElapsed(startedAt)) :
                    null),
      h('div.iprog-bar.iprog-bar-sm',
        {class: total > 0 ? 'determinate' : 'indeterminate'},
        h('div.iprog-fill', total > 0 ? {style: {width: `${pct}%`}} : {})));
}

/**
 * Detailed live progress card for `import_detail.js` during active phases.
 * @param {*} imp
 * @return {*}
 */
export function progressCard(imp) {
  if (!isActivePhase(imp.phase)) return null;
  const p = imp.progress || {};
  const label = defaultProgressLabel(imp);
  const imgs = (imp.images || []).length;
  const fallbackDetail = imp.phase === 'analyzing' ?
      `${imgs} image${imgs === 1 ? '' : 's'} \u00b7 ${
          (imp.analysis && imp.analysis.model) || 'gemini-3.8-flash'}` :
      '';
  const detail = p.detail || fallbackDetail;
  const startedAt = p.started_at || imp.updated_at || imp.created_at || '';
  const total = Number(p.total) || 0;
  const done = Number(p.done) || 0;
  const pct = total > 0 ?
      Math.max(4, Math.min(100, Math.round((done / total) * 100))) :
      0;
  const steps = Array.isArray(p.steps) ? p.steps : [];
  return h(
      'div.import-progress', {dataset: {phase: imp.phase, step: p.step || ''}},
      h('div.iprog-head',
        h('div.iprog-title-wrap', h('span.spinner'),
          h('div.iprog-titles', h('div.iprog-label', label),
            detail ? h('div.iprog-detail.small.muted', detail) : null)),
        h('div.iprog-meta',
          total > 0 ? h('span.iprog-count.mono', `${done}/${total}`) : null,
          startedAt ?
              h('span.iprog-elapsed',
                {title: 'Elapsed time', dataset: {elapsedFrom: startedAt}},
                fmtElapsed(startedAt)) :
              null)),
      h('div.iprog-bar', {class: total > 0 ? 'determinate' : 'indeterminate'},
        h('div.iprog-fill', total > 0 ? {style: {width: `${pct}%`}} : {})),
      steps.length ? h('ol.iprog-steps', steps.map((st) => {
        const stStatus = st.status || 'todo';
        return h(
            'li.iprog-step',
            {class: `is-${stStatus}`, dataset: {stepId: st.id || ''}},
            h('span.iprog-step-icon',
              stStatus === 'running'   ? h('span.spinner.spinner-sm') :
                  stStatus === 'done'  ? icon('check') :
                  stStatus === 'error' ? icon('priority_high') :
                                         h('span.iprog-step-dot')),
            h('span.iprog-step-body',
              h('span.iprog-step-label', st.label || st.id),
              st.detail ? h('span.iprog-step-detail', st.detail) : null),
            stStatus === 'running' && st.started_at ?
                h('span.iprog-step-time.mono',
                  {dataset: {elapsedFrom: st.started_at}},
                  fmtElapsed(st.started_at)) :
                stStatus === 'done' && st.started_at && st.finished_at ?
                h('span.iprog-step-time.mono',
                  fmtStepDuration(st.started_at, st.finished_at)) :
                null);
      })) :
                     null);
}

/**
 * @param {*} root
 * @param {*} params
 * @return {*}
 */
export async function render(root, params) {
  const pid = params.pid;
  let imports = [];
  let alive = true;
  let timer = null;
  const listEl =
      h('div.import-list#import-list', spinner('Loading imports\u2026'));

  root.appendChild(h(
      'div.page.page-imports',
      h('div.page-head',
        h('div', h('h1', 'Imports'),
          h('p.lede',
            'Figma files, Stitch projects and screenshots. Each import is analyzed into components, templates and token reference; you answer questions and confirm before anything is merged into the design system or the constitution.')),
        h('div.page-actions',
          h('button.btn.btn-primary#new-import', {
            type: 'button',
            onclick: () => openImportDrawer(pid, {origin: 'imports'})
          },
            icon('add'), 'New import'))),
      listEl));

  const tickTimer = setInterval(() => {
    if (alive) tickElapsed(listEl);
  }, 1000);
  await load();
  return () => {
    alive = false;
    clearTimeout(timer);
    clearInterval(tickTimer);
  };

  async function load(quiet = false) {
    try {
      const res = await get(`/api/projects/${pid}/imports`, {quiet});
      imports = res.imports || [];
    } catch (e) {
      if (!quiet)
        mount(listEl, emptyState('Could not load imports', e.message));
      return;
    }
    if (!alive) return;
    try {
      draw();
    } catch (err) {
      console.error('Failed to draw imports list:', err);
    }
    clearTimeout(timer);
    if (imports.some((i) => isActivePhase(i.phase)))
      timer = setTimeout(() => alive && load(true), 2000);
  }

  function draw() {
    if (!imports.length) {
      mount(
          listEl,
          emptyState(
              'No imports yet',
              'Bring in a Figma file or frame, a Stitch project, or a few screenshots. Gemini separates components, templates and style reference, asks about anything unclear, and drafts a merge plan you approve line by line.',
              h('button.btn.btn-primary', {
                type: 'button',
                onclick: () => openImportDrawer(pid, {origin: 'imports'})
              },
                icon('add'), 'New import')));
      return;
    }
    mount(listEl, imports.map(row));
  }

  function row(imp) {
    const href = `/p/${pid}/imports/${imp.id}`;
    const imgs = (imp.images || []).filter((im) => im.url);
    const c = imp.counts || {};
    const legacy = imp.phase === 'legacy';
    return h(
        'article.import-row', {
          id: `import-${imp.id}`,
          class: [`ph-${imp.phase}`, legacy ? 'legacy' : ''],
          dataset: {iid: imp.id}
        },
        h('a.import-thumbs',
          {href, 'aria-label': `Open ${imp.title || 'import'}`},
          imgs.length ?
              imgs.slice(0, 4).map(
                  (im) => h('img', {src: im.url, alt: '', loading: 'lazy'})) :
              h('span.import-thumbs-empty',
                icon((SOURCE[imp.kind] || SOURCE.screenshots).icon)),
          imgs.length > 4 ?
              h('span.import-thumbs-more', `+${imgs.length - 4}`) :
              null),
        h('div.import-row-main',
          h('div.title-row',
            h('a.import-title', {href}, imp.title || 'Untitled import'),
            phaseBadge(imp),
            legacy ? h('span.chip.chip-xs.chip-quiet', 'legacy') : null),
          h('div.import-meta.small.muted', sourceChip(imp),
            h('span', `${imgs.length} image${imgs.length === 1 ? '' : 's'}`),
            h('span', '\u00b7'), h('span', imp.created_by || ''),
            h('span', '\u00b7'),
            h('span', {title: imp.created_at || ''}, relTime(imp.created_at)),
            imp.origin && imp.origin !== 'imports' ?
                h('span.chip.chip-xs.chip-quiet', {title: 'Started from'},
                  `from ${
                      imp.origin === 'design' ? 'Design system' :
                                                'Constitution'}`) :
                null),
          imp.phase === 'error' && imp.error ?
              h('div.import-err.small', icon('error'), h('span', imp.error)) :
              null,
          progressLine(imp), countsLine(c)),
        h('div.import-row-side',
          c.merged ? h('span.chip.chip-xs.import-merged', icon('merge'),
                       `${c.merged} merged`) :
                     null,
          legacy && imp.chat_message_id ?
              h('button.btn.btn-ghost.btn-sm.import-open-chat', {
                type: 'button',
                onclick: () =>
                    emit('chat-focus', {message_id: imp.chat_message_id})
              },
                icon('forum'), 'Chat proposal') :
              null,
          h('a.btn.btn-ghost.btn-sm', {href},
            c.open_questions               ? [icon('help'), 'Answer'] :
                imp.phase === 'plan_ready' ? [icon('merge'), 'Review plan'] :
                                             [icon('arrow_forward'), 'Open'])));
  }
}
