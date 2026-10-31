// Theme & design-token editor (visual token groups, custom tokens, presets,
// two-way CSS editor, live preview, "Edit tokens via chat"). Used by the
// Design system › Tokens tab. Storage is `settings.theme`.
import {DEFAULT_THEME, ensureThemeFonts, parseCssToTheme, themeToCssText} from '../renderer/a2ui.js';

import {get, put} from './api.js';
import {enhanceCodeEditor} from './code_highlight.js';
import {debounce, h, icon, mount} from './dom.js';
import {emit} from './store.js';
import {ALL_THEME_FIELDS, THEME_GROUPS, themeFieldControl} from './theme_tokens.js';
import {segmented, toast} from './ui.js';

const CANONICAL_SLOTS =
    ALL_THEME_FIELDS.map((f) => ({key: f.key, label: `${f.label} (${f.key})`}));

export const THEME_PRESETS = [
  {
    id: 'm3_default',
    label: 'M3 Default',
    theme: {...DEFAULT_THEME},
  },
  {
    id: 'editorial_serif',
    label: 'Editorial Serif',
    theme: {
      ...DEFAULT_THEME,
      primary: '#7A2E1D',
      on_primary: '#FFFFFF',
      primary_container: '#F9E2DC',
      on_primary_container: '#380C03',
      secondary: '#5C5346',
      secondary_container: '#E8E0D5',
      on_secondary_container: '#221D16',
      surface: '#FCF9F4',
      surface_container_low: '#F6F2EB',
      surface_container: '#EFEAE1',
      surface_container_high: '#E5DFD3',
      on_surface: '#1E1B18',
      on_surface_variant: '#524B42',
      outline: '#BDB4A7',
      radius_card: 12,
      radius_control: 6,
      radius_chip: 4,
      radius_media: 8,
      font_family: 'Georgia, serif',
      font_family_heading: 'Georgia, serif',
      heading_scale: 1.1,
      heading_weight: 700,
      line_height: 1.6,
      space: 10,
      card_padding: 16,
    },
  },
  {
    id: 'compact_enterprise',
    label: 'Compact Enterprise',
    theme: {
      ...DEFAULT_THEME,
      primary: '#0F62FE',
      on_primary: '#FFFFFF',
      primary_container: '#D0E2FF',
      on_primary_container: '#001D6C',
      secondary: '#393939',
      secondary_container: '#E0E0E0',
      on_secondary_container: '#161616',
      surface: '#FFFFFF',
      surface_container_low: '#F8F9FA',
      surface_container: '#F1F3F5',
      surface_container_high: '#E9ECEF',
      on_surface: '#161616',
      on_surface_variant: '#525252',
      outline: '#C6C6C6',
      radius_card: 8,
      radius_control: 4,
      radius_chip: 4,
      radius_media: 6,
      type_scale: 0.95,
      heading_weight: 600,
      space: 6,
      card_padding: 10,
      surface_padding: 12,
      control_height: 34,
    },
  },
  {
    id: 'warm_care',
    label: 'Warm Care & Health',
    theme: {
      ...DEFAULT_THEME,
      primary: '#006A60',
      on_primary: '#FFFFFF',
      primary_container: '#9EF2E4',
      on_primary_container: '#00201C',
      secondary: '#4A635F',
      secondary_container: '#CCE8E2',
      on_secondary_container: '#05201C',
      tertiary: '#456179',
      surface: '#FBFDFB',
      surface_container_low: '#F3F7F5',
      surface_container: '#EBF1EE',
      surface_container_high: '#E1EAE6',
      on_surface: '#191C1B',
      on_surface_variant: '#3F4946',
      outline: '#BEC9C5',
      radius_card: 28,
      radius_control: 16,
      radius_chip: 12,
      radius_media: 16,
      type_scale: 1.05,
      space: 10,
      card_padding: 16,
      control_height: 44,
    },
  },
  {
    id: 'high_contrast_dark',
    label: 'High-Contrast Dark',
    theme: {
      ...DEFAULT_THEME,
      scheme: 'dark',
      primary: '#8AB4F8',
      on_primary: '#041E49',
      primary_container: '#0842A0',
      on_primary_container: '#D3E3FD',
      secondary: '#81C995',
      on_secondary: '#0A3818',
      secondary_container: '#0F5223',
      on_secondary_container: '#C4EED0',
      surface: '#0E0E10',
      on_surface: '#F1F3F4',
      surface_container_low: '#16171A',
      surface_container: '#1C1D21',
      surface_container_high: '#26282D',
      on_surface_variant: '#BDC1C6',
      outline: '#5F6368',
      outline_variant: '#3C4043',
      radius_card: 20,
      radius_control: 10,
    },
  },
];

const PREVIEW_MESSAGES = [
  {
    version: 'v0.9',
    createSurface: {
      surfaceId: 'preview',
      catalogId:
          'https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json',
    },
  },
  {
    version: 'v0.9',
    updateComponents: {
      surfaceId: 'preview',
      components: [
        {id: 'root', component: 'Card', child: 'col'},
        {
          id: 'col',
          component: 'Column',
          children: ['img', 'title', 'body', 'chips', 'divider', 'actions'],
        },
        {
          id: 'img',
          component: 'Image',
          url: '/img/harbor-morning/640x280.svg',
          fit: 'cover',
        },
        {
          id: 'title',
          component: 'Text',
          text: 'Saturday at the harbor',
          variant: 'h3',
        },
        {
          id: 'body',
          component: 'Text',
          text:
              'Ferry at **9:40**, lunch near the market, back before the tide turns. Two options need your call.',
          variant: 'body',
        },
        {
          id: 'chips',
          component: 'ChoicePicker',
          label: 'Lunch',
          variant: 'mutuallyExclusive',
          displayStyle: 'chips',
          options: [
            {label: 'Fish market', value: 'fish'},
            {label: 'Picnic', value: 'picnic'},
            {label: 'Caf\u00e9', value: 'cafe'},
          ],
          value: {path: '/lunch'},
        },
        {id: 'divider', component: 'Divider'},
        {
          id: 'actions',
          component: 'Row',
          children: ['b2', 'b1'],
          justify: 'end',
        },
        {id: 'b1t', component: 'Text', text: 'Book ferry'},
        {
          id: 'b1',
          component: 'Button',
          child: 'b1t',
          variant: 'primary',
          action: {event: {name: 'book', context: {}}},
        },
        {id: 'b2t', component: 'Text', text: 'Later'},
        {
          id: 'b2',
          component: 'Button',
          child: 'b2t',
          action: {event: {name: 'later', context: {}}},
        },
      ],
    },
  },
  {
    version: 'v0.9',
    updateDataModel: {surfaceId: 'preview', value: {lunch: ['fish']}},
  },
];

/** settings.theme → a complete, editable copy (defaults filled in). */
export function normalizeTheme(theme) {
  return {
    ...DEFAULT_THEME,
    ...(theme || {}),
    custom_tokens: {...(theme?.custom_tokens || {})},
    custom_css: String(theme?.custom_css || ''),
  };
}

/** CSS custom property for a canonical token key or custom-token slug. */
export function tokenCssVar(key) {
  return `--a2ui-${
      String(key)
          .replace(/^--a2ui-/, '')
          .replace(/^--/, '')
          .replace(/_/g, '-')}`;
}

/**
 * Saves `theme` into the project's settings without touching other keys:
 * re-fetches settings, replaces only `theme`, PUTs. Emits settings-changed.
 * Returns the saved settings.
 */
export async function saveThemeToProject(
    pid, theme, {source = 'theme-editor', summary = ''} = {}) {
  const {settings: fresh} = await get(`/api/projects/${pid}/settings`);
  const next = {...(fresh || {}), theme};
  const res = await put(
      `/api/projects/${pid}/settings`, {settings: next, source, summary});
  const saved = (res && res.settings) || next;
  emit('settings-changed', {
    settings: saved,
    token_revision: res && res.token_revision,
    source,
  });
  return saved;
}

/**
 * Mounts the theme editor into `container`.
 * opts: {theme, onDirty(), usedBy: {cssVar: [componentName]}}
 * Returns {getTheme(), setTheme(theme), setUsedBy(map), destroy()}.
 */
export function mountThemeEditor(container, opts = {}) {
  const state = {theme: normalizeTheme(opts.theme)};
  let usedBy = opts.usedBy || {};
  const markDirty = () => opts.onDirty && opts.onDirty();

  const previewHost = h('div.theme-preview-surface#theme-preview');
  const previewNote = h('p.help');
  let surface = null;
  let renderer = null;
  let themeEditMode = localStorage.getItem('dcb.themeEditMode') || 'visual';

  const fieldElementsByKey = new Map();
  const customTokensListEl = h('div.custom-tokens-list#custom-tokens-list');

  const cssEditorTa =
      h('textarea.input.code-input.theme-css-editor#theme-css-editor', {
        rows: 26,
        spellcheck: 'false',
        placeholder: ':root {\n  --a2ui-primary: #0B57D0;\n}\n',
        oninput: () => {
          const parsed = parseCssToTheme(cssEditorTa.value, state.theme);
          state.theme = parsed;
          markDirty();
          syncVisualControlsFromTheme();
          renderCustomTokensList();
          updatePreviewSoon();
        },
      });

  function usedByHint(cssVar) {
    const raw = usedBy[cssVar];
    const names = Array.isArray(raw) ? raw : (raw && raw.components) || [];
    const rules = (!Array.isArray(raw) && raw && raw.constitution_rules) || [];
    if (!names.length && !rules.length) return null;
    const labelParts = [];
    if (names.length) {
      labelParts.push(
          names.length <= 2 ? names.join(', ') :
                              `${names[0]} +${names.length - 1}`);
    }
    if (rules.length) {
      labelParts.push(rules.slice(0, 2).join(', '));
    }
    const titleParts = [];
    if (names.length) {
      titleParts.push(`Used by custom components: ${names.join(', ')}`);
    }
    if (rules.length) {
      titleParts.push(`Referenced in constitution: ${rules.join(', ')}`);
    }
    return h(
        'span.token-usedby', {title: titleParts.join(' \u00b7 ')},
        icon(names.length ? 'widgets' : 'gavel'), labelParts.join(' \u00b7 '));
  }

  function buildFieldControl(fd) {
    const row = themeFieldControl(fd, state.theme[fd.key], {
      idPrefix: 'theme-',
      onChange: (v) => {
        state.theme[fd.key] = v;
        markDirty();
        syncCssEditorFromTheme();
        updatePreviewSoon();
      },
    });
    const hintSlot = h('span.token-usedby-slot');
    row.querySelector('.theme-label')?.appendChild(hintSlot);
    fieldElementsByKey.set(fd.key, {fd, row, hintSlot});
    return row;
  }

  function drawFieldHints() {
    for (const [key, {hintSlot}] of fieldElementsByKey.entries()) {
      mount(hintSlot, usedByHint(tokenCssVar(key)));
    }
  }

  function renderCustomTokensList() {
    const customTokens = state.theme.custom_tokens || {};
    const entries = Object.entries(customTokens);
    if (!entries.length) {
      mount(
          customTokensListEl,
          h('p.muted.small',
            'No custom tokens yet. Add custom tokens (e.g. brand-accent or status-warning) to expose extra --a2ui-* variables and optionally map them to native renderer slots.'),
      );
      return;
    }
    mount(
        customTokensListEl,
        entries.map(([slug, rawEntry]) => {
          const entry = (rawEntry && typeof rawEntry === 'object') ?
              rawEntry :
              {value: String(rawEntry ?? ''), type: 'string', maps_to: ''};
          const cleanSlug =
              String(slug).replace(/^--a2ui-/, '').replace(/^--/, '');
          const isColor = entry.type === 'color' ||
              /^#[0-9a-f]{6}$/i.test(String(entry.value || ''));

          const valInput = h('input.input.input-sm.mono', {
            value: entry.value ?? '',
            placeholder: 'Value (e.g. #FF5722 or 14px)',
            oninput: (e) => {
              entry.value = e.target.value;
              state.theme.custom_tokens[cleanSlug] = entry;
              if (colorPicker && /^#[0-9a-f]{6}$/i.test(entry.value.trim())) {
                colorPicker.value = entry.value.trim();
              }
              markDirty();
              syncCssEditorFromTheme();
              updatePreviewSoon();
            },
          });
          const colorPicker = isColor ? h('input.color', {
            type: 'color',
            value: /^#[0-9a-f]{6}$/i.test(String(entry.value || '')) ?
                entry.value :
                '#0B57D0',
            oninput: (e) => {
              entry.value = e.target.value.toUpperCase();
              valInput.value = entry.value;
              state.theme.custom_tokens[cleanSlug] = entry;
              markDirty();
              syncCssEditorFromTheme();
              updatePreviewSoon();
            },
          }) :
                                        null;

          const typeSelect = h(
              'select.input.input-sm',
              {
                title: 'Token type',
                onchange: (e) => {
                  entry.type = e.target.value;
                  state.theme.custom_tokens[cleanSlug] = entry;
                  markDirty();
                  renderCustomTokensList();
                  syncCssEditorFromTheme();
                  updatePreviewSoon();
                },
              },
              ['color', 'dimension', 'number', 'string'].map(
                  (t) => h(
                      'option',
                      {value: t, selected: (entry.type || 'string') === t}, t),
                  ),
          );

          const mapsToSelect = h(
              'select.input.input-sm',
              {
                title:
                    'Optional native renderer slot this custom token drives on Android Compose / .rc / iOS SwiftUI',
                onchange: (e) => {
                  entry.maps_to = e.target.value;
                  state.theme.custom_tokens[cleanSlug] = entry;
                  markDirty();
                  syncCssEditorFromTheme();
                  updatePreviewSoon();
                },
              },
              [
                h('option', {value: '', selected: !entry.maps_to},
                  'Web CSS only (no native slot)'),
                ...CANONICAL_SLOTS.map(
                    (s) =>
                        h('option',
                          {value: s.key, selected: entry.maps_to === s.key},
                          `Maps to: ${s.label}`),
                    ),
              ],
          );

          return h(
              'div.custom-token-row',
              h('div.custom-token-name-cell',
                h('code.custom-token-name', `--a2ui-${cleanSlug}`),
                usedByHint(`--a2ui-${cleanSlug}`)),
              typeSelect,
              h('div.custom-token-val-wrap', colorPicker, valInput),
              mapsToSelect,
              h('button.icon-btn', {
                type: 'button',
                title: `Remove --a2ui-${cleanSlug}`,
                onclick: () => {
                  delete state.theme.custom_tokens[cleanSlug];
                  markDirty();
                  renderCustomTokensList();
                  syncCssEditorFromTheme();
                  updatePreviewSoon();
                },
              },
                icon('delete')),
          );
        }),
    );
  }

  const newTokenNameIn = h('input.input.input-sm.mono#new-custom-token-name', {
    placeholder: 'Token name (e.g. brand-accent)',
  });
  const newTokenValIn = h('input.input.input-sm.mono#new-custom-token-val', {
    placeholder: 'Value (e.g. #E8710A or 18px)',
  });
  const newTokenMapsToIn = h(
      'select.input.input-sm#new-custom-token-maps-to',
      {},
      [
        h('option', {value: ''}, 'Web CSS only (or pick native target…)'),
        ...CANONICAL_SLOTS.map(
            (s) => h('option', {value: s.key}, `Maps to: ${s.label}`)),
      ],
  );
  const addCustomTokenBtn = h(
      'button.btn.btn-ghost.btn-sm#add-custom-token',
      {
        type: 'button',
        onclick: () => {
          const rawName = newTokenNameIn.value.trim()
                              .replace(/^--a2ui-/, '')
                              .replace(/^--/, '')
                              .replace(/[^a-zA-Z0-9_-]/g, '-')
                              .toLowerCase();
          const rawVal = newTokenValIn.value.trim() || '#0B57D0';
          if (!rawName) {
            toast(
                'Enter a custom token name first (e.g. brand-accent)', 'warn');
            newTokenNameIn.focus();
            return;
          }
          const isColor = /^#[0-9a-f]{3,8}$/i.test(rawVal);
          const isDim = /^-?\d+(\.\d+)?(px|dp|rem|em)$/i.test(rawVal);
          state.theme.custom_tokens = state.theme.custom_tokens || {};
          state.theme.custom_tokens[rawName] = {
            value: rawVal,
            type: isColor ? 'color' :
                isDim     ? 'dimension' :
                            'string',
            maps_to: newTokenMapsToIn.value || '',
          };
          newTokenNameIn.value = '';
          newTokenValIn.value = '';
          newTokenMapsToIn.value = '';
          markDirty();
          renderCustomTokensList();
          syncCssEditorFromTheme();
          updatePreviewSoon();
        },
      },
      icon('add'),
      'Add token',
  );

  const visualTokensPane = h(
      'div.theme-visual-pane#theme-visual-pane',
      THEME_GROUPS.map(
          (grp, idx) =>
              h('details.theme-group-details', {open: idx === 0 || idx === 1},
                h('summary.theme-group-summary',
                  h('span.theme-group-title', grp.title),
                  h('span.muted.small', `${grp.fields.length} tokens`)),
                grp.help ? h('p.help.theme-group-help', grp.help) : null,
                h('div.theme-fields-grid',
                  grp.fields.map((fd) => buildFieldControl(fd)))),
          ),
      h('details.theme-group-details',
        {open: true, id: 'custom-tokens-section'},
        h('summary.theme-group-summary',
          h('span.theme-group-title', 'Custom Design Tokens (--a2ui-*)'),
          h('span.muted.small', 'Extensible tokens + native slot mapping')),
        h('p.help.theme-group-help',
          'Define custom tokens for your design system. Map a custom token to a canonical slot so Android Compose, RemoteCompose (.rc), and iOS SwiftUI renderers apply it too.'),
        customTokensListEl,
        h('div.custom-token-add-bar', newTokenNameIn, newTokenValIn,
          newTokenMapsToIn, addCustomTokenBtn)),
  );

  const cssEditor = enhanceCodeEditor(cssEditorTa, {lang: 'css'});
  const cssEditorPane = h(
      'div.theme-css-pane#theme-css-pane',
      {hidden: themeEditMode !== 'css'},
      h('div.theme-css-toolbar',
        h('span.muted.small',
          'Edit --a2ui-* custom properties in :root (synced with Visual Tokens & native renderers) or add custom Web CSS rules below.'),
        h('button.btn.btn-ghost.btn-sm#format-theme-css', {
          type: 'button',
          onclick: () => {
            syncCssEditorFromTheme();
            toast('Formatted CSS from current tokens', 'info');
          },
        },
          icon('auto_fix_high'), 'Format CSS')),
      cssEditor.wrap,
  );

  function setThemeEditMode(m) {
    themeEditMode = m;
    localStorage.setItem('dcb.themeEditMode', m);
    visualTokensPane.hidden = m !== 'visual';
    cssEditorPane.hidden = m !== 'css';
    if (m === 'css') syncCssEditorFromTheme();
  }

  const modeSwitch = segmented(
      [
        {value: 'visual', label: 'Visual Tokens', id: 'theme-mode-visual'},
        {value: 'css', label: 'CSS & Variables Editor', id: 'theme-mode-css'},
      ],
      themeEditMode,
      (v) => setThemeEditMode(v),
  );

  const presetsBar = h(
      'div.theme-presets-bar',
      h('span.muted.small', 'Presets:'),
      ...THEME_PRESETS.map(
          (pr) =>
              h('button.btn.btn-ghost.btn-sm.preset-btn', {
                type: 'button',
                id: `theme-preset-${pr.id}`,
                onclick: () => {
                  state.theme = {
                    ...JSON.parse(JSON.stringify(pr.theme)),
                    custom_tokens: {...(state.theme.custom_tokens || {})},
                    custom_css: state.theme.custom_css || '',
                  };
                  markDirty();
                  rerender();
                  toast(`Applied \u201c${pr.label}\u201d preset`, 'info');
                },
              },
                pr.label),
          ),
      h('button.btn.btn-quiet.btn-sm#theme-chat-btn', {
        type: 'button',
        onclick: () => emit('chat-open', {
          text: 'Update the design tokens to ',
        }),
      },
        icon('forum'), 'Edit tokens via chat'),
      h('button.btn.btn-quiet.btn-sm#theme-reset', {
        type: 'button',
        onclick: () => {
          state.theme = {
            ...DEFAULT_THEME,
            custom_tokens: {},
            custom_css: '',
          };
          markDirty();
          rerender();
        },
      },
        icon('restart_alt'), 'Reset'),
  );

  const el =
      h('div.theme-editor', h('div.theme-toolbar-row', modeSwitch, presetsBar),
        h('div.theme-layout',
          h('div.theme-fields-col', visualTokensPane, cssEditorPane),
          h('div.theme-preview',
            h('div.theme-preview-label', 'Live Renderer Preview'), previewHost,
            previewNote)));
  container.appendChild(el);

  function syncVisualControlsFromTheme() {
    for (const [key, {row}] of fieldElementsByKey.entries()) {
      const v = state.theme[key] ?? DEFAULT_THEME[key] ?? '';
      if (typeof row.setValue === 'function') {
        row.setValue(v);
      }
    }
  }

  function syncCssEditorFromTheme() {
    const formatted = themeToCssText(state.theme);
    if (cssEditorTa.value !== formatted) {
      cssEditorTa.value = formatted;
    }
  }

  async function updatePreview() {
    const theme = state.theme;
    try {
      ensureThemeFonts(theme, document);
    } catch (_) {
    }
    try {
      if (!renderer) renderer = await import('/renderer/a2ui.js');
      if (surface) {
        try {
          surface.destroy();
        } catch (_) { /* ignore */
        }
      }
      previewHost.replaceChildren();
      surface = renderer.mountSurface(
          previewHost, PREVIEW_MESSAGES,
          {theme, interactive: true, onAction: () => {}});
      previewNote.textContent = '';
    } catch (e) {
      console.warn('renderer preview unavailable', e);
      previewHost.replaceChildren(
          h('div.preview-fallback', {
            style: {
              background: theme.surface,
              color: theme.on_surface,
              borderRadius: `${theme.radius_card}px`,
              border: `1px solid ${theme.outline}`,
              padding: `${theme.space * 2}px`,
              fontFamily: theme.font_family,
            },
          },
            h('div', {
              style: {
                fontSize: `${20 * theme.type_scale}px`,
                fontWeight: theme.heading_weight || 600,
              },
            },
              'Saturday at the harbor'),
            h('p', {style: {color: theme.on_surface_variant}},
              'Renderer preview unavailable \u2014 showing token swatches.'),
            h('span', {
              style: {
                display: 'inline-block',
                background: theme.primary,
                color: theme.on_primary,
                borderRadius: `${theme.radius_control}px`,
                padding: '8px 16px',
              },
            },
              'Book ferry')));
      previewNote.textContent =
          'The A2UI renderer could not be loaded; the swatch approximates the tokens.';
    }
  }
  const updatePreviewSoon = debounce(updatePreview, 120);

  function rerender() {
    syncVisualControlsFromTheme();
    renderCustomTokensList();
    syncCssEditorFromTheme();
    updatePreview();
  }

  renderCustomTokensList();
  drawFieldHints();
  syncCssEditorFromTheme();
  setThemeEditMode(themeEditMode);
  updatePreviewSoon();

  return {
    el,
    getTheme: () => state.theme,
    setTheme(theme) {
      state.theme = normalizeTheme(theme);
      rerender();
    },
    setUsedBy(map) {
      usedBy = map || {};
      drawFieldHints();
      renderCustomTokensList();
    },
    destroy() {
      if (surface) {
        try {
          surface.destroy();
        } catch (_) { /* ignore */
        }
      }
    },
  };
}
