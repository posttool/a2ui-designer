// "New run" dialog shared by Runs, Run detail and Constitution (baseline).
// mode:'effect' turns it into the "Constitution effect" study dialog: the
// chosen revision vs no constitution on the same samples.
import {cached, get, post, put} from './api.js';
import {jobActive} from './component_badges.js';
import {h, icon, mount, spinner} from './dom.js';
import {emit, navigate, store} from './store.js';
import {modal, toast} from './ui.js';

const STAGE_TEXT = {
  queued: 'Queued',
  extracting: 'Reading source',
  drafting: 'Drafting HTML/CSS',
  rendering: 'Rendering preview',
  comparing: 'Comparing to reference',
  repairing: 'Repairing mismatches',
  error: 'Build failed',
};

/**
 * Checks whether any custom components are currently building or have build /
 * validation errors, and whether the chosen constitution revision has any
 * cross-layer errors or warnings.
 */
export async function checkRunReadiness(pid, {revisionId} = {}) {
  const qs = revisionId ? `?revision_id=${encodeURIComponent(revisionId)}` : '';
  const [depsRes, compsRes] = await Promise.all([
    get(`/api/projects/${pid}/dependencies${qs}`, {
      quiet: true
    }).catch(() => null),
    get(`/api/projects/${pid}/components`, {quiet: true}).catch(() => null),
  ]);
  const deps = depsRes || {};
  const comps = (compsRes && Array.isArray(compsRes.components)) ?
      compsRes.components :
      [];
  const byId = new Map();
  for (const b of deps.building_components || []) {
    if (b && b.id) byId.set(b.id, b);
  }
  for (const c of comps) {
    if (!c || c.status === 'archived') continue;
    if (jobActive(c) && !byId.has(c.id)) {
      const st = (c.job && c.job.stage) || 'queued';
      byId.set(c.id, {
        id: c.id,
        name: c.name,
        status: c.status || 'draft',
        stage: st,
        stage_label: STAGE_TEXT[st] || st,
        rule_ids: [],
        template_names: [],
      });
    }
  }
  const buildingComponents = [...byId.values()];
  const issues = Array.isArray(deps.issues) ? [...deps.issues] : [];
  const seenCompErr =
      new Set(issues
                  .filter(
                      (i) => i.kind === 'component_build_error' ||
                          i.kind === 'component_validation_error')
                  .map((i) => i.component || i.target_name));
  for (const c of comps) {
    if (!c || c.status === 'archived' || jobActive(c)) continue;
    if (seenCompErr.has(c.name)) continue;
    const jobErr = c.job && c.job.stage === 'error' ?
        (c.job.error || 'Component build failed') :
        '';
    const valIssues = Array.isArray(c.issues) ? c.issues.filter(Boolean) : [];
    if (jobErr) {
      issues.push({
        kind: 'component_build_error',
        level: 'error',
        target_kind: 'component',
        target_name: c.name,
        component: c.name,
        component_id: c.id,
        rule_ids: [],
        message: `Custom component \u201c${c.name}\u201d failed to build: ${
            jobErr}.`,
      });
      seenCompErr.add(c.name);
    } else if (valIssues.length && c.status === 'active') {
      issues.push({
        kind: 'component_validation_error',
        level: 'error',
        target_kind: 'component',
        target_name: c.name,
        component: c.name,
        component_id: c.id,
        rule_ids: [],
        message:
            `Custom component \u201c${c.name}\u201d has validation errors: ${
                valIssues.slice(0, 2).join('; ')}.`,
      });
      seenCompErr.add(c.name);
    }
  }
  const curId = store.project && store.project.current_revision &&
      store.project.current_revision.id;
  let unsavedDraft = false;
  if (typeof store.draftProvider === 'function') {
    try {
      const d = store.draftProvider();
      if (d && d.dirty && (!revisionId || revisionId === curId)) {
        unsavedDraft = true;
      }
    } catch (_) {
    }
  }
  const errorCount = issues.filter((i) => i.level === 'error').length;
  const warnCount = issues.length - errorCount + (unsavedDraft ? 1 : 0);
  const hasBuilding = buildingComponents.length > 0;
  const hasIssues = issues.length > 0 || unsavedDraft;
  return {
    buildingComponents,
    issues,
    unsavedDraft,
    errorCount,
    warnCount,
    hasBuilding,
    hasIssues,
    needsWarning: hasBuilding || hasIssues,
  };
}

/**
 * Renders the warning banner for building components and constitution errors.
 */
export function renderReadinessBanner(
    pid, readiness, {onNavigate, compact = false} = {}) {
  if (!readiness || !readiness.needsWarning) return null;
  const {buildingComponents, issues, unsavedDraft, errorCount, warnCount} =
      readiness;
  const go = (url) => {
    if (onNavigate)
      onNavigate(url);
    else
      navigate(url);
  };
  const blocks = [];
  if (buildingComponents.length > 0) {
    const n = buildingComponents.length;
    blocks.push(h(
        'div.run-warn-block.warn-building#run-building-warning',
        h('div.run-warn-head',
          h('div.run-warn-title', spinner(),
            h('strong',
              `${n} custom component${
                  n === 1 ? ' is' : 's are'} still building`)),
          h('button.btn.btn-ghost.btn-xs#run-warn-open-components', {
            type: 'button',
            onclick: () => go(`/p/${pid}/design-system?section=components`),
          },
            icon('widgets'), 'View components')),
        h('p.run-warn-sub',
          'Runs snapshot the active component catalog when they start. Components still building remain in draft and won\u2019t be used by the generator until their build finishes:'),
        h('div.run-warn-chips', buildingComponents.map((b) => {
          const stLabel = STAGE_TEXT[b.stage] || b.stage_label || b.stage;
          const rules = (b.rule_ids || []).length ?
              ` \u00b7 ${b.rule_ids.join(', ')}` :
              '';
          return h(
              'span.chip.chip-sm.run-building-chip',
              {dataset: {component: b.name, stage: b.stage}},
              icon('progress_activity'), h('strong', b.name),
              h('span.muted', `${stLabel}${rules}`));
        }))));
  }
  if (issues.length > 0 || unsavedDraft) {
    const parts = [];
    if (errorCount)
      parts.push(`${errorCount} error${errorCount === 1 ? '' : 's'}`);
    if (warnCount)
      parts.push(`${warnCount} warning${warnCount === 1 ? '' : 's'}`);
    const summaryText = errorCount ?
        `Constitution & catalog errors (${parts.join(', ')})` :
        `Constitution warnings (${parts.join(', ')})`;
    const listItems = [];
    if (unsavedDraft) {
      listItems.push(h(
          'li.run-warn-item.lvl-warn',
          h('span.chip.chip-xs.v-mixed', 'UNSAVED'),
          h('span.run-warn-msg',
            'The Constitution editor has unsaved changes. Starting a run now will use the last saved revision unless you save first.')));
    }
    for (const iss of issues.slice(0, compact ? 4 : 10)) {
      const isErr = iss.level === 'error';
      listItems.push(
          h('li.run-warn-item', {class: isErr ? 'lvl-error' : 'lvl-warn'},
            h('span.chip.chip-xs', {class: isErr ? 'v-bad' : 'v-mixed'},
              isErr ? 'ERROR' : 'WARN'),
            h('span.run-warn-msg', iss.message || iss.target_name || 'Issue')));
    }
    if (issues.length > (compact ? 4 : 10)) {
      listItems.push(
          h('li.run-warn-item.muted',
            `+${issues.length - (compact ? 4 : 10)} more issue${
                issues.length - (compact ? 4 : 10) === 1 ? '' : 's'}\u2026`));
    }
    blocks.push(
        h('div.run-warn-block#run-constitution-warning',
          {class: errorCount ? 'lvl-error' : 'lvl-warn'},
          h('div.run-warn-head',
            h('div.run-warn-title', icon(errorCount ? 'error' : 'warning'),
              h('strong', summaryText)),
            h('div.row-inline',
              h('button.btn.btn-ghost.btn-xs#run-warn-open-constitution', {
                type: 'button',
                onclick: () => go(`/p/${pid}/constitution`),
              },
                icon('gavel'), 'Open Constitution'),
              h('button.btn.btn-ghost.btn-xs#run-warn-open-ds', {
                type: 'button',
                onclick: () => go(`/p/${pid}/design-system`),
              },
                icon('palette'), 'Design system'))),
          h('ul.run-warn-list', listItems)));
  }
  return h('div.run-readiness-banner', blocks);
}

/**
 * Checks readiness before starting a run/study and prompts the user to confirm
 * if components are still building or if the constitution has errors/warnings.
 */
export async function confirmRunWarnings(
    pid, {revisionId, mode = 'run', readiness = null, onNavigate = null} = {}) {
  const r = readiness || (await checkRunReadiness(pid, {revisionId}));
  if (!r || !r.needsWarning) return true;
  const isEffect = mode === 'effect';
  const title = r.hasBuilding && r.hasIssues ?
      'Components building & constitution issues' :
      r.hasBuilding ? 'Components are still building' :
      r.errorCount  ? 'Errors in constitution or catalog' :
                      'Constitution warnings before running';
  return new Promise((resolve) => {
    let settled = false;
    const finish = (v) => {
      if (settled) return;
      settled = true;
      resolve(v);
    };
    const body = h(
        'div.confirm-body.run-warn-confirm-body',
        renderReadinessBanner(pid, r, {
          onNavigate: (url) => {
            finish(false);
            dlg.close();
            if (onNavigate)
              onNavigate(url);
            else
              navigate(url);
          },
        }),
        h('p.muted.small', {style: 'margin-top: 10px;'},
          r.hasBuilding ?
              'Wait for component builds to finish so they are active in the catalog, or start the run anyway without them.' :
              'You can fix these issues in the Constitution or Design system first, or start the run anyway.'));
    const dlg = modal({
      title,
      id: 'run-warning-confirm-dialog',
      width: '560px',
      body,
      onClose: () => finish(false),
      actions: [
        {
          label: r.hasBuilding ? 'Wait / Cancel' : 'Cancel',
          id: 'confirm-run-cancel',
          onClick: () => finish(false),
        },
        {
          label: isEffect ? 'Start study anyway' : 'Start run anyway',
          id: 'confirm-run-anyway',
          kind: r.errorCount ? 'danger' : 'primary',
          onClick: () => finish(true),
        },
      ],
    });
  });
}

const DEFAULT_RENDERERS = [
  'web',
];

const FALLBACK_RENDERERS = [
  {
    id: 'web',
    label: 'Web (HTML/CSS)',
    short_label: 'Web',
    engine: 'Headless Chromium (CDP) + A2UI Web Renderer',
    badge: 'WEB',
  },
];

/**
 * Live library tree: categories → subcategories that have samples, with
 * counts. Built from the samples themselves so new/custom categories and
 * connected-app imports always show up; labels come from /api/genres.
 */
function buildLibrary(samples, groups) {
  const gLabel = new Map();
  const kLabel = new Map();
  const gCustom = new Map();
  const kCustom = new Map();
  for (const g of groups) {
    gLabel.set(g.id, g.label);
    gCustom.set(g.id, !!g.custom);
    for (const k of g.genres || []) {
      kLabel.set(`${g.id}::${k.id}`, k.label);
      kCustom.set(`${g.id}::${k.id}`, !!k.custom);
    }
  }
  const order = new Map(groups.map((g, i) => [g.id, i]));
  const byGroup = new Map();
  const dayAgo = Date.now() - 24 * 3600 * 1000;
  for (const x of samples) {
    const gid = x.group || 'uncategorized';
    const key = `${gid}::${x.genre}`;
    if (!byGroup.has(gid)) {
      byGroup.set(gid, {
        id: gid,
        label: gLabel.get(gid) || gid,
        custom: gCustom.get(gid) ?? !gLabel.has(gid),
        count: 0,
        apps: 0,
        recent: 0,
        genres: new Map(),
      });
    }
    const g = byGroup.get(gid);
    if (!g.genres.has(key)) {
      g.genres.set(key, {
        key,
        label: kLabel.get(key) ||
            String(x.genre || 'Uncategorized').replace(/_/g, ' '),
        custom: kCustom.get(key) ?? !kLabel.has(key),
        count: 0,
        apps: 0,
        recent: 0,
      });
    }
    const k = g.genres.get(key);
    const isApp = x.source === 'mcp';
    const isNew = x.created_at && Date.parse(x.created_at) > dayAgo;
    for (const t of [g, k]) {
      t.count++;
      if (isApp) t.apps++;
      if (isNew) t.recent++;
    }
  }
  return [...byGroup.values()]
      .map((g) => ({
             ...g,
             genres: [...g.genres.values()].sort(
                 (a, b) => a.label.localeCompare(b.label)),
           }))
      // Categories you created come first, then the built-in taxonomy order.
      .sort(
          (a, b) => (b.custom - a.custom) ||
              (order.get(a.id) ?? 1e9) - (order.get(b.id) ?? 1e9) ||
              a.label.localeCompare(b.label));
}

function libChips(t) {
  return [
    t.recent ? h('span.chip.chip-xs.lib-new',
                 {title: `${t.recent} added in the last 24 h`}, 'new') :
               null,
    t.apps ?
        h('span.chip.chip-xs.app-chip',
          {title: `${t.apps} from connected apps`}, icon('extension'), 'App') :
        null,
    t.custom ? h('span.chip.chip-xs.chip-quiet',
                 {title: 'Category you created'}, 'yours') :
               null,
  ];
}

/**
 * openNewRunDialog({projectId, preset:'baseline'|'selection'|null, sampleSetId,
 * sampleIds, revisionId, viewports, renderers, mode:'run'|'effect'})
 */
export async function openNewRunDialog(opts) {
  const pid = opts.projectId;
  const effect = opts.mode === 'effect';
  const body = h('div.newrun', spinner('Loading\u2026'));
  const dlg = modal({
    title: effect                  ? 'Constitution effect' :
        opts.preset === 'baseline' ? 'Run a baseline' :
                                     'New run',
    id: effect ? 'effect-dialog' : 'new-run-dialog',
    body,
    onClose: () => clearTimeout(readinessTimer),
    actions: [
      {label: 'Cancel'}, {
        label: effect ? 'Start study' : 'Start run',
        kind: 'primary',
        id: effect ? 'effect-start' : 'run-start',
        onClick: () => start()
      }
    ]
  });
  let revs = [];
  let sets = [];
  let vps = [];
  let renderersList = FALLBACK_RENDERERS;
  let settings = null;
  // sample id → source, to know whether the chosen samples include MCP ones.
  let sourceById = null;
  let allSamples = [];
  let genreGroups = [];
  try {
    const [r, s, v, st, renRes, smp, gen] = await Promise.all([
      get(`/api/projects/${pid}/revisions`),
      get('/api/sample_sets'),
      cached('/api/viewports'),
      get(`/api/projects/${pid}/settings`),
      get('/api/renderers').catch(() => ({renderers: FALLBACK_RENDERERS})),
      get('/api/samples', {quiet: true}).catch(() => null),
      get('/api/genres', {quiet: true}).catch(() => null),
    ]);
    allSamples = (smp && Array.isArray(smp.samples)) ? smp.samples : [];
    genreGroups = (gen && Array.isArray(gen.groups)) ? gen.groups : [];
    revs = r.revisions || [];
    sets = s.sets || [];
    vps = Array.isArray(v) ? v : (v.viewports || []);
    settings = st.settings || {};
    if (renRes && Array.isArray(renRes.renderers) && renRes.renderers.length) {
      renderersList = renRes.renderers;
    }
    if (smp && Array.isArray(smp.samples)) {
      sourceById = new Map(smp.samples.map((x) => [x.id, x.source]));
    }
  } catch (e) {
    mount(body, h('p.form-error', 'Could not load run options.'));
    return;
  }
  const curId = store.project && store.project.current_revision &&
      store.project.current_revision.id;
  const selectableRevs = effect ? revs.filter((r) => r.chars !== 0) : revs;
  const noEffectRevs = effect && selectableRevs.length === 0;
  const revSel = h(
      'select.input#run-revision', {disabled: noEffectRevs},
      noEffectRevs ?
          [h('option', {value: ''},
             'No non-empty constitution revisions yet')] :
          selectableRevs.map(
              (r) => h(
                  'option',
                  {value: r.id, selected: (opts.revisionId || curId) === r.id},
                  `rev ${r.number}${
                      r.id === curId ? ' (current)' : ''} \u2014 ${
                      r.summary || (r.number === 0 ? 'initial' : '')}`)));
  if (noEffectRevs && dlg.el) {
    const startBtn = dlg.el.querySelector('#effect-start');
    if (startBtn) startBtn.disabled = true;
  }

  const selIds = opts.sampleIds ||
      (opts.preset === 'selection' ? [...store.dataSelection] : []);
  const projDs = (settings && settings.data_source) || {};
  const projSetId = projDs.kind === 'set' && projDs.sample_set_id &&
          sets.some((s) => s.id === projDs.sample_set_id) ?
      projDs.sample_set_id :
      '';
  const projCatKey =
      projDs.kind === 'category' && projDs.category ? projDs.category : '';
  // Sources: 'library' (live categories, incl. new/custom ones and
  // connected-app imports), 'set' (frozen sample sets), 'selection'.
  let source = selIds.length ? 'selection' :
      (opts.sampleSetId || projSetId || opts.preset === 'baseline') ? 'set' :
                                                                      'library';
  const lib = buildLibrary(allSamples, genreGroups);
  const picked = new Set();  // "group::genre" keys
  if (projCatKey && !opts.sampleSetId && !selIds.length) {
    for (const g of lib) {
      if (g.id === projCatKey || g.label === projCatKey) {
        for (const k of g.genres) {
          if (!projDs.subcategory || k.id === projDs.subcategory ||
              k.label === projDs.subcategory) {
            picked.add(k.key);
          }
        }
      }
    }
  }
  const libIds = () =>
      allSamples.filter((x) => picked.has(`${x.group}::${x.genre}`))
          .map((x) => x.id);
  const allSet = sets.find((s) => /all genres/i.test(s.name)) ||
      sets.slice().sort((a, b) => (b.count || 0) - (a.count || 0))[0];
  const setSel = h('select.input.indent#run-sample-set', sets.map((s) => {
    const baseLabel = /\(\d+\)\s*$/.test(s.name) ?
        s.name :
        `${s.name} (${s.count ?? (s.sample_ids || []).length})`;
    const isDefault = projSetId && projSetId === s.id;
    return h(
        'option', {
          value: s.id,
          selected: opts.sampleSetId ? opts.sampleSetId === s.id :
              projSetId              ? projSetId === s.id :
                                       (allSet && allSet.id === s.id)
        },
        isDefault ? `${baseLabel} \u2014 project default` : baseLabel);
  }));
  const saveDefaultData =
      h('input#run-save-default-data', {type: 'checkbox', checked: false});
  const defaultDsBadge =
      h('span.chip.chip-xs.v-good#run-default-ds-badge', {
        hidden: true,
        title: 'Matches the project\u2019s configured default data source',
      },
        icon('dataset'), 'Project default');
  const srcSet = h('input', {
    type: 'radio',
    name: 'run-src',
    id: 'run-src-set',
    checked: source === 'set',
    onchange: () => {
      source = 'set';
      sync();
    }
  });
  const srcLib = h('input', {
    type: 'radio',
    name: 'run-src',
    id: 'run-src-library',
    checked: source === 'library',
    disabled: !allSamples.length,
    onchange: () => {
      source = 'library';
      sync();
    }
  });
  const libCount = h('span.muted.lib-count');
  const libTree = h('div.run-lib#run-library', lib.map((g) => {
    const boxes = [];
    const gBox = h('input', {
      type: 'checkbox',
      'aria-label': `All of ${g.label}`,
      onchange: (e) => {
        for (const k of g.genres) {
          e.target.checked ? picked.add(k.key) : picked.delete(k.key);
        }
        chooseLibrary();
      }
    });
    const rows = g.genres.map((k) => {
      const box = h('input', {
        type: 'checkbox',
        dataset: {key: k.key},
        onchange: (e) => {
          e.target.checked ? picked.add(k.key) : picked.delete(k.key);
          chooseLibrary();
        }
      });
      boxes.push(box);
      return h(
          'label.check.lib-genre', box, h('span', k.label),
          h('span.muted', `${k.count}`), libChips(k));
    });
    const details = h(
        'details.lib-group', {dataset: {group: g.id}},
        h('summary',
          h('label.check.lib-group-head', {onclick: (e) => e.stopPropagation()},
            gBox, h('span.lib-group-name', g.label),
            h('span.muted', `${g.count}`), libChips(g)),
          h('span.muted.lib-sub',
            `${g.genres.length} subcategor${
                g.genres.length === 1 ? 'y' : 'ies'}`)),
        h('div.lib-genres', rows));
    details._sync = () => {
      const n = g.genres.filter((k) => picked.has(k.key)).length;
      gBox.checked = n === g.genres.length;
      gBox.indeterminate = n > 0 && n < g.genres.length;
      for (const b of boxes) b.checked = picked.has(b.dataset.key);
    };
    return details;
  }));
  function chooseLibrary() {
    if (source !== 'library') {
      source = 'library';
      srcLib.checked = true;
    }
    sync();
  }
  function syncLibrary() {
    for (const d of libTree.children) d._sync && d._sync();
    const n = libIds().length;
    libCount.textContent = n ? `${n} selected` : 'pick categories';
  }
  const libAll =
      h('button.btn.btn-quiet.btn-xs#run-library-all', {
        type: 'button',
        onclick: () => {
          for (const g of lib)
            for (const k of g.genres) picked.add(k.key);
          chooseLibrary();
        }
      },
        'All');
  const libNone =
      h('button.btn.btn-quiet.btn-xs#run-library-none', {
        type: 'button',
        onclick: () => {
          picked.clear();
          chooseLibrary();
        }
      },
        'None');
  const srcSel = h('input', {
    type: 'radio',
    name: 'run-src',
    id: 'run-src-selection',
    checked: source === 'selection',
    disabled: !selIds.length,
    onchange: () => {
      source = 'selection';
      sync();
    }
  });
  const defaultVps = opts.viewports || settings.viewports || ['phone'];
  const vpBoxes = vps.map(
      (v) =>
          h('label.check.vp-check', h('input', {
              type: 'checkbox',
              value: v.id,
              id: `run-vp-${v.id}`,
              checked: defaultVps.includes(v.id)
            }),
            h('span', v.label || v.id),
            h('span.muted', `${v.width}\u00d7${v.height}`)));

  // The web renderer is the only renderer.
  void renderersList;

  const judge = h('input#run-judge', {
    type: 'checkbox',
    checked: settings.judge ? settings.judge.enabled !== false : true
  });
  const estimate = h('p.help.run-estimate');
  const noise = h('input#effect-noise', {type: 'checkbox', checked: true});
  const attribute =
      h('input#effect-attribute', {type: 'checkbox', checked: true});
  const projTemp =
      settings.generator && settings.generator.temperature != null ?
      settings.generator.temperature :
      0.7;
  const tempSel =
      h('select.input#effect-temperature',
        h('option', {value: ''}, `Project setting (${projTemp})`),
        h('option', {value: '0.2'}, '0.2 \u2014 less run-to-run noise'),
        h('option', {value: '0'}, '0 \u2014 near-deterministic'));
  const includeForms =
      h('input#run-include-forms', {type: 'checkbox', checked: true});
  const formsHelp = h('span.help.forms-help');
  const formsField =
      h('div.field.forms-field#run-forms-field',
        h('label.check', includeForms, 'Include input forms'), formsHelp);

  /** Number of connected-app samples in the current choice, or null. */
  function mcpCount() {
    if (!sourceById) return null;
    let ids;
    if (source === 'set') {
      const set = sets.find((s) => s.id === setSel.value);
      if (!set || !Array.isArray(set.sample_ids)) return null;
      ids = set.sample_ids;
    } else if (source === 'library') {
      ids = libIds();
    } else {
      ids = selIds;
    }
    return ids.filter((id) => sourceById.get(id) === 'mcp').length;
  }

  function sync() {
    setSel.disabled = source !== 'set';
    libTree.classList.toggle('inactive', source !== 'library');
    syncLibrary();
    const isUsingProjectDefault =
        (source === 'set' && projSetId && setSel.value === projSetId) ||
        (source === 'library' && projCatKey && picked.size > 0);
    defaultDsBadge.hidden = !isUsingProjectDefault;
    const n = source === 'set' ?
        ((sets.find((s) => s.id === setSel.value) || {}).count || 0) :
        source === 'library' ? libIds().length :
                               selIds.length;
    const nv = vpBoxes.filter((b) => b.querySelector('input').checked).length;
    const arms = effect ? (noise.checked ? 3 : 2) : 1;
    const nm = effect ? 0 : mcpCount();
    formsField.hidden = effect || nm === 0;
    formsHelp.textContent = nm == null ?
        'For samples imported from connected apps: also generate each tool\u2019s input form as a separate \u201cform\u201d output next to the result.' :
        `${nm} connected-app sample${
            nm === 1 ? '' : 's'} \u2014 also generate ${
            nm === 1 ?
                'its' :
                'each'} tool input form as a \u201cform\u201d output next to the result.`;
    const extra = !effect && includeForms.checked && nm ? nm * nv : 0;
    estimate.textContent =
        `${n} sample${n === 1 ? '' : 's'} \u00d7 ${nv} viewport${
            nv === 1 ? '' : 's'} = ${n * nv} surface${n * nv === 1 ? '' : 's'}${
            effect ? ` \u00d7 ${arms} arms (${n * nv * arms} generations)` :
                     ''}${
            extra ? ` + ${extra} input form${extra === 1 ? '' : 's'}` : ''}`;
  }
  noise.addEventListener('change', sync);
  includeForms.addEventListener('change', sync);
  srcSet.addEventListener('change', sync);
  srcLib.addEventListener('change', sync);
  srcSel.addEventListener('change', sync);
  setSel.addEventListener('change', sync);
  vpBoxes.forEach(
      (b) => b.querySelector('input').addEventListener('change', sync));

  const readinessWrap = h('div.run-readiness-wrap#run-readiness-warning');
  let readinessTimer = null;
  let latestReadiness = null;
  async function refreshReadiness() {
    clearTimeout(readinessTimer);
    if (!dlg.el || !dlg.el.isConnected) return;
    const r = await checkRunReadiness(pid, {revisionId: revSel.value});
    if (!dlg.el || !dlg.el.isConnected) return;
    latestReadiness = r;
    const banner = renderReadinessBanner(pid, r, {
      onNavigate: (url) => {
        clearTimeout(readinessTimer);
        dlg.close();
        navigate(url);
      },
    });
    mount(readinessWrap, banner);
    if (r.hasBuilding && dlg.el && dlg.el.isConnected) {
      readinessTimer = setTimeout(refreshReadiness, 2000);
    }
  }
  revSel.addEventListener('change', () => {
    latestReadiness = null;
    refreshReadiness();
  });

  mount(
      body, readinessWrap,
      opts.preset === 'baseline' ?
          h('p.lede.small',
            'Runs the current (empty) constitution so you can see what the generator does on its own. Then write rules from what goes wrong.') :
          null,
      effect ?
          h('p.lede.small',
            'Generates every sample twice \u2014 with no constitution and with the chosen revision \u2014 then diffs the layouts and outlines where and why the constitution changed them. Both arms are judged against the revision, so scores compare.') :
          null,
      h('label.field',
        h('span.field-label',
          effect ? 'Constitution revision (vs. no constitution)' :
                   'Constitution revision'),
        revSel,
        noEffectRevs ?
            h('p.help', {style: 'color: var(--bad, #b91c1c);'},
              'Save a constitution revision first (from Constitution or Teach) before running a Constitution Effect Study.') :
            null),
      h('div.field',
        h('div.row-inline', h('span.field-label', 'Samples'), defaultDsBadge),
        h('div.lib-head', h('label.radio', srcLib, 'From the data library'),
          libCount, h('span.lib-actions', libAll, libNone)),
        allSamples.length ? libTree :
                            h('p.help.indent', 'The data library is empty.'),
        h('label.radio', srcSet, 'Sample set'), setSel,
        !sets.length ? h('p.help', 'No sample sets yet \u2014 ',
                         h('a', {href: '/data', onclick: () => dlg.close()},
                           'generate the starter library'),
                         ' first.') :
                       null,
        h('label.radio', {class: selIds.length ? '' : 'disabled'}, srcSel,
          `Current data selection (${selIds.length})`),
        h('label.check.small', {style: 'margin-top: 6px;'}, saveDefaultData,
          'Save as default data source for runs in this project')),
      h('div.field', h('span.field-label', 'Viewports'),
        h('div.vp-grid', vpBoxes)),
      effect ?
          h('div.field', h('span.field-label', 'Study'),
            h('label.check', noise,
              'Run a second no-constitution arm to measure the noise floor'),
            h('label.check', attribute,
              'Attribute each change to constitution rules (LLM)'),
            h('label.field.indent',
              h('span.field-label', 'Generator temperature (all arms)'),
              tempSel),
            null) :
          null,
      effect ? null : formsField,
      h('label.check', judge, 'Judge outputs with the LLM judge'), estimate);
  sync();
  refreshReadiness();

  async function start() {
    const viewports = vpBoxes.map((b) => b.querySelector('input'))
                          .filter((i) => i.checked)
                          .map((i) => i.value);
    if (!viewports.length) {
      toast('Pick at least one viewport.', 'warn');
      return false;
    }
    const renderers = DEFAULT_RENDERERS;
    const req =
        {revision_id: revSel.value, viewports, renderers, judge: judge.checked};
    if (source === 'set') {
      if (!setSel.value) {
        toast('Pick a sample set.', 'warn');
        return false;
      }
      req.sample_set_id = setSel.value;
    } else if (source === 'library') {
      const ids = libIds();
      if (!ids.length) {
        toast('Pick at least one category or subcategory.', 'warn');
        return false;
      }
      req.sample_ids = ids;
    } else {
      req.sample_ids = selIds;
    }
    const proceed = await confirmRunWarnings(pid, {
      revisionId: revSel.value,
      mode: effect ? 'effect' : 'run',
      onNavigate: (url) => {
        clearTimeout(readinessTimer);
        dlg.close();
        navigate(url);
      },
    });
    if (!proceed) return false;
    clearTimeout(readinessTimer);
    if (saveDefaultData.checked) {
      try {
        const chosenSet =
            source === 'set' ? sets.find((s) => s.id === setSel.value) : null;
        let nextDs = {
          kind: '',
          sample_set_id: '',
          sample_set_name: '',
          category: '',
          subcategory: '',
          sample_ids: [],
          import_id: '',
          source_kind: '',
        };
        if (source === 'set' && chosenSet) {
          nextDs = {
            ...nextDs,
            kind: 'set',
            sample_set_id: chosenSet.id,
            sample_set_name: chosenSet.name || '',
            sample_ids: chosenSet.sample_ids || [],
          };
        } else if (source === 'library') {
          nextDs = {
            ...nextDs,
            kind: 'selection',
            sample_ids: req.sample_ids || [],
          };
        } else if (source === 'selection') {
          nextDs = {
            ...nextDs,
            kind: 'selection',
            sample_ids: selIds,
          };
        }
        const {settings: fresh} =
            await get(`/api/projects/${pid}/settings`, {quiet: true});
        const nextSettings = {...(fresh || {}), data_source: nextDs};
        const saved = await put(
            `/api/projects/${pid}/settings`, {settings: nextSettings});
        if (store.project && store.project.id === pid) {
          store.project.settings = saved.settings || nextSettings;
          emit('settings-changed', {
            settings: store.project.settings,
            source: 'newrun',
          });
        }
      } catch (_) {
      }
    }
    if (effect) {
      Object.assign(req, {
        noise: noise.checked,
        attribute: attribute.checked,
        temperature: tempSel.value === '' ? null : Number(tempSel.value),
      });
      const res = await post(`/api/projects/${pid}/effects`, req);
      if (store.project && store.project.id === pid && store.project.stats) {
        store.project.stats.runs =
            (store.project.stats.runs || 0) + (noise.checked ? 3 : 2);
        emit('project-updated');
      }
      toast('Constitution effect study started', 'good');
      navigate(`/p/${pid}/effects/${res.effect.id}`);
      return;
    }
    if (!formsField.hidden) req.include_forms = includeForms.checked;
    const {run} = await post(`/api/projects/${pid}/runs`, req);
    if (store.project && store.project.id === pid && store.project.stats) {
      store.project.stats.runs = (store.project.stats.runs || 0) + 1;
      emit('project-updated');
    }
    toast('Run started', 'good');
    navigate(`/p/${pid}/runs/${run.id}`);
  }
}
