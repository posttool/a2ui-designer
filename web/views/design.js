// Design system page: custom components (drafts + active) and the Basic
// catalog, theme tokens, templates and imagery. The downloadable design
// package lives on its own page (views/package.js, /p/:pid/package).
// Route: /p/:pid/design?tab=tokens|components|templates|imagery
// (default: tokens; the old ?tab=package redirects to /p/:pid/package)
import {del, get, patch, post, put} from '../components/api.js';
import {activateComponent, basicFitChip, buildFromProposal, fidelityChip, issuesBadge, jobActive, jobProgress, needsBuild, pollComponent, proposeComponentFixModal, sourceBadge, strategyLabel, tokenSuggestionList, tokenUsage, warningsBadge, webOnlyChip} from '../components/component_badges.js';
import {download, emptyState, fmtTime, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {jsonTree} from '../components/jsontree.js';
import {emit, navigate, on, store} from '../components/store.js';
import {mountThemeEditor, saveThemeToProject} from '../components/theme_editor.js';
import {confirmDialog, drawer, segmented, toast} from '../components/ui.js';
import {ensureThemeFonts} from '../renderer/a2ui.js';

import {createImageryTab} from './imagery_editor.js';
import {openImportDrawer} from './import_drawer.js';
import {createTemplatesTab} from './templates_tab.js';

// Tab order is also the landing order: Tokens first, then Components,
// Templates, Imagery & Icons.
const TABS = [
  {value: 'tokens', label: 'Tokens', icon: 'palette', id: 'design-tab-tokens'},
  {
    value: 'components',
    label: 'Components',
    icon: 'widgets',
    id: 'design-tab-components'
  },
  {
    value: 'templates',
    label: 'Templates',
    icon: 'dashboard',
    id: 'design-tab-templates'
  },
  {
    value: 'imagery',
    label: 'Imagery & Icons',
    icon: 'add_photo_alternate',
    id: 'design-tab-imagery'
  },
];
const DEFAULT_TAB = TABS[0].value;

export async function render(root, params, ctx) {
  const pid = params.pid;
  const url = (ctx && ctx.url) || new URL(location.href);
  if (url.searchParams.get('tab') === 'package') {
    navigate(`/p/${pid}/package`, {replace: true});
    return () => {};
  }
  let tab = TABS.some((t) => t.value === url.searchParams.get('tab')) ?
      url.searchParams.get('tab') :
      DEFAULT_TAB;
  let alive = true;
  const disposers = [];
  const stops = new Map();  // cid → stop polling

  // Shared data.
  let components = [];
  let catalogId = '';
  let settings = null;
  let depsData = null;

  const panes = {
    components: h('div.design-pane#design-pane-components'),
    templates: h('div.design-pane#design-pane-templates'),
    tokens: h('div.design-pane#design-pane-tokens'),
    imagery: h('div.design-pane#design-pane-imagery'),
  };
  const imageryTab = createImageryTab(pid);
  const templatesTab = createTemplatesTab(pid, {
    onCount: (n) => seg.setCounts({templates: n}),
  });
  const mounted = {};
  const headActions = h('div.page-actions.design-head-actions');

  const seg = segmented(TABS, tab, (v) => setTab(v), {id: 'design-tabs'});
  const pStats =
      (store.project && store.project.id === pid && store.project.stats) || {};
  seg.setCounts({
    components: pStats.components || 0,
    templates: pStats.templates || 0,
    imagery: pStats.imagery_styles || 0,
  });
  root.appendChild(
      h('div.page.page-design',
        h('div.page-head.page-head-tight.design-head',
          h('div.title-row', h('h1', 'Design system'), seg), headActions),
        panes.tokens, panes.components, panes.templates, panes.imagery));

  function updateTabCounts(extra = {}) {
    const compCount = components.filter((c) => c.status !== 'archived').length;
    seg.setCounts({
      ...(components.length || mounted.components ? {components: compCount} :
                                                    {}),
      ...extra,
    });
  }

  async function loadComponents() {
    const res = await get(`/api/projects/${pid}/components`);
    components = res.components || [];
    catalogId = res.catalog_id || catalogId;
    updateTabCounts({
      components: components.filter((c) => c.status !== 'archived').length,
    });
    return components;
  }
  async function refreshCountsQuiet() {
    try {
      const [cRes, tRes] = await Promise.all([
        mounted.components ?
            null :
            get(`/api/projects/${pid}/components`, {quiet: true}),
        mounted.templates ?
            null :
            get(`/api/projects/${pid}/templates`, {quiet: true}),
      ]);
      if (!alive) return;
      const next = {};
      if (cRes && Array.isArray(cRes.components)) {
        if (!mounted.components) components = cRes.components;
        next.components =
            cRes.components.filter((c) => c.status !== 'archived').length;
      }
      if (tRes && Array.isArray(tRes.templates)) {
        next.templates =
            tRes.templates.filter((t) => (t.status || 'active') !== 'archived')
                .length;
      }
      if (Object.keys(next).length) seg.setCounts(next);
    } catch (_) { /* ignore */
    }
  }
  async function loadSettings() {
    const res = await get(`/api/projects/${pid}/settings`);
    settings = res.settings || {};
    if (settings.theme) {
      try {
        ensureThemeFonts(settings.theme, document);
      } catch (_) {
      }
    }
    return settings;
  }
  async function loadDependencies() {
    try {
      depsData = await get(`/api/projects/${pid}/dependencies`, {quiet: true});
    } catch (_) { /* ignore */
    }
    return depsData;
  }
  function compUsedBy(compName) {
    const u = depsData && depsData.used_by && depsData.used_by.components &&
        depsData.used_by.components[compName];
    return {
      rules: (u && u.constitution_rules) || [],
      templates: (u && u.templates) || [],
    };
  }
  function compUsedByBadge(compName) {
    const {rules, templates: tpls} = compUsedBy(compName);
    if (!rules.length && !tpls.length) return null;
    const parts = [];
    if (rules.length) parts.push(rules.slice(0, 2).join(', '));
    if (tpls.length) {
      parts.push(`${tpls.length} template${tpls.length === 1 ? '' : 's'}`);
    }
    const titleParts = [];
    if (rules.length) {
      titleParts.push(`Constitution rules: ${rules.join(', ')}`);
    }
    if (tpls.length) {
      titleParts.push(`Templates: ${tpls.join(', ')}`);
    }
    return h(
        'span.chip.chip-xs.deps-usedby-chip',
        {title: `Used in ${titleParts.join(' \u00b7 ')}`}, icon('account_tree'),
        `Used in ${parts.join(' \u00b7 ')}`);
  }

  function setTab(v) {
    if (tokens.dirty && tab === 'tokens' && v !== 'tokens') {
      // Keep the edits (the pane stays mounted), just remind.
      toast(
          'Token edits are unsaved \u2014 they\u2019re kept on the Tokens tab.',
          'warn');
    }
    tab = v;
    seg.set(v);
    const u = new URL(location.href);
    u.searchParams.set('tab', v);
    history.replaceState(null, '', u.pathname + u.search);
    showTab();
  }

  async function showTab() {
    for (const [k, el] of Object.entries(panes)) el.hidden = k !== tab;
    mount(headActions);
    if (tab === 'components') {
      mount(headActions, componentsTab.actions());
      if (!mounted.components) {
        mounted.components = true;
        await componentsTab.mount();
      }
    } else if (tab === 'tokens') {
      mount(headActions, tokens.actions());
      if (!mounted.tokens) {
        mounted.tokens = true;
        await tokens.mount();
      } else {
        tokens.refreshUsage();
      }
    } else if (tab === 'templates') {
      mount(headActions, templatesTab.actions());
      if (!mounted.templates) {
        mounted.templates = true;
        await templatesTab.mount(panes.templates);
      } else {
        templatesTab.reload();
      }
    } else if (tab === 'imagery') {
      mount(headActions, imageryTab.actions());
      if (!mounted.imagery) {
        mounted.imagery = true;
        await imageryTab.mount(panes.imagery);
      }
    }
  }

  // ======================================================= Components tab
  const componentsTab = (() => {
    const draftsEl = h('section.design-section#design-drafts');
    const constructionEl =
        h('section.design-section.design-construction#design-construction');
    const customEl = h('section.design-section#design-custom');
    const basicEl = h('section.design-section#design-basic');
    let basicComps = [];
    const pendingToggles = new Map();  // name → enabled
    let toggleTimer = null;
    let constructionTimer = null;
    let restrategizePreview = null;  // last dry-run of /components/restrategize

    function actions() {
      const menuWrap = h('div.new-comp-wrap');
      const newBtn =
          h('button.btn.btn-primary#new-component', {
            type: 'button',
            'aria-haspopup': 'menu',
            onclick: (e) => {
              e.stopPropagation();
              const open = menuWrap.querySelector('.menu');
              if (open) {
                open.remove();
                return;
              }
              const close = () => menu.remove();
              const item = (id, ic, label, help, fn) =>
                  h('button.menu-item', {
                    type: 'button',
                    id,
                    onclick: () => {
                      close();
                      fn();
                    }
                  },
                    icon(ic),
                    h('span.menu-item-main', h('div', label),
                      h('div.muted.small', help)));
              const menu =
                  h('div.menu.new-comp-menu', {role: 'menu'},
                    item(
                        'new-comp-screenshot', 'image', 'From screenshots',
                        'Imported and analyzed first',
                        () => openImport('screenshot')),
                    item(
                        'new-comp-describe', 'edit_note', 'Describe it',
                        'Gemini drafts it from text',
                        () => openImport('describe')),
                    h('div.menu-sep'),
                    item(
                        'new-comp-blank', 'add', 'Blank component',
                        'Start from an empty definition', () => createBlank()));
              menuWrap.appendChild(menu);
              setTimeout(
                  () => document.addEventListener('click', function off(ev) {
                    if (!menu.contains(ev.target)) {
                      menu.remove();
                      document.removeEventListener('click', off);
                    }
                  }), 0);
            }
          },
            icon('add'), 'New component', icon('arrow_drop_down'));
      menuWrap.appendChild(newBtn);
      return [
        h('button.btn.btn-ghost#import-figma',
          {type: 'button', onclick: () => openImport('figma')},
          icon('design_services'), 'Import from Figma'),
        h('button.btn.btn-ghost#import-stitch',
          {type: 'button', onclick: () => openImport('stitch')},
          icon('auto_awesome_mosaic'), 'Import from Stitch'),
        menuWrap,
      ];
    }

    async function openImport(which) {
      if (which !== 'describe') {
        // Figma / Stitch / screenshots go through Imports (analyze first).
        openImportDrawer(pid, {
          tab: which === 'screenshot' ? 'screenshots' : which,
          origin: 'design'
        });
        return;
      }
      const mod = await import('./component_import.js');
      mod.openComponentImport(pid, {tab: which});
    }

    async function createBlank() {
      const taken = new Set(components.map((c) => c.name));
      let name = 'NewComponent';
      for (let i = 2; taken.has(name); i++) name = `NewComponent${i}`;
      const {component} = await post(`/api/projects/${pid}/components`, {
        definition: {
          name,
          description: '',
          usage: '',
          props: {
            type: 'object',
            properties: {
              title: {$ref: 'common_types.json#/$defs/DynamicString'},
            },
            required: ['title'],
          },
          web: {
            template: '<div class="root">{{title}}</div>',
            css:
                ':host { display: block; }\n.root {\n  padding: var(--a2ui-card-padding);\n  border-radius: var(--a2ui-radius-card);\n  background: var(--a2ui-surface-container);\n  color: var(--a2ui-on-surface);\n  font-family: var(--a2ui-font-family);\n}\n',
          },
          sample: {props: {title: 'Hello'}, data: {}, components: []},
        },
        status: 'draft',
      });
      navigate(`/p/${pid}/design/components/${component.id}`);
    }

    async function mountTab() {
      mount(panes.components, spinner('Loading components\u2026'));
      const [, cat] = await Promise.all([
        loadComponents(),
        get(`/api/projects/${pid}/catalog`).catch(() => ({components: []})),
        settings ? null : loadSettings(),
        loadDependencies(),
      ]);
      if (!alive) return;
      basicComps = (cat.components || []).filter((c) => !c.custom);
      if (cat.catalog_id) catalogId = cat.catalog_id;
      mount(panes.components, draftsEl, constructionEl, customEl, basicEl);
      draw();
      drawBasic();
      refreshRestrategizePreview();
    }

    function draw() {
      updateTabCounts();
      drawDrafts();
      drawConstruction();
      drawCustom();
      syncPolling();
    }

    // ------------------------------------------------ construction panel
    // Project-wide rule for how imported / described components are built:
    // settings.catalog.component_strategy (auto | web_template |
    // basic_composition | native_targets) and, for `auto`, the Basic-fit
    // threshold deciding between a Basic catalog composition (template) and a
    // custom web component.
    function catalogCfg() {
      return (settings && settings.catalog) || {};
    }

    function thresholdOf(cfg) {
      const t = Number(cfg.basic_fit_threshold);
      return Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : 0.7;
    }

    function fitBuckets(th) {
      const customs =
          components.filter((c) => (c.classification || 'custom') === 'custom');
      const out =
          {all: customs, pinned: [], basic: [], custom: [], unknown: []};
      for (const c of customs) {
        const dec = c.strategy_decision || {};
        const fit = c.basic_fit && c.basic_fit.score != null ?
            Number(c.basic_fit.score) :
            null;
        if (dec.auto === false) {
          out.pinned.push(c);
        } else if (fit == null) {
          out.unknown.push(c);
        } else if (fit >= th) {
          out.basic.push(c);
        } else {
          out.custom.push(c);
        }
      }
      return out;
    }

    function drawConstruction() {
      const cfg = catalogCfg();
      const strategy = cfg.component_strategy || 'auto';
      let th = thresholdOf(cfg);
      const liveEl = h('div.construct-live');
      const trackEl = h('div.fit-track', {role: 'presentation'});
      const pctEl = h('output.construct-pct', {for: 'basic-fit-threshold'
      });
      const applyBtn =
          h('button.btn.btn-ghost.btn-sm#restrategize', {
            type: 'button',
            onclick: (e) => reapplyStrategy(e.currentTarget),
          },
            icon('sync_alt'), 'Re-apply to existing components');

      function drawLive() {
        const b = fitBuckets(th);
        pctEl.textContent = `${Math.round(th * 100)}%`;
        const bits = [];
        if (strategy === 'auto') {
          bits.push(
              h('span.chip.chip-xs.fit-basic',
                `${b.basic.length} \u2192 Basic composition`));
          bits.push(
              h('span.chip.chip-xs.fit-custom',
                `${b.custom.length} \u2192 custom web`));
        } else {
          bits.push(h(
              'span.chip.chip-xs',
              `${b.basic.length + b.custom.length + b.unknown.length} \u2192 ${
                  strategyLabel(strategy)}`));
        }
        if (b.pinned.length)
          bits.push(
              h('span.chip.chip-xs.chip-quiet',
                {title: 'Strategy chosen on the component itself'},
                `${b.pinned.length} pinned`));
        if (b.unknown.length)
          bits.push(
              h('span.chip.chip-xs.chip-quiet', {
                title:
                    'No Basic-fit estimate yet (built before fit scoring, or manual)'
              },
                `${b.unknown.length} without a fit estimate`));
        mount(liveEl, h('span.small.muted', 'Your components: '), bits);
        // Fit strip: one marker per component with a fit score.
        const marks =
            [...b.basic, ...b.custom, ...b.pinned]
                .filter((c) => c.basic_fit && c.basic_fit.score != null)
                .map((c) => {
                  const s = Number(c.basic_fit.score);
                  const dec = c.strategy_decision || {};
                  const side = dec.auto === false ?
                      'pinned' :
                      (s >= th ? 'basic' : 'custom');
                  return h('a.fit-mark', {
                    class: `fm-${side}`,
                    href: `/p/${pid}/design/components/${c.id}`,
                    style: `left:${(s * 100).toFixed(1)}%`,
                    title: `${c.name}: Basic fit ${Math.round(s * 100)}%${
                        dec.auto === false ?
                            ` (pinned: ${strategyLabel(dec.strategy)})` :
                            ''}`,
                    'aria-label':
                        `${c.name}, Basic fit ${Math.round(s * 100)} percent`,
                  });
                });
        mount(
            trackEl, h('span.fit-track-rail'),
            h('span.fit-track-thresh',
              {style: `left:${(th * 100).toFixed(1)}%`}),
            marks);
        const p = restrategizePreview;
        const n = p && !p.error ? p.changed : 0;
        applyBtn.disabled = !n;
        mount(
            applyBtn, icon('sync_alt'),
            n ? `Re-apply to ${n} existing component${n === 1 ? '' : 's'}` :
                'Existing components already match');
        applyBtn.title = n ?
            (p.items || [])
                .filter((it) => it.status === 'changed')
                .map(
                    (it) => `${it.name}: ${strategyLabel(it.from)} \u2192 ${
                        strategyLabel(it.to)}`)
                .join('\n') :
            'Moving the threshold or strategy re-decides components that were built automatically (pinned ones keep their choice). No Gemini calls: both the web template and the Basic composition are kept.';
      }

      const strategySel = h(
          'select.input.input-sm#component-strategy', {
            onchange: () => {
              queueConstructionSave({component_strategy: strategySel.value});
              drawConstruction();
            },
          },
          h('option', {value: 'auto', selected: strategy === 'auto'},
            'Auto \u2014 decide per component by Basic fit'),
          h('option',
            {value: 'web_template', selected: strategy === 'web_template'},
            'Always custom web components (HTML/CSS)'),
          h('option', {
            value: 'basic_composition',
            selected: strategy === 'basic_composition'
          },
            'Always Basic catalog compositions (templates)'),
          h('option',
            {value: 'native_targets', selected: strategy === 'native_targets'},
            'Always multi-target native code (Compose + SwiftUI + Web)'));
      const slider = h('input.construct-slider#basic-fit-threshold', {
        type: 'range',
        min: '0',
        max: '100',
        step: '5',
        value: String(Math.round(th * 100)),
        'aria-label': 'Basic-fit threshold',
        'aria-valuetext': `${Math.round(th * 100)} percent`,
        oninput: () => {
          th = Number(slider.value) / 100;
          slider.setAttribute('aria-valuetext', `${slider.value} percent`);
          drawLive();
        },
        onchange: () => {
          th = Number(slider.value) / 100;
          queueConstructionSave({basic_fit_threshold: th});
        },
      });
      const presets =
          h('div.construct-presets',
            [['Mostly templates', 40], ['Balanced', 70], ['Mostly custom', 90]]
                .map(
                    ([label, v]) =>
                        h('button.btn.btn-quiet.btn-xs', {
                          type: 'button',
                          class: Math.round(th * 100) === v ? 'is-on' : '',
                          onclick: () => {
                            slider.value = String(v);
                            th = v / 100;
                            drawLive();
                            queueConstructionSave({basic_fit_threshold: th});
                            drawConstruction();
                          },
                        },
                          label)));
      const auto = strategy === 'auto';
      mount(
          constructionEl,
          h('div.design-section-head',
            h('h2', 'How custom components are built'),
            h('p.help',
              'Imported Figma / Stitch / screenshot components and described ones can become either a custom web component (your own HTML + CSS, Web-native) or a template composed from the A2UI Basic catalog (Card, Row, Column, Text, Image, Icon, Button, Divider \u2014 renders natively on every renderer). Each candidate gets a Basic fit score: how faithfully its structure maps onto Basic components.')),
          h('div.construct-card',
            h('label.field.construct-field',
              h('span.field-label', 'Strategy for new components'),
              strategySel),
            auto ?
                h('div.field.construct-threshold',
                  h('div.field-head',
                    h('span.field-label', 'Basic-fit threshold'),
                    h('span.grow'), presets, pctEl),
                  h('div.construct-slider-row',
                    h('span.construct-end.construct-end-l',
                      icon('dashboard_customize'),
                      'Prefer Basic catalog templates'),
                    slider,
                    h('span.construct-end.construct-end-r',
                      'Prefer custom components', icon('code')),
                    trackEl),
                  h('p.help.construct-rule',
                    'A component whose Basic fit is at least ',
                    h('strong.construct-rule-pct', `${Math.round(th * 100)}%`),
                    ' becomes a Basic catalog composition (a template, also usable on Android Compose, RemoteCompose and iOS); below that it becomes a custom web component. Figma and Stitch imports score the real layer structure; screenshots and descriptions use Gemini\u2019s estimate. You can still pin a strategy on any component.')) :
                h('p.help',
                  strategy === 'web_template' ?
                      'Every new component is drafted as a custom web component (HTML + CSS). Native renderers fall back to a derived Basic composition.' :
                      strategy === 'basic_composition' ?
                      'Every new component is composed from the Basic catalog and can be saved as a template.' :
                      'Every new component ships Kotlin Compose, RemoteCompose and SwiftUI code next to its web template.'),
            h('div.construct-foot', liveEl, h('span.grow'), applyBtn)));
      drawLive();
      // Keep the rule sentence in sync while dragging.
      slider.addEventListener('input', () => {
        const el = constructionEl.querySelector('.construct-rule-pct');
        if (el) el.textContent = `${Math.round(th * 100)}%`;
      });
    }

    function queueConstructionSave(patchObj) {
      settings = settings || {};
      settings.catalog = {...(settings.catalog || {}), ...patchObj};
      clearTimeout(constructionTimer);
      constructionTimer = setTimeout(() => saveConstruction(patchObj), 350);
    }

    async function saveConstruction(patchObj) {
      try {
        const {settings: fresh} = await get(`/api/projects/${pid}/settings`);
        const next = {...(fresh || {})};
        next.catalog = {...(next.catalog || {}), ...patchObj};
        const res =
            await put(`/api/projects/${pid}/settings`, {settings: next});
        settings = (res && res.settings) || next;
        if (store.project) store.project.settings = settings;
        emit('settings-changed', {settings, source: 'design-construction'});
        await refreshRestrategizePreview();
      } catch (e) {
        await loadSettings().catch(() => null);
        if (alive) drawConstruction();
      }
    }

    async function refreshRestrategizePreview() {
      try {
        restrategizePreview = await post(
            `/api/projects/${pid}/components/restrategize`, {dry_run: true},
            {quiet: true});
      } catch (e) {
        restrategizePreview = {changed: 0, error: true};
      }
      if (alive && mounted.components) drawConstruction();
    }

    async function reapplyStrategy(btn) {
      const p = restrategizePreview || {};
      const n = p.changed || 0;
      if (!n) return;
      const list =
          (p.items || [])
              .filter((it) => it.status === 'changed')
              .map(
                  (it) => `${it.name}: ${strategyLabel(it.from)} \u2192 ${
                      strategyLabel(it.to)}`)
              .join(', ');
      if (!(await confirmDialog(
              `Re-apply the construction rule to ${n} component${
                  n === 1 ? '' : 's'}?`,
              `${list}. Previews are re-rendered; no Gemini calls are made. Components with a pinned strategy are not touched.`,
              {confirmLabel: 'Re-apply'}))) {
        return;
      }
      btn.disabled = true;
      btn.classList.add('busy');
      try {
        const res =
            await post(`/api/projects/${pid}/components/restrategize`, {});
        toast(
            `Re-applied to ${res.changed} component${
                res.changed === 1 ? '' : 's'}`,
            'good');
        await loadComponents();
        await refreshRestrategizePreview();
        if (alive) draw();
      } finally {
        btn.classList.remove('busy');
      }
    }

    function syncPolling() {
      for (const c of components) {
        if (jobActive(c) && !stops.has(c.id)) {
          stops.set(c.id, pollComponent(c.id, (fresh) => {
                      replaceComponent(fresh);
                      if (!jobActive(fresh)) {
                        stops.get(fresh.id)?.();
                        stops.delete(fresh.id);
                      }
                    }));
        }
      }
    }

    function replaceComponent(fresh) {
      const i = components.findIndex((c) => c.id === fresh.id);
      if (fresh.status === 'archived') {
        if (i >= 0) components.splice(i, 1);
      } else if (i >= 0) {
        components[i] = fresh;
      } else {
        components.unshift(fresh);
      }
      if (alive) {
        drawDrafts();
        drawCustom();
      }
    }

    function drawDrafts() {
      const drafts = components.filter((c) => c.status === 'draft');
      if (!drafts.length) {
        mount(draftsEl);
        draftsEl.hidden = true;
        return;
      }
      draftsEl.hidden = false;
      mount(
          draftsEl,
          h('div.design-section-head',
            h('h2', 'Drafts', h('span.count', ` ${drafts.length}`)),
            h('p.help',
              'Built from your sources and checked against the reference. Accept the ones you want the generator to use.')),
          h('div.draft-strip', drafts.map(draftCard)));
    }

    function draftCard(c) {
      const def = c.definition || {};
      const name = c.name || def.name || 'Untitled';
      const cls = c.classification || 'custom';
      const busy = jobActive(c);
      const media =
          h('div.draft-media',
            h('figure.draft-fig',
              c.reference_url ? h('img', {
                src: c.reference_url,
                alt: `${name} reference`,
                loading: 'lazy'
              }) :
                                h('div.draft-fig-empty',
                                  icon('image_not_supported'), 'No reference'),
              h('figcaption', 'Reference')),
            h('figure.draft-fig',
              c.preview_url ? h('img', {
                src: c.preview_url,
                alt: `${name} web preview`,
                loading: 'lazy'
              }) :
                              h('div.draft-fig-empty',
                                busy ? spinner() : icon('hourglass_empty'),
                                busy ? 'Building\u2026' : 'No preview yet'),
              h('figcaption', 'Web preview')));

      let body;
      let acts;
      if (cls === 'basic_equivalent') {
        const be = c.basic_equivalent || {};
        body =
            h('div.draft-basic',
              h('div.draft-basic-title', icon('swap_horiz'),
                h('span', 'Matches Basic ', h('code', be.component || '?'))),
              be.notes ? h('p.small.muted', be.notes) : null,
              (c.token_suggestions || []).length ?
                  tokenSuggestionList(c.token_suggestions) :
                  h('p.small.muted', 'No token changes suggested.'));
        acts = [
          (c.token_suggestions || []).length ?
              h('button.btn.btn-primary.btn-sm.draft-apply-tokens', {
                type: 'button',
                disabled: busy,
                onclick: (e) => applyTokens(c, e.currentTarget),
              },
                icon('palette'),
                `Apply ${c.token_suggestions.length} token${
                    c.token_suggestions.length === 1 ? '' : 's'}`) :
              null,
          discardBtn(c),
        ];
      } else if (cls === 'skip') {
        body =
            h('div.draft-skip', icon('block'),
              h('span',
                (c.basic_equivalent && c.basic_equivalent.notes) || c.notes ||
                    'Not a reusable component.'));
        acts = [discardBtn(c)];
      } else {
        const issues = c.issues || [];
        const warns = c.warnings || [];
        const mismatches = (c.fidelity && c.fidelity.mismatches) || [];
        const unbuilt = needsBuild(c);
        body = [
          def.description ? h('p.draft-desc', def.description) : null,
          !unbuilt && issues.length ?
              h('div.draft-issues.draft-issues-bad', icon('error'),
                h('ul.small', issues.slice(0, 3).map((x) => h('li', x)))) :
              !unbuilt && warns.length ?
              h('div.draft-issues.draft-issues-warn', icon('lightbulb'),
                h('ul.small', warns.slice(0, 2).map((x) => h('li', x)))) :
              null,
        ];
        acts = [
          unbuilt ? h('button.btn.btn-primary.btn-sm.draft-build', {
            type: 'button',
            disabled: busy,
            title:
                'Gemini writes the HTML/CSS and sample data for this proposed component',
            onclick: (e) => build(c, e.currentTarget),
          },
                      icon('auto_awesome'), 'Build with Gemini') :
                    null,
          !unbuilt && issues.length ?
              h('button.btn.btn-primary.btn-sm.draft-fix', {
                type: 'button',
                disabled: busy,
                title: 'Ask Gemini to fix validation errors in this draft',
                onclick: () => fixDraft(c, {activateAfter: true}),
              },
                icon('auto_awesome'), 'Fix with Gemini') :
              null,
          !unbuilt ?
              h(`button.btn.${
                    issues.length ? 'btn-ghost' :
                                    'btn-primary'}.btn-sm.draft-accept`,
                  {
                    type: 'button',
                    disabled: busy,
                    title: issues.length ?
                        'Review issues and fix with Gemini before activating' :
                        'Make it available to the generator',
                    onclick: (e) => accept(c, e.currentTarget),
                  },
                  icon('check'), 'Accept') :
              null,
          !unbuilt && !issues.length && (warns.length || mismatches.length) ?
              h('button.btn.btn-ghost.btn-sm.draft-fix', {
                type: 'button',
                disabled: busy,
                title:
                    'Ask Gemini to resolve token suggestions or visual mismatches',
                onclick: () => fixDraft(c, {
                  instruction: warns.length ?
                      'Replace hard-coded colors with var(--a2ui-*) theme tokens and address all suggestions.' :
                      'Fix visual mismatches against the reference image.',
                }),
              },
                icon('auto_awesome'), 'Polish') :
              null,
          h('a.btn.btn-ghost.btn-sm.draft-edit',
            {href: `/p/${pid}/design/components/${c.id}`}, icon('edit'),
            'Edit'),
          discardBtn(c),
        ];
      }
      return h(
          'article.draft-card', {dataset: {cid: c.id}, class: `cls-${cls}`},
          h('div.draft-head',
            h('a.draft-name', {href: `/p/${pid}/design/components/${c.id}`},
              name),
            h('span.grow'), sourceBadge(c.source)),
          media,
          h('div.chip-row.draft-chips', fidelityChip(c.fidelity),
            issuesBadge(c.issues), warningsBadge(c.warnings),
            cls === 'custom' ? webOnlyChip(c) : null,
            cls === 'custom' ? basicFitChip(c) : null,
            c.source && c.source.figma_name && c.source.figma_name !== name ?
                h('span.muted.small', {title: 'Figma name'},
                  c.source.figma_name) :
                null),
          jobProgress(c), body, h('div.draft-actions', acts));
    }

    function discardBtn(c) {
      return h(
          'button.btn.btn-quiet.btn-sm.draft-discard', {
            type: 'button',
            onclick: async (e) => {
              const b = e.currentTarget;
              if (!(await confirmDialog(
                      `Discard ${c.name || 'draft'}?`,
                      'The draft and its preview are deleted.',
                      {confirmLabel: 'Discard', danger: true})))
                return;
              b.disabled = true;
              try {
                await del(`/api/components/${c.id}`);
                stops.get(c.id)?.();
                stops.delete(c.id);
                components = components.filter((x) => x.id !== c.id);
                draw();
                toast(`Discarded ${c.name || 'draft'}`, 'good');
              } finally {
                b.disabled = false;
              }
            }
          },
          'Discard');
    }

    async function accept(c, btn) {
      btn.disabled = true;
      try {
        const fresh = await activateComponent(c, {
          onSaved: (saved, {appliedTokens} = {}) => {
            replaceComponent(saved);
            syncPolling();
            if (appliedTokens) themeChanged();
          },
        });
        if (!fresh) return;
        replaceComponent(fresh);
        syncPolling();
        toast(`${fresh.name || c.name} is active`, 'good');
        if (c.token_suggestions && c.token_suggestions.length) themeChanged();
      } finally {
        btn.disabled = false;
      }
    }

    async function fixDraft(c, {instruction = '', activateAfter = false} = {}) {
      await proposeComponentFixModal(c, {
        instruction,
        activateAfter,
        onSaved: (saved, {appliedTokens} = {}) => {
          replaceComponent(saved);
          syncPolling();
          if (appliedTokens) themeChanged();
        },
      });
    }

    async function build(c, btn) {
      btn.disabled = true;
      btn.classList.add('busy');
      try {
        const fresh = await buildFromProposal(c);
        if (fresh) replaceComponent(fresh);
        syncPolling();
        toast(`Building ${c.name || 'component'}\u2026`, 'good');
      } finally {
        btn.disabled = false;
        btn.classList.remove('busy');
      }
    }

    async function applyTokens(c, btn) {
      btn.disabled = true;
      try {
        const res = await post(`/api/components/${c.id}/apply_tokens`);
        const n = (res.applied || []).length;
        if (res.component)
          replaceComponent(res.component);
        else {
          components = components.filter((x) => x.id !== c.id);
          draw();
        }
        toast(`Applied ${n} token${n === 1 ? '' : 's'} to the theme`, 'good');
        themeChanged();
      } finally {
        btn.disabled = false;
      }
    }

    async function themeChanged() {
      try {
        const s = await loadSettings();
        emit('settings-changed', {settings: s, source: 'design-components'});
      } catch (_) { /* ignore */
      }
    }

    function drawCustom() {
      const active = components.filter((c) => c.status === 'active');
      const hasDrafts = components.some((c) => c.status === 'draft');
      mount(
          customEl,
          h('div.design-section-head',
            h('h2', 'Custom components', h('span.count', ` ${active.length}`)),
            h('p.help',
              'Your own components, part of this project\u2019s catalog. Custom web components render natively on Web (native renderers use their derived Basic composition); Basic catalog compositions render natively on every renderer.')),
          active.length ?
              h('div.comp-grid#custom-component-grid', active.map(customCard)) :
              emptyState(
                  hasDrafts ? 'No active custom components yet' :
                              'No custom components yet',
                  hasDrafts ?
                      'Review and click Accept on the drafts above to activate them in this project\u2019s catalog, or import more components.' :
                      'Import a Figma library, Stitch screens or screenshots (analyzed into components, templates and tokens first), or describe a component to Gemini.',
                  h('button.btn.btn-ghost',
                    {type: 'button', onclick: () => openImport('figma')},
                    icon('design_services'), 'Import from Figma'),
                  h('button.btn.btn-ghost',
                    {type: 'button', onclick: () => openImport('stitch')},
                    icon('auto_awesome_mosaic'), 'Import from Stitch'),
                  h('button.btn.btn-ghost',
                    {type: 'button', onclick: () => openImport('describe')},
                    icon('edit_note'), 'Describe one')));
    }

    function customCard(c) {
      const def = c.definition || {};
      const name = c.name || def.name || 'Untitled';
      return h(
          'article.comp-card', {dataset: {cid: c.id}},
          h('a.comp-thumb', {href: `/p/${pid}/design/components/${c.id}`},
            c.preview_url ?
                h('img', {
                  src: c.preview_url,
                  alt: `${name} preview`,
                  loading: 'lazy'
                }) :
                h('div.draft-fig-empty', icon('widgets'), 'No preview'),
            jobActive(c) ? h('span.comp-thumb-busy', spinner()) : null),
          h('div.comp-card-body',
            h('div.comp-card-title',
              h('a.comp-name', {href: `/p/${pid}/design/components/${c.id}`},
                name),
              h('span.grow'), issuesBadge(c.issues), warningsBadge(c.warnings)),
            def.description ? h('p.comp-desc', def.description) : null,
            h('div.chip-row', sourceBadge(c.source), webOnlyChip(c),
              basicFitChip(c), fidelityChip(c.fidelity), compUsedByBadge(name)),
            h('div.comp-card-actions',
              (c.issues || []).length ?
                  h('button.btn.btn-primary.btn-sm.comp-fix', {
                    type: 'button',
                    onclick: () => fixDraft(c),
                  },
                    icon('auto_awesome'), 'Fix') :
                  null,
              h('a.btn.btn-ghost.btn-sm',
                {href: `/p/${pid}/design/components/${c.id}`}, icon('edit'),
                'Edit'),
              h('button.btn.btn-quiet.btn-sm.comp-deactivate', {
                type: 'button',
                onclick: async (e) => {
                  const b = e.currentTarget;
                  const {rules, templates: tpls} = compUsedBy(name);
                  if (rules.length || tpls.length) {
                    const refs = [
                      rules.length ? `constitution (${rules.join(', ')})` :
                                     null,
                      tpls.length ? `templates (${tpls.join(', ')})` : null,
                    ].filter(Boolean).join(' and ');
                    if (!(await confirmDialog(
                            `Deactivate ${name}?`,
                            `${name} is referenced in ${
                                refs}. Deactivating moves it back to Drafts and removes it from the active catalog.`,
                            {
                              confirmLabel: 'Deactivate anyway',
                              danger: true
                            }))) {
                      return;
                    }
                  }
                  b.disabled = true;
                  try {
                    const {component} = await patch(
                        `/api/components/${c.id}`, {status: 'draft'});
                    replaceComponent(component);
                    toast(`${name} deactivated`, 'good');
                  } finally {
                    b.disabled = false;
                  }
                }
              },
                'Deactivate'))));
    }

    function drawBasic() {
      const disabled = new Set(
          (settings && settings.catalog &&
           settings.catalog.disabled_components) ||
          []);
      mount(
          basicEl,
          h('div.design-section-head',
            h('h2', 'Basic catalog',
              h('span.count',
                ` ${basicComps.filter((c) => !disabled.has(c.name)).length}/${
                    basicComps.length}`)),
            h('p.help',
              'Which A2UI Basic components the generator may use. The constitution decides when to use them; behavior belongs here.')),
          basicComps.length ?
              h('div.catalog-grid#catalog-components', basicComps.map((c) => {
                const id = `comp-${c.name}`;
                return h(
                    'label.catalog-item',
                    {for: id, title: c.description || ''
                    },
                    h('input', {
                      type: 'checkbox',
                      id,
                      checked: !disabled.has(c.name),
                      onchange: (e) => queueToggle(c.name, e.target.checked),
                    }),
                    h('span.catalog-name', c.name, compUsedByBadge(c.name)),
                    h('span.catalog-desc', c.description || ''));
              })) :
              h('p.muted', 'Catalog unavailable.'));
    }

    function queueToggle(name, enabled) {
      if (!enabled) {
        const {rules, templates: tpls} = compUsedBy(name);
        if (rules.length || tpls.length) {
          const where = [
            rules.length ? `constitution (${rules.join(', ')})` : null,
            tpls.length ? `templates (${tpls.join(', ')})` : null,
          ].filter(Boolean).join(' & ');
          toast(`Note: ${name} is referenced in ${where}.`, 'warn', 3500);
        }
      }
      pendingToggles.set(name, enabled);
      clearTimeout(toggleTimer);
      toggleTimer = setTimeout(flushToggles, 450);
    }

    async function flushToggles() {
      if (!pendingToggles.size) return;
      const changes = new Map(pendingToggles);
      pendingToggles.clear();
      try {
        const {settings: fresh} = await get(`/api/projects/${pid}/settings`);
        const next = {...(fresh || {})};
        next.catalog = {...(next.catalog || {})};
        const dis = new Set(next.catalog.disabled_components || []);
        for (const [n, en] of changes) en ? dis.delete(n) : dis.add(n);
        next.catalog.disabled_components = [...dis];
        const res =
            await put(`/api/projects/${pid}/settings`, {settings: next});
        settings = (res && res.settings) || next;
        if (store.project) store.project.settings = settings;
        emit('settings-changed', {settings, source: 'design-catalog'});
        toast('Catalog updated', 'good', 1800);
      } catch (e) {
        // Re-sync checkboxes with the server state.
        await loadSettings().catch(() => null);
      }
      if (alive) drawBasic();
    }

    async function reload() {
      await loadComponents();
      if (alive && mounted.components) draw();
    }

    return {actions, mount: mountTab, reload, replaceComponent, flushToggles};
  })();

  // =========================================================== Tokens tab
  const tokens = (() => {
    let editor = null;
    const state = {dirty: false};
    const tokenRevChip =
        h('span.chip.token-rev-chip#current-token-rev',
          {title: 'Current design tokens revision'}, icon('palette'),
          `Tokens T${
              (store.project && store.project.current_token_revision &&
               store.project.current_token_revision.number) ||
              1}`);
    const historyBtn =
        h('button.btn.btn-ghost#open-token-history',
          {type: 'button', onclick: () => openTokenHistory()}, icon('history'),
          'Token history');
    const dirtyEl =
        h('span.dirty', {hidden: true}, h('span.dot'), 'Unsaved changes');
    const saveBtn = h(
        'button.btn.btn-primary#save-tokens',
        {type: 'button', disabled: true, onclick: () => save()}, 'Save tokens');
    const staleEl = h('div.callout.callout-warn#tokens-stale', {hidden: true});

    function markDirty() {
      state.dirty = true;
      saveBtn.disabled = false;
      dirtyEl.hidden = false;
    }
    function clean() {
      state.dirty = false;
      saveBtn.disabled = true;
      dirtyEl.hidden = true;
      staleEl.hidden = true;
    }

    function actions() {
      return [tokenRevChip, historyBtn, dirtyEl, saveBtn];
    }

    function syncTokenRevBadge(num) {
      const n = num ||
          (depsData && depsData.snapshot &&
           depsData.snapshot.token_revision_number) ||
          (store.project && store.project.current_token_revision &&
           store.project.current_token_revision.number) ||
          1;
      mount(tokenRevChip, icon('palette'), `Tokens T${n}`);
    }

    function combinedTokenUsage() {
      const compMap = tokenUsage(components) || {};
      const depTokens =
          (depsData && depsData.used_by && depsData.used_by.tokens) || {};
      const out = {};
      for (const [cssVar, names] of Object.entries(compMap)) {
        out[cssVar] = {
          components: [...names],
          constitution_rules: [],
        };
      }
      for (const [cssVar, info] of Object.entries(depTokens)) {
        if (!out[cssVar]) {
          out[cssVar] = {components: [], constitution_rules: []};
        }
        const compSet = new Set([
          ...out[cssVar].components,
          ...((info && info.components) || []),
        ]);
        out[cssVar].components = [...compSet];
        out[cssVar].constitution_rules =
            (info && info.constitution_rules) || [];
      }
      return out;
    }

    async function mountTab() {
      mount(panes.tokens, spinner('Loading tokens\u2026'));
      await Promise.all([
        settings ? null : loadSettings(),
        components.length ? null : loadComponents().catch(() => []),
        loadDependencies(),
      ]);
      if (!alive) return;
      syncTokenRevBadge();
      mount(
          panes.tokens,
          h('p.help.tokens-lede',
            'Visual styling lives here \u2014 never in the constitution. Tokens map to Web CSS custom properties (--a2ui-*), Android Jetpack Compose MaterialTheme, RemoteCompose (.rc) and iOS SwiftUI. Custom component CSS reads the same variables; ',
            icon('widgets'),
            ' marks tokens used by a custom component or referenced in the constitution.'),
          staleEl);
      editor = mountThemeEditor(panes.tokens, {
        theme: settings.theme,
        onDirty: markDirty,
        usedBy: combinedTokenUsage(),
      });
    }

    function refreshUsage() {
      syncTokenRevBadge();
      if (editor) editor.setUsedBy(combinedTokenUsage());
    }

    async function save() {
      if (!editor) return;
      saveBtn.disabled = true;
      try {
        const saved = await saveThemeToProject(
            pid, editor.getTheme(), {source: 'design-tokens'});
        settings = saved;
        if (store.project) store.project.settings = saved;
        await loadDependencies();
        syncTokenRevBadge();
        clean();
        toast('Tokens saved', 'good');
      } catch (e) {
        saveBtn.disabled = false;
      }
    }

    async function openTokenHistory() {
      const d = drawer({
        title: 'Token revision history',
        wide: true,
        id: 'token-history-drawer',
        body: spinner('Loading token history\u2026'),
      });
      const res = await get(`/api/projects/${pid}/token_revisions`);
      const list = res.token_revisions || [];
      const curNum = list.length ? list[0].number : 1;
      const listEl = h('ol.rev-list#token-revision-list');
      const detail = h('div.rev-detail');

      function swatchFor(val) {
        const s = String(val ?? '');
        if (/^#[0-9a-f]{3,8}$/i.test(s)) {
          return h('span.token-diff-swatch', {style: {background: s}});
        }
        return null;
      }

      function showTokenRev(tr) {
        const isCurrent = tr.number === curNum;
        const diff = tr.diff || {};
        const changed = diff.changes || diff.changed || [];
        mount(
            detail,
            h('div.rev-detail-head',
              h('div',
                h('h3', `Tokens T${tr.number}`,
                  isCurrent ? h('span.chip.c-current', 'current') : null),
                h('p.muted',
                  `${tr.summary || 'Token update'} \u2014 ${
                      fmtTime(tr.created_at)}`),
                h('p.muted.small',
                  `Source: ${tr.source || 'manual'} \u00b7 Hash: ${
                      tr.theme_hash || ''}`)),
              h('div.rev-detail-actions',
                !isCurrent ?
                    h('button.btn.btn-sm.btn-ghost#revert-token-revision', {
                      type: 'button',
                      onclick: async () => {
                        if (state.dirty &&
                            !(await confirmDialog(
                                'Discard unsaved token edits?',
                                `Reverting to T${
                                    tr.number} creates a new token revision and replaces your unsaved changes.`,
                                {confirmLabel: 'Revert', danger: true}))) {
                          return;
                        }
                        const rRes = await post(`/api/projects/${
                            pid}/token_revisions/${tr.id}/revert`);
                        if (rRes.settings) {
                          settings = rRes.settings;
                          if (store.project) store.project.settings = settings;
                          if (editor) editor.setTheme(settings.theme);
                        }
                        if (rRes.token_revision) {
                          syncTokenRevBadge(rRes.token_revision.number);
                        }
                        clean();
                        emit('settings-changed', {
                          settings,
                          token_revision: rRes.token_revision,
                          source: 'token-revert',
                        });
                        toast(
                            `Reverted tokens \u2014 now T${
                                (rRes.token_revision &&
                                 rRes.token_revision.number) ||
                                ''}`,
                            'good');
                        d.close();
                        loadDependencies();
                      },
                    },
                      icon('undo'), `Revert to T${tr.number}`) :
                    null)),
            changed.length ?
                h('div.token-diff-section',
                  h('h4.label',
                    `${changed.length} changed token${
                        changed.length === 1 ? '' : 's'}`),
                  h('div.token-diff-chips',
                    changed.map(
                        (c) => h(
                            'div.token-diff-row',
                            h('code.token-diff-key', c.css_var || c.token),
                            h('span.token-diff-val.before', swatchFor(c.before),
                              String(c.before ?? '\u2014')),
                            h('span.muted', '\u2192'),
                            h('span.token-diff-val.after', swatchFor(c.after),
                              String(c.after ?? '\u2014')))))) :
                h('p.muted.small',
                  tr.number === 1 ? 'Initial project theme baseline.' :
                                    'Full theme snapshot recorded.'),
            h('details.token-snapshot-details',
              h('summary', 'Full theme JSON snapshot'),
              jsonTree(tr.theme || {}, {collapseDepth: 2})));
      }

      mount(
          listEl,
          list.map(
              (tr, i) => h(
                  'li',
                  h('button.rev-item', {
                    type: 'button',
                    id: `token-rev-${tr.id}`,
                    class: `${tr.number === curNum ? 'current' : ''} ${
                        i === 0 ? 'on' : ''}`,
                    onclick: (e) => {
                      listEl.querySelectorAll('.rev-item')
                          .forEach((b) => b.classList.remove('on'));
                      e.currentTarget.classList.add('on');
                      showTokenRev(tr);
                    },
                  },
                    h('span.rev-num', `T${tr.number}`),
                    h('span.rev-main',
                      h('span.rev-summary',
                        tr.summary ||
                            (tr.number === 1 ? 'Initial project tokens' :
                                               'Token update')),
                      h('span.rev-meta',
                        `${tr.source || 'manual'} \u00b7 ${
                            tr.author_ldap ||
                            ''} \u00b7 ${relTime(tr.created_at)}`)),
                    tr.number === curNum ? h('span.chip.c-current', 'current') :
                                           null))));
      mount(d.body, h('div.history', listEl, detail));
      if (list.length) showTokenRev(list[0]);
    }

    function onExternal(updated) {
      if (!editor || !updated || !updated.theme) return;
      if (!state.dirty) {
        editor.setTheme(updated.theme);
        return;
      }
      staleEl.hidden = false;
      mount(
          staleEl, icon('sync_problem'),
          h('span', 'The theme was changed elsewhere (chat or a component). '),
          h('button.link-btn', {
            type: 'button',
            onclick: () => {
              editor.setTheme(updated.theme);
              clean();
            }
          },
            'Load it (drops your edits)'));
    }

    return {
      actions,
      mount: mountTab,
      refreshUsage,
      onExternal,
      get dirty() {
        return state.dirty;
      },
      destroy: () => editor && editor.destroy(),
    };
  })();

  // ------------------------------------------------------------- events
  disposers.push(
      on('components-changed', async ({component, components: list} = {}) => {
        if (list && list.length)
          list.forEach((c) => componentsTab.replaceComponent(c));
        else if (component)
          componentsTab.replaceComponent(component);
        await componentsTab.reload().catch(() => null);
        if (mounted.tokens) tokens.refreshUsage();
      }));
  disposers.push(on('settings-changed', ({settings: updated, source} = {}) => {
    if (!updated) return;
    if (source !== 'design-tokens') tokens.onExternal(updated);
    settings = {...(settings || {}), ...updated};
  }));
  const unsaved = (e) => {
    if (tokens.dirty) {
      e.preventDefault();
      e.returnValue = '';
    }
  };
  window.addEventListener('beforeunload', unsaved);

  await showTab();
  refreshCountsQuiet();

  return () => {
    alive = false;
    componentsTab.flushToggles();
    window.removeEventListener('beforeunload', unsaved);
    disposers.forEach((d) => d());
    stops.forEach((s) => s());
    tokens.destroy();
    imageryTab.destroy();
  };
}
