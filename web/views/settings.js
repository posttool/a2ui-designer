// Project settings: general, viewports & renderers, generator, judge, export.
// Catalog selection, custom components and theme tokens live on the Design
// system page (/p/:pid/design).
import {cached, del, get, patch, put} from '../components/api.js';
import {download, h, icon} from '../components/dom.js';
import {emit, navigate, store} from '../components/store.js';
import {confirmDialog, toast, toggle} from '../components/ui.js';

// Top-level settings keys this page edits. Saving re-fetches the project's
// settings and replaces only these, so theme/catalog edits made on the Design
// system page (or by chat) in the meantime are never clobbered.
const OWNED_KEYS =
    ['viewports', 'renderers', 'data_source', 'generator', 'judge', 'imagery'];

const FALLBACK_MODELS = [
  {id: 'gemini-3.8-flash', label: 'Gemini 3.8 Flash (Latest)'},
  {id: 'gemini-3.7-flash', label: 'Gemini 3.7 Flash'},
  {id: 'gemini-3.6-flash', label: 'Gemini 3.6 Flash'},
  {id: 'gemini-3.5-flash', label: 'Gemini 3.5 Flash'},
  {id: 'gemini-3.5-flash-lite', label: 'Gemini 3.5 Flash Lite'},
  {id: 'gemini-3.1-pro-preview', label: 'Gemini 3.1 Pro Preview'},
  {id: 'gemini-3.1-flash-lite', label: 'Gemini 3.1 Flash Lite'},
  {id: 'gemini-4-argon', label: 'Gemini 4 Argon'},
  {id: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro'},
  {id: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash'},
];

const FALLBACK_IMAGE_MODELS = [
  {
    id: 'gemini-3.1-flash-image',
    label: 'Gemini 3.1 Flash Image — Nano Banana 2 (Latest)'
  },
  {
    id: 'gemini-3.1-flash-image-preview',
    label: 'Gemini 3.1 Flash Image Preview'
  },
  {id: 'gemini-nano-banana-2.1', label: 'Gemini Nano Banana 2.1'},
  {id: 'gemini-3-pro-image', label: 'Gemini 3 Pro Image — Nano Banana Pro'},
  {id: 'gemini-3-pro-image-preview', label: 'Gemini 3 Pro Image Preview'},
  {id: 'gemini-3.1-flash-lite-image', label: 'Gemini 3.1 Flash Lite Image'},
  {id: 'gemini-2.5-flash-image', label: 'Gemini 2.5 Flash Image'},
];

function modelSelect(id, currentValue, options, onChange) {
  const list =
      Array.isArray(options) && options.length ? options : FALLBACK_MODELS;
  const cur = String(currentValue || '').trim() || list[0].id;
  const hasCur = list.some((m) => m.id === cur);
  const items = hasCur ? list : [{id: cur, label: `${cur} (Custom)`}, ...list];
  return h(
      `select.input#${id}`, {
        onchange: (e) => onChange(e.target.value),
      },
      items.map(
          (m) =>
              h('option', {value: m.id, selected: m.id === cur},
                `${m.label} (${m.id})`)));
}

export async function render(root, params) {
  const pid = params.pid;
  let project = store.project;
  const [{settings: loaded}, vpRes, renRes, modelsRes, setsRes] =
      await Promise.all([
        get(`/api/projects/${pid}/settings`),
        cached('/api/viewports').catch(() => []),
        get('/api/renderers').catch(() => ({renderers: []})),
        cached('/api/models').catch(() => ({})),
        get('/api/sample_sets', {quiet: true}).catch(() => ({sets: []})),
      ]);
  const sampleSets =
      (setsRes && Array.isArray(setsRes.sets)) ? setsRes.sets : [];
  const textModels =
      (modelsRes && modelsRes.models && modelsRes.models.length) ?
      modelsRes.models :
      FALLBACK_MODELS;
  const imageModels =
      (modelsRes && modelsRes.image_models && modelsRes.image_models.length) ?
      modelsRes.image_models :
      FALLBACK_IMAGE_MODELS;
  const settings = JSON.parse(JSON.stringify(loaded || {}));
  settings.viewports = settings.viewports || ['phone'];
  settings.renderers = settings.renderers || [
    'web',
  ];
  settings.generator = {
    model: 'gemini-3.8-flash',
    temperature: 0.7,
    max_repairs: 1,
    include_guidelines: false,
    framing: 'card',
    ...(settings.generator || {}),
  };
  settings.judge = {
    enabled: true,
    model: 'gemini-3.8-flash',
    use_screenshot: true,
    ...(settings.judge || {}),
  };
  settings.imagery = {
    enabled: true,
    image_model: 'gemini-3.1-flash-image',
    svg_model: 'gemini-3.8-flash',
    ...(settings.imagery || {}),
  };
  settings.data_source = {
    kind: '',
    sample_set_id: '',
    sample_set_name: '',
    category: '',
    subcategory: '',
    sample_ids: [],
    import_id: '',
    source_kind: '',
    ...(settings.data_source || {}),
  };
  const viewports = Array.isArray(vpRes) ? vpRes : (vpRes.viewports || []);
  let dirty = false;

  const saveBtn = h(
      'button.btn.btn-primary#save-settings',
      {type: 'button', disabled: true, onclick: () => save()}, 'Save settings');
  const dirtyEl =
      h('span.dirty', {hidden: true}, h('span.dot'), 'Unsaved changes');
  const markDirty = () => {
    dirty = true;
    saveBtn.disabled = false;
    dirtyEl.hidden = false;
  };

  // ---------- General ----------
  const nameIn = h('input.input#settings-name', {value: project.name});
  const descIn =
      h('textarea.input#settings-description', {rows: 2},
        project.description || '');
  const general = section(
      'general', 'Project', null,
      h('label.field', h('span.field-label', 'Name'), nameIn),
      h('label.field', h('span.field-label', 'Description'), descIn),
      h('div',
        h('button.btn.btn-ghost.btn-sm#save-project-meta', {
          type: 'button',
          onclick: async () => {
            const {project: p} = await patch(
                `/api/projects/${pid}`,
                {name: nameIn.value.trim(), description: descIn.value.trim()});
            store.project = {...store.project, ...p};
            project = store.project;
            emit('project-updated');
            toast('Project updated', 'good');
          },
        },
          'Update name & description')));

  // ---------- Viewports & Renderers ----------
  const renderersList =
      (renRes && renRes.renderers && renRes.renderers.length) ?
      renRes.renderers :
      [
        {
          id: 'web',
          label: 'Web (HTML/CSS)',
          engine: 'Headless Chromium (CDP) + A2UI Web Renderer',
          badge: 'WEB',
        },
        {
          id: 'android_compose',
          label: 'Android Jetpack Compose',
          engine: 'AndroidX Compose M3 A2UI + Robolectric Skia (API 35)',
          badge: 'COMPOSE',
        },
        {
          id: 'android_remote_compose',
          label: 'Android RemoteCompose (.rc)',
          engine:
              'RemoteComposeCreation (.rc wire) + RemoteComposePlayer + Skia',
          badge: 'RC WIRE',
        },
        {
          id: 'ios_swiftui',
          label: 'iOS SwiftUI',
          engine: 'Apple SwiftUI A2UI + iOS Simulator (IPHONE_13_PRO)',
          badge: 'SWIFTUI',
        },
      ];
  const vpSection = section(
      'viewports', 'Default viewports & target renderers',
      'Used for new runs (you can change them per run). Native Android Compose / RemoteCompose (.rc) and iOS SwiftUI renderers run headless alongside Web.',
      h('div.field', h('span.field-label', 'Viewports'),
        h('div.vp-grid#settings-viewports',
          viewports.map(
              (v) =>
                  h('label.check.vp-check', h('input', {
                      type: 'checkbox',
                      id: `settings-vp-${v.id}`,
                      checked: settings.viewports.includes(v.id),
                      onchange: (e) => {
                        settings.viewports = e.target.checked ?
                            [...new Set([...settings.viewports, v.id])] :
                            settings.viewports.filter((x) => x !== v.id);
                        markDirty();
                      },
                    }),
                    h('span', v.label || v.id),
                    h('span.muted', `${v.width}\u00d7${v.height}`))))),
      h('div.field', h('span.field-label', 'Default Target Renderers'),
        h('div.renderer-grid#settings-renderers',
          renderersList.map(
              (ren) =>
                  h('label.check.vp-check.renderer-check', h('input', {
                      type: 'checkbox',
                      id: `settings-ren-${ren.id}`,
                      checked: settings.renderers.includes(ren.id),
                      onchange: (e) => {
                        settings.renderers = e.target.checked ?
                            [...new Set([...settings.renderers, ren.id])] :
                            settings.renderers.filter((x) => x !== ren.id);
                        markDirty();
                      },
                    }),
                    h('span.renderer-check-main',
                      h('span.renderer-check-title',
                        h('span.renderer-badge', {dataset: {renderer: ren.id}},
                          ren.badge || ren.id),
                        h('span', ren.label || ren.id)),
                      h('span.muted.small', ren.engine || '')))))),
      h('label.field', h('span.field-label', 'Default Data Source for Runs'),
        h('select.input#settings-data-source', {
          onchange: (e) => {
            const val = e.target.value;
            if (!val) {
              settings.data_source = {
                kind: '',
                sample_set_id: '',
                sample_set_name: '',
                category: '',
                subcategory: '',
                sample_ids: [],
                import_id: '',
                source_kind: '',
              };
            } else {
              const chosen = sampleSets.find((s) => s.id === val);
              settings.data_source = {
                kind: 'set',
                sample_set_id: val,
                sample_set_name: (chosen && chosen.name) || '',
                category: '',
                subcategory: '',
                sample_ids: (chosen && chosen.sample_ids) || [],
                import_id: '',
                source_kind: '',
              };
            }
            markDirty();
          },
        },
          h('option', {
            value: '',
            selected: !settings.data_source.sample_set_id,
          },
            'None (pick in New Run dialog or use starter library)'),
          sampleSets.map(
              (s) =>
                  h('option', {
                    value: s.id,
                    selected: settings.data_source.sample_set_id === s.id,
                  },
                    `${s.name} (${s.count ?? (s.sample_ids || []).length})`))),
        h('span.help',
          'Pre-selected in the New Run dialog and used when a run is started without an explicit sample set. Figma and Stitch imports can set this automatically.')));

  // ---------- Generator ----------
  const g = settings.generator;
  const generator = section(
      'generator', 'Generator',
      'The model that turns constitution + catalog + sample into A2UI.',
      h('label.field', h('span.field-label', 'Gemini Version'),
        modelSelect(
            'gen-model', g.model, textModels,
            (val) => {
              g.model = val;
              markDirty();
            })),
      h('div.form-grid',
        h('label.field', h('span.field-label', 'Temperature'),
          h('input.input#gen-temperature', {
            type: 'number',
            min: 0,
            max: 2,
            step: 0.1,
            value: g.temperature,
            oninput: (e) => {
              g.temperature = Number(e.target.value);
              markDirty();
            },
          })),
        h('label.field', h('span.field-label', 'Max repairs'),
          h('input.input#gen-max-repairs', {
            type: 'number',
            min: 0,
            max: 5,
            step: 1,
            value: g.max_repairs,
            oninput: (e) => {
              g.max_repairs = Number(e.target.value);
              markDirty();
            },
          })),
        h('label.field', h('span.field-label', 'Framing'),
          h('select.input#gen-framing', {
            onchange: (e) => {
              g.framing = e.target.value;
              markDirty();
            },
          },
            [
              ['card', 'Card (assistant answer)'],
              ['page', 'Page (full surface)'],
            ]
                .map(
                    ([v, l]) => h(
                        'option', {value: v, selected: g.framing === v}, l))))),
      toggle({
        id: 'gen-include-guidelines',
        checked: g.include_guidelines,
        label: 'Include Dos & Don\u2019ts in the generator prompt',
        help: 'Off: runs measure the constitution alone.',
        onChange: (v) => {
          g.include_guidelines = v;
          markDirty();
        },
      }));

  // ---------- Judge ----------
  const j = settings.judge;
  const judge = section(
      'judge', 'Judge',
      'Scores outputs against the constitution and general layout quality \u2014 not pixel styling. Human verdicts always win.',
      toggle({
        id: 'judge-enabled',
        checked: j.enabled,
        label: 'Judge outputs by default',
        onChange: (v) => {
          j.enabled = v;
          markDirty();
        },
      }),
      h('label.field', h('span.field-label', 'Gemini Version'),
        modelSelect(
            'judge-model', j.model, textModels,
            (val) => {
              j.model = val;
              markDirty();
            })),
      toggle({
        id: 'judge-use-screenshot',
        checked: j.use_screenshot,
        label: 'Send the screenshot to the judge',
        help: 'Multimodal judging sees what people see.',
        onChange: (v) => {
          j.use_screenshot = v;
          markDirty();
        },
      }));

  // ---------- Generated Imagery & SVGs ----------
  const im = settings.imagery;
  const imagerySection = section(
      'imagery', 'Generated Imagery & SVGs',
      'Models used when the UI generator emits /gen/img?... (full-bleed Card background images, hero banners, inline illustrations) or /gen/svg?... (custom SVG icons and vector illustrations) referencing your style presets.',
      toggle({
        id: 'imagery-enabled',
        checked: im.enabled !== false,
        label: 'Materialize generated imagery & SVGs during runs',
        help:
            'Calls Gemini with your style guidance and reference images, caching assets by content hash.',
        onChange: (v) => {
          im.enabled = v;
          markDirty();
        },
      }),
      h('div.form-grid.form-grid-2',
        h('label.field',
          h('span.field-label', 'Image Generation Model (Raster)'),
          modelSelect(
              'imagery-image-model', im.image_model, imageModels,
              (val) => {
                im.image_model = val;
                markDirty();
              })),
        h('label.field', h('span.field-label', 'SVG Generation Model (Vector)'),
          modelSelect(
              'imagery-svg-model', im.svg_model, textModels,
              (val) => {
                im.svg_model = val;
                markDirty();
              }))),
      h('p.help.small',
        'Configure reusable imagery & iconography style presets and reference images in ',
        h('a', {href: `/p/${pid}/design?tab=imagery`},
          'Design system \u203a Imagery & Icons \u2192'),
        '.'));

  // ---------- Export ----------
  const exp = section(
      'export', 'Export', null,
      h('div.export-row',
        h('button.btn.btn-ghost#export-constitution', {
          type: 'button',
          onclick: () =>
              download(`/api/export/projects/${pid}/constitution.md`),
        },
          icon('description'), 'Constitution .md'),
        h('button.btn.btn-ghost#export-history', {
          type: 'button',
          onclick: () => download(`/api/export/projects/${pid}/history.json`),
        },
          icon('history'), 'History .json'),
        h('button.btn.btn-ghost#export-guidelines-zip', {
          type: 'button',
          onclick: () => download(`/api/export/projects/${pid}/guidelines.zip`),
        },
          icon('folder_zip'), 'Dos & Don\u2019ts .zip'),
        h('button.btn.btn-ghost#export-package-zip', {
          type: 'button',
          title:
              'Constitution + A2UI catalog.json + custom component implementations + tokens',
          onclick: () => download(`/api/export/projects/${pid}/package.zip`),
        },
          icon('inventory_2'), 'Design package .zip'),
        h('button.btn.btn-ghost#export-project-json', {
          type: 'button',
          onclick: () => {
            const blob = new Blob(
                [JSON.stringify({project, settings}, null, 2)],
                {type: 'application/json'});
            const a = h('a', {
              href: URL.createObjectURL(blob),
              download: `${
                  (project.name || 'project')
                      .replace(/\W+/g, '-')
                      .toLowerCase()}-settings.json`,
            });
            document.body.appendChild(a);
            a.click();
            a.remove();
          },
        },
          icon('data_object'), 'Settings .json')));

  const danger = section(
      'danger', 'Archive or delete', null,
      h('div.export-row',
        h('button.btn.btn-ghost#archive-project', {
          type: 'button',
          onclick: async () => {
            await patch(`/api/projects/${pid}`, {archived: !project.archived});
            toast(
                project.archived ? 'Project restored' : 'Project archived',
                'good');
            navigate('/');
          },
        },
          icon('archive'), project.archived ? 'Unarchive' : 'Archive project'),
        h('button.btn.btn-danger-quiet#delete-project', {
          type: 'button',
          onclick: async () => {
            if (!(await confirmDialog(
                    'Delete project?',
                    `\u201c${
                        project
                            .name}\u201d and its revisions, runs and Dos & Don\u2019ts will be permanently deleted.`,
                    {confirmLabel: 'Delete forever', danger: true})))
              return;
            await del(`/api/projects/${pid}`);
            toast('Project deleted', 'good');
            navigate('/');
          },
        },
          icon('delete_forever'), 'Delete project')));

  function section(id, title, help, ...children) {
    return h(
        'section.settings-section', {id},
        h('div.settings-section-head', h('h2', title),
          help ? h('p.help', help) : null),
        h('div.settings-section-body', children));
  }

  const navItems = [
    ['general', 'Project'],
    ['viewports', 'Viewports'],
    ['generator', 'Generator'],
    ['judge', 'Judge'],
    ['imagery', 'Imagery & SVGs'],
    ['export', 'Export'],
  ];
  const designPointer = h(
      'div.callout.settings-moved#settings-design-pointer', icon('palette'),
      h('span', 'Catalog, tokens & imagery styles live in ',
        h('a', {href: `/p/${pid}/design?tab=components`},
          'Design system \u2192'),
        ' \u2014 components, theme tokens, imagery/icon presets and the design package live there.'));
  const page =
      h('div.page.page-settings',
        h('div.page-head.sticky-head',
          h('div.title-row', h('h1', 'Settings'), dirtyEl),
          h('div.page-actions', saveBtn)),
        h('div.settings-layout',
          h('nav.settings-nav',
            navItems.map(
                ([id, l]) =>
                    h('a', {
                      href: `#${id}`,
                      onclick: (e) => {
                        e.preventDefault();
                        document.getElementById(id).scrollIntoView(
                            {behavior: 'smooth', block: 'start'});
                      },
                    },
                      l)),
            h('a.settings-nav-ext', {href: `/p/${pid}/design`},
              'Design system \u2192')),
          h('div.settings-main', designPointer, general, vpSection, generator,
            judge, imagerySection, exp, danger)));
  root.appendChild(page);

  async function save() {
    saveBtn.disabled = true;
    try {
      // Merge only the keys this page owns into the server's current copy.
      const {settings: fresh} = await get(`/api/projects/${pid}/settings`);
      const next = {...(fresh || {})};
      for (const k of OWNED_KEYS) next[k] = settings[k];
      const res = await put(`/api/projects/${pid}/settings`, {settings: next});
      const saved = (res && res.settings) || next;
      for (const k of OWNED_KEYS) {
        if (saved[k] !== undefined) settings[k] = saved[k];
      }
      if (store.project) store.project.settings = saved;
      dirty = false;
      dirtyEl.hidden = true;
      emit('settings-changed', {settings: saved, source: 'settings'});
      toast('Settings saved', 'good');
    } catch (e) {
      saveBtn.disabled = false;
    }
  }

  const unsaved = (e) => {
    if (dirty) {
      e.preventDefault();
      e.returnValue = '';
    }
  };
  window.addEventListener('beforeunload', unsaved);
  // Old deep links (#theme, #catalog) now live on the Design system page.
  const legacy = {
    theme: 'tokens',
    catalog: 'components'
  }[location.hash.slice(1)];
  if (legacy) {
    navigate(`/p/${pid}/design?tab=${legacy}`, {replace: true});
  } else if (location.hash) {
    setTimeout(
        () => document.getElementById(location.hash.slice(1))?.scrollIntoView({
          block: 'start',
        }),
        50);
  }
  return () => {
    window.removeEventListener('beforeunload', unsaved);
  };
}
