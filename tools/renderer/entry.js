// Entry for web/vendor/a2ui.bundle.js. Re-exports the UNMODIFIED published
// @a2ui/web_core (protocol v1.0 surface) and @a2ui/lit packages, plus Lit and
// Zod so app code (custom component wrappers, extension catalog) can use the
// exact same instances the renderer uses.
export * from '@a2ui/web_core/v1_0';
export { A2uiSurface, A2uiLitElement, A2uiController, renderA2uiNode } from '@a2ui/lit';
export { LitElement, html, css, nothing, svg, unsafeCSS } from 'lit';
export { unsafeHTML } from 'lit/directives/unsafe-html.js';
export { z } from 'zod';
