"""Swaps `/img/<slug>/<w>x<h>` placeholders in sample payloads for real photos.

The synthesizer writes images as descriptive slugs (e.g.
`/img/pad-thai-takeout-box/800x600`). This module searches Wikimedia Commons
for each slug and rewrites the URL to a Commons thumbnail of roughly the
requested width, so generated layouts are judged with real imagery. Results are
cached in the `image_cache` table; credits (title, author, license, page) are
returned so callers can store them next to the sample. Any failure keeps the
local placeholder, so synthesis never depends on the network.

Set `DCB_WEB_IMAGES=0` to disable lookups (tests do).
"""

from __future__ import annotations

import concurrent.futures
import html
import json
import os
import re
import sys
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from core import util

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = (
    "A2UIDesigner/0.1 (+https://github.com/posttool/a2ui-designer)"
)
PLACEHOLDER_RE = re.compile(
    r"/img/([A-Za-z0-9][A-Za-z0-9_\-]{0,120})/(\d{2,4})x(\d{2,4})(?:\.(?:svg|png|jpe?g))?"
)
_OK_MIME = {"image/jpeg", "image/png", "image/webp"}
_TAG_RE = re.compile(r"<[^>]+>")


def enabled() -> bool:
  return os.environ.get("DCB_WEB_IMAGES", "1") != "0"


# Wikimedia rate-limits anonymous API clients per IP (HTTP 429 with
# Retry-After ~30 s), so lookups are serialized, spaced out, and retried.
_LOCK = threading.Lock()
_MIN_INTERVAL_S = 1.0
_last = [0.0]


def _http_json(url: str, timeout: float = 10.0, attempts: int = 4) -> Any:
  req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
  for k in range(attempts):
    wait = 0.0
    with _LOCK:
      gap = _MIN_INTERVAL_S - (time.monotonic() - _last[0])
      if gap > 0:
        time.sleep(gap)
      try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec
          return json.loads(resp.read().decode("utf-8"))
      except urllib.error.HTTPError as e:
        if e.code not in (429, 500, 502, 503, 504) or k == attempts - 1:
          raise
        try:
          wait = float(e.headers.get("Retry-After") or 0)
        except ValueError:
          wait = 0.0
        wait = min(45.0, max(wait, 2.0 * (k + 1)))
      except (urllib.error.URLError, TimeoutError):
        if k == attempts - 1:
          raise
        wait = 2.0 * (k + 1)
      finally:
        _last[0] = time.monotonic()
      # Hold the lock while backing off so other lookups wait too.
      time.sleep(wait)
  raise RuntimeError("unreachable")


# Overridable in tests: (query, width) -> list of candidate dicts.
Searcher = Callable[[str, int], list[dict[str, Any]]]


def commons_search(query: str, width: int) -> list[dict[str, Any]]:
  """Top Wikimedia Commons bitmap results for `query`, thumbed to `width`."""
  params = {
      "action": "query",
      "format": "json",
      "generator": "search",
      "gsrnamespace": "6",
      "gsrsearch": f"{query} filetype:bitmap",
      "gsrlimit": "12",
      "prop": "imageinfo",
      "iiprop": "url|size|mime|extmetadata",
      "iiextmetadatafilter": "Artist|LicenseShortName",
      "iiurlwidth": str(max(160, min(int(width), 1600))),
  }
  data = _http_json(COMMONS_API + "?" + urllib.parse.urlencode(params))
  pages = ((data or {}).get("query") or {}).get("pages") or {}
  out = []
  for p in sorted(pages.values(), key=lambda p: p.get("index", 0)):
    ii = (p.get("imageinfo") or [{}])[0]
    if ii.get("mime") not in _OK_MIME or not ii.get("thumburl"):
      continue
    if int(ii.get("width") or 0) < 480:
      continue
    meta = ii.get("extmetadata") or {}
    artist = _TAG_RE.sub("", (meta.get("Artist") or {}).get("value") or "")
    thumb = ii["thumburl"].split("?", 1)[0]
    out.append({
        "url": thumb,
        "width": int(ii.get("thumbwidth") or 0),
        "height": int(ii.get("thumbheight") or 0),
        "title": str(p.get("title") or "").removeprefix("File:"),
        "page": ii.get("descriptionurl") or "",
        "license": (meta.get("LicenseShortName") or {}).get("value") or "",
        "artist": html.unescape(artist).strip()[:120],
        "source": "Wikimedia Commons",
    })
  return out


class Resolver:
  """Resolves placeholder slugs to cached web photos."""

  def __init__(self, store: Any, searcher: Searcher | None = None):
    self.store = store
    self.searcher = searcher or commons_search

  def _candidates(self, slug: str, width: int) -> list[dict[str, Any]]:
    words = [
        w for w in re.split(r"[-_]+", slug.lower()) if w and not w.isdigit()
    ]
    # Try the full description first, then progressively broader queries.
    tries = []
    for k in range(len(words), 0, -1):
      q = " ".join(words[:k])
      if q and q not in tries:
        tries.append(q)
      if len(tries) >= 2:
        break
    bucket = 640 if width <= 640 else 1280
    for q in tries:
      key = f"commons:{bucket}:{q}"
      row = self.store.get("image_cache", key, "query")
      if row is not None:
        res = row.get("results") or []
      else:
        try:
          res = self.searcher(q, bucket)
        except Exception as e:  # pylint: disable=broad-except
          # Network trouble: keep placeholders, don't cache.
          print(f"images: lookup failed for {q!r}: {e}", file=sys.stderr)
          return []
        self.store.execute(
            "INSERT OR REPLACE INTO image_cache (query, results_json,"
            " created_at) VALUES (?, ?, ?)",
            [key, util.dumps(res), util.now_iso()],
        )
      if res:
        return res
    return []

  def resolve(self, payload: Any) -> tuple[Any, list[dict[str, Any]]]:
    """Returns (payload with web image URLs, credits for the images used)."""
    if not enabled():
      return payload, []
    found: dict[str, tuple[str, int, int]] = {}

    def scan(v: Any) -> None:
      if isinstance(v, str):
        for m in PLACEHOLDER_RE.finditer(v):
          found[m.group(0)] = (m.group(1), int(m.group(2)), int(m.group(3)))
      elif isinstance(v, dict):
        for x in v.values():
          scan(x)
      elif isinstance(v, list):
        for x in v:
          scan(x)

    scan(payload)
    if not found:
      return payload, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
      cands = dict(
          zip(
              found,
              ex.map(
                  lambda k: self._candidates(found[k][0], found[k][1]), found
              ),
          )
      )
    used: set[str] = set()
    mapping: dict[str, str] = {}
    credits = []
    for ph, (slug, w, h) in found.items():
      want = w / max(1, h)
      best, best_score = None, None
      for i, c in enumerate(cands.get(ph) or []):
        if c["url"] in used:
          continue
        ratio = (c["width"] or 4) / max(1, c["height"] or 3)
        score = i + 4 * abs((ratio - want) / want)
        if best_score is None or score < best_score:
          best, best_score = c, score
      if best is None:
        continue
      used.add(best["url"])
      mapping[ph] = best["url"]
      credits.append({"placeholder": ph, "slug": slug, **best})

    def sub(v: Any) -> Any:
      if isinstance(v, str):
        return PLACEHOLDER_RE.sub(
            lambda m: mapping.get(m.group(0), m.group(0)), v
        )
      if isinstance(v, dict):
        return {k: sub(x) for k, x in v.items()}
      if isinstance(v, list):
        return [sub(x) for x in v]
      return v

    return sub(payload), credits


def has_placeholders(payload: Any) -> bool:
  return bool(PLACEHOLDER_RE.search(json.dumps(payload, ensure_ascii=False)))
