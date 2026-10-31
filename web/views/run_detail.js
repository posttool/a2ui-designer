// Run detail: live progress + output gallery with multi-renderer switcher.
import {cached, get, post, qs} from '../components/api.js';
import {chip, genreChip, loadGenres, scoreBadge, statusChip, verdictMark} from '../components/badges.js';
import {emptyState, fmtTime, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {openNewRunDialog} from '../components/newrun.js';
import {navigate, store} from '../components/store.js';
import {segmented, toast} from '../components/ui.js';

import {deleteRun, runProgress, verdictCounts} from './runs.js';

const ACTIVE = new Set(['queued', 'running']);
const STEP_LABEL = {
  queued: 'Queued',
  generating: 'Generating',
  rendering: 'Rendering',
  judging: 'Judging',
  error: 'Error'
};

export const RENDERER_META = {
  web: {
    id: 'web',
    label: 'Web (HTML/CSS)',
    short: 'Web',
    badge: 'WEB',
    engine: 'Headless Chromium (CDP) + A2UI Web Renderer',
  },
  android_compose: {
    id: 'android_compose',
    label: 'Android Jetpack Compose',
    short: 'Compose',
    badge: 'COMPOSE',
    engine: 'AndroidX Compose M3 A2UI + Robolectric Skia (API 35)',
  },
  android_remote_compose: {
    id: 'android_remote_compose',
    label: 'Android RemoteCompose (.rc)',
    short: 'RemoteCompose (.rc)',
    badge: 'RC WIRE',
    engine: 'RemoteComposeCreation (.rc wire) + RemoteComposePlayer + Skia',
  },
  ios_swiftui: {
    id: 'ios_swiftui',
    label: 'iOS SwiftUI',
    short: 'iOS SwiftUI',
    badge: 'SWIFTUI',
    engine: 'Apple SwiftUI A2UI + iOS Simulator (IPHONE_13_PRO)',
  },
};

export const ALL_RENDERER_IDS = [
  'web',
  'android_compose',
  'android_remote_compose',
  'ios_swiftui',
];

/**
 * Renderers enabled in project settings (Web when unset), in canonical order.
 */
export function enabledRendererIds(settings) {
  const sel =
      settings && Array.isArray(settings.renderers) ? settings.renderers : null;
  const ids = sel && sel.length ?
      ALL_RENDERER_IDS.filter((id) => sel.includes(id)) :
      ['web'];
  return ids.length ? ids : ['web'];
}

/** Human list of renderer labels: "Web, Android Compose and iOS SwiftUI". */
export function rendererList(ids, key = 'short') {
  const names = ids.map((id) => (RENDERER_META[id] || {})[key] || id);
  return names.length <= 1 ?
      (names[0] || '') :
      `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

export function outputShotForRenderer(o, rendererId) {
  const renders = o.renders || {};
  if (rendererId && renders[rendererId]) {
    const r = renders[rendererId];
    if (r.thumb_url || r.screenshot_url) {
      return {
        thumb: r.thumb_url || r.screenshot_url,
        full: r.screenshot_url || r.thumb_url,
        status: r.status || 'done',
        meta: r.meta || {},
        error: r.error || null,
      };
    }
    return {
      thumb: null,
      full: null,
      status: r.status || 'missing',
      meta: r.meta || {},
      error: r.error || null,
    };
  }
  if (!rendererId || rendererId === 'web') {
    if (o.thumb_url || o.screenshot_url) {
      return {
        thumb: o.thumb_url || o.screenshot_url,
        full: o.screenshot_url || o.thumb_url,
        status: 'done',
        meta: o.render_meta || {},
        error: null,
      };
    }
  }
  return {thumb: null, full: null, status: 'missing', meta: {}, error: null};
}

const STAGE_ORDER = {
  form: 0,
  result: 1
};

/** Whether any output is a connected-app input form (run has stages). */
export function hasStages(outputs) {
  return (outputs || []).some((o) => o.stage === 'form');
}

/**
 * Keeps form/result pairs adjacent: groups outputs by sample + viewport in the
 * order the server sorted them (first appearance wins), form before result.
 * Runs without form outputs are returned unchanged.
 */
export function groupStagePairs(outputs) {
  if (!hasStages(outputs)) return outputs;
  const groups = new Map();
  outputs.forEach((o, i) => {
    const key = `${o.sample_id}|${o.viewport}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push({o, i});
  });
  const out = [];
  for (const g of groups.values()) {
    g.sort(
        (a, b) =>
            ((STAGE_ORDER[a.o.stage] ?? 2) - (STAGE_ORDER[b.o.stage] ?? 2)) ||
            a.i - b.i);
    g.forEach((x) => out.push(x.o));
  }
  return out;
}

/** "Form" / "Result" stage chip (null for outputs without a stage). */
export function stageChip(o, cls = '') {
  if (o.stage !== 'form' && o.stage !== 'result') return null;
  return h(
      'span.stage-chip', {
        class: [`stage-${o.stage}`, cls].join(' '),
        title: o.stage === 'form' ? 'Input form for the connected-app tool' :
                                    'Tool result'
      },
      icon(o.stage === 'form' ? 'edit_note' : 'output'),
      o.stage === 'form' ? 'Form' : 'Result');
}

export function outputTile(
    o, href, activeRenderer = 'web',
    {showStage = false, renderers = ALL_RENDERER_IDS} = {}) {
  const shot = outputShotForRenderer(o, activeRenderer);
  const ready = o.status === 'done' && shot.thumb;
  const errCount =
      o.validation && !o.validation.ok ? (o.validation.errors || []).length : 0;
  const renders = o.renders || {};
  const renderedBadges = renderers.length <= 1 ?
      [] :
      renderers.filter(
          (rid) => renders[rid] &&
              (renders[rid].screenshot_url || renders[rid].thumb_url));

  return h(
      'a.tile', {
        href,
        dataset: {outputId: o.id, renderer: activeRenderer},
        class: [
          `st-${o.status}`, o.human_verdict ? `hv-${o.human_verdict}` : ''
        ].join(' ')
      },
      h('div.tile-shot',
        ready ? h('img', {
          src: shot.thumb,
          alt: `${o.sample_title} on ${o.viewport} (${activeRenderer})`,
          loading: 'lazy'
        }) :
                h('div.tile-placeholder',
                  o.status === 'error' || shot.status === 'error' ?
                      icon('error') :
                      (shot.status === 'missing' && o.status === 'done' ?
                           icon('layers_clear') :
                           h('span.spinner')),
                  h('span',
                    shot.status === 'missing' && o.status === 'done' ?
                        `Not rendered on ${
                            (RENDERER_META[activeRenderer] || {}).short ||
                            activeRenderer}` :
                        (STEP_LABEL[shot.status] || STEP_LABEL[o.status] ||
                         o.status))),
        h('div.tile-badges',
          o.judge_score != null ? scoreBadge(o.judge_score) : null,
          verdictMark(o.human_verdict)),
        renderedBadges.length ?
            h('div.tile-renderers',
              renderedBadges.map(
                  (rid) =>
                      h('span.renderer-badge.renderer-badge-xs', {
                        dataset: {renderer: rid},
                        class: rid === activeRenderer ? 'active' : ''
                      },
                        (RENDERER_META[rid] || {}).badge || rid))) :
            null,
        errCount ? h('span.tile-err', {
          title: (o.validation.errors || [])
                     .map((e) => `${e.path}: ${e.message}`)
                     .join('\n')
        },
                     icon('warning'), `${errCount}`) :
                   null),
      h('div.tile-meta', h('div.tile-title', o.sample_title),
        h('div.tile-sub', showStage ? stageChip(o, 'tile-stage') : null,
          genreChip(o.genre, o.group), h('span.muted', o.viewport))));
}

export async function render(root, params, ctx) {
  const {pid, rid} = params;
  const sp = ctx.url.searchParams;
  const f = {
    sort: sp.get('sort') || 'worst',
    genre: sp.get('genre') || '',
    group: sp.get('group') || '',
    viewport: sp.get('viewport') || '',
    verdict: sp.get('verdict') || '',
    valid: sp.get('valid') || '',
    renderer: sp.get('renderer') || 'web',
  };
  const compareWith = sp.get('compare_with');
  let run = null;
  let outputs = [];
  let alive = true;
  let timer = null;
  let otherRuns = [];
  await loadGenres();
  const genres = await cached('/api/genres').catch(() => ({groups: []}));
  // Only renderers enabled in project settings are shown on this page.
  const projSettings = await get(`/api/projects/${pid}/settings`, {quiet: true})
                           .then((r) => r.settings || {})
                           .catch(() => ({}));
  const enabled = enabledRendererIds(projSettings);
  if (!enabled.includes(f.renderer)) f.renderer = enabled[0];

  const headEl = h('div.run-head');
  const statsEl = h('div.run-stats#run-stats');
  const galleryEl = h('div.gallery#run-gallery');
  const filterEl = h('div.filterbar.run-filters');
  root.appendChild(
      h('div.page.page-run', headEl, statsEl, filterEl, galleryEl));

  function sel(id, key, options) {
    return h(
        'select.input.input-sm', {
          id,
          onchange: (e) => {
            f[key] = e.target.value;
            syncUrl();
            loadOutputs();
          }
        },
        options.map(
            ([v, l]) => h('option', {value: v, selected: f[key] === v}, l)));
  }
  function syncUrl() {
    const u = new URL(location.href);
    Object.entries(f).forEach(
        ([k, v]) =>
            (v && !(k === 'sort' && v === 'worst') &&
                     !(k === 'renderer' && v === 'web') ?
                 u.searchParams.set(k, v) :
                 u.searchParams.delete(k)));
    history.replaceState(null, '', u.pathname + u.search);
  }
  function filterQuery() {
    const q = {...f};
    delete q.renderer;
    return q;
  }
  function reviewHref(o) {
    const q = {
      ...filterQuery(),
      o: o ? o.id : undefined,
      renderer: f.renderer !== 'web' ? f.renderer : undefined,
    };
    if (q.sort === 'worst') delete q.sort;
    return `/p/${pid}/review/${rid}${qs(q)}`;
  }

  function drawFilters() {
    const vps = run ? run.viewports || [] : [];
    const renSeg = enabled.length <= 1 ?
        null :
        segmented(
            enabled.map((id) => ({
                          value: id,
                          label: RENDERER_META[id].short,
                          id: `run-ren-${id}`,
                        })),
            f.renderer, (v) => {
              f.renderer = v;
              syncUrl();
              drawGallery();
            });
    mount(
        filterEl, renSeg,
        sel('run-filter-group', 'group',
            [['', 'All groups'], ...genres.groups.map((g) => [g.id, g.label])]),
        sel('run-filter-genre', 'genre',
            [
              ['', 'All genres'],
              ...genres.groups.flatMap(
                  (g) => g.genres.map((x) => [x.id, x.label]))
            ]),
        sel('run-filter-viewport', 'viewport',
            [['', 'All viewports'], ...vps.map((v) => [v, v])]),
        sel('run-filter-verdict', 'verdict',
            [
              ['', 'Any verdict'], ['unreviewed', 'Unreviewed'],
              ['good', 'Good'], ['mixed', 'Mixed'], ['bad', 'Bad']
            ]),
        sel('run-filter-valid', 'valid',
            [
              ['', 'Valid or not'], ['1', 'Valid only'],
              ['0', 'Validation errors']
            ]),
        h('div.grow'), h('label.inline-label', 'Sort', sel('run-sort', 'sort', [
                           ['worst', 'Worst first'], ['best', 'Best first'],
                           ['genre', 'By genre'], ['recent', 'Most recent']
                         ])));
  }

  function drawHead() {
    const active = ACTIVE.has(run.status);
    const canResume =
        ['interrupted', 'cancelled', 'failed'].includes(run.status) ||
        (run.status === 'done' && (run.counts || {}).error);
    const others = otherRuns.filter((r) => r.id !== rid);
    const runRenderers =
        (run.renderers || ['web']).filter((r) => enabled.includes(r));
    const tokNum =
        run.rendered_token_revision_number || run.token_revision_number || 1;
    const curTokNum = run.current_token_revision_number || tokNum;
    mount(
        headEl,
        h('div.crumbs', h('a', {href: `/p/${pid}/runs`}, 'Runs'),
          h('span', '/'), h('span', `Run on rev ${run.revision_number}`)),
        h('div.page-head',
          h('div',
            h('div.title-row',
              h('h1', run.sample_set_name || 'Custom selection'),
              statusChip(run.status),
              h('span.chip.chip-xs.token-rev-chip', {
                title: run.theme_drifted ?
                    `Rendered with Tokens T${
                        tokNum}; live project tokens are T${curTokNum}` :
                    `Rendered with Tokens T${tokNum}`,
              },
                icon('palette'), `Tokens T${tokNum}`),
              runRenderers.map(
                  (r) =>
                      h('span.renderer-badge', {dataset: {renderer: r}},
                        (RENDERER_META[r] || {}).badge || r))),
            h('p.lede.small',
              `rev ${run.revision_number} \u00b7 Tokens T${tokNum} \u00b7 ${
                  (run.viewports || []).join(', ')} \u00b7 started ${
                  fmtTime(run.started_at || run.created_at)}${
                  run.finished_at ?
                      ` \u00b7 finished ${relTime(run.finished_at)}` :
                      ''}${
                  run.created_by ? ` \u00b7 ${run.created_by}` : ''}`)),
          h('div.page-actions',
            active ? h('button.btn.btn-ghost#run-cancel', {
              type: 'button',
              onclick: async (e) => {
                e.currentTarget.disabled = true;
                const r = await post(`/api/runs/${rid}/cancel`);
                run = r.run;
                toast('Run cancelled', 'info');
                refresh();
              }
            },
                       icon('stop_circle'), 'Cancel') :
                     null,
            canResume ? h('button.btn.btn-ghost#run-resume', {
              type: 'button',
              onclick: async (e) => {
                e.currentTarget.disabled = true;
                const r = await post(`/api/runs/${rid}/resume`);
                run = r.run;
                toast('Run resumed', 'good');
                refresh();
              }
            },
                          icon('play_circle'), 'Resume') :
                        null,
            h('button.btn.btn-ghost#run-render-all', {
              type: 'button',
              title: `Render all outputs on ${rendererList(enabled, 'label')}`,
              onclick: async (e) => {
                e.currentTarget.disabled = true;
                try {
                  await post(`/api/runs/${rid}/render`, {renderers: enabled});
                  toast(`Rendering on ${rendererList(enabled)}\u2026`, 'good');
                  setTimeout(refresh, 1000);
                } finally {
                  e.currentTarget.disabled = false;
                }
              }
            },
              icon('devices'),
              enabled.length > 1 ? `Render on all ${enabled.length}` :
                                   `Render on ${rendererList(enabled)}`),
            others.length ?
                h('select.input.input-sm#compare-with', {
                  onchange: (e) => {
                    if (e.target.value)
                      navigate(
                          `/p/${pid}/compare?a=${e.target.value}&b=${rid}`);
                  }
                },
                  h('option', {value: ''}, 'Compare with\u2026'),
                  others.map(
                      (r) => h(
                          'option', {value: r.id},
                          `rev ${r.revision_number} \u00b7 ${
                              r.sample_set_name ||
                              'selection'} \u00b7 ${relTime(r.created_at)}`))) :
                null,
            h('button.btn.btn-ghost#run-again', {
              type: 'button',
              title:
                  'Start a new run with the same samples, viewports, and renderers',
              onclick: () => openNewRunDialog({
                projectId: pid,
                sampleSetId: run.sample_set_id,
                sampleIds: run.sample_set_id ?
                    null :
                    (run.config && run.config.sample_ids),
                viewports: run.viewports,
                renderers: run.renderers,
              })
            },
              icon('replay'), 'Run again'),
            h('button.btn.btn-ghost.btn-danger-quiet#run-delete', {
              type: 'button',
              title: run.effect_id ? 'Delete this run and its effect study' :
                                     'Delete this run',
              onclick: async () => {
                if (await deleteRun(run)) navigate(`/p/${pid}/runs`);
              }
            },
              icon('delete'), 'Delete'),
            h('button.btn.btn-primary#review-worst',
              {type: 'button', onclick: () => navigate(reviewHref(null))},
              icon('rate_review'), 'Review worst-first'))),
        run.theme_drifted ?
            h('div.banner.banner-info.token-drift-banner#run-token-drift-banner',
              icon('palette'),
              h('span.banner-text',
                `Design tokens updated since this run was rendered (Tokens T${
                    tokNum} \u2192 T${
                    curTokNum}). Re-render outputs with current tokens (0 LLM calls)?`),
              h('div.banner-actions',
                h('button.btn.btn-sm.btn-primary#run-retheme-btn', {
                  type: 'button',
                  onclick: async (e) => {
                    e.currentTarget.disabled = true;
                    try {
                      const r = await post(
                          `/api/runs/${rid}/retheme`, {renderers: enabled});
                      if (r && r.run) run = r.run;
                      toast(
                          `Applied Tokens T${
                              curTokNum} \u2014 re-rendering on ${
                              rendererList(enabled)}\u2026`,
                          'good');
                      await refresh();
                    } finally {
                      e.currentTarget.disabled = false;
                    }
                  },
                },
                  icon('format_paint'), `Apply T${curTokNum} & re-render`))) :
            null,
        compareWith ?
            h('div.banner.banner-info', icon('compare'),
              h('span.banner-text',
                'When this run finishes, compare it with the previous one to see what the new revision changed.'),
              h('div.banner-actions',
                h('a.btn.btn-sm.btn-ghost#compare-previous',
                  {href: `/p/${pid}/compare?a=${compareWith}&b=${rid}`},
                  'Compare now'))) :
            null);
  }

  function drawStats() {
    const c = run.counts || {};
    const step = (k, label) =>
        h('div.stat', {class: `stat-${k}`}, h('span.stat-num', `${c[k] || 0}`),
          h('span.stat-label', label));
    mount(
        statsEl,
        h('div.run-progress', runProgress(run),
          h('span.muted',
            `${(c.done || 0) + (c.error || 0)} / ${c.total || 0}`)),
        h('div.stat-row', step('queued', 'queued'),
          step('generating', 'generating'), step('rendering', 'rendering'),
          step('judging', 'judging'), step('done', 'done'),
          step('error', 'errors'), h('div.stat-sep'),
          h('div.stat',
            h('span.stat-num',
              scoreBadge(
                  run.avg_score != null ? Math.round(run.avg_score * 10) / 10 :
                                          null)),
            h('span.stat-label', 'avg score')),
          h('div.stat', verdictCounts(run.verdicts),
            h('span.stat-label',
              `${(run.verdicts || {}).unreviewed || 0} unreviewed`))));
  }

  function drawGallery() {
    if (!outputs.length) {
      mount(
          galleryEl,
          ACTIVE.has(run.status) ?
              h('div.gallery-wait',
                spinner('Waiting for the first outputs\u2026')) :
              emptyState('No outputs match these filters'));
      return;
    }
    const staged = hasStages(outputs);
    mount(
        galleryEl,
        groupStagePairs(outputs).map(
            (o) => outputTile(
                o, reviewHref(o), f.renderer,
                {showStage: staged, renderers: enabled})));
  }

  let outSeq = 0;
  async function loadOutputs() {
    const seq = ++outSeq;
    const q = {...filterQuery()};
    const res = await get(`/api/runs/${rid}/outputs${qs(q)}`, {
                  quiet: true
                }).catch(() => null);
    if (!res || seq !== outSeq || !alive) return;
    outputs = res.outputs || [];
    drawGallery();
  }

  async function refresh() {
    clearTimeout(timer);
    const res = await get(`/api/runs/${rid}`, {quiet: !!run}).catch(() => null);
    if (!alive) return;
    if (!res) {
      if (!run) mount(headEl, h('h1', 'Run not found'));
      return;
    }
    run = res.run;
    store.lastRunId[pid] = rid;
    drawHead();
    drawStats();
    await loadOutputs();
    const anyRendering = outputs.some(
        (o) =>
            Object.values(o.renders || {})
                .some(
                    (r) => r &&
                        (r.status === 'queued' || r.status === 'rendering')));
    if (ACTIVE.has(run.status) || anyRendering)
      timer = setTimeout(refresh, 2000);
  }

  mount(galleryEl, spinner('Loading\u2026'));
  get(`/api/projects/${pid}/runs`, {quiet: true})
      .then((r) => {
        otherRuns = r.runs || [];
        if (run && alive) drawHead();
      })
      .catch(() => {});
  await refresh();
  drawFilters();
  return () => {
    alive = false;
    clearTimeout(timer);
  };
}
