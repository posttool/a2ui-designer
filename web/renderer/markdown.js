// Tiny safe Markdown subset -> AST (DOM-free). The DOM builder in a2ui.js
// only ever creates elements + text nodes from this AST (never innerHTML).
//
// Blocks: paragraph (single newlines -> line breaks), heading (#..######),
// bullet list (-, *, +), ordered list (1.), blockquote (>), code fence.
// Inlines: **bold** __bold__ *italic* _italic_ ~~strike~~ `code`
// [text](http(s)/mailto url), backslash escapes.

export function parseMarkdown(text) {
  const lines = String(text ?? '').replace(/\r\n?/g, '\n').split('\n');
  // Single-line text only gets inline markup + heading markers stripped;
  // a lone "- foo" or "> foo" is literal text, not a list/quote.
  const multiline = lines.length > 1;
  const blocks = [];
  let para = null;
  let list = null;
  const flush = () => {
    if (para) blocks.push({type: 'p', inlines: parseInline(para.join('\n'))});
    para = null;
  };
  const endList = () => {
    list = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flush();
      endList();
      const body = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i]))
        body.push(lines[i++]);
      blocks.push({type: 'code', text: body.join('\n')});
      continue;
    }
    if (!line.trim()) {
      flush();
      endList();
      continue;
    }
    let m = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
    if (m) {
      flush();
      endList();
      blocks.push({type: 'h', level: m[1].length, inlines: parseInline(m[2])});
      continue;
    }
    m = /^\s*([-*+])\s+(.*)$/.exec(line);
    const om = /^\s*(\d{1,3})[.)]\s+(.*)$/.exec(line);
    if (multiline && ((m && !/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) || om)) {
      flush();
      const type = m ? 'ul' : 'ol';
      if (!list || list.type !== type) {
        list = {type, items: [], start: om ? Number(om[1]) : 1};
        blocks.push(list);
      }
      list.items.push(parseInline((m || om)[2]));
      continue;
    }
    if (multiline && /^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flush();
      endList();
      blocks.push({type: 'hr'});
      continue;
    }
    m = multiline ? /^\s*>\s?(.*)$/.exec(line) : null;
    if (m) {
      flush();
      endList();
      const last = blocks[blocks.length - 1];
      if (last && last.type === 'quote')
        last.raw.push(m[1]);
      else
        blocks.push({type: 'quote', raw: [m[1]]});
      continue;
    }
    if (list && /^\s{2,}\S/.test(line)) {
      // Continuation of the previous list item.
      const items = list.items;
      items[items.length - 1].push({t: 'br'}, ...parseInline(line.trim()));
      continue;
    }
    endList();
    (para ||= []).push(line);
  }
  flush();
  for (const b of blocks) {
    if (b.type === 'quote') {
      b.inlines = parseInline(b.raw.join('\n'));
      delete b.raw;
    }
  }
  return blocks;
}

const SAFE_SCHEMES = /^(https?:|mailto:|tel:)/i;

export function safeLinkHref(href) {
  const h = String(href || '').trim();
  if (SAFE_SCHEMES.test(h)) return h;
  if (/^[/#?]/.test(h) && !h.startsWith('//')) return h;  // relative
  return null;
}

/**
 * Parses inline markdown into
 * [{t:'text'|'strong'|'em'|'del'|'code'|'link'|'br', ...}].
 */
export function parseInline(src) {
  const out = [];
  let buf = '';
  const pushText = () => {
    if (buf) out.push({t: 'text', v: buf});
    buf = '';
  };
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    const rest = src.slice(i);
    if (c === '\\' && i + 1 < src.length &&
        /[\\`*_{}\[\]()#+\-.!~>|$]/.test(src[i + 1])) {
      buf += src[i + 1];
      i += 2;
      continue;
    }
    if (c === '\n') {
      pushText();
      out.push({t: 'br'});
      i++;
      continue;
    }
    if (c === '`') {
      const end = src.indexOf('`', i + 1);
      if (end > i) {
        pushText();
        out.push({t: 'code', v: src.slice(i + 1, end)});
        i = end + 1;
        continue;
      }
    }
    if (c === '[') {
      const m = /^\[([^\]]+)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)/.exec(rest);
      if (m) {
        pushText();
        const href = safeLinkHref(m[2]);
        const children = parseInline(m[1]);
        out.push(
            href ? {t: 'link', href, c: children} : {t: 'span', c: children});
        i += m[0].length;
        continue;
      }
    }
    let m = /^(\*\*|__)(?=\S)([\s\S]*?\S)\1/.exec(rest);
    if (m && (m[1] === '**' || isWordBoundary(src, i, m[0].length))) {
      pushText();
      out.push({t: 'strong', c: parseInline(m[2])});
      i += m[0].length;
      continue;
    }
    m = /^~~(?=\S)([\s\S]*?\S)~~/.exec(rest);
    if (m) {
      pushText();
      out.push({t: 'del', c: parseInline(m[1])});
      i += m[0].length;
      continue;
    }
    m = /^(\*|_)(?=[^\s*_])([\s\S]*?[^\s\\])\1(?!\1)/.exec(rest);
    if (m && (m[1] === '*' || isWordBoundary(src, i, m[0].length))) {
      pushText();
      out.push({t: 'em', c: parseInline(m[2])});
      i += m[0].length;
      continue;
    }
    buf += c;
    i++;
  }
  pushText();
  return out;
}

function isWordBoundary(src, start, len) {
  const before = start > 0 ? src[start - 1] : ' ';
  const after = start + len < src.length ? src[start + len] : ' ';
  return !/[A-Za-z0-9]/.test(before) && !/[A-Za-z0-9]/.test(after);
}

/** Plain-text rendering of an inline AST (for aria labels / tests). */
export function inlineText(nodes) {
  return nodes
      .map(
          (n) => n.t === 'text' || n.t === 'code' ? n.v :
              n.t === 'br'                        ? '\n' :
                                                    inlineText(n.c || []))
      .join('');
}
