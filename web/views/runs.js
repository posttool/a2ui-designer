// Runs list for a project.
import {del, get} from '../components/api.js';
import {chip, scoreBadge, statusChip} from '../components/badges.js';
import {emptyState, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {checkRunReadiness, openNewRunDialog, renderReadinessBanner} from '../components/newrun.js';
import {emit, navigate, store} from '../components/store.js';
import {confirmDialog, progressBar, toast} from '../components/ui.js';

const ACTIVE = new Set(['queued', 'running']);

/**
 * Confirms and deletes a run (and, for effect-study runs, the whole study).
 * Resolves to true if deleted.
 */
export async function deleteRun(run) {
  const total = (run.counts || {}).total || 0;
  const reviewed = Object.entries(run.verdicts || {})
                       .filter(([k]) => k !== 'unreviewed')
                       .reduce((n, [, v]) => n + (v || 0), 0);
  const parts = [
    `${total} output${total === 1 ? '' : 's'}${
        reviewed ? ` (${reviewed} reviewed)` :
                   ''} and their screenshots will be permanently deleted.`,
  ];
  if (run.effect_id)
    parts.push(
        'This run is part of a Constitution effect study \u2014 the study and all of its runs will be deleted too.');
  if (ACTIVE.has(run.status)) parts.push('The run will be stopped first.');
  parts.push('Dos & Don\u2019ts keep their own screenshots.');
  if (!(await confirmDialog(
          run.effect_id ? 'Delete effect study?' : 'Delete run?',
          parts.join(' '), {confirmLabel: 'Delete', danger: true})))
    return false;
  const res = await del(`/api/runs/${run.id}`);
  const n = (res.deleted_run_ids || []).length;
  for (const [pid, rid] of Object.entries(store.lastRunId || {}))
    if ((res.deleted_run_ids || []).includes(rid)) delete store.lastRunId[pid];
  if (store.project && store.project.id === run.project_id &&
      store.project.stats) {
    store.project.stats.runs = Math.max(0, (store.project.stats.runs || 0) - n);
    emit('project-updated');
  }
  toast(n > 1 ? `Deleted effect study (${n} runs)` : 'Run deleted', 'info');
  return true;
}

export function runProgress(run) {
  const c = run.counts || {};
  const total = c.total || 0;
  if (!total) return progressBar(0);
  const seg = (n, cls, title) =>
      ({value: (n || 0) / total, cls, title: `${n || 0} ${title}`});
  return progressBar(null, {
    segments: [
      seg(c.done, 'p-done', 'done'),
      seg(c.error, 'p-error', 'error'),
      seg(c.judging, 'p-judging', 'judging'),
      seg(c.rendering, 'p-rendering', 'rendering'),
      seg(c.generating, 'p-generating', 'generating'),
    ]
  });
}

export function verdictCounts(v) {
  v = v || {};
  return h(
      'span.vcounts',
      h('span.vc.v-good', {title: 'Good'}, icon('thumb_up'), v.good || 0),
      h('span.vc.v-mixed', {title: 'Mixed'}, icon('thumbs_up_down'),
        v.mixed || 0),
      h('span.vc.v-bad', {title: 'Bad'}, icon('thumb_down'), v.bad || 0));
}

export async function render(root, params, ctx) {
  const pid = params.pid;
  const picked = [];
  let runs = [];
  let timer = null;
  let alive = true;
  const listEl = h('div.table-wrap#runs-table');
  const effectsEl = h('div#effects-list');
  const readinessEl = h('div#runs-readiness-banner');
  let effects = [];
  let readiness = null;
  const compareBtn =
      h('button.btn.btn-ghost#compare-runs', {
        type: 'button',
        disabled: true,
        onclick: () => {
          if (picked.length === 2)
            navigate(`/p/${pid}/compare?a=${picked[0]}&b=${picked[1]}`);
        }
      },
        icon('compare'), 'Compare');

  root.appendChild(h(
      'div.page.page-runs',
      h('div.page-head',
        h('div', h('h1', 'Runs'),
          h('p.lede',
            'One revision \u00d7 a sample set \u00d7 viewports. Each output is generated, rendered, screenshotted and judged.')),
        h('div.page-actions',
          h('span.muted.compare-hint', 'Pick two to compare'), compareBtn,
          h('button.btn.btn-ghost#new-effect', {
            type: 'button',
            title:
                'Generate with and without the constitution and see where it changes the layout',
            onclick: () => openNewRunDialog({projectId: pid, mode: 'effect'})
          },
            icon('difference'), 'Constitution effect'),
          h('button.btn.btn-primary#new-run',
            {type: 'button', onclick: () => openNewRunDialog({projectId: pid})},
            icon('play_arrow'), 'New run'))),
      readinessEl, effectsEl, listEl));

  async function load() {
    try {
      const [res, eff, rdy] = await Promise.all([
        get(`/api/projects/${pid}/runs`, {quiet: !!runs.length}),
        get(`/api/projects/${pid}/effects`, {
          quiet: true
        }).catch(() => ({effects: []})),
        checkRunReadiness(pid).catch(() => null),
      ]);
      if (!alive) return;
      runs = res.runs || [];
      effects = eff.effects || [];
      readiness = rdy;
    } catch (e) {
      return;
    }
    if (runs[0]) store.lastRunId[pid] = runs[0].id;
    else if (store.lastRunId)
      delete store.lastRunId[pid];
    if (store.project && store.project.id === pid && store.project.stats &&
        store.project.stats.runs !== runs.length) {
      store.project.stats.runs = runs.length;
      emit('project-updated');
    }
    draw();
    clearTimeout(timer);
    if (runs.some((r) => ACTIVE.has(r.status)) ||
        effects.some(
            (e) => e.status === 'running' || e.status === 'analyzing') ||
        (readiness && readiness.hasBuilding))
      timer = setTimeout(load, 2000);
  }

  function drawEffects() {
    if (!effects.length) {
      mount(effectsEl);
      return;
    }
    mount(
        effectsEl,
        h('div.effects-strip',
          h('div.effects-strip-head', h('h2', 'Constitution effect studies'),
            h('span.muted.small',
              'Revision vs. no constitution on the same samples')),
          h('div.effects-cards', effects.slice(0, 6).map((e) => {
            const t = (e.runs || {}).treatment || {};
            return h(
                'a.effect-card',
                {href: `/p/${pid}/effects/${e.id}`, dataset: {effectId: e.id}},
                h('div.effect-card-title',
                  h('span.rev-badge', `rev ${e.revision_number}`),
                  h('span.muted', 'vs'), h('span', 'no constitution')),
                h('div.effect-card-meta', statusChip(e.status),
                  h('span.muted.small',
                    `${(t.counts || {}).total || 0} surfaces \u00b7 ${
                        relTime(e.created_at)}`)),
                e.headline && (e.headline.top_rules || []).length ?
                    h('div.muted.small',
                      `Most layout-changing: ${
                          e.headline.top_rules.join(', ')}`) :
                    null);
          }))));
  }

  function draw() {
    mount(readinessEl, renderReadinessBanner(pid, readiness, {compact: true}));
    drawEffects();
    compareBtn.disabled = picked.length !== 2;
    if (!runs.length) {
      mount(
          listEl,
          emptyState(
              'No runs yet',
              'Start with a baseline: run the constitution as it is across all genres to see what the generator does on its own.',
              h('button.btn.btn-primary#new-run-empty', {
                type: 'button',
                onclick: () =>
                    openNewRunDialog({projectId: pid, preset: 'baseline'})
              },
                icon('play_arrow'), 'Run a baseline')));
      return;
    }
    mount(
        listEl,
        h('table.table.runs',
          h('thead',
            h('tr', h('th.cell-check', ''), h('th', 'Revision'),
              h('th', 'Samples'), h('th', 'Viewports'), h('th', 'Status'),
              h('th.cell-progress', 'Progress'), h('th.num', 'Avg score'),
              h('th', 'Review'), h('th', 'Started'), h('th.cell-actions', ''))),
          h('tbody', runs.map((r) => {
            const c = r.counts || {};
            const isPicked = picked.includes(r.id);
            return h(
                'tr.row-click', {
                  dataset: {runId: r.id},
                  class: isPicked ? 'sel' : '',
                  onclick: (e) => {
                    if (e.target.closest('input,button')) return;
                    navigate(`/p/${pid}/runs/${r.id}`);
                  }
                },
                h('td.cell-check', h('input', {
                    type: 'checkbox',
                    checked: isPicked,
                    'aria-label': 'Pick for compare',
                    id: `pick-run-${r.id}`,
                    onchange: (e) => {
                      if (e.target.checked) {
                        picked.push(r.id);
                        if (picked.length > 2) picked.shift();
                      } else
                        picked.splice(picked.indexOf(r.id), 1);
                      draw();
                    }
                  })),
                h('td',
                  r.no_constitution ?
                      h('span.rev-badge.rev-none', 'no constitution') :
                      h('span.rev-badge', `rev ${r.revision_number}`),
                  r.effect_id ?
                      h('a.chip.chip-quiet.effect-link', {
                        href: `/p/${pid}/effects/${r.effect_id}`,
                        title: 'Part of a Constitution effect study',
                        onclick: (e) => e.stopPropagation()
                      },
                        r.kind === 'effect_noise' ? 'effect \u00b7 noise' :
                                                    'effect') :
                      null),
                h('td',
                  h('div.cell-title',
                    r.sample_set_name ||
                        (r.config && r.config.sample_ids ?
                             `${r.config.sample_ids.length} selected samples` :
                             'Custom selection'))),
                h('td', (r.viewports || []).map((v) => chip(v, 'chip-quiet'))),
                h('td', statusChip(r.status)),
                h('td.cell-progress', runProgress(r),
                  h('div.muted.small',
                    `${c.done || 0}${c.error ? ` + ${c.error} err` : ''} / ${
                        c.total || 0}`)),
                h('td.num',
                  scoreBadge(
                      r.avg_score != null ? Math.round(r.avg_score * 10) / 10 :
                                            null,
                      {title: 'Average judge score'})),
                h('td', verdictCounts(r.verdicts),
                  r.verdicts && r.verdicts.unreviewed ?
                      h('span.muted.small',
                        ` ${r.verdicts.unreviewed} to review`) :
                      null),
                h('td.muted', relTime(r.started_at || r.created_at)),
                h('td.cell-actions',
                  h('button.icon-btn.run-del', {
                    type: 'button',
                    id: `delete-run-${r.id}`,
                    'aria-label': 'Delete run',
                    title: r.effect_id ? 'Delete effect study' : 'Delete run',
                    onclick: async (e) => {
                      e.stopPropagation();
                      if (!(await deleteRun(r))) return;
                      const i = picked.indexOf(r.id);
                      if (i >= 0) picked.splice(i, 1);
                      load();
                    }
                  },
                    icon('delete'))));
          }))));
  }

  mount(listEl, spinner('Loading runs\u2026'));
  await load();
  const preset = ctx.url.searchParams.get('new');
  if (preset) {
    history.replaceState(null, '', `/p/${pid}/runs`);
    openNewRunDialog({
      projectId: pid,
      preset: preset === 'baseline' ? 'baseline' :
          preset === 'selection'    ? 'selection' :
                                      null
    });
  }
  return () => {
    alive = false;
    clearTimeout(timer);
  };
}
