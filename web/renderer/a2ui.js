// A2UI v0.9 web renderer (Basic Catalog), written from the public spec.
//
//   import {mountSurface, themeToCssVars} from '/renderer/a2ui.js';
//   const s = mountSurface(el, messages, {theme, onAction, interactive});
//   s.update(more); s.meta; s.destroy();
//
// Rendering model: each surface is built into DOM once per structural change
// (createSurface / updateComponents / template list shape change). Every
// dynamic property registers an "updater" closure; data changes (server
// updateDataModel or two-way input bindings) just re-run updaters, so inputs
// keep focus and caret. Untrusted text only ever becomes text nodes.

import {compileCustomComponent, fromNestedControl, renderCustomTemplate} from './custom.js';
import {callFunction, evaluateChecks, isPathBinding, parseDate, resolveActionContext, resolveDynamic, safeHttpUrl, toDisplayString,} from './functions.js';
import {iconGlyph} from './icons.js';
import {parseMarkdown} from './markdown.js';
import {BASIC_COMPONENTS, childRefs, expandChildList, resolvePath, SurfaceStore, templateSignature,} from './model.js';

export {compileCustomComponent} from './custom.js';
export {SurfaceStore} from './model.js';

// ---------------------------------------------------------------------------
// Theme.

export const DEFAULT_THEME = Object.freeze({
  primary: '#0B57D0',
  on_primary: '#FFFFFF',
  primary_container: '#D3E3FD',
  on_primary_container: '#041E49',
  secondary: '#00639B',
  on_secondary: '#FFFFFF',
  secondary_container: '#C2E7FF',
  on_secondary_container: '#001D35',
  tertiary: '#146C2E',
  on_tertiary: '#FFFFFF',
  surface: '#FFFFFF',
  on_surface: '#1F1F1F',
  surface_container_low: '#F8FAFD',
  surface_container: '#F0F4F9',
  surface_container_high: '#E9EEF6',
  on_surface_variant: '#444746',
  outline: '#C4C7C5',
  outline_variant: '#E0E3E1',
  error: '#B3261E',
  on_error: '#FFFFFF',
  error_container: '#F9DEDC',
  on_error_container: '#410E0B',
  radius_card: 24,
  radius_control: 12,
  radius_chip: 8,
  radius_media: 12,
  border_width: 1,
  card_elevation: 0,
  font_family: 'Google Sans Flex',
  font_family_heading: '',
  font_family_mono: 'Google Sans Code',
  type_scale: 1.0,
  heading_scale: 1.0,
  heading_weight: 500,
  body_weight: 400,
  line_height: 1.5,
  space: 8,
  card_padding: 12,
  surface_padding: 16,
  card_margin: 4,
  item_margin: 4,
  control_height: 40,
  scheme: 'light',
  custom_tokens: Object.freeze({}),
  custom_css: '',
});

export const DARK_COLORS = Object.freeze({
  primary: '#A8C7FA',
  on_primary: '#062E6F',
  primary_container: '#0842A0',
  on_primary_container: '#D3E3FD',
  secondary: '#7FCFFF',
  on_secondary: '#003355',
  secondary_container: '#004A77',
  on_secondary_container: '#C2E7FF',
  tertiary: '#6DD58C',
  on_tertiary: '#0A3818',
  surface: '#131314',
  on_surface: '#E3E3E3',
  surface_container_low: '#1B1B1F',
  surface_container: '#1E1F20',
  surface_container_high: '#282A2C',
  on_surface_variant: '#C4C7C5',
  outline: '#444746',
  outline_variant: '#333537',
  error: '#F2B8B5',
  on_error: '#601410',
  error_container: '#8C1D18',
  on_error_container: '#F9DEDC',
});

const COLOR_KEYS = Object.keys(DARK_COLORS);

export const CSS_VAR_TO_TOKEN = Object.freeze({
  '--a2ui-primary': 'primary',
  '--a2ui-on-primary': 'on_primary',
  '--a2ui-primary-container': 'primary_container',
  '--a2ui-on-primary-container': 'on_primary_container',
  '--a2ui-secondary': 'secondary',
  '--a2ui-on-secondary': 'on_secondary',
  '--a2ui-secondary-container': 'secondary_container',
  '--a2ui-on-secondary-container': 'on_secondary_container',
  '--a2ui-tertiary': 'tertiary',
  '--a2ui-on-tertiary': 'on_tertiary',
  '--a2ui-surface': 'surface',
  '--a2ui-on-surface': 'on_surface',
  '--a2ui-surface-container-low': 'surface_container_low',
  '--a2ui-surface-container': 'surface_container',
  '--a2ui-surface-container-high': 'surface_container_high',
  '--a2ui-on-surface-variant': 'on_surface_variant',
  '--a2ui-outline': 'outline',
  '--a2ui-outline-variant': 'outline_variant',
  '--a2ui-error': 'error',
  '--a2ui-on-error': 'on_error',
  '--a2ui-error-container': 'error_container',
  '--a2ui-on-error-container': 'on_error_container',
  '--a2ui-radius-card': 'radius_card',
  '--a2ui-radius-control': 'radius_control',
  '--a2ui-radius-chip': 'radius_chip',
  '--a2ui-radius-media': 'radius_media',
  '--a2ui-border-width': 'border_width',
  '--a2ui-card-elevation': 'card_elevation',
  '--a2ui-font': 'font_family',
  '--a2ui-font-family': 'font_family',
  '--a2ui-font-heading': 'font_family_heading',
  '--a2ui-font-mono': 'font_family_mono',
  '--a2ui-type-scale': 'type_scale',
  '--a2ui-heading-scale': 'heading_scale',
  '--a2ui-heading-weight': 'heading_weight',
  '--a2ui-body-weight': 'body_weight',
  '--a2ui-line-height': 'line_height',
  '--a2ui-space': 'space',
  '--a2ui-card-padding': 'card_padding',
  '--a2ui-surface-padding': 'surface_padding',
  '--a2ui-card-margin': 'card_margin',
  '--a2ui-item-margin': 'item_margin',
  '--a2ui-control-height': 'control_height',
  '--a2ui-scheme': 'scheme',
});

export const BOX_PX_TOKENS = new Set([
  'card_padding',
  'surface_padding',
  'card_margin',
  'item_margin',
]);

const PX_TOKENS = new Set([
  'radius_card',
  'radius_control',
  'radius_chip',
  'radius_media',
  'border_width',
  'card_elevation',
  'space',
  'control_height',
]);

const NUM_TOKENS = new Set([
  'type_scale',
  'heading_scale',
  'heading_weight',
  'body_weight',
  'line_height',
]);

/**
 * Parses a 1..4 value box spacing token (number, array, or CSS string like
 * "12px 16px 8px 16px") into `[top, right, bottom, left]` integer px.
 */
export function parseBoxPx(val, fallback = 0) {
  const d =
      Number.isFinite(Number(fallback)) ? Math.round(Number(fallback)) : 0;
  if (typeof val === 'number' && Number.isFinite(val)) {
    const n = Math.round(val);
    return [n, n, n, n];
  }
  let nums = [];
  if (Array.isArray(val)) {
    nums = val.slice(0, 4)
               .map((x) => {
                 const m = String(x ?? '').match(/-?\d+(?:\.\d+)?/);
                 return m ? Math.round(parseFloat(m[0])) : NaN;
               })
               .filter((n) => Number.isFinite(n));
  } else {
    const matches = String(val ?? '').match(/-?\d+(?:\.\d+)?/g) || [];
    nums = matches.slice(0, 4).map((m) => Math.round(parseFloat(m)));
  }
  if (!nums.length) return [d, d, d, d];
  if (nums.length === 1) return [nums[0], nums[0], nums[0], nums[0]];
  if (nums.length === 2) return [nums[0], nums[1], nums[0], nums[1]];
  if (nums.length === 3) return [nums[0], nums[1], nums[2], nums[1]];
  return [nums[0], nums[1], nums[2], nums[3]];
}

/**
 * Coerces a box spacing value to a scalar integer when all 4 sides match, or
 * `'Tpx Rpx Bpx Lpx'` when sides differ.
 */
export function coerceBoxPx(val, fallback = 0) {
  const [t, r, b, l] = parseBoxPx(val, fallback);
  if (t === r && r === b && b === l) return t;
  return `${t}px ${r}px ${b}px ${l}px`;
}

/**
 * Formats a box spacing value as CSS (`'Npx'` when all 4 sides match, or
 * `'Tpx Rpx Bpx Lpx'` when sides differ).
 */
export function boxPx(val, fallback = 0) {
  const [t, r, b, l] = parseBoxPx(val, fallback);
  if (t === r && r === b && b === l) return `${t}px`;
  return `${t}px ${r}px ${b}px ${l}px`;
}

/**
 * Fills defaults. In dark scheme, color tokens that are missing or still equal
 * to the light defaults are swapped for dark defaults. Also resolves
 * custom_tokens `maps_to` references onto canonical keys.
 */
export function resolveTheme(theme = {}) {
  const t = {
    ...DEFAULT_THEME,
    ...(theme || {}),
    custom_tokens: {...(theme?.custom_tokens || {})},
    custom_css: String(theme?.custom_css || ''),
  };
  for (const k of Object.keys(DEFAULT_THEME)) {
    if (k === 'custom_tokens' || k === 'custom_css' ||
        k === 'font_family_heading')
      continue;
    if (t[k] == null || t[k] === '') t[k] = DEFAULT_THEME[k];
  }
  if (t.font_family_heading == null) t.font_family_heading = '';
  if (theme && theme.space != null && theme.space !== '') {
    const spaceNum = Number(String(t.space).replace(/px$/i, ''));
    if (Number.isFinite(spaceNum) && spaceNum >= 0) {
      const derivedMargin = Math.max(0, Math.round(spaceNum / 2));
      if (theme.card_margin == null || theme.card_margin === '') {
        t.card_margin = derivedMargin;
      }
      if (theme.item_margin == null || theme.item_margin === '') {
        t.item_margin = derivedMargin;
      }
    }
  }
  for (const bk of BOX_PX_TOKENS) {
    t[bk] = coerceBoxPx(t[bk], DEFAULT_THEME[bk]);
  }
  if (String(t.scheme).toLowerCase() === 'dark') {
    t.scheme = 'dark';
    for (const k of COLOR_KEYS) {
      const given = theme && theme[k];
      if (!given ||
          String(given).toLowerCase() ===
              String(DEFAULT_THEME[k]).toLowerCase()) {
        t[k] = DARK_COLORS[k];
      }
    }
  } else {
    t.scheme = 'light';
  }
  if (t.custom_tokens && typeof t.custom_tokens === 'object') {
    for (const entry of Object.values(t.custom_tokens)) {
      if (entry && typeof entry === 'object' && entry.maps_to &&
          entry.value != null && entry.value !== '') {
        const target = String(entry.maps_to).trim();
        if (target in DEFAULT_THEME && target !== 'custom_tokens' &&
            target !== 'custom_css') {
          if (BOX_PX_TOKENS.has(target)) {
            t[target] = coerceBoxPx(entry.value, DEFAULT_THEME[target]);
          } else if (PX_TOKENS.has(target) || NUM_TOKENS.has(target)) {
            t[target] =
                Number(String(entry.value).replace(/px$/i, '')) || t[target];
          } else {
            t[target] = String(entry.value);
          }
        }
      }
    }
  }
  return t;
}

const px = (v, d) => {
  if (typeof v === 'number' && Number.isFinite(v)) return `${v}px`;
  if (Array.isArray(v)) return boxPx(v, d);
  if (typeof v === 'string' && v.trim()) {
    const s = v.trim();
    if (/^-?\d+(\.\d+)?$/.test(s)) return `${s}px`;
    if (/^-?\d+(\.\d+)?(\s+-?\d+(\.\d+)?){1,3}$/.test(s)) return boxPx(s, d);
    return s;
  }
  return `${d}px`;
};

const MONO_FONT_RE =
    /\b(mono|monospace|code|courier|consolas|menlo|jetbrains|inconsolata|iosevka|hack)\b/i;
const SERIF_FONT_RE =
    /\b(serif|garamond|georgia|baskerville|playfair|merriweather|lora|fraunces|cormorant|times|bodoni|caslon|newsreader|literata|spectral)\b/i;

function inferFontFallback(family) {
  const f = String(family || '').trim();
  if (MONO_FONT_RE.test(f)) {
    return '"Google Sans Code", "Roboto Mono", ui-monospace, monospace';
  }
  if (SERIF_FONT_RE.test(f) && !/\bsans\b/i.test(f)) {
    return '"Noto Serif", Georgia, Cambria, "Times New Roman", Times, serif';
  }
  return '"Google Sans", Roboto, system-ui, -apple-system, sans-serif';
}

function fontStack(family, fallback = null) {
  const f = String(family || DEFAULT_THEME.font_family)
                .replace(/["';{}<>]/g, '')
                .trim();
  const resolvedFallback = fallback || inferFontFallback(f);
  const fams =
      f.split(',')
          .map((x) => x.trim())
          .filter(Boolean)
          .map(
              (x) => (
                  /^(system-ui|sans-serif|serif|monospace|cursive|fantasy|ui-\w+)$/
                          .test(x) ?
                      x :
                      `"${x}"`));
  return [...fams, resolvedFallback].join(', ');
}

/** Theme tokens -> {'--a2ui-*': value}. */
export function themeToCssVars(theme = {}) {
  const t = resolveTheme(theme);
  const scale = Number(t.type_scale);
  const hScale = Number(t.heading_scale);
  const hWeight = Number(t.heading_weight);
  const bWeight = Number(t.body_weight);
  const lineH = Number(t.line_height);
  const space = Number(t.space);
  const elev = Number(t.card_elevation);
  const bodyStack = fontStack(t.font_family);
  const headingStack =
      t.font_family_heading && String(t.font_family_heading).trim() ?
      fontStack(t.font_family_heading) :
      bodyStack;
  const monoStack = fontStack(
      t.font_family_mono || 'Google Sans Code',
      '"Roboto Mono", ui-monospace, monospace');

  const vars = {
    '--a2ui-primary': t.primary,
    '--a2ui-on-primary': t.on_primary,
    '--a2ui-primary-container': t.primary_container,
    '--a2ui-on-primary-container': t.on_primary_container,
    '--a2ui-secondary': t.secondary,
    '--a2ui-on-secondary': t.on_secondary,
    '--a2ui-secondary-container': t.secondary_container,
    '--a2ui-on-secondary-container': t.on_secondary_container,
    '--a2ui-tertiary': t.tertiary,
    '--a2ui-on-tertiary': t.on_tertiary,
    '--a2ui-surface': t.surface,
    '--a2ui-on-surface': t.on_surface,
    '--a2ui-surface-container-low': t.surface_container_low,
    '--a2ui-surface-container': t.surface_container,
    '--a2ui-surface-container-high': t.surface_container_high,
    '--a2ui-on-surface-variant': t.on_surface_variant,
    '--a2ui-outline': t.outline,
    '--a2ui-outline-variant': t.outline_variant,
    '--a2ui-error': t.error,
    '--a2ui-on-error': t.on_error,
    '--a2ui-error-container': t.error_container,
    '--a2ui-on-error-container': t.on_error_container,
    '--a2ui-radius-card': px(t.radius_card, 24),
    '--a2ui-radius-control': px(t.radius_control, 12),
    '--a2ui-radius-chip': px(t.radius_chip, 8),
    '--a2ui-radius-media': px(t.radius_media, 12),
    '--a2ui-border-width': px(t.border_width, 1),
    '--a2ui-card-elevation':
        px(Number.isFinite(elev) && elev >= 0 ? elev : 0, 0),
    '--a2ui-card-shadow': (Number.isFinite(elev) && elev > 0) ?
        `0 ${Math.round(elev * 1.5)}px ${
            Math.round(elev * 4)}px rgba(0, 0, 0, ${
            t.scheme === 'dark' ? 0.42 : 0.12})` :
        'none',
    '--a2ui-font': bodyStack,
    '--a2ui-font-heading': headingStack,
    '--a2ui-font-mono': monoStack,
    '--a2ui-type-scale':
        String(Number.isFinite(scale) && scale > 0 ? scale : 1),
    '--a2ui-heading-scale':
        String(Number.isFinite(hScale) && hScale > 0 ? hScale : 1),
    '--a2ui-heading-weight':
        String(Number.isFinite(hWeight) && hWeight >= 100 ? hWeight : 500),
    '--a2ui-body-weight':
        String(Number.isFinite(bWeight) && bWeight >= 100 ? bWeight : 400),
    '--a2ui-line-height':
        String(Number.isFinite(lineH) && lineH > 0 ? lineH : 1.5),
    '--a2ui-space': px(Number.isFinite(space) && space >= 0 ? space : 8, 8),
    '--a2ui-card-padding': boxPx(t.card_padding, 12),
    '--a2ui-surface-padding': boxPx(t.surface_padding, 16),
    '--a2ui-card-margin': boxPx(t.card_margin, 4),
    '--a2ui-item-margin': boxPx(t.item_margin, 4),
    '--a2ui-control-height': px(t.control_height, 40),
    'color-scheme': t.scheme,
  };

  if (t.custom_tokens && typeof t.custom_tokens === 'object') {
    for (const [rawSlug, entry] of Object.entries(t.custom_tokens)) {
      const slug = String(rawSlug)
                       .replace(/^--a2ui-/, '')
                       .replace(/^--/, '')
                       .replace(/[^a-zA-Z0-9_-]/g, '-');
      if (!slug) continue;
      const val = (entry && typeof entry === 'object') ? entry.value : entry;
      if (val != null && String(val).trim() !== '') {
        const type = (entry && typeof entry === 'object') ? entry.type : '';
        vars[`--a2ui-${slug}`] =
            type === 'dimension' ? px(val, 0) : String(val).trim();
      }
    }
  }
  return vars;
}

export function applyTheme(el, theme) {
  for (const [k, v] of Object.entries(themeToCssVars(theme))) {
    el.style.setProperty(k, v);
  }
  const doc =
      el?.ownerDocument || (typeof document !== 'undefined' ? document : null);
  if (doc) {
    try {
      ensureThemeFonts(theme, doc);
    } catch {
    }
  }
  if (doc && el?.classList?.contains('a2ui-root')) {
    let styleEl = el.querySelector(':scope > style[data-a2ui-custom-css]');
    const css = String(theme?.custom_css || '').trim();
    if (css) {
      if (!styleEl) {
        styleEl = doc.createElement('style');
        styleEl.setAttribute('data-a2ui-custom-css', 'true');
        el.prepend(styleEl);
      }
      styleEl.textContent = css;
    } else if (styleEl) {
      styleEl.remove();
    }
  }
}

/**
 * Formats a theme object into an editable CSS stylesheet (`:root { --a2ui-...
 * }` + custom CSS).
 */
export function themeToCssText(theme = {}) {
  const t = resolveTheme(theme);
  const lines = [
    '/* Cross-platform Design Tokens (Web CSS, Android Compose, RemoteCompose .rc, iOS SwiftUI) */',
    ':root {',
    `  --a2ui-scheme: ${t.scheme};`,
    '',
    '  /* Color Palette (M3) */',
    `  --a2ui-primary: ${t.primary};`,
    `  --a2ui-on-primary: ${t.on_primary};`,
    `  --a2ui-primary-container: ${t.primary_container};`,
    `  --a2ui-on-primary-container: ${t.on_primary_container};`,
    `  --a2ui-secondary: ${t.secondary};`,
    `  --a2ui-on-secondary: ${t.on_secondary};`,
    `  --a2ui-secondary-container: ${t.secondary_container};`,
    `  --a2ui-on-secondary-container: ${t.on_secondary_container};`,
    `  --a2ui-tertiary: ${t.tertiary};`,
    `  --a2ui-on-tertiary: ${t.on_tertiary};`,
    `  --a2ui-surface: ${t.surface};`,
    `  --a2ui-on-surface: ${t.on_surface};`,
    `  --a2ui-surface-container-low: ${t.surface_container_low};`,
    `  --a2ui-surface-container: ${t.surface_container};`,
    `  --a2ui-surface-container-high: ${t.surface_container_high};`,
    `  --a2ui-on-surface-variant: ${t.on_surface_variant};`,
    `  --a2ui-outline: ${t.outline};`,
    `  --a2ui-outline-variant: ${t.outline_variant};`,
    `  --a2ui-error: ${t.error};`,
    `  --a2ui-on-error: ${t.on_error};`,
    `  --a2ui-error-container: ${t.error_container};`,
    `  --a2ui-on-error-container: ${t.on_error_container};`,
    '',
    '  /* Shape & Elevation */',
    `  --a2ui-radius-card: ${px(t.radius_card, 24)};`,
    `  --a2ui-radius-control: ${px(t.radius_control, 12)};`,
    `  --a2ui-radius-chip: ${px(t.radius_chip, 8)};`,
    `  --a2ui-radius-media: ${px(t.radius_media, 12)};`,
    `  --a2ui-border-width: ${px(t.border_width, 1)};`,
    `  --a2ui-card-elevation: ${px(t.card_elevation, 0)};`,
    '',
    '  /* Typography */',
    `  --a2ui-font: "${t.font_family}";`,
    `  --a2ui-font-heading: "${t.font_family_heading || t.font_family}";`,
    `  --a2ui-font-mono: "${t.font_family_mono || 'Google Sans Code'}";`,
    `  --a2ui-type-scale: ${t.type_scale};`,
    `  --a2ui-heading-scale: ${t.heading_scale};`,
    `  --a2ui-heading-weight: ${t.heading_weight};`,
    `  --a2ui-body-weight: ${t.body_weight};`,
    `  --a2ui-line-height: ${t.line_height};`,
    '',
    '  /* Spacing & Density */',
    `  --a2ui-space: ${px(t.space, 8)};`,
    `  --a2ui-card-padding: ${boxPx(t.card_padding, 12)};`,
    `  --a2ui-surface-padding: ${boxPx(t.surface_padding, 16)};`,
    `  --a2ui-card-margin: ${boxPx(t.card_margin, 4)};`,
    `  --a2ui-item-margin: ${boxPx(t.item_margin, 4)};`,
    `  --a2ui-control-height: ${px(t.control_height, 40)};`,
  ];

  const customEntries = Object.entries(t.custom_tokens || {});
  if (customEntries.length) {
    lines.push('', '  /* Custom Design Tokens */');
    for (const [rawSlug, entry] of customEntries) {
      const slug = String(rawSlug).replace(/^--a2ui-/, '').replace(/^--/, '');
      const val = (entry && typeof entry === 'object') ? entry.value : entry;
      const mapsTo = (entry && typeof entry === 'object' && entry.maps_to) ?
          ` /* maps_to: ${entry.maps_to} */` :
          '';
      lines.push(`  --a2ui-${slug}: ${val};${mapsTo}`);
    }
  }
  lines.push('}');
  if (t.custom_css && String(t.custom_css).trim()) {
    lines.push('', '/* Web Custom CSS Rules */', String(t.custom_css).trim());
  }
  return lines.join('\n');
}

/**
 * Parses a CSS text string (`:root { --a2ui-... }` + optional extra CSS rules)
 * back into a normalized theme object.
 */
export function parseCssToTheme(cssText = '', baseTheme = {}) {
  const next = {
    ...DEFAULT_THEME,
    ...(baseTheme || {}),
    custom_tokens: {...(baseTheme?.custom_tokens || {})},
  };
  const src = String(cssText || '');
  const propRe =
      /(--a2ui-[a-zA-Z0-9_-]+)\s*:\s*([^;}\n]+);?(?:\s*\/\*\s*maps_to:\s*([a-zA-Z0-9_-]+)\s*\*\/)?/g;
  let m;
  while ((m = propRe.exec(src)) !== null) {
    const varName = m[1].trim().toLowerCase();
    const rawVal = m[2].replace(/\/\*[\s\S]*?\*\//g, '').trim();
    const mapsToComment = (m[3] || '').trim();
    if (!rawVal) continue;
    const mappedKey = CSS_VAR_TO_TOKEN[varName];
    if (mappedKey) {
      if (BOX_PX_TOKENS.has(mappedKey)) {
        next[mappedKey] = coerceBoxPx(rawVal, DEFAULT_THEME[mappedKey]);
      } else if (PX_TOKENS.has(mappedKey)) {
        const num = parseFloat(rawVal.replace(/px$/i, ''));
        if (Number.isFinite(num)) next[mappedKey] = Math.round(num);
      } else if (NUM_TOKENS.has(mappedKey)) {
        const num = parseFloat(rawVal);
        if (Number.isFinite(num)) next[mappedKey] = num;
      } else if (
          mappedKey === 'font_family' || mappedKey === 'font_family_heading' ||
          mappedKey === 'font_family_mono') {
        const first = rawVal.split(',')[0].trim().replace(/^['"]|['"]$/g, '');
        next[mappedKey] = (mappedKey === 'font_family_heading' &&
                           first === next.font_family) ?
            '' :
            first;
      } else if (mappedKey === 'scheme') {
        next.scheme = rawVal.toLowerCase() === 'dark' ? 'dark' : 'light';
      } else {
        next[mappedKey] = rawVal;
      }
    } else {
      const slug = varName.slice('--a2ui-'.length);
      const prev = next.custom_tokens[slug] || {};
      const isColor =
          /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(rawVal) ||
          /^rgba?\(/i.test(rawVal);
      const isDim =
          /^-?\d+(\.\d+)?(px|dp|rem|em)?(\s+-?\d+(\.\d+)?(px|dp|rem|em)?){0,3}$/i
              .test(rawVal);
      next.custom_tokens[slug] = {
        value: rawVal,
        type: prev.type ||
            (isColor   ? 'color' :
                 isDim ? 'dimension' :
                         'string'),
        maps_to: mapsToComment || prev.maps_to || '',
      };
    }
  }

  // Extract any non-:root / non-.a2ui-root selector rules as custom_css
  const stripped =
      src.replace(/\/\* Cross-platform Design Tokens[\s\S]*?\*\//g, '')
          .replace(/\/\* Web Custom CSS Rules \*\//g, '')
          .replace(/(?::root|\.a2ui-root)\s*\{[\s\S]*?\}/g, '')
          .trim();
  next.custom_css = stripped;
  return next;
}

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex || '').trim());
  if (!m) return null;
  let h = m[1];
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

/** Picks black/white text for a background color by WCAG contrast. */
export function onColorFor(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return '#FFFFFF';
  const lin = rgb.map((c) => {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  const L = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
  return (1.05 / (L + 0.05)) >= ((L + 0.05) / 0.05) ? '#FFFFFF' : '#1F1F1F';
}

// ---------------------------------------------------------------------------
// Assets.

export const FONT_URLS = [
  'https://fonts.googleapis.com/css2?family=Google+Sans+Flex:opsz,wght@6..144,1..1000&display=swap',
  'https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=block',
];

const BUILTIN_OR_SYSTEM_FONTS = new Set([
  'google sans',
  'google sans text',
  'google sans display',
  'google sans flex',
  'google sans code',
  'material symbols rounded',
  'material symbols outlined',
  'material icons',
  'system-ui',
  'sans-serif',
  'serif',
  'monospace',
  'cursive',
  'fantasy',
  'ui-sans-serif',
  'ui-serif',
  'ui-monospace',
  'ui-rounded',
  '-apple-system',
  'blinkmacsystemfont',
  'segoe ui',
  'san francisco',
  'helvetica',
  'helvetica neue',
  'arial',
  'times new roman',
  'times',
  'courier new',
  'courier',
  'georgia',
  'cambria',
  'verdana',
  'tahoma',
  'trebuchet ms',
  'impact',
  'inherit',
  'initial',
  'unset',
]);

/** Extracts unique Google Font family names referenced by `theme`. */
export function extractGoogleFontFamilies(theme = {}) {
  const t = resolveTheme(theme);
  const rawSources = [
    t.font_family,
    t.font_family_heading,
    t.font_family_mono,
  ];
  if (t.custom_tokens && typeof t.custom_tokens === 'object') {
    for (const [k, entry] of Object.entries(t.custom_tokens)) {
      if (!/font/i.test(k)) continue;
      const v = (entry && typeof entry === 'object') ? entry.value : entry;
      if (typeof v === 'string' && !/^\d/.test(v.trim())) {
        rawSources.push(v);
      }
    }
  }
  const seen = new Set();
  const out = [];
  for (const raw of rawSources) {
    if (!raw || typeof raw !== 'string') continue;
    for (const part of raw.split(',')) {
      const clean = part.replace(/["';{}<>]/g, '').trim();
      if (!clean || clean.startsWith('var(') || clean.startsWith('--'))
        continue;
      const key = clean.toLowerCase();
      if (BUILTIN_OR_SYSTEM_FONTS.has(key) || seen.has(key)) continue;
      seen.add(key);
      out.push(clean);
    }
  }
  return out;
}

/** Builds a Google Fonts stylesheet URL for an array of font family names. */
export function buildGoogleFontsUrl(families = []) {
  if (!families.length) return '';
  const specs = families.map((fam) => {
    const encoded = encodeURIComponent(fam).replace(/%20/g, '+');
    return `${encoded}:300,400,500,600,700,800,400italic,700italic`;
  });
  return `https://fonts.googleapis.com/css?family=${
      specs.join('|')}&display=swap`;
}

/**
 * Ensures any non-default Google Fonts referenced by `theme` are loaded via a
 * `<link rel="stylesheet" data-a2ui-theme-fonts>` in `doc.head`.
 */
export function ensureThemeFonts(theme = {}, doc = document) {
  if (!doc) return null;
  const head = doc.head || doc.documentElement;
  if (!head) return null;
  const families = extractGoogleFontFamilies(theme);
  if (!families.length) return null;
  const href = buildGoogleFontsUrl(families);
  const key = families.map((f) => f.toLowerCase()).sort().join('|');
  let link = doc.querySelector('link[data-a2ui-theme-fonts]');
  if (link && link.getAttribute('data-a2ui-theme-fonts') === key) {
    return link;
  }
  if (!link) {
    link = doc.createElement('link');
    link.rel = 'stylesheet';
    head.appendChild(link);
  }
  link.setAttribute('data-a2ui-theme-fonts', key);
  link.__a2uiLoaded = false;
  link.__a2uiLoadPromise = new Promise((resolve) => {
    const done = () => {
      link.__a2uiLoaded = true;
      resolve();
    };
    link.addEventListener('load', done, {once: true});
    link.addEventListener('error', done, {once: true});
  });
  link.href = href;
  return link;
}

/**
 * Waits for dynamically injected theme font stylesheets and font faces to load
 * (bounded by `timeoutMs`) so headless screenshots never capture fallback
 * fonts.
 */
export async function waitForThemeFonts(
    theme = {}, doc = document, timeoutMs = 2500) {
  if (!doc) return;
  const t = resolveTheme(theme);
  const families = extractGoogleFontFamilies(t);
  const link = ensureThemeFonts(t, doc);
  const work = async () => {
    if (link && link.__a2uiLoadPromise && !link.__a2uiLoaded) {
      await link.__a2uiLoadPromise;
    }
    if (doc.fonts) {
      if (typeof doc.fonts.load === 'function' && families.length) {
        const weights = new Set([
          400,
          500,
          700,
          Number(t.body_weight) || 400,
          Number(t.heading_weight) || 500,
        ]);
        const loads = [];
        for (const fam of families) {
          for (const w of weights) {
            loads.push(doc.fonts.load(`${w} 16px "${fam}"`).catch(() => {}));
          }
        }
        await Promise.all(loads);
      }
      if (doc.fonts.ready) {
        await doc.fonts.ready.catch(() => {});
      }
    }
  };
  await Promise.race([
    work(),
    new Promise((resolve) => setTimeout(resolve, timeoutMs)),
  ]);
}

/** Injects a2ui.css + Google Fonts links into `doc` if missing. */
export function ensureAssets(doc = document) {
  const head = doc.head || doc.documentElement;
  const have =
      (needle) => [...doc.querySelectorAll('link[rel="stylesheet"]')].some(
          (l) => (l.href || '').includes(needle));
  const add = (href) => {
    const l = doc.createElement('link');
    l.rel = 'stylesheet';
    l.href = href;
    head.appendChild(l);
  };
  if (!have('a2ui.css')) add(new URL('./a2ui.css', import.meta.url).href);
  if (!have('Google+Sans+Flex')) add(FONT_URLS[0]);
  if (!have('Material+Symbols+Rounded')) add(FONT_URLS[1]);
}

// ---------------------------------------------------------------------------
// Mount.

const VALUE_KEY = Symbol('a2uiLast');

/**
 * Mounts A2UI surfaces into `container`.
 * opts: {theme, onAction(payload), interactive=true, framing:'card'|'page',
 *        locale='en-US', injectAssets=true, onError(msg),
 *        customComponents: {Name: Definition}}
 * Custom components (web-native HTML/CSS, see custom.js) render as
 * <div class="a2ui-custom" data-a2ui-custom="Name"> with an open shadow root.
 */
export function mountSurface(container, messages = [], opts = {}) {
  const doc = container.ownerDocument || document;
  if (opts.injectAssets !== false) {
    try {
      ensureAssets(doc);
    } catch {
    }
  }
  const theme = resolveTheme(opts.theme || {});
  const interactive = opts.interactive !== false;
  applyTheme(container, theme);
  const host = doc.createElement('div');
  host.className = [
    'a2ui-root',
    `a2ui-scheme-${theme.scheme}`,
    `a2ui-framing-${opts.framing === 'page' ? 'page' : 'card'}`,
    interactive ? 'a2ui-interactive' : 'a2ui-inert',
  ].join(' ');
  container.appendChild(host);
  // The stylesheet declares defaults on .a2ui-root, so the theme must be set
  // on the host itself (inline wins) as well as on the container.
  applyTheme(host, theme);

  const store = new SurfaceStore();
  const views = new Map();
  // Compile each custom definition once per mount.
  const custom = new Map();
  const defs = opts.customComponents;
  if (defs && typeof defs === 'object') {
    for (const [name, def] of Object.entries(defs)) {
      if (!name || BASIC_COMPONENTS.has(name)) continue;
      try {
        custom.set(name, compileCustomComponent(def));
      } catch (e) {
        custom.set(name, {ok: false, errors: [String(e.message || e)]});
      }
    }
  }
  const env = {
    doc,
    interactive,
    custom,
    locale: opts.locale || 'en-US',
    onAction: typeof opts.onAction === 'function' ? opts.onAction : null,
    openUrl: (url) => {
      if (typeof opts.onOpenUrl === 'function') return opts.onOpenUrl(url);
      try {
        doc.defaultView.open(url, '_blank', 'noopener,noreferrer');
      } catch {
      }
    },
  };

  const apply = (msgs) => {
    const ch = store.process(msgs);
    for (const id of ch.deleted) {
      const v = views.get(id);
      if (v) {
        v.destroy();
        views.delete(id);
      }
    }
    for (const [id, state] of store.surfaces) {
      let v = views.get(id);
      if (!v) {
        v = new SurfaceView(state, env);
        views.set(id, v);
        ch.structure.add(id);
      }
      if (ch.structure.has(id))
        v.rebuild();
      else if (ch.data.has(id))
        v.refresh();
    }
    // Keep DOM order == surface creation order.
    for (const id of store.surfaces.keys()) host.appendChild(views.get(id).el);
  };

  apply(messages);

  const api = {
    host,
    store,
    update(more) {
      apply(more);
      return api;
    },
    get meta() {
      const unsupported = new Set();
      const customNames = new Set();
      const errors = [...store.errors];
      let missingImages = 0;
      let components = 0;
      for (const [id, v] of views) {
        components += v.state.components.size;
        v.unsupported.forEach((n) => unsupported.add(n));
        v.custom.forEach((n) => customNames.add(n));
        missingImages += v.missingImages.size;
        for (const e of v.errors)
          errors.push(views.size > 1 ? `[${id}] ${e}` : e);
        if (!v.state.hasRoot)
          errors.push(`surface "${id}" has no "root" component`);
      }
      return {
        surfaces: views.size,
        components,
        unsupported: [...unsupported],
        custom: [...customNames],
        missingImages,
        errors: [...new Set(errors)],
        warnings: [...store.warnings],
      };
    },
    /** Resolves after fonts + images settle (or `capMs`). */
    settled(capMs = 4000) {
      const waits = [
        waitForThemeFonts(theme, doc, Math.min(capMs, 2500)).catch(() => {}),
      ];
      if (doc.fonts && doc.fonts.ready)
        waits.push(doc.fonts.ready.catch(() => {}));
      const imgs = [...host.querySelectorAll('img')];
      // Custom components keep their images inside open shadow roots.
      for (const c of host.querySelectorAll('.a2ui-custom')) {
        if (c.shadowRoot) imgs.push(...c.shadowRoot.querySelectorAll('img'));
      }
      for (const img of imgs) {
        if (img.complete) continue;
        waits.push(new Promise((r) => {
          img.addEventListener('load', r, {once: true});
          img.addEventListener('error', r, {once: true});
        }));
      }
      const cap = new Promise((r) => setTimeout(r, capMs));
      // Two frames so error handlers can swap in placeholders before layout.
      return Promise.race([Promise.all(waits), cap])
          .then(
              () => new Promise(
                  (r) =>
                      requestAnimationFrame(() => requestAnimationFrame(r))));
    },
    getData(surfaceId) {
      const s = surfaceId ? store.surfaces.get(surfaceId) :
                            store.surfaces.values().next().value;
      return s ? s.data : undefined;
    },
    destroy() {
      for (const v of views.values()) v.destroy();
      views.clear();
      host.remove();
    },
  };
  return api;
}

// ---------------------------------------------------------------------------
// Surface view.

const JUSTIFY = {
  start: 'flex-start',
  center: 'center',
  end: 'flex-end',
  spaceBetween: 'space-between',
  spaceAround: 'space-around',
  spaceEvenly: 'space-evenly',
  stretch: 'stretch',
};
const ALIGN = {
  start: 'flex-start',
  center: 'center',
  end: 'flex-end',
  stretch: 'stretch'
};

class SurfaceView {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this.doc = env.doc;
    this.el = this.doc.createElement('div');
    this.el.className = 'a2ui-surface';
    this.el.dataset.surfaceId = state.surfaceId;
    this.uiState =
        new Map();  // per-instance local UI state (tabs, modal, touched...)
    this.updaters = [];
    this.templates = [];
    this.errors = [];
    this.unsupported = new Set();
    this.custom = new Set();
    this.missingImages = new Set();
    this.refreshing = false;
  }

  destroy() {
    this.el.remove();
    this.updaters = [];
  }

  ctx(scope) {
    return {
      getData: (p) => this.state.getData(p),
      scope,
      locale: this.env.locale,
      errors: this.errors,
      openUrl: this.env.openUrl,
    };
  }

  resolve(value, scope) {
    return resolveDynamic(value, this.ctx(scope));
  }

  /** Registers `fn` to run now and on every data refresh. */
  bind(fn) {
    try {
      fn();
    } catch (e) {
      this.err(`binding: ${e.message}`);
    }
    this.updaters.push(fn);
  }

  err(msg) {
    if (!this.errors.includes(msg)) this.errors.push(msg);
  }

  write(path, scope, value) {
    this.state.setData(resolvePath(scope, path), value);
    this.refresh();
  }

  rebuild() {
    const focus = this.captureFocus();
    this.updaters = [];
    this.templates = [];
    this.errors = [];
    this.unsupported = new Set();
    this.custom = new Set();
    this.missingImages = new Set();
    const th = this.state.theme || {};
    if (typeof th.primaryColor === 'string' && hexToRgb(th.primaryColor)) {
      this.el.style.setProperty('--a2ui-primary', th.primaryColor);
      this.el.style.setProperty(
          '--a2ui-on-primary', onColorFor(th.primaryColor));
    } else {
      this.el.style.removeProperty('--a2ui-primary');
      this.el.style.removeProperty('--a2ui-on-primary');
    }
    const kids = [];
    if (th.agentDisplayName || th.iconUrl) kids.push(this.buildAttribution(th));
    if (this.state.hasRoot) {
      const node = this.build('root', '', new Set(), null);
      if (node) kids.push(node);
    }
    this.el.replaceChildren(...kids);
    this.restoreFocus(focus);
  }

  refresh() {
    if (this.refreshing) return;
    this.refreshing = true;
    try {
      for (const t of this.templates) {
        if (templateSignature(t.ref, t.scope, (p) => this.state.getData(p)) !==
            t.sig) {
          this.rebuild();
          return;
        }
      }
      for (const u of this.updaters) {
        try {
          u();
        } catch (e) {
          this.err(`binding: ${e.message}`);
        }
      }
    } finally {
      this.refreshing = false;
    }
  }

  captureFocus() {
    const a = this.doc.activeElement;
    if (!a || !this.el.contains(a) || !a.dataset || !a.dataset.a2uiKey)
      return null;
    let sel = null;
    try {
      sel = [a.selectionStart, a.selectionEnd];
    } catch {
    }
    return {key: a.dataset.a2uiKey, sel};
  }

  restoreFocus(f) {
    if (!f) return;
    const esc = (s) =>
        (globalThis.CSS && CSS.escape ? CSS.escape(s) : s.replace(/"/g, '\\"'));
    const el = this.el.querySelector(`[data-a2ui-key="${esc(f.key)}"]`);
    if (!el) return;
    el.focus({preventScroll: true});
    if (f.sel && f.sel[0] != null) {
      try {
        el.setSelectionRange(f.sel[0], f.sel[1]);
      } catch {
      }
    }
  }

  h(tag, cls, text) {
    const el = this.doc.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  }

  buildAttribution(th) {
    const row = this.h('div', 'a2ui-attribution');
    if (th.iconUrl) {
      const safe = safeHttpUrl(th.iconUrl);
      if (safe) {
        const img = this.h('img', 'a2ui-attribution-icon');
        img.alt = '';
        img.src = safe;
        img.addEventListener('error', () => img.remove(), {once: true});
        row.append(img);
      }
    }
    if (th.agentDisplayName)
      row.append(this.h('span', '', String(th.agentDisplayName)));
    return row;
  }

  // -- tree ---------------------------------------------------------------

  build(id, scope, stack, parentType) {
    const comp = this.state.components.get(id);
    if (!comp) {
      this.err(`missing component "${id}"`);
      return null;
    }
    const key = `${id}|${scope}`;
    if (stack.has(key) || stack.size > 64) {
      this.err(`cycle or excessive depth at "${id}"`);
      return null;
    }
    stack.add(key);
    let el;
    try {
      el = this.buildComponent(comp, scope, stack);
    } catch (e) {
      this.err(`render ${comp.component || '?'} "${id}": ${e.message}`);
      el = this.h(
          'div', 'a2ui-error-box',
          `${comp.component || 'Component'} failed to render`);
    }
    stack.delete(key);
    if (!el) return null;
    el.dataset.a2uiId = id;
    if (scope) el.dataset.a2uiScope = scope;
    if (comp.weight != null &&
        (parentType === 'Row' || parentType === 'Column')) {
      const w = Number(comp.weight);
      if (Number.isFinite(w) && w > 0) {
        el.style.flex = `${w} 1 0`;
        el.classList.add('a2ui-weighted');
      }
    }
    const acc = comp.accessibility;
    if (acc && typeof acc === 'object') {
      this.bind(() => {
        const label = acc.label != null ?
            toDisplayString(this.resolve(acc.label, scope)) :
            '';
        const desc = acc.description != null ?
            toDisplayString(this.resolve(acc.description, scope)) :
            '';
        const target =
            el.querySelector(':scope > input, :scope > textarea') || el;
        if (label) target.setAttribute('aria-label', label);
        if (desc) target.setAttribute('aria-description', desc);
      });
    }
    return el;
  }

  /** Builds children for a ChildList / single child ref. */
  buildChildren(ref, scope, stack, parentType) {
    if (ref && typeof ref === 'object' && !Array.isArray(ref)) {
      const getData = (p) => this.state.getData(p);
      this.templates.push(
          {ref, scope, sig: templateSignature(ref, scope, getData)});
    }
    const out = [];
    for (const ch of expandChildList(
             ref, scope, (p) => this.state.getData(p))) {
      const el = this.build(ch.id, ch.scope, stack, parentType);
      if (el) out.push(el);
    }
    return out;
  }

  buildComponent(comp, scope, stack) {
    const type = comp.component;
    const fn = this[`c${type}`];
    if (BASIC_COMPONENTS.has(type) && fn)
      return fn.call(this, comp, scope, stack);
    const def = !BASIC_COMPONENTS.has(type) && this.env.custom &&
        this.env.custom.get(type);
    if (def) return this.cCustom(comp, scope, stack, def);
    return this.cUnsupported(comp, scope, stack);
  }

  /**
   * Custom catalog component: host div (light DOM, carries data-a2ui-id) +
   * open shadow root with the sanitized CSS and template. Slot props build
   * their children with the normal child pipeline and append them to the
   * host with slot="<prop>".
   */
  cCustom(comp, scope, stack, def) {
    const name = comp.component;
    if (!def.ok) {
      this.err(`custom component "${name}": ${
          (def.errors || []).slice(0, 3).join('; ') || 'invalid definition'}`);
      return this.cUnsupported(comp, scope, stack);
    }
    const host = this.h('div', 'a2ui-custom');
    host.dataset.a2uiCustom = name;
    let root = host;
    try {
      root = host.attachShadow({mode: 'open'});
    } catch {
      // No shadow DOM (very old engines): render inline, unscoped.
    }
    const style = this.h('style');
    style.textContent = def.styleText;
    root.appendChild(style);
    for (const slotName of def.slots || []) {
      const ref = comp[slotName];
      if (ref == null) continue;
      for (const child of this.buildChildren(ref, scope, stack, 'Custom')) {
        child.setAttribute('slot', slotName);
        host.appendChild(child);
      }
    }
    const key = `${comp.id}|${scope}`;
    const inst = renderCustomTemplate(def, root, {
      doc: this.doc,
      prop: (p) => {
        const raw = comp[p];
        if (raw === undefined)
          return def.defaults ? def.defaults[p] : undefined;
        return this.resolve(raw, scope);
      },
      propPath: (p) =>
          isPathBinding(comp[p]) ? resolvePath(scope, comp[p].path) : null,
      resolveIn: (value, itemScope) => this.resolve(value, itemScope ?? scope),
      icon: (n) => iconGlyph(n),
      image: (img) => {
        img.decoding = 'async';
        img.draggable = false;
        img.addEventListener('error', () => this.missingImages.add(key));
      },
      action: (el, prop, itemScope, resolvedAction) => {
        const action =
            resolvedAction !== undefined ? resolvedAction : comp[prop];
        if (!action || typeof action !== 'object') return false;
        el.setAttribute('role', 'button');
        el.tabIndex = this.env.interactive ? 0 : -1;
        const fire = (e) => {
          if (!this.env.interactive || fromNestedControl(e, el)) return;
          this.dispatchAction({...comp, action}, itemScope ?? scope);
        };
        el.addEventListener('click', fire);
        el.addEventListener('keydown', (e) => {
          if (e.key !== 'Enter' && e.key !== ' ') return;
          if (fromNestedControl(e, el)) return;
          e.preventDefault();
          fire(e);
        });
        return true;
      },
    });
    this.bind(() => inst.update());
    this.custom.add(name);
    return host;
  }

  // -- components ---------------------------------------------------------

  cText(comp, scope) {
    const VARIANT_ALIASES = {
      display: 'h1',
      headline: 'h2',
      title: 'h3',
      subtitle: 'h4',
      subhead: 'h4',
      h6: 'h5',
      label: 'caption',
      overline: 'caption',
      code: 'mono',
      monospace: 'mono',
    };
    const rawVariant = String(comp.variant || 'body').trim();
    const mapped = VARIANT_ALIASES[rawVariant] || rawVariant;
    const variant =
        /^(h[1-5]|caption|body|mono)$/.test(mapped) ? mapped : 'body';
    const isHeading = variant[0] === 'h';
    const el =
        this.h(isHeading ? variant : 'div', `a2ui-Text a2ui-v-${variant}`);
    this.bind(() => {
      const s = toDisplayString(this.resolve(comp.text, scope));
      if (el[VALUE_KEY] === s) return;
      el[VALUE_KEY] = s;
      this.renderMarkdown(el, s, isHeading);
    });
    return el;
  }

  renderMarkdown(el, text, isHeading) {
    const blocks = parseMarkdown(text);
    el.replaceChildren();
    if (!blocks.length) return;
    if (blocks.length === 1 &&
        (blocks[0].type === 'p' || (blocks[0].type === 'h' && isHeading))) {
      this.appendInlines(el, blocks[0].inlines);
      return;
    }
    for (const b of blocks) {
      let node;
      switch (b.type) {
        case 'p':
          node = this.h('p', 'a2ui-md-p');
          this.appendInlines(node, b.inlines);
          break;
        case 'h':
          node = this.h('div', `a2ui-md-h a2ui-md-h${b.level}`);
          this.appendInlines(node, b.inlines);
          break;
        case 'ul':
        case 'ol':
          node = this.h(b.type, 'a2ui-md-list');
          if (b.type === 'ol' && b.start !== 1) node.start = b.start;
          for (const it of b.items) {
            const li = this.h('li');
            this.appendInlines(li, it);
            node.append(li);
          }
          break;
        case 'quote':
          node = this.h('blockquote', 'a2ui-md-quote');
          this.appendInlines(node, b.inlines);
          break;
        case 'code':
          node = this.h('pre', 'a2ui-md-pre');
          node.append(this.h('code', '', b.text));
          break;
        case 'hr':
          node = this.h('hr', 'a2ui-md-hr');
          break;
        default:
          continue;
      }
      el.append(node);
    }
  }

  appendInlines(parent, nodes) {
    for (const n of nodes) {
      switch (n.t) {
        case 'text':
          parent.append(this.doc.createTextNode(n.v));
          break;
        case 'br':
          parent.append(this.h('br'));
          break;
        case 'code':
          parent.append(this.h('code', 'a2ui-md-code', n.v));
          break;
        case 'strong':
        case 'em':
        case 'del':
        case 'span': {
          const e = this.h(n.t);
          this.appendInlines(e, n.c);
          parent.append(e);
          break;
        }
        case 'link': {
          const a = this.h('a', 'a2ui-md-link');
          a.href = n.href;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          if (!this.env.interactive) a.tabIndex = -1;
          this.appendInlines(a, n.c);
          parent.append(a);
          break;
        }
        default:
          break;
      }
    }
  }

  cImage(comp, scope) {
    const variants = [
      'icon', 'avatar', 'smallFeature', 'mediumFeature', 'largeFeature',
      'header'
    ];
    const variant =
        variants.includes(comp.variant) ? comp.variant : 'mediumFeature';
    const fits = {
      contain: 'contain',
      cover: 'cover',
      fill: 'fill',
      none: 'none',
      scaleDown: 'scale-down'
    };
    const el = this.h('div', `a2ui-Image a2ui-v-${variant}`);
    const key = `${comp.id}|${scope}`;
    const img = this.h('img');
    img.decoding = 'async';
    img.draggable = false;
    // Explicit fit wins; otherwise crop to the variant's box (cover).
    img.style.objectFit = fits[comp.fit] || 'cover';
    const markMissing = () => {
      this.missingImages.add(key);
      el.classList.add('a2ui-missing');
      if (!el.querySelector('.a2ui-placeholder-glyph')) {
        const g = this.h('span', 'a2ui-placeholder-glyph a2ui-symbol', 'image');
        g.setAttribute('aria-hidden', 'true');
        el.append(g);
      }
    };
    const clearMissing = () => {
      this.missingImages.delete(key);
      el.classList.remove('a2ui-missing');
      el.querySelector('.a2ui-placeholder-glyph')?.remove();
    };
    img.addEventListener('error', markMissing);
    img.addEventListener('load', clearMissing);
    el.append(img);
    this.bind(() => {
      const url = toDisplayString(this.resolve(comp.url, scope)).trim();
      const alt = comp.description != null ?
          toDisplayString(this.resolve(comp.description, scope)) :
          '';
      img.alt = alt;
      if (alt) el.title = alt;
      if (img[VALUE_KEY] === url) return;
      img[VALUE_KEY] = url;
      if (!url || /^(javascript|vbscript):/i.test(url)) {
        img.removeAttribute('src');
        markMissing();
      } else {
        img.src = url;
      }
    });
    return el;
  }

  cIcon(comp, scope) {
    const el = this.h('span', 'a2ui-Icon');
    if (typeof comp.size === 'number' && Number.isFinite(comp.size)) {
      const sz = Math.max(12, Math.min(96, Math.round(comp.size)));
      el.style.setProperty('--a2ui-icon-size', `${sz}px`);
      el.style.width = `${sz}px`;
      el.style.height = `${sz}px`;
      el.style.fontSize = `${sz}px`;
    }
    el.setAttribute(
        'aria-hidden',
        comp.accessibility && comp.accessibility.label ? 'false' : 'true');
    const key = `icon:${comp.id}|${scope}`;
    this.bind(() => {
      let name = comp.name;
      if (isPathBinding(name) ||
          (name && typeof name === 'object' && name.call))
        name = this.resolve(name, scope);
      let resolvedUrl = null;
      if (name && typeof name === 'object' && name.url != null) {
        resolvedUrl = toDisplayString(this.resolve(name.url, scope)).trim();
      } else if (
          typeof name === 'string' &&
          /^(https?:\/\/|\/|data:image\/)/i.test(name.trim())) {
        resolvedUrl = name.trim();
      } else if (
          name && typeof name === 'object' && typeof name.svg === 'string' &&
          name.svg.trim()) {
        const clean =
            name.svg
                .replace(
                    /<\s*(script|foreignObject|iframe)\b[^>]*>[\s\S]*?<\s*\/\s*\1\s*>/gi,
                    '')
                .replace(/\bon[a-z]+\s*=\s*(['"]).*?\1/gi, '');
        resolvedUrl = `data:image/svg+xml;utf8,${encodeURIComponent(clean)}`;
      }
      const sig =
          resolvedUrl != null ? `url:${resolvedUrl}` : JSON.stringify(name);
      if (el[VALUE_KEY] === sig) return;
      el[VALUE_KEY] = sig;
      el.replaceChildren();
      this.missingImages.delete(key);
      if (resolvedUrl != null) {
        el.classList.remove('a2ui-symbol');
        el.classList.add('a2ui-Icon-custom');
        const img = this.h('img', 'a2ui-icon-img');
        img.decoding = 'async';
        img.draggable = false;
        img.alt = '';
        img.addEventListener('error', () => this.missingImages.add(key));
        img.addEventListener('load', () => this.missingImages.delete(key));
        if (!resolvedUrl || /^(javascript|vbscript):/i.test(resolvedUrl)) {
          this.missingImages.add(key);
        } else {
          img.src = resolvedUrl;
          img.setAttribute('src', resolvedUrl);
        }
        el.append(img);
      } else if (
          name && typeof name === 'object' &&
          typeof name.svgPath === 'string') {
        const NS = 'http://www.w3.org/2000/svg';
        const svg = this.doc.createElementNS(NS, 'svg');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('class', 'a2ui-icon-svg');
        const p = this.doc.createElementNS(NS, 'path');
        p.setAttribute('d', name.svgPath);
        p.setAttribute('fill', 'currentColor');
        svg.append(p);
        el.append(svg);
        el.classList.remove('a2ui-symbol', 'a2ui-Icon-custom');
      } else {
        const g = iconGlyph(typeof name === 'string' ? name : '');
        el.classList.remove('a2ui-Icon-custom');
        el.classList.add('a2ui-symbol');
        el.textContent = g.glyph;
        el.style.setProperty('--a2ui-icon-fill', String(g.fill));
        if (!g.known && name) this.err(`unknown icon name "${name}"`);
      }
    });
    return el;
  }

  cVideo(comp, scope) {
    const el = this.h('div', 'a2ui-Video');
    const v = this.h('video');
    v.controls = true;
    v.preload = 'metadata';
    v.playsInline = true;
    const ph = this.h('div', 'a2ui-media-placeholder');
    ph.append(this.h('span', 'a2ui-symbol', 'play_circle'));
    el.append(v, ph);
    v.addEventListener('loadeddata', () => el.classList.add('a2ui-loaded'));
    v.addEventListener('error', () => el.classList.add('a2ui-failed'));
    this.bind(() => {
      const url = toDisplayString(this.resolve(comp.url, scope)).trim();
      if (v[VALUE_KEY] === url) return;
      v[VALUE_KEY] = url;
      const safe = url ? safeHttpUrl(url) : null;
      if (safe)
        v.src = safe;
      else {
        v.removeAttribute('src');
        el.classList.add('a2ui-failed');
      }
    });
    return el;
  }

  cAudioPlayer(comp, scope) {
    const el = this.h('div', 'a2ui-AudioPlayer');
    const head = this.h('div', 'a2ui-audio-head');
    const icon = this.h('span', 'a2ui-symbol a2ui-audio-icon', 'graphic_eq');
    icon.setAttribute('aria-hidden', 'true');
    const desc = this.h('div', 'a2ui-audio-desc');
    head.append(icon, desc);
    const a = this.h('audio');
    a.controls = true;
    a.preload = 'none';
    el.append(head, a);
    this.bind(() => {
      const d = comp.description != null ?
          toDisplayString(this.resolve(comp.description, scope)) :
          '';
      desc.textContent = d;
      head.hidden = !d;
      const url = toDisplayString(this.resolve(comp.url, scope)).trim();
      if (a[VALUE_KEY] === url) return;
      a[VALUE_KEY] = url;
      const safe = url ? safeHttpUrl(url) : null;
      if (safe)
        a.src = safe;
      else
        a.removeAttribute('src');
    });
    return el;
  }

  layout(cls, comp, scope, stack, type) {
    const el = this.h('div', cls);
    const justify = JUSTIFY[comp.justify] ? comp.justify : 'start';
    const align = ALIGN[comp.align] ? comp.align : 'stretch';
    el.classList.add(`a2ui-justify-${justify}`, `a2ui-align-${align}`);
    el.style.justifyContent =
        JUSTIFY[justify] === 'stretch' ? 'flex-start' : JUSTIFY[justify];
    el.style.alignItems = ALIGN[align];
    el.append(...this.buildChildren(comp.children, scope, stack, type));
    return el;
  }

  cRow(comp, scope, stack) {
    return this.layout('a2ui-Row', comp, scope, stack, 'Row');
  }
  cColumn(comp, scope, stack) {
    return this.layout('a2ui-Column', comp, scope, stack, 'Column');
  }

  cList(comp, scope, stack) {
    const dir = comp.direction === 'horizontal' ? 'horizontal' : 'vertical';
    const align = ALIGN[comp.align] ? comp.align : 'stretch';
    const el = this.h('div', `a2ui-List a2ui-dir-${dir} a2ui-align-${align}`);
    el.style.alignItems = ALIGN[align];
    el.setAttribute('role', 'list');
    for (const child of this.buildChildren(
             comp.children, scope, stack, 'List')) {
      child.setAttribute('role', 'listitem');
      el.append(child);
    }
    return el;
  }

  cCard(comp, scope, stack) {
    const el = this.h('div', 'a2ui-Card');
    const children = this.buildChildren(comp.child, scope, stack, 'Card');
    if (comp.backgroundImage != null) {
      const bgSpec = comp.backgroundImage;
      const isObj = bgSpec && typeof bgSpec === 'object' &&
          !isPathBinding(bgSpec) && !bgSpec.call;
      const rawUrl = isObj ? bgSpec.url : bgSpec;
      const scrim =
          (isObj && ['auto', 'dark', 'light', 'none'].includes(bgSpec.scrim)) ?
          bgSpec.scrim :
          'auto';
      const fit = (isObj && bgSpec.fit === 'contain') ? 'contain' : 'cover';
      el.classList.add('a2ui-card-has-bg', `a2ui-card-scrim-${scrim}`);
      const img = this.h('img', 'a2ui-card-bg-img');
      img.decoding = 'async';
      img.draggable = false;
      img.alt = '';
      img.style.objectFit = fit;
      const key = `cardbg:${comp.id}|${scope}`;
      img.addEventListener('error', () => {
        this.missingImages.add(key);
        img.style.display = 'none';
      });
      img.addEventListener('load', () => {
        this.missingImages.delete(key);
        img.style.display = '';
      });
      const scrimEl = this.h('div', 'a2ui-card-bg-scrim');
      const contentEl = this.h('div', 'a2ui-card-content');
      contentEl.append(...children);
      el.append(img, scrimEl, contentEl);
      this.bind(() => {
        const url = toDisplayString(this.resolve(rawUrl, scope)).trim();
        if (img[VALUE_KEY] === url) return;
        img[VALUE_KEY] = url;
        if (!url || /^(javascript|vbscript):/i.test(url)) {
          img.removeAttribute('src');
          this.missingImages.add(key);
        } else {
          img.src = url;
          img.setAttribute('src', url);
        }
      });
      return el;
    }
    el.append(...children);
    return el;
  }

  cTabs(comp, scope, stack) {
    const el = this.h('div', 'a2ui-Tabs');
    const bar = this.h('div', 'a2ui-tabbar');
    bar.setAttribute('role', 'tablist');
    const panel = this.h('div', 'a2ui-tabpanel');
    panel.setAttribute('role', 'tabpanel');
    const tabs = (Array.isArray(comp.tabs) ? comp.tabs : [])
                     .filter((t) => t && typeof t === 'object');
    const key = `tabs:${comp.id}|${scope}`;
    let sel = Number(this.uiState.get(key) || 0);
    if (sel >= tabs.length) sel = 0;
    tabs.forEach((t, i) => {
      const b = this.h('button', 'a2ui-tab');
      b.type = 'button';
      b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', String(i === sel));
      if (i === sel) b.classList.add('a2ui-selected');
      this.bind(() => {
        b.textContent = toDisplayString(this.resolve(t.title, scope));
      });
      b.addEventListener('click', () => {
        if (!this.env.interactive || i === sel) return;
        this.uiState.set(key, i);
        this.rebuild();
      });
      bar.append(b);
    });
    if (tabs[sel])
      panel.append(
          ...this.buildChildren(tabs[sel].child, scope, stack, 'Tabs'));
    el.append(bar, panel);
    return el;
  }

  cModal(comp, scope, stack) {
    const el = this.h('div', 'a2ui-Modal');
    const key = `modal:${comp.id}|${scope}`;
    const trigger =
        comp.trigger ? this.build(comp.trigger, scope, stack, 'Modal') : null;
    if (trigger) el.append(trigger);
    const dialog = this.h('dialog', 'a2ui-dialog');
    const sheet = this.h('div', 'a2ui-dialog-sheet');
    const close = this.h('button', 'a2ui-dialog-close a2ui-symbol', 'close');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close');
    const body = this.h('div', 'a2ui-dialog-body');
    sheet.append(close, body);
    dialog.append(sheet);
    el.append(dialog);
    let built = false;
    const contentStack = new Set(stack);
    const open = () => {
      if (!built && comp.content) {
        built = true;
        const c = this.build(comp.content, scope, contentStack, 'Modal');
        if (c) body.append(c);
      }
      this.uiState.set(key, true);
      if (!dialog.open) {
        try {
          dialog.showModal();
        } catch {
          dialog.setAttribute('open', '');
        }
      }
    };
    const shut = () => {
      this.uiState.delete(key);
      if (dialog.open) dialog.close();
    };
    el.addEventListener('click', (e) => {
      if (!this.env.interactive || !trigger || !trigger.contains(e.target))
        return;
      e.preventDefault();
      e.stopPropagation();
      open();
    }, true);
    close.addEventListener('click', shut);
    dialog.addEventListener('click', (e) => {
      if (e.target === dialog) shut();
    });
    dialog.addEventListener('close', () => this.uiState.delete(key));
    if (this.uiState.get(key))
      queueMicrotask(() => {
        if (el.isConnected) open();
      });
    return el;
  }

  cDivider(comp) {
    const axis = comp.axis === 'vertical' ? 'vertical' : 'horizontal';
    const el = this.h('div', `a2ui-Divider a2ui-axis-${axis}`);
    el.setAttribute('role', 'separator');
    el.setAttribute('aria-orientation', axis);
    return el;
  }

  cButton(comp, scope, stack) {
    const variant = ['primary', 'borderless'].includes(comp.variant) ?
        comp.variant :
        'default';
    const btn = this.h('button', `a2ui-Button a2ui-v-${variant}`);
    btn.type = 'button';
    const kids = this.buildChildren(comp.child, scope, stack, 'Button');
    btn.append(...kids);
    const childComp = this.state.components.get(comp.child);
    if (childComp && childComp.component === 'Icon')
      btn.classList.add('a2ui-icon-only');
    if (Array.isArray(comp.checks) && comp.checks.length) {
      this.bind(() => {
        const failing = evaluateChecks(comp.checks, this.ctx(scope));
        btn.disabled = failing.length > 0;
        btn.classList.toggle('a2ui-disabled', failing.length > 0);
        if (failing.length)
          btn.title = failing.join('\n');
        else
          btn.removeAttribute('title');
      });
    }
    btn.addEventListener('click', () => {
      if (!this.env.interactive || btn.disabled) return;
      this.dispatchAction(comp, scope);
    });
    return btn;
  }

  dispatchAction(comp, scope) {
    const action = comp.action;
    if (!action || typeof action !== 'object') return;
    const ctx = this.ctx(scope);
    if (action.functionCall && typeof action.functionCall.call === 'string') {
      callFunction(
          action.functionCall.call, action.functionCall.args || {}, ctx);
      return;
    }
    const ev = action.event;
    if (!ev || typeof ev.name !== 'string') return;
    const payload = {
      version: 'v0.9',
      action: {
        name: ev.name,
        surfaceId: this.state.surfaceId,
        sourceComponentId: comp.id,
        timestamp: new Date().toISOString(),
        context: resolveActionContext(ev.context, ctx),
      },
    };
    if (this.state.sendDataModel) {
      payload.metadata = {
        a2uiClientDataModel:
            {surfaces: {[this.state.surfaceId]: this.state.data}}
      };
    }
    if (this.env.onAction) {
      try {
        this.env.onAction(payload);
      } catch (e) {
        this.err(`onAction: ${e.message}`);
      }
    }
  }

  fieldLabel(comp, scope, forEl) {
    const label = this.h('label', 'a2ui-field-label');
    this.bind(() => {
      const t = comp.label != null ?
          toDisplayString(this.resolve(comp.label, scope)) :
          '';
      label.textContent = t;
      label.hidden = !t;
    });
    if (forEl) {
      const id = `a2ui-f-${Math.random().toString(36).slice(2, 10)}`;
      forEl.id = id;
      label.htmlFor = id;
    }
    return label;
  }

  inertInput(input) {
    if (!this.env.interactive) {
      input.tabIndex = -1;
      input.readOnly = true;
    }
  }

  cTextField(comp, scope) {
    const variant = ['longText', 'number', 'obscured'].includes(comp.variant) ?
        comp.variant :
        'shortText';
    const el = this.h('div', `a2ui-TextField a2ui-v-${variant}`);
    let input;
    if (variant === 'longText') {
      input = this.h('textarea', 'a2ui-input');
      input.rows = 3;
    } else {
      input = this.h('input', 'a2ui-input');
      input.type = variant === 'number' ? 'text' :
          variant === 'obscured'        ? 'password' :
                                          'text';
      if (variant === 'number') input.inputMode = 'decimal';
    }
    input.dataset.a2uiKey = `${comp.id}|${scope}`;
    this.inertInput(input);
    const label = this.fieldLabel(comp, scope, input);
    const help = this.h('div', 'a2ui-field-error');
    help.setAttribute('aria-live', 'polite');
    el.append(label, input, help);
    const tkey = `touched:${comp.id}|${scope}`;
    let rx = null;
    if (typeof comp.validationRegexp === 'string' && comp.validationRegexp) {
      try {
        rx = new RegExp(comp.validationRegexp);
      } catch {
        this.err(`TextField "${comp.id}": invalid validationRegexp`);
      }
    }
    this.bind(() => {
      const v = toDisplayString(this.resolve(comp.value, scope));
      if (input[VALUE_KEY] !== v) {
        input[VALUE_KEY] = v;
        if (input.value !== v) input.value = v;
      }
      const failing = evaluateChecks(comp.checks, this.ctx(scope));
      const cur = input.value;
      if (rx && cur && !rx.test(cur)) failing.unshift('Invalid format');
      const show = failing.length > 0 && (this.uiState.get(tkey) || cur !== '');
      help.textContent = show ? failing[0] : '';
      el.classList.toggle('a2ui-invalid', !!show);
      input.setAttribute('aria-invalid', show ? 'true' : 'false');
    });
    input.addEventListener('input', () => {
      if (!this.env.interactive) return;
      this.uiState.set(tkey, true);
      input[VALUE_KEY] = input.value;
      if (isPathBinding(comp.value))
        this.write(comp.value.path, scope, input.value);
      else
        this.refresh();
    });
    input.addEventListener('blur', () => {
      if (!this.env.interactive || this.uiState.get(tkey)) return;
      this.uiState.set(tkey, true);
      this.refresh();
    });
    return el;
  }

  cCheckBox(comp, scope) {
    const el = this.h('label', 'a2ui-CheckBox');
    const input = this.h('input', 'a2ui-checkbox');
    input.type = 'checkbox';
    input.dataset.a2uiKey = `${comp.id}|${scope}`;
    if (!this.env.interactive) input.tabIndex = -1;
    const text = this.h('span', 'a2ui-checkbox-label');
    const help = this.h('div', 'a2ui-field-error');
    el.append(input, text);
    const wrap = this.h('div', 'a2ui-CheckBox-wrap');
    // Keep the outer element the label so the whole row is clickable.
    this.bind(() => {
      const v = this.resolve(comp.value, scope);
      input.checked = v === true || v === 'true';
      const t = comp.label != null ?
          toDisplayString(this.resolve(comp.label, scope)) :
          '';
      text.textContent = t;
      text.hidden = !t;
      const failing = evaluateChecks(comp.checks, this.ctx(scope));
      const show =
          failing.length && this.uiState.get(`touched:${comp.id}|${scope}`);
      help.textContent = show ? failing[0] : '';
      help.hidden = !show;
    });
    input.addEventListener('change', () => {
      if (!this.env.interactive) {
        input.checked = !input.checked;
        return;
      }
      this.uiState.set(`touched:${comp.id}|${scope}`, true);
      if (isPathBinding(comp.value))
        this.write(comp.value.path, scope, input.checked);
    });
    if (Array.isArray(comp.checks) && comp.checks.length) {
      wrap.append(el, help);
      wrap.classList.add('a2ui-CheckBox');
      el.className = 'a2ui-checkbox-row';
      return wrap;
    }
    return el;
  }

  cChoicePicker(comp, scope) {
    const multi = comp.variant === 'multipleSelection';
    const style = comp.displayStyle === 'chips' ? 'chips' : 'checkbox';
    const el = this.h(
        'div',
        `a2ui-ChoicePicker a2ui-style-${style} a2ui-v-${
            multi ? 'multipleSelection' : 'mutuallyExclusive'}`);
    el.setAttribute('role', multi ? 'group' : 'radiogroup');
    const label = this.h('div', 'a2ui-field-label');
    el.append(label);
    const fkey = `filter:${comp.id}|${scope}`;
    let filter = null;
    if (comp.filterable) {
      filter = this.h('input', 'a2ui-input a2ui-choice-filter');
      filter.type = 'search';
      filter.placeholder = 'Filter options';
      filter.dataset.a2uiKey = `${comp.id}|${scope}|filter`;
      filter.value = this.uiState.get(fkey) || '';
      this.inertInput(filter);
      el.append(filter);
    }
    const list = this.h('div', 'a2ui-choice-options');
    el.append(list);
    const help = this.h('div', 'a2ui-field-error');
    el.append(help);
    const options = (Array.isArray(comp.options) ? comp.options : [])
                        .filter((o) => o && typeof o === 'object');
    const name = `a2ui-c-${Math.random().toString(36).slice(2, 10)}`;
    const current = () => {
      const v = this.resolve(comp.value, scope);
      if (Array.isArray(v)) return v.map(String);
      if (v == null || v === '') return [];
      return [String(v)];
    };
    const setSel = (value, on) => {
      if (!this.env.interactive) return;
      let cur = current();
      if (multi)
        cur =
            on ? [...new Set([...cur, value])] : cur.filter((x) => x !== value);
      else
        cur = on ? [value] : cur;
      this.uiState.set(`touched:${comp.id}|${scope}`, true);
      if (isPathBinding(comp.value)) this.write(comp.value.path, scope, cur);
    };
    const items = options.map((o) => {
      const value = String(o.value ?? '');
      let item, input, text;
      if (style === 'chips') {
        item = this.h('button', 'a2ui-chip');
        item.type = 'button';
        const check = this.h('span', 'a2ui-symbol a2ui-chip-check', 'check');
        check.setAttribute('aria-hidden', 'true');
        text = this.h('span', 'a2ui-chip-label');
        item.append(check, text);
        if (!this.env.interactive) item.tabIndex = -1;
        item.addEventListener(
            'click',
            () => setSel(
                value, item.getAttribute('aria-pressed') !== 'true' || !multi));
      } else {
        item = this.h('label', 'a2ui-option');
        input = this.h('input', multi ? 'a2ui-checkbox' : 'a2ui-radio');
        input.type = multi ? 'checkbox' : 'radio';
        input.name = name;
        if (!this.env.interactive) input.tabIndex = -1;
        text = this.h('span', 'a2ui-option-label');
        item.append(input, text);
        input.addEventListener('change', () => setSel(value, input.checked));
      }
      item.dataset.value = value;
      list.append(item);
      return {o, value, item, input, text};
    });
    const applyFilter = () => {
      const q = (filter ? filter.value : '').trim().toLowerCase();
      for (const it of items)
        it.item.hidden = !!q && !it.text.textContent.toLowerCase().includes(q);
    };
    if (filter) {
      filter.addEventListener('input', () => {
        this.uiState.set(fkey, filter.value);
        applyFilter();
      });
    }
    this.bind(() => {
      const t = comp.label != null ?
          toDisplayString(this.resolve(comp.label, scope)) :
          '';
      label.textContent = t;
      label.hidden = !t;
      const sel = new Set(current());
      for (const it of items) {
        it.text.textContent = toDisplayString(this.resolve(it.o.label, scope));
        const on = sel.has(it.value);
        if (it.input)
          it.input.checked = on;
        else
          it.item.setAttribute('aria-pressed', String(on));
        it.item.classList.toggle('a2ui-selected', on);
      }
      applyFilter();
      const failing = evaluateChecks(comp.checks, this.ctx(scope));
      const show =
          failing.length && this.uiState.get(`touched:${comp.id}|${scope}`);
      help.textContent = show ? failing[0] : '';
      help.hidden = !show;
    });
    return el;
  }

  cSlider(comp, scope) {
    const el = this.h('div', 'a2ui-Slider');
    const head = this.h('div', 'a2ui-slider-head');
    const input = this.h('input', 'a2ui-range');
    input.type = 'range';
    input.dataset.a2uiKey = `${comp.id}|${scope}`;
    if (!this.env.interactive) input.tabIndex = -1;
    const label = this.fieldLabel(comp, scope, input);
    const out = this.h('span', 'a2ui-slider-value');
    head.append(label, out);
    const help = this.h('div', 'a2ui-field-error');
    el.append(head, input, help);
    const min = Number.isFinite(Number(comp.min)) ? Number(comp.min) : 0;
    let max = Number.isFinite(Number(comp.max)) ? Number(comp.max) : 100;
    if (max <= min) max = min + 1;
    const intRange =
        Number.isInteger(min) && Number.isInteger(max) && max - min >= 10;
    input.min = String(min);
    input.max = String(max);
    input.step = intRange ? '1' : 'any';
    const fmt = (n) =>
        (intRange ? String(Math.round(n)) : String(Math.round(n * 100) / 100));
    const paint = (n) => {
      const pct = Math.max(0, Math.min(100, ((n - min) / (max - min)) * 100));
      input.style.setProperty('--a2ui-fill', `${pct}%`);
      out.textContent = fmt(n);
    };
    this.bind(() => {
      let n = Number(this.resolve(comp.value, scope));
      if (!Number.isFinite(n)) n = min;
      if (input[VALUE_KEY] !== n) {
        input[VALUE_KEY] = n;
        input.value = String(n);
      }
      paint(Number(input.value));
      const failing = evaluateChecks(comp.checks, this.ctx(scope));
      help.textContent =
          failing.length && this.uiState.get(`touched:${comp.id}|${scope}`) ?
          failing[0] :
          '';
    });
    input.addEventListener('input', () => {
      if (!this.env.interactive) return;
      const n = Number(input.value);
      input[VALUE_KEY] = n;
      paint(n);
      this.uiState.set(`touched:${comp.id}|${scope}`, true);
      if (isPathBinding(comp.value)) this.write(comp.value.path, scope, n);
    });
    return el;
  }

  cDateTimeInput(comp, scope) {
    let date = comp.enableDate === true;
    let time = comp.enableTime === true;
    if (!date && !time) {
      date = true;
      time = true;
    }
    const type = date && time ? 'datetime-local' : date ? 'date' : 'time';
    const el = this.h('div', `a2ui-DateTimeInput a2ui-type-${type}`);
    const input = this.h('input', 'a2ui-input');
    input.type = type;
    input.dataset.a2uiKey = `${comp.id}|${scope}`;
    this.inertInput(input);
    const label = this.fieldLabel(comp, scope, input);
    const help = this.h('div', 'a2ui-field-error');
    el.append(label, input, help);
    const p2 = (n) => String(n).padStart(2, '0');
    const toInput = (v) => {
      const d = parseDate(v);
      if (!d) return '';
      const ds =
          `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
      const ts = `${p2(d.getHours())}:${p2(d.getMinutes())}`;
      return type === 'date' ? ds : type === 'time' ? ts : `${ds}T${ts}`;
    };
    const fromInput = (s) =>
        (!s ? '' :
             type === 'datetime-local' ? `${s}:00` :
                                         s);
    this.bind(() => {
      const v = toInput(this.resolve(comp.value, scope));
      if (input[VALUE_KEY] !== v) {
        input[VALUE_KEY] = v;
        if (input.value !== v) input.value = v;
      }
      const mn = comp.min != null ? toInput(this.resolve(comp.min, scope)) : '';
      const mx = comp.max != null ? toInput(this.resolve(comp.max, scope)) : '';
      if (mn)
        input.min = mn;
      else
        input.removeAttribute('min');
      if (mx)
        input.max = mx;
      else
        input.removeAttribute('max');
      const failing = evaluateChecks(comp.checks, this.ctx(scope));
      help.textContent =
          failing.length && this.uiState.get(`touched:${comp.id}|${scope}`) ?
          failing[0] :
          '';
    });
    input.addEventListener('change', () => {
      if (!this.env.interactive) return;
      input[VALUE_KEY] = input.value;
      this.uiState.set(`touched:${comp.id}|${scope}`, true);
      if (isPathBinding(comp.value))
        this.write(comp.value.path, scope, fromInput(input.value));
    });
    return el;
  }

  cUnsupported(comp, scope, stack) {
    const name = typeof comp.component === 'string' && comp.component ?
        comp.component :
        '(missing component type)';
    this.unsupported.add(name);
    const el = this.h('div', 'a2ui-unsupported');
    const head = this.h('div', 'a2ui-unsupported-label');
    const g = this.h('span', 'a2ui-symbol', 'extension');
    g.setAttribute('aria-hidden', 'true');
    head.append(g, this.h('span', '', name));
    el.append(head);
    const label = comp.label ?? comp.text ?? comp.title;
    if (label != null && typeof label !== 'object' || isPathBinding(label)) {
      const t = this.h('div', 'a2ui-unsupported-text');
      this.bind(() => {
        t.textContent = toDisplayString(this.resolve(label, scope));
      });
      el.append(t);
    }
    const kids = [];
    for (const {ref} of childRefs(comp))
      kids.push(...this.buildChildren(ref, scope, stack, 'Column'));
    if (kids.length) {
      const body = this.h('div', 'a2ui-unsupported-children');
      body.append(...kids);
      el.append(body);
    }
    return el;
  }
}
