// App shell: router (history API), top bar, project rail, chat panel.
import {get, post, setUnauthorizedHandler} from './components/api.js';
import {loadGenres} from './components/badges.js';
import {openBugReport} from './components/bug_report.js';
import {CHAT_VIEWS, ChatPanel} from './components/chat.js';
import {designSourceChips} from './components/component_badges.js';
import {installDiagnostics, recordClientError} from './components/diagnostics.js';
import {h, icon, mount} from './components/dom.js';
import {emit, navigate, on, store} from './components/store.js';
import {closeDrawer} from './components/ui.js';

installDiagnostics();

const ROUTES = [
  {path: '/login', view: 'login', bare: true},
  {path: '/', view: 'projects'},
  {path: '/projects', view: 'projects'},
  {path: '/wizard', view: 'wizard'},
  {path: '/data', view: 'data'},
  {path: '/apps', view: 'apps'},
  {path: '/apps/:aid', view: 'apps'},
  {path: '/admin', view: 'admin', admin: true},
  {path: '/p/:pid', view: 'constitution', project: true, nav: 'constitution'},
  {path: '/p/:pid/wizard', view: 'wizard', project: true},
  {
    path: '/p/:pid/constitution',
    view: 'constitution',
    project: true,
    nav: 'constitution'
  },
  // The data library is global; old project-scoped links redirect.
  {path: '/p/:pid/data', redirect: () => '/data'},
  // Design references became Imports.
  {path: '/p/:pid/references', redirect: (p) => `/p/${p.pid}/imports`},
  {path: '/p/:pid/imports', view: 'imports', project: true, nav: 'imports'},
  {
    path: '/p/:pid/imports/:iid',
    view: 'import_detail',
    project: true,
    nav: 'imports'
  },
  {path: '/p/:pid/runs', view: 'runs', project: true, nav: 'runs'},
  {path: '/p/:pid/runs/:rid', view: 'run_detail', project: true, nav: 'runs'},
  {path: '/p/:pid/compare', view: 'compare', project: true, nav: 'runs'},
  {path: '/p/:pid/effects/:eid', view: 'effect', project: true, nav: 'runs'},
  {path: '/p/:pid/review', view: 'review', project: true, nav: 'review'},
  {path: '/p/:pid/review/:rid', view: 'review', project: true, nav: 'review'},
  {
    path: '/p/:pid/guidelines',
    view: 'guidelines',
    project: true,
    nav: 'guidelines'
  },
  {path: '/p/:pid/teach', view: 'teach', project: true, nav: 'teachings'},
  // The Teach workspace is full-width (no rail), like the wizard.
  {path: '/p/:pid/teach/:sid', view: 'teach', project: true, rail: false},
  {
    path: '/p/:pid/teachings',
    view: 'teachings',
    project: true,
    nav: 'teachings'
  },
  {path: '/p/:pid/settings', view: 'settings', project: true, nav: 'settings'},
  {path: '/p/:pid/package', view: 'package', project: true, nav: 'package'},
  {path: '/p/:pid/design', view: 'design', project: true, nav: 'design'},
  {
    path: '/p/:pid/design/components/:cid',
    view: 'component_editor',
    project: true,
    nav: 'design'
  },
];

const NAV = [
  {id: 'constitution', label: 'Constitution', icon: 'article'},
  {id: 'design', label: 'Design system', icon: 'palette'},
  {id: 'runs', label: 'Runs', icon: 'play_circle'},
  {id: 'review', label: 'Review', icon: 'rate_review'},
  {id: 'guidelines', label: 'Dos & Don\u2019ts', icon: 'fact_check'},
  {id: 'teachings', label: 'Teachings', icon: 'school'},
  {id: 'imports', label: 'Imports', icon: 'move_to_inbox'},
  {id: 'package', label: 'Package', icon: 'inventory_2'},
  {id: 'settings', label: 'Settings', icon: 'tune'},
];

const SERVER_PREFIXES =
    ['/api/', '/files/', '/render/', '/renderer/', '/img/', '/docs/', '/mcp/'];

function match(pathname) {
  for (const r of ROUTES) {
    const ps = r.path.split('/').filter(Boolean);
    const xs = pathname.split('/').filter(Boolean);
    if (ps.length !== xs.length) continue;
    const params = {};
    let ok = true;
    for (let i = 0; i < ps.length; i++) {
      if (ps[i].startsWith(':'))
        params[ps[i].slice(1)] = decodeURIComponent(xs[i]);
      else if (ps[i] !== xs[i]) {
        ok = false;
        break;
      }
    }
    if (ok) return {route: r, params};
  }
  return null;
}

const app = document.getElementById('app');
const chat = new ChatPanel();
let cleanup = null;
let renderSeq = 0;
const viewCache = {};

const topbar = h('header.topbar');
const rail = h('nav.rail', {'aria-label': 'Project navigation'});
const main = h('main.main#main');
const shell = h('div.shell', topbar, h('div.shell-body', rail, main, chat.el));

async function loadView(name) {
  if (!viewCache[name]) viewCache[name] = await import(`./views/${name}.js`);
  return viewCache[name];
}

async function ensureProject(pid) {
  if (store.project && store.project.id === pid) {
    rememberProject(pid);
    return store.project;
  }
  const {project} = await get(`/api/projects/${pid}`);
  store.project = project;
  rememberProject(pid);
  return project;
}

function rememberProject(pid) {
  store.lastProjectId = pid;
  try {
    localStorage.setItem('dcb.lastProject', pid);
  } catch {
  }
}

export async function refreshProject() {
  if (!store.project) return null;
  const {project} =
      await get(`/api/projects/${store.project.id}`, {quiet: true});
  store.project = project;
  renderTopbar();
  return project;
}

function renderTopbar(route) {
  route = route || currentRoute;
  const u = store.user;
  const p = route && route.project ? store.project : null;
  const switcher = h('div.switcher');
  const btn =
      h('button.switcher-btn#project-switcher', {
        type: 'button',
        'aria-haspopup': 'menu',
        onclick: (e) => {
          e.stopPropagation();
          toggleMenu(switcher, buildProjectMenu);
        }
      },
        h('span.switcher-name', p ? p.name : 'All projects'),
        icon('unfold_more'));
  switcher.appendChild(btn);

  const userBtn =
      h('button.user-btn#user-menu', {
        type: 'button',
        onclick: (e) => {
          e.stopPropagation();
          toggleMenu(
              userWrap,
              () => h(
                  'div.menu',
                  h('div.menu-head', h('div.strong', u.display_name || u.ldap),
                    h('div.muted', `${u.ldap}${u.is_admin ? ' · admin' : ''}`)),
                  h('button.menu-item#user-report-bug', {
                    type: 'button',
                    onclick: () => {
                      if (openMenu) {
                        openMenu.el.remove();
                        openMenu = null;
                      }
                      openBugReport();
                    }
                  },
                    icon('bug_report'), 'Report a bug'),
                  h('button.menu-item#logout', {
                    type: 'button',
                    onclick: async () => {
                      await post('/api/logout').catch(() => {});
                      store.user = null;
                      store.project = null;
                      navigate('/login');
                    }
                  },
                    icon('logout'), 'Sign out')));
        }
      },
        h('span.avatar',
          (u.display_name || u.ldap || '?').slice(0, 1).toUpperCase()),
        h('span.user-name', u.ldap));
  const userWrap = h('div.user-wrap', userBtn);

  mount(
      topbar,
      h('a.brand',
        {href: '/', 'aria-label': 'Design Constitution Builder home'},
        h('span.brand-mark', '\u00a7'),
        h('span.brand-text', 'Constitution Builder')),
      h('span.topbar-sep', '/'), switcher, h('div.topbar-spacer'),
      h('nav.topnav',
        h('a.topnav-link#nav-projects',
          {href: '/', class: route && route.view === 'projects' ? 'on' : ''},
          'Projects'),
        h('a.topnav-link#nav-data',
          {href: '/data', class: route && route.view === 'data' ? 'on' : ''},
          'Data library'),
        h('a.topnav-link#nav-apps',
          {href: '/apps', class: route && route.view === 'apps' ? 'on' : ''},
          'Apps'),
        u.is_admin ? h('a.topnav-link#nav-admin', {
          href: '/admin',
          class: route && route.view === 'admin' ? 'on' : ''
        },
                       'Admin') :
                     null),
      p && route && CHAT_VIEWS.has(route.view) ?
          h('button.btn.btn-ghost.btn-sm#chat-toggle', {
            type: 'button',
            onclick: () => chat.toggle(),
            'aria-pressed': String(chat.isOpen())
          },
            icon('forum'), 'Chat') :
          null,
      h('button.btn.btn-ghost.btn-sm#report-bug-btn', {
        type: 'button',
        title: 'Report a bug (share reviewable context to AIR GenUX)',
        onclick: () => openBugReport()
      },
        icon('bug_report'), 'Report bug'),
      userWrap);
}

async function buildProjectMenu() {
  const menu =
      h('div.menu.menu-projects', h('div.menu-loading', 'Loading\u2026'));
  try {
    const {projects} = await get('/api/projects');
    store.projects = projects;
    mount(
        menu, h('div.menu-label', 'Your projects'),
        projects.filter((p) => !p.archived)
            .map(
                (p) =>
                    h('a.menu-item', {
                      href: `/p/${p.id}/constitution`,
                      class: store.project && store.project.id === p.id ? 'on' :
                                                                          ''
                    },
                      h('span.menu-item-main', p.name),
                      h('span.muted',
                        `rev ${
                            p.current_revision ? p.current_revision.number :
                                                 0}`))),
        projects.filter((p) => !p.archived).length ?
            null :
            h('div.menu-empty', 'No projects yet.'),
        h('div.menu-sep'),
        h('a.menu-item', {href: '/'}, icon('grid_view'), 'All projects'));
  } catch (e) {
    mount(menu, h('div.menu-empty', 'Could not load projects.'));
  }
  return menu;
}

let openMenu = null;
async function toggleMenu(anchor, build) {
  if (openMenu) {
    const same = openMenu.anchor === anchor;
    openMenu.el.remove();
    openMenu = null;
    if (same) return;
  }
  const el = await build();
  anchor.appendChild(el);
  openMenu = {anchor, el};
}
document.addEventListener('click', (e) => {
  if (openMenu && !openMenu.el.contains(e.target)) {
    openMenu.el.remove();
    openMenu = null;
  }
});

function renderRail(route, params) {
  if (!route.project || route.view === 'wizard' || route.rail === false) {
    rail.hidden = true;
    return;
  }
  rail.hidden = false;
  const pid = params.pid;
  const p = store.project;
  mount(
      rail,
      h('div.rail-project', h('div.rail-project-name', p ? p.name : ''),
        p ? h('div.rail-project-meta',
              `rev ${p.current_revision ? p.current_revision.number : 0}`,
              p.stats && p.stats.runs ?
                  ` · ${p.stats.runs} run${p.stats.runs === 1 ? '' : 's'}` :
                  '') :
            null,
        p ? designSourceChips(p.design_sources, {compact: true}) : null),
      h('div.rail-items',
        NAV.map(
            (n) =>
                h('a.rail-item', {
                  href: `/p/${pid}/${n.id}`,
                  id: `rail-${n.id}`,
                  class: route.nav === n.id ? 'on' : '',
                  'aria-current': route.nav === n.id ? 'page' : null,
                },
                  icon(n.icon), h('span', n.label)))),
      h('div.rail-foot',
        h('p.rail-hint', 'Constitutions decide ', h('em', 'what'),
          ' to show and ', h('em', 'how to arrange it'),
          '. Colors, type and radii live in ',
          h('a', {href: `/p/${pid}/design?tab=tokens`}, 'Design system'),
          '.')));
}

let currentRoute = null;

async function render() {
  const seq = ++renderSeq;
  closeDrawer();
  if (openMenu) {
    openMenu.el.remove();
    openMenu = null;
  }
  const url = new URL(location.href);
  let m = match(url.pathname);
  if (!m) m = {route: {view: 'notfound'}, params: {}};
  const {route, params} = m;
  if (route.redirect) {
    history.replaceState(null, '', route.redirect(params) + url.search);
    return render();
  }

  if (!store.user && route.view !== 'login') {
    const me = await get('/api/me', {quiet: true}).catch(() => ({user: null}));
    store.user = me && me.user;
    if (!store.user) {
      const next = url.pathname + url.search;
      history.replaceState(
          null, '',
          `/login${
              next && next !== '/' ? `?next=${encodeURIComponent(next)}` :
                                     ''}`);
      return render();
    }
  }
  if (route.admin && !store.user.is_admin) {
    history.replaceState(null, '', '/');
    return render();
  }
  if (cleanup) {
    try {
      cleanup();
    } catch (e) {
      console.error(e);
    }
    cleanup = null;
  }
  store.draftProvider = null;
  currentRoute = route;
  if (store.user) loadGenres();

  if (route.bare) {
    document.body.classList.remove('has-shell');
    mount(app);
    const mod = await loadView(route.view);
    if (seq !== renderSeq) return;
    cleanup = (await mod.render(app, params, {url})) || null;
    return;
  }

  document.body.classList.add('has-shell');
  if (!app.contains(shell)) mount(app, shell);

  if (route.project) {
    try {
      await ensureProject(params.pid);
    } catch (e) {
      mount(
          main,
          h('div.page', h('h1', 'Project not found'),
            h('p', h('a', {href: '/'}, 'Back to projects'))));
      return;
    }
    await chat.setProject(params.pid);
    document.body.classList.add('has-project');
  } else {
    store.project = null;
    await chat.setProject(null);
    document.body.classList.remove('has-project');
  }
  chat.setView(route.view);
  if (seq !== renderSeq) return;
  renderTopbar(route);
  renderRail(route, params);
  document.body.dataset.view = route.view;

  if (route.view === 'notfound') {
    mount(
        main,
        h('div.page', h('h1', 'Not found'),
          h('p', h('a', {href: '/'}, 'Back to projects'))));
    return;
  }
  mount(main, h('div.page-loading', h('span.spinner')));
  try {
    const mod = await loadView(route.view);
    if (seq !== renderSeq) return;
    mount(main);
    main.scrollTop = 0;
    cleanup = (await mod.render(
                  main, params,
                  {url, project: store.project, chat, refreshProject})) ||
        null;
  } catch (e) {
    console.error(e);
    recordClientError(e && e.message || String(e), e && e.stack);
    if (seq !== renderSeq) return;
    const errText = String(e && e.stack || e);
    mount(
        main,
        h('div.page',
          h('div.page-head', h('h1', 'Something went wrong'),
            h('button.btn.btn-primary#report-page-error', {
              type: 'button',
              onclick: () => openBugReport({
                prefill: {
                  title: `Page error on ${location.pathname}: ${
                      String(e && e.message || e).slice(0, 80)}`,
                  what_happened: errText,
                }
              })
            },
              icon('bug_report'), 'Report this error')),
          h('pre.error-pre', errText)));
  }
}

function go(path, replace = false) {
  if (replace)
    history.replaceState(null, '', path);
  else
    history.pushState(null, '', path);
  render();
}

on('navigate', ({path, replace}) => go(path, replace));
on('chat-toggled', () => {
  const b = document.getElementById('chat-toggle');
  if (b) b.setAttribute('aria-pressed', String(chat.isOpen()));
});
on('revision-changed', () => {
  refreshProject().then(
      () => currentRoute && currentRoute.project &&
          renderRail(currentRoute, {pid: store.project.id}));
});
on('project-updated', () => {
  renderTopbar();
  if (currentRoute && currentRoute.project)
    renderRail(currentRoute, {pid: store.project.id});
});

window.addEventListener('popstate', render);
document.addEventListener('click', (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey ||
      e.shiftKey || e.altKey)
    return;
  const a = e.target.closest('a[href]');
  if (!a || a.target || a.hasAttribute('download') ||
      a.dataset.external != null)
    return;
  const url = new URL(a.href, location.href);
  if (url.origin !== location.origin) return;
  if (SERVER_PREFIXES.some((p) => url.pathname.startsWith(p))) return;
  e.preventDefault();
  if (url.pathname === location.pathname && url.search === location.search &&
      url.hash) {
    location.hash = url.hash;
    return;
  }
  go(url.pathname + url.search + url.hash);
});

setUnauthorizedHandler(() => {
  store.user = null;
  if (!location.pathname.startsWith('/login')) {
    go(`/login?next=${encodeURIComponent(location.pathname + location.search)}`,
        true);
  }
});

// Expose for debugging and tests.
window.__dcb = {
  store,
  navigate,
  emit,
  openBugReport,
};

render();
