"""Deterministic Figma node compiler for Web HTML/CSS and A2UI Basic Catalog.

Inspired by Stitch's `FigmaHtmlConverter` (`figma_html_converter.cc`), this
module deterministically compiles Figma REST API nodes (or DCB's condensed
`component_outline` trees) into:
1. Scoped Web HTML + CSS (`compile_node_to_html_css`) using `--a2ui-*` tokens
   and `{{prop}}` bindings that pass `core.a2ui.custom_web` validation.
2. Parameterized A2UI Basic Catalog compositions
   (`compile_node_to_basic_skeleton`) using only standard Basic components
   (`Card`, `Row`, `Column`, `Text`, `Image`, `Icon`, `Button`, `Divider`) that
   any Basic-catalog A2UI renderer can display.
"""

from __future__ import annotations

import html
import re
from typing import Any

from core.a2ui import custom_web

DS_REF = "common_types.json#/$defs/"

_MAIN_ALIGN_CSS = {
    "MIN": "flex-start",
    "CENTER": "center",
    "MAX": "flex-end",
    "SPACE_BETWEEN": "space-between",
}
_CROSS_ALIGN_CSS = {
    "MIN": "flex-start",
    "CENTER": "center",
    "MAX": "flex-end",
    "BASELINE": "baseline",
}
_MAIN_ALIGN_A2UI = {
    "MIN": "start",
    "CENTER": "center",
    "MAX": "end",
    "SPACE_BETWEEN": "spaceBetween",
}
_CROSS_ALIGN_A2UI = {
    "MIN": "start",
    "CENTER": "center",
    "MAX": "end",
    "BASELINE": "center",
}


def _camel_prop(raw: str, used: set[str], fallback: str = "text") -> str:
  words = re.findall(r"[A-Za-z0-9]+", raw or "")
  if not words:
    base = fallback
  else:
    base = words[0].lower() + "".join(
        w[:1].upper() + w[1:].lower() for w in words[1:]
    )
  if base[0].isdigit():
    base = fallback + base
  if base in ("id", "component", "weight", "accessibility", "children"):
    base = f"{base}Value"
  cand = base
  i = 2
  while cand in used:
    cand = f"{base}{i}"
    i += 1
  used.add(cand)
  return cand


def _hex_from_color(c: dict[str, Any], opacity: float = 1.0) -> str:
  r = round(float(c.get("r", 0)) * 255)
  g = round(float(c.get("g", 0)) * 255)
  b = round(float(c.get("b", 0)) * 255)
  a = float(c.get("a", 1.0)) * float(opacity)
  if a < 0.99:
    return f"#{r:02X}{g:02X}{b:02X}{round(a * 255):02X}"
  return f"#{r:02X}{g:02X}{b:02X}"


def _rgb_tuple(hx: str) -> tuple[int, int, int] | None:
  h = str(hx or "").strip().lstrip("#")[:6]
  if len(h) != 6:
    return None
  try:
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
  except ValueError:
    return None


def _token_css_var(token_name: str) -> str:
  t = token_name.strip()
  if t.startswith("--a2ui-"):
    return t
  if t == "font_family":
    return "--a2ui-font"
  if t == "font_family_heading":
    return "--a2ui-font-heading"
  if t == "font_family_mono":
    return "--a2ui-font-mono"
  return f"--a2ui-{t.replace('_', '-')}"


def _resolve_color_to_css_var(
    raw_color: str | None,
    palette: dict[str, str] | None,
    default_var: str = "--a2ui-surface-container",
) -> str:
  """Maps a hex or outline color string (`#0B57D0 = primary`) to `var(--a2ui-*)`."""
  if not raw_color:
    return f"var({default_var})"
  s = str(raw_color).strip()
  if "=" in s:
    tok = s.split("=", 1)[1].strip().split()[0]
    return f"var({_token_css_var(tok)})"
  if "≈" in s:
    tok = s.split("≈", 1)[1].strip().split()[0]
    return f"var({_token_css_var(tok)})"
  m = re.search(r"#[0-9A-Fa-f]{6}", s)
  if not m:
    return f"var({default_var})"
  hx = m.group(0).upper()
  rgb = _rgb_tuple(hx)
  if rgb and palette:
    best_tok = None
    best_dist = 1e9
    for k, v in palette.items():
      prgb = _rgb_tuple(str(v))
      if not prgb:
        continue
      d = sum((a - b) ** 2 for a, b in zip(rgb, prgb)) ** 0.5
      if d < best_dist:
        best_dist = d
        best_tok = k
    if best_tok and best_dist <= 65:
      return f"var({_token_css_var(best_tok)})"
  return f"var({default_var}, {hx})"


def _radius_css(px: float | int | None, is_root: bool = False) -> str | None:
  if not px or float(px) <= 0:
    return None
  r = float(px)
  if r >= 999:
    return "999px"
  if is_root or r >= 18:
    return "var(--a2ui-radius-card)"
  if r >= 10:
    return "var(--a2ui-radius-control)"
  return "var(--a2ui-radius-chip)"


def _extract_node_fill(
    node: dict[str, Any], palette: dict[str, str] | None
) -> str | None:
  if "fill" in node and isinstance(node["fill"], str):
    return node["fill"]
  for p in node.get("fills") or []:
    if p.get("visible", True) is False:
      continue
    if p.get("type") == "SOLID":
      return _hex_from_color(p.get("color") or {}, p.get("opacity", 1.0))
    if p.get("type") == "IMAGE":
      return "image"
  return None


def _extract_node_text_color(
    node: dict[str, Any], palette: dict[str, str] | None
) -> str | None:
  if "color" in node and isinstance(node["color"], str):
    return node["color"]
  return _extract_node_fill(node, palette)


def _node_size(node: dict[str, Any]) -> tuple[int, int]:
  if isinstance(node.get("size"), list) and len(node["size"]) == 2:
    return int(node["size"][0] or 0), int(node["size"][1] or 0)
  box = node.get("absoluteBoundingBox") or {}
  return round(float(box.get("width") or 0)), round(
      float(box.get("height") or 0)
  )


def _node_layout(node: dict[str, Any]) -> dict[str, Any] | None:
  if isinstance(node.get("layout"), dict):
    return node["layout"]
  lm = node.get("layoutMode")
  if lm in ("HORIZONTAL", "VERTICAL"):
    return {
        "dir": "row" if lm == "HORIZONTAL" else "column",
        "gap": node.get("itemSpacing") or 0,
        "pad": [
            node.get("paddingTop") or 0,
            node.get("paddingRight") or 0,
            node.get("paddingBottom") or 0,
            node.get("paddingLeft") or 0,
        ],
        "main": node.get("primaryAxisAlignItems") or "MIN",
        "cross": node.get("counterAxisAlignItems") or "MIN",
    }
  return None


def compile_node_to_html_css(
    node: dict[str, Any] | None,
    palette: dict[str, str] | None = None,
    property_defs: dict[str, Any] | None = None,
    variant_props: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
  """Deterministically compiles a Figma node or outline into Web HTML + CSS + props."""
  if not isinstance(node, dict) or not node:
    return {
        "template": "",
        "css": "",
        "props": {"type": "object", "properties": {}, "required": []},
        "sample_props": {},
    }

  palette = palette or {}
  used_props: set[str] = set()
  props_schema: dict[str, Any] = {}
  required_props: list[str] = []
  sample_props: dict[str, Any] = {}
  css_rules: list[str] = []
  cls_counter = [0]

  # Pre-populate declared Figma component properties & variants
  prop_map: dict[str, str] = {}
  for raw_k, pdef in (property_defs or {}).items():
    if not isinstance(pdef, dict):
      continue
    clean_k = raw_k.split("#", 1)[0].strip()
    ptype = str(pdef.get("type") or "TEXT").upper()
    pname = _camel_prop(clean_k, used_props, "prop")
    prop_map[clean_k] = pname
    if ptype == "BOOLEAN":
      props_schema[pname] = {
          "$ref": DS_REF + "DynamicBoolean",
          "description": f"Figma boolean property '{clean_k}'.",
      }
      sample_props[pname] = bool(pdef.get("default", True))
    elif ptype == "VARIANT" and pdef.get("options"):
      opts = [str(o) for o in pdef["options"] if o]
      props_schema[pname] = {
          "type": "string",
          "enum": opts,
          "default": str(pdef.get("default") or opts[0]),
          "description": f"Variant selector for {clean_k}.",
      }
      sample_props[pname] = str(pdef.get("default") or opts[0])
    else:
      props_schema[pname] = {
          "$ref": DS_REF + "DynamicString",
          "description": f"Text/value for {clean_k}.",
      }
      default_val = str(pdef.get("default") or clean_k)
      sample_props[pname] = default_val
      if len(required_props) < 3:
        required_props.append(pname)

  for vk, vopts in (variant_props or {}).items():
    if vk in prop_map or not vopts:
      continue
    pname = _camel_prop(vk, used_props, "variant")
    prop_map[vk] = pname
    opts = [str(o) for o in vopts if o]
    if opts:
      props_schema[pname] = {
          "type": "string",
          "enum": opts,
          "default": opts[0],
          "description": f"Variant '{vk}'.",
      }
      sample_props[pname] = opts[0]

  def next_cls(prefix: str = "el") -> str:
    cls_counter[0] += 1
    slug = re.sub(r"[^a-z0-9]+", "-", prefix.lower()).strip("-")[:18] or "el"
    return f"f-{slug}-{cls_counter[0]}"

  def walk(n: dict[str, Any], depth: int = 0) -> str:
    if depth > 8 or n.get("visible") is False:
      return ""
    ntype = str(n.get("type") or "FRAME").upper()
    nname = str(n.get("name") or ntype)
    w, h = _node_size(n)
    bound = n.get("bound_to") or {}
    if not bound and isinstance(n.get("componentPropertyReferences"), dict):
      bound = {
          k: str(v).split("#", 1)[0]
          for k, v in n["componentPropertyReferences"].items()
      }

    vis_prop = None
    if bound.get("visible"):
      raw_vis = bound["visible"]
      vis_prop = prop_map.get(raw_vis) or _camel_prop(
          raw_vis, used_props, "show"
      )
      if vis_prop not in props_schema:
        props_schema[vis_prop] = {"$ref": DS_REF + "DynamicBoolean"}
        sample_props[vis_prop] = True

    if ntype == "TEXT":
      chars = str(
          n.get("text") if "text" in n else (n.get("characters") or "")
      ).strip()
      if not chars and not bound.get("characters"):
        return ""
      raw_char_ref = bound.get("characters") or nname or "label"
      pname = prop_map.get(raw_char_ref)
      if not pname:
        pname = _camel_prop(raw_char_ref, used_props, "text")
        props_schema[pname] = {
            "$ref": DS_REF + "DynamicString",
            "description": f"Text for {nname or pname}.",
        }
        sample_props[pname] = chars or nname or "Sample text"
        if len(required_props) < 2:
          required_props.append(pname)

      st = (
          n.get("font")
          if isinstance(n.get("font"), dict)
          else (n.get("style") or {})
      )
      fsize = float(st.get("size") or st.get("fontSize") or 14)
      fweight = int(st.get("weight") or st.get("fontWeight") or 400)
      tag = (
          "h3"
          if fsize >= 20
          else (
              "h4"
              if fsize >= 16 and fweight >= 600
              else ("small" if fsize <= 12 else "p")
          )
      )
      cls = next_cls(nname or "text")
      tcolor = _resolve_color_to_css_var(
          _extract_node_text_color(n, palette),
          palette,
          "--a2ui-on-surface" if fsize >= 14 else "--a2ui-on-surface-variant",
      )
      decls = [
          "margin: 0",
          f"color: {tcolor}",
          (
              "font-family: var(--a2ui-font-heading)"
              if tag in ("h3", "h4")
              else "font-family: var(--a2ui-font)"
          ),
          f"font-size: {round(fsize)}px",
          f"font-weight: {fweight}",
      ]
      lheight = st.get("line") or st.get("lineHeightPx")
      if lheight:
        decls.append(f"line-height: {round(float(lheight))}px")
      css_rules.append(f".{cls} {{ {'; '.join(decls)}; }}")
      ifAttr = f' data-if="{vis_prop}"' if vis_prop else ""
      return f'<{tag} class="{cls}"{ifAttr}>{{{{{pname}}}}}</{tag}>'

    fill = _extract_node_fill(n, palette)
    if fill == "image" or (
        ntype in ("RECTANGLE", "FRAME")
        and "image" in nname.lower()
        and not (n.get("children") or [])
    ):
      pname = _camel_prop(nname or "imageUrl", used_props, "imageUrl")
      props_schema[pname] = {
          "$ref": DS_REF + "DynamicString",
          "description": f"Image URL for {nname or 'media'}.",
      }
      img_w = max(64, w or 320)
      img_h = max(64, h or 180)
      sample_props[pname] = f"/img/editorial-photo/{img_w}x{img_h}"
      cls = next_cls(nname or "img")
      css_rules.append(
          f".{cls} {{ display: block; width: 100%; height: auto;"
          " object-fit: cover; border-radius: var(--a2ui-radius-media); }"
      )
      ifAttr = f' data-if="{vis_prop}"' if vis_prop else f' data-if="{pname}"'
      return f'<img class="{cls}"{ifAttr} src="{{{{{pname}}}}}" alt="">'

    if ntype in (
        "VECTOR",
        "BOOLEAN_OPERATION",
        "STAR",
        "POLYGON",
        "ELLIPSE",
    ) or (w and h and w <= 32 and h <= 32 and not (n.get("children") or [])):
      icon_slug = (
          re.sub(r"[^a-z0-9_]+", "_", nname.lower()).strip("_") or "star"
      )
      cls = next_cls("icon")
      ic_color = _resolve_color_to_css_var(fill, palette, "--a2ui-primary")
      css_rules.append(
          f".{cls} {{ display: inline-flex; align-items: center;"
          f" justify-content: center; color: {ic_color}; }}"
      )
      return (
          f'<span class="{cls}"'
          f' data-icon="{html.escape(icon_slug, quote=True)}"></span>'
      )

    # Container node (COMPONENT, INSTANCE, FRAME, GROUP, SECTION)
    cls = "root-card" if depth == 0 else next_cls(nname or "box")
    layout = _node_layout(n)
    decls: list[str] = ["box-sizing: border-box"]
    if layout:
      decls.append("display: flex")
      decls.append(
          f"flex-direction: {'row' if layout.get('dir') == 'row' else 'column'}"
      )
      gap = float(layout.get("gap") or 0)
      if gap > 0:
        if abs(gap - 8) <= 2:
          decls.append("gap: var(--a2ui-space)")
        else:
          decls.append(f"gap: {round(gap)}px")
      pad = layout.get("pad") or [0, 0, 0, 0]
      if any(pad):
        if depth == 0 and all(abs(float(p) - pad[0]) <= 2 for p in pad):
          decls.append("padding: var(--a2ui-card-padding)")
        else:
          decls.append(
              f"padding: {round(pad[0])}px {round(pad[1])}px"
              f" {round(pad[2])}px {round(pad[3])}px"
          )
      main_css = _MAIN_ALIGN_CSS.get(
          str(layout.get("main") or "MIN"), "flex-start"
      )
      cross_css = _CROSS_ALIGN_CSS.get(
          str(layout.get("cross") or "MIN"), "stretch"
      )
      if main_css != "flex-start":
        decls.append(f"justify-content: {main_css}")
      if cross_css != "stretch":
        decls.append(f"align-items: {cross_css}")
    else:
      decls.append("display: flex")
      decls.append("flex-direction: column")
      decls.append("gap: var(--a2ui-space)")
      if depth == 0:
        decls.append("padding: var(--a2ui-card-padding)")

    if fill and fill != "image":
      bg_var = _resolve_color_to_css_var(
          fill,
          palette,
          "--a2ui-surface-container-low"
          if depth == 0
          else "--a2ui-surface-container",
      )
      decls.append(f"background: {bg_var}")
    elif depth == 0:
      decls.append("background: var(--a2ui-surface-container-low)")

    rad = n.get("radius") if "radius" in n else n.get("cornerRadius")
    rad_css = _radius_css(rad, is_root=(depth == 0))
    if rad_css:
      decls.append(f"border-radius: {rad_css}")
    elif depth == 0:
      decls.append("border-radius: var(--a2ui-radius-card)")

    stroke = n.get("stroke")
    if not stroke:
      for s in n.get("strokes") or []:
        if s.get("type") == "SOLID" and s.get("visible", True):
          stroke = _hex_from_color(s.get("color") or {})
          break
    if stroke:
      scolor = _resolve_color_to_css_var(
          stroke, palette, "--a2ui-outline-variant"
      )
      decls.append(f"border: var(--a2ui-border-width, 1px) solid {scolor}")

    css_rules.append(f".{cls} {{ {'; '.join(decls)}; }}")
    child_html = [
        walk(c, depth + 1)
        for c in n.get("children") or []
        if isinstance(c, dict)
    ]
    inner = "".join(x for x in child_html if x)
    if not inner and depth == 0:
      pname = _camel_prop(nname or "title", used_props, "title")
      props_schema[pname] = {
          "$ref": DS_REF + "DynamicString",
          "description": "Primary label.",
      }
      sample_props[pname] = nname or "Title"
      if pname not in required_props:
        required_props.append(pname)
      inner = f"<h3>{{{{{pname}}}}}</h3>"
    tag = "article" if depth == 0 else "div"
    ifAttr = f' data-if="{vis_prop}"' if vis_prop else ""
    return f'<{tag} class="{cls}"{ifAttr}>{inner}</{tag}>'

  raw_html = walk(node, 0)
  raw_css = "\n".join(css_rules)
  san_t = custom_web.sanitize_template(raw_html)
  san_c = custom_web.sanitize_css(raw_css)
  return {
      "template": custom_web.format_template(san_t["html"]),
      "css": custom_web.format_css(san_c["css"]),
      "props": {
          "type": "object",
          "properties": props_schema,
          "required": required_props,
      },
      "sample_props": sample_props,
  }


# ----------------------------------------------------------- Basic fit score
# Every visible node that `compile_node_to_basic_skeleton` visits is scored on
# how faithfully its Basic Catalog mapping reproduces it: 1.0 (faithful), 0.5
# (partial — approximated, e.g. a gradient fill becomes one flat token color)
# or 0.0 (lossy — no Basic component can express it, e.g. absolute
# positioning or custom vector artwork). The mean over all nodes is the
# component's *Basic fit* (0–1); `settings.catalog.basic_fit_threshold`
# decides whether an `auto` component becomes a Basic composition (fit ≥
# threshold) or a custom web component (fit < threshold).
FIT_FAITHFUL = 1.0
FIT_PARTIAL = 0.5
FIT_LOSSY = 0.0
MAX_FIT_REASONS = 12
_SHAPE_TYPES = ("VECTOR", "BOOLEAN_OPERATION", "STAR", "POLYGON")
_ICON_MAX = 32  # px; leaf shapes up to this size map to Icon faithfully


def _fit_issues(
    n: dict[str, Any],
    ntype: str,
    w: int,
    h: int,
    kids: list[dict[str, Any]],
    depth: int,
) -> list[tuple[float, str]]:
  """Lists `(weight, reason)` pairs for what the Basic mapping of `n` loses.

  Works on raw Figma REST nodes, DCB's condensed `component_outline` trees and
  the Figma-like outlines `refs.stitch` builds from Stitch DOM regions.
  """
  out: list[tuple[float, str]] = []
  if depth > 8:
    out.append((FIT_LOSSY, "nested deeper than 8 levels"))
  if str(n.get("layoutPositioning") or "").upper() == "ABSOLUTE":
    out.append((FIT_LOSSY, "absolute positioning"))
  rot = n.get("rotation")
  if isinstance(rot, (int, float)) and abs(float(rot)) > 1:
    out.append((FIT_LOSSY, "rotated layer"))
  if n.get("isMask"):
    out.append((FIT_LOSSY, "mask layer"))
  if ntype != "TEXT" and len(kids) >= 2 and _node_layout(n) is None:
    out.append(
        (FIT_LOSSY, "free-form layout (no auto-layout; layers may overlap)")
    )

  fill_str = n.get("fill") if isinstance(n.get("fill"), str) else ""
  if fill_str.startswith("gradient"):
    out.append((FIT_PARTIAL, "gradient fill"))
  for p in n.get("fills") or []:
    if not isinstance(p, dict) or p.get("visible", True) is False:
      continue
    if str(p.get("type") or "").startswith("GRADIENT"):
      out.append((FIT_PARTIAL, "gradient fill"))
      break
  for e in n.get("effects") or []:
    if not isinstance(e, dict) or e.get("visible", True) is False:
      continue
    if e.get("type") in ("LAYER_BLUR", "BACKGROUND_BLUR", "INNER_SHADOW"):
      out.append((FIT_PARTIAL, "blur / inner-shadow effect"))
      break
  op = n.get("opacity")
  if isinstance(op, (int, float)) and float(op) < 0.98:
    out.append((FIT_PARTIAL, "layer opacity"))
  if n.get("strokeDashes"):
    out.append((FIT_PARTIAL, "dashed border"))
  if isinstance(n.get("individualStrokeWeights"), dict):
    out.append((FIT_PARTIAL, "per-side border"))
  radii = n.get("rectangleCornerRadii")
  if isinstance(radii, list) and len(set(radii)) > 1:
    out.append((FIT_PARTIAL, "mixed corner radii"))

  if ntype == "TEXT":
    if n.get("characterStyleOverrides"):
      out.append((FIT_PARTIAL, "mixed inline text styles"))
    return out

  is_image = bool(n.get("image_fill")) or _extract_node_fill(n, None) == "image"
  big = (w or 0) > _ICON_MAX or (h or 0) > _ICON_MAX
  if ntype in _SHAPE_TYPES and big:
    out.append((FIT_LOSSY, "custom vector artwork"))
  elif ntype == "ELLIPSE" and big:
    out.append(
        (FIT_PARTIAL, "circular image")
        if is_image
        else (FIT_LOSSY, "ellipse shape")
    )
  elif ntype == "RECTANGLE" and not kids and not is_image and big:
    thin = (h and h <= 2 and w >= 40) or (w and w <= 2 and h >= 40)
    if not thin:
      out.append((FIT_PARTIAL, "plain colored box"))
  return out


def _summarize_fit(
    total: float, counts: dict[str, int], reasons: list[dict[str, Any]], n: int
) -> dict[str, Any]:
  by_count = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
  return {
      "score": round(total / n, 3) if n else None,
      "nodes": n,
      "faithful": int(counts.get("__faithful__") or 0),
      "partial": int(counts.get("__partial__") or 0),
      "lossy": int(counts.get("__lossy__") or 0),
      "reasons": reasons[:MAX_FIT_REASONS],
      "summary": [
          f"{c}× {reason}"
          for reason, c in by_count
          if not reason.startswith("__")
      ][:6],
  }


def compile_node_to_basic_skeleton(
    node: dict[str, Any] | None,
    property_defs: dict[str, Any] | None = None,
) -> dict[str, Any]:
  """Deterministically maps a Figma node or outline into an A2UI Basic Catalog composition.

  Returns `{composition: {root, components}, props, sample_props, fit}` where
  `fit` is `{score (0–1 or None), nodes, faithful, partial, lossy, reasons:
  [{node, reason}], summary: ["2× absolute positioning", …]}`.
  """
  if not isinstance(node, dict) or not node:
    return {
        "composition": {
            "root": "card",
            "components": [
                {"id": "card", "component": "Card", "child": "col"},
                {"id": "col", "component": "Column", "children": ["title_txt"]},
                {
                    "id": "title_txt",
                    "component": "Text",
                    "text": "### {{title}}",
                },
            ],
        },
        "props": {
            "type": "object",
            "properties": {"title": {"$ref": DS_REF + "DynamicString"}},
            "required": ["title"],
        },
        "sample_props": {"title": "Sample title"},
        "fit": _summarize_fit(0.0, {}, [], 0),
    }

  used_props: set[str] = set()
  props_schema: dict[str, Any] = {}
  required_props: list[str] = []
  sample_props: dict[str, Any] = {}
  components: list[dict[str, Any]] = []
  id_counter = [0]
  fit_total = [0.0]
  fit_nodes = [0]
  fit_counts: dict[str, int] = {}
  fit_reasons: list[dict[str, Any]] = []

  def score_node(
      n: dict[str, Any],
      ntype: str,
      w: int,
      h: int,
      kids: list[dict[str, Any]],
      depth: int,
  ) -> None:
    issues = _fit_issues(n, ntype, w, h, kids, depth)
    weight = min([wt for wt, _ in issues], default=FIT_FAITHFUL)
    fit_total[0] += weight
    fit_nodes[0] += 1
    bucket = (
        "__faithful__"
        if weight >= FIT_FAITHFUL
        else ("__partial__" if weight > FIT_LOSSY else "__lossy__")
    )
    fit_counts[bucket] = fit_counts.get(bucket, 0) + 1
    name = str(n.get("name") or ntype)[:50]
    for wt, reason in issues:
      fit_counts[reason] = fit_counts.get(reason, 0) + 1
      if len(fit_reasons) < MAX_FIT_REASONS:
        fit_reasons.append({
            "node": name,
            "reason": reason,
            "severity": "lossy" if wt <= FIT_LOSSY else "partial",
        })

  prop_map: dict[str, str] = {}
  for raw_k, pdef in (property_defs or {}).items():
    if not isinstance(pdef, dict):
      continue
    clean_k = raw_k.split("#", 1)[0].strip()
    pname = _camel_prop(clean_k, used_props, "prop")
    prop_map[clean_k] = pname
    props_schema[pname] = {
        "$ref": DS_REF + "DynamicString",
        "description": f"Value for {clean_k}.",
    }
    sample_props[pname] = str(pdef.get("default") or clean_k)
    if len(required_props) < 2:
      required_props.append(pname)

  def next_id(prefix: str) -> str:
    id_counter[0] += 1
    slug = re.sub(r"[^a-z0-9]+", "_", prefix.lower()).strip("_")[:16] or "node"
    return f"{slug}_{id_counter[0]}"

  def walk_basic(n: dict[str, Any], depth: int = 0) -> str | None:
    if n.get("visible") is False:
      return None
    ntype = str(n.get("type") or n.get("kind") or "FRAME").upper()
    nname = str(n.get("name") or ntype)
    w, h = _node_size(n)
    kids = [c for c in n.get("children") or [] if isinstance(c, dict)]
    score_node(n, ntype, w, h, kids, depth)
    if depth > 8:
      return None
    bound = n.get("bound_to") or {}
    if not bound and isinstance(n.get("componentPropertyReferences"), dict):
      bound = {
          k: str(v).split("#", 1)[0]
          for k, v in n["componentPropertyReferences"].items()
      }

    if ntype == "TEXT":
      chars = str(
          n.get("text") if "text" in n else (n.get("characters") or "")
      ).strip()
      raw_ref = (
          n.get("bound_prop") or bound.get("characters") or nname or "text"
      )
      pname = (
          str(n["bound_prop"]) if n.get("bound_prop") else prop_map.get(raw_ref)
      )
      if not pname:
        pname = _camel_prop(raw_ref, used_props, "text")
      else:
        used_props.add(pname)
      if pname not in props_schema:
        props_schema[pname] = {
            "$ref": DS_REF + "DynamicString",
            "description": f"Text for {nname or pname}.",
        }
        sample_props[pname] = chars or nname or "Text"
        if len(required_props) < 2:
          required_props.append(pname)
      st = (
          n.get("font")
          if isinstance(n.get("font"), dict)
          else (n.get("style") or {})
      )
      fsize = float(st.get("size") or st.get("fontSize") or 14)
      fweight = int(st.get("weight") or st.get("fontWeight") or 400)
      # A2UI v1.0 Text has only body|caption; headings are Markdown.
      heading = (
          "## "
          if fsize >= 24
          else (
              "### "
              if fsize >= 20
              else ("#### " if fsize >= 16 and fweight >= 600 else "")
          )
      )
      tid = next_id(nname or "txt")
      tnode: dict[str, Any] = {
          "id": tid,
          "component": "Text",
          "text": f"{heading}{{{{{pname}}}}}",
      }
      if not heading:
        tnode["variant"] = "caption" if fsize <= 12 else "body"
      components.append(tnode)
      return tid

    fill = "image" if n.get("image_fill") else _extract_node_fill(n, None)
    if fill == "image":
      pname = (
          str(n["bound_prop"])
          if n.get("bound_prop")
          else _camel_prop(nname or "imageUrl", used_props, "imageUrl")
      )
      used_props.add(pname)
      props_schema[pname] = {
          "$ref": DS_REF + "DynamicString",
          "description": f"Image URL for {nname or 'media'}.",
      }
      img_w = max(64, w or 320)
      img_h = max(64, h or 180)
      sample_props[pname] = f"/img/editorial-photo/{img_w}x{img_h}"
      iid = next_id("img")
      components.append({
          "id": iid,
          "component": "Image",
          "url": f"{{{{{pname}}}}}",
          "fit": "cover",
      })
      return iid

    if h and w and h <= 2 and w >= 40:
      did = next_id("div")
      components.append({"id": did, "component": "Divider"})
      return did

    if ntype in (
        "VECTOR",
        "BOOLEAN_OPERATION",
        "STAR",
        "POLYGON",
        "ELLIPSE",
    ) or (w and h and w <= 32 and h <= 32 and not (n.get("children") or [])):
      ic_id = next_id("icon")
      components.append({
          "id": ic_id,
          "component": "Icon",
          "name": "star",
      })
      return ic_id

    # Check if this is a Button-like instance
    if depth > 0 and "button" in nname.lower() and len(kids) <= 2:
      btn_text = next(
          (
              str(k.get("text") or k.get("characters") or "")
              for k in kids
              if str(k.get("type") or "").upper() == "TEXT"
          ),
          "Continue",
      )
      lbl_prop = _camel_prop(nname + "Label", used_props, "actionLabel")
      act_prop = _camel_prop(nname + "Action", used_props, "action")
      props_schema[lbl_prop] = {"$ref": DS_REF + "DynamicString"}
      props_schema[act_prop] = {"$ref": DS_REF + "Action"}
      sample_props[lbl_prop] = btn_text or "Action"
      sample_props[act_prop] = {"event": {"name": "tap"}}
      lbl_id = next_id("btn_lbl")
      btn_id = next_id("btn")
      components.append({
          "id": lbl_id,
          "component": "Text",
          "text": f"{{{{{lbl_prop}}}}}",
      })
      components.append({
          "id": btn_id,
          "component": "Button",
          "variant": "primary",
          "child": lbl_id,
          "action": f"{{{{{act_prop}}}}}",
      })
      return btn_id

    child_ids = []
    for k in kids:
      cid = walk_basic(k, depth + 1)
      if cid:
        child_ids.append(cid)

    if not child_ids and depth == 0:
      pname = _camel_prop(nname or "title", used_props, "title")
      props_schema[pname] = {"$ref": DS_REF + "DynamicString"}
      sample_props[pname] = nname or "Title"
      required_props.append(pname)
      tid = next_id("title")
      components.append({
          "id": tid,
          "component": "Text",
          "text": f"### {{{{{pname}}}}}",
      })
      child_ids.append(tid)

    if not child_ids:
      return None

    layout = _node_layout(n)
    is_row = bool(layout and layout.get("dir") == "row")
    container_id = next_id("row" if is_row else "col")
    container_node: dict[str, Any] = {
        "id": container_id,
        "component": "Row" if is_row else "Column",
        "children": child_ids,
    }
    if layout:
      j = _MAIN_ALIGN_A2UI.get(str(layout.get("main") or "MIN"))
      a = _CROSS_ALIGN_A2UI.get(str(layout.get("cross") or "MIN"))
      if j and j != "start":
        container_node["justify"] = j
      if a and a != "start":
        container_node["align"] = a

    if depth == 0:
      card_id = "card"
      # Insert Card before container so root is first in list
      components.insert(
          0,
          {"id": card_id, "component": "Card", "child": container_id},
      )
      components.append(container_node)
      return card_id

    components.append(container_node)
    return container_id

  root_id = walk_basic(node, 0) or "card"
  return {
      "composition": {
          "root": root_id,
          "components": components,
      },
      "props": {
          "type": "object",
          "properties": props_schema,
          "required": required_props,
      },
      "sample_props": sample_props,
      "fit": _summarize_fit(
          fit_total[0], fit_counts, fit_reasons, fit_nodes[0]
      ),
  }
