// "New import" drawer: Figma file/frame, Stitch screens or screenshots.
// Creating an import starts ingest + analysis on the server; the drawer then
// navigates to the import page (/p/:pid/imports/:iid), where the designer
// answers questions and reviews extractions before anything is merged.
// Also exports the Figma token / Stitch key widgets and `downscale`.
import {del, get, post, put} from '../components/api.js';
import {chip} from '../components/badges.js';
import {h, icon, mount, spinner} from '../components/dom.js';
import {renderMarkdown} from '../components/markdown.js';
import {navigate} from '../components/store.js';
import {drawer, segmented, toast} from '../components/ui.js';

const MAX_IMAGES = 20;
const MAX_SIDE = 2000;
const MAX_SCREENS = 20;
const VIEWPORT_HINTS =
    ['unknown', 'phone', 'tablet', 'desktop', 'smart_display', 'watch'];
const TABS = ['figma', 'stitch', 'screenshots'];
// Stitch enums (ROUND_EIGHT, FONT_INTER, DARK, MOBILE…) → "eight", "inter"…
/**
 * @param {*} v
 * @return {string}
 */
const pretty = (v) =>
    String(v || '')
        .replace(/^(ROUND|FONT|COLOR_VARIANT|DEVICE_TYPE)_/, '')
        .replace(/_/g, ' ')
        .toLowerCase();

/**
 * Formats an ISO timestamp into a short date string (e.g. "Oct 16").
 * @param {string|null|undefined} iso
 * @return {string}
 */
function shortDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString('en-US', {month: 'short', day: 'numeric'});
}

/**
 * Opens the "New import" drawer for project `pid`.
 * opts.tab: figma | stitch | screenshots (default: last used tab).
 * opts.origin: constitution | design | imports (where it was opened from).
 */
export function openImportDrawer(
    pid, {tab: initialTab, origin = 'imports'} = {}) {
  let tab = TABS.includes(initialTab) ?
      initialTab :
      (localStorage.getItem('a2uid.refs.tab') || 'figma');
  if (!TABS.includes(tab)) tab = 'figma';
  const images =
      [];  // [{data_url, name, caption, viewport_hint, width, height}]
  let titleAuto = '';  // last title auto-filled from a picked Figma/Stitch item

  function setAutoTitle(next) {
    const clean = String(next || '').trim();
    if (!clean) return;
    if (!title.value.trim() || title.value === titleAuto) {
      title.value = clean;
      titleAuto = clean;
    }
  }

  // ---- Figma tab
  let figmaProjects = null;  // GET /api/figma/projects response
  let figmaFilter = '';
  let selectedFigmaKey = '';
  let figmaData = null;  // POST /api/figma/frames response
  let figmaLoadedUrl = '';
  let figmaPageId = '';
  const figmaSelected = new Set();

  const figProjectsEl = h('div.ref-projects-wrap#figma-projects-wrap');
  const figPickEl = h('div.stitch-picker-wrap#figma-picker');
  const figUrl = h('input.input#ref-figma-url', {
    type: 'url',
    placeholder:
        'https://www.figma.com/design/<file>/\u2026 or /files/project/<id>',
    onkeydown: (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        loadFigmaFromInput();
      }
    },
    oninput: () => {
      if (figmaData && figUrl.value.trim() !== figmaLoadedUrl) {
        figmaData = null;
        figmaSelected.clear();
        drawFigmaPicker();
      }
    },
  });
  const figLoadBtn =
      h('button.btn.btn-ghost#figma-load',
        {type: 'button', onclick: () => loadFigmaFromInput()}, icon('download'),
        'Load frames');
  const {el: tokenEl, load: loadToken} =
      figmaTokenWidget({onSaved: () => loadFigmaProjects(true)});

  async function loadFigmaProjects(refresh = false) {
    mount(figProjectsEl, spinner('Loading your Figma files\u2026'));
    try {
      const q = refresh ? '?refresh=1' : '';
      figmaProjects = await get(`/api/figma/projects${q}`, {quiet: true});
    } catch (_) {
      figmaProjects = {files: [], sources: [], has_token: false};
    }
    drawFigmaProjects();
  }

  function drawFigmaProjects() {
    const files = (figmaProjects && figmaProjects.files) || [];
    const sources = (figmaProjects && figmaProjects.sources) || [];
    if (!files.length && !sources.length) {
      mount(
          figProjectsEl,
          h('div.ref-projects-empty#figma-projects-empty',
            icon('design_services'),
            h('div', h('strong', 'No saved Figma files or project folders yet'),
              h('div.muted.small',
                'Paste a Figma file link below (or a Figma project folder / team link like figma.com/files/project/\u2026) once \u2014 it stays in this list so you can pick it next time without typing a URL.'))));
      return;
    }
    const q = figmaFilter.trim().toLowerCase();
    const shown = q ?
        files.filter(
            (f) =>
                `${f.name || ''} ${f.project_name || ''} ${f.team_name || ''}`
                    .toLowerCase()
                    .includes(q)) :
        files;
    const searchIn = files.length > 4 ?
        h('input.input.input-sm.ref-proj-search#figma-project-search', {
          type: 'search',
          placeholder: 'Filter files\u2026',
          value: figmaFilter,
          oninput: (e) => {
            figmaFilter = e.target.value;
            drawFigmaProjects();
            const el = figProjectsEl.querySelector('#figma-project-search');
            if (el) {
              el.focus();
              el.setSelectionRange(el.value.length, el.value.length);
            }
          },
        }) :
        null;

    const rows = shown.map((f) => {
      const active = f.key === selectedFigmaKey;
      const meta = [
        f.team_name && f.project_name ?
            `${f.team_name} \u203a ${f.project_name}` :
            (f.project_name || f.team_name || ''),
        f.page_count ? `${f.page_count} page${f.page_count === 1 ? '' : 's'}` :
                       '',
        shortDate(f.last_modified) ? `updated ${shortDate(f.last_modified)}` :
                                     '',
      ].filter(Boolean).join(' \u00b7 ');
      return h(
          'div.ref-proj-row.figma-file-row', {
            className: active ? 'active' : '',
            dataset: {fileKey: f.key},
            tabindex: '0',
            role: 'button',
            'aria-pressed': active ? 'true' : 'false',
            onclick: (e) => {
              if (e.target.closest('a, button.icon-btn')) return;
              selectFigmaFile(f);
            },
            onkeydown: (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                selectFigmaFile(f);
              }
            },
          },
          h('span.ref-proj-icon.tint-7', icon('design_services')),
          h('div.ref-proj-main', h('div.ref-proj-title', f.name || f.key),
            meta ? h('div.ref-proj-meta.muted.small', meta) : null),
          h('div.ref-proj-chips',
            f.import_count ? chip('imported', 'chip-quiet') : null,
            active ? chip('selected', 'tint-7') : null,
            h('a.icon-btn', {
              href: f.url,
              target: '_blank',
              rel: 'noopener',
              title: 'Open in Figma',
            },
              icon('open_in_new'))));
    });

    mount(
        figProjectsEl,
        h('div.ref-projects-box#figma-projects-list',
          h('div.ref-projects-head', icon('folder_special'),
            h('strong', 'Your Figma files'),
            chip(`${files.length}`, 'chip-quiet'), h('span.grow'), searchIn,
            h('button.btn.btn-quiet.btn-sm#figma-projects-refresh', {
              type: 'button',
              title: 'Refresh files from Figma',
              onclick: () => loadFigmaProjects(true),
            },
              icon('refresh'), 'Refresh')),
          sources.length ? h('div.ref-sources-bar',
                             h('span.muted.small', 'Connected folders:'),
                             sources.map(
                                 (s) => h(
                                     'span.chip.chip-quiet.ref-source-chip',
                                     `${s.name} (${s.file_count || 0})`,
                                     h('button.chip-x', {
                                       type: 'button',
                                       title: `Disconnect ${s.name}`,
                                       onclick: async () => {
                                         figmaProjects =
                                             await del(
                                                 `/api/figma/projects/${
                                                     encodeURIComponent(s.id)}`,
                                                 {quiet: true})
                                                 .catch(() => figmaProjects);
                                         loadFigmaProjects(false);
                                       },
                                     },
                                       '\u00d7')))) :
                           null,
          shown.length ? h('div.ref-projects-rows', rows) :
                         h('div.ref-projects-none.muted.small',
                           'No matching Figma files.')));
  }

  async function selectFigmaFile(f) {
    selectedFigmaKey = f.key;
    figUrl.value = f.url || `https://www.figma.com/design/${f.key}`;
    setAutoTitle(f.name);
    drawFigmaProjects();
    await loadFigmaFrames(figUrl.value, f.name);
  }

  async function loadFigmaFromInput() {
    const raw = figUrl.value.trim();
    if (!raw) {
      toast('Choose a Figma file above or paste a link first.', 'warn');
      figUrl.focus();
      return;
    }
    figLoadBtn.disabled = true;
    figLoadBtn.classList.add('busy');
    try {
      // Check if it is a Figma team or project folder URL.
      if (/figma\.com\/files\/.*\/(team|project)\/\d+/i.test(raw) ||
          /^(team|project):\d+$/i.test(raw)) {
        mount(figPickEl, spinner('Loading files from Figma folder\u2026'));
        const res = await post('/api/figma/projects', {url: raw});
        figmaProjects = res;
        figUrl.value = '';
        mount(figPickEl);
        drawFigmaProjects();
        const added = res.added || {};
        toast(
            `Loaded ${added.file_count || 0} file${
                added.file_count === 1 ? '' : 's'} from ${
                added.name || 'Figma'}. Pick one from the list!`,
            'good');
        return;
      }
      await loadFigmaFrames(raw);
      loadFigmaProjects(false);
    } catch (e) {
      mount(
          figPickEl,
          h('div.callout.callout-bad#figma-error', icon('error'),
            h('span', e.message || 'Could not load from Figma.')));
    } finally {
      figLoadBtn.disabled = false;
      figLoadBtn.classList.remove('busy');
    }
  }

  async function loadFigmaFrames(url, fallbackName = '') {
    figLoadBtn.disabled = true;
    figLoadBtn.classList.add('busy');
    mount(figPickEl, spinner('Loading pages and frames from Figma\u2026'));
    try {
      figmaData = await post('/api/figma/frames', {url}, {quiet: true});
      figmaLoadedUrl = url;
      const f = (figmaData && figmaData.file) || {};
      if (f.key) selectedFigmaKey = f.key;
      setAutoTitle(f.name || fallbackName);
      const pages = (figmaData && figmaData.pages) || [];
      const realPages = pages.filter((p) => !p.is_divider);
      const pre = new Set((figmaData && figmaData.preselect) || []);
      figmaSelected.clear();
      // Find if preselected node IDs match a page or frame on a page.
      let matchedPage = realPages.find(
          (p) =>
              pre.has(p.id) || (p.frames || []).some((fr) => pre.has(fr.id)));
      if (!matchedPage) {
        matchedPage = realPages.find((p) => (p.frames || []).length > 0) ||
            realPages[0] || pages[0];
      }
      figmaPageId = matchedPage ? matchedPage.id : '';
      const pageFrames = (matchedPage && matchedPage.frames) || [];
      const preFrames = pageFrames.filter((fr) => pre.has(fr.id));
      (preFrames.length ? preFrames : pageFrames)
          .slice(0, MAX_SCREENS)
          .forEach((fr) => figmaSelected.add(fr.id));
      drawFigmaProjects();
      drawFigmaPicker();
    } catch (e) {
      figmaData = null;
      figmaSelected.clear();
      mount(
          figPickEl,
          h('div.callout.callout-bad#figma-error', icon('error'),
            h('span', e.message || 'Could not load pages/frames from Figma.')));
    } finally {
      figLoadBtn.disabled = false;
      figLoadBtn.classList.remove('busy');
    }
  }

  function currentFigmaNodeIds() {
    if (!figmaData) return [];
    const pages = figmaData.pages || [];
    const page = pages.find((p) => p.id === figmaPageId) || pages[0];
    if (!page) return [];
    const pageFrames = page.frames || [];
    const picked =
        pageFrames.map((f) => f.id).filter((id) => figmaSelected.has(id));
    if (picked.length) return picked;
    return page.id ? [page.id] : [];
  }

  function drawFigmaPicker() {
    if (!figmaData) {
      mount(figPickEl);
      return;
    }
    const f = figmaData.file || {};
    const pages = (figmaData.pages || []).filter((p) => !p.is_divider);
    const curPage = pages.find((p) => p.id === figmaPageId) || pages[0] ||
        (figmaData.pages || [])[0];
    const frames = (curPage && curPage.frames) || [];
    const count = h('span.stitch-count#figma-count');
    const all = h('input#figma-select-all', {type: 'checkbox'});
    const boxes = [];
    const sync = () => {
      const n = figmaSelected.size;
      count.textContent = `${n} selected (max ${MAX_SCREENS})`;
      count.classList.toggle('at-max', n >= MAX_SCREENS);
      const cap = Math.min(frames.length, MAX_SCREENS);
      all.checked = n > 0 && n >= cap;
      all.indeterminate = n > 0 && n < cap;
      boxes.forEach(({cb, row, id}) => {
        cb.checked = figmaSelected.has(id);
        cb.disabled = !cb.checked && n >= MAX_SCREENS;
        row.classList.toggle('on', cb.checked);
        row.classList.toggle('off', cb.disabled);
      });
    };
    all.addEventListener('change', () => {
      figmaSelected.clear();
      if (all.checked) {
        frames.slice(0, MAX_SCREENS).forEach((fr) => figmaSelected.add(fr.id));
      }
      sync();
    });
    const pageSel = pages.length > 1 ?
        h('select.input.input-sm#figma-page-select', {
          'aria-label': 'Figma page',
          onchange: (e) => {
            figmaPageId = e.target.value;
            const nextPage = pages.find((p) => p.id === figmaPageId);
            figmaSelected.clear();
            ((nextPage && nextPage.frames) || [])
                .slice(0, MAX_SCREENS)
                .forEach((fr) => figmaSelected.add(fr.id));
            if (f.name && nextPage && nextPage.name) {
              setAutoTitle(`${f.name} \u2014 ${nextPage.name}`);
            }
            drawFigmaPicker();
          },
        },
          pages.map(
              (p) =>
                  h('option',
                    {value: p.id, selected: p.id === (curPage && curPage.id)},
                    `${p.name} (${(p.frames || []).length})`))) :
        null;

    const rows = frames.map((fr) => {
      const cb = h('input.figma-cb', {
        type: 'checkbox',
        'aria-label': `Include ${fr.name || fr.id}`,
        onchange: () => {
          if (cb.checked) {
            if (figmaSelected.size >= MAX_SCREENS) {
              cb.checked = false;
              return;
            }
            figmaSelected.add(fr.id);
          } else {
            figmaSelected.delete(fr.id);
          }
          sync();
        },
      });
      const row =
          h('label.stitch-screen.figma-frame-row', {dataset: {id: fr.id}}, cb,
            h('div.stitch-screen-main',
              h('div.stitch-screen-title', fr.name || fr.id),
              h('div.stitch-screen-meta.muted.small',
                [
                  pretty(fr.type || 'frame'),
                  fr.width && fr.height ? `${fr.width}\u00d7${fr.height}` : '',
                ].filter(Boolean)
                    .join(' \u00b7 '))),
            h('div.stitch-screen-chips',
              chip(fr.viewport_hint || 'unknown', 'chip-quiet')));
      boxes.push({cb, row, id: fr.id});
      return row;
    });

    mount(
        figPickEl,
        h('div.stitch-picker#figma-frame-picker',
          h('div.stitch-proj',
            h('div.stitch-proj-title', icon('design_services'),
              f.url ? h('a', {href: f.url, target: '_blank', rel: 'noopener'},
                        f.name || f.key || 'Figma file') :
                      h('strong', f.name || f.key || 'Figma file'),
              h('span.muted.small',
                ` ${pages.length} page${pages.length === 1 ? '' : 's'}`),
              h('span.grow'),
              pageSel ? h('label.row-inline.small', h('span.muted', 'Page:'),
                          pageSel) :
                        null)),
          frames.length ?
              [
                h('div.stitch-toolbar', h('label.check', all, 'Select all'),
                  h('span.grow'), count),
                h('div.stitch-screens', rows),
              ] :
              h('p.muted.small.pad-sm',
                curPage ?
                    `Page \u201c${
                        curPage
                            .name}\u201d has no top-level frames or sections \u2014 importing will scan the page directly.` :
                    'No frames found in this file.')));
    sync();
  }

  const figPane = h(
      'div.ref-pane#ref-pane-figma', figProjectsEl, figPickEl,
      h('label.field.ref-url-fallback',
        h('span.field-label',
          'Or paste a Figma file, frame, project folder or team link'),
        h('div.row-inline', figUrl, figLoadBtn),
        h('span.help',
          'Pick one of your files above to browse its pages and frames, or paste a Figma file/folder link to add it to your list.')),
      tokenEl);

  // ---- Stitch tab
  let stitchProjects = null;  // GET /api/stitch/projects response
  let stitchFilter = '';
  let selectedStitchId = '';
  const stitchProjectsEl = h('div.ref-projects-wrap#stitch-projects-wrap');
  const stitchUrl = h('input.input#ref-stitch-url', {
    type: 'url',
    placeholder: 'https://stitch.withgoogle.com/projects/\u2026',
    onkeydown: (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        loadScreens();
      }
    },
    oninput: () => {
      // A different project invalidates the loaded picker.
      if (stitch && stitchUrl.value.trim() !== stitchLoadedUrl) {
        stitch = null;
        selected.clear();
        drawPicker();
      }
    },
  });
  const {el: keyEl, load: loadKey} =
      stitchKeyWidget({onSaved: () => loadStitchProjects()});
  let stitch = null;  // POST /api/stitch/screens response
  let stitchLoadedUrl = '';
  const selected = new Set();
  const loadBtn =
      h('button.btn.btn-ghost#stitch-load',
        {type: 'button', onclick: () => loadScreens()}, icon('download'),
        'Load screens');
  const pickEl = h('div.stitch-picker-wrap#stitch-picker');

  async function loadStitchProjects() {
    mount(stitchProjectsEl, spinner('Loading your Stitch projects\u2026'));
    try {
      stitchProjects = await get('/api/stitch/projects', {quiet: true});
    } catch (_) {
      stitchProjects = {projects: [], has_key: false};
    }
    drawStitchProjects();
  }

  function drawStitchProjects() {
    const projs = (stitchProjects && stitchProjects.projects) || [];
    const hasKey = Boolean(stitchProjects && stitchProjects.has_key);
    const err = stitchProjects && stitchProjects.error;
    if (!projs.length) {
      mount(
          stitchProjectsEl,
          h('div.ref-projects-empty#stitch-projects-empty',
            icon('auto_awesome_mosaic'),
            h('div',
              h('strong',
                hasKey ? 'No Stitch projects found yet' :
                         'Add your Stitch API key to browse your projects'),
              h('div.muted.small',
                err ?
                    err :
                    'Once your Stitch API key is saved, your owned and shared Stitch projects appear here so you can pick one with a click.'))));
      return;
    }
    const q = stitchFilter.trim().toLowerCase();
    const shown = q ?
        projs.filter((p) => (p.title || '').toLowerCase().includes(q)) :
        projs;
    const searchIn = projs.length > 4 ?
        h('input.input.input-sm.ref-proj-search#stitch-project-search', {
          type: 'search',
          placeholder: 'Filter projects\u2026',
          value: stitchFilter,
          oninput: (e) => {
            stitchFilter = e.target.value;
            drawStitchProjects();
            const el = stitchProjectsEl.querySelector('#stitch-project-search');
            if (el) {
              el.focus();
              el.setSelectionRange(el.value.length, el.value.length);
            }
          },
        }) :
        null;

    const rows = shown.map((p) => {
      const active = p.id === selectedStitchId;
      const meta = [
        p.device_type ? pretty(p.device_type) : '',
        p.screen_count != null ?
            `${p.screen_count} screen${p.screen_count === 1 ? '' : 's'}` :
            '',
        p.color_mode ? p.color_mode : '',
        shortDate(p.update_time) ? `updated ${shortDate(p.update_time)}` : '',
      ].filter(Boolean).join(' \u00b7 ');
      const swatch = p.primary_color ?
          h('span.ref-proj-swatch', {
            style: `background:${p.primary_color}`,
            title: `Theme primary ${p.primary_color}`,
          }) :
          h('span.ref-proj-icon.tint-6', icon('auto_awesome_mosaic'));
      return h(
          'div.ref-proj-row.stitch-proj-row', {
            className: active ? 'active' : '',
            dataset: {projectId: p.id},
            tabindex: '0',
            role: 'button',
            'aria-pressed': active ? 'true' : 'false',
            onclick: (e) => {
              if (e.target.closest('a')) return;
              selectStitchProject(p);
            },
            onkeydown: (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                selectStitchProject(p);
              }
            },
          },
          swatch,
          h('div.ref-proj-main',
            h('div.ref-proj-title', p.title || `Project ${p.id}`),
            meta ? h('div.ref-proj-meta.muted.small', meta) : null),
          h('div.ref-proj-chips',
            p.source === 'shared' ? chip('shared', 'chip-quiet') : null,
            p.import_count ? chip('imported', 'chip-quiet') : null,
            active ? chip('selected', 'tint-6') : null,
            p.url ? h('a.icon-btn', {
              href: p.url,
              target: '_blank',
              rel: 'noopener',
              title: 'Open in Stitch',
            },
                      icon('open_in_new')) :
                    null));
    });

    mount(
        stitchProjectsEl,
        h('div.ref-projects-box#stitch-projects-list',
          h('div.ref-projects-head', icon('auto_awesome_mosaic'),
            h('strong', 'Your Stitch projects'),
            chip(`${projs.length}`, 'chip-quiet'), h('span.grow'), searchIn,
            h('button.btn.btn-quiet.btn-sm#stitch-projects-refresh', {
              type: 'button',
              title: 'Refresh projects from Stitch',
              onclick: () => loadStitchProjects(),
            },
              icon('refresh'), 'Refresh')),
          shown.length ? h('div.ref-projects-rows', rows) :
                         h('div.ref-projects-none.muted.small',
                           'No matching Stitch projects.')));
  }

  async function selectStitchProject(p) {
    selectedStitchId = p.id;
    stitchUrl.value = p.url || `https://stitch.withgoogle.com/projects/${p.id}`;
    setAutoTitle(p.title);
    drawStitchProjects();
    await loadScreens();
  }

  async function loadScreens() {
    const url = stitchUrl.value.trim();
    if (!url) {
      toast('Choose a Stitch project above or paste a link first.', 'warn');
      stitchUrl.focus();
      return;
    }
    loadBtn.disabled = true;
    loadBtn.classList.add('busy');
    mount(pickEl, spinner('Loading screens from Stitch\u2026'));
    try {
      stitch = await post('/api/stitch/screens', {url}, {quiet: true});
      stitchLoadedUrl = url;
      if (stitch.project && stitch.project.id) {
        selectedStitchId = stitch.project.id;
        drawStitchProjects();
      }
      selected.clear();
      const screens = stitch.screens || [];
      const ids = new Set(screens.map((s) => s.id));
      const pre = (stitch.preselect || []).filter((id) => ids.has(id));
      (pre.length ? pre : screens.map((s) => s.id))
          .slice(0, MAX_SCREENS)
          .forEach((id) => selected.add(id));
      const pt = (stitch.project && stitch.project.title) || '';
      setAutoTitle(pt);
      drawPicker();
    } catch (e) {
      stitch = null;
      selected.clear();
      mount(
          pickEl,
          h('div.callout.callout-bad#stitch-error', icon('error'),
            h('span', e.message || 'Could not load the Stitch project.')));
    } finally {
      loadBtn.disabled = false;
      loadBtn.classList.remove('busy');
    }
  }

  function drawPicker() {
    if (!stitch) {
      mount(pickEl);
      return;
    }
    const screens = stitch.screens || [];
    const p = stitch.project || {};
    const systems = stitch.design_systems || [];
    const count = h('span.stitch-count#stitch-count');
    const all = h('input#stitch-select-all', {type: 'checkbox'});
    const boxes = [];
    const sync = () => {
      const n = selected.size;
      count.textContent = `${n} selected (max ${MAX_SCREENS})`;
      count.classList.toggle('at-max', n >= MAX_SCREENS);
      const cap = Math.min(screens.length, MAX_SCREENS);
      all.checked = n > 0 && n >= cap;
      all.indeterminate = n > 0 && n < cap;
      boxes.forEach(({cb, row, id}) => {
        cb.checked = selected.has(id);
        cb.disabled = !cb.checked && n >= MAX_SCREENS;
        row.classList.toggle('on', cb.checked);
        row.classList.toggle('off', cb.disabled);
      });
    };
    all.addEventListener('change', () => {
      selected.clear();
      if (all.checked)
        screens.slice(0, MAX_SCREENS).forEach((s) => selected.add(s.id));
      sync();
    });
    const rows = screens.map((s) => {
      const cb = h('input.stitch-cb', {
        type: 'checkbox',
        'aria-label': `Include ${s.title || s.id}`,
        onchange: () => {
          if (cb.checked) {
            if (selected.size >= MAX_SCREENS) {
              cb.checked = false;
              return;
            }
            selected.add(s.id);
          } else {
            selected.delete(s.id);
          }
          sync();
        }
      });
      const row = h(
          'label.stitch-screen', {dataset: {id: s.id}}, cb,
          h('div.stitch-screen-main',
            h('div.stitch-screen-title',
              s.title || h('span.muted', 'Untitled screen')),
            h('div.stitch-screen-meta.muted.small',
              [
                s.device_type ? pretty(s.device_type) : '',
                s.width && s.height ? `${s.width}\u00d7${s.height}` : ''
              ].filter(Boolean)
                  .join(' \u00b7 '))),
          h('div.stitch-screen-chips',
            !s.has_screenshot && s.has_html ? h('span.chip.chip-quiet', {
              title:
                  'No screenshot; will be rendered from the screen\u2019s HTML'
            },
                                                'HTML only') :
                                              null,
            !s.has_screenshot && !s.has_html ?
                h('span.chip.c-bad',
                  {title: 'This screen has no image or HTML'}, 'no image') :
                null,
            chip(s.viewport_hint || 'unknown', 'chip-quiet')));
      boxes.push({cb, row, id: s.id});
      return row;
    });
    mount(
        pickEl,
        h('div.stitch-picker',
          h('div.stitch-proj',
            h('div.stitch-proj-title', icon('auto_awesome_mosaic'),
              p.url ? h('a', {href: p.url, target: '_blank', rel: 'noopener'},
                        p.title || p.id || 'Stitch project') :
                      h('strong', p.title || p.id || 'Stitch project'),
              h('span.muted.small',
                ` ${screens.length} screen${screens.length === 1 ? '' : 's'}`)),
            systems.length ?
                h('div.chip-row.stitch-ds',
                  h('span.muted.small', 'Design system'),
                  systems.map(
                      (d) => chip(
                          d.display_name || d.name || 'Design system',
                          'tint-6'))) :
                null),
          screens.length ?
              [
                h('div.stitch-toolbar', h('label.check', all, 'Select all'),
                  h('span.grow'), count),
                h('div.stitch-screens', rows),
              ] :
              h('p.muted.small', 'This project has no screens yet.')));
    sync();
  }

  const stitchPane = h(
      'div.ref-pane#ref-pane-stitch', stitchProjectsEl, pickEl,
      h('label.field.ref-url-fallback',
        h('span.field-label', 'Or paste a Stitch project link or ID'),
        h('div.row-inline', stitchUrl, loadBtn),
        h('span.help',
          'Pick a project above to load its screens (up to 20), or paste a link directly. Its design system (theme) is imported too.')),
      keyEl);

  // ---- Screenshots tab
  const fileIn = h('input#ref-files', {
    type: 'file',
    accept: 'image/*',
    multiple: true,
    onchange: () => {
      addFiles([...fileIn.files]);
      fileIn.value = '';
    }
  });
  const listEl = h('div.shot-list#ref-shot-list');
  const drop = h(
      'div.drop-zone#ref-drop', {
        tabindex: '0',
        ondragover: (e) => {
          e.preventDefault();
          drop.classList.add('over');
        },
        ondragleave: () => drop.classList.remove('over'),
        ondrop: (e) => {
          e.preventDefault();
          drop.classList.remove('over');
          addFiles([...(e.dataTransfer.files || [])]);
        },
        onclick: (e) => {
          if (e.target === drop || e.target.closest('.drop-hint'))
            fileIn.click();
        },
      },
      h('div.drop-hint', icon('add_photo_alternate'),
        h('div', h('strong', 'Drop screenshots here'),
          h('div.muted.small',
            `or click to choose, or paste from the clipboard (\u2318V). Up to ${
                MAX_IMAGES} images; large images are downscaled to ${
                MAX_SIDE}px.`))),
      fileIn);
  const shotPane = h('div.ref-pane#ref-pane-shots', drop, listEl);

  function drawShots() {
    mount(
        listEl,
        images.map(
            (im, i) =>
                h('div.shot-row',
                  h('img.shot-thumb', {src: im.data_url, alt: ''}),
                  h('div.shot-fields', h('input.input.input-sm.shot-caption', {
                      value: im.caption,
                      placeholder: 'Caption \u2014 what this screen shows',
                      'aria-label': `Caption for ${im.name}`,
                      oninput: (e) => {
                        im.caption = e.target.value;
                      }
                    }),
                    h('div.row-inline',
                      h('select.input.input-sm.shot-vp', {
                        'aria-label': 'Viewport hint',
                        onchange: (e) => {
                          im.viewport_hint = e.target.value;
                        }
                      },
                        VIEWPORT_HINTS.map(
                            (v) =>
                                h('option',
                                  {value: v, selected: v === im.viewport_hint},
                                  v))),
                      h('span.muted.small.shot-name',
                        `${im.name} \u00b7 ${im.width}\u00d7${im.height}`))),
                  h('button.icon-btn', {
                    type: 'button',
                    'aria-label': `Remove ${im.name}`,
                    onclick: () => {
                      images.splice(i, 1);
                      drawShots();
                    }
                  },
                    icon('close')))),
        images.length ? h('p.help', `${images.length} / ${MAX_IMAGES} images`) :
                        null);
  }

  async function addFiles(files) {
    const imgs = files.filter((f) => f && /^image\//.test(f.type));
    if (!imgs.length) return;
    const room = MAX_IMAGES - images.length;
    if (room <= 0) {
      toast(`At most ${MAX_IMAGES} images.`, 'warn');
      return;
    }
    if (imgs.length > room)
      toast(
          `Only the first ${room} images were added (max ${MAX_IMAGES}).`,
          'warn');
    for (const f of imgs.slice(0, room)) {
      try {
        const im = await downscale(f);
        images.push({
          ...im,
          name: f.name || `pasted-${images.length + 1}.png`,
          caption: '',
          // 'unknown' lets the server guess from the pixel size.
          viewport_hint: 'unknown',
        });
        drawShots();
      } catch (e) {
        toast(`Could not read ${f.name || 'image'}: ${e.message}`, 'bad');
      }
    }
  }

  const onPaste = (e) => {
    const files = [...((e.clipboardData && e.clipboardData.items) || [])]
                      .filter((it) => it.kind === 'file')
                      .map((it) => it.getAsFile())
                      .filter(Boolean);
    if (!files.length) return;
    e.preventDefault();
    if (tab !== 'screenshots') {
      tab = 'screenshots';
      seg.set(tab);
      syncTab();
    }
    addFiles(files);
  };
  document.addEventListener('paste', onPaste);

  // ---- Common
  const title =
      h('input.input#ref-title', {placeholder: 'e.g. Checkout redesign v3'});
  const notes = h('textarea.input#ref-notes', {
    rows: 3,
    placeholder:
        'Optional \u2014 what to learn from these, e.g. \u201cThe dashboard cards are the pattern to copy; ignore the onboarding screens.\u201d'
  });
  let importDataOn = true;
  let setDefaultDataOn = true;
  const setDefaultCb = h('input#ref-set-default-data', {
    type: 'checkbox',
    checked: true,
    onchange: (e) => {
      setDefaultDataOn = e.target.checked;
    },
  });
  const importDataCb = h('input#ref-import-data', {
    type: 'checkbox',
    checked: true,
    onchange: (e) => {
      importDataOn = e.target.checked;
      setDefaultCb.disabled = !importDataOn;
      if (!importDataOn) {
        setDefaultCb.checked = false;
        setDefaultDataOn = false;
      } else {
        setDefaultCb.checked = true;
        setDefaultDataOn = true;
      }
    },
  });
  const dataOptsBox = h(
      'div.field.ref-data-opts#ref-project-data-opts',
      h('span.field-label', icon('dataset'), ' Project data for runs'),
      h('label.check', importDataCb,
        h('span',
          'Import project-related data samples from frames/screens (domain records, scenarios & states)')),
      h('label.check', setDefaultCb,
        h('span',
          'Set imported data as the default data source for runs in this project')));
  const guide = guidelinesPanel();
  const seg = segmented(
      [
        {
          value: 'figma',
          label: 'Figma',
          icon: 'design_services',
          id: 'ref-tab-figma'
        },
        {
          value: 'stitch',
          label: 'Stitch',
          icon: 'auto_awesome_mosaic',
          id: 'ref-tab-stitch'
        },
        {
          value: 'screenshots',
          label: 'Screenshots',
          icon: 'image',
          id: 'ref-tab-shots'
        },
      ],
      tab, (v) => {
        tab = v;
        localStorage.setItem('a2uid.refs.tab', v);
        syncTab();
      }, {id: 'ref-tabs'});
  function syncTab() {
    figPane.hidden = tab !== 'figma';
    stitchPane.hidden = tab !== 'stitch';
    shotPane.hidden = tab !== 'screenshots';
    dataOptsBox.hidden = tab === 'screenshots';
    guide.focusSection(tab === 'stitch' ? 'stitch' : null);
  }
  const submitBtn =
      h('button.btn.btn-primary#ref-submit', {type: 'button', onclick: submit},
        icon('move_to_inbox'), 'Import & analyze');
  const onImports = location.pathname.startsWith(`/p/${pid}/imports`);
  const d = drawer({
    title: 'New import',
    wide: true,
    id: 'reference-drawer',
    actions: onImports ?
        null :
        h('a.btn.btn-ghost.btn-sm#all-imports-link',
          {href: `/p/${pid}/imports`, title: 'All imports in this project'},
          icon('move_to_inbox'), 'All imports'),
    onClose: () => document.removeEventListener('paste', onPaste),
    body: h(
        'div.ref-import', h('div.ref-tabs-row', seg), figPane, stitchPane,
        shotPane, dataOptsBox,
        h('label.field', h('span.field-label', 'Title'), title),
        h('label.field', h('span.field-label', 'Notes for the analysis'),
          notes),
        h('div.callout.import-flow-note', icon('route'),
          h('span',
            'Gemini first splits the material into components, templates, token reference and project data samples, and asks you about anything unclear. Nothing reaches tokens, components, data or the constitution until you apply a merge plan.')),
        guide,
        h('div.drawer-actions', submitBtn,
          h('button.btn.btn-ghost', {type: 'button', onclick: () => d.close()},
            'Cancel'))),
  });
  syncTab();
  drawShots();
  loadToken();
  loadKey();
  loadFigmaProjects();
  loadStitchProjects();

  async function submit() {
    let body;
    if (tab === 'figma') {
      const url = figUrl.value.trim();
      if (!url) {
        toast('Choose a Figma file above or paste a figma.com link.', 'warn');
        figUrl.focus();
        return;
      }
      if (!/^https:\/\/([\w-]+\.)?figma\.com\//i.test(url) &&
          !/^[A-Za-z0-9]{18,32}$/.test(url)) {
        toast('Choose a Figma file or paste a figma.com design link.', 'warn');
        figUrl.focus();
        return;
      }
      body = {kind: 'figma', url};
      if (figmaData && url === figmaLoadedUrl) {
        const nids = currentFigmaNodeIds();
        if (!nids.length) {
          toast('Select at least one frame or section.', 'warn');
          return;
        }
        body.node_ids = nids;
      }
    } else if (tab === 'stitch') {
      const url = stitchUrl.value.trim();
      if (!url) {
        toast('Choose a Stitch project above or paste a link.', 'warn');
        stitchUrl.focus();
        return;
      }
      body = {kind: 'stitch', url};
      if (stitch && url === stitchLoadedUrl) {
        if (!selected.size) {
          toast('Select at least one screen.', 'warn');
          return;
        }
        // Keep the project's screen order.
        body.screen_ids = (stitch.screens || [])
                              .map((s) => s.id)
                              .filter((id) => selected.has(id));
      }
    } else {
      if (!images.length) {
        toast('Add at least one screenshot.', 'warn');
        return;
      }
      body = {
        kind: 'screenshots',
        images: images.map((im) => ({
                             data_url: im.data_url,
                             caption: im.caption.trim() || undefined,
                             viewport_hint: im.viewport_hint,
                             name: im.name
                           }))
      };
    }
    if (title.value.trim()) body.title = title.value.trim();
    if (notes.value.trim()) body.notes = notes.value.trim();
    if (tab === 'figma' || tab === 'stitch') {
      body.import_data = Boolean(importDataOn);
      body.set_default_data_source = Boolean(importDataOn && setDefaultDataOn);
    }
    body.origin = origin;
    submitBtn.disabled = true;
    submitBtn.classList.add('busy');
    try {
      const res = await post(`/api/projects/${pid}/references`, body);
      const imp = res.import || res.reference;
      d.close();
      toast(
          'Import started \u2014 analysis runs as soon as it\u2019s ingested.',
          'good');
      navigate(`/p/${pid}/imports/${imp.id}`);
    } finally {
      submitBtn.disabled = false;
      submitBtn.classList.remove('busy');
    }
  }
}

/**
 * Figma personal-access-token status / form (GET/PUT /api/me/figma).
 * Returns {el, load}; call load() to fetch the status.
 */
/** @return {*} */
export function figmaTokenWidget({onSaved} = {}) {
  const tokenEl =
      h('div.figma-token#figma-token', spinner('Checking Figma token\u2026'));
  async function loadToken() {
    let st = null;
    try {
      st = await get('/api/me/figma', {quiet: true});
    } catch (_) {
      st = null;
    }
    drawToken(st);
  }
  function drawToken(st, forceEdit = false) {
    if (st && st.has_token && !forceEdit) {
      mount(
          tokenEl, icon('key'),
          h('span',
            st.source === 'server' ? 'Using the server\u2019s Figma token.' :
                                     'Using your saved Figma token.'),
          h('button.link-btn',
            {type: 'button', onclick: () => drawToken(st, true)},
            st.source === 'server' ? 'Use my own' : 'Replace'),
          st.source === 'user' ? h('button.link-btn', {
            type: 'button',
            onclick: async () => {
              await put('/api/me/figma', {token: ''});
              toast('Figma token removed', 'good');
              await loadToken();
              if (onSaved) onSaved();
            }
          },
                                   'Remove') :
                                 null);
      return;
    }
    const tok = h('input.input#figma-token-input', {
      type: 'password',
      placeholder: 'figd_\u2026 personal access token',
      autocomplete: 'off'
    });
    mount(
        tokenEl,
        h('div.figma-token-form',
          h('label.field', h('span.field-label', 'Figma personal access token'),
            h('div.row-inline', tok,
              h('button.btn.btn-ghost.btn-sm#figma-token-save', {
                type: 'button',
                onclick: async () => {
                  if (!tok.value.trim()) return;
                  await put('/api/me/figma', {token: tok.value.trim()});
                  toast('Figma token saved', 'good');
                  await loadToken();
                  if (onSaved) onSaved();
                }
              },
                'Save token'),
              st && st.has_token ?
                  h('button.btn.btn-quiet.btn-sm',
                    {type: 'button', onclick: () => drawToken(st)}, 'Cancel') :
                  null),
            h('span.help',
              'Figma \u203a Settings \u203a Security \u203a Personal access tokens (read-only file content scope). Stored for your account; never shown again.'))));
  }
  return {el: tokenEl, load: loadToken};
}

/**
 * Stitch API key status / form (GET/PUT /api/me/stitch). Returns {el, load};
 * call load() to fetch the status.
 */
/** @return {*} */
export function stitchKeyWidget({onSaved} = {}) {
  const keyEl =
      h('div.figma-token#stitch-key', spinner('Checking Stitch API key\u2026'));
  async function loadKey() {
    let st = null;
    try {
      st = await get('/api/me/stitch', {quiet: true});
    } catch (_) {
      st = null;
    }
    drawKey(st);
  }
  function drawKey(st, forceEdit = false) {
    if (st && st.has_key && !forceEdit) {
      mount(
          keyEl, icon('key'),
          h('span',
            st.source === 'server' ? 'Using the server\u2019s Stitch API key.' :
                                     'Using your saved Stitch API key.'),
          h('button.link-btn#stitch-key-edit',
            {type: 'button', onclick: () => drawKey(st, true)},
            st.source === 'server' ? 'Use my own' : 'Replace'),
          st.source === 'user' ? h('button.link-btn', {
            type: 'button',
            onclick: async () => {
              await put('/api/me/stitch', {key: ''});
              toast('Stitch API key removed', 'good');
              await loadKey();
              if (onSaved) onSaved();
            }
          },
                                   'Remove') :
                                 null);
      return;
    }
    const key = h(
        'input.input#stitch-key-input',
        {type: 'password', placeholder: 'Stitch API key', autocomplete: 'off'});
    mount(
        keyEl,
        h('div.figma-token-form',
          h('label.field', h('span.field-label', 'Stitch API key'),
            h('div.row-inline', key,
              h('button.btn.btn-ghost.btn-sm#stitch-key-save', {
                type: 'button',
                onclick: async () => {
                  if (!key.value.trim()) return;
                  await put('/api/me/stitch', {key: key.value.trim()});
                  toast('Stitch API key saved', 'good');
                  await loadKey();
                  if (onSaved) onSaved();
                }
              },
                'Save key'),
              st && st.has_key ?
                  h('button.btn.btn-quiet.btn-sm',
                    {type: 'button', onclick: () => drawKey(st)}, 'Cancel') :
                  null),
            h('span.help',
              'Create a key in Stitch \u203a Settings \u203a API keys. Stored for your account; never shown again.'))));
  }
  return {el: keyEl, load: loadKey};
}

/** @return {*} */
function guidelinesPanel() {
  const body = h('div.ref-guide-body');
  let loaded = false;
  let focus = null;  // section keyword to scroll to / highlight (e.g. 'stitch')
  const hint = h('span.muted.small.ref-guide-hint');
  const applyFocus = () => {
    body.querySelectorAll('.guide-focus')
        .forEach((el) => el.classList.remove('guide-focus'));
    if (!focus || !det.open) return;
    const re = new RegExp(`^(\\d+\\.\\s*)?${focus}\\b`, 'i');
    const head = [...body.querySelectorAll('h2')].find(
        (el) => re.test(el.textContent.trim()));
    if (!head) return;
    // Highlight the heading and everything up to the next h2.
    for (let el = head; el && (el === head || el.tagName !== 'H2');
         el = el.nextElementSibling)
      el.classList.add('guide-focus');
    // .ref-guide-body is position:relative, so it is the heading's
    // offsetParent.
    body.scrollTop = Math.max(0, head.offsetTop - 4);
  };
  const det =
      h('details.ref-guide#ref-guidelines', {
        ontoggle: async () => {
          if (!det.open) return;
          if (loaded) {
            applyFocus();
            return;
          }
          loaded = true;
          mount(body, spinner('Loading\u2026'));
          try {
            const res = await fetch(
                '/docs/DESIGN_REFERENCE_GUIDELINES.md',
                {credentials: 'same-origin'});
            if (!res.ok) throw new Error(String(res.status));
            const text = await res.text();
            mount(body, renderMarkdown(text, {className: 'md'}));
            applyFocus();
          } catch (e) {
            loaded = false;
            mount(
                body,
                h('p.muted.small',
                  'The preparation guide isn\u2019t available yet. In short: name frames, use auto layout and real components, keep one screen per frame, and caption screenshots with what they show.'));
          }
        }
      },
        h('summary', icon('help'), 'How to prepare your files', hint), body);
  det.focusSection = (name) => {
    focus = name;
    hint.textContent = name ?
        ` \u00b7 ${name[0].toUpperCase()}${name.slice(1)} section highlighted` :
        '';
    if (loaded) applyFocus();
  };
  return det;
}

/** Reads an image file → {data_url, width, height}, ≤ MAX_SIDE long side. */
/**
 * @param {*} file
 * @return {*}
 */
export async function downscale(file) {
  const src = await new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.onerror = () => reject(fr.error || new Error('read failed'));
    fr.readAsDataURL(file);
  });
  const img = await new Promise((resolve, reject) => {
    const i = new Image();
    i.onload = () => resolve(i);
    i.onerror = () => reject(new Error('not a decodable image'));
    i.src = src;
  });
  const w = img.naturalWidth;
  const ht = img.naturalHeight;
  const scale = Math.min(1, MAX_SIDE / Math.max(w, ht));
  if (scale === 1 && file.size < 3.5 * 1024 * 1024)
    return {data_url: src, width: w, height: ht};
  const cw = Math.max(1, Math.round(w * scale));
  const ch = Math.max(1, Math.round(ht * scale));
  const canvas = document.createElement('canvas');
  canvas.width = cw;
  canvas.height = ch;
  const g = canvas.getContext('2d');
  g.imageSmoothingQuality = 'high';
  g.drawImage(img, 0, 0, cw, ch);
  let out = canvas.toDataURL(
      file.type === 'image/png' ? 'image/png' : 'image/jpeg', 0.9);
  if (out.length > 6 * 1024 * 1024) out = canvas.toDataURL('image/jpeg', 0.88);
  return {data_url: out, width: cw, height: ch};
}
