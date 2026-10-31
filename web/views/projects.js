// Projects grid.
import {get, patch, post} from '../components/api.js';
import {designSourceChips} from '../components/component_badges.js';
import {emptyState, h, icon, mount, relTime, spinner} from '../components/dom.js';
import {navigate, store} from '../components/store.js';
import {confirmDialog, modal, toast} from '../components/ui.js';

export async function render(root) {
  let showArchived = false;
  let showAll = false;
  const grid = h('div.project-grid#project-grid');
  const archivedToggle =
      h('label.check', h('input#show-archived', {
          type: 'checkbox',
          onchange: (e) => {
            showArchived = e.target.checked;
            load();
          }
        }),
        'Show archived');
  const allToggle = store.user.is_admin ?
      h('label.check', h('input#show-all-projects', {
          type: 'checkbox',
          onchange: (e) => {
            showAll = e.target.checked;
            load();
          }
        }),
        'Everyone\u2019s projects') :
      null;

  root.appendChild(h(
      'div.page.page-projects',
      h('div.page-head',
        h('div', h('h1', 'Projects'),
          h('p.lede',
            'Each project holds one design constitution, its revision history, runs, and the Dos & Don\u2019ts you collect along the way.')),
        h('div.page-actions', allToggle, archivedToggle,
          h('button.btn.btn-ghost#new-blank-project',
            {type: 'button', onclick: () => newProjectDialog()},
            'Blank project'),
          h('button.btn.btn-primary#new-project',
            {type: 'button', onclick: () => navigate('/wizard')},
            icon('school'), 'New project'))),
      grid));

  async function load() {
    mount(grid, spinner('Loading projects\u2026'));
    const {projects} = await get(`/api/projects${showAll ? '?all=1' : ''}`);
    store.projects = projects;
    const list = projects.filter((p) => showArchived || !p.archived);
    if (!list.length) {
      mount(
          grid,
          emptyState(
              'No projects yet',
              'Start with a teaching session: look at cards rendered from real data, point out what works and what doesn\u2019t, and the rules you confirm become your Design Constitution.',
              h('button.btn.btn-primary#new-project-empty',
                {type: 'button', onclick: () => navigate('/wizard')},
                icon('school'), 'New project'),
              h('button.btn.btn-ghost#new-blank-project-empty',
                {type: 'button', onclick: () => newProjectDialog()},
                icon('add'), 'Blank project')));
      grid.classList.add('is-empty');
      return;
    }
    grid.classList.remove('is-empty');
    mount(grid, list.map(card));
  }

  function card(p) {
    const s = p.stats || {};
    const rev = p.current_revision ? p.current_revision.number : 0;
    const showTeachLink = rev === 0 && (s.runs ?? 0) === 0;
    return h(
        'article.project-card',
        {class: p.archived ? 'archived' : '', dataset: {projectId: p.id}},
        h('a.project-card-link',
          {href: `/p/${p.id}/constitution`, 'aria-label': `Open ${p.name}`}),
        h('div.project-card-top', h('h2.project-name', p.name),
          h('div.project-menu',
            h('button.icon-btn.project-archive', {
              type: 'button',
              title: p.archived ? 'Unarchive' : 'Archive',
              'aria-label': p.archived ? 'Unarchive project' :
                                         'Archive project',
              onclick: async (e) => {
                e.preventDefault();
                e.stopPropagation();
                if (!p.archived &&
                    !(await confirmDialog(
                        'Archive project?',
                        `\u201c${
                            p.name}\u201d will be hidden from your list. You can unarchive it later.`,
                        {confirmLabel: 'Archive'})))
                  return;
                await patch(`/api/projects/${p.id}`, {archived: !p.archived});
                toast(
                    p.archived ? 'Project restored' : 'Project archived',
                    'good');
                load();
              }
            },
              icon(p.archived ? 'unarchive' : 'archive')))),
        p.description ? h('p.project-desc', p.description) :
                        h('p.project-desc.muted', 'No description'),
        h('dl.project-stats', h('div', h('dt', 'Revision'), h('dd', `${rev}`)),
          h('div', h('dt', 'Runs'), h('dd', `${s.runs ?? 0}`)),
          h('div', h('dt', 'Dos'), h('dd.c-good', `${s.dos ?? 0}`)),
          h('div', h('dt', 'Don\u2019ts'), h('dd.c-bad', `${s.donts ?? 0}`))),
        designSourceChips(p.design_sources),
        h('div.project-foot',
          h('span.muted',
            s.last_run_at ? `Last run ${relTime(s.last_run_at)}` :
                            'No runs yet'),
          showTeachLink ? h('a.chip.chip-accent.project-wizard-link', {
            href: `/p/${p.id}/teach`,
            onclick: (e) => e.stopPropagation(),
          },
                            icon('school'), 'Teach by example') :
                          null,
          store.user.ldap !== p.owner_ldap ? h('span.chip', p.owner_ldap) :
                                             null,
          p.archived ? h('span.chip', 'archived') : null));
  }

  function newProjectDialog() {
    const name = h(
        'input.input#np-name',
        {placeholder: 'e.g. Assistant answers \u2014 phone', autofocus: true});
    const desc = h('textarea.input#np-description', {
      rows: 3,
      placeholder: 'What surface or product is this constitution for?'
    });
    let importedMd = '';
    const fileInfo = h('span.muted', 'Optional');
    const file = h('input#np-import', {
      type: 'file',
      accept: '.md,.markdown,.txt,text/markdown,text/plain',
      onchange: async (e) => {
        const f = e.target.files[0];
        if (!f) {
          importedMd = '';
          fileInfo.textContent = 'Optional';
          return;
        }
        importedMd = await f.text();
        fileInfo.textContent =
            `${f.name} \u00b7 ${importedMd.split('\n').length} lines`;
        if (!name.value.trim())
          name.value = f.name.replace(/\.(md|markdown|txt)$/i, '')
                           .replace(/[-_]+/g, ' ');
      }
    });
    modal({
      title: 'New project',
      id: 'new-project-dialog',
      body: [
        h('label.field', h('span.field-label', 'Name'), name),
        h('label.field', h('span.field-label', 'Description'), desc),
        h('div.field', h('span.field-label', 'Import an existing constitution'),
          h('label.file-pick', icon('upload_file'),
            h('span', 'Choose .md file'), file),
          fileInfo,
          h('p.help',
            'Leave empty to start from a blank constitution with suggestions.')),
      ],
      actions: [
        {label: 'Cancel'},
        {
          label: 'Create project',
          kind: 'primary',
          id: 'np-create',
          onClick: async () => {
            if (!name.value.trim()) {
              toast('Give the project a name.', 'warn');
              name.focus();
              return false;
            }
            const body = {
              name: name.value.trim(),
              description: desc.value.trim()
            };
            if (importedMd) body.constitution_md = importedMd;
            const {project} = await post('/api/projects', body);
            store.project = project;
            navigate(`/p/${project.id}/constitution`);
          }
        },
      ],
    });
  }

  await load();
}
