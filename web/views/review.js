// Review: focus mode for one output at a time (screenshot ⇄ live ⇄ JSON),
// judge findings, human verdict, Save as Do/Don't, send to chat.
import {get, post, put, qs} from '../components/api.js';
import {chip, genreChip, judgeVerdictChip, loadGenres, scoreBadge, statusChip} from '../components/badges.js';
import {reviewChatTabs, sendOutputToChat} from '../components/chat.js';
import {debounce, emptyState, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {jsonTree} from '../components/jsontree.js';
import {openPatternCapture} from '../components/pattern_capture.js';
import {emit, navigate, on, store} from '../components/store.js';
import {findComponent, relatedThemeFields, themeFieldControl, themeGroupFor} from '../components/theme_tokens.js';
import {modal, segmented, toast} from '../components/ui.js';
import {DEFAULT_THEME} from '../renderer/host.js';

import {groupStagePairs, hasStages, outputShotForRenderer, RENDERER_META, stageChip} from './run_detail.js';

const DONT_TEMPLATE = '**What**\n\n\n**Why it matters**\n\n\n**Instead**\n';
const DO_TEMPLATE = '**What**\n\n\n**Why it works**\n\n\n**When to use it**\n';
const FILTER_KEYS = ['sort', 'genre', 'group', 'viewport', 'verdict', 'valid'];

export async function render(root, params, ctx) {
  const pid = params.pid;
  let rid = params.rid;
  const sp = ctx.url.searchParams;
  await loadGenres();

  if (!rid) {
    rid = store.lastRunId[pid];
    if (!rid) {
      const {runs} = await get(`/api/projects/${pid}/runs`);
      const r = (runs || []).find((x) => x.counts && x.counts.done) ||
          (runs || [])[0];
      rid = r && r.id;
    }
    if (!rid) {
      root.appendChild(
          h('div.page', h('h1', 'Review'),
            emptyState(
                'Nothing to review yet',
                'Start a run, then review its outputs worst-first.',
                h('a.btn.btn-primary', {href: `/p/${pid}/runs?new=baseline`},
                  icon('play_arrow'), 'Run a baseline'))));
      return;
    }
    history.replaceState(null, '', `/p/${pid}/review/${rid}${location.search}`);
  }

  const filters = {};
  FILTER_KEYS.forEach((k) => {
    if (sp.get(k)) filters[k] = sp.get(k);
  });
  if (!filters.sort) filters.sort = 'worst';
  let list = [];
  let staged = false;      // run has connected-app form/result pairs
  let pairOf = new Map();  // output id → pair_id (from the list endpoint)
  let idx = 0;
  let cur = null;  // {output, sample}
  let view = localStorage.getItem('a2uid.reviewView') || 'screenshot';
  // The web renderer is the only renderer.
  const RIDS = ['web'];
  const activeRenderer = 'web';
  let alive = true;
  let tree = null;
  let iframe = null;
  let pollTimer = null;
  // Live-inspector theme editing: draft of the project theme, previewed in the
  // live iframe and saved project-wide on demand.
  let projectSettings = null;
  let themeDraft = null;
  let themeDirty = false;
  let selectedId = null;
  let runMeta = null;
  let liveThemeMode = 'run';
  const themePaneEl = h('div.theme-pane#inspector-theme');

  const posEl = h('span.review-pos#review-position');
  const leftEl = h('div.review-left');
  const rendererBarEl = h('div.renderer-bar#review-renderer-bar');
  const stageEl = h('div.review-stage#review-stage');
  // Right column: the review overview; the chat panel replaces it when open
  // (toggle via the Review / Chat tabs at the top of either panel).
  const rightEl = h('div.review-panel-body');
  const rightWrap =
      h('div.review-right#review-panel',
        // Same header row as the chat panel (.chat-head) so the tabs and the
        // panel content don't shift when switching Review ⇄ Chat.
        h('div.review-head', reviewChatTabs('review', {
            onReview: () => {},
            onChat: () => emit('chat-open', {}),
          })),
        rightEl);
  const viewSeg = segmented(
      [
        {
          value: 'screenshot',
          label: 'Screenshot',
          id: 'view-screenshot',
          icon: 'image',
          title: 'Screenshot (1)'
        },
        {
          value: 'live',
          label: 'Live (Web)',
          id: 'view-live',
          icon: 'touch_app',
          title: 'Live Web render with inspector (2)'
        },
        {
          value: 'json',
          label: 'JSON',
          id: 'view-json',
          icon: 'data_object',
          title: 'A2UI JSON (3)'
        },
      ],
      view, (v) => setView(v));

  const prevBtn =
      h('button.icon-btn#review-prev', {
        type: 'button',
        title: 'Previous (K)',
        'aria-label': 'Previous output',
        onclick: () => go(idx - 1)
      },
        icon('chevron_left'));
  const nextBtn =
      h('button.icon-btn#review-next', {
        type: 'button',
        title: 'Next (J)',
        'aria-label': 'Next output',
        onclick: () => go(idx + 1)
      },
        icon('chevron_right'));

  // The toolbar lives in the left column so the right column (Review panel)
  // starts at the top, exactly where the Chat panel sits when it replaces it.
  const reviewBar = h(
      'div.review-bar',
      h('a.btn.btn-quiet.btn-sm#review-back', {
        href: `/p/${pid}/runs/${rid}${
            qs(filters.sort === 'worst' ? {...filters, sort: undefined} :
                                          filters)}`
      },
        icon('arrow_back'), 'Run'),
      h('div.review-nav', prevBtn, posEl, nextBtn),
      h('span.muted.small.review-sortlabel',
        filters.sort === 'worst' ? 'worst first' : `sorted: ${filters.sort}`),
      h('div.grow'), viewSeg,
      h('button.icon-btn#review-keys', {
        type: 'button',
        title: 'Keyboard shortcuts',
        'aria-label': 'Keyboard shortcuts',
        onclick: () => showKeys()
      },
        icon('keyboard')));
  root.appendChild(
      h('div.review#review', h('div.review-body', leftEl, rightWrap)));
  leftEl.append(reviewBar, rendererBarEl, stageEl);

  function onMessage(e) {
    const d = e.data;
    if (!d || typeof d !== 'object' || !iframe ||
        e.source !== iframe.contentWindow)
      return;
    const id = d.id ?? d.componentId ?? null;
    if (d.type === 'a2ui-hover' && tree) {
      if (tree.hover) {
        tree.hover(id, {scroll: false});
      } else {
        tree.highlight(id, {scroll: false});
      }
    }
    if (d.type === 'a2ui-click') {
      selectComponent(id, {scrollTree: true, scrollFrame: false});
    }
    if (d.type === 'a2ui-ready') {
      if (themeDirty) postTheme();
      if (selectedId && iframe.contentWindow) {
        iframe.contentWindow.postMessage(
            {type: 'a2ui-select', id: selectedId, scroll: false}, '*');
      }
    }
  }
  window.addEventListener('message', onMessage);

  function onKey(e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (document.querySelector('.overlay')) return;
    const t = e.target;
    if (t &&
        (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
         t.tagName === 'SELECT' || t.isContentEditable))
      return;
    const k = e.key.toLowerCase();
    const map = {
      j: () => go(idx + 1),
      arrowright: () => go(idx + 1),
      k: () => go(idx - 1),
      arrowleft: () => go(idx - 1),
      g: () => setVerdict('good'),
      b: () => setVerdict('bad'),
      m: () => setVerdict('mixed'),
      d: () => saveGuideline('dont'),
      o: () => saveGuideline('do'),
      c: () => toChat(),
      1: () => setView('screenshot'),
      2: () => setView('live'),
      3: () => setView('json'),
      escape: () => {
        if (selectedId) selectComponent(null);
      },
      '?': () => showKeys(),
    };
    if (map[k] && cur) {
      e.preventDefault();
      map[k]();
    }
  }
  document.addEventListener('keydown', onKey);

  function showKeys() {
    const row = (k, d) => h('tr', h('td', h('kbd', k)), h('td', d));
    modal({
      title: 'Keyboard shortcuts',
      id: 'keys-dialog',
      body:
          h('table.keys',
            h('tbody', row('J / K', 'Next / previous output'),
              row('G', 'Verdict: Good'), row('B', 'Verdict: Bad'),
              row('M', 'Verdict: Mixed'), row('O', 'Save as Do'),
              row('D', 'Save as Don\u2019t'), row('C', 'Send to chat'),
              row('1 / 2 / 3', 'Screenshot / Live / JSON'))),
      actions: [{label: 'Close', kind: 'primary'}]
    });
  }

  async function loadRunMeta() {
    try {
      const r = await get(`/api/runs/${rid}`, {quiet: true});
      if (r && r.run) runMeta = r.run;
    } catch (_) { /* ignore */
    }
  }

  async function rethemeRunFromReview(btn) {
    if (btn) btn.disabled = true;
    try {
      const r = await post(`/api/runs/${rid}/retheme`, {renderers: RIDS});
      if (r && r.run) runMeta = r.run;
      liveThemeMode = 'run';
      const curTok = (runMeta &&
                      (runMeta.rendered_token_revision_number ||
                       runMeta.current_token_revision_number)) ||
          '';
      toast(`Applied Tokens T${curTok} \u2014 re-rendering\u2026`, 'good');
      await refreshCurrent();
      drawRendererBar();
      drawStage();
      drawPanel();
    } catch (e) {
      toast(`Re-theme failed: ${e.message || e}`, 'bad');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function loadList() {
    const res = await get(`/api/runs/${rid}/outputs${qs(filters)}`);
    list = groupStagePairs(res.outputs || []);
    staged = hasStages(list);
    // The list endpoint carries pair_id; keep it in case the detail lacks it.
    pairOf = new Map(list.map((o) => [o.id, o.pair_id || null]));
    const want = sp.get('o');
    idx = Math.max(0, want ? list.findIndex((o) => o.id === want) : 0);
    if (want && idx < 0) idx = 0;
  }

  function go(i) {
    if (!list.length) return;
    if (i < 0 || i >= list.length) {
      toast(
          i < 0 ? 'This is the first output.' :
                  'That was the last output in this run.',
          'info', 2000);
      return;
    }
    idx = i;
    const u = new URL(location.href);
    u.searchParams.set('o', list[idx].id);
    history.replaceState(null, '', u.pathname + u.search);
    loadOutput();
  }

  /** Jumps to output `id`: within the list, else reloads without filters. */
  function goToOutput(id) {
    const i = list.findIndex((x) => x.id === id);
    if (i >= 0) {
      go(i);
      return;
    }
    navigate(`/p/${pid}/review/${rid}${qs({o: id})}`);
  }

  async function triggerRender(o, targetRenderers) {
    try {
      toast('Rendering\u2026', 'info', 2500);
      const res = await post(`/api/outputs/${o.id}/render`, {
        renderers: targetRenderers,
      });
      if (res && res.output && cur && cur.output.id === o.id) {
        cur.output = {...cur.output, ...res.output};
        list[idx] = {...list[idx], ...res.output};
        drawRendererBar();
        drawStage();
        drawPanel();
        const anyActive =
            Object.values(cur.output.renders || {})
                .some(
                    (x) => x &&
                        (x.status === 'queued' || x.status === 'rendering'));
        if (anyActive) {
          clearTimeout(pollTimer);
          pollTimer = setTimeout(() => refreshCurrent(), 2000);
        }
      }
    } catch (e) {
      toast(`Render failed: ${e.message || e}`, 'bad');
    }
  }

  let loadSeq = 0;
  async function loadOutput() {
    const seq = ++loadSeq;
    clearTimeout(pollTimer);
    posEl.textContent = list.length ? `${idx + 1} / ${list.length}` : '0 / 0';
    prevBtn.disabled = idx <= 0;
    nextBtn.disabled = idx >= list.length - 1;
    if (!list.length) {
      mount(rendererBarEl);
      mount(stageEl, emptyState('No outputs match', 'Change the run filters.'));
      mount(rightEl);
      return;
    }
    mount(rightEl, spinner('Loading\u2026'));
    const res = await get(`/api/outputs/${list[idx].id}`);
    if (seq !== loadSeq || !alive) return;
    cur = res;
    if (selectedId &&
        !findComponent(res.output && res.output.a2ui, selectedId)) {
      selectedId = null;
    }
    list[idx] = {...list[idx], ...res.output};
    syncReviewSelection();
    drawRendererBar();
    drawStage();
    drawPanel();
    const anyRendering =
        Object.values(res.output.renders || {})
            .some(
                (r) =>
                    r && (r.status === 'queued' || r.status === 'rendering'));
    if ((res.output.status !== 'done' && res.output.status !== 'error') ||
        anyRendering)
      pollTimer = setTimeout(() => refreshCurrent(), 2000);
  }

  async function refreshCurrent() {
    if (!cur || !alive) return;
    const res = await get(`/api/outputs/${cur.output.id}`, {
                  quiet: true
                }).catch(() => null);
    if (!res || !alive || !cur || res.output.id !== cur.output.id) return;
    const prevRendersJson = JSON.stringify(cur.output.renders || {});
    const nextRendersJson = JSON.stringify(res.output.renders || {});
    const changed = res.output.status !== cur.output.status ||
        prevRendersJson !== nextRendersJson;
    cur = res;
    list[idx] = {...list[idx], ...res.output};
    if (changed) {
      drawRendererBar();
      drawStage();
    }
    drawPanel();
    const anyRendering =
        Object.values(res.output.renders || {})
            .some(
                (r) =>
                    r && (r.status === 'queued' || r.status === 'rendering'));
    if ((res.output.status !== 'done' && res.output.status !== 'error') ||
        anyRendering)
      pollTimer = setTimeout(() => refreshCurrent(), 2000);
  }

  function setView(v) {
    view = v;
    localStorage.setItem('a2uid.reviewView', v);
    viewSeg.set(v);
    drawRendererBar();
    drawStage();
  }

  function liveUrl(o) {
    if (!o.render_url) return null;
    const sep = o.render_url.includes('?') ? '&' : '?';
    const tm = liveThemeMode === 'live' ? '&theme=live' : '';
    return `${o.render_url}${sep}inspect=1${tm}`;
  }

  function drawRendererBar() {
    const drifted = Boolean(runMeta && runMeta.theme_drifted);
    const runTok = (runMeta &&
                    (runMeta.rendered_token_revision_number ||
                     runMeta.token_revision_number)) ||
        1;
    const curTok = (runMeta && runMeta.current_token_revision_number) || runTok;

    if (!cur || (view !== 'screenshot' && !(view === 'live' && drifted))) {
      rendererBarEl.hidden = true;
      return;
    }
    rendererBarEl.hidden = false;

    if (view === 'live') {
      mount(
          rendererBarEl,
          h('span.chip.chip-xs.token-drift-pill', icon('palette'),
            `Tokens T${runTok} \u2192 T${curTok}`),
          segmented(
              [
                {
                  value: 'run',
                  label: `Run tokens (T${runTok})`,
                  id: 'review-live-theme-run',
                },
                {
                  value: 'live',
                  label: `Current tokens (T${curTok})`,
                  id: 'review-live-theme-live',
                },
              ],
              liveThemeMode,
              (v) => {
                liveThemeMode = v;
                drawStage();
              }),
          h('div.grow'),
          h('button.btn.btn-sm.btn-ghost#review-live-retheme-btn', {
            type: 'button',
            onclick: (e) => rethemeRunFromReview(e.currentTarget),
          },
            icon('format_paint'), `Apply T${curTok} to run`));
      return;
    }

    const o = cur.output;
    const missing = RIDS.filter((rid) => !outputShotForRenderer(o, rid).full);
    mount(
        rendererBarEl, h('div.grow'),
        drifted ? h('div.token-drift-inline',
                    h('span.chip.chip-xs.token-drift-pill', icon('palette'),
                      `Tokens T${runTok} \u2192 T${curTok}`),
                    h('button.btn.btn-sm.btn-ghost#review-retheme-btn', {
                      type: 'button',
                      title: `Re-render all outputs in this run with Tokens T${
                          curTok} (0 LLM calls)`,
                      onclick: (e) => rethemeRunFromReview(e.currentTarget),
                    },
                      icon('format_paint'), `Apply T${curTok} & re-render`)) :
                  null,
        missing.length && o.a2ui ?
            h('button.btn.btn-sm.btn-ghost#render-missing',
              {type: 'button', onclick: () => triggerRender(o, missing)},
              icon('bolt'), 'Render') :
            null);
  }

  function drawStage() {
    if (!cur) return;
    const o = cur.output;
    iframe = null;
    tree = null;
    stageEl.className = `review-stage view-${view} ren-${activeRenderer}`;
    if (view === 'screenshot') {
      const m = RENDERER_META[activeRenderer] || RENDERER_META.web;
      const shot = outputShotForRenderer(o, activeRenderer);
      const meta = shot.meta || {};
      if (shot.full) {
        mount(
            stageEl,
            h('div.shot-scroll',
              h('div.single-render-banner',
                h('span.renderer-badge', {dataset: {renderer: activeRenderer}},
                  m.badge),
                h('strong', m.label), h('span.muted.small', m.engine),
                meta.width && meta.height ?
                    h('span.chip.chip-quiet',
                      `${meta.width}\u00d7${meta.height}px`) :
                    null,
                meta.latency_ms ?
                    h('span.chip.chip-quiet', `${meta.latency_ms} ms`) :
                    null,
                h('div.grow'),
                h('button.btn.btn-xs.btn-ghost', {
                  type: 'button',
                  onclick: () => triggerRender(o, [activeRenderer])
                },
                  icon('refresh'), 'Re-render')),
              h('img.shot#review-screenshot', {
                src: shot.full,
                alt: `Screenshot of ${o.sample_title} on ${m.label}`
              })));
      } else {
        mount(
            stageEl,
            h('div.stage-wait',
              o.status === 'error' || shot.status === 'error' ?
                  icon('error') :
                  (shot.status === 'rendering' || shot.status === 'queued' ?
                       h('span.spinner') :
                       icon('devices')),
              h('p',
                shot.error ?
                    shot.error :
                    (shot.status === 'rendering' || shot.status === 'queued' ?
                         `Rendering on ${m.label}\u2026` :
                         `Not yet rendered on ${m.label}.`)),
              o.a2ui && shot.status !== 'rendering' ?
                  h('button.btn.btn-sm.btn-primary', {
                    type: 'button',
                    onclick: () => triggerRender(o, [activeRenderer])
                  },
                    icon('play_arrow'), `Render on ${m.label} now`) :
                  null));
      }
    } else if (view === 'live') {
      const vpW = (o.render_meta && o.render_meta.width) || ({
                    phone: 412,
                    tablet: 834,
                    desktop: 1280,
                    smart_display: 1024,
                    watch: 192
                  }[o.viewport]) ||
          412;
      const src = liveUrl(o);
      if (!src || !o.a2ui) {
        mount(stageEl, h('div.stage-wait', h('p', 'No A2UI to render yet.')));
        return;
      }
      iframe =
          h('iframe.live-frame#review-live',
            {src, title: 'Live render', style: {width: `${vpW}px`}});
      const frameBox = h('div.live-box', iframe);
      tree = jsonTree(o.a2ui, {
        collapseDepth: 3,
        onNodeClick: (id) => {
          selectComponent(id, {scrollTree: false, scrollFrame: true});
        }
      });
      tree.addEventListener('mouseover', (e) => {
        const n = e.target.closest('.jt-a2ui');
        postHighlight(n ? n.parentElement.dataset.a2uiId : null, false);
      });
      tree.addEventListener('mouseleave', () => {
        postHighlight(null, false);
      });
      const inspector =
          h('div.inspector',
            h('div.inspector-pane',
              h('div.inspector-head', icon('frame_inspect'), 'Inspector',
                h('span.muted.small', 'hover the render or click a component')),
              h('div.inspector-tree', tree)),
            themePaneEl);
      if (selectedId && !findComponent(o.a2ui, selectedId)) selectedId = null;
      drawThemePane();
      ensureSettings();
      if (selectedId)
        requestAnimationFrame(
            () => tree &&
                (tree.select ? tree.select(selectedId, {scroll: true}) :
                               tree.highlight(selectedId)));
      mount(
          stageEl,
          h('div.live-split', h('div.live-scroll', frameBox), inspector));
      const fit = () => {
        const avail = frameBox.parentElement ?
            frameBox.parentElement.clientWidth - 32 :
            vpW;
        const scale = Math.min(1, avail / vpW);
        iframe.style.transform = scale < 1 ? `scale(${scale})` : '';
        iframe.style.transformOrigin = 'top left';
        const hgt =
            Math.max(400, ((o.render_meta && o.render_meta.height) || 900));
        iframe.style.height = `${hgt}px`;
        frameBox.style.width = `${vpW * scale}px`;
        frameBox.style.height = `${hgt * scale}px`;
      };
      requestAnimationFrame(fit);
    } else {
      if (!o.a2ui) {
        mount(stageEl, h('div.stage-wait', h('p', 'No A2UI yet.')));
        return;
      }
      tree = jsonTree(o.a2ui, {collapseDepth: 3});
      mount(
          stageEl,
          h('div.json-stage',
            h('div.json-actions',
              h('button.btn.btn-sm.btn-quiet',
                {type: 'button', onclick: () => tree.expandAll()},
                'Expand all'),
              h('button.btn.btn-sm.btn-quiet',
                {type: 'button', onclick: () => tree.collapseAll()},
                'Collapse'),
              h('button.btn.btn-sm.btn-quiet', {
                type: 'button',
                onclick: () => navigator.clipboard
                                   .writeText(JSON.stringify(o.a2ui, null, 2))
                                   .then(() => toast('A2UI copied', 'good'))
              },
                icon('content_copy'), 'Copy')),
            tree,
            o.prompt_text ? h('details.prompt',
                              h('summary', 'Prompt sent to the generator'),
                              h('pre.code', o.prompt_text)) :
                            null));
    }
  }

  function cloneTheme(t) {
    const src = t || {};
    return {
      ...DEFAULT_THEME,
      ...src,
      custom_tokens: JSON.parse(JSON.stringify(src.custom_tokens || {})),
      custom_css: String(src.custom_css || ''),
    };
  }

  let settingsLoading = null;
  function ensureSettings() {
    if (projectSettings || settingsLoading) return settingsLoading;
    settingsLoading = get(`/api/projects/${pid}/settings`, {quiet: true})
                          .then((res) => {
                            projectSettings = (res && res.settings) || {};
                            if (!themeDraft)
                              themeDraft = cloneTheme(projectSettings.theme);
                            if (alive) drawThemePane();
                          })
                          .catch(() => {
                            if (alive)
                              mount(
                                  themePaneEl,
                                  h('p.muted.small.theme-pane-msg',
                                    'Theme tokens unavailable.'));
                          })
                          .finally(() => {
                            settingsLoading = null;
                          });
    return settingsLoading;
  }

  function currentSelectedComp() {
    if (!cur || !cur.output || !selectedId) return null;
    return findComponent(cur.output.a2ui, selectedId);
  }

  function syncReviewSelection() {
    const comp = currentSelectedComp();
    store.reviewSelection = cur && cur.output ? {
      output: cur.output,
      sample: cur.sample || null,
      component: comp || null,
    } :
                                                null;
    emit('review-selection-changed', store.reviewSelection);
  }

  function selectComponent(id, {scrollTree = false, scrollFrame = false} = {}) {
    selectedId = id || null;
    if (cur && cur.output && selectedId &&
        !findComponent(cur.output.a2ui, selectedId)) {
      selectedId = null;
    }
    if (tree) {
      if (tree.select) {
        tree.select(selectedId, {scroll: scrollTree});
      } else {
        tree.highlight(selectedId, {scroll: scrollTree});
      }
    }
    if (iframe && iframe.contentWindow) {
      iframe.contentWindow.postMessage(
          {type: 'a2ui-select', id: selectedId, scroll: scrollFrame}, '*');
    }
    syncReviewSelection();
    drawThemePane();
    if (cur) drawPanel();
  }

  const offClearSelection = on('review-clear-selection', () => {
    if (selectedId) selectComponent(null);
  });

  function postTheme() {
    if (iframe && iframe.contentWindow && themeDraft)
      iframe.contentWindow.postMessage(
          {type: 'a2ui-theme', theme: themeDraft}, '*');
  }
  const postThemeSoon = debounce(postTheme, 60);

  function drawThemePane() {
    if (!cur || view !== 'live') return;
    if (!themeDraft) {
      mount(themePaneEl, spinner('Loading theme\u2026'));
      return;
    }
    const comp = findComponent(cur.output.a2ui, selectedId);
    const fields = relatedThemeFields(comp);
    const dirtyEl =
        h('span.dirty#inspector-theme-dirty', {hidden: !themeDirty},
          h('span.dot'), 'Unsaved');
    const saveBtn =
        h('button.btn.btn-primary.btn-sm#inspector-theme-save', {
          type: 'button',
          disabled: !themeDirty,
          onclick: () => saveTheme(saveBtn),
        },
          'Save theme');
    const revertBtn =
        h('button.btn.btn-quiet.btn-sm#inspector-theme-revert', {
          type: 'button',
          disabled: !themeDirty,
          onclick: () => revertTheme(),
        },
          'Revert');
    const markDirty = () => {
      themeDirty = true;
      dirtyEl.hidden = false;
      saveBtn.disabled = false;
      revertBtn.disabled = false;
    };
    // Group rows under their token group, preserving relevance order.
    const groups = [];
    for (const fd of fields) {
      const g = themeGroupFor(fd.key);
      let bucket = groups.find((x) => x.g === g);
      if (!bucket) groups.push(bucket = {g, rows: []});
      bucket.rows.push(themeFieldControl(fd, themeDraft[fd.key], {
        idPrefix: 'insp-theme-',
        onChange: (v) => {
          themeDraft[fd.key] = v;
          markDirty();
          postThemeSoon();
        },
      }));
    }
    mount(
        themePaneEl,
        h('div.inspector-head', icon('palette'), 'Theme tokens',
          h('span.theme-pane-target',
            comp ? [h('strong', comp.component), ` \u00b7 ${comp.id}`] :
                   'Surface'),
          comp ? h('button.icon-btn#inspector-theme-clear', {
            type: 'button',
            title: 'Show surface tokens',
            'aria-label': 'Show surface tokens',
            onclick: () => selectComponent(null),
          },
                   icon('close')) :
                 null),
        h('div.theme-pane-body',
          h('p.help.theme-pane-help',
            comp ?
                `Tokens the ${comp.component} uses. ` :
                'Click a component in the render or tree to see its tokens. ',
            'Edits preview live and apply project-wide when saved.'),
          groups.map(
              ({g, rows}) =>
                  h('div.theme-pane-group',
                    h('div.theme-pane-group-title', g ? g.title : 'Tokens'),
                    h('div.theme-pane-fields', rows)))),
        h('div.theme-pane-foot', dirtyEl, h('div.grow'),
          h('a.btn.btn-quiet.btn-sm', {
            href: `/p/${pid}/design?tab=tokens`,
            title: 'Open the full theme editor',
          },
            'All tokens'),
          revertBtn, saveBtn));
  }

  function revertTheme() {
    themeDraft = cloneTheme(projectSettings && projectSettings.theme);
    themeDirty = false;
    postTheme();
    drawThemePane();
  }

  async function saveTheme(btn) {
    if (!projectSettings || !themeDraft) return;
    btn.disabled = true;
    try {
      const res = await put(`/api/projects/${pid}/settings`, {
        settings: {...projectSettings, theme: themeDraft},
        source: 'review-inspector',
      });
      projectSettings = res.settings || {...projectSettings, theme: themeDraft};
      themeDraft = cloneTheme(projectSettings.theme);
      themeDirty = false;
      if (store.project) store.project.settings = projectSettings;
      emit('settings-changed', {settings: projectSettings, source: 'review'});
      await loadRunMeta();
      drawRendererBar();
      drawPanel();
      toast(
          'Theme saved \u2014 click Apply & re-render to update screenshots',
          'good');
      drawThemePane();
    } catch (e) {
      btn.disabled = false;
    }
  }

  const offSettings =
      on('settings-changed', async ({settings: updated, source} = {}) => {
        if (source === 'review' || !updated) return;
        projectSettings = updated;
        await loadRunMeta();
        if (alive) {
          drawRendererBar();
          if (cur) drawPanel();
        }
        if (themeDirty) return;  // keep the user's in-progress edits
        themeDraft = cloneTheme(updated.theme);
        postTheme();
        drawThemePane();
      });

  function postHighlight(id, scroll = true) {
    if (iframe && iframe.contentWindow)
      iframe.contentWindow.postMessage(
          {type: 'a2ui-highlight', id, scroll}, '*');
    if (tree && scroll) tree.highlight(id, {scroll: false});
  }

  function drawPanel() {
    const {output: o, sample: s} = cur;
    const comp = currentSelectedComp();
    const pairId = o.pair_id || pairOf.get(o.id) || null;
    const j = o.judge;
    const errors = (o.validation && o.validation.errors) || [];
    const verdictBtn = (v, label, ic, key) =>
        h('button.verdict-btn', {
          type: 'button',
          id: `verdict-${v}`,
          class: [`vb-${v}`, o.human_verdict === v ? 'on' : ''].join(' '),
          'aria-pressed': String(o.human_verdict === v),
          title: `${label} (${key})`,
          onclick: () => setVerdict(v)
        },
          icon(ic), h('span', label), h('kbd', key));
    const note =
        h('textarea.input.note#verdict-note', {
          rows: 2,
          placeholder: comp ?
              `Note about ${comp.component} (#${
                  comp.id}) \u2014 what\u2019s wrong or right?` :
              'Note (optional) \u2014 what\u2019s wrong or right?',
          onchange: () => saveNote()
        },
          o.human_note || '');

    const pnotes = (j && j.platform_notes) || {};
    const pnoteEntries =
        RIDS.filter((rid) => pnotes[rid] && String(pnotes[rid]).trim());

    mount(
        rightEl,
        h('section.rp-section.rp-sample',
          h('div.rp-title-row',
            h('h2.rp-title', o.sample_title || (s && s.title)),
            statusChip(o.status)),
          staged && o.stage ?
              h('div.stage-row', stageChip(o),
                pairId ? h('button.link-btn#review-pair', {
                  type: 'button',
                  title: o.stage === 'form' ?
                      'Open the tool result generated from this form' :
                      'Open the input form for this result',
                  onclick: () => goToOutput(pairId)
                },
                           o.stage === 'form' ? 'View result \u2192' :
                                                '\u2190 View form') :
                         h('span.muted.small', 'no paired output')) :
              null,
          h('div.chip-row', genreChip(o.genre, o.group), chip(o.viewport),
            s ? chip(`size ${s.size_class}`) : null,
            s && s.state !== 'content' ? chip(s.state, `st-${s.state}`) : null,
            h('span.muted.small', `rev ${o.revision_number}`),
            runMeta ? h('span.chip.chip-xs.token-rev-chip', icon('palette'),
                        `Tokens T${
                            runMeta.rendered_token_revision_number ||
                            runMeta.token_revision_number || 1}`) :
                      null),
          s && s.intent ? h('p.intent', s.intent) : null,
          s ? h('details.payload#sample-payload',
                h('summary', 'Sample payload'),
                jsonTree(s.payload, {collapseDepth: 1})) :
              null),

        errors.length || o.error ?
            h('section.rp-section.rp-errors',
              h('h3.label.c-bad', icon('warning'),
                `Validation (${errors.length})`),
              o.error ? h('p.c-bad.small', o.error) : null,
              h('ul.errors',
                errors.map(
                    (e) =>
                        h('li', h('code', e.path || '/'), ' ', e.message)))) :
            null,

        h('section.rp-section.rp-verdict', h('h3.label', 'Your verdict'),
          comp ? h('div.rp-selected-comp#review-selected-component',
                   icon('ads_click'),
                   h('div.rp-selected-comp-main',
                     h('span.rp-selected-comp-label', 'Selected component'),
                     h('div.rp-selected-comp-id', h('code', comp.component),
                       h('span.muted', ` #${comp.id}`))),
                   h('button.btn.btn-xs.btn-ghost#review-comp-chat', {
                     type: 'button',
                     title: 'Discuss this component in chat (C)',
                     onclick: () => toChat(),
                   },
                     icon('forum'), 'Chat'),
                   h('button.btn.btn-xs.btn-ghost#review-comp-template', {
                     type: 'button',
                     title: 'Make a template from this example',
                     onclick: () => openPatternCapture({
                       pid,
                       output: o,
                       selection: {key: comp.id},
                       onSaved: () => emit('templates-changed'),
                     }),
                   },
                     icon('dashboard_customize'), 'Make a template from this'),
                   h('button.icon-btn#review-comp-clear', {
                     type: 'button',
                     title: 'Clear component selection (Esc)',
                     'aria-label': 'Clear component selection',
                     onclick: () => selectComponent(null),
                   },
                     icon('close'))) :
                 null,
          h('div.verdict-btns', verdictBtn('good', 'Good', 'thumb_up', 'G'),
            verdictBtn('mixed', 'Mixed', 'thumbs_up_down', 'M'),
            verdictBtn('bad', 'Bad', 'thumb_down', 'B')),
          note,
          o.reviewed_by ?
              h('p.muted.small',
                `Reviewed by ${o.reviewed_by} ${relTime(o.reviewed_at)}`) :
              null),

        h('section.rp-section.rp-actions',
          h('button.btn.btn-ghost.btn-sm#save-do', {
            type: 'button',
            title: 'Save as Do (O)',
            onclick: () => saveGuideline('do')
          },
            icon('check'), 'Save as Do'),
          h('button.btn.btn-ghost.btn-sm#save-dont', {
            type: 'button',
            title: 'Save as Don\u2019t (D)',
            onclick: () => saveGuideline('dont')
          },
            icon('block'), 'Save as Don\u2019t'),
          h('button.btn.btn-ghost.btn-sm#send-to-chat', {
            type: 'button',
            title: comp ?
                `Discuss ${comp.component} (#${comp.id}) in chat (C)` :
                'Send to chat (C)',
            onclick: () => toChat()
          },
            icon('forum'),
            comp ? `Chat about ${comp.component}` : 'Send to chat'),
          h('div.grow'),
          h(
              'button.btn.btn-quiet.btn-sm#rejudge', {
                type: 'button',
                title: 'Run the judge again',
                onclick: (e) => act('rejudge', e.currentTarget)
              },
              icon('gavel'), 'Re-judge'),
          h('button.btn.btn-quiet.btn-sm#regenerate', {
            type: 'button',
            title: 'Generate this output again with the same revision',
            onclick: (e) => act('regenerate', e.currentTarget)
          },
            icon('refresh'), 'Regenerate')),

        j ? h('section.rp-section.rp-judge#judge-panel',
              h('div.judge-head', h('h3.label', 'Judge'),
                scoreBadge(j.overall_score), judgeVerdictChip(j.verdict)),
              j.summary ? h('p.judge-summary', j.summary) : null,
              j.likely_unfixable_by_constitution ?
                  h('div.callout.callout-warn', icon('report'),
                    h('div',
                      h('strong', 'Likely not fixable by the constitution. '),
                      j.unfixable_reason || '')) :
                  null,
              pnoteEntries.length ?
                  h('div.judge-platform-notes', h('h4', 'Rendering notes'),
                    h('ul.plain',
                      pnoteEntries.map((rid) => h('li', pnotes[rid])))) :
                  null,
              (j.findings || []).length ?
                  h('ul.findings',
                    j.findings.map(
                        (fi) => h(
                            'li.finding',
                            {class: `f-${fi.status} sev-${fi.severity}`},
                            h('div.finding-head',
                              h('span.finding-status',
                                fi.status === 'followed'     ? icon('check') :
                                    fi.status === 'violated' ? icon('close') :
                                                               icon('remove')),
                              h('span.finding-rule', fi.rule),
                              fi.status === 'violated' ?
                                  h('span.sev', fi.severity) :
                                  null),
                            fi.evidence ? h('p.finding-ev', fi.evidence) :
                                          null))) :
                  null,
              (j.strengths || []).length || (j.issues || []).length ?
                  h('div.judge-lists',
                    (j.strengths || []).length ?
                        h('div', h('h4', 'Strengths'),
                          h('ul.plain', j.strengths.map((x) => h('li', x)))) :
                        null,
                    (j.issues || []).length ?
                        h('div', h('h4', 'Issues'),
                          h('ul.plain', j.issues.map((x) => h('li', x)))) :
                        null) :
                  null,
              (j.suggested_constitution_changes || []).length ?
                  h('div.judge-sugs', h('h4', 'Suggested constitution changes'),
                    h('ul.sug-list',
                      j.suggested_constitution_changes.map(
                          (x, i) => h(
                              'li', h('span', x),
                              h('button.btn.btn-sm.btn-quiet', {
                                type: 'button',
                                id: `discuss-suggestion-${i}`,
                                onclick: () => sendOutputToChat(
                                    o,
                                    `The judge suggested: \u201c${
                                        x}\u201d \u2014 should the constitution say this? Propose a change if so.`,
                                    currentSelectedComp())
                              },
                                icon('forum'), 'Discuss'))))) :
                  null) :
            h('section.rp-section.rp-judge', h('h3.label', 'Judge'),
              h('p.muted.small',
                o.status === 'done' ? 'Not judged (judge off for this run).' :
                                      'Waiting for the judge\u2026')),

        h('section.rp-section.rp-meta.muted.small', h('span', o.model || ''),
          o.latency_ms ? h('span', `${(o.latency_ms / 1000).toFixed(1)} s`) :
                         null,
          o.repairs ?
              h('span', `${o.repairs} repair${o.repairs > 1 ? 's' : ''}`) :
              null,
          o.render_meta && o.render_meta.overflowX ?
              h('span.c-bad', 'horizontal overflow') :
              null,
          o.render_meta && (o.render_meta.unsupported || []).length ?
              h('span.c-mixed',
                `unsupported: ${o.render_meta.unsupported.join(', ')}`) :
              null));
  }

  function formatComponentNote(rawNote, comp, hasVerdict = false) {
    const trimmed = String(rawNote || '').trim();
    if (!comp) return rawNote || '';
    const tag = `[${comp.component} #${comp.id}]`;
    if (trimmed.includes(tag) || trimmed.includes(`#${comp.id}`)) {
      return rawNote;
    }
    if (trimmed) return `${tag} ${trimmed}`;
    return hasVerdict ? tag : '';
  }

  async function setVerdict(v) {
    if (!cur) return;
    const o = cur.output;
    const comp = currentSelectedComp();
    const next = o.human_verdict === v ? null : v;
    const noteEl = document.getElementById('verdict-note');
    const rawNote = noteEl ? noteEl.value : (o.human_note || '');
    const humanNote = formatComponentNote(rawNote, comp, Boolean(next));
    const res = await post(`/api/outputs/${o.id}/verdict`, {
      human_verdict: next,
      human_note: humanNote,
      selected_component: comp ? {id: comp.id, component: comp.component} :
                                 null,
    });
    cur.output = {...o, ...res.output};
    list[idx] = {...list[idx], ...res.output};
    syncReviewSelection();
    drawPanel();
  }

  async function saveNote() {
    if (!cur) return;
    const o = cur.output;
    const comp = currentSelectedComp();
    const noteEl = document.getElementById('verdict-note');
    const humanNote =
        formatComponentNote(noteEl ? noteEl.value : '', comp, false);
    if (noteEl && humanNote !== noteEl.value) noteEl.value = humanNote;
    const res = await post(
        `/api/outputs/${o.id}/verdict`, {
          human_verdict: o.human_verdict || null,
          human_note: humanNote,
          selected_component: comp ? {id: comp.id, component: comp.component} :
                                     null,
        },
        {quiet: false});
    cur.output = {...o, ...res.output};
    syncReviewSelection();
    toast('Note saved', 'good', 1500);
  }

  async function act(kind, btn) {
    btn.disabled = true;
    try {
      const res = await post(`/api/outputs/${cur.output.id}/${kind}`);
      cur.output = {...cur.output, ...res.output};
      toast(
          kind === 'rejudge' ? 'Re-judging\u2026' : 'Regenerating\u2026',
          'info', 2000);
      await refreshCurrent();
      drawStage();
    } finally {
      btn.disabled = false;
    }
  }

  function toChat() {
    if (!cur) return;
    const o = cur.output;
    const comp = currentSelectedComp();
    let prefill = '';
    if (o.human_note) {
      prefill = comp ? `About \`${comp.component}\` (\`#${
                           comp.id}\`) in this output: ${o.human_note}` :
                       `About this output: ${o.human_note}`;
    }
    sendOutputToChat(o, prefill, comp);
  }

  function saveGuideline(kind) {
    if (!cur) return;
    const o = cur.output;
    const comp = currentSelectedComp();
    const j = o.judge || {};
    const title = h('input.input#guideline-title', {
      placeholder: comp ?
          (kind === 'do' ?
               `e.g. ${comp.component} keeps key details scannable` :
               `e.g. ${comp.component} crowds too many fields`) :
          (kind === 'do' ? 'e.g. Summary card first, details behind tap' :
                           'e.g. Dumps 20 meds as equal-weight cards'),
      autofocus: true
    });
    const compIntro =
        comp ? `Component \`${comp.component}\` (\`#${comp.id}\`):\n` : '';
    const defaultBody = kind === 'do' ?
        `**What**\n${compIntro}\n\n**Why it works**\n\n\n**When to use it**\n` :
        `**What**\n${compIntro}\n\n**Why it matters**\n\n\n**Instead**\n`;
    const bodyMd =
        h('textarea.input.code-input#guideline-body', {rows: 9}, defaultBody);
    const glRenderer = activeRenderer;
    const tags = h('input.input#guideline-tags', {
      placeholder: 'comma-separated, e.g. density, lists',
      value: [
        o.genre, o.viewport, comp ? comp.component : null
      ].filter(Boolean)
                 .join(', ')
    });
    let fixable = 'unsure';
    if (kind === 'dont' && j.likely_unfixable_by_constitution) fixable = 'no';
    const fixRadios = ['yes', 'no', 'unsure'].map(
        (v) =>
            h('label.radio', h('input', {
                type: 'radio',
                name: 'fixable',
                value: v,
                id: `fixable-${v}`,
                checked: v === fixable,
                onchange: () => {
                  fixable = v;
                }
              }),
              v === 'yes' ?
                  'Yes \u2014 a rule can fix it' :
                  v === 'no' ?
                  'No \u2014 needs a model, catalog or renderer change' :
                  'Not sure'));
    const preview = h('div.gl-shot');
    function updatePreviewShot() {
      const shot = outputShotForRenderer(o, glRenderer);
      const url = shot.thumb || shot.full || o.thumb_url || o.screenshot_url;
      mount(preview, url ? h('img', {src: url, alt: ''}) : null);
    }
    updatePreviewShot();

    modal({
      title: kind === 'do' ? 'Save as Do' : 'Save as Don\u2019t',
      wide: true,
      id: 'guideline-dialog',
      body: h(
          'div.gl-form', preview,
          h('div.gl-fields',
            comp ? h('div.rp-selected-comp', icon('ads_click'),
                     h('div.rp-selected-comp-main',
                       h('span.rp-selected-comp-label',
                         'Scoped to selected component'),
                       h('div.rp-selected-comp-id', h('code', comp.component),
                         h('span.muted', ` #${comp.id}`)))) :
                   null,
            h('label.field', h('span.field-label', 'Title'), title),
            h('label.field', h('span.field-label', 'Description (markdown)'),
              bodyMd),
            h('label.field', h('span.field-label', 'Tags'), tags),
            h('div.field',
              h('span.field-label', 'Fixable by the constitution?'),
              h('div.radio-col', fixRadios),
              kind === 'dont' && j.likely_unfixable_by_constitution ?
                  h('p.help.c-mixed', icon('report'),
                    ` Judge: ${
                        j.unfixable_reason ||
                        'likely not fixable by the constitution.'}`) :
                  null,
              kind === 'dont' ?
                  h('p.help',
                    'Unfixable Don\u2019ts go to the admin inbox for model and benchmark designers.') :
                  null))),
      actions: [
        {label: 'Cancel'}, {
          label: kind === 'do' ? 'Save Do' : 'Save Don\u2019t',
          kind: 'primary',
          id: 'guideline-save',
          onClick: async () => {
            if (!title.value.trim()) {
              toast('Add a short title.', 'warn');
              title.focus();
              return false;
            }
            await post(`/api/projects/${pid}/guidelines`, {
              kind,
              title: title.value.trim(),
              body_md: bodyMd.value,
              output_id: o.id,
              renderer: glRenderer,
              fixable: kind === 'do' ? 'yes' : fixable,
              tags: tags.value.split(',').map((t) => t.trim()).filter(Boolean),
            });
            toast(
                kind === 'do' ? 'Saved as Do' : 'Saved as Don\u2019t', 'good');
            emit('guidelines-changed');
          }
        }
      ],
    });
  }

  const unsavedTheme = (e) => {
    if (themeDirty) {
      e.preventDefault();
      e.returnValue = '';
    }
  };
  window.addEventListener('beforeunload', unsavedTheme);

  mount(stageEl, spinner('Loading outputs\u2026'));
  await Promise.all([loadList(), loadRunMeta()]);
  await loadOutput();

  return () => {
    alive = false;
    clearTimeout(pollTimer);
    store.reviewSelection = null;
    emit('review-selection-changed', null);
    window.removeEventListener('message', onMessage);
    document.removeEventListener('keydown', onKey);
    offSettings();
    offClearSelection();
    window.removeEventListener('beforeunload', unsavedTheme);
  };
}
