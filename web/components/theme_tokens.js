// Shared theme-token metadata and editor controls, used by Settings (full
// theme editor) and the Review live inspector (tokens related to the selected
// component).
import {coerceBoxPx, DEFAULT_THEME, parseBoxPx} from '../renderer/a2ui.js';

import {h} from './dom.js';

export const THEME_GROUPS = [
  {
    id: 'colors_core',
    title: 'Core Color Palette \u0026 Mode',
    help:
        'Applies across Web CSS, Android MaterialTheme ColorScheme, RemoteCompose (.rc), and iOS SwiftUI.',
    fields: [
      {
        key: 'scheme',
        label: 'Color scheme',
        type: 'select',
        options: ['light', 'dark']
      },
      {key: 'primary', label: 'Primary', type: 'color'},
      {key: 'on_primary', label: 'On primary', type: 'color'},
      {key: 'primary_container', label: 'Primary container', type: 'color'},
      {
        key: 'on_primary_container',
        label: 'On primary container',
        type: 'color'
      },
      {key: 'secondary', label: 'Secondary', type: 'color'},
      {key: 'on_secondary', label: 'On secondary', type: 'color'},
      {key: 'secondary_container', label: 'Secondary container', type: 'color'},
      {
        key: 'on_secondary_container',
        label: 'On secondary container',
        type: 'color'
      },
      {key: 'tertiary', label: 'Tertiary', type: 'color'},
      {key: 'on_tertiary', label: 'On tertiary', type: 'color'},
      {key: 'surface', label: 'Surface', type: 'color'},
      {key: 'on_surface', label: 'On surface', type: 'color'},
      {
        key: 'surface_container_low',
        label: 'Surface container low',
        type: 'color'
      },
      {key: 'surface_container', label: 'Surface container', type: 'color'},
      {
        key: 'surface_container_high',
        label: 'Surface container high',
        type: 'color'
      },
      {key: 'on_surface_variant', label: 'On surface variant', type: 'color'},
      {key: 'outline', label: 'Outline', type: 'color'},
      {key: 'outline_variant', label: 'Outline variant', type: 'color'},
      {key: 'error', label: 'Error', type: 'color'},
      {key: 'on_error', label: 'On error', type: 'color'},
      {key: 'error_container', label: 'Error container', type: 'color'},
      {key: 'on_error_container', label: 'On error container', type: 'color'},
    ],
  },
  {
    id: 'shapes',
    title: 'Shape, Borders \u0026 Elevation',
    help:
        'Corner radii and border/elevation tokens mapped to CSS, Compose Shapes, .rc RoundRects, and SwiftUI clipShapes.',
    fields: [
      {
        key: 'radius_card',
        label: 'Card radius',
        type: 'number',
        min: 0,
        max: 48,
        step: 1,
        unit: 'px'
      },
      {
        key: 'radius_control',
        label: 'Control / Button radius',
        type: 'number',
        min: 0,
        max: 32,
        step: 1,
        unit: 'px'
      },
      {
        key: 'radius_chip',
        label: 'Chip / Option radius',
        type: 'number',
        min: 0,
        max: 24,
        step: 1,
        unit: 'px'
      },
      {
        key: 'radius_media',
        label: 'Image / Media radius',
        type: 'number',
        min: 0,
        max: 36,
        step: 1,
        unit: 'px'
      },
      {
        key: 'border_width',
        label: 'Border / Divider width',
        type: 'number',
        min: 0,
        max: 6,
        step: 1,
        unit: 'px'
      },
      {
        key: 'card_elevation',
        label: 'Card elevation',
        type: 'number',
        min: 0,
        max: 24,
        step: 1,
        unit: 'dp'
      },
    ],
  },
  {
    id: 'typography',
    title: 'Typography',
    help:
        'Font families, scale multipliers, weights, and line height across all 4 renderers.',
    fields: [
      {
        key: 'font_family',
        label: 'Body font family',
        type: 'text',
        placeholder: 'Google Sans Flex'
      },
      {
        key: 'font_family_heading',
        label: 'Heading font family',
        type: 'text',
        placeholder: 'Same as body (or e.g. Georgia, serif)'
      },
      {
        key: 'font_family_mono',
        label: 'Monospace font family',
        type: 'text',
        placeholder: 'Google Sans Code'
      },
      {
        key: 'type_scale',
        label: 'Body type scale',
        type: 'number',
        min: 0.75,
        max: 1.5,
        step: 0.05,
        unit: '\u00d7'
      },
      {
        key: 'heading_scale',
        label: 'Heading scale',
        type: 'number',
        min: 0.75,
        max: 1.6,
        step: 0.05,
        unit: '\u00d7'
      },
      {
        key: 'heading_weight',
        label: 'Heading weight',
        type: 'select',
        options: [400, 500, 600, 700, 800]
      },
      {
        key: 'body_weight',
        label: 'Body weight',
        type: 'select',
        options: [300, 400, 500, 600]
      },
      {
        key: 'line_height',
        label: 'Line height',
        type: 'number',
        min: 1.1,
        max: 2.0,
        step: 0.05,
        unit: '\u00d7'
      },
    ],
  },
  {
    id: 'spacing',
    title: 'Spacing \u0026 Density',
    help:
        'Base spacing rhythm, 4-sided (top / right / bottom / left) padding \u0026 margin tokens, and control height.',
    fields: [
      {
        key: 'space',
        label: 'Spacing unit',
        type: 'number',
        min: 2,
        max: 20,
        step: 1,
        unit: 'px'
      },
      {
        key: 'card_padding',
        label: 'Card padding',
        type: 'box',
        min: 0,
        max: 64,
        step: 1,
        unit: 'px'
      },
      {
        key: 'surface_padding',
        label: 'Surface padding',
        type: 'box',
        min: 0,
        max: 64,
        step: 1,
        unit: 'px'
      },
      {
        key: 'card_margin',
        label: 'Card margin',
        type: 'box',
        min: 0,
        max: 48,
        step: 1,
        unit: 'px'
      },
      {
        key: 'item_margin',
        label: 'Item margin',
        type: 'box',
        min: 0,
        max: 48,
        step: 1,
        unit: 'px'
      },
      {
        key: 'control_height',
        label: 'Control / Button height',
        type: 'number',
        min: 28,
        max: 60,
        step: 2,
        unit: 'px'
      },
    ],
  },
];

export const ALL_THEME_FIELDS = THEME_GROUPS.flatMap((g) => g.fields);
const FIELD_BY_KEY = new Map(ALL_THEME_FIELDS.map((f) => [f.key, f]));
const GROUP_BY_KEY =
    new Map(THEME_GROUPS.flatMap((g) => g.fields.map((f) => [f.key, g])));

// Tokens that style the surface itself (shown when nothing is selected).
const SURFACE_TOKENS = [
  'scheme',
  'surface',
  'on_surface',
  'font_family',
  'type_scale',
  'body_weight',
  'line_height',
  'space',
  'surface_padding',
  'card_margin',
  'item_margin',
];
const BODY_TEXT =
    ['on_surface', 'font_family', 'type_scale', 'body_weight', 'line_height'];
const HEADING_TEXT = [
  'on_surface',
  'font_family_heading',
  'heading_scale',
  'heading_weight',
  'type_scale',
];

/**
 * Theme tokens each basic-catalog component reads in the web renderer
 * (mirrors the var(--a2ui-*) usage in renderer/a2ui.css). Functions receive
 * the component JSON so variants can narrow the list.
 */
const COMPONENT_TOKENS = {
  Text: (c) => {
    const v = String(c.variant || 'body');
    if (/^h[1-6]$/.test(v)) return [...HEADING_TEXT, 'item_margin'];
    if (v === 'caption')
      return [
        'on_surface_variant', 'font_family', 'type_scale', 'body_weight',
        'item_margin'
      ];
    return [...BODY_TEXT, 'primary', 'font_family_mono', 'item_margin'];
  },
  Image: ['radius_media', 'surface_container', 'space', 'item_margin'],
  Video: ['radius_media', 'surface_container', 'space', 'item_margin'],
  AudioPlayer:
      ['radius_control', 'primary', 'on_surface', 'space', 'item_margin'],
  Icon: ['on_surface_variant', 'on_surface', 'primary'],
  Row: ['space', 'item_margin'],
  Column: ['space', 'item_margin'],
  List: ['space', 'item_margin'],
  Card: [
    'surface_container',
    'on_surface',
    'radius_card',
    'card_padding',
    'card_margin',
    'card_elevation',
    'outline',
    'border_width',
  ],
  Tabs: [
    'primary', 'on_surface_variant', 'outline', 'border_width', 'type_scale',
    'space'
  ],
  Divider: ['outline_variant', 'outline', 'border_width', 'space'],
  Modal: ['surface', 'on_surface', 'radius_card', 'space'],
  Button: (c) => {
    const v = String(c.variant || '');
    const base = [
      'radius_control', 'control_height', 'border_width', 'space', 'type_scale'
    ];
    if (v === 'primary') return ['primary', 'on_primary', ...base];
    if (v === 'borderless')
      return ['primary', 'radius_control', 'control_height', 'space'];
    return ['primary', 'outline', ...base];
  },
  CheckBox: ['primary', 'on_primary', 'on_surface_variant', 'on_surface'],
  TextField: [
    'surface',
    'on_surface',
    'on_surface_variant',
    'outline',
    'border_width',
    'radius_control',
    'control_height',
    'primary',
    'error',
  ],
  DateTimeInput: [
    'surface',
    'on_surface',
    'on_surface_variant',
    'outline',
    'border_width',
    'radius_control',
    'control_height',
    'primary',
  ],
  ChoicePicker: (c) => {
    if (String(c.displayStyle || '') === 'chips') {
      return [
        'radius_chip',
        'outline',
        'border_width',
        'on_surface',
        'secondary_container',
        'on_secondary_container',
        'on_surface_variant',
        'space',
      ];
    }
    return [
      'primary', 'on_primary', 'on_surface_variant', 'on_surface',
      'radius_chip', 'space'
    ];
  },
  Slider: ['primary', 'surface', 'on_surface_variant', 'space'],
};

/** Finds a component by id in a list of A2UI v0.9 messages. */
export function findComponent(messages, id) {
  if (!id || !Array.isArray(messages)) return null;
  let found = null;
  for (const m of messages) {
    const comps = m && m.updateComponents && m.updateComponents.components;
    if (!Array.isArray(comps)) continue;
    for (const c of comps) {
      if (c && c.id === id) found = c;  // later updates win
    }
  }
  return found;
}

/**
 * Theme fields related to a component (or to the surface when `comp` is
 * null), in display order. Unknown/custom components fall back to the surface
 * tokens.
 */
export function relatedThemeFields(comp) {
  let keys = SURFACE_TOKENS;
  if (comp && typeof comp.component === 'string') {
    const spec = COMPONENT_TOKENS[comp.component];
    if (spec) keys = typeof spec === 'function' ? spec(comp) : spec;
  }
  return [...new Set(keys)].map((k) => FIELD_BY_KEY.get(k)).filter(Boolean);
}

/** The THEME_GROUPS entry a token belongs to. */
export function themeGroupFor(key) {
  return GROUP_BY_KEY.get(key) || null;
}

/**
 * Box-spacing (padding / margin) editor row. Starts in "All sides" mode (one
 * value); a toggle switches to per-side Top / Right / Bottom / Left. Values
 * that already differ per side open in per-side mode so nothing is hidden.
 * Uses custom −/+ steppers (native number spinners are hidden) and a plain
 * <div> row (a <label> row forwards clicks to its first input).
 * opts: {idPrefix, defaultValue, varName, onChange(value)}
 */
export function boxSpacingControl(fd, value, opts = {}) {
  const idPrefix = opts.idPrefix ?? 'theme-';
  const defVal = opts.defaultValue ?? 0;
  const onVal = (v) => opts.onChange && opts.onChange(v);
  const min = fd.min ?? 0;
  const max = fd.max ?? 64;
  const step = fd.step ?? 1;
  const unit = fd.unit || 'px';
  const clamp = (n) => Math.min(max, Math.max(min, Math.round(n)));
  const uniform = (s) => s.every((x) => x === s[0]);
  let sides = parseBoxPx(value ?? defVal, defVal);
  let mode = uniform(sides) ? 'all' : 'sides';

  const emit = () => onVal(coerceBoxPx(sides, defVal));

  /** One [−][value][+] stepper; `get`/`set` read and write the model. */
  const stepper = ({id, ariaLabel, tag, get, set}) => {
    const inp = h('input.box-step-input', {
      type: 'number',
      inputmode: 'numeric',
      id,
      'aria-label': ariaLabel,
      min,
      max,
      step,
      value: get(),
      oninput: () => {
        const n = Number(inp.value);
        if (inp.value === '' || !Number.isFinite(n)) return;
        set(clamp(n));
        emit();
      },
      onblur: () => {
        inp.value = String(get());
      },
    });
    let holdTimer = null;
    const stop = () => {
      clearTimeout(holdTimer);
      clearInterval(holdTimer);
      holdTimer = null;
    };
    const bump = (dir) => {
      const next = clamp(get() + dir * step);
      if (next === get()) return;
      set(next);
      inp.value = String(next);
      emit();
    };
    const btn = (dir, glyph, label) =>
        h('button.box-step-btn', {
          type: 'button',
          tabindex: '-1',
          'aria-label': `${label} ${ariaLabel}`,
          title: label,
          // Keep focus where it is and avoid any layout shift from focus
          // changes.
          onpointerdown: (e) => {
            e.preventDefault();
            bump(dir);
            stop();
            holdTimer = setTimeout(() => {
              holdTimer = setInterval(() => bump(dir), 60);
            }, 380);
          },
          onpointerup: stop,
          onpointerleave: stop,
          onpointercancel: stop,
          onclick: (e) => {
            // Keyboard activation (pointer clicks are handled on pointerdown).
            if (e.detail === 0) bump(dir);
          },
        },
          glyph);
    const el =
        h('div.box-step',
          tag ? h('span.box-step-tag', {title: ariaLabel}, tag) : null,
          btn(-1, '\u2212', 'Decrease'), inp, btn(1, '+', 'Increase'));
    el.sync = () => {
      if (document.activeElement !== inp) inp.value = String(get());
    };
    return el;
  };

  const allStepper = stepper({
    id: `${idPrefix}${fd.key}`,
    ariaLabel: `${fd.label} (all sides, ${unit})`,
    get: () => sides[0],
    set: (n) => {
      sides = [n, n, n, n];
    },
  });
  const sideMeta = [
    {idx: 0, short: 'T', title: 'Top'},
    {idx: 1, short: 'R', title: 'Right'},
    {idx: 2, short: 'B', title: 'Bottom'},
    {idx: 3, short: 'L', title: 'Left'},
  ];
  const sideSteppers =
      sideMeta.map(({idx, short, title}) => stepper({
                     id: `${idPrefix}${fd.key}-${short.toLowerCase()}`,
                     ariaLabel: `${fd.label} ${title.toLowerCase()} (${unit})`,
                     tag: short,
                     get: () => sides[idx],
                     set: (n) => {
                       sides = sides.slice();
                       sides[idx] = n;
                     },
                   }));
  const allPane = h('div.box-all', allStepper, h('span.box-unit', unit));
  const sidesPane = h('div.box-sides', sideSteppers);

  const modeBtn = (m, text, title) =>
      h('button.box-mode-btn', {
        type: 'button',
        title,
        'aria-label': title,
        onclick: () => setMode(m, true),
      },
        text);
  const allBtn = modeBtn('all', 'All', 'One value for all sides');
  const sidesBtn =
      modeBtn('sides', 'T R B L', 'Separate top, right, bottom, left');
  const toggle =
      h('div.box-mode', {role: 'group', 'aria-label': `${fd.label} mode`},
        allBtn, sidesBtn);

  const setMode = (m, fromUser) => {
    if (fromUser && m === 'all' && !uniform(sides)) {
      sides = [sides[0], sides[0], sides[0], sides[0]];
      emit();
    }
    mode = m;
    allPane.hidden = m !== 'all';
    sidesPane.hidden = m !== 'sides';
    allBtn.classList.toggle('on', m === 'all');
    sidesBtn.classList.toggle('on', m === 'sides');
    allBtn.setAttribute('aria-pressed', String(m === 'all'));
    sidesBtn.setAttribute('aria-pressed', String(m === 'sides'));
    allStepper.sync();
    sideSteppers.forEach((s) => s.sync());
  };

  const row =
      h('div.theme-field.theme-field-box',
        h('div.box-head',
          h('span.theme-label', fd.label,
            h('code.theme-var-hint', {title: opts.varName || ''},
              opts.varName || '')),
          toggle),
        allPane, sidesPane);
  setMode(mode, false);
  row.setValue = (v) => {
    sides = parseBoxPx(v ?? defVal, defVal);
    if (mode === 'all' && !uniform(sides)) {
      setMode('sides', false);
    } else {
      allStepper.sync();
      sideSteppers.forEach((s) => s.sync());
    }
  };
  return row;
}

/**
 * Builds a labeled editor row for one theme token.
 * opts: {idPrefix='theme-', onChange(value)}
 * Returns the row element; `row.setValue(v)` refreshes its inputs.
 */
export function themeFieldControl(fd, value, opts = {}) {
  const idPrefix = opts.idPrefix ?? 'theme-';
  const onVal = (v) => opts.onChange && opts.onChange(v);
  const val = value ?? DEFAULT_THEME[fd.key] ?? '';
  if (fd.type === 'box') {
    return boxSpacingControl(fd, val, {
      idPrefix,
      defaultValue: DEFAULT_THEME[fd.key] ?? 0,
      varName: `--a2ui-${fd.key.replace(/_/g, '-')}`,
      onChange: onVal,
    });
  }
  let input;
  if (fd.type === 'color') {
    const txt = h('input.input.input-sm.mono', {
      value: val,
      id: `${idPrefix}${fd.key}-hex`,
      size: 8,
      oninput: (e) => {
        if (/^#[0-9a-f]{6}$/i.test(e.target.value.trim())) {
          picker.value = e.target.value.trim();
          onVal(e.target.value.trim().toUpperCase());
        }
      },
    });
    const picker = h('input.color', {
      type: 'color',
      id: `${idPrefix}${fd.key}`,
      value: /^#[0-9a-f]{6}$/i.test(String(val)) ? val : '#000000',
      oninput: (e) => {
        txt.value = e.target.value.toUpperCase();
        onVal(e.target.value.toUpperCase());
      },
    });
    input = h('div.color-field', picker, txt);
  } else if (fd.type === 'number') {
    input = h(
        'div.num-field',
        h('input.input.input-sm', {
          type: 'number',
          id: `${idPrefix}${fd.key}`,
          min: fd.min,
          max: fd.max,
          step: fd.step,
          value: val,
          oninput: (e) => {
            const n = Number(e.target.value);
            if (e.target.value !== '' && !isNaN(n)) onVal(n);
          },
        }),
        h('span.muted', fd.unit || ''),
    );
  } else if (fd.type === 'select') {
    input = h(
        'select.input.input-sm',
        {
          id: `${idPrefix}${fd.key}`,
          onchange: (e) => {
            const raw = e.target.value;
            onVal(typeof fd.options[0] === 'number' ? Number(raw) : raw);
          },
        },
        fd.options.map(
            (o) =>
                h('option',
                  {value: String(o), selected: String(val) === String(o)},
                  String(o))),
    );
  } else {
    input = h('input.input.input-sm', {
      id: `${idPrefix}${fd.key}`,
      value: val,
      placeholder: fd.placeholder || '',
      oninput: (e) => onVal(e.target.value),
    });
  }
  const row = h(
      'label.theme-field',
      h('span.theme-label', fd.label,
        h('code.theme-var-hint', `--a2ui-${fd.key.replace(/_/g, '-')}`)),
      input,
  );
  row.setValue = (v) => {
    const s = v ?? DEFAULT_THEME[fd.key] ?? '';
    row.querySelectorAll('input,select').forEach((inp) => {
      if (inp.type === 'color') {
        if (/^#[0-9a-f]{6}$/i.test(String(s))) inp.value = s;
      } else if (document.activeElement !== inp) {
        inp.value = String(s);
      }
    });
  };
  return row;
}
