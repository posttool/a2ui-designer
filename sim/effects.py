"""Constitution effect studies: current constitution vs no constitution.

A study creates linked runs over the same samples / viewports / renderers:

- treatment: the revision's constitution (kind `effect_treatment`)
- control:   no constitution (kind `effect_control`); the judge still scores
             against the revision so judge deltas are comparable
- noise:     optional second control (kind `effect_noise`), giving a
             per-sample noise floor: whatever changes between two control
             runs is generator randomness, not the constitution.

Per (sample, viewport) the structural layout diff (core/layout_diff.py) lists
the concrete changes; an LLM pass attributes each change to constitution
rules (or to noise) once all runs finish. Diffs are cheap and recomputed on
read; attributions are cached on the study row.
"""

from __future__ import annotations

import collections
import concurrent.futures
import threading
import traceback
from typing import Any

from core import layout_diff, util
from core.service import ApiError, Service
from llm.gemini import Image, LlmError
from sim import common, generator
from sim.common import arr, obj, str_schema
from sim.runs import ACTIVE, RunManager

KINDS = ("effect_treatment", "effect_control", "effect_noise")

ATTRIBUTION_SCHEMA = obj({
    "summary": str_schema(
        "One sentence: what the constitution changed in this layout."
    ),
    "overall": {
        "type": "string",
        "enum": ["improved", "regressed", "mixed", "neutral"],
    },
    "changes": arr(
        obj({
            "change": str_schema("Change id, e.g. C0."),
            "cause": {"type": "string", "enum": ["rule", "indirect", "noise"]},
            "rule_ids": arr(str_schema("Rule id, e.g. R3.")),
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "explanation": str_schema(),
        })
    ),
    "unapplied_rules": arr(
        obj({
            "rule_id": str_schema(),
            "applies": {
                "type": "string",
                "enum": ["yes", "no"],
                "description": (
                    "Does the rule's situation occur in THIS input? Only list"
                    " rules that apply (yes) but whose effect is missing."
                ),
            },
            "why": str_schema(),
        })
    ),
})


class EffectManager:

  def __init__(self, svc: Service, runs: RunManager, workers: int = 4):
    self.svc = svc
    self.store = svc.store
    self.llm = svc.llm
    self.runs = runs
    self.workers = workers
    self._lock = threading.Lock()
    self._analyzing: set[str] = set()
    self._diff_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    svc.effects = self

  # ------------------------------------------------------------ create
  def create(
      self, user: dict[str, Any], pid: str, body: dict[str, Any]
  ) -> dict[str, Any]:
    p = self.svc._project_row(pid, user, write=True)  # pylint: disable=protected-access
    rid = body.get("revision_id") or p["current_revision_id"]
    rev = self.store.get("constitution_revisions", rid)
    if not rev or rev["project_id"] != pid:
      raise ApiError(400, "Unknown revision")
    if not (rev["text_md"] or "").strip():
      raise ApiError(
          400,
          "This revision's constitution is empty, so there is nothing to "
          "compare against. Write some rules first.",
      )
    options = {
        "noise": bool(body.get("noise", True)),
        "attribute": bool(body.get("attribute", True)),
        "use_screenshots": bool(body.get("use_screenshots", True)),
        "temperature": body.get("temperature"),
    }
    eid = util.new_id("eff")
    now = util.now_iso()
    self.store.insert(
        "effects",
        {
            "id": eid,
            "project_id": pid,
            "revision_id": rid,
            "options": options,
            "status": "running",
            "analysis": {"status": "pending"},
            "created_by": user["ldap"],
            "created_at": now,
            "updated_at": now,
        },
    )
    run_body = {
        "revision_id": rid,
        "sample_set_id": body.get("sample_set_id"),
        "sample_ids": body.get("sample_ids"),
        "viewports": body.get("viewports"),
        "renderers": body.get("renderers"),
        "judge": body.get("judge"),
        "temperature": options["temperature"],
    }
    try:
      control = self.runs.create_run(
          user,
          pid,
          run_body,
          kind="effect_control",
          effect_id=eid,
          constitution_override="",
      )
      treatment = self.runs.create_run(
          user, pid, run_body, kind="effect_treatment", effect_id=eid
      )
      noise = None
      if options["noise"]:
        noise = self.runs.create_run(
            user,
            pid,
            run_body,
            kind="effect_noise",
            effect_id=eid,
            constitution_override="",
        )
    except ApiError:
      self.store.delete("effects", eid)
      raise
    self.store.update(
        "effects",
        eid,
        {
            "treatment_run_id": treatment["id"],
            "control_run_id": control["id"],
            "noise_run_id": noise["id"] if noise else None,
        },
    )
    return self.get(user, eid)

  def create_from_runs(
      self,
      user: dict[str, Any],
      pid: str,
      *,
      control_run_id: str,
      treatment_run_id: str,
      revision_id: str,
      labels: dict[str, str] | None = None,
      attribute: bool = True,
  ) -> dict[str, Any]:
    """Effect study over two existing runs (e.g. teaching Before vs After).

    The control need not be "no constitution": `labels` names both arms in
    the attribution prompt and in the UI.
    """
    self.svc._project_row(pid, user, write=True)  # pylint: disable=protected-access
    eid = util.new_id("eff")
    now = util.now_iso()
    self.store.insert(
        "effects",
        {
            "id": eid,
            "project_id": pid,
            "revision_id": revision_id,
            "treatment_run_id": treatment_run_id,
            "control_run_id": control_run_id,
            "noise_run_id": None,
            "options": {
                "noise": False,
                "attribute": bool(attribute),
                "use_screenshots": True,
                "labels": dict(labels or {}),
                "source": "teach",
            },
            "status": "running",
            "analysis": {"status": "pending"},
            "created_by": user["ldap"],
            "created_at": now,
            "updated_at": now,
        },
    )
    return self.store.get("effects", eid)

  # -------------------------------------------------------------- read
  def _row(self, user: dict[str, Any], eid: str) -> dict[str, Any]:
    e = self.store.get("effects", eid)
    if not e:
      raise ApiError(404, "Study not found")
    self.svc._project_row(e["project_id"], user)  # pylint: disable=protected-access
    return e

  def list(self, user: dict[str, Any], pid: str) -> list[dict[str, Any]]:
    self.svc._project_row(pid, user)  # pylint: disable=protected-access
    out = []
    for e in self.store.query(
        "SELECT * FROM effects WHERE project_id=? ORDER BY created_at DESC, "
        "rowid DESC",
        [pid],
    ):
      out.append(self._fmt_brief(e))
    return out

  def _fmt_brief(self, e: dict[str, Any]) -> dict[str, Any]:
    rev = self.store.get("constitution_revisions", e["revision_id"]) or {}
    runs = {}
    for k in ("treatment", "control", "noise"):
      r = self.store.get("runs", e.get(f"{k}_run_id") or "")
      runs[k] = self.runs.fmt_run(r) if r else None
    status = self._status(e, runs)
    an = e.get("analysis") or {}
    return {
        "id": e["id"],
        "project_id": e["project_id"],
        "revision_id": e["revision_id"],
        "revision_number": rev.get("number"),
        "options": e.get("options") or {},
        "status": status,
        "runs": runs,
        "analysis_status": an.get("status"),
        "headline": an.get("headline"),
        "created_by": e["created_by"],
        "created_at": e["created_at"],
    }

  @staticmethod
  def _status(e: dict[str, Any], runs: dict[str, Any]) -> str:
    # Judged by output counts, not the run flag: a run can be flagged
    # `interrupted` (server restart) after all of its outputs finished.
    rs = [r for r in runs.values() if r]
    active = lambda r: sum(  # noqa: E731
        (r.get("counts") or {}).get(k, 0)
        for k in ("queued", "generating", "rendering", "judging")
    )
    if any(active(r) and r["status"] in ("queued", "running") for r in rs):
      return "running"
    an = (e.get("analysis") or {}).get("status")
    if an == "running":
      return "analyzing"
    if any(active(r) or (r.get("counts") or {}).get("error") for r in rs):
      return "incomplete"
    return "done"

  def _index(self, run_id: str | None) -> dict[tuple[str, str], dict[str, Any]]:
    if not run_id:
      return {}
    return {
        (o["sample_id"], o["viewport"]): o
        for o in self.store.query(
            "SELECT * FROM outputs WHERE run_id=?", [run_id]
        )
    }

  def _diff(self, a: dict[str, Any] | None, b: dict[str, Any] | None):
    if not a or not b or not a.get("a2ui") or not b.get("a2ui"):
      return None
    key = (
        a["id"],
        a.get("updated_at") or "",
        b["id"],
        b.get("updated_at") or "",
    )
    d = self._diff_cache.get(key)
    if d is None:
      try:
        d = layout_diff.diff(a["a2ui"], b["a2ui"])
      except Exception as e:  # pylint: disable=broad-except
        traceback.print_exc()
        d = {
            "error": f"{type(e).__name__}: {e}",
            "changes": [],
            "magnitude": 0,
            "changed": False,
        }
      with self._lock:
        if len(self._diff_cache) > 4000:
          self._diff_cache.clear()
        self._diff_cache[key] = d
    return d

  @staticmethod
  def _boxes(o: dict[str, Any] | None, keys: set[str]) -> dict[str, Any] | None:
    if not o:
      return None
    renders = o.get("renders") or {}
    meta = (
        ((renders.get("web") or {}).get("meta")) or o.get("render_meta") or {}
    )
    boxes = meta.get("boxes")
    if not isinstance(boxes, list):
      return None
    sel: dict[str, list[list[int]]] = {}
    for bx in boxes:
      k = bx.get("k")
      if k in keys:
        sel.setdefault(k, []).append([bx["x"], bx["y"], bx["w"], bx["h"]])
    return {
        "width": meta.get("width"),
        "height": (
            meta.get("captured_height")
            or meta.get("page_height")
            or meta.get("height")
        ),
        "boxes": sel,
    }

  def get(self, user: dict[str, Any], eid: str) -> dict[str, Any]:
    e = self._row(user, eid)
    brief = self._fmt_brief(e)
    rev = self.store.get("constitution_revisions", e["revision_id"]) or {}
    ic = self._index(e.get("control_run_id"))
    it = self._index(e.get("treatment_run_id"))
    inz = self._index(e.get("noise_run_id"))
    analysis = e.get("analysis") or {}
    attributions = analysis.get("pairs") or {}
    rules = analysis.get("rules") or layout_diff.parse_rules(
        rev.get("text_md") or ""
    )
    samples: dict[str, dict[str, Any]] = {}
    pairs = []
    all_keys = sorted(set(ic) | set(it))
    if (e.get("options") or {}).get("source") == "teach" and ic:
      all_keys = [k for k in all_keys if k in ic]
    for key in all_keys:
      sid, vp = key
      if sid not in samples:
        samples[sid] = self.store.get("samples", sid) or {}
      sm = samples[sid]
      c, t, n = ic.get(key), it.get(key), inz.get(key)
      d = self._diff(c, t)
      nd = self._diff(c, n)
      pkey = f"{sid}|{vp}"
      att = attributions.get(pkey) or {}
      att_by_change = {
          a.get("change"): a
          for a in att.get("changes") or []
          if isinstance(a, dict)
      }
      changes = []
      keys_a: set[str] = set()
      keys_b: set[str] = set()
      for ch in (d or {}).get("changes") or []:
        ch = dict(ch)
        ch["in_noise"] = bool(nd) and any(
            layout_diff.same_change(ch, x) for x in nd.get("changes") or []
        )
        ch["attribution"] = att_by_change.get(f"C{ch['index']}")
        keys_a.update(ch["a_focus"])
        keys_b.update(ch["b_focus"])
        changes.append(ch)
      verdict = self._verdict(d, nd, changes)
      pairs.append({
          "key": pkey,
          "sample_id": sid,
          "sample_title": sm.get("title"),
          "genre": sm.get("genre"),
          "viewport": vp,
          "control": self.runs.fmt_output(c, sm) if c else None,
          "treatment": self.runs.fmt_output(t, sm) if t else None,
          "noise": self.runs.fmt_output(n, sm) if n else None,
          "control_boxes": self._boxes(c, keys_a),
          "treatment_boxes": self._boxes(t, keys_b),
          "magnitude": (d or {}).get("magnitude"),
          "noise_magnitude": (nd or {}).get("magnitude"),
          "stats": {
              "control": (d or {}).get("a"),
              "treatment": (d or {}).get("b"),
          },
          "changes": changes,
          "verdict": verdict,
          "attribution": (
              {
                  k: att.get(k)
                  for k in ("summary", "overall", "unapplied_rules", "error")
              }
              if att
              else None
          ),
      })
    summary = self._summary(pairs, rules, brief)
    if (
        brief["status"] in ("done", "incomplete")
        and (e.get("options") or {}).get("attribute", True)
        and analysis.get("status") in (None, "pending")
        and self.llm is not None
    ):
      self.start_analysis(eid)
      brief["status"] = "analyzing"
      brief["analysis_status"] = "running"
    return {
        "effect": brief,
        "revision": {
            "id": rev.get("id"),
            "number": rev.get("number"),
            "text_md": rev.get("text_md") or "",
        },
        "rules": rules,
        "analysis": {
            k: analysis.get(k)
            for k in (
                "status",
                "done",
                "total",
                "error",
                "started_at",
                "finished_at",
            )
        },
        "pairs": pairs,
        "summary": summary,
    }

  @staticmethod
  def _verdict(d, nd, changes) -> str:
    """effect | noise | unchanged | pending, judged per change.

    A change counts as a constitution effect when it does not also occur
    between the two no-constitution runs (when a noise arm exists) and the
    attribution pass (once done) traced it to a rule. Before attribution,
    structural changes outside the noise floor count.
    """
    del nd
    if d is None:
      return "pending"
    if not d.get("changed"):
      return "unchanged"
    for c in changes:
      if c.get("in_noise"):
        continue
      a = c.get("attribution")
      if a is not None:
        if a.get("cause") in ("rule", "indirect"):
          return "effect"
      elif c["kind"] != "emphasis":
        return "effect"
    return "noise"

  def _summary(self, pairs, rules, brief) -> dict[str, Any]:
    total = len(pairs)
    ready = [p for p in pairs if p["verdict"] != "pending"]
    verdicts = collections.Counter(p["verdict"] for p in pairs)
    mags = [p["magnitude"] for p in ready if p["magnitude"] is not None]
    nmags = [
        p["noise_magnitude"] for p in ready if p["noise_magnitude"] is not None
    ]
    kinds = collections.Counter()
    kinds_effect = collections.Counter()
    rule_hits: dict[str, dict[str, Any]] = {
        r["id"]: {
            "changes": 0,
            "pairs": set(),
            "kinds": collections.Counter(),
            "unapplied": 0,
        }
        for r in rules
    }
    by_genre: dict[str, dict[str, Any]] = {}
    overall = collections.Counter()
    for p in pairs:
      g = by_genre.setdefault(
          p["genre"] or "?",
          {
              "genre": p["genre"],
              "total": 0,
              "effect": 0,
              "changed": 0,
              "mag": [],
          },
      )
      g["total"] += 1
      if p["verdict"] == "effect":
        g["effect"] += 1
      if p["verdict"] in ("effect", "noise"):
        g["changed"] += 1
      if p["magnitude"] is not None:
        g["mag"].append(p["magnitude"])
      att = p.get("attribution") or {}
      if att.get("overall"):
        overall[att["overall"]] += 1
      for u in att.get("unapplied_rules") or []:
        rh = rule_hits.get((u or {}).get("rule_id"))
        if rh is not None:
          rh["unapplied"] += 1
      for c in p["changes"]:
        kinds[c["kind"]] += 1
        if p["verdict"] == "effect" and not c.get("in_noise"):
          kinds_effect[c["kind"]] += 1
        a = c.get("attribution") or {}
        if a.get("cause") in ("rule", "indirect"):
          for rid in a.get("rule_ids") or []:
            rh = rule_hits.get(rid)
            if rh is not None:
              rh["changes"] += 1
              rh["pairs"].add(p["key"])
              rh["kinds"][c["kind"]] += 1
    rule_rows = []
    for r in rules:
      rh = rule_hits[r["id"]]
      rule_rows.append({
          **r,
          "changes": rh["changes"],
          "pairs": len(rh["pairs"]),
          "kinds": dict(rh["kinds"].most_common()),
          "unapplied": rh["unapplied"],
      })
    ctrl = (brief["runs"] or {}).get("control") or {}
    treat = (brief["runs"] or {}).get("treatment") or {}
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731
    return {
        "total": total,
        "ready": len(ready),
        "verdicts": dict(verdicts),
        "avg_magnitude": avg(mags),
        "avg_noise_magnitude": avg(nmags),
        "judge": {
            "control": ctrl.get("avg_score"),
            "treatment": treat.get("avg_score"),
        },
        "kinds": dict(kinds.most_common()),
        "kinds_effect": dict(kinds_effect.most_common()),
        "overall": dict(overall),
        "rules": rule_rows,
        "by_genre": sorted(
            (
                {
                    **{k: v for k, v in g.items() if k != "mag"},
                    "avg_magnitude": avg(g["mag"]),
                }
                for g in by_genre.values()
            ),
            key=lambda g: (
                -(g["effect"] / max(1, g["total"])),
                str(g["genre"]),
            ),
        ),
    }

  # ---------------------------------------------------------- analysis
  def analyze(self, user: dict[str, Any], eid: str) -> dict[str, Any]:
    e = self._row(user, eid)
    self.svc._project_row(e["project_id"], user, write=True)  # pylint: disable=protected-access
    brief = self._fmt_brief(e)
    if brief["status"] == "running":
      raise ApiError(409, "Runs are still in progress")
    self.store.update("effects", eid, {"analysis": {"status": "pending"}})
    self.start_analysis(eid)
    return self.get(user, eid)

  def start_analysis(self, eid: str) -> None:
    with self._lock:
      if eid in self._analyzing:
        return
      self._analyzing.add(eid)
    self.store.update(
        "effects",
        eid,
        {
            "analysis": {"status": "running", "started_at": util.now_iso()},
            "updated_at": util.now_iso(),
        },
    )
    self.runs.job_executor.submit(self._safe_analyze, eid)

  def _safe_analyze(self, eid: str) -> None:
    try:
      self._analyze(eid)
    except Exception as e:  # pylint: disable=broad-except
      traceback.print_exc()
      an = (self.store.get("effects", eid) or {}).get("analysis") or {}
      an.update({
          "status": "error",
          "error": f"{type(e).__name__}: {e}"[:500],
          "finished_at": util.now_iso(),
      })
      self.store.update("effects", eid, {"analysis": an})
    finally:
      with self._lock:
        self._analyzing.discard(eid)

  def _analyze(self, eid: str) -> None:
    e = self.store.get("effects", eid)
    rev = self.store.get("constitution_revisions", e["revision_id"]) or {}
    rules = layout_diff.parse_rules(rev.get("text_md") or "")
    project = self.store.get("projects", e["project_id"]) or {}
    judge_model = ((project.get("settings") or {}).get("judge") or {}).get(
        "model"
    )
    use_shots = (e.get("options") or {}).get("use_screenshots", True)
    ic = self._index(e.get("control_run_id"))
    it = self._index(e.get("treatment_run_id"))
    jobs = []
    for key in sorted(set(ic) & set(it)):
      d = self._diff(ic[key], it[key])
      if d and d.get("changed") and d.get("changes"):
        jobs.append((key, ic[key], it[key], d))
    state: dict[str, Any] = {
        "status": "running",
        "started_at": (
            (e.get("analysis") or {}).get("started_at") or util.now_iso()
        ),
        "done": 0,
        "total": len(jobs),
        "rules": rules,
        "pairs": {},
    }
    self.store.update("effects", eid, {"analysis": state})
    lock = threading.Lock()

    def one(job):
      key, c, t, d = job
      sample = self.store.get("samples", key[0]) or {}
      try:
        res = self._attribute(
            rules,
            sample,
            key[1],
            c,
            t,
            d,
            use_shots,
            judge_model,
            labels=(e.get("options") or {}).get("labels") or None,
        )
      except LlmError as err:
        res = {"error": str(err)[:300]}
      with lock:
        state["pairs"][f"{key[0]}|{key[1]}"] = res
        state["done"] += 1
        self.store.update(
            "effects", eid, {"analysis": state, "updated_at": util.now_iso()}
        )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=self.workers, thread_name_prefix="dcb-effect"
    ) as ex:
      list(ex.map(one, jobs))
    # Headline for the list view.
    hits = collections.Counter()
    for res in state["pairs"].values():
      for c in res.get("changes") or []:
        if c.get("cause") in ("rule", "indirect"):
          for rid in c.get("rule_ids") or []:
            hits[rid] += 1
    state.update({
        "status": "done",
        "finished_at": util.now_iso(),
        "headline": {
            "attributed_pairs": len(state["pairs"]),
            "top_rules": [rid for rid, _ in hits.most_common(3)],
        },
    })
    self.store.update(
        "effects", eid, {"analysis": state, "updated_at": util.now_iso()}
    )
    ledger = getattr(self.svc, "teach_ledger", None)
    if ledger is not None:
      try:
        ledger.on_effect_analyzed(self.store.get("effects", eid))
      except Exception:  # pylint: disable=broad-except
        traceback.print_exc()

  def _attribute(
      self, rules, sample, viewport, c, t, d, use_shots, model, labels=None
  ):
    labels = labels or {}
    c_lbl = labels.get("control") or "no constitution"
    t_lbl = labels.get("treatment") or "with constitution"
    tc = layout_diff.Tree(c["a2ui"])
    tt = layout_diff.Tree(t["a2ui"])
    vp = generator.VIEWPORTS.get(viewport) or generator.VIEWPORTS["phone"]
    rule_lines = (
        "\n".join(
            f"- {r['id']}"
            + (f" [{r['section']}]" if r.get("section") else "")
            + f": {r['text']}"
            for r in rules
        )
        or "(no rules)"
    )
    change_lines = "\n".join(
        f"- C{ch['index']} [{ch['kind_label']}] {ch['label']}"
        for ch in d["changes"]
    )
    text = (
        f"# Constitution rules\n\n{rule_lines}\n\n# Input"
        f" ({sample.get('genre')}: {sample.get('title')})\n\nIntent:"
        f" {sample.get('intent') or ''}\n\n```json\n{common.clip_json(common.sample_input(self.svc.fmt_sample(sample)) if sample else {}, 3500)}\n```\n\n#"
        f" Viewport\n\n{vp['label']} {vp['width']}×{vp['height']}\n\n# Control"
        f" layout ({c_lbl})\n\n```\n{tc.outline(110)}\n```\n\n# Treatment"
        f" layout ({t_lbl})\n\n```\n{tt.outline(110)}\n```\n\n# Changes found"
        f" by the structural diff (control → treatment)\n\n{change_lines}\n"
    )
    if labels:
      text = (
          "Note: in this study the control was generated with"
          f" {c_lbl} and the treatment with {t_lbl}; attribute changes to the"
          " numbered rules of the treatment constitution.\n\n"
          + text
      )
    parts: list[Any] = [text]
    if use_shots:
      for label, o in (
          (f"Control ({c_lbl})", c),
          (f"Treatment ({t_lbl})", t),
      ):
        sp = ((o.get("renders") or {}).get("web") or {}).get(
            "screenshot_path"
        ) or o.get("screenshot_path")
        if sp:
          try:
            with open(sp, "rb") as fh:
              png = fh.read()
            parts.append(f"Screenshot — {label}:")
            parts.append(Image(png, "image/png"))
          except OSError:
            pass
    resp = self.llm.generate(
        purpose="attribute",
        system=common.prompt("attribute_effect.md"),
        parts=parts,
        json_schema=ATTRIBUTION_SCHEMA,
        temperature=0.2,
        model=model,
    )
    data = resp.data or {}
    valid_rules = {r["id"] for r in rules}
    valid_changes = {f"C{ch['index']}" for ch in d["changes"]}
    data["changes"] = [
        dict(
            x, rule_ids=[r for r in x.get("rule_ids") or [] if r in valid_rules]
        )
        for x in data.get("changes") or []
        if isinstance(x, dict) and x.get("change") in valid_changes
    ]
    data["unapplied_rules"] = [
        u
        for u in data.get("unapplied_rules") or []
        if isinstance(u, dict) and u.get("rule_id") in valid_rules
    ]
    data["model"] = resp.model
    return data
