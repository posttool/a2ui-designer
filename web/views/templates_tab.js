// Design system → Templates: reusable screen/section templates created by
// applying an import's merge plan (template extractions). Each template has
// an image, when-to-use guidance, a structure outline and the components it
// is built from. Edit / archive / delete via /api/templates/{tid}.
import {del, get, patch} from '../components/api.js';
import {emptyState, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {renderMarkdown} from '../components/markdown.js';
import {confirmDialog, modal, toast} from '../components/ui.js';

import {openImportDrawer} from './import_drawer.js';

/**
 * @param {*} pid
 * @return {*}
 */
export function createTemplatesTab(pid, {onCount} = {}) {
  let root = null;
  let templates = [];
  let depsData = null;
  let showArchived = false;
  const gridWrap = h('div.tpl-wrap#templates-list');

  function actions() {
    return [
      h('label.check.tpl-archived-toggle', h('input#templates-show-archived', {
          type: 'checkbox',
          checked: showArchived,
          onchange: (e) => {
            showArchived = e.target.checked;
            draw();
          }
        }),
        'Show archived'),
      h('button.btn.btn-ghost#templates-import', {
        type: 'button',
        onclick: () => openImportDrawer(pid, {origin: 'design'})
      },
        icon('move_to_inbox'), 'Import screens'),
    ];
  }

  async function mountTab(el) {
    root = el;
    mount(
        root,
        h('section.design-section',
          h('div.design-section-head',
            h('h2', 'Templates', h('span.count#templates-count', '')),
            h('p.help',
              'Screens and sections composed from components, saved from imports. The generator and the constitution use them as layout patterns; they are exported in the design package under templates/.')),
          gridWrap));
    await reload();
  }

  async function reload() {
    if (!root) return;
    mount(gridWrap, spinner('Loading templates\u2026'));
    try {
      const [res, dRes] = await Promise.all([
        get(`/api/projects/${pid}/templates`),
        get(`/api/projects/${pid}/dependencies`, {
          quiet: true
        }).catch(() => null),
      ]);
      templates = res.templates || [];
      if (dRes) depsData = dRes;
    } catch (e) {
      mount(gridWrap, emptyState('Could not load templates', e.message));
      return;
    }
    draw();
  }

  function draw() {
    const activeCount =
        templates.filter((t) => (t.status || 'active') !== 'archived').length;
    if (onCount) onCount(activeCount);
    const list = templates.filter(
        (t) => showArchived || (t.status || 'active') !== 'archived');
    const countEl = root && root.querySelector('#templates-count');
    if (countEl) countEl.textContent = ` ${list.length}`;
    if (!list.length) {
      mount(
          gridWrap,
          emptyState(
              templates.length ? 'All templates are archived' :
                                 'No templates yet',
              'Import Figma frames, Stitch screens or screenshots. During analysis, screens built from components become templates; applying the merge plan saves them here.',
              h('button.btn.btn-primary', {
                type: 'button',
                onclick: () => openImportDrawer(pid, {origin: 'design'})
              },
                icon('move_to_inbox'), 'New import')));
      return;
    }
    mount(gridWrap, h('div.tpl-grid#template-grid', list.map(card)));
  }

  function componentStatus(compName) {
    if (!depsData || !depsData.snapshot) return 'active';
    const snap = depsData.snapshot;
    // Snapshot keys come from core/dependencies.py design_system_snapshot():
    // enabled_basic / disabled_components / active_custom are name lists.
    const names = (list) =>
        (list || []).map((c) => (typeof c === 'string' ? c : c && c.name));
    if (names(snap.disabled_components).includes(compName)) {
      return 'disabled';
    }
    if (names(snap.enabled_basic).includes(compName)) {
      return 'active';
    }
    if (names(snap.active_custom).includes(compName)) {
      return 'active';
    }
    if ((snap.draft_custom_components || []).some((c) => c.name === compName)) {
      return 'draft';
    }
    return 'missing';
  }

  function card(t) {
    const archived = t.status === 'archived';
    const src = t.source || {};
    const usedByRules =
        (depsData && depsData.used_by && depsData.used_by.templates &&
         depsData.used_by.templates[t.name] &&
         depsData.used_by.templates[t.name].constitution_rules) ||
        [];
    return h(
        'article.tpl-card', {
          class: archived ? 'archived' : '',
          id: `tpl-${t.id}`,
          dataset: {tid: t.id}
        },
        h('div.tpl-thumb',
          t.image_url ?
              h('img', {src: t.image_url, alt: t.name || '', loading: 'lazy'}) :
              h('div.tpl-thumb-empty', icon('dashboard'))),
        h('div.tpl-body',
          h('div.tpl-title-row',
            h('h3.tpl-name', t.name || 'Untitled template'),
            usedByRules.length ?
                h('span.chip.chip-xs.deps-usedby-chip', {
                  title: `Referenced in constitution: ${usedByRules.join(', ')}`
                },
                  icon('gavel'),
                  `Used in ${usedByRules.slice(0, 2).join(', ')}`) :
                null,
            archived ? h('span.chip.chip-xs.chip-quiet', 'archived') : null),
          t.description ? h('p.tpl-desc.small', t.description) : null,
          t.when_to_use ? h('div.tpl-when', h('span.label', 'When to use'),
                            h('p.small', t.when_to_use)) :
                          null,
          (t.components || []).length ?
              h('div.chip-row.tpl-comps', t.components.map((c) => {
                const st = componentStatus(c);
                return h(
                    'span.chip.chip-xs.kind-chip.k-component', {
                      class: st !== 'active' ? `st-${st}` : '',
                      title: st !== 'active' ?
                          `Component ${c} is ${st} in catalog` :
                          `Component ${c}`,
                    },
                    st !== 'active' ? icon('warning') : icon('widgets'), c,
                    st !== 'active' ? ` (${st})` : '');
              })) :
              null,
          t.structure_md ?
              h('details.tpl-structure', h('summary', 'Structure'),
                renderMarkdown(t.structure_md, {className: 'md'})) :
              null,
          h('div.tpl-foot',
            src.import_id ? h('a.small.tpl-source', {
              href: `/p/${pid}/imports/${src.import_id}${
                  src.extraction_id ? `#x-${src.extraction_id}` : ''}`,
              title: 'Open the import this template came from'
            },
                              icon('move_to_inbox'), 'From import') :
                            null,
            h('span.muted.small', relTime(t.updated_at || t.created_at)),
            h('span.grow'),
            h('button.icon-btn.tpl-edit', {
              type: 'button',
              title: 'Edit',
              'aria-label': `Edit ${t.name}`,
              onclick: () => edit(t)
            },
              icon('edit')),
            h('button.icon-btn.tpl-archive', {
              type: 'button',
              title: archived ? 'Restore' : 'Archive',
              'aria-label': archived ? 'Restore' : 'Archive',
              onclick: () => setStatus(t, archived ? 'active' : 'archived')
            },
              icon(archived ? 'unarchive' : 'archive')),
            h('button.icon-btn.tpl-delete', {
              type: 'button',
              title: 'Delete',
              'aria-label': `Delete ${t.name}`,
              onclick: () => remove(t)
            },
              icon('delete')))));
  }

  function replace(t) {
    const i = templates.findIndex((x) => x.id === t.id);
    if (i >= 0) templates[i] = t;
    draw();
  }

  async function setStatus(t, status) {
    const usedByRules =
        (depsData && depsData.used_by && depsData.used_by.templates &&
         depsData.used_by.templates[t.name] &&
         depsData.used_by.templates[t.name].constitution_rules) ||
        [];
    if (status === 'archived' && usedByRules.length) {
      if (!(await confirmDialog(
              `Archive \u201c${t.name}\u201d?`,
              `This template is referenced in constitution rules (${
                  usedByRules.join(
                      ', ')}). Archiving it will leave a broken reference until the constitution is updated.`,
              {confirmLabel: 'Archive anyway', danger: true}))) {
        return;
      }
    }
    const res = await patch(`/api/templates/${t.id}`, {status});
    replace(res.template || {...t, status});
  }

  async function remove(t) {
    if (!(await confirmDialog(
            'Delete template?',
            `\u201c${
                t.name ||
                'Untitled'}\u201d will be removed from the design system. The import record keeps the extraction.`,
            {confirmLabel: 'Delete', danger: true})))
      return;
    await del(`/api/templates/${t.id}`);
    templates = templates.filter((x) => x.id !== t.id);
    draw();
  }

  function edit(t) {
    const name = h('input.input#tpl-edit-name', {value: t.name || ''});
    const desc =
        h('textarea.input#tpl-edit-desc', {rows: 2}, t.description || '');
    const when =
        h('textarea.input#tpl-edit-when', {rows: 3}, t.when_to_use || '');
    const struct =
        h('textarea.input.code-input#tpl-edit-structure', {rows: 10},
          t.structure_md || '');
    modal({
      title: 'Edit template',
      id: 'tpl-edit-dialog',
      wide: true,
      body: [
        h('label.field', h('span.field-label', 'Name'), name),
        h('label.field', h('span.field-label', 'Description'), desc),
        h('label.field', h('span.field-label', 'When to use'), when),
        h('label.field', h('span.field-label', 'Structure (Markdown)'), struct),
      ],
      actions: [
        {label: 'Cancel'}, {
          label: 'Save',
          kind: 'primary',
          id: 'tpl-edit-save',
          onClick: async () => {
            const res = await patch(`/api/templates/${t.id}`, {
              name: name.value.trim(),
              description: desc.value.trim(),
              when_to_use: when.value.trim(),
              structure_md: struct.value,
            });
            replace(res.template || t);
            toast('Template saved', 'good');
          }
        }
      ]
    });
  }

  return {actions, mount: mountTab, reload};
}
