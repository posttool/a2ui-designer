// Shared bits for the Design system pages: custom-component badges, job
// polling, Gemini fix proposals, and the accept/activate flow.
import {get, patch, post} from './api.js';
import {diffView, lineDiff} from './diff.js';
import {h, icon, mount, spinner} from './dom.js';
import {emit, navigate} from './store.js';
import {modal, toast} from './ui.js';

export const SOURCE_LABELS = {
  figma: ['Figma', 'tint-7', 'design_services'],
  screenshot: ['Screenshot', 'tint-3', 'image'],
  stitch: ['Stitch', 'tint-6', 'auto_awesome_mosaic'],
  describe: ['Described', 'tint-1', 'edit_note'],
  proposal: ['Proposal', 'tint-4', 'forum'],
  manual: ['Manual', '', 'construction'],
  legacy: ['Legacy', 'tint-2', 'history'],
};

export const JOB_STAGES =
    ['queued', 'extracting', 'drafting', 'rendering', 'comparing', 'repairing'];
const STAGE_LABEL = {
  queued: 'Queued',
  extracting: 'Reading the source',
  drafting: 'Drafting HTML/CSS',
  rendering: 'Rendering preview',
  comparing: 'Comparing to reference',
  repairing: 'Repairing mismatches',
  done: 'Done',
  error: 'Failed',
};

export function jobActive(c) {
  const st = c && c.job && c.job.stage;
  return !!st && st !== 'done' && st !== 'error';
}

export function sourceBadge(source) {
  const kind = (source && source.kind) || 'manual';
  const [label, tint, ic] = SOURCE_LABELS[kind] || [kind, '', 'widgets'];
  return h(
      'span.chip.src-badge', {class: tint, title: `Source: ${label}`}, icon(ic),
      label);
}

/**
 * Linked Figma files / Stitch projects (`project.design_sources`) as chips
 * that open the source in a new tab. `compact` drops the kind label.
 */
export function designSourceChips(sources, {compact = false} = {}) {
  const list = Array.isArray(sources) ? sources : [];
  if (!list.length) return null;
  return h(
      'div.chip-row.design-sources', {class: compact ? 'compact' : ''},
      list.map((s) => {
        const [label, tint, ic] = SOURCE_LABELS[s.kind] || [s.kind, '', 'link'];
        const uses = [
          s.references ?
              `${s.references} reference${s.references === 1 ? '' : 's'}` :
              '',
          s.components ?
              `${s.components} component${s.components === 1 ? '' : 's'}` :
              '',
        ].filter(Boolean).join(' · ');
        return h(
            'a.chip.design-source', {
              class: tint,
              href: s.url,
              target: '_blank',
              rel: 'noopener noreferrer',
              title: `${label}: ${s.name}${uses ? ` (${uses})` : ''}`,
              dataset: {kind: s.kind},
              onclick: (e) => e.stopPropagation(),
            },
            icon(ic), compact ? null : h('span.ds-kind', label),
            h('span.ds-name', s.name));
      }));
}

export function webOnlyChip(c = null) {
  const def = (c && c.definition) || {};
  const strategy = (c && c.strategy) || def.strategy || '';
  const targets = (c && c.targets) || Object.keys((def && def.targets) || {});
  const hasComp = Boolean(
      (c && c.has_composition) ||
      (def.composition && def.composition.root &&
       (def.composition.components || []).length));
  if (strategy === 'native_targets' || (targets && targets.length)) {
    return h(
        'span.chip.chip-quiet.web-only-chip', {
          title:
              'Ships dedicated Web HTML/CSS + Kotlin Jetpack Compose + RemoteCompose + iOS SwiftUI target implementations.'
        },
        icon('devices'), 'Multi-target Native');
  }
  if (strategy === 'basic_composition' || (hasComp && !(c && c.has_web))) {
    return h(
        'span.chip.chip-quiet.web-only-chip', {
          title:
              'Composed of A2UI Basic Catalog components — renders natively across Web, Android Compose, RemoteCompose, and iOS SwiftUI.'
        },
        icon('dashboard_customize'), 'All renderers (Basic)');
  }
  return h(
      'span.chip.chip-quiet.web-only-chip', {
        title:
            'Custom components render natively on Web only unless composed of Basic components or exported with Native target code.'
      },
      icon('language'), 'Web only');
}

export function statusChipFor(c) {
  const st = c.status || 'draft';
  const cls = st === 'active' ? 's-done' :
      st === 'archived'       ? 's-muted' :
                                's-queued';
  return h('span.status', {class: cls}, h('span.dot'), st);
}

export const STRATEGY_LABELS = {
  web_template: 'Custom web',
  basic_composition: 'Basic composition',
  native_targets: 'Native targets',
};

/** Short label of a construction strategy ('' → 'Auto'). */
export function strategyLabel(s) {
  return STRATEGY_LABELS[s] || (s ? s : 'Auto');
}

/**
 * "Basic fit 82% → Basic" chip: how faithfully the component's design maps
 * onto Basic Catalog components (0–1 from Figma/Stitch structure or Gemini's
 * estimate) and which construction the project's threshold picked. `c` is a
 * component (`basic_fit`, `strategy_decision`) or a merge-plan line detail;
 * pass `threshold` to preview the decision at another threshold.
 */
export function basicFitChip(c, {threshold = null, compact = false} = {}) {
  const fit = c && c.basic_fit;
  if (!fit || fit.score == null) return null;
  const dec = (c && c.strategy_decision) || {};
  const score = Number(fit.score);
  const pct = Math.round(score * 100);
  const pinned = dec.auto === false;
  const th = threshold != null ? Number(threshold) :
      dec.threshold != null    ? Number(dec.threshold) :
                                 null;
  let to = pinned ? dec.strategy : '';
  if (!to && th != null) {
    to = score >= th ? 'basic_composition' : 'web_template';
  }
  const cls = to === 'basic_composition' ? 'fit-basic' :
      to === 'native_targets'            ? 'fit-native' :
                                           'fit-custom';
  const srcText = fit.source === 'gemini' ?
      'Gemini estimate' :
      `from ${fit.source || 'design'} structure` +
          (fit.nodes ? ` (${fit.nodes} layers)` : '');
  const title = [
    `Basic fit ${pct}% \u2014 ${srcText}`,
    th != null ? `Threshold ${Math.round(th * 100)}%` : null,
    pinned ? `Pinned: ${strategyLabel(dec.strategy)}` : (dec.reason || null),
    ...((fit.summary || []).map((s) => `\u2022 ${s}`)),
  ].filter(Boolean).join('\n');
  return h(
      'span.chip.chip-quiet.fit-chip', {class: cls, title},
      icon('dashboard_customize'), compact ? `${pct}%` : `Basic fit ${pct}%`,
      to ? h('span.fit-arrow',
             ` \u2192 ${pinned ? 'pinned ' : ''}${
                 to === 'basic_composition'  ? 'Basic' :
                     to === 'native_targets' ? 'Native' :
                                               'Custom'}`) :
           null);
}

/** "★ 4/5 · 2 mismatches" chip, or null when never checked. */
export function fidelityChip(fidelity) {
  if (!fidelity || fidelity.score == null) return null;
  const s = Number(fidelity.score);
  const n = (fidelity.mismatches || []).length;
  const cls = s >= 4 ? 'v-good' : s >= 3 ? 'v-mixed' : 'v-bad';
  return h(
      'span.chip.fidelity-chip', {
        class: cls,
        title: n ? (fidelity.mismatches || [])
                       .map((m) => `${m.area || ''}: ${m.issue || ''}`)
                       .join('\n') :
                   'Visual match with the reference'
      },
      icon('compare'), `${s}/5`,
      n ? h('span.fidelity-n', ` \u00b7 ${n} mismatch${n === 1 ? '' : 'es'}`) :
          null);
}

export function issuesBadge(issues) {
  const list = issues || [];
  if (!list.length) return null;
  return h(
      'span.chip.c-bad.issues-badge', {title: list.join('\n')}, icon('error'),
      `${list.length} issue${list.length === 1 ? '' : 's'}`);
}

/** Soft badge for non-blocking warnings (e.g. hard-coded colors). */
export function warningsBadge(warnings) {
  const list = warnings || [];
  if (!list.length) return null;
  return h(
      'span.chip.v-mixed.warnings-badge', {title: list.join('\n')},
      icon('warning'), `${list.length} warning${list.length === 1 ? '' : 's'}`);
}

/** Drafts from chat proposals start without an implementation. */
export function needsBuild(c) {
  const tpl =
      c && c.definition && c.definition.web && c.definition.web.template;
  return !!c && !jobActive(c) && !String(tpl || '').trim() &&
      (c.source && c.source.kind) === 'proposal';
}

/** Asks Gemini to implement a proposal draft. Resolves to the updated row. */
export async function buildFromProposal(c) {
  const res = await post(`/api/projects/${c.project_id}/components/import`, {
    source: 'proposal',
    component_id: c.id,
    name: c.name || (c.definition && c.definition.name),
  });
  const list = (res && res.components) || [];
  return list.find((x) => x.id === c.id) || list[0] || null;
}

/** Progress row for a running import/re-check job (or its error). */
export function jobProgress(c) {
  const job = c && c.job;
  if (!job || !job.stage || job.stage === 'done') return null;
  if (job.stage === 'error') {
    return h(
        'div.comp-job.comp-job-error', icon('error'),
        h('span', job.error || 'The build failed.'));
  }
  const i = Math.max(0, JOB_STAGES.indexOf(job.stage));
  return h(
      'div.comp-job', h('span.spinner.spinner-sm'),
      h('span.comp-job-label', STAGE_LABEL[job.stage] || job.stage),
      h('div.comp-job-steps',
        JOB_STAGES.slice(0, 5).map((s, k) => h('span.comp-job-step', {
                                     class: k < i ? 'done' :
                                         k === i  ? 'on' :
                                                    '',
                                     title: STAGE_LABEL[s]
                                   }))));
}

/**
 * Polls GET /api/components/{cid} every `ms` while its job is running.
 * onUpdate(component) is called on every tick. Returns a stop() function.
 */
export function pollComponent(cid, onUpdate, ms = 2000) {
  let stopped = false;
  let t = null;
  const tick = async () => {
    if (stopped) return;
    try {
      const {component} = await get(`/api/components/${cid}`, {quiet: true});
      if (stopped) return;
      onUpdate(component);
      if (!jobActive(component)) return;
    } catch (e) {
      if (e && e.status === 404) return;
    }
    if (!stopped) t = setTimeout(tick, ms);
  };
  t = setTimeout(tick, ms);
  return () => {
    stopped = true;
    clearTimeout(t);
  };
}

/** Token suggestion rows ({token, value, reason, is_custom}). */
export function tokenSuggestionList(list) {
  return h(
      'ul.token-sugs',
      (list || [])
          .map(
              (s) => h(
                  'li.token-sug', h('code.token-key-name', s.token),
                  s.is_custom ? h('span.chip.chip-xs', 'custom') : null,
                  /^#[0-9a-f]{3,8}$/i.test(String(s.value || '')) ?
                      h('span.token-swatch', {style: {background: s.value}}) :
                      null,
                  h('code.token-sug-val', String(s.value ?? '')),
                  s.reason ? h('span.muted.small.token-sug-reason', s.reason) :
                             null)));
}

export function normalizeComponentDef(d) {
  const def = JSON.parse(JSON.stringify(d || {}));
  def.name = def.name || '';
  def.description = def.description || '';
  def.usage = def.usage || '';
  def.props = def.props && typeof def.props === 'object' ? def.props : {};
  def.props.type = def.props.type || 'object';
  def.props.properties = def.props.properties || {};
  def.props.required =
      Array.isArray(def.props.required) ? def.props.required : [];
  def.web = def.web || {};
  def.web.template = def.web.template || '';
  def.web.css = def.web.css || '';
  def.sample = def.sample || {};
  def.sample.props = def.sample.props || {};
  def.sample.data = def.sample.data || {};
  def.sample.components = def.sample.components || [];
  def.strategy = def.strategy || '';
  def.composition = def.composition && typeof def.composition === 'object' ?
      def.composition :
      {root: '', components: []};
  def.composition.root = def.composition.root || '';
  def.composition.components = Array.isArray(def.composition.components) ?
      def.composition.components :
      [];
  def.targets =
      def.targets && typeof def.targets === 'object' ? def.targets : {};
  return def;
}

/**
 * Opens a modal with a Gemini-proposed revision or fix for component `c`.
 * When `res` is omitted, calls POST /api/components/{cid}/revise first with a
 * loading state inside the modal.
 *
 * opts: {
 *   definition?: current editor definition,
 *   extraIssues?: string[],
 *   instruction?: string,
 *   fix?: boolean (default true),
 *   res?: pre-fetched revise response,
 *   onApplyToEditor?: (nextDef, res) => void,
 *   onSaved?: (updatedComponent, {activated, appliedTokens}) => void,
 * }
 * Resolves to the saved/activated component, or null if dismissed / only
 * applied to editor.
 */
export async function proposeComponentFixModal(c, opts = {}) {
  const curDef = normalizeComponentDef(opts.definition || c.definition);
  const compName = curDef.name || c.name || 'component';
  const curIssues = [
    ...new Set([...(c.issues || []), ...(opts.extraIssues || [])]),
  ];
  const curWarns = c.warnings || [];

  let res = opts.res || null;
  if (!res) {
    let cancelled = false;
    const waitModal = modal({
      title: `Proposing fix for ${compName}\u2026`,
      id: 'comp-fix-loading-modal',
      body:
          h('div.comp-fix-loading',
            spinner(
                'Gemini is inspecting the component and proposing fixes\u2026'),
            curIssues.length ?
                h('div.callout.callout-bad.comp-errs', icon('error'),
                  h('div',
                    h('div.callout-title',
                      `Fixing ${curIssues.length} issue${
                          curIssues.length === 1 ? '' : 's'}`),
                    h('ul', curIssues.map((x) => h('li', x))))) :
                null,
            curWarns.length ? h('div.callout.comp-warns', icon('lightbulb'),
                                h('div',
                                  h('div.callout-title',
                                    `Addressing ${curWarns.length} warning${
                                        curWarns.length === 1 ? '' : 's'}`),
                                  h('ul', curWarns.map((x) => h('li', x))))) :
                              null),
      actions: [{
        label: 'Cancel',
        onClick: () => {
          cancelled = true;
        },
      }],
    });
    try {
      res = await post(`/api/components/${c.id}/revise`, {
        instruction: opts.instruction || '',
        definition: curDef,
        issues: curIssues,
        fix: opts.fix !== false,
      });
    } catch (e) {
      waitModal.close();
      return null;
    }
    waitModal.close();
    if (cancelled || !res) return null;
  }

  const next = normalizeComponentDef({...curDef, ...(res.definition || {})});
  if (!next.name) next.name = compName;
  const notes = Array.isArray(res.notes) ? res.notes.filter(Boolean) :
      res.notes                          ? [String(res.notes)] :
                                           [];
  const nextIssues = res.issues || [];
  const nextWarns = res.warnings || [];
  const fixedIssues = res.fixed_issues || [];
  const sugs = res.token_suggestions || c.token_suggestions || [];
  const parts = [
    ['Description', curDef.description, next.description],
    ['Usage', curDef.usage, next.usage],
    [
      'Props',
      JSON.stringify(curDef.props, null, 2),
      JSON.stringify(next.props, null, 2),
    ],
    ['Template', curDef.web.template, next.web.template],
    ['CSS', curDef.web.css, next.web.css],
    [
      'Sample',
      JSON.stringify(curDef.sample, null, 2),
      JSON.stringify(next.sample, null, 2),
    ],
  ].filter(([, a, b]) => a !== b);

  const canActivate = c.status !== 'active' && nextIssues.length === 0;
  const tokenCb =
      h('input#revise-apply-tokens', {type: 'checkbox', checked: true});

  return new Promise((resolve) => {
    let outcome = null;
    const actions = [{label: 'Dismiss', id: 'comp-revise-dismiss'}];
    if (parts.length || nextIssues.length < curIssues.length) {
      if (opts.onApplyToEditor) {
        actions.push({
          label: 'Apply to editor',
          kind: canActivate ? 'ghost' : 'primary',
          id: 'comp-revise-apply',
          onClick: () => {
            opts.onApplyToEditor(next, res);
            toast(
                'Proposed fix applied to editor \u2014 save to keep it',
                'good');
          },
        });
      }
      actions.push({
        label: opts.onApplyToEditor ? 'Save fix' : 'Apply & save',
        kind: canActivate ? 'ghost' : 'primary',
        id: 'comp-revise-save',
        onClick: async () => {
          const {component: saved} =
              await patch(`/api/components/${c.id}`, {definition: next});
          outcome = saved;
          emit('components-changed', {component: saved});
          if (opts.onSaved) {
            opts.onSaved(saved, {activated: false, appliedTokens: false});
          }
          toast(`Saved fixes to ${saved.name || compName}`, 'good');
        },
      });
      if (canActivate) {
        actions.push({
          label: 'Save & Activate',
          kind: 'primary',
          id: 'comp-revise-activate',
          onClick: async () => {
            const body = {definition: next, status: 'active'};
            if (sugs.length) body.apply_token_suggestions = tokenCb.checked;
            const {component: saved} =
                await patch(`/api/components/${c.id}`, body);
            outcome = saved;
            emit('components-changed', {component: saved});
            if (opts.onSaved) {
              opts.onSaved(saved, {
                activated: true,
                appliedTokens: sugs.length > 0 && tokenCb.checked,
              });
            }
            toast(`${saved.name || compName} fixed and activated`, 'good');
          },
        });
      }
    }

    modal({
      title: opts.fix === false ? `Revision of ${compName}` :
                                  `Proposed fix for ${compName}`,
      wide: true,
      id: 'comp-revision-modal',
      body: h(
          'div.comp-revision',
          h('div.chip-row.comp-revise-status',
            nextIssues.length === 0 ?
                h('span.chip.v-good', icon('check_circle'),
                  fixedIssues.length ? `Fixed ${fixedIssues.length} issue${
                                           fixedIssues.length === 1 ?
                                               '' :
                                               's'} \u00b7 Ready to activate` :
                                       '0 issues \u00b7 Ready to activate') :
                issuesBadge(nextIssues),
            warningsBadge(nextWarns)),
          notes.length ? h('div.comp-revise-notes', icon('auto_awesome'),
                           h('div', notes.map((n) => h('p.small', n)))) :
                         null,
          sugs.length && canActivate ?
              h('div.comp-revise-tokens',
                h('div.field-label', 'Suggested design tokens'),
                tokenSuggestionList(sugs),
                h('label.check', tokenCb,
                  `Also apply ${sugs.length} token suggestion${
                      sugs.length === 1 ? '' : 's'} when activating`)) :
              null,
          parts.length ?
              parts.map(
                  ([label, a, b]) =>
                      h('div.comp-revise-part', h('div.field-label', label),
                        diffView(lineDiff(a, b), {
                          aLabel: 'Current',
                          bLabel: 'Proposed',
                          compact: true,
                          context: 2,
                          lang: label === 'Template' ? 'html' :
                              label === 'CSS'        ? 'css' :
                                                       '',
                        }))) :
              h('p.muted.small', 'No changes needed.')),
      actions,
      onClose: () => resolve(outcome),
    });
  });
}

/**
 * Activates a component. If the component has blocking issues, presents them
 * in a dialog where the user can ask Gemini to propose a fix with one click.
 * When it carries token suggestions, asks whether to apply them too.
 * Resolves to the updated component, or null if cancelled.
 */
export async function activateComponent(c, opts = {}) {
  const curDef = normalizeComponentDef(opts.definition || c.definition);
  const compName = curDef.name || c.name || 'component';
  const hasTpl = Boolean((curDef.web && curDef.web.template || '').trim()) ||
      Boolean(curDef.composition && curDef.composition.root &&
              (curDef.composition.components || []).length);
  const allIssues = [
    ...new Set([
      ...(c.issues || []),
      ...(opts.extraIssues || []),
      ...(!hasTpl && (c.classification || 'custom') === 'custom' ?
              ['web template is empty'] :
              []),
    ]),
  ];

  const promptFixDialog = (issuesList) => new Promise((resolve) => {
    let wantFix = false;
    let wantEdit = false;
    const actions = [{label: 'Cancel'}];
    if (!opts.inEditor && c.project_id && c.id) {
      actions.push({
        label: 'Edit manually',
        kind: 'ghost',
        id: 'comp-activate-edit',
        onClick: () => {
          wantEdit = true;
        },
      });
    }
    actions.push({
      label: [icon('auto_awesome'), 'Propose fix with Gemini'],
      kind: 'primary',
      id: 'comp-activate-propose-fix',
      onClick: () => {
        wantFix = true;
      },
    });
    modal({
      title: `Fix issues before activating ${compName}`,
      id: 'comp-activate-issues-modal',
      body: [
        h('p.muted',
          `${compName} has validation errors that prevent activation. Gemini can inspect these errors and propose a fix right here:`),
        h('div.callout.callout-bad.comp-errs', icon('error'),
          h('div',
            h('div.callout-title',
              `${issuesList.length} blocking issue${
                  issuesList.length === 1 ? '' : 's'}`),
            h('ul', issuesList.map((x) => h('li', x))))),
        (c.warnings || []).length ?
            h('div.callout.comp-warns', icon('lightbulb'),
              h('div',
                h('div.callout-title',
                  `${c.warnings.length} suggestion${
                      c.warnings.length === 1 ? '' : 's'}`),
                h('ul', c.warnings.map((x) => h('li', x))))) :
            null,
      ],
      actions,
      onClose: () => resolve({wantFix, wantEdit}),
    });
  });

  if (allIssues.length) {
    const {wantFix, wantEdit} = await promptFixDialog(allIssues);
    if (wantEdit) {
      navigate(`/p/${c.project_id}/design/components/${c.id}`);
      return null;
    }
    if (!wantFix) return null;
    return proposeComponentFixModal(c, {
      ...opts,
      definition: curDef,
      extraIssues: allIssues,
      fix: true,
    });
  }

  const sugs = c.token_suggestions || [];
  let apply = false;
  if (sugs.length) {
    const ok = await new Promise((resolve) => {
      let accepted = false;
      const cb =
          h('input#accept-apply-tokens', {type: 'checkbox', checked: true});
      modal({
        title: `Activate ${compName}?`,
        body: [
          h('p.muted',
            'The generator can use it in new runs. It also suggests design-token changes:'),
          tokenSuggestionList(sugs),
          h('label.check', cb,
            `Also apply ${sugs.length} token suggestion${
                sugs.length === 1 ? '' : 's'}`),
        ],
        actions: [
          {label: 'Cancel'},
          {
            label: 'Activate',
            kind: 'primary',
            id: 'accept-confirm',
            onClick: () => {
              accepted = true;
              apply = cb.checked;
            }
          },
        ],
        onClose: () => resolve(accepted),
      });
    });
    if (!ok) return null;
  }
  const body = {status: 'active'};
  if (sugs.length) body.apply_token_suggestions = apply;
  try {
    const {component} = await patch(`/api/components/${c.id}`, body);
    return component;
  } catch (e) {
    const msg = String((e && e.message) || '');
    if (msg.toLowerCase().includes('issue')) {
      const parsed = msg.replace(/^Fix the component's issues first:\s*/i, '')
                         .split(';')
                         .map((s) => s.trim())
                         .filter(Boolean);
      const {wantFix, wantEdit} =
          await promptFixDialog(parsed.length ? parsed : [msg]);
      if (wantEdit) {
        navigate(`/p/${c.project_id}/design/components/${c.id}`);
        return null;
      }
      if (wantFix) {
        return proposeComponentFixModal(c, {
          ...opts,
          definition: curDef,
          extraIssues: parsed,
          fix: true,
        });
      }
    }
    throw e;
  }
}

/** CSS var names (--a2ui-*) → [component names] over active components. */
export function tokenUsage(components) {
  const map = {};
  for (const c of components || []) {
    if (c.status !== 'active') continue;
    for (const v of c.token_refs || []) {
      (map[v] = map[v] || []).push(c.name || c.definition?.name);
    }
  }
  return map;
}
