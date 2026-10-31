"""Design package export: catalog + components + tokens + constitution.

`GET /api/export/projects/{pid}/package.zip` → a self-contained folder that a
client/model team can use directly: the A2UI v1.0 catalog (`$id` =
project catalogId), usage rules for prompts, one validated example surface per
custom component, each component's web implementation (template + CSS) and
previews, the theme tokens in three formats, the constitution, and a static
gallery (`index.html`).
"""

from __future__ import annotations

import copy
import hashlib
import html
import io
import json
import os
import re
from typing import Any
import urllib.parse
import zipfile

from core import settings as settings_mod
from core import util
from core.a2ui import catalog as catalog_mod
from core.a2ui import custom_web
from core.a2ui import lowering

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEDS = os.path.join(PKG, "seeds")
A2UI_SEEDS = os.path.join(SEEDS, "a2ui")
RENDERER_DIR = os.path.join(PKG, "web", "renderer")
VENDOR = os.path.join(PKG, "web", "vendor")
VENDOR_SHIM_JS = (
    "// The renderer host modules import ../vendor/a2ui.bundle.js; the bundle\n"
    "// itself ships in renderer/. This shim keeps the host files verbatim.\n"
    "export * from '../renderer/a2ui.bundle.js';\n"
)

COLOR_KEYS = [
    k
    for k, v in settings_mod.DEFAULT_THEME.items()
    if isinstance(v, str) and v.startswith("#")
]
PX_KEYS = (
    "radius_card",
    "radius_control",
    "radius_chip",
    "radius_media",
    "border_width",
    "card_elevation",
    "space",
    "card_padding",
    "surface_padding",
    "card_margin",
    "item_margin",
    "control_height",
)


def slug(text: str, n: int = 48) -> str:
  s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
  return s[:n] or "project"


def _px(v: Any) -> str:
  if isinstance(v, (list, tuple)):
    return settings_mod.format_box_css(v, 0)
  try:
    f = float(v)
  except (TypeError, ValueError):
    s = str(v).strip()
    if re.match(r"^-?\d+(\.\d+)?(\s+-?\d+(\.\d+)?){1,3}$", s):
      return settings_mod.format_box_css(s, 0)
    return s
  return f"{int(f) if f == int(f) else f}px"


_MONO_FONT_RE = re.compile(
    r"\b(mono|monospace|code|courier|consolas|menlo|jetbrains|inconsolata|iosevka|hack)\b",
    re.I,
)
_SERIF_FONT_RE = re.compile(
    r"\b(serif|garamond|georgia|baskerville|playfair|merriweather|lora|fraunces|cormorant|times|bodoni|caslon|newsreader|literata|spectral)\b",
    re.I,
)
_BUILTIN_OR_SYSTEM_FONTS = frozenset({
    "google sans",
    "google sans text",
    "google sans display",
    "google sans flex",
    "google sans code",
    "material symbols rounded",
    "material symbols outlined",
    "material icons",
    "system-ui",
    "sans-serif",
    "serif",
    "monospace",
    "cursive",
    "fantasy",
    "ui-sans-serif",
    "ui-serif",
    "ui-monospace",
    "ui-rounded",
    "-apple-system",
    "blinkmacsystemfont",
    "segoe ui",
    "san francisco",
    "helvetica",
    "helvetica neue",
    "arial",
    "times new roman",
    "times",
    "courier new",
    "courier",
    "georgia",
    "cambria",
    "verdana",
    "tahoma",
    "trebuchet ms",
    "impact",
    "inherit",
    "initial",
    "unset",
})


def _font_stack(family: str, fallback: str | None = None) -> str:
  raw = re.sub(r'["\';{}<>]', "", str(family or "Google Sans")).strip()
  if not fallback:
    if _MONO_FONT_RE.search(raw):
      fallback = '"Google Sans Code", "Roboto Mono", ui-monospace, monospace'
    elif _SERIF_FONT_RE.search(raw) and not re.search(r"\bsans\b", raw, re.I):
      fallback = (
          '"Noto Serif", Georgia, Cambria, "Times New Roman", Times, serif'
      )
    else:
      fallback = '"Google Sans", Roboto, system-ui, -apple-system, sans-serif'
  parts = []
  for p in raw.split(","):
    p = p.strip()
    if not p:
      continue
    if re.match(
        r"^(system-ui|sans-serif|serif|monospace|cursive|fantasy|ui-\w+)$", p
    ):
      parts.append(p)
    else:
      parts.append(f'"{p}"')
  parts.append(fallback)
  return ", ".join(parts)


def _google_fonts_import_url(theme: dict[str, Any]) -> str:
  t = settings_mod.resolve_effective_theme(theme)
  fams: list[str] = []
  seen: set[str] = set()
  for raw in (
      t.get("font_family"),
      t.get("font_family_heading"),
      t.get("font_family_mono"),
  ):
    if not raw:
      continue
    for part in str(raw).split(","):
      clean = re.sub(r'["\';{}<>]', "", part).strip()
      if not clean or clean.startswith("var(") or clean.startswith("--"):
        continue
      key = clean.lower()
      if key in _BUILTIN_OR_SYSTEM_FONTS or key in seen:
        continue
      seen.add(key)
      fams.append(clean)
  if not fams:
    return ""
  specs = [
      f"{fam.replace(' ', '+')}:300,400,500,600,700,800,400italic,700italic"
      for fam in fams
  ]
  return (
      f"https://fonts.googleapis.com/css?family={'|'.join(specs)}&display=swap"
  )


def css_vars(theme: dict[str, Any]) -> dict[str, str]:
  """Python twin of the web renderer's themeToCssVars (core subset)."""
  t = settings_mod.resolve_effective_theme(theme)
  out: dict[str, str] = {}
  for k in COLOR_KEYS:
    out["--a2ui-" + k.replace("_", "-")] = str(t.get(k))
  for k in PX_KEYS:
    out["--a2ui-" + k.replace("_", "-")] = _px(t.get(k))
  fam = str(t.get("font_family") or "Google Sans")
  head = str(t.get("font_family_heading") or "").strip() or fam
  mono = str(t.get("font_family_mono") or "Google Sans Code")
  out["--a2ui-font"] = _font_stack(fam)
  out["--a2ui-font-heading"] = _font_stack(head)
  out["--a2ui-font-mono"] = _font_stack(
      mono, '"Roboto Mono", ui-monospace, monospace'
  )
  for k in (
      "type_scale",
      "heading_scale",
      "heading_weight",
      "body_weight",
      "line_height",
  ):
    out["--a2ui-" + k.replace("_", "-")] = str(t.get(k))
  for raw, ent in (t.get("custom_tokens") or {}).items():
    s = re.sub(r"^--(a2ui-)?", "", str(raw))
    s = re.sub(r"[^a-zA-Z0-9_-]", "-", s)
    val = ent.get("value") if isinstance(ent, dict) else ent
    typ = ent.get("type") if isinstance(ent, dict) else ""
    if s and val not in (None, ""):
      out[f"--a2ui-{s}"] = _px(val) if typ == "dimension" else str(val)
  return out


def tokens_css(theme: dict[str, Any]) -> str:
  lines = [
      "/* A2UI theme tokens — generated by A2UI Designer. */",
  ]
  gf_url = _google_fonts_import_url(theme)
  if gf_url:
    lines.append(f"@import url('{gf_url}');")
    lines.append("")
  lines.append(":root, .a2ui-root {")
  for k, v in css_vars(theme).items():
    lines.append(f"  {k}: {v};")
  lines.append("}")
  if (theme or {}).get("custom_css"):
    lines += ["", "/* Web custom CSS */", str(theme["custom_css"]).strip()]
  return "\n".join(lines) + "\n"


def dtcg_tokens(theme: dict[str, Any]) -> dict[str, Any]:
  """W3C Design Tokens Community Group format."""
  t = settings_mod.resolve_effective_theme(theme)
  out: dict[str, Any] = {
      "color": {k: {"$type": "color", "$value": t.get(k)} for k in COLOR_KEYS},
      "dimension": {
          k: {"$type": "dimension", "$value": _px(t.get(k))} for k in PX_KEYS
      },
      "typography": {
          "font_family": {
              "$type": "fontFamily",
              "$value": t.get("font_family"),
          },
          "font_family_heading": {
              "$type": "fontFamily",
              "$value": t.get("font_family_heading") or t.get("font_family"),
          },
          "font_family_mono": {
              "$type": "fontFamily",
              "$value": t.get("font_family_mono"),
          },
          "heading_weight": {
              "$type": "fontWeight",
              "$value": t.get("heading_weight"),
          },
          "body_weight": {
              "$type": "fontWeight",
              "$value": t.get("body_weight"),
          },
          "type_scale": {"$type": "number", "$value": t.get("type_scale")},
          "heading_scale": {
              "$type": "number",
              "$value": t.get("heading_scale"),
          },
          "line_height": {"$type": "number", "$value": t.get("line_height")},
      },
      "$extensions": {"dcb": {"scheme": t.get("scheme")}},
  }
  custom = {}
  for raw, ent in (t.get("custom_tokens") or {}).items():
    val = ent.get("value") if isinstance(ent, dict) else ent
    typ = (ent.get("type") if isinstance(ent, dict) else "") or "string"
    custom[str(raw)] = {
        "$type": (
            {"color": "color", "dimension": "dimension"}.get(typ, "string")
        ),
        "$value": val,
    }
    if isinstance(ent, dict) and ent.get("maps_to"):
      custom[str(raw)]["$extensions"] = {"dcb": {"maps_to": ent["maps_to"]}}
  if custom:
    out["custom"] = custom
  return out


# ---------------------------------------------------------------- prompt
# App-only generated-media URLs (`/gen/img?prompt=...`) are materialized by
# the A2UI Designer server; a standalone agent has no such endpoint, so the
# exported prompt describes the image instead and tells the model to use real
# URLs.
_GEN_PROMPT_RE = re.compile(
    r'"?/gen/(img|svg)\?prompt=<([^>]*)>[^"`\s]*"?', re.S
)
_GEN_PLAIN_RE = re.compile(r'"?/gen/(img|svg)\?prompt=([^"`\s&]*)[^"`\s]*"?')
_GEN_STYLE_TEXT_RE = re.compile(
    r"\n[ \t]*-[ \t]+If the Design Constitution specifies inline style guidance"
    r".*?`/gen/svg`\.",
    re.S,
)
_GEN_BARE_RE = re.compile(r"`?/gen/(img|svg)`?")

STANDALONE_IMAGES_NOTE = """# Image URLs (standalone deployment)

This prompt was exported from A2UI Designer. There is no built-in image
generator: wherever the instructions mention a generated image or SVG icon
(written as `<URL of a generated … showing: …>`), use a fitting image URL that
is present in the input data. If none fits, omit the image (or use an `Icon`)
rather than inventing a URL. Never emit the `<URL of …>` text itself."""


def _gen_label(kind: str, what: str) -> str:
  what = re.sub(r"\s+", " ", urllib.parse.unquote_plus(what or "")).strip()
  noun = "SVG icon" if kind == "svg" else "image"
  return f"<URL of a generated {noun} showing: {what or 'the subject'}>"


def standalone_text(text: str) -> str:
  """Rewrites app-only image URL conventions out of prompt/constitution text."""
  if not text:
    return text or ""
  text = text.replace(
      "(real photo URLs or local `/img/...` placeholders)", "(real photo URLs)"
  )
  text = _GEN_STYLE_TEXT_RE.sub("", text)
  text = _GEN_PROMPT_RE.sub(lambda m: _gen_label(m.group(1), m.group(2)), text)
  text = _GEN_PLAIN_RE.sub(lambda m: _gen_label(m.group(1), m.group(2)), text)
  text = _GEN_BARE_RE.sub("a generated-image URL", text)
  text = re.sub(r"`?/img/\.\.\.`?", "an image URL", text)
  return text


def system_prompt_text(store, svc, project, cat, rev) -> str:
  """Exactly the generator system prompt the app uses, minus app-only URLs."""
  from core import imagery as imagery_mod  # pylint: disable=g-import-not-at-top
  from sim import generator  # pylint: disable=g-import-not-at-top

  pid = project["id"]
  s = settings_mod.merged(project["settings"])
  gen_cfg = s.get("generator") or {}
  gls = []
  if gen_cfg.get("include_guidelines"):
    gls = store.query(
        "SELECT kind, title, body_md FROM guidelines WHERE project_id=? "
        "AND status IN ('open','addressed')",
        [pid],
    )
  templates = store.query(
      "SELECT * FROM templates WHERE project_id=? AND status='active'"
      " ORDER BY name",
      [pid],
  )
  viewports = [v for v in s.get("viewports") or [] if v in generator.VIEWPORTS]
  ctx = generator.GenerationContext(
      constitution_md=(rev or {}).get("text_md") or "",
      catalog=cat,
      viewport=(viewports or ["phone"])[0],
      framing=gen_cfg.get("framing", "card"),
      guidelines=gls,
      imagery_styles=imagery_mod.list_styles(store, pid, enabled_only=True),
      templates=templates,
  )
  text = standalone_text(generator.system_prompt(ctx))
  return text.rstrip() + "\n\n" + STANDALONE_IMAGES_NOTE + "\n"


# ---------------------------------------------------------------- assets
_APP_HOST = r"(?:https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?)?"
_API_GEN_RE = re.compile(
    _APP_HOST + r"/api/gen/([A-Za-z0-9_\-]+\.(?:png|svg|jpe?g|webp|gif))"
)
_FILES_RE = re.compile(
    _APP_HOST
    + r"/files/((?:screens|thumbs|components|imagery|gen|templates|"
    r"references|guidelines|teach)/[A-Za-z0-9_\-./]+\.(?:png|svg|jpe?g|webp|gif))"
)
_GEN_URL_RE = re.compile(
    r"(?:https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?|"
    r"(?<![A-Za-z0-9_.~%\-]))/gen/(img|svg)(?:\?[^\s\"'`<>\\)]*)?"
)
_PLACEHOLDER_RE = re.compile(
    r"(?:https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?|"
    r"(?<![A-Za-z0-9_.~%\-]))/img/([A-Za-z0-9][A-Za-z0-9_\-]{0,120})/"
    r"(\d{2,4})x(\d{2,4})(?:\.(?:svg|png|jpe?g))?"
)
_TEXT_EXT = (".json", ".md", ".txt", ".html", ".css", ".js", ".svg")


def placeholder_svg(label: str, w: int, h: int) -> str:
  """A neutral local stand-in for an unresolved `/img/<slug>/WxH` photo."""
  words = re.sub(r"[-_]+", " ", label or "image").strip() or "image"
  esc = html.escape(words[:60])
  fs = max(10, min(28, int(min(w, h) / 8)))
  return (
      f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"'
      f' viewBox="0 0 {w} {h}" role="img" aria-label="{esc}">'
      f'<rect width="{w}" height="{h}" fill="#E7E2DA"/>'
      f'<path d="M0 {h} L{w * 0.35:.0f} {h * 0.55:.0f} L{w * 0.55:.0f}'
      f' {h * 0.75:.0f} L{w * 0.75:.0f} {h * 0.45:.0f} L{w} {h} Z"'
      ' fill="#D4CCC0"/>'
      f'<circle cx="{w * 0.78:.0f}" cy="{h * 0.25:.0f}" r="{min(w, h) * 0.08:.0f}"'
      ' fill="#D4CCC0"/>'
      f'<text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle"'
      f' font-family="system-ui, sans-serif" font-size="{fs}" fill="#5F574C">'
      f"{esc}</text>"
      f'<text x="50%" y="{h / 2 + fs * 1.3:.0f}" text-anchor="middle"'
      f' dominant-baseline="middle" font-family="system-ui, sans-serif"'
      f' font-size="{max(9, int(fs * 0.6))}" fill="#857B6E">{w}×{h}</text>'
      "</svg>\n"
  )


class _Assets:
  """Copies app-served media into `assets/` and rewrites URLs to it."""

  def __init__(self, store, files: dict[str, bytes]):
    self.store = store
    self.files = files
    self.resolved: dict[str, str] = {}
    self.missing: list[str] = []
    self._resolver = None

  def _put(self, rel: str, data: bytes | str) -> str:
    if isinstance(data, str):
      data = data.encode("utf-8")
    self.files[rel] = data
    return rel

  def _copy(self, rel: str, src: str) -> bool:
    if src and os.path.isfile(src):
      with open(src, "rb") as fh:
        self._put(rel, fh.read())
      return True
    return False

  def api_gen(self, m: re.Match) -> str:
    fname = m.group(1)
    key = "gen:" + fname
    if key not in self.resolved:
      rel = f"assets/gen/{fname}"
      if not self._copy(rel, self.store.file_path("gen", fname)):
        self.missing.append(m.group(0))
        rel = f"assets/gen/missing-{os.path.splitext(fname)[0][:16]}.svg"
        self._put(rel, placeholder_svg("generated image", 640, 360))
      self.resolved[key] = rel
    return self.resolved[key]

  def files_url(self, m: re.Match) -> str:
    sub = os.path.normpath(m.group(1))
    key = "files:" + sub
    if key not in self.resolved:
      rel = "assets/files/" + sub.replace(os.sep, "/")
      if sub.startswith("..") or not self._copy(
          rel, self.store.file_path(sub)
      ):
        self.missing.append(m.group(0))
        rel = f"assets/files/missing-{_short_hash(sub)}.svg"
        self._put(rel, placeholder_svg("image", 640, 360))
      self.resolved[key] = rel
    return self.resolved[key]

  def gen_url(self, m: re.Match) -> str:
    from sim import imagery_gen  # pylint: disable=g-import-not-at-top

    url = m.group(0)
    key = "genurl:" + url
    if key not in self.resolved:
      kind = m.group(1)
      qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
      prompt = (qs.get("prompt") or qs.get("q") or ["image"])[0]
      style = (qs.get("style_text") or [""])[0]
      if kind == "svg":
        svg = imagery_gen.fallback_icon_svg(prompt, style)
      else:
        svg = imagery_gen.fallback_raster_svg(prompt, style)
      rel = self._put(f"assets/generated/{kind}-{_short_hash(url)}.svg", svg)
      self.resolved[key] = rel
    return self.resolved[key]

  def placeholder(self, m: re.Match) -> str:
    raw = m.group(0)
    key = "ph:" + raw
    if key not in self.resolved:
      slug_, w, h = m.group(1), int(m.group(2)), int(m.group(3))
      url = self._cached_photo(f"/img/{slug_}/{w}x{h}")
      if not url:
        url = self._put(
            f"assets/placeholders/{slug_[:60]}-{w}x{h}.svg",
            placeholder_svg(slug_, w, h),
        )
      self.resolved[key] = url
    return self.resolved[key]

  def _cached_photo(self, ph: str) -> str:
    """A web photo already cached for this placeholder (never hits the net)."""
    try:
      from sim import images  # pylint: disable=g-import-not-at-top

      def offline(q, w):  # pylint: disable=unused-argument
        raise OSError("offline export")

      if self._resolver is None:
        self._resolver = images.Resolver(self.store, searcher=offline)
      out, _ = self._resolver.resolve(ph)
    except Exception:  # pylint: disable=broad-except
      return ""
    if isinstance(out, str) and out != ph and out.startswith("https://"):
      return out
    return ""

  def rewrite(self, text: str) -> str:
    text = _API_GEN_RE.sub(self.api_gen, text)
    text = _FILES_RE.sub(self.files_url, text)
    text = _GEN_URL_RE.sub(self.gen_url, text)
    text = _PLACEHOLDER_RE.sub(self.placeholder, text)
    return text


def _short_hash(s: str) -> str:
  return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- renderer
RENDERER_FILES = (
    "host.js",
    "tokens.js",
    "custom_element.js",
    "custom.js",
    "composition.js",
    "extensions.js",
    "markdown.js",
    "host.css",
)
VENDOR_FILES = ("a2ui.bundle.js", "A2UI_VERSIONS.json")

INDEX_JS = r"""// A2UI design package runtime.
//
//   import {loadPackage, mountSurface} from '.../renderer/index.js';
//   const pkg = await loadPackage('./');           // the package root URL
//   const s = pkg.mount(el, messages);             // or mountSurface(el, messages, {...pkg.options})
//   await s.settled(); console.log(s.meta);
//
// `mountSurface` is the A2UI Designer host over the unmodified official
// @a2ui/web_core + @a2ui/lit renderer (vendored in ./a2ui.bundle.js).
// `loadPackage` reads the package's components, theme tokens and examples:
// from a <script type="application/json" id="a2ui-package-data"> block when
// the page embeds one (demo.html does, so it also works from file://), else
// over fetch() from `baseUrl`.

import {mountSurface} from './host.js';

export * from './host.js';

export const PACKAGE_DATA_ELEMENT_ID = 'a2ui-package-data';

function readEmbedded(doc) {
  const el = doc && doc.getElementById(PACKAGE_DATA_ELEMENT_ID);
  if (!el) return null;
  try {
    return JSON.parse(el.textContent);
  } catch (e) {
    console.warn('[a2ui-package] bad embedded package data', e);
    return null;
  }
}

/** Rewrites package-relative `assets/...` URLs against `base`. */
function absolutizeAssets(value, base) {
  if (typeof value === 'string') {
    return value.startsWith('assets/') ? new URL(value, base).href : value;
  }
  if (Array.isArray(value)) return value.map((v) => absolutizeAssets(v, base));
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = absolutizeAssets(v, base);
    return out;
  }
  return value;
}

async function fetchPackage(base) {
  const get = async (rel) => {
    const res = await fetch(new URL(rel, base));
    if (!res.ok) throw new Error(`${rel}: HTTP ${res.status}`);
    return res.json();
  };
  const manifest = await get('manifest.json');
  const [components, theme, examples] = await Promise.all([
    get('catalog/components.json'),
    get('tokens/theme.json'),
    Promise.all((manifest.examples || []).map((n) => get(`catalog/examples/${n}.json`))),
  ]);
  return {manifest, components, theme, examples};
}

/**
 * Loads the design package at `baseUrl` (default: the current page's folder).
 * Returns {manifest, customComponents, theme, catalogs, examples, options,
 *          mount(el, messages, opts)}.
 */
export async function loadPackage(baseUrl, {embedded = true} = {}) {
  const doc = typeof document !== 'undefined' ? document : null;
  const here = doc ? doc.baseURI : (typeof location !== 'undefined' ? location.href : '');
  let raw = embedded && baseUrl == null ? readEmbedded(doc) : null;
  let base = new URL('./', here);
  if (!raw) {
    let b = baseUrl == null ? './' : String(baseUrl);
    if (!b.endsWith('/')) b += '/';
    base = new URL(b, here);
    raw = await fetchPackage(base);
  }
  let {manifest = {}, components = {}, theme = {}, examples = []} = raw;
  if (base.href !== new URL('./', here).href) {
    components = absolutizeAssets(components, base);
    examples = absolutizeAssets(examples, base);
  }
  const catalogs = {
    projectCatalogId: manifest.catalogId,
    extensions: Boolean(manifest.extensions),
  };
  const options = {theme, customComponents: components, catalogs};
  return {
    manifest,
    customComponents: components,
    theme,
    catalogs,
    examples,
    options,
    mount(el, messages, opts = {}) {
      return mountSurface(el, messages, {...options, ...opts});
    },
  };
}
"""

# Classic-script loader for file:// pages. Browsers refuse to load ES modules
# from file:// URLs, so demo.html falls back to this: it carries the module
# sources and imports them through blob: URLs with the relative specifiers
# rewritten.
OFFLINE_LOADER_JS = r"""
(function () {
  var SOURCES = window.__A2UI_PACKAGE_SOURCES__ || {};
  var cache = {};
  function resolve(from, spec) {
    var parts = from.split('/');
    parts.pop();
    spec.split('/').forEach(function (p) {
      if (p === '..') parts.pop();
      else if (p !== '.' && p) parts.push(p);
    });
    return parts.join('/');
  }
  function blobUrl(path) {
    if (cache[path]) return cache[path];
    var src = SOURCES[path];
    if (src == null) throw new Error('a2ui package: missing module ' + path);
    var real = new URL(path, document.baseURI).href;
    src = src.split('import.meta.url').join(JSON.stringify(real));
    src = src.replace(/(\bfrom\s*|\bimport\s*\(?\s*)(['"])(\.\.?\/[^'"]+)\2/g,
        function (m, kw, q, spec) {
          var p = resolve(path, spec);
          if (p === path) return m;
          return kw + q + blobUrl(p) + q;
        });
    cache[path] = URL.createObjectURL(new Blob([src], {type: 'text/javascript'}));
    return cache[path];
  }
  window.A2UIPackageOffline = {
    import: function (path) { return import(blobUrl(path || 'renderer/index.js')); },
  };
})();
"""


def _offline_modules_js(sources: dict[str, str]) -> str:
  payload = json.dumps(sources, ensure_ascii=False).replace("</", "<\\/")
  return (
      "// Generated: the renderer's ES module sources for pages opened from\n"
      "// file:// (see demo.html). Not needed when served over http(s).\n"
      f"window.__A2UI_PACKAGE_SOURCES__ = {payload};\n"
      + OFFLINE_LOADER_JS.lstrip()
  )


def _json_script(data: Any) -> str:
  return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _demo_html(project, data: dict[str, Any], viewport_w: int) -> str:
  esc = html.escape
  name = esc(project["name"])
  return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} — live A2UI demo</title>
<link rel="stylesheet" href="renderer/host.css">
<style>
body{{margin:0;font:14px/1.5 system-ui,sans-serif;background:#f4f0e8;color:#1b1b1b}}
main{{max-width:1240px;margin:0 auto;padding:28px 32px 48px}}
header h1{{font-size:26px;margin:0 0 4px}} header p{{margin:0;color:#5c554c}}
code{{font:12px ui-monospace,monospace}} a{{color:#6b3f1f}}
.grid{{display:flex;flex-wrap:wrap;gap:24px;margin-top:24px;align-items:flex-start}}
.ex{{background:#fffdf9;border:1px solid #e3dccf;border-radius:14px;padding:14px;width:{viewport_w + 28}px;box-sizing:border-box}}
.ex h2{{font-size:15px;margin:0 0 2px}} .ex p{{margin:0 0 10px;color:#6f675c;font-size:12px}}
.ex .links{{font-size:12px;margin-top:8px;display:flex;gap:12px}}
.stage{{width:{viewport_w}px;min-height:40px}}
.status{{font-size:12px;margin-top:8px;color:#6f675c}} .status.err{{color:#a3261b}}
</style></head>
<body><main>
<header><h1>{name}</h1>
<p>Live A2UI v1.0 surfaces rendered by <code>renderer/index.js</code> with this
package's components and tokens. <a href="index.html">Gallery</a> ·
<a href="README.md">README</a></p></header>
<div class="grid" id="demo-grid"></div>
<p class="status" id="demo-status">Loading the renderer…</p>
</main>
<script type="application/json" id="a2ui-package-data">{_json_script(data)}</script>
<script>
(async function () {{
  var statusEl = document.getElementById('demo-status');
  var grid = document.getElementById('demo-grid');
  window.__demo = {{done: false, results: [], mode: ''}};
  function loadScript(src) {{
    return new Promise(function (ok, fail) {{
      var s = document.createElement('script');
      s.src = src; s.onload = ok; s.onerror = function () {{ fail(new Error('failed to load ' + src)); }};
      document.head.appendChild(s);
    }});
  }}
  var mod;
  try {{
    if (location.protocol === 'file:') throw new Error('file://');
    mod = await import('./renderer/index.js');
    window.__demo.mode = 'module';
  }} catch (e) {{
    try {{
      await loadScript('renderer/offline_modules.js');
      mod = await window.A2UIPackageOffline.import('renderer/index.js');
      window.__demo.mode = 'offline';
    }} catch (e2) {{
      statusEl.textContent = 'Could not load the renderer: ' + (e2.message || e2);
      statusEl.className = 'status err';
      window.__demo.error = String(e2.message || e2);
      window.__demo.done = true;
      return;
    }}
  }}
  var pkg = await mod.loadPackage();
  statusEl.textContent = '';
  for (var i = 0; i < pkg.examples.length; i++) {{
    var ex = pkg.examples[i];
    var card = document.createElement('section');
    card.className = 'ex';
    card.dataset.example = ex.name;
    var h = document.createElement('h2'); h.textContent = ex.name;
    var p = document.createElement('p'); p.textContent = ex.description || '';
    var stage = document.createElement('div'); stage.className = 'stage';
    var links = document.createElement('div'); links.className = 'links';
    [['template.html', 'template'], ['styles.css', 'styles'], ['definition.json', 'definition']].forEach(function (l) {{
      if (!pkg.customComponents[ex.name]) return;
      var a = document.createElement('a');
      a.href = 'catalog/components/' + encodeURIComponent(ex.name) + '/' + l[0];
      a.textContent = l[1];
      links.appendChild(a);
    }});
    var st = document.createElement('div'); st.className = 'status';
    card.append(h, p, stage, links, st);
    grid.appendChild(card);
    var meta;
    try {{
      var s = pkg.mount(stage, ex.messages, {{framing: 'card', viewport: {{width: {viewport_w}}}}});
      await s.settled();
      meta = s.meta;
    }} catch (e) {{
      meta = {{errors: [String(e.message || e)], custom: []}};
    }}
    window.__demo.results.push({{name: ex.name, meta: meta}});
    if (meta.errors && meta.errors.length) {{
      st.textContent = meta.errors.join('; ');
      st.className = 'status err';
    }}
  }}
  if (!pkg.examples.length) statusEl.textContent = 'This package has no custom components yet.';
  window.__demo.done = true;
}})();
</script>
</body></html>
"""


# ---------------------------------------------------------------- docs
def _versions() -> dict[str, Any]:
  try:
    with open(os.path.join(VENDOR, "A2UI_VERSIONS.json"), encoding="utf-8") as fh:
      return json.load(fh)
  except (OSError, ValueError):
    return {}


def _readme(project, cat, comps, rev, has_ext, has_templates, has_imagery) -> str:
  names = ", ".join(f"`{r['name']}`" for r in comps) or "(none yet)"
  v = _versions()
  ver_rows = "\n".join(
      f"| `{k}` | {val} |" for k, val in v.items() if k != "note"
  )
  ext_line = (
      f"\n| `catalog/extensions_catalog.json` | The A2UI Designer extension"
      f" catalog (`HeroCard`, `SvgIcon`), id `{catalog_mod.EXTENSION_CATALOG_ID}`."
      " This project uses it. |"
      if has_ext
      else ""
  )
  tpl_line = (
      "\n| `templates/` | Reusable screen/section templates (`template.json`"
      " + `reference.png`), already summarized in the system prompt. |"
      if has_templates
      else ""
  )
  img_line = (
      "\n| `imagery/` | Generated-imagery style presets and their reference"
      " images. |"
      if has_imagery
      else ""
  )
  catalogs_adv = f"`{cat.basic_catalog_id}`, `{cat.catalog_id}`" + (
      f", `{catalog_mod.EXTENSION_CATALOG_ID}`" if has_ext else ""
  )
  return f"""# {project['name']} — A2UI design package

A self-contained design system for [A2UI](https://a2ui.org) v1.0 agents and
renderers, exported from A2UI Designer (constitution revision
{rev.get('number', '?')}). Nothing here depends on the A2UI Designer server.

Custom components: {names}.

## Quick start

### 1. Agent (generator)

Use [`prompt/system_prompt.md`](prompt/system_prompt.md) as the system prompt
of the model that emits A2UI messages — it is exactly what A2UI Designer sends
to its generator (role, design constitution, catalog, templates, viewport,
imagery rules). The user turn is the data to present; the model answers with
a JSON object of A2UI v1.0 messages.

- Surfaces use the Basic catalog as `createSurface.catalogId`
  (`{cat.basic_catalog_id}`).
- Every design-system component instance carries
  `"catalogId": "{cat.catalog_id}"` (A2UI v1.0 mixed catalogs).
  Set a real, stable catalog id for your project in A2UI Designer before
  shipping (Package page → Catalog ID).
- Clients advertise: {catalogs_adv}.
- To validate output, use `catalog/catalog.json` (self-contained: Basic +
  custom components) with any JSON Schema validator / the A2UI SDKs.

### 2. Web renderer

Serve this folder from any static host (or copy `renderer/` + the JSON into
your app) and mount surfaces with the bundled renderer:

```html
<div id="surface"></div>
<script type="module">
  import {{loadPackage}} from './renderer/index.js';
  const pkg = await loadPackage('./');             // URL of this package folder
  const messages = [/* A2UI v1.0 messages from your agent */];
  const surface = pkg.mount(document.getElementById('surface'), messages, {{
    onAction: (payload) => console.log('user action', payload),
  }});
  await surface.settled();
  // Stream more messages later: surface.update(moreMessages)
</script>
```

`pkg.mount(el, messages, opts)` = `mountSurface(el, messages, {{theme,
customComponents, catalogs, ...opts}})`; `mountSurface` is also exported for
full control (`opts`: `theme`, `customComponents`, `catalogs:
{{projectCatalogId, extensions}}`, `framing: 'card'|'page'`, `interactive`,
`onAction`). Open [`demo.html`](demo.html) to see every example rendered live
(works from a static host and from `file://`).

### 3. Files

| Path | What |
|---|---|
| `prompt/system_prompt.md` | Generator system prompt (see above). |
| `prompt/catalog_prompt.txt` | Compact catalog description used inside the prompt. |
| `constitution.md` | The design constitution (layout/decision rules). |
| `catalog/catalog.json` | A2UI v1.0 catalog, `$id` = `catalogId` = `{cat.catalog_id}`: enabled Basic components + design-system components, `instructions` embedded. |
| `catalog/project_catalog.json` | Only the design-system components (what per-component `catalogId` references). |{ext_line}
| `catalog/components.json` | `{{Name: Definition}}` — the `customComponents` option of `mountSurface` (props schema, `web.template`/`web.css`, `composition`, `sample`). |
| `catalog/components/<Name>/` | `definition.json`, `template.html` + `styles.css` (web implementation), `composition.json` (Basic-component lowering, when present), `preview.png` / `reference.png`. |
| `catalog/examples/<Name>.json` | One validated example surface per component (`{{name, description, valid, messages}}`). |{tpl_line}{img_line}
| `tokens/theme.json` | Resolved theme tokens (the `theme` option of `mountSurface`). |
| `tokens/tokens.css` | The same tokens as `--a2ui-*` CSS custom properties. |
| `tokens/design-tokens.json` | The same tokens in W3C DTCG format. |
| `assets/` | Images referenced by the examples/definitions (generated media, placeholders). |
| `renderer/` | `index.js` (`loadPackage`, `mountSurface`), the host modules, and the official renderer bundle `a2ui.bundle.js`. `offline_modules.js` only serves `file://` pages. |
| `manifest.json` | Package metadata (`catalogId`, `extensions`, example names, versions). |
| `demo.html` | Live demo of every example with this package's components + tokens. |
| `index.html` | Static gallery (previews, props, tokens). |
| `LICENSE-A2UI.txt` | License of the A2UI specification/renderer (Apache 2.0). |

### Custom component templates

| Syntax | Meaning |
|---|---|
| `{{{{prop}}}}` in text | resolved value as text |
| `attr="{{{{prop}}}}"` | attribute interpolation (`src alt title class aria-* data-*`) |
| `style="--name: {{{{prop}}}}"` | custom-property assignment |
| `data-if="prop"`, `data-if="prop:value"`, `data-unless` | conditional |
| `data-each="prop as it"` + `{{{{it.field}}}}` | repeat per array item |
| `<slot name="prop">` | children of a ComponentId / ChildList prop |
| `data-action="prop"` | element triggers the Action prop |
| `data-icon="{{{{prop}}}}"` | Material Symbols Rounded icon |

`styles.css` is scoped to the component (shadow root) and uses the theme
tokens. Other A2UI renderers can implement each component from
`definition.json` + the previews.

### 4. Versions

A2UI protocol **v1.0**. Bundled renderer:

| Package | Version |
|---|---|
{ver_rows}

The A2UI specification, Basic catalog and renderer packages are © the A2UI
authors, Apache License 2.0 (`LICENSE-A2UI.txt`).
"""


def _gallery(project, catalog_id, comps, theme) -> str:
  esc = html.escape
  vars_css = "".join(f"{k}:{v};" for k, v in css_vars(theme).items())
  cards = []
  for r in comps:
    d = r.get("definition") or {}
    n = esc(r["name"])
    props = (d.get("props") or {}).get("properties") or {}
    req = set((d.get("props") or {}).get("required") or [])
    rows = "".join(
        f"<tr><td><code>{esc(k)}</code>{' *' if k in req else ''}</td>"
        f"<td>{esc(catalog_mod._type_label(v))}</td>"  # pylint: disable=protected-access
        f"<td>{esc(str(v.get('description') or ''))}</td></tr>"
        for k, v in props.items()
    )
    imgs = ""
    if r.get("preview_file"):
      imgs += (
          f'<figure><img src="catalog/components/{n}/preview.png"'
          ' alt=""><figcaption>Web implementation</figcaption></figure>'
      )
    if r.get("reference_file"):
      imgs += (
          f'<figure><img src="catalog/components/{n}/'
          'reference.png" alt=""><figcaption>Design reference</figcaption>'
          "</figure>"
      )
    links = (
        f'<p class="links"><a href="catalog/components/{n}/template.html">'
        f'template.html</a> · <a href="catalog/components/{n}/styles.css">'
        f'styles.css</a> · <a href="catalog/components/{n}/definition.json">'
        f'definition.json</a> · <a href="catalog/examples/{n}.json">example'
        "</a></p>"
    )
    cards.append(
        f'<section class="comp" id="c-{n}"><h2>{n}</h2>'
        f"<p>{esc(d.get('description') or '')}</p>"
        f'<p class="usage">{esc(d.get("usage") or "")}</p>{links}'
        f'<div class="imgs">{imgs}</div>'
        f"<table><tr><th>Prop</th><th>Type</th><th>Description</th></tr>{rows}"
        "</table></section>"
    )
  t = settings_mod.resolve_effective_theme(theme)
  swatches = "".join(
      f'<div class="sw"><span style="background:{esc(str(t.get(k)))}"></span>'
      f"<code>{esc(k)}</code><small>{esc(str(t.get(k)))}</small></div>"
      for k in COLOR_KEYS
  )
  return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{esc(project['name'])} — design package</title>
<style>
:root{{{vars_css}}}
body{{margin:0;font:14px/1.5 system-ui,sans-serif;background:#f6f3ee;color:#1b1b1b}}
main{{max-width:1100px;margin:0 auto;padding:32px}}
h1{{font-size:28px;margin:0 0 4px}} code{{font:12px ui-monospace,monospace}}
.comp{{background:#fff;border-radius:16px;padding:20px 24px;margin:20px 0;box-shadow:0 1px 3px #0001}}
.comp h2{{margin:0 0 4px}} .usage{{color:#555}} .links{{font-size:13px}}
.imgs{{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}}
figure{{margin:0}} figure img{{max-width:420px;max-height:420px;border:1px solid #ddd;border-radius:8px;background:#fff}}
figcaption{{font-size:12px;color:#777}}
table{{border-collapse:collapse;margin-top:12px;width:100%}} td,th{{text-align:left;padding:4px 8px;border-bottom:1px solid #eee;vertical-align:top}}
.sws{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px}}
.sw{{display:flex;align-items:center;gap:8px}} .sw span{{width:28px;height:28px;border-radius:6px;border:1px solid #0002}}
.sw small{{color:#777}}
.nav a{{margin-right:14px}}
</style></head><body><main>
<h1>{esc(project['name'])}</h1>
<p>catalogId <code>{esc(catalog_id)}</code> · {len(comps)} custom component(s)</p>
<p class="nav"><a href="demo.html"><b>Live demo</b></a><a href="README.md">README</a><a href="prompt/system_prompt.md">System prompt</a><a href="catalog/catalog.json">catalog.json</a><a href="tokens/tokens.css">tokens.css</a></p>
{''.join(cards) or '<p>No custom components.</p>'}
<section class="comp"><h2>Color tokens</h2><div class="sws">{swatches}</div></section>
</main></body></html>
"""


# ---------------------------------------------------------------- package
def package_zip(
    store, svc, project: dict[str, Any], include_sources: bool = False
) -> tuple[str, bytes]:
  """Returns (file name, zip bytes)."""
  root, files = package_files(
      store, svc, project, include_sources=include_sources
  )
  buf = io.BytesIO()
  with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for path, data in files.items():
      z.writestr(root + path, data)
  return root.rstrip("/") + ".zip", buf.getvalue()


def file_list(store, svc, project: dict[str, Any]) -> list[str]:
  """The package's relative file paths (derived from package_files)."""
  return sorted(package_files(store, svc, project)[1])


_SOURCE_KEYS = (
    "kind",
    "url",
    "node_id",
    "figma_name",
    "variant_props",
    "screen_title",
)


def _definition_for_export(
    r: dict[str, Any], include_sources: bool
) -> dict[str, Any]:
  d = copy.deepcopy(r.get("definition") or {})
  d["name"] = r["name"]
  d.pop("native", None)
  src = d.get("source")
  if isinstance(src, dict) and not include_sources:
    d["source"] = {k: v for k, v in src.items() if k != "url"}
  if not lowering.has_composition(d):
    d.pop("composition", None)
  return d


def package_files(
    store, svc, project: dict[str, Any], include_sources: bool = False
) -> tuple[str, dict[str, bytes]]:
  """Returns (root dir with trailing slash, {relative path: bytes}).

  The single source of the package contents: package_zip zips exactly these
  files, the Package page lists/serves them one by one and package_check
  reports them. App-served media (`/api/gen/*`, `/files/*`, `/gen/img|svg`,
  `/img/<slug>/WxH`) is copied/rendered into `assets/` and every reference is
  rewritten to the package-relative path, so the folder works standalone.
  `include_sources` keeps design-source URLs (Figma/Stitch) in definitions.
  """
  from core.a2ui import prompt_schema  # pylint: disable=g-import-not-at-top
  from core.a2ui import validator  # pylint: disable=g-import-not-at-top
  from core.components import sample_messages  # pylint: disable=g-import-not-at-top
  from exports import bundles  # pylint: disable=g-import-not-at-top

  pid = project["id"]
  cat = svc.catalog_for(project)
  s = settings_mod.merged(project["settings"])
  theme = s["theme"]
  rev = svc.current_revision(pid) or {}
  comps = store.query(
      "SELECT * FROM custom_components WHERE project_id=? AND status='active'"
      " ORDER BY name",
      [pid],
  )
  root = f"{slug(project['name'])}-design-package/"
  files: dict[str, bytes] = {}

  def put(path: str, data: Any) -> None:
    if isinstance(data, (dict, list)):
      data = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if isinstance(data, str):
      data = data.encode("utf-8")
    files[path] = data

  def put_file(path: str, src: str | None) -> bool:
    if src and os.path.isfile(src):
      with open(src, "rb") as fh:
        put(path, fh.read())
      return True
    return False

  title = f"{project['name']} — A2UI catalog"
  desc = project.get("description") or ""
  has_ext = cat.extension_enabled
  put(
      "constitution.md",
      standalone_text(
          bundles.constitution_md(store, project, rev, header=False)
          if rev
          else ""
      ),
  )
  put("prompt/system_prompt.md", system_prompt_text(store, svc, project, cat, rev))
  put("prompt/catalog_prompt.txt", prompt_schema.catalog_prompt(cat) + "\n")
  put(
      "catalog/catalog.json",
      catalog_mod.to_a2ui_catalog_json(
          cat, cat.catalog_id, title=title, description=desc
      ),
  )
  put(
      "catalog/project_catalog.json",
      catalog_mod.project_catalog_json(
          cat, title=f"{project['name']} — design-system components"
      ),
  )
  if has_ext:
    put("catalog/extensions_catalog.json", catalog_mod.extension_catalog_json())

  components_json: dict[str, Any] = {}
  example_names: list[str] = []
  examples: list[dict[str, Any]] = []
  for r in comps:
    d = r.get("definition") or {}
    name = r["name"]
    export_def = _definition_for_export(r, include_sources)
    components_json[name] = export_def
    msgs = sample_messages(d, cat.catalog_id, surface_id=slug(name, 40).replace("-", "_"))
    res = validator.validate(msgs, cat)
    ex = {
        "name": name,
        "description": d.get("description") or "",
        "valid": res.ok,
        "messages": msgs,
    }
    examples.append(ex)
    example_names.append(name)
    put(f"catalog/examples/{name}.json", ex)
    chk = custom_web.check_definition(d, catalog_mod.reserved_names())
    source = {
        k: v
        for k, v in (r.get("source") or {}).items()
        if k in _SOURCE_KEYS and (include_sources or k != "url")
    }
    put(
        f"catalog/components/{name}/definition.json",
        {
            **export_def,
            "token_refs": chk["token_refs"],
            "source": source,
            "fidelity": r.get("fidelity"),
        },
    )
    put(f"catalog/components/{name}/template.html", chk["template"] + "\n")
    put(f"catalog/components/{name}/styles.css", chk["css"] + "\n")
    if lowering.has_composition(d):
      put(f"catalog/components/{name}/composition.json", d["composition"])
    if r.get("preview_file"):
      put_file(
          f"catalog/components/{name}/preview.png",
          store.file_path("components", r["preview_file"]),
      )
    if r.get("reference_file"):
      put_file(
          f"catalog/components/{name}/reference.png",
          store.file_path("components", r["reference_file"]),
      )
  put("catalog/components.json", components_json)

  templates = store.query(
      "SELECT * FROM templates WHERE project_id=? AND status='active'"
      " ORDER BY name",
      [pid],
  )
  tpl_index = []
  for t in templates:
    ts = slug(t["name"]) or t["id"]
    if any(e["path"] == f"templates/{ts}/" for e in tpl_index):
      ts = f"{ts}-{t['id'][-4:]}"
    put(
        f"templates/{ts}/template.json",
        {
            "name": t["name"],
            "description": t.get("description") or "",
            "when_to_use": t.get("when_to_use") or "",
            "structure_md": t.get("structure_md") or "",
            "components": t.get("components") or [],
        },
    )
    if t.get("image_file"):
      put_file(
          f"templates/{ts}/reference.png",
          store.file_path("templates", t["image_file"]),
      )
    tpl_index.append({"name": t["name"], "path": f"templates/{ts}/"})
  if tpl_index:
    put("templates/index.json", tpl_index)

  resolved_theme = settings_mod.resolve_effective_theme(theme)
  put("tokens/theme.json", resolved_theme)
  put("tokens/tokens.css", tokens_css(theme))
  put("tokens/design-tokens.json", dtcg_tokens(theme))

  imagery_styles = store.query(
      "SELECT * FROM imagery_styles WHERE project_id=? AND enabled=1"
      " ORDER BY slug, created_at",
      [pid],
  )
  if imagery_styles:
    export_styles = []
    for st in imagery_styles:
      refs = []
      for idx, ref in enumerate(st.get("reference_images") or []):
        fname = os.path.basename(str(ref.get("file") or ""))
        ext = os.path.splitext(fname)[1] or ".png"
        rel_ref = f"imagery/refs/{st['slug']}_{idx}{ext}"
        ref_fp = store.file_path("imagery", st["id"], fname)
        if not os.path.isfile(ref_fp):
          ref_fp = store.file_path("imagery", fname)
        if not put_file(rel_ref, ref_fp):
          continue
        refs.append({
            "path": rel_ref,
            "name": ref.get("name") or "",
            "caption": ref.get("caption") or "",
        })
      export_styles.append({
          "slug": st["slug"],
          "name": st["name"],
          "medium": st["medium"],
          "target_slot": st["target_slot"],
          "incorporation": st["incorporation"],
          "style_text": st["style_text"],
          "aspect_ratio": st["aspect_ratio"],
          "scrim": st["scrim"],
          "reference_images": refs,
      })
    put("imagery/styles.json", export_styles)

  manifest = {
      "name": project["name"],
      "protocol": catalog_mod.PROTOCOL_VERSION,
      "catalogId": cat.catalog_id,
      "basicCatalogId": cat.basic_catalog_id,
      "extensions": has_ext,
      "extensionCatalogId": catalog_mod.EXTENSION_CATALOG_ID if has_ext else None,
      "components": sorted(components_json),
      "examples": example_names,
      "constitutionRevision": rev.get("number"),
      "exportedAt": util.now_iso(),
      "renderer": _versions(),
  }
  put("manifest.json", manifest)

  # Renderer runtime (verbatim copies + the package entry point).
  sources: dict[str, str] = {}
  for fn in RENDERER_FILES:
    if put_file(f"renderer/{fn}", os.path.join(RENDERER_DIR, fn)):
      if fn.endswith(".js"):
        sources[f"renderer/{fn}"] = files[f"renderer/{fn}"].decode("utf-8")
  for fn in VENDOR_FILES:
    put_file(f"renderer/{fn}", os.path.join(VENDOR, fn))
  if "renderer/a2ui.bundle.js" in files:
    sources["renderer/a2ui.bundle.js"] = files["renderer/a2ui.bundle.js"].decode(
        "utf-8"
    )
  # The host modules import the bundle as ../vendor/a2ui.bundle.js; in the
  # package everything sits in renderer/, so a two-line shim keeps the host
  # files verbatim.
  put("vendor/a2ui.bundle.js", VENDOR_SHIM_JS)
  sources["vendor/a2ui.bundle.js"] = VENDOR_SHIM_JS
  put("renderer/index.js", INDEX_JS)
  sources["renderer/index.js"] = INDEX_JS

  readme = _readme(
      project, cat, comps, rev, has_ext, bool(tpl_index), bool(imagery_styles)
  )
  put("README.md", readme)
  put("index.html", _gallery(project, cat.catalog_id, comps, theme))
  a2ui_license = os.path.join(
      PKG, "node_modules", "@a2ui", "web_core", "LICENSE"
  )
  if not os.path.isfile(a2ui_license):
    a2ui_license = os.path.join(A2UI_SEEDS, "LICENSE")
  if not os.path.isfile(a2ui_license):
    a2ui_license = os.path.join(PKG, "LICENSE")
  put_file("LICENSE-A2UI.txt", a2ui_license)

  # Asset materialization over every generated text file (not the verbatim
  # renderer copies).
  assets = _Assets(store, files)
  for path in list(files):
    if not path.endswith(_TEXT_EXT) or path.startswith(
        ("renderer/", "vendor/", "assets/")
    ) or path == "LICENSE-A2UI.txt":
      continue
    text = files[path].decode("utf-8")
    new = assets.rewrite(text)
    if new != text:
      files[path] = new.encode("utf-8")
  # demo.html embeds the (rewritten) JSON so it works from file://.
  def load(path):
    return json.loads(files[path].decode("utf-8"))

  demo_data = {
      "manifest": load("manifest.json"),
      "components": load("catalog/components.json"),
      "theme": load("tokens/theme.json"),
      "examples": [load(f"catalog/examples/{n}.json") for n in example_names],
  }
  vp_w = 412
  put("demo.html", _demo_html(project, demo_data, vp_w))
  put("renderer/offline_modules.js", _offline_modules_js(sources))
  return root, files
