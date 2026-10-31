// Shared blocks for design-reference material (Figma / Stitch facts, Stitch
// design system, legacy analysis), used by the Imports pages. The references
// list moved to Imports (/p/:pid/references redirects to /p/:pid/imports) and
// the import drawer lives in import_drawer.js; its exports are re-exported
// here for older callers.
import {h, icon} from '../components/dom.js';
import {renderMarkdown} from '../components/markdown.js';

export {downscale, figmaTokenWidget, openImportDrawer, openImportDrawer as openReferenceImport, stitchKeyWidget} from './import_drawer.js';

// Stitch enums (ROUND_EIGHT, FONT_INTER, DARK, MOBILE…) → "eight", "inter"…
const pretty = (v) =>
    String(v || '')
        .replace(/^(ROUND|FONT|COLOR_VARIANT|DEVICE_TYPE)_/, '')
        .replace(/_/g, ' ')
        .toLowerCase();

// ------------------------------------------------------------ shared blocks

/**
 * @param {*} f
 * @return {*}
 */
export function factsSummary(f) {
  const frames = f.frames || [];
  const colors = (f.colors || []).slice(0, 12);
  const radii = (f.radii || []).slice(0, 8);
  const styles = (f.text_styles || []).slice(0, 8);
  const comps = (f.components || []).slice(0, 10);
  const outline = (f.html_outline || []).filter((o) => o && o.outline);
  return h(
      'details.ref-facts', {open: true},
      h('summary', icon('straighten'),
        f.source === 'stitch' ? 'Facts from Stitch' : 'Facts from Figma',
        h('span.muted.small',
          ` ${frames.length} frame${frames.length === 1 ? '' : 's'}${
              f.file_name ? ` \u00b7 ${f.file_name}` : ''}`)),
      h('div.facts-grid',
        colors.length ?
            h('div.facts-cell', h('div.label', 'Top colors'),
              h('div.swatches',
                colors.map(
                    (c) => h(
                        'span.swatch', {
                          title: `${c.hex} \u00b7 ${c.count}\u00d7${
                              (c.roles || []).length ?
                                  ` \u00b7 ${c.roles.join(', ')}` :
                                  ''}`
                        },
                        h('span.swatch-dot', {style: {backgroundColor: c.hex}}),
                        h('code', c.hex))))) :
            null,
        radii.length ?
            h('div.facts-cell', h('div.label', 'Corner radii'),
              h('div.chip-row',
                radii.map(
                    (r) =>
                        h('span.chip.chip-quiet',
                          {title: (r.on || []).join(', ')},
                          `${r.px >= 999 ? 'full' : `${r.px}px`} \u00d7${
                              r.count}`)))) :
            null,
        styles.length ?
            h('div.facts-cell.facts-wide', h('div.label', 'Text styles'),
              h('table.facts-table',
                h('tbody',
                  styles.map(
                      (s) =>
                          h('tr', h('td', s.name || '\u2014'),
                            h('td.muted', s.family || ''),
                            h('td', `${s.size || ''}${s.size ? 'px' : ''}`),
                            h('td.muted', s.weight ? String(s.weight) : ''),
                            h('td.muted',
                              s.count ? `\u00d7${s.count}` : '')))))) :
            null,
        f.spacing &&
                ((f.spacing.item_spacing || []).length ||
                 (f.spacing.padding || []).length) ?
            h('div.facts-cell', h('div.label', 'Spacing'),
              h('p.small',
                (f.spacing.item_spacing || []).length ?
                    `gaps ${f.spacing.item_spacing.join(', ')}px` :
                    '',
                (f.spacing.padding || []).length ?
                    ` \u00b7 padding ${f.spacing.padding.join(', ')}px` :
                    '',
                f.spacing.grid_hint ? ` \u00b7 ${f.spacing.grid_hint}` : '')) :
            null,
        comps.length ? h('div.facts-cell', h('div.label', 'Components'),
                         h('div.chip-row',
                           comps.map(
                               (c) =>
                                   h('span.chip.chip-quiet',
                                     `${c.name} \u00d7${c.instances}`)))) :
                       null,
        frames.length ?
            h('div.facts-cell', h('div.label', 'Frames'),
              h('ul.plain.small',
                frames.slice(0, 8).map(
                    (fr) =>
                        h('li', fr.name,
                          h('span.muted',
                            ` ${fr.width}\u00d7${fr.height} \u00b7 ${
                                    [pretty(fr.device_type), fr.viewport_hint]
                                        .filter(Boolean)
                                        .join(' \u00b7 ')}`))))) :
            null),
      outline.length ?
          h('details.ref-outline',
            h('summary', icon('code'), 'HTML structure',
              h('span.muted.small',
                ` ${outline.length} screen${outline.length === 1 ? '' : 's'}`)),
            outline.map(
                (o) =>
                    h('div.ref-outline-item',
                      h('div.ref-outline-frame', o.frame || 'Screen'),
                      h('pre.ref-outline-pre', String(o.outline))))) :
          null);
}

/** Stitch design system (theme) panel. */
/**
 * @param {*} ds
 * @return {*}
 */
export function designSystemPanel(ds) {
  const COLORS = [
    ['seed_color', 'Seed'], ['primary', 'Primary'], ['secondary', 'Secondary'],
    ['tertiary', 'Tertiary'], ['neutral', 'Neutral']
  ];
  const colors = COLORS.filter(([k]) => ds[k]);
  const fonts = ds.fonts || {};
  const fontRows = [
    ['headline', 'Headline'], ['body', 'Body'], ['label', 'Label']
  ].filter(([k]) => fonts[k]);
  const extra = (label, v) => {
    if (v == null || v === '' ||
        (typeof v === 'object' && !Object.keys(v).length))
      return null;
    const text = typeof v === 'object' ?
        Object.entries(v)
            .map(
                ([k, x]) =>
                    `${k}: ${typeof x === 'object' ? JSON.stringify(x) : x}`)
            .join(' \u00b7 ') :
        String(v);
    return h(
        'div.facts-cell', h('div.label', label),
        h('p.small.ds-extra', {title: text},
          text.length > 280 ? `${text.slice(0, 280)}\u2026` : text));
  };
  const md = ds.design_md ? String(ds.design_md) : '';
  return h(
      'details.ref-facts.ref-ds', {open: true},
      h('summary', icon('palette'), 'Design system',
        h('span.muted.small',
          ` ${
                  [ds.display_name,
                   ds.color_mode ? `${pretty(ds.color_mode)} mode` : '']
                      .filter(Boolean)
                      .join(' \u00b7 ')}`)),
      h('div.facts-grid',
        colors.length ?
            h('div.facts-cell.facts-wide', h('div.label', 'Colors'),
              h('div.ds-colors',
                colors.map(
                    ([k, label]) =>
                        h('div.ds-color', {title: `${label} ${ds[k]}`},
                          h('span.ds-color-chip',
                            {style: {backgroundColor: ds[k]}}),
                          h('div.ds-color-text', h('div.small', label),
                            h('code', ds[k])))))) :
            null,
        fontRows.length ? h('div.facts-cell', h('div.label', 'Fonts'),
                            h('table.facts-table',
                              h('tbody',
                                fontRows.map(
                                    ([k, label]) => h(
                                        'tr', h('td.muted', label),
                                        h('td', String(fonts[k]))))))) :
                          null,
        ds.roundness || ds.roundness_px != null || ds.color_variant ||
                ds.color_mode ?
            h('div.facts-cell', h('div.label', 'Shape & color'),
              h('div.chip-row',
                ds.roundness || ds.roundness_px != null ?
                    h('span.chip.chip-quiet', {title: ds.roundness || ''},
                      `roundness ${ds.roundness ? pretty(ds.roundness) : ''}${
                          ds.roundness_px != null ?
                              `${ds.roundness ? ' \u00b7 ' : ''}${
                                  ds.roundness_px >= 999 ?
                                      'full' :
                                      `${ds.roundness_px}px`}` :
                              ''}`) :
                    null,
                ds.color_variant ?
                    h('span.chip.chip-quiet', {title: ds.color_variant},
                      `variant ${pretty(ds.color_variant)}`) :
                    null,
                ds.color_mode ?
                    h('span.chip.chip-quiet', `${pretty(ds.color_mode)} mode`) :
                    null)) :
            null,
        extra('Typography', ds.typography), extra('Spacing', ds.spacing)),
      md ? h('details.ref-outline.ref-ds-md',
             h('summary', icon('description'), 'DESIGN.md',
               h('span.muted.small', ` ${md.split('\n').length} lines`)),
             h('div.ref-ds-md-body', renderMarkdown(md, {className: 'md'}))) :
           null);
}

/**
 * @param {*} a
 * @return {*}
 */
export function analysisBlock(a) {
  const obs = a.observations || [];
  const fb = a.formatting_feedback || [];
  return h(
      'div.ref-analysis', a.summary ? h('p.ref-summary', a.summary) : null,
      obs.length ?
          h('div.ref-obs', h('div.label', 'Observations'),
            h('ul.obs-list',
              obs.map(
                  (o) => h(
                      'li', o.topic ? h('span.chip.chip-quiet', o.topic) : null,
                      h('span', ` ${o.text}`),
                      (o.evidence || []).length ?
                          h('div.muted.small',
                            `Evidence: ${o.evidence.join(' \u00b7 ')}`) :
                          null)))) :
          null,
      fb.length ?
          h('div.ref-feedback',
            h('div.label', icon('checklist'), 'How to prepare files better'),
            h('ul.checklist',
              fb.map(
                  (x) =>
                      h('li', {class: `sev-${x.severity || 'low'}`},
                        h('span.check-box', icon('check_box_outline_blank')),
                        h('div',
                          h('div.check-issue', x.issue,
                            h('span.sev', x.severity || 'low')),
                          x.fix ? h('div.check-fix', x.fix) : null))))) :
          null);
}
