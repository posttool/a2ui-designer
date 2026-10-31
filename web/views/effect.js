// Constitution effect study: no constitution vs a revision, side by side,
// with structural layout changes outlined on the screenshots and attributed
// to constitution rules.
import {get, post} from '../components/api.js';
import {genreChip, loadGenres, scoreBadge, statusChip} from '../components/badges.js';
import {emptyState, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {navigate} from '../components/store.js';
import {segmented, toast, toggle} from '../components/ui.js';

import {outputShotForRenderer, RENDERER_META} from './run_detail.js';
import {runProgress} from './runs.js';

const VERDICTS = {
  effect: {label: 'Constitution effect', cls: 'v-effect'},
  noise: {label: 'Within noise', cls: 'v-noise'},
  unchanged: {label: 'No layout change', cls: 'v-same'},
  pending: {label: 'Pending', cls: 'v-pending'},
};

const CAUSES = {
  rule: 'rule',
  indirect: 'side effect',
  noise: 'noise',
};

const fmt1 = (x) =>
    (x == null ? '\u2013' : (Math.round(x * 10) / 10).toFixed(1));

export async function render(root, params, ctx) {
  const pid = params.pid;
  const eid = params.eid;
  await loadGenres();
  let data = null;
  let alive = true;
  let timer = null;
  const renderer = 'web';
  let verdictFilter = ctx.url.searchParams.get('show') || 'changed';
  let sort = 'magnitude';
  let ruleFilter = null;
  let kindFilter = null;
  // Difference outlines over the screenshots; persisted across visits. When
  // off, hovering a change still outlines just that change.
  let outlines = localStorage.getItem('a2uid.effectOutlines') !== '0';

  const head = h('div');
  const summaryEl = h('div');
  const filterEl = h('div');
  const pairsEl =
      h('div.effect-pairs#effect-pairs', spinner('Loading study\u2026'));
  const page =
      h('div.page.page-effect', {class: outlines ? '' : 'outlines-off'}, head,
        summaryEl, filterEl, pairsEl);
  root.appendChild(page);

  async function load() {
    try {
      data = await get(`/api/effects/${eid}`, {quiet: !!data});
    } catch (e) {
      if (!data)
        mount(pairsEl, emptyState('Study not found', String(e.message || e)));
      return;
    }
    if (!alive) return;
    draw();
    clearTimeout(timer);
    const st = data.effect.status;
    if (st === 'running' || st === 'analyzing') timer = setTimeout(load, 2500);
  }

  function draw() {
    drawHead();
    drawSummary();
    drawFilters();
    drawPairs();
  }

  // ---------------------------------------------------------------- head
  function drawHead() {
    const e = data.effect;
    const runs = e.runs || {};
    const an = data.analysis || {};
    const armRow = (label, r) => r ?
        h('a.effect-arm', {href: `/p/${pid}/runs/${r.id}`},
          h('span.effect-arm-label', label), runProgress(r),
          h('span.muted.small',
            `${(r.counts || {}).done || 0}/${(r.counts || {}).total || 0}`)) :
        null;
    const analyzing = e.status === 'analyzing' || an.status === 'running';
    mount(
        head,
        h('div.crumbs', h('a', {href: `/p/${pid}/runs`}, 'Runs'),
          h('span', '/'), h('span', 'Constitution effect')),
        h('div.page-head',
          h('div',
            h('h1', h('span', 'No constitution'), h('span.muted', ' \u2192 '),
              h('span', `rev ${e.revision_number}`)),
            h('p.lede.small',
              'Same samples, viewports and generator. Differences between the arms are what the constitution did to the layout \u2014 minus the noise floor of two runs with no constitution.')),
          h('div.page-actions',
            runs.control && runs.treatment ?
                h('a.btn.btn-ghost#effect-open-compare', {
                  href: `/p/${pid}/compare?a=${runs.control.id}&b=${
                      runs.treatment.id}`
                },
                  icon('compare'), 'Compare view') :
                null,
            h('button.btn.btn-ghost#effect-reanalyze', {
              type: 'button',
              disabled: e.status === 'running' || analyzing,
              title: 'Re-run the rule attribution pass',
              onclick: async () => {
                data = await post(`/api/effects/${eid}/analyze`, {});
                toast('Re-analyzing', 'info');
                draw();
                clearTimeout(timer);
                timer = setTimeout(load, 2000);
              }
            },
              icon('psychology'), 'Re-analyze'))),
        h('div.effect-arms', armRow('No constitution', runs.control),
          armRow(`rev ${e.revision_number}`, runs.treatment),
          armRow('No constitution (noise)', runs.noise),
          h('div.effect-arm.effect-arm-status', statusChip(e.status),
            analyzing && an.total != null ?
                h('span.muted.small',
                  `attributing ${an.done || 0}/${an.total} changed outputs`) :
                an.status === 'error' ?
                h('span.c-bad.small', `attribution failed: ${an.error || ''}`) :
                h('span.muted.small', relTime(e.created_at)))));
  }

  // -------------------------------------------------------------- summary
  function drawSummary() {
    const s = data.summary || {};
    const v = s.verdicts || {};
    const jd = s.judge || {};
    const jdelta = jd.control != null && jd.treatment != null ?
        jd.treatment - jd.control :
        null;
    const ov = s.overall || {};
    const cell = (label, value, cls = '', title = null) =>
        h('div.cs-cell', {title}, h('span.cs-label', label),
          h('span.cs-num', {class: cls}, value));
    const rules =
        (s.rules || [])
            .slice()
            .sort(
                (a, b) => (b.changes - a.changes) ||
                    (b.unapplied - a.unapplied) ||
                    a.id.localeCompare(b.id, undefined, {numeric: true}));
    const maxHits = Math.max(1, ...rules.map((r) => r.changes));
    mount(
        summaryEl,
        h('div.compare-summary.effect-summary',
          cell(
              'Changed by constitution', `${v.effect || 0}/${s.total || 0}`,
              'c-effect',
              'Layout differs from no-constitution by more than the noise floor (or by rule-attributed changes)'),
          cell(
              'Within noise', `${v.noise || 0}`, '',
              'Differs, but no more than two no-constitution runs differ from each other'),
          cell('Unchanged', `${v.unchanged || 0}`),
          cell(
              'Avg layout change', fmt1(s.avg_magnitude), '',
              '0\u2013100: share of content whose structure changed'),
          cell(
              'Noise floor', fmt1(s.avg_noise_magnitude), 'muted',
              'Avg change between the two no-constitution runs'),
          cell(
              'Judge', `${fmt1(jd.control)} \u2192 ${fmt1(jd.treatment)}`,
              jdelta > 0     ? 'c-good' :
                  jdelta < 0 ? 'c-bad' :
                               '',
              'Average judge score; both arms judged against the revision'),
          Object.keys(ov).length ?
              cell(
                  'LLM verdict',
                  h('span.effect-overall',
                    ['improved', 'mixed', 'neutral', 'regressed']
                        .filter((k) => ov[k])
                        .map((k) => h(`span.ov.ov-${k}`, `${ov[k]} ${k}`)))) :
              null),
        h('div.effect-panels',
          h('section.effect-panel.effect-rules#effect-rules',
            h('div.effect-panel-head', h('h2', 'Rule impact'),
              h('span.muted.small',
                'Changes attributed to each rule across all outputs')),
            !rules.length ?
                h('p.muted', 'No rules found in this revision.') :
                h('table.table.rule-table',
                  h('thead',
                    h('tr', h('th', 'Rule'), h('th.num', 'Changes'),
                      h('th.num', 'Outputs'), h('th', 'Kinds'),
                      h('th.num',
                        {title: 'Applied to the input but no visible effect'},
                        'No effect'))),
                  h('tbody',
                    rules.map(
                        (r) => h(
                            'tr.row-click', {
                              class: ruleFilter === r.id ? 'sel' : '',
                              dataset: {rule: r.id},
                              onclick: () => {
                                ruleFilter = ruleFilter === r.id ? null : r.id;
                                draw();
                              }
                            },
                            h('td.rule-cell', h('span.rule-id', r.id),
                              h('span.rule-text', {title: r.text}, r.text),
                              r.section ?
                                  h('span.muted.small.rule-sec', r.section) :
                                  null),
                            h('td.num',
                              h('span.rule-bar', h('span.rule-bar-fill', {
                                  style: `width:${(100 * r.changes) / maxHits}%`
                                })),
                              `${r.changes}`),
                            h('td.num', `${r.pairs}`),
                            h('td',
                              Object.entries(r.kinds || {})
                                  .slice(0, 3)
                                  .map(([k, n]) => kindChip(k, n))),
                            h('td.num',
                              r.unapplied ?
                                  h('span.c-mixed', {
                                    title:
                                        'Relevant to these inputs, but the generator didn\u2019t act on it'
                                  },
                                    `${r.unapplied}`) :
                                  h('span.muted', '0'))))))),
          h('section.effect-panel#effect-genres',
            h('div.effect-panel-head', h('h2', 'Where it changes layout')),
            h('div.genre-bars',
              (s.by_genre || [])
                  .map(
                      (g) =>
                          h('div.genre-bar-row', genreChip(g.genre),
                            h('span.genre-bar',
                              h('span.genre-bar-fill.fill-effect', {
                                style: `width:${
                                    (100 * g.effect) / Math.max(1, g.total)}%`
                              }),
                              h('span.genre-bar-fill.fill-noise', {
                                style: `width:${
                                    (100 * (g.changed - g.effect)) /
                                    Math.max(1, g.total)}%`
                              })),
                            h('span.muted.small', `${g.effect}/${g.total}`)))),
            h('div.effect-panel-head.mt', h('h2', 'Change types')),
            h('div.kind-chips',
              Object.entries(s.kinds_effect || s.kinds || {})
                  .map(
                      ([k, n]) =>
                          h('button.kind-filter', {
                            type: 'button',
                            class: kindFilter === k ? 'on' : '',
                            onclick: () => {
                              kindFilter = kindFilter === k ? null : k;
                              draw();
                            }
                          },
                            kindChip(k, n)))))));
  }

  function kindChip(kind, n) {
    return h(
        `span.kchip.k-${kind}`, kindLabel(kind),
        n != null ? h('b', ` ${n}`) : null);
  }

  // -------------------------------------------------------------- filters
  function drawFilters() {
    const rf =
        ruleFilter && (data.rules || []).find((r) => r.id === ruleFilter);
    mount(
        filterEl,
        h('div.filterbar',
          segmented(
              [
                {value: 'changed', label: 'Changed', id: 'eff-show-changed'},
                {
                  value: 'effect',
                  label: 'Constitution effect',
                  id: 'eff-show-effect'
                },
                {value: 'all', label: 'All', id: 'eff-show-all'},
              ],
              verdictFilter,
              (v) => {
                verdictFilter = v;
                drawPairs();
              }),
          segmented(
              [
                {
                  value: 'magnitude',
                  label: 'Biggest change',
                  id: 'eff-sort-mag'
                },
                {value: 'judge', label: 'Judge \u0394', id: 'eff-sort-judge'},
                {value: 'genre', label: 'Genre', id: 'eff-sort-genre'},
              ],
              sort,
              (v) => {
                sort = v;
                drawPairs();
              }),
          toggle({
            id: 'eff-outlines',
            checked: outlines,
            label: 'Outlines',
            onChange: (on) => {
              outlines = on;
              localStorage.setItem('a2uid.effectOutlines', on ? '1' : '0');
              page.classList.toggle('outlines-off', !on);
            },
          }),
          rf ? h('button.chip.chip-filter#eff-rule-filter', {
            type: 'button',
            title: rf.text,
            onclick: () => {
              ruleFilter = null;
              draw();
            }
          },
                 `${rf.id}: ${rf.text.slice(0, 40)}`, icon('close')) :
               null,
          kindFilter ? h('button.chip.chip-filter#eff-kind-filter', {
            type: 'button',
            onclick: () => {
              kindFilter = null;
              draw();
            }
          },
                         kindLabel(kindFilter), icon('close')) :
                       null));
  }

  // ---------------------------------------------------------------- pairs
  function judgeDelta(p) {
    const a = p.control && p.control.judge_score;
    const b = p.treatment && p.treatment.judge_score;
    return a != null && b != null ? b - a : null;
  }

  function drawPairs() {
    let list = (data.pairs || []).slice();
    if (verdictFilter === 'changed')
      list = list.filter(
          (p) => p.verdict === 'effect' || p.verdict === 'noise' ||
              p.verdict === 'pending');
    else if (verdictFilter === 'effect')
      list = list.filter((p) => p.verdict === 'effect');
    if (ruleFilter)
      list = list.filter(
          (p) => p.changes.some(
              (c) => c.attribution &&
                  (c.attribution.rule_ids || []).includes(ruleFilter)));
    if (kindFilter)
      list = list.filter((p) => p.changes.some((c) => c.kind === kindFilter));
    if (sort === 'genre')
      list.sort(
          (x, y) => String(x.genre).localeCompare(String(y.genre)) ||
              String(x.sample_title).localeCompare(String(y.sample_title)));
    else if (sort === 'judge')
      list.sort((x, y) => (judgeDelta(x) ?? 0) - (judgeDelta(y) ?? 0));
    else
      list.sort((x, y) => (y.magnitude ?? -1) - (x.magnitude ?? -1));
    if (!list.length) {
      const st = data.effect.status;
      mount(
          pairsEl,
          st === 'running' ?
              spinner('Generating both arms\u2026') :
              emptyState(
                  'Nothing to show',
                  verdictFilter === 'all' ?
                      'This study has no outputs.' :
                      'No outputs match. Try \u201cAll\u201d to see unchanged ones.'));
      return;
    }
    mount(pairsEl, list.map(pairCard));
  }

  function pairCard(p) {
    const v = VERDICTS[p.verdict] || VERDICTS.pending;
    const jd = judgeDelta(p);
    const att = p.attribution || {};
    const card = h('article.effect-pair', {dataset: {key: p.key}});
    const shotA =
        shot(p.control, p.control_boxes, p.changes, 'a', 'No constitution');
    const shotB = shot(
        p.treatment, p.treatment_boxes, p.changes, 'b',
        `rev ${data.effect.revision_number}`);
    const ruleText = (rid) =>
        ((data.rules || []).find((r) => r.id === rid) || {}).text || rid;
    const focus = (idx, on) => {
      card.classList.toggle('focusing', on);
      card.querySelectorAll('.eff-box').forEach((b) => {
        b.classList.toggle('on', on && b.dataset.c === String(idx));
      });
      card.querySelectorAll('.eff-change').forEach((r) => {
        r.classList.toggle('on', on && r.dataset.c === String(idx));
      });
    };
    card.addEventListener('mouseover', (e) => {
      const t = e.target.closest('[data-c]');
      if (t && card.contains(t)) focus(t.dataset.c, true);
    });
    card.addEventListener('mouseleave', () => focus(null, false));
    card.addEventListener('click', (e) => {
      const b = e.target.closest('.eff-box');
      if (!b) return;
      e.preventDefault();
      const row = card.querySelector(`.eff-change[data-c="${b.dataset.c}"]`);
      if (row) {
        row.scrollIntoView({block: 'nearest', behavior: 'smooth'});
        row.classList.add('flash');
        setTimeout(() => row.classList.remove('flash'), 900);
      }
    });
    const changes = p.changes.map((c) => {
      const a = c.attribution;
      return h(
          `li.eff-change.k-${c.kind}`, {dataset: {c: String(c.index)}},
          h('div.eff-change-head', h('span.kdot'),
            h('span.kname', c.kind_label),
            c.in_noise ? h('span.tag.tag-sm.tag-noise', {
              title:
                  'The same change also appears between the two no-constitution runs'
            },
                           'also in noise') :
                         null,
            a ? h(`span.cause.cause-${a.cause}`, CAUSES[a.cause] || a.cause) :
                null,
            a && a.confidence ? h('span.muted.small', a.confidence) : null),
          h('div.eff-change-label', c.label),
          a ? h('div.eff-attr',
                (a.rule_ids || [])
                    .map(
                        (rid) =>
                            h('button.rule-chip', {
                              type: 'button',
                              title: ruleText(rid),
                              onclick: (e) => {
                                e.stopPropagation();
                                ruleFilter = rid;
                                draw();
                              }
                            },
                              rid)),
                h('span.eff-expl', a.explanation || '')) :
              null);
    });
    const unapplied = att.unapplied_rules || [];
    mount(
        card,
        h('div.cmp-head', h('div.cmp-title', p.sample_title),
          genreChip(p.genre), h('span.muted', p.viewport), h('span.grow'),
          h(`span.verdict-chip.${v.cls}`, v.label),
          p.magnitude != null ?
              h('span.mag',
                {title: 'Layout change (0\u2013100) vs. noise floor'},
                h('b', `\u0394 ${p.magnitude}`),
                p.noise_magnitude != null ?
                    h('span.muted', ` / noise ${p.noise_magnitude}`) :
                    null) :
              null,
          h('span.judge-pair', scoreBadge(p.control && p.control.judge_score),
            icon('arrow_forward'),
            scoreBadge(p.treatment && p.treatment.judge_score),
            jd != null && jd !== 0 ?
                h('span.delta', {class: jd > 0 ? 'up' : 'down'},
                  `${jd > 0 ? '+' : ''}${jd}`) :
                null)),
        att.summary || att.error ?
            h('p.effect-pair-summary',
              att.overall ? h(`span.ov.ov-${att.overall}`, att.overall) : null,
              att.error ? h('span.c-bad', `Attribution failed: ${att.error}`) :
                          att.summary) :
            null,
        h('div.effect-grid', shotA, shotB,
          h('div.eff-changes',
            p.changes.length ?
                h('ol.eff-change-list', changes) :
                h('p.muted',
                  p.verdict === 'pending' ? 'Waiting for both arms\u2026' :
                                            'No structural layout changes.'),
            unapplied.length ?
                h('div.eff-unapplied',
                  h('span.muted.small', 'Applies here, no visible effect: '),
                  unapplied.map(
                      (u) =>
                          h('span.rule-chip.rule-chip-warn',
                            {title: `${ruleText(u.rule_id)}\n\n${u.why || ''}`},
                            u.rule_id))) :
                null)));
    return card;
  }

  function shot(o, boxes, changes, side, label) {
    if (!o) {
      return h('div.eff-side.cmp-missing', h('span.muted', 'Not in this arm'));
    }
    const s = outputShotForRenderer(o, renderer);
    const overlay = h('div.eff-overlay');
    if (renderer === 'web' && boxes && boxes.width && boxes.height) {
      for (const c of changes) {
        const keys = side === 'a' ? c.a_focus : c.b_focus;
        for (const k of keys || []) {
          for (const [x, y, w, hh] of (boxes.boxes || {})[k] || []) {
            overlay.appendChild(h(`span.eff-box.k-${c.kind}`, {
              dataset: {c: String(c.index)},
              title: `${c.kind_label}: ${c.label}`,
              style: `left:${(100 * x) / boxes.width}%;top:${
                  (100 * y) /
                  boxes.height}%;width:${(100 * w) / boxes.width}%;height:${
                  (100 * hh) / boxes.height}%`,
            }));
          }
        }
      }
    }
    return h(
        'div.eff-side', {class: `side-${side}`},
        h('div.eff-side-head', h('span.eff-side-label', label),
          scoreBadge(o.judge_score),
          o.validation && o.validation.ok === false ?
              h('span.chip.c-bad',
                `${(o.validation.errors || []).length} invalid`) :
              null,
          h('span.grow'),
          h('a.link-btn.small', {
            href: `/p/${pid}/review/${o.run_id}?o=${o.id}`,
            onclick: (e) => {
              e.preventDefault();
              navigate(`/p/${pid}/review/${o.run_id}?o=${o.id}`);
            }
          },
            'Review')),
        h('div.eff-shot',
          s.full || s.thumb ?
              h('div.eff-shot-inner',
                h('img', {src: s.full || s.thumb, alt: label, loading: 'lazy'}),
                overlay) :
              h('div.tile-placeholder',
                h('span',
                  o.status !== 'done' ?
                      `${o.status}\u2026` :
                      `Not rendered on ${
                          (RENDERER_META[renderer] || {}).short ||
                          renderer}`))));
  }

  function kindLabel(kind) {
    return {
      container_swap: 'Container changed',
      regrouped: 'New grouping',
      ungrouped: 'Grouping removed',
      component_swap: 'Component changed',
      emphasis: 'Hierarchy',
      reordered: 'Reordered',
      added: 'Added',
      removed: 'Dropped',
    }[kind] ||
        kind;
  }

  await load();
  return () => {
    alive = false;
    clearTimeout(timer);
  };
}
