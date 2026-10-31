"""Runs, outputs and background jobs.

Each output is processed by a worker thread: generate → render (Web, Android
Compose, Android RemoteCompose, iOS SwiftUI) → judge.
Statuses are persisted after every step so the UI can poll. On startup
unfinished runs become `interrupted` and can be resumed.
"""

from __future__ import annotations

import concurrent.futures
import functools
import hashlib
import hmac
import os
import threading
import time
import traceback
from typing import Any, Callable

from core import dependencies as deps_mod
from core import imagery as imagery_mod
from core import settings as settings_mod, util
from core.a2ui import custom_web
from core.a2ui import lowering
from core.service import ApiError, Service
from llm.gemini import LlmError
from render.android_worker import AndroidRenderWorker
from render.browser_pool import BrowserPool, make_thumb
from render.ios_runner import IosSimulatorRunner
from render.native_images import NativeImageInliner
from sim import generator, imagery_gen, images, judge as judge_mod, synthesizer, taxonomy

ACTIVE = ("queued", "generating", "rendering", "judging")


# Native renderers get web images inlined as data URIs (see
# render/native_images.py). Commons serves 330/500/960px thumbnails.
ANDROID_IMAGE_WIDTH = 500
# iOS batches ride in one CloudSims launch argument (≤120K chars after
# deflate+base64, see render/cloudsims_runner.py), so iOS images are re-encoded
# as small JPEGs and capped per surface.
IOS_IMAGE_WIDTH = 400
IOS_IMAGE_QUALITY = 0.55
IOS_IMAGE_BUDGET_BYTES = 80_000


class RunManager:

  def __init__(
      self,
      svc: Service,
      pool: BrowserPool | None,
      base_url: str,
      secret: bytes,
      workers: int = 8,
      android_worker: AndroidRenderWorker | None = None,
      ios_runner: IosSimulatorRunner | None = None,
  ):
    self.svc = svc
    self.store = svc.store
    self.llm = svc.llm
    self.pool = pool
    self.android_worker = android_worker
    self.ios_runner = ios_runner
    # Native renderers can't reach the web; images are inlined as data URIs.
    self.native_images = NativeImageInliner(self.store.file_path("imgcache"))
    self.base_url = base_url.rstrip("/")
    self.secret = secret
    self.executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="dcb-run"
    )
    self.job_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=3, thread_name_prefix="dcb-job"
    )
    self._cancelled: set[str] = set()
    self._lock = threading.Lock()
    self._ios_cond = threading.Condition()
    self._ios_pending: list[
        tuple[str, dict[str, Any], concurrent.futures.Future]
    ] = []
    self._ios_flushing = False
    svc.runs = self

  # -------------------------------------------------------------- tokens
  def token(self, output_id: str) -> str:
    return hmac.new(
        self.secret, output_id.encode(), hashlib.sha256
    ).hexdigest()[:24]

  def check_token(self, output_id: str, token: str) -> bool:
    return hmac.compare_digest(self.token(output_id), token or "")

  # ------------------------------------------------------------ startup
  def recover(self) -> None:
    self.store.execute(
        "UPDATE runs SET status='interrupted' WHERE status IN ('queued',"
        "'running')"
    )
    self.store.execute(
        "UPDATE jobs SET status='failed', error='server restarted' WHERE "
        "status IN ('queued','running')"
    )

  def renderer_statuses(self) -> list[dict[str, Any]]:
    web_ok = self.pool is not None
    android_st = (
        self.android_worker.status()
        if self.android_worker
        else {"available": False, "warm": False}
    )
    ios_st = (
        self.ios_runner.status()
        if self.ios_runner
        else {"available": False, "running": False}
    )
    out = []
    for r in settings_mod.RENDERERS:
      rid = r["id"]
      item = dict(r)
      if rid == "web":
        item["status"] = {"available": web_ok, "warm": web_ok}
      elif rid in ("android_compose", "android_remote_compose"):
        item["status"] = android_st
      elif rid == "ios_swiftui":
        item["status"] = ios_st
      out.append(item)
    return out

  # ---------------------------------------------------------- formatting
  def _format_renders(
      self, o: dict[str, Any], v: str
  ) -> dict[str, dict[str, Any]]:
    raw = dict(o.get("renders") or {})
    if "web" not in raw and (o.get("screenshot_path") or o.get("render_meta")):
      raw["web"] = {
          "status": "done" if o.get("screenshot_path") else "error",
          "screenshot_path": o.get("screenshot_path"),
          "thumb_path": o.get("thumb_path"),
          "meta": o.get("render_meta") or {},
          "error": None,
          "updated_at": o.get("updated_at"),
      }
    formatted: dict[str, dict[str, Any]] = {}
    for rid, info in raw.items():
      if not isinstance(info, dict):
        continue
      entry = dict(info)
      if isinstance(entry.get("meta"), dict) and "boxes" in entry["meta"]:
        entry["meta"] = {k: v for k, v in entry["meta"].items() if k != "boxes"}
      sp = entry.get("screenshot_path")
      tp = entry.get("thumb_path")
      rv = (entry.get("updated_at") or v or "").replace(":", "")
      entry["screenshot_url"] = (
          f"/files/screens/{os.path.basename(sp)}?v={rv}"
          if sp and os.path.exists(sp)
          else None
      )
      entry["thumb_url"] = (
          f"/files/thumbs/{os.path.basename(tp)}?v={rv}"
          if tp and os.path.exists(tp)
          else entry["screenshot_url"]
      )
      formatted[rid] = entry
    return formatted

  def fmt_output(
      self,
      o: dict[str, Any],
      sample: dict[str, Any] | None = None,
      full: bool = False,
  ) -> dict[str, Any]:
    sample = sample or self.store.get("samples", o["sample_id"]) or {}
    rev = self.store.get("constitution_revisions", o["revision_id"]) or {}
    v = (o.get("updated_at") or "").replace(":", "")
    renders = self._format_renders(o, v)
    primary_shot = (
        f"/files/screens/{os.path.basename(o['screenshot_path'])}?v={v}"
        if o.get("screenshot_path") and os.path.exists(o["screenshot_path"])
        else None
    )
    primary_thumb = (
        f"/files/thumbs/{os.path.basename(o['thumb_path'])}?v={v}"
        if o.get("thumb_path") and os.path.exists(o["thumb_path"])
        else primary_shot
    )
    if not primary_shot:
      for rid in settings_mod.RENDERER_IDS:
        rinfo = renders.get(rid) or {}
        if rinfo.get("screenshot_url"):
          primary_shot = rinfo["screenshot_url"]
          primary_thumb = rinfo.get("thumb_url") or primary_shot
          break

    out = {
        "id": o["id"],
        "run_id": o["run_id"],
        "project_id": o["project_id"],
        "revision_id": o["revision_id"],
        "revision_number": rev.get("number"),
        "sample_id": o["sample_id"],
        "sample_title": sample.get("title"),
        "genre": sample.get("genre"),
        "group": sample.get("grp"),
        "viewport": o["viewport"],
        "stage": o.get("stage") or "result",
        "pair_id": o.get("pair_id"),
        "status": o["status"],
        "validation": o.get("validation") or {"ok": None, "errors": []},
        "screenshot_url": primary_shot,
        "thumb_url": primary_thumb,
        "render_url": self.render_path(o["id"], o["viewport"]),
        "render_meta": {
            k: v
            for k, v in (o.get("render_meta") or {}).items()
            if k != "boxes"
        },
        "renders": renders,
        "judge": o.get("judge"),
        "judge_score": o.get("judge_score"),
        "human_verdict": o.get("human_verdict"),
        "human_note": o.get("human_note") or "",
        "reviewed_by": o.get("reviewed_by"),
        "reviewed_at": o.get("reviewed_at"),
        "model": o.get("model"),
        "latency_ms": o.get("latency_ms"),
        "repairs": o.get("repairs") or 0,
        "error": o.get("error"),
        "created_at": o.get("created_at"),
    }
    if full:
      out["a2ui"] = o.get("a2ui")
      out["prompt_text"] = o.get("prompt_text")
    return out

  def render_path(self, output_id: str, viewport: str) -> str:
    return (
        f"/render/{output_id}?viewport={viewport}&token={self.token(output_id)}"
    )

  def fmt_run(self, r: dict[str, Any]) -> dict[str, Any]:
    rows = self.store.query(
        "SELECT status, judge_score, human_verdict FROM outputs WHERE run_id=?",
        [r["id"]],
    )
    counts = {
        k: 0
        for k in (
            "queued",
            "generating",
            "rendering",
            "judging",
            "done",
            "error",
        )
    }
    verdicts = {"good": 0, "bad": 0, "mixed": 0, "unreviewed": 0}
    scores = []
    for o in rows:
      counts[o["status"]] = counts.get(o["status"], 0) + 1
      verdicts[o["human_verdict"] or "unreviewed"] = (
          verdicts.get(o["human_verdict"] or "unreviewed", 0) + 1
      )
      if o["judge_score"] is not None:
        scores.append(o["judge_score"])
    counts["total"] = len(rows)
    rev = self.store.get("constitution_revisions", r["revision_id"]) or {}
    kind = r.get("kind") or "standard"
    st = (
        self.store.get("sample_sets", r["sample_set_id"])
        if r.get("sample_set_id")
        else None
    )
    raw_cfg = r.get("config") or {}
    renderers = (
        r.get("renderers")
        or raw_cfg.get("renderers")
        or list(settings_mod.DEFAULT_RENDERERS)
    )
    judge_cfg = raw_cfg.get("judge") or {}
    cfg_view = dict(
        raw_cfg,
        sample_ids=r.get("sample_ids") or [],
        renderers=renderers,
        judge=bool(judge_cfg.get("enabled", True))
        if isinstance(judge_cfg, dict)
        else bool(judge_cfg),
        judge_settings=judge_cfg,
    )
    project = self.store.get("projects", r["project_id"])
    cur_tok = (
        self.svc.ensure_current_token_revision(
            r["project_id"], r.get("created_by") or ""
        )
        if project
        else None
    )
    tok_rev_id = raw_cfg.get("token_revision_id")
    tok_rev_num = raw_cfg.get("token_revision_number") or (
        1 if cur_tok else None
    )
    theme_hash = raw_cfg.get("theme_hash") or settings_mod.compute_theme_hash(
        raw_cfg.get("theme")
    )
    rendered_tok_rev_id = (
        raw_cfg.get("rendered_token_revision_id") or tok_rev_id
    )
    rendered_tok_rev_num = (
        raw_cfg.get("rendered_token_revision_number") or tok_rev_num
    )
    rendered_theme_hash = raw_cfg.get("rendered_theme_hash") or theme_hash
    cur_theme_hash = (
        cur_tok["theme_hash"]
        if cur_tok
        else (
            settings_mod.compute_theme_hash(
                (project.get("settings") or {}).get("theme")
            )
            if project
            else theme_hash
        )
    )
    cur_tok_num = cur_tok["number"] if cur_tok else tok_rev_num
    theme_drifted = bool(
        cur_theme_hash
        and rendered_theme_hash
        and cur_theme_hash != rendered_theme_hash
    )
    return {
        "id": r["id"],
        "project_id": r["project_id"],
        "revision_id": r["revision_id"],
        "revision_number": rev.get("number"),
        "token_revision_id": tok_rev_id,
        "token_revision_number": tok_rev_num,
        "initial_token_revision_number": (
            raw_cfg.get("initial_token_revision_number") or tok_rev_num
        ),
        "rendered_token_revision_id": rendered_tok_rev_id,
        "rendered_token_revision_number": rendered_tok_rev_num,
        "theme_hash": theme_hash,
        "rendered_theme_hash": rendered_theme_hash,
        "current_token_revision_number": cur_tok_num,
        "current_theme_hash": cur_theme_hash,
        "theme_drifted": theme_drifted,
        "catalog_hash": raw_cfg.get("catalog_hash"),
        "templates_hash": raw_cfg.get("templates_hash"),
        "kind": kind,
        "effect_id": r.get("effect_id"),
        "no_constitution": "constitution_override" in raw_cfg,
        "label": (
            "No constitution"
            if "constitution_override" in raw_cfg
            else f"rev {rev.get('number')}"
        ),
        "sample_set_id": r.get("sample_set_id"),
        "sample_set_name": (
            st["name"]
            if st
            else (f"{len(r.get('sample_ids') or [])} selected samples")
        ),
        "sample_ids": r.get("sample_ids") or [],
        "viewports": r.get("viewports") or [],
        "renderers": renderers,
        "config": cfg_view,
        "status": r["status"],
        "counts": counts,
        "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
        "verdicts": verdicts,
        "created_by": r["created_by"],
        "created_at": r["created_at"],
        "started_at": r.get("started_at"),
        "finished_at": r.get("finished_at"),
    }

  # ---------------------------------------------------------------- runs
  def create_run(
      self,
      user: dict[str, Any],
      pid: str,
      body: dict[str, Any],
      *,
      kind: str = "standard",
      effect_id: str | None = None,
      constitution_override: str | None = None,
      extra_config: dict[str, Any] | None = None,
  ) -> dict[str, Any]:
    """Creates and starts a run.

    extra_config (internal, used by teaching sessions): merged into the run
    config, e.g. `rule_map` ({"R1": {"t": teaching_id, "v": version}}),
    `judge_constitution` (score against this text) and `probe_teaching_id`.

    constitution_override (internal, used by Constitution effect studies):
    generate with this constitution text instead of the revision's, while the
    judge still scores against the revision so scores stay comparable.
    """
    p = self.svc._project_row(pid, user, write=True)  # pylint: disable=protected-access
    s = settings_mod.merged(p["settings"])
    rid = body.get("revision_id") or p["current_revision_id"]
    rev = self.store.get("constitution_revisions", rid)
    if not rev or rev["project_id"] != pid:
      raise ApiError(400, "Unknown revision")
    set_id = body.get("sample_set_id") or None
    sample_ids = list(body.get("sample_ids") or [])
    if not set_id and not sample_ids:
      ds = s.get("data_source") or {}
      if isinstance(ds, dict):
        if ds.get("kind") == "set" and ds.get("sample_set_id"):
          if self.store.get("sample_sets", ds["sample_set_id"]):
            set_id = ds["sample_set_id"]
          elif ds.get("sample_ids"):
            sample_ids = list(ds.get("sample_ids") or [])
        elif ds.get("kind") == "selection" and ds.get("sample_ids"):
          sample_ids = list(ds.get("sample_ids") or [])
        elif ds.get("kind") == "category" and (
            ds.get("category") or ds.get("subcategory")
        ):
          cat_rows = self.svc.list_samples(
              genre=str(ds.get("subcategory") or ""),
              group=str(ds.get("category") or ""),
          )
          sample_ids = [r["id"] for r in cat_rows]
    if set_id:
      st = self.store.get("sample_sets", set_id)
      if not st:
        raise ApiError(400, "Unknown sample set")
      if not sample_ids:
        sample_ids = list(st.get("sample_ids") or [])
    sample_ids = [
        sid
        for sid in dict.fromkeys(sample_ids)
        if self.store.get("samples", sid)
    ]
    if not sample_ids:
      raise ApiError(400, "Pick a sample set or samples to run")
    vps = [
        v
        for v in body.get("viewports") or s["viewports"]
        if v in generator.VIEWPORTS
    ] or ["phone"]
    req_renderers = (
        body.get("renderers")
        or s.get("renderers")
        or settings_mod.DEFAULT_RENDERERS
    )
    renderers = [
        r
        for r in dict.fromkeys(req_renderers)
        if r in settings_mod.RENDERER_IDS
    ] or list(settings_mod.DEFAULT_RENDERERS)
    judge_on = body.get("judge")
    if judge_on is None:
      judge_on = s["judge"]["enabled"]
    gen_cfg = dict(s["generator"])
    if body.get("temperature") is not None:
      gen_cfg["temperature"] = max(0.0, min(2.0, float(body["temperature"])))
    tok_rev = self.svc.ensure_current_token_revision(pid, user["ldap"])
    ds_snap = deps_mod.build_ds_snapshot(self.store, self.svc, p, tok_rev)
    active_tpls = self.store.query(
        "SELECT id, name, description, when_to_use, structure_md,"
        " components_json FROM templates WHERE project_id=? AND"
        " status='active' ORDER BY name",
        [pid],
    )
    config = {
        "generator": gen_cfg,
        "judge": dict(s["judge"], enabled=bool(judge_on)),
        "imagery": dict(s.get("imagery") or {}),
        "theme": s["theme"],
        "theme_hash": ds_snap["theme_hash"],
        "token_revision_id": ds_snap["token_revision_id"],
        "token_revision_number": ds_snap["token_revision_number"],
        "initial_token_revision_id": ds_snap["token_revision_id"],
        "initial_token_revision_number": ds_snap["token_revision_number"],
        "rendered_theme_hash": ds_snap["theme_hash"],
        "rendered_token_revision_id": ds_snap["token_revision_id"],
        "rendered_token_revision_number": ds_snap["token_revision_number"],
        "catalog": self.svc.catalog_snapshot(p),
        "catalog_hash": ds_snap["catalog_hash"],
        "templates": active_tpls,
        "templates_hash": ds_snap["templates_hash"],
        "renderers": renderers,
    }
    if constitution_override is not None:
      config["constitution_override"] = constitution_override
    if extra_config:
      config.update(extra_config)
    # Connected-app samples get an input-form stage too (effect studies and
    # comparisons only use the result stage).
    include_forms = body.get("include_forms")
    if include_forms is None:
      include_forms = kind == "standard"
    config["include_forms"] = bool(include_forms)
    form_sids = set()
    if include_forms:
      for sid in sample_ids:
        sm = self.store.get("samples", sid) or {}
        if (sm.get("meta") or {}).get("form"):
          form_sids.add(sid)
    run_id = util.new_id("run")
    now = util.now_iso()
    self.store.insert(
        "runs",
        {
            "id": run_id,
            "project_id": pid,
            "revision_id": rid,
            "sample_set_id": set_id,
            "sample_ids": sample_ids,
            "viewports": vps,
            "renderers": renderers,
            "config": config,
            "status": "queued",
            "kind": kind,
            "effect_id": effect_id,
            "created_by": user["ldap"],
            "created_at": now,
            "started_at": None,
            "finished_at": None,
        },
    )
    oids = []
    init_renders = {r: {"status": "queued"} for r in renderers}
    for sid in sample_ids:
      for vp in vps:
        for stage in (("form", "result") if sid in form_sids else ("result",)):
          oid = util.new_id("o")
          oids.append(oid)
          self.store.insert(
              "outputs",
              {
                  "id": oid,
                  "run_id": run_id,
                  "project_id": pid,
                  "revision_id": rid,
                  "sample_id": sid,
                  "viewport": vp,
                  "stage": stage,
                  "status": "queued",
                  "renders": init_renders,
                  "repairs": 0,
                  "created_at": now,
                  "updated_at": now,
              },
          )
    self.svc.touch(pid)
    self._start(run_id, oids)
    return self.fmt_run(self.store.get("runs", run_id))

  def _start(self, run_id: str, oids: list[str]) -> None:
    with self._lock:
      self._cancelled.discard(run_id)
    self.store.update(
        "runs",
        run_id,
        {
            "status": "running",
            "started_at": util.now_iso(),
            "finished_at": None,
        },
    )
    for oid in oids:
      self.executor.submit(self._safe_process, run_id, oid)

  def list_runs(self, user: dict[str, Any], pid: str) -> list[dict[str, Any]]:
    self.svc._project_row(pid, user)  # pylint: disable=protected-access
    return [
        self.fmt_run(r)
        for r in self.store.query(
            "SELECT * FROM runs WHERE project_id=? ORDER BY created_at DESC, "
            "rowid DESC",
            [pid],
        )
    ]

  def _run_row(self, user: dict[str, Any], run_id: str) -> dict[str, Any]:
    r = self.store.get("runs", run_id)
    if not r:
      raise ApiError(404, "Run not found")
    self.svc._project_row(r["project_id"], user)  # pylint: disable=protected-access
    return r

  def get_run(self, user: dict[str, Any], run_id: str) -> dict[str, Any]:
    return self.fmt_run(self._run_row(user, run_id))

  def cancel(self, user: dict[str, Any], run_id: str) -> dict[str, Any]:
    self._run_row(user, run_id)
    with self._lock:
      self._cancelled.add(run_id)
    self.store.execute(
        "UPDATE outputs SET status='error', error='cancelled', "
        "updated_at=? WHERE run_id=? AND status='queued'",
        [util.now_iso(), run_id],
    )
    self.store.update(
        "runs", run_id, {"status": "cancelled", "finished_at": util.now_iso()}
    )
    return self.get_run(user, run_id)

  def delete(self, user: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Deletes a run, its outputs and their screenshot files.

    A run that belongs to a Constitution effect study cannot be analyzed on
    its own, so deleting it deletes the whole study (all of its runs).
    Guidelines keep their own copies of screenshots; their output links are
    cleared.

    Returns:
      {"deleted_run_ids": [...], "deleted_effect_id": str | None}
    """
    r = self._run_row(user, run_id)
    self.svc._project_row(r["project_id"], user, write=True)  # pylint: disable=protected-access
    run_ids = [run_id]
    eid = r.get("effect_id")
    if eid:
      e = self.store.get("effects", eid)
      if e:
        for k in ("control", "treatment", "noise"):
          rid = e.get(f"{k}_run_id")
          if rid and rid not in run_ids:
            run_ids.append(rid)
      run_ids += [
          x["id"]
          for x in self.store.query(
              "SELECT id FROM runs WHERE effect_id=?", [eid]
          )
          if x["id"] not in run_ids
      ]
    with self._lock:
      self._cancelled.update(run_ids)
    for rid in run_ids:
      for o in self.store.query(
          "SELECT id, screenshot_path, thumb_path, renders_json FROM outputs "
          "WHERE run_id=?",
          [rid],
      ):
        self._remove_output_files(o)
        self.store.execute(
            "UPDATE guidelines SET output_id=NULL WHERE output_id=?", [o["id"]]
        )
      self.store.execute("DELETE FROM outputs WHERE run_id=?", [rid])
      self.store.execute("DELETE FROM teaching_trials WHERE run_id=?", [rid])
      self.store.delete("runs", rid)
    if eid:
      self.store.delete("effects", eid)
    self.svc.touch(r["project_id"])
    return {"deleted_run_ids": run_ids, "deleted_effect_id": eid}

  def _remove_output_files(self, o: dict[str, Any]) -> None:
    paths = {o.get("screenshot_path"), o.get("thumb_path")}
    renders = o.get("renders")
    if not isinstance(renders, dict):
      renders = {}
    for entry in renders.values():
      if isinstance(entry, dict):
        paths.add(entry.get("screenshot_path"))
        paths.add(entry.get("thumb_path"))
    roots = (
        os.path.realpath(self.store.file_path("screens")),
        os.path.realpath(self.store.file_path("thumbs")),
    )
    for p in paths:
      if not p or not isinstance(p, str):
        continue
      rp = os.path.realpath(p)
      # Only ever remove files inside the app's own screenshot dirs.
      if os.path.dirname(rp) not in roots:
        continue
      try:
        os.remove(rp)
      except OSError:
        pass

  def resume(self, user: dict[str, Any], run_id: str) -> dict[str, Any]:
    self._run_row(user, run_id)
    rows = self.store.query(
        "SELECT id FROM outputs WHERE run_id=? AND (status != 'done')", [run_id]
    )
    oids = [r["id"] for r in rows]
    self.store.execute(
        "UPDATE outputs SET status='queued', error=NULL, updated_at=? WHERE "
        "run_id=? AND status != 'done'",
        [util.now_iso(), run_id],
    )
    if oids:
      self._start(run_id, oids)
    else:
      self.store.update("runs", run_id, {"status": "done"})
    return self.get_run(user, run_id)

  def _maybe_finish(self, run_id: str) -> None:
    r = self.store.get("runs", run_id)
    if not r or r["status"] in ("cancelled", "interrupted"):
      return
    active = self.store.scalar(
        "SELECT COUNT(*) FROM outputs WHERE run_id=? AND status IN "
        "('queued','generating','rendering','judging')",
        [run_id],
    )
    if not active:
      self.store.update(
          "runs", run_id, {"status": "done", "finished_at": util.now_iso()}
      )

  # ------------------------------------------------------------ pipeline
  def _set(self, oid: str, **fields: Any) -> None:
    fields["updated_at"] = util.now_iso()
    self.store.update("outputs", oid, fields)

  def _update_render_entry(
      self, oid: str, renderer: str, entry: dict[str, Any]
  ) -> dict[str, Any]:
    with self._lock:
      o = self.store.get("outputs", oid) or {}
      renders = dict(o.get("renders") or {})
      entry["updated_at"] = util.now_iso()
      renders[renderer] = entry
      upd: dict[str, Any] = {
          "renders": renders,
          "updated_at": entry["updated_at"],
      }
      if renderer == "web" and entry.get("screenshot_path"):
        upd["screenshot_path"] = entry["screenshot_path"]
        upd["thumb_path"] = entry.get("thumb_path") or entry["screenshot_path"]
        upd["render_meta"] = entry.get("meta") or {}
      elif not o.get("screenshot_path") and entry.get("screenshot_path"):
        upd["screenshot_path"] = entry["screenshot_path"]
        upd["thumb_path"] = entry.get("thumb_path") or entry["screenshot_path"]
      self.store.update("outputs", oid, upd)
      return renders

  def _safe_process(self, run_id: str, oid: str, steps: str = "all") -> None:
    try:
      self.process(run_id, oid, steps)
    except Exception as e:  # pylint: disable=broad-except
      traceback.print_exc()
      self._set(oid, status="error", error=f"{type(e).__name__}: {e}"[:2000])
    finally:
      if run_id:
        self._maybe_finish(run_id)

  def process(self, run_id: str, oid: str, steps: str = "all") -> None:
    """steps: all | judge."""
    if run_id in self._cancelled:
      return
    o = self.store.get("outputs", oid)
    run = self.store.get("runs", o["run_id"])
    cfg = run.get("config") or {}
    project = self.store.get("projects", o["project_id"])
    s = settings_mod.merged(project["settings"])
    gen_cfg = cfg.get("generator") or s["generator"]
    judge_cfg = cfg.get("judge") or s["judge"]
    img_cfg = cfg.get("imagery") or s.get("imagery") or {}
    imagery_styles = imagery_mod.list_styles(
        self.store, o["project_id"], enabled_only=True
    )
    renderers = (
        run.get("renderers")
        or cfg.get("renderers")
        or s.get("renderers")
        or list(settings_mod.DEFAULT_RENDERERS)
    )
    rev = self.store.get("constitution_revisions", o["revision_id"])
    sample = self.store.get("samples", o["sample_id"])
    if not sample:
      self._set(oid, status="error", error="sample was deleted")
      return
    sample = self.svc.fmt_sample(sample)
    cat = self.svc.catalog_for(
        {"settings": dict(s, catalog=cfg.get("catalog") or s["catalog"])}
    )
    vp = generator.VIEWPORTS[o["viewport"]]
    stage = o.get("stage") or "result"
    if stage == "form" and not (sample.get("meta") or {}).get("form"):
      stage = "result"
    if steps == "all":
      self._set(oid, status="generating", error=None)
      gls = []
      if gen_cfg.get("include_guidelines"):
        gls = self.store.query(
            "SELECT kind, title, body_md FROM guidelines WHERE project_id=? "
            "AND status IN ('open','addressed')",
            [o["project_id"]],
        )
      gen_constitution = (
          cfg["constitution_override"]
          if "constitution_override" in cfg
          else rev["text_md"]
      )
      is_control = (
          "constitution_override" in cfg
          and not (cfg.get("constitution_override") or "").strip()
      )
      templates = (
          []
          if is_control
          else (
              cfg["templates"]
              if isinstance(cfg.get("templates"), list)
              else self.store.query(
                  "SELECT * FROM templates WHERE project_id=? AND"
                  " status='active' ORDER BY name",
                  [o["project_id"]],
              )
          )
      )
      ctx = generator.GenerationContext(
          constitution_md=gen_constitution or "",
          catalog=cat,
          viewport=o["viewport"],
          framing=gen_cfg.get("framing", "card"),
          guidelines=gls,
          imagery_styles=[] if is_control else imagery_styles,
          templates=templates,
          model=gen_cfg.get("model"),
          temperature=float(gen_cfg.get("temperature", 0.7)),
          max_repairs=int(gen_cfg.get("max_repairs", 1)),
          stage=stage,
      )
      user_turn = None
      if stage == "form":
        meta = sample.get("meta") or {}
        user_turn = generator.form_user_prompt(
            meta.get("form") or {},
            (meta.get("connected_app") or {}).get("app_name", ""),
            sample.get("intent") or "",
        )
      try:
        res = generator.generate(self.llm, ctx, sample, user=user_turn)
      except LlmError as e:
        self._set(oid, status="error", error=f"generation failed: {e}"[:2000])
        return
      msgs = res.messages
      if img_cfg.get("enabled", True):
        msgs, _ = imagery_gen.materialize_messages(
            msgs,
            store=self.store,
            llm=self.llm,
            project_id=o["project_id"],
            styles=imagery_styles,
            image_model=img_cfg.get("image_model"),
            svg_model=img_cfg.get("svg_model"),
        )
      self._set(
          oid,
          a2ui=msgs,
          validation=res.validation.to_dict(),
          model=res.model,
          latency_ms=res.latency_ms,
          repairs=res.repairs,
          prompt_text=res.prompt_text,
          status="rendering",
          judge=None,
          judge_score=None,
      )
      if run_id in self._cancelled:
        return
      self.render(oid, renderers=renderers)
    if run_id in self._cancelled:
      return
    if judge_cfg.get("enabled", True):
      self._set(oid, status="judging")
      o = self.store.get("outputs", oid)
      png = None
      screenshots: dict[str, bytes] = {}
      if judge_cfg.get("use_screenshot", True):
        for rid, rinfo in (o.get("renders") or {}).items():
          sp = rinfo.get("screenshot_path") if isinstance(rinfo, dict) else None
          if sp and os.path.exists(sp):
            try:
              with open(sp, "rb") as fh:
                screenshots[rid] = fh.read()
            except OSError:
              pass
        if (
            not screenshots
            and o.get("screenshot_path")
            and os.path.exists(o["screenshot_path"])
        ):
          with open(o["screenshot_path"], "rb") as fh:
            png = fh.read()
        elif "web" in screenshots:
          png = screenshots["web"]
        elif screenshots:
          png = next(iter(screenshots.values()))

      try:
        j = judge_mod.judge(
            self.llm,
            constitution_md=(
                cfg["judge_constitution"]
                if isinstance(cfg.get("judge_constitution"), str)
                else (rev["text_md"] or "")
            ),
            templates=(
                cfg.get("templates")
                if isinstance(cfg.get("templates"), list)
                else None
            ),
            sample=sample,
            messages=o.get("a2ui") or [],
            validation=o.get("validation") or {},
            viewport=vp,
            render_meta=o.get("render_meta"),
            screenshot_png=png,
            screenshots=screenshots or None,
            renders=o.get("renders") or None,
            imagery_styles=imagery_styles,
            model=judge_cfg.get("model"),
            stage=stage,
        )
        self._set(oid, judge=j, judge_score=j.get("overall_score"))
      except LlmError as e:
        self._set(oid, judge={"error": str(e)[:500]})
    self._teach_hooks(oid, cfg)
    self._set(oid, status="done")

  def _teach_hooks(self, oid: str, cfg: dict[str, Any]) -> None:
    """Feeds judge findings / template structure into the teachings ledger."""
    ledger = getattr(self.svc, "teach_ledger", None)
    if ledger is None:
      return
    try:
      o = self.store.get("outputs", oid)
      if not o or (o.get("stage") or "result") != "result":
        return
      if (o.get("judge") or {}).get("findings"):
        ledger.on_judged(o)
      ptid = cfg.get("probe_teaching_id")
      if ptid and o.get("a2ui"):
        t = self.store.get("teachings", ptid)
        tpl = (
            self.store.get("templates", t.get("template_id"))
            if t and t.get("kind") == "pattern"
            else None
        )
        skel = ((tpl or {}).get("source") or {}).get("skeleton")
        if skel:
          from core import data_links  # pylint: disable=g-import-not-at-top

          ledger.on_structure(
              o, ptid, data_links.skeleton_match(o["a2ui"], skel)
          )
    except Exception:  # pylint: disable=broad-except
      traceback.print_exc()

  def theme_for_run(
      self,
      run: dict[str, Any] | None,
      project: dict[str, Any] | None,
      *,
      theme_mode: str = "run",
      token_revision_id: str | None = None,
  ) -> dict[str, Any]:
    """Resolves the effective theme for a run output (`run`, `live`, or explicit token revision)."""
    s = settings_mod.merged((project or {}).get("settings"))
    if token_revision_id:
      tr = self.store.get("token_revisions", token_revision_id)
      if tr and (not project or tr.get("project_id") == project.get("id")):
        return settings_mod.resolve_effective_theme(tr.get("theme"))
    if theme_mode == "live":
      return settings_mod.resolve_effective_theme(s["theme"])
    cfg_theme = ((run or {}).get("config") or {}).get("theme")
    raw_theme = cfg_theme if isinstance(cfg_theme, dict) else s["theme"]
    return settings_mod.resolve_effective_theme(raw_theme)

  def render(self, oid: str, renderers: list[str] | None = None) -> None:
    o = self.store.get("outputs", oid)
    if not o:
      return
    run = self.store.get("runs", o["run_id"]) or {}
    project = self.store.get("projects", o["project_id"]) or {}
    s = settings_mod.merged(project.get("settings"))
    theme = self.theme_for_run(run, project, theme_mode="run")
    target_renderers = (
        renderers
        or run.get("renderers")
        or (run.get("config") or {}).get("renderers")
        or s.get("renderers")
        or ["web"]
    )
    for rid in target_renderers:
      if rid == "web":
        self._render_web(oid)
      elif rid in ("android_compose", "android_remote_compose"):
        self._render_android(oid, rid, theme)
      elif rid == "ios_swiftui":
        self._render_ios(oid, theme)

  def _render_web(self, oid: str) -> None:
    o = self.store.get("outputs", oid)
    vp = generator.VIEWPORTS[o["viewport"]]
    if self.pool is None:
      self._set(oid, render_meta={"skipped": "no browser"})
      self._update_render_entry(
          oid,
          "web",
          {"status": "skipped", "meta": {"skipped": "no browser"}},
      )
      return
    self._update_render_entry(oid, "web", {"status": "rendering"})
    shot = self.store.file_path("screens", f"{oid}.png")
    thumb = self.store.file_path("thumbs", f"{oid}.png")
    url = self.base_url + self.render_path(oid, o["viewport"])
    try:
      meta = self.pool.screenshot(url, vp["width"], vp["height"], shot)
      make_thumb(shot, thumb)
      self._update_render_entry(
          oid,
          "web",
          {
              "status": "done",
              "screenshot_path": shot,
              "thumb_path": thumb,
              "meta": meta,
              "error": None,
          },
      )
    except Exception as e:  # pylint: disable=broad-except
      err = f"screenshot failed: {e}"
      self._set(oid, render_meta={"errors": [err]})
      self._update_render_entry(
          oid,
          "web",
          {"status": "error", "meta": {"errors": [err]}, "error": err},
      )

  def _render_android(
      self, oid: str, renderer: str, theme: dict[str, Any]
  ) -> None:
    o = self.store.get("outputs", oid)
    vp = generator.VIEWPORTS[o["viewport"]]
    if self.android_worker is None:
      self._update_render_entry(
          oid,
          renderer,
          {"status": "skipped", "meta": {"skipped": "no android worker"}},
      )
      return
    self._update_render_entry(oid, renderer, {"status": "rendering"})
    shot = self.store.file_path("screens", f"{oid}_{renderer}.png")
    thumb = self.store.file_path("thumbs", f"{oid}_{renderer}.png")
    run = self.store.get("runs", o["run_id"]) or {}
    custom_defs = {
        **self.svc.custom_defs(o["project_id"]),
        **self.custom_components_for_run(run),
    }
    try:
      lowered_a2ui = lowering.expand_composite_components(
          o.get("a2ui") or [], custom_defs, renderer=renderer
      )
      a2ui, img_stats = self.native_images.inline(
          lowered_a2ui, width=ANDROID_IMAGE_WIDTH
      )
      res = self.android_worker.render_job(
          job_id=f"{oid}_{renderer}",
          renderer=renderer,
          a2ui=a2ui,
          width=vp["width"],
          height=vp["height"],
          theme=theme,
          output_path=shot,
          thumb_path=thumb,
      )
      self._update_render_entry(
          oid,
          renderer,
          {
              "status": "done",
              "screenshot_path": shot,
              "thumb_path": thumb,
              "meta": {**(res.get("meta") or {}), "images": img_stats},
              "error": None,
          },
      )
    except Exception as e:  # pylint: disable=broad-except
      err = f"{renderer} failed: {e}"
      self._update_render_entry(
          oid, renderer, {"status": "error", "meta": {}, "error": err[:600]}
      )

  def _render_ios(self, oid: str, theme: dict[str, Any]) -> None:
    o = self.store.get("outputs", oid)
    vp = generator.VIEWPORTS[o["viewport"]]
    if self.ios_runner is None:
      self._update_render_entry(
          oid,
          "ios_swiftui",
          {"status": "skipped", "meta": {"skipped": "no ios runner"}},
      )
      return
    self._update_render_entry(oid, "ios_swiftui", {"status": "rendering"})
    shot = self.store.file_path("screens", f"{oid}_ios_swiftui.png")
    thumb = self.store.file_path("thumbs", f"{oid}_ios_swiftui.png")
    img_stats: dict[str, Any] = {}
    run = self.store.get("runs", o["run_id"]) or {}
    custom_defs = {
        **self.svc.custom_defs(o["project_id"]),
        **self.custom_components_for_run(run),
    }
    a2ui = lowering.expand_composite_components(
        o.get("a2ui") or [], custom_defs, renderer="ios_swiftui"
    )
    try:
      a2ui, img_stats = self.native_images.inline(
          a2ui,
          width=IOS_IMAGE_WIDTH,
          budget_bytes=IOS_IMAGE_BUDGET_BYTES,
          compact_quality=IOS_IMAGE_QUALITY,
      )
    except Exception:  # pylint: disable=broad-except
      traceback.print_exc()
    job = {
        "id": f"{oid}_ios_swiftui",
        "oid": oid,
        "run_id": o["run_id"],
        "a2ui": a2ui,
        "width": vp["width"],
        "height": vp["height"],
        "theme": theme,
        "output_path": shot,
        "thumb_path": thumb,
    }
    try:
      res = self._coalesce_ios_job(o["run_id"], job)
      if res.get("ok"):
        self._update_render_entry(
            oid,
            "ios_swiftui",
            {
                "status": "done",
                "screenshot_path": shot,
                "thumb_path": thumb,
                "meta": {**(res.get("meta") or {}), "images": img_stats},
                "error": None,
            },
        )
      else:
        err = res.get("error") or "iOS Simulator render failed"
        self._update_render_entry(
            oid,
            "ios_swiftui",
            {"status": "error", "meta": {}, "error": err[:600]},
        )
    except Exception as e:  # pylint: disable=broad-except
      err = f"ios_swiftui failed: {e}"
      self._update_render_entry(
          oid,
          "ios_swiftui",
          {"status": "error", "meta": {}, "error": err[:600]},
      )

  def _coalesce_ios_job(
      self, run_id: str, job: dict[str, Any]
  ) -> dict[str, Any]:
    """Coalesces concurrent iOS jobs from the same run into one Simulator batch."""
    fut: concurrent.futures.Future = concurrent.futures.Future()
    should_drive = False
    with self._ios_cond:
      self._ios_pending.append((run_id, job, fut))
      if not self._ios_flushing:
        self._ios_flushing = True
        should_drive = True
      else:
        self._ios_cond.notify_all()

    if should_drive:
      try:
        while True:
          deadline = time.time() + 1.5
          while time.time() < deadline:
            if not run_id or run_id in self._cancelled:
              break
            generating_count = self.store.scalar(
                "SELECT COUNT(*) FROM outputs WHERE run_id=? AND status IN"
                " ('queued','generating')",
                [run_id],
            )
            if not generating_count:
              # Linger briefly so concurrently-dispatched outputs join the same launch.
              with self._ios_cond:
                self._ios_cond.wait(timeout=0.6)
              break
            with self._ios_cond:
              self._ios_cond.wait(timeout=0.1)

          with self._ios_cond:
            if not self._ios_pending:
              self._ios_flushing = False
              break
            batch = list(self._ios_pending)
            self._ios_pending.clear()

          jobs_payload = [b[1] for b in batch]
          try:
            assert self.ios_runner is not None
            results_by_id = self.ios_runner.render_batch(jobs_payload)
            for _, jb, f in batch:
              if not f.done():
                f.set_result(
                    results_by_id.get(jb["id"])
                    or {"ok": False, "error": "missing from batch output"}
                )
          except Exception as e:  # pylint: disable=broad-except
            for _, _, f in batch:
              if not f.done():
                f.set_exception(e)

          with self._ios_cond:
            if not self._ios_pending:
              self._ios_flushing = False
              break
      finally:
        with self._ios_cond:
          self._ios_flushing = False

    return fut.result(timeout=660.0)

  # ------------------------------------------------------------- outputs
  def _output_row(self, user: dict[str, Any], oid: str) -> dict[str, Any]:
    o = self.store.get("outputs", oid)
    if not o:
      raise ApiError(404, "Output not found")
    self.svc._project_row(o["project_id"], user)  # pylint: disable=protected-access
    return o

  def list_outputs(
      self, user: dict[str, Any], run_id: str, q: dict[str, str]
  ) -> list[dict[str, Any]]:
    self._run_row(user, run_id)
    rows = self.store.query("SELECT * FROM outputs WHERE run_id=?", [run_id])
    samples = {
        s["id"]: s
        for s in self.store.query(
            "SELECT id, title, genre, grp FROM samples WHERE id IN (SELECT "
            "sample_id FROM outputs WHERE run_id=?)",
            [run_id],
        )
    }
    by_key = {
        (o["sample_id"], o["viewport"], o.get("stage") or "result"): o["id"]
        for o in rows
    }
    for o in rows:
      other = "result" if (o.get("stage") or "result") == "form" else "form"
      o["pair_id"] = by_key.get((o["sample_id"], o["viewport"], other))
    outs = [self.fmt_output(o, samples.get(o["sample_id"], {})) for o in rows]
    if q.get("stage"):
      outs = [o for o in outs if o["stage"] == q["stage"]]
    if q.get("verdict"):
      want = q["verdict"]
      outs = [o for o in outs if (o["human_verdict"] or "unreviewed") == want]
    if q.get("viewport"):
      outs = [o for o in outs if o["viewport"] == q["viewport"]]
    if q.get("genre"):
      outs = [o for o in outs if o["genre"] == q["genre"]]
    if q.get("group"):
      outs = [o for o in outs if o["group"] == q["group"]]
    if q.get("valid") in ("true", "false"):
      want_ok = q["valid"] == "true"
      outs = [o for o in outs if bool(o["validation"].get("ok")) == want_ok]
    sort = q.get("sort") or "worst"

    def score_key(o):
      sc = o["judge_score"]
      invalid = (
          0
          if o["validation"].get("ok") is False or o["status"] == "error"
          else 1
      )
      return (invalid, sc if sc is not None else 99, o["sample_title"] or "")

    if sort == "worst":
      outs.sort(key=score_key)
    elif sort == "best":
      outs.sort(key=lambda o: -(o["judge_score"] or 0))
    elif sort == "genre":
      outs.sort(
          key=lambda o: (
              o["group"] or "",
              o["genre"] or "",
              o["sample_title"] or "",
              o["viewport"],
          )
      )
    else:
      outs.sort(key=lambda o: o["created_at"] or "", reverse=True)
    return outs

  def get_output(self, user: dict[str, Any], oid: str) -> dict[str, Any]:
    o = self._output_row(user, oid)
    other = "result" if (o.get("stage") or "result") == "form" else "form"
    o["pair_id"] = self.store.scalar(
        "SELECT id FROM outputs WHERE run_id=? AND sample_id=? AND viewport=?"
        " AND COALESCE(stage, 'result')=?",
        [o["run_id"], o["sample_id"], o["viewport"], other],
    )
    sample = self.store.get("samples", o["sample_id"])
    return {
        "output": self.fmt_output(o, sample, full=True),
        "sample": self.svc.fmt_sample(sample) if sample else None,
    }

  def verdict(
      self,
      user: dict[str, Any],
      oid: str,
      verdict: str | None,
      note: str | None,
      *,
      selected_component: dict[str, Any] | None = None,
  ) -> dict[str, Any]:
    self._output_row(user, oid)
    if verdict not in ("good", "bad", "mixed", None, "", "null"):
      raise ApiError(400, "verdict must be good, bad, mixed or null")
    v = verdict if verdict in ("good", "bad", "mixed") else None
    if isinstance(selected_component, dict) and selected_component.get("id"):
      cid = str(selected_component.get("id"))
      cname = str(selected_component.get("component") or "Component")
      tag = f"[{cname} #{cid}]"
      trimmed = (note or "").strip()
      if trimmed and tag not in trimmed and f"#{cid}" not in trimmed:
        note = f"{tag} {trimmed}"
      elif not trimmed and v:
        note = tag
    upd = {
        "human_verdict": v,
        "reviewed_by": user["ldap"] if v else None,
        "reviewed_at": util.now_iso() if v else None,
    }
    if note is not None:
      upd["human_note"] = note
    self.store.update("outputs", oid, upd)
    return self.fmt_output(self.store.get("outputs", oid))

  def rejudge(self, user: dict[str, Any], oid: str) -> dict[str, Any]:
    o = self._output_row(user, oid)
    if not o.get("a2ui"):
      raise ApiError(400, "Nothing to judge yet")
    self._safe_process("", oid, steps="judge")
    return self.fmt_output(self.store.get("outputs", oid))

  def regenerate(self, user: dict[str, Any], oid: str) -> dict[str, Any]:
    o = self._output_row(user, oid)
    self._set(oid, status="queued", error=None)
    self.executor.submit(self._safe_process, o["run_id"], oid)
    r = self.store.get("runs", o["run_id"])
    if r["status"] in ("done", "cancelled", "interrupted"):
      self.store.update("runs", r["id"], {"status": "running"})
    return self.fmt_output(self.store.get("outputs", oid))

  def render_output(
      self,
      user: dict[str, Any],
      oid: str,
      renderer: str | None = None,
      renderers: list[str] | None = None,
      async_mode: bool = False,
  ) -> dict[str, Any]:
    o = self._output_row(user, oid)
    if not o.get("a2ui"):
      raise ApiError(400, "Output has no generated A2UI messages yet")
    targets = list(
        renderers
        or ([renderer] if renderer else settings_mod.DEFAULT_RENDERERS)
    )
    targets = [
        r for r in dict.fromkeys(targets) if r in settings_mod.RENDERER_IDS
    ]
    if not targets:
      raise ApiError(400, "Specify at least one valid renderer")
    for rid in targets:
      self._update_render_entry(oid, rid, {"status": "queued"})
    if async_mode:
      self.executor.submit(self.render, oid, targets)
    else:
      self.render(oid, targets)
    return self.get_output(user, oid)

  def render_run(
      self,
      user: dict[str, Any],
      run_id: str,
      renderer: str | None = None,
      renderers: list[str] | None = None,
  ) -> dict[str, Any]:
    r = self._run_row(user, run_id)
    targets = list(
        renderers
        or ([renderer] if renderer else settings_mod.DEFAULT_RENDERERS)
    )
    targets = [
        t for t in dict.fromkeys(targets) if t in settings_mod.RENDERER_IDS
    ]
    if not targets:
      raise ApiError(400, "Specify at least one valid renderer")
    cur_renderers = list(dict.fromkeys((r.get("renderers") or []) + targets))
    self.store.update("runs", run_id, {"renderers": cur_renderers})
    rows = self.store.query(
        "SELECT id, a2ui_json FROM outputs WHERE run_id=?", [run_id]
    )
    for row in rows:
      if row.get("a2ui"):
        for rid in targets:
          self._update_render_entry(row["id"], rid, {"status": "queued"})
        self.executor.submit(self.render, row["id"], targets)
    return self.get_run(user, run_id)

  def retheme_run(
      self,
      user: dict[str, Any],
      run_id: str,
      *,
      token_revision_id: str | None = None,
      renderers: list[str] | None = None,
  ) -> dict[str, Any]:
    """Re-binds a run to current (or specified) tokens and re-renders screenshots (0 LLM calls)."""
    r = self._run_row(user, run_id)
    pid = r["project_id"]
    self.svc._project_row(pid, user, write=True)  # pylint: disable=protected-access
    if token_revision_id:
      tr = self.store.get("token_revisions", token_revision_id)
      if not tr or tr.get("project_id") != pid:
        raise ApiError(404, "Token revision not found")
    else:
      tr = self.svc.ensure_current_token_revision(pid, user["ldap"])
    cfg = dict(r.get("config") or {})
    if not cfg.get("initial_token_revision_id") and cfg.get(
        "token_revision_id"
    ):
      cfg["initial_token_revision_id"] = cfg["token_revision_id"]
    if not cfg.get("initial_token_revision_number") and cfg.get(
        "token_revision_number"
    ):
      cfg["initial_token_revision_number"] = cfg["token_revision_number"]
    cfg["theme"] = tr["theme"]
    cfg["theme_hash"] = tr["theme_hash"]
    cfg["token_revision_id"] = tr["id"]
    cfg["token_revision_number"] = tr["number"]
    cfg["rendered_theme_hash"] = tr["theme_hash"]
    cfg["rendered_token_revision_id"] = tr["id"]
    cfg["rendered_token_revision_number"] = tr["number"]
    self.store.update("runs", run_id, {"config": cfg})
    target_renderers = (
        renderers
        or r.get("renderers")
        or cfg.get("renderers")
        or list(settings_mod.DEFAULT_RENDERERS)
    )
    rethemed_count = int(
        self.store.scalar(
            "SELECT COUNT(*) FROM outputs WHERE run_id=? AND a2ui_json IS NOT"
            " NULL",
            [run_id],
        )
        or 0
    )
    updated_run = self.render_run(user, run_id, renderers=target_renderers)
    return {
        "run": updated_run,
        "token_revision": tr,
        "rethemed_outputs": rethemed_count,
    }

  def compare(
      self, user: dict[str, Any], pid: str, run_a: str, run_b: str
  ) -> dict[str, Any]:
    ra, rb = self._run_row(user, run_a), self._run_row(user, run_b)
    if ra["project_id"] != pid or rb["project_id"] != pid:
      raise ApiError(400, "Runs must belong to this project")

    def index(run_id):
      out = {}
      for o in self.store.query(
          "SELECT * FROM outputs WHERE run_id=? AND COALESCE(stage, 'result')"
          " = 'result'",
          [run_id],
      ):
        out[(o["sample_id"], o["viewport"])] = o
      return out

    ia, ib = index(run_a), index(run_b)
    keys = sorted(set(ia) | set(ib))
    samples = {}
    pairs = []
    for sid, vp in keys:
      if sid not in samples:
        samples[sid] = self.store.get("samples", sid) or {}
      sm = samples[sid]
      a = ia.get((sid, vp))
      b = ib.get((sid, vp))
      pairs.append({
          "sample_id": sid,
          "sample_title": sm.get("title"),
          "genre": sm.get("genre"),
          "viewport": vp,
          "a": self.fmt_output(a, sm) if a else None,
          "b": self.fmt_output(b, sm) if b else None,
      })
    fa, fb = self.fmt_run(ra), self.fmt_run(rb)
    return {
        "pairs": pairs,
        "run_a": fa,
        "run_b": fb,
        "summary": {"a_avg": fa["avg_score"], "b_avg": fb["avg_score"]},
    }

  # ---------------------------------------------------------------- jobs
  def _job(self, kind: str, user: dict[str, Any], total: int) -> str:
    jid = util.new_id("job")
    now = util.now_iso()
    self.store.insert(
        "jobs",
        {
            "id": jid,
            "kind": kind,
            "status": "queued",
            "progress": {"done": 0, "total": total},
            "message": "",
            "result": None,
            "error": None,
            "created_by": user["ldap"],
            "created_at": now,
            "updated_at": now,
        },
    )
    return jid

  def get_job(self, jid: str) -> dict[str, Any]:
    j = self.store.get("jobs", jid)
    if not j:
      raise ApiError(404, "Job not found")
    return j

  def _with_web_images(self, item: dict[str, Any]) -> dict[str, Any]:
    """Swaps /img placeholders for real photos; records credits in meta."""
    try:
      payload, credits = images.Resolver(self.store).resolve(
          item.get("payload")
      )
    except Exception:  # pylint: disable=broad-except
      traceback.print_exc()
      return item
    if credits:
      item = dict(item)
      item["payload"] = payload
      meta = dict(item.get("meta") or {})
      meta["image_credits"] = (meta.get("image_credits") or []) + credits
      item["meta"] = meta
    return item

  def synthesize(
      self,
      user: dict[str, Any],
      genres: list[str],
      per_genre: int,
      set_name: str | None = None,
      group_sets: bool = False,
      all_set_name: str | None = None,
      brief: str = "",
      count: int = 0,
  ) -> dict[str, Any]:
    """Synthesizes samples into chosen categories, or freely from a brief.

    With `genres`, makes `per_genre` samples for each (built-in or custom),
    steered by the optional `brief`. With no genres but a `brief`, makes
    `count` samples and lets the model file them into existing or new
    categories.
    """
    known = taxonomy.all_genres(self.store)
    genres = [g for g in dict.fromkeys(genres or []) if g in known]
    brief = (brief or "").strip()[:4000]
    if not genres and not brief:
      raise ApiError(
          400, "Pick at least one category or describe what to synthesize"
      )
    per_genre = max(1, min(int(per_genre or 3), 12))
    count = max(1, min(int(count or per_genre or 3), 24))
    # Work units: (label, callable returning sample dicts).
    units: list[tuple[str, Callable[[], list[dict[str, Any]]]]] = []
    for gid in genres:
      units.append((
          gid,
          functools.partial(
              synthesizer.synthesize,
              self.llm,
              known[gid],
              per_genre,
              brief=brief,
          ),
      ))
    if not genres:
      existing = taxonomy.groups_api(self.store)
      left, k = count, 0
      while left > 0:
        n = min(6, left)
        units.append((
            f"brief#{k + 1}",
            functools.partial(
                synthesizer.synthesize_brief,
                self.llm,
                brief,
                n,
                existing,
                seed=k,
            ),
        ))
        left -= n
        k += 1
    jid = self._job("synthesize", user, len(units))

    def work():
      self.store.update(
          "jobs", jid, {"status": "running", "message": "Synthesizing samples…"}
      )
      created: dict[str, list[str]] = {}
      errors = []
      lock = threading.Lock()
      done = [0]

      def one(unit: tuple[str, Callable[[], list[dict[str, Any]]]]) -> None:
        label, fn = unit
        try:
          items = [self._with_web_images(it) for it in fn()]
          with lock:  # category creation must not race
            ids = [
                self.svc.create_sample(user, it, source="synth")["id"]
                for it in items
            ]
        except Exception as e:  # pylint: disable=broad-except
          ids = []
          errors.append(f"{label}: {e}"[:300])
        with lock:
          created[label] = ids
          done[0] += 1
          self.store.update(
              "jobs",
              jid,
              {
                  "progress": {"done": done[0], "total": len(units)},
                  "message": f"{done[0]}/{len(units)} batches",
                  "updated_at": util.now_iso(),
              },
          )

      with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(one, units))
      all_ids = [sid for lbl, _ in units for sid in created.get(lbl, [])]
      sets = []
      if group_sets:
        for grp in taxonomy.GROUPS:
          ids = [
              sid for gid, _, _ in grp["genres"] for sid in created.get(gid, [])
          ]
          if ids:
            sets.append(
                self.svc.create_set(
                    user, grp["label"], ids, f"Starter library: {grp['label']}"
                )
            )
      if all_set_name and all_ids:
        sets.append(
            self.svc.create_set(
                user,
                all_set_name.replace("{n}", str(len(all_ids))),
                all_ids,
                "Starter library: every genre",
            )
        )
      if set_name and all_ids:
        sets.append(self.svc.create_set(user, set_name, all_ids, brief[:300]))
      status = "done" if all_ids else "failed"
      self.store.update(
          "jobs",
          jid,
          {
              "status": status,
              "result": {
                  "sample_ids": all_ids,
                  "set_ids": [s["id"] for s in sets],
                  "errors": errors,
              },
              "error": "; ".join(errors)[:2000] if errors else None,
              "message": (
                  f"Created {len(all_ids)} samples"
                  + (f" ({len(errors)} batch(es) failed)" if errors else "")
              ),
              "updated_at": util.now_iso(),
          },
      )

    self.job_executor.submit(self._wrap_job, jid, work)
    return self.get_job(jid)

  def starter_library(self, user: dict[str, Any]) -> dict[str, Any]:
    return self.synthesize(
        user,
        list(taxonomy.GENRES),
        3,
        group_sets=True,
        all_set_name="All genres ({n})",
    )

  def resolve_images(
      self, user: dict[str, Any], sample_ids: list[str] | None = None
  ) -> dict[str, Any]:
    """Job: swap /img placeholders for web photos in existing samples."""
    if sample_ids:
      rows = [self.store.get("samples", sid) for sid in sample_ids]
      rows = [r for r in rows if r]
    else:
      rows = self.store.query("SELECT * FROM samples")
    rows = [r for r in rows if images.has_placeholders(r.get("payload"))]
    if not user["is_admin"]:
      rows = [r for r in rows if r["owner_ldap"] == user["ldap"]]
    jid = self._job("resolve_images", user, len(rows))

    def work():
      self.store.update(
          "jobs", jid, {"status": "running", "message": "Finding photos…"}
      )
      changed = 0
      for i, r in enumerate(rows):
        item = self._with_web_images(
            {"payload": r.get("payload"), "meta": r.get("meta") or {}}
        )
        if item.get("payload") is not r.get("payload"):
          self.store.update(
              "samples",
              r["id"],
              {"payload": item["payload"], "meta": item["meta"]},
          )
          changed += 1
        self.store.update(
            "jobs",
            jid,
            {
                "progress": {"done": i + 1, "total": len(rows)},
                "message": f"{i + 1}/{len(rows)} samples",
                "updated_at": util.now_iso(),
            },
        )
      self.store.update(
          "jobs",
          jid,
          {
              "status": "done",
              "result": {"changed": changed},
              "message": f"Added web photos to {changed} sample(s)",
              "updated_at": util.now_iso(),
          },
      )

    self.job_executor.submit(self._wrap_job, jid, work)
    return self.get_job(jid)

  def _wrap_job(self, jid: str, fn: Callable[[], None]) -> None:
    try:
      fn()
    except Exception as e:  # pylint: disable=broad-except
      traceback.print_exc()
      self.store.update(
          "jobs",
          jid,
          {"status": "failed", "error": str(e), "updated_at": util.now_iso()},
      )

  # ---------------------------------------------------------- render data
  def render_data(
      self,
      oid: str,
      token: str,
      viewport: str | None,
      *,
      theme_mode: str = "run",
      token_revision_id: str | None = None,
  ) -> dict[str, Any]:
    if not self.check_token(oid, token):
      raise ApiError(403, "Bad render token")
    o = self.store.get("outputs", oid)
    if not o:
      raise ApiError(404, "Output not found")
    run = self.store.get("runs", o["run_id"]) or {}
    project = self.store.get("projects", o["project_id"])
    s = settings_mod.merged(project["settings"] if project else None)
    theme = self.theme_for_run(
        run,
        project,
        theme_mode=theme_mode or "run",
        token_revision_id=token_revision_id,
    )
    framing = (
        (run.get("config") or {}).get("generator") or s["generator"]
    ).get("framing", "card")
    vp = generator.VIEWPORTS.get(
        viewport or o["viewport"], generator.VIEWPORTS["phone"]
    )
    custom_defs = self.custom_components_for_run(run)
    msgs = o.get("a2ui") or []
    if any(
        lowering.should_lower_for_renderer(d, "web")
        for d in custom_defs.values()
    ):
      msgs = lowering.expand_composite_components(
          msgs, custom_defs, renderer="web"
      )
    return {
        "messages": msgs,
        "theme": theme,
        "viewport": vp,
        "framing": framing,
        "validation": o.get("validation"),
        "custom_components": custom_defs,
    }

  @staticmethod
  def custom_components_for_run(run: dict[str, Any] | None) -> dict[str, Any]:
    """Custom component Definitions snapshotted in a run's config."""
    cat = ((run or {}).get("config") or {}).get("catalog") or {}
    return {
        n: d
        for n, d in (cat.get("custom_components") or {}).items()
        if custom_web.is_definition(d)
    }
