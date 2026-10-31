// Dos & Don'ts gallery + detail drawer.
import {del, get, patch, post, qs} from '../components/api.js';
import {chip, fixableChip, kindChip, statusChip} from '../components/badges.js';
import {sendGuidelineToChat} from '../components/chat.js';
import {download, emptyState, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {renderMarkdown} from '../components/markdown.js';
import {navigate, on, store} from '../components/store.js';
import {confirmDialog, drawer, modal, promptDialog, segmented, toast} from '../components/ui.js';

import {RENDERER_META} from './run_detail.js';

export async function render(root, params, ctx) {
  const pid = params.pid;
  const sp = ctx.url.searchParams;
  const f = {
    kind: sp.get('kind') || '',
    status: sp.get('status') || '',
    fixable: sp.get('fixable') || '',
    tag: sp.get('tag') || ''
  };
  let items = [];
  let alive = true;
  const disposers = [];

  const galleryEl = h('div.gl-gallery#guidelines-gallery');
  const tagSel = h('select.input.input-sm#gl-filter-tag', {
    onchange: (e) => {
      f.tag = e.target.value;
      draw();
    }
  });
  const kindSeg = segmented(
      [
        {value: '', label: 'All', id: 'gl-tab-all'},
        {value: 'do', label: 'Dos', id: 'gl-tab-do'},
        {value: 'dont', label: 'Don\u2019ts', id: 'gl-tab-dont'},
      ],
      f.kind, (v) => {
        f.kind = v;
        draw();
      });

  root.appendChild(h(
      'div.page.page-guidelines',
      h('div.page-head',
        h('div', h('h1', 'Dos & Don\u2019ts'),
          h('p.lede',
            'Examples worth keeping \u2014 each with screenshots and a short write-up. Don\u2019ts the constitution can\u2019t fix go to admins for model and benchmark work.')),
        h('div.page-actions',
          h('button.btn.btn-ghost#export-guidelines', {
            type: 'button',
            onclick: () => download(`/api/export/projects/${
                pid}/guidelines.zip${qs({kind: f.kind, status: f.status})}`)
          },
            icon('download'), 'Export ZIP'))),
      h('div.filterbar', kindSeg,
        h('select.input.input-sm#gl-filter-status', {
          onchange: (e) => {
            f.status = e.target.value;
            load();
          }
        },
          [
            ['', 'Any status'], ['open', 'Open'], ['addressed', 'Addressed'],
            ['escalated', 'Escalated'], ['packaged', 'Packaged']
          ].map(([v,
                  l]) => h('option', {value: v, selected: f.status === v}, l))),
        h('select.input.input-sm#gl-filter-fixable', {
          onchange: (e) => {
            f.fixable = e.target.value;
            draw();
          }
        },
          [
            ['', 'Any fixability'], ['yes', 'Fixable'], ['no', 'Not fixable'],
            ['unsure', 'Unsure']
          ]
              .map(
                  ([v, l]) =>
                      h('option', {value: v, selected: f.fixable === v}, l))),
        tagSel),
      galleryEl));

  async function load() {
    const res =
        await get(`/api/projects/${pid}/guidelines${qs({status: f.status})}`);
    if (!alive) return;
    items = res.guidelines || [];
    const tags = [...new Set(items.flatMap((g) => g.tags || []))].sort();
    mount(
        tagSel, h('option', {value: ''}, 'Any tag'),
        tags.map((t) => h('option', {value: t, selected: f.tag === t}, t)));
    draw();
  }

  function filtered() {
    return items.filter(
        (g) => (!f.kind || g.kind === f.kind) &&
            (!f.fixable || g.fixable === f.fixable) &&
            (!f.tag || (g.tags || []).includes(f.tag)));
  }

  function draw() {
    const u = new URL(location.href);
    Object.entries(f).forEach(
        ([k, v]) => (v ? u.searchParams.set(k, v) : u.searchParams.delete(k)));
    history.replaceState(null, '', u.pathname + u.search);
    const list = filtered();
    if (!list.length) {
      mount(
          galleryEl,
          items.length ?
              emptyState('Nothing matches these filters') :
              emptyState(
                  'No Dos or Don\u2019ts yet',
                  'While reviewing outputs, press O to save a Do or D to save a Don\u2019t. The screenshot is attached automatically.',
                  h('a.btn.btn-primary', {href: `/p/${pid}/review`},
                    icon('rate_review'), 'Start reviewing')));
      return;
    }
    if (!f.kind) {
      const dos = list.filter((g) => g.kind === 'do');
      const donts = list.filter((g) => g.kind === 'dont');
      mount(
          galleryEl,
          h('div.gl-columns',
            h('section.gl-col',
              h('h2.gl-col-title.c-good', icon('check'), `Dos`,
                h('span.count', dos.length)),
              h('div.gl-grid', dos.map(card)),
              !dos.length ? h('p.muted.small', 'None yet.') : null),
            h('section.gl-col',
              h('h2.gl-col-title.c-bad', icon('block'), `Don\u2019ts`,
                h('span.count', donts.length)),
              h('div.gl-grid', donts.map(card)),
              !donts.length ? h('p.muted.small', 'None yet.') : null)));
    } else {
      mount(galleryEl, h('div.gl-grid.gl-grid-wide', list.map(card)));
    }
  }

  function card(g) {
    const shot = g.screenshots && g.screenshots[0];
    const ren = g.renderer || (shot && shot.renderer) || 'web';
    const renBadge = ren === 'all' ?
        'ALL 4' :
        ((RENDERER_META[ren] || {}).badge || ren.toUpperCase());
    return h(
        'button.gl-card', {
          type: 'button',
          dataset: {guidelineId: g.id},
          class: `k-${g.kind}`,
          onclick: () => openDetail(g.id)
        },
        h('div.gl-card-shot',
          shot ? h('img', {src: shot.url, alt: '', loading: 'lazy'}) :
                 h('div.tile-placeholder', icon('image_not_supported')),
          g.screenshots && g.screenshots.length > 1 ?
              h('span.gl-more', `+${g.screenshots.length - 1}`) :
              null),
        h('div.gl-card-body', h('div.gl-card-title', g.title),
          h('div.chip-row',
            h('span.renderer-badge.renderer-badge-xs',
              {dataset: {renderer: ren}}, renBadge),
            g.kind === 'dont' ? fixableChip(g.fixable) : null,
            g.status !== 'open' ? statusChip(g.status) : null,
            (g.tags || []).slice(0, 2).map((t) => chip(t, 'chip-quiet')))));
  }

  async function openDetail(id) {
    const d = drawer({
      title: 'Guideline',
      wide: true,
      id: 'guideline-drawer',
      body: spinner('Loading\u2026')
    });
    let g = (await get(`/api/guidelines/${id}`)).guideline;
    const refreshG = (ng) => {
      g = ng;
      const i = items.findIndex((x) => x.id === g.id);
      if (i >= 0) items[i] = g;
      draw();
      showView();
    };

    function showView() {
      d.setTitle(g.kind === 'do' ? 'Do' : 'Don\u2019t');
      const curRev = store.project && store.project.current_revision;
      mount(
          d.body,
          h('div.gl-detail',
            h('div.chip-row', kindChip(g.kind),
              h('span.renderer-badge',
                {dataset: {renderer: g.renderer || 'web'}},
                g.renderer === 'all' ?
                    'ALL 4 PLATFORMS' :
                    ((RENDERER_META[g.renderer || 'web'] || {}).badge ||
                     (g.renderer || 'web').toUpperCase())),
              g.kind === 'dont' ? fixableChip(g.fixable) : null,
              statusChip(g.status),
              (g.tags || []).map((t) => chip(t, 'chip-quiet'))),
            h('h2.gl-detail-title', g.title),
            h('div.gl-shots',
              (g.screenshots || [])
                  .map(
                      (s) =>
                          h('figure.gl-shot-fig',
                            h('a',
                              {href: s.url, target: '_blank', rel: 'noopener'},
                              h('img', {src: s.url, alt: ''})),
                            h('figcaption',
                              s.renderer ?
                                  h('span.renderer-badge.renderer-badge-xs',
                                    {dataset: {renderer: s.renderer}},
                                    (RENDERER_META[s.renderer] || {}).badge ||
                                        s.renderer) :
                                  null,
                              ' ',
                              s.output_id ? h('a', {
                                href: `/p/${pid}/review?o=${s.output_id}`,
                                onclick: (e) => {
                                  e.preventDefault();
                                  openOutput(s.output_id);
                                }
                              },
                                              'Source output') :
                                            null)))),
            g.body_md && g.body_md.trim() ?
                renderMarkdown(g.body_md, {className: 'md md-doc'}) :
                h('p.muted', 'No description.'),
            g.escalation_note ?
                h('div.callout.callout-warn', icon('flag'),
                  h('div', h('strong', 'Escalated: '), g.escalation_note)) :
                null,
            g.addressed_revision_id ?
                h('p.muted.small', 'Marked addressed in a later revision.') :
                null,
            h('p.muted.small',
              `${g.created_by || ''} \u00b7 ${relTime(g.created_at)}${
                  g.updated_at && g.updated_at !== g.created_at ?
                      ` \u00b7 edited ${relTime(g.updated_at)}` :
                      ''}`),
            h('div.drawer-actions.wrap',
              h('button.btn.btn-ghost#gl-edit',
                {type: 'button', onclick: () => showEdit()}, icon('edit'),
                'Edit'),
              h('button.btn.btn-ghost#gl-add-screenshot',
                {type: 'button', onclick: () => addScreenshot()},
                icon('add_photo_alternate'), 'Add screenshot'),
              h('button.btn.btn-ghost#gl-discuss', {
                type: 'button',
                onclick: () => {
                  sendGuidelineToChat(
                      g,
                      g.kind === 'dont' ?
                          'How should the constitution prevent this?' :
                          'Should the constitution encourage this pattern?');
                  d.close();
                }
              },
                icon('forum'), 'Discuss'),
              g.status !== 'addressed' && curRev ?
                  h('button.btn.btn-ghost#gl-mark-addressed', {
                    type: 'button',
                    onclick: async () => {
                      const {guideline} =
                          await patch(`/api/guidelines/${g.id}`, {
                            status: 'addressed',
                            addressed_revision_id: curRev.id
                          });
                      toast(`Marked addressed in rev ${curRev.number}`, 'good');
                      refreshG(guideline);
                    }
                  },
                    icon('task_alt'),
                    `Mark addressed in rev ${curRev.number}`) :
                  null,
              g.kind === 'dont' && g.status !== 'escalated' &&
                      g.status !== 'packaged' ?
                  h('button.btn.btn-ghost#gl-escalate', {
                    type: 'button',
                    onclick: async () => {
                      const note = await promptDialog('Escalate to admins', {
                        label: 'Note for model / benchmark designers',
                        multiline: true,
                        confirmLabel: 'Escalate',
                        placeholder:
                            'Why can\u2019t the constitution fix this? What would?',
                        help:
                            'Escalated Don\u2019ts appear in the admin inbox and can be packaged into benchmark bundles.'
                      });
                      if (note == null) return;
                      const {guideline} = await post(
                          `/api/guidelines/${g.id}/escalate`, {note});
                      toast('Escalated to admins', 'good');
                      refreshG(guideline);
                    }
                  },
                    icon('flag'), 'Escalate to admins') :
                  null,
              h('div.grow'),
              h('button.btn.btn-danger-quiet#gl-delete', {
                type: 'button',
                onclick: async () => {
                  if (!(await confirmDialog(
                          'Delete this guideline?', g.title,
                          {confirmLabel: 'Delete', danger: true})))
                    return;
                  await del(`/api/guidelines/${g.id}`);
                  items = items.filter((x) => x.id !== g.id);
                  d.close();
                  draw();
                }
              },
                icon('delete'), 'Delete'))));
    }

    function showEdit() {
      const title = h('input.input#gl-edit-title', {value: g.title});
      const body =
          h('textarea.input.code-input#gl-edit-body', {rows: 12},
            g.body_md || '');
      const tags =
          h('input.input#gl-edit-tags', {value: (g.tags || []).join(', ')});
      const fix = h('select.input#gl-edit-fixable', [
        'yes', 'no', 'unsure'
      ].map((v) => h('option', {value: v, selected: g.fixable === v}, v)));
      const status = h('select.input#gl-edit-status', [
        'open', 'addressed', 'escalated', 'packaged'
      ].map((v) => h('option', {value: v, selected: g.status === v}, v)));
      mount(
          d.body,
          h('div.gl-edit',
            h('label.field', h('span.field-label', 'Title'), title),
            h('label.field', h('span.field-label', 'Description (markdown)'),
              body),
            h('label.field', h('span.field-label', 'Tags'), tags),
            h('div.form-row',
              h('label.field', h('span.field-label', 'Fixable by constitution'),
                fix),
              h('label.field', h('span.field-label', 'Status'), status)),
            h('div.drawer-actions',
              h('button.btn.btn-primary#gl-save', {
                type: 'button',
                onclick: async () => {
                  const {guideline} = await patch(`/api/guidelines/${g.id}`, {
                    title: title.value.trim(),
                    body_md: body.value,
                    tags: tags.value.split(',')
                              .map((t) => t.trim())
                              .filter(Boolean),
                    fixable: fix.value,
                    status: status.value,
                  });
                  toast('Saved', 'good');
                  refreshG(guideline);
                }
              },
                'Save'),
              h('button.btn.btn-ghost',
                {type: 'button', onclick: () => showView()}, 'Cancel'))));
    }

    function addScreenshot() {
      const outInput =
          h('input.input#gl-shot-output', {placeholder: 'Output id'});
      let dataUrl = null;
      const previewEl = h('div.paste-preview');
      const file = h('input#gl-shot-file', {
        type: 'file',
        accept: 'image/*',
        onchange: (e) => readFile(e.target.files[0])
      });
      function readFile(fl) {
        if (!fl) return;
        const r = new FileReader();
        r.onload = () => {
          dataUrl = r.result;
          mount(previewEl, h('img', {src: dataUrl, alt: ''}));
        };
        r.readAsDataURL(fl);
      }
      const pasteZone =
          h('div.paste-zone#gl-shot-paste', {
            tabindex: 0,
            onpaste: (e) => {
              const it = [...(e.clipboardData || {}).items || []].find(
                  (x) => x.type.startsWith('image/'));
              if (it) {
                e.preventDefault();
                readFile(it.getAsFile());
              }
            }
          },
            icon('content_paste'),
            'Click here and paste an image (\u2318V), or choose a file');
      modal({
        title: 'Add screenshot',
        id: 'add-screenshot-dialog',
        body: [
          h('label.field', h('span.field-label', 'From an output'), outInput,
            h('p.help', 'Paste an output id (from Review URL ?o=\u2026).')),
          h('div.or', 'or'), pasteZone,
          h('label.file-pick', icon('upload'), h('span', 'Choose image'), file),
          previewEl
        ],
        actions: [
          {label: 'Cancel'}, {
            label: 'Add',
            kind: 'primary',
            id: 'gl-shot-submit',
            onClick: async () => {
              const body = outInput.value.trim() ?
                  {output_id: outInput.value.trim()} :
                  dataUrl ? {data_url: dataUrl} :
                            null;
              if (!body) {
                toast('Give an output id or an image.', 'warn');
                return false;
              }
              const {guideline} =
                  await post(`/api/guidelines/${g.id}/screenshots`, body);
              toast('Screenshot added', 'good');
              refreshG(guideline);
            }
          }
        ]
      });
    }

    async function openOutput(oid) {
      const res = await get(`/api/outputs/${oid}`).catch(() => null);
      if (!res) return;
      d.close();
      navigate(`/p/${pid}/review/${res.output.run_id}?o=${oid}`);
    }

    showView();
  }

  disposers.push(on('guidelines-changed', () => load()));
  mount(galleryEl, spinner('Loading\u2026'));
  await load();
  const openId = sp.get('open');
  if (openId) openDetail(openId);
  return () => {
    alive = false;
    disposers.forEach((x) => x());
  };
}
