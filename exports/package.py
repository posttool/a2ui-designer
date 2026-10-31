"""Design package export: catalog + components + tokens + constitution.

`GET /api/export/projects/{pid}/package.zip` → a self-contained folder that a
client/model team can use directly: the A2UI v0.9 catalog (`$id` =
project catalogId), usage rules for prompts, one validated example surface per
custom component, each component's web implementation (template + CSS) and
previews, the theme tokens in three formats, the constitution, and a static
gallery (`index.html`).
"""

from __future__ import annotations

import html
import io
import json
import os
import re
from typing import Any
import zipfile

from core import settings as settings_mod
from core import util
from core.a2ui import catalog as catalog_mod
from core.a2ui import custom_web
from core.a2ui import lowering

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEDS = os.path.join(PKG, "seeds")

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


def file_list(
    component_names: list[str],
    components: list[dict[str, Any]] | None = None,
) -> list[str]:
  files = [
      "README.md",
      "constitution.md",
      "index.html",
      "catalog/catalog.json",
      "catalog/rules.txt",
      "tokens/theme.json",
      "tokens/tokens.css",
      "tokens/design-tokens.json",
      "LICENSE-A2UI.txt",
  ]
  by_name = {
      r["name"]: r.get("definition") or r
      for r in components or []
      if isinstance(r, dict) and r.get("name")
  }
  for n in sorted(component_names):
    files += [
        f"catalog/examples/{n}.json",
        f"catalog/components/{n}/definition.json",
        f"catalog/components/{n}/template.html",
        f"catalog/components/{n}/styles.css",
        f"catalog/components/{n}/preview.png",
    ]
    if lowering.has_composition(by_name.get(n) or {}):
      files.append(f"catalog/components/{n}/composition.json")
  return files


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
      "/* A2UI theme tokens — generated by Design Constitution Builder. */",
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


def rules_txt(catalog: catalog_mod.Catalog) -> str:
  try:
    with open(
        os.path.join(SEEDS, "a2ui_basic_rules_v0_9.txt"), encoding="utf-8"
    ) as fh:
      base = fh.read().strip()
  except OSError:
    base = ""
  parts = [base] if base else []
  basic_disabled = [
      n for n in catalog.disabled if not catalog.components[n].custom
  ]
  if basic_disabled:
    parts.append(
        "**NOT AVAILABLE:** Do not use these Basic components: "
        + ", ".join(sorted(basic_disabled))
        + "."
    )
  custom = [(n, s) for n, s in catalog.enabled_components().items() if s.custom]
  if custom:
    lines = [
        "**DESIGN-SYSTEM COMPONENTS:** Prefer these over assembling the same"
        " thing from Basic components when their usage fits. Slot props"
        " (ComponentId / ChildList) take ids of other components."
    ]
    for n, s in custom:
      lines.append(f"- {n}: {s.description}".rstrip())
      if s.usage:
        lines.append(f"  Usage: {s.usage}")
    parts.append("\n".join(lines))
  return "\n\n".join(parts) + "\n"


def _readme(project, catalog_id, comps, rev) -> str:
  names = ", ".join(r["name"] for r in comps) or "(none)"
  return f"""# {project['name']} — design package

Exported {util.now_iso()} from Design Constitution Builder.
Constitution revision {rev.get('number', '?')}.

## Contents

| Path | What |
|---|---|
| `constitution.md` | Layout/decision rules for the A2UI generator (put it in the system prompt). |
| `catalog/catalog.json` | A2UI v0.9 catalog, `$id` = `catalogId` = `{catalog_id}`. Enabled Basic components + design-system components. |
| `catalog/rules.txt` | Catalog rules for the generator prompt (Basic rules, disabled components, component usage). |
| `catalog/examples/<Name>.json` | One example surface per custom component (`{{name, description, messages}}`), validated against the catalog. |
| `catalog/components/<Name>/` | `definition.json` (props, description, usage, sample), `template.html` + `styles.css` (web implementation), `preview.png`, `reference.png` (design source, when available). |
| `tokens/theme.json` | Resolved theme tokens (DCB canonical keys). |
| `tokens/tokens.css` | The same tokens as `--a2ui-*` CSS custom properties. |
| `tokens/design-tokens.json` | The same tokens in W3C DTCG format. |
| `templates/<slug>/` | Reusable templates (screens/sections composed from components): `template.json` (name, when to use, structure, components) and `reference.png` when available. |
| `index.html` | Static gallery of the components and tokens. |

Custom components: {names}.

## Using the catalog

Generators: include `catalog/catalog.json` (or the A2UI Python SDK's
`A2uiSchemaManager` / `PromptGenerator` pointed at it), `catalog/rules.txt`,
and `constitution.md` in the system prompt, and set
`createSurface.catalogId` to `{catalog_id}`. Clients must advertise the same
catalogId.

Renderers: the Basic components follow the A2UI v0.9 Basic catalog. Each
custom component ships a reference web implementation as a declarative
template:

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
tokens from `tokens/tokens.css`. Other A2UI renderers can implement each
component from `definition.json` + the previews.

The Basic catalog schema is © the A2UI authors, Apache 2.0
(`LICENSE-A2UI.txt`).
"""


def _gallery(project, catalog_id, comps, theme) -> str:
  esc = html.escape
  vars_css = "".join(f"{k}:{v};" for k, v in css_vars(theme).items())
  cards = []
  for r in comps:
    d = r.get("definition") or {}
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
          f'<figure><img src="catalog/components/{esc(r["name"])}/preview.png"'
          ' alt=""><figcaption>Web implementation</figcaption></figure>'
      )
    if r.get("reference_file"):
      imgs += (
          f'<figure><img src="catalog/components/{esc(r["name"])}/'
          'reference.png" alt=""><figcaption>Design reference</figcaption>'
          "</figure>"
      )
    cards.append(
        f'<section class="comp"><h2>{esc(r["name"])}</h2>'
        f"<p>{esc(d.get('description') or '')}</p>"
        f'<p class="usage">{esc(d.get("usage") or "")}</p>'
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
.comp h2{{margin:0 0 4px}} .usage{{color:#555}}
.imgs{{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}}
figure{{margin:0}} figure img{{max-width:420px;max-height:420px;border:1px solid #ddd;border-radius:8px;background:#fff}}
figcaption{{font-size:12px;color:#777}}
table{{border-collapse:collapse;margin-top:12px;width:100%}} td,th{{text-align:left;padding:4px 8px;border-bottom:1px solid #eee;vertical-align:top}}
.sws{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px}}
.sw{{display:flex;align-items:center;gap:8px}} .sw span{{width:28px;height:28px;border-radius:6px;border:1px solid #0002}}
.sw small{{color:#777}}
</style></head><body><main>
<h1>{esc(project['name'])}</h1>
<p>catalogId <code>{esc(catalog_id)}</code> · {len(comps)} custom component(s)</p>
{''.join(cards) or '<p>No custom components.</p>'}
<section class="comp"><h2>Color tokens</h2><div class="sws">{swatches}</div></section>
</main></body></html>
"""


def package_zip(store, svc, project: dict[str, Any]) -> tuple[str, bytes]:
  """Returns (file name, zip bytes)."""
  root, files = package_files(store, svc, project)
  buf = io.BytesIO()
  with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for path, data in files.items():
      z.writestr(root + path, data)
  return root.rstrip("/") + ".zip", buf.getvalue()


def package_files(
    store, svc, project: dict[str, Any]
) -> tuple[str, dict[str, bytes]]:
  """Returns (root dir with trailing slash, {relative path: bytes}).

  The single source of the package contents: package_zip zips exactly these
  files, and the Package page lists/serves them one by one.
  """
  from core.components import sample_messages  # pylint: disable=g-import-not-at-top
  from core.a2ui import validator  # pylint: disable=g-import-not-at-top
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

  def put_file(path: str, src: str | None) -> None:
    if src and os.path.isfile(src):
      with open(src, "rb") as fh:
        put(path, fh.read())

  put("README.md", _readme(project, cat.catalog_id, comps, rev))
  put(
      "constitution.md",
      bundles.constitution_md(store, project, rev) if rev else "",
  )
  put(
      "catalog/catalog.json",
      catalog_mod.to_a2ui_catalog_json(
          cat,
          cat.catalog_id,
          title=f"{project['name']} — A2UI catalog",
          description=project.get("description") or "",
      ),
  )
  put("catalog/rules.txt", rules_txt(cat))
  for r in comps:
    d = r.get("definition") or {}
    name = r["name"]
    msgs = sample_messages(d, cat.catalog_id, surface_id=slug(name))
    res = validator.validate(msgs, cat)
    put(
        f"catalog/examples/{name}.json",
        {
            "name": name,
            "description": d.get("description") or "",
            "valid": res.ok,
            "messages": msgs,
        },
    )
    chk = custom_web.check_definition(d, catalog_mod.basic_names())
    put(
        f"catalog/components/{name}/definition.json",
        {
            "name": name,
            "description": d.get("description") or "",
            "usage": d.get("usage") or "",
            "strategy": d.get("strategy") or "",
            "props": d.get("props") or {},
            "composition": (
                d.get("composition") if lowering.has_composition(d) else None
            ),
            "sample": d.get("sample") or {},
            "token_refs": chk["token_refs"],
            "source": {
                k: v
                for k, v in (r.get("source") or {}).items()
                if k
                in (
                    "kind",
                    "url",
                    "node_id",
                    "figma_name",
                    "variant_props",
                    "screen_title",
                )
            },
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
  put("tokens/theme.json", settings_mod.resolve_effective_theme(theme))
  put("tokens/tokens.css", tokens_css(theme))
  put("tokens/design-tokens.json", dtcg_tokens(theme))
  imagery_styles = svc.store.query(
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
        put_file(rel_ref, ref_fp)
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
  put("index.html", _gallery(project, cat.catalog_id, comps, theme))
  put_file("LICENSE-A2UI.txt", os.path.join(SEEDS, "A2UI_LICENSE"))
  return root, files
