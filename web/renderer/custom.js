// Custom catalog components: web-native HTML/CSS templates (docs/CONTRACT.md
// "Design system: custom components, tokens, package").
//
//   import {compileCustomComponent, renderCustomTemplate} from './custom.js';
//   const c = compileCustomComponent(definition);
//   // c = {ok, errors, warnings, bindings, slots, actions, tree, css,
//   styleText}
//
// The compiler is DOM-free (own tiny HTML tokenizer) so the same code runs in
// the renderer, the component editor (instant error display) and node tests.
// It mirrors core/a2ui/custom_web.py: an allowlist of tags/attributes, a tiny
// binding syntax and CSS scrubbing. `ok` means "renderable": the sanitized
// tree is non-empty and the definition is usable. `errors` lists everything
// that was removed or invalid (what the server calls issues); the renderer
// still renders the sanitized result when `ok` is true.
//
// renderCustomTemplate() instantiates a compiled tree into a (shadow) root
// with caller-supplied hooks for prop resolution, slots, actions and icons,
// and returns {update()} that re-evaluates every binding (cheap no-ops when
// values did not change; data-each blocks rebuild only when items change).

const SVG_TAGS =
    new Set(('svg g path circle rect line polyline polygon ellipse defs ' +
             'lineargradient radialgradient stop clippath')
                .split(' '));
export const ALLOWED_TAGS = new Set([
  ...('div span p h1 h2 h3 h4 h5 h6 ul ol li img figure figcaption section ' +
      'article header footer strong em small hr br slot')
      .split(' '),
  ...SVG_TAGS,
]);
const SVG_ATTR_CASE = {
  viewbox: 'viewBox',
  gradientunits: 'gradientUnits',
  gradienttransform: 'gradientTransform',
  clippath: 'clipPath',
};
const SVG_ATTRS = new Set(
    ('viewbox xmlns d fill stroke stroke-width stroke-linecap stroke-linejoin ' +
     'opacity fill-opacity stroke-opacity cx cy r rx ry x y x1 y1 x2 y2 ' +
     'width height points transform offset stop-color stop-opacity ' +
     'gradientunits gradienttransform id clip-path')
        .split(' '));
const VOID_TAGS = new Set([
  'img', 'br', 'hr', 'input', 'link', 'meta', 'source', 'area', 'base', 'col',
  'embed', 'wbr', 'track', 'param'
]);
// Whole subtree dropped (same list as core/a2ui/custom_web.py); any other
// disallowed element (a, form, button, input, …) is unwrapped.
const DROP_SUBTREE = new Set([
  'script', 'style', 'template', 'iframe', 'object', 'embed', 'foreignobject',
  'math', 'noscript', 'textarea', 'select', 'link', 'meta', 'base', 'title',
  'head'
]);
// Raw-text elements: their content is not markup.
const RAW_TEXT = new Set(['script', 'style', 'textarea', 'title', 'xmp']);
const DIRECTIVES = new Set([
  'data-if', 'data-unless', 'data-each', 'data-action', 'data-icon', 'data-bg'
]);
const INTERP_ATTRS =
    new Set(['src', 'alt', 'title', 'class', 'd', 'fill', 'stroke']);
const IDENT = /^[A-Za-z_]\w*$/;
const EXPR = /^[A-Za-z_]\w*(?:\.\w+)*$/;
const BIND_G = /\{\{\s*([^{}]*?)\s*\}\}/g;
const EACH_RE = /^\s*([A-Za-z_]\w*)\s+as\s+([A-Za-z_]\w*)\s*$/;
const COND_RE = /^\s*([A-Za-z_]\w*(?:\.\w+)*)\s*(?::\s*([\w .-]*?))?\s*$/;
const STYLE_DECL_RE = /^\s*(--[A-Za-z0-9_-]+)\s*:\s*(.+?)\s*$/;
const RESERVED_PROPS = new Set(['id', 'component', 'weight', 'accessibility']);

export const MAX_TEMPLATE = 20000;
export const MAX_CSS = 30000;

/**
 * Base styles prepended to every shadow root (author CSS comes after, so it
 * can override any of these). The host is a flex column and a single
 * top-level template element grows to fill it, so tiles stretched by a Row /
 * horizontal List keep equal heights.
 */
export const BASE_SHADOW_CSS =
    `:host{display:flex;flex-direction:column;box-sizing:border-box;min-width:0;max-width:calc(100% - var(--a2ui-space,8px));margin:var(--_gap,4px)}
:host([hidden]){display:none}
:host>:nth-child(2):last-child{flex:1 1 auto}
*,*::before,*::after{box-sizing:border-box}
[hidden]{display:none!important}
h1,h2,h3,h4,h5,h6,p,figure,blockquote{margin:0}
h1,h2,h3,h4,h5,h6{font-family:var(--a2ui-font-heading,inherit);font-weight:var(--a2ui-heading-weight,500)}
img{max-width:100%}
[data-icon]{font-family:"Material Symbols Rounded";font-weight:normal;font-style:normal;line-height:1;letter-spacing:normal;text-transform:none;white-space:nowrap;word-wrap:normal;direction:ltr;font-feature-settings:"liga";-webkit-font-feature-settings:"liga";font-variation-settings:"FILL" var(--a2ui-icon-fill,0),"wght" 400,"GRAD" 0,"opsz" 24;display:inline-block;overflow:hidden;user-select:none;flex:0 0 auto}
[data-action][role=button]{cursor:pointer}
[data-action][role=button]:focus-visible{outline:2px solid var(--a2ui-primary);outline-offset:2px}
`;

// ---------------------------------------------------------------------------
// HTML tokenizer -> raw tree {tag, attrs:[[name, value]], children} | string.

const ENTITIES = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: '\'',
  nbsp: '\u00a0',
  middot: '·',
  bull: '•',
  ndash: '–',
  mdash: '—',
  hellip: '…',
  copy: '©',
  reg: '®',
  trade: '™',
  times: '×',
  rarr: '→',
  larr: '←',
  laquo: '«',
  raquo: '»',
  lsquo: '‘',
  rsquo: '’',
  ldquo: '“',
  rdquo: '”',
  deg: '°',
  euro: '€',
  pound: '£',
  yen: '¥',
  cent: '¢',
  star: '☆',
};

export function decodeEntities(s) {
  return String(s).replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] === '#') {
      const n = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) :
                                               parseInt(e.slice(1), 10);
      return Number.isFinite(n) && n > 0 && n < 0x110000 ?
          String.fromCodePoint(n) :
          m;
    }
    const v = ENTITIES[e.toLowerCase()];
    return v != null ? v : m;
  });
}

const ATTR_RE =
    /([^\s"'>\/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;

/** Forgiving HTML parser (enough for component templates). */
export function parseHtml(src) {
  const root = {tag: '#root', attrs: [], children: []};
  const stack = [root];
  const top = () => stack[stack.length - 1];
  let i = 0;
  const n = src.length;
  while (i < n) {
    const lt = src.indexOf('<', i);
    if (lt < 0) {
      top().children.push(decodeEntities(src.slice(i)));
      break;
    }
    if (lt > i) top().children.push(decodeEntities(src.slice(i, lt)));
    i = lt;
    if (src.startsWith('<!--', i)) {
      const end = src.indexOf('-->', i + 4);
      i = end < 0 ? n : end + 3;
      continue;
    }
    if (src[i + 1] === '!' || src[i + 1] === '?') {
      const end = src.indexOf('>', i);
      i = end < 0 ? n : end + 1;
      continue;
    }
    const close = /^<\/\s*([A-Za-z][\w-]*)\s*>/.exec(src.slice(i, i + 64));
    if (close) {
      const tag = close[1].toLowerCase();
      for (let k = stack.length - 1; k > 0; k--) {
        if (stack[k].tag === tag) {
          stack.length = k;
          break;
        }
      }
      i += close[0].length;
      continue;
    }
    const open = /^<([A-Za-z][\w-]*)/.exec(src.slice(i, i + 64));
    if (!open) {
      top().children.push('<');
      i += 1;
      continue;
    }
    const tag = open[1].toLowerCase();
    // Find the end of the tag, honoring quoted attribute values.
    let j = i + open[0].length;
    let q = '';
    for (; j < n; j++) {
      const c = src[j];
      if (q) {
        if (c === q) q = '';
      } else if (c === '"' || c === '\'') {
        q = c;
      } else if (c === '>') {
        break;
      }
    }
    let body = src.slice(i + open[0].length, j);
    i = j + 1;
    let selfClosing = false;
    if (/\/\s*$/.test(body)) {
      selfClosing = true;
      body = body.replace(/\/\s*$/, '');
    }
    const attrs = [];
    ATTR_RE.lastIndex = 0;
    let m;
    while ((m = ATTR_RE.exec(body)) !== null) {
      const v = m[2] ?? m[3] ?? m[4];
      attrs.push([m[1].toLowerCase(), v == null ? '' : decodeEntities(v)]);
    }
    const node = {tag, attrs, children: []};
    // Implicit closes for the common sloppy cases.
    if ((tag === 'li' && top().tag === 'li') ||
        (tag === 'p' && top().tag === 'p')) {
      stack.pop();
    }
    top().children.push(node);
    if (RAW_TEXT.has(tag)) {
      const re = new RegExp(`</\\s*${tag}\\s*>`, 'i');
      const rest = src.slice(i);
      const mm = re.exec(rest);
      node.children.push(mm ? rest.slice(0, mm.index) : rest);
      i = mm ? i + mm.index + mm[0].length : n;
      continue;
    }
    if (!VOID_TAGS.has(tag) && !selfClosing) stack.push(node);
  }
  return root;
}

// ---------------------------------------------------------------------------
// Bindings.

/** "From {{price}}!" -> ['From ', {expr:'price'}, '!'] (null if static). */
export function parseInterpolation(text, onBad) {
  const parts = [];
  let last = 0;
  let dynamic = false;
  BIND_G.lastIndex = 0;
  let m;
  while ((m = BIND_G.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const expr = m[1];
    if (EXPR.test(expr)) {
      parts.push({expr});
      dynamic = true;
    } else if (onBad) {
      onBad(expr);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  if (!dynamic && parts.length <= 1 && last === 0) return null;
  return parts;
}

export function parseCondition(s) {
  const m = COND_RE.exec(String(s ?? ''));
  if (!m) return null;
  return {expr: m[1], value: m[2] != null && m[2] !== '' ? m[2] : null};
}

/** Truthiness for data-if: '', 0, false, 'false', null, [] and {} are falsy. */
export function truthy(v) {
  if (v == null || v === false || v === 0 || v === '') return false;
  if (typeof v === 'string') {
    const s = v.trim().toLowerCase();
    return s !== '' && s !== 'false' && s !== '0';
  }
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === 'number') return !Number.isNaN(v);
  if (typeof v === 'object') return Object.keys(v).length > 0;
  return true;
}

/** Evaluates a data-if/unless condition given the resolved value. */
export function testCondition(cond, value) {
  if (!cond) return true;
  if (cond.value == null) return truthy(value);
  return displayString(value) === cond.value;
}

export function displayString(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

/** URL allowed in an <img src>: https/http, site-relative, data:image. */
export function safeImageUrl(url) {
  const s = String(url ?? '').trim();
  if (!s) return null;
  if (/^data:image\//i.test(s)) return s;
  if (/^https?:\/\//i.test(s)) return s;
  if (/^\/(?!\/)/.test(s)) return s;
  return null;
}

/** Sanitizes a value bound into a custom property (style="--x: {{v}}"). */
export function safeStyleValue(v) {
  const s = displayString(v);
  if (/[;{}<>\\]/.test(s)) return '';
  if (/url\s*\(/i.test(s) && !/url\s*\(\s*['"]?data:/i.test(s)) return '';
  if (/expression\s*\(|javascript:|behavior\s*:|-moz-binding/i.test(s))
    return '';
  return s.trim();
}

/** Class tokens only. */
export function safeClass(v) {
  return displayString(v)
      .split(/\s+/)
      .filter((t) => /^[A-Za-z_-][\w-]*$/.test(t))
      .join(' ');
}

// ---------------------------------------------------------------------------
// CSS.

export function sanitizeCss(css) {
  let out = String(css ?? '');
  const errors = [];
  if (out.length > MAX_CSS) {
    errors.push(`CSS longer than ${MAX_CSS} characters (truncated)`);
    out = out.slice(0, MAX_CSS);
  }
  out = out.replace(/<\s*\/?\s*style[^>]*>/gi, '');
  if (/@import/i.test(out)) {
    errors.push('@import is not allowed (removed)');
    out = out.replace(/@import[^;]*;?/gi, '');
  }
  out = out.replace(/url\(\s*([^)]*)\)/gi, (m, inner) => {
    const u = String(inner).trim().replace(/^['"]|['"]$/g, '');
    if (/^data:/i.test(u)) return m;
    errors.push(
        `url(${u.slice(0, 60)}) is not allowed; only data: URLs (removed)`);
    return 'none';
  });
  for (const bad
           of ['expression(', 'behavior:', '-moz-binding', 'javascript:']) {
    if (out.toLowerCase().includes(bad)) {
      errors.push(`'${bad}' is not allowed (removed)`);
      out = out.replace(
          new RegExp(bad.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), '');
    }
  }
  const tokenRefs = [];
  for (const m of out.matchAll(/var\(\s*(--a2ui-[A-Za-z0-9_-]+)/g)) {
    if (!tokenRefs.includes(m[1])) tokenRefs.push(m[1]);
  }
  return {css: out.trim(), errors, tokenRefs};
}

// ---------------------------------------------------------------------------
// Compiler.

function propKind(schema) {
  if (!schema || typeof schema !== 'object') return 'any';
  const ref = String(schema.$ref || '');
  if (ref) return ref.split('/').pop();
  if (schema.type === 'array') return 'array';
  if (schema.type === 'string' && Array.isArray(schema.enum)) return 'enum';
  return String(schema.type || 'any');
}

/**
 * Compiles a custom component Definition.
 * → {ok, errors:[str], warnings:[str], bindings:[prop], slots:[prop],
 *    actions:[prop], tokenRefs:[--a2ui-*], tree, css, styleText, defaults}
 */
export function compileCustomComponent(def) {
  const errors = [];
  const warnings = [];
  const err = (m) => {
    if (!errors.includes(m) && errors.length < 40) errors.push(m);
  };
  const warn = (m) => {
    if (!warnings.includes(m) && warnings.length < 40) warnings.push(m);
  };
  const fail = (m) => ({
    ok: false,
    errors: [m, ...errors],
    warnings,
    bindings: [],
    slots: [],
    actions: [],
    tokenRefs: [],
    tree: [],
    css: '',
    styleText: BASE_SHADOW_CSS,
    defaults: {},
  });
  if (!def || typeof def !== 'object')
    return fail('definition is not an object');
  const web = def.web && typeof def.web === 'object' ? def.web : {};
  let template = typeof web.template === 'string' ? web.template : '';
  if (!template.trim()) return fail('web template is empty');
  if (template.length > MAX_TEMPLATE) {
    err(`template longer than ${MAX_TEMPLATE} characters (truncated)`);
    template = template.slice(0, MAX_TEMPLATE);
  }
  const properties =
      (def.props && typeof def.props === 'object' && def.props.properties &&
       typeof def.props.properties === 'object') ?
      def.props.properties :
      null;
  const defaults = {};
  if (properties) {
    for (const [k, s] of Object.entries(properties)) {
      if (s && typeof s === 'object' && s.default !== undefined)
        defaults[k] = s.default;
    }
  }

  const bindings = [];
  const slots = [];
  const actions = [];
  const loopVars = [];
  const ref = (expr) => {
    const head = String(expr).split('.')[0];
    if (loopVars.includes(head)) return;
    if (!bindings.includes(head)) bindings.push(head);
  };
  const interp = (text, where) => parseInterpolation(text, (bad) => {
    err(`unsupported binding {{${bad}}} in ${where} (removed)`);
  });

  let eachDepth = 0;
  const conv = (raw) => {
    if (typeof raw === 'string') {
      if (!raw) return null;
      const parts = interp(raw, 'text');
      if (parts) {
        for (const p of parts)
          if (typeof p !== 'string') ref(p.expr);
        return {type: 'text', parts};
      }
      return {type: 'text', text: raw};
    }
    const tag = raw.tag;
    if (DROP_SUBTREE.has(tag)) {
      err(`<${tag}> is not allowed (removed)`);
      return null;
    }
    if (tag !== '#root' && !ALLOWED_TAGS.has(tag)) {
      err(`<${tag}> is not allowed (unwrapped)`);
      return {type: 'frag', children: raw.children.map(conv).filter(Boolean)};
    }
    const node = {
      type: 'el',
      tag,
      attrs: [],
      dyn: [],
      styleVars: [],
      cond: null,
      neg: null,
      each: null,
      action: null,
      icon: null,
      slot: null,
      children: [],
    };
    // data-each first so its loop variable is in scope for sibling attrs.
    const attrs = [...raw.attrs].sort(
        (a, b) =>
            (a[0] === 'data-each' ? 0 : 1) - (b[0] === 'data-each' ? 0 : 1));
    let pushedLoop = false;
    for (const [name, value] of attrs) {
      if (name.startsWith('on')) {
        err(`event handler attribute '${name}' removed`);
        continue;
      }
      const ok = INTERP_ATTRS.has(name) || DIRECTIVES.has(name) ||
          SVG_ATTRS.has(name) || name === 'style' || name === 'name' ||
          name === 'role' || name.startsWith('aria-') ||
          /^data-[a-z0-9-]+$/.test(name);
      if (!ok) {
        err(`attribute '${name}' on <${tag}> removed`);
        continue;
      }
      const low = value.trim().toLowerCase();
      if (/^(javascript|vbscript):/.test(
              low.replace(/[\s\u0000-\u001f]/g, ''))) {
        err(`script URL in '${name}' removed`);
        continue;
      }
      if (name === 'data-each') {
        const m = EACH_RE.exec(value);
        if (!m) {
          err(`data-each must be 'prop as item' (got '${value}')`);
          continue;
        }
        ref(m[1]);
        node.each = {expr: m[1], alias: m[2]};
        loopVars.push(m[2]);
        pushedLoop = true;
        continue;
      }
      if (name === 'data-if' || name === 'data-unless') {
        const c = parseCondition(value);
        if (!c) {
          err(`${name} must be 'prop' or 'prop:value' (got '${value}')`);
          continue;
        }
        ref(c.expr);
        if (name === 'data-if')
          node.cond = c;
        else
          node.neg = c;
        continue;
      }
      if (name === 'data-action') {
        const p = value.trim();
        if (!EXPR.test(p)) {
          err(`data-action must name an Action prop (got '${value}')`);
          continue;
        }
        const head = p.split('.')[0];
        const inLoop = loopVars.includes(head);
        ref(p);
        if (!inLoop && !p.includes('.') && !actions.includes(p))
          actions.push(p);
        node.action = p;
        node.attrs.push(['data-action', p]);
        continue;
      }
      if (name === 'data-bg') {
        const parts = interp(value, 'data-bg');
        if (parts) {
          for (const p of parts)
            if (typeof p !== 'string') ref(p.expr);
          node.bg = {parts};
        } else if (EXPR.test(value.trim())) {
          ref(value.trim());
          node.bg = {parts: [{expr: value.trim()}]};
        } else {
          node.bg = {parts: [value.trim()]};
        }
        continue;
      }
      if (name === 'data-icon') {
        const parts = interp(value, 'data-icon');
        if (parts) {
          for (const p of parts)
            if (typeof p !== 'string') ref(p.expr);
          node.icon = {parts};
        } else {
          node.icon = {parts: [value.trim()]};
        }
        continue;
      }
      if (name === 'name') {
        if (tag !== 'slot') {
          err(`attribute 'name' on <${tag}> removed`);
          continue;
        }
        const p = value.trim();
        if (!IDENT.test(p)) {
          err(`<slot name> must be a prop name (got '${value}')`);
          continue;
        }
        ref(p);
        if (!slots.includes(p)) slots.push(p);
        if (eachDepth > 0 || pushedLoop)
          warn(`<slot name="${p}"> inside data-each renders only once`);
        node.slot = p;
        continue;
      }
      if (name === 'style') {
        for (const part of value.split(';')) {
          if (!part.trim()) continue;
          const m = STYLE_DECL_RE.exec(part);
          if (!m || /url\s*\(/i.test(m[2]) || /expression\s*\(/i.test(m[2])) {
            err('style attribute may only set custom properties (--x: …); ' +
                'put other styles in the CSS');
            continue;
          }
          const parts = interp(m[2], 'style');
          if (parts) {
            for (const p of parts)
              if (typeof p !== 'string') ref(p.expr);
            node.styleVars.push({name: m[1], parts});
          } else {
            node.styleVars.push({name: m[1], parts: [m[2]]});
          }
        }
        continue;
      }
      const parts = interp(value, `'${name}'`);
      if (parts) {
        if (!(INTERP_ATTRS.has(name) || name.startsWith('aria-') ||
              name.startsWith('data-'))) {
          err(`bindings are not allowed in '${name}' (removed)`);
          continue;
        }
        for (const p of parts)
          if (typeof p !== 'string') ref(p.expr);
        node.dyn.push({name, parts});
        continue;
      }
      if (name === 'src' &&
          !/^(https:\/\/|data:image\/|\/img\/|\/gen\/|\/api\/)/i.test(
              value.trim())) {
        err('img src must be a {{prop}} binding, https: or data:image');
        continue;
      }
      const attrName = SVG_ATTR_CASE[name] || name;
      node.attrs.push([attrName, name === 'class' ? safeClass(value) : value]);
    }
    if (tag === 'slot' && !node.slot) {
      err('<slot> needs name="<prop>"');
      if (pushedLoop) loopVars.pop();
      return null;
    }
    if (node.each) eachDepth++;
    node.children = raw.children.map(conv).filter(Boolean);
    if (node.each) eachDepth--;
    if (pushedLoop) loopVars.pop();
    return node;
  };

  let tree;
  try {
    const root = conv(parseHtml(template));
    tree = flatten(root.children);
  } catch (e) {
    return fail(`template could not be parsed: ${e.message}`);
  }
  if (!tree.some(
          (n) => n.type === 'el' ||
              (n.type === 'text' && (n.parts || n.text.trim())))) {
    return fail('web template is empty after sanitizing');
  }

  if (properties) {
    for (const b of bindings) {
      if (!(b in properties))
        err(`template uses '${b}' which is not a declared prop`);
    }
    for (const s of slots) {
      const k = propKind(properties[s]);
      if (s in properties && k !== 'ComponentId' && k !== 'ChildList')
        err(`<slot name="${s}"> needs a ComponentId or ChildList prop`);
    }
    for (const a of actions) {
      if (a in properties && propKind(properties[a]) !== 'Action')
        err(`data-action="${a}" needs an Action prop`);
    }
    for (const [p, s] of Object.entries(properties)) {
      if (RESERVED_PROPS.has(p)) err(`prop '${p}' is reserved`);
      const k = propKind(s);
      if ((k === 'ComponentId' || k === 'ChildList') && !slots.includes(p))
        warn(`slot prop '${p}' has no <slot name="${p}">`);
    }
  }

  const css = sanitizeCss(typeof web.css === 'string' ? web.css : '');
  for (const e of css.errors) err(`css: ${e}`);
  return {
    ok: true,
    errors,
    warnings,
    bindings,
    slots,
    actions,
    tokenRefs: css.tokenRefs,
    tree,
    css: css.css,
    styleText: `${BASE_SHADOW_CSS}${css.css}`,
    defaults,
  };
}

function flatten(nodes) {
  const out = [];
  for (const n of nodes) {
    if (!n) continue;
    if (n.type === 'frag')
      out.push(...flatten(n.children));
    else {
      if (n.type === 'el') n.children = flatten(n.children);
      out.push(n);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Instantiation.

/**
 * Evaluates an expression ("prop", "prop.a.b", "it", "it.field").
 * locals: {alias: {value, scope}}; hooks.prop(name) resolves top-level
 * props; hooks.resolveIn(value, scope) resolves nested dynamic values
 * ({path}/{call}) found inside items/objects.
 */
export function evalExpr(expr, locals, hooks) {
  const segs = String(expr).split('.');
  const head = segs[0];
  let value;
  let scope = null;
  if (locals && Object.prototype.hasOwnProperty.call(locals, head)) {
    value = locals[head].value;
    scope = locals[head].scope;
  } else {
    value = hooks.prop(head);
  }
  for (let i = 1; i < segs.length; i++) {
    if (value == null) return undefined;
    if (isDynamic(value) && hooks.resolveIn)
      value = hooks.resolveIn(value, scope);
    if (value == null || typeof value !== 'object') return undefined;
    value = value[segs[i]];
  }
  if (isDynamic(value) && hooks.resolveIn)
    value = hooks.resolveIn(value, scope);
  return value;
}

function isDynamic(v) {
  return !!v && typeof v === 'object' && !Array.isArray(v) &&
      (typeof v['@path'] === 'string' || typeof v['@call'] === 'string' ||
       typeof v.path === 'string' || typeof v.call === 'string');
}

export function interpolate(parts, locals, hooks) {
  let s = '';
  for (const p of parts) {
    s += typeof p === 'string' ? p :
                                 displayString(evalExpr(p.expr, locals, hooks));
  }
  return s;
}

/**
 * Items of a data-each source → [{value, scope}] (scope = data path or null).
 */
export function eachItems(expr, locals, hooks) {
  const value = evalExpr(expr, locals, hooks);
  let base = null;
  const head = String(expr).split('.')[0];
  if (!String(expr).includes('.')) {
    if (locals && Object.prototype.hasOwnProperty.call(locals, head))
      base = locals[head].scope;
    else if (hooks.propPath)
      base = hooks.propPath(head);
  }
  if (Array.isArray(value)) {
    return value.map(
        (v, i) => ({value: v, scope: base != null ? `${base}/${i}` : null}));
  }
  if (value && typeof value === 'object') {
    return Object.keys(value).map(
        (k) => ({
          value: value[k],
          scope: base != null ?
              `${base}/${k.replace(/~/g, '~0').replace(/\//g, '~1')}` :
              null,
        }));
  }
  return [];
}

function sig(items) {
  try {
    return JSON.stringify(items.map((x) => [x.value, x.scope]));
  } catch {
    return String(Math.random());
  }
}

/**
 * Instantiates `compiled.tree` into `root`.
 * hooks: {doc, prop(name), propPath(name)?, resolveIn(value, scope)?,
 *         icon(name) -> {glyph, fill}, action(el, prop, itemScope) -> bool,
 *         slot(name, el)?, image(img)?}
 * → {update()}
 */
export function renderCustomTemplate(compiled, root, hooks) {
  const doc = hooks.doc;
  const updaters = [];

  const build = (node, parent, locals, ups) => {
    if (node.type === 'text') {
      if (!node.parts) {
        parent.appendChild(doc.createTextNode(node.text));
        return;
      }
      const t = doc.createTextNode('');
      let last = null;
      ups.push(() => {
        const s = interpolate(node.parts, locals, hooks);
        if (s !== last) {
          last = s;
          t.data = s;
        }
      });
      parent.appendChild(t);
      return;
    }
    if (node.each) {
      const start = doc.createComment(`each ${node.each.expr}`);
      const end = doc.createComment('/each');
      parent.appendChild(start);
      parent.appendChild(end);
      let lastSig = null;
      let nodes = [];
      let inner = [];
      ups.push(() => {
        const items = eachItems(node.each.expr, locals, hooks);
        const s = sig(items);
        if (s !== lastSig) {
          lastSig = s;
          for (const n of nodes) n.remove();
          nodes = [];
          inner = [];
          const holder = doc.createDocumentFragment();
          items.forEach((it) => {
            const loc = {...(locals || {}), [node.each.alias]: it};
            const before = holder.childNodes.length;
            buildElement(node, holder, loc, inner);
            for (let k = before; k < holder.childNodes.length; k++)
              nodes.push(holder.childNodes[k]);
          });
          end.parentNode.insertBefore(holder, end);
        }
        for (const u of inner) u();
      });
      return;
    }
    buildElement(node, parent, locals, ups);
  };

  const buildElement = (node, parent, locals, ups) => {
    const isSvg = SVG_TAGS.has(node.tag);
    const svgTag = {
      lineargradient: 'linearGradient',
      radialgradient: 'radialGradient',
      clippath: 'clipPath',
    }[node.tag] ||
        node.tag;
    const el = isSvg && typeof doc.createElementNS === 'function' ?
        doc.createElementNS('http://www.w3.org/2000/svg', svgTag) :
        doc.createElement(node.tag);
    for (const [k, v] of node.attrs) el.setAttribute(k, v);
    if (node.slot) {
      el.setAttribute('name', node.slot);
      if (hooks.slot) hooks.slot(node.slot, el);
    }
    if (node.bg) {
      let last = null;
      ups.push(() => {
        const raw = interpolate(node.bg.parts, locals, hooks).trim();
        const safe = safeImageUrl(raw);
        if (safe === last) return;
        last = safe;
        if (safe) {
          el.style.backgroundImage = `url("${safe.replace(/"/g, '\\"')}")`;
          el.style.backgroundSize = el.style.backgroundSize || 'cover';
          el.style.backgroundPosition = el.style.backgroundPosition || 'center';
        } else {
          el.style.removeProperty('background-image');
        }
      });
    }
    for (const d of node.dyn) {
      let last = null;
      ups.push(() => {
        let v = interpolate(d.parts, locals, hooks);
        if (d.name === 'class') v = safeClass(v);
        if (v === last) return;
        last = v;
        if (d.name === 'src') {
          const safe = safeImageUrl(v);
          if (safe)
            el.setAttribute('src', safe);
          else
            el.removeAttribute('src');
        } else {
          el.setAttribute(d.name, v);
        }
      });
    }
    for (const sv of node.styleVars) {
      let last = null;
      ups.push(() => {
        const v = safeStyleValue(interpolate(sv.parts, locals, hooks));
        if (v === last) return;
        last = v;
        if (v)
          el.style.setProperty(sv.name, v);
        else
          el.style.removeProperty(sv.name);
      });
    }
    if (node.cond || node.neg) {
      let last = null;
      ups.push(() => {
        let show = true;
        if (node.cond)
          show =
              testCondition(node.cond, evalExpr(node.cond.expr, locals, hooks));
        if (show && node.neg)
          show =
              !testCondition(node.neg, evalExpr(node.neg.expr, locals, hooks));
        if (show === last) return;
        last = show;
        if (show)
          el.removeAttribute('hidden');
        else
          el.setAttribute('hidden', '');
      });
    }
    if (node.icon) {
      let last = null;
      ups.push(() => {
        const name = interpolate(node.icon.parts, locals, hooks).trim();
        if (name === last) return;
        last = name;
        el.setAttribute('data-icon', name);
        el.setAttribute('aria-hidden', 'true');
        const g = hooks.icon ? hooks.icon(name) : {glyph: name, fill: 0};
        el.textContent = name ? g.glyph : '';
        el.style.setProperty('--a2ui-icon-fill', String(g.fill ?? 0));
      });
    }
    if (node.action && hooks.action) {
      const scope = locals ? lastScope(locals) : null;
      const head = node.action.split('.')[0];
      const resolved = node.action.includes('.') ||
              (locals && Object.prototype.hasOwnProperty.call(locals, head)) ?
          evalExpr(node.action, locals, hooks) :
          undefined;
      hooks.action(el, node.action, scope, resolved);
    }
    if (node.tag === 'img' && hooks.image) hooks.image(el);
    if (!node.icon) {
      for (const c of node.children) build(c, el, locals, ups);
    }
    parent.appendChild(el);
  };

  for (const n of compiled.tree || []) build(n, root, null, updaters);
  return {
    update() {
      for (const u of updaters) u();
    },
  };
}

// The innermost loop item scope (for relative action context paths).
function lastScope(locals) {
  const vals = Object.values(locals);
  for (let i = vals.length - 1; i >= 0; i--) {
    if (vals[i] && vals[i].scope != null) return vals[i].scope;
  }
  return null;
}

/**
 * True if the event came from an interactive element nested inside `el`
 * (e.g. a slotted Button), so the container action must not also fire.
 */
export function fromNestedControl(e, el) {
  const path = typeof e.composedPath === 'function' ? e.composedPath() : [];
  for (const n of path) {
    if (n === el) return false;
    const tag = n && n.tagName ? String(n.tagName).toLowerCase() : '';
    if ([
          'button', 'a', 'input', 'textarea', 'select', 'label', 'summary'
        ].includes(tag))
      return true;
    if (n && n.getAttribute && n.getAttribute('role') === 'button') return true;
  }
  return false;
}
