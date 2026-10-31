// The Teachings ledger (/p/:pid/teachings): every rule and template the
// project has learned, with phrasings, trials (expected vs followed) and the
// signal derived from them; plus the teaching sessions.
import {get, patch, post} from '../components/api.js';
import {emptyState, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {openPatternCapture} from '../components/pattern_capture.js';
import {modal, toast} from '../components/ui.js';

import {historyView, newSessionDialog, rateBar, ridChip, sessionTable, signalPill} from './teach.js';

const BAD = new Set(['not_followed', 'rephrased_no_gain']);
const FILTERS = [
  ['all', 'All'],
  ['attention', 'Needs attention'],
  ['rule', 'Rules'],
  ['pattern', 'Templates'],
  ['candidate', 'Candidates'],
  ['retired', 'Retired & escalated'],
];

export async function render(root, params, ctx) {
  const pid = params.pid;
  const url = ctx && ctx.url;
  let tab =
      url && url.searchParams.get('tab') === 'sessions' ? 'sessions' : 'rules';
  let filter = 'all';
  let data = null;
  let sessions = [];
  const open = new Set();
  const details = new Map();

  const page = h('div.page.teach-scope.ledger-page#teachings-page');
  root.appendChild(page);
  mount(page, spinner('Loading teachings\u2026'));
  await load();
  return () => {};

  async function load() {
    try {
      const [d, s] = await Promise.all([
        get(`/api/projects/${pid}/teachings`),
        get(`/api/projects/${pid}/teach`, {
          quiet: true
        }).catch(() => ({sessions: []})),
      ]);
      data = d;
      sessions = s.sessions || [];
    } catch (e) {
      mount(
          page, emptyState('Could not load teachings', String(e.message || e)));
      return;
    }
    draw();
  }

  function setTab(t) {
    tab = t;
    const u = new URL(location.href);
    if (t === 'sessions')
      u.searchParams.set('tab', 'sessions');
    else
      u.searchParams.delete('tab');
    history.replaceState(null, '', u.pathname + u.search);
    draw();
  }

  function draw() {
    const seg =
        h('div.segmented',
          h('button.seg-btn#ledger-tab-rules', {
            type: 'button',
            class: tab === 'rules' ? 'on' : '',
            onclick: () => setTab('rules')
          },
            icon('rule'), 'Rules & templates'),
          h('button.seg-btn#ledger-tab-sessions', {
            type: 'button',
            class: tab === 'sessions' ? 'on' : '',
            onclick: () => setTab('sessions')
          },
            icon('school'), 'Sessions ',
            h('span.mono', {style: 'opacity:.7'}, String(sessions.length))));
    mount(
        page,
        h('div.ledger-titlebar', h('h1', 'Teachings'), seg,
          h('span.topbar-spacer'),
          h('button.btn.btn-ghost#ledger-new-session', {
            type: 'button',
            onclick: () => newSessionDialog(
                pid, {defaultName: `Session ${sessions.length + 1}`})
          },
            icon('add'), 'New teaching session')),
        h('p.muted', {style: 'margin:6px 0 0'},
          'Every rule and template the project has learned, with its phrasings and how often the model actually followed it. Trials come from effect studies, the judge, and your verdicts on probe cards.'),
        tab === 'sessions' ?
            h('div', {style: 'margin-top:18px'},
              sessions.length ?
                  sessionTable(pid, sessions, load) :
                  emptyState(
                      'No teaching sessions yet',
                      'Teach this project by example.',
                      h('button.btn.btn-primary', {
                        type: 'button',
                        onclick: () =>
                            newSessionDialog(pid, {defaultName: 'Session 1'})
                      },
                        icon('school'), 'New teaching session'))) :
            rulesView());
  }

  function computedSummary(ts) {
    const by = (s) => ts.filter((t) => (t.efficacy || {}).signal === s).length;
    return {
      total: ts.length,
      rules: ts.filter((t) => t.kind === 'rule').length,
      templates: ts.filter((t) => t.kind === 'pattern').length,
      reliable: by('reliable'),
      flaky: by('flaky'),
      not_followed: by('not_followed'),
      rephrased_no_gain: by('rephrased_no_gain'),
      escalated: ts.filter((t) => t.status === 'escalated').length,
      retired: ts.filter((t) => t.status === 'retired').length,
      trials: ts.reduce((n, t) => n + ((t.efficacy || {}).trials || 0), 0),
    };
  }

  function legendText(th) {
    if (!th || typeof th !== 'object') return '';
    const pct = (v) =>
        v == null ? '?' : `${Math.round((v <= 1 ? v * 100 : v))} %`;
    const parts = [];
    for (const [k, v] of Object.entries(th)) {
      const label = k.replace(/_/g, ' ').replace('no gain', 'without gain');
      if (v && typeof v === 'object') {
        const bits = [];
        const mt = v.min_trials ?? v.trials;
        if (mt != null) bits.push(`\u2265 ${mt} trials`);
        if (v.min_phrasings != null)
          bits.push(`\u2265 ${v.min_phrasings} phrasings`);
        if (v.min_rate != null) bits.push(`\u2265 ${pct(v.min_rate)}`);
        if (v.max_rate != null) bits.push(`< ${pct(v.max_rate)}`);
        if (v.rate != null) bits.push(pct(v.rate));
        if (v.text || v.description) bits.push(v.text || v.description);
        parts.push(`${label} ${bits.join(' & ') || JSON.stringify(v)}`);
      } else {
        parts.push(`${label}: ${typeof v === 'number' && v <= 1 ? pct(v) : v}`);
      }
    }
    return `Legend: ${parts.join(' \u00b7 ')}`;
  }

  function matches(t) {
    const sig = (t.efficacy || {}).signal;
    if (filter === 'attention') return BAD.has(sig) || sig === 'flaky';
    if (filter === 'rule') return t.kind === 'rule' && t.status !== 'candidate';
    if (filter === 'pattern') return t.kind === 'pattern';
    if (filter === 'candidate') return t.status === 'candidate';
    if (filter === 'retired')
      return t.status === 'retired' || t.status === 'escalated';
    return t.status !== 'dismissed' && t.status !== 'merged';
  }

  function rulesView() {
    const ts = data.teachings || [];
    const cs = computedSummary(ts);
    const sm = {...cs, ...(data.summary || {})};
    const card = (cls, v, l) =>
        h('div.sum-card', {class: cls}, h('span.v', String(v ?? 0)),
          h('span.l', l));
    const rows = ts.filter(matches).sort(
        (a, b) => (BAD.has((b.efficacy || {}).signal) -
                   BAD.has((a.efficacy || {}).signal)) ||
            String(a.label).localeCompare(
                String(b.label), undefined, {numeric: true}));
    return h(
        'div',
        h('div.ledger-sum',
          card(
              '', sm.total,
              `tracked (${sm.rules ?? cs.rules} rules \u00b7 ${
                  sm.templates ?? cs.templates} templates)`),
          card('r', sm.reliable, 'reliable'), card('f', sm.flaky, 'flaky'),
          card('n', sm.not_followed, 'not followed'),
          card('g', sm.rephrased_no_gain, 'rephrased without gain'),
          card('', sm.escalated, `escalated \u00b7 ${sm.retired ?? 0} retired`),
          card(
              '', sm.trials,
              `trials${sm.runs != null ? ` across ${sm.runs} runs` : ''}`)),
        h('div.filters', 'Show ',
          FILTERS.map(
              ([v, l]) =>
                  h('span.chip', {
                    class: filter === v ? '' : 'chip-quiet',
                    role: 'button',
                    id: `ledger-filter-${v}`,
                    onclick: () => {
                      filter = v;
                      draw();
                    }
                  },
                    l)),
          data.thresholds ? h('span.lg', legendText(data.thresholds)) : null),
        rows.length ?
            h('table.ledger#ledger-table',
              h('thead',
                h('tr',
                  [
                    'Id', 'Teaching', 'Section', 'Status', 'Phrasings',
                    'Trials', 'Followed', 'Signal', ''
                  ].map((x) => h('th', x)))),
              h('tbody',
                rows.map(
                    (t) => [row(t), open.has(t.id) ? detailRow(t) : null]))) :
            emptyState(
                'Nothing here',
                filter === 'all' ? 'Teach by example to start the ledger.' :
                                   'No teachings match this filter.'));
  }

  function row(t) {
    const e = t.efficacy || {};
    const isOpen = open.has(t.id);
    const sub =
        [
          t.origin ? `${
                         t.origin === 'revision' ? 'bound from a revision' :
                                                   `born from ${t.origin}`}` :
                     '',
          t.session_id ? 'in a session' : '',
          e.last_trial_at ? `last trial ${relTime(e.last_trial_at)}` : ''
        ].filter(Boolean)
            .join(' \u00b7 ');
    return h(
        'tr', {class: isOpen ? 'open' : '', dataset: {tid: t.id}},
        h('td', ridChip(t)),
        h('td', h('div.title', t.title || t.body_md || ''),
          sub ? h('div.sub', sub) : null),
        h('td', t.kind === 'pattern' ? 'Template' : (t.section || '')),
        h('td', t.status,
          t.version > 1 ? h('span.muted', ` \u00b7 v${t.version}`) : null),
        h('td.ph', String(e.phrasings || t.version || 1)),
        h('td.ph', String(e.trials || 0)), h('td', rateBar(e)),
        h('td', signalPill(e.signal || t.status)),
        h('td',
          h('button.icon-btn', {
            type: 'button',
            'aria-label': isOpen ? 'Collapse' : 'Expand',
            onclick: () => toggle(t)
          },
            icon(isOpen ? 'expand_less' : 'expand_more'))));
  }

  async function toggle(t) {
    if (open.has(t.id)) {
      open.delete(t.id);
      draw();
      return;
    }
    open.add(t.id);
    draw();
    if (!details.has(t.id)) {
      try {
        details.set(t.id, await get(`/api/teachings/${t.id}`, {quiet: true}));
      } catch (e) {
        details.set(t.id, {error: String(e.message || e)});
      }
      draw();
    }
  }

  function replace(t) {
    if (!t) return;
    const i = (data.teachings || []).findIndex((x) => x.id === t.id);
    if (i >= 0)
      data.teachings[i] = {
        ...data.teachings[i],
        ...t,
        label: t.label || data.teachings[i].label
      };
    else
      data.teachings.push(t);
    details.delete(t.id);
  }

  function detailRow(t) {
    const d = details.get(t.id);
    const e = t.efficacy || {};
    const bad = BAD.has(e.signal);
    return h(
        'tr.detail',
        h('td', {colspan: 9},
          bad ?
              h('div.tch-callout', {style: 'margin:0 0 12px'},
                h('div.t', icon('report'),
                  `${e.phrasings || 1} phrasing${
                      (e.phrasings || 1) === 1 ? '' : 's'}, ${
                      e.trials || 0} trials, followed ${
                      e.followed ||
                      0}. The model is not getting this as prose.`),
                h('p',
                  'Ways out that don\u2019t rely on wording: capture a good example as a template, or hand it to the benchmark designers as a Don\u2019t the constitution can\u2019t fix.'),
                actions(t, d)) :
              h('div.acts', {
                style: 'display:flex;gap:6px;margin:4px 0 12px;flex-wrap:wrap'
              },
                actions(t, d)),
          !d ? spinner('Loading history\u2026') :
              d.error ? h('p.c-bad.small', d.error) :
                        historyView(d)));
  }

  function actions(t, d) {
    return h(
        'div.acts', {style: 'display:flex;gap:6px;flex-wrap:wrap'},
        h('button.btn.btn-ghost.btn-sm',
          {type: 'button', onclick: () => templateFromGood(t, d)},
          icon('dashboard_customize'),
          'Make it a template from a good example'),
        t.status !== 'escalated' ?
            h('button.btn.btn-ghost.btn-sm',
              {type: 'button', onclick: () => escalate(t)}, icon('flag'),
              'Escalate as unfixable Don\u2019t') :
            null,
        h('button.btn.btn-ghost.btn-sm',
          {type: 'button', onclick: (ev) => rephrase(t, ev.currentTarget)},
          icon('auto_awesome'), 'Rephrase once more (with the evidence)'),
        d && (d.versions || []).length > 1 ?
            h('button.btn.btn-ghost.btn-sm',
              {type: 'button', onclick: () => split(t, d)}, icon('call_split'),
              'Split binding') :
            null,
        t.status === 'candidate' ? h('button.btn.btn-primary.btn-sm', {
          type: 'button',
          onclick: async () => {
            replace((await patch(`/api/teachings/${t.id}`, {
                      status: 'confirmed'
                    })).teaching);
            draw();
          }
        },
                                     icon('check'), 'Confirm') :
                                   null);
  }

  async function templateFromGood(t, d) {
    const trials = (d && d.trials) || [];
    const pick = trials.find((x) => x.followed === 'yes') || trials[0];
    if (!pick) {
      toast(
          'No example output yet \u2014 open a teaching session and use \u201cMake a template from this\u201d on a card',
          'warn');
      return;
    }
    const res = await get(`/api/outputs/${pick.output_id}`);
    const output = res.output || res;
    openPatternCapture(
        {pid, output, selection: {whole: true}, onSaved: () => load()});
  }

  function escalate(t) {
    const noteIn = h('textarea.input', {
      rows: 3,
      placeholder: 'Optional note for benchmark / model designers'
    });
    const m = modal({
      title: `Escalate ${t.label} as an unfixable Don\u2019t?`,
      body: [
        h('p.muted.small',
          'Retires it from the constitution and records a Don\u2019t, with the trials as evidence.'),
        noteIn
      ],
      actions: [
        {label: 'Cancel'}, {
          label: 'Escalate',
          kind: 'danger',
          onClick: async () => {
            const res = await post(
                `/api/teachings/${t.id}/escalate`,
                {note: noteIn.value.trim() || undefined});
            replace(res.teaching);
            toast(`${t.label} escalated`, 'good');
            draw();
          }
        }
      ]
    });
    m.el.parentElement.classList.add('teach-scope');
  }

  async function rephrase(t, btn) {
    if (btn) btn.disabled = true;
    let suggestion;
    try {
      suggestion =
          (await post(`/api/teachings/${t.id}/rephrase`, {})).suggestion || {};
    } catch {
      if (btn) btn.disabled = false;
      return;
    }
    if (btn) btn.disabled = false;
    const body =
        h('textarea.input',
          {rows: 4, value: suggestion.body_md || t.body_md || ''});
    const when =
        h('input.input',
          {value: suggestion.applies_when || t.applies_when || ''});
    const reason = h('input.input', {value: suggestion.reason || ''});
    const m = modal({
      title: `Rephrase ${t.label}`,
      wide: true,
      body: [
        h('label.field', h('span.field-label', 'Rule'), body),
        h('label.field', h('span.field-label', 'Applies when'), when),
        h('label.field', h('span.field-label', 'Reason'), reason)
      ],
      actions: [
        {label: 'Cancel'}, {
          label: 'Save new phrasing',
          kind: 'primary',
          onClick: async () => {
            const res = await patch(`/api/teachings/${t.id}`, {
              body_md: body.value,
              applies_when: when.value,
              reason: reason.value.trim() || undefined
            });
            replace(res.teaching);
            draw();
          }
        }
      ]
    });
    m.el.parentElement.classList.add('teach-scope');
  }

  function split(t, d) {
    const sel = h(
        'select.input',
        (d.versions || [])
            .filter((v) => v.number > 1)
            .map(
                (v) => h(
                    'option', {value: v.number},
                    `v${v.number} \u2014 ${(v.body_md || '').slice(0, 70)}`)));
    const m = modal({
      title: `Split ${t.label}`,
      body: [
        h('p.muted.small', {style: 'margin-top:0'},
          'Turns a phrasing that was bound by similarity into its own teaching (with its trials).'),
        sel
      ],
      actions: [
        {label: 'Cancel'}, {
          label: 'Split',
          kind: 'primary',
          onClick: async () => {
            const res = await post(
                `/api/teachings/${t.id}/split`, {version: Number(sel.value)});
            replace(res.teaching);
            replace(res.new_teaching);
            toast(
                `Split into ${
                    res.new_teaching ? res.new_teaching.label :
                                       'a new teaching'}`,
                'good');
            draw();
          }
        }
      ]
    });
    m.el.parentElement.classList.add('teach-scope');
  }
}
