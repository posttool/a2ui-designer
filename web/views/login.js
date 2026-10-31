// Login: dev-mode LDAP sign-in.
import {post} from '../components/api.js';
import {h} from '../components/dom.js';
import {navigate, store} from '../components/store.js';

export async function render(root, params, {url}) {
  const next = url.searchParams.get('next') || '/';
  const err = h('p.login-error', {role: 'alert'});
  const input = h('input.input.input-lg#login-ldap', {
    name: 'ldap',
    autocomplete: 'username',
    placeholder: 'ldap',
    autofocus: true,
    spellcheck: 'false'
  });
  const btn =
      h('button.btn.btn-primary.btn-lg#login-submit', {type: 'submit'},
        'Continue');
  const form =
      h('form.login-form', {
        onsubmit: async (e) => {
          e.preventDefault();
          const ldap = input.value.trim().replace(/@.*$/, '');
          if (!ldap) {
            err.textContent = 'Enter your LDAP.';
            input.focus();
            return;
          }
          btn.disabled = true;
          err.textContent = '';
          try {
            const {user} =
                await post('/api/login', {ldap}, {quiet: true, allow401: true});
            store.user = user;
            navigate(
                next.startsWith('/') && !next.startsWith('/login') ? next : '/',
                {replace: true});
          } catch (ex) {
            err.textContent = ex.message || 'Sign-in failed.';
            btn.disabled = false;
          }
        }
      },
        h('label.field', h('span.field-label', 'LDAP'), input), btn, err);

  root.appendChild(h(
      'div.login',
      h('div.login-card', h('div.login-mark', '\u00a7'),
        h('h1.login-title', 'Design Constitution Builder'),
        h('p.login-lede',
          'Write the decisions a UI generator should make in context \u2014 then test them against hundreds of real-feeling situations, review what comes back, and revise.'),
        form,
        h('p.login-foot',
          'Development sign-in. Your LDAP identifies your projects.')),
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
  setTimeout(() => input.focus(), 0);
}
