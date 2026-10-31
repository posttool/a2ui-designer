#!/usr/bin/env python3
"""Builds the shipped starter-example cache (seeds/starter_examples/).

Every new immersive project starts a teaching session whose baseline renders
starter seeds with the default design system; `sim/starter_cache.py` serves
those outputs from this cache. Re-run this script whenever a cache-key input
changes (generate_ui.md, the default theme/catalog/generator settings, the
starter seeds) — `tests/starter_cache_test.py` fails when the committed cache
is stale.

It starts the server in-process on --port (with its own --data_dir), creates a
scratch project with default settings, runs every seed in
seeds/starter_samples.json × --viewports through the real pipeline (Gemini +
headless Chrome) and exports the finished outputs.

  python3 tools/build_starter_cache.py                # real run (Gemini key + Chrome)
  python3 tools/build_starter_cache.py --dry_run      # scripted LLM, fake browser,
                                                      # writes to a temp dir
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import threading
import time

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG)

# The cache must never feed itself.
os.environ["A2UID_STARTER_CACHE"] = "0"

from server import main as server_main  # noqa: E402  pylint: disable=g-import-not-at-top,wrong-import-position
from server.auth import LOCAL_USER  # noqa: E402  pylint: disable=g-import-not-at-top,wrong-import-position
from server.http import make_server  # noqa: E402  pylint: disable=g-import-not-at-top,wrong-import-position
from sim import starter_cache, wizard as wizard_mod  # noqa: E402  pylint: disable=g-import-not-at-top,wrong-import-position

ACTIVE = ("queued", "generating", "rendering", "judging")


def parse_args(argv=None) -> argparse.Namespace:
  ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  ap.add_argument("--port", type=int, default=8160)
  ap.add_argument("--data_dir", default="/tmp/starter-cache-data")
  ap.add_argument(
      "--out",
      default=None,
      help=(
          "Cache dir to write (default seeds/starter_examples; a temp dir"
          " with --dry_run)."
      ),
  )
  ap.add_argument("--viewports", default="phone")
  ap.add_argument(
      "--seeds", default="", help="Comma-separated seed ids (default: all)."
  )
  ap.add_argument("--timeout", type=float, default=900.0)
  ap.add_argument("--tabs", type=int, default=3)
  ap.add_argument("--workers", type=int, default=4)
  ap.add_argument(
      "--keep_data", action="store_true", help="Don't wipe --data_dir first."
  )
  ap.add_argument(
      "--dry_run",
      action="store_true",
      help="Scripted LLM from tests/fixtures.py (no Gemini).",
  )
  ap.add_argument(
      "--fake_browser",
      action="store_true",
      help="Fake screenshots (default with --dry_run unless --chrome).",
  )
  ap.add_argument(
      "--chrome",
      action="store_true",
      help="With --dry_run: still render with headless Chrome.",
  )
  return ap.parse_args(argv)


def build(args: argparse.Namespace) -> dict:
  out_dir = args.out or (
      tempfile.mkdtemp(prefix="starter-cache-dry-")
      if args.dry_run
      else starter_cache.CACHE_DIR
  )
  if not args.keep_data:
    shutil.rmtree(args.data_dir, ignore_errors=True)
  os.makedirs(args.data_dir, exist_ok=True)

  llm = pool = None
  if args.dry_run:
    sys.path.insert(0, os.path.join(PKG, "tests"))
    import fixtures  # pylint: disable=g-import-not-at-top,import-outside-toplevel

    llm = fixtures.make_llm()
    if not args.chrome:
      pool = fixtures.FakePool()
  if args.fake_browser and pool is None:
    sys.path.insert(0, os.path.join(PKG, "tests"))
    import fixtures  # pylint: disable=g-import-not-at-top,import-outside-toplevel

    pool = fixtures.FakePool()

  sargs = server_main.parse_args([
      "--port",
      str(args.port),
      "--data_dir",
      args.data_dir,
      "--workers",
      str(args.workers),
      "--tabs",
      str(args.tabs),
  ])
  app, pool = server_main.build(sargs, llm=llm, pool=pool)
  srv = make_server(app, "127.0.0.1", args.port)
  port = srv.server_address[1]
  app.runs.base_url = f"http://127.0.0.1:{port}"
  th = threading.Thread(target=srv.serve_forever, daemon=True)
  th.start()
  try:
    return _run(app, args, out_dir)
  finally:
    srv.shutdown()
    srv.server_close()
    app.runs.executor.shutdown(wait=False, cancel_futures=True)
    if pool is not None and hasattr(pool, "close"):
      try:
        pool.close()
      except Exception:  # pylint: disable=broad-except
        pass


def _run(app, args: argparse.Namespace, out_dir: str) -> dict:
  svc, runs, store = app.svc, app.runs, app.svc.store
  user = svc.user(LOCAL_USER) or svc.login(LOCAL_USER, admin=True)
  proj = svc.create_project(user, "Starter cache")
  pid = proj["id"]
  seeds = wizard_mod.ensure_starter_samples(svc, user, None)
  want = {s.strip() for s in args.seeds.split(",") if s.strip()}
  sample_ids = [
      s["id"]
      for s in seeds
      if not want or starter_cache.seed_id_of(s) in want
  ]
  if not sample_ids:
    raise SystemExit("no starter seeds found")
  vps = [v.strip() for v in args.viewports.split(",") if v.strip()]
  print(
      f"building {len(sample_ids)} seeds × {vps} → {out_dir}"
      f" ({'dry run' if args.dry_run else 'live'})",
      flush=True,
  )
  # Same request create_session() makes for a teaching baseline.
  run = runs.create_run(
      user,
      pid,
      {
          "sample_ids": sample_ids,
          "viewports": vps,
          "renderers": ["web"],
          "judge": False,
          "include_forms": False,
      },
      kind="teach_baseline",
  )
  rid = run["id"]
  deadline = time.time() + args.timeout
  last = None
  while time.time() < deadline:
    rows = store.query(
        "SELECT id, status FROM outputs WHERE run_id=?", [rid]
    )
    counts = {}
    for r in rows:
      counts[r["status"]] = counts.get(r["status"], 0) + 1
    if counts != last:
      print("  ", dict(sorted(counts.items())), flush=True)
      last = counts
    if not any(r["status"] in ACTIVE for r in rows):
      break
    time.sleep(1.0)
  else:
    raise SystemExit(f"timed out after {args.timeout}s")

  run_row = store.get("runs", rid)
  entries, failures = [], []
  if os.path.isdir(out_dir) and out_dir == starter_cache.CACHE_DIR:
    for name in os.listdir(out_dir):
      if name != starter_cache.INDEX_NAME:
        shutil.rmtree(os.path.join(out_dir, name), ignore_errors=True)
  for o in store.query(
      "SELECT * FROM outputs WHERE run_id=? ORDER BY rowid", [rid]
  ):
    try:
      # Never ship a payload that failed validation (the scripted dry-run
      # payload may lag the protocol, so it's exported regardless).
      if not args.dry_run and (o.get("validation") or {}).get("ok") is False:
        raise ValueError("payload failed validation")
      entries.append(starter_cache.export_entry(store, run_row, o, out_dir))
    except Exception as e:  # pylint: disable=broad-except
      failures.append((o["id"], o.get("sample_id"), str(e) or o.get("error")))
  if not entries:
    raise SystemExit(f"no outputs exported: {failures}")
  gen = (run_row.get("config") or {}).get("generator") or {}
  path = starter_cache.write_index(
      out_dir, entries, model=gen.get("model"), dry_run=bool(args.dry_run)
  )
  print(f"wrote {len(entries)} entries → {path}", flush=True)
  for f in failures:
    print("  FAILED", *f, flush=True)
  return {"out": out_dir, "entries": entries, "failures": failures}


def main(argv=None) -> int:
  args = parse_args(argv)
  res = build(args)
  return 1 if res["failures"] else 0


if __name__ == "__main__":
  sys.exit(main())
