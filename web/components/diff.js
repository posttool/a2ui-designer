// Side-by-side diff view from diff.rows (see CONTRACT: {type, a_line, b_line,
// a, b}).
import {highlightCss, highlightHtml} from './code_highlight.js';
import {h} from './dom.js';

function renderLine(text, lang) {
  const s = text ?? '';
  if (!s) return '';
  if (lang === 'html') return highlightHtml(s);
  if (lang === 'css') return highlightCss(s);
  return s;
}

/**
 * diffView(diff, {aLabel, bLabel, context: n|null, lang?: 'html'|'css'})
 * context: when a number, collapse runs of equal rows longer than 2*context.
 */
export function diffView(diff, {
  aLabel = 'Before',
  bLabel = 'After',
  context = 3,
  compact = false,
  lang = '',
} = {}) {
  const rows = (diff && diff.rows) || [];
  const stats = (diff && diff.stats) || {};
  const wrap = h('div.diff', {class: compact ? 'diff-compact' : ''});
  wrap.appendChild(
      h('div.diff-head', h('span.diff-label', aLabel),
        h('span.diff-label', bLabel),
        h('span.diff-stats', h('span.add', `+${stats.added ?? 0}`), ' ',
          h('span.del', `−${stats.removed ?? 0}`))));
  if (!rows.length) {
    wrap.appendChild(h('div.diff-empty', 'No changes.'));
    return wrap;
  }
  const table = h('div.diff-table');
  const emitRow = (r) => {
    const t = r.type;
    const aCls = t === 'delete' || t === 'replace' ? 'del' :
        t === 'insert'                             ? 'blank' :
                                                     '';
    const bCls = t === 'insert' || t === 'replace' ? 'add' :
        t === 'delete'                             ? 'blank' :
                                                     '';
    table.append(
        h('div.diff-num', {class: aCls}, r.a_line ?? ''),
        h('div.diff-cell', {class: aCls},
          t === 'insert' ? '' : renderLine(r.a, lang)),
        h('div.diff-num', {class: bCls}, r.b_line ?? ''),
        h('div.diff-cell', {class: bCls},
          t === 'delete' ? '' : renderLine(r.b, lang)));
  };
  // Collapse long equal runs.
  let i = 0;
  while (i < rows.length) {
    if (rows[i].type === 'equal' && context != null) {
      let j = i;
      while (j < rows.length && rows[j].type === 'equal') j++;
      const runLen = j - i;
      const keepHead = i === 0 ? 0 : context;
      const keepTail = j === rows.length ? 0 : context;
      if (runLen > keepHead + keepTail + 1) {
        rows.slice(i, i + keepHead).forEach(emitRow);
        const hidden = rows.slice(i + keepHead, j - keepTail);
        const fold =
            h('button.diff-fold', {
              type: 'button',
              onclick: () => {
                const frag = document.createDocumentFragment();
                const tmp = table;
                const marker = fold;
                // Expand: insert hidden rows before the marker.
                const cells = [];
                hidden.forEach((r) => {
                  cells.push(
                      h('div.diff-num', r.a_line ?? ''),
                      h('div.diff-cell', renderLine(r.a, lang)),
                      h('div.diff-num', r.b_line ?? ''),
                      h('div.diff-cell', renderLine(r.b, lang)));
                });
                cells.forEach((c) => frag.appendChild(c));
                tmp.insertBefore(frag, marker);
                marker.remove();
              }
            },
              `⋯ ${hidden.length} unchanged line${
                  hidden.length === 1 ? '' : 's'}`);
        table.appendChild(fold);
        rows.slice(j - keepTail, j).forEach(emitRow);
      } else {
        rows.slice(i, j).forEach(emitRow);
      }
      i = j;
    } else {
      emitRow(rows[i]);
      i++;
    }
  }
  wrap.appendChild(table);
  return wrap;
}

/**
 * Client-side line diff (LCS) in the same shape as the server's diff:
 * {rows: [{type: 'equal'|'insert'|'delete'|'replace', a_line, b_line, a, b}],
 *  stats: {added, removed}}. Meant for small texts (templates, CSS).
 */
export function lineDiff(aText, bText) {
  const a = String(aText ?? '').split('\n');
  const b = String(bText ?? '').split('\n');
  const n = a.length;
  const m = b.length;
  // LCS table (n, m are small: component templates / CSS).
  const dp = Array.from({length: n + 1}, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 :
                                 Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({type: 'equal', ai: i++, bj: j++});
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      ops.push({type: 'delete', ai: i++});
    } else {
      ops.push({type: 'insert', bj: j++});
    }
  }
  while (i < n) ops.push({type: 'delete', ai: i++});
  while (j < m) ops.push({type: 'insert', bj: j++});
  // Pair adjacent delete/insert runs into replace rows.
  const rows = [];
  let added = 0;
  let removed = 0;
  for (let k = 0; k < ops.length;) {
    if (ops[k].type === 'equal') {
      const o = ops[k++];
      rows.push({
        type: 'equal',
        a_line: o.ai + 1,
        b_line: o.bj + 1,
        a: a[o.ai],
        b: b[o.bj]
      });
      continue;
    }
    const dels = [];
    const ins = [];
    while (k < ops.length && ops[k].type !== 'equal') {
      (ops[k].type === 'delete' ? dels : ins).push(ops[k]);
      k++;
    }
    removed += dels.length;
    added += ins.length;
    const len = Math.max(dels.length, ins.length);
    for (let x = 0; x < len; x++) {
      const d = dels[x];
      const s = ins[x];
      if (d && s) {
        rows.push({
          type: 'replace',
          a_line: d.ai + 1,
          b_line: s.bj + 1,
          a: a[d.ai],
          b: b[s.bj]
        });
      } else if (d) {
        rows.push({type: 'delete', a_line: d.ai + 1, b_line: null, a: a[d.ai]});
      } else {
        rows.push({type: 'insert', a_line: null, b_line: s.bj + 1, b: b[s.bj]});
      }
    }
  }
  return {rows, stats: {added, removed}};
}
