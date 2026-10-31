"""Minimal routing HTTP server on ThreadingHTTPServer."""

from __future__ import annotations

import collections
import http.server
import json
import mimetypes
import os
import re
import socketserver
import threading
import time
import traceback
from typing import Any, Callable
import urllib.parse

from core import util
from core.service import ApiError

SERVER_STARTED_AT = util.now_iso()
_ERROR_LOCK = threading.Lock()
_RECENT_ERRORS: collections.deque[dict[str, Any]] = collections.deque(maxlen=30)


def record_error(
    method: str,
    path: str,
    status: int,
    error: str,
    tb: str | None = None,
) -> None:
  entry: dict[str, Any] = {
      "at": util.now_iso(),
      "ts_ms": int(time.time() * 1000),
      "method": method,
      "path": path,
      "status": status,
      "error": error[:500],
  }
  if tb:
    entry["traceback"] = tb[-2000:]
  with _ERROR_LOCK:
    _RECENT_ERRORS.append(entry)


def recent_errors() -> list[dict[str, Any]]:
  with _ERROR_LOCK:
    return list(_RECENT_ERRORS)


def clear_errors() -> None:
  with _ERROR_LOCK:
    _RECENT_ERRORS.clear()


mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("text/markdown", ".md")


class Request:

  def __init__(self, handler: "Handler", method: str):
    self.h = handler
    self.method = method
    u = urllib.parse.urlsplit(handler.path)
    self.path = urllib.parse.unquote(u.path)
    self.query = {
        k: v[-1]
        for k, v in urllib.parse.parse_qs(
            u.query, keep_blank_values=True
        ).items()
    }
    self.params: dict[str, str] = {}
    self.user: dict[str, Any] | None = None
    self._body: Any = None

  @property
  def headers(self):
    return self.h.headers

  def body(self) -> Any:
    if self._body is None:
      n = int(self.headers.get("Content-Length") or 0)
      if n > 40 * 1024 * 1024:
        raise ApiError(413, "Request too large")
      raw = self.h.rfile.read(n) if n else b""
      if not raw:
        self._body = {}
      else:
        try:
          self._body = json.loads(raw.decode("utf-8"))
        except ValueError as e:
          raise ApiError(400, "Body must be JSON") from e
    return self._body

  def cookies(self, name: str) -> list[str]:
    raw = self.headers.get("Cookie") or ""
    out: list[str] = []
    for part in raw.split(";"):
      k, _, v = part.strip().partition("=")
      if k == name and v:
        out.append(v)
    return out

  def cookie(self, name: str) -> str | None:
    vals = self.cookies(name)
    return vals[0] if vals else None


class Response:

  def __init__(
      self,
      status: int = 200,
      body: bytes | str = b"",
      content_type: str = "application/json; charset=utf-8",
      headers: dict[str, str] | None = None,
  ):
    self.status = status
    self.body = body.encode("utf-8") if isinstance(body, str) else body
    self.content_type = content_type
    self.headers = headers or {}


def json_response(
    data: Any, status: int = 200, headers: dict[str, str] | None = None
) -> Response:
  return Response(
      status, json.dumps(data, ensure_ascii=False, default=str), headers=headers
  )


def file_response(
    path: str, cache: str = "no-cache", download_name: str | None = None
) -> Response:
  if not os.path.isfile(path):
    raise ApiError(404, "Not found")
  ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
  if ctype.startswith("text/") or ctype in (
      "application/json",
      "text/javascript",
  ):
    ctype += "; charset=utf-8"
  with open(path, "rb") as fh:
    data = fh.read()
  headers = {"Cache-Control": cache}
  if download_name:
    headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
  return Response(200, data, ctype, headers)


def download(data: bytes | str, name: str, ctype: str) -> Response:
  return Response(
      200,
      data,
      ctype,
      {
          "Content-Disposition": f'attachment; filename="{name}"',
          "Cache-Control": "no-store",
      },
  )


Route = tuple[str, re.Pattern, Callable[[Request], Response], bool]


class Router:

  def __init__(self):
    self.routes: list[Route] = []

  def add(
      self,
      method: str,
      pattern: str,
      fn: Callable[[Request], Response],
      auth: bool = True,
  ) -> None:
    # "{name:path}" captures the rest of the path (slashes included).
    rx = re.sub(r"\{(\w+):path\}", r"(?P<\1>.+)", pattern)
    rx = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", rx) + "$"
    self.routes.append((method, re.compile(rx), fn, auth))

  def match(self, method: str, path: str):
    allowed = False
    for m, rx, fn, auth in self.routes:
      mm = rx.match(path)
      if mm:
        if m == method:
          return fn, mm.groupdict(), auth
        allowed = True
    return None, None, allowed


class Handler(http.server.BaseHTTPRequestHandler):
  server_version = "DCB/1.0"
  protocol_version = "HTTP/1.1"
  app: Any = None  # set by make_server

  def log_message(self, fmt: str, *args: Any) -> None:  # quieter logs
    if os.environ.get("DCB_ACCESS_LOG"):
      super().log_message(fmt, *args)

  def _dispatch(self, method: str) -> None:
    req = Request(self, method)
    try:
      resp = self.app.handle(req)
    except ApiError as e:
      if e.status >= 400 and e.status != 401:
        record_error(method, req.path, e.status, str(e))
      resp = json_response({"error": str(e)}, e.status)
    except Exception as e:  # pylint: disable=broad-except
      tb = traceback.format_exc()
      traceback.print_exc()
      record_error(method, req.path, 500, f"Internal error: {e}", tb=tb)
      resp = json_response({"error": f"Internal error: {e}"}, 500)
    # Keep-alive: drain a body the handler never read, or its bytes would be
    # parsed as the start of the next request on this connection.
    if req._body is None:  # pylint: disable=protected-access
      try:
        n = int(self.headers.get("Content-Length") or 0)
      except ValueError:
        n = 0
      if 0 < n <= 40 * 1024 * 1024:
        try:
          self.rfile.read(n)
        except OSError:
          self.close_connection = True
      elif n:
        self.close_connection = True
    try:
      self.send_response(resp.status)
      self.send_header("Content-Type", resp.content_type)
      self.send_header("Content-Length", str(len(resp.body)))
      for k, v in resp.headers.items():
        self.send_header(k, v)
      self.end_headers()
      if method != "HEAD":
        self.wfile.write(resp.body)
    except (BrokenPipeError, ConnectionResetError):
      pass

  def do_GET(self):  # noqa: N802
    self._dispatch("GET")

  def do_HEAD(self):  # noqa: N802
    self._dispatch("HEAD")

  def do_POST(self):  # noqa: N802
    self._dispatch("POST")

  def do_PUT(self):  # noqa: N802
    self._dispatch("PUT")

  def do_PATCH(self):  # noqa: N802
    self._dispatch("PATCH")

  def do_DELETE(self):  # noqa: N802
    self._dispatch("DELETE")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
  daemon_threads = True
  allow_reuse_address = True
  request_queue_size = 128


def make_server(app: Any, host: str, port: int) -> Server:
  handler = type("BoundHandler", (Handler,), {"app": app})
  return Server((host, port), handler)
