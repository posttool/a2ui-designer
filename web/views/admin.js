// Admin: overview, users, projects with downloads, inbox, bundles.
import {get, post} from '../components/api.js';
import {chip, fixableChip, statusChip} from '../components/badges.js';
import {download, emptyState, fmtBytes, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {renderMarkdown} from '../components/markdown.js';
import {drawer, segmented, toast} from '../components/ui.js';

export async function render(root, params, ctx) {
  let tab = ctx.url.searchParams.get('tab') || 'overview';
  const body = h('div.admin-body#admin-body');
  const tabs = segmented(
      [
        {value: 'overview', label: 'Overview', id: 'admin-tab-overview'},
        {value: 'inbox', label: 'Inbox', id: 'admin-tab-inbox'},
        {value: 'bundles', label: 'Bundles', id: 'admin-tab-bundles'},
      ],
      tab, (v) => {
        tab = v;
        history.replaceState(null, '', `/admin?tab=${v}`);
        draw();
      });

  root.appendChild(h(
      'div.page.page-admin',
      h('div.page-head',
        h('div', h('h1', 'Admin'),
          h('p.lede',
            'Everyone\u2019s constitutions and the Don\u2019ts a constitution can\u2019t fix \u2014 packaged for model and benchmark designers.')),
        h('div.page-actions', tabs)),
      body));

  async function draw() {
    mount(body, spinner('Loading\u2026'));
    if (tab === 'overview')
      await overview();
    else if (tab === 'inbox')
      await inbox();
    else
      await bundles();
  }

  async function overview() {
    const data = await get('/api/admin/overview');
    const t = data.totals || {};
    const tot = (k, l) =>
        h('div.total', h('span.total-num', `${t[k] ?? 0}`),
          h('span.total-label', l));
    mount(
        body,
        h('div.totals#admin-totals', tot('users', 'users'),
          tot('projects', 'projects'), tot('revisions', 'revisions'),
          tot('runs', 'runs'), tot('outputs', 'outputs'),
          tot('samples', 'samples'), tot('dos', 'dos'),
          tot('donts', 'don\u2019ts'), tot('escalated', 'escalated')),
        h('section.admin-section', h('h2', 'Projects'),
          h('div.table-wrap',
            h('table.table#admin-projects',
              h('thead',
                h('tr', h('th', 'Project'), h('th', 'Owner'),
                  h('th.num', 'Revs'), h('th.num', 'Runs'), h('th.num', 'Dos'),
                  h('th.num', 'Don\u2019ts'), h('th', 'Last activity'),
                  h('th', 'Download'))),
              h('tbody', (data.projects || []).map((p) => {
                const s = p.stats || {};
                return h(
                    'tr', {dataset: {projectId: p.id}},
                    h('td', h('a', {href: `/p/${p.id}/constitution`}, p.name),
                      p.archived ? chip('archived') : null,
                      h('div.cell-sub', p.description || '')),
                    h('td', p.owner_ldap),
                    h('td.num',
                      `${
                          s.revisions ??
                          (p.current_revision ? p.current_revision.number + 1 :
                                                0)}`),
                    h('td.num', `${s.runs ?? 0}`),
                    h('td.num.c-good', `${s.dos ?? 0}`),
                    h('td.num.c-bad', `${s.donts ?? 0}`),
                    h('td.muted', relTime(p.updated_at || s.last_run_at)),
                    h('td.cell-dl',
                      h('a.dl', {
                        href: `/api/export/projects/${p.id}/constitution.md`,
                        download: '',
                        title: 'Constitution (markdown)'
                      },
                        icon('description'), '.md'),
                      h('a.dl', {
                        href: `/api/export/projects/${p.id}/history.json`,
                        download: '',
                        title: 'Revision history'
                      },
                        icon('history'), 'history'),
                      h('a.dl', {
                        href: `/api/export/projects/${p.id}/guidelines.zip`,
                        download: '',
                        title: 'Dos & Don\u2019ts'
                      },
                        icon('folder_zip'), 'zip')));
              }))))),
        h('section.admin-section', h('h2', 'Users'),
          h('div.table-wrap',
            h('table.table#admin-users',
              h('thead',
                h('tr', h('th', 'User'), h('th', 'Role'),
                  h('th.num', 'Projects'), h('th', 'Last seen'))),
              h('tbody',
                (data.users || [])
                    .map(
                        (u) =>
                            h('tr',
                              h('td', h('strong', u.display_name || u.ldap),
                                h('div.cell-sub', u.ldap)),
                              h('td',
                                u.is_admin ? chip('admin', 'c-current') :
                                             h('span.muted', 'designer')),
                              h('td.num',
                                `${u.projects ?? u.project_count ?? '\u2014'}`),
                              h('td.muted',
                                relTime(
                                    u.last_seen_at || u.last_login_at ||
                                    u.created_at)))))))));
  }

  async function inbox() {
    const {guidelines} = await get('/api/admin/inbox');
    const picked = new Set();
    const actionBar = h('div.selbar#inbox-actions');
    const drawBar = () => {
      mount(
          actionBar, h('span', h('strong', `${picked.size}`), ' selected'),
          h('button.btn.btn-sm.btn-primary#package-benchmark', {
            type: 'button',
            disabled: !picked.size,
            onclick: (e) => pack('benchmark', e.currentTarget)
          },
            icon('inventory_2'), 'Package benchmark bundle'),
          h('button.btn.btn-sm.btn-ghost#package-guidelines', {
            type: 'button',
            disabled: !picked.size,
            onclick: (e) => pack('guidelines', e.currentTarget)
          },
            icon('folder_zip'), 'Package guidelines bundle'));
    };
    async function pack(kind, btn) {
      btn.disabled = true;
      try {
        const {bundle} = await post(
            '/api/admin/bundles', {kind, guideline_ids: [...picked]});
        toast(`Bundle ready (${fmtBytes(bundle.size_bytes)})`, 'good');
        if (bundle.download_url) download(bundle.download_url);
        tab = 'bundles';
        tabs.set('bundles');
        history.replaceState(null, '', '/admin?tab=bundles');
        draw();
      } finally {
        btn.disabled = false;
      }
    }
    if (!(guidelines || []).length) {
      mount(
          body,
          emptyState(
              'Inbox is empty',
              'Escalated Don\u2019ts and Don\u2019ts marked \u201cnot fixable by the constitution\u201d show up here.'));
      return;
    }
    drawBar();
    mount(
        body,
        h('p.help',
          'Don\u2019ts designers escalated or marked as not fixable by a constitution. Select some to package for model and benchmark work.'),
        actionBar, h('div.inbox-list#admin-inbox', guidelines.map((g) => {
          const shot = g.screenshots && g.screenshots[0];
          const cb = h('input', {
            type: 'checkbox',
            id: `inbox-pick-${g.id}`,
            'aria-label': `Select ${g.title}`,
            onchange: (e) => {
              e.target.checked ? picked.add(g.id) : picked.delete(g.id);
              e.target.closest('.inbox-item')
                  .classList.toggle('sel', e.target.checked);
              drawBar();
            }
          });
          return h(
              'article.inbox-item', {dataset: {guidelineId: g.id}},
              h('label.inbox-check', cb),
              h('div.inbox-shot',
                shot ? h('img', {src: shot.url, alt: '', loading: 'lazy'}) :
                       h('div.tile-placeholder', icon('image_not_supported'))),
              h('div.inbox-main',
                h('button.inbox-title',
                  {type: 'button', onclick: () => openGuideline(g)}, g.title),
                h('div.chip-row', chip(g.project_name || g.project_id),
                  h('span.muted.small', g.owner_ldap), fixableChip(g.fixable),
                  statusChip(g.status),
                  (g.tags || []).slice(0, 3).map((t) => chip(t, 'chip-quiet'))),
                g.escalation_note ?
                    h('p.inbox-note', icon('flag'), g.escalation_note) :
                    null,
                h('p.muted.small', relTime(g.updated_at || g.created_at))));
        })));
  }

  function openGuideline(g) {
    drawer({
      title: g.title,
      wide: true,
      body: h(
          'div.gl-detail',
          h('div.chip-row', chip(g.project_name || ''), fixableChip(g.fixable),
            statusChip(g.status)),
          h('div.gl-shots',
            (g.screenshots || []).map((s) => h('figure.gl-shot-fig', h('img', {
                                                 src: s.url,
                                                 alt: ''
                                               })))),
          renderMarkdown(g.body_md || '', {className: 'md md-doc'}),
          g.escalation_note ?
              h('div.callout.callout-warn', icon('flag'),
                h('div', h('strong', 'Note: '), g.escalation_note)) :
              null)
    });
  }

  async function bundles() {
    const res = await get('/api/admin/bundles');
    const list = res.bundles || [];
    if (!list.length) {
      mount(
          body,
          emptyState(
              'No bundles yet',
              'Package Don\u2019ts from the Inbox into a benchmark or guidelines bundle.'));
      return;
    }
    mount(
        body,
        h('div.table-wrap',
          h('table.table#admin-bundles',
            h('thead',
              h('tr', h('th', 'Kind'), h('th', 'Contents'), h('th', 'Created'),
                h('th', 'By'), h('th.num', 'Size'), h('th', ''))),
            h(
                'tbody',
                list
                    .map(
                        (b) =>
                            h('tr',
                              h('td',
                                chip(
                                    b.kind,
                                    b.kind === 'benchmark' ? 'c-current' : '')),
                              h('td.muted',
                                Object.entries(b.counts || {})
                                        .map(([k, v]) => `${v} ${k}`)
                                        .join(' \u00b7 ') ||
                                    '\u2014'),
                              h('td.muted', relTime(b.created_at)),
                              h('td', b.created_by || ''),
                              h('td.num', fmtBytes(b.size_bytes)),
                              h('td',
                                h(
                                    'a.btn.btn-sm.btn-ghost', {
                                      href: b.download_url ||
                                          `/api/admin/bundles/${b.id}.zip`,
                                      download: ''
                                    },
                                    icon('download'), 'Download'))))))));
  }

  await draw();
}
