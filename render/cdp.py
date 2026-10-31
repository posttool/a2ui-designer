"""Tiny stdlib Chrome DevTools Protocol client (headless Chrome + RFC 6455 websocket).

    with Browser(width=560, height=960) as b:
      page = b.page
      page.navigate("http://localhost:8790/skins/default/?user=u1")
      page.click("#unlock")
      page.type_text("#prompt", "hello", submit=True)
      page.screenshot("/tmp/x.png")

No third-party deps: the websocket is a minimal client over `socket` (client
frames
masked, server frames unmasked; text/binary/continuation/ping/pong/close).
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import queue
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Optional
import urllib.parse
import urllib.request

CHROME = (
    os.environ.get("CHROME_BIN")
    or shutil.which("google-chrome")
    or "/usr/bin/google-chrome"
)


class CdpError(RuntimeError):
  pass


# ----------------------------------------------------------------- websocket
class WebSocket:
  """Minimal RFC 6455 client (no extensions, no TLS)."""

  def __init__(self, url: str, timeout: float = 10.0):
    u = urllib.parse.urlparse(url)
    if u.scheme != "ws":
      raise ValueError(f"only ws:// supported: {url}")
    self.sock = socket.create_connection(
        (u.hostname, u.port or 80), timeout=timeout
    )
    key = base64.b64encode(os.urandom(16)).decode()
    path = u.path + (("?" + u.query) if u.query else "")
    req = (
        f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade:"
        " websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key:"
        f" {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    self.sock.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
      chunk = self.sock.recv(4096)
      if not chunk:
        raise CdpError("websocket handshake: connection closed")
      buf += chunk
    head, self._rest = buf.split(b"\r\n\r\n", 1)
    if b" 101 " not in head.split(b"\r\n", 1)[0]:
      raise CdpError(f"websocket handshake failed: {head[:200]!r}")
    self.sock.settimeout(None)
    self._send_lock = threading.Lock()
    self.closed = False

  def _recv_exact(self, n: int) -> bytes:
    out = bytearray()
    if self._rest:
      take = self._rest[:n]
      out += take
      self._rest = self._rest[len(take) :]
    while len(out) < n:
      chunk = self.sock.recv(max(65536, n - len(out)))
      if not chunk:
        raise ConnectionError("websocket closed")
      need = n - len(out)
      out += chunk[:need]
      if len(chunk) > need:
        self._rest = chunk[need:] + self._rest
    return bytes(out)

  def send(self, text: str, opcode: int = 0x1) -> None:
    payload = text.encode("utf-8") if isinstance(text, str) else text
    hdr = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
      hdr.append(0x80 | n)
    elif n < 65536:
      hdr.append(0x80 | 126)
      hdr += struct.pack(">H", n)
    else:
      hdr.append(0x80 | 127)
      hdr += struct.pack(">Q", n)
    mask = os.urandom(4)
    hdr += mask
    # XOR mask via int arithmetic (fast enough for CDP command sizes).
    m = (mask * (n // 4 + 1))[:n]
    masked = (
        (int.from_bytes(payload, "big") ^ int.from_bytes(m, "big")).to_bytes(
            n, "big"
        )
        if n
        else b""
    )
    with self._send_lock:
      self.sock.sendall(bytes(hdr) + masked)

  def recv(self) -> Optional[str]:
    """Next complete text/binary message; None on close."""
    parts: list[bytes] = []
    while True:
      b0, b1 = self._recv_exact(2)
      fin, opcode = b0 & 0x80, b0 & 0x0F
      n = b1 & 0x7F
      if n == 126:
        n = struct.unpack(">H", self._recv_exact(2))[0]
      elif n == 127:
        n = struct.unpack(">Q", self._recv_exact(8))[0]
      mask = self._recv_exact(4) if b1 & 0x80 else None
      data = self._recv_exact(n) if n else b""
      if mask:
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
      if opcode == 0x8:
        self.closed = True
        return None
      if opcode == 0x9:
        self.send(data, opcode=0xA)
        continue
      if opcode == 0xA:
        continue
      parts.append(data)
      if fin:
        return b"".join(parts).decode("utf-8", "replace")

  def close(self) -> None:
    try:
      self.send(b"", opcode=0x8)
    except OSError:
      pass
    try:
      self.sock.close()
    except OSError:
      pass
    self.closed = True


# ---------------------------------------------------------------------- page
class Page:
  """One CDP target session (a tab)."""

  def __init__(self, ws_url: str):
    self.ws = WebSocket(ws_url)
    self._ids = itertools.count(1)
    self._pending: dict[int, queue.Queue] = {}
    self._lock = threading.Lock()
    self._listeners: list[Callable[[str, dict], None]] = []
    self.console: list[dict] = []
    self.events: "queue.Queue[tuple[str, dict]]" = queue.Queue()
    self._reader = threading.Thread(
        target=self._read_loop, daemon=True, name="cdp-reader"
    )
    self._reader.start()
    self.send("Page.enable")
    self.send("Runtime.enable")
    self.send("Log.enable")

  def _read_loop(self) -> None:
    while True:
      try:
        msg = self.ws.recv()
      except (OSError, ConnectionError):
        msg = None
      if msg is None:
        with self._lock:
          for q in self._pending.values():
            q.put({"error": {"message": "connection closed"}})
        return
      try:
        data = json.loads(msg)
      except json.JSONDecodeError:
        continue
      if "id" in data:
        with self._lock:
          q = self._pending.pop(data["id"], None)
        if q is not None:
          q.put(data)
        continue
      method, params = data.get("method", ""), data.get("params") or {}
      self._record_console(method, params)
      self.events.put((method, params))
      for fn in list(self._listeners):
        try:
          fn(method, params)
        except Exception:  # pylint: disable=broad-except
          pass

  def _record_console(self, method: str, params: dict) -> None:
    if method == "Runtime.consoleAPICalled":
      args = " ".join(
          str(a.get("value", a.get("description", "")))
          for a in params.get("args", [])
      )
      self.console.append({
          "level": params.get("type", "log"),
          "text": args[:2000],
          "at": time.time(),
      })
    elif method == "Runtime.exceptionThrown":
      d = params.get("exceptionDetails") or {}
      exc = d.get("exception") or {}
      self.console.append({
          "level": "exception",
          "text": (exc.get("description") or d.get("text") or "")[:2000],
          "at": time.time(),
      })
    elif method == "Log.entryAdded":
      e = params.get("entry") or {}
      self.console.append({
          "level": e.get("level", "info"),
          "text": f"{e.get('text', '')} {e.get('url', '')}".strip()[:2000],
          "at": time.time(),
      })

  # -------------------------------------------------------------- protocol
  def send(
      self, method: str, params: Optional[dict] = None, timeout: float = 30.0
  ) -> dict:
    mid = next(self._ids)
    q: queue.Queue = queue.Queue()
    with self._lock:
      self._pending[mid] = q
    self.ws.send(
        json.dumps({"id": mid, "method": method, "params": params or {}})
    )
    try:
      resp = q.get(timeout=timeout)
    except queue.Empty:
      with self._lock:
        self._pending.pop(mid, None)
      raise CdpError(f"{method}: timeout after {timeout}s")
    if "error" in resp:
      raise CdpError(f"{method}: {resp['error'].get('message')}")
    return resp.get("result") or {}

  def wait_event(
      self,
      method: str,
      timeout: float = 15.0,
      pred: Optional[Callable[[dict], bool]] = None,
  ) -> Optional[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
      try:
        m, p = self.events.get(timeout=max(0.01, deadline - time.time()))
      except queue.Empty:
        return None
      if m == method and (pred is None or pred(p)):
        return p
    return None

  def _drain_events(self) -> None:
    while True:
      try:
        self.events.get_nowait()
      except queue.Empty:
        return

  # ---------------------------------------------------------------- helpers
  def evaluate(
      self,
      expression: str,
      *,
      await_promise: bool = True,
      timeout: float = 30.0,
  ) -> Any:
    r = self.send(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
            "userGesture": True,
        },
        timeout=timeout,
    )
    if r.get("exceptionDetails"):
      d = r["exceptionDetails"]
      raise CdpError(
          "JS: "
          + ((d.get("exception") or {}).get("description") or d.get("text", ""))
      )
    return (r.get("result") or {}).get("value")

  def set_viewport(self, width: int, height: int, scale: float = 1.0) -> None:
    self.send(
        "Emulation.setDeviceMetricsOverride",
        {
            "width": int(width),
            "height": int(height),
            "deviceScaleFactor": scale,
            "mobile": False,
        },
    )

  def navigate(self, url: str, timeout: float = 20.0) -> None:
    self._drain_events()
    self.send("Page.navigate", {"url": url})
    self.wait_event("Page.loadEventFired", timeout=timeout)

  def wait_for(
      self, js_predicate: str, timeout: float = 10.0, interval: float = 0.15
  ) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
      try:
        if self.evaluate(f"!!({js_predicate})", timeout=5):
          return True
      except CdpError:
        pass
      time.sleep(interval)
    return False

  def wait_selector(
      self, selector: str, timeout: float = 10.0, visible: bool = True
  ) -> bool:
    sel = json.dumps(selector)
    if visible:
      pred = (
          f"(() => {{ const e = document.querySelector({sel}); if (!e) return"
          " false; const r = e.getBoundingClientRect(); return r.width > 0 &&"
          " r.height > 0 && !e.closest('[hidden]'); })()"
      )
    else:
      pred = f"document.querySelector({sel})"
    return self.wait_for(pred, timeout)

  def element_center(
      self,
      selector: str = "",
      *,
      text: str = "",
      within: str = "",
      index: int = 0,
  ) -> Optional[tuple[float, float]]:
    """Scroll the element into view and return its viewport center (or None).

    `text` matches (case-insensitive substring of innerText) among `selector`
    matches,
    preferring the innermost/shortest match; `within` scopes the search to a
    container.
    """
    js = f"""(() => {{
      const root = {json.dumps(within)} ? document.querySelector({json.dumps(within)}) : document;
      if (!root) return null;
      const sel = {json.dumps(selector or '*')};
      const want = {json.dumps(text.lower())};
      let els = Array.from(root.querySelectorAll(sel)).filter((e) => {{
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && !e.closest('[hidden]');
      }});
      if (want) {{
        els = els.filter((e) => (e.innerText || e.value || e.getAttribute('aria-label') || '')
                                  .toLowerCase().includes(want));
        els.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
      }}
      const e = els[{int(index)}];
      if (!e) return null;
      e.scrollIntoView({{block: 'center', inline: 'center', behavior: 'instant'}});
      const r = e.getBoundingClientRect();
      return [r.left + r.width / 2, r.top + r.height / 2];
    }})()"""
    v = self.evaluate(js)
    return (float(v[0]), float(v[1])) if v else None

  def click_at(self, x: float, y: float) -> None:
    base = {"x": x, "y": y, "button": "left", "clickCount": 1}
    self.send(
        "Input.dispatchMouseEvent",
        {**base, "type": "mouseMoved", "button": "none"},
    )
    self.send("Input.dispatchMouseEvent", {**base, "type": "mousePressed"})
    self.send("Input.dispatchMouseEvent", {**base, "type": "mouseReleased"})

  def click(
      self,
      selector: str = "",
      *,
      text: str = "",
      within: str = "",
      index: int = 0,
      timeout: float = 8.0,
  ) -> bool:
    deadline = time.time() + timeout
    while True:
      c = self.element_center(selector, text=text, within=within, index=index)
      if c:
        time.sleep(0.05)
        c = (
            self.element_center(selector, text=text, within=within, index=index)
            or c
        )
        self.click_at(*c)
        return True
      if time.time() > deadline:
        return False
      time.sleep(0.2)

  def type_text(
      self,
      selector: str,
      text: str,
      *,
      submit: bool = False,
      clear: bool = True,
  ) -> bool:
    if not self.click(selector):
      return False
    sel = json.dumps(selector)
    if clear:
      self.evaluate(
          f"(() => {{ const e = document.querySelector({sel}); e.focus(); if"
          " ('value' in e) { e.value = ''; e.dispatchEvent(new Event('input',"
          " {bubbles: true})); } })()"
      )
    self.send("Input.insertText", {"text": text})
    if submit:
      self.press_key("Enter")
    return True

  def press_key(self, key: str = "Enter") -> None:
    codes = {"Enter": 13, "Escape": 27, "Tab": 9, "Backspace": 8}
    kc = codes.get(key, 0)
    common = {
        "key": key,
        "code": key,
        "windowsVirtualKeyCode": kc,
        "nativeVirtualKeyCode": kc,
    }
    extra = {"text": "\r", "unmodifiedText": "\r"} if key == "Enter" else {}
    self.send("Input.dispatchKeyEvent", {"type": "keyDown", **common, **extra})
    self.send("Input.dispatchKeyEvent", {"type": "keyUp", **common})

  def screenshot(self, path: str, *, clip: Optional[dict] = None) -> str:
    params: dict = {"format": "png", "captureBeyondViewport": False}
    if clip:
      params["clip"] = {**clip, "scale": 1}
    r = self.send("Page.captureScreenshot", params, timeout=60)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
      fh.write(base64.b64decode(r["data"]))
    return path

  def close(self) -> None:
    self.ws.close()


# ------------------------------------------------------------------- browser
class Browser:
  """Launches headless Chrome with a fresh profile and attaches to its first tab."""

  def __init__(
      self,
      *,
      width: int = 560,
      height: int = 960,
      port: int = 0,
      chrome: str = CHROME,
      extra_args: tuple = (),
  ):
    self.width, self.height = width, height
    self.port = port or _free_port()
    self.profile = tempfile.mkdtemp(prefix="dcb_chrome_")
    args = [
        chrome,
        "--headless=new",
        f"--remote-debugging-port={self.port}",
        f"--user-data-dir={self.profile}",
        f"--window-size={width},{height}",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-scrollbars",
        "--disable-gpu",
        "--mute-audio",
        "--disable-extensions",
        "--autoplay-policy=no-user-gesture-required",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--force-color-profile=srgb",
        # Mirrors chromedp defaults (gbrowser): on cloudtops the out-of-process
        # network service otherwise hangs on http:// navigations.
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--ignore-certificate-errors",
        "--enable-features=NetworkService,NetworkServiceInProcess",
        "--disable-features=site-per-process,Translate,BlinkGenPropertyTrees",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-default-apps",
        "--disable-component-extensions-with-background-pages",
        "--disable-breakpad",
        "--disable-client-side-phishing-detection",
        "--disable-hang-monitor",
        "--disable-ipc-flooding-protection",
        "--disable-popup-blocking",
        "--metrics-recording-only",
        "--password-store=basic",
        "--use-mock-keychain",
        "--enable-automation",
        "--allow-browser-signin=false",
        *extra_args,
        "about:blank",
    ]
    self.proc = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    self.page = self._attach()
    self.page.set_viewport(width, height)

  def _json(self, path: str, method: str = "GET"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{self.port}{path}", method=method
    )
    with urllib.request.urlopen(req, timeout=5) as r:
      return json.loads(r.read().decode())

  def _attach(self) -> Page:
    deadline = time.time() + 20
    last = None
    while time.time() < deadline:
      if self.proc.poll() is not None:
        raise CdpError(f"chrome exited with {self.proc.returncode}")
      try:
        targets = self._json("/json/list")
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
          pages = [self._json("/json/new?about:blank", method="PUT")]
        return Page(pages[0]["webSocketDebuggerUrl"])
      except (OSError, CdpError, KeyError, ValueError) as e:
        last = e
        time.sleep(0.2)
    raise CdpError(f"could not attach to chrome: {last}")

  def close(self) -> None:
    try:
      self.page.close()
    except Exception:  # pylint: disable=broad-except
      pass
    self.proc.terminate()
    try:
      self.proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
      self.proc.kill()
    shutil.rmtree(self.profile, ignore_errors=True)

  def __enter__(self) -> "Browser":
    return self

  def __exit__(self, *exc) -> None:
    self.close()


def _free_port() -> int:
  s = socket.socket()
  s.bind(("127.0.0.1", 0))
  port = s.getsockname()[1]
  s.close()
  return port
