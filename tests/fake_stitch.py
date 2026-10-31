"""Fake Stitch MCP server for tests and local UI work.

Implements the Stitch MCP tools DCB uses (get_project, list_screens,
list_design_systems) plus download URLs that exercise every outcome the real
service can produce: raw bytes, a JSON `DownloadFileResponse` wrapper
(`dataString`), and a redirect to a Google sign-in page (b/571529127).

  python3 -B tests/fake_stitch.py --port 8835        # any API key accepted
  DCB_STITCH_MCP_URL=http://127.0.0.1:8835/mcp ./run.sh --port 8823 ...
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import struct
import threading
import zlib

PROJECT_ID = "4044680601076201931"


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
  def chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )

  row = b"\x00" + bytes(rgb) * width
  raw = row * height
  return (
      b"\x89PNG\r\n\x1a\n"
      + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
      + chunk(b"IDAT", zlib.compress(raw, 6))
      + chunk(b"IEND", b"")
  )


HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<script>tailwind.config = { theme: { extend: { colors: { "primary": "#0B57D0",
"surface": "#F8FAFD", "on-surface": "#1F1F1F" }, fontFamily: { "headline":
["Inter", "sans-serif"] }, borderRadius: { "DEFAULT": "0.75rem", "lg": "1rem" } } } }</script>
<style>body{{font-family:Inter,sans-serif;margin:0;background:#F8FAFD;color:#1F1F1F}}
.card{{background:#fff;border-radius:16px;margin:12px;padding:16px}}
button{{background:#0B57D0;color:#fff;border:0;border-radius:999px;padding:10px 16px}}</style>
</head><body class="bg-surface"><header class="p-4"><h1 class="text-primary">{title}</h1>
<nav></nav></header><main class="gap-4 p-4">{cards}</main></body></html>"""


def screen_html(title: str, n: int) -> str:
  cards = "".join(
      f'<section class="card rounded-lg p-4"><img alt=""><h3>Item {i + 1}</h3>'
      '<p>Details</p><button class="rounded-full">Refill</button></section>'
      for i in range(n)
  )
  return (
      HTML.replace("{{", "{")
      .replace("}}", "}")
      .replace("{title}", title)
      .replace("{cards}", cards)
  )


SCREENS = [
    # id, title, device, w, h, screenshot mode, html mode, cards
    (
        "scr_results",
        "Refill / Results / Content @ Phone",
        "MOBILE",
        412,
        915,
        "raw",
        "json",
        3,
    ),
    (
        "scr_empty",
        "Refill / Results / Empty @ Phone",
        "MOBILE",
        412,
        915,
        "json",
        "raw",
        0,
    ),
    (
        "scr_detail",
        "Refill / Detail / Content @ Desktop",
        "DESKTOP",
        1280,
        800,
        "signin",
        "raw",
        2,
    ),
    (
        "scr_broken",
        "Refill / Error @ Phone",
        "MOBILE",
        412,
        915,
        "signin",
        "signin",
        1,
    ),
]
DESIGN_SYSTEM = {
    "name": "assets/15996705518239280238",
    "designSystem": {
        "displayName": "Pharmacy DS",
        "theme": {
            "colorMode": "LIGHT",
            "headlineFont": "OPEN_SANS",
            "bodyFont": "INTER",
            "labelFont": "INTER",
            "roundness": "ROUND_TWELVE",
            "customColor": "#0B57D0",
            "colorVariant": "FIDELITY",
            "overrideSecondaryColor": "#00639B",
            "overrideNeutralColor": "#1F1F1F",
            "designMd": (
                "# Pharmacy DS\n\n- Cards group one prescription each.\n"
            ),
        },
    },
}


class FakeStitch:
  """In-process fake; `require_key=None` accepts any non-empty key."""

  def __init__(self, port: int = 0, require_key: str | None = "test-key"):
    self.require_key = require_key
    self.calls: list[tuple[str, dict]] = []
    self.seen_keys: list[str] = []
    fake = self

    class H(http.server.BaseHTTPRequestHandler):

      def log_message(self, *a):
        pass

      def _key_ok(self) -> bool:
        k = self.headers.get("X-Goog-Api-Key") or ""
        fake.seen_keys.append(k)
        return bool(k) and (fake.require_key is None or k == fake.require_key)

      def _send(self, status, body=b"", ctype="application/json", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
          self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

      def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        msg = json.loads(self.rfile.read(n) or b"{}")
        if not self._key_ok():
          return self._send(401, b'{"error":"unauthenticated"}')
        if "id" not in msg:
          return self._send(202)
        return self._send(200, json.dumps(fake.rpc(msg)).encode())

      def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path.startswith("/signin"):
          return self._send(
              200,
              b"<html>Sign in -"
              b" https://accounts.google.com/v3/signin/identifier</html>",
              "text/html; charset=utf-8",
          )
        if not self._key_ok():
          return self._send(403, b"forbidden", "text/plain")
        kind, sid, mode = (path.strip("/").split("/") + ["", "", ""])[:3]
        scr = next((s for s in SCREENS if s[0] == sid), None)
        if not scr:
          return self._send(404, b"nope", "text/plain")
        if mode == "signin":
          return self._send(
              302, b"", headers={"Location": "/signin/identifier"}
          )
        if kind == "png":
          png = solid_png(
              scr[3] // 4,
              scr[4] // 4,
              (11, 87, 208) if scr[7] else (230, 233, 240),
          )
          if mode == "json":
            body = json.dumps(
                {"dataString": base64.b64encode(png).decode()}
            ).encode()
            return self._send(200, body)
          return self._send(200, png, "image/png")
        html = screen_html(scr[1], scr[7])
        if mode == "json":
          return self._send(200, json.dumps({"dataString": html}).encode())
        return self._send(200, html.encode(), "text/html; charset=utf-8")

    self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    self.port = self.srv.server_address[1]
    self.base = f"http://127.0.0.1:{self.port}"
    self.mcp_url = self.base + "/mcp"

  def start(self) -> "FakeStitch":
    threading.Thread(target=self.srv.serve_forever, daemon=True).start()
    return self

  def stop(self) -> None:
    self.srv.shutdown()
    self.srv.server_close()

  # ---------------------------------------------------------------- MCP
  def _screen(self, s):
    sid, title, device, w, h, smode, hmode, _ = s
    return {
        "name": f"projects/{PROJECT_ID}/screens/{sid}",
        "title": title,
        "deviceType": device,
        "width": str(w),
        "height": str(h),
        "screenshot": {"downloadUrl": f"{self.base}/png/{sid}/{smode}"},
        "htmlCode": {"downloadUrl": f"{self.base}/html/{sid}/{hmode}"},
    }

  def rpc(self, msg):
    rid, method, params = (
        msg.get("id"),
        msg.get("method"),
        msg.get("params") or {},
    )
    ok = lambda r: {"jsonrpc": "2.0", "id": rid, "result": r}  # noqa: E731
    if method == "initialize":
      return ok({
          "protocolVersion": "2025-06-18",
          "capabilities": {"tools": {}},
          "serverInfo": {"name": "fake-stitch", "version": "0"},
      })
    if method == "tools/list":
      return ok({
          "tools": [
              {"name": n, "inputSchema": {"type": "object"}}
              for n in (
                  "list_projects",
                  "get_project",
                  "list_screens",
                  "get_screen",
                  "list_design_systems",
              )
          ]
      })
    if method != "tools/call":
      return {
          "jsonrpc": "2.0",
          "id": rid,
          "error": {"code": -32601, "message": "nope"},
      }
    name, args = params.get("name"), params.get("arguments") or {}
    self.calls.append((name, args))
    if name == "list_projects":
      flt = str(args.get("filter") or "view=owned")
      if "shared" in flt:
        projects = []
      else:
        projects = [{
            "name": f"projects/{PROJECT_ID}",
            "title": "Pharmacy refills",
            "deviceType": "MOBILE",
            "visibility": "PRIVATE",
            "updateTime": "2026-10-16T18:00:00Z",
            "designTheme": DESIGN_SYSTEM["designSystem"]["theme"],
            "screenInstances": [
                {
                    "id": s[0],
                    "sourceScreen": f"projects/{PROJECT_ID}/screens/{s[0]}",
                }
                for s in SCREENS
            ],
        }]
      data = {"projects": projects}
      return ok({
          "content": [{"type": "text", "text": json.dumps(data)}],
          "structuredContent": data,
      })
    name_parts = str(args.get("name") or "").split("/")
    pid = args.get("projectId") or (
        name_parts[1] if len(name_parts) > 1 else ""
    )
    if pid != PROJECT_ID:
      return ok({
          "content": [
              {"type": "text", "text": "Project not found or permission denied"}
          ],
          "isError": True,
      })
    if name == "get_project":
      data = {"name": f"projects/{PROJECT_ID}", "title": "Pharmacy refills"}
    elif name == "list_screens":
      data = {"screens": [self._screen(s) for s in SCREENS]}
    elif name == "list_design_systems":
      data = {"designSystems": [DESIGN_SYSTEM]}
    else:
      data = {}
    return ok({
        "content": [{"type": "text", "text": json.dumps(data)}],
        "structuredContent": data,
    })


if __name__ == "__main__":
  ap = argparse.ArgumentParser()
  ap.add_argument("--port", type=int, default=8835)
  a = ap.parse_args()
  f = FakeStitch(a.port, require_key=None).start()
  print(f"Fake Stitch MCP on {f.mcp_url} (any key)", flush=True)
  threading.Event().wait()
