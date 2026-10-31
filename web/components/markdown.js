// Small safe markdown renderer → DOM nodes (never innerHTML of user text).
// Supports: ATX headings, paragraphs, bullet/numbered lists (one nesting level
// by indentation), blockquotes, fenced code, inline code, bold, italic, links
// (http(s), relative, mailto only), hr, simple pipe tables, HTML comments
// (rendered as muted guidance blocks).
import {h} from './dom.js';

const SAFE_URL = /^(https?:\/\/|\/|#|mailto:|\.\.?\/)/i;

export function renderInline(text) {
  const out = [];
  // Tokenize: code, links, bold, italic.
  const re =
      /(`[^`]+`)|(\[([^\]]+)\]\(([^)\s]+)\))|(\*\*([^*]+)\*\*)|(__([^_]+)__)|(\*([^*\s][^*]*?)\*)|(_([^_\s][^_]*?)_)/g;
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1])
      out.push(h('code', m[1].slice(1, -1)));
    else if (m[2]) {
      const href = m[4];
      if (SAFE_URL.test(href))
        out.push(
            h('a', {
              href,
              target: href.startsWith('http') ? '_blank' : null,
              rel: 'noopener'
            },
              renderInline(m[3])));
      else
        out.push(m[3]);
    } else if (m[5])
      out.push(h('strong', renderInline(m[6])));
    else if (m[7])
      out.push(h('strong', renderInline(m[8])));
    else if (m[9])
      out.push(h('em', renderInline(m[10])));
    else if (m[11]) {
      // Avoid treating snake_case words as emphasis.
      const before = text[m.index - 1];
      if (before && /\w/.test(before))
        out.push(m[0]);
      else
        out.push(h('em', renderInline(m[12])));
    }
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function isTableSep(line) {
  return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
}
// Multi-line tables continue a row with lines starting with ':', whose
// cells line up with the '|' columns of the separator row.
function columnCuts(sepLine) {
  const cuts = [];
  for (let k = 0; k < sepLine.length; k++) {
    if (sepLine[k] === '|') cuts.push(k);
  }
  return cuts;
}
function sliceCells(line, cuts) {
  const cells = [];
  for (let k = 0; k + 1 < cuts.length; k++) {
    cells.push(line.slice(cuts[k] + 1, cuts[k + 1]).trim());
  }
  return cells;
}
function splitRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map((c) => c.trim());
}

export function renderMarkdown(src, {className = 'md'} = {}) {
  const root = h('div', {class: className});
  const lines = String(src ?? '').replace(/\r\n?/g, '\n').split('\n');
  let i = 0;
  let para = [];
  const flushPara = () => {
    if (para.length) {
      const p = h('p');
      para.forEach((l, idx) => {
        if (idx) p.appendChild(h('br'));
        p.append(...renderInline(l).map(
            (n) => (n instanceof Node ? n : document.createTextNode(n))));
      });
      root.appendChild(p);
      para = [];
    }
  };
  while (i < lines.length) {
    const line = lines[i];
    // fenced code
    const fence = line.match(/^\s*(```|~~~)\s*([\w-]*)\s*$/);
    if (fence) {
      flushPara();
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1]))
        buf.push(lines[i++]);
      i++;
      root.appendChild(
          h('pre',
            h('code', {class: fence[2] ? `lang-${fence[2]}` : null},
              buf.join('\n'))));
      continue;
    }
    // HTML comment (guidance)
    if (line.trim().startsWith('<!--')) {
      flushPara();
      const buf = [line.trim().replace(/^<!--/, '')];
      while (!buf[buf.length - 1].includes('-->') && i + 1 < lines.length)
        buf.push(lines[++i]);
      i++;
      const text = buf.join('\n').replace(/-->\s*$/, '').trim();
      if (text) root.appendChild(h('div.md-comment', renderInline(text)));
      continue;
    }
    if (!line.trim()) {
      flushPara();
      i++;
      continue;
    }
    const hm = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
    if (hm) {
      flushPara();
      root.appendChild(h(`h${Math.min(6, hm[1].length)}`, renderInline(hm[2])));
      i++;
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flushPara();
      root.appendChild(h('hr'));
      i++;
      continue;
    }
    if (/^\s*>/.test(line)) {
      flushPara();
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i]))
        buf.push(lines[i++].replace(/^\s*>\s?/, ''));
      root.appendChild(
          h('blockquote',
            renderMarkdown(buf.join('\n'), {className: 'md-inner'})));
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length &&
        isTableSep(lines[i + 1])) {
      flushPara();
      const head = splitRow(line);
      const cuts = columnCuts(lines[i + 1]);
      const headCont = [];
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim() &&
             (lines[i].includes('|') || /^\s*:/.test(lines[i]))) {
        const l = lines[i++];
        if (/^\s*:/.test(l) && cuts.length > 1) {
          const extra = sliceCells(
              l.replace(/:/g, (m, off) => cuts.includes(off) ? '|' : m), cuts);
          const target = rows.length ? rows[rows.length - 1] : headCont;
          extra.forEach((c, ci) => {
            if (c) target[ci] = target[ci] ? `${target[ci]} ${c}` : c;
          });
        } else {
          rows.push(splitRow(l));
        }
      }
      headCont.forEach((c, ci) => {
        if (c) head[ci] = head[ci] ? `${head[ci]} ${c}` : c;
      });
      root.appendChild(h(
          'div.md-table-wrap',
          h('table.md-table',
            h('thead', h('tr', head.map((c) => h('th', renderInline(c))))),
            h('tbody',
              rows.map(
                  (r) => h(
                      'tr',
                      head.map(
                          (_, ci) => h('td', renderInline(r[ci] || '')))))))));
      continue;
    }
    const lm = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    if (lm) {
      flushPara();
      i = renderList(lines, i, root);
      continue;
    }
    para.push(line.trim());
    i++;
  }
  flushPara();
  return root;
}

function renderList(lines, i, parent) {
  const first = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+/);
  const indent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const list = h(ordered ? 'ol' : 'ul');
  let li = null;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      // blank line: continue list only if next line is a list item at same
      // indent
      const nxt = lines[i + 1];
      if (nxt && /^(\s*)([-*+]|\d+[.)])\s+/.test(nxt) &&
          nxt.match(/^\s*/)[0].length >= indent) {
        i++;
        continue;
      }
      break;
    }
    const m = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    const lead = line.match(/^\s*/)[0].length;
    if (m && lead === indent) {
      let text = m[3];
      const task = text.match(/^\[( |x|X)\]\s+(.*)$/);
      li = h('li');
      if (task) {
        li.appendChild(
            h('input',
              {type: 'checkbox', checked: task[1] !== ' ', disabled: true}));
        text = task[2];
      }
      li.append(...renderInline(text).map(
          (n) => (n instanceof Node ? n : document.createTextNode(n))));
      list.appendChild(li);
      i++;
    } else if (m && lead > indent && li) {
      i = renderList(lines, i, li);
    } else if (lead > indent && li) {
      li.append(
          ' ',
          ...renderInline(line.trim())
              .map(
                  (n) => (n instanceof Node ? n : document.createTextNode(n))));
      i++;
    } else {
      break;
    }
  }
  parent.appendChild(list);
  return i;
}
