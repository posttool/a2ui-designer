"""Headless-Chrome screenshot pool: one browser, N tabs, thread-safe."""

from __future__ import annotations

import base64
import os
import queue
import struct
import threading
import time
from typing import Any
import zlib

from render import cdp

MAX_HEIGHT = 4000


class BrowserPool:
  """Lazily starts Chrome; screenshot() borrows a tab."""

  def __init__(self, tabs: int = 3, chrome: str = cdp.CHROME):
    self.n_tabs = tabs
    self.chrome = chrome
    self._lock = threading.Lock()
    self._browser: cdp.Browser | None = None
    self._tabs: "queue.Queue[cdp.Page]" = queue.Queue()

  def _ensure(self) -> None:
    with self._lock:
      if self._browser is not None and self._browser.proc.poll() is None:
        return
      self._close_locked()
      self._browser = cdp.Browser(
          width=412,
          height=915,
          chrome=self.chrome,
          extra_args=("--disable-backgrounding-occluded-windows",),
      )
      self._tabs = queue.Queue()
      pages = [self._browser.page]
      for _ in range(self.n_tabs - 1):
        t = self._browser._json("/json/new?about:blank", method="PUT")  # pylint: disable=protected-access
        pages.append(cdp.Page(t["webSocketDebuggerUrl"]))
      # Extra tabs start in the background, where rendering frames are
      # throttled until the tab has been foregrounded once; the render page
      # then never signals ready. Foreground each tab once and keep all of
      # them focused/active.
      for p in pages:
        for method, params in (
            ("Page.bringToFront", {}),
            ("Emulation.setFocusEmulationEnabled", {"enabled": True}),
            ("Page.setWebLifecycleState", {"state": "active"}),
        ):
          try:
            p.send(method, params)
          except cdp.CdpError:
            pass
      for p in pages:
        self._tabs.put(p)

  def _close_locked(self) -> None:
    if self._browser is not None:
      try:
        self._browser.close()
      except Exception:  # pylint: disable=broad-except
        pass
    self._browser = None

  def close(self) -> None:
    with self._lock:
      self._close_locked()

  def screenshot(
      self,
      url: str,
      width: int,
      height: int,
      out_path: str,
      scale: float = 2.0,
      timeout: float = 25.0,
  ) -> dict[str, Any]:
    """Renders url at width×height; captures full surface height."""
    for attempt in range(2):
      self._ensure()
      try:
        page = self._tabs.get(timeout=120)
      except queue.Empty as e:
        raise cdp.CdpError("no free browser tab") from e
      ok = True
      try:
        return self._shoot(page, url, width, height, out_path, scale, timeout)
      except (cdp.CdpError, OSError, ConnectionError) as e:
        ok = False
        if attempt:
          raise cdp.CdpError(f"screenshot failed: {e}") from e
      finally:
        if ok:
          self._tabs.put(page)
        else:
          with self._lock:
            self._close_locked()
    raise cdp.CdpError("screenshot failed")

  def _shoot(
      self,
      page: cdp.Page,
      url: str,
      width: int,
      height: int,
      out_path: str,
      scale: float,
      timeout: float,
  ) -> dict[str, Any]:
    page.set_viewport(width, height, scale)
    page.navigate(url, timeout=timeout)
    deadline = time.time() + timeout
    meta = None
    while time.time() < deadline:
      try:
        meta = page.evaluate("window.__a2uiReady || null", timeout=5)
      except cdp.CdpError:
        meta = None
      if meta:
        break
      time.sleep(0.15)
    if not meta:
      meta = {"errors": ["render page did not signal ready"]}
    full_h = int(
        page.evaluate(
            "Math.ceil(Math.max(document.documentElement.scrollHeight, "
            "document.body ? document.body.scrollHeight : 0))"
        )
        or height
    )
    # The document is never shorter than the viewport, so short surfaces
    # would be padded with blank space down to the viewport height. The A2UI
    # render page reports the real content height in __a2uiReady.height;
    # capture exactly that. Pages without it (e.g. raw Stitch HTML) keep the
    # document height.
    content_h = meta.get("height") if isinstance(meta, dict) else None
    if isinstance(content_h, (int, float)) and content_h > 0:
      shot_h = int(content_h)
    else:
      shot_h = full_h
    cap_h = max(min(shot_h, MAX_HEIGHT), 1)
    if cap_h > height:
      page.set_viewport(width, cap_h, scale)
      time.sleep(0.15)
    try:
      boxes = page.evaluate(
          "window.__a2uiBoxes ? window.__a2uiBoxes() : null", timeout=5
      )
    except cdp.CdpError:
      boxes = None
    r = page.send(
        "Page.captureScreenshot",
        {
            "format": "png",
            "captureBeyondViewport": True,
            "clip": {
                "x": 0,
                "y": 0,
                "width": width,
                "height": cap_h,
                "scale": 1,
            },
        },
        timeout=60,
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as fh:
      fh.write(base64.b64decode(r["data"]))
    page.set_viewport(width, height, scale)
    meta = dict(meta)
    meta.update({
        "page_height": full_h,
        "captured_height": cap_h,
        "truncated": shot_h > MAX_HEIGHT,
        "scale": scale,
        "console": [
            c
            for c in page.console[-20:]
            if c.get("level") in ("error", "exception")
        ],
    })
    page.console.clear()
    if isinstance(boxes, list):
      meta["boxes"] = boxes
    return meta


def png_size(path: str) -> tuple[int, int]:
  with open(path, "rb") as fh:
    head = fh.read(24)
  return struct.unpack(">II", head[16:24])


def make_thumb(src: str, dst: str, max_h_ratio: float = 1.6) -> None:
  """Crops a PNG to at most width*ratio tall (top part) for gallery tiles.

  Pure-stdlib: handles 8-bit RGB/RGBA non-interlaced PNGs (Chrome output).
  Falls back to copying the file.
  """
  try:
    with open(src, "rb") as fh:
      data = fh.read()
    pos, chunks = 8, []
    w = h = 0
    bit_depth, color_type = 8, 6
    while pos < len(data):
      length = struct.unpack(">I", data[pos : pos + 4])[0]
      ctype = data[pos + 4 : pos + 8]
      cdata = data[pos + 8 : pos + 8 + length]
      if ctype == b"IHDR":
        w, h, bit_depth, color_type = struct.unpack(">IIBB", cdata[:10])
      chunks.append((ctype, cdata))
      pos += 12 + length
    new_h = int(w * max_h_ratio)
    if new_h >= h or bit_depth != 8 or color_type not in (2, 6):
      raise ValueError("no crop")
    bpp = 4 if color_type == 6 else 3
    raw = zlib.decompress(b"".join(cd for ct, cd in chunks if ct == b"IDAT"))
    stride = 1 + w * bpp
    cropped = raw[: new_h * stride]
    out = bytearray(data[:8])

    def chunk(t: bytes, d: bytes) -> bytes:
      return (
          struct.pack(">I", len(d))
          + t
          + d
          + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
      )

    for ct, cd in chunks:
      if ct == b"IHDR":
        cd = struct.pack(">II", w, new_h) + cd[8:]
        out += chunk(ct, cd)
      elif ct == b"IDAT":
        continue
      elif ct == b"IEND":
        out += chunk(b"IDAT", zlib.compress(cropped, 6))
        out += chunk(ct, cd)
      else:
        out += chunk(ct, cd)
    with open(dst, "wb") as fh:
      fh.write(out)
  except Exception:  # pylint: disable=broad-except
    with open(src, "rb") as a, open(dst, "wb") as b:
      b.write(a.read())
