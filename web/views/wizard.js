// New project setup: name, scenario areas and an optional brief, then start a
// Teaching session (or open a blank Studio / import a .md constitution).
// The old 3-step Quick-start card-critique wizard was removed (2026-10-19);
// `/p/:pid/wizard` now redirects to the project's teaching sessions.
import {get, post} from '../components/api.js';
import {h, icon, mount, spinner} from '../components/dom.js';
import {navigate, store} from '../components/store.js';
import {toast} from '../components/ui.js';

export async function render(root, params) {
  if (params.pid) {
    navigate(`/p/${params.pid}/teach`, {replace: true});
    return;
  }
  let domains = [];
  let isStarting = false;
  let draftName = '';
  let draftBrief = '';
  let draftImportedMd = '';
  const selectedGroups = new Set(['assistant', 'care']);

  const pageEl = h('div.page.page-wizard#wizard-page');
  const headerEl = h('div.wiz-header#wizard-header');
  const bodyEl = h('div.wiz-body#wizard-body');
  pageEl.append(headerEl, bodyEl);
  root.appendChild(pageEl);

  mount(bodyEl, h('div.wiz-loading', spinner('Loading\u2026')));
  try {
    domains = (await get('/api/wizard/domains')).domains || [];
  } catch (e) {
    mount(
        bodyEl,
        h('div.empty', h('div.empty-title', 'Could not load setup'),
          h('p.empty-body', String(e.message || e)),
          h('a.btn.btn-primary', {href: '/'}, 'Back to projects')));
    return;
  }
  drawHeader();
  drawSetup();

  function drawHeader() {
    mount(
        headerEl,
        h('div.wiz-header-left',
          h('span.wiz-badge', icon('school'), 'New project'),
          h('span.wiz-proj-title', draftName.trim() || 'Teaching session')),
        h('div.wiz-header-right'));
  }

  function drawSetup() {
    const nameInput = h('input.input.wiz-input-lg#wiz-name', {
      type: 'text',
      value: draftName,
      placeholder: 'e.g. Assistant Daily Cards \u2014 Mobile',
      oninput: (e) => {
        draftName = e.target.value;
        drawHeader();
      },
    });
    const briefInput = h('textarea.input#wiz-brief', {
      rows: 3,
      value: draftBrief,
      placeholder:
          'Optional: describe your product surface, tone, or any instincts you already have (e.g. \u201cCalm consumer mobile cards; lead with the key answer, avoid walls of text\u201d).',
      oninput: (e) => {
        draftBrief = e.target.value;
      },
    });

    const domainGrid = h('div.wiz-domain-grid#wiz-domains');
    function renderDomainCards() {
      mount(domainGrid, domains.map((d) => {
        const on = selectedGroups.has(d.id);
        return h(
            'button.wiz-domain-card', {
              type: 'button',
              id: `wiz-domain-${d.id}`,
              class: on ? 'is-selected' : '',
              'aria-pressed': String(on),
              onclick: () => {
                if (selectedGroups.has(d.id)) {
                  if (selectedGroups.size > 1) selectedGroups.delete(d.id);
                } else {
                  selectedGroups.add(d.id);
                }
                renderDomainCards();
              },
            },
            h('div.wiz-domain-top',
              h('span.wiz-domain-icon', icon(d.icon || 'widgets')),
              h('span.wiz-domain-check',
                icon(on ? 'check_circle' : 'radio_button_unchecked'))),
            h('div.wiz-domain-label', d.label),
            h('div.wiz-domain-sub', d.subtitle || d.description || ''),
            h('div.wiz-domain-examples',
              Array.isArray(d.examples) ? d.examples.join(' \u00b7 ') :
                                          (d.examples || '')));
      }));
    }
    renderDomainCards();

    const fileInfo = h(
        'span.muted.small',
        draftImportedMd ? `Loaded ${draftImportedMd.split('\n').length} lines` :
                          'Already have a constitution .md?');
    const fileInput = h('input#wiz-import-md', {
      type: 'file',
      accept: '.md,.markdown,.txt,text/markdown,text/plain',
      onchange: async (e) => {
        const f = e.target.files && e.target.files[0];
        if (!f) {
          draftImportedMd = '';
          fileInfo.textContent = 'Already have a constitution .md?';
          return;
        }
        draftImportedMd = await f.text();
        fileInfo.textContent = `${f.name} \u00b7 ${
            draftImportedMd.split('\n')
                .length} lines \u2014 the session will teach on top of it`;
        if (!draftName.trim()) {
          draftName = f.name.replace(/\.(md|markdown|txt)$/i, '')
                          .replace(/[-_]+/g, ' ');
          nameInput.value = draftName;
          drawHeader();
        }
      },
    });

    mount(
        bodyEl,
        h('div.wiz-step1-wrap',
          h('div.wiz-hero', h('h1', 'Teach by example, not from a blank page'),
            h('p.lede',
              'Tell us roughly what you\u2019re building. We\u2019ll render 6 cards from real data; point at what works and what doesn\u2019t \u2014 by voice or typing \u2014 and the rules you confirm become your Design Constitution, tracked over time.')),
          h('div.wiz-card.wiz-setup-card',
            h('div.wiz-field',
              h('label.wiz-label',
                {for: 'wiz-name'
                },
                '1. Project name'),
              nameInput),
            h('div.wiz-field',
              h('div.wiz-label-row',
                h('label.wiz-label', '2. Pick 1\u20133 scenario areas'),
                h('span.muted.small',
                  'We\u2019ll pick 6 diverse scenarios across your selection; add more any time')),
              domainGrid),
            h('div.wiz-field',
              h('label.wiz-label',
                {for: 'wiz-brief'
                },
                '3. Product context or initial instincts ',
                h('span.muted.normal', '(optional)')),
              briefInput),
            h('div.wiz-setup-actions',
              h('button.btn.btn-primary.btn-lg#wizard-start-btn', {
                type: 'button',
                disabled: isStarting,
                onclick: () => startTeachingSession(),
              },
                isStarting ? spinner('Starting teaching session\u2026') :
                             [
                               icon('school'), 'Start teaching session',
                               icon('arrow_forward')
                             ]),
              h('div.wiz-setup-secondary',
                h('label.file-pick.btn-sm', icon('upload_file'),
                  h('span', 'Import .md'), fileInput),
                fileInfo)),
             // Starting without a teaching session is an expert path.
             h('details.adv-create.wiz-advanced#wizard-advanced',
               h('summary.btn.btn-ghost.btn-sm', 'Advanced'),
               h('div.adv-create-menu',
                 h('button.btn.btn-ghost.btn-sm#wizard-skip-btn',
                   {type: 'button', onclick: () => skipToStudio()},
                   'Skip to a blank studio', icon('arrow_forward')),
                 h('span.muted.small',
                   'Creates the project without a teaching session.'))))));
  }

  function defaultName() {
    const picked =
        domains.filter((d) => selectedGroups.has(d.id)).map((d) => d.label);
    if (!picked.length) return 'Mobile Card Constitution';
    if (picked.length === 1) return `${picked[0]} Cards`;
    return `${picked[0]} & ${picked[1]}`;
  }

  async function startTeachingSession() {
    if (isStarting) return;
    isStarting = true;
    drawSetup();
    try {
      const desc = draftBrief.trim();
      const created = await post('/api/projects', {
        name: draftName.trim() || defaultName(),
        description: desc,
        constitution_md: draftImportedMd || '',
      });
      const pid = created.project.id;
      store.project = created.project;
      const st = await post(`/api/projects/${pid}/teach`, {
        name: 'Session 1',
        brief: desc,
        groups: Array.from(selectedGroups),
        count: 6,
      });
      navigate(`/p/${pid}/teach/${st.session.id}?mode=immersive`);
    } catch (e) {
      toast(e.message || 'Could not start the teaching session', 'bad');
      isStarting = false;
      drawSetup();
    }
  }

  async function skipToStudio() {
    const created = await post('/api/projects', {
      name: draftName.trim() || 'Untitled project',
      description: draftBrief.trim(),
      constitution_md: draftImportedMd || '',
    });
    store.project = created.project;
    navigate(`/p/${created.project.id}/constitution`);
  }
}
