// Custom catalog components (web templates) as official A2UI Lit elements.
//
// defineCustomComponent(definition, catalogId) turns an A2UI Designer custom
// component Definition ({name, props (JSON schema), web: {template, css}})
// into a WebComponentImplementation ({name, schema, tagName, element}) for a
// project Catalog. The element extends the unmodified A2uiLitElement:
//   * props are bound by the library's A2uiController/GenericBinder using a
//     Zod schema derived from the Definition's props JSON schema (common-type
//     `$ref`s map onto the v1.0 schemas, so bindings, actions and child refs
//     behave exactly like Basic components);
//   * the sanitized template (compiled by custom.js) is instantiated in an
//     open shadow root with the sanitized, scoped CSS; design tokens reach it
//     through inherited `--a2ui-*` custom properties;
//   * slot props (ComponentId / ChildList) render their children with the
//     library's renderNode() into light-DOM wrappers carrying slot="<prop>".
// Tag names are versioned by a content hash, so editing a Definition and
// re-mounting registers a fresh element instead of colliding with the old one.

import {AccessibilityAttributesSchema, A2uiLitElement, ActionSchema, ChildListSchema, ChildSchema, DataContext, DynamicBooleanSchema, DynamicNumberSchema, DynamicStringListSchema, DynamicStringSchema, DynamicValueSchema, html, nothing, z,} from '../vendor/a2ui.bundle.js';
import {compileCustomComponent, fromNestedControl, renderCustomTemplate} from './custom.js';

// Basic Catalog icon names -> Material Symbols Rounded ligatures [glyph, fill].
const ICONS = {
  accountCircle: ['account_circle'], add: ['add'], arrowBack: ['arrow_back'],
  arrowForward: ['arrow_forward'], attachFile: ['attach_file'],
  calendarToday: ['calendar_today'], call: ['call'], camera: ['photo_camera'],
  check: ['check'], close: ['close'], delete: ['delete'], download: ['download'],
  edit: ['edit'], event: ['event'], error: ['error'], fastForward: ['fast_forward', 1],
  favorite: ['favorite', 1], favoriteOff: ['favorite', 0], folder: ['folder'],
  help: ['help'], home: ['home'], info: ['info'], locationOn: ['location_on'],
  lock: ['lock'], lockOpen: ['lock_open'], mail: ['mail'], menu: ['menu'],
  moreVert: ['more_vert'], moreHoriz: ['more_horiz'],
  notificationsOff: ['notifications_off'], notifications: ['notifications'],
  pause: ['pause', 1], payment: ['credit_card'], person: ['person'], phone: ['phone'],
  photo: ['photo'], play: ['play_arrow', 1], print: ['print'], refresh: ['refresh'],
  rewind: ['fast_rewind', 1], search: ['search'], send: ['send'], settings: ['settings'],
  share: ['share'], shoppingCart: ['shopping_cart'], skipNext: ['skip_next', 1],
  skipPrevious: ['skip_previous', 1], star: ['star', 1], starHalf: ['star_half', 1],
  starOff: ['star', 0], stop: ['stop', 1], upload: ['upload'], visibility: ['visibility'],
  visibilityOff: ['visibility_off'], volumeDown: ['volume_down'],
  volumeMute: ['volume_mute'], volumeOff: ['volume_off'], volumeUp: ['volume_up'],
  warning: ['warning'],
};

/** Icon name -> {glyph, fill, known}; unknown names are snake_cased. */
export function iconGlyph(name) {
  const s = String(name || '').trim();
  const hit = ICONS[s];
  if (hit) return {glyph: hit[0], fill: hit[1] ?? 0, known: true};
  const snake = s.replace(/([a-z0-9])([A-Z])/g, '$1_$2').replace(/[\s-]+/g, '_').toLowerCase();
  return {glyph: snake || 'help', fill: 0, known: false};
}

const REF_SCHEMAS = {
  DynamicString: () => DynamicStringSchema,
  DynamicNumber: () => DynamicNumberSchema,
  DynamicBoolean: () => DynamicBooleanSchema,
  DynamicStringList: () => DynamicStringListSchema,
  DynamicValue: () => DynamicValueSchema,
  DataBinding: () => DynamicValueSchema,
  ComponentId: () => ChildSchema,
  Child: () => ChildSchema,
  ChildList: () => ChildListSchema,
  Action: () => ActionSchema.describe('REF:#/$defs/Action'),
};

/** Kind of a props JSON-schema entry: common-type name or 'value'. */
export function propKind(schema) {
  if (!schema || typeof schema !== 'object') return 'value';
  const ref = String(schema.$ref || '');
  if (ref) return ref.split('/').pop();
  return 'value';
}

/**
 * Props JSON schema -> Zod object schema for the A2uiController. Common-type
 * refs map onto the library's v1.0 schemas (which carry the REF markers the
 * GenericBinder keys on); every other prop is a DynamicValue so literals,
 * `@path` bindings and `@call`s all resolve (and stay reactive).
 */
export function zodFromProps(props) {
  const shape = {};
  const properties = props && typeof props === 'object' && props.properties &&
          typeof props.properties === 'object' ?
      props.properties :
      {};
  const required = new Set(Array.isArray(props?.required) ? props.required : []);
  for (const [name, s] of Object.entries(properties)) {
    if (['id', 'component', 'catalogId', 'weight', 'accessibility'].includes(name)) continue;
    const kind = propKind(s);
    const make = REF_SCHEMAS[kind] || (() => DynamicValueSchema);
    let zs = make();
    if (!required.has(name) || (s && s.default !== undefined)) zs = zs.optional();
    shape[name] = zs;
  }
  shape.weight = z.number().optional();
  shape.accessibility = AccessibilityAttributesSchema.optional();
  return z.object(shape).passthrough();
}

function hash(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(36);
}

const kebab = (name) => String(name || 'component')
                            .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
                            .replace(/[^A-Za-z0-9-]+/g, '-')
                            .toLowerCase()
                            .replace(/^-+|-+$/g, '') || 'component';

const isBinding = (v) => !!v && typeof v === 'object' && !Array.isArray(v) &&
    (typeof v['@path'] === 'string' || typeof v.path === 'string');

const REGISTRY = new Map();  // tagName -> implementation

/**
 * Definition -> WebComponentImplementation for a project Catalog.
 * Returns {impl, compiled, tagName}; `compiled.ok === false` still returns an
 * implementation that renders an error placeholder (and reports the errors).
 */
export function defineCustomComponent(definition, catalogId = '') {
  const def = definition && typeof definition === 'object' ? definition : {};
  const name = String(def.name || 'Custom');
  const key = JSON.stringify({n: name, w: def.web || null, p: def.props || null});
  const tagName = `a2uid-x-${kebab(name)}-${hash(key)}`;
  const cached = REGISTRY.get(tagName);
  if (cached) return {impl: cached, compiled: cached.compiled, tagName};

  let compiled;
  try {
    compiled = compileCustomComponent(def);
  } catch (e) {
    compiled = {ok: false, errors: [String(e && e.message || e)], slots: [], defaults: {}, styleText: ''};
  }
  const schema = zodFromProps(def.props);
  const slots = compiled.slots || [];

  class CustomComponentElement extends A2uiLitElement {
    createRenderRoot() {
      if (!this._a2uidShadow) {
        this._a2uidShadow = this.attachShadow({mode: 'open'});
        const style = document.createElement('style');
        style.textContent = compiled.styleText || '';
        this._a2uidShadow.appendChild(style);
      }
      // Lit renders the slot wrappers into the light DOM; the template lives
      // in the shadow root and is driven imperatively from updated().
      return this;
    }

    willUpdate(changed) {
      super.willUpdate(changed);
      const props = this.controller?.props;
      if (props && typeof props.weight === 'number') this.style.flex = String(props.weight);
      else this.style.removeProperty('flex');
    }

    render() {
      const props = this.controller?.props;
      if (!props || !compiled.ok) return nothing;
      return html`${slots.map((s) => {
        const v = props[s];
        if (v == null) return nothing;
        const items = Array.isArray(v) ? v : [v];
        return html`<div slot=${s} class="a2uid-slot" style="display:contents">${
            items.map((c) => this.renderNode(c))}</div>`;
      })}`;
    }

    updated(changed) {
      super.updated?.(changed);
      const root = this._a2uidShadow;
      if (!root) return;
      if (!compiled.ok) {
        if (!this._a2uidError) {
          this._a2uidError = document.createElement('div');
          this._a2uidError.className = 'a2uid-custom-error';
          this._a2uidError.textContent = `${name}: ${(compiled.errors || []).slice(0, 2).join('; ') ||
              'invalid definition'}`;
          root.appendChild(this._a2uidError);
        }
        return;
      }
      if (!this._a2uidInst) {
        this._a2uidInst = renderCustomTemplate(compiled, root, this._hooks());
      }
      try {
        this._a2uidInst.update();
      } catch (e) {
        console.error(`[a2ui-designer] ${name} template update failed`, e);
      }
    }

    _dataContextFor(scope) {
      const dc = this.context?.dataContext;
      if (scope == null || !dc) return dc;
      const surface = dc.surface;
      return surface ? new DataContext(surface, scope) : dc;
    }

    _hooks() {
      const self = this;
      const interactive = () => !self.closest('.a2ui-inert');
      return {
        doc: document,
        prop(p) {
          const props = self.controller?.props || {};
          const v = props[p];
          if (v === undefined) return compiled.defaults ? compiled.defaults[p] : undefined;
          return v;
        },
        propPath(p) {
          const raw = self.context?.componentModel?.properties?.[p];
          if (!isBinding(raw)) return null;
          try {
            return self.context.dataContext.resolvePath(raw['@path'] ?? raw.path);
          } catch {
            return null;
          }
        },
        resolveIn(value, scope) {
          try {
            return self._dataContextFor(scope).resolveDynamicValue(value);
          } catch {
            return undefined;
          }
        },
        icon: (n) => iconGlyph(n),
        image(img) {
          img.decoding = 'async';
          img.draggable = false;
          img.addEventListener('error', () => { img.dataset.a2uiMissing = '1'; });
          img.addEventListener('load', () => { delete img.dataset.a2uiMissing; });
        },
        action(el, prop, itemScope, resolvedAction) {
          const props = self.controller?.props || {};
          const bound = resolvedAction === undefined ? props[prop] : undefined;
          const raw = resolvedAction !== undefined ? resolvedAction : null;
          if (typeof bound !== 'function' && (!raw || typeof raw !== 'object')) return false;
          el.setAttribute('role', 'button');
          el.tabIndex = interactive() ? 0 : -1;
          const fire = (e) => {
            if (!interactive() || fromNestedControl(e, el)) return;
            if (typeof bound === 'function') {
              bound();
              return;
            }
            try {
              const dc = self._dataContextFor(itemScope);
              const action = dc && typeof dc.resolveAction === 'function' ? dc.resolveAction(raw) : raw;
              self.context.dispatchAction(action);
            } catch (err) {
              console.error(`[a2ui-designer] ${name} action failed`, err);
            }
          };
          el.addEventListener('click', fire);
          el.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            if (fromNestedControl(e, el)) return;
            e.preventDefault();
            fire(e);
          });
          return true;
        },
      };
    }
  }
  CustomComponentElement.tagName = tagName;

  const impl = {
    name,
    schema,
    tagName,
    element: CustomComponentElement,
    compiled,
    catalogId,
  };
  REGISTRY.set(tagName, impl);
  return {impl, compiled, tagName};
}
