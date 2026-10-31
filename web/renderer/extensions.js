// A2UI Designer extension catalog: HeroCard + SvgIcon as Lit elements on top
// of the unmodified @a2ui/web_core A2uiLitElement base class.
//
// Catalog id (see seeds/a2ui_designer_catalog.json):
//   https://posttool.github.io/a2ui-designer/catalogs/extensions/v1/catalog.json
// Components from this catalog carry `"catalogId": EXTENSION_CATALOG_ID` on
// the component object (v1.0 mixed-catalog rules); host.js injects it when an
// older payload omits it.

import {A2uiLitElement, AccessibilityAttributesSchema, Catalog, ChildSchema, css, DynamicStringSchema, html, nothing, z,} from '../vendor/a2ui.bundle.js';

export const EXTENSION_CATALOG_ID =
    'https://posttool.github.io/a2ui-designer/catalogs/extensions/v1/catalog.json';

const ref = (schema, def, desc = '') =>
    schema.describe(`REF:#/$defs/${def}${desc ? `|${desc}` : ''}`);

/** URL guard for images (same policy as custom.js safeImageUrl). */
export function safeMediaUrl(url) {
  const s = String(url ?? '').trim();
  if (!s) return '';
  if (/^(javascript|vbscript):/i.test(s.replace(/[\s\u0000-\u001f]/g, ''))) return '';
  if (/^data:/i.test(s) && !/^data:image\//i.test(s)) return '';
  return s;
}

/**
 * Inline SVG markup -> sanitized `data:image/svg+xml` URL. The markup is
 * parsed as XML and only drawing elements/attributes survive; scripts,
 * foreignObject, event handlers, external hrefs and CSS url()/expressions are
 * dropped. The result is shown through <img>, where SVG scripts never run.
 */
export function sanitizeSvg(markup) {
  const src = String(markup ?? '').trim();
  if (!src || src.length > 100000) return '';
  const ALLOWED = new Set([
    'svg', 'g', 'path', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
    'rect', 'text', 'tspan', 'defs', 'lineargradient', 'radialgradient',
    'stop', 'clippath', 'mask', 'title', 'desc', 'symbol', 'use',
  ]);
  try {
    const doc = new DOMParser().parseFromString(src, 'image/svg+xml');
    const root = doc.documentElement;
    if (!root || root.nodeName.toLowerCase() !== 'svg' ||
        doc.getElementsByTagName('parsererror').length) {
      return '';
    }
    const walk = (el) => {
      for (const child of [...el.children]) {
        if (!ALLOWED.has(child.localName.toLowerCase())) {
          child.remove();
          continue;
        }
        walk(child);
      }
      for (const attr of [...el.attributes]) {
        const n = attr.name.toLowerCase();
        const v = attr.value;
        const bad = n.startsWith('on') ||
            ((n === 'href' || n === 'xlink:href') && !/^#/.test(v.trim())) ||
            /url\s*\(\s*['"]?\s*(?!#)/i.test(v) || /expression\s*\(|javascript:/i.test(v);
        if (bad) el.removeAttribute(attr.name);
      }
    };
    walk(root);
    if (!root.getAttribute('xmlns')) root.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    const out = new XMLSerializer().serializeToString(root);
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(out)}`;
  } catch {
    return '';
  }
}

export const HeroCardApi = {
  name: 'HeroCard',
  schema: z.object({
    child: ref(ChildSchema, 'Child', 'Content laid over the image.'),
    image: ref(DynamicStringSchema, 'DynamicString', 'Background image URL.'),
    scrim: z.enum(['auto', 'dark', 'light', 'none']).optional(),
    fit: z.enum(['cover', 'contain']).optional(),
    weight: z.number().optional(),
    accessibility: AccessibilityAttributesSchema.optional(),
  }).strict(),
};

export const SvgIconApi = {
  name: 'SvgIcon',
  schema: z.object({
    url: ref(DynamicStringSchema, 'DynamicString', 'Icon image URL.').optional(),
    svg: z.string().optional(),
    size: z.number().min(12).max(96).optional(),
    weight: z.number().optional(),
    accessibility: AccessibilityAttributesSchema.optional(),
  }).strict(),
};

/** Shared Light-DOM base: flex weight + aria attributes like the basic elements. */
class ExtensionElement extends A2uiLitElement {
  createRenderRoot() {
    return this;
  }

  willUpdate(changed) {
    super.willUpdate(changed);
    const props = this.controller?.props;
    if (props && typeof props.weight === 'number') {
      this.style.flex = String(props.weight);
    } else {
      this.style.removeProperty('flex');
    }
    const a11y = props?.accessibility;
    for (const [attr, v] of [['aria-label', a11y?.label], ['aria-description', a11y?.description]]) {
      if (v != null && v !== '') this.setAttribute(attr, String(v));
      else this.removeAttribute(attr);
    }
  }
}

export class A2uidHeroCardElement extends ExtensionElement {
  render() {
    const props = this.controller?.props;
    if (!props) return nothing;
    const url = safeMediaUrl(props.image);
    const scrim = ['auto', 'dark', 'light', 'none'].includes(props.scrim) ? props.scrim : 'auto';
    const fit = props.fit === 'contain' ? 'contain' : 'cover';
    return html`<div class="a2uid-hero a2uid-hero-scrim-${scrim}">
      ${url ? html`<img class="a2uid-hero-img" alt="" decoding="async" draggable="false"
          style="object-fit:${fit}" src=${url}
          @error=${(e) => { e.target.dataset.a2uiMissing = '1'; e.target.style.display = 'none'; }}
          @load=${(e) => { delete e.target.dataset.a2uiMissing; e.target.style.display = ''; }}>` : nothing}
      <div class="a2uid-hero-scrim"></div>
      <div class="a2uid-hero-content">${props.child ? this.renderNode(props.child) : nothing}</div>
    </div>`;
  }
}
A2uidHeroCardElement.tagName = 'a2uid-hero-card';
A2uidHeroCardElement.styles = css`
  :host { display: block; min-width: 0; }
  .a2uid-hero {
    position: relative; overflow: hidden; isolation: isolate;
    margin: var(--a2ui-card-margin, 4px);
    border-radius: var(--a2ui-card-border-radius, 24px);
    background: var(--a2ui-card-background, #f0f4f9);
    box-shadow: var(--a2ui-card-box-shadow, none);
    min-height: 160px; display: flex; flex-direction: column; justify-content: flex-end;
  }
  .a2uid-hero-img { position: absolute; inset: 0; width: 100%; height: 100%; z-index: -2; }
  .a2uid-hero-scrim { position: absolute; inset: 0; z-index: -1; pointer-events: none; }
  .a2uid-hero-scrim-auto .a2uid-hero-scrim,
  .a2uid-hero-scrim-dark .a2uid-hero-scrim {
    background: linear-gradient(to top, rgba(0,0,0,.72) 0%, rgba(0,0,0,.38) 55%, rgba(0,0,0,.12) 100%);
  }
  .a2uid-hero-scrim-light .a2uid-hero-scrim {
    background: linear-gradient(to top, rgba(255,255,255,.88) 0%, rgba(255,255,255,.55) 60%, rgba(255,255,255,.2) 100%);
  }
  .a2uid-hero-scrim-auto, .a2uid-hero-scrim-dark {
    --a2ui-color-on-background: #fff; --a2ui-color-on-surface: #fff;
    --a2ui-text-caption-color: rgba(255,255,255,.82); --a2ui-icon-color: #fff;
    color: #fff;
  }
  .a2uid-hero-scrim-light {
    --a2ui-color-on-background: #1f1f1f; --a2ui-color-on-surface: #1f1f1f;
    --a2ui-text-caption-color: #444746; color: #1f1f1f;
  }
  .a2uid-hero-content { position: relative; padding: var(--a2ui-card-padding, 12px); }
  .a2uid-hero-content a2ui-card { --a2ui-card-background: transparent; --a2ui-card-box-shadow: none; }
`;

export class A2uidSvgIconElement extends ExtensionElement {
  render() {
    const props = this.controller?.props;
    if (!props) return nothing;
    const size = Math.max(12, Math.min(96, Math.round(Number(props.size) || 20)));
    let src = '';
    if (typeof props.svg === 'string' && props.svg.trim()) src = sanitizeSvg(props.svg);
    else src = safeMediaUrl(props.url);
    const labelled = !!props.accessibility?.label;
    return html`<span class="a2uid-svgicon" aria-hidden=${labelled ? 'false' : 'true'}
        style="width:${size}px;height:${size}px">${src ?
        html`<img alt="" decoding="async" draggable="false" src=${src}
            @error=${(e) => { e.target.dataset.a2uiMissing = '1'; }}
            @load=${(e) => { delete e.target.dataset.a2uiMissing; }}>` :
        nothing}</span>`;
  }
}
A2uidSvgIconElement.tagName = 'a2uid-svg-icon';
A2uidSvgIconElement.styles = css`
  :host { display: inline-flex; flex: 0 0 auto; align-self: center; }
  .a2uid-svgicon { display: inline-block; line-height: 0; }
  .a2uid-svgicon img { width: 100%; height: 100%; object-fit: contain; display: block; }
`;

const impl = (element, api) => ({...api, tagName: element.tagName, element});

export const HeroCard = impl(A2uidHeroCardElement, HeroCardApi);
export const SvgIcon = impl(A2uidSvgIconElement, SvgIconApi);

/** The extension Catalog (protocol 1.0). */
export const extensionCatalog = new Catalog(EXTENSION_CATALOG_ID, '1.0', [HeroCard, SvgIcon]);

export const EXTENSION_COMPONENTS = new Set(['HeroCard', 'SvgIcon']);
