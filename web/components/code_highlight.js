// Lightweight, zero-dependency HTML & CSS formatter and syntax highlighter
// for A2UI Designer's warm editorial palette.

const VOID_TAGS = new Set(['img', 'hr', 'br']);
const INLINE_PHRASE_TAGS = new Set(['strong', 'em', 'small']);
const DIRECTIVES = new Set([
  'data-if',
  'data-unless',
  'data-each',
  'data-action',
  'data-icon',
  'data-bg',
]);
const CSS_FUNCTIONS = new Set([
  'var',
  'calc',
  'min',
  'max',
  'clamp',
  'minmax',
  'repeat',
  'fit-content',
  'rgb',
  'rgba',
  'hsl',
  'hsla',
  'color-mix',
  'linear-gradient',
  'radial-gradient',
  'conic-gradient',
  'url',
  'translate',
  'translateX',
  'translateY',
  'scale',
  'rotate',
]);
const CSS_KEYWORDS = new Set([
  'auto',         'block',        'border-box',    'both',
  'break-word',   'center',       'clip',          'collapse',
  'column',       'contain',      'content-box',   'cover',
  'currentcolor', 'currentColor', 'dashed',        'dotted',
  'ease',         'ease-in',      'ease-in-out',   'ease-out',
  'ellipsis',     'end',          'fixed',         'flex',
  'flex-end',     'flex-start',   'grid',          'hidden',
  'inherit',      'initial',      'inline',        'inline-block',
  'inline-flex',  'inline-grid',  'italic',        'justify',
  'left',         'linear',       'middle',        ' monospace',
  'no-repeat',    'none',         'normal',        'nowrap',
  'pointer',      'pre',          'pre-line',      'pre-wrap',
  'relative',     'repeat',       'right',         'row',
  ' row-reverse', 'sans-serif',   'scroll',        ' Search',
  'serif',        'solid',        'space-around',  'space-between',
  'space-evenly', 'start',        'static',        'sticky',
  'stretch',      'subgrid',      ' tabular-nums', 'text',
  'top',          'transparent',  'underline',     'unset',
  'uppercase',    'visible',      'wrap',
]);

function span(cls, text) {
  const el = document.createElement('span');
  el.className = cls;
  el.textContent = text;
  return el;
}

function appendTextWithBindings(parent, text, baseCls = '') {
  if (!text) return;
  const bindRe = /(\{\{\s*[A-Za-z_]\w*(?:\.\w+)*\s*\}\})/g;
  let last = 0;
  let m;
  while ((m = bindRe.exec(text)) !== null) {
    if (m.index > last) {
      const pre = text.slice(last, m.index);
      parent.appendChild(
          baseCls ? span(baseCls, pre) : document.createTextNode(pre));
    }
    parent.appendChild(span('hl-bind', m[1]));
    last = m.index + m[1].length;
  }
  if (last < text.length) {
    const rest = text.slice(last);
    parent.appendChild(
        baseCls ? span(baseCls, rest) : document.createTextNode(rest));
  }
}

/**
 * Tokenizes an HTML template string into a DocumentFragment with .hl-* spans.
 */
export function highlightHtml(src) {
  const frag = document.createDocumentFragment();
  const s = String(src ?? '');
  if (!s) return frag;

  const tagRe = /<!--[\s\S]*?-->|<\/?[A-Za-z][A-Za-z0-9-]*\b[^>]*>/g;
  let last = 0;
  let m;
  while ((m = tagRe.exec(s)) !== null) {
    if (m.index > last) {
      appendTextWithBindings(frag, s.slice(last, m.index));
    }
    const tok = m[0];
    if (tok.startsWith('<!--')) {
      frag.appendChild(span('hl-comment', tok));
    } else {
      highlightHtmlTag(frag, tok);
    }
    last = m.index + tok.length;
  }
  if (last < s.length) {
    appendTextWithBindings(frag, s.slice(last));
  }
  return frag;
}

function highlightHtmlTag(parent, rawTag) {
  const openMatch =
      rawTag.match(/^(\s*<\/?)([A-Za-z][A-Za-z0-9-]*)([\s\S]*?)(\/?>)$/);
  if (!openMatch) {
    parent.appendChild(document.createTextNode(rawTag));
    return;
  }
  const [, openBracket, tagName, attrPart, closeBracket] = openMatch;
  const lowerTag = tagName.toLowerCase();
  const tagCls = lowerTag === 'slot' ? 'hl-slot' : 'hl-tag';

  parent.appendChild(span('hl-punc', openBracket));
  parent.appendChild(span(tagCls, tagName));

  if (attrPart) {
    const attrRe =
        /([A-Za-z_:][A-Za-z0-9_.:-]*)(\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?/g;
    let last = 0;
    let am;
    while ((am = attrRe.exec(attrPart)) !== null) {
      if (am.index > last) {
        parent.appendChild(
            document.createTextNode(attrPart.slice(last, am.index)));
      }
      const attrName = am[1];
      const lowerAttr = attrName.toLowerCase();
      const attrCls = DIRECTIVES.has(lowerAttr) ||
              (lowerTag === 'slot' && lowerAttr === 'name') ?
          'hl-dir' :
          'hl-attr';
      parent.appendChild(span(attrCls, attrName));

      if (am[2]) {
        const eqMatch = am[2].match(/^(\s*=\s*)([\s\S]*)$/);
        if (eqMatch) {
          parent.appendChild(span('hl-punc', eqMatch[1]));
          const valRaw = eqMatch[2];
          if ((valRaw.startsWith('"') && valRaw.endsWith('"')) ||
              (valRaw.startsWith('\'') && valRaw.endsWith('\''))) {
            const q = valRaw[0];
            const inner = valRaw.slice(1, -1);
            parent.appendChild(span('hl-punc', q));
            appendTextWithBindings(parent, inner, 'hl-str');
            parent.appendChild(span('hl-punc', q));
          } else {
            appendTextWithBindings(parent, valRaw, 'hl-str');
          }
        } else {
          parent.appendChild(span('hl-str', am[2]));
        }
      }
      last = am.index + am[0].length;
    }
    if (last < attrPart.length) {
      parent.appendChild(document.createTextNode(attrPart.slice(last)));
    }
  }

  parent.appendChild(span('hl-punc', closeBracket));
}

/**
 * Tokenizes a CSS stylesheet string into a DocumentFragment with .hl-* spans.
 */
export function highlightCss(src) {
  const frag = document.createDocumentFragment();
  const s = String(src ?? '');
  if (!s) return frag;

  let i = 0;
  const n = s.length;
  let inBlock = 0;
  let afterColon = false;

  while (i < n) {
    // Whitespace
    if (/\s/.test(s[i])) {
      let j = i + 1;
      while (j < n && /\s/.test(s[j])) j++;
      frag.appendChild(document.createTextNode(s.slice(i, j)));
      i = j;
      continue;
    }
    // Block comment /* ... */
    if (s[i] === '/' && s[i + 1] === '*') {
      const end = s.indexOf('*/', i + 2);
      const j = end >= 0 ? end + 2 : n;
      frag.appendChild(span('hl-comment', s.slice(i, j)));
      i = j;
      continue;
    }
    // Quoted string
    if (s[i] === '"' || s[i] === '\'') {
      const q = s[i];
      let j = i + 1;
      while (j < n) {
        if (s[j] === '\\') {
          j += 2;
          continue;
        }
        if (s[j] === q) {
          j++;
          break;
        }
        j++;
      }
      frag.appendChild(span('hl-str', s.slice(i, j)));
      i = j;
      continue;
    }
    // Structural punctuation
    if (s[i] === '{') {
      inBlock++;
      afterColon = false;
      frag.appendChild(span('hl-punc', '{'));
      i++;
      continue;
    }
    if (s[i] === '}') {
      inBlock = Math.max(0, inBlock - 1);
      afterColon = false;
      frag.appendChild(span('hl-punc', '}'));
      i++;
      continue;
    }
    if (s[i] === ';') {
      afterColon = false;
      frag.appendChild(span('hl-punc', ';'));
      i++;
      continue;
    }
    if (s[i] === ':' && inBlock > 0 && !afterColon) {
      afterColon = true;
      frag.appendChild(span('hl-punc', ':'));
      i++;
      continue;
    }
    if (s[i] === '(' || s[i] === ')' || s[i] === ',') {
      frag.appendChild(span('hl-punc', s[i]));
      i++;
      continue;
    }

    // Outside declaration block (or nested @media selector header): selector
    // mode
    if (inBlock === 0 || (!afterColon && isNestedRuleAhead(s, i))) {
      // Read until '{', ',', comment, or pseudo-selector
      if (s[i] === '@') {
        let j = i + 1;
        while (j < n && /[A-Za-z0-9_-]/.test(s[j])) j++;
        frag.appendChild(span('hl-at', s.slice(i, j)));
        i = j;
        continue;
      }
      if (s[i] === ':') {
        let j = i + 1;
        if (s[j] === ':') j++;
        while (j < n && /[A-Za-z0-9_-]/.test(s[j])) j++;
        frag.appendChild(span('hl-pseudo', s.slice(i, j)));
        i = j;
        continue;
      }
      let j = i;
      while (j < n && !/[\s{,:/*()"']/.test(s[j])) j++;
      if (j > i) {
        frag.appendChild(span('hl-sel', s.slice(i, j)));
        i = j;
      } else {
        frag.appendChild(span('hl-punc', s[i]));
        i++;
      }
      continue;
    }

    // Inside declaration block before ':': property name (or custom property
    // --*)
    if (!afterColon) {
      let j = i;
      while (j < n && /[A-Za-z0-9_-]/.test(s[j])) j++;
      if (j > i) {
        const tok = s.slice(i, j);
        frag.appendChild(
            span(tok.startsWith('--') ? 'hl-var' : 'hl-prop', tok));
        i = j;
      } else {
        frag.appendChild(span('hl-punc', s[i]));
        i++;
      }
      continue;
    }

    // Inside declaration value (after ':')
    // Template binding {{...}} if any
    if (s[i] === '{' && s[i + 1] === '{') {
      const end = s.indexOf('}}', i + 2);
      if (end >= 0) {
        frag.appendChild(span('hl-bind', s.slice(i, end + 2)));
        i = end + 2;
        continue;
      }
    }
    // Hex color
    if (s[i] === '#') {
      let j = i + 1;
      while (j < n && /[0-9a-fA-F]/.test(s[j])) j++;
      if (j > i + 1) {
        frag.appendChild(span('hl-hex', s.slice(i, j)));
        i = j;
        continue;
      }
    }
    // Custom property --a2ui-*
    if (s[i] === '-' && s[i + 1] === '-') {
      let j = i + 2;
      while (j < n && /[A-Za-z0-9_-]/.test(s[j])) j++;
      frag.appendChild(span('hl-var', s.slice(i, j)));
      i = j;
      continue;
    }
    // Number + optional unit (px, rem, em, %, vh, vw, s, ms, deg, fr)
    const numMatch = s.slice(i).match(
        /^-?(?:\d+\.?\d*|\.\d+)(?:px|rem|em|%|vh|vw|vmin|vmax|ch|ex|s|ms|deg|rad|turn|fr|dpi)?\b/i);
    if (numMatch && (i === 0 || !/[A-Za-z_]/.test(s[i - 1]))) {
      frag.appendChild(span('hl-num', numMatch[0]));
      i += numMatch[0].length;
      continue;
    }
    // Identifier / function / keyword
    const idMatch = s.slice(i).match(/^[A-Za-z_][A-Za-z0-9_-]*/);
    if (idMatch) {
      const word = idMatch[0];
      const nextCh = s[i + word.length];
      if (nextCh === '(' || CSS_FUNCTIONS.has(word.toLowerCase())) {
        if (word.toLowerCase() === 'url') {
          frag.appendChild(span('hl-fn', word));
          i += word.length;
          // Consume (data:...) as string
          if (s[i] === '(') {
            frag.appendChild(span('hl-punc', '('));
            i++;
            let j = i;
            while (j < n && s[j] !== ')') j++;
            if (j > i) frag.appendChild(span('hl-str', s.slice(i, j)));
            i = j;
          }
          continue;
        }
        frag.appendChild(span('hl-fn', word));
      } else if (CSS_KEYWORDS.has(word.toLowerCase())) {
        frag.appendChild(span('hl-kw', word));
      } else {
        frag.appendChild(document.createTextNode(word));
      }
      i += word.length;
      continue;
    }
    // Operators (+, -, *, /, !important, etc.)
    if (s.slice(i).startsWith('!important')) {
      frag.appendChild(span('hl-at', '!important'));
      i += 10;
      continue;
    }
    frag.appendChild(span('hl-punc', s[i]));
    i++;
  }
  return frag;
}

function isNestedRuleAhead(s, idx) {
  for (let k = idx; k < s.length; k++) {
    if (s[k] === '{') return true;
    if (s[k] === ';' || s[k] === '}') return false;
  }
  return false;
}

/**
 * Pretty-prints an HTML template string with 2-space indentation per nesting
 * level while keeping short inline/leaf elements on a single line.
 */
export function formatHtml(src) {
  const raw = String(src ?? '').trim();
  if (!raw) return '';
  const root = {type: 'root', children: []};
  const stack = [root];
  const tokenRe = /<!--[\s\S]*?-->|<\/?[A-Za-z][A-Za-z0-9-]*\b[^>]*>|[^<]+/g;
  let m;
  while ((m = tokenRe.exec(raw)) !== null) {
    const tok = m[0];
    if (tok.startsWith('<!--')) continue;
    if (tok.startsWith('</')) {
      const em = tok.match(/^<\/\s*([A-Za-z][A-Za-z0-9-]*)/);
      if (!em) continue;
      const tag = em[1].toLowerCase();
      if (VOID_TAGS.has(tag)) continue;
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tag === tag) {
          stack.length = i;
          break;
        }
      }
    } else if (tok.startsWith('<')) {
      const sm = tok.match(/^<\s*([A-Za-z][A-Za-z0-9-]*)([\s\S]*?)(\/?)>$/);
      if (!sm) continue;
      const tag = sm[1].toLowerCase();
      const attrPart = sm[2].replace(/\s+/g, ' ').trim();
      const selfClosing = Boolean(sm[3]) || VOID_TAGS.has(tag);
      const isVoid = VOID_TAGS.has(tag);
      const node = {
        type: 'el',
        tag,
        attrTxt: attrPart ? ` ${attrPart}` : '',
        void: isVoid,
        children: [],
      };
      stack[stack.length - 1].children.push(node);
      if (!selfClosing) stack.push(node);
    } else {
      const ch = stack[stack.length - 1].children;
      if (ch.length && ch[ch.length - 1].type === 'text') {
        ch[ch.length - 1].text += tok;
      } else {
        ch.push({type: 'text', text: tok});
      }
    }
  }

  function canInline(children) {
    if (!children.length) return [true, ''];
    const parts = [];
    for (const c of children) {
      if (c.type === 'text') {
        parts.push(c.text.replace(/\s+/g, ' '));
      } else if (
          c.type === 'el' && INLINE_PHRASE_TAGS.has(c.tag) && !c.void &&
          c.children.every((gc) => gc.type === 'text')) {
        const inner = c.children.map((gc) => gc.text.replace(/\s+/g, ' '))
                          .join('')
                          .trim();
        parts.push(`<${c.tag}${c.attrTxt}>${inner}</${c.tag}>`);
      } else {
        return [false, ''];
      }
    }
    const joined = parts.join('').trim();
    if (joined.length <= 90 && !joined.includes('\n')) return [true, joined];
    return [false, ''];
  }

  function renderEl(node, depth) {
    const pad = '  '.repeat(depth);
    if (node.void) return `${pad}<${node.tag}${node.attrTxt}>`;
    const [ok, inlineStr] = canInline(node.children);
    if (ok)
      return `${pad}<${node.tag}${node.attrTxt}>${inlineStr}</${node.tag}>`;
    const lines = [];
    for (const c of node.children) {
      if (c.type === 'text') {
        const t = c.text.replace(/\s+/g, ' ').trim();
        if (t) lines.push(`${pad}  ${t}`);
      } else {
        lines.push(renderEl(c, depth + 1));
      }
    }
    if (!lines.length)
      return `${pad}<${node.tag}${node.attrTxt}></${node.tag}>`;
    return `${pad}<${node.tag}${node.attrTxt}>\n${lines.join('\n')}\n${pad}</${
        node.tag}>`;
  }

  const out = [];
  for (const c of root.children) {
    if (c.type === 'text') {
      const t = c.text.replace(/\s+/g, ' ').trim();
      if (t) out.push(t);
    } else {
      out.push(renderEl(c, 0));
    }
  }
  return out.join('\n').trim();
}

function splitCssTop(text, sep) {
  const parts = [];
  let cur = '';
  let paren = 0;
  let quote = '';
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quote) {
      cur += ch;
      if (ch === '\\' && i + 1 < text.length) {
        cur += text[++i];
      } else if (ch === quote) {
        quote = '';
      }
      continue;
    }
    if (ch === '/' && text[i + 1] === '*') {
      const end = text.indexOf('*/', i + 2);
      if (end < 0) {
        cur += text.slice(i);
        break;
      }
      cur += text.slice(i, end + 2);
      i = end + 1;
      continue;
    }
    if (ch === '"' || ch === '\'') {
      quote = ch;
      cur += ch;
      continue;
    }
    if (ch === '(')
      paren++;
    else if (ch === ')' && paren > 0)
      paren--;
    if (paren === 0 && ch === sep) {
      parts.push(cur);
      cur = '';
      continue;
    }
    cur += ch;
  }
  if (cur) parts.push(cur);
  return parts;
}

/**
 * Pretty-prints scoped CSS rules across multiple lines with 2-space indent.
 */
export function formatCss(src, indent = '') {
  const raw = String(src ?? '').trim();
  if (!raw) return '';
  const blocks = [];
  let i = 0;
  const n = raw.length;
  while (i < n) {
    while (i < n && /\s/.test(raw[i])) i++;
    if (i >= n) break;
    if (raw.startsWith('/*', i)) {
      const end = raw.indexOf('*/', i + 2);
      if (end < 0) {
        blocks.push(`${indent}${raw.slice(i).trim()}`);
        break;
      }
      blocks.push(`${indent}${raw.slice(i, end + 2).trim()}`);
      i = end + 2;
      continue;
    }
    const selStart = i;
    let paren = 0;
    let quote = '';
    let bracePos = -1;
    while (i < n) {
      const ch = raw[i];
      if (quote) {
        if (ch === '\\' && i + 1 < n) {
          i += 2;
          continue;
        }
        if (ch === quote) quote = '';
        i++;
        continue;
      }
      if (ch === '/' && raw[i + 1] === '*') {
        const end = raw.indexOf('*/', i + 2);
        i = end >= 0 ? end + 2 : n;
        continue;
      }
      if (ch === '"' || ch === '\'') {
        quote = ch;
        i++;
        continue;
      }
      if (ch === '(')
        paren++;
      else if (ch === ')' && paren > 0)
        paren--;
      else if (ch === '{' && paren === 0) {
        bracePos = i;
        break;
      }
      i++;
    }
    if (bracePos < 0) {
      const rem = raw.slice(selStart).trim();
      if (rem) blocks.push(`${indent}${rem}`);
      break;
    }
    const selector = raw.slice(selStart, bracePos).replace(/\s+/g, ' ').trim();
    i = bracePos + 1;
    const bodyStart = i;
    let depth = 1;
    while (i < n && depth > 0) {
      const ch = raw[i];
      if (quote) {
        if (ch === '\\' && i + 1 < n) {
          i += 2;
          continue;
        }
        if (ch === quote) quote = '';
        i++;
        continue;
      }
      if (ch === '/' && raw[i + 1] === '*') {
        const end = raw.indexOf('*/', i + 2);
        i = end >= 0 ? end + 2 : n;
        continue;
      }
      if (ch === '"' || ch === '\'') {
        quote = ch;
        i++;
        continue;
      }
      if (ch === '{')
        depth++;
      else if (ch === '}') {
        depth--;
        if (depth === 0) break;
      }
      i++;
    }
    const body = raw.slice(bodyStart, i).trim();
    if (i < n && raw[i] === '}') i++;
    if (!body) {
      blocks.push(`${indent}${selector} {}`);
      continue;
    }
    if (body.includes('{') && selector.startsWith('@')) {
      const nested = formatCss(body, `${indent}  `);
      blocks.push(`${indent}${selector} {\n${nested}\n${indent}}`);
      continue;
    }
    const decls = [];
    for (const part of splitCssTop(body, ';')) {
      const p = part.trim();
      if (!p) continue;
      const colonIdx = p.indexOf(':');
      if (colonIdx > 0 && !p.startsWith('/*')) {
        const k = p.slice(0, colonIdx).trim();
        const v = p.slice(colonIdx + 1).replace(/\s+/g, ' ').trim();
        decls.push(`${indent}  ${k}: ${v};`);
      } else {
        decls.push(`${indent}  ${p};`);
      }
    }
    if (decls.length) {
      blocks.push(`${indent}${selector} {\n${decls.join('\n')}\n${indent}}`);
    } else {
      blocks.push(`${indent}${selector} {}`);
    }
  }
  return indent ? blocks.join('\n') : blocks.join('\n\n');
}

/**
 * Wraps a <textarea> in a syntax-highlighted overlay container
 * (.code-hl-editor) while keeping the <textarea> itself interactive with its
 * original id/events.
 */
export function enhanceCodeEditor(textarea, {lang = 'html'} = {}) {
  const wrap = document.createElement('div');
  wrap.className = `code-hl-editor code-hl-${lang}`;
  const pre = document.createElement('pre');
  pre.className = 'code-hl-backdrop';
  pre.setAttribute('aria-hidden', 'true');
  const code = document.createElement('code');
  code.className = `code-hl-code lang-${lang}`;
  pre.appendChild(code);

  textarea.classList.add('code-hl-input');

  const protoDesc =
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');

  function syncHighlight() {
    const val = protoDesc ? protoDesc.get.call(textarea) : textarea.value;
    code.textContent = '';
    const frag = lang === 'css' ? highlightCss(val) : highlightHtml(val);
    code.appendChild(frag);
    if (val.endsWith('\n')) {
      code.appendChild(document.createTextNode(' '));
    }
    syncScroll();
  }

  function syncScroll() {
    pre.scrollTop = textarea.scrollTop;
    pre.scrollLeft = textarea.scrollLeft;
  }

  if (protoDesc && protoDesc.get && protoDesc.set) {
    Object.defineProperty(textarea, 'value', {
      configurable: true,
      enumerable: true,
      get() {
        return protoDesc.get.call(this);
      },
      set(v) {
        protoDesc.set.call(this, v);
        syncHighlight();
      },
    });
  }

  textarea.addEventListener('input', syncHighlight);
  textarea.addEventListener('scroll', syncScroll);

  wrap.append(pre, textarea);
  syncHighlight();
  return {wrap, sync: syncHighlight};
}
