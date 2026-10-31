// "Make a template from this example": a three-step dialog.
//   1 Example — crop, outline with abstracted roles, skeleton, data paths,
//     name / structure / components (drafted by Gemini).
//   2 When should it appear again? — trigger chips (Gemini-proposed) plus
//     "Also when…" / "But not when…", composed into one editable sentence.
//   3 Check it — library scenarios where it would / would not appear; flips
//     become examples; Re-check lets Gemini rewrite when_to_use.
// Save → POST /api/teach/patterns.
import {post} from './api.js';
import {h, icon, mount, spinner} from './dom.js';
import {modal, toast} from './ui.js';

const GROUP_ICON = {
  data: 'data_object',
  'data shape': 'data_object',
  shape: 'data_object',
  intent: 'flag',
  context: 'smartphone',
};

function groupTitle(g) {
  const s = String(g || 'other');
  if (s === 'data') return 'Data shape';
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Outline text with `{roles}` and leading component types emphasized. */
function outlineView(text) {
  const lines = String(text || '').split('\n');
  return h('div.outline', lines.map((line) => {
    const parts = line.split(/(\{[^}]+\})/g);
    const indent = (/^\s*/.exec(line) || [''])[0].length;
    return h(
        'div', {style: {paddingLeft: `${indent * 4}px`}},
        parts.map(
            (p) => /^\{[^}]+\}$/.test(p) ? h('span.role', p) : p.trimStart()));
  }));
}

function composeWhen(on, also, butNot) {
  const clauses = on.map((t) => t.trim()).filter(Boolean);
  let s = clauses.length ? `Use when ${clauses.join(', and ')}` : '';
  if (also.trim())
    s += s ? `, or when ${also.trim()}` : `Use when ${also.trim()}`;
  if (butNot.trim()) s += `${s ? ' \u2014 ' : ''}not when ${butNot.trim()}`;
  return s ? `${s}.` : '';
}

/**
 * openPatternCapture({pid, sessionId, output, selection, cropDataUrl,
 *                     onSaved({template, teaching})})
 * selection: {key} | {region: [x, y, w, h]} | {whole: true}
 */
export function openPatternCapture(
    {pid, sessionId, output, selection, cropDataUrl, onSaved}) {
  const sel = selection && (selection.key || selection.region) ? selection :
                                                                 {whole: true};
  let draft = null;
  let triggers = [];
  let whenEdited = false;
  let checks = null;  // [{sample_id, title, how, def: bool, applies: bool}]
  let checkBusy = false;

  const nameIn = h('input.input#pc-name', {placeholder: 'Template name'});
  const structIn = h('textarea.input#pc-structure', {rows: 4});
  const alsoIn =
      h('input.input#pc-also',
        {placeholder: 'e.g. a receipt or invoice that needs approval'});
  const notIn =
      h('input.input#pc-not',
        {placeholder: 'e.g. the list is read-only history'});
  const whenIn = h('textarea.input#pc-when', {rows: 3});
  whenIn.addEventListener('input', () => {
    whenEdited = true;
    recomposeBtn.hidden = false;
  });
  const recomposeBtn =
      h('button.mini-btn', {
        type: 'button',
        hidden: true,
        onclick: () => {
          whenEdited = false;
          recomposeBtn.hidden = true;
          recompose();
        }
      },
        'Recompose from chips');
  alsoIn.addEventListener('input', () => recompose());
  notIn.addEventListener('input', () => recompose());

  const step1 =
      h('section.pc-step', h('h3', h('span.pc-num', '1'), 'Example'),
        h('div.pc-loading', spinner('Gemini is reading the example\u2026')));
  const step2 =
      h('section.pc-step',
        h('h3', h('span.pc-num', '2'), 'When should it appear again?'),
        h('div.pc-loading', spinner('Proposing triggers\u2026')));
  const step3 =
      h('section.pc-step',
        h('h3', h('span.pc-num', '3'), 'Check it ',
          h('span.muted.small', {style: 'font-weight:400'},
            '\u00b7 library scenarios')),
        h('p.muted.small', 'Runs once the example is drafted.'));
  const note = h(
      'span.note',
      'Saves to Design system \u2192 Templates (active) and to the ledger as a template teaching; trials record whether it is used when expected.');

  const m = modal({
    title: 'Make a template from this example',
    wide: true,
    id: 'pattern-capture',
    body: h('div.pc-steps', step1, step2, step3),
    actions: [
      {label: 'Cancel'},
      {
        label: 'Save template',
        kind: 'primary',
        id: 'pc-save',
        onClick: () => save()
      },
    ],
  });
  m.el.classList.add('pc');
  m.el.parentElement.classList.add('teach-scope');
  const foot = m.el.querySelector('.modal-foot');
  foot.prepend(note);
  const saveBtn = m.el.querySelector('#pc-save');
  saveBtn.disabled = true;

  function recompose() {
    if (whenEdited) return;
    const on = triggers.filter((t) => t.on).map((t) => t.text);
    const s = composeWhen(on, alsoIn.value, notIn.value);
    if (s) whenIn.value = s;
  }

  function drawStep1() {
    const d = draft || {};
    nameIn.value = nameIn.value || d.name || '';
    structIn.value = structIn.value || d.structure_md || '';
    const crop = cropDataUrl || (sel.whole ? output.screenshot_url : null) ||
        output.thumb_url;
    mount(
        step1, h('h3', h('span.pc-num', '1'), 'Example'),
        h('div.pc-example', h('div.pc-crop', {
            title: 'crop of the selection',
            style: crop ? {backgroundImage: `url("${crop}")`} : null
          }),
          h('div',
            d.outline ? outlineView(d.outline) :
                        h('div.outline.muted', 'No outline returned.'),
            h('div.muted.small', {style: 'margin-top:6px'},
              d.skeleton ?
                  [
                    'skeleton ',
                    h('code.mono',
                      typeof d.skeleton === 'string' ?
                          d.skeleton :
                          JSON.stringify(d.skeleton))
                  ] :
                  null,
              (d.data_paths || []).length ?
                  [
                    ' \u00b7 feeds from ',
                    (d.data_paths || [])
                        .slice(0, 6)
                        .map((p, i) => [i ? ', ' : '', h('code.mono', p)])
                  ] :
                  null))),
        d.description ?
            h('p.small.muted', {style: 'margin:10px 0 0'}, d.description) :
            null,
        h('div.pc-field',
          h('label',
            {for: 'pc-name'
            },
            'Name'),
          nameIn),
        h('div.pc-field',
          h('label',
            {for: 'pc-structure'
            },
            'Structure (what the generator should reproduce)'),
          structIn),
        (d.components || []).length ?
            h('div.pc-field', h('label', 'Components'),
              h('div.chip-row',
                (d.components || [])
                    .map(
                        (c) =>
                            h('span.chip.chip-quiet',
                              typeof c === 'string' ?
                                  c :
                                  (c.type || c.name || JSON.stringify(c)))))) :
            null);
  }

  function drawStep2() {
    const groups = [];
    for (const t of triggers) {
      let g = groups.find((x) => x.name === t.group);
      if (!g) groups.push(g = {name: t.group, items: []});
      g.items.push(t);
    }
    mount(
        step2, h('h3', h('span.pc-num', '2'), 'When should it appear again?'),
        h('p.muted.small', {style: 'margin:-6px 0 12px'},
          'Gemini proposed these from the example\u2019s data, intent and context. Toggle what matters; add your own.'),
        groups.length ?
            groups.map(
                (g) => h(
                    'div.trig-group',
                    h('div.g',
                      icon(GROUP_ICON[String(g.name).toLowerCase()] || 'label'),
                      groupTitle(g.name)),
                    g.items.map(
                        (t) =>
                            h('button.trig', {
                              type: 'button',
                              class: t.on ? 'on' : '',
                              'aria-pressed': String(!!t.on),
                              onclick: () => {
                                t.on = !t.on;
                                drawStep2();
                                recompose();
                              }
                            },
                              icon(
                                  t.on ? 'check_circle' :
                                         'radio_button_unchecked'),
                              t.text)))) :
            h('p.muted.small',
              'No triggers proposed \u2014 write the sentence below.'),
        h('div.pc-field',
          h('label',
            {for: 'pc-also'
            },
            'Also when\u2026'),
          alsoIn),
        h('div.pc-field',
          h('label',
            {for: 'pc-not'
            },
            'But not when\u2026'),
          notIn),
        h('div.pc-compose',
          h('div', {
            style: 'display:flex;align-items:center;gap:6px;margin-bottom:4px'
          },
            h('b', 'when_to_use'), h('span', '\u2192 edit freely'),
            h('span', {style: 'flex:1'}), recomposeBtn),
          whenIn));
  }

  function drawStep3() {
    const kids = [
      h('h3', h('span.pc-num', '3'), 'Check it ',
        h('span.muted.small', {style: 'font-weight:400'},
          '\u00b7 library scenarios')),
      h('p.muted.small', {style: 'margin:-6px 0 8px'},
        'Would this template appear in\u2026? Flip any answer; Gemini re-reads ',
        h('i', 'when_to_use'), ' with your examples.'),
    ];
    if (checkBusy) {
      kids.push(spinner('Checking against the library\u2026'));
    } else if (checks) {
      if (!checks.length)
        kids.push(
            h('p.muted.small', checks.none_reason || 'No matching scenarios.'));
      for (const c of checks) {
        const flipped = c.applies !== c.def;
        kids.push(
            h('div.pc-check', {dataset: {sample: c.sample_id}},
              h('span.tog',
                h('b', {
                  class: c.applies ? 'on y' : '',
                  role: 'button',
                  title: 'Would appear',
                  onclick: () => {
                    c.applies = true;
                    drawStep3();
                  }
                },
                  '\u2713'),
                h('b', {
                  class: !c.applies ? 'on n' : '',
                  role: 'button',
                  title: 'Would not appear',
                  onclick: () => {
                    c.applies = false;
                    drawStep3();
                  }
                },
                  '\u2715')),
              h('div',
                h('div.name', c.title || c.sample_id,
                  flipped ? h('span.flip', 'you flipped this') : null),
                h('div.how', c.how || ''))));
      }
      const yes = checks.filter((c) => c.applies).length;
      const flips = checks.filter((c) => c.applies !== c.def).length;
      kids.push(
          h('div',
            {style: 'display:flex;gap:8px;align-items:center;margin-top:10px'},
            h('button.btn.btn-ghost.btn-sm#pc-recheck',
              {type: 'button', onclick: () => runCheck()}, icon('refresh'),
              flips ? 'Re-check with my answers' : 'Re-check'),
            h('span.muted.small',
              `${yes} would appear \u00b7 ${checks.length - yes} would not${
                  flips ? ` \u00b7 ${flips} corrected` : ''}`)));
    } else {
      kids.push(
          h('button.btn.btn-ghost.btn-sm',
            {type: 'button', onclick: () => runCheck()}, icon('travel_explore'),
            'Check against the library'));
    }
    mount(step3, ...kids);
  }

  function examples() {
    return (checks || [])
        .filter((c) => c.applies !== c.def)
        .map((c) => ({sample_id: c.sample_id, applies: c.applies}));
  }

  async function runCheck() {
    if (checkBusy) return;
    checkBusy = true;
    drawStep3();
    try {
      const res = await post('/api/teach/patterns/check', {
        session_id: sessionId || undefined,
        output_id: output.id,
        project_id: pid || output.project_id || undefined,
        name: nameIn.value.trim(),
        structure_md: structIn.value,
        when_to_use: whenIn.value.trim(),
        examples: examples(),
      });
      const prev = new Map((checks || []).map((c) => [c.sample_id, c]));
      const rows = [];
      for (const a of res.applies || []) {
        const p = prev.get(a.sample_id);
        rows.push({
          sample_id: a.sample_id,
          title: a.title,
          how: a.how_it_applies || a.how || '',
          def: true,
          applies: p && p.applies !== p.def ? p.applies : true
        });
      }
      for (const x of res.exceptions || []) {
        const p = prev.get(x.sample_id);
        rows.push({
          sample_id: x.sample_id,
          title: x.title,
          how: x.why || '',
          def: false,
          applies: p && p.applies !== p.def ? p.applies : false
        });
      }
      rows.none_reason = res.none_reason || '';
      checks = rows;
      if (res.when_to_use && res.when_to_use.trim() &&
          res.when_to_use.trim() !== whenIn.value.trim()) {
        whenIn.value = res.when_to_use.trim();
        whenEdited = true;
        recomposeBtn.hidden = false;
      }
    } catch (e) {
      // error toast already shown by api.js
    } finally {
      checkBusy = false;
      drawStep3();
    }
  }

  async function save() {
    if (!nameIn.value.trim() || !whenIn.value.trim()) {
      toast('Give the template a name and a when-to-use sentence', 'warn');
      return false;
    }
    const d = draft || {};
    const res = await post('/api/teach/patterns', {
      session_id: sessionId || undefined,
      output_id: output.id,
      selection: sel,
      crop_data_url: cropDataUrl || undefined,
      name: nameIn.value.trim(),
      description: d.description || '',
      structure_md: structIn.value,
      components: d.components || [],
      skeleton: d.skeleton,
      when_to_use: whenIn.value.trim(),
      examples: examples(),
      triggers:
          triggers.map((t) => ({group: t.group, text: t.text, on: !!t.on})),
    });
    toast(
        `Template \u201c${
            res.template ? res.template.name : nameIn.value}\u201d saved`,
        'good');
    onSaved && onSaved(res);
    return true;
  }

  drawStep3();
  post(
      '/api/teach/patterns/draft', {output_id: output.id, selection: sel},
      {quiet: true})
      .then((res) => {
        draft = res.draft || {};
        triggers = (draft.triggers || []).map((t) => ({
                                                group: t.group || 'other',
                                                text: t.text || String(t),
                                                on: t.on !== false
                                              }));
        whenIn.value = draft.when_to_use || '';
        drawStep1();
        drawStep2();
        saveBtn.disabled = false;
        if (whenIn.value.trim()) runCheck();
      })
      .catch((e) => {
        draft = {};
        drawStep1();
        drawStep2();
        saveBtn.disabled = false;
        step1.append(h('p.small.c-bad', `Draft failed: ${e.message || e}`));
      });
  return m;
}
