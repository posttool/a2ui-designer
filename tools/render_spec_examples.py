#!/usr/bin/env python3
"""Screenshots every A2UI spec example with headless Chrome -> contact sheet.

Stdlib only. Starts a throwaway HTTP server on a random port that mirrors
the backend routes the render page needs:

  /render/example/{name}      -> web/render.html
  /renderer/*                 -> web/renderer/*
  /api/render_example/{name}  -> {messages, theme, viewport, framing}
  /img/{slug}/{w}x{h}.svg     -> neutral placeholder SVG

then drives Chrome over the DevTools protocol (minimal built-in websocket
client), waits for window.__a2uiReady, and captures a full-height PNG.

Usage (cwd = package root):
  python3 tools/render_spec_examples.py [--out_dir /tmp/dcb_spec_examples]
      [--viewport phone|tablet|desktop|smart_display|watch] [--scheme
      light|dark]
      [--framing card|page] [--only 05_product-card,36_modal]
      [--base_url http://127.0.0.1:8810]   # use a running backend instead
"""

import argparse
import base64
import functools
import html
import http.server
import json
import os
import pathlib
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
_NPM_EXAMPLES = (
    ROOT / 'node_modules' / '@a2ui' / 'web_core' / 'src' / 'v1_0' / 'schemas' /
    'catalogs' / 'basic' / 'examples'
)
EXAMPLES = (
    _NPM_EXAMPLES if _NPM_EXAMPLES.is_dir() else
    ROOT / 'seeds' / 'a2ui' / 'catalogs' / 'basic' / 'v1' / 'examples'
)
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))
from render import cdp as _cdp  # pylint: disable=g-import-not-at-top,wrong-import-position

CHROME = _cdp.find_chrome()

VIEWPORTS = {
    'phone': (412, 915),
    'tablet': (834, 1194),
    'desktop': (1280, 800),
    'smart_display': (1024, 600),
    'watch': (192, 192),
}


# ---------------------------------------------------------------------------
# Local mirror server.


class _Handler(http.server.BaseHTTPRequestHandler):
  viewport = 'phone'
  framing = 'card'

  def log_message(self, *args):  # quiet
    pass

  def _send(self, code, body, ctype):
    if isinstance(body, str):
      body = body.encode('utf-8')
    self.send_response(code)
    self.send_header('Content-Type', ctype)
    self.send_header('Content-Length', str(len(body)))
    self.send_header('Cache-Control', 'no-store')
    self.end_headers()
    self.wfile.write(body)

  def do_GET(self):  # pylint: disable=invalid-name
    path = urllib.parse.urlparse(self.path).path
    parts = [urllib.parse.unquote(p) for p in path.split('/') if p]
    try:
      if parts[:1] == ['render']:
        return self._send(
            200, (WEB / 'render.html').read_bytes(), 'text/html; charset=utf-8'
        )
      if parts[:1] == ['renderer'] and len(parts) == 2:
        f = WEB / 'renderer' / parts[1]
        if f.is_file() and '..' not in parts[1]:
          ctype = {'.js': 'text/javascript', '.css': 'text/css'}.get(
              f.suffix, 'application/octet-stream'
          )
          return self._send(200, f.read_bytes(), ctype + '; charset=utf-8')
      if parts[:2] == ['api', 'render_example'] and len(parts) == 3:
        f = EXAMPLES / (parts[2] + '.json')
        if f.is_file():
          ex = json.loads(f.read_text())
          w, h = VIEWPORTS[self.viewport]
          data = {
              'messages': ex.get('messages', []),
              'theme': {},
              'viewport': {'id': self.viewport, 'width': w, 'height': h},
              'framing': self.framing,
              'name': ex.get('name'),
          }
          return self._send(200, json.dumps(data), 'application/json')
      if parts[:1] == ['img'] and len(parts) == 3:
        return self._send(
            200, _placeholder_svg(parts[1], parts[2]), 'image/svg+xml'
        )
    except Exception as e:  # pylint: disable=broad-except
      return self._send(500, json.dumps({'error': str(e)}), 'application/json')
    return self._send(
        404, json.dumps({'error': 'not found'}), 'application/json'
    )


def _placeholder_svg(slug, size):
  try:
    w, h = (int(x) for x in size.replace('.svg', '').split('x'))
  except ValueError:
    w, h = 400, 300
  label = html.escape(slug.replace('-', ' ')[:40])
  return (
      f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
      f'viewBox="0 0 {w} {h}"><rect width="100%" height="100%" '
      'fill="#dfe3e8"/><text x="50%" y="50%" font-family="sans-serif" '
      f'font-size="{max(10, min(w, h) // 10)}" fill="#5f6368" '
      f'text-anchor="middle" dominant-baseline="middle">{label}</text></svg>'
  )


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
  daemon_threads = True
  allow_reuse_address = True


def start_server(viewport, framing):
  handler = type('H', (_Handler,), {'viewport': viewport, 'framing': framing})
  srv = _Server(('127.0.0.1', 0), handler)
  threading.Thread(target=srv.serve_forever, daemon=True).start()
  return srv, f'http://127.0.0.1:{srv.server_address[1]}'


# ---------------------------------------------------------------------------
# Minimal DevTools-protocol client over a stdlib websocket.


class CDP:
  """Tiny synchronous CDP client (one page target)."""

  def __init__(self, ws_url):
    u = urllib.parse.urlparse(ws_url)
    self.sock = socket.create_connection((u.hostname, u.port), timeout=30)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f'GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n'
        'Upgrade: websocket\r\nConnection: Upgrade\r\n'
        f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'
    )
    self.sock.sendall(req.encode())
    resp = b''
    while b'\r\n\r\n' not in resp:
      chunk = self.sock.recv(4096)
      if not chunk:
        raise RuntimeError('websocket handshake failed')
      resp += chunk
    if b' 101 ' not in resp.split(b'\r\n', 1)[0]:
      raise RuntimeError('websocket handshake rejected: %r' % resp[:200])
    self.buf = resp.split(b'\r\n\r\n', 1)[1]
    self.next_id = 0

  def _recv_exact(self, n):
    while len(self.buf) < n:
      chunk = self.sock.recv(max(65536, n - len(self.buf)))
      if not chunk:
        raise RuntimeError('websocket closed')
      self.buf += chunk
    out, self.buf = self.buf[:n], self.buf[n:]
    return out

  def _send_frame(self, payload):
    data = payload.encode('utf-8')
    head = bytearray([0x81])
    n = len(data)
    if n < 126:
      head.append(0x80 | n)
    elif n < 65536:
      head.append(0x80 | 126)
      head += struct.pack('>H', n)
    else:
      head.append(0x80 | 127)
      head += struct.pack('>Q', n)
    mask = os.urandom(4)
    head += mask
    self.sock.sendall(
        bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    )

  def _recv_message(self):
    parts = []
    while True:
      b1, b2 = self._recv_exact(2)
      op = b1 & 0x0F
      n = b2 & 0x7F
      if n == 126:
        n = struct.unpack('>H', self._recv_exact(2))[0]
      elif n == 127:
        n = struct.unpack('>Q', self._recv_exact(8))[0]
      if b2 & 0x80:
        mask = self._recv_exact(4)
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(self._recv_exact(n)))
      else:
        data = self._recv_exact(n)
      if op == 0x9:  # ping -> ignore (Chrome doesn't require pong)
        continue
      if op == 0x8:
        raise RuntimeError('websocket closed by peer')
      parts.append(data)
      if b1 & 0x80:
        return b''.join(parts).decode('utf-8')

  def call(self, method, **params):
    self.next_id += 1
    mid = self.next_id
    self._send_frame(
        json.dumps({'id': mid, 'method': method, 'params': params})
    )
    while True:
      msg = json.loads(self._recv_message())
      if msg.get('id') == mid:
        if 'error' in msg:
          raise RuntimeError(f'{method}: {msg["error"]}')
        return msg.get('result', {})

  def evaluate(self, expr):
    r = self.call(
        'Runtime.evaluate',
        expression=expr,
        returnByValue=True,
        awaitPromise=True,
    )
    return r.get('result', {}).get('value')

  def close(self):
    try:
      self.sock.close()
    except OSError:
      pass


def _free_port():
  with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    return s.getsockname()[1]


class Browser:
  """Headless Chrome with one page target driven over CDP."""

  def __init__(self, width, height):
    if not CHROME:
      raise SystemExit(_cdp.CHROME_MISSING_MESSAGE)
    self.port = _free_port()
    self.profile = tempfile.mkdtemp(prefix='dcb_chrome_')
    args = [
        CHROME,
        '--headless=new',
        '--no-sandbox',
        '--disable-gpu',
        '--enable-features=NetworkService,NetworkServiceInProcess',
        '--disable-features=site-per-process,Translate',
        '--disable-dev-shm-usage',
        '--hide-scrollbars',
        '--mute-audio',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-extensions',
        '--disable-background-networking',
        '--disable-sync',
        '--disable-default-apps',
        '--disable-breakpad',
        '--disable-component-extensions-with-background-pages',
        '--disable-background-timer-throttling',
        '--disable-renderer-backgrounding',
        '--force-color-profile=srgb',
        '--metrics-recording-only',
        '--password-store=basic',
        '--use-mock-keychain',
        '--enable-automation',
        f'--remote-debugging-port={self.port}',
        f'--user-data-dir={self.profile}',
        f'--window-size={width},{height}',
        'about:blank',
    ]
    self.proc = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    ws = None
    deadline = time.time() + 20
    while time.time() < deadline and ws is None:
      try:
        with urllib.request.urlopen(
            f'http://127.0.0.1:{self.port}/json/list', timeout=2
        ) as r:
          for t in json.loads(r.read()):
            if t.get('type') == 'page':
              ws = t['webSocketDebuggerUrl']
              break
      except OSError:
        time.sleep(0.2)
    if not ws:
      self.close()
      raise RuntimeError('Chrome DevTools endpoint did not come up')
    self.cdp = CDP(ws)
    self.cdp.call('Page.enable')
    self.cdp.call('Runtime.enable')

  def screenshot(self, url, width, height, out_png, timeout=15.0):
    """Loads url, waits for __a2uiReady, saves a full-height PNG."""
    c = self.cdp
    c.call(
        'Emulation.setDeviceMetricsOverride',
        width=width,
        height=height,
        deviceScaleFactor=1,
        mobile=False,
    )
    c.call('Page.navigate', url=url)
    ready = None
    deadline = time.time() + timeout
    time.sleep(0.15)
    while time.time() < deadline:
      try:
        ready = c.evaluate(
            'window.__a2uiReady ? JSON.stringify(window.__a2uiReady) : null'
        )
      except RuntimeError:
        ready = None
      if ready:
        break
      time.sleep(0.1)
    ready = (
        json.loads(ready)
        if ready
        else {'errors': ['timeout waiting for __a2uiReady'], 'height': height}
    )
    full_h = max(1, min(int(ready.get('height') or height), 12000))
    c.call(
        'Emulation.setDeviceMetricsOverride',
        width=width,
        height=full_h,
        deviceScaleFactor=1,
        mobile=False,
    )
    time.sleep(0.1)
    shot = c.call(
        'Page.captureScreenshot',
        format='png',
        captureBeyondViewport=True,
        clip={'x': 0, 'y': 0, 'width': width, 'height': full_h, 'scale': 1},
    )
    pathlib.Path(out_png).write_bytes(base64.b64decode(shot['data']))
    return ready

  def close(self):
    try:
      self.cdp.close()
    except Exception:  # pylint: disable=broad-except
      pass
    if self.proc.poll() is None:
      self.proc.terminate()
      try:
        self.proc.wait(5)
      except subprocess.TimeoutExpired:
        self.proc.kill()
    shutil.rmtree(self.profile, ignore_errors=True)


# ---------------------------------------------------------------------------
# Contact sheet.


def write_contact_sheet(out_dir, rows, title):
  cards = []
  for r in rows:
    meta = r['ready']
    flags = []
    if meta.get('unsupported'):
      flags.append(
          '<span class="tag t-uns">unsupported: %s</span>'
          % html.escape(', '.join(meta['unsupported']))
      )
    if meta.get('missingImages'):
      flags.append(
          '<span class="tag t-img">missing images: %d</span>'
          % meta['missingImages']
      )
    if meta.get('overflowX'):
      flags.append('<span class="tag t-bug">overflow-x</span>')
    for e in meta.get('errors') or []:
      flags.append('<span class="tag t-bug">%s</span>' % html.escape(e))
    cards.append(
        '<figure><a href="{png}" target="_blank"><img src="{png}"'
        ' loading="lazy" alt=""></a><figcaption><b>{name}</b><span'
        ' class="desc">{desc}</span>{flags}</figcaption></figure>'.format(
            png=html.escape(r['png']),
            name=html.escape(r['name']),
            desc=html.escape(r.get('title') or ''),
            flags=''.join(flags),
        )
    )
  doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  body {{ background:#f7f4ed; color:#111; margin:0; padding:28px 32px;
         font:14px/1.45 "Google Sans", system-ui, sans-serif; }}
  h1 {{ font-size:22px; font-weight:600; margin:0 0 4px; }}
  .sub {{ color:#555; margin:0 0 24px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr));
          gap:22px; align-items:start; }}
  figure {{ margin:0; background:#fff; border:1px solid #e3ddd0; border-radius:12px;
           overflow:hidden; }}
  figure img {{ display:block; width:100%; max-height:560px; object-fit:cover;
               object-position:top; border-bottom:1px solid #eee8dc; }}
  figcaption {{ padding:10px 12px 12px; display:flex; flex-direction:column; gap:4px; }}
  .desc {{ color:#666; font-size:12px; }}
  .tag {{ font-size:11px; padding:2px 6px; border-radius:5px; align-self:flex-start; }}
  .t-uns {{ background:#efe6ff; color:#4a2a8a; }}
  .t-img {{ background:#eef1f4; color:#3c4650; }}
  .t-bug {{ background:#fde3df; color:#8c1d18; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p class="sub">{len(rows)} examples · click a thumbnail for the full-size PNG</p>
<div class="grid">{''.join(cards)}</div></body></html>"""
  path = pathlib.Path(out_dir) / 'index.html'
  path.write_text(doc, encoding='utf-8')
  return path


def main(argv=None):
  ap = argparse.ArgumentParser(
      description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
  )
  ap.add_argument('--out_dir', default='/tmp/dcb_spec_examples')
  ap.add_argument('--viewport', default='phone', choices=sorted(VIEWPORTS))
  ap.add_argument('--scheme', default='light', choices=['light', 'dark'])
  ap.add_argument('--framing', default='card', choices=['card', 'page'])
  ap.add_argument('--only', default='', help='comma-separated example names')
  ap.add_argument(
      '--base_url',
      default='',
      help=(
          'use a running backend (e.g. http://127.0.0.1:8810) '
          'instead of the built-in mirror server'
      ),
  )
  args = ap.parse_args(argv)

  names = sorted(p.stem for p in EXAMPLES.glob('*.json'))
  if args.only:
    want = [n.strip() for n in args.only.split(',') if n.strip()]
    names = [
        n for n in names if n in want or any(n.startswith(w) for w in want)
    ]
  out_dir = pathlib.Path(args.out_dir)
  out_dir.mkdir(parents=True, exist_ok=True)
  width, height = VIEWPORTS[args.viewport]

  srv = None
  base = args.base_url.rstrip('/')
  if not base:
    srv, base = start_server(args.viewport, args.framing)
  browser = Browser(width, height)
  rows = []
  try:
    for name in names:
      title = json.loads((EXAMPLES / f'{name}.json').read_text()).get(
          'name', ''
      )
      q = urllib.parse.urlencode({
          'viewport': args.viewport,
          'scheme': args.scheme,
          'framing': args.framing,
          'interactive': '0',
      })
      url = f'{base}/render/example/{urllib.parse.quote(name)}?{q}'
      png = f'{name}.png'
      t0 = time.time()
      try:
        ready = browser.screenshot(url, width, height, out_dir / png)
      except Exception as e:  # pylint: disable=broad-except
        ready = {'errors': [f'screenshot failed: {e}']}
      rows.append({'name': name, 'title': title, 'png': png, 'ready': ready})
      flag = []
      if ready.get('unsupported'):
        flag.append('unsupported=' + ','.join(ready['unsupported']))
      if ready.get('missingImages'):
        flag.append(f'missingImages={ready["missingImages"]}')
      if ready.get('overflowX'):
        flag.append('OVERFLOW-X')
      if ready.get('errors'):
        flag.append('errors=' + '; '.join(ready['errors']))
      print(
          f'  {name:36s} {ready.get("height", "?")!s:>5}px '
          f'{time.time() - t0:4.1f}s {" ".join(flag)}',
          flush=True,
      )
  finally:
    browser.close()
    if srv:
      srv.shutdown()
  (out_dir / 'results.json').write_text(json.dumps(rows, indent=2))
  sheet = write_contact_sheet(
      out_dir,
      rows,
      f'A2UI v1.0 Basic catalog examples — {args.viewport} {width}px · {args.scheme} · '
      f'{args.framing}',
  )
  print(sheet)
  return 0


if __name__ == '__main__':
  sys.exit(main())
