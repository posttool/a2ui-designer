// Design tokens for the A2UI Designer renderer host.
//
// Canonical token set (mirrors core/settings.py DEFAULT_THEME) and helpers to
// turn a theme into CSS custom properties. Two families of variables are
// produced by themeToCssVars():
//   * our canonical `--a2ui-*` tokens (used by custom component templates,
//     the theme editor's CSS view and the exported tokens.css), and
//   * the `--a2ui-*` variables read by the official @a2ui/web_core basic
//     catalog elements (see officialCssVars below), derived from the same
//     tokens so the unmodified library picks up the design system.

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
  // Official basic-catalog variables come last only where they don't clash
  // with a custom token of the same name (designers may override them).
  for (const [k, v] of Object.entries(officialCssVars(t))) {
    if (!(k in vars)) vars[k] = v;
  }
  return vars;
}

const num = (v, d) => {
  const n = Number(String(v ?? '').replace(/px$/i, ''));
  return Number.isFinite(n) ? n : d;
};
const r1 = (n) => Math.round(n * 10) / 10;

/**
 * Canonical tokens -> the CSS variables the official @a2ui/web_core basic
 * catalog elements read (universal/basic_catalog/styles/default.js and each
 * component's documented `--a2ui-<component>-*` variables). Values are plain
 * colors/lengths so they work without `light-dark()` support.
 */
export function officialCssVars(theme = {}) {
  const t = resolveTheme(theme);
  const ts = num(t.type_scale, 1) > 0 ? num(t.type_scale, 1) : 1;
  const hs = num(t.heading_scale, 1) > 0 ? num(t.heading_scale, 1) : 1;
  const space = Math.max(0, num(t.space, 8));
  const ctrl = Math.max(24, num(t.control_height, 40));
  const bw = Math.max(0, num(t.border_width, 1));
  const elev = Math.max(0, num(t.card_elevation, 0));
  const dark = t.scheme === 'dark';
  const shadow = elev > 0 ?
      `0 ${Math.round(elev * 1.5)}px ${Math.round(elev * 4)}px rgba(0, 0, 0, ${
          dark ? 0.42 : 0.12})` :
      'none';
  const fs = 16 * ts;
  const bodyStack = fontStack(t.font_family);
  const headingStack = t.font_family_heading && String(t.font_family_heading).trim() ?
      fontStack(t.font_family_heading) :
      bodyStack;
  const monoStack = fontStack(
      t.font_family_mono || 'Google Sans Code',
      '"Roboto Mono", ui-monospace, monospace');
  const btnPadV = Math.max(4, Math.round((ctrl - 20) / 2));
  return {
    // Global palette.
    '--a2ui-color-background': t.surface,
    '--a2ui-color-on-background': t.on_surface,
    '--a2ui-color-surface': t.surface_container,
    '--a2ui-color-on-surface': t.on_surface,
    '--a2ui-color-primary': t.primary,
    '--a2ui-color-primary-light':
        `color-mix(in srgb, ${t.primary} 85%, white)`,
    '--a2ui-color-primary-dark': `color-mix(in srgb, ${t.primary} 85%, black)`,
    '--a2ui-color-primary-hover':
        `color-mix(in srgb, ${t.on_primary} 10%, ${t.primary})`,
    '--a2ui-color-on-primary': t.on_primary,
    '--a2ui-color-secondary': t.secondary_container,
    '--a2ui-color-secondary-hover':
        `color-mix(in srgb, ${t.on_surface} 8%, transparent)`,
    '--a2ui-color-on-secondary': t.primary,
    '--a2ui-color-border': t.outline,
    '--a2ui-border-width': `${bw}px`,
    '--a2ui-border': `${bw}px solid ${t.outline}`,
    '--a2ui-border-radius': px(t.radius_control, 12),
    '--a2ui-color-input': t.surface,
    '--a2ui-color-on-input': t.on_surface,
    // Type.
    '--a2ui-font-family-title': headingStack,
    '--a2ui-font-family-monospace': monoStack,
    '--a2ui-font-size': `${r1(fs)}px`,
    '--a2ui-font-size-xs': `${r1(12 * ts)}px`,
    '--a2ui-font-size-s': `${r1(14 * ts)}px`,
    '--a2ui-font-size-m': `${r1(fs)}px`,
    '--a2ui-font-size-l': `${r1(18 * ts * hs)}px`,
    '--a2ui-font-size-xl': `${r1(22 * ts * hs)}px`,
    '--a2ui-font-size-2xl': `${r1(28 * ts * hs)}px`,
    '--a2ui-line-height-body': String(num(t.line_height, 1.5) || 1.5),
    '--a2ui-line-height-headings': '1.25',
    '--a2ui-text-caption-color': t.on_surface_variant,
    '--a2ui-text-a-color': t.primary,
    '--a2ui-label-font-size': `${r1(14 * ts)}px`,
    '--a2ui-label-font-weight': '500',
    // Spacing.
    '--a2ui-grid-base': `${space}px`,
    '--a2ui-spacing-xs': `${r1(space / 4)}px`,
    '--a2ui-spacing-s': `${r1(space / 2)}px`,
    '--a2ui-spacing-m': `${space}px`,
    '--a2ui-spacing-l': `${space * 2}px`,
    '--a2ui-spacing-xl': `${space * 4}px`,
    // Components.
    '--a2ui-card-background': t.surface_container,
    '--a2ui-card-border-radius': px(t.radius_card, 24),
    '--a2ui-card-padding': boxPx(t.card_padding, 12),
    '--a2ui-card-margin': boxPx(t.card_margin, 4),
    '--a2ui-card-box-shadow': shadow,
    '--a2ui-card-border': 'none',
    '--a2ui-button-border-radius': px(t.radius_control, 12),
    '--a2ui-button-padding': `${btnPadV}px ${space * 2}px`,
    '--a2ui-button-margin': boxPx(t.item_margin, 4),
    '--a2ui-button-background': 'transparent',
    '--a2ui-button-font-weight': '500',
    '--a2ui-button-border': `${bw}px solid ${t.outline}`,
    '--a2ui-textfield-border-radius': px(t.radius_control, 12),
    '--a2ui-textfield-padding': `${Math.max(4, Math.round((ctrl - 22) / 2))}px ${
        Math.round(space * 1.5)}px`,
    '--a2ui-textfield-color-error': t.error,
    '--a2ui-textfield-color-border-focus': t.primary,
    '--a2ui-datetimeinput-border-radius': px(t.radius_control, 12),
    '--a2ui-datetimeinput-color-error': t.error,
    '--a2ui-datetimeinput-background': t.surface,
    '--a2ui-datetimeinput-color': t.on_surface,
    '--a2ui-checkbox-color-error': t.error,
    '--a2ui-checkbox-border-radius': '4px',
    '--a2ui-slider-color-error': t.error,
    '--a2ui-slider-track-color': t.outline_variant,
    '--a2ui-slider-thumb-color': t.primary,
    '--a2ui-choicepicker-color-error': t.error,
    '--a2ui-choicepicker-chip-border-radius': px(t.radius_chip, 8),
    '--a2ui-choicepicker-chip-background': t.surface,
    '--a2ui-choicepicker-label-color': t.on_surface_variant,
    '--a2ui-image-border-radius': px(t.radius_media, 12),
    '--a2ui-video-border-radius': px(t.radius_media, 12),
    '--a2ui-audioplayer-border-radius': px(t.radius_control, 12),
    '--a2ui-audioplayer-background': t.surface_container_high,
    '--a2ui-tabs-header-color': t.on_surface_variant,
    '--a2ui-tabs-header-color-active': t.primary,
    '--a2ui-tabs-header-background': 'transparent',
    '--a2ui-tabs-header-background-active':
        `color-mix(in srgb, ${t.primary} 10%, transparent)`,
    '--a2ui-tabs-border': `${bw}px solid ${t.outline_variant}`,
    '--a2ui-modal-background': t.surface,
    '--a2ui-modal-border-radius': px(t.radius_card, 24),
    '--a2ui-modal-box-shadow': '0 12px 40px rgba(0, 0, 0, 0.28)',
    '--a2ui-divider-border': `${bw}px solid ${t.outline_variant}`,
    '--a2ui-divider-spacing': boxPx(t.item_margin, 4).split(' ')[0],
    '--a2ui-icon-font-family': '"Material Symbols Rounded", "Material Icons"',
    '--a2ui-icon-font-variation-settings':
        '"FILL" 1, "wght" 400, "GRAD" 0, "opsz" 24',
    // Host-only helpers (host.css).
    '--a2ui-host-font': bodyStack,
  };
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

/** Injects host.css + Google Fonts links into `doc` if missing. */
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
  if (!have('renderer/host.css')) add(new URL('./host.css', import.meta.url).href);
  if (!have('Google+Sans+Flex')) add(FONT_URLS[0]);
  if (!have('Material+Symbols+Rounded')) add(FONT_URLS[1]);
}
