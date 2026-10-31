"""Project settings defaults, expanded design token catalog, and theme helpers."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from core import material_color_utilities as mcu

RENDERERS: list[dict[str, Any]] = [
    {
        "id": "web",
        "label": "Web (HTML/CSS)",
        "short_label": "Web",
        "engine": "Headless Chromium (CDP) + A2UI Web Renderer",
        "platform": "web",
        "badge": "WEB",
    },
]

RENDERER_IDS: list[str] = [r["id"] for r in RENDERERS]
DEFAULT_RENDERERS: list[str] = [
    "web",
]

GEMINI_MODELS: list[dict[str, str]] = [
    {
        "id": "gemini-3.8-flash",
        "label": "Gemini 3.8 Flash (Latest · Default)",
        "note": "Fastest latest multimodal & structured JSON/SVG model",
    },
    {
        "id": "gemini-3.7-flash",
        "label": "Gemini 3.7 Flash",
        "note": "Fast multimodal & structured JSON/SVG model",
    },
    {
        "id": "gemini-3.6-flash",
        "label": "Gemini 3.6 Flash",
        "note": "Balanced speed & layout fidelity",
    },
    {
        "id": "gemini-3.5-flash",
        "label": "Gemini 3.5 Flash",
        "note": "Stable 3.5 Flash",
    },
    {
        "id": "gemini-3.5-flash-lite",
        "label": "Gemini 3.5 Flash Lite",
        "note": "Low-latency lightweight Flash",
    },
    {
        "id": "gemini-3.1-pro-preview",
        "label": "Gemini 3.1 Pro Preview",
        "note": "High reasoning & complex SVG synthesis",
    },
    {
        "id": "gemini-3.1-flash-lite",
        "label": "Gemini 3.1 Flash Lite",
        "note": "Fast lightweight 3.1",
    },
    {
        "id": "gemini-3-flash-preview",
        "label": "Gemini 3.0 Flash Preview",
        "note": "Gemini 3.0 Flash",
    },
    {
        "id": "gemini-4-argon",
        "label": "Gemini 4 Argon",
        "note": "Experimental Gemini 4",
    },
    {
        "id": "gemini-flash-latest",
        "label": "Gemini Flash Latest (Rolling Alias)",
        "note": "Rolling latest Flash alias",
    },
    {
        "id": "gemini-pro-latest",
        "label": "Gemini Pro Latest (Rolling Alias)",
        "note": "Rolling latest Pro alias",
    },
    {
        "id": "gemini-2.5-flash-lite",
        "label": "Gemini 2.5 Flash-Lite",
        "note": "Ultra-fast 2.5 Flash-Lite",
    },
]

GEMINI_IMAGE_MODELS: list[dict[str, str]] = [
    {
        "id": "gemini-3.1-flash-image",
        "label": "Gemini 3.1 Flash Image — Nano Banana 2 (Latest · Default)",
        "note": "Fast multimodal image generation with style reference images",
    },
    {
        "id": "gemini-3.1-flash-image-preview",
        "label": "Gemini 3.1 Flash Image Preview — Nano Banana 2 Preview",
        "note": "Preview channel for Nano Banana 2",
    },
    {
        "id": "gemini-nano-banana-2.1",
        "label": "Gemini Nano Banana 2.1",
        "note": "Nano Banana 2.1 experimental image generation",
    },
    {
        "id": "gemini-3-pro-image",
        "label": "Gemini 3 Pro Image — Nano Banana Pro",
        "note": "High-detail Pro image generation with reference images",
    },
    {
        "id": "gemini-3-pro-image-preview",
        "label": "Gemini 3 Pro Image Preview — Nano Banana Pro Preview",
        "note": "Preview channel for Nano Banana Pro",
    },
    {
        "id": "gemini-3.1-flash-lite-image",
        "label": "Gemini 3.1 Flash-Lite Image — Nano Banana 2 Lite",
        "note": "Low-latency lightweight image generation",
    },
    {
        "id": "gemini-2.5-flash-image",
        "label": "Gemini 2.5 Flash Image — Nano Banana",
        "note": "Stable 2.5 Flash image generation",
    },
]

DEFAULT_TEXT_MODEL = "gemini-3.8-flash"
DEFAULT_MODEL = DEFAULT_TEXT_MODEL
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
DEFAULT_SVG_MODEL = "gemini-3.8-flash"

DEFAULT_THEME: dict[str, Any] = {
    # Core & Extended M3 Color Palette (20 semantic roles)
    "primary": "#0B57D0",
    "on_primary": "#FFFFFF",
    "primary_container": "#D3E3FD",
    "on_primary_container": "#041E49",
    "secondary": "#00639B",
    "on_secondary": "#FFFFFF",
    "secondary_container": "#C2E7FF",
    "on_secondary_container": "#001D35",
    "tertiary": "#146C2E",
    "on_tertiary": "#FFFFFF",
    "surface": "#FFFFFF",
    "on_surface": "#1F1F1F",
    "surface_container_low": "#F8FAFD",
    "surface_container": "#F0F4F9",
    "surface_container_high": "#E9EEF6",
    "on_surface_variant": "#444746",
    "outline": "#C4C7C5",
    "outline_variant": "#E0E3E1",
    "error": "#B3261E",
    "on_error": "#FFFFFF",
    "error_container": "#F9DEDC",
    "on_error_container": "#410E0B",
    # Shape, Borders & Elevation
    "radius_card": 24,
    "radius_control": 12,
    "radius_chip": 8,
    "radius_media": 12,
    "border_width": 1,
    "card_elevation": 0,
    # Typography
    "font_family": "Google Sans Flex",
    "font_family_heading": "",
    "font_family_mono": "Google Sans Code",
    "type_scale": 1.0,
    "heading_scale": 1.0,
    "heading_weight": 500,
    "body_weight": 400,
    "line_height": 1.5,
    # Spacing & Density
    "space": 8,
    "card_padding": 12,
    "surface_padding": 16,
    "card_margin": 4,
    "item_margin": 4,
    "control_height": 40,
    # Mode & Extensibility
    "scheme": "light",
    "custom_tokens": {},
    "custom_css": "",
}

DARK_THEME_COLORS: dict[str, str] = {
    "primary": "#A8C7FA",
    "on_primary": "#062E6F",
    "primary_container": "#0842A0",
    "on_primary_container": "#D3E3FD",
    "secondary": "#7FCFFF",
    "on_secondary": "#003355",
    "secondary_container": "#004A77",
    "on_secondary_container": "#C2E7FF",
    "tertiary": "#6DD58C",
    "on_tertiary": "#0A3818",
    "surface": "#131314",
    "on_surface": "#E3E3E3",
    "surface_container_low": "#1B1B1F",
    "surface_container": "#1E1F20",
    "surface_container_high": "#282A2C",
    "on_surface_variant": "#C4C7C5",
    "outline": "#444746",
    "outline_variant": "#333537",
    "error": "#F2B8B5",
    "on_error": "#601410",
    "error_container": "#8C1D18",
    "on_error_container": "#F9DEDC",
}

CANONICAL_TOKEN_KEYS: set[str] = {
    k for k in DEFAULT_THEME if k not in ("custom_tokens", "custom_css")
}

BOX_SPACING_TOKENS: set[str] = {
    "card_padding",
    "surface_padding",
    "card_margin",
    "item_margin",
}

NUMERIC_INT_TOKENS: set[str] = {
    "radius_card",
    "radius_control",
    "radius_chip",
    "radius_media",
    "border_width",
    "card_elevation",
    "heading_weight",
    "body_weight",
    "space",
    "control_height",
}

NUMERIC_FLOAT_TOKENS: set[str] = {
    "type_scale",
    "heading_scale",
    "line_height",
}

ORDERED_COLOR_TOKENS: tuple[str, ...] = (
    "primary",
    "on_primary",
    "primary_container",
    "on_primary_container",
    "secondary",
    "on_secondary",
    "secondary_container",
    "on_secondary_container",
    "tertiary",
    "on_tertiary",
    "surface",
    "on_surface",
    "surface_container_low",
    "surface_container",
    "surface_container_high",
    "on_surface_variant",
    "outline",
    "outline_variant",
    "error",
    "on_error",
    "error_container",
    "on_error_container",
)

COLOR_TOKENS: set[str] = set(ORDERED_COLOR_TOKENS)

# Mapping between CSS custom property names (`--a2ui-...`) and canonical theme keys.
CSS_VAR_TO_TOKEN: dict[str, str] = {
    "--a2ui-primary": "primary",
    "--a2ui-on-primary": "on_primary",
    "--a2ui-primary-container": "primary_container",
    "--a2ui-on-primary-container": "on_primary_container",
    "--a2ui-secondary": "secondary",
    "--a2ui-on-secondary": "on_secondary",
    "--a2ui-secondary-container": "secondary_container",
    "--a2ui-on-secondary-container": "on_secondary_container",
    "--a2ui-tertiary": "tertiary",
    "--a2ui-on-tertiary": "on_tertiary",
    "--a2ui-surface": "surface",
    "--a2ui-on-surface": "on_surface",
    "--a2ui-surface-container-low": "surface_container_low",
    "--a2ui-surface-container": "surface_container",
    "--a2ui-surface-container-high": "surface_container_high",
    "--a2ui-on-surface-variant": "on_surface_variant",
    "--a2ui-outline": "outline",
    "--a2ui-outline-variant": "outline_variant",
    "--a2ui-error": "error",
    "--a2ui-on-error": "on_error",
    "--a2ui-error-container": "error_container",
    "--a2ui-on-error-container": "on_error_container",
    "--a2ui-radius-card": "radius_card",
    "--a2ui-radius-lg": "radius_card",
    "--a2ui-radius-control": "radius_control",
    "--a2ui-radius-chip": "radius_chip",
    "--a2ui-radius-media": "radius_media",
    "--a2ui-border-width": "border_width",
    "--a2ui-card-elevation": "card_elevation",
    "--a2ui-font": "font_family",
    "--a2ui-font-family": "font_family",
    "--a2ui-font-heading": "font_family_heading",
    "--a2ui-font-family-heading": "font_family_heading",
    "--a2ui-font-mono": "font_family_mono",
    "--a2ui-font-family-mono": "font_family_mono",
    "--a2ui-type-scale": "type_scale",
    "--a2ui-heading-scale": "heading_scale",
    "--a2ui-heading-weight": "heading_weight",
    "--a2ui-body-weight": "body_weight",
    "--a2ui-line-height": "line_height",
    "--a2ui-space": "space",
    "--a2ui-card-padding": "card_padding",
    "--a2ui-surface-padding": "surface_padding",
    "--a2ui-card-margin": "card_margin",
    "--a2ui-item-margin": "item_margin",
    "--a2ui-control-height": "control_height",
    "--a2ui-scheme": "scheme",
}

TOKEN_TO_CSS_VAR: dict[str, str] = {
    k: f"--a2ui-{k.replace('_', '-')}" for k in CANONICAL_TOKEN_KEYS
}
TOKEN_TO_CSS_VAR["font_family"] = "--a2ui-font"
TOKEN_TO_CSS_VAR["font_family_heading"] = "--a2ui-font-heading"
TOKEN_TO_CSS_VAR["font_family_mono"] = "--a2ui-font-mono"

TOKEN_TO_ALL_CSS_VARS: dict[str, list[str]] = {}
for _css_var, _tok_key in CSS_VAR_TO_TOKEN.items():
  TOKEN_TO_ALL_CSS_VARS.setdefault(_tok_key, []).append(_css_var)
for _tok_key in CANONICAL_TOKEN_KEYS:
  _default_var = f"--a2ui-{_tok_key.replace('_', '-')}"
  if _default_var not in TOKEN_TO_ALL_CSS_VARS.get(_tok_key, []):
    TOKEN_TO_ALL_CSS_VARS.setdefault(_tok_key, []).append(_default_var)


def normalize_token_key(raw_key: str) -> str:
  """Normalizes a CSS variable name or token key to snake_case or custom slug."""
  k = (raw_key or "").strip()
  if not k:
    return ""
  lower = k.lower()
  if lower in CSS_VAR_TO_TOKEN:
    return CSS_VAR_TO_TOKEN[lower]
  if lower.startswith("--a2ui-"):
    slug = lower[len("--a2ui-") :].replace("-", "_")
    if slug in CANONICAL_TOKEN_KEYS:
      return slug
    return lower[len("--a2ui-") :]
  if lower.startswith("--"):
    slug = lower[2:].replace("-", "_")
    if slug in CANONICAL_TOKEN_KEYS:
      return slug
    return lower[2:]
  snake = lower.replace("-", "_")
  if snake in CANONICAL_TOKEN_KEYS:
    return snake
  return k.strip().replace("_", "-")


def parse_box_px(val: Any, default: int = 0) -> tuple[int, int, int, int]:
  """Parses a 1..4 value box spacing token into `(top, right, bottom, left)` integer px."""
  d = int(round(float(default))) if isinstance(default, (int, float)) else 0
  if isinstance(val, (int, float)) and not isinstance(val, bool):
    n = int(round(float(val)))
    return (n, n, n, n)
  if isinstance(val, (list, tuple)):
    nums: list[int] = []
    for item in val[:4]:
      m = re.search(r"-?\d+(?:\.\d+)?", str(item))
      if m:
        nums.append(int(round(float(m.group(0)))))
  else:
    nums = [
        int(round(float(m)))
        for m in re.findall(r"-?\d+(?:\.\d+)?", str(val or ""))[:4]
    ]
  if not nums:
    return (d, d, d, d)
  if len(nums) == 1:
    return (nums[0], nums[0], nums[0], nums[0])
  if len(nums) == 2:
    return (nums[0], nums[1], nums[0], nums[1])
  if len(nums) == 3:
    return (nums[0], nums[1], nums[2], nums[1])
  return (nums[0], nums[1], nums[2], nums[3])


def coerce_box_value(val: Any, default: int = 0) -> int | str:
  """Coerces a box spacing token to int when all 4 sides match, or 'Tpx Rpx Bpx Lpx'."""
  t, r, b, l = parse_box_px(val, default)
  if t == r == b == l:
    return t
  return f"{t}px {r}px {b}px {l}px"


def format_box_css(val: Any, default: int = 0) -> str:
  """Formats a box spacing token as CSS ('Npx' or 'Tpx Rpx Bpx Lpx')."""
  t, r, b, l = parse_box_px(val, default)
  if t == r == b == l:
    return f"{t}px"
  return f"{t}px {r}px {b}px {l}px"


def coerce_token_value(key: str, val: Any) -> Any:
  """Coerces a token value to its canonical Python type."""
  if key == "scheme":
    s = str(val or "light").strip().lower()
    return "dark" if s == "dark" else "light"
  if key in BOX_SPACING_TOKENS:
    return coerce_box_value(val, DEFAULT_THEME[key])
  if key in NUMERIC_INT_TOKENS:
    if isinstance(val, (int, float)):
      return int(round(val))
    m = re.search(r"-?\d+(?:\.\d+)?", str(val or ""))
    return int(round(float(m.group(0)))) if m else DEFAULT_THEME[key]
  if key in NUMERIC_FLOAT_TOKENS:
    if isinstance(val, (int, float)):
      return round(float(val), 3)
    m = re.search(r"-?\d+(?:\.\d+)?", str(val or ""))
    return round(float(m.group(0)), 3) if m else DEFAULT_THEME[key]
  if key in COLOR_TOKENS:
    s = str(val or "").strip()
    return s if s else DEFAULT_THEME[key]
  if key in ("font_family", "font_family_heading", "font_family_mono"):
    s = str(val or "").strip()
    # Strip wrapping quotes from CSS font-family strings when primary font is extracted
    if "," in s:
      first = s.split(",")[0].strip().strip("'\"")
      return first
    return s.strip("'\"")
  return val


def resolve_effective_theme(theme: dict[str, Any] | None) -> dict[str, Any]:
  """Resolves effective theme values for all renderers (including custom_tokens maps_to)."""
  raw = copy.deepcopy(DEFAULT_THEME)
  if isinstance(theme, dict):
    for k, v in theme.items():
      if k == "custom_tokens" and isinstance(v, dict):
        raw["custom_tokens"] = copy.deepcopy(v)
      elif v is not None:
        raw[k] = v

  # If `space` was customized and `card_margin`/`item_margin` were not explicitly set,
  # scale default margins to `space / 2` to preserve backward-compatible gap scaling.
  if (
      isinstance(theme, dict)
      and "space" in theme
      and theme.get("space") is not None
  ):
    space_px = coerce_token_value("space", raw.get("space"))
    derived_margin = max(0, int(round(float(space_px) / 2.0)))
    if "card_margin" not in theme or theme.get("card_margin") is None:
      raw["card_margin"] = derived_margin
    if "item_margin" not in theme or theme.get("item_margin") is None:
      raw["item_margin"] = derived_margin

  for bk in BOX_SPACING_TOKENS:
    raw[bk] = coerce_token_value(bk, raw.get(bk))

  # Apply dark scheme defaults for any color role still at its light default
  if raw.get("scheme") == "dark":
    for ck, dark_val in DARK_THEME_COLORS.items():
      user_val = (theme or {}).get(ck)
      if not user_val or user_val == DEFAULT_THEME.get(ck):
        raw[ck] = dark_val

  # Resolve custom_tokens that map onto canonical renderer slots (`maps_to`)
  custom = raw.get("custom_tokens")
  if isinstance(custom, dict):
    for _, entry in custom.items():
      if isinstance(entry, dict):
        maps_to = str(entry.get("maps_to") or "").strip()
        val = entry.get("value")
        if maps_to and val is not None and str(val).strip() != "":
          target = normalize_token_key(maps_to)
          if target in CANONICAL_TOKEN_KEYS:
            raw[target] = coerce_token_value(target, val)

  # Resolve any `var(--a2ui-...)` references inside canonical or custom tokens
  var_re = re.compile(r"^var\(\s*(--[a-zA-Z0-9_-]+)\s*\)$")
  for k in list(CANONICAL_TOKEN_KEYS):
    v = raw.get(k)
    if isinstance(v, str):
      m = var_re.match(v.strip())
      if m:
        ref = normalize_token_key(m.group(1))
        if ref in CANONICAL_TOKEN_KEYS and raw.get(ref) is not None:
          raw[k] = raw[ref]
        elif isinstance(custom, dict) and ref in custom:
          c_entry = custom[ref]
          c_val = c_entry.get("value") if isinstance(c_entry, dict) else c_entry
          if c_val is not None:
            raw[k] = coerce_token_value(k, c_val)

  return raw


def apply_theme_changes(
    current_theme: dict[str, Any] | None,
    theme_changes: list[dict[str, Any]] | dict[str, Any] | None,
    custom_css: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
  """Applies proposed token changes & optional custom_css to a theme dict.

  Returns `(updated_theme, applied_changes_log)`.
  """
  updated = copy.deepcopy(DEFAULT_THEME)
  if isinstance(current_theme, dict):
    for k, v in current_theme.items():
      if k == "custom_tokens" and isinstance(v, dict):
        updated["custom_tokens"] = copy.deepcopy(v)
      else:
        updated[k] = copy.deepcopy(v)

  if not isinstance(updated.get("custom_tokens"), dict):
    updated["custom_tokens"] = {}

  applied: list[dict[str, Any]] = []

  items: list[dict[str, Any]] = []
  if isinstance(theme_changes, dict):
    for k, v in theme_changes.items():
      if isinstance(v, dict) and "value" in v:
        items.append({"token": k, **v})
      else:
        items.append({"token": k, "value": v})
  elif isinstance(theme_changes, list):
    items = [it for it in theme_changes if isinstance(it, dict)]

  for item in items:
    raw_token = str(item.get("token") or item.get("key") or "").strip()
    if not raw_token:
      continue
    raw_val = item.get("value")
    if raw_val is None:
      continue

    norm = normalize_token_key(raw_token)
    is_custom = bool(item.get("is_custom")) or (
        norm not in CANONICAL_TOKEN_KEYS
    )

    if not is_custom and norm in CANONICAL_TOKEN_KEYS:
      before = updated.get(norm)
      after = coerce_token_value(norm, raw_val)
      updated[norm] = after
      applied.append({
          "token": norm,
          "before": before,
          "after": after,
          "is_custom": False,
          "reason": str(item.get("reason") or ""),
      })
    else:
      slug = (
          re.sub(r"[^a-z0-9-]+", "-", norm.lower().replace("_", "-")).strip("-")
          or "custom-token"
      )
      prev_entry = updated["custom_tokens"].get(slug)
      before = (
          prev_entry.get("value")
          if isinstance(prev_entry, dict)
          else prev_entry
      )
      token_type = str(item.get("type") or "").strip().lower()
      if not token_type:
        s_val = str(raw_val).strip()
        if s_val.startswith("#") or s_val.startswith("rgb"):
          token_type = "color"
        elif re.match(
            r"^-?\d+(\.\d+)?(px|dp|rem|em)?(\s+-?\d+(\.\d+)?(px|dp|rem|em)?){0,3}$",
            s_val,
        ):
          token_type = "dimension"
        else:
          token_type = "string"
      maps_to = str(item.get("maps_to") or "").strip()
      if not maps_to and isinstance(prev_entry, dict):
        maps_to = str(prev_entry.get("maps_to") or "").strip()
      updated["custom_tokens"][slug] = {
          "value": str(raw_val).strip(),
          "type": token_type,
          "maps_to": maps_to,
      }
      if maps_to:
        mapped_key = normalize_token_key(maps_to)
        if mapped_key in CANONICAL_TOKEN_KEYS:
          updated[mapped_key] = coerce_token_value(mapped_key, raw_val)
      applied.append({
          "token": f"--a2ui-{slug}",
          "before": before,
          "after": str(raw_val).strip(),
          "is_custom": True,
          "maps_to": maps_to,
          "reason": str(item.get("reason") or ""),
      })

  if custom_css is not None and custom_css != "":
    before_css = updated.get("custom_css", "")
    if custom_css != before_css:
      updated["custom_css"] = str(custom_css)
      applied.append({
          "token": "custom_css",
          "before": before_css,
          "after": str(custom_css),
          "is_custom": True,
          "reason": "Updated custom CSS rules",
      })

  return updated, applied


COMPONENT_STRATEGIES: tuple[str, ...] = (
    "auto",
    "web_template",
    "basic_composition",
)
# `auto` components whose *Basic fit* (0–1: how faithfully the design can be
# rebuilt from Basic Catalog components only; see
# `refs.figma_compiler.compile_node_to_basic_skeleton`) is at least this
# threshold become Basic catalog compositions (templates); below it they become
# custom web components. 0 = always compose from the Basic catalog, 1 = only
# pixel-faithful matches.
BASIC_FIT_THRESHOLD_DEFAULT = 0.7

DEFAULT_SETTINGS: dict[str, Any] = {
    "catalog": {
        "base": "a2ui-basic-v1",
        # Opt-in extension catalogs ("a2ui-designer" = HeroCard, SvgIcon).
        "extensions": [],
        "disabled_components": [],
        "custom_components": {},
        "proposed_components": [],
        "component_strategy": "auto",
        "basic_fit_threshold": BASIC_FIT_THRESHOLD_DEFAULT,
    },
    "theme": DEFAULT_THEME,
    "viewports": ["phone"],
    "renderers": list(DEFAULT_RENDERERS),
    "generator": {
        "model": DEFAULT_TEXT_MODEL,
        "temperature": 0.7,
        "max_repairs": 1,
        "include_guidelines": False,
        "framing": "card",
    },
    "judge": {
        "enabled": True,
        "model": DEFAULT_TEXT_MODEL,
        "use_screenshot": True,
    },
    "imagery": {
        "enabled": True,
        "image_model": DEFAULT_IMAGE_MODEL,
        "svg_model": DEFAULT_SVG_MODEL,
    },
    "data_source": {
        "kind": "",
        "sample_set_id": "",
        "sample_set_name": "",
        "category": "",
        "subcategory": "",
        "sample_ids": [],
        "import_id": "",
        "source_kind": "",
    },
}


def merged(settings: dict[str, Any] | None) -> dict[str, Any]:
  out = copy.deepcopy(DEFAULT_SETTINGS)
  for k, v in (settings or {}).items():
    if k == "theme" and isinstance(v, dict):
      theme_out = copy.deepcopy(DEFAULT_THEME)
      for tk, tv in v.items():
        if tk == "custom_tokens" and isinstance(tv, dict):
          theme_out["custom_tokens"] = copy.deepcopy(tv)
        elif tv is not None:
          theme_out[tk] = copy.deepcopy(tv)
      out["theme"] = theme_out
    elif isinstance(v, dict) and isinstance(out.get(k), dict):
      out[k].update(v)
    else:
      out[k] = v
  if not out.get("renderers"):
    out["renderers"] = list(DEFAULT_RENDERERS)
  cat_cfg = out.get("catalog")
  if isinstance(cat_cfg, dict):
    cs = str(cat_cfg.get("component_strategy") or "auto").strip().lower()
    cat_cfg["component_strategy"] = cs if cs in COMPONENT_STRATEGIES else "auto"
    cat_cfg["basic_fit_threshold"] = _clamp_threshold(
        cat_cfg.get("basic_fit_threshold")
    )
  return out


def _clamp_threshold(raw: Any) -> float:
  try:
    th = float(raw)
  except (TypeError, ValueError):
    return BASIC_FIT_THRESHOLD_DEFAULT
  if th != th:  # NaN
    return BASIC_FIT_THRESHOLD_DEFAULT
  return round(min(1.0, max(0.0, th)), 3)


def basic_fit_threshold(settings: dict[str, Any] | None) -> float:
  """The project's Basic-fit threshold (0–1) used by the `auto` strategy."""
  s = merged(settings)
  return _clamp_threshold((s.get("catalog") or {}).get("basic_fit_threshold"))


def component_strategy_decision(
    settings: dict[str, Any] | None,
    override: str | None = None,
    basic_fit: float | None = None,
) -> dict[str, Any]:
  """Resolves the construction strategy of one custom component.

  Returns `{strategy, auto, threshold, fit, reason}`. `auto` is True when the
  strategy was decided by the project's `auto` rule (Basic fit vs. threshold,
  or the custom-web fallback when no fit estimate exists) rather than pinned by
  the designer (`override`) or by an explicit project-wide strategy.
  """
  ov = str(override or "").strip().lower()
  s = merged(settings)
  threshold = _clamp_threshold(
      (s.get("catalog") or {}).get("basic_fit_threshold")
  )
  fit: float | None
  try:
    fit = (
        None
        if basic_fit is None
        else round(min(1.0, max(0.0, float(basic_fit))), 3)
    )
  except (TypeError, ValueError):
    fit = None
  base = {"threshold": threshold, "fit": fit}
  if ov in ("web_template", "basic_composition"):
    return dict(base, strategy=ov, auto=False, reason="pinned on the component")
  raw = str((s.get("catalog") or {}).get("component_strategy") or "auto")
  raw = raw.strip().lower()
  if raw in ("web_template", "basic_composition"):
    return dict(base, strategy=raw, auto=False, reason="project-wide strategy")
  if fit is not None:
    pct, tpct = round(fit * 100), round(threshold * 100)
    if fit >= threshold:
      return dict(
          base,
          strategy="basic_composition",
          auto=True,
          reason=(
              f"Basic fit {pct}% ≥ {tpct}% threshold → Basic catalog"
              " composition"
          ),
      )
    return dict(
        base,
        strategy="web_template",
        auto=True,
        reason=f"Basic fit {pct}% < {tpct}% threshold → custom web component",
    )
  return dict(
      base,
      strategy="web_template",
      auto=True,
      reason="no Basic-fit estimate → custom web component",
  )


def resolve_component_strategy(
    settings: dict[str, Any] | None,
    override: str | None = None,
    basic_fit: float | None = None,
) -> str:
  """Resolves the active component construction strategy (`web_template` or `basic_composition`).

  With `component_strategy: "auto"`, a known `basic_fit` (0–1) is compared with
  `settings.catalog.basic_fit_threshold`; without one the project falls back to
  `web_template`.
  """
  return component_strategy_decision(
      settings, override=override, basic_fit=basic_fit
  )["strategy"]


def normalize_theme(theme: dict[str, Any] | None) -> dict[str, Any]:
  """Returns a canonical normalized theme dictionary for hashing & diffing."""
  raw = merged({"theme": theme or {}})["theme"]
  out: dict[str, Any] = {}
  for k in sorted(CANONICAL_TOKEN_KEYS):
    out[k] = coerce_token_value(k, raw.get(k))
  custom_out: dict[str, dict[str, str]] = {}
  raw_custom = raw.get("custom_tokens")
  if isinstance(raw_custom, dict):
    for ck in sorted(raw_custom.keys()):
      slug = (
          re.sub(
              r"[^a-z0-9-]+",
              "-",
              str(ck).lower().removeprefix("--a2ui-").replace("_", "-"),
          ).strip("-")
          or "custom-token"
      )
      entry = raw_custom[ck]
      if isinstance(entry, dict):
        val = str(entry.get("value") or "").strip()
        ttype = str(entry.get("type") or "string").strip().lower()
        maps_to = str(entry.get("maps_to") or "").strip()
      else:
        val = str(entry if entry is not None else "").strip()
        ttype = "string"
        maps_to = ""
      custom_out[slug] = {
          "value": val,
          "type": ttype,
          "maps_to": maps_to,
      }
  out["custom_tokens"] = custom_out
  out["custom_css"] = str(raw.get("custom_css") or "").strip()
  return out


def compute_theme_hash(theme: dict[str, Any] | None) -> str:
  """Deterministic 12-character sha256 hex digest of a normalized theme."""
  norm = normalize_theme(theme)
  payload = json.dumps(norm, sort_keys=True, separators=(",", ":"))
  return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def compute_theme_diff(
    old_theme: dict[str, Any] | None,
    new_theme: dict[str, Any] | None,
) -> dict[str, Any]:
  """Computes structured token-level diff between two themes."""
  old_norm = normalize_theme(old_theme)
  new_norm = normalize_theme(new_theme)
  changes: list[dict[str, Any]] = []
  changed_css_vars: set[str] = set()

  for k in sorted(CANONICAL_TOKEN_KEYS):
    before = old_norm.get(k)
    after = new_norm.get(k)
    if before != after:
      css_var = TOKEN_TO_CSS_VAR.get(k, f"--a2ui-{k.replace('_', '-')}")
      changes.append({
          "token": k,
          "css_var": css_var,
          "before": before,
          "after": after,
          "is_custom": False,
      })
      for vname in TOKEN_TO_ALL_CSS_VARS.get(k, [css_var]):
        changed_css_vars.add(vname)

  old_custom = old_norm.get("custom_tokens") or {}
  new_custom = new_norm.get("custom_tokens") or {}
  for slug in sorted(set(old_custom.keys()) | set(new_custom.keys())):
    b_entry = old_custom.get(slug)
    a_entry = new_custom.get(slug)
    b_val = b_entry.get("value") if isinstance(b_entry, dict) else None
    a_val = a_entry.get("value") if isinstance(a_entry, dict) else None
    b_maps = b_entry.get("maps_to", "") if isinstance(b_entry, dict) else ""
    a_maps = a_entry.get("maps_to", "") if isinstance(a_entry, dict) else ""
    if b_val != a_val or b_maps != a_maps:
      css_var = f"--a2ui-{slug}"
      changes.append({
          "token": css_var,
          "css_var": css_var,
          "before": b_val,
          "after": a_val,
          "is_custom": True,
          "maps_to": a_maps or b_maps,
      })
      changed_css_vars.add(css_var)
      for mapped in (b_maps, a_maps):
        if mapped:
          mk = normalize_token_key(mapped)
          for vname in TOKEN_TO_ALL_CSS_VARS.get(mk, []):
            changed_css_vars.add(vname)

  old_css = old_norm.get("custom_css") or ""
  new_css = new_norm.get("custom_css") or ""
  custom_css_changed = old_css != new_css

  parts: list[str] = []
  if changes:
    names = [
        c["token"] if not c["is_custom"] else c["css_var"] for c in changes[:4]
    ]
    more = f" +{len(changes) - 4} more" if len(changes) > 4 else ""
    parts.append(f"Updated {', '.join(names)}{more}")
  if custom_css_changed:
    parts.append("updated custom CSS")
  summary = "; ".join(parts) if parts else "No token changes"

  return {
      "changes": changes,
      "changed": changes,
      "custom_css_changed": custom_css_changed,
      "changed_css_vars": sorted(changed_css_vars),
      "summary": summary,
  }


_CSS_VAR_REF_RE = re.compile(r"--a2ui-([a-zA-Z0-9_-]+)")
_CSS_VAR_CALL_RE = re.compile(r"var\(\s*(--a2ui-[a-zA-Z0-9_-]+)")
_CSS_VAR_DECL_RE = re.compile(r"(--a2ui-[a-zA-Z0-9_-]+)\s*:\s*([^;}\n]+)")


def extract_css_var_usages(
    texts: list[str] | tuple[str, ...] | None,
) -> tuple[list[str], dict[str, str]]:
  """Extracts ordered `--a2ui-*` variable names and `var(--a2ui-*, fallback)` values from texts."""
  ordered: list[str] = []
  seen: set[str] = set()
  fallbacks: dict[str, str] = {}

  def _norm_var(raw_var: str) -> str:
    slug = re.sub(
        r"[^a-z0-9-]+",
        "-",
        raw_var.lower().removeprefix("--a2ui-").replace("_", "-"),
    ).strip("-")
    return f"--a2ui-{slug}" if slug else ""

  for raw_text in texts or []:
    if not isinstance(raw_text, str) or "--a2ui-" not in raw_text:
      continue
    for m in _CSS_VAR_CALL_RE.finditer(raw_text):
      vname = _norm_var(m.group(1))
      if not vname:
        continue
      if vname not in seen:
        seen.add(vname)
        ordered.append(vname)
      pos = m.end()
      n = len(raw_text)
      while pos < n and raw_text[pos].isspace():
        pos += 1
      if pos < n and raw_text[pos] == ",":
        pos += 1
        fb_start = pos
        depth = 1
        while pos < n and depth > 0:
          ch = raw_text[pos]
          if ch == "(":
            depth += 1
          elif ch == ")":
            depth -= 1
            if depth == 0:
              break
          pos += 1
        if depth == 0:
          fb = raw_text[fb_start:pos].strip()
          if fb and vname not in fallbacks:
            fallbacks[vname] = fb
    for m in _CSS_VAR_DECL_RE.finditer(raw_text):
      vname = _norm_var(m.group(1))
      val = m.group(2).strip()
      if (
          vname
          and val
          and not val.startswith("var(")
          and vname not in fallbacks
      ):
        fallbacks[vname] = val
    for m in _CSS_VAR_REF_RE.finditer(raw_text):
      vname = _norm_var(f"--a2ui-{m.group(1)}")
      if vname and vname not in seen:
        seen.add(vname)
        ordered.append(vname)
  return ordered, fallbacks


def is_token_available(
    theme: dict[str, Any] | None, token_or_css_var: str
) -> bool:
  """Returns True if `token_or_css_var` is defined in canonical tokens or `theme.custom_tokens`."""
  raw = str(token_or_css_var or "").strip()
  if not raw:
    return True
  norm_key = normalize_token_key(raw)
  css_var = (
      raw.lower()
      if raw.startswith("--")
      else f"--a2ui-{raw.lower().replace('_', '-')}"
  )
  if (
      norm_key in CANONICAL_TOKEN_KEYS
      or css_var in CSS_VAR_TO_TOKEN
      or css_var in TOKEN_TO_CSS_VAR.values()
  ):
    return True
  slug = re.sub(r"[^a-z0-9-]+", "-", norm_key.lower().replace("_", "-")).strip(
      "-"
  )
  if not slug:
    return True
  custom = (theme or {}).get("custom_tokens") if isinstance(theme, dict) else {}
  if isinstance(custom, dict) and slug in custom:
    entry = custom[slug]
    val = entry.get("value") if isinstance(entry, dict) else entry
    return val is not None and str(val).strip() != ""
  return False


def _infer_custom_token_fallback_value(
    slug: str, eff_theme: dict[str, Any]
) -> str:
  """Synthesizes a sensible default value from `eff_theme` for an undefined custom token slug."""
  s = slug.lower()
  if "shadow" in s:
    return "0 2px 8px rgba(0, 0, 0, 0.06)"
  if "radius" in s or "corner" in s:
    return f"{eff_theme.get('radius_card', 12)}px"
  if "font" in s:
    if "mono" in s or "code" in s:
      return str(eff_theme.get("font_family_mono") or "Roboto Mono")
    if "head" in s or "display" in s or "title" in s:
      return str(eff_theme.get("font_family_heading") or "Inter")
    return str(eff_theme.get("font_family") or "Inter")
  if s.endswith(("-fg", "-text", "-ink", "-icon", "-label", "-on")):
    if "primary" in s:
      return str(
          eff_theme.get("on_primary_container")
          or eff_theme.get("on_surface")
          or "#1B1B1F"
      )
    if "tertiary" in s or "deposit" in s:
      return str(
          eff_theme.get("on_tertiary_container")
          or eff_theme.get("on_surface")
          or "#1B1B1F"
      )
    if "error" in s or "danger" in s or "alert" in s:
      return str(eff_theme.get("error") or "#B3261E")
    if "muted" in s or "sub" in s or "variant" in s:
      return str(eff_theme.get("on_surface_variant") or "#444746")
    return str(eff_theme.get("on_surface") or "#1F1F1F")
  if s.endswith(("-border", "-outline", "-divider", "-stroke", "-rule")):
    return str(
        eff_theme.get("outline_variant")
        or eff_theme.get("outline")
        or "#C4C7C5"
    )
  if s.endswith((
      "-bg",
      "-background",
      "-surface",
      "-container",
      "-tint",
      "-halo",
      "-fill",
      "-pill",
      "-track",
      "-tile",
      "-card",
      "-banner",
  )):
    if "primary" in s:
      return str(eff_theme.get("primary_container") or "#D3E3FD")
    if "tertiary" in s or "deposit" in s or "halo" in s:
      return str(eff_theme.get("tertiary_container") or "#C4EED0")
    if "error" in s or "danger" in s or "alert" in s:
      return str(eff_theme.get("error_container") or "#F9DEDC")
    if "secondary" in s or "badge" in s or "chip" in s or "eco" in s:
      return str(eff_theme.get("secondary_container") or "#C2E7FF")
    return str(eff_theme.get("surface_container") or "#F3F6FC")
  if "tertiary" in s:
    return str(eff_theme.get("tertiary") or "#146C2E")
  if "secondary" in s:
    return str(eff_theme.get("secondary") or "#00639B")
  if "error" in s or "danger" in s:
    return str(eff_theme.get("error") or "#B3261E")
  return str(eff_theme.get("primary") or "#0B57D0")


def resolve_missing_tokens_from_material(
    current_theme: dict[str, Any] | None,
    *,
    token_refs: list[str] | tuple[str, ...] | set[str] | None = None,
    texts: list[str] | tuple[str, ...] | None = None,
    token_suggestions: list[dict[str, Any]] | None = None,
    source_facts: dict[str, Any] | None = None,
    sources: list[str] | None = None,
    reason_prefix: str = (
        "Brought in missing token read by imported reference material"
    ),
) -> list[dict[str, Any]]:
  """Finds any tokens read or suggested by imported material that are not available in `current_theme` and synthesizes token definitions for them."""
  eff_theme = resolve_effective_theme(current_theme)
  text_vars, css_fallbacks = extract_css_var_usages(list(texts or []))

  def _to_slug(raw_tok: str) -> str:
    norm = normalize_token_key(raw_tok)
    return re.sub(r"[^a-z0-9-]+", "-", norm.lower().replace("_", "-")).strip(
        "-"
    )

  suggestions_by_slug: dict[str, dict[str, Any]] = {}
  for s in token_suggestions or []:
    if not isinstance(s, dict):
      continue
    raw_tok = str(s.get("token") or s.get("key") or "").strip()
    if not raw_tok:
      continue
    slug = _to_slug(raw_tok)
    if slug and slug not in suggestions_by_slug:
      suggestions_by_slug[slug] = s

  fact_values: dict[str, str] = {}
  if isinstance(source_facts, dict):
    tw = source_facts.get("tailwind")
    tw_colors = tw.get("colors") if isinstance(tw, dict) else {}
    if isinstance(tw_colors, dict):
      for k, v in tw_colors.items():
        if isinstance(v, str) and v.strip():
          fact_values[_to_slug(str(k))] = v.strip()
        elif isinstance(v, dict):
          for subk, subv in v.items():
            if isinstance(subv, str) and subv.strip():
              combo = (
                  _to_slug(str(k))
                  if str(subk).lower() == "default"
                  else _to_slug(f"{k}-{subk}")
              )
              fact_values[combo] = subv.strip()
    for st in source_facts.get("styles") or []:
      if isinstance(st, dict) and st.get("name") and st.get("hex"):
        fact_values[_to_slug(str(st["name"]))] = str(st["hex"]).strip()

  ordered_slugs: list[str] = []
  seen_slugs: set[str] = set()

  def _consider(raw_tok: str) -> None:
    if not raw_tok or is_token_available(current_theme, raw_tok):
      return
    slug = _to_slug(raw_tok)
    if not slug or slug in seen_slugs:
      return
    if is_token_available(current_theme, f"--a2ui-{slug}"):
      return
    seen_slugs.add(slug)
    ordered_slugs.append(slug)

  for ref in token_refs or []:
    _consider(str(ref or ""))
  for vname in text_vars:
    _consider(vname)
  for s in token_suggestions or []:
    if isinstance(s, dict):
      _consider(str(s.get("token") or s.get("key") or ""))

  def _resolve_css_fallback(fb: str, depth: int = 0) -> str:
    fb = fb.strip()
    if not fb or depth > 4:
      return ""
    if fb.startswith("var("):
      inner_vars, inner_fbs = extract_css_var_usages([fb])
      if inner_vars:
        iv = inner_vars[0]
        if iv in inner_fbs and inner_fbs[iv]:
          res = _resolve_css_fallback(inner_fbs[iv], depth + 1)
          if res:
            return res
        iv_norm = normalize_token_key(iv)
        if (
            iv_norm in CANONICAL_TOKEN_KEYS
            and eff_theme.get(iv_norm) is not None
        ):
          return str(eff_theme[iv_norm])
        iv_slug = _to_slug(iv)
        if iv_slug in suggestions_by_slug:
          sval = suggestions_by_slug[iv_slug].get("value")
          if sval is not None and str(sval).strip():
            return str(sval).strip()
      return ""
    return fb

  out: list[dict[str, Any]] = []
  for slug in ordered_slugs:
    vname = f"--a2ui-{slug}"
    sug = suggestions_by_slug.get(slug)
    sug_val = (
        str(sug.get("value")).strip()
        if isinstance(sug, dict) and sug.get("value") is not None
        else ""
    )
    fb_val = _resolve_css_fallback(css_fallbacks.get(vname, ""))
    if sug_val:
      val = sug_val
      confidence = str(sug.get("confidence") or "exact")
      reason = str(sug.get("reason") or f"{reason_prefix} ({vname})")
      src_list = list(sug.get("sources") or sources or [])
    elif fb_val:
      val = fb_val
      confidence = "exact"
      reason = f"{reason_prefix} (from CSS fallback for {vname})"
      src_list = list(sources or [])
    elif slug in fact_values:
      val = fact_values[slug]
      confidence = "approx"
      reason = f"{reason_prefix} (from source design facts for {vname})"
      src_list = list(sources or [])
    else:
      val = _infer_custom_token_fallback_value(slug, eff_theme)
      confidence = "approx"
      reason = f"{reason_prefix} ({vname})"
      src_list = list(sources or [])

    item: dict[str, Any] = {
        "token": slug,
        "value": val,
        "is_custom": True,
        "maps_to": "",
        "confidence": confidence,
        "reason": reason,
    }
    if src_list:
      item["sources"] = src_list
    out.append(item)
  return out


def _facts_have_reference_colors(facts: dict[str, Any] | None) -> bool:
  """Returns True if `facts` contains Stitch `design_system` key colors or extracted `colors`."""
  if not isinstance(facts, dict):
    return False
  ds = facts.get("design_system")
  if isinstance(ds, dict):
    for k in ("primary", "seed_color", "secondary", "tertiary", "neutral"):
      if mcu.is_valid_hex(ds.get(k)):
        return True
  for c in facts.get("colors") or []:
    if isinstance(c, dict) and mcu.is_valid_hex(c.get("hex")):
      return True
  return False


def expand_complementary_color_tokens(
    raw_tokens: list[dict[str, Any]] | None,
    *,
    facts: dict[str, Any] | None = None,
    current_theme: dict[str, Any] | None = None,
    sources: list[str] | None = None,
    include_facts_when_no_color_tokens: bool = False,
) -> list[dict[str, Any]]:
  """Computes complementary Material 3 colors for any unspecified canonical `COLOR_TOKENS`.

  Adapts the key colors provided in `raw_tokens` and/or `facts`
  (`design_system`,
  `colors`) using `core.material_color_utilities`. Explicitly provided color
  tokens
  are preserved first in order, followed by computed complementary tokens.
  """
  out: list[dict[str, Any]] = []
  explicit_colors: dict[str, str] = {}
  direct_canonical_seen: set[str] = set()
  inferred_sources: list[str] = list(sources or [])

  for item in raw_tokens or []:
    if not isinstance(item, dict):
      continue
    if item.get("complementary"):
      continue
    raw_key = str(item.get("token") or item.get("key") or "").strip()
    if not raw_key:
      continue
    out.append(dict(item))
    if not inferred_sources and item.get("sources"):
      inferred_sources = list(item.get("sources") or [])

    norm = normalize_token_key(raw_key)
    is_custom = bool(item.get("is_custom")) or (
        norm not in CANONICAL_TOKEN_KEYS
    )
    raw_val = item.get("value")
    if not is_custom:
      if norm in COLOR_TOKENS and mcu.is_valid_hex(raw_val):
        if norm not in explicit_colors:
          explicit_colors[norm] = str(raw_val).strip()
        direct_canonical_seen.add(norm)
      elif norm == "scheme" and str(raw_val or "").strip().lower() in (
          "light",
          "dark",
      ):
        explicit_colors["scheme"] = str(raw_val).strip().lower()
        direct_canonical_seen.add("scheme")
    else:
      maps_to = str(item.get("maps_to") or "").strip()
      if maps_to and mcu.is_valid_hex(raw_val):
        mapped_norm = normalize_token_key(maps_to)
        if mapped_norm in COLOR_TOKENS and mapped_norm not in explicit_colors:
          explicit_colors[mapped_norm] = str(raw_val).strip()

  # If all 22 canonical color tokens are already explicitly specified, nothing to compute.
  if COLOR_TOKENS.issubset(direct_canonical_seen):
    return out

  has_explicit_color = any(k in COLOR_TOKENS for k in explicit_colors)
  has_fact_color = _facts_have_reference_colors(facts)

  if not has_explicit_color and not (
      include_facts_when_no_color_tokens and has_fact_color
  ):
    return out

  # If the only explicit colors match DEFAULT_THEME verbatim and there are no
  # reference facts with distinct colors, check if any explicit color differs
  # from DEFAULT_THEME before synthesizing 21 complementary tokens.
  if has_explicit_color and not has_fact_color:
    base_theme = (
        current_theme if isinstance(current_theme, dict) else DEFAULT_THEME
    )
    any_differs = False
    for ck, cval in explicit_colors.items():
      if ck in COLOR_TOKENS:
        cur_hex = str(base_theme.get(ck) or DEFAULT_THEME.get(ck) or "").strip()
        if cval.upper() != cur_hex.upper():
          any_differs = True
          break
      elif ck == "scheme":
        if cval != str(base_theme.get("scheme") or "light").strip().lower():
          any_differs = True
          break
    if not any_differs:
      return out

  m3_colors = mcu.compute_m3_color_tokens(
      explicit_colors,
      facts=facts,
      current_theme=current_theme,
  )

  key_parts: list[str] = []
  for role in ("primary", "secondary", "tertiary", "surface", "error"):
    if role in explicit_colors:
      key_parts.append(f"{role}={explicit_colors[role]}")
  if not key_parts and isinstance(facts, dict):
    ds = facts.get("design_system")
    if isinstance(ds, dict):
      for k in ("primary", "seed_color", "secondary", "tertiary", "neutral"):
        if mcu.is_valid_hex(ds.get(k)):
          key_parts.append(f"{k}={ds[k]}")
  if not key_parts:
    key_parts.append(f"primary={m3_colors.get('primary')}")
  key_summary = ", ".join(key_parts[:4])
  reason = (
      "Computed complementary Material 3 color from reference palette"
      f" ({key_summary}) via material-color-utilities"
  )

  base_scheme = (
      str((current_theme or DEFAULT_THEME).get("scheme") or "light")
      .strip()
      .lower()
  )
  if (
      "scheme" not in direct_canonical_seen
      and m3_colors.get("scheme") in ("light", "dark")
      and m3_colors["scheme"] != base_scheme
  ):
    scheme_item: dict[str, Any] = {
        "token": "scheme",
        "value": m3_colors["scheme"],
        "is_custom": False,
        "maps_to": "",
        "confidence": "approx",
        "reason": reason,
        "complementary": True,
    }
    if inferred_sources:
      scheme_item["sources"] = list(inferred_sources)
    out.append(scheme_item)

  for ck in ORDERED_COLOR_TOKENS:
    if ck in direct_canonical_seen:
      continue
    c_val = m3_colors.get(ck)
    if not c_val:
      continue
    comp_item: dict[str, Any] = {
        "token": ck,
        "value": c_val,
        "is_custom": False,
        "maps_to": "",
        "confidence": "approx",
        "reason": reason,
        "complementary": True,
    }
    if inferred_sources:
      comp_item["sources"] = list(inferred_sources)
    out.append(comp_item)

  return out
