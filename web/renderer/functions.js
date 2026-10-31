// A2UI v0.9 Basic Catalog functions + dynamic value resolution. DOM-free.
//
// A "context" is {getData(pointer), scope, locale?, openUrl?(url), errors?}.

import {resolvePath} from './model.js';

const DEFAULT_LOCALE = 'en-US';

export function isPathBinding(v) {
  return v != null && typeof v === 'object' && !Array.isArray(v) &&
      typeof v.path === 'string' && !('call' in v);
}

export function isFunctionCall(v) {
  return v != null && typeof v === 'object' && !Array.isArray(v) &&
      typeof v.call === 'string';
}

function noteError(ctx, msg) {
  if (ctx && Array.isArray(ctx.errors) && !ctx.errors.includes(msg))
    ctx.errors.push(msg);
}

/** Resolves a Dynamic* value (literal, {path}, {call}) in ctx. */
export function resolveDynamic(value, ctx) {
  if (isPathBinding(value))
    return ctx.getData(resolvePath(ctx.scope, value.path));
  if (isFunctionCall(value))
    return callFunction(value.call, value.args || {}, ctx);
  return value;
}

/** Resolves function args: dynamic values, arrays element-wise. */
function resolveArgs(args, ctx) {
  const out = {};
  for (const [k, v] of Object.entries(args || {})) {
    if (Array.isArray(v))
      out[k] = v.map((x) => resolveDynamic(x, ctx));
    else
      out[k] = resolveDynamic(v, ctx);
  }
  return out;
}

export function toDisplayString(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function toNumber(v) {
  if (typeof v === 'number') return v;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v.replace(/,/g, ''));
    return Number.isNaN(n) ? NaN : n;
  }
  if (typeof v === 'boolean') return v ? 1 : 0;
  return NaN;
}

// ---------------------------------------------------------------------------
// Function registry. Each receives already-resolved args except formatString
// (which needs ctx to interpolate) — all get (args, ctx).

export const FUNCTIONS = {
  required: ({value}) =>
      !(value === null || value === undefined || value === '' ||
        (Array.isArray(value) && value.length === 0)),

  regex: ({value, pattern}, ctx) => {
    try {
      return new RegExp(String(pattern ?? '')).test(toDisplayString(value));
    } catch (e) {
      noteError(ctx, `regex: invalid pattern ${JSON.stringify(pattern)}`);
      return false;
    }
  },

  length: ({value, min, max}) => {
    const len =
        Array.isArray(value) ? value.length : toDisplayString(value).length;
    if (min != null && len < Number(min)) return false;
    if (max != null && len > Number(max)) return false;
    return true;
  },

  numeric: ({value, min, max}) => {
    const n = toNumber(value);
    if (Number.isNaN(n)) return false;
    if (min != null && n < Number(min)) return false;
    if (max != null && n > Number(max)) return false;
    return true;
  },

  email: ({value}) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(toDisplayString(value)),

  formatNumber: ({value, decimals, grouping}, ctx) => {
    const n = toNumber(value);
    if (Number.isNaN(n)) return toDisplayString(value);
    const opts = {useGrouping: grouping !== false};
    if (decimals != null && !Number.isNaN(Number(decimals))) {
      const d = Math.max(0, Math.min(20, Number(decimals)));
      opts.minimumFractionDigits = d;
      opts.maximumFractionDigits = d;
    }
    return new Intl.NumberFormat(ctx.locale || DEFAULT_LOCALE, opts).format(n);
  },

  formatCurrency: ({value, currency, decimals, grouping}, ctx) => {
    const n = toNumber(value);
    if (Number.isNaN(n)) return toDisplayString(value);
    const opts = {
      style: 'currency',
      currency: String(currency || 'USD').toUpperCase(),
      useGrouping: grouping !== false
    };
    if (decimals != null && !Number.isNaN(Number(decimals))) {
      const d = Math.max(0, Math.min(20, Number(decimals)));
      opts.minimumFractionDigits = d;
      opts.maximumFractionDigits = d;
    }
    try {
      return new Intl.NumberFormat(ctx.locale || DEFAULT_LOCALE, opts)
          .format(n);
    } catch (e) {
      noteError(ctx, `formatCurrency: ${e.message}`);
      return `${currency} ${n}`;
    }
  },

  formatDate: ({value, format}, ctx) => {
    const d = parseDate(value);
    if (!d) return toDisplayString(value);
    return formatDateTR35(
        d, String(format || 'yyyy-MM-dd'), ctx.locale || DEFAULT_LOCALE);
  },

  pluralize: (args, ctx) => {
    const n = toNumber(args.value);
    if (Number.isNaN(n)) return toDisplayString(args.other);
    // Explicit zero wins for 0 even in locales without a CLDR "zero" category.
    if (n === 0 && args.zero != null) return toDisplayString(args.zero);
    let cat = 'other';
    try {
      cat = new Intl.PluralRules(ctx.locale || DEFAULT_LOCALE).select(n);
    } catch {
    }
    return toDisplayString(args[cat] != null ? args[cat] : args.other);
  },

  openUrl: ({url}, ctx) => {
    const safe = safeHttpUrl(url, ctx.baseUrl);
    if (!safe) {
      noteError(ctx, `openUrl: blocked non-http(s) url ${JSON.stringify(url)}`);
      return undefined;
    }
    if (ctx.openUrl) ctx.openUrl(safe);
    return undefined;
  },

  and: ({values}) => Array.isArray(values) && values.every((v) => !!v),
  or: ({values}) => Array.isArray(values) && values.some((v) => !!v),
  not: ({value}) => !value,

  // Not in the Basic Catalog but referenced by the formatString prose spec.
  now: () => new Date().toISOString(),
};

export function callFunction(name, rawArgs, ctx) {
  const fn = FUNCTIONS[name];
  if (!fn) {
    noteError(ctx, `unknown function "${name}"`);
    return undefined;
  }
  try {
    if (name === 'formatString')
      return formatString(resolveDynamic(rawArgs.value, ctx), ctx);
    // Short-circuit logic without evaluating everything is not observable here.
    return fn(resolveArgs(rawArgs, ctx), ctx);
  } catch (e) {
    noteError(ctx, `function ${name}: ${e && e.message || e}`);
    return undefined;
  }
}
FUNCTIONS.formatString = ({value}, ctx) => formatString(value, ctx);

export function safeHttpUrl(url, base) {
  try {
    const u = new URL(
        String(url),
        base ||
            (typeof location !== 'undefined' ? location.href :
                                               'http://localhost/'));
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.href : null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// formatString: `${expr}` interpolation with nested expressions.
//
// expr     := '${' expr '}' | literal | call | path
// literal  := 'str' | "str" | number | true | false | null
// call     := ident '(' [ident ':' expr (',' ident ':' expr)*] ')'
// path     := anything else ("/abs/path" or "relative/path")

export function formatString(template, ctx) {
  if (template == null) return '';
  const s = String(template);
  let out = '';
  let i = 0;
  while (i < s.length) {
    if (s[i] === '\\' && s.startsWith('${', i + 1)) {
      out += '${';
      i += 3;
      continue;
    }
    if (s.startsWith('${', i)) {
      const end = findClose(s, i + 2);
      if (end < 0) {
        out += s.slice(i);
        break;
      }
      const inner = s.slice(i + 2, end);
      try {
        out += toDisplayString(evalExpr(inner, ctx));
      } catch (e) {
        noteError(ctx, `formatString: ${e.message} in \${${inner}}`);
      }
      i = end + 1;
      continue;
    }
    out += s[i++];
  }
  return out;
}

/** Index of the "}" closing a "${" whose body starts at `from`. */
function findClose(s, from) {
  let depth = 1;
  let quote = null;
  for (let i = from; i < s.length; i++) {
    const c = s[i];
    if (quote) {
      if (c === '\\') {
        i++;
        continue;
      }
      if (c === quote) quote = null;
      continue;
    }
    if (c === '\'' || c === '"')
      quote = c;
    else if (c === '$' && s[i + 1] === '{') {
      depth++;
      i++;
    } else if (c === '{')
      depth++;
    else if (c === '}') {
      if (--depth === 0) return i;
    }
  }
  return -1;
}

function evalExpr(src, ctx) {
  const p = new ExprParser(src, ctx);
  const v = p.parseExpr();
  p.skipWs();
  if (p.i < p.s.length) {
    // Not a pure expression; treat the whole thing as a path.
    return ctx.getData(resolvePath(ctx.scope, src.trim()));
  }
  return v;
}

class ExprParser {
  constructor(s, ctx) {
    this.s = s;
    this.i = 0;
    this.ctx = ctx;
  }
  skipWs() {
    while (this.i < this.s.length && /\s/.test(this.s[this.i])) this.i++;
  }
  parseExpr() {
    this.skipWs();
    const s = this.s;
    const c = s[this.i];
    if (s.startsWith('${', this.i)) {
      const end = findClose(s, this.i + 2);
      if (end < 0) throw new Error('unterminated ${');
      const v = evalExpr(s.slice(this.i + 2, end), this.ctx);
      this.i = end + 1;
      return v;
    }
    if (c === '\'' || c === '"') return this.parseString(c);
    const numM = /^-?\d+(\.\d+)?(?=\s*[,)]|\s*$)/.exec(s.slice(this.i));
    if (numM) {
      this.i += numM[0].length;
      return Number(numM[0]);
    }
    const kw = /^(true|false|null)(?=\s*[,)]|\s*$)/.exec(s.slice(this.i));
    if (kw) {
      this.i += kw[0].length;
      return kw[1] === 'true' ? true : kw[1] === 'false' ? false : null;
    }
    const call = /^([A-Za-z_][A-Za-z0-9_]*)\s*\(/.exec(s.slice(this.i));
    if (call) {
      this.i += call[0].length;
      const args = {};
      this.skipWs();
      if (s[this.i] === ')') {
        this.i++;
        return this.invoke(call[1], args);
      }
      for (;;) {
        this.skipWs();
        const nameM = /^([A-Za-z_][A-Za-z0-9_]*)\s*:/.exec(s.slice(this.i));
        if (!nameM) throw new Error(`expected named argument in ${call[1]}()`);
        this.i += nameM[0].length;
        args[nameM[1]] = this.parseExpr();
        this.skipWs();
        if (s[this.i] === ',') {
          this.i++;
          continue;
        }
        if (s[this.i] === ')') {
          this.i++;
          break;
        }
        throw new Error(`expected , or ) in ${call[1]}()`);
      }
      return this.invoke(call[1], args);
    }
    // Path: read until a delimiter that can end an argument.
    const m = /^[^,()\s]+/.exec(s.slice(this.i));
    if (!m) throw new Error('empty expression');
    this.i += m[0].length;
    return this.ctx.getData(resolvePath(this.ctx.scope, m[0]));
  }
  parseString(q) {
    let out = '';
    this.i++;
    while (this.i < this.s.length && this.s[this.i] !== q) {
      if (this.s[this.i] === '\\' && this.i + 1 < this.s.length) {
        out += this.s[this.i + 1];
        this.i += 2;
        continue;
      }
      out += this.s[this.i++];
    }
    this.i++;
    return out;
  }
  invoke(name, args) {
    if (name === 'formatString') return formatString(args.value, this.ctx);
    const fn = FUNCTIONS[name];
    if (!fn) {
      noteError(this.ctx, `unknown function "${name}"`);
      return undefined;
    }
    return fn(args, this.ctx);
  }
}

// ---------------------------------------------------------------------------
// Dates.

/**
 * Parses Date | epoch ms | ISO string. Date-only ("2026-01-16") and
 * zone-less date-times are interpreted as local wall time; time-only
 * ("14:30") is today at that time.
 */
export function parseDate(v) {
  if (v == null || v === '') return null;
  if (v instanceof Date) return Number.isNaN(v.getTime()) ? null : v;
  if (typeof v === 'number') return new Date(v);
  const s = String(v).trim();
  let m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
  m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(s);
  if (m) {
    const d = new Date();
    d.setHours(+m[1], +m[2], +(m[3] || 0), 0);
    return d;
  }
  m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$/
          .exec(s);
  if (m) return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
  if (/^\d+$/.test(s)) return new Date(Number(s));
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

const pad = (n, w) => String(n).padStart(w, '0');

/**
 * Unicode TR35 subset: G y Y u M L d E c a h H k K m s S z Z, quoted literals.
 */
export function formatDateTR35(d, pattern, locale = DEFAULT_LOCALE) {
  const name = (opts) => new Intl.DateTimeFormat(locale, opts).format(d);
  let out = '';
  let i = 0;
  while (i < pattern.length) {
    const c = pattern[i];
    if (c === '\'') {
      if (pattern[i + 1] === '\'') {
        out += '\'';
        i += 2;
        continue;
      }
      const end = pattern.indexOf('\'', i + 1);
      out += end < 0 ? pattern.slice(i + 1) : pattern.slice(i + 1, end);
      i = end < 0 ? pattern.length : end + 1;
      continue;
    }
    if (!/[A-Za-z]/.test(c)) {
      out += c;
      i++;
      continue;
    }
    let n = 1;
    while (pattern[i + n] === c) n++;
    i += n;
    const h = d.getHours();
    switch (c) {
      case 'G':
        out += d.getFullYear() > 0 ? 'AD' : 'BC';
        break;
      case 'y':
      case 'Y':
      case 'u':
        out +=
            n === 2 ? pad(d.getFullYear() % 100, 2) : pad(d.getFullYear(), n);
        break;
      case 'M':
      case 'L':
        if (n === 1)
          out += d.getMonth() + 1;
        else if (n === 2)
          out += pad(d.getMonth() + 1, 2);
        else if (n === 3)
          out += name({month: 'short'});
        else if (n === 4)
          out += name({month: 'long'});
        else
          out += name({month: 'narrow'});
        break;
      case 'd':
        out += n === 1 ? d.getDate() : pad(d.getDate(), 2);
        break;
      case 'D': {
        const start = new Date(d.getFullYear(), 0, 1);
        out += pad(Math.floor((d - start) / 864e5) + 1, n);
        break;
      }
      case 'E':
      case 'c':
      case 'e':
        if ((c !== 'E') && n <= 2) {
          out += d.getDay() || 7;
          break;
        }
        out += name({weekday: n === 4 ? 'long' : n === 5 ? 'narrow' : 'short'});
        break;
      case 'a':
      case 'b':
      case 'B':
        out += h < 12 ? 'AM' : 'PM';
        break;
      case 'h':
        out += pad(h % 12 === 0 ? 12 : h % 12, Math.min(n, 2));
        break;
      case 'H':
        out += pad(h, Math.min(n, 2));
        break;
      case 'k':
        out += pad(h === 0 ? 24 : h, Math.min(n, 2));
        break;
      case 'K':
        out += pad(h % 12, Math.min(n, 2));
        break;
      case 'm':
        out += pad(d.getMinutes(), Math.min(n, 2));
        break;
      case 's':
        out += pad(d.getSeconds(), Math.min(n, 2));
        break;
      case 'S':
        out += pad(d.getMilliseconds(), 3).slice(0, n);
        break;
      case 'z':
      case 'Z':
      case 'O':
      case 'v':
      case 'V':
      case 'X':
      case 'x': {
        const parts = new Intl.DateTimeFormat(locale, {timeZoneName: 'short'})
                          .formatToParts(d);
        out += (parts.find((p) => p.type === 'timeZoneName') || {}).value || '';
        break;
      }
      default:
        out += c.repeat(n);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Checks.

/**
 * Evaluates a Checkable's `checks` array. Accepts CheckRule
 * {condition, message} and the legacy inline form {call, args, message}.
 * Returns the list of failing messages.
 */
export function evaluateChecks(checks, ctx) {
  const failing = [];
  for (const rule of Array.isArray(checks) ? checks : []) {
    if (!rule || typeof rule !== 'object') continue;
    const cond = 'condition' in rule ?
        rule.condition :
        (typeof rule.call === 'string' ? {call: rule.call, args: rule.args} :
                                         true);
    let ok;
    try {
      ok = !!resolveDynamic(cond, ctx);
    } catch {
      ok = false;
    }
    if (!ok)
      failing.push(
          typeof rule.message === 'string' ? rule.message : 'Invalid value');
  }
  return failing;
}

/** Resolves an action's event context into plain JSON. */
export function resolveActionContext(context, ctx) {
  const out = {};
  for (const [k, v] of Object.entries(context || {})) {
    const r = resolveDynamic(v, ctx);
    out[k] = r === undefined ? null : r;
  }
  return out;
}
