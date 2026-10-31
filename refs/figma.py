"""Stdlib Figma REST client + design-fact extraction.

Fetches top-level frames (PNG renders at 2x), the node tree, published styles
and (Enterprise only, best effort) local variables, then condenses the tree
into frequency-sorted facts the analyzer can cite: colors with their roles,
corner radii, text styles, auto-layout spacing, component instances, and the
text of frames named "Principles".
"""

from __future__ import annotations

import collections
import json
import os
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.figma.com/v1"
TOKEN_FILE = "~/.figma_token"
MAX_FRAMES = 20
VIEWPORT_WIDTHS = {  # frame width → viewport hint (±tolerance)
    "watch": (150, 240),
    "phone": (320, 480),
    "tablet": (600, 900),
    "smart_display": (960, 1100),
    "desktop": (1100, 2000),
}


class FigmaError(RuntimeError):

  def __init__(self, message: str, status: int | None = None):
    super().__init__(message)
    self.status = status


def server_token() -> str | None:
  """FIGMA_API_KEY env var, then `~/.figma_token` (`export FIGMA_API_KEY=…`)."""
  t = os.environ.get("FIGMA_API_KEY", "").strip()
  if t:
    return t
  p = os.path.expanduser(TOKEN_FILE)
  try:
    with open(p, encoding="utf-8") as fh:
      for line in fh:
        m = re.match(
            r"\s*(?:export\s+)?FIGMA_API_KEY\s*=\s*['\"]?([^'\"\s]+)", line
        )
        if m:
          return m.group(1)
  except OSError:
    pass
  return None


def parse_url(url: str) -> tuple[str, list[str]]:
  """Figma URL or bare file key → (file_key, [node ids in `1:2` form])."""
  raw_url = (url or "").strip()
  if re.fullmatch(r"[A-Za-z0-9]{18,32}", raw_url):
    return raw_url, []
  u = urllib.parse.urlsplit(raw_url)
  if "figma.com" not in u.netloc:
    raise FigmaError("Not a figma.com URL")
  parts = [p for p in u.path.split("/") if p]
  if len(parts) < 2 or parts[0] not in ("design", "file"):
    raise FigmaError(
        "Use a Figma design link (figma.com/design/<file>/…); prototype,"
        " FigJam and slides links are not supported"
    )
  key = parts[1]
  q = urllib.parse.parse_qs(u.query)
  ids = []
  for raw in q.get("node-id", []):
    for nid in raw.split(","):
      nid = nid.strip().replace("-", ":")
      if re.fullmatch(r"\d+:\d+", nid):
        ids.append(nid)
  return key, ids


def parse_source_url(url: str) -> dict[str, Any]:
  """Parses a Figma file, project folder, or team URL / ID."""
  raw = (url or "").strip()
  m_team = re.fullmatch(r"team:(\d{5,30})", raw, re.I)
  if m_team:
    tid = m_team.group(1)
    return {
        "kind": "team",
        "id": tid,
        "url": f"https://www.figma.com/files/team/{tid}",
    }
  m_proj = re.fullmatch(r"project:(\d{5,30})", raw, re.I)
  if m_proj:
    pid = m_proj.group(1)
    return {
        "kind": "project",
        "id": pid,
        "url": f"https://www.figma.com/files/project/{pid}",
    }
  if "figma.com" in raw:
    u = urllib.parse.urlsplit(raw)
    m_p = re.search(r"/project/(\d+)", u.path)
    if m_p:
      pid = m_p.group(1)
      return {
          "kind": "project",
          "id": pid,
          "url": f"https://www.figma.com/files/project/{pid}",
      }
    m_t = re.search(r"/team/(\d+)", u.path)
    if m_t:
      tid = m_t.group(1)
      return {
          "kind": "team",
          "id": tid,
          "url": f"https://www.figma.com/files/team/{tid}",
      }
  key, ids = parse_url(raw)
  slug = ""
  if "figma.com" in raw:
    m_s = re.search(r"/(?:design|file)/[^/?#]+/([^/?#]+)", raw)
    if m_s:
      slug = re.sub(r"[-_]+", " ", urllib.parse.unquote(m_s.group(1))).strip()
  return {
      "kind": "file",
      "key": key,
      "node_ids": ids,
      "name": slug,
      "url": f"https://www.figma.com/design/{key}",
  }


def viewport_hint(width: float) -> str:
  for name, (lo, hi) in VIEWPORT_WIDTHS.items():
    if lo <= width <= hi:
      return name
  return "unknown"


class FigmaClient:

  def __init__(self, token: str, timeout_s: float = 60.0):
    if not token:
      raise FigmaError("No Figma token. Add a personal access token first.")
    self.token = token
    self.timeout_s = timeout_s

  def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
    url = API + path
    if params:
      url += "?" + urllib.parse.urlencode(
          {k: v for k, v in params.items() if v is not None}
      )
    req = urllib.request.Request(url, headers={"X-Figma-Token": self.token})
    try:
      with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
        return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
      msg = {
          403: (
              "Figma refused the request (token expired, missing scope, or"
              " no access to this file)"
          ),
          404: "Figma file or node not found",
          429: "Figma rate limit hit; try again in a minute",
      }.get(e.code, f"Figma API error HTTP {e.code}")
      raise FigmaError(msg, e.code) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
      raise FigmaError(f"Could not reach api.figma.com: {e}") from e

  def download(self, url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=self.timeout_s) as r:
      return r.read()

  def file_meta(self, key: str) -> dict[str, Any]:
    return self._get(f"/files/{key}", {"depth": 2})

  def nodes(self, key: str, ids: list[str]) -> dict[str, Any]:
    return self._get(f"/files/{key}/nodes", {"ids": ",".join(ids)})

  def images(
      self, key: str, ids: list[str], scale: float = 2
  ) -> dict[str, str]:
    res = self._get(
        f"/images/{key}",
        {"ids": ",".join(ids), "format": "png", "scale": scale},
    )
    return res.get("images") or {}

  def styles(self, key: str) -> list[dict[str, Any]]:
    try:
      return (self._get(f"/files/{key}/styles").get("meta") or {}).get(
          "styles"
      ) or []
    except FigmaError:
      return []

  def variables(self, key: str) -> dict[str, Any] | None:
    try:
      return self._get(f"/files/{key}/variables/local").get("meta")
    except FigmaError:
      return None  # Enterprise-only; 403 elsewhere

  def team_projects(self, team_id: str) -> dict[str, Any]:
    return self._get(f"/teams/{team_id}/projects")

  def project_files(self, project_id: str) -> dict[str, Any]:
    return self._get(f"/projects/{project_id}/files")


def _norm_project_file(
    f: dict[str, Any],
    project_id: str | None = None,
    project_name: str = "",
    team_id: str | None = None,
    team_name: str = "",
) -> dict[str, Any] | None:
  key = str(f.get("key") or "").strip()
  if not key:
    return None
  return {
      "key": key,
      "name": str(f.get("name") or "").strip() or f"File {key[:8]}",
      "url": f"https://www.figma.com/design/{key}",
      "thumbnail_url": f.get("thumbnail_url") or f.get("thumbnailUrl") or None,
      "last_modified": f.get("last_modified") or f.get("lastModified") or "",
      "project_id": str(project_id) if project_id else None,
      "project_name": project_name or "",
      "team_id": str(team_id) if team_id else None,
      "team_name": team_name or "",
  }


def list_source_files(token: str, src: dict[str, Any]) -> dict[str, Any]:
  """Fetches files for a Figma team, project folder, or single file source."""
  import concurrent.futures  # pylint: disable=g-import-not-at-top

  c = FigmaClient(token)
  kind = src.get("kind")
  if kind == "team":
    tid = str(src.get("id") or "")
    res = c.team_projects(tid)
    team_name = str(res.get("name") or "").strip() or f"Team {tid}"
    projs = [
        p
        for p in res.get("projects") or []
        if isinstance(p, dict) and p.get("id")
    ][:12]
    files: list[dict[str, Any]] = []

    def fetch_proj(p):
      pid = str(p["id"])
      pname = str(p.get("name") or "").strip()
      try:
        pf = c.project_files(pid)
        return pid, pname or str(pf.get("name") or ""), pf.get("files") or []
      except FigmaError:
        return pid, pname, []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
      for pid, pname, raw_files in ex.map(fetch_proj, projs):
        for rf in raw_files:
          if isinstance(rf, dict):
            nf = _norm_project_file(rf, pid, pname, tid, team_name)
            if nf:
              files.append(nf)
    return {
        "source": {
            "kind": "team",
            "id": tid,
            "name": team_name,
            "url": f"https://www.figma.com/files/team/{tid}",
            "project_count": len(projs),
        },
        "files": files,
    }
  if kind == "project":
    pid = str(src.get("id") or "")
    res = c.project_files(pid)
    pname = str(res.get("name") or "").strip() or f"Project {pid}"
    files = []
    for rf in res.get("files") or []:
      if isinstance(rf, dict):
        nf = _norm_project_file(rf, pid, pname)
        if nf:
          files.append(nf)
    return {
        "source": {
            "kind": "project",
            "id": pid,
            "name": pname,
            "url": f"https://www.figma.com/files/project/{pid}",
        },
        "files": files,
    }
  key = str(src.get("key") or "")
  doc = c._get(f"/files/{key}", {"depth": 1})  # pylint: disable=protected-access
  pages = ((doc.get("document") or {}).get("children")) or []
  fname = (
      str(doc.get("name") or "").strip()
      or str(src.get("name") or "").strip()
      or f"File {key[:8]}"
  )
  entry = {
      "key": key,
      "name": fname,
      "url": f"https://www.figma.com/design/{key}",
      "thumbnail_url": doc.get("thumbnailUrl") or None,
      "last_modified": doc.get("lastModified") or "",
      "page_count": len(pages),
  }
  return {
      "source": {
          "kind": "file",
          "key": key,
          "name": fname,
          "url": entry["url"],
      },
      "files": [entry],
  }


def list_file_frames(token: str, url: str) -> dict[str, Any]:
  """Lists pages and top-level sections/frames of a Figma file (`depth=2`)."""
  key, pre_ids = parse_url(url)
  c = FigmaClient(token)
  doc = c._get(f"/files/{key}", {"depth": 2})  # pylint: disable=protected-access
  pages_out: list[dict[str, Any]] = []
  for p in ((doc.get("document") or {}).get("children")) or []:
    if not isinstance(p, dict) or p.get("type") != "CANVAS":
      continue
    pname = str(p.get("name") or "").strip()
    is_divider = bool(not pname or re.fullmatch(r"[-—–_=~\s•·*]+", pname))
    items: list[dict[str, Any]] = []
    for ch in p.get("children") or []:
      if not isinstance(ch, dict) or ch.get("visible") is False:
        continue
      ct = ch.get("type")
      if ct not in (
          "FRAME",
          "SECTION",
          "COMPONENT_SET",
          "COMPONENT",
          "INSTANCE",
      ):
        continue
      box = ch.get("absoluteBoundingBox") or {}
      w = round(box.get("width") or 0)
      h = round(box.get("height") or 0)
      items.append({
          "id": ch.get("id"),
          "name": str(ch.get("name") or ch.get("id") or ""),
          "type": ct,
          "width": w,
          "height": h,
          "viewport_hint": viewport_hint(w) if w else "unknown",
      })
    pages_out.append({
        "id": p.get("id"),
        "name": pname or "Untitled page",
        "is_divider": is_divider,
        "frames": items,
    })
  return {
      "file": {
          "key": key,
          "name": str(doc.get("name") or "").strip(),
          "last_modified": doc.get("lastModified") or "",
          "thumbnail_url": doc.get("thumbnailUrl") or None,
          "url": f"https://www.figma.com/design/{key}",
      },
      "pages": pages_out,
      "preselect": pre_ids,
  }


def top_frames(
    file_doc: dict[str, Any], ids: list[str], nodes_doc: dict | None
):
  """Frames to render: the linked nodes (or their child frames when a node is a

  page/section), else every top-level frame of the first page.
  """
  out = []

  def add_frames(node):
    t = node.get("type")
    if t in ("FRAME", "COMPONENT", "COMPONENT_SET", "INSTANCE"):
      out.append(node)
    elif t in ("CANVAS", "SECTION", "GROUP"):
      for c in node.get("children") or []:
        add_frames(c)

  if ids and nodes_doc:
    for nid in ids:
      doc = ((nodes_doc.get("nodes") or {}).get(nid) or {}).get("document")
      if doc:
        add_frames(doc)
    if not out:
      for nid in ids:
        doc = ((nodes_doc.get("nodes") or {}).get(nid) or {}).get("document")
        if (
            doc
            and doc.get("type") in ("SECTION", "GROUP")
            and doc.get("absoluteBoundingBox")
        ):
          out.append(doc)
  else:
    pages = (file_doc.get("document") or {}).get("children") or []
    if pages:
      add_frames(pages[0])
  seen, uniq = set(), []
  for f in out:
    if f["id"] not in seen:
      seen.add(f["id"])
      uniq.append(f)
  return uniq[:MAX_FRAMES]


def _hex(c: dict[str, Any], opacity: float = 1.0) -> str:
  r, g, b = (round(max(0, min(1, c.get(k, 0))) * 255) for k in "rgb")
  a = c.get("a", 1) * opacity
  base = f"#{r:02X}{g:02X}{b:02X}"
  return base if a >= 0.99 else base + f"{round(a * 255):02X}"


def extract_facts(
    frames: list[dict[str, Any]],
    styles: list[dict[str, Any]] | None = None,
    variables: dict[str, Any] | None = None,
    file_name: str = "",
) -> dict[str, Any]:
  colors: dict[str, dict[str, Any]] = {}
  radii: dict[float, dict[str, Any]] = {}
  text_styles: dict[tuple, dict[str, Any]] = {}
  item_spacing: collections.Counter = collections.Counter()
  padding: collections.Counter = collections.Counter()
  components: collections.Counter = collections.Counter()
  principles: list[str] = []
  frame_contents: list[dict[str, Any]] = []

  def role_name(node, kind):
    return f"{kind}:{node.get('name', '')[:40]}"

  def walk(
      node,
      depth=0,
      in_principles=False,
      frame_texts: list[str] | None = None,
  ):
    if depth > 40 or node.get("visible") is False:
      return
    name = node.get("name") or ""
    in_principles = in_principles or name.strip().lower().startswith(
        "principle"
    )
    t = node.get("type")
    for kind, key in (("fill", "fills"), ("stroke", "strokes")):
      for p in node.get(key) or []:
        if p.get("type") == "SOLID" and p.get("visible", True):
          hx = _hex(p.get("color") or {}, p.get("opacity", 1))
          ent = colors.setdefault(hx, {"hex": hx, "count": 0, "roles": []})
          ent["count"] += 1
          role = role_name(node, "text" if t == "TEXT" else kind)
          if role not in ent["roles"] and len(ent["roles"]) < 6:
            ent["roles"].append(role)
    r = node.get("cornerRadius")
    if isinstance(r, (int, float)) and r > 0:
      ent = radii.setdefault(r, {"px": r, "count": 0, "on": []})
      ent["count"] += 1
      if name and name not in ent["on"] and len(ent["on"]) < 6:
        ent["on"].append(name[:40])
    if t == "TEXT":
      st = node.get("style") or {}
      key = (
          st.get("fontFamily"),
          st.get("fontSize"),
          st.get("fontWeight"),
          round(st.get("lineHeightPx") or 0),
      )
      ent = text_styles.setdefault(
          key,
          {
              "name": name[:40],
              "family": st.get("fontFamily"),
              "size": st.get("fontSize"),
              "weight": st.get("fontWeight"),
              "line_height": round(st.get("lineHeightPx") or 0) or None,
              "count": 0,
          },
      )
      ent["count"] += 1
      chars = (node.get("characters") or "").strip()
      if frame_texts is not None and chars and len(frame_texts) < 40:
        cleaned = " ".join(chars.split())[:200]
        if cleaned and cleaned not in frame_texts:
          frame_texts.append(cleaned)
      if in_principles:
        for line in (node.get("characters") or "").splitlines():
          line = line.strip(" •-–\t")
          if len(line) > 3 and line not in principles and len(principles) < 40:
            principles.append(line[:300])
    if node.get("layoutMode") in ("HORIZONTAL", "VERTICAL"):
      if node.get("itemSpacing"):
        item_spacing[node["itemSpacing"]] += 1
      for k in ("paddingLeft", "paddingTop"):
        if node.get(k):
          padding[node[k]] += 1
    if t == "INSTANCE":
      components[name[:60]] += 1
    for c in node.get("children") or []:
      walk(c, depth + 1, in_principles, frame_texts)

  for f in frames:
    ftexts: list[str] = []
    walk(f, frame_texts=ftexts)
    if ftexts:
      frame_contents.append({
          "id": f.get("id") or "",
          "name": f.get("name") or "",
          "texts": ftexts,
      })
  spacings = [v for v, _ in item_spacing.most_common(8)]
  grid = None
  for g in (8, 4):
    if spacings and all(float(v) % g == 0 for v in spacings):
      grid = g
      break
  var_count = 0
  if variables:
    var_count = len(variables.get("variables") or {})
  return {
      "file_name": file_name,
      "frames": [
          {
              "id": f["id"],
              "name": f.get("name", ""),
              "width": round(
                  ((f.get("absoluteBoundingBox") or {}).get("width") or 0)
              ),
              "height": round(
                  ((f.get("absoluteBoundingBox") or {}).get("height") or 0)
              ),
              "viewport_hint": viewport_hint(
                  (f.get("absoluteBoundingBox") or {}).get("width") or 0
              ),
          }
          for f in frames
      ],
      "colors": sorted(colors.values(), key=lambda c: -c["count"])[:24],
      "radii": sorted(radii.values(), key=lambda r: -r["count"])[:10],
      "text_styles": sorted(text_styles.values(), key=lambda t: -t["count"])[
          :16
      ],
      "spacing": {
          "item_spacing": spacings,
          "padding": [v for v, _ in padding.most_common(6)],
          "grid_hint": grid,
      },
      "components": [
          {"name": n, "instances": c} for n, c in components.most_common(24)
      ],
      "principles_text": principles,
      "frame_contents": frame_contents[:24],
      "styles": [
          {"name": s.get("name"), "type": s.get("style_type")}
          for s in (styles or [])[:60]
      ],
      "variables": {"available": variables is not None, "count": var_count},
  }


def fetch(token: str, url: str, on_progress: Any = None) -> dict[str, Any]:
  """URL → {file_key, node_ids, file_name, frames:[{id,name,width,height,

  viewport_hint,png:bytes|None,node:trimmed subtree|None}], facts}.
  """
  key, ids = parse_url(url)
  if on_progress:
    on_progress({
        "step": "fetch_file",
        "label": "Fetching Figma document & frame nodes…",
        "detail": f"File {key}" + (f" · {len(ids)} node(s)" if ids else ""),
    })
  c = FigmaClient(token)
  file_doc = c.file_meta(key) if not ids else {}
  nodes_doc = c.nodes(key, ids) if ids else None
  if ids and not (nodes_doc or {}).get("nodes"):
    raise FigmaError("None of the linked nodes were found in the file")
  frames = top_frames(file_doc, ids, nodes_doc)
  if not frames:
    raise FigmaError(
        "No frames found. Link a page or frames that contain top-level frames"
    )
  file_name = file_doc.get("name") or (nodes_doc or {}).get("name") or ""
  # The depth=2 file fetch has no subtree; refetch the frames fully.
  if not ids:
    if on_progress:
      on_progress({
          "step": "fetch_file",
          "label": (
              f"Loading node tree for {len(frames)} frame"
              f"{'s' if len(frames) != 1 else ''}…"
          ),
          "detail": file_name or f"File {key}",
      })
    full = c.nodes(key, [f["id"] for f in frames])
    frames = [
        ((full.get("nodes") or {}).get(f["id"]) or {}).get("document") or f
        for f in frames
    ]
  # Full subtrees by frame id: trimmed copies travel with the frames so
  # imports can score component regions and resolve boxes later.
  full_by_id = {f.get("id"): f for f in frames if isinstance(f, dict)}
  if on_progress:
    on_progress({
        "step": "fetch_file",
        "label": "Extracting Figma styles, variables & layout tokens…",
        "detail": (
            f"{file_name or key} · {len(frames)} frame"
            f"{'s' if len(frames) != 1 else ''}"
        ),
    })
  facts = extract_facts(frames, c.styles(key), c.variables(key), file_name)
  total = len(facts["frames"])
  if on_progress:
    on_progress({
        "step": "download_frames",
        "label": (
            f"Rendering {total} frame{'s' if total != 1 else ''} in Figma"
            " (2× PNG)…"
        ),
        "detail": file_name or f"File {key}",
        "done": 0,
        "total": total,
        "file_name": file_name,
        "facts": facts,
    })
  pngs = {}
  try:
    urls = c.images(key, [f["id"] for f in frames])
    for idx, fr in enumerate(facts["frames"]):
      fid = fr["id"]
      u = urls.get(fid)
      if u:
        try:
          pngs[fid] = c.download(u)
        except (urllib.error.URLError, OSError):
          pass
      if on_progress:
        on_progress({
            "step": "download_frames",
            "label": f"Downloaded frame {idx + 1} of {total} from Figma…",
            "detail": (
                f"“{fr.get('name') or fid}”"
                f" ({fr.get('width')}×{fr.get('height')})"
            ),
            "done": idx + 1,
            "total": total,
            "frame_index": idx,
            "frame": fr,
            "png": pngs.get(fid),
        })
  except FigmaError:
    pass
  return {
      "file_key": key,
      "node_ids": ids,
      "file_name": file_name,
      "frames": [
          dict(
              fr,
              png=pngs.get(fr["id"]),
              node=trim_node_tree(full_by_id.get(fr["id"])),
          )
          for fr in facts["frames"]
      ],
      "facts": facts,
  }


# Node keys the deterministic compilers (`refs/figma_compiler.py`) and the
# Basic-fit scoring read; everything else (vector paths, constraints, export
# settings, …) is dropped so a frame subtree stays small enough to keep next
# to the import's images.
_TRIM_KEYS = (
    "id",
    "name",
    "type",
    "visible",
    "absoluteBoundingBox",
    "layoutMode",
    "layoutPositioning",
    "itemSpacing",
    "paddingTop",
    "paddingRight",
    "paddingBottom",
    "paddingLeft",
    "primaryAxisAlignItems",
    "counterAxisAlignItems",
    "cornerRadius",
    "rectangleCornerRadii",
    "rotation",
    "isMask",
    "opacity",
    "strokeWeight",
    "strokeDashes",
    "individualStrokeWeights",
    "characters",
    "componentPropertyReferences",
)
_TRIM_STYLE_KEYS = ("fontFamily", "fontSize", "fontWeight", "lineHeightPx")
TRIM_MAX_NODES = 3000


def trim_node_tree(
    node: dict[str, Any] | None, max_nodes: int = TRIM_MAX_NODES
) -> dict[str, Any] | None:
  """Copies a Figma node subtree keeping only compiler/fit-relevant fields.

  Children beyond `max_nodes` (depth-first) are dropped; the result is what
  imports store per frame (`images[i].nodes_file`) so merge plans can score
  the Basic fit of a component region and resolve boxes to nodes without
  refetching Figma.
  """
  if not isinstance(node, dict):
    return None
  budget = [max_nodes]

  def paint(p: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"type": p.get("type")}
    for k in ("color", "opacity", "visible"):
      if k in p:
        out[k] = p[k]
    return out

  def walk(n: dict[str, Any], depth: int) -> dict[str, Any] | None:
    if budget[0] <= 0 or depth > 16:
      return None
    budget[0] -= 1
    out: dict[str, Any] = {}
    for k in _TRIM_KEYS:
      if k in n:
        out[k] = n[k]
    if isinstance(n.get("fills"), list):
      out["fills"] = [paint(p) for p in n["fills"] if isinstance(p, dict)][:4]
    if isinstance(n.get("strokes"), list):
      out["strokes"] = [paint(p) for p in n["strokes"] if isinstance(p, dict)][
          :2
      ]
    if isinstance(n.get("effects"), list):
      out["effects"] = [
          {
              k: e.get(k)
              for k in ("type", "visible", "radius", "offset")
              if k in e
          }
          for e in n["effects"]
          if isinstance(e, dict)
      ][:4]
    if isinstance(n.get("style"), dict):
      out["style"] = {
          k: n["style"][k] for k in _TRIM_STYLE_KEYS if k in n["style"]
      }
    if n.get("characterStyleOverrides"):
      out["characterStyleOverrides"] = True
    kids = []
    for c in n.get("children") or []:
      if isinstance(c, dict):
        t = walk(c, depth + 1)
        if t is not None:
          kids.append(t)
    if kids:
      out["children"] = kids
    return out

  return walk(node, 0)


# ---------------------------------------------------------------- components
MAX_SCAN = 80  # components listed by a scan
SCAN_DEPTH = 5  # file tree depth searched when the URL names no node
ATOM_MAX = 96  # px; single components smaller than this are "atoms"


def _private(name: str) -> bool:
  n = (name or "").strip()
  return n.startswith((".", "_"))


def _prop_defs(node: dict[str, Any]) -> dict[str, Any]:
  out = {}
  for raw, d in (node.get("componentPropertyDefinitions") or {}).items():
    if not isinstance(d, dict):
      continue
    name = raw.split("#", 1)[0].strip() or raw
    ent = {"type": d.get("type"), "default": d.get("defaultValue")}
    if d.get("variantOptions"):
      ent["options"] = list(d["variantOptions"])[:20]
    out[name] = ent
  return out


def _variant_props(node: dict[str, Any]) -> dict[str, list[str]]:
  props: dict[str, list[str]] = {}
  for c in node.get("children") or []:
    for part in str(c.get("name") or "").split(","):
      if "=" in part:
        k, v = (x.strip() for x in part.split("=", 1))
        vals = props.setdefault(k, [])
        if v not in vals and len(vals) < 20:
          vals.append(v)
  for k, d in _prop_defs(node).items():
    if d.get("type") == "VARIANT" and d.get("options"):
      props.setdefault(k, d["options"])
  return props


def find_components(
    roots: list[dict[str, Any]], meta: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
  """COMPONENT_SETs and standalone COMPONENTs under `roots` (tree order)."""
  meta = meta or {}
  comp_meta = meta.get("components") or {}
  set_meta = meta.get("componentSets") or {}
  out: list[dict[str, Any]] = []
  seen: set[str] = set()

  def walk(node, depth=0):
    if depth > 40 or len(out) >= MAX_SCAN:
      return
    t = node.get("type")
    nid = node.get("id")
    if t in ("COMPONENT_SET", "COMPONENT") and nid not in seen:
      seen.add(nid)
      box = node.get("absoluteBoundingBox") or {}
      kids = node.get("children") or []
      default = kids[0] if t == "COMPONENT_SET" and kids else node
      dbox = default.get("absoluteBoundingBox") or box
      desc = (
          node.get("description")
          or (set_meta.get(nid) or comp_meta.get(nid) or {}).get("description")
          or ""
      )
      out.append({
          "node_id": nid,
          "name": node.get("name") or "",
          "kind": t,
          "description": str(desc)[:500],
          "width": round(dbox.get("width") or 0),
          "height": round(dbox.get("height") or 0),
          "variant_count": len(kids) if t == "COMPONENT_SET" else 1,
          "variant_props": _variant_props(node) if t == "COMPONENT_SET" else {},
          "property_defs": _prop_defs(node),
          "default_variant_id": default.get("id"),
          # Preselect component sets and component-sized singles; leave
          # atoms (icons, dots, small chips) and private parts unselected.
          "suggested": (
              not _private(node.get("name"))
              and (node.get("name") or "").strip() not in ("", "-")
              and (
                  t == "COMPONENT_SET"
                  or (
                      (dbox.get("width") or 0) >= ATOM_MAX
                      and (dbox.get("height") or 0) >= ATOM_MAX * 0.6
                  )
              )
          ),
      })
      return  # variants inside a set are not listed separately
    for c in node.get("children") or []:
      walk(c, depth + 1)

  for r in roots:
    walk(r)
  return out


def scan_components(token: str, url: str) -> dict[str, Any]:
  """Figma URL → {file_key, file_name, components:[…], truncated}."""
  key, ids = parse_url(url)
  c = FigmaClient(token)
  if ids:
    doc = c.nodes(key, ids)
    nodes = doc.get("nodes") or {}
    roots, meta = [], {"components": {}, "componentSets": {}}
    for nid in ids:
      ent = nodes.get(nid) or {}
      if ent.get("document"):
        roots.append(ent["document"])
      meta["components"].update(ent.get("components") or {})
      meta["componentSets"].update(ent.get("componentSets") or {})
    if not roots:
      raise FigmaError("None of the linked nodes were found in the file")
    file_name = doc.get("name") or ""
  else:
    doc = c._get(f"/files/{key}", {"depth": SCAN_DEPTH})  # pylint: disable=protected-access
    roots = (doc.get("document") or {}).get("children") or []
    meta = doc
    file_name = doc.get("name") or ""
  comps = find_components(roots, meta)
  return {
      "file_key": key,
      "file_name": file_name,
      "components": comps,
      "truncated": len(comps) >= MAX_SCAN,
  }


def _nearest_token(hx: str, palette: dict[str, str]) -> str:
  def rgb(h):
    h = h.lstrip("#")[:6]
    try:
      return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
      return None

  a = rgb(hx)
  if not a or not palette:
    return hx
  best, dist = None, 1e9
  for k, v in palette.items():
    b = rgb(str(v))
    if not b:
      continue
    d = sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
    if d < dist:
      best, dist = k, d
  if best is None:
    return hx
  if dist < 3:
    return f"{hx} = {best}"
  if dist < 40:
    return f"{hx} ≈ {best} (Δ{round(dist)})"
  return f"{hx} (no close token; nearest {best} Δ{round(dist)})"


def component_outline(
    node: dict[str, Any],
    palette: dict[str, str] | None = None,
    max_nodes: int = 140,
) -> dict[str, Any]:
  """Condensed node tree of one component for the builder prompt."""
  palette = palette or {}
  count = [0]

  def fill_of(n):
    for p in n.get("fills") or []:
      if p.get("visible", True) is False:
        continue
      if p.get("type") == "SOLID":
        return _nearest_token(
            _hex(p.get("color") or {}, p.get("opacity", 1)), palette
        )
      if p.get("type") == "IMAGE":
        return "image"
      if str(p.get("type", "")).startswith("GRADIENT"):
        return p.get("type").lower()
    return None

  def walk(n, depth=0):
    if count[0] >= max_nodes or depth > 8 or n.get("visible") is False:
      return None
    count[0] += 1
    box = n.get("absoluteBoundingBox") or {}
    out: dict[str, Any] = {
        "type": n.get("type"),
        "name": (n.get("name") or "")[:50],
        "size": [round(box.get("width") or 0), round(box.get("height") or 0)],
    }
    if n.get("layoutMode") in ("HORIZONTAL", "VERTICAL"):
      out["layout"] = {
          "dir": "row" if n["layoutMode"] == "HORIZONTAL" else "column",
          "gap": n.get("itemSpacing") or 0,
          "pad": [
              n.get("paddingTop") or 0,
              n.get("paddingRight") or 0,
              n.get("paddingBottom") or 0,
              n.get("paddingLeft") or 0,
          ],
          "main": n.get("primaryAxisAlignItems"),
          "cross": n.get("counterAxisAlignItems"),
      }
    f = fill_of(n)
    if f and n.get("type") != "TEXT":
      out["fill"] = f
    for s in n.get("strokes") or []:
      if s.get("type") == "SOLID" and s.get("visible", True):
        out["stroke"] = _nearest_token(_hex(s.get("color") or {}), palette)
        out["stroke_w"] = n.get("strokeWeight")
        break
    if n.get("cornerRadius"):
      out["radius"] = n["cornerRadius"]
    for e in n.get("effects") or []:
      if e.get("type") == "DROP_SHADOW" and e.get("visible", True):
        out["shadow"] = {
            "y": (e.get("offset") or {}).get("y"),
            "blur": e.get("radius"),
        }
        break
    if n.get("type") == "TEXT":
      st = n.get("style") or {}
      out["text"] = (n.get("characters") or "")[:80]
      out["font"] = {
          "family": st.get("fontFamily"),
          "size": st.get("fontSize"),
          "weight": st.get("fontWeight"),
          "line": round(st.get("lineHeightPx") or 0) or None,
      }
      if f:
        out["color"] = f
    if n.get("type") == "INSTANCE":
      out["instance_of"] = (n.get("name") or "")[:50]
    refs = n.get("componentPropertyReferences") or {}
    if refs:
      out["bound_to"] = {k: v.split("#", 1)[0] for k, v in refs.items()}
    kids = []
    if n.get("type") not in ("TEXT", "VECTOR", "BOOLEAN_OPERATION"):
      for c in n.get("children") or []:
        k = walk(c, depth + 1)
        if k:
          kids.append(k)
    if kids:
      out["children"] = kids
    return out

  return walk(node) or {}


def component_detail(
    token: str, file_key: str, node_id: str, palette: dict[str, str] | None
) -> dict[str, Any]:
  """Full detail for one component: outline, props, variants, PNGs."""
  c = FigmaClient(token)
  doc = c.nodes(file_key, [node_id])
  ent = (doc.get("nodes") or {}).get(node_id) or {}
  node = ent.get("document")
  if not node:
    raise FigmaError(f"Component {node_id} not found")
  found = find_components([node], ent)
  info = (
      found[0]
      if found
      else {
          "node_id": node_id,
          "name": node.get("name"),
          "kind": node.get("type"),
          "property_defs": {},
          "variant_props": {},
          "default_variant_id": node_id,
          "width": 0,
          "height": 0,
          "description": "",
          "variant_count": 1,
      }
  )
  default_id = info.get("default_variant_id") or node_id
  default = node
  if node.get("type") == "COMPONENT_SET":
    for k in node.get("children") or []:
      if k.get("id") == default_id:
        default = k
        break
  pngs: dict[str, bytes] = {}
  want = [default_id] + ([node_id] if node_id != default_id else [])
  try:
    for nid, u in c.images(file_key, want).items():
      if u:
        try:
          pngs[nid] = c.download(u)
        except (urllib.error.URLError, OSError):
          pass
  except FigmaError:
    pass
  variants = []
  if node.get("type") == "COMPONENT_SET":
    for k in (node.get("children") or [])[:12]:
      variants.append(k.get("name"))
  return {
      "info": info,
      "outline": component_outline(default, palette),
      "raw_node": default,
      "variants": variants,
      "reference_png": pngs.get(default_id),
      "set_png": pngs.get(node_id) if node_id != default_id else None,
  }


def resolve_box_node(
    frame_node: dict[str, Any] | None,
    box_pct: dict[str, Any] | None,
    *,
    palette: dict[str, str] | None = None,
) -> dict[str, Any] | None:
  """Resolves a percentage bounding box `{x, y, w, h}` on `frame_node` to the best child Figma node.

  Returns `{node_id, name, kind, width, height, node, outline}` or `None` when
  no child node overlaps the target region with sufficient IoU/containment.
  """
  if not isinstance(frame_node, dict):
    return None
  if isinstance(box_pct, (list, tuple)) and len(box_pct) == 4:
    box_pct = {
        "x": box_pct[0],
        "y": box_pct[1],
        "w": box_pct[2],
        "h": box_pct[3],
    }
  elif not isinstance(box_pct, dict):
    return None
  fbox = frame_node.get("absoluteBoundingBox") or {}
  fw = float(fbox.get("width") or 0)
  fh = float(fbox.get("height") or 0)
  if fw <= 0 or fh <= 0:
    return None
  fx = float(fbox.get("x") or 0)
  fy = float(fbox.get("y") or 0)
  try:
    bx = float(box_pct.get("x") or 0)
    by = float(box_pct.get("y") or 0)
    bw = float(box_pct.get("w") or 0)
    bh = float(box_pct.get("h") or 0)
  except (TypeError, ValueError):
    return None
  if bw <= 0 or bh <= 0:
    return None

  tx0 = fx + (bx / 100.0) * fw
  ty0 = fy + (by / 100.0) * fh
  tx1 = tx0 + (bw / 100.0) * fw
  ty1 = ty0 + (bh / 100.0) * fh
  t_area = max(1.0, (tx1 - tx0) * (ty1 - ty0))

  best_node: dict[str, Any] | None = None
  best_score = 0.0

  def walk(n: dict[str, Any], depth: int = 0) -> None:
    nonlocal best_node, best_score
    if depth > 12 or n.get("visible") is False:
      return
    if depth > 0:
      nbox = n.get("absoluteBoundingBox") or {}
      nw = float(nbox.get("width") or 0)
      nh = float(nbox.get("height") or 0)
      if nw > 8 and nh > 8:
        nx0 = float(nbox.get("x") or 0)
        ny0 = float(nbox.get("y") or 0)
        nx1 = nx0 + nw
        ny1 = ny0 + nh
        ix0 = max(tx0, nx0)
        iy0 = max(ty0, ny0)
        ix1 = min(tx1, nx1)
        iy1 = min(ty1, ny1)
        if ix1 > ix0 and iy1 > iy0:
          inter = (ix1 - ix0) * (iy1 - iy0)
          n_area = max(1.0, nw * nh)
          union = t_area + n_area - inter
          iou = inter / max(1.0, union)
          ntype = str(n.get("type") or "")
          bonus = (
              0.12
              if ntype in ("COMPONENT", "COMPONENT_SET", "INSTANCE")
              else (
                  0.05
                  if n.get("layoutMode") in ("HORIZONTAL", "VERTICAL")
                  else 0.0
              )
          )
          score = iou + bonus
          if iou >= 0.35 and score > best_score:
            best_score = score
            best_node = n
    for c in n.get("children") or []:
      if isinstance(c, dict):
        walk(c, depth + 1)

  walk(frame_node, 0)
  if not best_node:
    return None
  b = best_node.get("absoluteBoundingBox") or {}
  return {
      "node_id": best_node.get("id") or "",
      "name": best_node.get("name") or "",
      "kind": best_node.get("type") or "FRAME",
      "width": round(float(b.get("width") or 0)),
      "height": round(float(b.get("height") or 0)),
      "node": best_node,
      "outline": component_outline(best_node, palette),
  }


def thumbnails(token: str, file_key: str, ids: list[str]) -> dict[str, bytes]:
  """node id → PNG bytes (scale 1) for scan thumbnails (best effort)."""
  import concurrent.futures  # pylint: disable=g-import-not-at-top

  c = FigmaClient(token)
  out: dict[str, bytes] = {}
  for i in range(0, len(ids), 40):
    try:
      urls = c.images(file_key, ids[i : i + 40], scale=1)
    except FigmaError:
      continue

    def dl(item):
      nid, u = item
      try:
        return nid, c.download(u) if u else None
      except (urllib.error.URLError, OSError):
        return nid, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
      for nid, data in ex.map(dl, urls.items()):
        if data:
          out[nid] = data
  return out
