// Chips & badges: genre (tinted by group), verdicts, judge score, status.
import {cached, invalidate} from './api.js';
import {h, icon} from './dom.js';

const GROUP_TINTS =
    ['tint-1', 'tint-2', 'tint-3', 'tint-4', 'tint-5', 'tint-6', 'tint-7'];
const KNOWN_GROUP_ORDER = [
  'agentic', 'editorial', 'documents', 'apps', 'media', 'communication', 'care',
  'health', 'enterprise'
];

let genreIndex = null;  // genre id → {label, group, groupLabel, tint}
let groupIndex = null;  // group id → {label, tint}

/** Drops cached categories (call after creating/renaming/deleting one). */
export function resetGenres() {
  invalidate('/api/genres');
  genreIndex = null;
  groupIndex = null;
}

export async function loadGenres() {
  if (genreIndex) return genreIndex;
  try {
    const {groups} = await cached('/api/genres');
    genreIndex = {};
    groupIndex = {};
    groups.forEach((g, gi) => {
      const tint = GROUP_TINTS[gi % GROUP_TINTS.length];
      groupIndex[g.id] = {label: g.label, tint};
      g.genres.forEach((x) => {
        genreIndex[x.id] = {
          label: x.label,
          group: g.id,
          groupLabel: g.label,
          tint,
          description: x.description
        };
      });
    });
  } catch (e) {
    genreIndex = null;
    groupIndex = null;
    return {};
  }
  return genreIndex;
}

function tintForGroup(group) {
  if (groupIndex && groupIndex[group]) return groupIndex[group].tint;
  if (!group) return 'tint-0';
  const k = KNOWN_GROUP_ORDER.findIndex(
      (p) => String(group).toLowerCase().includes(p));
  if (k >= 0)
    return GROUP_TINTS[Math.min(
        GROUP_TINTS.length - 1, Math.floor(k * 6 / KNOWN_GROUP_ORDER.length))];
  let hsh = 0;
  for (const c of String(group)) hsh = (hsh * 31 + c.charCodeAt(0)) >>> 0;
  return GROUP_TINTS[hsh % GROUP_TINTS.length];
}

export function genreLabel(id) {
  return (genreIndex && genreIndex[id] && genreIndex[id].label) ||
      String(id || '').replace(/_/g, ' ');
}
export function groupLabel(id) {
  return (groupIndex && groupIndex[id] && groupIndex[id].label) ||
      String(id || '').replace(/_/g, ' ');
}

export function genreChip(genre, group) {
  const g = genreIndex && genreIndex[genre];
  const tint = g ? g.tint : tintForGroup(group);
  return h(
      'span.chip.genre',
      {class: tint, title: g ? `${g.groupLabel} · ${g.label}` : genre},
      genreLabel(genre));
}

export function chip(text, cls = '') {
  return h('span.chip', {class: cls}, text);
}

const VERDICT = {
  good: {label: 'Good', icon: 'thumb_up', cls: 'v-good'},
  bad: {label: 'Bad', icon: 'thumb_down', cls: 'v-bad'},
  mixed: {label: 'Mixed', icon: 'thumbs_up_down', cls: 'v-mixed'},
};
export function verdictMark(v, {label = false} = {}) {
  if (!v || !VERDICT[v]) return null;
  const d = VERDICT[v];
  return h(
      'span.verdict', {class: d.cls, title: `Human verdict: ${d.label}`},
      icon(d.icon), label ? h('span', d.label) : null);
}

export function scoreClass(score) {
  if (score == null) return 's-none';
  if (score >= 8) return 's-good';
  if (score >= 5) return 's-mixed';
  return 's-bad';
}
export function scoreBadge(score, {title = 'Judge score'} = {}) {
  return h(
      'span.score', {class: scoreClass(score), title},
      score == null ? '–' : String(score));
}

const JUDGE_VERDICT = {
  pass: 'v-good',
  warn: 'v-mixed',
  fail: 'v-bad'
};
export function judgeVerdictChip(v) {
  if (!v) return null;
  return h('span.chip.jv', {class: JUDGE_VERDICT[v] || ''}, v);
}

const STATUS_CLS = {
  done: 's-done',
  running: 's-run',
  queued: 's-queued',
  generating: 's-run',
  rendering: 's-run',
  judging: 's-run',
  error: 's-err',
  failed: 's-err',
  cancelled: 's-muted',
  interrupted: 's-warn',
  open: 's-queued',
  addressed: 's-done',
  escalated: 's-warn',
  packaged: 's-muted',
};
export function statusChip(status) {
  return h(
      'span.status', {class: STATUS_CLS[status] || ''}, h('span.dot'),
      status || '—');
}

const FIX = {
  yes: ['Fixable', 'fx-yes'],
  no: ['Not fixable by constitution', 'fx-no'],
  unsure: ['Fixability unsure', 'fx-unsure']
};
export function fixableChip(f) {
  const d = FIX[f] || FIX.unsure;
  return h(
      'span.chip.fix', {class: d[1], title: d[0]},
      f === 'yes'    ? 'fixable' :
          f === 'no' ? 'unfixable' :
                       'unsure');
}

export function kindChip(kind) {
  return kind === 'do' ?
      h('span.chip.kind.k-do', icon('check'), 'Do') :
      h('span.chip.kind.k-dont', icon('block'), 'Don\u2019t');
}
