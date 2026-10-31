// Login: shared-password sign-in (`--auth_mode password`). In the default
// `--auth_mode local` there is no sign-in and this view redirects home.
import {get, post} from '../components/api.js';
import {h} from '../components/dom.js';
import {navigate, store} from '../components/store.js';

export async function render(root, params, {url}) {
  const next = url.searchParams.get('next') || '/';
  const safeNext =
      next.startsWith('/') && !next.startsWith('/login') ? next : '/';
  const me = await get('/api/me', {quiet: true, allow401: true})
                 .catch(() => ({user: null}));
  if (me && me.auth_mode) store.authMode = me.auth_mode;
  if (me && me.user) {
    store.user = me.user;
    navigate(safeNext, {replace: true});
    return;
  }
  const err = h('p.login-error', {role: 'alert'});
  const pwInput = h('input.input.input-lg#login-password', {
    name: 'password',
    type: 'password',
    autocomplete: 'current-password',
    placeholder: 'password',
    autofocus: true,
  });
  const input = h('input.input.input-lg#login-username', {
    name: 'username',
    autocomplete: 'username',
    placeholder: 'optional',
    spellcheck: 'false'
  });
  const btn =
      h('button.btn.btn-primary.btn-lg#login-submit', {type: 'submit'},
        'Continue');
  const form =
      h('form.login-form', {
        onsubmit: async (e) => {
          e.preventDefault();
          const password = pwInput.value;
          const username = input.value.trim();
          if (!password) {
            err.textContent = 'Enter the password.';
            pwInput.focus();
            return;
          }
          btn.disabled = true;
          err.textContent = '';
          try {
            const {user} = await post(
                '/api/login', {password, username},
                {quiet: true, allow401: true});
            store.user = user;
            navigate(safeNext, {replace: true});
          } catch (ex) {
            err.textContent = ex.message || 'Sign-in failed.';
            btn.disabled = false;
          }
        }
      },
        h('label.field', h('span.field-label', 'Password'), pwInput),
        h('label.field', h('span.field-label', 'Username (optional)'), input),
        btn, err);

  root.appendChild(h(
      'div.login',
      h('div.login-card', h('div.login-mark', '\u00a7'),
        h('h1.login-title', 'A2UI Designer'),
        h('p.login-lede',
          'Write the decisions a UI generator should make in context \u2014 then test them against hundreds of real-feeling situations, review what comes back, and revise.'),
        form,
        h('p.login-foot',
          'This server is protected by a shared password. Your username, if you give one, labels your projects.')),
      h('div.login-aside',
        h('ol.login-steps',
          h('li', h('strong', 'Write'),
            h('span',
              'a constitution: which components and layouts fit which data, what comes first, what waits behind disclosure.')),
          h('li', h('strong', 'Run'),
            h('span',
              'it on synthetic data across 26+ genres \u2014 agents, editorial, apps, care, enterprise, personal assistant \u2014 plus any categories you add.')),
          h('li', h('strong', 'Review'),
            h('span',
              'the rendered outputs, worst first. Save Dos and Don\u2019ts.')),
          h('li', h('strong', 'Revise'),
            h('span',
              'with chat. Each accepted change becomes a new revision you can compare.'))))));
  setTimeout(() => pwInput.focus(), 0);
}
