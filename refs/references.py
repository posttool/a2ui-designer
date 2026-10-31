"""Design references: storage, Figma fetch, screenshots, analysis → chat proposal."""

from __future__ import annotations

import base64
import concurrent.futures
import inspect
import json
import os
import re
import struct
import traceback
from typing import Any

from core import diffs, settings as settings_mod
from core import util
from core.service import ApiError
from llm.gemini import Image, LlmError
from refs import figma
from refs import stitch
from sim import common
from sim.common import arr, obj, str_schema
from sim.reviser import THEME_CHANGE_ITEM_SCHEMA, _theme_digest  # pylint: disable=protected-access

MAX_IMAGES = 20
MAX_IMAGE_BYTES = 12 * 1024 * 1024
VIEWPORT_HINTS = (
    "phone",
    "tablet",
    "desktop",
    "smart_display",
    "watch",
    "unknown",
)
FOCUS = ("all", "constitution", "tokens", "catalog")

ANALYSIS_SCHEMA = obj({
    "reply_md": str_schema("Short summary for the designer (≤150 words)."),
    "observations": arr(
        obj({
            "topic": {
                "type": "string",
                "enum": [
                    "hierarchy",
                    "density",
                    "grouping",
                    "order",
                    "states",
                    "components",
                    "actions",
                    "tokens",
                    "content",
                ],
            },
            "text": str_schema(),
            "evidence": arr(str_schema("img:<n> or frame:<name>")),
        })
    ),
    "proposal": obj({
        "has_proposal": {"type": "boolean"},
        "constitution_md": str_schema(
            "Full new constitution text, or '' if unchanged."
        ),
        "change_summary": str_schema(),
        "changed_sections": arr(str_schema()),
        "theme_changes": arr(
            obj(
                dict(
                    THEME_CHANGE_ITEM_SCHEMA["properties"],
                    confidence={"type": "string", "enum": ["exact", "approx"]},
                )
            )
        ),
        "catalog_changes": obj({
            "enable": arr(str_schema()),
            "disable": arr(str_schema()),
            "proposed_components": arr(
                obj({
                    "name": str_schema(),
                    "purpose": str_schema(),
                    "maps_to_basic": arr(str_schema()),
                    "props_schema_json": str_schema(
                        "JSON Schema of the props, serialized to a string."
                    ),
                })
            ),
        }),
    }),
    "out_of_scope": arr(
        obj({
            "rule": str_schema(),
            "why": str_schema(),
            "suggested_home": {
                "type": "string",
                "enum": ["theme", "catalog", "component"],
            },
        })
    ),
    "formatting_feedback": arr(
        obj({
            "issue": str_schema(),
            "fix": str_schema(),
            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        })
    ),
})


def png_size(data: bytes) -> tuple[int, int]:
  """(width, height) for PNG/JPEG/WebP/GIF bytes, (0, 0) if unknown."""
  try:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
      return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a"):
      return struct.unpack("<HH", data[6:10])
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
      if data[12:16] == b"VP8X":
        w = int.from_bytes(data[24:27], "little") + 1
        h = int.from_bytes(data[27:30], "little") + 1
        return w, h
    if data[:2] == b"\xff\xd8":
      i = 2
      while i < len(data) - 9:
        if data[i] != 0xFF:
          i += 1
          continue
        marker = data[i + 1]
        seg = int.from_bytes(data[i + 2 : i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
          h = int.from_bytes(data[i + 5 : i + 7], "big")
          w = int.from_bytes(data[i + 7 : i + 9], "big")
          return w, h
        i += 2 + seg
  except (struct.error, IndexError):
    pass
  return 0, 0


def guess_viewport(width: int, height: int) -> str:
  """Viewport hint for a screenshot, allowing 1x–4x device pixel ratios."""
  if not width or not height:
    return "unknown"
  if abs(width - height) <= 0.1 * width and width <= 600:
    return "watch"
  if height >= width * 1.25:  # portrait
    for scale in (1, 2, 3, 4):
      if 320 <= width / scale <= 480:
        return "phone"
    for scale in (1, 2):
      if 600 <= width / scale <= 900:
        return "tablet"
    return "unknown"
  for scale in (1, 2):
    hint = figma.viewport_hint(width / scale)
    if hint in ("desktop", "smart_display", "tablet"):
      return hint
  return "unknown"


def decode_data_url(data_url: str) -> tuple[bytes, str]:
  m = re.match(
      r"data:(image/(png|jpeg|jpg|webp|gif));base64,(.*)$", data_url or "", re.S
  )
  if not m:
    raise ApiError(400, "Images must be data:image/png|jpeg|webp;base64 URLs")
  data = base64.b64decode(m.group(3), validate=False)
  if len(data) > MAX_IMAGE_BYTES:
    raise ApiError(413, "An image is larger than 12 MB")
  ext = {"jpeg": "jpg", "jpg": "jpg"}.get(m.group(2), m.group(2))
  return data, ext


def make_progress(
    kind: str, mode: str = "import", detail: str = ""
) -> dict[str, Any]:
  """Initial structured progress payload for an import pipeline run."""
  now = util.now_iso()
  if mode == "reanalyze":
    raw_steps = [
        ("gemini_analyze", "Re-analyze with Gemini"),
        ("crop_regions", "Crop extracted UI regions"),
    ]
  elif mode == "plan":
    raw_steps = [
        ("gemini_plan", "Draft merge plan with Gemini"),
        ("build_diff", "Compute token changes & constitution diff"),
    ]
  elif kind == "figma":
    raw_steps = [
        ("fetch_file", "Fetch Figma document & design tokens"),
        ("download_frames", "Render & download frame PNGs"),
        ("scan_components", "Scan Figma components & thumbnails"),
        (
            "gemini_analyze",
            "Separate components, templates & tokens with Gemini",
        ),
        ("crop_regions", "Crop extracted UI regions"),
    ]
  elif kind == "stitch":
    raw_steps = [
        ("fetch_file", "Fetch Stitch project, screens & design system"),
        ("download_frames", "Download or render screen images"),
        (
            "gemini_analyze",
            "Separate components, templates & tokens with Gemini",
        ),
        ("crop_regions", "Crop extracted UI regions"),
    ]
  else:
    raw_steps = [
        ("upload_images", "Store uploaded screenshots"),
        (
            "gemini_analyze",
            "Separate components, templates & tokens with Gemini",
        ),
        ("crop_regions", "Crop extracted UI regions"),
    ]
  steps = []
  for i, (sid, slabel) in enumerate(raw_steps):
    is_done = mode == "import" and kind == "screenshots" and i == 0
    is_running = (
        (i == 1) if (mode == "import" and kind == "screenshots") else (i == 0)
    )
    steps.append({
        "id": sid,
        "label": slabel,
        "status": "done" if is_done else ("running" if is_running else "todo"),
        "detail": detail if is_done else "",
        "started_at": now if (is_done or is_running) else None,
        "finished_at": now if is_done else None,
    })
  active = next((s for s in steps if s["status"] == "running"), steps[0])
  return {
      "mode": mode,
      "step": active["id"],
      "label": active["label"] + "…",
      "detail": detail if active["id"] != "gemini_analyze" else "",
      "done": None,
      "total": None,
      "started_at": now,
      "step_started_at": now,
      "updated_at": now,
      "steps": steps,
  }


def step_progress(
    prog: dict[str, Any] | None,
    step_id: str,
    *,
    label: str | None = None,
    detail: str | None = None,
    step_detail: str | None = None,
    done: int | None = None,
    total: int | None = None,
    status: str = "running",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
  """Advances or updates `prog` on `step_id` and returns a new progress dict."""
  now = util.now_iso()
  out = dict(prog or make_progress("screenshots"))
  steps = [dict(s) for s in out.get("steps") or []]
  target_idx = next(
      (i for i, s in enumerate(steps) if s.get("id") == step_id), None
  )
  if target_idx is not None:
    for i in range(target_idx):
      if steps[i].get("status") in ("todo", "running"):
        steps[i]["status"] = "done"
        steps[i]["finished_at"] = steps[i].get("finished_at") or now
    st = steps[target_idx]
    st["status"] = status
    st["started_at"] = st.get("started_at") or now
    if status in ("done", "error"):
      st["finished_at"] = now
    if step_detail is not None:
      st["detail"] = step_detail
    elif detail is not None:
      st["detail"] = detail
  out["steps"] = steps
  if out.get("step") != step_id:
    out["step"] = step_id
    out["step_started_at"] = now
  if label is not None:
    out["label"] = label
  if detail is not None:
    out["detail"] = detail
  out["done"] = done
  out["total"] = total
  out["updated_at"] = now
  if extra:
    out.update(extra)
  return out


class ReferenceManager:

  def __init__(self, svc, figma_fetch=None, pool=None, stitch_fetch=None):
    self.svc = svc
    self.store = svc.store
    self.pool = pool  # BrowserPool for rendering Stitch HTML when needed
    self.executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="refs"
    )
    # Injectable for tests: (token, url) -> figma.fetch()-shaped dict.
    self.figma_fetch = figma_fetch or figma.fetch
    # Injectable for tests: (token, url) -> figma.list_file_frames()-shaped dict.
    self.figma_frames = figma.list_file_frames
    # Injectable for tests: (token, src) -> figma.list_source_files()-shaped dict.
    self.figma_source_files = figma.list_source_files
    # Injectable for tests: (api_key, project_id, screen_ids, ref_id) -> dict.
    self.stitch_fetch = stitch_fetch or self._stitch_fetch_live
    # Injectable for tests: (api_key) -> [project dicts].
    self.stitch_projects = None
    # Set by ImportManager: (ref_id, secrets) -> None once ingest succeeded.
    self.on_ready = None

  def _ready(self, rid: str, secrets: dict[str, Any] | None = None) -> None:
    if self.on_ready:
      self.on_ready(rid, secrets or {})
    else:
      self.store.update("design_references", rid, {"phase": "review"})

  # -------------------------------------------------------- figma token
  def figma_token(self, user: dict[str, Any]) -> tuple[str | None, str | None]:
    row = self.store.get("users", user["ldap"]) or {}
    if row.get("figma_token"):
      return row["figma_token"], "user"
    t = figma.server_token()
    return (t, "server") if t else (None, None)

  def figma_status(self, user: dict[str, Any]) -> dict[str, Any]:
    tok, src = self.figma_token(user)
    return {"has_token": bool(tok), "source": src}

  def set_figma_token(self, user: dict[str, Any], token: str) -> dict:
    token = (token or "").strip()
    if token and (len(token) > 200 or re.search(r"\s", token)):
      raise ApiError(400, "That does not look like a Figma token")
    self.store.update(
        "users", user["ldap"], {"figma_token": token or None}, key_col="ldap"
    )
    return self.figma_status(user)

  def _user_figma_sources(self, user: dict[str, Any]) -> list[dict[str, Any]]:
    row = self.store.get("users", user["ldap"]) or {}
    raw = row.get("figma_sources")
    return (
        [dict(x) for x in raw if isinstance(x, dict)]
        if isinstance(raw, list)
        else []
    )

  def _save_user_figma_sources(
      self, user: dict[str, Any], sources: list[dict[str, Any]]
  ) -> None:
    self.store.update(
        "users",
        user["ldap"],
        {"figma_sources": sources[:60]},
        key_col="ldap",
    )

  def _record_figma_file(
      self,
      ldap: str | None,
      key: str,
      name: str = "",
      last_modified: str = "",
      thumbnail_url: str | None = None,
      page_count: int | None = None,
  ) -> None:
    if not ldap or not key:
      return
    row = self.store.get("users", ldap)
    if not row:
      return
    sources = [
        dict(x) for x in row.get("figma_sources") or [] if isinstance(x, dict)
    ]
    now = util.now_iso()
    for s in sources:
      if s.get("kind") == "file" and s.get("key") == key:
        if name:
          s["name"] = name
        if last_modified:
          s["last_modified"] = last_modified
        if thumbnail_url:
          s["thumbnail_url"] = thumbnail_url
        if page_count is not None:
          s["page_count"] = page_count
        s["updated_at"] = now
        self.store.update(
            "users", ldap, {"figma_sources": sources[:60]}, key_col="ldap"
        )
        return
    sources.insert(
        0,
        {
            "kind": "file",
            "key": key,
            "name": name or f"File {key[:8]}",
            "url": f"https://www.figma.com/design/{key}",
            "last_modified": last_modified,
            "thumbnail_url": thumbnail_url,
            "page_count": page_count,
            "updated_at": now,
        },
    )
    self.store.update(
        "users", ldap, {"figma_sources": sources[:60]}, key_col="ldap"
    )

  def _dcb_figma_files(self) -> dict[str, dict[str, Any]]:
    """Figma files already imported/referenced across DCB projects."""
    found: dict[str, dict[str, Any]] = {}

    def add(src: dict[str, Any], updated_at: str = "", title: str = "") -> None:
      url = str(src.get("url") or "")
      key = str(src.get("file_key") or "").strip()
      if not key and url:
        try:
          key, _ = figma.parse_url(url)
        except figma.FigmaError:
          return
      if not key:
        return
      fname = str(src.get("file_name") or "").strip()
      if not fname and title and title != "Figma import":
        fname = title.strip()
      if not fname and url:
        m = re.search(r"/(?:design|file)/[^/?#]+/([^/?#]+)", url)
        if m:
          fname = re.sub(r"[-_]+", " ", m.group(1)).strip()
      ent = found.setdefault(
          key,
          {
              "key": key,
              "name": "",
              "url": f"https://www.figma.com/design/{key}",
              "last_modified": "",
              "import_count": 0,
              "source": "recent",
          },
      )
      ent["import_count"] += 1
      if fname and not ent["name"]:
        ent["name"] = fname
      if updated_at and updated_at > ent["last_modified"]:
        ent["last_modified"] = updated_at

    for r in self.store.query(
        "SELECT title, source_json, updated_at FROM design_references"
        " WHERE kind='figma' ORDER BY updated_at DESC"
    ):
      add(
          r.get("source") or {}, r.get("updated_at") or "", r.get("title") or ""
      )
    for r in self.store.query(
        "SELECT source_json, updated_at FROM custom_components"
        " WHERE status!='archived' ORDER BY updated_at DESC"
    ):
      src = r.get("source") or {}
      if src.get("kind") == "figma":
        add(src, r.get("updated_at") or "")
    return found

  def list_figma_projects(
      self, user: dict[str, Any], refresh: bool = False
  ) -> dict[str, Any]:
    """Lists Figma files & connected folders/teams for the import picker."""
    tok, _ = self.figma_token(user)
    dcb_files = self._dcb_figma_files()
    sources = self._user_figma_sources(user)
    by_key: dict[str, dict[str, Any]] = {}
    dirty = False

    for s in sources:
      skind = s.get("kind")
      if skind in ("team", "project"):
        if tok and (refresh or not isinstance(s.get("files"), list)):
          try:
            synced = self.figma_source_files(tok, s)
            s.update(synced.get("source") or {})
            s["files"] = synced.get("files") or []
            s["updated_at"] = util.now_iso()
            dirty = True
          except figma.FigmaError:
            pass
        for f in s.get("files") or []:
          if not isinstance(f, dict) or not f.get("key"):
            continue
          k = str(f["key"])
          by_key[k] = {
              "key": k,
              "name": f.get("name") or f"File {k[:8]}",
              "url": f"https://www.figma.com/design/{k}",
              "thumbnail_url": f.get("thumbnail_url"),
              "last_modified": (
                  f.get("last_modified") or s.get("updated_at") or ""
              ),
              "project_id": (
                  f.get("project_id")
                  or (s.get("id") if skind == "project" else None)
              ),
              "project_name": (
                  f.get("project_name")
                  or (s.get("name") if skind == "project" else "")
              ),
              "team_id": (
                  f.get("team_id") or (s.get("id") if skind == "team" else None)
              ),
              "team_name": (
                  f.get("team_name")
                  or (s.get("name") if skind == "team" else "")
              ),
              "import_count": (dcb_files.get(k) or {}).get("import_count", 0),
              "source": skind,
          }
      elif skind == "file" and s.get("key"):
        k = str(s["key"])
        ent = by_key.setdefault(
            k,
            {
                "key": k,
                "name": s.get("name") or f"File {k[:8]}",
                "url": f"https://www.figma.com/design/{k}",
                "thumbnail_url": s.get("thumbnail_url"),
                "last_modified": (
                    s.get("last_modified") or s.get("updated_at") or ""
                ),
                "page_count": s.get("page_count"),
                "project_name": s.get("project_name") or "",
                "team_name": s.get("team_name") or "",
                "import_count": (dcb_files.get(k) or {}).get("import_count", 0),
                "source": "saved",
            },
        )
        if s.get("name") and not ent.get("name"):
          ent["name"] = s["name"]
        if s.get("page_count") is not None:
          ent["page_count"] = s["page_count"]

    if dirty:
      self._save_user_figma_sources(user, sources)

    for k, df in dcb_files.items():
      if k in by_key:
        by_key[k]["import_count"] = max(
            by_key[k].get("import_count") or 0, df.get("import_count") or 0
        )
        if not by_key[k].get("name") and df.get("name"):
          by_key[k]["name"] = df["name"]
        if not by_key[k].get("last_modified") and df.get("last_modified"):
          by_key[k]["last_modified"] = df["last_modified"]
      else:
        by_key[k] = {
            "key": k,
            "name": df.get("name") or f"File {k[:8]}",
            "url": df["url"],
            "thumbnail_url": None,
            "last_modified": df.get("last_modified") or "",
            "project_name": "",
            "team_name": "",
            "import_count": df.get("import_count") or 0,
            "source": "recent",
        }

    files = sorted(
        by_key.values(),
        key=lambda f: (f.get("last_modified") or "", f.get("name") or ""),
        reverse=True,
    )
    folder_sources = [
        {
            "kind": s.get("kind"),
            "id": s.get("id"),
            "name": s.get("name") or f"{s.get('kind')} {s.get('id')}",
            "url": s.get("url"),
            "file_count": len(s.get("files") or []),
            "updated_at": s.get("updated_at") or "",
        }
        for s in sources
        if s.get("kind") in ("team", "project")
    ]
    return {
        "files": files,
        "sources": folder_sources,
        "has_token": bool(tok),
    }

  def add_figma_source(self, user: dict[str, Any], url: str) -> dict[str, Any]:
    """Adds a Figma file, project folder, or team to the user's picker list."""
    try:
      parsed = figma.parse_source_url(url)
    except figma.FigmaError as e:
      raise ApiError(400, str(e)) from e
    tok, _ = self.figma_token(user)
    kind = parsed["kind"]
    if kind in ("team", "project") and not tok:
      raise ApiError(
          400,
          "Add a Figma personal access token first to load team/project files",
      )
    sources = self._user_figma_sources(user)
    now = util.now_iso()
    if tok:
      try:
        synced = self.figma_source_files(tok, parsed)
      except figma.FigmaError as e:
        if kind in ("team", "project"):
          raise ApiError(
              e.status if e.status in (400, 403, 404) else 502, str(e)
          ) from e
        synced = {
            "source": parsed,
            "files": [{
                "key": parsed["key"],
                "name": parsed.get("name") or f"File {parsed['key'][:8]}",
                "url": parsed["url"],
                "last_modified": now,
            }],
        }
    else:
      synced = {
          "source": parsed,
          "files": [{
              "key": parsed["key"],
              "name": parsed.get("name") or f"File {parsed['key'][:8]}",
              "url": parsed["url"],
              "last_modified": now,
          }],
      }
    s_info = dict(synced.get("source") or parsed, updated_at=now)
    if kind in ("team", "project"):
      s_info["files"] = synced.get("files") or []
      sources = [
          s
          for s in sources
          if not (
              s.get("kind") == kind
              and str(s.get("id")) == str(s_info.get("id"))
          )
      ]
      sources.insert(0, s_info)
    else:
      f0 = (synced.get("files") or [{}])[0]
      k = s_info["key"]
      entry = {
          "kind": "file",
          "key": k,
          "name": f0.get("name") or s_info.get("name") or f"File {k[:8]}",
          "url": f"https://www.figma.com/design/{k}",
          "last_modified": f0.get("last_modified") or now,
          "thumbnail_url": f0.get("thumbnail_url"),
          "page_count": f0.get("page_count"),
          "updated_at": now,
      }
      sources = [
          s
          for s in sources
          if not (s.get("kind") == "file" and s.get("key") == k)
      ]
      sources.insert(0, entry)
    self._save_user_figma_sources(user, sources)
    res = self.list_figma_projects(user)
    res["added"] = {
        "kind": kind,
        "id": s_info.get("id") or s_info.get("key"),
        "name": s_info.get("name") or "",
        "file_count": len(synced.get("files") or []),
        "selected_key": parsed.get("key") if kind == "file" else None,
        "preselect_node_ids": parsed.get("node_ids") or [],
    }
    return res

  def remove_figma_source(
      self, user: dict[str, Any], key_or_id: str
  ) -> dict[str, Any]:
    target = (key_or_id or "").strip()
    sources = [
        s
        for s in self._user_figma_sources(user)
        if str(s.get("key") or "") != target
        and str(s.get("id") or "") != target
    ]
    self._save_user_figma_sources(user, sources)
    return self.list_figma_projects(user)

  def list_figma_frames(self, user: dict[str, Any], url: str) -> dict[str, Any]:
    """Lists pages & top-level sections/frames of a Figma file for picking."""
    try:
      key, pre_ids = figma.parse_url(url)
    except figma.FigmaError as e:
      raise ApiError(400, str(e)) from e
    tok, _ = self.figma_token(user)
    if not tok:
      raise ApiError(
          400, "Add a Figma personal access token first (read-only scope)"
      )
    try:
      res = self.figma_frames(tok, url)
    except figma.FigmaError as e:
      raise ApiError(
          e.status if e.status in (400, 403, 404) else 502, str(e)
      ) from e
    f_info = res.get("file") or {}
    self._record_figma_file(
        user.get("ldap"),
        f_info.get("key") or key,
        f_info.get("name") or "",
        f_info.get("last_modified") or "",
        f_info.get("thumbnail_url"),
        len(res.get("pages") or []),
    )
    if pre_ids and not res.get("preselect"):
      res["preselect"] = pre_ids
    return res

  # -------------------------------------------------------- stitch key
  def stitch_key(self, user: dict[str, Any]) -> tuple[str | None, str | None]:
    row = self.store.get("users", user["ldap"]) or {}
    if row.get("stitch_api_key"):
      return row["stitch_api_key"], "user"
    k = stitch.server_key()
    return (k, "server") if k else (None, None)

  def stitch_status(self, user: dict[str, Any]) -> dict[str, Any]:
    key, src = self.stitch_key(user)
    return {"has_key": bool(key), "source": src}

  def set_stitch_key(self, user: dict[str, Any], key: str) -> dict:
    key = (key or "").strip()
    if key and (len(key) > 300 or re.search(r"\s", key)):
      raise ApiError(400, "That does not look like a Stitch API key")
    self.store.update(
        "users", user["ldap"], {"stitch_api_key": key or None}, key_col="ldap"
    )
    return self.stitch_status(user)

  def _stitch_client(self, user: dict[str, Any]) -> stitch.StitchClient:
    key, _ = self.stitch_key(user)
    if not key:
      raise ApiError(
          400, "Add a Stitch API key first (Stitch → Settings → API keys)"
      )
    return stitch.StitchClient(key)

  def _dcb_stitch_projects(self) -> dict[str, dict[str, Any]]:
    """Stitch projects already imported/referenced across DCB projects."""
    found: dict[str, dict[str, Any]] = {}

    def add(src: dict[str, Any], updated_at: str = "", title: str = "") -> None:
      pid = str(src.get("project_id") or "").strip()
      url = str(src.get("url") or "")
      if not pid and url:
        try:
          pid, _ = stitch.parse_url(url)
        except stitch.StitchError:
          return
      if not pid:
        return
      pname = str(src.get("file_name") or "").strip()
      if not pname and title and title != "Stitch import":
        pname = title.strip()
      sids = src.get("screen_ids")
      ent = found.setdefault(
          pid,
          {
              "id": pid,
              "name": f"projects/{pid}",
              "title": "",
              "url": f"https://stitch.withgoogle.com/projects/{pid}",
              "device_type": None,
              "viewport_hint": "unknown",
              "visibility": None,
              "update_time": "",
              "screen_count": (
                  len(sids) if isinstance(sids, list) and sids else None
              ),
              "primary_color": None,
              "color_mode": None,
              "source": "recent",
              "import_count": 0,
          },
      )
      ent["import_count"] += 1
      if pname and not ent["title"]:
        ent["title"] = pname
      if updated_at and updated_at > ent["update_time"]:
        ent["update_time"] = updated_at
      if isinstance(sids, list) and sids and not ent.get("screen_count"):
        ent["screen_count"] = len(sids)

    for r in self.store.query(
        "SELECT title, source_json, updated_at FROM design_references"
        " WHERE kind='stitch' ORDER BY updated_at DESC"
    ):
      add(
          r.get("source") or {}, r.get("updated_at") or "", r.get("title") or ""
      )
    for r in self.store.query(
        "SELECT source_json, updated_at FROM custom_components"
        " WHERE status!='archived' ORDER BY updated_at DESC"
    ):
      src = r.get("source") or {}
      if src.get("kind") == "stitch":
        add(src, r.get("updated_at") or "")
    for pid, ent in found.items():
      if not ent["title"]:
        ent["title"] = f"Project {pid}"
    return found

  def list_stitch_projects(self, user: dict[str, Any]) -> dict[str, Any]:
    """Lists the user's Stitch projects (live MCP + recent DCB imports)."""
    key, _ = self.stitch_key(user)
    recent = self._dcb_stitch_projects()
    live: list[dict[str, Any]] = []
    err: str | None = None
    if key:
      try:
        if self.stitch_projects is not None:
          live = list(self.stitch_projects(key) or [])
        else:
          live = stitch.StitchClient(key).projects()
      except stitch.StitchError as e:
        err = str(e)
    by_id: dict[str, dict[str, Any]] = {}
    for p in live:
      if not isinstance(p, dict) or not p.get("id"):
        continue
      pid = str(p["id"])
      rec = recent.get(pid) or {}
      by_id[pid] = dict(
          p,
          import_count=rec.get("import_count", 0),
          title=p.get("title") or rec.get("title") or f"Project {pid}",
      )
    for pid, rec in recent.items():
      if pid not in by_id:
        by_id[pid] = rec
    projects = sorted(
        by_id.values(),
        key=lambda p: (p.get("update_time") or "", p.get("title") or ""),
        reverse=True,
    )
    return {
        "projects": projects,
        "has_key": bool(key),
        "error": err,
    }

  def list_stitch_screens(self, user: dict[str, Any], url: str) -> dict:
    try:
      pid, pre = stitch.parse_url(url)
    except stitch.StitchError as e:
      raise ApiError(400, str(e)) from e
    c = self._stitch_client(user)
    try:
      proj = c.project(pid)
      screens = c.screens(pid)
      systems = c.design_systems(pid)
    except stitch.StitchError as e:
      raise ApiError(e.status if e.status in (400, 403) else 502, str(e)) from e
    return {
        "project": {
            "id": pid,
            "title": proj.get("title") or proj.get("displayName") or "",
            "url": f"https://stitch.withgoogle.com/projects/{pid}",
        },
        "screens": [
            {
                k: sc[k]
                for k in (
                    "id",
                    "title",
                    "width",
                    "height",
                    "device_type",
                    "viewport_hint",
                )
            }
            | {
                "has_screenshot": bool(sc["screenshot_url"]),
                "has_html": bool(sc["html_url"]),
            }
            for sc in screens
        ],
        "design_systems": [
            {"name": d["name"], "display_name": d["display_name"]}
            for d in systems
        ],
        "preselect": (
            [pre] if pre and any(sc["id"] == pre for sc in screens) else []
        ),
    }

  # --------------------------------------------------------------- CRUD
  def _row(self, user: dict[str, Any], ref_id: str) -> dict[str, Any]:
    r = self.store.get("design_references", ref_id)
    if not r:
      raise ApiError(404, "Reference not found")
    self.svc._project_row(r["project_id"], user)  # pylint: disable=protected-access
    return r

  @staticmethod
  def fmt(r: dict[str, Any]) -> dict[str, Any]:
    v = (r.get("updated_at") or "").replace(":", "")
    images = []
    for im in r.get("images") or []:
      im = dict(im)
      fn = im.pop("file", None)
      im["url"] = f"/files/references/{fn}?v={v}" if fn else None
      hfn = im.get("html_file")
      im["html_url"] = f"/files/references/{hfn}?v={v}" if hfn else None
      images.append(im)
    return {
        "id": r["id"],
        "project_id": r["project_id"],
        "kind": r["kind"],
        "title": r.get("title") or "",
        "status": r.get("status"),
        "error": r.get("error"),
        "source": r.get("source") or {},
        "images": images,
        "facts": r.get("facts"),
        "notes": r.get("notes") or "",
        "analysis": r.get("analysis"),
        "progress": r.get("progress"),
        "chat_message_id": r.get("chat_message_id"),
        "created_by": r.get("created_by"),
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
    }

  def list(self, user: dict[str, Any], pid: str) -> list[dict[str, Any]]:
    self.svc._project_row(pid, user)  # pylint: disable=protected-access
    rows = self.store.query(
        "SELECT * FROM design_references WHERE project_id=? ORDER BY"
        " created_at DESC",
        [pid],
    )
    return [self.fmt(r) for r in rows]

  def get(self, user: dict[str, Any], ref_id: str) -> dict[str, Any]:
    return self.fmt(self._row(user, ref_id))

  def _save_image(self, ref_id: str, idx: int, data: bytes, ext: str) -> str:
    fn = f"{ref_id}_{idx:02d}.{ext}"
    with open(self.store.file_path("references", fn), "wb") as fh:
      fh.write(data)
    return fn

  def create(
      self, user: dict[str, Any], pid: str, body: dict[str, Any]
  ) -> dict:
    self.svc._project_row(pid, user, write=True)  # pylint: disable=protected-access
    kind = body.get("kind")
    now = util.now_iso()
    rid = util.new_id("ref")
    row: dict[str, Any] = {
        "id": rid,
        "project_id": pid,
        "kind": kind,
        "title": str(body.get("title") or "")[:200],
        "status": "ready",
        "error": None,
        "source": {},
        "images": [],
        "facts": None,
        "notes": str(body.get("notes") or "")[:4000],
        "analysis": None,
        "chat_message_id": None,
        "phase": "ingesting",
        "origin": (
            body.get("origin")
            if body.get("origin") in ("constitution", "design", "imports")
            else "imports"
        ),
        "questions": [],
        "merge_plan": None,
        "merge_log": [],
        "progress": None,
        "created_by": user["ldap"],
        "created_at": now,
        "updated_at": now,
    }
    if kind == "screenshots":
      items = body.get("images") or []
      if not isinstance(items, list) or not items:
        raise ApiError(400, "Add at least one screenshot")
      if len(items) > MAX_IMAGES:
        raise ApiError(400, f"At most {MAX_IMAGES} images per reference")
      images = []
      for i, it in enumerate(items):
        data, ext = decode_data_url((it or {}).get("data_url", ""))
        w, h = png_size(data)
        hint = (it or {}).get("viewport_hint") or ""
        if hint not in VIEWPORT_HINTS or hint == "unknown":
          hint = (
              guess_viewport(w, h)
              if not hint or hint == "unknown"
              else "unknown"
          )
        images.append({
            "file": self._save_image(rid, i, data, ext),
            "name": str((it or {}).get("name") or f"Screenshot {i + 1}")[:120],
            "caption": str((it or {}).get("caption") or "")[:1000],
            "viewport_hint": hint,
            "width": w,
            "height": h,
            "frame_id": None,
            "role": "upload",
        })
      row["images"] = images
      row["source"] = {
          "import_data": bool(body.get("import_data", False)),
          "set_default_data_source": bool(
              body.get("set_default_data_source", False)
          ),
      }
      if not row["title"]:
        row["title"] = (
            f"{len(images)} screenshot{'s' if len(images) != 1 else ''}"
        )
      row["progress"] = make_progress(
          "screenshots",
          "import",
          f"{len(images)} screenshot{'s' if len(images) != 1 else ''}",
      )
      self.store.insert("design_references", row)
      self._ready(rid)
    elif kind == "figma":
      fk = str(body.get("file_key") or "").strip()
      url = str(
          body.get("url")
          or (f"https://www.figma.com/design/{fk}" if fk else "")
      ).strip()
      try:
        key, ids = figma.parse_url(url)
      except figma.FigmaError as e:
        raise ApiError(400, str(e)) from e
      extra_ids = [
          str(x).strip().replace("-", ":")
          for x in body.get("node_ids") or []
          if re.fullmatch(r"\d+[:-]\d+", str(x).strip())
      ]
      if extra_ids:
        ids = extra_ids[: figma.MAX_FRAMES]
      if ids:
        q_ids = ",".join(i.replace(":", "-") for i in ids)
        url = f"https://www.figma.com/design/{key}?node-id={q_ids}"
      elif not url.startswith("http"):
        url = f"https://www.figma.com/design/{key}"
      token, _ = self.figma_token(user)
      if not token:
        raise ApiError(
            400, "Add a Figma personal access token first (read-only scope)"
        )
      import_data = bool(body["import_data"]) if "import_data" in body else True
      set_default_ds = (
          bool(body["set_default_data_source"])
          if "set_default_data_source" in body
          else import_data
      )
      row.update({
          "status": "fetching",
          "source": {
              "url": url,
              "file_key": key,
              "node_ids": ids,
              "file_name": "",
              "import_data": import_data,
              "set_default_data_source": set_default_ds,
          },
          "progress": make_progress(
              "figma",
              "import",
              f"File {key}"
              + (f" · {len(ids)} selected frame(s)" if ids else ""),
          ),
      })
      if not row["title"]:
        row["title"] = "Figma import"
      self.store.insert("design_references", row)
      self.executor.submit(self._fetch_figma, rid, token, url)
    elif kind == "stitch":
      try:
        spid, pre = stitch.parse_url(str(body.get("url") or ""))
      except stitch.StitchError as e:
        raise ApiError(400, str(e)) from e
      key, _ = self.stitch_key(user)
      if not key:
        raise ApiError(
            400, "Add a Stitch API key first (Stitch → Settings → API keys)"
        )
      ids = [str(x) for x in body.get("screen_ids") or [] if x][
          : stitch.MAX_SCREENS
      ]
      if not ids and pre:
        ids = [pre]
      import_data = bool(body["import_data"]) if "import_data" in body else True
      set_default_ds = (
          bool(body["set_default_data_source"])
          if "set_default_data_source" in body
          else import_data
      )
      row.update({
          "status": "fetching",
          "source": {
              "url": f"https://stitch.withgoogle.com/projects/{spid}",
              "project_id": spid,
              "screen_ids": ids,
              "file_name": "",
              "import_data": import_data,
              "set_default_data_source": set_default_ds,
          },
          "progress": make_progress(
              "stitch",
              "import",
              f"Project {spid}"
              + (f" · {len(ids)} selected screen(s)" if ids else ""),
          ),
      })
      if not row["title"]:
        row["title"] = "Stitch import"
      self.store.insert("design_references", row)
      self.executor.submit(self._fetch_stitch, rid, key, spid, ids)
    else:
      raise ApiError(400, "kind must be 'figma', 'stitch' or 'screenshots'")
    return self.get(user, rid)

  def _set_progress(self, rid: str, **kwargs) -> dict[str, Any]:
    r = self.store.get("design_references", rid)
    if not r:
      return {}
    step_id = (
        kwargs.pop("step", None)
        or (r.get("progress") or {}).get("step")
        or "fetch_file"
    )
    extra_fields = kwargs.pop("row_fields", None) or {}
    prog = step_progress(r.get("progress"), step_id, **kwargs)
    upd = dict(extra_fields, progress=prog, updated_at=util.now_iso())
    self.store.update("design_references", rid, upd)
    return prog

  def _fetch_figma(self, rid: str, token: str, url: str) -> None:
    try:
      live_images: list[dict[str, Any]] = []

      def on_prog(ev: dict[str, Any]) -> None:
        r_cur = self.store.get("design_references", rid) or {}
        row_upd: dict[str, Any] = {}
        fname = ev.get("file_name")
        if fname:
          src_cur = dict(r_cur.get("source") or {}, file_name=fname)
          row_upd["source"] = src_cur
          if r_cur.get("title") == "Figma import":
            row_upd["title"] = fname
        if ev.get("facts") is not None:
          row_upd["facts"] = ev["facts"]
        if "frame_index" in ev and ev.get("frame"):
          i = ev["frame_index"]
          fr = ev["frame"]
          png = ev.get("png")
          fn = self._save_image(rid, i, png, "png") if png else None
          w, h = png_size(png) if png else (fr.get("width"), fr.get("height"))
          im_entry = {
              "file": fn,
              "name": fr.get("name") or f"Frame {i + 1}",
              "caption": "",
              "viewport_hint": fr.get("viewport_hint") or "unknown",
              "width": fr.get("width") or w,
              "height": fr.get("height") or h,
              "frame_id": fr.get("id"),
              "role": "frame",
          }
          while len(live_images) <= i:
            live_images.append(im_entry)
          live_images[i] = im_entry
          row_upd["images"] = list(live_images)
        step_id = ev.get("step") or "fetch_file"
        step_det = None
        if step_id == "download_frames" and ev.get("total"):
          step_det = f"{ev.get('done', 0)}/{ev['total']} frames"
        self._set_progress(
            rid,
            step=step_id,
            label=ev.get("label"),
            detail=ev.get("detail"),
            step_detail=step_det,
            done=ev.get("done"),
            total=ev.get("total"),
            row_fields=row_upd,
        )

      params = inspect.signature(self.figma_fetch).parameters
      if "on_progress" in params:
        res = self.figma_fetch(token, url, on_progress=on_prog)
      else:
        res = self.figma_fetch(token, url)
      images = []
      for i, fr in enumerate(res.get("frames") or []):
        # Trimmed node subtree (figma.trim_node_tree): lets merge plans score
        # the Basic fit of component regions and resolve boxes to nodes.
        nodes_fn = (
            self._save_image(
                rid,
                i,
                json.dumps(fr["node"], separators=(",", ":")).encode("utf-8"),
                "nodes.json",
            )
            if isinstance(fr.get("node"), dict)
            else None
        )
        if (
            i < len(live_images)
            and live_images[i].get("frame_id") == fr.get("id")
            and live_images[i].get("file")
        ):
          if nodes_fn:
            live_images[i]["nodes_file"] = nodes_fn
          images.append(live_images[i])
          continue
        fn = (
            self._save_image(rid, i, fr["png"], "png")
            if fr.get("png")
            else None
        )
        w, h = (
            png_size(fr["png"])
            if fr.get("png")
            else (fr.get("width"), fr.get("height"))
        )
        images.append({
            "file": fn,
            "name": fr.get("name") or f"Frame {i + 1}",
            "caption": "",
            "viewport_hint": fr.get("viewport_hint") or "unknown",
            "width": fr.get("width") or w,
            "height": fr.get("height") or h,
            "frame_id": fr.get("id"),
            "role": "frame",
            **({"nodes_file": nodes_fn} if nodes_fn else {}),
        })
      r = self.store.get("design_references", rid) or {}
      src = dict(r.get("source") or {}, file_name=res.get("file_name") or "")
      title = r.get("title")
      if title == "Figma import" and res.get("file_name"):
        title = res["file_name"]
      prog = step_progress(
          r.get("progress"),
          "download_frames",
          status="done",
          label=(
              f"Downloaded {len(images)} frame{'s' if len(images) != 1 else ''}"
              " from Figma"
          ),
          detail=res.get("file_name") or "",
          step_detail=f"{len(images)} frame{'s' if len(images) != 1 else ''}",
          done=len(images),
          total=len(images),
      )
      self.store.update(
          "design_references",
          rid,
          {
              "status": "ready",
              "images": images,
              "facts": res.get("facts"),
              "source": src,
              "title": title,
              "progress": prog,
              "updated_at": util.now_iso(),
          },
      )
      self._record_figma_file(
          r.get("created_by"),
          src.get("file_key") or res.get("file_key") or "",
          res.get("file_name") or "",
      )
      self._ready(rid, {"figma_token": token})
    except figma.FigmaError as e:
      r = self.store.get("design_references", rid) or {}
      cur_step = (r.get("progress") or {}).get("step") or "fetch_file"
      prog = step_progress(
          r.get("progress"),
          cur_step,
          status="error",
          label="Figma import failed",
          detail=str(e),
      )
      self.store.update(
          "design_references",
          rid,
          {
              "status": "error",
              "phase": "error",
              "error": str(e),
              "progress": prog,
              "updated_at": util.now_iso(),
          },
      )
    except Exception as e:  # pylint: disable=broad-except
      traceback.print_exc()
      r = self.store.get("design_references", rid) or {}
      cur_step = (r.get("progress") or {}).get("step") or "fetch_file"
      prog = step_progress(
          r.get("progress"),
          cur_step,
          status="error",
          label="Figma import failed",
          detail=str(e)[:200],
      )
      self.store.update(
          "design_references",
          rid,
          {
              "status": "error",
              "phase": "error",
              "error": f"Figma import failed: {e}"[:500],
              "progress": prog,
              "updated_at": util.now_iso(),
          },
      )

  # -------------------------------------------------------------- stitch
  def _render_html(
      self, rid: str, idx: int, html_text: str, width: int, height: int
  ) -> str | None:
    """Renders Stitch HTML in headless Chrome; returns the PNG file name."""
    if not self.pool:
      return None
    ready = (
        "<script>addEventListener('load',function(){setTimeout(function(){"
        "window.__a2uiReady={html:true};},1200);});</script>"
    )
    page = html_text.replace("</head>", ready + "</head>", 1)
    if page == html_text:
      page = ready + html_text
    tmp = self.store.file_path("references", f"{rid}_{idx:02d}.render.html")
    with open(tmp, "w", encoding="utf-8") as fh:
      fh.write(page)
    fn = f"{rid}_{idx:02d}.png"
    try:
      self.pool.screenshot(
          "file://" + tmp,
          max(width or 412, 192),
          max(height or 915, 192),
          self.store.file_path("references", fn),
          scale=2.0,
          timeout=20,
      )
      return fn
    except Exception:  # pylint: disable=broad-except
      traceback.print_exc()
      return None
    finally:
      try:
        os.remove(tmp)
      except OSError:
        pass

  def _stitch_fetch_live(self, key, pid, ids, rid) -> dict[str, Any]:
    self._set_progress(
        rid,
        step="fetch_file",
        label="Fetching Stitch project, screens & design system…",
        detail=f"Project {pid}",
    )
    c = stitch.StitchClient(key)
    proj = c.project(pid)
    screens = c.screens(pid)
    if ids:
      by = {s["id"]: s for s in screens}
      screens = [by[i] for i in ids if i in by]
    screens = screens[: stitch.MAX_SCREENS]
    if not screens:
      raise stitch.StitchError("No screens found in that Stitch project")
    systems = c.design_systems(pid)
    proj_title = proj.get("title") or ""
    r_cur = self.store.get("design_references", rid) or {}
    row_upd: dict[str, Any] = {}
    if proj_title:
      row_upd["source"] = dict(r_cur.get("source") or {}, file_name=proj_title)
      if r_cur.get("title") == "Stitch import":
        row_upd["title"] = proj_title
    total = len(screens)
    self._set_progress(
        rid,
        step="download_frames",
        label=(
            f"Downloading {total} screen{'s' if total != 1 else ''} from"
            " Stitch…"
        ),
        detail=proj_title or f"Project {pid}",
        step_detail=f"0/{total} screens",
        done=0,
        total=total,
        row_fields=row_upd,
    )
    images, htmls = [], {}
    for i, sc in enumerate(screens):
      self._set_progress(
          rid,
          step="download_frames",
          label=f"Fetching screen {i + 1} of {total} from Stitch…",
          detail=f"“{sc['title']}” ({sc['width']}×{sc['height']})",
          step_detail=f"{i}/{total} screens",
          done=i,
          total=total,
      )
      img = {
          "file": None,
          "name": sc["title"],
          "caption": "",
          "viewport_hint": sc["viewport_hint"],
          "width": sc["width"],
          "height": sc["height"],
          "frame_id": sc["id"],
          "render": None,
          "error": None,
          "role": "screen",
      }
      html_text = None
      if sc["html_url"]:
        try:
          html_text = c.download(sc["html_url"], want="html").decode(
              "utf-8", "replace"
          )
          htmls[sc["id"]] = html_text
          html_fn = f"{rid}_{i:02d}.html"
          html_path = self.store.file_path("references", html_fn)
          os.makedirs(os.path.dirname(html_path), exist_ok=True)
          with open(html_path, "w", encoding="utf-8") as hfh:
            hfh.write(html_text)
          img["html_file"] = html_fn
        except stitch.StitchError as e:
          img["error"] = str(e)
        except OSError:
          pass
      if sc["screenshot_url"]:
        try:
          data = c.download(sc["screenshot_url"], want="image")
          w, h = png_size(data)
          ext = "jpg" if data[:2] == b"\xff\xd8" else "png"
          img.update({
              "file": self._save_image(rid, i, data, ext),
              "render": "screenshot",
              "error": None,
              "width": sc["width"] or w,
              "height": sc["height"] or h,
          })
        except stitch.StitchError as e:
          img["error"] = str(e)
      if not img["file"] and html_text:
        self._set_progress(
            rid,
            step="download_frames",
            label=f"Rendering HTML for screen {i + 1} of {total}…",
            detail=f"“{sc['title']}” in headless Chrome",
            step_detail=f"{i}/{total} screens",
            done=i,
            total=total,
        )
        fn = self._render_html(rid, i, html_text, sc["width"], sc["height"])
        if fn:
          img.update({"file": fn, "render": "html", "error": None})
        elif not img["error"]:
          img["error"] = "Screenshot unavailable and HTML could not be rendered"
      images.append(img)
      self._set_progress(
          rid,
          step="download_frames",
          label=f"Downloaded screen {i + 1} of {total} from Stitch…",
          detail=f"“{sc['title']}”",
          step_detail=f"{i + 1}/{total} screens",
          done=i + 1,
          total=total,
          row_fields={"images": list(images)},
      )
    return {
        "file_name": proj.get("title") or "",
        "images": images,
        "htmls": htmls,
        "facts": stitch.stitch_facts(proj, screens, systems, htmls),
    }

  def _fetch_stitch(self, rid: str, key: str, pid: str, ids: list[str]) -> None:
    try:
      res = self.stitch_fetch(key, pid, ids, rid)
      images = res.get("images") or []
      for im in images:
        im.setdefault("role", "screen")
      r = self.store.get("design_references", rid) or {}
      src = dict(
          r.get("source") or {},
          file_name=res.get("file_name") or "",
          screen_ids=[im.get("frame_id") for im in images],
      )
      title = r.get("title")
      if title == "Stitch import" and res.get("file_name"):
        title = res["file_name"]
      ok = any(im.get("file") for im in images)
      prog = step_progress(
          r.get("progress"),
          "download_frames",
          status="done" if (ok or res.get("facts")) else "error",
          label=(
              f"Downloaded {len(images)}"
              f" screen{'s' if len(images) != 1 else ''} from Stitch"
              if (ok or res.get("facts"))
              else "No screen images could be downloaded"
          ),
          detail=res.get("file_name") or "",
          step_detail=f"{len(images)} screen{'s' if len(images) != 1 else ''}",
          done=len(images),
          total=len(images),
      )
      self.store.update(
          "design_references",
          rid,
          {
              "status": "ready" if ok or res.get("facts") else "error",
              "error": (
                  None
                  if ok
                  else (
                      "No screen images could be downloaded"
                      " (see per-screen errors)"
                  )
              ),
              "images": images,
              "facts": res.get("facts"),
              "source": src,
              "title": title,
              "progress": prog,
              "updated_at": util.now_iso(),
          },
      )
      if ok or res.get("facts"):
        self._ready(rid)
      else:
        self.store.update("design_references", rid, {"phase": "error"})
    except (stitch.StitchError,) as e:
      r = self.store.get("design_references", rid) or {}
      cur_step = (r.get("progress") or {}).get("step") or "fetch_file"
      prog = step_progress(
          r.get("progress"),
          cur_step,
          status="error",
          label="Stitch import failed",
          detail=str(e),
      )
      self.store.update(
          "design_references",
          rid,
          {
              "status": "error",
              "phase": "error",
              "error": str(e),
              "progress": prog,
              "updated_at": util.now_iso(),
          },
      )
    except Exception as e:  # pylint: disable=broad-except
      traceback.print_exc()
      r = self.store.get("design_references", rid) or {}
      cur_step = (r.get("progress") or {}).get("step") or "fetch_file"
      prog = step_progress(
          r.get("progress"),
          cur_step,
          status="error",
          label="Stitch import failed",
          detail=str(e)[:200],
      )
      self.store.update(
          "design_references",
          rid,
          {
              "status": "error",
              "phase": "error",
              "error": f"Stitch import failed: {e}"[:500],
              "progress": prog,
              "updated_at": util.now_iso(),
          },
      )

  def update(self, user: dict[str, Any], ref_id: str, body: dict) -> dict:
    r = self._row(user, ref_id)
    upd: dict[str, Any] = {}
    for k in ("title", "notes"):
      if k in body:
        upd[k] = str(body[k] or "")[: 200 if k == "title" else 4000]
    if isinstance(body.get("images"), list):
      images = [dict(im) for im in r.get("images") or []]
      for patch in body["images"]:
        i = (patch or {}).get("index")
        if not isinstance(i, int) or not 0 <= i < len(images):
          continue
        if "caption" in patch:
          images[i]["caption"] = str(patch["caption"] or "")[:1000]
        if patch.get("viewport_hint") in VIEWPORT_HINTS:
          images[i]["viewport_hint"] = patch["viewport_hint"]
      upd["images"] = images
    if upd:
      upd["updated_at"] = util.now_iso()
      self.store.update("design_references", ref_id, upd)
    return self.get(user, ref_id)

  def delete(self, user: dict[str, Any], ref_id: str) -> None:
    r = self._row(user, ref_id)
    for im in r.get("images") or []:
      if im.get("file"):
        try:
          os.remove(self.store.file_path("references", im["file"]))
        except OSError:
          pass
    self.store.delete("design_references", ref_id)

  # ------------------------------------------------------------ analyze
  def analyze(self, user: dict[str, Any], ref_id: str, body: dict) -> dict:
    r = self._row(user, ref_id)
    if r["status"] in ("fetching", "analyzing"):
      raise ApiError(409, f"Reference is {r['status']}; wait for it to finish")
    if not r.get("images") and not r.get("facts"):
      raise ApiError(400, "Nothing to analyze yet")
    focus = body.get("focus") or "all"
    if focus not in FOCUS:
      raise ApiError(400, f"focus must be one of {', '.join(FOCUS)}")
    self.store.update(
        "design_references",
        ref_id,
        {"status": "analyzing", "error": None, "updated_at": util.now_iso()},
    )
    self.executor.submit(
        self._analyze, user, ref_id, focus, str(body.get("instructions") or "")
    )
    return self.get(user, ref_id)

  def _analyze(self, user, ref_id, focus, instructions) -> None:
    try:
      self.run_analysis(user, ref_id, focus, instructions)
    except Exception as e:  # pylint: disable=broad-except
      traceback.print_exc()
      self.store.update(
          "design_references",
          ref_id,
          {
              "status": "error",
              "error": f"Analysis failed: {e}"[:600],
              "updated_at": util.now_iso(),
          },
      )

  def run_analysis(self, user, ref_id, focus="all", instructions="") -> dict:
    """Synchronous analysis; creates the chat message. Returns the reference."""
    r = self.store.get("design_references", ref_id)
    pid = r["project_id"]
    p = self.store.get("projects", pid)
    s = settings_mod.merged(p["settings"])
    cur = self.svc.current_revision(pid)
    cons = cur["text_md"] or ""
    cat = self.svc.catalog_for(p)
    enabled = cat.enabled_components()
    disabled = sorted(set(cat.components) - set(enabled))
    ctx = [
        f"# Current constitution\n\n{cons.strip() or '(empty)'}",
        _theme_digest(s["theme"]),
        "# Catalog\n\nEnabled: "
        + ", ".join(enabled)
        + ("\nDisabled: " + ", ".join(disabled) if disabled else ""),
        f"# Reference\n\nKind: {r['kind']}. Title: {r.get('title') or ''}",
    ]
    if r.get("notes"):
      ctx.append("# Designer's notes\n\n" + r["notes"])
    if r.get("facts"):
      label = (
          "# Stitch facts (design_system values are exact; Tailwind/HTML"
          " values are exact only where they agree with the design system;"
          " html_outline is the screens' semantic structure)"
          if (r["facts"] or {}).get("source") == "stitch"
          else "# Figma facts (exact values from the file)"
      )
      ctx.append(
          label
          + "\n\n```json\n"
          + common.clip_json(r["facts"], 14000)
          + "\n```"
      )
    if focus != "all":
      ctx.append(
          f"# Focus\n\nConcentrate the proposal on {focus}; leave other parts"
          " unchanged unless something is clearly wrong."
      )
    if instructions:
      ctx.append("# Designer's instructions\n\n" + instructions)
    parts: list[Any] = ["\n\n".join(ctx)]
    for i, im in enumerate(r.get("images") or []):
      label = (
          f"img:{i + 1} — {im.get('name')} [viewport:"
          f" {im.get('viewport_hint')}, {im.get('width')}×{im.get('height')}]"
          + (f" — caption: {im['caption']}" if im.get("caption") else "")
      )
      parts.append(label)
      if im.get("file"):
        path = self.store.file_path("references", im["file"])
        try:
          with open(path, "rb") as fh:
            data = fh.read()
          mime = {
              "jpg": "image/jpeg",
              "webp": "image/webp",
              "gif": "image/gif",
          }.get(im["file"].rsplit(".", 1)[-1], "image/png")
          parts.append(Image(data, mime))
        except OSError:
          parts.append("(image file missing)")
    try:
      resp = self.svc.llm.generate(
          purpose="analyze_reference",
          system=common.prompt("analyze_design_reference.md"),
          parts=parts,
          json_schema=ANALYSIS_SCHEMA,
          temperature=0.3,
          model=s["generator"].get("model"),
          timeout_s=300,
      )
    except LlmError as e:
      raise RuntimeError(f"the model is unavailable ({e})") from e
    data = resp.data or {}
    proposal = self._proposal(
        data.get("proposal") or {}, cons, s, cat, facts=r.get("facts")
    )
    observations = data.get("observations") or []
    feedback = data.get("formatting_feedback") or []
    reply = data.get("reply_md") or "Analysis of the design reference."
    now = util.now_iso()
    msg = self.store.insert(
        "chat_messages",
        {
            "id": util.new_id("m"),
            "project_id": pid,
            "role": "assistant",
            "text_md": reply,
            "context": {
                "output_ids": [],
                "guideline_ids": [],
                "reference_ids": [ref_id],
            },
            "proposal": proposal,
            "out_of_scope": data.get("out_of_scope") or [],
            "proposal_status": "pending" if proposal else "none",
            "accepted_revision_id": None,
            "author_ldap": "assistant",
            "created_at": now,
        },
    )
    self.store.update(
        "design_references",
        ref_id,
        {
            "status": "analyzed",
            "analysis": {
                "summary": reply,
                "observations": observations,
                "formatting_feedback": feedback,
                "focus": focus,
                "model": resp.model,
            },
            "chat_message_id": msg["id"],
            "updated_at": now,
        },
    )
    self.svc.touch(pid)
    return self.fmt(self.store.get("design_references", ref_id))

  @staticmethod
  def _proposal(prop, cons, s, cat, facts=None) -> dict[str, Any] | None:
    new_md = (prop.get("constitution_md") or "").strip()
    has_cons = bool(new_md and new_md != cons.strip())
    raw_tc = [t for t in prop.get("theme_changes") or [] if t.get("token")]
    raw_tc = settings_mod.expand_complementary_color_tokens(
        raw_tc,
        facts=facts if isinstance(facts, dict) else None,
        current_theme=s.get("theme") if isinstance(s, dict) else None,
    )
    conf = {
        settings_mod.normalize_token_key(t["token"]): t.get("confidence")
        for t in raw_tc
    }
    _, applied = settings_mod.apply_theme_changes(s["theme"], raw_tc, None)
    preview = []
    for c in applied:
      if c.get("token") == "custom_css":
        continue
      c = dict(c)
      c["confidence"] = conf.get(c.get("token")) or "approx"
      preview.append(c)
    cc = prop.get("catalog_changes") or {}
    names = set(cat.components)
    disabled_now = set(cat.disabled)
    proposed = []
    for pc in cc.get("proposed_components") or []:
      name = re.sub(r"[^A-Za-z0-9]", "", str(pc.get("name") or ""))[:60]
      if not name or name in names:
        continue
      try:
        schema = json.loads(pc.get("props_schema_json") or "{}")
        if not isinstance(schema, dict):
          schema = {}
      except ValueError:
        schema = {}
      proposed.append({
          "name": name,
          "purpose": str(pc.get("purpose") or "")[:500],
          "maps_to_basic": [
              m for m in pc.get("maps_to_basic") or [] if m in names
          ],
          "props_schema": schema,
      })
    catalog_changes = {
        "enable": sorted(
            {n for n in cc.get("enable") or [] if n in disabled_now}
        ),
        "disable": sorted({
            n
            for n in cc.get("disable") or []
            if n in names and n not in disabled_now
        }),
        "proposed_components": proposed,
    }
    has_cat = any(catalog_changes.values())
    if not (
        prop.get("has_proposal", True) and (has_cons or preview or has_cat)
    ):
      return None
    summary = prop.get("change_summary") or "Proposal from design reference"
    return {
        "has_constitution_change": has_cons,
        "constitution_md": (new_md + "\n") if has_cons else "",
        "change_summary": summary,
        "changed_sections": prop.get("changed_sections") or [],
        "diff": (
            diffs.diff(cons, new_md + "\n", "current", "proposed")
            if has_cons
            else None
        ),
        "theme_changes": preview,
        "raw_theme_changes": raw_tc,
        "custom_css": "",
        "catalog_changes": catalog_changes,
    }
