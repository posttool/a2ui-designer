// Custom component editor: definition (props table / JSON, template, CSS),
// live Web preview in a /render/live iframe, and the reference panel
// (fidelity, re-check, revise with Gemini).
// Route: /p/:pid/design/components/:cid
import {del, get, patch, post} from '../components/api.js';
import {enhanceCodeEditor, formatCss, formatHtml} from '../components/code_highlight.js';
import {activateComponent, basicFitChip, buildFromProposal, fidelityChip, issuesBadge, jobActive, jobProgress, needsBuild, normalizeComponentDef as normalizeDef, pollComponent, proposeComponentFixModal, sourceBadge, statusChipFor, strategyLabel, tokenSuggestionList, warningsBadge, webOnlyChip} from '../components/component_badges.js';
import {debounce, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {navigate} from '../components/store.js';
import {confirmDialog, segmented, toast} from '../components/ui.js';

const REF = 'common_types.json#/$defs/';
// [type key, short label, title]
const PROP_TYPES = [
  ['DynamicString', 'Text', 'DynamicString: literal or {path} binding'],
  ['DynamicNumber', 'Number', 'DynamicNumber'],
  ['DynamicBoolean', 'Boolean', 'DynamicBoolean'],
  ['DynamicStringList', 'Text list', 'DynamicStringList'],
  ['enum', 'Enum', 'string with enum values (variants)'],
  ['ComponentId', 'Slot (one)', 'ComponentId: one child component'],
  ['ChildList', 'Slot (many)', 'ChildList: child components'],
  ['Action', 'Action', 'Action (event or function call)'],
  ['array', 'Array', 'array of objects (literal or {path} binding)'],
];
const REF_TYPES = new Set([
  'DynamicString', 'DynamicNumber', 'DynamicBoolean', 'DynamicStringList',
  'ComponentId', 'ChildList', 'Action'
]);
const NO_DEFAULT = new Set(['ComponentId', 'ChildList', 'Action', 'array']);

/** Schema → editor type key ('raw' when it's something else). */
function typeOf(schema) {
  const s = schema || {};
  if (typeof s.$ref === 'string') {
    const m = s.$ref.match(/\/([A-Za-z]+)$/);
    if (m && REF_TYPES.has(m[1])) return m[1];
    return 'raw';
  }
  if (s.type === 'string' && Array.isArray(s.enum)) return 'enum';
  if (s.type === 'array') return 'array';
  return 'raw';
}

function schemaFor(type, prev) {
  const keep = {};
  if (prev && prev.description) keep.description = prev.description;
  if (prev && prev.default !== undefined && !NO_DEFAULT.has(type))
    keep.default = prev.default;
  if (REF_TYPES.has(type)) return {$ref: `${REF}${type}`, ...keep};
  if (type === 'enum') {
    return {
      type: 'string',
      enum: Array.isArray(prev && prev.enum) && prev.enum.length ? prev.enum :
                                                                   ['default'],
      ...keep
    };
  }
  if (type === 'array') {
    return {
      type: 'array',
      items: (prev && prev.items) || {type: 'object', properties: {}},
      ...(prev && prev.description ? {description: prev.description} : {})
    };
  }
  return {...(prev || {})};
}

function parseDefault(type, raw) {
  const t = String(raw).trim();
  if (t === '') return undefined;
  if (type === 'DynamicNumber') return isNaN(Number(t)) ? t : Number(t);
  if (type === 'DynamicBoolean')
    return t === 'true' ? true : t === 'false' ? false : t;
  if (type === 'DynamicStringList') {
    try {
      const v = JSON.parse(t);
      if (Array.isArray(v)) return v;
    } catch (_) { /* fall through */
    }
    return t.split(',').map((x) => x.trim()).filter(Boolean);
  }
  return t;
}

function fmtDefault(v) {
  if (v === undefined || v === null) return '';
  return typeof v === 'object' ? JSON.stringify(v) : String(v);
}

/** Tab inserts two spaces; Shift+Tab un-indents the current line. */
function codeKeys(ta, onChange) {
  ta.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab' || e.metaKey || e.ctrlKey || e.altKey) return;
    e.preventDefault();
    const {selectionStart: s, selectionEnd: en, value: v} = ta;
    if (e.shiftKey) {
      const ls = v.lastIndexOf('\n', s - 1) + 1;
      const n = v.slice(ls, ls + 2) === '  ' ? 2 : v[ls] === ' ' ? 1 : 0;
      if (!n) return;
      ta.value = v.slice(0, ls) + v.slice(ls + n);
      ta.selectionStart = ta.selectionEnd = Math.max(ls, s - n);
    } else {
      ta.value = `${v.slice(0, s)}  ${v.slice(en)}`;
      ta.selectionStart = ta.selectionEnd = s + 2;
    }
    onChange();
  });
}

export async function render(root, params) {
  const {pid, cid} = params;
  let alive = true;
  let stopPoll = null;
  const [{component: loaded}, settingsRes, listRes] = await Promise.all([
    get(`/api/components/${cid}`),
    get(`/api/projects/${pid}/settings`).catch(() => ({settings: {}})),
    get(`/api/projects/${pid}/components`).catch(() => ({components: []})),
  ]);
  let comp = loaded;
  const settings = settingsRes.settings || {};
  const catalogId = (settings.catalog && settings.catalog.catalog_id) ||
      listRes.catalog_id || 'https://dcb.local/catalogs/project';
  let def = normalizeDef(comp.definition);
  if (def.web.template && !def.web.template.trim().includes('\n') &&
      def.web.template.includes('><')) {
    def.web.template = formatHtml(def.web.template);
  }
  if (def.web.css && !def.web.css.trim().includes('\n') &&
      def.web.css.includes('{')) {
    def.web.css = formatCss(def.web.css);
  }
  let dirty = false;
  let scheme =
      (settings.theme && settings.theme.scheme) === 'dark' ? 'dark' : 'light';
  const srcWidth = Number(
      (comp.source && (comp.source.width || comp.source.frame_width)) ||
      (comp.definition && comp.definition.preview_width) || 0);
  let viewport = srcWidth ? 'source' : 'phone';

  // Optional client-side compiler from the renderer (may not exist yet).
  let compileFn = null;
  import('/renderer/host.js').then((m) => {if (typeof m.compileCustomComponent === 'function') {
          compileFn = m.compileCustomComponent;
          drawCompile();
                             }}).catch(() => {});

  // ---------------------------------------------------------------- head
  const dirtyEl =
      h('span.dirty', {hidden: true}, h('span.dot'), 'Unsaved changes');
  const saveBtn =
      h('button.btn.btn-primary#comp-save',
        {type: 'button', disabled: true, onclick: () => save()}, icon('save'),
        'Save');
  const statusBtn =
      h('button.btn.btn-ghost#comp-status',
        {type: 'button', onclick: () => toggleStatus()});
  const deleteBtn =
      h('button.btn.btn-danger-quiet#comp-delete',
        {type: 'button', onclick: () => remove()}, icon('delete'), 'Delete');
  const titleEl = h('h1.comp-ed-title');
  const chipsEl = h('div.chip-row.comp-ed-chips');

  function drawHead() {
    titleEl.textContent = def.name || 'Untitled component';
    mount(
        chipsEl, statusChipFor(comp), sourceBadge(comp.source),
        webOnlyChip({...comp, definition: def}), basicFitChip(comp),
        issuesBadge(comp.issues), warningsBadge(comp.warnings), dirtyEl);
    mount(
        statusBtn,
        comp.status === 'active' ? [icon('pause_circle'), 'Deactivate'] :
                                   [icon('check_circle'), 'Activate']);
    drawStrategyHelp();
  }

  // Explains what "Auto" resolves to for this component (Basic fit vs. the
  // project threshold) and whether the strategy is pinned.
  const strategyHelp = h('span.help#comp-strategy-help');
  function drawStrategyHelp() {
    const fit = comp.basic_fit;
    const dec = comp.strategy_decision || {};
    const pct =
        fit && fit.score != null ? Math.round(Number(fit.score) * 100) : null;
    const th =
        dec.threshold != null ? Math.round(Number(dec.threshold) * 100) : null;
    const parts = [];
    if (strategyIsAuto()) {
      if (pct != null && th != null) {
        parts.push(`Auto: Basic fit ${pct}% ${pct >= th ? '\u2265' : '<'} ${
            th}% threshold \u2192 ${
            pct >= th ?
                'Basic catalog composition' :
                'custom web component'}. Pick another option to pin it.`);
      } else if (dec.reason) {
        parts.push(`Auto: ${dec.reason}.`);
      } else {
        parts.push(
            'Auto follows the project rule in Design system \u2192 Components.');
      }
    } else {
      parts.push(`Pinned to ${strategyLabel(def.strategy)}${
          pct != null ?
              ` (Basic fit ${pct}%)` :
              ''}. Choose Auto to follow the project threshold again.`);
    }
    if (fit && (fit.summary || []).length) {
      parts.push(`Basic catalog would lose: ${fit.summary.join('; ')}.`);
    } else if (fit && fit.model_reason && fit.source === 'gemini') {
      parts.push(fit.model_reason);
    }
    strategyHelp.textContent = parts.join(' ');
  }

  function markDirty() {
    dirty = true;
    saveBtn.disabled = false;
    dirtyEl.hidden = false;
  }

  // ---------------------------------------------------------- definition
  const nameIn = h('input.input.mono#comp-name', {
    value: def.name,
    spellcheck: 'false',
    oninput: () => {
      def.name = nameIn.value.trim();
      titleEl.textContent = def.name || 'Untitled component';
      changed();
    },
  });
  const descIn =
      h('textarea.input#comp-description', {
        rows: 2,
        oninput: () => {
          def.description = descIn.value;
          changed(false);
        },
      },
        def.description);
  const usageIn =
      h('textarea.input#comp-usage', {
        rows: 3,
        placeholder:
            'When to use it, and when not to (goes into the generator prompt).',
        oninput: () => {
          def.usage = usageIn.value;
          changed(false);
        },
      },
        def.usage);
  // The project's `auto` rule writes its pick onto `definition.strategy`
  // (so renderers lower Basic compositions on Web); the editor shows such a
  // frozen auto decision as "Auto" and only treats a *different* value as
  // pinned by the designer.
  function autoStrategy() {
    const dec = comp.strategy_decision || {};
    return dec.auto ? (dec.strategy || 'web_template') : null;
  }
  function strategyIsAuto() {
    if (!def.strategy) return true;
    const a = autoStrategy();
    return a != null && def.strategy === a;
  }
  function autoOptionLabel() {
    const a = autoStrategy();
    const fit = comp.basic_fit;
    const pct =
        fit && fit.score != null ? Math.round(Number(fit.score) * 100) : null;
    if (!a) return 'Auto (project default)';
    return `Auto \u2192 ${strategyLabel(a)}${
        pct != null ? ` (Basic fit ${pct}%)` : ''}`;
  }
  const autoOption = h('option', {value: ''}, autoOptionLabel());
  const strategySel = h(
      'select.input.input-sm#comp-strategy', {
        onchange: () => {
          def.strategy = strategySel.value || '';
          drawHead();
          changed();
        },
      },
      autoOption,
      h('option',
        {value: 'web_template', selected: def.strategy === 'web_template'},
        'Web HTML/CSS Template'),
      h('option', {
        value: 'basic_composition',
        selected: def.strategy === 'basic_composition'
      },
        'A2UI Basic Composition (any A2UI renderer)'));
  function syncStrategySelect() {
    autoOption.textContent = autoOptionLabel();
    strategySel.value = strategyIsAuto() ? '' : def.strategy;
  }
  const compositionTa = h('textarea.input.code-input#comp-composition-json', {
    rows: 10,
    spellcheck: 'false',
    oninput: () => {
      try {
        const v = JSON.parse(compositionTa.value || '{}');
        if (!v || typeof v !== 'object' || Array.isArray(v))
          throw new Error('composition must be an object {root, components}');
        def.composition = normalizeDef({composition: v}).composition;
        compositionErr.textContent = '';
        drawHead();
        changed();
      } catch (e) {
        compositionErr.textContent = `Invalid JSON: ${e.message}`;
      }
    },
  });
  codeKeys(
      compositionTa, () => compositionTa.dispatchEvent(new Event('input')));
  const compositionErr = h('p.form-error#comp-composition-error');
  const toTemplateBtn =
      h('button.btn.btn-ghost.btn-sm#comp-to-template', {
        type: 'button',
        onclick: async () => {
          toTemplateBtn.disabled = true;
          try {
            const res = await post(`/api/components/${cid}/to_template`, {});
            if (res && res.component) {
              updateFromServer(res.component, {keepDef: false});
            }
            toast(
                `Saved "${
                    (res.template && res.template.name) ||
                    def.name}" to Templates`,
                'good');
          } finally {
            toTemplateBtn.disabled = false;
          }
        },
      },
        icon('dashboard_customize'), 'Save as Basic Template');
  function syncCompositionEditor() {
    compositionTa.value =
        JSON.stringify(def.composition || {root: '', components: []}, null, 2);
    compositionErr.textContent = '';
  }

  let propsMode = 'table';
  const propsTableEl = h('div.props-table#comp-props-table');
  const propsJson = h('textarea.input.code-input#comp-props-json', {
    rows: 14,
    spellcheck: 'false',
    hidden: true,
    oninput: () => {
      try {
        const v = JSON.parse(propsJson.value);
        if (!v || typeof v !== 'object' || Array.isArray(v))
          throw new Error('props must be an object schema');
        def.props = normalizeDef({props: v}).props;
        propsErr.textContent = '';
        changed();
      } catch (e) {
        propsErr.textContent = `Invalid JSON: ${e.message}`;
      }
    },
  });
  codeKeys(propsJson, () => propsJson.dispatchEvent(new Event('input')));
  const propsErr = h('p.form-error#comp-props-error');
  const propsSeg = segmented(
      [
        {value: 'table', label: 'Table', id: 'props-mode-table'},
        {value: 'json', label: 'JSON', id: 'props-mode-json'}
      ],
      propsMode, (v) => {
        propsMode = v;
        propsErr.textContent = '';
        if (v === 'json')
          propsJson.value = JSON.stringify(def.props, null, 2);
        else
          drawPropsTable();
        propsJson.hidden = v !== 'json';
        propsTableEl.hidden = v !== 'table';
      });

  function drawPropsTable() {
    const props = def.props.properties;
    const req = new Set(def.props.required);
    const rows = Object.entries(props).map(([name, schema]) => {
      const type = typeOf(schema);
      const rename = (e) => {
        const nn = e.target.value.trim();
        if (!nn || nn === name) {
          e.target.value = name;
          return;
        }
        if (props[nn]) {
          toast(`A prop named ${nn} already exists`, 'warn');
          e.target.value = name;
          return;
        }
        // Keep order.
        const next = {};
        for (const [k, v] of Object.entries(props))
          next[k === name ? nn : k] = v;
        def.props.properties = next;
        def.props.required = def.props.required.map((r) => r === name ? nn : r);
        if (name in def.sample.props) {
          def.sample.props[nn] = def.sample.props[name];
          delete def.sample.props[name];
          syncSampleEditor();
        }
        changed();
        drawPropsTable();
      };
      const typeSel =
          h('select.input.input-sm.prop-type', {
            'aria-label': `Type of ${name}`,
            onchange: (e) => {
              props[name] = schemaFor(e.target.value, schema);
              changed();
              drawPropsTable();
            },
          },
            type === 'raw' ?
                h('option', {value: 'raw', selected: true, disabled: true},
                  'Custom schema (JSON)') :
                null,
            PROP_TYPES.map(
                ([v, l, t]) => h(
                    'option', {value: v, selected: v === type, title: t}, l)));
      return h(
          'div.props-row', {dataset: {prop: name}},
          h('input.input.input-sm.mono.prop-name',
            {value: name, 'aria-label': 'Prop name', onchange: rename}),
          typeSel,
          h('label.prop-req', {title: 'Required'}, h('input', {
              type: 'checkbox',
              checked: req.has(name),
              onchange: (e) => {
                const s = new Set(def.props.required);
                e.target.checked ? s.add(name) : s.delete(name);
                def.props.required = [...s];
                changed();
              },
            }),
            'req'),
          type === 'enum' ?
              h('input.input.input-sm.mono.prop-enum', {
                value: (schema.enum || []).join(', '),
                placeholder: 'values, comma-separated',
                'aria-label': `Enum values of ${name}`,
                onchange: (e) => {
                  schema.enum = e.target.value.split(',')
                                    .map((x) => x.trim())
                                    .filter(Boolean);
                  changed();
                },
              }) :
              type === 'array' ?
              h('span.muted.small.prop-enum',
                {title: JSON.stringify(schema.items || {})},
                `items: ${
                    Object.keys((schema.items && schema.items.properties) || {})
                        .join(', ') ||
                    'object'}`) :
              h('span.prop-enum'),
          NO_DEFAULT.has(type) || type === 'raw' ?
              h('span.prop-default') :
              h('input.input.input-sm.prop-default', {
                value: fmtDefault(schema.default),
                placeholder: 'default',
                'aria-label': `Default of ${name}`,
                onchange: (e) => {
                  const v = parseDefault(type, e.target.value);
                  if (v === undefined)
                    delete schema.default;
                  else
                    schema.default = v;
                  changed();
                },
              }),
          h('input.input.input-sm.prop-desc', {
            value: schema.description || '',
            placeholder: 'description',
            'aria-label': `Description of ${name}`,
            onchange: (e) => {
              if (e.target.value.trim())
                schema.description = e.target.value.trim();
              else
                delete schema.description;
              changed(false);
            },
          }),
          h('button.icon-btn.prop-del', {
            type: 'button',
            title: `Remove ${name}`,
            onclick: () => {
              delete props[name];
              def.props.required = def.props.required.filter((r) => r !== name);
              changed();
              drawPropsTable();
            },
          },
            icon('close')));
    });
    mount(
        propsTableEl,
        rows.length ?
            [
              h('div.props-row.props-head', h('span', 'Name'),
                h('span', 'Type'), h('span', ''), h('span', 'Values / items'),
                h('span', 'Default'), h('span', 'Description'), h('span', '')),
              rows
            ] :
            h('p.muted.small', 'No props yet.'),
        h('button.btn.btn-ghost.btn-sm#comp-add-prop', {
          type: 'button',
          onclick: () => {
            let n = 'prop';
            for (let i = 2; props[n]; i++) n = `prop${i}`;
            props[n] = {$ref: `${REF}DynamicString`};
            changed();
            drawPropsTable();
            propsTableEl.querySelector(`[data-prop="${n}"] .prop-name`)
                ?.select();
          },
        },
          icon('add'), 'Add prop'));
  }

  const templateTa =
      h('textarea.input.code-input.code-editor#comp-template', {
        rows: 14,
        spellcheck: 'false',
        oninput: () => {
          def.web.template = templateTa.value;
          changed();
        },
      },
        def.web.template);
  const cssTa =
      h('textarea.input.code-input.code-editor#comp-css', {
        rows: 14,
        spellcheck: 'false',
        oninput: () => {
          def.web.css = cssTa.value;
          changed();
        },
      },
        def.web.css);
  codeKeys(templateTa, () => {
    def.web.template = templateTa.value;
    changed();
  });
  codeKeys(cssTa, () => {
    def.web.css = cssTa.value;
    changed();
  });
  const templateEditor = enhanceCodeEditor(templateTa, {lang: 'html'});
  const cssEditor = enhanceCodeEditor(cssTa, {lang: 'css'});
  const formatTemplateBtn =
      h('button.btn.btn-ghost.btn-xs#comp-format-template', {
        type: 'button',
        title: 'Format HTML with 2-space indentation',
        onclick: () => {
          const next = formatHtml(templateTa.value);
          if (next && next !== templateTa.value) {
            templateTa.value = next;
            def.web.template = next;
            changed();
          }
        },
      },
        icon('auto_fix_high'), 'Format');
  const formatCssBtn =
      h('button.btn.btn-ghost.btn-xs#comp-format-css', {
        type: 'button',
        title: 'Format CSS rules across multiple lines',
        onclick: () => {
          const next = formatCss(cssTa.value);
          if (next && next !== cssTa.value) {
            cssTa.value = next;
            def.web.css = next;
            changed();
          }
        },
      },
        icon('auto_fix_high'), 'Format');
  const compileEl = h('div.comp-compile#comp-compile');
  const issuesEl = h('div.comp-issues#comp-issues');
  const bannerEl = h('div.comp-ed-banner#comp-ed-banner');
  const quickChipsEl = h('div.comp-quick-chips#comp-quick-chips');
  let compileErrors = [];
  let renderErrors = [];

  function allCurrentIssues() {
    const out = [];
    const seen = new Set();
    for (const x
             of [...(comp.issues || []), ...compileErrors, ...renderErrors]) {
      const s = typeof x === 'string' ? x.trim() :
                                        (x && x.message) || JSON.stringify(x);
      if (s && !seen.has(s)) {
        seen.add(s);
        out.push(s);
      }
    }
    return out;
  }

  async function proposeFix({instruction = '', activateAfter = false} = {}) {
    await proposeComponentFixModal(comp, {
      instruction,
      definition: def,
      extraIssues: [...compileErrors, ...renderErrors],
      inEditor: true,
      activateAfter,
      onApplyToEditor: (next) => {
        const keepName = def.name;
        def = normalizeDef(next);
        if (!def.name) def.name = keepName;
        loadIntoEditor();
        changed();
        toast('Fix applied to editor \u2014 save to keep it', 'good');
      },
      onSaved: (saved, {activated} = {}) => {
        dirty = false;
        dirtyEl.hidden = true;
        saveBtn.disabled = true;
        updateFromServer(saved, {keepDef: false});
        startPolling();
        if (activated) {
          toast(`${saved.name || def.name} fixed and activated`, 'good');
        }
      },
    });
  }

  function drawBanner() {
    if (needsBuild(comp) || jobActive(comp)) {
      mount(bannerEl);
      return;
    }
    const errs = allCurrentIssues();
    const warns = comp.warnings || [];
    if (!errs.length && !warns.length) {
      mount(bannerEl);
      return;
    }
    const hasErrs = errs.length > 0;
    const shown = hasErrs ? errs : warns;
    mount(
        bannerEl,
        h(`div.callout.${
              hasErrs ? 'callout-bad' : 'callout-warn'}.comp-ed-banner-inner`,
            icon(hasErrs ? 'error' : 'lightbulb'),
            h('div.comp-ed-banner-body',
              h('div.callout-title',
                hasErrs ?
                    `${errs.length} issue${
                        errs.length === 1 ? '' : 's'} preventing activation` :
                    `${warns.length} suggestion${
                        warns.length === 1 ? '' : 's'} in this component`),
              h('ul.small', shown.slice(0, 4).map((x) => h('li', x)))),
            h('div.comp-ed-banner-actions',
              h(`button.btn.${
                    hasErrs ? 'btn-primary' :
                              'btn-ghost'}.btn-sm#comp-propose-fix`,
                  {
                    type: 'button',
                    onclick: () => proposeFix(
                        {activateAfter: hasErrs && comp.status !== 'active'}),
                  },
                  icon('auto_awesome'), 'Propose fix with Gemini'))));
  }

  function drawQuickChips() {
    const errs = allCurrentIssues();
    const warns = comp.warnings || [];
    const mismatches = (comp.fidelity && comp.fidelity.mismatches) || [];
    const chips = [];
    if (errs.length) {
      chips.push(
          h('button.btn.btn-ghost.btn-sm.comp-quick-chip', {
            type: 'button',
            onclick: () => proposeFix({
              instruction: `Fix all validation and template errors (${
                  errs.join('; ')}).`,
              activateAfter: comp.status !== 'active',
            }),
          },
            icon('build_circle'),
            `Fix ${errs.length} issue${errs.length === 1 ? '' : 's'}`));
    }
    if (warns.length) {
      chips.push(
          h('button.btn.btn-ghost.btn-sm.comp-quick-chip', {
            type: 'button',
            onclick: () => proposeFix({
              instruction:
                  'Replace hard-coded colors and styling values with var(--a2ui-*) theme tokens and resolve CSS suggestions.',
            }),
          },
            icon('palette'), 'Use theme tokens in CSS'));
    }
    if (mismatches.length) {
      chips.push(
          h('button.btn.btn-ghost.btn-sm.comp-quick-chip', {
            type: 'button',
            onclick: () => proposeFix({
              instruction:
                  'Fix the visual fidelity mismatches against the reference image.',
            }),
          },
            icon('compare'),
            `Fix ${mismatches.length} visual mismatch${
                mismatches.length === 1 ? '' : 'es'}`));
    }
    mount(quickChipsEl, chips);
  }

  function drawCompile() {
    if (!compileFn) {
      compileErrors = [];
      mount(compileEl);
      drawBanner();
      drawQuickChips();
      return;
    }
    let res = null;
    try {
      res = compileFn(def);
    } catch (e) {
      res = {ok: false, errors: [String(e && e.message || e)]};
    }
    const errs = ((res && res.errors) || [])
                     .map(
                         (e) => typeof e === 'string' ?
                             e :
                             (e.message || JSON.stringify(e)));
    compileErrors = errs;
    mount(
        compileEl,
        errs.length ?
            h('div.callout.callout-warn.comp-errs', icon('code_off'),
              h('div.grow', h('div.callout-title', 'Template check'),
                h('ul', errs.map((e) => h('li', e)))),
              h('button.btn.btn-ghost.btn-sm#comp-compile-fix', {
                type: 'button',
                onclick: () => proposeFix(),
              },
                icon('auto_awesome'), 'Propose fix')) :
            h('p.comp-ok.small', icon('check'), ' Template compiles',
              res && res.bindings && res.bindings.length ?
                  h('span.muted',
                    ` \u00b7 binds ${
                        res.bindings
                            .map(
                                (b) => typeof b === 'string' ? b :
                                                               b.prop || b.name)
                            .filter(Boolean)
                            .join(', ')}`) :
                  null));
    drawBanner();
    drawQuickChips();
  }

  function drawIssues() {
    const list = comp.issues || [];
    const warns = comp.warnings || [];
    mount(
        issuesEl,
        list.length ?
            h('div.callout.callout-bad.comp-errs', icon('error'),
              h('div.grow',
                h('div.callout-title', `Server check (${list.length})`),
                h('ul', list.map((x) => h('li', x)))),
              h('button.btn.btn-primary.btn-sm#comp-issues-fix', {
                type: 'button',
                onclick: () =>
                    proposeFix({activateAfter: comp.status !== 'active'}),
              },
                icon('auto_awesome'), 'Propose fix with Gemini')) :
            null,
        warns.length ?
            h('div.callout.comp-warns', icon('lightbulb'),
              h('div.grow',
                h('div.callout-title', `Suggestions (${warns.length})`),
                h('ul', warns.map((x) => h('li', x)))),
              !list.length ? h('button.btn.btn-ghost.btn-sm#comp-warns-fix', {
                type: 'button',
                onclick: () => proposeFix({
                  instruction:
                      'Replace hard-coded colors with var(--a2ui-*) theme tokens and address all suggestions.',
                }),
              },
                               icon('auto_awesome'), 'Fix with Gemini') :
                             null) :
            null);
    drawBanner();
    drawQuickChips();
  }

  // ------------------------------------------------------------ preview
  const iframe = h('iframe.comp-preview-frame#comp-preview-frame', {
    title: 'Component preview',
  });
  const frameWrap = h('div.comp-preview-stage', iframe);
  const previewNote = h('div.comp-preview-note.small.muted');
  let frameReady = false;
  const onMsg = (e) => {
    if (e.source !== iframe.contentWindow) return;
    const d = e.data || {};
    if (d.type === 'a2ui-render-page-ready') {
      frameReady = true;
      postRender();
    } else if (d.type === 'a2ui-ready') {
      const ht = Math.max(120, Math.min(4000, Number(d.height) || 0));
      iframe.style.height = `${ht}px`;
      const r = d.ready || {};
      const errs = (r.errors || []).length;
      const uns = (r.unsupported || []).length;
      renderErrors = [
        ...(r.errors || []).map((x) => `preview render: ${x}`),
        ...(r.unsupported || []).map((x) => `preview unsupported: ${x}`),
      ];
      previewNote.textContent = [
        `${vpWidth()}px wide`,
        errs ? `${errs} render error${errs === 1 ? '' : 's'}: ${
                   (r.errors || []).slice(0, 2).join('; ')}` :
               '',
        uns ? `unsupported: ${(r.unsupported || []).join(', ')}` : '',
      ].filter(Boolean).join(' \u00b7 ');
      previewNote.classList.toggle('c-bad', !!errs || !!uns);
      drawBanner();
      drawQuickChips();
    } else if (d.type === 'a2ui-action') {
      const name = d.payload && d.payload.action && d.payload.action.name;
      previewNote.textContent = `action: ${name || '?'}`;
    }
  };
  window.addEventListener('message', onMsg);
  iframe.addEventListener('load', () => {
    // Fallback if the page-ready message was missed.
    setTimeout(() => {
      frameReady = true;
      postRender();
    }, 150);
  });
  iframe.src = '/render/live?interactive=1';

  function vpWidth() {
    return viewport === 'source' ? Math.max(120, Math.round(srcWidth) + 32) :
        viewport === 'desktop'   ? 1280 :
                                   412;
  }

  function messages() {
    const name = def.name || 'Untitled';
    const sp = def.sample.props || {};
    return [
      {version: 'v0.9', createSurface: {surfaceId: 'preview', catalogId}},
      {
        version: 'v0.9',
        updateComponents: {
          surfaceId: 'preview',
          components: [
            {...sp, id: 'root', component: name},
            ...(def.sample.components || []),
          ],
        },
      },
      {
        version: 'v0.9',
        updateDataModel:
            {surfaceId: 'preview', path: '/', value: def.sample.data || {}},
      },
    ];
  }

  function postRender() {
    if (!frameReady || !iframe.contentWindow) return;
    const w = vpWidth();
    iframe.style.width = `${w}px`;
    const theme = {...(settings.theme || {}), scheme};
    iframe.contentWindow.postMessage(
        {
          type: 'a2ui-render',
          messages: messages(),
          theme,
          components: {[def.name || 'Untitled']: def},
          viewport: {id: viewport, width: w, height: 800},
          framing: 'card',
        },
        location.origin);
  }
  const renderSoon = debounce(() => {
    drawCompile();
    postRender();
  }, 300);

  function changed(rerender = true) {
    markDirty();
    if (rerender) renderSoon();
  }

  const vpSeg = segmented(
      [
        srcWidth ? {
          value: 'source',
          label: `Component ${Math.round(srcWidth)}px`,
          id: 'vp-source'
        } :
                   null,
        {value: 'phone', label: 'Phone 412', id: 'vp-phone'},
        {value: 'desktop', label: 'Desktop 1280', id: 'vp-desktop'},
      ].filter(Boolean),
      viewport, (v) => {
        viewport = v;
        postRender();
      }, {id: 'comp-vp'});
  const schemeSeg = segmented(
      [
        {
          value: 'light',
          label: 'Light',
          icon: 'light_mode',
          id: 'scheme-light'
        },
        {value: 'dark', label: 'Dark', icon: 'dark_mode', id: 'scheme-dark'}
      ],
      scheme, (v) => {
        scheme = v;
        postRender();
      }, {id: 'comp-scheme'});

  const sampleTa = h('textarea.input.code-input#comp-sample', {
    rows: 10,
    spellcheck: 'false',
    oninput: () => {
      try {
        const v = JSON.parse(sampleTa.value);
        if (!v || typeof v !== 'object' || Array.isArray(v))
          throw new Error('sample must be an object');
        def.sample = normalizeDef({sample: v}).sample;
        sampleErr.textContent = '';
        changed();
      } catch (e) {
        sampleErr.textContent = `Invalid JSON: ${e.message}`;
      }
    },
  });
  codeKeys(sampleTa, () => sampleTa.dispatchEvent(new Event('input')));
  const sampleErr = h('p.form-error#comp-sample-error');
  function syncSampleEditor() {
    sampleTa.value = JSON.stringify(def.sample, null, 2);
    sampleErr.textContent = '';
  }
  const genSampleBtn =
      h('button.btn.btn-ghost.btn-sm#comp-gen-sample', {
        type: 'button',
        onclick: async () => {
          genSampleBtn.disabled = true;
          genSampleBtn.classList.add('busy');
          try {
            const {sample} = await post(`/api/components/${cid}/sample`, {});
            def.sample = normalizeDef({sample}).sample;
            syncSampleEditor();
            changed();
            toast('Sample generated (not saved yet)', 'good');
          } finally {
            genSampleBtn.disabled = false;
            genSampleBtn.classList.remove('busy');
          }
        },
      },
        icon('auto_awesome'), 'Generate sample');

  // ----------------------------------------------------------- reference
  const refEl = h('div.comp-ref#comp-reference');
  const fidEl = h('div.comp-fidelity#comp-fidelity');
  const jobEl = h('div.comp-ed-job');
  const recheckBtn =
      h('button.btn.btn-ghost.btn-sm#comp-recheck', {
        type: 'button',
        onclick: async () => {
          if (dirty &&
              !(await confirmDialog(
                  'Re-check the saved version?',
                  'The check renders the last saved definition. Save first to include your edits.',
                  {confirmLabel: 'Re-check saved'})))
            return;
          recheckBtn.disabled = true;
          try {
            const {component} = await post(`/api/components/${cid}/recheck`);
            updateFromServer(component, {keepDef: true});
            startPolling();
          } finally {
            recheckBtn.disabled = false;
          }
        },
      },
        icon('compare'), 'Re-check fidelity');

  function drawReference() {
    const src = comp.source || {};
    mount(
        refEl,
        comp.reference_url ?
            h('a.comp-ref-img',
              {href: comp.reference_url, target: '_blank', rel: 'noopener'},
              h('img', {src: comp.reference_url, alt: 'Reference'})) :
            h('div.draft-fig-empty.comp-ref-empty', icon('image_not_supported'),
              src.kind === 'describe' || src.kind === 'manual' ?
                  'No visual reference (built from text)' :
                  'No reference image'),
        h('dl.comp-src',
          src.figma_name ? [h('dt', 'Figma'), h('dd', src.figma_name)] : null,
          src.screen_title ? [h('dt', 'Screen'), h('dd', src.screen_title)] :
                             null,
          src.url ?
              [
                h('dt', 'Link'),
                h('dd',
                  h('a', {href: src.url, target: '_blank', rel: 'noopener'},
                    src.url))
              ] :
              null,
          src.hint ? [h('dt', 'Hint'), h('dd', src.hint)] : null,
          src.text ? [h('dt', 'Brief'), h('dd.comp-src-text', src.text)] : null,
          src.variant_props && Object.keys(src.variant_props).length ?
              [
                h('dt', 'Variants'),
                h('dd',
                  Object.entries(src.variant_props)
                      .map(
                          ([k, v]) =>
                              h('div.small', h('strong', k),
                                ` ${[].concat(v).join(' / ')}`)))
              ] :
              null));
    const f = comp.fidelity;
    mount(
        fidEl,
        h('div.comp-fid-head', h('span.field-label', 'Fidelity'),
          fidelityChip(f) || h('span.muted.small', 'not checked'),
          h('span.grow'),
          f && f.checked_at ? h('span.muted.small', relTime(f.checked_at)) :
                              null),
        f && (f.mismatches || []).length ?
            h('ul.mismatches#comp-mismatches',
              f.mismatches.map(
                  (m) =>
                      h('li.mismatch',
                        h('div.mismatch-head',
                          m.area ? h('span.chip.chip-xs', m.area) : null,
                          h('span', m.issue || '')),
                        m.fix ? h('div.mismatch-fix.muted.small', icon('build'),
                                  m.fix) :
                                null))) :
            f && f.score != null ? h('p.small.muted', 'No mismatches noted.') :
                                   null,
        (comp.token_suggestions || []).length ?
            h('div.comp-sugs', h('span.field-label', 'Token suggestions'),
              tokenSuggestionList(comp.token_suggestions),
              h('p.help',
                'Applied when you activate with \u201cAlso apply token suggestions\u201d.')) :
            null);
    mount(
        jobEl, jobProgress(comp),
        needsBuild(comp) ?
            h('div.callout.comp-build-callout', icon('auto_awesome'),
              h('div',
                h('div.callout-title',
                  'Proposed in chat \u2014 no implementation yet'),
                h('p.small',
                  'Gemini can write the HTML/CSS (using your tokens) and sample data.'),
                h('button.btn.btn-primary.btn-sm#comp-build', {
                  type: 'button',
                  onclick: async (e) => {
                    const b = e.currentTarget;
                    b.disabled = true;
                    b.classList.add('busy');
                    try {
                      const c = await buildFromProposal(comp);
                      if (c) updateFromServer(c, {keepDef: dirty});
                      startPolling();
                    } finally {
                      b.disabled = false;
                      b.classList.remove('busy');
                    }
                  },
                },
                  icon('auto_awesome'), 'Build with Gemini'))) :
            null);
    recheckBtn.disabled = jobActive(comp);
  }

  // Revise with Gemini.
  const reviseTa = h('textarea.input#comp-revise-text', {
    rows: 3,
    placeholder:
        'e.g. \u201cMake the price larger and right-aligned; use the primary container for the badge.\u201d',
  });
  const reviseOut = h('div.comp-revise-out#comp-revise-out');
  const reviseBtn =
      h('button.btn.btn-ghost.btn-sm#comp-revise', {
        type: 'button',
        onclick: async () => {
          const instruction = reviseTa.value.trim();
          const hasProblems =
              allCurrentIssues().length > 0 || (comp.warnings || []).length > 0;
          if (!instruction && !hasProblems) {
            reviseTa.focus();
            return;
          }
          await proposeFix({
            instruction,
            activateAfter:
                allCurrentIssues().length > 0 && comp.status !== 'active',
          });
        },
      },
        icon('auto_awesome'), 'Revise with Gemini');

  function loadIntoEditor() {
    if (def.web.template && !def.web.template.trim().includes('\n') &&
        def.web.template.includes('><')) {
      def.web.template = formatHtml(def.web.template);
    }
    if (def.web.css && !def.web.css.trim().includes('\n') &&
        def.web.css.includes('{')) {
      def.web.css = formatCss(def.web.css);
    }
    nameIn.value = def.name;
    descIn.value = def.description;
    usageIn.value = def.usage;
    strategySel.value = def.strategy || '';
    syncStrategySelect();
    templateTa.value = def.web.template;
    cssTa.value = def.web.css;
    templateEditor.sync();
    cssEditor.sync();
    if (propsMode === 'json')
      propsJson.value = JSON.stringify(def.props, null, 2);
    drawPropsTable();
    syncCompositionEditor();
    syncSampleEditor();
    drawHead();
    drawCompile();
    postRender();
  }

  // ---------------------------------------------------------- server sync
  function updateFromServer(c, {keepDef = false} = {}) {
    comp = c;
    if (!keepDef && !dirty) {
      def = normalizeDef(c.definition);
      loadIntoEditor();
    }
    drawHead();
    drawIssues();
    drawReference();
  }

  function startPolling() {
    if (stopPoll) stopPoll();
    stopPoll = null;
    if (!jobActive(comp)) return;
    stopPoll = pollComponent(cid, (c) => {
      if (!alive) return;
      updateFromServer(c, {keepDef: true});
    });
  }

  async function save() {
    if (!def.name) {
      toast('Give the component a name first.', 'warn');
      nameIn.focus();
      return false;
    }
    saveBtn.disabled = true;
    saveBtn.classList.add('busy');
    try {
      const {component} =
          await patch(`/api/components/${cid}`, {definition: def});
      dirty = false;
      dirtyEl.hidden = true;
      updateFromServer(component, {keepDef: true});
      startPolling();
      toast(
          (component.issues || []).length ? 'Saved \u2014 with issues to fix' :
                                            'Saved',
          (component.issues || []).length ? 'warn' : 'good');
      return true;
    } catch (e) {
      saveBtn.disabled = false;
      return false;
    } finally {
      saveBtn.classList.remove('busy');
    }
  }

  async function toggleStatus() {
    statusBtn.disabled = true;
    try {
      if (dirty && !(await save())) return;
      if (comp.status === 'active') {
        const {component} =
            await patch(`/api/components/${cid}`, {status: 'draft'});
        updateFromServer(component, {keepDef: true});
        toast(`${def.name} deactivated`, 'good');
      } else {
        const c = await activateComponent(comp, {
          inEditor: true,
          definition: def,
          extraIssues: [...compileErrors, ...renderErrors],
          onApplyToEditor: (next) => {
            const keepName = def.name;
            def = normalizeDef(next);
            if (!def.name) def.name = keepName;
            loadIntoEditor();
            changed();
            toast('Fix applied to editor \u2014 save to keep it', 'good');
          },
          onSaved: (saved) => {
            dirty = false;
            dirtyEl.hidden = true;
            saveBtn.disabled = true;
            updateFromServer(saved, {keepDef: false});
            startPolling();
          },
        });
        if (!c) return;
        updateFromServer(c, {keepDef: false});
        toast(`${c.name || def.name} is active`, 'good');
      }
    } finally {
      statusBtn.disabled = false;
    }
  }

  async function remove() {
    if (!(await confirmDialog(
            `Delete ${def.name || 'this component'}?`,
            'The component, its implementation and previews are deleted. Past runs keep their snapshot.',
            {confirmLabel: 'Delete', danger: true})))
      return;
    await del(`/api/components/${cid}`);
    dirty = false;
    toast('Component deleted', 'good');
    navigate(`/p/${pid}/design?tab=components`);
  }

  // -------------------------------------------------------------- layout
  const page = h(
      'div.page.page-comp-editor',
      h('div.page-head.page-head-tight.sticky-head.comp-ed-head',
        h('div',
          h('div.crumbs',
            h('a#comp-back', {href: `/p/${pid}/design?tab=components`},
              '\u2190 Design system')),
          h('div.title-row', titleEl, chipsEl)),
        h('div.page-actions', deleteBtn, statusBtn, saveBtn)),
      bannerEl,
      h('div.comp-ed-cols',
        h('section.comp-ed-col.comp-ed-def',
          h('div.comp-ed-col-title', 'Definition'),
          h('label.field', h('span.field-label', 'Name'), nameIn,
            h('span.help',
              'PascalCase, unique in the project, not a Basic component name.')),
          h('label.field', h('span.field-label', 'Description'), descIn),
          h('label.field', h('span.field-label', 'Usage'), usageIn),
          h('label.field', h('span.field-label', 'Target rendering strategy'),
            strategySel, strategyHelp),
          h('div.field',
            h('div.field-head', h('span.field-label', 'Props'), h('span.grow'),
              propsSeg),
            propsTableEl, propsJson, propsErr),
          h('div.field',
            h('div.field-head', h('span.field-label', 'Template (HTML)'),
              h('span.grow'), formatTemplateBtn),
            templateEditor.wrap,
            h('details.help.tpl-help', h('summary', 'Template syntax'),
              h('ul.small',
                h('li', h('code', '{{prop}}'), ' text, ',
                  h('code', 'class="x {{variant}}"'),
                  ' attrs (src alt title class aria-* data-*)'),
                h('li', h('code', 'data-if="prop"'), ' / ',
                  h('code', 'data-if="prop:value"'), ' / ',
                  h('code', 'data-unless'), ' conditionals'),
                h('li', h('code', 'data-each="items as it"'), ' + ',
                  h('code', '{{it.field}}'), ' repeat'),
                h('li', h('code', '<slot name="prop"></slot>'),
                  ' children of a slot prop'),
                h('li', h('code', 'data-action="prop"'), ' clickable Action, ',
                  h('code', 'data-icon="{{prop}}"'), ' Material icon'),
                h('li', 'No script, style, links, forms or event handlers.')))),
          h('div.field',
            h('div.field-head', h('span.field-label', 'CSS'), h('span.grow'),
              formatCssBtn),
            cssEditor.wrap,
            h('span.help',
              'Scoped to the component (shadow root). Use var(--a2ui-*) tokens for colors, radii, type and spacing.')),
          h('details.field#comp-composition-details',
            {open: def.strategy === 'basic_composition'},
            h('summary.field-head',
              h('span.field-label',
                'A2UI Basic Catalog Composition (lowering)'),
              h('span.grow'), toTemplateBtn),
            compositionTa, compositionErr,
            h('span.help',
              'Lowers this component into A2UI Basic Catalog nodes (Card, Row, Column, Text, Image, Icon, Button, Divider) so any A2UI renderer that implements the Basic catalog can display it.')),
          compileEl, issuesEl),
        h('section.comp-ed-col.comp-ed-preview',
          h('div.comp-ed-col-title', 'Preview'),
          h('div.comp-preview-bar', vpSeg, schemeSeg), frameWrap, previewNote,
          h('div.field.comp-sample-field',
            h('div.field-head',
              h('span.field-label', 'Sample (props, data, extra components)'),
              h('span.grow'), genSampleBtn),
            sampleTa, sampleErr)),
        h('section.comp-ed-col.comp-ed-ref',
          h('div.comp-ed-col-title', 'Reference'), refEl, jobEl, fidEl,
          h('div.row-actions', recheckBtn),
          h('div.field.comp-revise',
            h('span.field-label', 'Revise with Gemini'), quickChipsEl, reviseTa,
            h('div.row-actions', reviseBtn)),
          reviseOut)));
  root.appendChild(page);

  drawPropsTable();
  syncCompositionEditor();
  syncSampleEditor();
  drawHead();
  drawIssues();
  drawReference();
  drawCompile();
  startPolling();

  const unsaved = (e) => {
    if (dirty) {
      e.preventDefault();
      e.returnValue = '';
    }
  };
  window.addEventListener('beforeunload', unsaved);
  return () => {
    alive = false;
    if (stopPoll) stopPoll();
    window.removeEventListener('message', onMsg);
    window.removeEventListener('beforeunload', unsaved);
  };
}
