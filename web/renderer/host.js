// A2UI Designer renderer host: a thin layer over the UNMODIFIED official
// A2UI web renderer (@a2ui/web_core + @a2ui/lit 0.14, protocol v1.0, bundled
// as ../vendor/a2ui.bundle.js).
//
//   import {mountSurface, themeToCssVars} from '/renderer/host.js';
//   const s = mountSurface(el, messages, {theme, customComponents, onAction});
//   await s.settled(); s.meta; s.update(more); s.destroy();
//
// What the host adds around the library:
//   * catalogs: Basic (verbatim upstream v1) + the A2UI Designer extension
//     catalog (HeroCard, SvgIcon) + a per-mount project catalog built from the
//     custom component Definitions (custom_element.js); composition
//     components are lowered to Basic first (composition.js);
//   * design tokens -> CSS variables on the host element (tokens.js), mapped
//     onto the official `--a2ui-*` variables;
//   * a safe markdown renderer (markdown.js) for Text;
//   * inspect metadata: every rendered component element gets
//     `data-a2ui-id` / `data-a2ui-scope` (template instance data path) from
//     its ComponentContext, so render.html boxes/highlighting keep working;
//   * per-message error isolation, an "unsupported component" placeholder,
//     diagnostics (`meta`) and a `settled()` promise for screenshotters.

import {A2uiLitElement, basicCatalog, Catalog, css, html, MessageProcessor, nothing, setMarkdownRenderer, z,} from '../vendor/a2ui.bundle.js';
import {expandCompositeComponents, shouldLower} from './composition.js';
import {defineCustomComponent} from './custom_element.js';
import {EXTENSION_CATALOG_ID, EXTENSION_COMPONENTS, extensionCatalog} from './extensions.js';
import {renderMarkdownHtml} from './markdown.js';
import {applyTheme, ensureAssets, resolveTheme, waitForThemeFonts} from './tokens.js';

export * from './tokens.js';
export {compileCustomComponent} from './custom.js';
export {defineCustomComponent} from './custom_element.js';
export {expandCompositeComponents} from './composition.js';
export {EXTENSION_CATALOG_ID, extensionCatalog} from './extensions.js';

export const BASIC_CATALOG_ID = basicCatalog.id;
export const DEFAULT_PROJECT_CATALOG_ID =
    'https://posttool.github.io/a2ui-designer/catalogs/projects/local/catalog.json';
/** Private catalog for host-only placeholders (never sent to agents). */
export const HOST_CATALOG_ID = 'urn:a2ui-designer:host';
export const BASIC_COMPONENT_NAMES = new Set(basicCatalog.components.keys());

setMarkdownRenderer(async (md) => renderMarkdownHtml(md));

// ---------------------------------------------------------------------------
// Unsupported-component placeholder (host catalog).

const CHILD_FIELDS = ['child', 'content', 'trigger', 'children'];

class A2uidUnsupportedElement extends A2uiLitElement {
  createRenderRoot() {
    return this;
  }
  render() {
    const p = this.controller?.props;
    if (!p) return nothing;
    const kids = Array.isArray(p.children) ? p.children : [];
    return html`<div class="a2uid-unsupported">
      <div class="a2uid-unsupported-label"><span class="a2uid-symbol" aria-hidden="true">extension</span><span>${
        p.name || 'Unknown component'}</span></div>
      ${p.label ? html`<div class="a2uid-unsupported-text">${p.label}</div>` : nothing}
      ${kids.length ? html`<div class="a2uid-unsupported-children">${kids.map((c) => this.renderNode(c))}</div>` : nothing}
    </div>`;
  }
}
A2uidUnsupportedElement.tagName = 'a2uid-unsupported';
A2uidUnsupportedElement.styles = css`
  :host { display: block; min-width: 0; }
`;

const UnsupportedApi = {
  name: 'Unsupported',
  schema: z.object({
    name: z.string().optional(),
    label: z.any().optional(),
    children: z.any().optional(),
    weight: z.number().optional(),
  }).passthrough(),
};
const hostCatalog = new Catalog(HOST_CATALOG_ID, '1.0', [{
  ...UnsupportedApi,
  tagName: A2uidUnsupportedElement.tagName,
  element: A2uidUnsupportedElement,
}]);

// ---------------------------------------------------------------------------
// Message preprocessing.

const isObj = (v) => !!v && typeof v === 'object' && !Array.isArray(v);
const clone = (v) => JSON.parse(JSON.stringify(v));

function childIdsOf(comp) {
  const out = [];
  for (const k of CHILD_FIELDS) {
    const v = comp[k];
    if (typeof v === 'string') out.push(v);
    else if (Array.isArray(v)) out.push(...v.filter((x) => typeof x === 'string'));
  }
  if (Array.isArray(comp.tabs)) {
    for (const t of comp.tabs) if (isObj(t) && typeof t.child === 'string') out.push(t.child);
  }
  return out;
}

function labelOf(comp) {
  const l = comp.label ?? comp.text ?? comp.title;
  if (typeof l === 'string' || typeof l === 'number') return String(l);
  if (isObj(l) && (typeof l['@path'] === 'string' || typeof l['@call'] === 'string')) return l;
  return undefined;
}

/**
 * Normalizes messages for the official processor without changing their
 * meaning: lowers composition components, fills `version`, injects the
 * per-component `catalogId` for extension/project components (mirrors
 * core/a2ui/normalize.py), points surfaces at the Basic catalog when they
 * named an unregistered default, and swaps unknown components for the host
 * placeholder so the rest of the surface still renders.
 */
function preprocess(messages, env) {
  const list = Array.isArray(messages) ? messages : [messages];
  const lowered = expandCompositeComponents(list.filter(isObj), env.defs);
  const known = (catalogId, name) => {
    if (catalogId === BASIC_CATALOG_ID) return BASIC_COMPONENT_NAMES.has(name);
    if (catalogId === EXTENSION_CATALOG_ID) return env.extensions && EXTENSION_COMPONENTS.has(name);
    if (catalogId === env.projectId) return env.projectNames.has(name);
    return false;
  };
  const fixComponent = (c) => {
    if (!isObj(c)) return c;
    const name = typeof c.component === 'string' ? c.component : '';
    const cid = typeof c.catalogId === 'string' ? c.catalogId : undefined;
    if (!name) return c;  // partial update of an existing component
    if (cid && known(cid, name)) return c;
    if (env.projectNames.has(name)) return {...c, catalogId: env.projectId};
    if (env.extensions && EXTENSION_COMPONENTS.has(name)) return {...c, catalogId: EXTENSION_CATALOG_ID};
    if (BASIC_COMPONENT_NAMES.has(name)) {
      const {catalogId: _drop, ...rest} = c;
      return rest;
    }
    env.unsupported.add(name);
    const out = {id: c.id, component: 'Unsupported', catalogId: HOST_CATALOG_ID, name};
    const label = labelOf(c);
    if (label !== undefined) out.label = label;
    const kids = childIdsOf(c);
    if (kids.length) out.children = kids;
    if (typeof c.weight === 'number') out.weight = c.weight;
    return out;
  };
  return lowered.map((m) => {
    const msg = clone(m);
    if (!msg.version) msg.version = 'v1.0';
    if (isObj(msg.createSurface)) {
      const cs = msg.createSurface;
      if (cs.catalogId !== undefined && cs.catalogId !== BASIC_CATALOG_ID) {
        env.warnings.add(`surface "${cs.surfaceId}" catalogId ${cs.catalogId} rendered with the Basic catalog as default`);
        cs.catalogId = BASIC_CATALOG_ID;
      }
      if (cs.catalogId === undefined) cs.catalogId = BASIC_CATALOG_ID;
      if ('theme' in cs) delete cs.theme;  // removed in v1.0; tokens come from the project
      if (Array.isArray(cs.components)) cs.components = cs.components.map(fixComponent);
    }
    if (isObj(msg.updateComponents) && Array.isArray(msg.updateComponents.components)) {
      msg.updateComponents.components = msg.updateComponents.components.map(fixComponent);
    }
    return msg;
  });
}

// ---------------------------------------------------------------------------
// Inspect annotation.

/** Template-instance scope like the old renderer: '' at the root, else the data path. */
function scopeOf(context) {
  const p = context?.dataContext?.path;
  return !p || p === '/' ? '' : String(p);
}

function annotateTree(root) {
  const els = root.querySelectorAll('*');
  for (const el of els) {
    const ctx = el.context;
    if (!(el instanceof A2uiLitElement) || !ctx || !ctx.componentModel) continue;
    const id = String(ctx.componentModel.id);
    const scope = scopeOf(ctx);
    let target = el;
    // Basic Text is `display: contents`; its box lives on the inner span.
    if (el.localName === 'a2ui-basic-text' || getComputedStyle(el).display === 'contents') {
      const kids = [...el.children];
      if (kids.length === 1) target = kids[0];
    }
    if (target !== el) {
      el.removeAttribute('data-a2ui-id');
      el.removeAttribute('data-a2ui-scope');
    }
    if (target.dataset.a2uiId !== id) target.dataset.a2uiId = id;
    if (scope) {
      if (target.dataset.a2uiScope !== scope) target.dataset.a2uiScope = scope;
    } else if (target.hasAttribute('data-a2ui-scope')) {
      target.removeAttribute('data-a2ui-scope');
    }
    if (!target.dataset.a2uiComponent) target.dataset.a2uiComponent = String(ctx.componentModel.type || '');
  }
}

/** All <img> under `root`, including open shadow roots of custom components. */
function allImages(root) {
  const out = [...root.querySelectorAll('img')];
  for (const el of root.querySelectorAll('*')) {
    if (el.shadowRoot) out.push(...allImages(el.shadowRoot));
  }
  return out;
}

/** Waits for pending Lit updates of every A2UI element under `root`. */
async function litSettled(root, rounds = 4) {
  for (let i = 0; i < rounds; i++) {
    const pending = [];
    for (const el of root.querySelectorAll('*')) {
      if (el.updateComplete && typeof el.updateComplete.then === 'function' && el.isUpdatePending) {
        pending.push(el.updateComplete.catch(() => {}));
      }
    }
    if (!pending.length) return;
    await Promise.all(pending);
  }
}

// ---------------------------------------------------------------------------
// Mount.

/**
 * Mounts A2UI v1.0 surfaces into `container`.
 * opts: {theme, customComponents: {Name: Definition},
 *        catalogs: {projectCatalogId, extensions: bool}, viewport,
 *        framing: 'card'|'page', interactive=true, inspect, onAction(payload),
 *        onOpenUrl(url), injectAssets=true}
 * Returns {host, processor, settled(capMs), meta, update(messages),
 *          annotate(), getData(surfaceId), destroy()}.
 */
export function mountSurface(container, messages = [], opts = {}) {
  const doc = container.ownerDocument || document;
  if (opts.injectAssets !== false) {
    try {
      ensureAssets(doc);
    } catch {
    }
  }
  const theme = resolveTheme(opts.theme || {});
  const interactive = opts.interactive !== false;
  applyTheme(container, theme);
  const host = doc.createElement('div');
  host.className = [
    'a2ui-root',
    'a2ui-host',
    `a2ui-scheme-${theme.scheme}`,
    `a2ui-framing-${opts.framing === 'page' ? 'page' : 'card'}`,
    interactive ? 'a2ui-interactive' : 'a2ui-inert',
  ].join(' ');
  if (opts.inspect) host.classList.add('a2ui-inspecting');
  container.appendChild(host);
  applyTheme(host, theme);

  // Catalogs.
  const catalogsOpt = isObj(opts.catalogs) ? opts.catalogs : {};
  const extensions = catalogsOpt.extensions !== false;
  const projectId = String(catalogsOpt.projectCatalogId || DEFAULT_PROJECT_CATALOG_ID);
  const defs = {};
  const customErrors = [];
  const impls = [];
  const projectNames = new Set();
  if (isObj(opts.customComponents)) {
    for (const [name, raw] of Object.entries(opts.customComponents)) {
      if (!name || BASIC_COMPONENT_NAMES.has(name) || !isObj(raw)) continue;
      const def = {...raw, name: raw.name || name};
      defs[name] = def;
      if (shouldLower(def)) continue;
      try {
        const {impl, compiled} = defineCustomComponent(def, projectId);
        impls.push({...impl, name});
        projectNames.add(name);
        if (!compiled.ok) {
          customErrors.push(`custom component "${name}": ${
              (compiled.errors || []).slice(0, 3).join('; ') || 'invalid definition'}`);
        }
      } catch (e) {
        customErrors.push(`custom component "${name}": ${e.message || e}`);
      }
    }
  }
  const catalogs = [basicCatalog];
  if (extensions) catalogs.push(extensionCatalog);
  if (impls.length) catalogs.push(new Catalog(projectId, '1.0', impls));
  catalogs.push(hostCatalog);

  const env = {
    defs,
    extensions,
    projectId,
    projectNames,
    unsupported: new Set(),
    warnings: new Set(),
  };
  const errors = [];
  const err = (m) => {
    const s = String(m);
    if (!errors.includes(s) && errors.length < 200) errors.push(s);
  };
  customErrors.forEach(err);

  const onAction = typeof opts.onAction === 'function' ? opts.onAction : null;
  const processor = new MessageProcessor(catalogs, (action) => {
    if (!interactive) return;
    const payload = {version: 'v1.0', action};
    if (onAction) {
      try {
        onAction(payload);
      } catch (e) {
        err(`onAction: ${e.message || e}`);
      }
    }
  }, {version: 'v1.0'});

  const views = new Map();
  const subs = [];
  subs.push(processor.onSurfaceCreated((surface) => {
    const el = doc.createElement('a2ui-surface');
    el.className = 'a2ui-surface';
    el.dataset.surfaceId = surface.id;
    el.surface = surface;
    views.set(surface.id, el);
    host.appendChild(el);
  }));
  subs.push(processor.onSurfaceDeleted((id) => {
    const el = views.get(id);
    if (el) el.remove();
    views.delete(id);
  }));

  const runOne = (msg) => {
    try {
      processor.processMessages([msg]);
      return;
    } catch (e) {
      const uc = msg.updateComponents;
      if (!isObj(uc) || !Array.isArray(uc.components) || uc.components.length < 2) {
        err(describeError(msg, e));
        return;
      }
    }
    // Isolate the failing component(s) so the rest of the surface renders.
    for (const comp of msg.updateComponents.components) {
      try {
        processor.processMessages([{
          version: msg.version,
          updateComponents: {surfaceId: msg.updateComponents.surfaceId, components: [comp]},
        }]);
      } catch (e2) {
        err(describeError({updateComponents: {components: [comp]}}, e2));
      }
    }
  };

  const apply = (msgs) => {
    let prepared = [];
    try {
      prepared = preprocess(msgs, env);
    } catch (e) {
      err(`preprocess: ${e.message || e}`);
      return;
    }
    for (const m of prepared) {
      if (m.callRendererFunction || m.agentFunctionResponse) continue;  // tolerated, ignored
      runOne(m);
    }
    scheduleAnnotate();
  };

  let annotateQueued = false;
  const annotate = () => {
    annotateQueued = false;
    try {
      annotateTree(host);
    } catch (e) {
      console.warn('[a2ui-designer] annotate failed', e);
    }
  };
  const scheduleAnnotate = () => {
    if (annotateQueued) return;
    annotateQueued = true;
    (doc.defaultView || window).requestAnimationFrame(annotate);
  };
  const mo = new MutationObserver(scheduleAnnotate);
  mo.observe(host, {childList: true, subtree: true});

  apply(messages);

  const api = {
    host,
    processor,
    update(more) {
      apply(more);
      return api;
    },
    annotate,
    get meta() {
      let components = 0;
      for (const s of processor.getSurfaces().values()) components += s.componentsModel?.size || 0;
      const customNames = new Set();
      for (const el of host.querySelectorAll('[data-a2ui-component]')) {
        const c = el.dataset.a2uiComponent;
        if (projectNames.has(c)) customNames.add(c);
      }
      const surfaceErrors = [];
      for (const [id, s] of processor.getSurfaces()) {
        if (!s.componentsModel?.get('root')) surfaceErrors.push(`surface "${id}" has no "root" component`);
      }
      const missingImages = allImages(host).filter((img) => {
        const src = img.getAttribute('src');
        if (!src) return true;
        return img.dataset.a2uiMissing === '1' || (img.complete && img.naturalWidth === 0);
      }).length;
      return {
        surfaces: processor.getSurfaces().size,
        components,
        unsupported: [...env.unsupported],
        custom: [...customNames],
        missingImages,
        errors: [...new Set([...errors, ...surfaceErrors])],
        warnings: [...env.warnings],
      };
    },
    /** Resolves after Lit updates, fonts and images settle (or `capMs`). */
    async settled(capMs = 4000) {
      const cap = new Promise((r) => setTimeout(r, capMs));
      const work = (async () => {
        await litSettled(host);
        annotate();
        const waits = [waitForThemeFonts(theme, doc, Math.min(capMs, 2500)).catch(() => {})];
        if (doc.fonts && doc.fonts.ready) waits.push(doc.fonts.ready.catch(() => {}));
        for (const img of allImages(host)) {
          if (img.complete || !img.getAttribute('src')) continue;
          waits.push(new Promise((r) => {
            img.addEventListener('load', r, {once: true});
            img.addEventListener('error', r, {once: true});
          }));
        }
        await Promise.all(waits);
        await litSettled(host);
      })();
      await Promise.race([work, cap]);
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      annotate();
    },
    getData(surfaceId) {
      const s = surfaceId ? processor.getSurface(surfaceId) : processor.getSurfaces().values().next().value;
      try {
        return s ? s.dataModel.getSignal('/').value : undefined;
      } catch {
        return undefined;
      }
    },
    destroy() {
      mo.disconnect();
      for (const s of subs) {
        try {
          s.unsubscribe();
        } catch {
        }
      }
      try {
        processor.dispose();
      } catch {
      }
      views.clear();
      host.remove();
    },
  };
  return api;
}

function describeError(msg, e) {
  const text = String((e && e.message) || e).split('\n').slice(0, 4).join(' ').slice(0, 400);
  const uc = msg && msg.updateComponents;
  if (isObj(uc) && Array.isArray(uc.components) && uc.components.length === 1) {
    const c = uc.components[0] || {};
    return `${c.component || 'component'} "${c.id}": ${text}`;
  }
  const kind = msg && Object.keys(msg).find((k) => k !== 'version');
  return `${kind || 'message'}: ${text}`;
}
