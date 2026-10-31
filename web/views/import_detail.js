// Import detail: /p/:pid/imports/:iid. One import moves through
// Ingest → Analyze → Questions → Merge → Done. The page shows the source
// images with extraction regions overlaid, the extraction board (questions
// first, then components / templates / token reference / ignored), the merge
// plan grouped by destination, and the record of what was applied.
import {del, get, patch, post} from '../components/api.js';
import {basicFitChip} from '../components/component_badges.js';
import {diffView, lineDiff} from '../components/diff.js';
import {emptyState, fmtTime, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {renderMarkdown} from '../components/markdown.js';
import {emit, navigate, store} from '../components/store.js';
import {confirmDialog, modal, toast} from '../components/ui.js';

import {countsLine, defaultProgressLabel, isActivePhase, KIND, kindChip, KINDS, phaseBadge, progressCard, sourceChip, tickElapsed} from './imports.js';
import {designSystemPanel, factsSummary} from './references.js';

const STEPS = [
  {id: 'ingest', label: 'Ingest', icon: 'download'},
  {id: 'analyze', label: 'Analyze', icon: 'auto_awesome'},
  {id: 'questions', label: 'Questions', icon: 'help'},
  {id: 'merge', label: 'Merge', icon: 'merge'},
  {id: 'done', label: 'Done', icon: 'check'},
];
const PHASE_STEP = {
  ingesting: 0,
  analyzing: 1,
  review: 2,
  planning: 3,
  plan_ready: 3,
  merged: 4
};
const DEFAULT_OPTIONS = [
  {value: 'component', label: 'Component'},
  {value: 'template', label: 'Template'},
  {value: 'token_reference', label: 'Token reference'},
  {value: 'ignore', label: 'Ignore'},
];
const STATUS_LABEL = {
  proposed: 'Proposed',
  needs_answer: 'Needs answer',
  confirmed: 'Confirmed',
  ignored: 'Ignored',
  merged: 'Merged',
};
const PLAN_GROUPS = [
  {
    type: 'tokens',
    label: 'Tokens',
    icon: 'palette',
    dest: 'Design system \u203a Tokens'
  },
  {
    type: 'component',
    label: 'Components',
    icon: 'widgets',
    dest: 'Design system \u203a Components'
  },
  {
    type: 'template',
    label: 'Templates',
    icon: 'dashboard',
    dest: 'Design system \u203a Templates'
  },
  {
    type: 'constitution',
    label: 'Constitution',
    icon: 'article',
    dest: 'New constitution revision'
  },
  {
    type: 'data',
    label: 'Project data',
    icon: 'dataset',
    dest: 'Data library & default run data'
  },
];
const ACTION_LABEL = {
  draft_custom: 'Draft custom component',
  map_basic: 'Map to Basic',
  link_existing: 'Link existing',
};
const STRATEGY_SHORT = {
  web_template: 'custom web',
  basic_composition: 'Basic composition',
  native_targets: 'native targets',
};
const COLOR_RE = /^(#[0-9a-f]{3,8}|rgba?\(|hsla?\()/i;

/**
 * @param {*} root
 * @param {*} params
 * @param {*} ctx
 * @return {*}
 */
export async function render(root, params, ctx) {
  const pid = params.pid;
  const iid = params.iid;
  let imp = null;
  let lastJson = '';
  let alive = true;
  let timer = null;
  let activeImage = 0;
  let drawMode = false;
  let pending = null;  // drawn box awaiting kind/title: {image_index, box}
  const selected = new Set();  // xids selected for "Merge into one"
  const notes = new Map();     // xid/qid → note typed into a question
  let planSel = new Map();     // line id → bool
  let planStrategies = new Map();  // line id → '' (auto) | strategy override
  let planKey = '';            // prepared_at of the plan planSel belongs to
  let constEdit = null;        // edited constitution_md (null = unchanged)
  let constBase = null;  // current constitution text (for the diff fallback)
  let setDefaultData =
      true;  // whether applying data sets project default data source

  const headEl = h('div.import-head');
  const stepperEl = h('ol.import-stepper#import-stepper');
  const bannerEl = h('div.import-banner');
  const viewerEl = h('div.iv#import-viewer');
  const boardEl = h('div.xboard#extraction-board');
  const planEl = h('section.import-plan#merge-plan');
  const recordEl = h('section.import-record#import-record');
  const factsEl = h('div.import-facts');

  root.appendChild(
      h('div.page.page-import',
        h('div.crumbs', h('a', {href: `/p/${pid}/imports`}, 'Imports'),
          h('span', '/'), h('span#import-crumb', '\u2026')),
        headEl, stepperEl, bannerEl,
        h('div.import-work', h('div.import-left', viewerEl),
          h('div.import-right', boardEl)),
        planEl, recordEl, factsEl));
  mount(boardEl, spinner('Loading import\u2026'));

  const tickTimer = setInterval(() => {
    if (alive && imp && isActivePhase(imp.phase)) tickElapsed(root);
  }, 1000);
  await load();
  if (imp && location.hash.startsWith('#x-')) {
    requestAnimationFrame(() => focusCard(location.hash.slice(3)));
  }
  return () => {
    alive = false;
    clearTimeout(timer);
    clearInterval(tickTimer);
  };

  // ------------------------------------------------------------- data

  async function load(quiet = false) {
    let res;
    try {
      res = await get(`/api/imports/${iid}`, {quiet});
    } catch (e) {
      if (!quiet) {
        mount(
            boardEl,
            emptyState(
                'Could not load this import', e.message,
                h('a.btn.btn-ghost', {href: `/p/${pid}/imports`},
                  'All imports')));
      }
      return;
    }
    if (!alive) return;
    const json = JSON.stringify(res.import);
    if (json !== lastJson) {
      lastJson = json;
      imp = res.import;
      try {
        drawAll();
      } catch (err) {
        console.error('Failed to render import detail:', err);
      }
    }
    clearTimeout(timer);
    if (isActivePhase(imp.phase))
      timer = setTimeout(() => alive && load(true), 2000);
  }
  function reload() {
    return load(true);
  }
  // Function declarations (hoisted): drawAll() runs during the first load().
  function xs() {
    return (imp && imp.extractions) || [];
  }
  function xById(id) {
    return xs().find((x) => x.id === id);
  }
  function images() {
    return (imp && imp.images) || [];
  }
  function openQuestions() {
    return (imp.questions || []).filter((q) => !q.answer);
  }
  function needsAnswer() {
    return xs().filter((x) => x.status === 'needs_answer');
  }
  function proposed() {
    return xs().filter((x) => x.status === 'proposed');
  }

  async function act(fn, okMsg) {
    try {
      const r = await fn();
      if (okMsg) toast(okMsg, 'good', 2400);
      await reload();
      return r;
    } catch (e) {
      // api() already toasted.
      return null;
    }
  }

  // ------------------------------------------------------------- draw

  function drawAll() {
    document.getElementById('import-crumb').textContent =
        imp.title || 'Untitled import';
    if (activeImage >= images().length) activeImage = 0;
    drawHead();
    drawStepper();
    drawBanner();
    drawViewer();
    drawBoard();
    drawPlan();
    drawRecord();
    drawFacts();
  }

  function drawHead() {
    mount(
        headEl,
        h('div.page-head.page-head-tight',
          h('div.import-head-main',
            h('div.title-row',
              h('h1#import-title', imp.title || 'Untitled import'),
              phaseBadge(imp)),
            h('div.import-meta.small.muted', sourceChip(imp),
              h('span',
                `${images().length} image${images().length === 1 ? '' : 's'}`),
              h('span', '\u00b7'), h('span', imp.created_by || ''),
              h('span', '\u00b7'),
              h('span', {title: imp.created_at || ''}, relTime(imp.created_at)),
              imp.analysis && imp.analysis.model ?
                  [h('span', '\u00b7'), h('span', imp.analysis.model)] :
                  null),
            imp.notes ? h('p.import-notes.small', imp.notes) : null),
          h('div.page-actions',
            h('button.btn.btn-ghost.btn-sm#import-edit',
              {type: 'button', onclick: openEdit}, icon('edit'), 'Edit'),
            h('button.btn.btn-ghost.btn-sm#import-delete',
              {type: 'button', onclick: remove}, icon('delete'), 'Delete'))));
  }

  function drawStepper() {
    if (imp.phase === 'legacy') {
      mount(stepperEl);
      stepperEl.hidden = true;
      return;
    }
    stepperEl.hidden = false;
    let cur = PHASE_STEP[imp.phase];
    let err = false;
    if (imp.phase === 'error') {
      err = true;
      cur = images().length ? 1 : 0;
    }
    const nOpen = needsAnswer().length + openQuestions().length;
    const prog = imp.progress || {};
    const pDone = Number(prog.done) || 0;
    const pTotal = Number(prog.total) || 0;
    const sub = {
      ingest: imp.phase === 'ingesting' && pTotal > 0 ?
          `${pDone}/${pTotal} image${pTotal === 1 ? '' : 's'}` :
          `${images().length} image${images().length === 1 ? '' : 's'}`,
      analyze: imp.phase === 'analyzing' ?
          (prog.step === 'crop_regions' && pTotal > 0 ?
               `cropping ${pDone}/${pTotal}\u2026` :
               prog.step === 'scan_components' ?
               'scanning components\u2026' :
               xs().length ?
               `${xs().length} extraction${xs().length === 1 ? '' : 's'}` :
               'running Gemini\u2026') :
          xs().length ?
          `${xs().length} extraction${xs().length === 1 ? '' : 's'}` :
          '',
      questions: nOpen ? `${nOpen} open` : (cur >= 2 ? 'answered' : ''),
      merge: imp.phase === 'planning' ?
          (prog.step === 'build_diff' ? 'building diff\u2026' :
                                        'drafting plan\u2026') :
          imp.phase === 'plan_ready' ? 'plan ready' :
                                       '',
      done: (imp.counts && imp.counts.merged) ? `${imp.counts.merged} merged` :
                                                '',
    };
    mount(stepperEl, STEPS.map((s, i) => {
      const state = i < cur ? 'done' :
          i === cur         ? (err ? 'error' : 'current') :
                              'todo';
      const spinning = state === 'current' && isActivePhase(imp.phase);
      return h(
          'li.istep', {class: `is-${state}`, dataset: {step: s.id}},
          h('span.istep-dot',
            spinning ? h('span.spinner.spinner-sm') :
                       icon(
                           state === 'done'      ? 'check' :
                               state === 'error' ? 'priority_high' :
                                                   s.icon)),
          h('span.istep-text', h('span.istep-label', s.label),
            sub[s.id] && i <= cur ? h('span.istep-sub', sub[s.id]) : null));
    }));
  }

  function drawBanner() {
    const parts = [];
    if (imp.phase === 'ingesting' || imp.phase === 'analyzing') {
      const card = progressCard(imp);
      if (card) parts.push(card);
    } else if (imp.phase === 'error') {
      parts.push(
          h('div.callout.callout-bad#import-error', icon('error'),
            h('div.grow', h('div', imp.error || 'Import failed.')),
            images().length ? h('button.btn.btn-ghost.btn-sm',
                                {type: 'button', onclick: () => openAnalyze()},
                                icon('refresh'), 'Retry analysis') :
                              null));
    } else if (imp.phase === 'legacy') {
      parts.push(h(
          'div.callout.callout-warn#import-legacy', icon('history'),
          h('div.grow', h('div.callout-title', 'Legacy design reference'),
            h('div',
              'This reference was analyzed straight into a chat proposal before Imports existed. Re-analyze it to split it into components, templates and token reference.')),
          h('div.row-inline',
            imp.chat_message_id ?
                h('button.btn.btn-ghost.btn-sm#legacy-open-chat', {
                  type: 'button',
                  onclick: () =>
                      emit('chat-focus', {message_id: imp.chat_message_id})
                },
                  icon('forum'), 'Chat proposal') :
                null,
            images().length ?
                h('button.btn.btn-primary.btn-sm#legacy-reanalyze',
                  {type: 'button', onclick: () => openAnalyze()},
                  icon('auto_awesome'), 'Analyze as import') :
                null)));
    }
    if (imp.phase === 'merged' && (imp.merge_log || []).length) {
      const latest = imp.merge_log[imp.merge_log.length - 1] || {};
      const app = latest.applied || [];
      const nComp = app.filter((a) => a.type === 'component').length;
      const nTpl = app.filter((a) => a.type === 'template').length;
      const nTok = app.filter((a) => a.type === 'tokens').length;
      const nData = app.filter((a) => a.type === 'data').length;
      const hasRev = Boolean(
          latest.revision_id || app.some((a) => a.type === 'constitution'));
      const projDs = store.project?.settings?.data_source || {};
      const isDefaultDs = Boolean(
          (latest.sample_set_id &&
           projDs.sample_set_id === latest.sample_set_id) ||
          projDs.import_id === iid || latest.default_data_source_set);
      parts.push(h(
          'div.callout.callout-good.import-merged-banner#import-merged-banner',
          icon('check_circle'),
          h('div.grow',
            h('div.callout-title.row-inline', 'Merged into project',
              isDefaultDs && (nData || latest.sample_set_id) ?
                  h('span.chip.chip-xs.v-good#merged-default-data-badge',
                    icon('dataset'), 'Default run data source') :
                  null),
            h('div.small',
              'Components, templates, tokens, project data and layout guidance from this import are now in your Design system, Data library and Constitution.')),
          h('div.row-inline',
            nComp ? h('a.btn.btn-primary.btn-sm#merged-open-components',
                      {href: `/p/${pid}/design?tab=components`},
                      icon('widgets'), `Components (${nComp})`) :
                    null,
            nTpl ? h('a.btn.btn-primary.btn-sm#merged-open-templates',
                     {href: `/p/${pid}/design?tab=templates`},
                     icon('dashboard'), `Templates (${nTpl})`) :
                   null,
            (nData || latest.sample_set_id) ?
                h('a.btn.btn-primary.btn-sm#merged-open-data', {
                  href: latest.sample_set_id ?
                      `/data?set=${encodeURIComponent(latest.sample_set_id)}` :
                      '/data',
                },
                  icon('dataset'),
                  `Project data${nData ? ` (${nData})` : ''}`) :
                null,
            !isDefaultDs && (nData || (imp.counts && imp.counts.data)) ?
                h('button.btn.btn-ghost.btn-sm#merged-set-default-data', {
                  type: 'button',
                  onclick: () => act(
                      async () => {
                        const r =
                            await post(`/api/imports/${iid}/data-source`, {});
                        if (r?.data_source && store.project) {
                          store.project.settings = {
                            ...(store.project.settings || {}),
                            data_source: r.data_source,
                          };
                          emit('settings-changed', {
                            settings: store.project.settings,
                            source: 'import',
                          });
                        }
                        return r;
                      },
                      'Set as default data source for runs'),
                },
                  icon('play_circle'), 'Set as default run data') :
                null,
            nTok ? h('a.btn.btn-ghost.btn-sm#merged-open-tokens',
                     {href: `/p/${pid}/design?tab=tokens`}, icon('palette'),
                     `Tokens (${nTok})`) :
                   null,
            hasRev ? h('a.btn.btn-ghost.btn-sm#merged-open-constitution',
                       {href: `/p/${pid}/constitution`}, icon('article'),
                       'Constitution') :
                     null)));
    }
    if (imp.analysis && imp.analysis.summary && imp.phase !== 'legacy') {
      parts.push(
          h('div.import-summary', icon('auto_awesome'),
            h('div', renderMarkdown(imp.analysis.summary, {className: 'md'}))));
    }
    mount(bannerEl, parts);
  }

  // ------------------------------------------------------------- viewer

  function regionsOn(index) {
    const out = [];
    for (const x of xs()) {
      const r = x.region || {};
      if ((r.image_index ?? 0) === index)
        out.push({x, box: r.box || null, inst: false});
      for (const ins of r.instances || []) {
        if ((ins.image_index ?? 0) === index)
          out.push({x, box: ins.box || null, inst: true});
      }
    }
    return out;
  }

  function canDraw(im) {
    return !!im && im.role !== 'component' && !isActivePhase(imp.phase) &&
        imp.phase !== 'merged' && imp.phase !== 'legacy';
  }

  function drawViewer() {
    const imgs = images();
    if (!imgs.length) {
      mount(
          viewerEl,
          h('div.iv-empty',
            isActivePhase(imp.phase) ?
                spinner('Waiting for images\u2026') :
                h('span.muted', icon('hide_image'), ' No images')));
      return;
    }
    const im = imgs[activeImage];
    const stage = h('div.iv-stage', {class: drawMode ? 'drawing' : ''});
    const imgEl =
        h('img.iv-img', {src: im.url, alt: im.name || '', draggable: 'false'});
    stage.appendChild(imgEl);
    for (const {x, box, inst} of regionsOn(activeImage)) {
      stage.appendChild(boxEl(x, box, inst));
    }
    if (pending && pending.image_index === activeImage) {
      stage.appendChild(
          h('div.iv-box.iv-pending', {style: boxStyle(pending.box)}));
    }
    if (drawMode) attachDraw(stage);
    const drawable = canDraw(im);
    if (!drawable && drawMode) drawMode = false;
    mount(
        viewerEl,
        h('div.iv-bar',
          h('div.iv-name.small', {title: im.name || ''},
            h('strong', im.name || `Image ${activeImage + 1}`),
            h('span.muted',
              ` ${im.role || ''}${
                  im.width ? ` \u00b7 ${im.width}\u00d7${im.height}` : ''}`)),
          h('span.grow'),
          im.html_url ? h('a.btn.btn-ghost.btn-sm#iv-open-html', {
            href: im.html_url,
            target: '_blank',
            rel: 'noopener',
            title: 'Open the raw Stitch HTML for this screen',
          },
                          icon('code'), 'Screen HTML') :
                        null,
          h('div.iv-legend',
            KINDS.map(
                (k) =>
                    h('span.iv-legend-item', {class: `k-${k}`},
                      h('span.iv-legend-sw'), KIND[k].label))),
          drawable ? h('button.btn.btn-sm#iv-draw', {
            type: 'button',
            class: drawMode ? 'btn-primary' : 'btn-ghost',
            title: 'Drag on the image to mark a region the analysis missed',
            onclick: () => {
              drawMode = !drawMode;
              pending = null;
              drawViewer();
            }
          },
                       icon(drawMode ? 'close' : 'crop_free'),
                       drawMode ? 'Cancel' : 'Add region') :
                     null),
        drawMode ? h('div.iv-hint.small', icon('gesture'),
                     'Drag a box around the part you want to add.') :
                   null,
        h('div.iv-stage-wrap', stage), pending ? pendingForm() : null,
        imgs.length > 1 ? h('div.iv-thumbs#iv-thumbs', imgs.map((m, i) => {
          const n = regionsOn(i).length;
          return h(
              'button.iv-thumb', {
                type: 'button',
                class: i === activeImage ? 'on' : '',
                title: m.name || `Image ${i + 1}`,
                onclick: () => {
                  activeImage = i;
                  pending = null;
                  drawViewer();
                }
              },
              m.url ? h('img', {src: m.url, alt: '', loading: 'lazy'}) :
                      icon('hide_image'),
              n ? h('span.iv-thumb-n', String(n)) : null);
        })) :
                          null);
  }

  function boxStyle(box) {
    const [x0, y0, x1, y1] = box || [0, 0, 1000, 1000];
    return {
      left: `${x0 / 10}%`,
      top: `${y0 / 10}%`,
      width: `${Math.max(0.5, (x1 - x0) / 10)}%`,
      height: `${Math.max(0.5, (y1 - y0) / 10)}%`,
    };
  }

  function boxEl(x, box, inst) {
    return h(
        'div.iv-box', {
          class: [
            `k-${x.kind}`, `st-${x.status}`, box ? '' : 'whole',
            inst ? 'inst' : '',
            box && (box[2] - box[0]) * (box[3] - box[1]) > 400000 ? 'big' : ''
          ],
          style: boxStyle(box),
          dataset: {xid: x.id},
          title: `${KIND[x.kind] ? KIND[x.kind].label : x.kind}: ${
              x.title || ''}${inst ? ' (instance)' : ''}`,
          onmouseenter: () => hover(x.id, true),
          onmouseleave: () => hover(x.id, false),
          onclick: (e) => {
            if (drawMode) return;
            e.stopPropagation();
            focusCard(x.id);
          },
        },
        h('span.iv-tag', icon(KIND[x.kind] ? KIND[x.kind].icon : 'help'),
          h('span', x.title || 'Untitled')));
  }

  function attachDraw(stage) {
    let start = null;
    let ghost = null;
    const pt = (e) => {
      const r = stage.getBoundingClientRect();
      return [
        Math.round(
            Math.min(1000, Math.max(0, (e.clientX - r.left) / r.width * 1000))),
        Math.round(
            Math.min(1000, Math.max(0, (e.clientY - r.top) / r.height * 1000))),
      ];
    };
    const norm = (a, b) =>
        [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]),
         Math.max(a[1], b[1])];
    stage.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      stage.setPointerCapture(e.pointerId);
      start = pt(e);
      ghost =
          h('div.iv-box.iv-pending', {style: boxStyle([...start, ...start])});
      stage.appendChild(ghost);
    });
    stage.addEventListener('pointermove', (e) => {
      if (!start) return;
      Object.assign(ghost.style, boxStyle(norm(start, pt(e))));
    });
    stage.addEventListener('pointerup', (e) => {
      if (!start) return;
      const box = norm(start, pt(e));
      start = null;
      if (box[2] - box[0] < 12 || box[3] - box[1] < 12) {
        ghost.remove();
        return;
      }
      pending = {image_index: activeImage, box};
      drawMode = false;
      drawViewer();
      setTimeout(() => document.getElementById('iv-new-title')?.focus(), 0);
    });
  }

  function pendingForm() {
    const title =
        h('input.input.input-sm#iv-new-title',
          {placeholder: 'Name, e.g. Order summary card'});
    let kind = 'component';
    const kindBtns =
        h('div.kind-pick#iv-new-kind',
          KINDS.map(
              (k) =>
                  h('button.kind-opt', {
                    type: 'button',
                    class: [`k-${k}`, k === kind ? 'on' : ''],
                    dataset: {kind: k},
                    onclick: (e) => {
                      kind = k;
                      kindBtns.querySelectorAll('.kind-opt')
                          .forEach(
                              (b) => b.classList.toggle(
                                  'on', b.dataset.kind === k));
                    }
                  },
                    icon(KIND[k].icon), KIND[k].label)));
    const desc =
        h('input.input.input-sm#iv-new-desc',
          {placeholder: 'Optional note for the analysis'});
    const add = async () => {
      const body = {
        kind,
        title: title.value.trim() || KIND[kind].label,
        description: desc.value.trim(),
        region: {image_index: pending.image_index, box: pending.box},
      };
      const r = await act(
          () => post(`/api/imports/${iid}/extractions`, body), 'Region added');
      if (r) pending = null;
      drawViewer();
    };
    title.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') add();
    });
    return h(
        'div.iv-new#iv-new-region',
        h('div.label', icon('crop_free'), 'New region'), title, kindBtns, desc,
        h('div.row-inline',
          h('button.btn.btn-primary.btn-sm#iv-new-add',
            {type: 'button', onclick: add}, icon('add'), 'Add extraction'),
          h('button.btn.btn-quiet.btn-sm', {
            type: 'button',
            onclick: () => {
              pending = null;
              drawViewer();
            }
          },
            'Cancel')));
  }

  function hover(xid, on) {
    document.querySelectorAll(`[data-xid="${CSS.escape(xid)}"]`)
        .forEach((el) => el.classList.toggle('hot', on));
  }

  /** Scrolls the board to an extraction card and flashes it. */
  function focusCard(xid) {
    const el = document.getElementById(`x-${xid}`);
    if (!el) return;
    el.scrollIntoView({block: 'center', behavior: 'smooth'});
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 2200);
  }

  /** Shows an extraction's image in the viewer and pulses its box. */
  function showInViewer(x) {
    const idx = (x.region && x.region.image_index) || 0;
    if (idx !== activeImage) {
      activeImage = idx;
      pending = null;
      drawViewer();
    }
    const el =
        viewerEl.querySelector(`.iv-box[data-xid="${CSS.escape(x.id)}"]`);
    if (el) {
      el.classList.remove('pulse');
      void el.offsetWidth;
      el.classList.add('pulse');
    }
    viewerEl.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  }

  // ------------------------------------------------------------- crops

  /** Crop of an extraction: crop_url if present, else a CSS crop by box. */
  /**
   * @param {*} x
   * @param {*} W
   * @param {*} H
   * @return {*}
   */
  function cropOf(x, W, H) {
    const wrap = h('div.xcrop', {
      style: {width: `${W}px`, height: `${H}px`},
      title: 'Show in the source image',
      onclick: () => showInViewer(x),
    });
    if (x.crop_url) {
      wrap.appendChild(
          h('img.xcrop-img', {src: x.crop_url, alt: '', loading: 'lazy'}));
      return wrap;
    }
    const r = x.region || {};
    const im = images()[r.image_index || 0];
    if (!im || !im.url) {
      wrap.appendChild(icon('image_not_supported'));
      return wrap;
    }
    wrap.appendChild(cssCrop(im, r.box, W, H));
    return wrap;
  }

  function cssCrop(im, box, W, H) {
    const inner = h('div.xcrop-inner');
    const img =
        h('img', {src: im.url, alt: '', loading: 'lazy', draggable: 'false'});
    inner.appendChild(img);
    const layout = (iw, ih) => {
      const [x0, y0, x1, y1] = box || [0, 0, 1000, 1000];
      const cw = Math.max(1, (x1 - x0)) / 1000 * iw;
      const ch = Math.max(1, (y1 - y0)) / 1000 * ih;
      const s = Math.min(W / cw, H / ch);
      Object.assign(inner.style, {width: `${cw * s}px`, height: `${ch * s}px`});
      Object.assign(img.style, {
        width: `${iw * s}px`,
        height: `${ih * s}px`,
        left: `${- x0 / 1000 * iw * s}px`,
        top: `${- y0 / 1000 * ih * s}px`,
      });
    };
    if (im.width && im.height) {
      layout(im.width, im.height);
    } else {
      layout(1000, 1000);
      img.addEventListener(
          'load', () => layout(img.naturalWidth, img.naturalHeight));
    }
    return inner;
  }

  // ------------------------------------------------------------- board

  function drawBoard() {
    if (!xs().length && !(imp.questions || []).length) {
      const prog = imp.progress || {};
      mount(
          boardEl,
          isActivePhase(imp.phase) ?
              h('div.xboard-wait',
                spinner(defaultProgressLabel(imp) || 'Analyzing\u2026'),
                prog.detail ?
                    h('p.xboard-wait-sub.small.muted', prog.detail) :
                    h('p.xboard-wait-sub.small.muted',
                      imp.phase === 'ingesting' ?
                          'Once source frames finish downloading, Gemini will separate components, templates and token reference here.' :
                          'Extractions and clarifying questions will appear here as soon as Gemini finishes analyzing the frames.')) :
              imp.phase === 'legacy' || imp.phase === 'error' ?
              null :
              emptyState(
                  'No extractions',
                  'Re-analyze, or draw regions on the image to add them by hand.',
                  h('button.btn.btn-ghost',
                    {type: 'button', onclick: () => openAnalyze()},
                    icon('auto_awesome'), 'Re-analyze')));
      return;
    }
    const busy = isActivePhase(imp.phase);
    const locked = busy || imp.phase === 'legacy';
    const nOpen = needsAnswer().length + openQuestions().length;
    const nProposed = proposed().length;
    const hasConfirmedOrData = xs().some((x) => x.status === 'confirmed') ||
        Boolean(imp.counts && imp.counts.data);
    const prepReason = busy ?
        'Wait for the current step to finish.' :
        nOpen ?
        `Answer ${nOpen} open question${nOpen === 1 ? '' : 's'} first.` :
        !hasConfirmedOrData ? 'Confirm at least one extraction first.' :
                              '';
    const bar = h(
        'div.xboard-bar', h('div.xboard-counts', countsLine(imp.counts)),
        h('div.xboard-actions',
          selected.size >= 2 ?
              h('button.btn.btn-ghost.btn-sm#x-merge-dupes', {
                type: 'button',
                disabled: locked,
                title: 'Fold duplicates into one extraction',
                onclick: () => openMergeDupes()
              },
                icon('join'), `Merge ${selected.size} into one`) :
              null,
          h('button.btn.btn-ghost.btn-sm#x-confirm-all', {
            type: 'button',
            disabled: locked || !nProposed,
            onclick: () =>
                act(() => post(`/api/imports/${iid}/confirm`, {}), 'Confirmed')
          },
            icon('done_all'),
            `Confirm all proposed${nProposed ? ` (${nProposed})` : ''}`),
          h('button.btn.btn-ghost.btn-sm#x-reanalyze', {
            type: 'button',
            disabled: locked,
            title:
                'Run the analysis again; your answers, confirmations and regions are kept and sent as context',
            onclick: () => openAnalyze()
          },
            icon('auto_awesome'), 'Re-analyze with my answers'),
          h('span.prep-wrap', {title: prepReason || null},
            h('button.btn.btn-primary.btn-sm#x-prepare', {
              type: 'button',
              disabled: !!prepReason,
              onclick: () => openPrepare()
            },
              icon('merge'),
              imp.merge_plan &&
                      (imp.phase === 'plan_ready' || imp.phase === 'merged') ?
                  'Re-plan merge' :
                  'Prepare merge'))),
        prepReason && !busy && imp.phase !== 'merged' ?
            h('div.xboard-reason.small', icon('info'), prepReason) :
            (!prepReason && nProposed && imp.phase !== 'merged') ?
            h('div.xboard-reason.small', icon('info'),
              `${nProposed} proposed extraction${
                  nProposed === 1 ?
                      ' is' :
                      's are'} not confirmed yet and will be left out of the merge plan.`) :
            null);

    const sections = [bar, questionsSection(locked)];
    for (const k of KINDS) {
      const list =
          xs().filter((x) => x.kind === k && x.status !== 'needs_answer');
      if (!list.length) continue;
      sections.push(
          h('section.xsec', {class: `k-${k}`, id: `xsec-${k}`},
            h('div.xsec-head',
              h('h3', icon(KIND[k].icon), KIND[k].plural,
                h('span.count', ` ${list.length}`))),
            h('div.xgrid', list.map((x) => xCard(x, locked)))));
    }
    mount(boardEl, sections);
  }

  function questionsSection(locked) {
    const qs = imp.questions || [];
    const xq = needsAnswer();
    if (!qs.length && !xq.length) return null;
    const open = openQuestions().length + xq.length;
    return h(
        'section.xsec.xsec-questions#needs-answer',
        h('div.xsec-head',
          h('h3', icon('help'), open ? 'Needs your answer' : 'Questions',
            h('span.count', open ? ` ${open} open` : ' all answered')),
          open ?
              h('p.help',
                'Gemini wasn\u2019t sure about these. Your answers decide where each part goes.') :
              null),
        qs.map((q) => importQuestion(q, locked)),
        xq.map((x) => xQuestion(x, locked)));
  }

  function noteInput(key, placeholder = 'Optional note') {
    return h('input.input.input-sm.q-note', {
      value: notes.get(key) || '',
      placeholder,
      'aria-label': 'Note',
      oninput: (e) => notes.set(key, e.target.value),
    });
  }

  function importQuestion(q, locked) {
    const answered = q.answer && q.answer.choice != null;
    const opts = (q.options || []).length ? q.options : [];
    const note =
        noteInput(`q:${q.id}`, opts.length ? 'Optional note' : 'Your answer');
    const answer = (choice) =>
        act(() => post(
                `/api/imports/${iid}/questions/${q.id}`,
                {answer: {choice, note: note.value.trim()}}),
            'Answered');
    const about = (q.about || []).map(xById).filter(Boolean);
    return h(
        'article.qcard.qcard-import',
        {id: `q-${q.id}`, class: answered ? 'answered' : ''},
        h('div.qcard-body',
          h('div.qcard-kicker.small', icon('forum'), 'About this import'),
          h('div.qcard-text', q.text),
          about.length ?
              h('div.chip-row.qcard-about', about.map((x) => xLinkChip(x))) :
              null,
          h('div.q-opts',
            opts.map(
                (o) =>
                    h('button.q-opt', {
                      type: 'button',
                      class: answered && q.answer.choice === o.value ? 'on' :
                                                                       '',
                      disabled: locked,
                      onclick: () => answer(o.value)
                    },
                      o.label || o.value)),
            note,
            !opts.length ? h('button.btn.btn-primary.btn-sm', {
              type: 'button',
              disabled: locked,
              onclick: () => note.value.trim() && answer(note.value.trim())
            },
                             'Answer') :
                           null),
          answered ? h('div.q-answered.small', icon('check'),
                       `Answered${q.answer.by ? ` by ${q.answer.by}` : ''}${
                           q.answer.note ? ` \u2014 ${q.answer.note}` : ''}`) :
                     null));
  }

  function xQuestion(x, locked) {
    const opts = (x.question && (x.question.options || []).length) ?
        x.question.options :
        DEFAULT_OPTIONS;
    const note = noteInput(`x:${x.id}`);
    return h(
        'article.qcard', {
          id: `x-${x.id}`,
          class: `k-${x.kind}`,
          dataset: {xid: x.id},
          onmouseenter: () => hover(x.id, true),
          onmouseleave: () => hover(x.id, false),
        },
        cropOf(x, 168, 128),
        h('div.qcard-body',
          h('div.qcard-kicker.small', kindChip(x.kind),
            h('span.muted', 'Gemini\u2019s guess'),
            x.confidence != null ?
                h('span.muted', `\u00b7 ${Math.round(x.confidence * 100)}%`) :
                null),
          h('div.qcard-title', x.title || 'Untitled'),
          h('div.qcard-text',
            (x.question && x.question.text) ||
                'Is this a component, a template, or token reference?'),
          x.rationale ? h('p.qcard-why.small.muted', x.rationale) : null,
          h('div.q-opts',
            opts.map(
                (o) =>
                    h('button.q-opt', {
                      type: 'button',
                      class: KIND[o.value] ? `k-${o.value}` : '',
                      disabled: locked,
                      dataset: {choice: o.value},
                      onclick: () =>
                          act(() => patch(
                                  `/api/imports/${iid}/extractions/${x.id}`, {
                                    answer: {
                                      choice: o.value,
                                      note: note.value.trim()
                                    }
                                  }),
                              `Marked as ${o.label || o.value}`)
                    },
                      KIND[o.value] ? icon(KIND[o.value].icon) : null,
                      o.label || o.value)),
            note)));
  }

  function xLinkChip(x) {
    return h(
        'button.chip.chip-xs.x-link', {
          type: 'button',
          class: `k-${x.kind}`,
          title: `${KIND[x.kind] ? KIND[x.kind].label : x.kind}: ${x.title}`,
          onmouseenter: () => hover(x.id, true),
          onmouseleave: () => hover(x.id, false),
          onclick: () => focusCard(x.id),
        },
        icon(KIND[x.kind] ? KIND[x.kind].icon : 'help'), x.title || 'Untitled');
  }

  function usesChips(x) {
    const uses = x.uses || [];
    const usedBy = (x.used_by || []).map(xById).filter(Boolean);
    if (!uses.length && !usedBy.length) return null;
    return h(
        'div.xuses',
        uses.length ?
            h('div.chip-row', h('span.xuses-label', 'uses'), uses.map((u) => {
              if (u.xid) {
                const t = xById(u.xid);
                return t ? xLinkChip(t) :
                           h('span.chip.chip-xs.chip-quiet', u.name || u.xid);
              }
              if (u.component_id) {
                return h(
                    'a.chip.chip-xs.chip-quiet', {
                      href: `/p/${pid}/design/components/${u.component_id}`,
                      title: 'Existing custom component'
                    },
                    icon('widgets'), u.name || u.component_id);
              }
              return h(
                  'span.chip.chip-xs.chip-quiet',
                  {title: 'Basic catalog component'},
                  `Basic \u00b7 ${u.basic || u.name}`);
            })) :
            null,
        usedBy.length ? h('div.chip-row', h('span.xuses-label', 'used by'),
                          usedBy.map((t) => xLinkChip(t))) :
                        null);
  }

  function payloadBlock(x) {
    const p = x.payload || {};
    if (x.kind === 'component' && p.component) {
      const c = p.component;
      return h(
          'div.xpayload.small', c.purpose ? h('div', c.purpose) : null,
          (c.maps_to_basic || []).length ?
              h('div.chip-row', h('span.xuses-label', 'maps to'),
                c.maps_to_basic.map(
                    (b) => h(
                        'span.chip.chip-xs.chip-quiet', `Basic \u00b7 ${b}`))) :
              null,
          c.matches_component_id ?
              h('a.small',
                {href: `/p/${pid}/design/components/${c.matches_component_id}`},
                icon('link'), 'Matches an existing component') :
              null);
    }
    if (x.kind === 'template' && p.template) {
      const t = p.template;
      return h(
          'div.xpayload.small',
          t.when_to_use ?
              h('div', h('span.xuses-label', 'when '), t.when_to_use) :
              null,
          (t.slots || []).length ?
              h('div.chip-row', h('span.xuses-label', 'slots'),
                t.slots.map((s) => h('span.chip.chip-xs.chip-quiet', s))) :
              null);
    }
    if (x.kind === 'token_reference' && (p.tokens || []).length) {
      return h(
          'div.xtokens',
          p.tokens.slice(0, 12).map(
              (t) =>
                  h('span.xtoken', {
                    title: `${t.token}: ${t.value}${
                        t.reason ? ` \u2014 ${t.reason}` : ''}`
                  },
                    swatch(t.value), h('code', t.token),
                    t.confidence === 'approx' ? h('span.muted', '\u2248') :
                                                null)),
          p.tokens.length > 12 ?
              h('span.muted.small', `+${p.tokens.length - 12}`) :
              null);
    }
    return null;
  }

  function mergedChips(x) {
    const m = x.merged_into || [];
    if (!m.length) return null;
    return h(
        'div.chip-row.xmerged', h('span.xuses-label', icon('merge'), 'became'),
        m.map(
            (t) => t.url ?
                h('a.chip.chip-xs.merged-chip', {href: t.url},
                  t.label || t.type) :
                h('span.chip.chip-xs.merged-chip', t.label || t.type)));
  }

  function xCard(x, locked) {
    const editable = !locked && x.status !== 'merged';
    const sel = h('input.xsel', {
      type: 'checkbox',
      checked: selected.has(x.id),
      disabled: !editable,
      'aria-label': `Select ${x.title}`,
      title: 'Select to merge duplicates',
      onchange: (e) => {
        if (e.target.checked)
          selected.add(x.id);
        else
          selected.delete(x.id);
        drawBoard();
      },
    });
    const title = h('input.xtitle', {
      value: x.title || '',
      disabled: !editable,
      'aria-label': 'Title',
      onkeydown: (e) => {
        if (e.key === 'Enter') e.target.blur();
        if (e.key === 'Escape') {
          e.target.value = x.title || '';
          e.target.blur();
        }
      },
      onchange: (e) => {
        const v = e.target.value.trim();
        if (v && v !== x.title)
          act(() =>
                  patch(`/api/imports/${iid}/extractions/${x.id}`, {title: v}));
      },
    });
    const kindSel = h(
        'select.xkind', {
          class: `k-${x.kind}`,
          disabled: !editable,
          'aria-label': 'Kind',
          onchange: (e) =>
              act(() => patch(
                      `/api/imports/${iid}/extractions/${x.id}`,
                      {kind: e.target.value}),
                  `Moved to ${KIND[e.target.value].plural}`),
        },
        KINDS.map(
            (k) => h(
                'option', {value: k, selected: k === x.kind}, KIND[k].label)));
    return h(
        'article.xcard', {
          id: `x-${x.id}`,
          class: [
            `k-${x.kind}`, `st-${x.status}`,
            selected.has(x.id) ? 'selected' : ''
          ],
          dataset: {xid: x.id},
          onmouseenter: () => hover(x.id, true),
          onmouseleave: () => hover(x.id, false),
        },
        h('div.xcard-top', cropOf(x, 112, 84),
          h('div.xcard-head', h('div.xcard-title-row', sel, title),
            h('div.xcard-meta', kindSel,
              h('span.xstatus', {class: `st-${x.status}`},
                STATUS_LABEL[x.status] || x.status),
              x.confidence != null ?
                  h('span.xconf.small', {title: 'Analysis confidence'},
                    `${Math.round(x.confidence * 100)}%`) :
                  null,
              (x.region && (x.region.instances || []).length) ?
                  h('span.small.muted',
                    {title: 'Duplicate instances folded into this one'},
                    `\u00d7${x.region.instances.length + 1}`) :
                  null))),
        x.rationale ? h('p.xwhy.small', x.rationale) : null, payloadBlock(x),
        usesChips(x), mergedChips(x),
        x.answer ? h('div.q-answered.small', icon('check'),
                     `Answered${x.answer.by ? ` by ${x.answer.by}` : ''}${
                         x.answer.note ? ` \u2014 ${x.answer.note}` : ''}`) :
                   null,
        editable ?
            h('div.xcard-actions',
              x.status === 'proposed' ?
                  h('button.btn.btn-ghost.btn-xs.x-confirm', {
                    type: 'button',
                    onclick: () =>
                        act(() => post(
                                `/api/imports/${iid}/confirm`, {ids: [x.id]}))
                  },
                    icon('check'), 'Confirm') :
                  null,
              x.status === 'confirmed' ?
                  h('button.btn.btn-quiet.btn-xs.x-unconfirm', {
                    type: 'button',
                    onclick: () =>
                        act(() => patch(
                                `/api/imports/${iid}/extractions/${x.id}`,
                                {status: 'proposed'}))
                  },
                    'Unconfirm') :
                  null,
              h('span.grow'),
              h(
                  'button.icon-btn.x-delete', {
                    type: 'button',
                    title: 'Delete extraction',
                    'aria-label': `Delete ${x.title}`,
                    onclick:
                        async () => {
                          if (!(await confirmDialog(
                                  'Delete extraction?',
                                  `\u201c${
                                      x.title ||
                                      'Untitled'}\u201d will be removed from this import.`,
                                  {confirmLabel: 'Delete', danger: true})))
                            return;
                          selected.delete(x.id);
                          act(() => del(
                                  `/api/imports/${iid}/extractions/${x.id}`));
                        }
                  },
                  icon('delete'))) :
            null);
  }

  // ------------------------------------------------------------- dialogs

  function openAnalyze() {
    const instr = h('textarea.input#reanalyze-instructions', {
      rows: 3,
      placeholder:
          'Optional, e.g. \u201cThe bottom nav is chrome \u2014 ignore it. Treat each list row as one component.\u201d',
    });
    modal({
      title: 'Re-analyze with my answers',
      id: 'reanalyze-dialog',
      body: [
        h('p.muted.small',
          'Confirmed and ignored extractions, hand-drawn regions and your answers are kept and sent to Gemini as context.'),
        h('label.field', h('span.field-label', 'Instructions'), instr),
      ],
      actions: [
        {label: 'Cancel'}, {
          label: 'Re-analyze',
          kind: 'primary',
          id: 'reanalyze-start',
          onClick: async () => {
            const body =
                instr.value.trim() ? {instructions: instr.value.trim()} : {};
            await post(`/api/imports/${iid}/analyze`, body);
            selected.clear();
            await load(true);
          }
        }
      ]
    });
  }

  function openPrepare() {
    const instr = h('textarea.input#prepare-instructions', {
      rows: 3,
      placeholder:
          'Optional, e.g. \u201cMap the chips to Basic ChoicePicker; keep token changes to color only.\u201d',
    });
    modal({
      title: 'Prepare merge plan',
      id: 'prepare-dialog',
      body: [
        h('p.muted.small',
          'Gemini drafts a plan from the confirmed extractions: token changes, components to draft or map, templates to save, and constitution guidance. You pick the lines to apply.'),
        h('label.field', h('span.field-label', 'Instructions'), instr),
      ],
      actions: [
        {label: 'Cancel'}, {
          label: 'Prepare',
          kind: 'primary',
          id: 'prepare-start',
          onClick: async () => {
            const body =
                instr.value.trim() ? {instructions: instr.value.trim()} : {};
            await post(`/api/imports/${iid}/merge/prepare`, body);
            await load(true);
            requestAnimationFrame(
                () => planEl.scrollIntoView(
                    {block: 'start', behavior: 'smooth'}));
          }
        }
      ]
    });
  }

  function openMergeDupes() {
    const ids = [...selected].filter((id) => xById(id));
    let keep = ids[0];
    modal({
      title: 'Merge into one',
      id: 'merge-dupes-dialog',
      body: [
        h('p.muted.small',
          'The others become instances of the one you keep (their regions are kept).'),
        h('div.radio-col', ids.map((id) => {
          const x = xById(id);
          return h(
              'label.radio.merge-keep', h('input', {
                type: 'radio',
                name: 'merge-keep',
                value: id,
                checked: id === keep,
                onchange: () => {
                  keep = id;
                }
              }),
              cropOf(x, 48, 36), kindChip(x.kind),
              h('span', x.title || 'Untitled'));
        })),
      ],
      actions: [
        {label: 'Cancel'}, {
          label: 'Merge',
          kind: 'primary',
          id: 'merge-dupes-ok',
          onClick: async () => {
            await post(
                `/api/imports/${iid}/extractions/merge`,
                {keep, ids: ids.filter((id) => id !== keep)});
            selected.clear();
            toast('Merged', 'good', 2000);
            await load(true);
          }
        }
      ]
    });
  }

  function openEdit() {
    const title = h('input.input#import-edit-title', {value: imp.title || ''});
    const notesIn =
        h('textarea.input#import-edit-notes', {rows: 4}, imp.notes || '');
    modal({
      title: 'Edit import',
      id: 'import-edit-dialog',
      body: [
        h('label.field', h('span.field-label', 'Title'), title),
        h('label.field', h('span.field-label', 'Notes for the analysis'),
          notesIn),
      ],
      actions: [
        {label: 'Cancel'}, {
          label: 'Save',
          kind: 'primary',
          onClick: async () => {
            await patch(
                `/api/references/${iid}`,
                {title: title.value.trim(), notes: notesIn.value.trim()});
            await load(true);
          }
        }
      ]
    });
  }

  async function remove() {
    if (!(await confirmDialog(
            'Delete import?',
            `\u201c${
                imp.title ||
                'Untitled'}\u201d, its images and extractions will be removed. Anything already merged (tokens, components, templates, revisions) stays.`,
            {confirmLabel: 'Delete', danger: true})))
      return;
    await del(`/api/references/${iid}`);
    toast('Import deleted', 'good');
    navigate(`/p/${pid}/imports`, {replace: true});
  }

  // ------------------------------------------------------------- merge plan

  function drawPlan() {
    if (imp.phase === 'planning') {
      mount(
          planEl, h('div.plan-head', h('h2', icon('merge'), 'Merge plan')),
          progressCard(imp) ||
              h('div.callout.callout-info',
                spinner('Drafting the merge plan\u2026')));
      return;
    }
    const plan = imp.merge_plan;
    if (!plan || imp.phase !== 'plan_ready') {
      mount(planEl);
      return;
    }
    const lines = plan.lines || [];
    if (planKey !== (plan.prepared_at || 'x')) {
      planKey = plan.prepared_at || 'x';
      planSel = new Map(lines.map((l) => [l.id, l.selected !== false]));
      planStrategies = new Map();
      constEdit = null;
      setDefaultData = plan.set_default_data_source !== false;
    }
    const nSel = lines.filter((l) => planSel.get(l.id)).length;
    const applyBtn =
        h('button.btn.btn-primary#plan-apply', {
          type: 'button',
          disabled: !nSel,
          onclick: () => apply(applyBtn),
        },
          icon('done_all'), `Apply selected (${nSel})`);
    const groups = PLAN_GROUPS.map((g) => {
      const gl = lines.filter((l) => l.type === g.type);
      if (!gl.length) return null;
      const allOn = gl.every((l) => planSel.get(l.id));
      const someOn = gl.some((l) => planSel.get(l.id));
      return h(
          'section.plan-group', {class: `pg-${g.type}`, id: `plan-${g.type}`},
          h('div.plan-group-head',
            h('label.check', h('input', {
                type: 'checkbox',
                checked: allOn,
                indeterminate: someOn && !allOn,
                'aria-label': `Select all ${g.label}`,
                onchange: (e) => {
                  gl.forEach((l) => planSel.set(l.id, e.target.checked));
                  drawPlan();
                }
              }),
              h('h3', icon(g.icon), g.label, h('span.count', ` ${gl.length}`))),
            g.type === 'data' ?
                h('label.check.small#plan-default-data-wrap',
                  h('input#plan-set-default-data', {
                    type: 'checkbox',
                    checked: setDefaultData,
                    disabled: !someOn,
                    onchange: (e) => {
                      setDefaultData = e.target.checked;
                    },
                  }),
                  h('span', 'Set as default data source for runs')) :
                null,
            h('span.muted.small', `\u2192 ${g.dest}`)),
          g.type === 'data' && plan.sample_set_name ?
              h('div.small.muted', {style: 'padding: 4px 12px 8px;'},
                `Creates sample set \u201c${plan.sample_set_name}\u201d${
                    plan.sample_set_description ?
                        ` \u2014 ${plan.sample_set_description}` :
                        ''}`) :
              null,
          h('div.plan-lines', gl.map((l) => planLine(l))));
    });
    mount(
        planEl,
        h('div.plan-head',
          h('div', h('h2', icon('merge'), 'Merge plan'),
            h('p.muted.small',
              `Prepared ${
                  relTime(
                      plan.prepared_at)}. Uncheck anything you don\u2019t want; applying posts a short summary to Chat.`)),
          h('div.row-inline', applyBtn)),
        plan.summary ? h('div.plan-summary',
                         renderMarkdown(plan.summary, {className: 'md'})) :
                       null,
        lines.length ?
            groups :
            emptyState(
                'Nothing to merge',
                'The plan has no lines. Confirm more extractions and re-plan.'),
        lines.length ? h('div.plan-foot', applyBtn.cloneNode(true)) : null);
    const foot = planEl.querySelector('.plan-foot .btn');
    if (foot) foot.addEventListener('click', () => apply(foot));
  }

  function fromChips(l) {
    const from = (l.from || []).map(xById).filter(Boolean);
    return from.length ? h('div.chip-row.plan-from',
                           h('span.xuses-label', 'from'), from.map(xLinkChip)) :
                         null;
  }

  function planLine(l) {
    const d = l.detail || {};
    const cb = h('input.plan-cb', {
      type: 'checkbox',
      checked: !!planSel.get(l.id),
      'aria-label': `Apply ${l.label || l.id}`,
      onchange: (e) => {
        planSel.set(l.id, e.target.checked);
        drawPlan();
      },
    });
    let body;
    if (l.type === 'tokens') {
      body =
          h('div.pl-token', h('code.pl-token-name', d.token || l.label),
            d.is_custom ? h('span.chip.chip-xs.chip-quiet', 'custom') : null,
            h('span.pl-val', swatch(d.before), h('span', valText(d.before))),
            h('span.pl-arrow', icon('arrow_forward')),
            h('span.pl-val', swatch(d.after), h('strong', valText(d.after))),
            d.confidence ?
                h('span.chip.chip-xs',
                  {class: d.confidence === 'exact' ? 'v-good' : 'v-mixed'},
                  d.confidence) :
                null,
            d.reason ? h('span.pl-reason.small.muted', d.reason) : null);
    } else if (l.type === 'component') {
      const isDraft = d.action === 'draft_custom';
      const dec = d.strategy_decision || {};
      const chosen = planStrategies.get(l.id) || '';
      const autoLabel = dec.strategy ?
          `Auto \u2192 ${STRATEGY_SHORT[dec.strategy] || dec.strategy}${
              d.basic_fit && d.basic_fit.score != null ?
                  ` (fit ${Math.round(d.basic_fit.score * 100)}%)` :
                  ''}` :
          'Auto (project rule)';
      const strategySel = isDraft ? h('select.input.input-xs.pl-strategy', {
        'aria-label': `Construction of ${d.name || l.label}`,
        title: dec.reason ||
            'How this component is built; Auto follows the project\u2019s Basic-fit threshold.',
        onchange: (e) => {
          const v = e.target.value;
          if (v) {
            planStrategies.set(l.id, v);
          } else {
            planStrategies.delete(l.id);
          }
          drawPlan();
        },
      },
                                      h('option',
                                        {value: '', selected: !chosen},
                                        autoLabel),
                                      h('option', {
                                        value: 'web_template',
                                        selected: chosen === 'web_template'
                                      },
                                        'Custom web component'),
                                      h('option', {
                                        value: 'basic_composition',
                                        selected: chosen === 'basic_composition'
                                      },
                                        'Basic catalog composition'),
                                      h('option', {
                                        value: 'native_targets',
                                        selected: chosen === 'native_targets'
                                      },
                                        'Multi-target native')) :
                                    null;
      body = h(
          'div.pl-rich',
          d.crop_url ?
              h('img.pl-crop', {src: d.crop_url, alt: '', loading: 'lazy'}) :
              null,
          h('div.pl-rich-main',
            h('div.pl-title',
              h('span.chip.chip-xs.pl-action', {class: `a-${d.action}`},
                ACTION_LABEL[d.action] || d.action),
              h('strong', d.name || l.label),
              d.basic ? h('span.muted.small', `Basic \u00b7 ${d.basic}`) : null,
              d.component_id ?
                  h('a.small',
                    {href: `/p/${pid}/design/components/${d.component_id}`},
                    'existing component') :
                  null,
              isDraft ? basicFitChip(d, {threshold: dec.threshold}) : null),
            d.purpose ? h('div.small', d.purpose) : null,
            isDraft ?
                h('div.pl-strategy-row', h('span.xuses-label', 'build as'),
                  strategySel,
                  d.basic_fit && (d.basic_fit.summary || []).length ?
                      h('span.small.muted.pl-fit-loss',
                        {title: d.basic_fit.summary.join('\n')},
                        `Basic would lose: ${
                            d.basic_fit.summary.slice(0, 2).join('; ')}${
                            d.basic_fit.summary.length > 2 ? '\u2026' : ''}`) :
                      null) :
                null,
            fromChips(l)));
    } else if (l.type === 'template') {
      body = h(
          'div.pl-rich',
          d.crop_url ?
              h('img.pl-crop', {src: d.crop_url, alt: '', loading: 'lazy'}) :
              null,
          h('div.pl-rich-main',
            h('div.pl-title', h('strong', d.name || l.label)),
            d.when_to_use ?
                h('div.small', h('span.xuses-label', 'when '), d.when_to_use) :
                null,
            (d.components || []).length ?
                h('div.chip-row', h('span.xuses-label', 'components'),
                  d.components.map(
                      (c) => h('span.chip.chip-xs.kind-chip.k-component', c))) :
                null,
            d.structure_md ?
                h('details.pl-structure', h('summary.small', 'Structure'),
                  renderMarkdown(d.structure_md, {className: 'md'})) :
                null,
            fromChips(l)));
    } else if (l.type === 'constitution') {
      body = constitutionLine(l, d);
    } else if (l.type === 'data') {
      body = h(
          'div.pl-rich',
          h('div.pl-rich-main',
            h('div.pl-title', h('strong', d.title || l.label),
              h('span.chip.chip-xs.chip-quiet',
                `${d.category || 'Imported'} / ${d.subcategory || 'Screens'}`),
              d.size_class ?
                  h('span.chip.chip-xs.chip-quiet', `size ${d.size_class}`) :
                  null,
              d.state && d.state !== 'content' ?
                  h('span.chip.chip-xs', {class: `st-${d.state}`}, d.state) :
                  null),
            d.intent ? h('div.small', d.intent) : null,
            d.payload && Object.keys(d.payload).length ?
                h('details.pl-structure', h('summary.small', 'Sample payload'),
                  h('pre.code-block.small',
                    JSON.stringify(d.payload, null, 2))) :
                null,
            fromChips(l)));
    } else {
      body = h('div', l.label || l.id);
    }
    return h(
        'div.plan-line', {
          class: [`pl-${l.type}`, planSel.get(l.id) ? 'on' : 'off'],
          dataset: {line: l.id}
        },
        cb,
        h('div.plan-line-body',
          l.type === 'tokens' ? [body, fromChips(l)] : body));
  }

  function constitutionLine(l, d) {
    const text = constEdit != null ? constEdit : (d.constitution_md || '');
    const diffHost = h('div.pl-diff');
    const drawDiff = () => {
      let diff = null;
      if (constEdit == null && d.diff && d.diff.rows)
        diff = d.diff;
      else if (constBase != null)
        diff = lineDiff(constBase, text);
      if (diff) {
        mount(diffHost, diffView(diff, {
                aLabel: 'Current',
                bLabel: constEdit != null ? 'Edited' : 'Proposed',
                context: 2,
                compact: true
              }));
      } else {
        mount(diffHost, spinner('Loading current constitution\u2026'));
        loadConstBase().then(() => drawDiff());
      }
    };
    drawDiff();
    const editing = constEdit != null;
    let t = null;
    const ta =
        h('textarea.input.code-input#plan-const-edit', {
          rows: 14,
          oninput: (e) => {
            constEdit = e.target.value;
            clearTimeout(t);
            t = setTimeout(() => {
              if (constBase != null) drawDiff();
            }, 400);
          },
        },
          text);
    return h(
        'div.pl-const',
        h('div.pl-title', h('strong', l.label || 'Constitution guidance'),
          h('span.grow'),
          editing ?
              h('button.btn.btn-quiet.btn-xs#plan-const-revert', {
                type: 'button',
                title: 'Discard my edits and use the proposed text',
                onclick: () => {
                  constEdit = null;
                  drawPlan();
                }
              },
                icon('undo'), 'Use proposed') :
              h('button.btn.btn-quiet.btn-xs#plan-const-toggle', {
                type: 'button',
                onclick: () => {
                  constEdit = text;
                  drawPlan();
                  setTimeout(
                      () => document.getElementById('plan-const-edit')?.focus(),
                      0);
                }
              },
                icon('edit'), 'Edit')),
        d.change_summary && d.change_summary !== l.label ?
            h('div.small', d.change_summary) :
            null,
        fromChips(l), editing ? [ta, diffHost] : diffHost);
  }

  async function loadConstBase() {
    if (constBase != null) return;
    try {
      const p = store.project && store.project.id === pid ?
          store.project :
          (await get(`/api/projects/${pid}`, {quiet: true})).project;
      const rid = p && p.current_revision && p.current_revision.id;
      if (!rid) {
        constBase = '';
        return;
      }
      const r =
          await get(`/api/projects/${pid}/revisions/${rid}`, {quiet: true});
      constBase = ((r && (r.revision || r)) || {}).constitution_md || '';
    } catch (_) {
      constBase = '';
    }
  }

  async function apply(btn) {
    const lines = {};
    for (const [id, on] of planSel) lines[id] = !!on;
    const body = {lines, set_default_data_source: Boolean(setDefaultData)};
    if (planStrategies.size) {
      body.strategies = Object.fromEntries(planStrategies);
    }
    if (constEdit != null) body.constitution_md = constEdit;
    btn.disabled = true;
    btn.classList.add('busy');
    try {
      const res = await post(`/api/imports/${iid}/merge/apply`, body);
      const applied = (res && res.result && res.result.applied) || [];
      if (res && res.result && res.result.default_data_source_set) {
        try {
          const {settings: freshSettings} =
              await get(`/api/projects/${pid}/settings`, {quiet: true});
          if (store.project && freshSettings) {
            store.project.settings = freshSettings;
            emit(
                'settings-changed',
                {settings: freshSettings, source: 'import'});
          }
        } catch (_) {
        }
      }
      toast(
          `Applied${
              applied.length ? ` ${applied.length} change${
                                   applied.length === 1 ? '' : 's'}` :
                               ''} \u2014 summary posted to Chat.`,
          'good');
      if (ctx && ctx.chat) ctx.chat.loaded = false;
      emit('revision-changed', {});
      if (res && res.import) {
        imp = res.import.extractions ? res.import : {...imp, ...res.import};
        lastJson = '';
      }
      await load(true);
      requestAnimationFrame(
          () => recordEl.scrollIntoView({block: 'start', behavior: 'smooth'}));
    } catch (_) {
      btn.disabled = false;
      btn.classList.remove('busy');
    }
  }

  // ------------------------------------------------------------- record

  function drawRecord() {
    const log = imp.merge_log || [];
    if (!log.length) {
      mount(recordEl);
      return;
    }
    const typeOrder =
        {component: 0, template: 1, data: 2, constitution: 3, tokens: 4};
    mount(
        recordEl, h('h2', icon('history'), 'Record'),
        h('ol.record-list', [...log].reverse().map((e) => {
          const app = [...(e.applied || [])].sort(
              (a, b) => (typeOrder[a.type] ?? 9) - (typeOrder[b.type] ?? 9));
          const nComp = app.filter((a) => a.type === 'component').length;
          const nTpl = app.filter((a) => a.type === 'template').length;
          const nData = app.filter((a) => a.type === 'data').length;
          const nTok = app.filter((a) => a.type === 'tokens').length;
          return h(
              'li.record-entry',
              h('div.record-head',
                h('strong',
                  `Applied ${app.length} change${app.length === 1 ? '' : 's'}`),
                h('span.muted.small', `${fmtTime(e.at)} \u00b7 ${e.by || ''}`),
                e.default_data_source_set ?
                    h('span.chip.chip-xs.v-good', icon('dataset'),
                      'Default run data') :
                    null,
                h('span.grow'),
                nComp ? h('a.btn.btn-quiet.btn-xs',
                          {href: `/p/${pid}/design?tab=components`},
                          icon('widgets'), `Components (${nComp})`) :
                        null,
                nTpl ? h('a.btn.btn-quiet.btn-xs',
                         {href: `/p/${pid}/design?tab=templates`},
                         icon('dashboard'), `Templates (${nTpl})`) :
                       null,
                nData ? h('a.btn.btn-quiet.btn-xs', {
                  href: e.sample_set_id ?
                      `/data?set=${encodeURIComponent(e.sample_set_id)}` :
                      '/data'
                },
                          icon('dataset'), `Project data (${nData})`) :
                        null,
                nTok ? h('a.btn.btn-quiet.btn-xs',
                         {href: `/p/${pid}/design?tab=tokens`}, icon('palette'),
                         `Tokens (${nTok})`) :
                       null,
                e.revision_id ?
                    h('a.btn.btn-quiet.btn-xs',
                      {href: `/p/${pid}/constitution`, title: e.revision_id},
                      icon('article'), 'Constitution revision') :
                    null,
                e.chat_message_id ?
                    h('button.btn.btn-quiet.btn-xs.record-chat', {
                      type: 'button',
                      onclick: () =>
                          emit('chat-focus', {message_id: e.chat_message_id})
                    },
                      icon('forum'), 'Chat summary') :
                    null),
              h('ul.record-lines', app.map((a) => {
                const g = PLAN_GROUPS.find((x) => x.type === a.type);
                const t = a.target || {};
                return h(
                    'li', icon(g ? g.icon : 'check'),
                    h('span.record-label', a.label || a.line_id),
                    t.url || t.label ?
                        [
                          h('span.muted', '\u2192'),
                          t.url ? h('a', {href: t.url}, t.label || t.type) :
                                  h('span', t.label || t.type)
                        ] :
                        null);
              })));
        })));
  }

  function drawFacts() {
    const f = imp.facts;
    if (!f || !Object.keys(f).length) {
      mount(factsEl);
      return;
    }
    mount(
        factsEl,
        h('details.import-facts-box',
          h('summary', icon('straighten'), 'Source facts',
            h('span.muted.small', ' exact values read from the file')),
          f.design_system ? designSystemPanel(f.design_system) : null,
          factsSummary(f)));
  }
}

/**
 * @param {*} v
 * @return {string}
 */
function valText(v) {
  if (v == null || v === '') return '\u2014';
  return typeof v === 'object' ? JSON.stringify(v) : String(v);
}

/**
 * @param {*} v
 * @return {*}
 */
function swatch(v) {
  return typeof v === 'string' && COLOR_RE.test(v.trim()) ?
      h('span.swatch-dot', {style: {backgroundColor: v.trim()}}) :
      null;
}
