"""Generated raster imagery & SVG icon materialization with multimodal style references."""

from __future__ import annotations

from concurrent import futures
import hashlib
import html
import os
import re
from typing import Any
import urllib.parse

from core import imagery as imagery_mod
from core import settings as settings_mod
from core.storage import Store
from llm.gemini import Image, Llm, LlmError
from render import placeholders
from sim import common
from sim.common import obj, str_schema

SVG_SCHEMA = obj({
    "svg": str_schema(
        "Complete self-contained <svg xmlns='http://www.w3.org/2000/svg'"
        " ...>...</svg> markup."
    ),
})

GEN_IMG_PREFIX = "/gen/img"
GEN_SVG_PREFIX = "/gen/svg"

_UNSAFE_SVG_RE = re.compile(
    r"<\s*(script|foreignObject|iframe|object|embed)\b[^>]*>.*?<\s*/\s*\1\s*>|"
    r"<\s*(script|foreignObject|iframe|object|embed)\b[^>]*/?\s*>|"
    r"\bon[a-z]+\s*=\s*(['\"]).*?\3",
    re.IGNORECASE | re.DOTALL,
)


def is_gen_url(val: Any) -> bool:
  if not isinstance(val, str):
    return False
  s = val.strip()
  return s.startswith(GEN_IMG_PREFIX) or s.startswith(GEN_SVG_PREFIX)


def sanitize_svg(raw_svg: str, width: int = 64, height: int = 64) -> str:
  """Extracts and sanitizes an <svg> element from LLM output."""
  text = (raw_svg or "").strip()
  m = re.search(r"<svg\b.*?</svg>", text, re.IGNORECASE | re.DOTALL)
  if not m:
    return ""
  svg = _UNSAFE_SVG_RE.sub("", m.group(0))
  if "xmlns=" not in svg[:200]:
    svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
  if "viewBox=" not in svg[:240] and "viewbox=" not in svg[:240]:
    svg = svg.replace("<svg", f'<svg viewBox="0 0 {width} {height}"', 1)
  return svg


def fallback_icon_svg(prompt: str, style_text: str = "", size: int = 64) -> str:
  """Deterministic styled geometric vector icon used when LLM is offline."""
  seed = f"{prompt}|{style_text}"
  h = int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16)
  palettes = [
      ("#0B57D0", "#D3E3FD", "#041E49"),
      ("#00639B", "#C2E7FF", "#001D35"),
      ("#146C2E", "#C4EED0", "#072711"),
      ("#7D5260", "#FFD8E4", "#31111D"),
      ("#6750A4", "#EADDFF", "#21005D"),
      ("#B3261E", "#F9DEDC", "#410E0B"),
      ("#006A6A", "#9CF1F0", "#002020"),
      ("#8B5000", "#FFDCBE", "#2C1600"),
  ]
  # Check if style_text mentions a hex color
  hex_matches = re.findall(r"#[0-9a-fA-F]{6}\b", style_text or "")
  primary, bg, accent = palettes[h % len(palettes)]
  if len(hex_matches) >= 2:
    primary, bg = hex_matches[0], hex_matches[1]
    accent = hex_matches[2] if len(hex_matches) >= 3 else primary
  elif len(hex_matches) == 1:
    primary = hex_matches[0]

  shape_kind = h % 4
  if shape_kind == 0:
    motif = (
        f'<circle cx="32" cy="26" r="11" fill="{primary}"/>'
        f'<path d="M18 46c3-8 10-12 14-12s11 4 14 12" stroke="{accent}" '
        'stroke-width="4" stroke-linecap="round" fill="none"/>'
    )
  elif shape_kind == 1:
    motif = (
        f'<rect x="16" y="16" width="32" height="32" rx="8" fill="{primary}"'
        f' fill-opacity="0.18"/><path d="M22 33l7 7 13-15" stroke="{primary}"'
        ' stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"'
        ' fill="none"/>'
    )
  elif shape_kind == 2:
    motif = (
        f'<polygon points="32,14 48,44 16,44" fill="{primary}"'
        f' fill-opacity="0.88"/><circle cx="32" cy="36" r="5" fill="{bg}"/>'
    )
  else:
    motif = (
        f'<circle cx="26" cy="28" r="10" fill="{primary}"/><path d="M24 38h20a8'
        f' 8 0 0 0 0-16 10 10 0 0 0-19-3" fill="{accent}" fill-opacity="0.85"/>'
    )
  return (
      f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
      'viewBox="0 0 64 64">'
      f'<rect x="4" y="4" width="56" height="56" rx="16" fill="{bg}"/>'
      f"{motif}</svg>"
  )


def fallback_raster_svg(
    prompt: str, style_text: str = "", width: int = 800, height: int = 450
) -> str:
  """Deterministic atmospheric SVG backdrop when image model is unavailable."""
  seed = f"{prompt}|{style_text}"
  h = int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16)
  hex_matches = re.findall(r"#[0-9a-fA-F]{6}\b", style_text or "")
  top, bottom = placeholders.PALETTES[h % len(placeholders.PALETTES)]
  if len(hex_matches) >= 2:
    top, bottom = hex_matches[0], hex_matches[1]
  elif len(hex_matches) == 1:
    top = hex_matches[0]
  label = html.escape((prompt or "generated image")[:56])
  cx1, cy1 = 20 + (h >> 8) % 60, 20 + (h >> 16) % 50
  cx2, cy2 = 25 + (h >> 24) % 55, 30 + (h >> 32) % 50
  return (
      f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}"'
      f' height="{height}" viewBox="0 0 {width} {height}"'
      ' preserveAspectRatio="xMidYMid slice"><defs><linearGradient id="bg"'
      ' x1="0" y1="0" x2="1" y2="1"><stop offset="0%"'
      f' stop-color="{top}"/><stop offset="100%"'
      f' stop-color="{bottom}"/></linearGradient></defs><rect width="100%"'
      f' height="100%" fill="url(#bg)"/><circle cx="{cx1}%" cy="{cy1}%"'
      f' r="{min(width, height) * 0.42:.0f}" fill="#ffffff"'
      f' fill-opacity="0.16"/><circle cx="{cx2}%" cy="{cy2}%"'
      f' r="{min(width, height) * 0.28:.0f}" fill="#000000"'
      f' fill-opacity="0.12"/><path d="M0,{int(height * 0.72)}'
      f" Q{int(width * 0.35)},{int(height * 0.56)}"
      f" {int(width * 0.7)},{int(height * 0.7)} T{width},{int(height * 0.65)}"
      f' L{width},{height} L0,{height} Z" fill="#000000"'
      f' fill-opacity="0.14"/><text x="24" y="{height - 20}"'
      ' font-family="Google Sans, sans-serif" font-size="13" fill="#ffffff"'
      f' fill-opacity="0.65">{label}</text></svg>'
  )


def _cache_key(
    kind: str,
    prompt: str,
    style_slug: str,
    style_text: str,
    aspect_ratio: str,
    ref_files: list[str],
    model: str,
) -> str:
  raw = json.dumps(
      {
          "k": kind,
          "p": prompt.strip(),
          "s": style_slug,
          "st": style_text.strip(),
          "ar": aspect_ratio,
          "refs": ref_files,
          "m": model,
      },
      sort_keys=True,
  )
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


import json  # noqa: E402


def resolve_gen_url(
    url: str,
    *,
    store: Store,
    llm: Llm | None,
    project_id: str | None = None,
    styles_by_slug: dict[str, dict[str, Any]] | None = None,
    image_model: str | None = None,
    svg_model: str | None = None,
) -> tuple[str, dict[str, Any]]:
  """Materializes a `/gen/img?...` or `/gen/svg?...` URL into `/api/gen/<file>`."""
  parsed = urllib.parse.urlparse(url.strip())
  qs = urllib.parse.parse_qs(parsed.query)
  prompt = (qs.get("prompt") or qs.get("q") or ["visual"])[0].strip()
  style_slug = (
      imagery_mod.normalize_slug((qs.get("style") or [""])[0], fallback_name="")
      if (qs.get("style") or [""])[0]
      else ""
  )
  inline_style = (qs.get("style_text") or [""])[0].strip()
  pid = project_id or (qs.get("project_id") or [""])[0].strip() or None

  style_obj: dict[str, Any] | None = None
  if style_slug:
    if styles_by_slug and style_slug in styles_by_slug:
      style_obj = styles_by_slug[style_slug]
    elif pid:
      style_obj = imagery_mod.get_style(store, pid, style_slug)

  style_text = (
      inline_style or (style_obj.get("style_text") if style_obj else "") or ""
  )
  ref_imgs: list[Image] = (
      imagery_mod.load_reference_images(store, style_obj) if style_obj else []
  )
  ref_files = [
      str(r.get("file") or "")
      for r in (style_obj or {}).get("reference_images") or []
      if isinstance(r, dict)
  ]

  is_svg = parsed.path.startswith(GEN_SVG_PREFIX) or (
      style_obj is not None
      and imagery_mod.is_svg_medium(style_obj.get("medium"))
  )
  if is_svg:
    eff_model = svg_model or settings_mod.DEFAULT_SVG_MODEL
    w = int((qs.get("w") or [64])[0] or 64)
    h = int((qs.get("h") or [64])[0] or 64)
    key = _cache_key(
        "svg", prompt, style_slug, style_text, f"{w}x{h}", ref_files, eff_model
    )
    fname = f"{key}.svg"
    fpath = store.file_path("gen", fname)
    if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
      return f"/api/gen/{fname}", {
          "cached": True,
          "medium": "svg",
          "model": eff_model,
      }

    svg_str = ""
    used_model = "fallback-svg"
    if llm is not None:
      prompt_lines = [
          f"Subject to depict as an SVG icon/graphic: {prompt}",
          f"Target dimensions: {w}x{h} (viewBox 0 0 64 64).",
      ]
      if style_obj and style_obj.get("name"):
        prompt_lines.append(f"Style preset: {style_obj['name']} ({style_slug})")
      if style_text:
        prompt_lines.append(f"Visual style instructions: {style_text}")
      if ref_imgs:
        prompt_lines.append(
            "Match the visual language, stroke weight, palette, and form factor"
            " of the attached style reference image(s)."
        )
      parts: list[Any] = list(ref_imgs) + ["\n".join(prompt_lines)]
      try:
        resp = llm.generate(
            purpose="generate_svg",
            system=common.prompt("generate_svg.md"),
            parts=parts,
            json_schema=SVG_SCHEMA,
            temperature=0.4,
            model=eff_model,
            timeout_s=60.0,
        )
        raw_svg = (
            (resp.data or {}).get("svg")
            if isinstance(resp.data, dict)
            else resp.text
        )
        svg_str = sanitize_svg(str(raw_svg or ""), width=w, height=h)
        if svg_str:
          used_model = resp.model
      except LlmError:
        svg_str = ""

    if not svg_str:
      svg_str = fallback_icon_svg(prompt, style_text, size=max(w, h))
    with open(fpath, "w", encoding="utf-8") as fh:
      fh.write(svg_str)
    return f"/api/gen/{fname}", {
        "cached": False,
        "medium": "svg",
        "model": used_model,
    }

  # Raster generation
  eff_model = image_model or settings_mod.DEFAULT_IMAGE_MODEL
  aspect = (
      (qs.get("aspect") or [""])[0].strip()
      or (style_obj.get("aspect_ratio") if style_obj else "")
      or "16:9"
  )
  if aspect not in imagery_mod.ASPECT_RATIOS:
    aspect = "16:9"
  w = int((qs.get("w") or [800])[0] or 800)
  h = int((qs.get("h") or [450])[0] or 450)
  key = _cache_key(
      "img", prompt, style_slug, style_text, aspect, ref_files, eff_model
  )
  png_name = f"{key}.png"
  png_path = store.file_path("gen", png_name)
  svg_fallback_name = f"{key}.svg"
  svg_fallback_path = store.file_path("gen", svg_fallback_name)
  if os.path.isfile(png_path) and os.path.getsize(png_path) > 0:
    return f"/api/gen/{png_name}", {
        "cached": True,
        "medium": "raster",
        "model": eff_model,
    }

  if llm is not None and hasattr(llm, "generate_image"):
    prompt_parts = [
        f"Create a clean, text-free visual illustration/backdrop of: {prompt}.",
        (
            "Do NOT render any words, numbers, UI chrome, or typography inside"
            " the image."
        ),
    ]
    if style_text:
      prompt_parts.append(f"Visual style direction: {style_text}")
    if ref_imgs:
      prompt_parts.append(
          "Follow the color palette, brushwork, lighting, texture, and artistic"
          " style of the attached reference image(s)."
      )
    full_prompt = "\n".join(prompt_parts)
    try:
      img_resp = llm.generate_image(
          prompt=full_prompt,
          aspect_ratio=aspect,
          reference_images=ref_imgs,
          model=eff_model,
      )
      if img_resp and img_resp.data:
        with open(png_path, "wb") as fh:
          fh.write(img_resp.data)
        return (
            f"/api/gen/{png_name}",
            {"cached": False, "medium": "raster", "model": img_resp.model},
        )
    except LlmError:
      pass

  # Fallback deterministic atmospheric SVG when offline / no image handler
  svg_str = fallback_raster_svg(prompt, style_text, width=w, height=h)
  with open(svg_fallback_path, "w", encoding="utf-8") as fh:
    fh.write(svg_str)
  return (
      f"/api/gen/{svg_fallback_name}",
      {"cached": False, "medium": "raster", "model": "fallback-svg"},
  )


def _collect_gen_urls(node: Any, out: set[str]) -> None:
  if isinstance(node, str):
    if is_gen_url(node):
      out.add(node.strip())
  elif isinstance(node, dict):
    for v in node.values():
      _collect_gen_urls(v, out)
  elif isinstance(node, list):
    for item in node:
      _collect_gen_urls(item, out)


def _rewrite_gen_urls(node: Any, mapping: dict[str, str]) -> Any:
  if isinstance(node, str):
    s = node.strip()
    return mapping.get(s, node)
  if isinstance(node, dict):
    return {k: _rewrite_gen_urls(v, mapping) for k, v in node.items()}
  if isinstance(node, list):
    return [_rewrite_gen_urls(item, mapping) for item in node]
  return node


def materialize_messages(
    messages: list[dict[str, Any]],
    *,
    store: Store,
    llm: Llm | None,
    project_id: str | None = None,
    styles: list[dict[str, Any]] | None = None,
    image_model: str | None = None,
    svg_model: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
  """Finds all `/gen/img?...` and `/gen/svg?...` URLs in A2UI messages and materializes them."""
  urls: set[str] = set()
  _collect_gen_urls(messages, urls)
  if not urls:
    return messages, []

  style_list = (
      styles
      if styles is not None
      else (imagery_mod.list_styles(store, project_id) if project_id else [])
  )
  styles_by_slug = {
      str(s.get("slug")): s
      for s in style_list or []
      if isinstance(s, dict) and s.get("slug")
  }

  mapping: dict[str, str] = {}
  usage_log: list[dict[str, Any]] = []

  def _one(u: str) -> tuple[str, str, dict[str, Any]]:
    resolved, meta = resolve_gen_url(
        u,
        store=store,
        llm=llm,
        project_id=project_id,
        styles_by_slug=styles_by_slug,
        image_model=image_model,
        svg_model=svg_model,
    )
    return u, resolved, meta

  with futures.ThreadPoolExecutor(max_workers=min(4, len(urls))) as ex:
    for orig, resolved, meta in ex.map(_one, sorted(urls)):
      mapping[orig] = resolved
      usage_log.append({"source_url": orig, "resolved_url": resolved, **meta})

  return _rewrite_gen_urls(messages, mapping), usage_log


def generate_style_preview(
    store: Store,
    llm: Llm | None,
    style: dict[str, Any],
    *,
    prompt: str | None = None,
    image_model: str | None = None,
    svg_model: str | None = None,
) -> dict[str, Any]:
  """Generates a live preview asset for a style preset and updates its preview_url."""
  medium = style.get("medium") or "raster"
  svg_mode = imagery_mod.is_svg_medium(medium)
  default_prompt = (
      "Minimalist weather cloud and sun icon"
      if svg_mode
      else "Partly cloudy golden hour sky over coastal hills"
  )
  subj = (prompt or style.get("preview_prompt") or default_prompt).strip()
  slug = style.get("slug") or "default"
  pid = style.get("project_id") or ""
  aspect = style.get("aspect_ratio") or ("1:1" if svg_mode else "16:9")
  prefix = GEN_SVG_PREFIX if svg_mode else GEN_IMG_PREFIX
  gen_url = (
      f"{prefix}?prompt={urllib.parse.quote_plus(subj)}"
      f"&style={urllib.parse.quote_plus(slug)}"
      f"&aspect={urllib.parse.quote_plus(aspect)}"
  )
  resolved_url, meta = resolve_gen_url(
      gen_url,
      store=store,
      llm=llm,
      project_id=pid,
      styles_by_slug={slug: style},
      image_model=image_model,
      svg_model=svg_model,
  )
  updated = imagery_mod.upsert_style(
      store,
      pid,
      style.get("author_ldap") or "",
      {
          "id": style["id"],
          "slug": slug,
          "name": style.get("name") or slug,
          "preview_prompt": subj,
          "preview_url": resolved_url,
      },
  )
  return {"style": updated, "preview_url": resolved_url, "meta": meta}
