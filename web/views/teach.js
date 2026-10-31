// Teaching sessions.
//   /p/:pid/teach        session list + "New teaching session"
//   /p/:pid/teach/:sid   the 3-pane Teach workspace (input data · live card ·
//                        observations / learned so far) and the Finish
//                        screen (assemble → save → Before vs. After).
// Contract: docs/TEACH.md. Polarity, anchors and intent are authored by the
// designer (buttons) or by the server/LLM — never inferred from words here.
import {cached, del, get, patch, post} from '../components/api.js';
import {beforeAfter} from '../components/before_after.js';
import {compLabel, dataTree, keyMatches, splitKey} from '../components/datatree.js';
import {emptyState, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {openPatternCapture} from '../components/pattern_capture.js';
import {cropScreenshot, regionSelect, unionBox} from '../components/region_select.js';
import {navigate} from '../components/store.js';
import {confirmDialog, drawer, modal, toast} from '../components/ui.js';

import {mountImmersive} from './teach_immersive.js';

const CARD_W = 412;
const BUSY = new Set(
    ['queued', 'generating', 'rendering', 'judging', 'running', 'pending']);
// Workspace mode: 'immersive' (default) or 'expert' (3 panes). The mode is
// per session view and never sticky across sessions/projects: it comes from
// `?mode=immersive|expert` (kept in the URL when toggled), and a session with
// no observations yet always opens immersive.
const LEGACY_MODE_KEY = 'a2uid.teach.mode';
function initialMode(state) {
  try {
    localStorage.removeItem(LEGACY_MODE_KEY);  // old sticky preference
  } catch {
  }
  const q = new URLSearchParams(location.search).get('mode');
  const hasObs = ((state && state.observations) || []).length > 0;
  return q === 'expert' && hasObs ? 'expert' : 'immersive';
}

// Quick chips only insert text into the note; they carry no semantics.
const QUICK_CHIPS = [
  ['add', 'Should be the lead', 'fix'],
  ['add', 'Wrong component for this data', 'fix'],
  ['add', 'Hide / make secondary', 'fix'],
  ['add', 'Missing \u2014 should be shown', 'fix'],
  ['check', 'Good way to show this', 'good'],
];

export const SIGNALS = {
  reliable: ['reliable', 'reliable'],
  flaky: ['flaky', 'flaky'],
  not_followed: ['notfollowed', 'not followed'],
  rephrased_no_gain: ['rephrased', 'rephrased without gain'],
  untested: ['untested', 'untested'],
  retired: ['retired', 'retired'],
  escalated: ['escalated', 'escalated'],
  candidate: ['cand', 'candidate'],
};

// ------------------------------------------------------------ shared bits

export function signalPill(signal) {
  const s = SIGNALS[signal] ||
      [signal || 'untested', (signal || 'untested').replace(/_/g, ' ')];
  return h('span.sig', {class: s[0]}, s[1]);
}

export function ridChip(t) {
  if (!t) return null;
  const cls =
      t.kind === 'pattern' ? 'tpl' : (t.status === 'candidate' ? 'cand' : '');
  return h(
      'span.rid', {class: cls, title: t.title || t.id},
      t.label ||
          (t.kind === 'pattern'         ? 'T' :
               t.status === 'candidate' ? 'C' :
                                          'R'));
}

/** Little followed/partial/not-followed bars from a teaching's efficacy. */
export function effBars(eff) {
  const e = eff || {};
  const y = e.followed || 0, p = e.partial || 0;
  const n = e.not_followed || Math.max(0, (e.trials || 0) - y - p);
  const cells = [];
  for (let i = 0; i < y; i++) cells.push('y');
  for (let i = 0; i < p; i++) cells.push('p');
  for (let i = 0; i < n; i++) cells.push('n');
  const shown = cells.slice(0, 10);
  while (shown.length < 4) shown.push('');
  return h('span.bars', shown.map((c) => h('i', {class: c})));
}

export function rateBar(eff) {
  const e = eff || {};
  if (!e.trials) return h('span.muted.small', 'no trials');
  const rate = e.rate != null ? e.rate : (e.followed || 0) / e.trials;
  const cls = rate >= 0.8 ? 'g' : (rate >= 0.3 ? 'm' : 'b');
  const fol = (e.followed || 0) + (e.partial || 0) / 2;
  return h(
      'div.rate',
      h('div.bar',
        h('i', {class: cls, style: {width: `${Math.round(rate * 100)}%`}})),
      h('span.pct',
        `${Number.isInteger(fol) ? fol : fol.toFixed(1)}/${e.trials}`));
}

/** Chip for one observation anchor. */
export function anchorChip(a, {links, onRemove, onClick} = {}) {
  const comp = (key) => {
    const c =
        links && (links.components || []).find((x) => keyMatches(x.key, key));
    return c ? compLabel(c) : null;
  };
  let cls = 'comp', ic = 'widgets', text = '';
  if (a.kind === 'component') {
    text = a.label || comp(a.key) ||
        `${a.component || 'Component'} #${a.id || splitKey(a.key).id}`;
    const sc = splitKey(a.key).scope;
    if (sc && !text.includes(sc)) text += ` ${sc}`;
  } else if (a.kind === 'data') {
    cls = 'data';
    ic = 'data_object';
    text = `${a.path}${
        a.value_preview != null && a.value_preview !== '' ?
            ` = ${a.value_preview}` :
            ''}`;
  } else if (a.kind === 'relation') {
    cls = 'rel';
    ic = 'link';
    text = `${a.path} \u21c4 ${a.label || comp(a.key) || a.component || a.key}`;
  } else if (a.kind === 'region') {
    cls = 'region';
    ic = 'crop_free';
    const b = a.box || [0, 0, 0, 0];
    text = `region ${Math.round(b[2])}\u00d7${Math.round(b[3])} @ y ${
        Math.round(b[1])}`;
  } else {
    cls = 'region';
    ic = 'crop_landscape';
    text = 'whole card';
  }
  return h(
      'span.anc', {class: cls, title: a.why || text, onclick: onClick || null},
      icon(ic), text, a.why ? h('span.muted', ` (${a.why})`) : null,
      onRemove ? h('span.x', {
        role: 'button',
        'aria-label': 'Remove anchor',
        onclick: (e) => {
          e.stopPropagation();
          onRemove(a);
        }
      },
                   '\u2715') :
                 null);
}

/** "New teaching session" dialog → creates the session and opens it. */
export async function newSessionDialog(pid, {defaultName} = {}) {
  let domains = [];
  try {
    domains = (await cached('/api/wizard/domains')).domains || [];
  } catch {
  }
  const groups = new Set(domains.slice(0, 2).map((d) => d.id));
  const nameIn =
      h('input.input#ts-name',
        {value: defaultName || '', placeholder: 'Session 1'});
  const briefIn = h('textarea.input#ts-brief', {
    rows: 3,
    placeholder:
        'Optional: what should these cards be about? (e.g. caregivers glancing at medication schedules)'
  });
  const countIn = h('select.input.input-sm#ts-count', [
    4, 5, 6, 7, 8
  ].map((n) => h('option', {value: n, selected: n === 6}, `${n} cards`)));
  const grid = h('div.ts-domains');
  function drawDomains() {
    mount(
        grid,
        domains.map(
            (d) =>
                h('button.ts-dom', {
                  type: 'button',
                  class: groups.has(d.id) ? 'on' : '',
                  'aria-pressed': String(groups.has(d.id)),
                  onclick: () => {
                    if (groups.has(d.id)) {
                      if (groups.size > 1) groups.delete(d.id);
                    } else {
                      groups.add(d.id);
                    }
                    drawDomains();
                  }
                },
                  icon(
                      groups.has(d.id) ? 'check_circle' :
                                         'radio_button_unchecked'),
                  h('b', d.label),
                  h('span', d.subtitle || d.description || ''))));
  }
  drawDomains();
  const m = modal({
    title: 'New teaching session',
    wide: true,
    id: 'new-teach-session',
    body: [
      h('p.muted.small', {style: 'margin-top:0'},
        'Renders a baseline of phone cards with the current constitution (~1 min). You leave anchored observations, Gemini distills rules you confirm as you go.'),
      h('label.field', h('span.field-label', 'Name'), nameIn),
      h('label.field',
        h('span.field-label', 'Brief ', h('span.muted', '(optional)')),
        briefIn),
      domains.length ?
          h('div.field', h('span.field-label', 'Scenario domains'), grid) :
          null,
      h('label.field', h('span.field-label', 'Starter cards'), countIn),
    ],
    actions: [
      {label: 'Cancel'},
      {
        label: 'Start teaching session',
        kind: 'primary',
        id: 'ts-create',
        onClick: async () => {
          const st = await post(`/api/projects/${pid}/teach`, {
            name: nameIn.value.trim() || undefined,
            brief: briefIn.value.trim(),
            groups: [...groups],
            count: Number(countIn.value) || 6,
          });
          navigate(`/p/${pid}/teach/${st.session.id}`);
        }
      },
    ],
  });
  m.el.parentElement.classList.add('teach-scope');
  return m;
}

function isTyping(t) {
  return t &&
      (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
       t.tagName === 'SELECT' || t.isContentEditable);
}

// ------------------------------------------------------------- the router

export async function render(root, params, ctx) {
  if (!params.sid) return renderList(root, params, ctx);
  return renderWorkspace(root, params, ctx);
}

// ---------------------------------------------------------- session list

async function renderList(root, params) {
  const pid = params.pid;
  const page = h('div.page.teach-scope.teach-list#teach-list');
  root.appendChild(page);
  mount(page, spinner('Loading sessions\u2026'));
  await draw();

  async function draw() {
    let sessions = [];
    try {
      sessions = (await get(`/api/projects/${pid}/teach`)).sessions || [];
    } catch (e) {
      mount(
          page,
          emptyState(
              'Could not load teaching sessions', String(e.message || e)));
      return;
    }
    mount(
        page,
        h('div.page-head', h('div.title-row', h('h1', 'Teaching sessions')),
          h('div.page-actions',
            h('a.btn.btn-ghost', {href: `/p/${pid}/teachings`}, icon('school'),
              'Teachings ledger'),
            h('button.btn.btn-primary#new-teach-session-btn', {
              type: 'button',
              onclick: () => newSessionDialog(
                  pid, {defaultName: `Session ${sessions.length + 1}`})
            },
              icon('add'), 'New teaching session'))),
        h('p.muted', {style: 'margin:0 0 18px'},
          'A slower, learning-oriented wizard: point at components, input data and how they relate; Gemini distills rules you confirm as you go, tries them on other scenarios, and every rule is tracked afterwards.'),
        sessions.length ? sessionTable(pid, sessions, draw) :
                          emptyState(
                              'No teaching sessions yet',
                              'Start one to teach this project by example.',
                              h('button.btn.btn-primary', {
                                type: 'button',
                                onclick: () => newSessionDialog(
                                    pid, {defaultName: 'Session 1'})
                              },
                                icon('school'), 'New teaching session')));
  }
}

export function sessionTable(pid, sessions, onChange) {
  return h(
      'table.ledger.ts-table',
      h('thead',
        h('tr',
          [
            'Session', 'Status', 'Cards', 'Observations', 'Confirmed',
            'Started', ''
          ].map((t) => h('th', t)))),
      h('tbody',
        sessions.map(
            (s) => h(
                'tr', {dataset: {sid: s.id}},
                h('td',
                  h('a.title', {href: `/p/${pid}/teach/${s.id}`},
                    s.name || s.id),
                  s.brief ? h('div.sub', s.brief) : null),
                h('td',
                  h('span.chip',
                    {class: s.status === 'done' ? 'v-good' : 'chip-quiet'},
                    s.status || 'teaching')),
                h('td.ph', String(s.cards ?? '\u2014')),
                h('td.ph', String(s.observations ?? '\u2014')),
                h('td.ph', String(s.confirmed ?? '\u2014')),
                h('td.small.muted',
                  `${relTime(s.created_at)}${
                      s.created_by ? ` \u00b7 ${s.created_by}` : ''}`),
                h('td', {style: 'white-space:nowrap;text-align:right'},
                  h('a.btn.btn-ghost.btn-sm', {href: `/p/${pid}/teach/${s.id}`},
                    'Open', icon('arrow_forward')),
                  h('button.icon-btn', {
                    type: 'button',
                    title:
                        'Delete session (keeps runs, templates and the ledger)',
                    'aria-label': 'Delete session',
                    onclick: async () => {
                      if (!await confirmDialog(
                              'Delete teaching session?',
                              'Runs, templates and the teachings ledger are kept.',
                              {confirmLabel: 'Delete', danger: true}))
                        return;
                      await del(`/api/teach/${s.id}`);
                      toast('Session deleted', 'good');
                      onChange && onChange();
                    }
                  },
                    icon('delete')))))));
}

// -------------------------------------------------------------- workspace

async function renderWorkspace(root, params, ctx) {
  const pid = params.pid;
  const sid = params.sid;
  const url = ctx && ctx.url;
  let state = null;
  let alive = true;
  let pollT = null;
  let cardIdx = 0;
  let screen = url && url.searchParams.get('finish') ? 'finish' : 'work';
  let tab = 'obs';
  let stageView = 'live';
  let selectedKey = null;
  let hoverKey = null;
  let iframe = null;
  let tree = null;
  let region = null;
  let lastDistill = null;
  let distillError = null;
  let distilling = false;
  let distillingCount = 0;
  let distillingObsIds = new Set();
  let stateSeq = 0;
  let assembled = null;
  let assembling = false;
  let polish = false;
  let saving = false;
  let afterOutputs = null;
  let afterFetchedFor = '';
  let imm = null;
  let mode = 'immersive';  // this session view's mode (see initialMode)
  let hoverBoxes = [];
  const composerEl = h('div.composer#teach-composer');
  const pending = {polarity: 'bad', anchors: [], about: ''};
  const linksCache = new Map();
  const busyT = new Set();
  const tokenPick = new Set();

  const page = h('div.teach-scope.teach-root#teach-root');
  const headerEl = h('div.teach-header#teach-header');
  const stripEl = h('div.teach-strip#teach-strip');
  const dataPane = h('section.teach-pane.data-pane#teach-data');
  const cardPane = h('section.teach-pane.card-pane#teach-card');
  const teachPane = h('section.teach-pane.teaching-pane#teach-teaching');
  const workEl = h('div.teach-work#teach-work', dataPane, cardPane, teachPane);
  const finishEl = h('div.finish-grid#teach-finish');
  const pageInner = h('div.teach-page', headerEl, stripEl, workEl, finishEl);
  page.appendChild(pageInner);
  root.appendChild(page);

  // Persistent composer textarea (survives redraws and polling).
  const noteEl = h('textarea#teach-note', {
    placeholder:
        'What\u2019s good or bad here, and why? Point at components or data first \u2014 anchors are saved with the note.',
    onkeydown: (e) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        addObservation();
      }
    }
  });
  const selBarEl = h('div.sel-bar#teach-selbar');
  const selActsEl = h('div.sel-actions#teach-selacts');

  window.addEventListener('message', onMessage);
  document.addEventListener('keydown', onKey);

  mount(
      workEl,
      h('div.teach-loading', spinner('Loading teaching session\u2026')));
  try {
    state = await get(`/api/teach/${sid}`);
  } catch (e) {
    mount(
        page,
        h('div.page',
          emptyState(
              'Could not load teaching session', String(e.message || e),
              h('a.btn.btn-primary', {href: `/p/${pid}/teach`},
                'All sessions'))));
    return cleanup;
  }
  workEl.replaceChildren(dataPane, cardPane, teachPane);
  if (state.session &&
      (state.session.status === 'finishing' || state.session.status === 'done'))
    screen = 'finish';
  initCard();
  drawAll();
  schedulePoll();
  mode = initialMode(state);
  if (mode === 'immersive' && screen === 'work') setImmersive(true);
  return cleanup;

  // ------------------------------------------------------------ lifecycle
  function cleanup() {
    alive = false;
    clearTimeout(pollT);
    window.removeEventListener('message', onMessage);
    document.removeEventListener('keydown', onKey);
    if (imm) imm.destroy();
    imm = null;
    document.body.classList.remove('teach-immersive');
  }

  function initCard() {
    const cards = state.cards || [];
    const first = cards.findIndex((c) => !(c.observation_count > 0));
    cardIdx = Math.max(0, first);
    defaultAbout();
  }

  function schedulePoll() {
    clearTimeout(pollT);
    if (!alive || !state || (!state.busy && !isDistilling())) return;
    const seq = stateSeq;
    pollT = setTimeout(async () => {
      if (!alive) return;
      try {
        const next = await get(`/api/teach/${sid}`, {quiet: true});
        if (alive && seq === stateSeq)
          applyState(next);
        else if (alive)
          schedulePoll();
      } catch {
        schedulePoll();
      }
    }, 2500);
  }

  function cardSig(c) {
    if (!c) return '';
    const o = c.output || {};
    return [c.output_id, o.status, o.screenshot_url, o.render_url].join('|');
  }

  function applyState(next, {force = false} = {}) {
    stateSeq++;
    const prev = cardSig(curCard());
    state = next;
    const n = (state.cards || []).length;
    if (cardIdx >= n) cardIdx = Math.max(0, n - 1);
    drawHeader();
    if (screen === 'work') {
      drawStrip();
      if (force || cardSig(curCard()) !== prev) drawCardPanes();
      drawTeachPane();
    } else {
      drawFinish();
    }
    if (imm) imm.onState();
    schedulePoll();
  }

  async function refresh() {
    const seq = stateSeq;
    const next = await get(`/api/teach/${sid}`, {quiet: true});
    if (seq === stateSeq) applyState(next);
  }

  // --------------------------------------------------------------- lookups
  function curCard() {
    return (state && state.cards || [])[cardIdx] || null;
  }
  function outReady(o) {
    return o && !BUSY.has(o.status) && (o.render_url || o.screenshot_url) &&
        o.status !== 'failed' && o.status !== 'error';
  }
  function teachById(id) {
    return (state.teachings || []).find((t) => t.id === id) || null;
  }
  function liveTeachings() {
    return (state.teachings || [])
        .filter((t) => t.status !== 'dismissed' && t.status !== 'merged');
  }
  function openObsCount() {
    return (state.observations || []).filter((o) => o.status === 'open').length;
  }
  function isDistilling() {
    return Boolean(distilling || (state && state.distilling));
  }
  function canDistill() {
    return !isDistilling() && openObsCount() > 0;
  }
  function obsIndex(id) {
    const i = (state.observations || []).findIndex((o) => o.id === id);
    return i < 0 ? '?' : String(i + 1);
  }
  function cardIndexForOutput(oid) {
    return (state.cards || []).findIndex((c) => c.output_id === oid);
  }
  function defaultAbout() {
    const c = curCard();
    pending.about =
        c && c.origin === 'probe' && c.teaching_id ? c.teaching_id : '';
  }
  async function loadLinks(oid) {
    if (!oid) return null;
    if (!linksCache.has(oid)) {
      linksCache.set(
          oid, get(`/api/outputs/${oid}/links`, {quiet: true}).catch((e) => {
            linksCache.delete(oid);
            throw e;
          }));
    }
    return linksCache.get(oid);
  }
  function linksNow() {
    const c = curCard();
    const p = c && linksCache.get(c.output_id);
    return p && p.value ? p.value : null;
  }
  async function linksFor(oid) {
    const p = loadLinks(oid);
    const v = await p;
    p.value = v;
    return v;
  }
  function boxesFor(key, links) {
    const L = links || linksNow();
    if (!L) return [];
    return (L.components || [])
        .filter((c) => c.box && keyMatches(c.key, key))
        .map((c) => [c.box.x, c.box.y, c.box.w, c.box.h]);
  }
  function compFor(key, links) {
    const L = links || linksNow();
    if (!L || !key) return null;
    return (L.components || []).find((c) => c.key === key) ||
        (L.components || []).find((c) => keyMatches(c.key, key)) || null;
  }
  function leafFor(path, links) {
    const L = links || linksNow();
    return L ? (L.leaves || []).find((l) => l.path === path) : null;
  }
  /** Component keys that represent the given anchors (for card highlights). */
  function anchorKeys(anchors, links) {
    const L = links || linksNow();
    const keys = [];
    for (const a of anchors || []) {
      if ((a.kind === 'component' || a.kind === 'relation') && a.key)
        keys.push(a.key);
      if (a.kind === 'data' && L) {
        const leaf = leafFor(a.path, L);
        if (leaf)
          keys.push(...(leaf.components || []));
        else if (a.path.includes('*')) {
          const re = new RegExp(`^${
              a.path.split('*')
                  .map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
                  .join('[^/]+')}$`);
          (L.leaves || [])
              .filter((l) => re.test(l.path))
              .forEach((l) => keys.push(...(l.components || [])));
        }
      }
    }
    return [...new Set(keys)];
  }
  function anchorPaths(anchors) {
    return (anchors || []).filter((a) => a.path).map((a) => a.path);
  }
  async function cropFor(card, anchors) {
    const o = card && card.output;
    if (!o || !o.screenshot_url) return null;
    let L = null;
    try {
      L = await linksFor(card.output_id);
    } catch {
    }
    const boxes = [];
    for (const a of anchors || []) {
      if (a.kind === 'region' && a.box) boxes.push(a.box);
    }
    for (const k of anchorKeys(anchors, L)) boxes.push(...boxesFor(k, L));
    const u = unionBox(boxes);
    if (!u) return null;
    return cropScreenshot(
        o.screenshot_url, u,
        {cardWidth: (o.render_meta && o.render_meta.width) || CARD_W});
  }
  function liveSrc(o) {
    if (!o || !o.render_url) return null;
    return `${o.render_url}${o.render_url.includes('?') ? '&' : '?'}inspect=1`;
  }

  // --------------------------------------------------------- iframe bridge
  function postToFrame(msg) {
    if (iframe && iframe.contentWindow)
      iframe.contentWindow.postMessage(msg, '*');
  }
  function pushSelection() {
    const ids = anchorKeys(pending.anchors.filter((a) => a.kind !== 'data'));
    if (selectedKey && !ids.includes(selectedKey)) ids.push(selectedKey);
    postToFrame({type: 'a2ui-highlight', ids, select: true, scroll: false});
    drawOverlays();
  }
  function onMessage(e) {
    const d = e.data;
    if (!d || typeof d !== 'object' || !iframe ||
        e.source !== iframe.contentWindow)
      return;
    if (d.type === 'a2ui-ready') {
      const hgt = d.height || (d.ready && d.ready.height);
      if (hgt) iframe.style.height = `${Math.ceil(hgt)}px`;
      pushSelection();
    } else if (d.type === 'a2ui-hover') {
      hoverKey = d.id ? `${d.id}|${d.scope || ''}` : null;
      if (tree) tree.highlightComponent(hoverKey);
    } else if (d.type === 'a2ui-click') {
      if (!d.id) {
        selectedKey = null;
      } else {
        selectedKey = `${d.id}|${d.scope || ''}`;
        toggleComponentAnchor(selectedKey);
      }
      drawSelection();
      drawComposer();
      pushSelection();
    }
  }

  // --------------------------------------------------------------- anchors
  function toggleComponentAnchor(key) {
    const i = pending.anchors.findIndex(
        (a) => a.kind === 'component' && a.key === key);
    if (i >= 0) {
      pending.anchors.splice(i, 1);
      return false;
    }
    const c = compFor(key);
    const {id, scope} = splitKey(key);
    pending.anchors.push({
      kind: 'component',
      key,
      id,
      scope: scope || '',
      component: c ? c.type : undefined,
      label: c ? compLabel(c) : undefined
    });
    return true;
  }
  function toggleDataAnchor(leaf) {
    const i = pending.anchors.findIndex(
        (a) => a.kind === 'data' && a.path === leaf.path);
    if (i >= 0)
      pending.anchors.splice(i, 1);
    else
      pending.anchors.push({
        kind: 'data',
        path: leaf.path,
        value_preview: leaf.value_preview,
        represented: leaf.represented || 'none'
      });
  }
  function removeAnchor(a) {
    const i = pending.anchors.indexOf(a);
    if (i >= 0) pending.anchors.splice(i, 1);
    afterAnchorsChanged();
  }
  function afterAnchorsChanged() {
    drawComposer();
    drawSelection();
    if (tree) tree.setSelected(anchorPaths(pending.anchors));
    pushSelection();
  }
  function relateToData() {
    if (!selectedKey) return;
    const c = compFor(selectedKey);
    const dataAs = pending.anchors.filter((a) => a.kind === 'data');
    let path = dataAs.length ? dataAs[dataAs.length - 1].path : null;
    if (!path && c && (c.reads || []).length) path = c.reads[0];
    if (!path) {
      toast(
          'Click the data field this component should relate to first', 'warn');
      return;
    }
    pending.anchors = pending.anchors.filter(
        (a) => !(a.kind === 'data' && a.path === path) &&
            !(a.kind === 'component' && a.key === selectedKey));
    pending.anchors.push({
      kind: 'relation',
      path,
      key: selectedKey,
      component: c ? c.type : undefined,
      label: c ? compLabel(c) : undefined
    });
    afterAnchorsChanged();
  }

  // ------------------------------------------------------------ navigation
  function selectCard(i) {
    const n = (state.cards || []).length;
    if (!n) return;
    cardIdx = (i + n) % n;
    pending.anchors = [];
    selectedKey = null;
    hoverKey = null;
    defaultAbout();
    drawStrip();
    drawCardPanes();
    drawTeachPane();
    if (imm) imm.onCard();
  }
  function setScreen(s) {
    screen = s;
    const u = new URL(location.href);
    if (s === 'finish')
      u.searchParams.set('finish', '1');
    else
      u.searchParams.delete('finish');
    history.replaceState(null, '', u.pathname + u.search);
    if (s === 'finish' && imm) setImmersive(false, {remember: false});
    drawAll();
    if (s === 'work' && !imm && mode === 'immersive') setImmersive(true);
  }
  // `remember`: record the choice as this session view's mode (URL ?mode=);
  // nothing is persisted across sessions or projects.
  function setImmersive(on, {remember = true} = {}) {
    if (remember) {
      mode = on ? 'immersive' : 'expert';
      const u = new URL(location.href);
      u.searchParams.set('mode', mode);
      history.replaceState(null, '', u.pathname + u.search);
    }
    if (on && !imm && screen === 'work') {
      document.body.classList.add('teach-immersive');
      imm = mountImmersive(page, immApi());
    } else if (!on && imm) {
      imm.destroy();
      imm = null;
      document.body.classList.remove('teach-immersive');
      drawCardPanes();
      drawTeachPane();
    }
    drawHeader();
  }
  function onKey(e) {
    if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
    if (document.querySelector('.overlay')) return;
    if ((e.key === 'i' || e.key === 'I') && screen === 'work') {
      e.preventDefault();
      setImmersive(!imm);
    } else if (
        !imm && screen === 'work' && (e.key === 'd' || e.key === 'D') &&
        !document.querySelector('.drawer.open')) {
      e.preventDefault();
      if (canDistill()) runDistill();
    } else if (
        !imm && screen === 'work' &&
        (e.key === 'ArrowRight' || e.key === 'ArrowLeft') &&
        !document.querySelector('.drawer.open')) {
      e.preventDefault();
      selectCard(cardIdx + (e.key === 'ArrowRight' ? 1 : -1));
    }
  }

  // --------------------------------------------------------------- drawing
  function drawAll() {
    drawHeader();
    workEl.hidden = screen !== 'work';
    stripEl.hidden = screen !== 'work';
    finishEl.hidden = screen !== 'finish';
    if (screen === 'work') {
      drawStrip();
      drawCardPanes();
      drawTeachPane();
    } else {
      drawFinish();
    }
  }

  function drawHeader() {
    const s = state.session || {};
    const tl = liveTeachings();
    const rules =
        tl.filter((t) => t.kind === 'rule' && t.status === 'confirmed').length;
    const tpls =
        tl.filter((t) => t.kind === 'pattern' && t.status !== 'retired').length;
    const cands = tl.filter((t) => t.status === 'candidate').length;
    const open = openObsCount();
    const prog = [
      [(state.cards || []).length, 'cards'],
      [(state.observations || []).length, 'observations'],
      [rules, rules === 1 ? 'rule confirmed' : 'rules confirmed'],
      [tpls, tpls === 1 ? 'template' : 'templates'],
      [cands, cands === 1 ? 'candidate' : 'candidates'],
    ];
    const progEl =
        h('span.teach-progress',
          prog.map(
              ([n, l], i) =>
                  [i ? h('span.dot', '\u00b7') : null, h('b', String(n)),
                   ` ${l} `]));
    mount(
        headerEl, h('span.wiz-badge', icon('school'), 'Teaching session'),
        h('span.teach-title',
          `${s.name || 'Session'}${
              screen === 'finish' ?
                  ' \u00b7 Finish' :
                  ''} \u00b7 ${(state.project && state.project.name) || ''}`),
        progEl,
        (state.busy || isDistilling()) ?
            h('span.chip.chip-quiet.teach-busy', h('span.spinner'),
              isDistilling() ? ' distilling\u2026' : ' working') :
            null,
        h('span.spacer'),
        screen === 'work' ?
            [
              h('span.chip.chip-quiet#teach-expert-chip', {
                title:
                    'Expert mode: input data, card and observations side by side'
              },
                icon('tune'), 'Expert mode'),
              h('button.btn.btn-ghost.btn-sm#teach-immersive-btn', {
                type: 'button',
                title:
                    'Back to immersive mode (I): just the card, pointing and voice',
                onclick: () => setImmersive(!imm)
              },
                icon('open_in_full'), 'Immersive'),
              h('button.btn.btn-ghost.btn-sm#teach-add-scenarios',
                {type: 'button', onclick: () => addScenariosDialog()},
                icon('add'), 'Add scenarios\u2026'),
              h('button.btn.btn-ghost.btn-sm#teach-distill', {
                type: 'button',
                class: isDistilling() ? 'is-working' : '',
                disabled: !canDistill(),
                'aria-busy': String(isDistilling()),
                title: isDistilling() ?
                    'Distillation in progress\u2026' :
                    open ?
                    `Distill ${open} open observation${
                        open === 1 ? '' : 's'} into candidate rules` :
                    'All observations distilled \u2014 add a new observation to distill again',
                onclick: (e) => runDistill(e.currentTarget)
              },
                isDistilling() ?
                    [
                      spinner('Distilling\u2026'),
                      (distillingCount || open) ?
                          h('span.badge-count',
                            String(distillingCount || open)) :
                          null,
                    ] :
                    [
                      icon('auto_awesome'),
                      'Distill',
                      open ? h('span.badge-count', String(open)) : null,
                    ]),
              h('button.btn.btn-primary.btn-sm#teach-finish-btn',
                {type: 'button', onclick: () => setScreen('finish')}, 'Finish ',
                icon('arrow_forward')),
            ] :
            h('button.btn.btn-ghost.btn-sm#teach-back',
              {type: 'button', onclick: () => setScreen('work')},
              icon('arrow_back'), 'Back to teaching'));
  }

  function drawStrip() {
    const cards = state.cards || [];
    mount(
        stripEl, cards.map((c, i) => {
          const o = c.output || {};
          const busy = BUSY.has(o.status);
          const n = c.observation_count || 0;
          const t = c.teaching_id ? teachById(c.teaching_id) : null;
          return h(
              'button.wiz-strip-card', {
                type: 'button',
                id: `teach-card-${i}`,
                class: i === cardIdx ? 'is-active' : '',
                onclick: () => selectCard(i)
              },
              c.origin === 'added' ? h('span.origin-chip.added', 'Added') :
                                     null,
              c.origin === 'probe' ?
                  h('span.origin-chip',
                    `Probe${t ? ` \u00b7 ${t.label}` : ''}`) :
                  null,
              h('div.wiz-strip-thumb',
                busy || !o.thumb_url ?
                    (busy ? h('span.spinner') : icon('image_not_supported')) :
                    h('img', {src: o.thumb_url, alt: '', loading: 'lazy'})),
              h('div.wiz-strip-info',
                h('div.wiz-strip-title', h('span.n', String(i + 1)),
                  (c.sample && c.sample.title) || o.sample_title || 'Card'),
                busy ?
                    h('div.obs-count', icon('hourglass_top'),
                      c.origin === 'probe' ? 'Rendering with your rules\u2026' :
                                             'Rendering\u2026') :
                    h('div.obs-count', {class: n ? 'has' : ''},
                      icon(n ? 'chat_bubble' : 'radio_button_unchecked'),
                      n ? `${n} observation${n === 1 ? '' : 's'}` :
                          'Not yet')));
        }),
        h('button.add-card#teach-add-card',
          {type: 'button', onclick: () => addScenariosDialog()}, icon('add'),
          'Add scenarios\u2026'));
    const act = stripEl.querySelector('.is-active');
    if (act) act.scrollIntoView({block: 'nearest', inline: 'nearest'});
  }

  function drawCardPanes() {
    drawDataPane();
    drawCardPane();
  }

  function drawDataPane() {
    const c = curCard();
    tree = null;
    const head =
        h('div.pane-head', h('h3', 'Input data'),
          c && c.sample && c.sample.genre ?
              h('span.chip.chip-quiet', c.sample.genre) :
              null);
    if (!c) {
      mount(dataPane, head, h('div.pane-body', h('p.muted', 'No cards yet.')));
      return;
    }
    if (!outReady(c.output)) {
      mount(
          dataPane, head,
          h('div.pane-body', sampleSummary(c.sample),
            h('p.muted.small', 'Links appear once the card is rendered.')));
      return;
    }
    const body =
        h('div.pane-body', spinner('Linking data to components\u2026'));
    mount(dataPane, head, body);
    const oid = c.output_id;
    linksFor(oid)
        .then((links) => {
          if (!alive || !curCard() || curCard().output_id !== oid) return;
          tree = dataTree({
            links,
            sample: c.sample,
            onHover: (leaf, comps) => {
              postToFrame({
                type: 'a2ui-highlight',
                ids: leaf ? comps : [],
                select: false,
                scroll: false
              });
              hoverBoxes = leaf ? comps : [];
              drawOverlays();
            },
            onClick: (leaf, comps) => {
              toggleDataAnchor(leaf);
              afterAnchorsChanged();
              if (comps.length)
                postToFrame({
                  type: 'a2ui-highlight',
                  ids: comps,
                  select: false,
                  scroll: true
                });
            },
            onUnrepresented: (path, choice) => quickUnrepresented(path, choice),
          });
          tree.setSelected(anchorPaths(pending.anchors));
          mount(body, tree);
          drawSelection();
          drawOverlays();
        })
        .catch(() => {
          mount(
              body, sampleSummary(c.sample),
              h('p.muted.small', icon('link_off'),
                ' Data links are not available for this card.'));
        });
  }
  function sampleSummary(sm) {
    if (!sm) return null;
    return [
      sm.title ? h('div.dt-title', sm.title) : null,
      sm.intent ? h('p.data-intent', h('b', 'Intent: '), sm.intent) : null
    ];
  }

  function drawCardPane() {
    const c = curCard();
    iframe = null;
    if (region) {
      region.destroy();
      region = null;
    }
    if (!c) {
      mount(
          cardPane, h('div.pane-head', h('h3', 'Card')),
          h('div.stage', h('p.muted', 'No cards yet.')));
      return;
    }
    const o = c.output || {};
    const t = c.teaching_id ? teachById(c.teaching_id) : null;
    const originText = c.origin === 'probe' ?
        `Probe${t ? ` \u00b7 ${t.label}` : ''}` :
        c.origin === 'added' ? 'Added scenario' :
                               `Baseline \u00b7 Rev ${o.revision_number ?? 0}`;
    const L = linksNow();
    const hgt =
        (L && L.height) || (o.render_meta && o.render_meta.height) || 900;
    const seg =
        h('div.segmented',
          [
            ['live', 'touch_app', 'Live \u00b7 click to point'],
            ['shot', 'image', 'Screenshot'],
            ['region', 'crop_free', 'Mark region']
          ]
              .map(
                  ([v, ic, l]) =>
                      h('button.seg-btn', {
                        type: 'button',
                        id: `teach-view-${v}`,
                        class: stageView === v ? 'on' : '',
                        disabled: v !== 'live' && !o.screenshot_url,
                        onclick: () => {
                          stageView = v;
                          drawCardPane();
                        }
                      },
                        icon(ic), l)));
    const head =
        h('div.pane-head.card-pane-head',
          h('div.row',
            h('div.card-meta',
              h('span.wiz-card-counter',
                `Card ${cardIdx + 1} of ${(state.cards || []).length}`),
              h('span.chip.chip-quiet', originText),
              h('span.chip.chip-quiet', `${o.viewport || 'phone'} \u00b7 web`)),
            h('span.muted.small',
              stageView === 'region' ?
                  'Drag on the screenshot to mark a region' :
                  'Hover the data tree to see where each field landed')),
          h('div.row', seg,
            h('span.muted.small.mono', `${CARD_W} \u00d7 ${hgt} css px`)));
    let stageInner;
    if (BUSY.has(o.status)) {
      stageInner =
          h('div.phone-view.phone-wait',
            spinner(
                c.origin === 'probe' ? 'Rendering with your rules\u2026' :
                                       'Rendering\u2026'));
    } else if (!outReady(o)) {
      stageInner =
          h('div.phone-view.phone-wait',
            h('p.c-bad.small', icon('error'),
              ` ${o.error || `Card ${o.status || 'failed'}`}`));
    } else if (stageView === 'live' && liveSrc(o)) {
      iframe = h('iframe.live-frame#teach-live', {
        src: liveSrc(o),
        title: 'Live card',
        style: {width: `${CARD_W}px`, height: `${hgt}px`}
      });
      stageInner = h('div.phone-view.phone-live', iframe);
    } else {
      const shotWrap = h('div.shot-wrap', h('img', {
                           src: o.screenshot_url,
                           alt: 'Card screenshot',
                           draggable: 'false'
                         }));
      region = regionSelect(shotWrap, {
        cardWidth: (o.render_meta && o.render_meta.width) || CARD_W,
        enabled: stageView === 'region',
        onRegion: (box) => {
          pending.anchors.push({kind: 'region', box});
          afterAnchorsChanged();
        }
      });
      if (stageView === 'shot') {
        shotWrap.addEventListener('click', (e) => {
          const r = shotWrap.getBoundingClientRect();
          const s = r.width / CARD_W;
          const x = (e.clientX - r.left) / s, y = (e.clientY - r.top) / s;
          const hit =
              ((linksNow() || {}).components || [])
                  .filter(
                      (cc) => cc.box && x >= cc.box.x &&
                          x <= cc.box.x + cc.box.w && y >= cc.box.y &&
                          y <= cc.box.y + cc.box.h)
                  .sort((a, b) => a.box.w * a.box.h - b.box.w * b.box.h)[0];
          selectedKey = hit ? hit.key : null;
          if (hit) toggleComponentAnchor(hit.key);
          drawSelection();
          drawComposer();
          pushSelection();
        });
      }
      stageInner =
          h('div.phone-view.phone-shot',
            {class: stageView === 'region' ? 'marking' : ''}, shotWrap);
    }
    mount(
        cardPane, head,
        h('div.stage', h('div.phone-wrap', stageInner), selBarEl, selActsEl));
    drawSelection();
    drawOverlays();
  }

  function drawOverlays() {
    if (!region) return;
    const boxes = [];
    for (const a of pending.anchors) {
      if (a.kind === 'region') boxes.push({box: a.box, kind: 'region'});
    }
    for (const k of anchorKeys(pending.anchors))
      boxesFor(k).forEach((b) => boxes.push({box: b, kind: 'anch'}));
    if (selectedKey)
      boxesFor(selectedKey).forEach((b) => boxes.push({box: b, kind: 'sel'}));
    for (const k of hoverBoxes)
      boxesFor(k).forEach((b) => boxes.push({box: b, kind: 'hov'}));
    region.setBoxes(boxes);
  }

  function drawSelection() {
    const c = curCard();
    if (!c || !selectedKey) {
      mount(
          selBarEl, icon('my_location'),
          h('span.muted',
            'Click a component in the card to point at it \u00b7 click a data field to anchor it'));
      mount(
          selActsEl,
          c && outReady(c.output) ?
              h('button.btn.btn-ghost.btn-sm#teach-template-whole',
                {type: 'button', onclick: () => templateFrom({whole: true})},
                icon('dashboard_customize'),
                'Make a template from the whole card') :
              null);
      return;
    }
    const comp = compFor(selectedKey);
    const anchored = pending.anchors.some(
        (a) => a.kind === 'component' && a.key === selectedKey);
    const reads = comp ? [...(comp.reads || []), ...(comp.literal || [])] : [];
    mount(
        selBarEl, icon('my_location'), h('span', 'Selected'),
        h('code', comp ? compLabel(comp) : selectedKey),
        splitKey(selectedKey).scope ?
            h('span.muted.small.mono', splitKey(selectedKey).scope) :
            null,
        h('span.reads',
          reads.length ?
              [
                'reads ',
                reads.slice(0, 3).map((p, i) => [i ? ', ' : '', h('code', p)]),
                reads.length > 3 ? ` +${reads.length - 3}` : ''
              ] :
              'reads no input data',
          comp && comp.box ? ` \u00b7 y ${comp.box.y}` : ''));
    mount(
        selActsEl,
        h('button.btn.btn-ghost.btn-sm#teach-anchor-toggle', {
          type: 'button',
          onclick: () => {
            toggleComponentAnchor(selectedKey);
            afterAnchorsChanged();
          }
        },
          icon(anchored ? 'remove' : 'add_comment'),
          anchored ? 'Remove anchor' : 'Add as anchor'),
        h('button.btn.btn-ghost.btn-sm#teach-relate',
          {type: 'button', onclick: () => relateToData()}, icon('link'),
          'Relate to data'),
        h('button.btn.btn-ghost.btn-sm#teach-template-sel',
          {type: 'button', onclick: () => templateFrom({key: selectedKey})},
          icon('dashboard_customize'), 'Make a template from this'));
  }

  async function templateFrom(selection, {card} = {}) {
    const c = card || curCard();
    if (!c || !c.output) return;
    let crop = null;
    if (selection.key) {
      crop = await cropFor(c, [{kind: 'component', key: selection.key}]);
    } else if (selection.region) {
      crop = await cropFor(c, [{kind: 'region', box: selection.region}]);
    }
    openPatternCapture({
      pid,
      sessionId: sid,
      output: c.output,
      selection,
      cropDataUrl: crop,
      onSaved: () => {
        tab = 'learned';
        refresh();
      }
    });
  }

  // ---------------------------------------------------------- teaching pane
  function drawTeachPane() {
    if (imm) return;
    const c = curCard();
    const cardObs = c ?
        (state.observations || []).filter((o) => o.output_id === c.output_id) :
        [];
    const lt = liveTeachings();
    const tabs =
        h('div.tabs',
          h('button.tab#teach-tab-obs', {
            type: 'button',
            class: tab === 'obs' ? 'on' : '',
            onclick: () => {
              tab = 'obs';
              drawTeachPane();
            }
          },
            icon('chat_bubble'), 'Observations ',
            h('span.cnt', String(cardObs.length))),
          h('button.tab#teach-tab-learned', {
            type: 'button',
            class: tab === 'learned' ? 'on' : '',
            onclick: () => {
              tab = 'learned';
              drawTeachPane();
            }
          },
            isDistilling() ? h('span.spinner') : icon('school'),
            'Learned so far ', h('span.cnt', String(lt.length))));
    const body = h('div.pane-body');
    mount(
        teachPane,
        h('div.pane-head', tabs,
          h('span.muted.small', c ? `Card ${cardIdx + 1}` : '')),
        body);
    if (tab === 'obs') {
      body.append(composerEl);
      drawComposer();
      if (isDistilling()) {
        const n = distillingCount || openObsCount();
        body.append(h(
            'div.distill-note.is-working#teach-obs-distilling',
            {role: 'status', 'aria-live': 'polite'}, spinner(),
            h('div',
              h('b',
                `Distilling ${
                    n ? `${n} observation${n === 1 ? '' : 's'}` :
                        'observations'}\u2026`),
              h('p.muted.small',
                'Gemini is turning your observations into candidate rules. You can keep adding new observations while this runs.'))));
      }
      body.append(h(
          'div.obs-list#teach-obs-list',
          cardObs.length ?
              cardObs.slice().reverse().map(obsItem) :
              h('p.muted.small',
                'No observations on this card yet. Point at something and say what\u2019s good or bad about it.')));
    } else {
      renderLearnedInto(body);
    }
  }

  function drawComposer() {
    if (imm) return;
    const confirmed =
        (state.teachings || [])
            .filter(
                (t) => t.status === 'confirmed' ||
                    (t.kind === 'pattern' && t.status !== 'dismissed' &&
                     t.status !== 'retired'));
    const about =
        h('select#teach-about', {
          onchange: (e) => {
            pending.about = e.target.value;
          }
        },
          h('option', {value: ''}, '\u2014 (new thing)'),
          confirmed.map(
              (t) => h(
                  'option', {value: t.id, selected: pending.about === t.id},
                  `${t.label} ${(t.title || t.body_md || '').slice(0, 48)}`)));
    const polBtn = (v, ic, l) =>
        h('button.seg-btn', {
          type: 'button',
          id: `teach-pol-${v}`,
          class: [v, pending.polarity === v ? 'on' : ''],
          'aria-pressed': String(pending.polarity === v),
          onclick: () => {
            pending.polarity = v;
            drawComposer();
          }
        },
          icon(ic), l);
    const L = linksNow();
    mount(
        composerEl,
        h('div.row1',
          h('div.pol', polBtn('good', 'thumb_up', 'Good'),
            polBtn('bad', 'thumb_down', 'Bad'),
            polBtn('note', 'edit_note', 'Note')),
          h('label.about', 'About ', about)),
        h('div.anchors#teach-pending-anchors',
          pending.anchors.length ?
              pending.anchors.map(
                  (a) => anchorChip(a, {links: L, onRemove: removeAnchor})) :
              h('span.muted.small', 'No anchors = about the whole card'),
          h('button.anc-add', {
            type: 'button',
            onclick: () => {
              stageView = 'region';
              drawCardPane();
            }
          },
            '+ region'),
          pending.anchors.length ? h('button.anc-add', {
            type: 'button',
            onclick: () => {
              pending.anchors = [];
              selectedKey = null;
              afterAnchorsChanged();
            }
          },
                                     'clear') :
                                   null),
        h('div.quick',
          QUICK_CHIPS.map(
              ([ic, text, kind]) =>
                  h('button.wiz-quick-chip', {
                    type: 'button',
                    class: kind === 'good' ? 'chip-good-obs' : 'chip-fix',
                    onclick: () => insertText(text)
                  },
                    icon(ic), text))),
        noteEl,
        h('div.foot',
          h('span.hint',
            'Anchors and text are saved together; crops are made from the anchors.'),
          h('button.btn.btn-ghost.btn-sm', {
            type: 'button',
            onclick: () => {
              noteEl.value = '';
              pending.anchors = [];
              selectedKey = null;
              afterAnchorsChanged();
            }
          },
            'Cancel'),
          h('button.btn.btn-primary.btn-sm#teach-add-obs',
            {type: 'button', onclick: (e) => addObservation(e.currentTarget)},
            icon('add'), 'Add observation')));
  }

  function insertText(text) {
    const s = noteEl.selectionStart ?? noteEl.value.length;
    const e = noteEl.selectionEnd ?? noteEl.value.length;
    const before = noteEl.value.slice(0, s);
    const glue = before && !/\s$/.test(before) ? ' ' : '';
    noteEl.setRangeText(
        `${glue}${text}${/ [.!?] $ /.test(text) ? '' : '.'} `, s, e, 'end');
    noteEl.focus();
  }

  async function addObservation(btn) {
    const c = curCard();
    if (!c) return;
    const text = noteEl.value.trim();
    if (!text) {
      toast('Write what\u2019s good or bad first', 'warn');
      noteEl.focus();
      return;
    }
    if (btn) btn.disabled = true;
    try {
      const crop =
          await cropFor(c, pending.anchors.length ? pending.anchors : []);
      const res = await post(`/api/teach/${sid}/observations`, {
        output_id: c.output_id,
        polarity: pending.polarity,
        text,
        anchors: pending.anchors,
        about_teaching_id: pending.about || undefined,
        crop_data_url: crop || undefined,
        source: 'composer',
      });
      noteEl.value = '';
      pending.anchors = [];
      selectedKey = null;
      applyState(res.state);
      if (tree) tree.setSelected([]);
      pushSelection();
      drawSelection();
    } catch {
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function quickUnrepresented(path, choice) {
    const c = curCard();
    if (!c) return;
    const leaf =
        leafFor(path) || {path, value_preview: '', represented: 'none'};
    const res = await post(`/api/teach/${sid}/observations`, {
      output_id: c.output_id,
      polarity: choice === 'show' ? 'bad' : 'good',
      text: choice === 'show' ? `${path} is not shown but should be.` :
                                `Fine to omit ${path} here.`,
      anchors: [{
        kind: 'data',
        path,
        value_preview: leaf.value_preview,
        represented: leaf.represented || 'none'
      }],
      source: 'composer',
    });
    toast(
        choice === 'show' ? 'Noted: should be shown' : 'Noted: fine to omit',
        'good');
    applyState(res.state);
  }

  function obsStateEl(o) {
    if (o.status === 'distilled') {
      const ts = (o.teaching_ids || []).map(teachById).filter(Boolean);
      const oc = o.outcome || {};
      const bits = ts.map(
          (t) => `${t.label}${t.status === 'candidate' ? ' (candidate)' : ''}`);
      (oc.tokens || []).forEach((k) => bits.push(`token ${k.token}`));
      if (oc.out_of_scope)
        bits.push(
            `not a layout rule (${oc.out_of_scope.suggested_home || 'note'})`);
      return h(
          'span.state.dist',
          {title: oc.out_of_scope ? oc.out_of_scope.why : ''},
          icon('auto_awesome'),
          bits.length ? `distilled \u2192 ${bits.join(', ')}` :
                        'distilled \u2192 nothing new');
    }
    if (o.status === 'dismissed')
      return h('span.state', icon('block'), 'dismissed');
    if (isDistilling() &&
        (!distillingObsIds.size || distillingObsIds.has(o.id)))
      return h('span.state.dist', h('span.spinner'), 'distilling\u2026');
    return h('span.state', icon('fiber_new'), 'new \u00b7 not distilled');
  }

  function obsItem(o) {
    const about = o.about_teaching_id ? teachById(o.about_teaching_id) : null;
    const L = linksNow();
    return h(
        'div.obs', {
          dataset: {obid: o.id},
          onmouseenter: (e) => {
            e.currentTarget.classList.add('hl');
            postToFrame({
              type: 'a2ui-highlight',
              ids: anchorKeys(o.anchors),
              select: false,
              scroll: false
            });
            if (tree) tree.highlightPaths(anchorPaths(o.anchors));
            hoverBoxes = anchorKeys(o.anchors);
            drawOverlays();
          },
          onmouseleave: (e) => {
            e.currentTarget.classList.remove('hl');
            postToFrame({
              type: 'a2ui-highlight',
              ids: [],
              select: false,
              scroll: false
            });
            if (tree) tree.highlightPaths([]);
            hoverBoxes = [];
            drawOverlays();
          }
        },
        h('div',
          h('div.top',
            h('span.pol-pill', {class: o.polarity},
              (o.polarity || 'note').replace(/^./, (x) => x.toUpperCase())),
            h('span.muted', relTime(o.created_at)),
            o.source && o.source !== 'composer' ?
                h('span.muted',
                  icon(o.source === 'voice' ? 'mic' : 'keyboard')) :
                null,
            about ? h('span.muted', 'about ', ridChip(about)) : null,
            obsStateEl(o)),
          h('div.txt', o.text),
          (o.anchors || []).length ?
              h('div.anchors',
                o.anchors.map((a) => anchorChip(a, {links: L}))) :
              null),
        h('div.crop', {
          style: o.crop_url ? {backgroundImage: `url("${o.crop_url}")`} : null
        },
          o.crop_url ? null : icon('crop_landscape')),
        h('div.acts',
          h('button.icon-btn', {
            type: 'button',
            title: 'Edit',
            'aria-label': 'Edit observation',
            onclick: () => editObservation(o)
          },
            icon('edit')),
          h('button.icon-btn', {
            type: 'button',
            title: 'Delete',
            'aria-label': 'Delete observation',
            onclick: async () => {
              if (!await confirmDialog(
                      'Delete observation?', o.text.slice(0, 140),
                      {confirmLabel: 'Delete', danger: true}))
                return;
              await del(`/api/teach/observations/${o.id}`);
              refresh();
            }
          },
            icon('delete'))));
  }

  function editObservation(o) {
    let pol = o.polarity;
    const ta = h('textarea.input', {rows: 4, value: o.text});
    const seg = h('div.pol');
    const drawSeg = () => mount(
        seg,
        ['good', 'bad', 'note'].map(
            (v) =>
                h('button.seg-btn', {
                  type: 'button',
                  class: [v, pol === v ? 'on' : ''],
                  onclick: () => {
                    pol = v;
                    drawSeg();
                  }
                },
                  v.replace(/^./, (x) => x.toUpperCase()))));
    drawSeg();
    const m = modal({
      title: 'Edit observation',
      body: [seg, h('div', {style: 'height:10px'}), ta],
      actions: [
        {label: 'Cancel'}, {
          label: 'Save',
          kind: 'primary',
          onClick: async () => {
            await patch(
                `/api/teach/observations/${o.id}`,
                {polarity: pol, text: ta.value.trim()});
            await refresh();
          }
        }
      ]
    });
    m.el.parentElement.classList.add('teach-scope');
  }

  // ------------------------------------------------------------- learned
  function sortedTeachings() {
    const rank = (t) => t.status === 'confirmed' && t.kind === 'rule' ? 0 :
        t.kind === 'pattern'                                          ? 1 :
        t.status === 'candidate'                                      ? 2 :
                                                                        3;
    const num = (t) => Number(String(t.label || '').replace(/\D/g, '')) || 999;
    return liveTeachings().slice().sort(
        (a, b) => rank(a) - rank(b) || num(a) - num(b));
  }

  function renderLearnedInto(body) {
    const ts = sortedTeachings();
    const hidden = (state.teachings || []).length - ts.length;
    const rules =
        ts.filter((t) => t.kind === 'rule' && t.status === 'confirmed').length;
    const tpls = ts.filter((t) => t.kind === 'pattern').length;
    const cands = ts.filter((t) => t.status === 'candidate').length;
    body.append(h(
        'div.learned-head', {style: 'margin-top:0;border-top:0;padding-top:0'},
        h('h4', 'Learned so far'), h('span.muted.small', 'confirmed first'),
        h('span.chip.chip-quiet',
          `${rules} rule${rules === 1 ? '' : 's'} \u00b7 ${tpls} template${
              tpls === 1 ?
                  '' :
                  's'} \u00b7 ${cands} candidate${cands === 1 ? '' : 's'}`)));
    const openObs =
        (state.observations || []).filter((o) => o.status === 'open').length;
    if (isDistilling()) {
      const n = distillingCount || openObs;
      body.append(h(
          'div.distill-note.is-working#teach-distill-working',
          {role: 'status', 'aria-live': 'polite'}, spinner(),
          h('div',
            h('b',
              `Distilling ${
                  n ? `${n} observation${n === 1 ? '' : 's'}` :
                      'observations'}\u2026`),
            h('p.muted.small',
              'Gemini is turning your observations into candidate rules and token suggestions.'))));
    }
    if (lastDistill && !isDistilling()) {
      body.append(h(
          'div.distill-note', icon('auto_awesome'),
          h('div',
            h('b',
              `Distilled: ${(lastDistill.created || []).length} new \u00b7 ${
                  (lastDistill.updated || []).length} updated`),
            lastDistill.reply_md ? h('p', lastDistill.reply_md) : null,
            (lastDistill.theme_changes || []).length ?
                h('p.muted.small',
                  `${
                      lastDistill.theme_changes
                          .length} styling note(s) became token suggestions (see Finish).`) :
                null,
            (lastDistill.out_of_scope || []).length ?
                h('p.muted.small',
                  `Out of scope: ${
                      (lastDistill.out_of_scope || [])
                          .map(
                              (x) => typeof x === 'string' ?
                                  x :
                                  (x.why || x.text || x.reason || ''))
                          .join(' \u00b7 ')}`) :
                null),
          h('button.icon-btn', {
            type: 'button',
            'aria-label': 'Dismiss',
            onclick: () => {
              lastDistill = null;
              drawTeachPane();
            }
          },
            icon('close'))));
    }
    if (distillError && !isDistilling()) {
      body.append(
          h('div.tch-callout#teach-distill-error',
            h('div.t', icon('report'),
              'Distill failed \u2014 your observations are kept'),
            h('p', distillError),
            h('div.acts',
              h('button.btn.btn-ghost.btn-sm', {
                type: 'button',
                disabled: !canDistill(),
                onclick: () => runDistill(null)
              },
                icon('refresh'), 'Try again'))));
    }
    if (!ts.length && !isDistilling()) {
      body.append(h(
          'p.muted.small',
          openObs ?
              `${openObs} observation${
                  openObs === 1 ?
                      '' :
                      's'} waiting \u2014 press Distill to turn them into rules.` :
              'Nothing learned yet. Leave a few observations, then Distill.'));
    }
    ts.forEach((t) => body.append(teachingCard(t)));
    const summ = (state.session || {}).summary || {};
    const toks = summ.theme_suggestions || [];
    if (toks.length) {
      body.append(
          h('div.learned-head', h('h4', 'Token suggestions'),
            h('span.muted.small',
              'styling notes \u2014 applied to the theme at Finish')));
      toks.forEach(
          (k) => body.append(
              h('div.tch.tch-token',
                h('div.top', h('span.rid.tpl', 'Tok'), h('b.mono', k.token),
                  h('span.muted', '\u2192'), h('code', String(k.value ?? ''))),
                k.reason ? h('div.when', k.reason) : null,
                (k.observation_ids || []).length ?
                    h('div.when', h('b', 'from '),
                      `${k.observation_ids.length} observation${
                          k.observation_ids.length === 1 ? '' : 's'}`) :
                    null)));
    }
    const oos = summ.out_of_scope || [];
    if (oos.length) {
      body.append(
          h('div.learned-head', h('h4', 'Not a layout rule'),
            h('span.muted.small',
              'kept for the catalog, component or data owners')));
      oos.forEach((x) => {
        const ob =
            (state.observations || []).find((o) => o.id === x.observation_id);
        body.append(h(
            'div.tch.cand',
            h('div.top', h('span.chip.chip-quiet', x.suggested_home || 'note'),
              ob ? h('span.muted.small',
                     `\u201c${(ob.text || '').slice(0, 90)}\u201d`) :
                   null),
            h('div.when', x.why || '')));
      });
    }
    if (hidden > 0)
      body.append(
          h('p.muted.small', `${hidden} dismissed or merged not shown \u00b7 `,
            h('a', {href: `/p/${pid}/teachings`}, 'open the ledger')));
  }

  async function teachAction(t, fn) {
    if (busyT.has(t.id)) return;
    busyT.add(t.id);
    redrawLearned();
    try {
      await fn();
      // Labels (R1/T1/C1) are assigned in session state; re-read it.
      busyT.delete(t.id);
      await refresh();
    } catch {
    } finally {
      busyT.delete(t.id);
      redrawLearned();
    }
  }
  function redrawLearned() {
    if (imm)
      imm.onState();
    else if (tab === 'learned')
      drawTeachPane();
  }
  function replaceTeaching(t) {
    if (!t) return;
    const i = (state.teachings || []).findIndex((x) => x.id === t.id);
    if (i >= 0)
      state.teachings[i] = {
        ...state.teachings[i],
        ...t,
        label: t.label || state.teachings[i].label
      };
    else
      (state.teachings = state.teachings || []).push(t);
  }

  function teachingCard(t) {
    const cand = t.status === 'candidate';
    const tpl = t.kind === 'pattern';
    const eff = t.efficacy || {};
    const sig = eff.signal;
    const warn = sig === 'not_followed' || sig === 'rephrased_no_gain';
    const busy = busyT.has(t.id);
    const stEl = t.status === 'confirmed' ?
        h('span.st.ok', icon('check_circle'), tpl ? 'active' : 'confirmed') :
        cand ? h('span.st', signalPill('candidate')) :
               h('span.st', signalPill(t.status));
    const srcIds = (t.source_observation_ids || []).map(obsIndex);
    const apps = t.applications;
    return h(
        'div.tch',
        {class: [cand ? 'cand' : '', warn ? 'warn' : ''], dataset: {tid: t.id}},
        h('div.top', ridChip(t),
          h('span.sec', tpl ? 'Template' : (t.section || '')),
          t.refines ? h('span.chip.chip-quiet.chip-xs', {
            title:
                'Confirming adds this wording as a new phrasing of the existing teaching'
          },
                        `refines ${
                            (teachById(t.refines) || {}).label || t.refines}`) :
                      null,
          t.polarity === 'dont' ? h('span.pol-pill.bad', 'Don\'t') : null,
          busy ? spinner() : null, stEl),
        tpl && t.template && t.template.image_url ? h('div.tch-thumb', {
          style: {
            backgroundImage: `url("${t.template.image_url}")`,
            backgroundPosition: 'center top'
          }
        }) :
                                                    null,
        h('div.body',
          t.title && t.body_md && !t.body_md.startsWith(t.title) ?
              [h('b', t.title), ' \u2014 '] :
              null,
          t.body_md || t.title || ''),
        t.applies_when ?
            h('div.when', h('b', tpl ? 'appears when ' : 'applies when '),
              t.applies_when) :
            null,
        !cand ? h('div.eff', effBars(eff),
                  h('span',
                    eff.summary ||
                        (eff.trials ?
                             `followed ${eff.followed || 0} / ${eff.trials}` :
                             'no trials yet')),
                  sig ? signalPill(sig) : null) :
                null,
        apps ? appsBlock(t, apps) : null,
        t.suggestion && t.suggestion.kind && !warn ?
            suggestionCallout(t, busy) :
            null,
        warn ?
            h('div.tch-callout',
              h('div.t', icon('report'), 'The model isn\u2019t getting this.'),
              h('p',
                sig === 'rephrased_no_gain' ?
                    `${eff.phrasings || 2} phrasings, ${
                        eff.trials || 0} trials, followed ${
                        eff.followed ||
                        0} \u2014 prose may not be the right vehicle for this one.` :
                    `Followed ${eff.followed || 0} of ${
                        eff.trials || 0} times it was expected.`),
              h('div.acts',
                h('button.btn.btn-ghost.btn-sm',
                  {type: 'button', disabled: busy, onclick: () => rephrase(t)},
                  icon('auto_awesome'), 'Rephrase with Gemini'),
                h('button.btn.btn-ghost.btn-sm', {
                  type: 'button',
                  onclick: () => templateFrom(
                      selectedKey ? {key: selectedKey} : {whole: true})
                },
                  icon('dashboard_customize'), 'Make it a template'),
                h('button.btn.btn-ghost.btn-sm',
                  {type: 'button', disabled: busy, onclick: () => escalate(t)},
                  icon('flag'), 'Escalate as unfixable Don\u2019t'))) :
            null,
        h('div.acts',
          cand ?
              [
                h('button.btn.btn-primary.btn-sm', {
                  type: 'button',
                  disabled: busy,
                  onclick: () => teachAction(
                      t,
                      async () => replaceTeaching(
                          (await patch(`/api/teachings/${t.id}`, {
                            status: 'confirmed'
                          })).teaching))
                },
                  icon('check'), 'Confirm'),
                h('button.btn.btn-ghost.btn-sm',
                  {type: 'button', onclick: () => editTeaching(t)},
                  icon('edit'), 'Edit'),
                h('button.btn.btn-ghost.btn-sm', {
                  type: 'button',
                  disabled: busy,
                  onclick: () => teachAction(
                      t,
                      async () => replaceTeaching(
                          (await patch(`/api/teachings/${t.id}`, {
                            status: 'dismissed'
                          })).teaching))
                },
                  'Dismiss'),
              ] :
              [
                !apps ? h('button.btn.btn-ghost.btn-sm', {
                  type: 'button',
                  disabled: busy,
                  onclick: () => findApplications(t)
                },
                          icon('travel_explore'),
                          tpl ? 'Where else?' : 'Where else does this apply?') :
                        null,
                !tpl ? h('button.btn.btn-ghost.btn-sm',
                         {type: 'button', onclick: () => editTeaching(t)},
                         icon('edit'), 'Edit') :
                       null,
                tpl ? h('a.btn.btn-ghost.btn-sm',
                        {href: `/p/${pid}/design?tab=templates`},
                        icon('open_in_new'), 'Open in Design system') :
                      null,
              ],
          h('span.src',
            [
              srcIds.length ? `from ob ${srcIds.join(', ')}` :
                              (t.origin ? `origin: ${t.origin}` : ''),
              cand && t.confidence != null ?
                  ` \u00b7 confidence ${t.confidence}` :
                  ''
            ].join(''))),
        !cand ? historyDetails(t) : null);
  }

  function suggestionCallout(t, busy) {
    const tplK = t.suggestion.kind === 'template';
    return h(
        'div.tch-callout', {class: 'sugg'},
        h('div.t', icon(tplK ? 'dashboard_customize' : 'flag'),
          tplK ? 'Gemini suggests capturing this as a template' :
                 'Gemini suggests escalating this as an unfixable Don\u2019t'),
        t.suggestion.reason ? h('p', t.suggestion.reason) : null,
        h('div.acts',
          tplK ? h('button.btn.btn-ghost.btn-sm', {
            type: 'button',
            onclick: () =>
                templateFrom(selectedKey ? {key: selectedKey} : {whole: true})
          },
                   icon('dashboard_customize'), 'Make it a template') :
                 h('button.btn.btn-ghost.btn-sm',
                   {type: 'button', disabled: busy, onclick: () => escalate(t)},
                   icon('flag'), 'Escalate as unfixable Don\u2019t')));
  }

  function appsBlock(t, apps) {
    const applies = apps.applies || [];
    const exc = apps.exceptions || [];
    return h(
        'div.apps',
        h('div.t', icon('travel_explore'), 'Where else does this apply? ',
          h('span.muted',
            `${applies.length} scenario${
                applies.length === 1 ? '' : 's'} \u00b7 ${
                exc.length} exception${exc.length === 1 ? '' : 's'}`),
          h('span', {style: 'flex:1'}),
          h('button.mini-btn', {
            type: 'button',
            title: 'Recompute',
            onclick: () => findApplications(t)
          },
            icon('refresh'))),
        applies.map((a) => {
          const ci =
              a.probe_output_id ? cardIndexForOutput(a.probe_output_id) : -1;
          return h(
              'div.app', h('span.name', a.title || a.sample_id),
              h('span.how',
                a.how_it_applies ? `\u2014 ${a.how_it_applies}` : ''),
              ci >= 0 ? h('button.mini-btn.done',
                          {type: 'button', onclick: () => selectCard(ci)},
                          `Probe \u00b7 card ${ci + 1}`) :
                        h('button.mini-btn.try', {
                          type: 'button',
                          disabled: busyT.has(t.id),
                          onclick: () => tryIt(t, a)
                        },
                          'Try it'));
        }),
        exc.map(
            (x) =>
                h('div.app.exc', icon('block'),
                  h('span.name', x.title || x.sample_id),
                  h('span.how', x.why ? `\u2014 ${x.why}` : ''))),
        !applies.length && !exc.length ?
            h('div.small.muted',
              apps.none_reason || 'No other library scenario matches.') :
            null);
  }

  function findApplications(t) {
    return teachAction(t, async () => {
      const res = await post(`/api/teachings/${t.id}/applications`, {});
      replaceTeaching(res.teaching);
    });
  }

  function tryIt(t, a) {
    return teachAction(t, async () => {
      const st = await post(
          `/api/teachings/${t.id}/probe`,
          {session_id: sid, sample_id: a.sample_id});
      toast(
          `Probe card added \u2014 rendering ${a.title || ''} with ${t.label}`,
          'good');
      applyState(st);
    });
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
          'Retires the rule from the constitution and records a Don\u2019t for the model designers \u2014 with the trials as evidence.'),
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
            replaceTeaching(res.teaching);
            toast(
                `${t.label} escalated${
                    res.guideline ? ' \u2192 Dos & Don\u2019ts' : ''}`,
                'good');
            redrawLearned();
          }
        }
      ]
    });
    m.el.parentElement.classList.add('teach-scope');
  }

  function rephrase(t) {
    return teachAction(t, async () => {
      const {suggestion} = await post(`/api/teachings/${t.id}/rephrase`, {});
      editTeaching(t, {suggestion});
    });
  }

  function editTeaching(t, {suggestion} = {}) {
    const s = suggestion || {};
    const title = h('input.input', {value: t.title || ''});
    const body =
        h('textarea.input', {rows: 4, value: s.body_md ?? t.body_md ?? ''});
    const when =
        h('input.input', {value: s.applies_when ?? t.applies_when ?? ''});
    const section = h('input.input', {value: t.section || ''});
    const pol =
        h('select.input',
          ['do', 'dont'].map(
              (v) =>
                  h('option', {value: v, selected: (t.polarity || 'do') === v},
                    v === 'do' ? 'Do' : 'Don\u2019t')));
    const reason = h('input.input', {
      value: s.reason || '',
      placeholder: 'Why are you rephrasing? (kept with the phrasing)'
    });
    const m = modal({
      title: suggestion ?
          `Rephrase ${t.label} \u2014 Gemini\u2019s suggestion` :
          `Edit ${t.label}`,
      wide: true,
      body: [
        suggestion ?
            h('p.muted.small', {style: 'margin-top:0'},
              'Review and edit before saving; saving records a new phrasing so its trials are tracked separately.') :
            null,
        h('label.field', h('span.field-label', 'Title'), title),
        h('label.field', h('span.field-label', 'Rule'), body),
        h('label.field', h('span.field-label', 'Applies when'), when),
        h('div',
          {style: 'display:grid;grid-template-columns:1fr 160px;gap:12px'},
          h('label.field', h('span.field-label', 'Section'), section),
          h('label.field', h('span.field-label', 'Polarity'), pol)),
        h('label.field', h('span.field-label', 'Reason'), reason),
      ],
      actions: [
        {label: 'Cancel'}, {
          label: 'Save phrasing',
          kind: 'primary',
          onClick: async () => {
            const b = {};
            if (title.value !== (t.title || '')) b.title = title.value;
            if (body.value !== (t.body_md || '')) b.body_md = body.value;
            if (when.value !== (t.applies_when || ''))
              b.applies_when = when.value;
            if (section.value !== (t.section || '')) b.section = section.value;
            if (pol.value !== (t.polarity || 'do')) b.polarity = pol.value;
            if (reason.value.trim()) b.reason = reason.value.trim();
            if (!Object.keys(b).length) return;
            const res = await patch(`/api/teachings/${t.id}`, b);
            replaceTeaching(res.teaching);
            redrawLearned();
          }
        }
      ]
    });
    m.el.parentElement.classList.add('teach-scope');
  }

  function historyDetails(t) {
    const eff = t.efficacy || {};
    const box = h('div.hist-body');
    const det =
        h('details.hist',
          h('summary',
            `History \u00b7 ${eff.phrasings || t.version || 1} phrasing${
                (eff.phrasings || t.version || 1) === 1 ?
                    '' :
                    's'} \u00b7 ${eff.trials || 0} trials`),
          box);
    det.addEventListener('toggle', async () => {
      if (!det.open || box.dataset.loaded) return;
      box.dataset.loaded = '1';
      mount(box, spinner());
      try {
        const d = await get(`/api/teachings/${t.id}`, {quiet: true});
        mount(box, historyView(d));
      } catch (e) {
        mount(box, h('p.small.c-bad', String(e.message || e)));
      }
    });
    return det;
  }

  // -------------------------------------------------------- header actions
  function addScenariosDialog() {
    const briefIn = h('textarea.input', {
      rows: 3,
      placeholder:
          'Optional: what kinds of scenarios? (e.g. "more cards where the person must approve a cost")'
    });
    const countIn = h('select.input.input-sm', [
      1, 2, 3, 4
    ].map((n) => h('option', {value: n, selected: n === 2}, `${n}`)));
    const m = modal({
      title: 'Add scenarios',
      body: [
        h('p.muted.small', {style: 'margin-top:0'},
          'Picks matching scenarios from the library and renders them with the rules confirmed so far.'),
        h('label.field', h('span.field-label', 'Brief'), briefIn),
        h('label.field', h('span.field-label', 'How many cards'), countIn)
      ],
      actions: [
        {label: 'Cancel'}, {
          label: 'Add cards',
          kind: 'primary',
          onClick: async () => {
            const st = await post(`/api/teach/${sid}/cards`, {
              brief: briefIn.value.trim(),
              count: Number(countIn.value) || 2
            });
            applyState(st);
          }
        }
      ]
    });
    m.el.parentElement.classList.add('teach-scope');
  }

  async function runDistill(btn) {
    if (isDistilling() || !openObsCount()) return null;
    const openObs =
        (state.observations || []).filter((o) => o.status === 'open');
    distilling = true;
    distillingCount = openObs.length;
    distillingObsIds = new Set(openObs.map((o) => o.id));
    distillError = null;
    stateSeq++;
    if (btn) {
      btn.disabled = true;
      btn.classList.add('is-working');
      mount(
          btn, spinner('Distilling\u2026'),
          distillingCount ? h('span.badge-count', String(distillingCount)) :
                            null);
    }
    drawHeader();
    if (screen === 'work') drawTeachPane();
    if (imm) imm.onState();
    try {
      const res = await post(`/api/teach/${sid}/distill`, {});
      distilling = false;
      distillingCount = 0;
      distillingObsIds.clear();
      distillError = (res.result && (res.result.errors || []).length) ?
          res.result.errors.join(' \u00b7 ') :
          null;
      lastDistill = res.result || null;
      tab = 'learned';
      applyState(res.state);
      const r = res.result || {};
      toast(
          `Distilled: ${(r.created || []).length} new \u00b7 ${
              (r.updated || []).length} updated`,
          'good');
      return res;
    } catch (e) {
      distilling = false;
      distillingCount = 0;
      distillingObsIds.clear();
      distillError =
          (e && e.message) || 'Gemini or the server returned an error.';
      tab = 'learned';
      drawHeader();
      if (screen === 'work') drawTeachPane();
      if (imm) imm.onState();
      return null;
    }
  }

  // ---------------------------------------------------------------- finish
  async function drawFinish() {
    if (!finishEl.firstChild)
      mount(
          finishEl, h('section.teach-pane#finish-left'),
          h('section.teach-pane#finish-right'));
    const left = finishEl.querySelector('#finish-left');
    const right = finishEl.querySelector('#finish-right');
    drawFinishLeft(left);
    drawFinishRight(right);
    const s = state.session || {};
    if (!assembled && !assembling && s.status !== 'done' && !s.revision_id)
      assemble();
  }

  async function assemble() {
    assembling = true;
    drawFinishLeft(finishEl.querySelector('#finish-left'));
    try {
      assembled = await post(`/api/teach/${sid}/assemble`, {polish});
      tokenPick.clear();
      (assembled.theme_changes || []).forEach((c) => tokenPick.add(c.token));
    } catch {
      assembled = null;
    } finally {
      assembling = false;
      if (alive && screen === 'finish')
        drawFinishLeft(finishEl.querySelector('#finish-left'));
    }
  }

  function diffLines(asm) {
    const lines = [];
    const push = (op, text) => {
      const t = String(text ?? '');
      lines.push({op: /^#{1,6}\s/.test(t) ? 'h' : op, text: t});
    };
    const d0 = asm.diff;
    if (d0 && typeof d0 === 'object' && !Array.isArray(d0) &&
        Array.isArray(d0.rows)) {
      for (const r of d0.rows) {
        const t = r.type || r.op || '';
        if (/insert|add/.test(t))
          push('a', r.b);
        else if (/delete|remove/.test(t))
          push('d', r.a);
        else if (/replace|change/.test(t)) {
          push('d', r.a);
          push('a', r.b);
        } else
          push('c', r.b ?? r.a);
      }
    } else if (
        d0 && typeof d0 === 'object' && !Array.isArray(d0) &&
        typeof d0.unified === 'string') {
      for (const it of d0.unified.split('\n')) {
        if (/^(\+\+\+|---|@@)/.test(it)) continue;
        const c = it[0];
        push(
            c === '+'     ? 'a' :
                c === '-' ? 'd' :
                            'c',
            c === '+' || c === '-' || c === ' ' ? it.slice(1) : it);
      }
    } else if (Array.isArray(asm.diff)) {
      for (const it of asm.diff) {
        if (typeof it === 'string') {
          if (/^(\+\+\+|---|@@)/.test(it)) continue;
          const c = it[0];
          push(
              c === '+'     ? 'a' :
                  c === '-' ? 'd' :
                              'c',
              c === '+' || c === '-' || c === ' ' ? it.slice(1) : it);
        } else if (it && typeof it === 'object') {
          const op = it.op || it.type || it.kind || '';
          push(
              /add|insert|\+/.test(op)    ? 'a' :
                  /del|remove|-/.test(op) ? 'd' :
                                            'c',
              it.text ?? it.line ?? '');
        }
      }
    } else if (typeof asm.diff === 'string' && asm.diff.trim()) {
      for (const it of asm.diff.split('\n')) {
        if (/^(\+\+\+|---|@@)/.test(it)) continue;
        const c = it[0];
        push(
            c === '+'     ? 'a' :
                c === '-' ? 'd' :
                            'c',
            c === '+' || c === '-' || c === ' ' ? it.slice(1) : it);
      }
    } else {
      String(asm.constitution_md || '')
          .split('\n')
          .forEach((l) => push('a', l));
    }
    return lines.filter((l) => l.text.trim() !== '' || l.op !== 'c');
  }

  function tagFor(text) {
    const map = (assembled && assembled.rule_map) || {};
    for (const [label, tid] of Object.entries(map)) {
      const t = teachById(
          typeof tid === 'string' ?
              tid :
              (tid && (tid.t || tid.teaching_id || tid.id)));
      if (!t) continue;
      const ver = (tid && typeof tid === 'object' && tid.v) || t.version || 1;
      const probe = String(t.body_md || t.title || '').trim().slice(0, 36);
      if (probe && text.includes(probe))
        return `${label}${ver > 1 ? ` \u00b7 v${ver}` : ''}`;
    }
    return null;
  }

  function drawFinishLeft(el) {
    if (!el) return;
    const s = state.session || {};
    const confirmed =
        (state.teachings || []).filter((t) => t.status === 'confirmed').length;
    const candidates =
        (state.teachings ||
         []).filter((t) => t.status === 'candidate' && t.kind !== 'token');
    const revN = ((state.revision && state.revision.number) || 0) +
        (s.revision_id ? 0 : 1);
    if (s.revision_id || s.status === 'done') {
      mount(
          el,
          h('div.pane-head',
            h('h3',
              `Revision ${
                  (state.revision && state.revision.number) || ''} saved`)),
          h('div.pane-body',
            h('p.small', icon('check_circle'), ' Saved from ',
              String(confirmed),
              ' confirmed teachings. Every bullet stays bound to its teaching for tracking.'),
            state.revision && state.revision.text_md ?
                h('pre.fdiff.saved-md', state.revision.text_md) :
                null));
      return;
    }
    const polishBox =
        h('label.check', {style: 'font-size:12.5px'}, h('input#finish-polish', {
            type: 'checkbox',
            checked: polish,
            onchange: (e) => {
              polish = e.target.checked;
              assemble();
            }
          }),
          ' Polish with Gemini ',
          h('span.muted.small', '(wording only; every rule kept)'));
    const candidateBanner = candidates.length ?
        h('div.callout.warn#finish-candidates-callout', {
          style:
              'margin-bottom:12px;display:flex;flex-direction:column;gap:8px;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--paper)'
        },
          h('div', {
            style:
                'display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap'
          },
            h('div',
              h('b',
                `${candidates.length} candidate rule${
                    candidates.length === 1 ? '' : 's'} not yet confirmed`),
              h('div.muted.small',
                confirmed === 0 ?
                    'Confirm at least one candidate rule so Revision ' + revN +
                        ' can be assembled and the Before vs. After effect study can run.' :
                    'Confirm any additional candidate rules you want included in Revision ' +
                        revN + '.')),
            h('button.btn.btn-primary.btn-sm#finish-confirm-all', {
              type: 'button',
              onclick: async (e) => {
                const btn = e.currentTarget;
                btn.disabled = true;
                try {
                  for (const c of candidates) {
                    await patch(
                        `/api/teachings/${c.id}`, {status: 'confirmed'});
                  }
                  const st = await get(`/api/teach/${sid}`, {quiet: true});
                  applyState(st);
                  assembled = null;
                  await assemble();
                } finally {
                  btn.disabled = false;
                }
              },
            },
              icon('check'),
              `Confirm ${
                  candidates.length === 1 ?
                      'rule' :
                      `all ${candidates.length} rules`} & assemble`)),
          h('div.stack', {style: 'display:flex;flex-direction:column;gap:6px'},
            candidates.map(
                (c) =>
                    h('div', {
                      style:
                          'display:flex;align-items:flex-start;justify-content:space-between;gap:8px;padding:6px 8px;background:var(--bone);border-radius:6px;font-size:12.5px'
                    },
                      h('div', ridChip(c), ' ', h('b', c.title || ''),
                        c.rule_text ? h('div.muted.small', c.rule_text) : null),
                      h('button.btn.btn-ghost.btn-sm', {
                        type: 'button',
                        onclick: async (e) => {
                          e.currentTarget.disabled = true;
                          await patch(
                              `/api/teachings/${c.id}`, {status: 'confirmed'});
                          const st =
                              await get(`/api/teach/${sid}`, {quiet: true});
                          applyState(st);
                          assembled = null;
                          await assemble();
                        },
                      },
                        icon('check'), 'Confirm'))))) :
        null;
    if (assembling || !assembled) {
      mount(
          el,
          h('div.pane-head',
            h('h3', `Revision ${revN} \u2014 assembling\u2026`), polishBox),
          h('div.pane-body', candidateBanner,
            assembling ? spinner(
                             polish ? 'Assembling and polishing\u2026' :
                                      'Assembling from confirmed rules\u2026') :
                         h('button.btn.btn-ghost',
                           {type: 'button', onclick: () => assemble()},
                           icon('refresh'), 'Assemble')));
      return;
    }
    const lines = diffLines(assembled);
    const adds = lines.filter((l) => l.op === 'a').length,
          dels = lines.filter((l) => l.op === 'd').length;
    const theme = assembled.theme_changes || [];
    const gls = assembled.guideline_preview || [];
    mount(el,
          h('div.pane-head', h('h3', `Revision ${revN} \u2014 assembled from ${confirmed} confirmed rule${confirmed === 1 ? '' : 's'}`),
            h('div', {style: 'display:flex;align-items:center;gap:10px'}, polishBox, h('span.chip.chip-quiet', `+${adds} \u2212${dels} lines`))),
          h('div.pane-body',
            candidateBanner,
            h('div.fdiff#finish-diff', lines.map((l) => {
              const tag = l.op === 'a' ? tagFor(l.text) : null;
              return h('div.ln', {class: l.op}, l.text, tag ? h('span.tag', tag) : null);
            }),
              assembled.polished ? h('div.ln.c.muted', '(Polished with Gemini \u2014 wording only; every bullet stays bound to its teaching.)') : null),
            theme.length ? [
              h('h4.finish-h', 'Token suggestions ', h('span.muted.small', 'from styling observations \u2014 applied to the theme, not the constitution')),
              theme.map((c) => h('label.opt', h('input', {
                type: 'checkbox',
                checked: tokenPick.has(c.token),
                onchange: (e) => e.target.checked ? tokenPick.add(c.token) : tokenPick.delete(c.token)
              }),
                                 h('div', h('b.mono', c.token), c.from != null || c.current != null ? ` ${c.from ?? c.current} \u2192 ` : ' \u2192 ', h('b.mono', String(c.value)),
                                   c.reason ? h('div.why', c.reason) : null))),
            ] :
                           null,
            gls.length ? [
              h('h4.finish-h', 'Dos & Don\u2019ts ', h('span.muted.small', 'one per confirmed rule with an anchored observation \u00b7 crops from your anchors')),
              h('div.gl-prev', gls.map((g) => {
                const t = teachById(g.teaching_id);
                const isDont = (g.kind || g.polarity || (t && t.polarity)) === 'dont' || g.kind === 'bad';
                const img = g.image_url || g.crop_url || (g.crops || [])[0];
                return h('div.gl', h('div.img', {style: img ? {backgroundImage: `url("${img}")`, backgroundSize: 'contain', backgroundRepeat: 'no-repeat', backgroundPosition: 'center'} : null}),
                         h('div.b', h('b', h('span.pol-pill', {class: isDont ? 'bad' : 'good'}, isDont ? 'Don\u2019t' : 'Do'), ' ', g.title || (t && t.title) || ''),
                           h('span.muted', `${(t && t.label) || g.label || ''}${g.card_index != null ? ` \u00b7 from card ${g.card_index + 1}` : ''}`)));
              })),
            ] :
                         null,
            h('div.finish-save',
              h('span.muted.small', {style: 'flex:1'},
                `Saves Revision ${revN} (source: teach)${gls.length ? `, ${gls.length} Dos & Don\u2019ts` : ''}${tokenPick.size ? `, applies ${tokenPick.size} token${tokenPick.size === 1 ? '' : 's'}` : ''}, renders the After run with the judge on, and opens an effect study.`),
              h('button.btn.btn-primary.btn-lg#finish-save', {type: 'button', disabled: saving || !confirmed, onclick: () => saveFinish()},
                saving ? spinner('Saving\u2026') : [icon('save'), `Save Revision ${revN} & render After`]))));
  }

  async function saveFinish() {
    if (!assembled) return;
    saving = true;
    drawFinishLeft(finishEl.querySelector('#finish-left'));
    try {
      const st = await post(`/api/teach/${sid}/finish`, {
        constitution_md: assembled.constitution_md,
        bind_hint: assembled.bind_hint,
        apply_theme: [...tokenPick],
        guideline_teaching_ids: (assembled.guideline_preview || [])
                                    .map((g) => g.teaching_id)
                                    .filter(Boolean),
      });
      toast('Revision saved \u2014 rendering After', 'good');
      applyState(st);
    } catch {
    } finally {
      saving = false;
      if (alive && screen === 'finish')
        drawFinishLeft(finishEl.querySelector('#finish-left'));
    }
  }

  function drawFinishRight(el) {
    if (!el) return;
    const s = state.session || {};
    const effect = state.effect;
    const afterRun =
        s.after_run_id && state.runs ? state.runs[s.after_run_id] : null;
    const head = h(
        'div.pane-head', h('h3', 'Before vs. After \u00b7 effect study'),
        h('div', {style: 'display:flex;gap:6px;align-items:center'},
          afterRun ?
              h('span.chip', {
                class: afterRun.status === 'done' ? 'chip-good v-good' :
                                                    'chip-quiet'
              },
                `After run ${afterRun.status}${
                    afterRun.counts ? ` \u00b7 ${afterRun.counts.done ?? ''}${
                                          afterRun.counts.total ?
                                              ` / ${afterRun.counts.total}` :
                                              ''}` :
                                      ''}`) :
              null,
          effect ? h('span.chip.chip-quiet',
                     `attribution ${
                         effect.analysis_status || effect.status || ''}`) :
                   null,
          s.effect_id ? h('a.small', {href: `/p/${pid}/effects/${s.effect_id}`},
                          'Open effect study \u2192') :
                        null));
    if (!s.after_run_id) {
      mount(
          el, head,
          h('div.pane-body',
            h('p.muted',
              'Save the revision to render the session\u2019s cards again with your rules. The Before vs. After effect study attributes each change to the rule that caused it, and records a trial for every rule.')));
      return;
    }
    if (afterFetchedFor !==
        `${s.after_run_id}|${afterRun && afterRun.status}|${state.busy}`) {
      afterFetchedFor =
          `${s.after_run_id}|${afterRun && afterRun.status}|${state.busy}`;
      get(`/api/runs/${s.after_run_id}/outputs`, {quiet: true})
          .then((r) => {
            afterOutputs = r.outputs || r || [];
            if (alive && screen === 'finish')
              drawFinishRight(finishEl.querySelector('#finish-right'));
          })
          .catch(() => {});
    }
    const pairs = (effect && effect.pairs) || {};
    const pairFor = (sampleId) => pairs[`${sampleId}|phone`] ||
        Object.entries(pairs)
            .find(([k]) => k.startsWith(`${sampleId}|`))
            ?.[1] ||
        null;
    const cards = (state.cards || []).filter((c) => c.origin !== 'probe');
    const columns = cards.map((c, i) => {
      const pr = pairFor(c.sample_id);
      const ao = (pr && pr.treatment) ||
          (afterOutputs || []).find((o) => o.sample_id === c.sample_id);
      return {
        title: `${i + 1} \u00b7 ${(c.sample && c.sample.title) || ''}`,
        before: {
          url: c.output && c.output.screenshot_url,
          label:
              `Before \u00b7 Rev ${(c.output && c.output.revision_number) ?? 0}`
        },
        after: {
          url: ao && ao.screenshot_url,
          pending: !ao || BUSY.has(ao.status),
          label: `After \u00b7 Rev ${(ao && ao.revision_number) ?? ''}`
        },
        pair: pr,
        pairPending: !!effect && effect.analysis_status !== 'done',
      };
    });
    const ridLabel = (id) => {
      const t =
          teachById(id) || (state.teachings || []).find((x) => x.label === id);
      return t ?
          {label: t.label, kind: t.kind, title: t.title} :
          {label: id, kind: String(id).startsWith('T') ? 'pattern' : 'rule'};
    };
    const learned =
        (state.teachings || [])
            .filter(
                (t) => (t.status === 'confirmed' || t.kind === 'pattern') &&
                    (t.efficacy || {}).trials);
    mount(
        el, head,
        h('div.pane-body', beforeAfter({columns, ridLabel}),
          learned.length ?
              h('div.ledger-learned',
                h('div.lt', 'What the ledger learned from this run'),
                h('div.small.lgrid',
                  learned.map(
                      (t) =>
                          h('span', ridChip(t),
                            ` ${(t.efficacy || {}).summary || ''} `,
                            (t.efficacy || {}).signal ?
                                signalPill(t.efficacy.signal) :
                                null)))) :
              null,
          h('div.finish-next',
            h('button.btn.btn-ghost#finish-keep', {
              type: 'button',
              onclick: async () => {
                const st = await post(`/api/projects/${pid}/teach`, {count: 6});
                navigate(`/p/${pid}/teach/${st.session.id}`);
              }
            },
              icon('school'),
              `Keep teaching (new session on Rev ${
                  (state.revision && state.revision.number) || ''})`),
            h('button.btn.btn-primary#finish-studio', {
              type: 'button',
              onclick: async () => {
                await post(`/api/teach/${sid}/graduate`, {}).catch(() => {});
                navigate(`/p/${pid}/constitution`);
              }
            },
              icon('rocket_launch'), 'Open Studio'))));
  }

  // ------------------------------------------------------------ immersive
  function immApi() {
    return {
      pid,
      sid,
      getState: () => state,
      getIndex: () => cardIdx,
      setIndex: (i) => selectCard(i),
      card: () => curCard(),
      linksFor,
      anchorKeys,
      boxesFor,
      compFor,
      cropFor,
      applyState,
      refresh,
      distill: () => runDistill(null),
      isDistilling,
      canDistill,
      distillingCount: () => distillingCount || openObsCount(),
      renderLearnedInto,
      templateFrom: (sel) => templateFrom(sel),
      exit: () => setImmersive(false),
      finish: () => setScreen('finish'),
      teachById,
    };
  }
}

/** Phrasings + trials for a teaching (GET /api/teachings/{tid}). */
export function historyView(d) {
  const versions = d.versions || [];
  const trials = d.trials || [];
  return h(
      'div.detail-grid',
      h('div', h('h5', 'Phrasings'),
        versions.length ?
            versions.map(
                (v) =>
                    h('div.ver', h('span.v', `v${v.number}`),
                      h('div', h('div.txt', v.body_md || v.title || ''),
                        h('div.meta',
                          h('span',
                            [
                              v.source, v.revision_id ? 'revision' : '',
                              v.reason ? `\u201c${v.reason}\u201d` : ''
                            ].filter(Boolean)
                                .join(' \u00b7 ')),
                          h('span.tv', {
                            class: v.trials ? (v.rate >= 0.8     ? 'yes' :
                                                   v.rate >= 0.3 ? 'part' :
                                                                   'no') :
                                              ''
                          },
                            `${v.followed || 0} / ${v.trials || 0}`),
                          h('span.muted', relTime(v.created_at)))))) :
            h('p.muted.small', 'One phrasing.')),
      h('div', h('h5', `Trials (${trials.length})`),
        trials.length ?
            trials.slice(0, 24).map(
                (t) => h(
                    'div.trial', h('div.th', {
                      style: t.output &&
                              (t.output.thumb_url || t.output.screenshot_url) ?
                          {
                            backgroundImage: `url("${
                                t.output.thumb_url ||
                                t.output.screenshot_url}")`
                          } :
                          null
                    }),
                    h(
                        'div',
                        h(
                            'div.n',
                            h(
                                'span.tv', {
                                  class:
                                      t.followed ===
                                          'yes' ?
                                      'yes' :
                                      t.followed ===
                                          'partial' ?
                                      'part' :
                                      t.followed ===
                                          'no' ?
                                      'no' :
                                      ''
                                },
                                t.followed),
                            (t.output && t.output.sample_title) ||
                                t.sample_id || '',
                            h(
                                'span.src',
                                `${t.source} \u00b7 v${t.version}${
                                    t.run_kind ? ` \u00b7 ${t.run_kind}` :
                                                 ''}`)),
                        h('div.e',
                          t.evidence ?
                              (typeof t.evidence === 'string' ?
                                   t.evidence :
                                   (t.evidence.note || t.evidence.summary ||
                                    t.evidence.text ||
                                    t.evidence.why || '')) :
                              `expected ${t.expected}`)))) :
            h('p.muted.small',
              'No trials yet. Try it on another scenario, or finish the session to run Before vs. After.')));
}
