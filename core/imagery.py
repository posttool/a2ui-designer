"""Project imagery & iconography style presets and reference image storage."""

from __future__ import annotations

import base64
import os
import re
import shutil
from typing import Any

from core import util
from core.storage import Store
from llm.gemini import Image

MEDIUMS = ("raster", "svg", "raster_image", "svg_icon", "svg_illustration")
TARGET_SLOTS = (
    "card_background",
    "list_icon",
    "list_item_icon",
    "hero_banner",
    "inline_image",
    "image",
    "custom",
    "custom_svg",
)
ASPECT_RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16")
SCRIMS = ("auto", "dark", "light", "none")

SLUG_RE = re.compile(r"[^a-z0-9-]+")
MAX_REF_IMAGES = 6
MAX_REF_BYTES = 12 * 1024 * 1024


def is_svg_medium(medium: str | None) -> bool:
  return str(medium or "").strip().lower() in (
      "svg",
      "svg_icon",
      "svg_illustration",
  )


def normalize_slug(raw: str | None, fallback_name: str = "") -> str:
  """Normalizes a style identifier into a URL-safe lowercase hyphenated slug."""
  base = (raw or "").strip().lower() or (fallback_name or "").strip().lower()
  slug = SLUG_RE.sub("-", base.replace("_", "-").replace(" ", "-")).strip("-")
  return slug[:48] or "imagery-style"


def fmt_style(row: dict[str, Any] | None) -> dict[str, Any] | None:
  if not row:
    return None
  pid = row["project_id"]
  sid = row["id"]
  refs = []
  for i, im in enumerate(row.get("reference_images") or []):
    if not isinstance(im, dict):
      continue
    fname = im.get("file") or ""
    refs.append({
        "index": i,
        "name": im.get("name") or fname or f"Reference {i + 1}",
        "caption": im.get("caption") or "",
        "mime_type": im.get("mime_type") or "image/png",
        "width": im.get("width"),
        "height": im.get("height"),
        "file": fname,
        "url": f"/api/projects/{pid}/imagery/{sid}/refs/{i}" if fname else None,
    })
  return {
      "id": sid,
      "project_id": pid,
      "slug": row["slug"],
      "name": row.get("name") or row["slug"],
      "medium": row.get("medium") if row.get("medium") in MEDIUMS else "raster",
      "target_slot": (
          row.get("target_slot")
          if row.get("target_slot") in TARGET_SLOTS
          else "card_background"
      ),
      "incorporation": row.get("incorporation") or "",
      "style_text": row.get("style_text") or "",
      "aspect_ratio": (
          row.get("aspect_ratio")
          if row.get("aspect_ratio") in ASPECT_RATIOS
          else "16:9"
      ),
      "scrim": row.get("scrim") if row.get("scrim") in SCRIMS else "auto",
      "reference_images": refs,
      "preview_prompt": row.get("preview_prompt") or "",
      "preview_url": row.get("preview_url") or "",
      "enabled": bool(row.get("enabled", 1)),
      "author_ldap": row.get("author_ldap") or "",
      "created_at": row.get("created_at") or "",
      "updated_at": row.get("updated_at") or "",
  }


def style_dir(store: Store, style_id: str) -> str:
  p = store.file_path("imagery", style_id)
  os.makedirs(p, exist_ok=True)
  return p


def _decode_data_url(data_url: str) -> tuple[bytes, str, str]:
  if not data_url or not data_url.startswith("data:image/"):
    raise ValueError("Invalid image data URL")
  head, _, b64 = data_url.partition(",")
  mime = head[len("data:") :].split(";")[0].strip().lower() or "image/png"
  ext = (
      "png"
      if "png" in mime
      else "jpg"
      if ("jpeg" in mime or "jpg" in mime)
      else "webp"
      if "webp" in mime
      else "svg"
      if "svg" in mime
      else "png"
  )
  raw = base64.b64decode(b64)
  if len(raw) > MAX_REF_BYTES:
    raise ValueError("Reference image too large (12 MB max)")
  return raw, mime, ext


def save_reference_images(
    store: Store,
    style_id: str,
    raw_items: list[dict[str, Any]],
    existing: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
  """Writes reference images to disk and returns metadata entries."""
  sdir = style_dir(store, style_id)
  existing_by_file = {
      str(im.get("file")): im
      for im in existing or []
      if isinstance(im, dict) and im.get("file")
  }
  saved: list[dict[str, Any]] = []
  for idx, item in enumerate((raw_items or [])[:MAX_REF_IMAGES]):
    if not isinstance(item, dict):
      continue
    data_url = str(item.get("data_url") or "").strip()
    existing_file = str(item.get("file") or "").strip()
    if data_url:
      raw, mime, ext = _decode_data_url(data_url)
      fname = f"ref_{idx}_{util.new_id()[:8]}.{ext}"
      with open(os.path.join(sdir, fname), "wb") as fh:
        fh.write(raw)
      saved.append({
          "file": fname,
          "style_id": style_id,
          "name": str(item.get("name") or f"Reference {idx + 1}")[:120],
          "caption": str(item.get("caption") or "")[:240],
          "mime_type": mime,
          "width": item.get("width"),
          "height": item.get("height"),
      })
    elif existing_file and existing_file in existing_by_file:
      prev = dict(existing_by_file[existing_file])
      prev["style_id"] = style_id
      if "caption" in item:
        prev["caption"] = str(item.get("caption") or "")[:240]
      if "name" in item and item.get("name"):
        prev["name"] = str(item.get("name"))[:120]
      saved.append(prev)
    elif existing_file and item.get("style_id"):
      src_dir = store.file_path("imagery", str(item["style_id"]))
      src_fp = os.path.join(src_dir, os.path.basename(existing_file))
      dst_fname = os.path.basename(existing_file)
      dst_fp = os.path.join(sdir, dst_fname)
      if os.path.isfile(src_fp) and src_fp != dst_fp:
        shutil.copyfile(src_fp, dst_fp)
      if os.path.isfile(dst_fp):
        saved.append({
            "file": dst_fname,
            "style_id": style_id,
            "name": str(item.get("name") or f"Reference {idx + 1}")[:120],
            "caption": str(item.get("caption") or "")[:240],
            "mime_type": item.get("mime_type") or "image/png",
            "width": item.get("width"),
            "height": item.get("height"),
        })
    elif (
        item.get("index") is not None
        and existing
        and 0 <= int(item["index"]) < len(existing)
    ):
      prev = dict(existing[int(item["index"])])
      prev["style_id"] = style_id
      if "caption" in item:
        prev["caption"] = str(item.get("caption") or "")[:240]
      if "name" in item and item.get("name"):
        prev["name"] = str(item.get("name"))[:120]
      saved.append(prev)
  return saved


def load_reference_images(
    store: Store, style: dict[str, Any] | list[dict[str, Any]] | None
) -> list[Any]:
  """Loads reference image bytes from disk for multimodal Gemini prompts."""
  if not style:
    return []
  if isinstance(style, list):
    out_tuples: list[tuple[bytes, str, str]] = []
    for im in style:
      if not isinstance(im, dict):
        continue
      fname = im.get("file") or ""
      sid = im.get("style_id") or ""
      if not fname or "/" in fname or ".." in fname:
        continue
      fp = (
          os.path.join(store.file_path("imagery", sid), fname)
          if sid
          else store.file_path("imagery", fname)
      )
      if os.path.isfile(fp):
        try:
          with open(fp, "rb") as fh:
            data = fh.read()
          if data:
            out_tuples.append((
                data,
                im.get("mime_type") or "image/png",
                im.get("caption") or im.get("name") or "",
            ))
        except OSError:
          continue
    return out_tuples
  sid = style.get("id") or ""
  if not sid:
    return []
  sdir = store.file_path("imagery", sid)
  out: list[Image] = []
  for im in style.get("reference_images") or []:
    if not isinstance(im, dict):
      continue
    fname = im.get("file") or ""
    if not fname or "/" in fname or ".." in fname:
      continue
    fp = os.path.join(sdir, fname)
    if os.path.isfile(fp):
      try:
        with open(fp, "rb") as fh:
          data = fh.read()
        if data:
          out.append(
              Image(data=data, mime_type=im.get("mime_type") or "image/png")
          )
      except OSError:
        continue
  return out


def list_styles(
    store: Store,
    project_id: str,
    *,
    enabled_only: bool = False,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
  del include_archived
  where = "project_id=?" + (" AND enabled=1" if enabled_only else "")
  rows = store.query(
      f"SELECT * FROM imagery_styles WHERE {where} ORDER BY created_at ASC",
      [project_id],
  )
  return [fmt_style(r) for r in rows if r]


def get_style(
    store: Store, project_id: str, id_or_slug: str
) -> dict[str, Any] | None:
  key = (id_or_slug or "").strip()
  if not key:
    return None
  row = store.get("imagery_styles", key)
  if row and row.get("project_id") == project_id:
    return fmt_style(row)
  rows = store.query(
      "SELECT * FROM imagery_styles WHERE project_id=? AND slug=?",
      [project_id, normalize_slug(key)],
  )
  return fmt_style(rows[0]) if rows else None


def upsert_style(
    store: Store,
    project_id: str,
    author_ldap: str,
    body: dict[str, Any],
    *,
    sid: str | None = None,
) -> dict[str, Any]:
  """Creates or updates an imagery style preset by id or (project_id, slug)."""
  name = str(body.get("name") or body.get("slug") or "Imagery style").strip()[
      :120
  ]
  slug = normalize_slug(body.get("slug"), fallback_name=name)
  existing_row = None
  lookup_id = sid or body.get("id")
  if lookup_id:
    r = store.get("imagery_styles", str(lookup_id))
    if r and r.get("project_id") == project_id:
      existing_row = r
  if existing_row is None:
    rows = store.query(
        "SELECT * FROM imagery_styles WHERE project_id=? AND slug=?",
        [project_id, slug],
    )
    if rows:
      existing_row = rows[0]

  now = util.now_iso()
  sid = existing_row["id"] if existing_row else util.new_id()
  medium = str(
      body.get("medium") or (existing_row or {}).get("medium") or "raster"
  ).lower()
  if medium not in MEDIUMS:
    medium = "raster"
  default_slot = "list_icon" if is_svg_medium(medium) else "card_background"
  target_slot = str(
      body.get("target_slot")
      or (existing_row or {}).get("target_slot")
      or default_slot
  ).lower()
  if target_slot not in TARGET_SLOTS:
    target_slot = default_slot

  default_aspect = "1:1" if is_svg_medium(medium) else "16:9"
  aspect_ratio = str(
      body.get("aspect_ratio")
      or (existing_row or {}).get("aspect_ratio")
      or default_aspect
  )
  if aspect_ratio not in ASPECT_RATIOS:
    aspect_ratio = default_aspect

  scrim = str(
      body.get("scrim") or (existing_row or {}).get("scrim") or "auto"
  ).lower()
  if scrim not in SCRIMS:
    scrim = "auto"

  incorporation = str(
      body.get("incorporation")
      if "incorporation" in body
      else (existing_row or {}).get("incorporation") or ""
  ).strip()
  style_text = str(
      body.get("style_text")
      if "style_text" in body
      else (existing_row or {}).get("style_text") or ""
  ).strip()
  preview_prompt = str(
      body.get("preview_prompt")
      if "preview_prompt" in body
      else (existing_row or {}).get("preview_prompt") or ""
  ).strip()
  preview_url = str(
      body.get("preview_url")
      if "preview_url" in body
      else (existing_row or {}).get("preview_url") or ""
  ).strip()
  enabled = (
      1
      if bool(body.get("enabled", (existing_row or {}).get("enabled", 1)))
      else 0
  )

  prev_refs = (existing_row or {}).get("reference_images") or []
  if "reference_images" in body and isinstance(body["reference_images"], list):
    refs = save_reference_images(
        store, sid, body["reference_images"], existing=prev_refs
    )
  elif "append_reference_images" in body and isinstance(
      body["append_reference_images"], list
  ):
    combined = list(prev_refs) + list(body["append_reference_images"])
    refs = save_reference_images(store, sid, combined, existing=prev_refs)
  else:
    refs = prev_refs

  if existing_row:
    row = store.update(
        "imagery_styles",
        sid,
        {
            "slug": slug,
            "name": name,
            "medium": medium,
            "target_slot": target_slot,
            "incorporation": incorporation,
            "style_text": style_text,
            "aspect_ratio": aspect_ratio,
            "scrim": scrim,
            "reference_images": refs,
            "preview_prompt": preview_prompt,
            "preview_url": preview_url,
            "enabled": enabled,
            "updated_at": now,
        },
    )
  else:
    row = store.insert(
        "imagery_styles",
        {
            "id": sid,
            "project_id": project_id,
            "slug": slug,
            "name": name,
            "medium": medium,
            "target_slot": target_slot,
            "incorporation": incorporation,
            "style_text": style_text,
            "aspect_ratio": aspect_ratio,
            "scrim": scrim,
            "reference_images": refs,
            "preview_prompt": preview_prompt,
            "preview_url": preview_url,
            "enabled": enabled,
            "author_ldap": author_ldap,
            "created_at": now,
            "updated_at": now,
        },
    )
  return fmt_style(row)


def delete_style(store: Store, project_id: str, id_or_slug: str) -> bool:
  st = get_style(store, project_id, id_or_slug)
  if not st:
    return False
  sid = st["id"]
  store.delete("imagery_styles", sid)
  shutil.rmtree(store.file_path("imagery", sid), ignore_errors=True)
  return True


def apply_imagery_changes(
    store: Store,
    project_id: str,
    author_ldap: str,
    changes: list[dict[str, Any]] | None,
    attached_images: list[dict[str, Any]] | None = None,
    attached_refs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
  """Applies proposed imagery_changes from chat/reference analysis."""
  refs_to_attach = (
      attached_images if attached_images is not None else attached_refs
  )
  applied: list[dict[str, Any]] = []
  for item in changes or []:
    if not isinstance(item, dict):
      continue
    action = str(item.get("action") or "upsert").lower()
    slug = normalize_slug(
        item.get("slug"), fallback_name=str(item.get("name") or "")
    )
    if action == "delete":
      delete_style(store, project_id, slug)
      applied.append({"slug": slug, "action": "deleted"})
      continue
    payload: dict[str, Any] = {
        "slug": slug,
        "name": item.get("name") or slug.replace("-", " ").title(),
    }
    for k in (
        "medium",
        "target_slot",
        "incorporation",
        "style_text",
        "aspect_ratio",
        "scrim",
    ):
      if item.get(k):
        payload[k] = item[k]
    if item.get("use_attached_references", True) and refs_to_attach:
      payload["append_reference_images"] = refs_to_attach
    saved = upsert_style(store, project_id, author_ldap, payload)
    applied.append({
        "id": saved["id"],
        "slug": saved["slug"],
        "name": saved["name"],
        "medium": saved["medium"],
        "target_slot": saved["target_slot"],
        "incorporation": saved["incorporation"],
        "style_text": saved["style_text"],
        "reference_images": saved.get("reference_images") or [],
        "reference_count": len(saved.get("reference_images") or []),
    })
  return applied


def format_for_prompt(styles: list[dict[str, Any]] | None) -> str:
  """Formats active imagery & iconography style presets for LLM prompts."""
  active = [
      s for s in styles or [] if isinstance(s, dict) and s.get("enabled", True)
  ]
  if not active:
    return ""
  lines = [
      "# Generated Imagery & Iconography Styles",
      "",
      "The designer configured these generated media styles for this project.",
      "Incorporate them when their contextual trigger matches (or when the",
      "Design Constitution directs you to):",
      "",
  ]
  for s in active:
    slug = s.get("slug") or "default"
    medium = s.get("medium") or "raster"
    svg_mode = is_svg_medium(medium)
    slot = s.get("target_slot") or "card_background"
    inc = (s.get("incorporation") or "").strip()
    st_text = (s.get("style_text") or "").strip()
    aspect = s.get("aspect_ratio") or ("1:1" if svg_mode else "16:9")
    scrim = s.get("scrim") or "auto"
    ref_count = len(s.get("reference_images") or [])
    ref_note = (
        f" ({ref_count} style reference image{'s' if ref_count != 1 else ''}"
        " attached)"
        if ref_count
        else ""
    )
    lines.append(
        f"## Style `{slug}` — {s.get('name') or slug}"
        f" ({medium.upper()}{ref_note})"
    )
    lines.append(f"- **Target slot**: `{slot}`")
    if inc:
      lines.append(f"- **When & how to incorporate**: {inc}")
    if st_text:
      lines.append(f"- **Visual style guidance**: {st_text}")
    if svg_mode:
      lines.append(
          "- **How to emit in A2UI**: With the extension catalog, use"
          ' `SvgIcon` with `"url":'
          f' "/gen/svg?prompt=<concise visual subject>&style={slug}"` (or on'
          f' `Image`, set `"url": "/gen/svg?prompt=<subject>&style={slug}"`).'
      )
    elif slot == "card_background":
      lines.append(
          "- **How to emit in A2UI**: With the extension catalog, use a"
          ' `HeroCard` with `"image": "/gen/img?prompt=<specific visual scene'
          f' from data>&style={slug}&aspect={aspect}"` and `"scrim":'
          f' "{scrim}"`; otherwise a header `Image` with that `url`.'
      )
    else:
      lines.append(
          "- **How to emit in A2UI**: On `Image` (or custom component image"
          ' prop), set `"url": "/gen/img?prompt=<specific visual subject from'
          f' data>&style={slug}&aspect={aspect}"`.'
      )
    lines.append("")
  return "\n".join(lines).strip()
