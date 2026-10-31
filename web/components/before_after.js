// Before vs. After overview for a teaching session: one column per card
// (title · before · after · rule chips attributed by the effect study ·
// overall verdict · unapplied rules → trial recorded).
import {h, icon} from './dom.js';

const OVERALL = {
  improved: ['imp', 'trending_up', 'improved'],
  better: ['imp', 'trending_up', 'improved'],
  mixed: ['mix', 'swap_vert', 'mixed'],
  neutral: ['neu', 'remove', 'neutral'],
  unchanged: ['neu', 'remove', 'unchanged'],
  worse: ['wor', 'trending_down', 'worse'],
  regressed: ['wor', 'trending_down', 'worse'],
};

/**
 * beforeAfter({columns: [{title, before: {url, label}, after: {url, label},
 *   pair: {summary, overall, changes, unapplied_rules}}], ridLabel(id) →
 *   {label, kind}, onColumn(i)})
 */
export function beforeAfter({columns, ridLabel, onColumn}) {
  const rl = ridLabel || ((id) => ({label: id, kind: 'rule'}));
  const chip = (id) => {
    const r = rl(id) || {label: id, kind: 'rule'};
    return h(
        'span.rid',
        {class: r.kind === 'pattern' ? 'tpl' : '', title: r.title || id},
        r.label || id);
  };
  const shot = (s) => s && s.url ?
      h('div.shot', {style: {backgroundImage: `url("${s.url}")`}}) :
      h('div.shot.shot-wait', s && s.pending ? h('span.spinner') : '\u2014');
  return h(
      'div.ba',
      h('table',
        h('thead',
          h('tr',
            columns.map(
                (c, i) =>
                    h('th', {
                      title: c.title,
                      onclick: onColumn ? () => onColumn(i) : null
                    },
                      c.title)))),
        h('tbody',
          h('tr',
            columns.map(
                (c) =>
                    h('td',
                      h('div.lbl', (c.before && c.before.label) || 'Before'),
                      shot(c.before)))),
          h('tr',
            columns.map(
                (c) =>
                    h('td', h('div.lbl', (c.after && c.after.label) || 'After'),
                      shot(c.after)))),
          h('tr', columns.map((c) => {
            const p = c.pair || null;
            if (!p)
              return h(
                  'td', h('div.lbl', 'Attributed to'),
                  h('span.muted.small',
                    c.pairPending ? 'analyzing\u2026' : '\u2014'));
            const ids = new Set();
            (p.changes || [])
                .forEach(
                    (ch) => (ch.rule_ids ||
                             (ch.attribution && ch.attribution.rule_ids) ||
                             []).forEach((r) => ids.add(r)));
            const ov = OVERALL[String(p.overall || '').toLowerCase()] ||
                ['neu', 'remove', p.overall || 'no verdict'];
            const n = (p.changes || []).length;
            const noise = (p.changes || [])
                              .filter(
                                  (ch) => ch.attribution &&
                                      ch.attribution.cause === 'noise')
                              .length;
            return h(
                'td', h('div.lbl', 'Attributed to'),
                ids.size ? h('div.chips', [...ids].map(chip)) :
                           h('div.muted.small', 'no rule cited'),
                h('span.ov-v', {class: ov[0], title: p.summary || ''},
                  icon(ov[1]),
                  `${ov[2]} \u00b7 ${n} change${n === 1 ? '' : 's'}${
                      noise ? `, ${noise} noise` : ''}`),
                (p.unapplied_rules || []).length ?
                    h('div.small.muted', {style: 'margin-top:4px'},
                      (p.unapplied_rules || [])
                          .map((r) => {
                            const id = typeof r === 'string' ?
                                r :
                                (r.rule_id || r.id || '');
                            return rl(id).label || id;
                          })
                          .join(', '),
                      ' unapplied \u2192 trial recorded') :
                    null);
          })))));
}
