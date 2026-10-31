#!/usr/bin/env python3
"""Headless-Chrome check of the web renderer host (web/renderer/host.js).

Mounts every upstream Basic catalog v1 example (seeds/a2ui/catalogs/basic/v1/
examples/*.json) plus custom-component, composition, extension and
unsupported-component fixtures through `mountSurface`, using the official
@a2ui/web_core + @a2ui/lit bundle, and asserts for each:

  * no renderer errors (meta.errors) and no uncaught page exceptions,
  * a non-empty `__a2uiBoxes()`-style box list with `id|scope` keys,
  * design tokens applied as CSS variables on the host element.

Writes contact sheets to /tmp/a2ui_designer_renderer/ (override with
--out). Usage:

  python3 tests/renderer_check.py [--out DIR] [--only SUBSTR] [--keep]

Exit code 0 when every case passes. Stdlib only; needs Chrome/Chromium
(CHROME_BIN overrides discovery, see render/cdp.py).
"""

from __future__ import annotations

import argparse
import base64
import functools
import glob
import http.server
import json
import os
import shutil
import socket
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from render import cdp  # pylint: disable=g-import-not-at-top

EXAMPLES_DIRS = [
    os.path.join(ROOT, "seeds", "a2ui", "catalogs", "basic", "v1", "examples"),
]
BASIC = "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"
EXT = "https://posttool.github.io/a2ui-designer/catalogs/extensions/v1/catalog.json"
PROJECT = "https://posttool.github.io/a2ui-designer/catalogs/projects/check/catalog.json"
CELL_W = 412
COLS = 4

THEME = {
    "primary": "#6750A4",
    "on_primary": "#FFFFFF",
    "surface": "#FFFBFE",
    "on_surface": "#1C1B1F",
    "surface_container": "#F3EDF7",
    "radius_card": 20,
    "font_family": "Google Sans Flex",
}


def _msgs(sid, components, data=None, catalog=BASIC):
  out = [{"version": "v1.0", "createSurface": {"surfaceId": sid, "catalogId": catalog}}]
  out.append({"version": "v1.0", "updateComponents": {"surfaceId": sid, "components": components}})
  if data is not None:
    out.append({"version": "v1.0", "updateDataModel": {"surfaceId": sid, "path": "/", "value": data}})
  return out


STAT_TILE = {
    "name": "StatTile",
    "description": "Metric tile",
    "props": {
        "type": "object",
        "properties": {
            "label": {"$ref": "common_types.json#/$defs/DynamicString"},
            "value": {"$ref": "common_types.json#/$defs/DynamicString"},
            "icon": {"type": "string", "default": "star"},
            "items": {"type": "array"},
            "image": {"$ref": "common_types.json#/$defs/DynamicString"},
            "footer": {"$ref": "common_types.json#/$defs/ComponentId"},
            "action": {"$ref": "common_types.json#/$defs/Action"},
        },
        "required": ["label", "value"],
    },
    "web": {
        "template": (
            '<div class="tile" data-action="action">'
            '<span class="ic" data-icon="{{icon}}"></span>'
            '<div class="lbl">{{label}}</div><div class="val">{{value}}</div>'
            '<ul><li data-each="items as it">{{it.name}}: {{it.qty}}</li></ul>'
            '<img src="{{image}}" alt="">'
            '<slot name="footer"></slot></div>'
        ),
        "css": (
            ".tile{display:flex;flex-direction:column;gap:4px;padding:12px;"
            "border-radius:var(--a2ui-radius-card);background:var(--a2ui-primary-container);"
            "color:var(--a2ui-on-primary-container)}"
            ".ic{font-family:'Material Symbols Rounded';font-size:24px}"
            ".lbl{font-size:13px;opacity:.8}.val{font-size:28px;font-weight:600}"
            "img{width:48px;height:48px;border-radius:8px;object-fit:cover}"
            "ul{margin:0;padding-left:18px}"
        ),
    },
    "sample": {},
}

PROFILE_ROW = {
    "name": "ProfileRow",
    "description": "Composition component",
    "strategy": "basic_composition",
    "props": {
        "type": "object",
        "properties": {
            "name": {"$ref": "common_types.json#/$defs/DynamicString"},
            "role": {"$ref": "common_types.json#/$defs/DynamicString"},
        },
    },
    "composition": {
        "root": "row",
        "components": [
            {"id": "row", "component": "Row", "children": ["avatar", "col"], "align": "center"},
            {"id": "avatar", "component": "Icon", "name": "accountCircle"},
            {"id": "col", "component": "Column", "children": ["n", "r"]},
            {"id": "n", "component": "Text", "text": "{{name}}"},
            {"id": "r", "component": "Text", "text": "{{role}}", "variant": "caption"},
        ],
    },
}

SVG = (
    '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
    '<script>alert(2)</script><circle cx="12" cy="12" r="10" fill="#E8710A"/></svg>'
)
IMG = (
    "data:image/svg+xml;utf8,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' width='400' height='240'%3E"
    "%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E"
    "%3Cstop offset='0' stop-color='%23f6c26b'/%3E%3Cstop offset='1' stop-color='%233a6ea5'/%3E"
    "%3C/linearGradient%3E%3C/defs%3E%3Crect width='400' height='240' fill='url(%23g)'/%3E%3C/svg%3E"
)


def fixtures():
  """Custom/composition/extension/unsupported cases beyond the upstream examples."""
  return [
      {
          "name": "fixture: custom web template",
          "messages": _msgs("custom", [
              {"id": "root", "component": "Column", "children": ["tile", "tile2"]},
              {"id": "tile", "component": "StatTile", "catalogId": PROJECT,
               "label": "Steps", "value": {"@path": "/steps"},
               "items": {"@path": "/items"}, "image": IMG, "footer": "foot",
               "action": {"event": {"name": "open_steps"}}},
              {"id": "foot", "component": "Text", "text": "Footer *slot*"},
              # No catalogId: the host must inject the project catalog id.
              {"id": "tile2", "component": "StatTile", "label": "Water", "value": "1.2 L",
               "icon": "favorite"},
          ], {"steps": "8,204", "items": [{"name": "Apples", "qty": 2}, {"name": "Pears", "qty": 5}]}),
          "customComponents": {"StatTile": STAT_TILE},
          "catalogs": {"projectCatalogId": PROJECT, "extensions": True},
          "clickJs": "mount.querySelector('[data-a2ui-id=\"tile\"]').shadowRoot.querySelector('.tile').click()",
          "expect": {"custom": ["StatTile"], "ids": ["tile|", "tile2|", "foot|"],
                     "actions": ["open_steps"]},
      },
      {
          "name": "fixture: composition component",
          "messages": _msgs("comp", [
              {"id": "root", "component": "Card", "child": "list"},
              {"id": "list", "component": "Column", "children": ["p1", "p2"]},
              {"id": "p1", "component": "ProfileRow", "name": "Ada Lovelace", "role": "Engineer"},
              {"id": "p2", "component": "ProfileRow", "name": {"@path": "/n"}, "role": "Designer"},
          ], {"n": "Grace Hopper"}),
          "customComponents": {"ProfileRow": PROFILE_ROW},
          "catalogs": {"projectCatalogId": PROJECT},
          "expect": {"ids": ["p1|", "p1__n|", "p2__r|"], "text": ["Ada Lovelace", "Grace Hopper"]},
      },
      {
          "name": "fixture: extension catalog",
          "messages": _msgs("ext", [
              {"id": "root", "component": "Column", "children": ["hero", "icons"]},
              {"id": "hero", "component": "HeroCard", "catalogId": EXT, "image": IMG,
               "child": "hero_col", "scrim": "dark"},
              {"id": "hero_col", "component": "Column", "children": ["hero_t", "hero_s"]},
              {"id": "hero_t", "component": "Text", "text": "# Harbor weekend"},
              {"id": "hero_s", "component": "Text", "text": "Sat 9:00 · Pier 39", "variant": "caption"},
              {"id": "icons", "component": "Row", "children": ["svg1", "svg2"], "align": "center"},
              {"id": "svg1", "component": "SvgIcon", "svg": SVG, "size": 32},
              {"id": "svg2", "component": "SvgIcon", "catalogId": EXT, "url": IMG, "size": 48},
          ]),
          "catalogs": {"extensions": True},
          "expect": {"ids": ["hero|", "svg1|", "svg2|", "hero_t|"], "noScript": True},
      },
      {
          "name": "fixture: unsupported + templates",
          "messages": _msgs("unsup", [
              {"id": "root", "component": "Column", "children": ["mystery", "list"]},
              {"id": "mystery", "component": "FancyCarousel", "label": "Carousel", "children": ["t1"]},
              {"id": "t1", "component": "Text", "text": "inside unsupported"},
              {"id": "list", "component": "List", "children": {"componentId": "row_t", "path": "/rows"}},
              {"id": "row_t", "component": "Text", "text": {"@path": "label"}},
          ], {"rows": [{"label": "Row A"}, {"label": "Row B"}, {"label": "Row C"}]}),
          "expect": {"unsupported": ["FancyCarousel"], "ids": ["mystery|", "row_t|/rows/0", "row_t|/rows/2"],
                     "allowErrors": False},
      },
  ]


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Google+Sans+Flex:opsz,wght@6..144,1..1000&display=swap">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=block">
<link rel="stylesheet" href="/renderer/host.css">
<style>
  body { margin: 0; background: #e9e5dc; font: 12px/1.3 system-ui, sans-serif; }
  #grid { display: grid; grid-template-columns: repeat(__COLS__, __W__px); gap: 12px; padding: 12px; align-items: start; }
  .cell { background: #fff; outline: 1px solid #c9c2b4; }
  .cap { padding: 4px 8px; background: #f6f2ea; color: #333; border-bottom: 1px solid #ddd5c6;
         white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cap.bad { background: #fde3e1; color: #8c1d18; }
  .mount { width: __W__px; }
</style></head>
<body><div id="grid"></div>
<script type="module">
import {mountSurface} from '/renderer/host.js';
window.__errors = [];
window.addEventListener('error', (e) => window.__errors.push(String(e.message || e)));
window.addEventListener('unhandledrejection', (e) => window.__errors.push(String(e.reason && e.reason.message || e.reason)));
function boxes(root) {
  const out = [];
  const sx = window.scrollX, sy = window.scrollY;
  for (const n of root.querySelectorAll('[data-a2ui-id]')) {
    const r = n.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    out.push({k: `${n.dataset.a2uiId}|${n.dataset.a2uiScope || ''}`,
      x: Math.round(r.left + sx), y: Math.round(r.top + sy), w: Math.round(r.width), h: Math.round(r.height)});
  }
  return out;
}
function overflowX(mount) {
  const root = mount.querySelector('.a2ui-root');
  const rr = mount.getBoundingClientRect();
  for (const n of mount.querySelectorAll('[data-a2ui-id]')) {
    if (n.closest('dialog')) continue;
    const r = n.getBoundingClientRect();
    if (r.width && (r.right > rr.right + 1 || r.left < rr.left - 1)) return n.dataset.a2uiId;
  }
  return null;
}
window.__run = async (cases, theme) => {
  const grid = document.getElementById('grid');
  const results = [];
  for (const c of cases) {
    const cell = document.createElement('div'); cell.className = 'cell';
    const cap = document.createElement('div'); cap.className = 'cap'; cap.textContent = c.name;
    const mount = document.createElement('div'); mount.className = 'mount';
    cell.append(cap, mount); grid.append(cell);
    const actions = [];
    const errsBefore = window.__errors.length;
    let meta = null, err = null;
    try {
      const s = mountSurface(mount, c.messages, {theme: c.theme || theme, framing: 'card', interactive: true,
        customComponents: c.customComponents || {}, catalogs: c.catalogs || {},
        onAction: (p) => actions.push(p)});
      await s.settled(4000);
      meta = s.meta;
    } catch (e) { err = String(e && e.stack || e); }
    const host = mount.querySelector('.a2ui-host');
    const cs = host ? getComputedStyle(host) : null;
    const res = {
      name: c.name, meta, err, boxes: boxes(mount),
      vars: cs ? {primary: cs.getPropertyValue('--a2ui-color-primary').trim(),
                  canon: cs.getPropertyValue('--a2ui-primary').trim(),
                  cardRadius: cs.getPropertyValue('--a2ui-card-border-radius').trim()} : null,
      text: mount.innerText,
      pageErrors: window.__errors.slice(errsBefore),
      overflow: overflowX(mount),
      scripts: [...mount.querySelectorAll('script')].length,
      height: Math.round(mount.getBoundingClientRect().height),
    };
    if (c.clickJs) {
      try { new Function('mount', c.clickJs)(mount); } catch (e) { res.clickError = String(e); }
      await new Promise((r) => setTimeout(r, 100));
      res.actions = actions.map((a) => a.action && a.action.name);
    }
    const bad = err || (meta && meta.errors.length) || res.pageErrors.length || !res.boxes.length;
    if (bad) cap.classList.add('bad');
    results.push(res);
  }
  return results;
};
window.__pageReady = true;
</script></body></html>
"""


def check_render_page(pg, port, out_dir):
  """web/render.html contract: live render, __a2uiReady, boxes, select, update."""
  probs = []
  pg.set_viewport(412, 900)
  pg.navigate(f"http://127.0.0.1:{port}/render.html?inspect=1&viewport=phone")
  fx = fixtures()[0]
  payload = {"type": "a2ui-render", "messages": fx["messages"], "theme": THEME,
             "components": fx["customComponents"],
             "catalogs": fx["catalogs"]}
  pg.wait_for("document.getElementById('surface')", timeout=10)
  pg.evaluate(f"window.postMessage({json.dumps(payload)}, '*')")
  if not pg.wait_for("window.__a2uiReady", timeout=15):
    return [("render.html", ["__a2uiReady never set"])]
  ready = pg.evaluate("window.__a2uiReady")
  for k in ("height", "width", "overflowX", "unsupported", "custom", "missingImages",
            "errors", "surfaces", "components"):
    if k not in ready:
      probs.append(f"__a2uiReady missing {k!r}")
  if ready.get("errors"):
    probs.append(f"__a2uiReady.errors: {ready['errors']}")
  if not ready.get("height"):
    probs.append("zero height")
  boxes = pg.evaluate("window.__a2uiBoxes()") or []
  keys = {b.get("k") for b in boxes}
  if not {"root|", "tile|", "foot|"} <= keys:
    probs.append(f"__a2uiBoxes keys: {sorted(keys)}")
  if boxes and set(boxes[0]) != {"k", "x", "y", "w", "h"}:
    probs.append(f"box shape: {boxes[0]}")
  pg.evaluate("window.postMessage({type: 'a2ui-select', id: 'tile2', scroll: false}, '*')")
  if not pg.wait_for("document.querySelector('[data-a2ui-id=\"tile2\"].a2ui-inspect-selected')", timeout=5):
    probs.append("a2ui-select did not mark the component")
  upd = {"type": "a2ui-update", "messages": [{"version": "v1.0", "updateDataModel": {
      "surfaceId": "custom", "path": "/steps", "value": "9,999"}}]}
  pg.evaluate(f"window.postMessage({json.dumps(upd)}, '*')")
  if not pg.wait_for(
      "document.querySelector('[data-a2ui-id=\"tile\"]').shadowRoot.textContent.includes('9,999')",
      timeout=5):
    probs.append("a2ui-update (updateDataModel) did not re-render the custom component")
  pg.evaluate("window.postMessage({type: 'a2ui-theme', theme: {primary: '#B3261E'}}, '*')")
  if not pg.wait_for(
      "getComputedStyle(document.querySelector('.a2ui-host')).getPropertyValue('--a2ui-color-primary').trim().toLowerCase() === '#b3261e'",
      timeout=5):
    probs.append("a2ui-theme did not re-theme the host")
  # Editing a Definition must register a fresh element (versioned tag).
  def_b = dict(STAT_TILE, web=dict(STAT_TILE["web"], css=".tile{color:red}"))
  tags = pg.evaluate(
      "import('/renderer/host.js').then((m) => {"
      f" const a = m.defineCustomComponent({json.dumps(STAT_TILE)}).tagName;"
      f" const b = m.defineCustomComponent({json.dumps(def_b)}).tagName;"
      " return [a, b, !!customElements.get(a), m.defineCustomComponent("
      f"{json.dumps(STAT_TILE)}).tagName === a]; }})")
  if not tags or tags[0] == tags[1] or not tags[2] or not tags[3]:
    probs.append(f"custom element versioning: {tags}")
  pg.screenshot(os.path.join(out_dir, "render_page.png"))
  return [("render.html", probs)] if probs else []


def _free_port() -> int:
  for port in range(8150, 8200):
    with socket.socket() as s:
      try:
        s.bind(("127.0.0.1", port))
        return port
      except OSError:
        continue
  raise RuntimeError("no free port in 8150-8199")


def _serve(root: str, port: int) -> http.server.ThreadingHTTPServer:
  handler = functools.partial(_QuietHandler, directory=root)
  srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
  threading.Thread(target=srv.serve_forever, daemon=True).start()
  return srv


class _QuietHandler(http.server.SimpleHTTPRequestHandler):

  def log_message(self, *args):  # pylint: disable=arguments-differ
    pass

  def end_headers(self):
    self.send_header("Cache-Control", "no-store")
    super().end_headers()


def load_examples():
  for d in EXAMPLES_DIRS:
    files = sorted(glob.glob(os.path.join(d, "*.json")))
    if files:
      out = []
      for f in files:
        with open(f, encoding="utf-8") as fh:
          ex = json.load(fh)
        case = {"name": os.path.basename(f)[:-5], "messages": ex["messages"], "expect": {}}
        if case["name"] == "00_interactive-button":
          case["clickJs"] = "mount.querySelector('a2ui-basic-button button').click()"
          case["expect"]["actions"] = ["button_clicked"]
        out.append(case)
      return out, d
  return [], None


def check(results, cases, warnings):
  failures = []
  by_name = {c["name"]: c for c in cases}
  for r in results:
    exp = by_name[r["name"]].get("expect") or {}
    probs = []
    if r["err"]:
      probs.append(f"mount threw: {r['err'][:300]}")
    meta = r["meta"] or {}
    if meta.get("errors"):
      probs.append(f"meta.errors: {meta['errors'][:4]}")
    if r["pageErrors"]:
      probs.append(f"page errors: {r['pageErrors'][:3]}")
    if not r["boxes"]:
      probs.append("no boxes")
    keys = {b["k"] for b in r["boxes"]}
    for k in exp.get("ids", []):
      if k not in keys:
        probs.append(f"missing box {k!r} (have {sorted(keys)[:12]})")
    for t in exp.get("text", []):
      if t not in (r["text"] or ""):
        probs.append(f"missing text {t!r}")
    for n in exp.get("custom", []):
      if n not in meta.get("custom", []):
        probs.append(f"custom {n!r} not reported in meta.custom")
    for n in exp.get("unsupported", []):
      if n not in meta.get("unsupported", []):
        probs.append(f"unsupported {n!r} not reported")
    if "actions" in exp and r.get("actions") != exp["actions"]:
      probs.append(f"actions {r.get('actions')} != {exp['actions']} {r.get('clickError') or ''}")
    if exp.get("noScript") and r["scripts"]:
      probs.append("script element rendered")
    v = r["vars"] or {}
    want = (exp.get("darkPrimary") or THEME["primary"]).lower()
    if v.get("primary", "").lower() != want or v.get("canon", "").lower() != want:
      probs.append(f"theme vars not applied: {v}")
    if v.get("cardRadius") != f"{THEME['radius_card']}px":
      probs.append(f"card radius var: {v.get('cardRadius')}")
    if r["overflow"]:
      # Upstream examples are not all designed for a 412px phone; report
      # overflow (visual-quality signal) without failing, except fixtures.
      msg = f"horizontal overflow at {r['overflow']}"
      if r["name"].startswith("fixture"):
        probs.append(msg)
      else:
        warnings.append((r["name"], msg))
    if probs:
      failures.append((r["name"], probs))
  return failures


def main(argv=None) -> int:
  ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  ap.add_argument("--out", default="/tmp/a2ui_designer_renderer")
  ap.add_argument("--only", default="")
  ap.add_argument("--keep", action="store_true", help="keep the temp web root")
  args = ap.parse_args(argv)

  examples, src = load_examples()
  if not examples:
    print("no upstream examples found", file=sys.stderr)
    return 2
  dark = [dict(e, name=f"dark: {e['name']}", theme={**THEME, "scheme": "dark", "primary": "#D0BCFF",
                                                    "on_primary": "#381E72", "surface": "",
                                                    "on_surface": "", "surface_container": ""},
              expect={"darkPrimary": "#D0BCFF"})
          for e in examples if e["name"] in ("09_login-form", "13_coffee-order", "24_recipe-card")]
  cases = examples + fixtures() + dark
  if args.only:
    cases = [c for c in cases if args.only in c["name"]]

  tmp = tempfile.mkdtemp(prefix="a2uid_renderer_check_")
  for name in ("renderer", "vendor", "render.html"):
    os.symlink(os.path.join(ROOT, "web", name), os.path.join(tmp, name))
  page = PAGE.replace("__COLS__", str(COLS)).replace("__W__", str(CELL_W))
  with open(os.path.join(tmp, "check.html"), "w", encoding="utf-8") as fh:
    fh.write(page)
  port = _free_port()
  srv = _serve(tmp, port)
  os.makedirs(args.out, exist_ok=True)
  width = COLS * (CELL_W + 12) + 12
  browser = cdp.Browser(width=width, height=900)
  try:
    pg = browser.page
    pg.navigate(f"http://127.0.0.1:{port}/check.html")
    if not pg.wait_for("window.__pageReady === true", timeout=20):
      errs = [c for c in pg.console[-10:]]
      print(f"check page did not load: {errs}", file=sys.stderr)
      return 2
    results = pg.evaluate(
        f"window.__run({json.dumps(cases)}, {json.dumps(THEME)})", timeout=300)
    # Contact sheet (full page).
    full_h = int(pg.evaluate("document.documentElement.scrollHeight"))
    shots = []
    chunk = 4000
    pg.set_viewport(width, min(full_h, chunk))
    for i, y in enumerate(range(0, full_h, chunk)):
      h = min(chunk, full_h - y)
      r = pg.send("Page.captureScreenshot", {
          "format": "png", "captureBeyondViewport": True,
          "clip": {"x": 0, "y": y, "width": width, "height": h, "scale": 1}}, timeout=60)
      path = os.path.join(args.out, f"contact_sheet_{i + 1}.png")
      with open(path, "wb") as fh:
        fh.write(base64.b64decode(r["data"]))
      shots.append(path)
    # Per-case crops at native resolution (cases/<name>.png).
    rects = pg.evaluate(
        "[...document.querySelectorAll('.cell')].map((c) => {"
        " const r = c.getBoundingClientRect();"
        " return [r.left + scrollX, r.top + scrollY, r.width, r.height]; })")
    case_dir = os.path.join(args.out, "cases")
    os.makedirs(case_dir, exist_ok=True)
    for res, (x, y, w, h) in zip(results, rects):
      r = pg.send("Page.captureScreenshot", {
          "format": "png", "captureBeyondViewport": True,
          "clip": {"x": x, "y": y, "width": w, "height": h, "scale": 1}}, timeout=60)
      safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in res["name"])
      with open(os.path.join(case_dir, f"{safe}.png"), "wb") as fh:
        fh.write(base64.b64decode(r["data"]))
    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as fh:
      json.dump(results, fh, indent=1)
    page_failures = check_render_page(pg, port, args.out)
  finally:
    browser.close()
    srv.shutdown()
    if not args.keep:
      shutil.rmtree(tmp, ignore_errors=True)

  warnings = []
  failures = check(results, cases, warnings) + page_failures
  print(f"examples from {src}")
  print(f"{len(results) - len(failures)}/{len(results)} cases passed")
  for name, msg in warnings:
    print(f"warn {name}: {msg}")
  for name, probs in failures:
    print(f"FAIL {name}")
    for p in probs:
      print(f"   - {p}")
  print("contact sheets:", *shots, sep="\n  ")
  return 1 if failures else 0


if __name__ == "__main__":
  sys.exit(main())
