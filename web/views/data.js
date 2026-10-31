// Data library: global samples, sample sets, synthesis jobs.
import {cached, del, get, patch, pollJob, post, put, qs} from '../components/api.js';
import {chip, genreChip, loadGenres, resetGenres} from '../components/badges.js';
import {debounce, emptyState, fmtTime, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {jsonTree} from '../components/jsontree.js';
import {emit, navigate, store} from '../components/store.js';
import {confirmDialog, drawer, modal, progressBar, promptDialog, segmented, toast} from '../components/ui.js';

const SOURCE_LABEL = {
  synth: 'synthesized',
  manual: 'manual',
  import: 'imported',
  figma: 'Figma',
  stitch: 'Stitch',
  mcp: 'connected app'
};

/** "App" chip for samples imported from a connected app (MCP tool). */
function appChip(s) {
  const ca = (s.meta && s.meta.connected_app) || {};
  return h(
      'span.chip.app-chip', {
        title: ca.app_name ?
            `From ${ca.app_name}${ca.tool ? ` \u203a ${ca.tool}` : ''}` :
            'Imported from a connected app'
      },
      icon('extension'), 'App');
}

export async function render(root, params, ctx) {
  const pid = params.pid || null;

  /** The library is global: pick which project's constitution to run. */
  async function runOnSelection() {
    const {projects} = await get('/api/projects');
    const live = (projects || []).filter((p) => !p.archived);
    if (!live.length) {
      toast(
          'Create a project first, then run its constitution on these samples.',
          'warn');
      return;
    }
    let last = pid || store.lastProjectId;
    try {
      last = last || localStorage.getItem('dcb.lastProject');
    } catch {
    }
    const sel =
        h('select.input#run-selection-project',
          live.map(
              (p) =>
                  h('option', {value: p.id, selected: p.id === last}, p.name)));
    modal({
      title: `Run ${selected.size} sample${selected.size === 1 ? '' : 's'}`,
      id: 'run-selection-dialog',
      body: [
        h('label.field', h('span.field-label', 'Project'), sel),
        h('p.help',
          'Samples are shared by all projects. The run uses the chosen project\u2019s constitution, catalog and tokens.'),
      ],
      actions: [
        {label: 'Cancel'},
        {
          label: 'Continue',
          kind: 'primary',
          id: 'run-selection-go',
          onClick: () => navigate(`/p/${sel.value}/runs?new=selection`),
        },
      ],
    });
  }
  const url = ctx.url;
  const f = {
    group: url.searchParams.get('group') || '',
    genre: url.searchParams.get('genre') || '',
    q: url.searchParams.get('q') || '',
    set_id: url.searchParams.get('set') || ''
  };
  // Client-side filter (the samples API has no source param).
  let sourceFilter = url.searchParams.get('source') || '';
  let samples = [];
  let sets = [];
  let genres = {groups: []};
  const selected = store.dataSelection;
  let alive = true;

  const setsEl = h('nav.sets#sample-sets');
  const tableEl = h('div.table-wrap#samples-table');
  const jobEl = h('div.job-slot#job-progress');
  const selBar = h('div.selbar#selection-bar');
  const groupSel = h('select.input.input-sm#filter-group', {
    onchange: (e) => {
      f.group = e.target.value;
      f.genre = '';
      fillGenreSel();
      load();
    }
  });
  const genreSel = h('select.input.input-sm#filter-genre', {
    onchange: (e) => {
      f.genre = e.target.value;
      load();
    }
  });
  const search = h('input.input.input-sm#filter-q', {
    type: 'search',
    placeholder: 'Search titles, intents, tags\u2026',
    value: f.q,
    oninput: debounce(
        (e) => {
          f.q = e.target.value;
          load();
        },
        300)
  });
  const countEl = h('span.muted.count-label');
  const sourceSel = h(
      'select.input.input-sm#filter-source', {
        title: 'Filter by where samples came from',
        onchange: (e) => {
          sourceFilter = e.target.value;
          renderTable();
          syncSourceUrl();
        }
      },
      [
        ['', 'All sources'], ['synth', 'Synthesized'], ['figma', 'Figma'],
        ['stitch', 'Stitch'], ['mcp', 'Connected apps'], ['manual', 'Manual'],
        ['import', 'Imported']
      ].map(([v,
              l]) => h('option', {value: v, selected: sourceFilter === v}, l)));
  function syncSourceUrl() {
    const u = new URL(location.href);
    sourceFilter ? u.searchParams.set('source', sourceFilter) :
                   u.searchParams.delete('source');
    history.replaceState(null, '', u.pathname + u.search);
  }
  const visible = () => sourceFilter ?
      samples.filter((s) => (s.source || '') === sourceFilter) :
      samples;

  root.appendChild(h(
      'div.page.page-data',
      h('div.page-head',
        h('div', h('h1', 'Data library'),
          h('p.lede',
            'Synthetic situations the generator must handle \u2014 shared across all projects. Each sample has an intent, context, and a JSON payload.')),
        h('div.page-actions',
          h('button.btn.btn-ghost#add-sample',
            {type: 'button', onclick: () => addSampleDialog()}, icon('add'),
            'Add sample'),
          h('button.btn.btn-ghost#synthesize',
            {type: 'button', onclick: () => synthesizeDialog()},
            icon('auto_awesome'), 'Synthesize\u2026'),
          h('button.btn.btn-primary#starter-library',
            {type: 'button', onclick: () => starterLibrary()},
            icon('library_add'), 'Generate starter library'))),
      jobEl,
      h('div.data-layout', setsEl,
        h('div.data-main',
          h('div.filterbar', groupSel, genreSel, sourceSel, search,
            h('button.btn.btn-sm.btn-quiet#manage-categories', {
              type: 'button',
              title: 'Create, rename or delete your own categories',
              onclick: () => manageCategories()
            },
              icon('category'), 'Categories'),
            h('div.grow'), countEl),
          selBar, tableEl))));

  function fillGroupSel() {
    mount(
        groupSel, h('option', {value: ''}, 'All categories'),
        genres.groups.map(
            (g) => h(
                'option', {value: g.id, selected: f.group === g.id}, g.label)));
  }
  function fillGenreSel() {
    const groups =
        f.group ? genres.groups.filter((g) => g.id === f.group) : genres.groups;
    mount(
        genreSel, h('option', {value: ''}, 'All subcategories'),
        groups.map(
            (g) =>
                h('optgroup', {label: g.label},
                  g.genres.map(
                      (x) =>
                          h('option', {value: x.id, selected: f.genre === x.id},
                            x.label)))));
  }

  async function loadSets() {
    try {
      const res = await get('/api/sample_sets');
      sets = res.sets || [];
    } catch (e) {
      sets = [];
    }
    renderSets();
  }
  function renderSets() {
    const activeProj = store.project;
    const defaultSetId = activeProj?.settings?.data_source?.sample_set_id || '';
    mount(
        setsEl, h('div.sets-title', 'Sample sets'),
        h('button.set-item', {
          type: 'button',
          id: 'set-all',
          class: !f.set_id ? 'on' : '',
          onclick: () => {
            f.set_id = '';
            renderSets();
            load();
          }
        },
          h('span', 'All samples'), h('span.count', '')),
        sets.map((s) => {
          const isDefault = Boolean(defaultSetId && defaultSetId === s.id);
          return h(
              'div.set-row', {class: f.set_id === s.id ? 'on' : ''},
              h('button.set-item', {
                type: 'button',
                id: `set-${s.id}`,
                class: f.set_id === s.id ? 'on' : '',
                title: s.description || s.name,
                onclick: () => {
                  f.set_id = s.id;
                  renderSets();
                  load();
                }
              },
                h('span.set-name', s.name),
                isDefault ? h('span.chip.chip-xs.v-good', {
                  title: `Default run data source for ${
                      activeProj?.name || 'current project'}`
                },
                              'default') :
                            null,
                h('span.count', `${s.count ?? (s.sample_ids || []).length}`)),
              activeProj && !isDefault ? h('button.icon-btn.set-default-btn', {
                type: 'button',
                id: `set-default-${s.id}`,
                'aria-label': `Set ${s.name} as default run data source`,
                title: `Set as default run data source for ${
                    activeProj.name || 'project'}`,
                onclick: async () => {
                  const {settings: fresh} = await get(
                      `/api/projects/${activeProj.id}/settings`, {quiet: true});
                  const nextSettings = {
                    ...(fresh || {}),
                    data_source: {
                      kind: 'set',
                      sample_set_id: s.id,
                      sample_set_name: s.name || '',
                      category: '',
                      subcategory: '',
                      sample_ids: s.sample_ids || [],
                      import_id: '',
                      source_kind: '',
                    },
                  };
                  const saved = await put(
                      `/api/projects/${activeProj.id}/settings`,
                      {settings: nextSettings});
                  if (store.project && store.project.id === activeProj.id) {
                    store.project.settings = saved.settings || nextSettings;
                    emit('settings-changed', {
                      settings: store.project.settings,
                      source: 'data',
                    });
                  }
                  toast(
                      `\u201c${s.name}\u201d set as default run data source`,
                      'good');
                  renderSets();
                }
              },
                                           icon('star_outline')) :
                                         null,
              h('button.icon-btn.set-del', {
                type: 'button',
                'aria-label': `Delete set ${s.name}`,
                title: 'Delete set',
                onclick: async () => {
                  if (!(await confirmDialog(
                          'Delete sample set?',
                          `\u201c${
                              s.name}\u201d will be removed. Samples stay in the library.`,
                          {confirmLabel: 'Delete', danger: true})))
                    return;
                  await del(`/api/sample_sets/${s.id}`);
                  if (f.set_id === s.id) f.set_id = '';
                  loadSets();
                  load();
                }
              },
                icon('delete')));
        }),
        !sets.length ?
            h('p.help',
              'Sets are created by the starter library, by synthesis, by Figma/Stitch imports, or from a selection.') :
            null);
  }

  let loadSeq = 0;
  async function load() {
    const seq = ++loadSeq;
    if (!samples.length) mount(tableEl, spinner('Loading samples\u2026'));
    const res = await get(`/api/samples${qs(f)}`);
    if (seq !== loadSeq || !alive) return;
    samples = res.samples || [];
    renderTable();
    const u = new URL(location.href);
    ['group', 'genre', 'q'].forEach(
        (k) => (f[k] ? u.searchParams.set(k, f[k]) : u.searchParams.delete(k)));
    f.set_id ? u.searchParams.set('set', f.set_id) :
               u.searchParams.delete('set');
    sourceFilter ? u.searchParams.set('source', sourceFilter) :
                   u.searchParams.delete('source');
    history.replaceState(null, '', u.pathname + u.search);
  }

  function renderSel() {
    const n = selected.size;
    selBar.hidden = !n;
    if (!n) return;
    mount(
        selBar, h('span', h('strong', `${n}`), ` selected`),
        h('button.btn.btn-sm.btn-ghost#create-set',
          {type: 'button', onclick: () => createSet()}, icon('playlist_add'),
          'Create set'),
        h('button.btn.btn-sm.btn-ghost#move-selection',
          {type: 'button', onclick: () => moveSelection()},
          icon('drive_file_move'), 'Move to\u2026'),
        h('button.btn.btn-sm.btn-ghost#web-photos-selection', {
          type: 'button',
          title:
              'Replace placeholder images with matching photos from Wikimedia Commons',
          onclick: () => webPhotos([...selected])
        },
          icon('photo_library'), 'Add web photos'),
        h('button.btn.btn-sm.btn-ghost#run-selection',
          {type: 'button', onclick: () => runOnSelection()}, icon('play_arrow'),
          'Run on selection\u2026'),
        h('button.btn.btn-sm.btn-quiet#clear-selection', {
          type: 'button',
          onclick: () => {
            selected.clear();
            renderTable();
          }
        },
          'Clear'));
  }

  function renderTable() {
    const rows = visible();
    countEl.textContent =
        `${rows.length} sample${rows.length === 1 ? '' : 's'}${
            rows.length !== samples.length ? ` of ${samples.length}` : ''}`;
    renderSel();
    if (!rows.length) {
      const filtered = f.group || f.genre || f.q || f.set_id || sourceFilter;
      mount(
          tableEl,
          filtered ?
              emptyState('No samples match', 'Try clearing filters.') :
              emptyState(
                  'The library is empty',
                  `Generate the starter library to get 3 samples for each of the ${
                      builtinCount()} built-in subcategories, grouped into sets you can run against \u2014 or synthesize your own from a description.`,
                  h('button.btn.btn-primary#starter-library-empty',
                    {type: 'button', onclick: () => starterLibrary()},
                    icon('library_add'), 'Generate starter library')));
      return;
    }
    const allOn = rows.every((s) => selected.has(s.id));
    const headCheck = h('input#select-all', {
      type: 'checkbox',
      checked: allOn && rows.length > 0,
      'aria-label': 'Select all',
      onchange: (e) => {
        rows.forEach(
            (s) => (
                e.target.checked ? selected.add(s.id) : selected.delete(s.id)));
        renderTable();
      }
    });
    mount(
        tableEl,
        h('table.table.samples',
          h('thead',
            h('tr', h('th.cell-check', headCheck), h('th', 'Title'),
              h('th', 'Genre'), h('th', 'Size'), h('th', 'State'),
              h('th', 'Tags'), h('th', 'Source'))),
          h('tbody',
            rows.map(
                (s) =>
                    h('tr.row-click', {
                      dataset: {sampleId: s.id},
                      class: selected.has(s.id) ? 'sel' : '',
                      onclick: (e) => {
                        if (e.target.closest('input')) return;
                        openSample(s.id);
                      }
                    },
                      h('td.cell-check', h('input', {
                          type: 'checkbox',
                          checked: selected.has(s.id),
                          'aria-label': `Select ${s.title}`,
                          onchange: (e) => {
                            e.target.checked ? selected.add(s.id) :
                                               selected.delete(s.id);
                            e.target.closest('tr').classList.toggle(
                                'sel', e.target.checked);
                            renderSel();
                          }
                        })),
                      h('td',
                        h('div.cell-title', s.title,
                          s.source === 'mcp' ? appChip(s) : null),
                        h('div.cell-sub', s.intent || '')),
                      h('td', genreChip(s.genre, s.group)),
                      h('td', h('span.size', s.size_class || '')),
                      h('td',
                        s.state && s.state !== 'content' ?
                            chip(s.state, `st-${s.state}`) :
                            h('span.muted', 'content')),
                      h('td.cell-tags',
                        (s.tags || [])
                            .slice(0, 3)
                            .map((t) => chip(t, 'chip-quiet')),
                        (s.tags || []).length > 3 ?
                            h('span.muted', ` +${s.tags.length - 3}`) :
                            null),
                      h('td.muted',
                        SOURCE_LABEL[s.source] || s.source || ''))))));
  }

  async function createSet() {
    const name = await promptDialog('Create sample set', {
      label: 'Name',
      placeholder: 'e.g. Care \u2014 long lists',
      confirmLabel: 'Create set',
      help: `${selected.size} samples`
    });
    if (!name || !name.trim()) return;
    const res = await post(
        '/api/sample_sets', {name: name.trim(), sample_ids: [...selected]});
    toast(`Created set \u201c${name.trim()}\u201d`, 'good');
    selected.clear();
    await loadSets();
    if (res && res.set) {
      f.set_id = res.set.id;
      renderSets();
    }
    load();
  }

  async function openSample(id) {
    const d = drawer({
      title: 'Sample',
      wide: true,
      id: 'sample-drawer',
      body: spinner('Loading\u2026')
    });
    const {sample} = await get(`/api/samples/${id}`);
    const showView = () => {
      d.setTitle(sample.title);
      const c = sample.context || {};
      mount(
          d.body,
          h('div.sample-detail',
            h('div.chip-row', genreChip(sample.genre, sample.group),
              h('button.link-btn#sample-move', {
                type: 'button',
                onclick: async () => {
                  const target = await pickCategoryDialog(
                      'Move sample', sample.genre, 'Move');
                  if (!target) return;
                  const res = await patch(`/api/samples/${sample.id}`, target);
                  Object.assign(sample, res.sample);
                  await reloadGenres();
                  toast('Moved', 'good');
                  showView();
                  load();
                }
              },
                'Move\u2026'),
              chip(`size ${sample.size_class}`),
              chip(sample.state || 'content', `st-${sample.state}`),
              (sample.tags || []).map((t) => chip(t, 'chip-quiet'))),
            h('section', h('h3.label', 'Intent'),
              h('p.intent', sample.intent || '\u2014')),
            connectedAppSection(sample), imageStrip(sample),
            h('section', h('h3.label', 'Context'),
              h('dl.kv',
                Object.entries(c)
                    .filter(([, v]) => v != null && v !== '')
                    .map(
                        ([k, v]) =>
                            h('div', h('dt', k.replace(/_/g, ' ')),
                              h('dd',
                                typeof v === 'object' ? JSON.stringify(v) :
                                                        String(v)))))),
            (sample.edge_cases || []).length ?
                h('section', h('h3.label', 'Edge cases'),
                  h('ul.plain', sample.edge_cases.map((x) => h('li', x)))) :
                null,
            h('section',
              h('div.section-head', h('h3.label', 'Payload'),
                h('button.btn.btn-sm.btn-quiet', {
                  type: 'button',
                  onclick: () =>
                      navigator.clipboard
                          .writeText(JSON.stringify(sample.payload, null, 2))
                          .then(() => toast('Payload copied', 'good'))
                },
                  icon('content_copy'), 'Copy')),
              jsonTree(sample.payload, {collapseDepth: 2})),
            formSection(sample),
            h('p.muted.small',
              `${sample.source || ''} \u00b7 ${
                  sample.owner_ldap || ''} \u00b7 ${
                  relTime(sample.created_at)} \u00b7 id ${sample.id}`),
            h('div.drawer-actions',
              h('button.btn.btn-ghost#sample-edit',
                {type: 'button', onclick: () => showEdit()}, icon('edit'),
                'Edit JSON'),
              h('button.btn.btn-danger-quiet#sample-delete', {
                type: 'button',
                onclick: async () => {
                  if (!(await confirmDialog(
                          'Delete sample?',
                          `\u201c${
                              sample
                                  .title}\u201d will be removed from the library and all sets.`,
                          {confirmLabel: 'Delete', danger: true})))
                    return;
                  await del(`/api/samples/${sample.id}`);
                  toast('Sample deleted', 'good');
                  d.close();
                  selected.delete(sample.id);
                  load();
                  loadSets();
                }
              },
                icon('delete'), 'Delete'))));
    };
    const showEdit = () => {
      const editable = {
        title: sample.title,
        genre: sample.genre,
        group: sample.group,
        intent: sample.intent,
        context: sample.context,
        size_class: sample.size_class,
        state: sample.state,
        edge_cases: sample.edge_cases,
        tags: sample.tags,
        payload: sample.payload
      };
      const ta =
          h('textarea.input.code-input#sample-json',
            {rows: 28, spellcheck: 'false'}, JSON.stringify(editable, null, 2));
      const err = h('p.form-error');
      mount(
          d.body,
          h('div.sample-edit', ta, err,
            h('div.drawer-actions',
              h('button.btn.btn-primary#sample-save', {
                type: 'button',
                onclick: async () => {
                  let data;
                  try {
                    data = JSON.parse(ta.value);
                  } catch (e) {
                    err.textContent = `Invalid JSON: ${e.message}`;
                    return;
                  }
                  const res = await patch(`/api/samples/${sample.id}`, data);
                  Object.assign(sample, res.sample || data);
                  toast('Sample saved', 'good');
                  showView();
                  load();
                }
              },
                'Save'),
              h('button.btn.btn-ghost',
                {type: 'button', onclick: () => showView()}, 'Cancel'))));
    };
    showView();
  }

  function addSampleDialog() {
    const template = {
      title: '',
      group_label: '',
      genre_label: '',
      intent: '',
      context: {
        surface: 'assistant',
        user_state: '',
        role: '',
        locale: 'en-US',
        time_of_day: '',
        urgency: 'routine'
      },
      payload: {},
      size_class: 'm',
      state: 'content',
      edge_cases: [],
      tags: []
    };
    const ta =
        h('textarea.input.code-input#new-sample-json',
          {rows: 22, spellcheck: 'false', autofocus: true},
          JSON.stringify(template, null, 2));
    const err = h('p.form-error');
    modal({
      title: 'Add sample',
      wide: true,
      id: 'add-sample-dialog',
      body: [
        h('p.help',
          'Paste a sample as JSON. Required: title, payload. Categorize with an existing \u201cgenre\u201d id, or name any category / subcategory in group_label / genre_label (new ones are created; leave both empty for Uncategorized). Images: real https URLs, or /img/<photo-subject>/800x600 placeholders that \u201cAdd web photos\u201d turns into Wikimedia Commons photos.'),
        ta, err
      ],
      actions: [
        {label: 'Cancel'}, {
          label: 'Add sample',
          kind: 'primary',
          id: 'add-sample-submit',
          onClick: async () => {
            let data;
            try {
              data = JSON.parse(ta.value);
            } catch (e) {
              err.textContent = `Invalid JSON: ${e.message}`;
              return false;
            }
            const list = Array.isArray(data) ? data : [data];
            for (const s of list)
              await post('/api/samples', {source: 'manual', ...s});
            toast(
                `Added ${list.length} sample${list.length > 1 ? 's' : ''}`,
                'good');
            load();
          }
        }
      ]
    });
  }

  function synthesizeDialog() {
    const checked = new Set(f.genre ? [f.genre] : []);
    let mode = checked.size ? 'pick' : 'auto';
    const brief = h('textarea.input#synth-brief', {
      rows: 4,
      placeholder:
          'Optional when picking categories. e.g. \u201cA board member texts at 11pm asking to move tomorrow\u2019s 8am while I\u2019m on a red-eye; I need to reshuffle three meetings and reply.\u201d'
    });
    const howMany =
        h('input.input.input-sm#synth-count',
          {type: 'number', min: 1, max: 24, value: 6});
    const per =
        h('input.input.input-sm#synth-per-genre',
          {type: 'number', min: 1, max: 12, value: 3});
    const setName =
        h('input.input#synth-set-name',
          {placeholder: 'Optional \u2014 creates a sample set'});
    const treeEl = h('div.genre-pick#synth-genres');
    const autoPane = h(
        'div.synth-pane#synth-auto',
        h('p.help',
          'Gemini files each sample into the category that fits \u2014 reusing yours or the built-ins, and creating new categories / subcategories when nothing does. You can move samples later.'),
        h('label.field.inline', h('span.field-label', 'How many'), howMany));
    const pickPane = h(
        'div.synth-pane#synth-pick',
        h('div.row-actions',
          h('button.link-btn#synth-all', {
            type: 'button',
            onclick: () => {
              genres.groups.forEach(
                  (g) => g.genres.forEach((x) => checked.add(x.id)));
              renderTree();
            }
          },
            'Select all'),
          h('button.link-btn#synth-none', {
            type: 'button',
            onclick: () => {
              checked.clear();
              renderTree();
            }
          },
            'Clear'),
          h('span.grow'),
          h('button.link-btn#synth-new-group', {
            type: 'button',
            onclick: async () => {
              const label = await promptDialog('New category', {
                label: 'Category name',
                placeholder: 'e.g. Household logistics',
                confirmLabel: 'Create'
              });
              if (!label || !label.trim()) return;
              const {category} = await post(
                  '/api/categories', {kind: 'group', label: label.trim()});
              const sub = await promptDialog('First subcategory', {
                label: `Subcategory in \u201c${label.trim()}\u201d`,
                placeholder: 'e.g. Dinner for guests',
                confirmLabel: 'Create'
              });
              if (sub && sub.trim()) {
                const r = await post(
                    '/api/categories',
                    {kind: 'genre', parent_id: category.id, label: sub.trim()});
                checked.add(r.category.id);
              }
              await reloadGenres();
              renderTree();
            }
          },
            icon('add'), 'New category')),
        treeEl,
        h('label.field.inline', h('span.field-label', 'Per subcategory'), per));
    const seg = segmented(
        [
          {value: 'auto', label: 'Let Gemini categorize'},
          {value: 'pick', label: 'Into categories I pick'}
        ],
        mode, (v) => {
          mode = v;
          syncMode();
        }, {id: 'synth-mode'});
    function syncMode() {
      autoPane.hidden = mode !== 'auto';
      pickPane.hidden = mode !== 'pick';
    }
    function renderTree() {
      mount(treeEl, genres.groups.map((g) => {
        const ids = g.genres.map((x) => x.id);
        const n = ids.filter((id) => checked.has(id)).length;
        const gcb = h('input', {
          type: 'checkbox',
          id: `synth-group-${g.id}`,
          checked: n > 0 && n === ids.length,
          onchange: (e) => {
            ids.forEach(
                (id) =>
                    e.target.checked ? checked.add(id) : checked.delete(id));
            renderTree();
          }
        });
        gcb.indeterminate = n > 0 && n < ids.length;
        return h(
            'fieldset.genre-group',
            h('legend', h('label.check.strong', gcb, g.label),
              g.custom ? chip('yours', 'chip-quiet') : null),
            h('div.genre-boxes',
              g.genres.map(
                  (x) => h(
                      'label.check', {title: x.description || ''}, h('input', {
                        type: 'checkbox',
                        value: x.id,
                        id: `synth-genre-${x.id}`,
                        checked: checked.has(x.id),
                        onchange: (e) => {
                          e.target.checked ? checked.add(x.id) :
                                             checked.delete(x.id);
                          renderTree();
                        }
                      }),
                      x.label,
                      x.custom ? h('span.muted.small', ' \u00b7 yours') :
                                 null)),
              h('button.link-btn.small', {
                type: 'button',
                onclick: async () => {
                  const label = await promptDialog('New subcategory', {
                    label: `Subcategory in \u201c${g.label}\u201d`,
                    placeholder: 'e.g. Late delivery',
                    confirmLabel: 'Create'
                  });
                  if (!label || !label.trim()) return;
                  const r = await post(
                      '/api/categories',
                      {kind: 'genre', parent_id: g.id, label: label.trim()});
                  checked.add(r.category.id);
                  await reloadGenres();
                  renderTree();
                }
              },
                '+ subcategory')));
      }));
    }
    renderTree();
    syncMode();
    modal({
      title: 'Synthesize samples',
      wide: true,
      id: 'synth-dialog',
      body: [
        h('label.field',
          h('span.field-label', 'What should the samples be about?'), brief),
        h('p.help',
          'Gemini writes fictional, realistic data spread across size, state, locale, urgency and edge cases, with real photos from Wikimedia Commons wherever the content would have images.'),
        h('div.field', h('span.field-label', 'Categories'), seg),
        autoPane,
        pickPane,
        h('label.field', h('span.field-label', 'Set name'), setName),
      ],
      actions: [
        {label: 'Cancel'}, {
          label: 'Synthesize',
          kind: 'primary',
          id: 'synth-submit',
          onClick: async () => {
            const text = brief.value.trim();
            const ids = mode === 'pick' ? [...checked] : [];
            if (mode === 'auto' && !text) {
              toast('Describe what to synthesize, or pick categories.', 'warn');
              brief.focus();
              return false;
            }
            if (mode === 'pick' && !ids.length) {
              toast('Pick at least one subcategory.', 'warn');
              return false;
            }
            const n = mode === 'pick' ?
                ids.length * Math.max(1, Number(per.value) || 3) :
                Math.max(1, Number(howMany.value) || 6);
            const {job} = await post('/api/samples/synthesize', {
              genres: ids,
              per_genre: Math.max(1, Number(per.value) || 3),
              count: Math.max(1, Number(howMany.value) || 6),
              brief: text || undefined,
              set_name: setName.value.trim() || undefined
            });
            trackJob(job, `Synthesizing ${n} samples`);
          }
        }
      ]
    });
    brief.focus();
  }

  function builtinCount() {
    return genres.groups.filter((g) => !g.custom)
        .reduce((n, g) => n + g.genres.filter((x) => !x.custom).length, 0);
  }

  async function reloadGenres() {
    resetGenres();
    await loadGenres();
    try {
      genres = await cached('/api/genres');
    } catch (e) {
      genres = {groups: []};
    }
    fillGroupSel();
    fillGenreSel();
  }

  function connectedAppSection(sample) {
    const ca = sample.meta && sample.meta.connected_app;
    if (!ca) return null;
    return h(
        'section.ca-section',
        h('div.section-head', h('h3.label', icon('extension'), 'Connected app'),
          ca.app_id ? h('a.btn.btn-sm.btn-quiet', {
            href: `/apps/${ca.app_id}${
                ca.tool ? `?tool=${encodeURIComponent(ca.tool)}` : ''}`
          },
                        icon('open_in_new'), 'Open tool') :
                      null),
        h('dl.kv',
          h('div', h('dt', 'App'),
            h('dd', ca.app_name || ca.app_id || '\u2014')),
          h('div', h('dt', 'Tool'), h('dd', h('code', ca.tool || '\u2014'))),
          h('div', h('dt', 'Called'),
            h('dd', ca.called_at ? fmtTime(ca.called_at) : '\u2014')),
          ca.latency_ms != null ?
              h('div', h('dt', 'Latency'), h('dd', `${ca.latency_ms} ms`)) :
              null,
          ca.is_error ? h('div', h('dt', 'Result'),
                          h('dd', chip('tool error', 'c-bad'))) :
                        null),
        h('div.ca-args', h('div.small.muted', 'Arguments'),
          jsonTree(ca.args || {}, {collapseDepth: 2})));
  }

  function formSection(sample) {
    const fm = sample.meta && sample.meta.form;
    if (!fm) return null;
    return h(
        'section',
        h('details.ca-form',
          h('summary.label', icon('edit_note'),
            `Input form${fm.title ? ` \u2014 ${fm.title}` : ''}`),
          h('p.help',
            'Runs generate this tool\u2019s input form (prefilled with these values) as a separate \u201cform\u201d stage next to the result.'),
          fm.submit_label ?
              h('p.small', 'Submit button: ', h('strong', fm.submit_label)) :
              null,
          fm.values ? h('div', h('div.small.muted', 'Values'),
                        jsonTree(fm.values, {collapseDepth: 2})) :
                      null,
          h('div', h('div.small.muted', 'Schema'),
            jsonTree(fm.schema || {}, {collapseDepth: 3}))));
  }

  /** Images found in a sample payload (URLs ending like images or /img/). */
  function payloadImages(payload) {
    const out = [];
    const seen = new Set();
    const walk = (v) => {
      if (typeof v === 'string') {
        const re =
            /(https?:\/\/[^\s"')]+\.(?:jpe?g|png|webp|gif)|https:\/\/randomuser\.me\/api\/portraits\/[^\s"')]+|\/img\/[A-Za-z0-9_\-]+\/\d+x\d+(?:\.svg)?)/gi;
        for (const m of v.matchAll(re)) {
          if (!seen.has(m[0])) {
            seen.add(m[0]);
            out.push(m[0]);
          }
        }
      } else if (Array.isArray(v)) {
        v.forEach(walk);
      } else if (v && typeof v === 'object') {
        Object.values(v).forEach(walk);
      }
    };
    walk(payload);
    return out;
  }

  function imageStrip(sample) {
    const urls = payloadImages(sample.payload);
    const credits = (sample.meta && sample.meta.image_credits) || [];
    if (!urls.length) return null;
    const placeholders = urls.filter((u) => u.startsWith('/img/')).length;
    return h(
        'section',
        h('div.section-head', h('h3.label', `Images (${urls.length})`),
          placeholders ?
              h('button.btn.btn-sm.btn-quiet#sample-web-photos',
                {type: 'button', onclick: () => webPhotos([sample.id])},
                icon('photo_library'), `Add web photos (${placeholders})`) :
              null),
        h('div.img-strip',
          urls.slice(0, 24).map(
              (u) =>
                  h('a', {href: u, target: '_blank', rel: 'noopener'},
                    h('img', {src: u, loading: 'lazy', alt: ''})))),
        credits.length ?
            h('details.credits',
              h('summary.muted.small', `Photo credits (${credits.length})`),
              h('ul.plain.small',
                credits.map(
                    (c) =>
                        h('li',
                          h('a', {
                            href: c.page || c.url,
                            target: '_blank',
                            rel: 'noopener'
                          },
                            c.title || c.url),
                          ` \u2014 ${c.artist || 'unknown'} \u00b7 ${
                              c.license || ''} \u00b7 ${c.source || ''}`)))) :
            null);
  }

  async function webPhotos(ids) {
    const {job} = await post('/api/samples/resolve_images', {sample_ids: ids});
    trackJob(job, 'Adding web photos');
  }

  /**
   * Category picker: existing subcategory, or a new category / subcategory.
   * Resolves to a PATCH-able body ({genre} or {group_label, genre_label}).
   */
  function pickCategoryDialog(title, current, confirmLabel = 'Save') {
    return new Promise((resolve) => {
      let result = null;
      const NEW = '__new__';
      const curGroup =
          (genres.groups.find((g) => g.genres.some((x) => x.id === current)) ||
           {}).id;
      const groupSel = h('select.input#pick-group');
      const genreSel = h('select.input#pick-genre');
      const newGroup =
          h('input.input#pick-new-group',
            {placeholder: 'New category name', hidden: true});
      const newGenre =
          h('input.input#pick-new-genre',
            {placeholder: 'New subcategory name', hidden: true});
      const fillGroups = () => mount(
          groupSel,
          genres.groups.map(
              (g) =>
                  h('option', {value: g.id, selected: g.id === curGroup},
                    g.label)),
          h('option', {value: NEW}, '+ New category\u2026'));
      const fillGenres = () => {
        const g = genres.groups.find((x) => x.id === groupSel.value);
        newGroup.hidden = groupSel.value !== NEW;
        mount(
            genreSel,
            (g ? g.genres : [])
                .map(
                    (x) =>
                        h('option', {value: x.id, selected: x.id === current},
                          x.label)),
            h('option', {value: NEW, selected: !g}, '+ New subcategory\u2026'));
        newGenre.hidden = genreSel.value !== NEW;
      };
      groupSel.onchange = fillGenres;
      genreSel.onchange = () => {
        newGenre.hidden = genreSel.value !== NEW;
      };
      fillGroups();
      fillGenres();
      modal({
        title,
        id: 'pick-category-dialog',
        body: [
          h('label.field', h('span.field-label', 'Category'), groupSel,
            newGroup),
          h('label.field', h('span.field-label', 'Subcategory'), genreSel,
            newGenre),
          h('p.help',
            'New categories and subcategories are shared across the library.')
        ],
        actions: [
          {label: 'Cancel'}, {
            label: confirmLabel,
            kind: 'primary',
            id: 'pick-category-ok',
            onClick: () => {
              if (genreSel.value !== NEW) {
                result = {genre: genreSel.value};
                return;
              }
              const g = genres.groups.find((x) => x.id === groupSel.value);
              const gl = g ? g.label : newGroup.value.trim();
              const xl = newGenre.value.trim();
              if (!gl || !xl) {
                toast('Name the new category and subcategory.', 'warn');
                return false;
              }
              result = {group_label: gl, genre_label: xl};
            }
          }
        ],
        onClose: () => resolve(result)
      });
    });
  }

  async function moveSelection() {
    const target = await pickCategoryDialog(
        `Move ${selected.size} sample${selected.size === 1 ? '' : 's'}`,
        f.genre, 'Move');
    if (!target) return;
    let first = null;
    for (const id of selected) {
      // The first PATCH creates any new category; reuse its id for the rest.
      const body = first ? {genre: first} : target;
      const res = await patch(`/api/samples/${id}`, body);
      first = first || (res.sample && res.sample.genre);
    }
    toast(
        `Moved ${selected.size} sample${selected.size === 1 ? '' : 's'}`,
        'good');
    await reloadGenres();
    load();
  }

  function manageCategories() {
    const listEl = h('div.cat-manage#category-list');
    const draw = () => mount(
        listEl,
        genres.groups.map(
            (g) =>
                h('div.cat-group',
                  h('div.cat-row.cat-row-group', h('strong', g.label),
                    g.custom ? chip('yours', 'chip-quiet') :
                               h('span.muted.small', 'built-in'),
                    h('span.grow'), g.custom ? catActions(g, 'category') : null,
                    h('button.link-btn.small', {
                      type: 'button',
                      onclick: async () => {
                        const label = await promptDialog('New subcategory', {
                          label: `Subcategory in \u201c${g.label}\u201d`,
                          confirmLabel: 'Create'
                        });
                        if (!label || !label.trim()) return;
                        await post('/api/categories', {
                          kind: 'genre',
                          parent_id: g.id,
                          label: label.trim()
                        });
                        await reloadGenres();
                        draw();
                      }
                    },
                      '+ subcategory')),
                  g.genres.map(
                      (x) =>
                          h('div.cat-row', {title: x.description || ''},
                            h('span', x.label),
                            x.custom ? chip('yours', 'chip-quiet') : null,
                            h('span.grow'),
                            x.custom ? catActions(x, 'subcategory') : null)))));
    const catActions = (c, noun) =>
        [h('button.icon-btn', {
           type: 'button',
           title: `Rename ${noun}`,
           'aria-label': `Rename ${c.label}`,
           onclick: async () => {
             const label = await promptDialog(
                 `Rename ${noun}`, {value: c.label, confirmLabel: 'Rename'});
             if (!label || !label.trim() || label.trim() === c.label) return;
             await patch(`/api/categories/${c.id}`, {label: label.trim()});
             await reloadGenres();
             draw();
             load();
           }
         },
           icon('edit')),
         h('button.icon-btn', {
           type: 'button',
           title: `Delete ${noun}`,
           'aria-label': `Delete ${c.label}`,
           onclick: async () => {
             if (!(await confirmDialog(
                     `Delete ${noun}?`,
                     `\u201c${c.label}\u201d will be removed.`,
                     {confirmLabel: 'Delete', danger: true})))
               return;
             await del(`/api/categories/${c.id}`);
             await reloadGenres();
             draw();
           }
         },
           icon('delete'))];
    draw();
    modal({
      title: 'Categories',
      wide: true,
      id: 'categories-dialog',
      body: [
        h('p.help',
          'Built-in categories stay fixed; add your own categories and subcategories anywhere (including under built-ins). Synthesis can also create them for you.'),
        h('div.row-actions',
          h('button.btn.btn-sm.btn-ghost#new-category', {
            type: 'button',
            onclick: async () => {
              const label = await promptDialog(
                  'New category',
                  {label: 'Category name', confirmLabel: 'Create'});
              if (!label || !label.trim()) return;
              const {category} = await post(
                  '/api/categories', {kind: 'group', label: label.trim()});
              const sub = await promptDialog('First subcategory', {
                label: `Subcategory in \u201c${label.trim()}\u201d`,
                confirmLabel: 'Create'
              });
              if (sub && sub.trim())
                await post(
                    '/api/categories',
                    {kind: 'genre', parent_id: category.id, label: sub.trim()});
              await reloadGenres();
              draw();
            }
          },
            icon('add'), 'New category')),
        listEl
      ],
      actions: [{label: 'Done', kind: 'primary'}]
    });
  }

  async function starterLibrary() {
    const ok = await confirmDialog(
        'Generate starter library?',
        `3 samples for each of the ${builtinCount()} built-in subcategories (${
            builtinCount() *
            3} total), plus one sample set per category and an \u201cAll genres\u201d set. Takes a few minutes.`,
        {confirmLabel: 'Generate'});
    if (!ok) return;
    const {job} = await post('/api/samples/starter_library');
    trackJob(job, 'Generating starter library');
  }

  async function trackJob(job, label) {
    const draw = (j) => {
      const p = j.progress || {};
      const frac = p.total ? p.done / p.total : 0;
      mount(
          jobEl,
          h('div.job', {class: `job-${j.status}`},
            h('div.job-top',
              j.status === 'done'       ? icon('check_circle') :
                  j.status === 'failed' ? icon('error') :
                                          h('span.spinner'),
              h('strong', label), h('span.muted', j.message || j.status),
              h('span.grow'),
              p.total ? h('span.muted', `${p.done} / ${p.total}`) : null),
            progressBar(j.status === 'done' ? 1 : frac)));
    };
    draw(job);
    const final = await pollJob(job.id, (j) => {
      if (alive) {
        draw(j);
        if (j.progress && j.progress.done) loadSoon();
      }
    });
    if (!alive) return;
    if (final.status === 'done') {
      toast(`${label} \u2014 done`, 'good');
      setTimeout(() => mount(jobEl), 4000);
    } else {
      toast(
          `${label} failed: ${final.error || final.message || ''}`, 'bad',
          8000);
    }
    loadSets();
    load();
  }
  const loadSoon = debounce(() => {
    load();
    loadSets();
  }, 2500);

  // init
  await loadGenres();
  try {
    genres = await cached('/api/genres');
  } catch (e) {
    genres = {groups: []};
  }
  fillGroupSel();
  fillGenreSel();
  renderSets();
  await Promise.all([loadSets(), load()]);
  return () => {
    alive = false;
  };
}
