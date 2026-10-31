// "Describe a component" drawer: Gemini drafts a custom component from text
// (server job, source "describe"); the Design system page polls the draft.
// Figma, Stitch and screenshot material no longer builds components directly:
// it goes through Imports (analyze → questions → merge), so those tabs open
// the "New import" drawer instead. Exported for the Design system page.
import {post} from '../components/api.js';
import {h, icon} from '../components/dom.js';
import {emit} from '../components/store.js';
import {drawer, toast} from '../components/ui.js';

import {openImportDrawer} from './import_drawer.js';

const IMPORT_TABS = {
  figma: 'figma',
  stitch: 'stitch',
  screenshot: 'screenshots',
  screenshots: 'screenshots'
};

/**
 * Opens the describe drawer. opts.tab: describe (default) | figma | stitch |
 * screenshot — the last three open the import drawer (origin 'design').
 */
export function openComponentImport(pid, {tab = 'describe'} = {}) {
  if (IMPORT_TABS[tab]) {
    openImportDrawer(pid, {tab: IMPORT_TABS[tab], origin: 'design'});
    return;
  }
  const descText = h('textarea.input#comp-describe-text', {
    rows: 7,
    placeholder:
        'e.g. A compact flight segment row: airline logo, departure and arrival times with airport codes, duration and stops in the middle, a price on the right. Variants: selected / default.',
    oninput: () => sync(),
  });
  const descName =
      h('input.input#comp-describe-name',
        {placeholder: 'Optional, e.g. FlightSegment'});
  const strategySel = h(
      'select.input#comp-describe-strategy',
      h('option', {value: 'auto'},
        'Auto (decide by Basic fit)'),
      h('option', {value: 'web_template'},
        'Web HTML/CSS Template (deterministic HTML + CSS tokens)'),
      h('option', {value: 'basic_composition'},
        'A2UI Basic Catalog Composition (any A2UI renderer)'));
  const submitBtn =
      h('button.btn.btn-primary#comp-import-submit',
        {type: 'button', onclick: submit}, icon('construction'),
        'Build component');
  function sync() {
    submitBtn.disabled = !descText.value.trim();
  }

  const d = drawer({
    title: 'Describe a component',
    wide: true,
    id: 'component-import-drawer',
    body: h(
        'div.ref-import.comp-import',
        h('div.ref-pane#comp-pane-describe',
          h('label.field', h('span.field-label', 'Describe the component'),
            descText,
            h('span.help',
              'What it shows, its props and variants, and when to use it. Gemini drafts the definition, HTML/CSS (using your tokens) and sample data.')),
          h('label.field', h('span.field-label', 'Name'), descName),
          h('label.field', h('span.field-label', 'Target rendering strategy'),
            strategySel)),
        h('p.help.comp-import-alt',
          'Have a Figma file, Stitch project or screenshot? ',
          h('button.link-btn#comp-open-import', {
            type: 'button',
            onclick: () => openImportDrawer(pid, {origin: 'design'})
          },
            'Import it'),
          ' \u2014 it\u2019s analyzed into components, templates and tokens before anything is added.'),
        h('div.drawer-actions', submitBtn,
          h('button.btn.btn-ghost', {type: 'button', onclick: () => d.close()},
            'Cancel'),
          h('span.grow'),
          h('span.muted.small',
            'Drafts appear on the Components tab while they build.'))),
  });
  sync();
  setTimeout(() => descText.focus(), 250);

  async function submit() {
    const body = {
      source: 'describe',
      text: descText.value.trim(),
      strategy: strategySel.value || 'auto',
    };
    if (descName.value.trim()) body.name = descName.value.trim();
    submitBtn.disabled = true;
    submitBtn.classList.add('busy');
    try {
      const res = await post(`/api/projects/${pid}/components/import`, body);
      const list = (res && res.components) || [];
      d.close();
      toast('Building component\u2026', 'good');
      emit('components-changed', {components: list});
    } finally {
      submitBtn.classList.remove('busy');
      sync();
    }
  }
}
