"""Stitch (stitch.withgoogle.com) import via the Stitch MCP server.

Uses the generic Streamable HTTP MCP client with the user's Stitch API key in
`X-Goog-Api-Key`. Screens carry a screenshot and HTML (`File.download_url`);
downloads may return raw bytes, a JSON `DownloadFileResponse` wrapper
(`dataString`: raw HTML, or base64 for images), or — for private projects
a redirect to Google sign-in, which raises
`StitchDownloadBlocked` so callers can fall back (e.g. render the HTML).
"""

from __future__ import annotations

import base64
import collections
import html.parser
import json
import os
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from mcp.client import McpClient, McpError
from refs import figma

STITCH_MCP = "https://stitch.googleapis.com/mcp"
KEY_FILE = "~/.stitch_api_key"
MAX_SCREENS = 20
MAX_DOWNLOAD = 25 * 1024 * 1024
FONT_NAMES = {
    "OPEN_SANS": "Open Sans",
    "NOTO_SANS": "Noto Sans",
    "NOTO_SERIF": "Noto Serif",
    "PLUS_JAKARTA_SANS": "Plus Jakarta Sans",
    "IBM_PLEX_SANS": "IBM Plex Sans",
    "SPACE_GROTESK": "Space Grotesk",
    "DM_SANS": "DM Sans",
    "SOURCE_SANS_THREE": "Source Sans 3",
    "BE_VIETNAM_PRO": "Be Vietnam Pro",
}
ROUNDNESS_PX = {
    "ROUND_FOUR": 4,
    "ROUND_EIGHT": 8,
    "ROUND_TWELVE": 12,
    "ROUND_FULL": 9999,
}
DEVICE_VIEWPORT = {"MOBILE": "phone", "TABLET": "tablet", "DESKTOP": "desktop"}


class StitchError(RuntimeError):

  def __init__(self, message: str, status: int | None = None):
    super().__init__(message)
    self.status = status


class StitchDownloadBlocked(StitchError):
  pass


def mcp_url() -> str:
  return os.environ.get("DCB_STITCH_MCP_URL") or STITCH_MCP


def server_key() -> str | None:
  k = os.environ.get("STITCH_API_KEY", "").strip()
  if k:
    return k
  try:
    with open(os.path.expanduser(KEY_FILE), encoding="utf-8") as fh:
      for line in fh:
        line = line.strip()
        m = re.match(
            r"(?:export\s+)?STITCH_API_KEY\s*=\s*['\"]?([^'\"\s]+)", line
        )
        if m:
          return m.group(1)
        if line and not line.startswith("#"):
          return line.split()[0]
  except OSError:
    pass
  return None


def parse_url(url: str) -> tuple[str, str | None]:
  """Project URL or bare id → (project_id, preselected screen id | None)."""
  raw = (url or "").strip()
  m_bare = re.fullmatch(r"(?:projects/)?(\d{5,25})", raw)
  if m_bare:
    return m_bare.group(1), None
  u = urllib.parse.urlsplit(raw)
  if not u.netloc.endswith(("stitch.withgoogle.com", "stitch.google.com")):
    raise StitchError(
        "Paste a Stitch project link (stitch.withgoogle.com/projects/…)"
    )
  m = re.search(r"/projects/(\d+)", u.path)
  if not m:
    raise StitchError("The link has no project id (…/projects/<id>)")
  q = urllib.parse.parse_qs(u.query)
  screen = None
  for key in ("screen", "screenId", "node-id", "selected"):
    for v in q.get(key, []):
      if re.fullmatch(r"[A-Za-z0-9_\-]{8,64}", v):
        screen = v
        break
  return m.group(1), screen


def _tool_data(res: dict[str, Any]) -> Any:
  if res.get("is_error"):
    msg = res.get("text") or "Stitch tool error"
    status = 403 if re.search(r"permission|denied|unauth", msg, re.I) else 502
    raise StitchError(msg[:400], status)
  if res.get("structured") is not None:
    return res["structured"]
  try:
    return json.loads(res.get("text") or "null")
  except ValueError:
    return None


def _file_url(f: Any) -> str | None:
  if isinstance(f, dict):
    return f.get("downloadUrl") or f.get("download_url")
  return None


def _num(v: Any) -> int:
  try:
    return int(float(v))
  except (TypeError, ValueError):
    return 0


def normalize_screen(s: dict[str, Any]) -> dict[str, Any]:
  name = s.get("name") or ""
  sid = s.get("id") or name.rsplit("/", 1)[-1]
  w, h = _num(s.get("width")), _num(s.get("height"))
  device = str(s.get("deviceType") or s.get("device_type") or "").upper()
  hint = DEVICE_VIEWPORT.get(device) or (
      figma.viewport_hint(w) if w else "unknown"
  )
  return {
      "id": sid,
      "name": name,
      "title": s.get("title") or sid,
      "width": w,
      "height": h,
      "device_type": device or None,
      "viewport_hint": hint,
      "screenshot_url": _file_url(s.get("screenshot")),
      "html_url": _file_url(s.get("htmlCode") or s.get("html_code")),
  }


def normalize_project(
    p: dict[str, Any], source: str = "owned"
) -> dict[str, Any] | None:
  """Normalizes a Stitch `Project` message from `list_projects`."""
  name = str(p.get("name") or "")
  pid = str(
      p.get("id") or (name.split("/", 1)[1] if "/" in name else name)
  ).strip()
  if not pid:
    return None
  title = (
      p.get("title") or p.get("displayName") or p.get("display_name") or ""
  ).strip() or f"Project {pid}"
  device = str(p.get("deviceType") or p.get("device_type") or "").upper()
  hint = DEVICE_VIEWPORT.get(device) or "unknown"
  vis = str(p.get("visibility") or "").upper() or None
  updated = str(
      p.get("updateTime")
      or p.get("update_time")
      or p.get("createTime")
      or p.get("create_time")
      or ""
  )
  inst = p.get("screenInstances") or p.get("screen_instances")
  screen_count = p.get("screen_count")
  if isinstance(inst, list):
    screen_count = sum(
        1
        for x in inst
        if isinstance(x, dict) and x.get("type") != "DESIGN_SYSTEM_INSTANCE"
    )
  theme = p.get("designTheme") or p.get("design_theme") or {}
  primary = (
      theme.get("overridePrimaryColor") or theme.get("customColor") or None
      if isinstance(theme, dict)
      else None
  )
  color_mode = (
      (theme.get("colorMode") or "").lower() or None
      if isinstance(theme, dict)
      else None
  )
  return {
      "id": pid,
      "name": f"projects/{pid}",
      "title": title,
      "url": f"https://stitch.withgoogle.com/projects/{pid}",
      "device_type": device or None,
      "viewport_hint": hint,
      "visibility": vis,
      "update_time": updated,
      "screen_count": screen_count,
      "primary_color": primary,
      "color_mode": color_mode,
      "source": source,
  }


class StitchClient:

  def __init__(self, api_key: str, timeout_s: float = 60.0):
    if not api_key:
      raise StitchError("No Stitch API key. Add one first.", 400)
    self.key = api_key
    self.timeout_s = timeout_s
    self.mcp = McpClient(
        mcp_url(), {"X-Goog-Api-Key": api_key}, timeout_s=timeout_s
    )

  def _call(self, tool: str, args: dict[str, Any]) -> Any:
    try:
      return _tool_data(self.mcp.call_tool(tool, args))
    except McpError as e:
      status = 403 if e.code in (401, 403) else 502
      msg = (
          "Stitch rejected the API key"
          if e.code in (401, 403)
          else f"Stitch: {e}"
      )
      raise StitchError(msg, status) from e

  def projects(self, include_shared: bool = True) -> list[dict[str, Any]]:
    """Lists the user's Stitch projects (owned + shared, newest first)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
      data = self._call("list_projects", {"filter": "view=owned"}) or {}
    except StitchError as e:
      if e.status == 403:
        raise
      data = self._call("list_projects", {}) or {}
    items = data.get("projects") if isinstance(data, dict) else data
    for p in items or []:
      if isinstance(p, dict):
        np = normalize_project(p, "owned")
        if np and np["id"] not in seen:
          seen.add(np["id"])
          out.append(np)
    if include_shared:
      try:
        sdata = self._call("list_projects", {"filter": "view=shared"}) or {}
        sitems = sdata.get("projects") if isinstance(sdata, dict) else sdata
        for p in sitems or []:
          if isinstance(p, dict):
            np = normalize_project(p, "shared")
            if np and np["id"] not in seen:
              seen.add(np["id"])
              out.append(np)
      except StitchError:
        pass
    return out

  def project(self, pid: str) -> dict[str, Any]:
    return self._call("get_project", {"name": f"projects/{pid}"}) or {}

  def screens(self, pid: str) -> list[dict[str, Any]]:
    data = self._call("list_screens", {"projectId": pid}) or {}
    items = data.get("screens") if isinstance(data, dict) else data
    return [normalize_screen(s) for s in items or [] if isinstance(s, dict)]

  def design_systems(self, pid: str) -> list[dict[str, Any]]:
    try:
      data = self._call("list_design_systems", {"projectId": pid}) or {}
    except StitchError:
      return []
    items = (
        data.get("designSystems") or data.get("assets") or []
        if isinstance(data, dict)
        else data
    )
    out = []
    for a in items or []:
      if not isinstance(a, dict):
        continue
      ds = a.get("designSystem") or a
      out.append({
          "name": a.get("name"),
          "display_name": ds.get("displayName") or a.get("displayName") or "",
          "theme": ds.get("theme") or {},
      })
    return out

  def create_project(self, title: str) -> dict[str, Any]:
    """Creates a Stitch project via MCP `create_project`."""
    data = (
        self._call("create_project", {"title": title[:120] or "DCB Project"})
        or {}
    )
    return normalize_project(data, "owned") or data

  def sync_design_system(
      self,
      pid: str,
      theme: dict[str, Any] | None,
      *,
      display_name: str = "DCB Design System",
      design_md: str = "",
  ) -> dict[str, Any]:
    """Creates or updates a Stitch DesignSystem in `pid` from DCB theme tokens."""
    t = theme or {}
    is_dark = str(t.get("scheme") or "light").lower() == "dark"
    rad = float(t.get("radius_card") or 16)
    roundness = (
        "ROUND_FULL"
        if rad >= 32
        else (
            "ROUND_TWELVE"
            if rad >= 12
            else ("ROUND_EIGHT" if rad >= 6 else "ROUND_FOUR")
        )
    )
    ds_payload = {
        "displayName": display_name[:80],
        "theme": {
            "colorMode": "DARK" if is_dark else "LIGHT",
            "customColor": str(t.get("primary") or "#0B57D0"),
            "overridePrimaryColor": str(t.get("primary") or "#0B57D0"),
            "overrideSecondaryColor": str(t.get("secondary") or "#00639B"),
            "overrideTertiaryColor": str(t.get("tertiary") or "#146C2E"),
            "overrideNeutralColor": str(t.get("on_surface") or "#1F1F1F"),
            "roundness": roundness,
            "colorVariant": "FIDELITY",
            "designMd": (design_md or "")[:6000],
        },
    }
    existing = self.design_systems(pid)
    if existing and existing[0].get("name"):
      try:
        res = self._call(
            "update_design_system",
            {
                "name": existing[0]["name"],
                "projectId": pid,
                "designSystem": ds_payload,
            },
        )
        if isinstance(res, dict):
          return res
      except StitchError:
        pass
    res = self._call(
        "create_design_system",
        {"projectId": pid, "designSystem": ds_payload},
    )
    return res if isinstance(res, dict) else {}

  def generate_component_screen(
      self,
      pid: str,
      prompt: str,
      *,
      device_type: str = "MOBILE",
      design_system_id: str | None = None,
  ) -> dict[str, Any]:
    """Delegates screen/component generation to Stitch MCP `generate_screen_from_text`."""
    args: dict[str, Any] = {
        "projectId": pid,
        "prompt": prompt[:4000],
        "deviceType": (
            device_type
            if device_type in ("MOBILE", "TABLET", "DESKTOP")
            else "MOBILE"
        ),
    }
    if design_system_id:
      args["designSystemId"] = design_system_id
    data = self._call("generate_screen_from_text", args) or {}
    screens_raw = (
        data.get("screens")
        if isinstance(data, dict) and isinstance(data.get("screens"), list)
        else (
            [data.get("screen")]
            if isinstance(data, dict) and isinstance(data.get("screen"), dict)
            else ([data] if isinstance(data, dict) and data.get("name") else [])
        )
    )
    if screens_raw:
      return normalize_screen(screens_raw[0])
    all_screens = self.screens(pid)
    if all_screens:
      return all_screens[-1]
    raise StitchError("Stitch did not return a generated screen")

  def download(self, url: str, want: str = "image") -> bytes:
    """Bytes of a screenshot (want=image) or HTML (want=html)."""
    req = urllib.request.Request(url, headers={"X-Goog-Api-Key": self.key})
    try:
      with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
        final = r.geturl()
        ctype = r.headers.get("Content-Type", "")
        data = r.read(MAX_DOWNLOAD + 1)
    except urllib.error.HTTPError as e:
      if e.code in (401, 403):
        raise StitchDownloadBlocked(
            f"Stitch download refused (HTTP {e.code})", e.code
        ) from e
      raise StitchError(
          f"Stitch download failed (HTTP {e.code})", e.code
      ) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
      raise StitchError(f"Stitch download failed: {e}") from e
    host = urllib.parse.urlsplit(final).netloc
    if (
        "accounts.google.com" in host
        or ("text/html" in ctype and want == "image")
        or (
            want == "html"
            and b"accounts.google.com" in data[:4000]
            and b"signin" in data[:4000]
        )
    ):
      raise StitchDownloadBlocked(
          "Stitch download redirected to Google sign-in"
      )
    if len(data) > MAX_DOWNLOAD:
      raise StitchError("Stitch file is too large")
    if "json" in ctype or data[:1] in (b"{",) and b"ataString" in data[:200]:
      try:
        obj = json.loads(data.decode("utf-8"))
      except ValueError:
        obj = None
      if isinstance(obj, dict):
        s = obj.get("dataString") or obj.get("data_string") or ""
        if want == "html":
          return s.encode("utf-8")
        try:
          return base64.b64decode(s)
        except ValueError as e:
          raise StitchError("Stitch returned an undecodable image") from e
    return data


# ------------------------------------------------------------------ facts
def _font(v: Any) -> str | None:
  if not v:
    return None
  v = str(v)
  return FONT_NAMES.get(v) or v.replace("_", " ").title()


def design_system_facts(ds: dict[str, Any] | None) -> dict[str, Any] | None:
  if not ds:
    return None
  t = ds.get("theme") or {}
  rnd = t.get("roundness")
  return {
      "display_name": ds.get("display_name") or "",
      "color_mode": (t.get("colorMode") or "").lower() or None,
      "seed_color": t.get("customColor"),
      "primary": t.get("overridePrimaryColor") or t.get("customColor"),
      "secondary": t.get("overrideSecondaryColor"),
      "tertiary": t.get("overrideTertiaryColor"),
      "neutral": t.get("overrideNeutralColor"),
      "fonts": {
          "headline": _font(t.get("headlineFont")),
          "body": _font(t.get("bodyFont")),
          "label": _font(t.get("labelFont")),
      },
      "roundness": rnd,
      "roundness_px": ROUNDNESS_PX.get(rnd),
      "color_variant": t.get("colorVariant"),
      "typography": t.get("typography") or {},
      "spacing": t.get("spacing") or {},
      "design_md": (t.get("designMd") or "")[:6000],
  }


_TW_CONFIG = re.compile(r"tailwind\.config\s*=\s*(\{.*?\})\s*</script>", re.S)
_GF_LINK_RX = re.compile(
    r'href=["\']https://fonts\.googleapis\.com/css2?\?([^"\']+)["\']', re.I
)
_GF_FAMILY_RX = re.compile(r"family=([^&:;|\"']+)")
_CLASS_RX = {
    "rounded": re.compile(
        r"\brounded(?:-(?:[trbl]{1,2}-)?([a-z0-9\[\]\.px]+))?\b"
    ),
    "gap": re.compile(r"\bgap-(?:[xy]-)?(\d+(?:\.\d)?)\b"),
    "padding": re.compile(r"\bp[xytrbl]?-(\d+(?:\.\d)?)\b"),
    "color": re.compile(
        r"\b(?:bg|text|border)-((?:[a-z]+-)*[a-z]+(?:-\d{2,3})?)\b"
    ),
}
_HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")


def _js_object_to_json(src: str) -> Any:
  """Best-effort parse of a small JS object literal (Tailwind config)."""
  s = re.sub(r"//[^\n]*", "", src)
  s = re.sub(r"([{,]\s*)([A-Za-z_$][\w$-]*)\s*:", r'\1"\2":', s)
  s = s.replace("'", '"')
  s = re.sub(r",\s*([}\]])", r"\1", s)
  try:
    return json.loads(s)
  except ValueError:
    return None


class _Outline(html.parser.HTMLParser):
  KEEP = {
      "header",
      "nav",
      "main",
      "section",
      "article",
      "aside",
      "footer",
      "form",
      "ul",
      "ol",
      "li",
      "table",
      "h1",
      "h2",
      "h3",
      "h4",
      "button",
      "a",
      "input",
      "select",
      "textarea",
      "img",
      "figure",
      "dialog",
      "label",
  }
  VOID = {"img", "input", "br", "hr", "meta", "link", "source"}

  def __init__(self):
    super().__init__()
    self.stack: list[dict[str, Any]] = [{"tag": "body", "kids": []}]
    self.counts: collections.Counter = collections.Counter()
    self.texts: list[str] = []
    self.in_body = False
    self.in_skip = 0

  def handle_starttag(self, tag, attrs):
    if tag == "body":
      self.in_body = True
      return
    if not self.in_body:
      return
    if tag in ("script", "style"):
      self.in_skip += 1
      return
    if self.in_skip:
      return
    attr_map = dict(attrs)
    for k in ("placeholder", "value", "aria-label"):
      v = (attr_map.get(k) or "").strip()
      if v and len(v) > 1 and len(self.texts) < 40:
        cleaned = " ".join(v.split())[:200]
        if cleaned and cleaned not in self.texts:
          self.texts.append(cleaned)
    role = attr_map.get("role")
    keep = tag in self.KEEP or role in ("button", "tab", "dialog", "list")
    if keep:
      self.counts[role or tag] += 1
    node = {"tag": role or tag, "kids": [], "keep": keep}
    if tag in self.VOID:
      self.stack[-1]["kids"].append(node)
    else:
      self.stack[-1]["kids"].append(node)
      self.stack.append(node)

  def handle_data(self, data):
    if not self.in_body or self.in_skip or len(self.texts) >= 40:
      return
    cleaned = " ".join((data or "").split())[:200]
    if len(cleaned) > 1 and cleaned not in self.texts:
      self.texts.append(cleaned)

  def handle_endtag(self, tag):
    if tag in ("script", "style") and self.in_skip > 0:
      self.in_skip -= 1
      return
    if self.in_skip:
      return
    if len(self.stack) > 1 and tag not in self.VOID:
      self.stack.pop()


def _semantic_kids(node, depth=0) -> list:
  """Children with non-semantic wrappers (div/span) flattened away."""
  out = []
  for k in node["kids"]:
    if k.get("keep"):
      out.append(k)
    elif depth < 30:
      out.extend(_semantic_kids(k, depth + 1))
  return out


def _render_outline(node, depth=0, max_depth=4) -> str:
  parts = []
  kids = _semantic_kids(node)
  i = 0
  while i < len(kids):
    k = kids[i]
    n = 1
    while i + n < len(kids) and kids[i + n]["tag"] == k["tag"]:
      n += 1
    sub = _render_outline(k, depth + 1, max_depth) if depth < max_depth else ""
    parts.append(
        k["tag"] + (f"×{n}" if n > 1 else "") + (f" > {sub}" if sub else "")
    )
    i += n
  out = ", ".join(parts)
  return f"({out})" if depth and len(parts) > 1 else out


def html_facts(html_text: str) -> dict[str, Any]:
  out: dict[str, Any] = {
      "tailwind": None,
      "google_fonts": [],
      "classes": {},
      "hex": [],
      "outline": "",
      "tags": {},
  }
  gf_fams: list[str] = []
  for m_gf in _GF_LINK_RX.finditer(html_text):
    qs = urllib.parse.unquote(m_gf.group(1)).replace("+", " ")
    for m_fam in _GF_FAMILY_RX.finditer(qs):
      fam = m_fam.group(1).strip()
      if (
          fam
          and "material symbols" not in fam.lower()
          and "material icons" not in fam.lower()
          and fam not in gf_fams
      ):
        gf_fams.append(fam)
  out["google_fonts"] = gf_fams
  m = _TW_CONFIG.search(html_text)
  if m:
    cfg = _js_object_to_json(m.group(1))
    ext = (
        ((cfg or {}).get("theme") or {}).get("extend")
        or (cfg or {}).get("theme")
        or {}
    )
    if isinstance(ext, dict):
      out["tailwind"] = {
          k: ext.get(k)
          for k in ("colors", "fontFamily", "borderRadius", "spacing")
          if ext.get(k)
      }
  classes = " ".join(re.findall(r'class="([^"]*)"', html_text))
  for k, rx in _CLASS_RX.items():
    out["classes"][k] = collections.Counter(rx.findall(classes)).most_common(8)
  out["hex"] = collections.Counter(
      h.upper() for h in _HEX.findall(html_text)
  ).most_common(12)
  p = _Outline()
  try:
    p.feed(html_text)
  except Exception:  # pylint: disable=broad-except
    pass
  out["outline"] = _render_outline(p.stack[0])[:1200]
  out["tags"] = dict(p.counts.most_common(16))
  out["texts"] = p.texts[:40]
  return out


def stitch_facts(
    project: dict[str, Any],
    screens: list[dict[str, Any]],
    design_systems: list[dict[str, Any]],
    htmls: dict[str, str],
) -> dict[str, Any]:
  colors: collections.Counter = collections.Counter()
  roles: dict[str, list[str]] = collections.defaultdict(list)
  radii: collections.Counter = collections.Counter()
  fonts: collections.Counter = collections.Counter()
  font_roles: dict[str, collections.Counter] = collections.defaultdict(
      collections.Counter
  )
  spacing: collections.Counter = collections.Counter()
  padding: collections.Counter = collections.Counter()
  tags: collections.Counter = collections.Counter()
  outlines = []
  screen_contents = []
  tw_seen: dict[str, Any] = {}
  for s in screens:
    h = htmls.get(s["id"])
    if not h:
      continue
    f = html_facts(h)
    if f.get("texts"):
      screen_contents.append({
          "id": s["id"],
          "name": s["title"],
          "texts": f["texts"],
      })
    tw = f["tailwind"] or {}
    for name, val in (
        (tw.get("colors") or {}).items()
        if isinstance(tw.get("colors"), dict)
        else []
    ):
      if isinstance(val, str) and val.startswith("#"):
        colors[val.upper()] += 1
        if name not in roles[val.upper()] and len(roles[val.upper()]) < 6:
          roles[val.upper()].append(f"tailwind:{name}")
    for hx, n in f["hex"]:
      colors[hx] += n
    for name, val in (
        (tw.get("fontFamily") or {}).items()
        if isinstance(tw.get("fontFamily"), dict)
        else []
    ):
      fam = val[0] if isinstance(val, list) and val else val
      if isinstance(fam, str):
        clean_fam = fam.strip().strip("'\"")
        if clean_fam:
          fonts[clean_fam] += 1
          font_roles[str(name).lower()][clean_fam] += 1
    for gf in f.get("google_fonts") or []:
      if gf not in fonts:
        fonts[gf] += 1
    for name, val in (
        (tw.get("borderRadius") or {}).items()
        if isinstance(tw.get("borderRadius"), dict)
        else []
    ):
      tw_seen.setdefault("borderRadius", {})[name] = val
    for v, n in f["classes"].get("rounded", []):
      radii[v or "DEFAULT"] += n
    for v, n in f["classes"].get("gap", []):
      spacing[v] += n
    for v, n in f["classes"].get("padding", []):
      padding[v] += n
    tags.update(f["tags"])
    outlines.append({"frame": s["title"], "outline": f["outline"]})
  ds = design_system_facts(design_systems[0]) if design_systems else None
  role_map: dict[str, str] = {}
  for role_key, counter in font_roles.items():
    if counter:
      role_map[role_key] = counter.most_common(1)[0][0]
  if ds and isinstance(ds.get("fonts"), dict):
    ds_fonts = ds["fonts"]
    if not ds_fonts.get("headline"):
      ds_fonts["headline"] = (
          role_map.get("display")
          or role_map.get("heading")
          or role_map.get("headline")
          or role_map.get("serif")
      )
    if not ds_fonts.get("body"):
      ds_fonts["body"] = (
          role_map.get("sans")
          or role_map.get("body")
          or (fonts.most_common(1)[0][0] if fonts else None)
      )
    if not ds_fonts.get("mono") and (
        role_map.get("mono") or role_map.get("code")
    ):
      ds_fonts["mono"] = role_map.get("mono") or role_map.get("code")
  text_styles_list = []
  for role_key, fam in role_map.items():
    text_styles_list.append({
        "name": f"tailwind fontFamily.{role_key}",
        "role": role_key,
        "family": fam,
        "size": None,
        "weight": None,
        "line_height": None,
        "count": font_roles[role_key][fam],
    })
  for fam, n in fonts.most_common(6):
    if not any(ts["family"] == fam for ts in text_styles_list):
      text_styles_list.append({
          "name": "tailwind fontFamily",
          "family": fam,
          "size": None,
          "weight": None,
          "line_height": None,
          "count": n,
      })
  return {
      "source": "stitch",
      "file_name": project.get("title") or project.get("displayName") or "",
      "frames": [
          {
              "id": s["id"],
              "name": s["title"],
              "width": s["width"],
              "height": s["height"],
              "viewport_hint": s["viewport_hint"],
              "device_type": s["device_type"],
          }
          for s in screens
      ],
      "design_system": ds,
      "colors": [
          {"hex": hx, "count": n, "roles": roles.get(hx, [])}
          for hx, n in colors.most_common(20)
      ],
      "radii": [
          {
              "px": _tw_radius_px(k, tw_seen.get("borderRadius") or {}),
              "count": n,
              "on": [f"rounded-{k}" if k != "DEFAULT" else "rounded"],
          }
          for k, n in radii.most_common(8)
      ],
      "text_styles": text_styles_list,
      "spacing": {
          "item_spacing": [_tw_space_px(v) for v, _ in spacing.most_common(6)],
          "padding": [_tw_space_px(v) for v, _ in padding.most_common(6)],
          "grid_hint": 4,
      },
      "components": [
          {"name": k, "instances": n} for k, n in tags.most_common(16)
      ],
      "principles_text": [],
      "styles": [],
      "variables": {"available": False, "count": 0},
      "html_outline": outlines,
      "screen_contents": screen_contents[:24],
  }


_TW_RADIUS = {
    "none": 0,
    "sm": 2,
    "DEFAULT": 4,
    "md": 6,
    "lg": 8,
    "xl": 12,
    "2xl": 16,
    "3xl": 24,
    "full": 9999,
}


def _tw_radius_px(k: str, custom: dict[str, Any]) -> float | str:
  v = custom.get(k)
  if isinstance(v, str):
    m = re.match(r"([\d.]+)(px|rem)", v)
    if m:
      return float(m.group(1)) * (16 if m.group(2) == "rem" else 1)
  if k in _TW_RADIUS:
    return _TW_RADIUS[k]
  m = re.match(r"\[(\d+)px\]", k)
  return int(m.group(1)) if m else k


def _tw_space_px(v: str) -> float:
  try:
    return float(v) * 4
  except ValueError:
    return 0


class _SubtreeExtractor(html.parser.HTMLParser):
  """Extracts candidate component DOM subtrees (`<section>`, `<article>`, card `<div>`) from Stitch HTML."""

  VOID = {"img", "input", "br", "hr", "meta", "link", "source"}

  def __init__(self):
    super().__init__(convert_charrefs=True)
    self.root: dict[str, Any] = {
        "tag": "root",
        "attrs": {},
        "kids": [],
        "text": "",
    }
    self.stack: list[dict[str, Any]] = [self.root]
    self.in_body = False
    self.skip_depth = 0
    self.candidates: list[dict[str, Any]] = []

  def handle_starttag(
      self, tag: str, attrs: list[tuple[str, str | None]]
  ) -> None:
    tag = tag.lower()
    if tag == "body":
      self.in_body = True
      return
    if not self.in_body:
      return
    if tag in ("script", "style", "head", "meta", "link"):
      if tag not in self.VOID:
        self.skip_depth += 1
      return
    if self.skip_depth:
      if tag not in self.VOID:
        self.skip_depth += 1
      return
    attr_dict = {k.lower(): v or "" for k, v in attrs if k}
    node: dict[str, Any] = {
        "tag": tag,
        "attrs": attr_dict,
        "kids": [],
        "text": "",
    }
    self.stack[-1]["kids"].append(node)
    cls_str = attr_dict.get("class", "").lower()
    if tag in ("section", "article") or (
        tag == "div"
        and any(
            w in cls_str for w in ("card", "tile", "item", "panel", "rounded-")
        )
    ):
      self.candidates.append(node)
    if tag not in self.VOID:
      self.stack.append(node)

  def handle_endtag(self, tag: str) -> None:
    tag = tag.lower()
    if not self.in_body or tag in self.VOID:
      return
    if self.skip_depth:
      self.skip_depth -= 1
      return
    if len(self.stack) > 1:
      self.stack.pop()

  def handle_data(self, data: str) -> None:
    if not self.in_body or self.skip_depth:
      return
    t = " ".join((data or "").split())
    if t:
      cur = self.stack[-1]
      cur["text"] = (cur["text"] + " " + t).strip() if cur["text"] else t


def _dom_node_to_figma_like(node: dict[str, Any]) -> dict[str, Any]:
  """Converts a parsed Stitch DOM node into an outline tree compatible with `figma_compiler`."""
  tag = node.get("tag") or "div"
  attrs = node.get("attrs") or {}
  cls = attrs.get("class") or ""
  text = (node.get("text") or "").strip()
  kids = node.get("kids") or []

  if tag in ("img",):
    return {
        "type": "RECTANGLE",
        "name": attrs.get("alt") or "image",
        "fill": "image",
        "size": [320, 180],
    }

  if (
      tag
      in (
          "h1",
          "h2",
          "h3",
          "h4",
          "h5",
          "h6",
          "p",
          "span",
          "small",
          "strong",
          "em",
          "button",
          "a",
      )
      and not kids
  ):
    if tag == "button":
      return {
          "type": "INSTANCE",
          "name": "Button",
          "radius": 999 if "rounded-full" in cls else 12,
          "fill": "#0B57D0 = primary",
          "children": [{
              "type": "TEXT",
              "name": "actionLabel",
              "text": text or "Action",
              "font": {"size": 14, "weight": 600},
              "color": "#FFFFFF = on_primary",
          }],
      }
    fsize = (
        24
        if tag in ("h1", "h2")
        else (
            18
            if tag in ("h3", "h4", "strong")
            else (12 if tag == "small" else 14)
        )
    )
    fweight = 600 if tag.startswith("h") or tag == "strong" else 400
    return {
        "type": "TEXT",
        "name": tag,
        "text": text or "Label",
        "font": {"size": fsize, "weight": fweight},
        "color": (
            "#0B57D0 = primary"
            if "text-primary" in cls
            else "#1F1F1F = on_surface"
        ),
    }

  is_row = "flex-row" in cls or (
      "flex" in cls
      and "flex-col" not in cls
      and len(kids) > 1
      and tag not in ("section", "article", "main")
  )
  gap = 8
  m_gap = _CLASS_RX["gap"].search(cls)
  if m_gap:
    gap = round(_tw_space_px(m_gap.group(1)))
  pad = 16 if ("p-" in cls or tag in ("section", "article")) else 0
  radius = 16 if ("rounded" in cls or tag in ("section", "article")) else 0

  children_out: list[dict[str, Any]] = []
  if text:
    children_out.append({
        "type": "TEXT",
        "name": f"{tag}_text",
        "text": text,
        "font": {
            "size": 16 if tag.startswith("h") else 14,
            "weight": 500 if tag.startswith("h") else 400,
        },
    })
  for k in kids:
    children_out.append(_dom_node_to_figma_like(k))

  return {
      "type": "FRAME",
      "name": tag,
      "size": [360, 180],
      "layout": {
          "dir": "row" if is_row else "column",
          "gap": gap,
          "pad": [pad, pad, pad, pad],
          "main": "SPACE_BETWEEN" if "justify-between" in cls else "MIN",
          "cross": "CENTER" if "items-center" in cls else "MIN",
      },
      "radius": radius,
      "fill": "#F8FAFD = surface_container_low" if radius else None,
      "children": children_out,
  }


def extract_stitch_dom_region(
    html_text: str,
    *,
    hint: str = "",
    index: int = 0,
    palette: dict[str, str] | None = None,
) -> dict[str, Any]:
  """Extracts a component DOM region from Stitch HTML and compiles Web + Basic skeletons.

  Returns `{outline, web: {template, css, props, sample_props}, basic:
  {composition, props, sample_props}}`.
  """
  from refs import figma_compiler  # pylint: disable=g-import-not-at-top

  parser = _SubtreeExtractor()
  try:
    parser.feed(html_text or "")
  except Exception:  # pylint: disable=broad-except
    pass

  chosen: dict[str, Any] | None = None
  hint_words = [
      w.lower()
      for w in re.findall(r"[A-Za-z0-9]{3,}", hint or "")
      if w.lower() not in ("the", "and", "with", "card", "component", "section")
  ]
  if parser.candidates:
    if hint_words:
      best_score = -1
      for cand in parser.candidates:
        blob = json.dumps(cand).lower()
        score = sum(1 for w in hint_words if w in blob)
        if score > best_score:
          best_score = score
          chosen = cand
    if chosen is None:
      idx = max(0, min(index, len(parser.candidates) - 1))
      chosen = parser.candidates[idx]
  elif parser.root.get("kids"):
    chosen = parser.root["kids"][0]

  outline = _dom_node_to_figma_like(chosen) if chosen else {}
  web_compiled = figma_compiler.compile_node_to_html_css(
      outline, palette=palette
  )
  basic_compiled = figma_compiler.compile_node_to_basic_skeleton(outline)
  return {
      "outline": outline,
      "web": web_compiled,
      "basic": basic_compiled,
  }
