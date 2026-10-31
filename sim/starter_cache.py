"""Shipped starter-example cache for teaching-session baselines.

Every new immersive project opens a teaching session whose baseline run renders
starter seeds (`seeds/starter_samples.json`) with the default design system and
an empty constitution. Those outputs are a pure function of the inputs hashed by
`cache_key`, so they are generated once by `tools/build_starter_cache.py` and
shipped under `seeds/starter_examples/`:

  seeds/starter_examples/index.json
  seeds/starter_examples/<seed_id>/<viewport>/{output.json, screen.png, thumb.png}
  seeds/starter_examples/_assets/<content-addressed /api/gen/ files>

For `teach_baseline` runs, `apply()` (called from `RunManager._start`) writes
outputs whose inputs match a cache entry as `done` immediately (copying the
screenshot, thumbnail and generated assets into the data dir); all other outputs
go through the live pipeline unchanged. Set `A2UID_STARTER_CACHE=0` to disable.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import traceback
from typing import Any

from core import imagery as imagery_mod
from core import util
from sim import common

CACHE_DIR = os.path.join(common.PKG, "seeds", "starter_examples")
INDEX_NAME = "index.json"
ASSETS_DIR = "_assets"
ENV_FLAG = "A2UID_STARTER_CACHE"
RENDERER = "web"
PROTOCOL = "v1.0"
CACHE_VERSION = 1
GEN_PREFIX = "/api/gen/"

_index_lock = threading.Lock()
_index_cache: dict[str, Any] = {}


def enabled() -> bool:
  return os.environ.get(ENV_FLAG, "1").strip().lower() not in (
      "0",
      "false",
      "no",
      "off",
  )


def _sha(obj: Any) -> str:
  if isinstance(obj, bytes):
    raw = obj
  elif isinstance(obj, str):
    raw = obj.encode("utf-8")
  else:
    raw = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
  return hashlib.sha256(raw).hexdigest()


def prompt_sha() -> str:
  try:
    with open(os.path.join(common.PROMPTS, "generate_ui.md"), "rb") as fh:
      return _sha(fh.read())
  except OSError:
    return ""


def seed_id_of(sample: dict[str, Any] | None) -> str:
  return str(((sample or {}).get("meta") or {}).get("wizard_seed_id") or "")


def sample_sha(sample: dict[str, Any]) -> str:
  """Hash of the sample fields the generator sees (not ids/owners/timestamps)."""
  meta = sample.get("meta") or {}
  return _sha({
      "group": sample.get("group", sample.get("grp")),
      "genre": sample.get("genre"),
      "title": sample.get("title"),
      "intent": sample.get("intent"),
      "context": sample.get("context"),
      "payload": sample.get("payload"),
      "size_class": sample.get("size_class"),
      "state": sample.get("state"),
      "edge_cases": sample.get("edge_cases"),
      "form": meta.get("form"),
      "connected_app": meta.get("connected_app"),
  })


def key_inputs(
    run_config: dict[str, Any],
    sample: dict[str, Any],
    viewport: str,
    *,
    stage: str = "result",
    constitution_md: str = "",
    imagery_styles: list[dict[str, Any]] | None = None,
    guidelines: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
  """Everything that determines a baseline output (see module docstring)."""
  cfg = run_config or {}
  gen = cfg.get("generator") or {}
  img = cfg.get("imagery") or {}
  cat_cfg = {
      k: v
      for k, v in (cfg.get("catalog") or {}).items()
      # Project catalog ids embed the project slug; custom components are
      # covered by catalog_hash.
      if k not in ("catalog_id", "custom_components", "proposed_components")
  }
  styles = [
      {
          "slug": st.get("slug"),
          "style_text": st.get("style_text"),
          "medium": st.get("medium"),
          "aspect_ratio": st.get("aspect_ratio"),
          "refs": [
              r.get("name") or r.get("file")
              for r in st.get("reference_images") or []
              if isinstance(r, dict)
          ],
      }
      for st in imagery_styles or []
  ]
  return {
      "seed_id": seed_id_of(sample),
      "sample_sha": sample_sha(sample),
      "viewport": viewport,
      "stage": stage or "result",
      "catalog_hash": cfg.get("catalog_hash"),
      "catalog_cfg_sha": _sha(cat_cfg),
      "theme_hash": cfg.get("theme_hash"),
      "templates_hash": cfg.get("templates_hash"),
      "generator": {
          "model": gen.get("model"),
          "framing": gen.get("framing", "card"),
          "temperature": gen.get("temperature"),
          "max_repairs": gen.get("max_repairs"),
          "include_guidelines": bool(gen.get("include_guidelines")),
      },
      "imagery": {
          "enabled": bool(img.get("enabled", True)),
          "image_model": img.get("image_model"),
          "svg_model": img.get("svg_model"),
      },
      "imagery_styles_sha": _sha(styles) if styles else "",
      "guidelines_sha": (
          _sha(guidelines)
          if gen.get("include_guidelines") and guidelines
          else ""
      ),
      "constitution_sha": _sha((constitution_md or "").strip()),
      "prompt_sha": prompt_sha(),
      "renderer": RENDERER,
      "protocol": PROTOCOL,
  }


def key_from_inputs(inputs: dict[str, Any]) -> str:
  return _sha(inputs)


def cache_key(
    run_config: dict[str, Any],
    sample: dict[str, Any],
    viewport: str,
    **kw: Any,
) -> str:
  """sha256 over `key_inputs(...)`; kw as in key_inputs."""
  return key_from_inputs(key_inputs(run_config, sample, viewport, **kw))


def inputs_for_output(
    store: Any, run: dict[str, Any], output: dict[str, Any]
) -> dict[str, Any] | None:
  """Key inputs for an output row (None if its sample isn't a starter seed)."""
  srow = store.get("samples", output.get("sample_id"))
  if not srow or not seed_id_of(srow):
    return None
  cfg = run.get("config") or {}
  if "constitution_override" in cfg:
    constitution = cfg.get("constitution_override") or ""
  else:
    rev = store.get("constitution_revisions", output.get("revision_id")) or {}
    constitution = rev.get("text_md") or ""
  pid = output.get("project_id")
  gls = []
  if (cfg.get("generator") or {}).get("include_guidelines"):
    gls = store.query(
        "SELECT kind, title, body_md FROM guidelines WHERE project_id=? "
        "AND status IN ('open','addressed') ORDER BY rowid",
        [pid],
    )
  return key_inputs(
      cfg,
      srow,
      output.get("viewport") or "phone",
      stage=output.get("stage") or "result",
      constitution_md=constitution,
      imagery_styles=imagery_mod.list_styles(store, pid, enabled_only=True),
      guidelines=gls,
  )


# ----------------------------------------------------------------- index
def cache_dir_default() -> str:
  """CACHE_DIR, overridable via A2UID_STARTER_CACHE_DIR (tests, tools)."""
  return os.environ.get("A2UID_STARTER_CACHE_DIR") or CACHE_DIR


def load_index(cache_dir: str | None = None) -> dict[str, Any]:
  cache_dir = cache_dir or cache_dir_default()
  path = os.path.join(cache_dir, INDEX_NAME)
  try:
    mtime = os.path.getmtime(path)
  except OSError:
    return {"version": CACHE_VERSION, "entries": {}}
  with _index_lock:
    hit = _index_cache.get(path)
    if hit and hit[0] == mtime:
      return hit[1]
  try:
    with open(path, encoding="utf-8") as fh:
      data = json.load(fh)
  except (OSError, ValueError):
    data = {}
  if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
    data = {"version": CACHE_VERSION, "entries": {}}
  with _index_lock:
    _index_cache[path] = (mtime, data)
  return data


def lookup(
    run_config: dict[str, Any],
    sample: dict[str, Any],
    viewport: str,
    *,
    cache_dir: str | None = None,
    **kw: Any,
) -> dict[str, Any] | None:
  """Returns the cache entry (with `dir` and `output`) or None."""
  return lookup_key(cache_key(run_config, sample, viewport, **kw), cache_dir)


def lookup_key(key: str, cache_dir: str | None = None) -> dict[str, Any] | None:
  cache_dir = cache_dir or cache_dir_default()
  ent = (load_index(cache_dir).get("entries") or {}).get(key)
  if not isinstance(ent, dict) or not ent.get("path"):
    return None
  edir = os.path.join(cache_dir, ent["path"])
  try:
    with open(os.path.join(edir, "output.json"), encoding="utf-8") as fh:
      out = json.load(fh)
  except (OSError, ValueError):
    return None
  if not os.path.isfile(os.path.join(edir, "screen.png")):
    return None
  return dict(ent, key=key, dir=edir, cache_dir=cache_dir, output=out)


def materialize(
    entry: dict[str, Any], output_id: str, data_dir: str
) -> dict[str, Any]:
  """Copies the entry's files into data_dir; returns output-row fields."""
  out = entry["output"]
  edir = entry["dir"]
  assets = os.path.join(entry.get("cache_dir") or cache_dir_default(), ASSETS_DIR)
  gen_dir = os.path.join(data_dir, "gen")
  for fname in out.get("gen_assets") or []:
    fname = os.path.basename(str(fname))
    dst = os.path.join(gen_dir, fname)
    if fname and not os.path.isfile(dst):
      src = os.path.join(assets, fname)
      if os.path.isfile(src):
        os.makedirs(gen_dir, exist_ok=True)
        shutil.copyfile(src, dst)
  shot = os.path.join(data_dir, "screens", f"{output_id}.png")
  thumb = os.path.join(data_dir, "thumbs", f"{output_id}.png")
  os.makedirs(os.path.dirname(shot), exist_ok=True)
  os.makedirs(os.path.dirname(thumb), exist_ok=True)
  shutil.copyfile(os.path.join(edir, "screen.png"), shot)
  tsrc = os.path.join(edir, "thumb.png")
  shutil.copyfile(tsrc if os.path.isfile(tsrc) else shot, thumb)
  meta = dict(out.get("render_meta") or {})
  meta["starter_cache"] = entry.get("key", "")[:16]
  now = util.now_iso()
  return {
      "a2ui": out.get("a2ui") or [],
      "validation": out.get("validation") or {},
      "model": out.get("model"),
      "repairs": int(out.get("repairs") or 0),
      "latency_ms": out.get("latency_ms"),
      "prompt_text": out.get("prompt_text"),
      "render_meta": meta,
      "screenshot_path": shot,
      "thumb_path": thumb,
      "renders": {
          RENDERER: {
              "status": "done",
              "screenshot_path": shot,
              "thumb_path": thumb,
              "meta": meta,
              "error": None,
              "updated_at": now,
          }
      },
      "judge": None,
      "judge_score": None,
      "error": None,
      "status": "done",
      "updated_at": now,
  }


def apply(mgr: Any, run_id: str, oids: list[str]) -> list[str]:
  """Materializes cache hits of a teach_baseline run; returns the misses."""
  if not oids or not enabled():
    return oids
  store = mgr.store
  try:
    run = store.get("runs", run_id) or {}
    if run.get("kind") != "teach_baseline":
      return oids
    if list(run.get("renderers") or [RENDERER]) != [RENDERER]:
      return oids
    if not (load_index().get("entries") or {}):
      return oids
  except Exception:  # pylint: disable=broad-except
    traceback.print_exc()
    return oids
  misses = []
  for oid in oids:
    try:
      o = store.get("outputs", oid)
      if not o or o.get("status") not in ("queued", None):
        misses.append(oid)
        continue
      inputs = inputs_for_output(store, run, o)
      entry = lookup_key(key_from_inputs(inputs)) if inputs else None
      if not entry:
        misses.append(oid)
        continue
      fields = materialize(entry, oid, store.file_path())
      store.update("outputs", oid, fields)
    except Exception:  # pylint: disable=broad-except
      traceback.print_exc()
      misses.append(oid)
  return misses


# ---------------------------------------------------------------- export
def gen_assets_of(messages: Any) -> list[str]:
  found: set[str] = set()

  def walk(node: Any) -> None:
    if isinstance(node, str):
      i = node.find(GEN_PREFIX)
      while i >= 0:
        rest = node[i + len(GEN_PREFIX) :]
        name = ""
        for ch in rest:
          if ch.isalnum() or ch in "._-":
            name += ch
          else:
            break
        if name and "." in name:
          found.add(name)
        i = node.find(GEN_PREFIX, i + 1)
    elif isinstance(node, dict):
      for v in node.values():
        walk(v)
    elif isinstance(node, list):
      for v in node:
        walk(v)

  walk(messages)
  return sorted(found)


def export_entry(
    store: Any,
    run: dict[str, Any],
    output: dict[str, Any],
    cache_dir: str,
) -> dict[str, Any]:
  """Writes one finished output into cache_dir; returns its index entry."""
  inputs = inputs_for_output(store, run, output)
  if not inputs:
    raise ValueError(f"output {output.get('id')} is not a starter seed")
  if output.get("status") != "done":
    raise ValueError(f"output {output.get('id')} is {output.get('status')}")
  web = (output.get("renders") or {}).get(RENDERER) or {}
  shot = web.get("screenshot_path") or output.get("screenshot_path")
  if web.get("status") != "done" or not shot or not os.path.isfile(shot):
    raise ValueError(f"output {output.get('id')} has no web screenshot")
  thumb = web.get("thumb_path") or output.get("thumb_path") or shot
  meta = dict(web.get("meta") or output.get("render_meta") or {})
  meta.pop("starter_cache", None)
  rel = os.path.join(inputs["seed_id"], inputs["viewport"])
  edir = os.path.join(cache_dir, rel)
  os.makedirs(edir, exist_ok=True)
  assets = gen_assets_of(output.get("a2ui") or [])
  adir = os.path.join(cache_dir, ASSETS_DIR)
  for fname in assets:
    src = store.file_path("gen", fname)
    if os.path.isfile(src):
      os.makedirs(adir, exist_ok=True)
      shutil.copyfile(src, os.path.join(adir, fname))
  body = {
      "a2ui": output.get("a2ui") or [],
      "validation": output.get("validation") or {},
      "model": output.get("model"),
      "repairs": int(output.get("repairs") or 0),
      "latency_ms": output.get("latency_ms"),
      "prompt_text": output.get("prompt_text"),
      "render_meta": meta,
      "gen_assets": assets,
  }
  with open(os.path.join(edir, "output.json"), "w", encoding="utf-8") as fh:
    json.dump(body, fh, indent=1, ensure_ascii=False, sort_keys=True)
    fh.write("\n")
  shutil.copyfile(shot, os.path.join(edir, "screen.png"))
  shutil.copyfile(
      thumb if os.path.isfile(thumb) else shot, os.path.join(edir, "thumb.png")
  )
  return {
      "seed_id": inputs["seed_id"],
      "viewport": inputs["viewport"],
      "path": rel.replace(os.sep, "/"),
      "key": key_from_inputs(inputs),
      "inputs": inputs,
  }


def write_index(
    cache_dir: str, entries: list[dict[str, Any]], **extra: Any
) -> str:
  idx = {
      "version": CACHE_VERSION,
      "renderer": RENDERER,
      "protocol": PROTOCOL,
      "generated_at": util.now_iso(),
      **extra,
      "entries": {
          e["key"]: {k: v for k, v in e.items() if k != "key"}
          for e in sorted(entries, key=lambda e: (e["seed_id"], e["viewport"]))
      },
  }
  os.makedirs(cache_dir, exist_ok=True)
  path = os.path.join(cache_dir, INDEX_NAME)
  tmp = path + ".tmp"
  with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(idx, fh, indent=1, ensure_ascii=False, sort_keys=True)
    fh.write("\n")
  os.replace(tmp, path)
  with _index_lock:
    _index_cache.pop(path, None)
  return path
