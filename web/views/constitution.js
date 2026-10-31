// Constitution editor: markdown editor + preview, scope check,
// history drawer, save as revision, and "edit then accept" from chat.
import {get, post, put} from '../components/api.js';
import {diffView} from '../components/diff.js';
import {download, fmtTime, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {renderMarkdown} from '../components/markdown.js';
import {openNewRunDialog} from '../components/newrun.js';
import {emit, navigate, on, store} from '../components/store.js';
import {confirmDialog, drawer, modal, promptDialog, segmented, toast} from '../components/ui.js';

import {openImportDrawer} from './import_drawer.js';

const HOME_LABEL = {
  theme: 'Theme tokens',
  catalog: 'Catalog',
  component: 'Component behavior'
};

export async function render(root, params, ctx) {
  const pid = params.pid;
  let project = ctx.project;
  let baseRev = null;  // {id, number, text_md, summary}
  let mode = localStorage.getItem('a2uid.editorMode') || 'split';
  let pendingEdit = null;
  const disposers = [];

  const editor = h('textarea.editor#constitution-editor', {
    spellcheck: 'true',
    placeholder:
        '# Constitution\n\nWrite the decisions the generator should make in context\u2026',
    oninput: () => onEdit(),
    onkeydown: (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        save();
      }
      if (e.key === 'Tab' && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        document.execCommand('insertText', false, '  ');
      }
    },
    onkeyup: () => updateStatus(),
    onclick: () => updateStatus(),
  });
  const preview = h('div.preview#constitution-preview');
  const statusEl = h('div.editor-status');
  const dirtyEl =
      h('span.dirty#dirty-indicator', {hidden: true}, h('span.dot'),
        'Unsaved changes');
  const revChip = h('span.chip.rev-chip#current-rev');
  const depsStrip = h('div.deps-strip#constitution-deps', {hidden: true});
  const banner = h('div.banner-slot');
  const editorWrap = h('div.editor-wrap');
  const body = h('div.constitution-body');
  let depsData = null;

  const saveBtn = h(
      'button.btn.btn-primary#save-revision',
      {type: 'button', onclick: () => save(), disabled: true}, 'Save revision');
  const modeSeg = segmented(
      [
        {value: 'edit', label: 'Write', id: 'mode-edit'},
        {value: 'split', label: 'Split', id: 'mode-split'},
        {value: 'preview', label: 'Preview', id: 'mode-preview'},
      ],
      mode, (v) => {
        mode = v;
        localStorage.setItem('a2uid.editorMode', v);
        layout();
      });
  // Effect compares with/without the saved constitution: pointless if empty.
  const effectBtn =
      h('button.btn.btn-ghost#open-effect', {
        type: 'button',
        title:
            'Generate with and without this constitution and see where it changes the layout',
        onclick: () => openNewRunDialog({projectId: pid, mode: 'effect'})
      },
        icon('difference'), 'Effect');

  root.appendChild(h(
      'div.page.page-constitution',
      h('div.page-head.page-head-tight',
        h('div.title-row', h('h1', 'Constitution'), revChip, dirtyEl),
        h('div.page-actions',
          h('button.btn.btn-ghost#open-references', {
            type: 'button',
            title:
                'Import Figma frames, Stitch screens or screenshots; they are analyzed before anything is merged',
            onclick: () => openImportDrawer(pid, {origin: 'constitution'})
          },
            icon('add_photo_alternate'), 'From reference'),
          h('button.btn.btn-ghost#open-history',
            {type: 'button', onclick: () => openHistory()}, icon('history'),
            'History'),
          effectBtn,
          h('button.btn.btn-ghost#scope-check', {
            type: 'button',
            title: 'Find rules that belong in Theme or Catalog instead',
            onclick: (e) => scopeCheck(e.currentTarget)
          },
            icon('rule'), 'Scope check'),
          h('button.btn.btn-ghost#teach-by-example', {
            type: 'button',
            title:
                'Teaching session: point at real cards and their input data; rules are distilled, tried and tracked',
            onclick: () => navigate(`/p/${pid}/teach`)
          },
            icon('school'), 'Teach by example'),
          modeSeg, saveBtn)),
      depsStrip, banner, body));

  const unsaved = (e) => {
    if (isDirty()) {
      e.preventDefault();
      e.returnValue = '';
    }
  };
  window.addEventListener('beforeunload', unsaved);
  disposers.push(() => window.removeEventListener('beforeunload', unsaved));
  store.draftProvider = () => (isDirty() ? editor.value : null);
  disposers.push(on('pending-edit', (pe) => applyPendingEdit(pe)));
  disposers.push(on('settings-changed', () => loadDependencies()));
  disposers.push(on('components-changed', () => loadDependencies()));
  disposers.push(on('revision-changed', async ({revision}) => {
    if (pendingEdit) return;
    if (!isDirty()) {
      await loadCurrent();
    } else {
      showBanner(
          'warn',
          `Rev ${revision.number} was created while you have unsaved edits.`,
          h('button.btn.btn-sm.btn-ghost', {
            type: 'button',
            onclick: async () => {
              clearDraft();
              await loadCurrent();
            }
          },
            'Discard my edits and load it'));
    }
  }));

  function draftKey() {
    return `a2uid.draft.${pid}`;
  }
  function saveDraft() {
    if (isDirty() && !pendingEdit)
      localStorage.setItem(
          draftKey(),
          JSON.stringify({base_id: baseRev && baseRev.id, text: editor.value}));
    else
      localStorage.removeItem(draftKey());
  }
  function clearDraft() {
    localStorage.removeItem(draftKey());
  }

  function isDirty() {
    return baseRev != null && editor.value !== (baseRev.text_md || '');
  }

  let previewTimer = null;
  function onEdit() {
    const d = isDirty() || !!pendingEdit;
    dirtyEl.hidden = !isDirty();
    saveBtn.disabled = !d;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => {
      renderPreview();
      saveDraft();
    }, 180);
    updateStatus();
    const shouldEmpty = !editor.value.trim() && !started && !pendingEdit;
    if (shouldEmpty !== bodyIsEmptyState) layout();
  }

  function updateStatus() {
    const v = editor.value;
    const words = (v.match(/\S+/g) || []).length;
    const pos = editor.selectionStart || 0;
    const line = v.slice(0, pos).split('\n').length;
    const sections = (v.match(/^#{1,3}\s/gm) || []).length;
    mount(
        statusEl, h('span', `${words} words`),
        h('span', `${sections} section${sections === 1 ? '' : 's'}`),
        h('span', `Ln ${line}`),
        h('span.status-hint',
          'Decisions in context \u2014 not styling. \u2318S to save.'));
  }

  function renderPreview() {
    mount(
        preview,
        editor.value.trim() ?
            renderMarkdown(editor.value, {className: 'md md-doc'}) :
            h('p.muted', 'Nothing to preview yet.'));
  }

  function showBanner(kind, text, ...actions) {
    mount(
        banner,
        h('div.banner', {class: `banner-${kind}`},
          icon(
              kind === 'warn'     ? 'warning' :
                  kind === 'chat' ? 'forum' :
                                    'info'),
          h('span.banner-text', text), h('div.banner-actions', actions)));
  }
  function clearBanner() {
    mount(banner);
  }

  let bodyIsEmptyState = false;
  let started = false;  // user clicked "Start writing" on the empty state
  function layout() {
    const empty = !editor.value.trim() && !started && !pendingEdit;
    bodyIsEmptyState = empty;
    // display:none (not visibility) so no gap is left before Save revision.
    modeSeg.style.display = empty ? 'none' : '';
    syncEffect();
    if (empty) {
      depsStrip.hidden = true;
      mount(body, emptyStateView());
      body.className = 'constitution-body is-empty';
      return;
    }
    drawDepsStrip();
    body.className = `constitution-body mode-${mode}`;
    mount(
        editorWrap,
        mode !== 'preview' ? h('div.editor-pane', editor, statusEl) : null,
        mode !== 'edit' ? h('div.preview-pane', preview) : null);
    mount(body, editorWrap);
    renderPreview();
    updateStatus();
  }

  function syncEffect() {
    effectBtn.style.display =
        ((baseRev && baseRev.text_md) || '').trim() ? '' : 'none';
  }

  function emptyStateView() {
    return h(
        'div.const-empty#constitution-empty',
        h('div.const-empty-main', h('div.eyebrow', 'Empty constitution'),
          h('h2.const-empty-title', 'Tell the generator how to decide.'),
          h('p.const-empty-lede',
            'A constitution is the set of judgment calls a UI generator should make ',
            h('em', 'in context'),
            ', for whatever data arrives. Good ones are short, specific, and grounded in outputs you\u2019ve actually seen.'),
          h('div.const-empty-cols',
            h('div', h('h3', 'What it covers'),
              h('ul.ticks',
                h('li', 'Which components and templates fit which data shapes'),
                h('li', 'Hierarchy: what the person needs first'),
                h('li', 'Density per viewport, and when to summarize or page'),
                h('li',
                  'Progressive disclosure \u2014 what waits behind a tap'),
                h('li', 'When to ask, act, or punt'),
                h('li', 'Loading, empty, error and edge-case states'))),
            h('div', h('h3', 'What it doesn\u2019t'),
              h('ul.crosses',
                h('li', 'Colors, type, radii, spacing \u2014 those are ',
                  h('a', {href: `/p/${pid}/design?tab=tokens`},
                    'Theme tokens')),
                h('li',
                  'Which components exist and their props \u2014 that\u2019s the ',
                  h('a', {href: `/p/${pid}/design?tab=components`},
                    'Catalog'))),
              h('p.help',
                'Keeping styling out means the constitution spends every word on decisions.'))),
          h('div.const-empty-actions',
            h('button.btn.btn-primary.btn-lg#open-wizard',
              {type: 'button', onclick: () => navigate(`/p/${pid}/teach`)},
              icon('school'), 'Teach by example'),
            h('button.btn.btn-ghost.btn-lg#run-baseline', {
              type: 'button',
              onclick: () => navigate(`/p/${pid}/runs?new=baseline`)
            },
              icon('play_arrow'), 'Run a baseline'),
            h('button.btn.btn-ghost.btn-lg#start-writing', {
              type: 'button',
              onclick: () => {
                started = true;
                layout();
                editor.focus();
              }
            },
              icon('edit'), 'Start writing'),
            h('button.btn.btn-quiet.btn-lg#empty-open-chat',
              {type: 'button', onclick: () => emit('chat-open', {text: ''})},
              icon('forum'), 'Draft with chat')),
          h('p.help',
            'Guided setup renders 5 starter cards right away so you can point out what looks good or needs work, then drafts your constitution and theme tokens from your feedback.')));
  }

  // ---------- Dependencies ----------
  async function loadDependencies() {
    try {
      depsData = await get(`/api/projects/${pid}/dependencies`, {quiet: true});
      drawDepsStrip();
    } catch (_) { /* ignore */
    }
  }

  async function enableBasicComponent(compName) {
    const {settings} = await get(`/api/projects/${pid}/settings`);
    const next = {...(settings || {})};
    next.catalog = {...(next.catalog || {})};
    next.catalog.disabled_components =
        (next.catalog.disabled_components || []).filter((x) => x !== compName);
    const res = await put(
        `/api/projects/${pid}/settings`,
        {settings: next, source: 'constitution-deps'});
    if (store.project && res.settings) store.project.settings = res.settings;
    emit('settings-changed', {
      settings: res.settings || next,
      source: 'constitution-deps',
    });
    toast(`Enabled ${compName} in catalog`, 'good');
    await loadDependencies();
  }

  function drawDepsStrip() {
    if (bodyIsEmptyState || !depsData) {
      depsStrip.hidden = true;
      return;
    }
    depsStrip.hidden = false;
    const cdeps = depsData.constitution_deps || {};
    const snap = depsData.snapshot || {};
    const bound = depsData.bound_snapshot || {};
    const drift = depsData.revision_drift || {};
    const issues = depsData.issues || [];

    const comps = cdeps.components || [];
    const tpls = cdeps.templates || [];
    const toks = cdeps.tokens || [];
    const imgs = cdeps.imagery || [];

    const tokNum = snap.token_revision_number || 1;
    const boundTokNum = bound.token_revision_number || tokNum;
    const tokLabel = drift.token_drifted && boundTokNum !== tokNum ?
        `Tokens T${boundTokNum} \u2192 T${tokNum}` :
        `Tokens T${tokNum}`;

    const pills = [
      h('a.deps-pill.deps-pill-tokens', {
        href: `/p/${pid}/design?tab=tokens`,
        title: drift.token_drifted ?
            `Saved against T${boundTokNum}; live tokens are now T${tokNum}` :
            `Current design tokens revision T${tokNum}`,
      },
        icon('palette'), h('span.deps-pill-name', tokLabel)),
      ...comps.map(
          (c) =>
              h('a.deps-pill', {
                href: c.id ? `/p/${pid}/design/components/${c.id}` :
                             `/p/${pid}/design?tab=components`,
                class: `st-${c.status || 'active'}`,
                title:
                    `${c.kind === 'custom' ? 'Custom' : 'Basic'} component (${
                        c.status}) \u00b7 Referenced in ${
                        (c.rules || []).join(', ')}`,
              },
                h('span.deps-dot', {class: `st-${c.status || 'active'}`}),
                icon('widgets'), h('span.deps-pill-name', c.name),
                (c.rules || []).length ?
                    h('span.deps-rules', c.rules.slice(0, 2).join(',')) :
                    null)),
      ...tpls.map(
          (t) =>
              h('a.deps-pill', {
                href: `/p/${pid}/design?tab=templates`,
                class: `st-${t.status || 'active'}`,
                title: `Template (${t.status}) \u00b7 Referenced in ${
                    (t.rules || []).join(', ')}`,
              },
                h('span.deps-dot', {class: `st-${t.status || 'active'}`}),
                icon('dashboard'), h('span.deps-pill-name', t.name),
                (t.rules || []).length ?
                    h('span.deps-rules', t.rules.slice(0, 2).join(',')) :
                    null)),
      ...toks.map(
          (tk) =>
              h('a.deps-pill', {
                href: `/p/${pid}/design?tab=tokens`,
                class: `st-${tk.status === 'defined' ? 'active' : 'missing'}`,
                title: `Token ${tk.css_var || tk.token} (${
                    tk.status}) \u00b7 Referenced in ${
                    (tk.rules || []).join(', ')}`,
              },
                h('span.deps-dot', {
                  class: `st-${tk.status === 'defined' ? 'active' : 'missing'}`
                }),
                h('code.deps-pill-name', tk.css_var || tk.token),
                (tk.rules || []).length ?
                    h('span.deps-rules', tk.rules.slice(0, 2).join(',')) :
                    null)),
      ...imgs.map(
          (im) =>
              h('a.deps-pill', {
                href: `/p/${pid}/design?tab=imagery`,
                class: `st-${im.status || 'active'}`,
                title: `Imagery style (${im.status}) \u00b7 Referenced in ${
                    (im.rules || []).join(', ')}`,
              },
                h('span.deps-dot', {class: `st-${im.status || 'active'}`}),
                icon('image'), h('span.deps-pill-name', `style=${im.slug}`),
                (im.rules || []).length ?
                    h('span.deps-rules', im.rules.slice(0, 2).join(',')) :
                    null)),
    ];

    const hasRefs = comps.length || tpls.length || toks.length || imgs.length;
    const summaryFallback = !hasRefs ?
        h('span.muted.small.deps-summary',
          `Catalog: ${(snap.enabled_basic || []).length} basic, ${
              (snap.active_custom || []).length} custom \u00b7 ${
              (snap.active_templates || []).length} templates`) :
        null;

    mount(
        depsStrip,
        h('div.deps-strip-row',
          h('span.deps-label', icon('account_tree'), 'Dependencies'),
          h('div.deps-pills', pills, summaryFallback)),
        issues.length ? h('div.deps-issues', issues.map((iss) => {
          const canEnableBasic =
              (iss.kind === 'constitution_disabled_component' ||
               iss.kind === 'template_disabled_component' ||
               iss.kind === 'constitution_disabled_basic_component' ||
               iss.kind === 'template_disabled_basic_component') &&
              iss.target;
          return h(
              'div.deps-issue-item', {class: `lvl-${iss.level || 'warn'}`},
              icon(iss.level === 'error' ? 'error' : 'warning'),
              h('span.deps-issue-msg', iss.message),
              canEnableBasic ?
                  h('button.btn.btn-xs.btn-ghost', {
                    type: 'button',
                    onclick: () => enableBasicComponent(iss.target),
                  },
                    `Enable ${iss.target}`) :
                  iss.component_id ?
                  h('a.btn.btn-xs.btn-ghost', {
                    href: `/p/${pid}/design/components/${iss.component_id}`,
                  },
                    'Open component') :
                  h('a.btn.btn-xs.btn-ghost', {
                    href: `/p/${pid}/design?tab=${iss.layer || 'components'}`,
                  },
                    'Open Design system'));
        })) :
                        null);
  }

  // ---------- Load / save ----------
  async function loadCurrent() {
    const cur = project.current_revision;
    if (!cur) {
      baseRev = {id: null, number: 0, text_md: ''};
    } else {
      const {revision} = await get(`/api/projects/${pid}/revisions/${cur.id}`);
      baseRev = revision;
    }
    editor.value = baseRev.text_md || '';
    revChip.textContent = `rev ${baseRev.number}`;
    revChip.title = baseRev.summary || '';
    if (!pendingEdit) saveBtn.textContent = 'Save revision';
    clearBanner();
    onEdit();
    layout();
    loadDependencies();
  }

  function applyPendingEdit(pe) {
    if (!pe) return;
    pendingEdit = pe;
    store.pendingEdit = null;
    editor.value = pe.constitution_md || '';
    started = true;
    if (mode === 'preview') {
      mode = 'split';
      modeSeg.set('split');
    }
    const next =
        (project.current_revision ? project.current_revision.number : 0) + 1;
    showBanner(
        'chat',
        `Editing a chat proposal${
            pe.change_summary ? `: \u201c${pe.change_summary}\u201d` :
                                ''}. Saving accepts it as rev ${next}.`,
        h('button.btn.btn-sm.btn-ghost#cancel-pending-edit', {
          type: 'button',
          onclick: async () => {
            pendingEdit = null;
            await loadCurrent();
          }
        },
          'Cancel'));
    saveBtn.textContent = `Accept as rev ${next}`;
    layout();
    onEdit();
    editor.focus();
  }

  async function save() {
    if (pendingEdit) {
      saveBtn.disabled = true;
      try {
        const res = await post(
            `/api/projects/${pid}/chat/${pendingEdit.message_id}/accept`,
            {constitution_md: editor.value});
        const revision = res.revision;
        pendingEdit = null;
        saveBtn.textContent = 'Save revision';
        clearDraft();
        project.current_revision = revision;
        if (res.settings && store.project)
          store.project.settings = res.settings;
        toast(`Accepted as rev ${revision.number}`, 'good');
        emit('revision-changed', {revision, source: 'chat'});
        if (res.theme_changes_applied?.length || res.settings) {
          emit('settings-changed', {settings: res.settings, source: 'chat'});
        }
        ctx.chat.load();
        await loadCurrent();
      } finally {
        onEdit();
      }
      return;
    }
    if (!isDirty()) return;
    const summary = await promptDialog('Save revision', {
      label: 'What changed?',
      placeholder: 'e.g. Summarize lists beyond 5 items on phone',
      confirmLabel: 'Save revision',
      help: `Becomes rev ${
          (baseRev.number ?? 0) +
          1}. Summaries show up in History and exports.`,
    });
    if (summary == null) return;
    saveBtn.disabled = true;
    try {
      const {revision} = await post(
          `/api/projects/${pid}/revisions`,
          {text_md: editor.value, summary: summary.trim() || 'Manual edit'});
      clearDraft();
      project.current_revision = revision;
      baseRev = {...revision, text_md: editor.value};
      syncEffect();
      revChip.textContent = `rev ${revision.number}`;
      toast(`Saved rev ${revision.number}`, 'good');
      emit('revision-changed', {revision, source: 'manual'});
      loadDependencies();
    } finally {
      onEdit();
    }
  }

  // ---------- Scope check ----------
  async function scopeCheck(btn) {
    if (!editor.value.trim()) {
      toast('Nothing to check yet \u2014 the constitution is empty.', 'info');
      return;
    }
    btn.disabled = true;
    const orig = btn.innerHTML;
    btn.replaceChildren(h('span.spinner'), 'Checking\u2026');
    try {
      const res = await post(
          `/api/projects/${pid}/scope_check`, {text_md: editor.value});
      showScopeResults(res);
    } finally {
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  }

  function showScopeResults(res) {
    const items = res.items || [];
    const list = items.length ?
        h('ol.scope-list#scope-results',
          items.map(
              (it, i) => h(
                  'li.scope-item',
                  h('blockquote.scope-excerpt', it.rule_excerpt),
                  h('p', it.why || it.why_out_of_scope || ''),
                  h('div.scope-actions',
                    h('span.chip.home',
                      `Better in ${
                          HOME_LABEL[it.suggested_home] || it.suggested_home}`),
                    h('button.btn.btn-sm.btn-quiet', {
                      type: 'button',
                      id: `scope-find-${i}`,
                      onclick: () => findInEditor(it.rule_excerpt)
                    },
                      icon('search'), 'Find'),
                    it.suggested_theme_change ?
                        h('button.btn.btn-sm.btn-ghost', {
                          type: 'button',
                          id: `scope-apply-${i}`,
                          onclick: async (e) => {
                            e.currentTarget.disabled = true;
                            await applyThemeChange(it.suggested_theme_change);
                            e.currentTarget.replaceChildren(
                                icon('check'), 'Applied');
                          }
                        },
                          icon('palette'),
                          `Set theme ${it.suggested_theme_change.token} = ${
                              it.suggested_theme_change.value}`) :
                        null)))) :
        h('div.scope-clean', icon('check_circle'),
          h('div', h('strong', 'In scope.'),
            h('p.muted',
              'Every rule is about decisions in context. Nothing needs to move to Theme or Catalog.')));
    modal({
      title: 'Scope check',
      wide: true,
      id: 'scope-dialog',
      body: [
        res.summary ? h('p.lede', res.summary) : null, list,
        h('p.help',
          'Advisory only \u2014 nothing is changed unless you apply it. Remove moved rules from the constitution yourself.')
      ],
      actions: [{label: 'Done', kind: 'primary'}]
    });
  }

  function findInEditor(excerpt) {
    if (mode === 'preview') {
      mode = 'split';
      modeSeg.set('split');
      layout();
    }
    const v = editor.value;
    let idx = v.indexOf(excerpt);
    if (idx < 0) {
      const probe = String(excerpt).replace(/[\u2026.]+$/, '').slice(0, 40);
      idx = probe ? v.indexOf(probe) : -1;
      if (idx >= 0) excerpt = probe;
    }
    if (idx < 0) {
      toast('Couldn\u2019t find that exact text in the editor.', 'warn');
      return;
    }
    document.querySelector('.overlay')?.remove();
    editor.focus();
    editor.setSelectionRange(idx, idx + excerpt.length);
    const lines = v.slice(0, idx).split('\n').length;
    editor.scrollTop = Math.max(0, (lines - 4) * 22);
  }

  async function applyThemeChange(change) {
    const {settings} = await get(`/api/projects/${pid}/settings`);
    settings.theme = {...(settings.theme || {}), [change.token]: change.value};
    const res = await put(`/api/projects/${pid}/settings`, {settings});
    if (store.project && res.settings) store.project.settings = res.settings;
    emit(
        'settings-changed',
        {settings: res.settings || settings, source: 'scope_check'});
    toast(`Theme ${change.token} set to ${change.value}`, 'good');
  }

  // ---------- History ----------
  async function openHistory() {
    const d = drawer({
      title: 'Revision history',
      wide: true,
      id: 'history-drawer',
      body: spinner('Loading\u2026')
    });
    const {revisions} = await get(`/api/projects/${pid}/revisions`);
    const listEl = h('ol.rev-list#revision-list');
    const detail = h('div.rev-detail');
    const curId = project.current_revision && project.current_revision.id;
    mount(
        listEl,
        revisions.map(
            (r) => h(
                'li',
                h('button.rev-item',
                  {
                    type: 'button',
                    id: `rev-${r.id}`,
                    class: r.id === curId ? 'current' : '',
                    onclick: (e) => {
                      listEl.querySelectorAll('.rev-item')
                          .forEach((b) => b.classList.remove('on'));
                      e.currentTarget.classList.add('on');
                      showRevision(r);
                    }
                  },
                  h('span.rev-num', `${r.number}`),
                  h('span.rev-main',
                    h('span.rev-summary',
                      r.summary ||
                          (r.number === 0 ? 'Project created' :
                                            'Untitled change')),
                    h('span.rev-meta',
                      `${r.source} \u00b7 ${r.author_ldap || ''} \u00b7 ${
                          relTime(r.created_at)}${
                          r.ds_snapshot && r.ds_snapshot.token_revision_number ?
                              ` \u00b7 Tokens T${
                                  r.ds_snapshot.token_revision_number}` :
                              ''}`)),
                  r.id === curId ? h('span.chip.c-current', 'current') :
                                   null))));
    mount(d.body, h('div.history', listEl, detail));
    mount(
        detail,
        h('p.muted.rev-pick',
          'Pick a revision to see its text and how it differs from the current one.'));

    async function showRevision(r) {
      mount(detail, spinner('Loading revision\u2026'));
      const [{revision}, diffRes] = await Promise.all([
        get(`/api/projects/${pid}/revisions/${r.id}`),
        r.id !== curId && curId ?
            get(`/api/projects/${pid}/diff?a=${r.id}&b=${curId}`) :
            Promise.resolve(null),
      ]);
      let view = diffRes ? 'diff' : 'text';
      const content = h('div.rev-content');
      const draw = () => mount(
          content,
          view === 'diff' && diffRes ?
              diffView(diffRes.diff, {
                aLabel: `rev ${r.number}`,
                bLabel: `rev ${project.current_revision.number} (current)`
              }) :
              (revision.text_md || '').trim() ?
              renderMarkdown(revision.text_md, {className: 'md md-doc'}) :
              h('p.muted', 'Empty constitution.'));
      mount(
          detail,
          h('div.rev-detail-head',
            h('div',
              h('h3', `Rev ${r.number}`,
                r.id === curId ? h('span.chip.c-current', 'current') : null,
                revision.ds_snapshot &&
                        revision.ds_snapshot.token_revision_number ?
                    h('span.chip.chip-xs.token-rev-chip', icon('palette'),
                      `Tokens T${revision.ds_snapshot.token_revision_number}`) :
                    null),
              h('p.muted',
                `${r.summary || ''} \u2014 ${fmtTime(r.created_at)}`)),
            h('div.rev-detail-actions',
              diffRes ?
                  segmented(
                      [
                        {
                          value: 'diff',
                          label: 'Diff vs current',
                          id: 'rev-view-diff'
                        },
                        {value: 'text', label: 'Text', id: 'rev-view-text'}
                      ],
                      view,
                      (v) => {
                        view = v;
                        draw();
                      }) :
                  null,
              h('button.btn.btn-sm.btn-quiet', {
                type: 'button',
                id: 'rev-download',
                onclick: () => download(
                    `/api/export/projects/${pid}/constitution.md?rev=${r.id}`)
              },
                icon('download'), '.md'),
              r.id !== curId ? h('button.btn.btn-sm.btn-ghost#revert-revision', {
                type: 'button',
                onclick: async () => {
                  if (isDirty() &&
                      !(await confirmDialog(
                          'Discard unsaved edits?',
                          'Reverting creates a new revision and replaces the editor contents.',
                          {confirmLabel: 'Revert', danger: true})))
                    return;
                  if (!isDirty() &&
                      !(await confirmDialog(
                          `Revert to rev ${r.number}?`,
                          `This creates a new revision with rev ${
                              r.number}\u2019s text. Nothing is deleted.`,
                          {confirmLabel: 'Revert'})))
                    return;
                  const {revision: nr} = await post(
                      `/api/projects/${pid}/revisions/${r.id}/revert`);
                  project.current_revision = nr;
                  clearDraft();
                  toast(`Reverted \u2014 now rev ${nr.number}`, 'good');
                  d.close();
                  emit('revision-changed', {revision: nr, source: 'revert'});
                  await loadCurrent();
                }
              },
                                 icon('undo'), 'Revert to this') :
                               null)),
          content);
      draw();
    }
  }

  // ---------- init ----------
  layout();
  await loadCurrent();
  // Restore unsaved draft if any.
  try {
    const saved = JSON.parse(localStorage.getItem(draftKey()) || 'null');
    if (saved && saved.text != null && saved.text !== (baseRev.text_md || '')) {
      if (saved.base_id === baseRev.id) {
        editor.value = saved.text;
        started = true;
        layout();
        onEdit();
        showBanner(
            'info', 'Restored your unsaved draft.',
            h('button.btn.btn-sm.btn-quiet#discard-draft', {
              type: 'button',
              onclick: async () => {
                clearDraft();
                await loadCurrent();
              }
            },
              'Discard draft'));
      } else {
        clearDraft();
      }
    }
  } catch (_) { /* ignore */
  }
  if (store.pendingEdit) applyPendingEdit(store.pendingEdit);

  return () => {
    saveDraft();
    disposers.forEach((f) => f());
    store.draftProvider = null;
  };
}
