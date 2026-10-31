// Compare two runs: side-by-side pairs with judge score deltas.
import {get} from '../components/api.js';
import {genreChip, loadGenres, scoreBadge, verdictMark} from '../components/badges.js';
import {emptyState, h, icon, mount, spinner} from '../components/dom.js';
import {navigate} from '../components/store.js';
import {segmented} from '../components/ui.js';

import {outputShotForRenderer, RENDERER_META} from './run_detail.js';

export async function render(root, params, ctx) {
  const pid = params.pid;
  const a = ctx.url.searchParams.get('a');
  const b = ctx.url.searchParams.get('b');
  await loadGenres();
  if (!a || !b) {
    root.appendChild(
        h('div.page', h('h1', 'Compare runs'),
          emptyState(
              'Pick two runs',
              'Select two runs on the Runs page and press Compare.',
              h('a.btn.btn-primary', {href: `/p/${pid}/runs`}, 'Go to Runs'))));
    return;
  }
  const body =
      h('div.compare-body#compare-pairs', spinner('Loading comparison\u2026'));
  const head = h('div');
  root.appendChild(h('div.page.page-compare', head, body));
  const [ra, rb, cmp] = await Promise.all([
    get(`/api/runs/${a}`),
    get(`/api/runs/${b}`),
    get(`/api/projects/${pid}/compare?run_a=${a}&run_b=${b}`),
  ]);
  const runA = ra.run;
  const runB = rb.run;
  const pairs = cmp.pairs || [];
  const s = cmp.summary || {};
  let sort = 'delta_asc';
  let onlyChanged = false;
  const activeRenderer = 'web';

  const delta = (p) =>
      (p.a && p.b && p.a.judge_score != null && p.b.judge_score != null ?
           p.b.judge_score - p.a.judge_score :
           null);
  const fmtAvg = (x) =>
      (x == null ? '\u2013' : (Math.round(x * 10) / 10).toFixed(1));
  const avgDelta =
      s.a_avg != null && s.b_avg != null ? s.b_avg - s.a_avg : null;
  const improved = pairs.filter((p) => (delta(p) || 0) > 0).length;
  const regressed = pairs.filter((p) => (delta(p) || 0) < 0).length;

  const tokA =
      runA.rendered_token_revision_number || runA.token_revision_number || 1;
  const tokB =
      runB.rendered_token_revision_number || runB.token_revision_number || 1;
  const tokChanged = (runA.rendered_theme_hash || runA.theme_hash) !==
      (runB.rendered_theme_hash || runB.theme_hash);

  mount(
      head,
      h('div.crumbs', h('a', {href: `/p/${pid}/runs`}, 'Runs'), h('span', '/'),
        h('span', 'Compare')),
      h('div.page-head',
        h('div',
          h('h1',
            `rev ${runA.revision_number} \u2192 rev ${runB.revision_number}`),
          h('p.lede.small',
            `${runA.sample_set_name || 'selection'} (Tokens T${tokA}) vs ${
                runB.sample_set_name ||
                'selection'} (Tokens T${tokB}) \u00b7 ${pairs.length} pairs`)),
        h('div.page-actions',
          tokChanged ? h('span.chip.chip-xs.token-drift-pill', icon('palette'),
                         `Tokens T${tokA} \u2192 T${tokB}`) :
                       h('span.chip.chip-xs.token-rev-chip', icon('palette'),
                         `Tokens T${tokA}`),
          h('a.btn.btn-ghost#compare-swap',
            {href: `/p/${pid}/compare?a=${b}&b=${a}`}, icon('swap_horiz'),
            'Swap'))),
      h('div.compare-summary',
        h('div.cs-cell',
          h('span.cs-label', `rev ${runA.revision_number} \u00b7 T${tokA} avg`),
          h('span.cs-num', fmtAvg(s.a_avg))),
        h('div.cs-arrow', icon('arrow_forward')),
        h('div.cs-cell',
          h('span.cs-label', `rev ${runB.revision_number} \u00b7 T${tokB} avg`),
          h('span.cs-num', fmtAvg(s.b_avg))),
        h('div.cs-cell', h('span.cs-label', 'change'),
          h('span.cs-num.delta',
            {class: avgDelta > 0 ? 'up' :
                 avgDelta < 0    ? 'down' :
                                   ''},
            avgDelta == null ?
                '\u2013' :
                `${avgDelta > 0 ? '+' : ''}${avgDelta.toFixed(1)}`)),
        h('div.cs-cell', h('span.cs-label', 'improved'),
          h('span.cs-num.c-good', `${improved}`)),
        h('div.cs-cell', h('span.cs-label', 'regressed'),
          h('span.cs-num.c-bad', `${regressed}`))),
      h('div.filterbar',
        segmented(
            [
              {
                value: 'delta_asc',
                label: 'Regressions first',
                id: 'cmp-sort-regress'
              },
              {
                value: 'delta_desc',
                label: 'Improvements first',
                id: 'cmp-sort-improve'
              },
              {value: 'genre', label: 'By genre', id: 'cmp-sort-genre'},
            ],
            sort,
            (v) => {
              sort = v;
              draw();
            }),
        h('label.check', h('input#cmp-only-changed', {
            type: 'checkbox',
            onchange: (e) => {
              onlyChanged = e.target.checked;
              draw();
            }
          }),
          'Only changed scores')));

  function side(o, run) {
    if (!o)
      return h('div.cmp-side.cmp-missing', h('span.muted', 'Not in this run'));
    const shot = outputShotForRenderer(o, activeRenderer);
    return h(
        'a.cmp-side', {
          href:
              `/p/${pid}/review/${run.id}?o=${o.id}`
        },
        h('div.cmp-shot',
          shot.full || shot.thumb ?
              h('img',
                {src: shot.full || shot.thumb, alt: '', loading: 'lazy'}) :
              h('div.tile-placeholder',
                h('span',
                  `Not rendered on ${
                      (RENDERER_META[activeRenderer] || {}).short ||
                      activeRenderer}`))),
        h('div.cmp-meta',
          h('span.muted',
            `rev ${run.revision_number} \u00b7 T${
                run.rendered_token_revision_number ||
                run.token_revision_number || 1}`),
          scoreBadge(o.judge_score), verdictMark(o.human_verdict),
          o.validation && !o.validation.ok ?
              h('span.chip.c-bad',
                `${(o.validation.errors || []).length} invalid`) :
              null));
  }

  function draw() {
    let list = pairs.slice();
    if (onlyChanged) list = list.filter((p) => delta(p));
    if (sort === 'genre')
      list.sort(
          (x, y) => String(x.genre).localeCompare(String(y.genre)) ||
              String(x.sample_title).localeCompare(String(y.sample_title)));
    else
      list.sort(
          (x, y) => ((delta(x) ?? 0) - (delta(y) ?? 0)) *
              (sort === 'delta_asc' ? 1 : -1));
    if (!list.length) {
      mount(
          body,
          emptyState(
              'Nothing to compare',
              onlyChanged ? 'No scores changed.' :
                            'These runs share no samples.'));
      return;
    }
    mount(body, list.map((p) => {
      const d = delta(p);
      return h(
          'article.cmp-pair', {dataset: {sampleId: p.sample_id}},
          h('div.cmp-head', h('div.cmp-title', p.sample_title),
            genreChip(p.genre), h('span.muted', p.viewport), h('span.grow'),
            h('span.delta',
              {class: d > 0 ? 'up' :
                   d < 0    ? 'down' :
                              ''},
              d == null ? '\u2013' : `${d > 0 ? '+' : ''}${d}`)),
          h('div.cmp-sides', side(p.a, runA), side(p.b, runB)));
    }));
  }
  draw();
  return () => {};
}
