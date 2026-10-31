"""Teaching sessions: a slower wizard that learns from the designer.

Flow (see docs/TEACH.md):
  create → baseline run (cards) → observations (many per card, anchored to
  components / data paths / relations / regions) → distill (Gemini → candidate
  rules) → confirm / edit / dismiss → applications ("where else does this
  apply?") → probe ("Try it": render that scenario with the learned rules,
  becomes a new card) → templates from examples → finish (assemble the
  constitution from confirmed rules, Dos & Don'ts with crops, tokens, After run
  + effect study Before vs After) → graduate.

Every rule/template is a ledger teaching (sim/teach_ledger.py), so phrasings
and trials are tracked across sessions, chat and the Studio editor.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import traceback
from typing import Any

from core import data_links, diffs, layout_diff
from core import settings as settings_mod
from core import util
from core.service import ApiError, Service
from llm.gemini import Image, LlmError
from sim import common, reviser, synthesizer, taxonomy
from sim import wizard as wizard_mod
from sim.common import arr, obj, str_schema
from sim.teach_ledger import TeachLedger, norm_text, similarity

POLARITIES = ("good", "bad", "note")
ANCHOR_KINDS = ("component", "data", "relation", "region", "whole")
SECTIONS = (
    "Hierarchy & lead-in",
    "Data representation",
    "Component choice",
    "Density & disclosure",
    "Actions",
    "States & edge cases",
)
TEACH_RUN_KINDS = ("teach_baseline", "teach_more", "teach_probe", "teach_after")
ACTIVE = ("queued", "generating", "rendering", "judging")

_TEACHING_ITEM = obj({
    "action": {
        "type": "string",
        "enum": [
            "new",
            "refine",
            "merge",
            "suggest_template",
            "suggest_escalation",
        ],
    },
    "teaching_id": str_schema(
        "Existing teaching id for refine/merge/suggest_*, else ''."
    ),
    "merged_ids": arr(str_schema()),
    "title": str_schema("Short title (≤ 10 words)."),
    "body_md": str_schema(
        "One imperative rule; Don't-rules start with 'Don't'."
    ),
    "section": str_schema(),
    "polarity": {"type": "string", "enum": ["do", "dont"]},
    "applies_when": str_schema(),
    "data_signature": str_schema(),
    "reason": str_schema(),
    "source_observation_ids": arr(str_schema()),
    "confidence": {"type": "number"},
})

_THEME_ITEM = {
    **reviser.THEME_CHANGE_ITEM_SCHEMA,
    "properties": {
        **reviser.THEME_CHANGE_ITEM_SCHEMA["properties"],
        "observation_ids": arr(
            str_schema("Observations this token change comes from.")
        ),
    },
}

DISTILL_SCHEMA = obj({
    "reply_md": str_schema(),
    "teachings": arr(_TEACHING_ITEM),
    "theme_changes": arr(_THEME_ITEM),
    "out_of_scope": arr(
        obj({
            "observation_id": str_schema(),
            "why": str_schema(),
            "suggested_home": {
                "type": "string",
                "enum": ["theme", "catalog", "component", "data"],
            },
        })
    ),
})

APPLICATIONS_SCHEMA = obj({
    "applies": arr(
        obj({
            "sample_id": str_schema(),
            "how_it_applies": str_schema(),
            "relevance": {"type": "string", "enum": ["high", "medium"]},
        })
    ),
    "exceptions": arr(obj({"sample_id": str_schema(), "why": str_schema()})),
    "none_reason": str_schema(),
    "when_to_use": str_schema(),
})

PATTERN_SCHEMA = obj({
    "name": str_schema(),
    "description": str_schema(),
    "structure_md": str_schema(),
    "components": arr(str_schema()),
    "triggers": arr(
        obj({
            "group": {"type": "string", "enum": ["data", "intent", "context"]},
            "text": str_schema(),
            "on": {"type": "boolean"},
        })
    ),
    "when_to_use": str_schema(),
    "data_signature": str_schema(),
})

REPHRASE_SCHEMA = obj({
    "body_md": str_schema("The rule, reworded so the generator follows it."),
    "applies_when": str_schema(),
    "reason": str_schema(
        "Why this wording should work better, citing the misses."
    ),
})

VOICE_TURN_SCHEMA = obj({
    "intent": {
        "type": "string",
        "enum": [
            "observe",
            "answer",
            "capture",
            "undo",
            "navigate",
            "distill",
            "applications",
            "template",
            "chat",
        ],
    },
    "observation": obj({
        "polarity": {"type": "string", "enum": ["good", "bad", "note"]},
        "text": str_schema(),
        "anchors": arr(
            obj({
                "kind": {
                    "type": "string",
                    "enum": ["component", "data", "relation", "whole"],
                },
                "key": str_schema(
                    "Component key id|scope (component/relation)."
                ),
                "path": str_schema("Data path (data/relation)."),
                "why": str_schema(),
            })
        ),
        "about_teaching_id": str_schema(),
        "complete": {"type": "boolean"},
    }),
    "question": str_schema(),
    "quick_replies": arr(str_schema()),
    "say": str_schema(),
    "navigate": obj({
        "to": {"type": "string", "enum": ["next", "prev", "index", ""]},
        "index": {"type": "integer"},
    }),
    "template_from": str_schema(),
})


def _data_outline(payload: Any, depth: int = 0, limit: int = 40) -> list[str]:
  """Compact shape of a payload for prompts: keys, types, list lengths."""
  out: list[str] = []

  def rec(v: Any, path: str, d: int) -> None:
    if len(out) >= limit:
      return
    pad = "  " * d
    if isinstance(v, dict):
      for k, x in list(v.items())[:14]:
        if isinstance(x, (dict, list)):
          n = f"[{len(x)}]" if isinstance(x, list) else ""
          out.append(f"{pad}{k}{n}:")
          rec(x[0] if isinstance(x, list) and x else x, f"{path}/{k}", d + 1)
        else:
          s = layout_diff._clip(str(x), 40)  # pylint: disable=protected-access
          out.append(f"{pad}{k}: {s}")
    elif isinstance(v, list) and v:
      rec(v[0], path, d)

  rec(payload, "", depth)
  return out


class TeachManager:
  """Teaching sessions (see module docstring)."""

  def __init__(self, svc: Service, runs: Any, imports: Any, effects: Any):
    self.svc = svc
    self.store = svc.store
    self.llm = svc.llm
    self.runs = runs
    self.imports = imports
    self.effects = effects
    self.ledger = getattr(svc, "teach_ledger", None) or TeachLedger(svc)
    self._links_cache: dict[tuple[str, str], dict[str, Any]] = {}
    self._distilling: set[str] = set()
    self._lock = threading.RLock()

  # ------------------------------------------------------------- helpers
  def _session(self, user: dict[str, Any], sid: str, write=False):
    s = self.store.get("teach_sessions", sid)
    if not s:
      raise ApiError(404, "No such teaching session")
    self.svc._project_row(s["project_id"], user, write=write)  # pylint: disable=protected-access
    return s

  def _model(self, pid: str) -> str | None:
    p = self.store.get("projects", pid) or {}
    return settings_mod.merged(p.get("settings"))["generator"].get("model")

  def _fmt_session(self, s: dict[str, Any]) -> dict[str, Any]:
    return {
        k: s.get(k)
        for k in (
            "id",
            "project_id",
            "name",
            "status",
            "brief",
            "groups",
            "base_revision_id",
            "revision_id",
            "baseline_run_id",
            "after_run_id",
            "effect_id",
            "summary",
            "last_distilled_at",
            "created_by",
            "created_at",
            "updated_at",
        )
    } | {"groups": s.get("groups") or [], "summary": s.get("summary") or {}}

  def _run_cards(
      self,
      run_id: str,
      sample_ids: list[str],
      origin: str,
      teaching_id: str | None = None,
  ) -> list[dict[str, Any]]:
    rows = self.store.query(
        "SELECT id, sample_id, viewport FROM outputs WHERE run_id=? AND"
        " COALESCE(stage,'result')='result'",
        [run_id],
    )
    order = {sid: i for i, sid in enumerate(sample_ids)}
    rows.sort(key=lambda r: (order.get(r["sample_id"], 999), r["viewport"]))
    return [
        {
            "output_id": r["id"],
            "sample_id": r["sample_id"],
            "run_id": run_id,
            "origin": origin,
            "teaching_id": teaching_id,
        }
        for r in rows
    ]

  def _append_cards(self, sid: str, cards: list[dict[str, Any]]) -> None:
    with self._lock:
      s = self.store.get("teach_sessions", sid)
      cur = list(s.get("cards") or [])
      have = {c["output_id"] for c in cur}
      cur += [c for c in cards if c["output_id"] not in have]
      self.store.update(
          "teach_sessions", sid, {"cards": cur, "updated_at": util.now_iso()}
      )

  def _confirmed_rules(self, pid: str) -> list[dict[str, Any]]:
    return self.store.query(
        "SELECT * FROM teachings WHERE project_id=? AND kind='rule' AND"
        " status='confirmed' ORDER BY order_idx",
        [pid],
    )

  def assembled(self, pid: str) -> dict[str, Any]:
    """Deterministic constitution from confirmed rules, grouped by section."""
    rules = self._confirmed_rules(pid)
    cur = self.svc.current_revision(pid)
    bindings = self.ledger.bind_revision(cur) if cur else {}
    pos: dict[str, int] = {}
    sec_order: list[str] = []
    for rid, b in sorted(bindings.items(), key=lambda kv: int(kv[0][1:] or 0)):
      pos.setdefault(b["teaching_id"], int(rid[1:]))
      sec = b.get("section") or "General"
      if sec not in sec_order:
        sec_order.append(sec)
    for t in rules:
      sec = (t.get("section") or "").strip() or "General"
      if sec not in sec_order:
        sec_order.append(sec)
    by_sec: dict[str, list[dict[str, Any]]] = {s: [] for s in sec_order}
    for t in rules:
      by_sec[(t.get("section") or "").strip() or "General"].append(t)
    lines: list[str] = []
    order: list[dict[str, Any]] = []
    for sec in sec_order:
      ts = sorted(
          by_sec.get(sec) or [],
          key=lambda t: (pos.get(t["id"], 10**6), t.get("order_idx") or 0),
      )
      if not ts:
        continue
      lines.append(f"## {sec}\n")
      for t in ts:
        body = " ".join((t.get("body_md") or "").split())
        lines.append(f"- {body}")
        order.append(t)
      lines.append("")
    md = "\n".join(lines).strip() + ("\n" if lines else "")
    parsed = layout_diff.parse_rules(md)
    rule_map: dict[str, dict[str, Any]] = {}
    if len(parsed) == len(order):
      for r, t in zip(parsed, order):
        rule_map[r["id"]] = {"t": t["id"], "v": int(t.get("version") or 1)}
    else:  # Defensive: fall back to text matching.
      for r in parsed:
        best = max(
            order,
            key=lambda t: similarity(t["body_md"], r["text"]),
            default=None,
        )
        if best:
          rule_map[r["id"]] = {
              "t": best["id"],
              "v": int(best.get("version") or 1),
          }
    return {
        "constitution_md": md,
        "rule_map": rule_map,
        "sections": [s for s in sec_order if by_sec.get(s)],
        "labels": {v["t"]: k for k, v in rule_map.items()},
    }

  def labels(self, pid: str) -> dict[str, str]:
    return self._labels(pid)

  def _labels(self, pid: str, assembled: dict[str, Any] | None = None):
    a = assembled or self.assembled(pid)
    labels = dict(a["labels"])
    for i, p in enumerate(
        self.store.query(
            "SELECT id FROM teachings WHERE project_id=? AND kind='pattern' AND"
            " status NOT IN ('dismissed','merged') ORDER BY order_idx",
            [pid],
        ),
        start=1,
    ):
      labels[p["id"]] = f"T{i}"
    c = 0
    for t in self.store.query(
        "SELECT id FROM teachings WHERE project_id=? AND kind='rule' AND"
        " status='candidate' ORDER BY order_idx",
        [pid],
    ):
      c += 1
      labels.setdefault(t["id"], f"C{c}")
    return labels

  def fmt_teaching(
      self, t: dict[str, Any], labels: dict[str, str]
  ) -> dict[str, Any]:
    tpl = None
    if t.get("template_id"):
      tr = self.store.get("templates", t["template_id"])
      tpl = self.imports.fmt_template(tr) if tr else None
    out = self.ledger.fmt(t, label=labels.get(t["id"], ""), template=tpl)
    return out

  def _sample_brief(self, srow: dict[str, Any] | None) -> dict[str, Any]:
    if not srow:
      return {}
    s = self.svc.fmt_sample(srow)
    return {
        "id": s.get("id"),
        "title": s.get("title"),
        "intent": s.get("intent"),
        "context": s.get("context") or {},
        "payload": s.get("payload"),
        "genre": s.get("genre"),
        "group": s.get("group") or srow.get("grp"),
        "size_class": s.get("size_class"),
        "state": s.get("state"),
    }

  def _crop_url(self, ob: dict[str, Any]) -> str | None:
    f = ob.get("crop_file")
    if f and os.path.exists(self.store.file_path("teach", f)):
      v = (ob.get("updated_at") or "").replace(":", "")
      return f"/files/teach/{f}?v={v}"
    return None

  def fmt_observation(self, ob: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": ob["id"],
        "session_id": ob["session_id"],
        "output_id": ob["output_id"],
        "sample_id": ob.get("sample_id"),
        "polarity": ob.get("polarity") or "note",
        "text": ob.get("text") or "",
        "anchors": ob.get("anchors") or [],
        "crop_url": self._crop_url(ob),
        "about_teaching_id": ob.get("about_teaching_id"),
        "status": ob.get("status") or "open",
        "teaching_ids": ob.get("teaching_ids") or [],
        "source": ob.get("source") or "composer",
        "transcript": ob.get("transcript") or "",
        "created_by": ob.get("created_by"),
        "created_at": ob.get("created_at"),
        "updated_at": ob.get("updated_at"),
    }

  def _with_outcome(
      self, ob: dict[str, Any], s: dict[str, Any], labels: dict[str, str]
  ) -> dict[str, Any]:
    """What a distilled observation became: rules, token suggestions, or out of scope."""
    summ = s.get("summary") or {}
    tokens = [
        {"token": x.get("token"), "value": x.get("value")}
        for x in summ.get("theme_suggestions") or []
        if ob["id"] in (x.get("observation_ids") or [])
    ]
    oos = next(
        (
            x
            for x in summ.get("out_of_scope") or []
            if x.get("observation_id") == ob["id"]
        ),
        None,
    )
    ob["outcome"] = {
        "teachings": [
            {"id": t, "label": labels.get(t, "")}
            for t in ob.get("teaching_ids") or []
        ],
        "tokens": tokens,
        "out_of_scope": oos,
    }
    return ob

  # --------------------------------------------------------------- links
  def links_for_output(self, o: dict[str, Any]) -> dict[str, Any]:
    key = (o["id"], o.get("updated_at") or "")
    hit = self._links_cache.get(key)
    if hit is not None:
      return hit
    srow = self.store.get("samples", o["sample_id"]) or {}
    sample = self.svc.fmt_sample(srow) if srow else {}
    renders = o.get("renders") or {}
    meta = (
        ((renders.get("web") or {}).get("meta")) or o.get("render_meta") or {}
    )
    res = data_links.compute_links(
        o.get("a2ui") or [], sample, meta.get("boxes"), meta.get("height")
    )
    with self._lock:
      if len(self._links_cache) > 500:
        self._links_cache.clear()
      self._links_cache[key] = res
    return res

  def output_links(self, user: dict[str, Any], oid: str) -> dict[str, Any]:
    o = self.runs._output_row(user, oid)  # pylint: disable=protected-access
    return self.links_for_output(o)

  # --------------------------------------------------------------- state
  def list_sessions(
      self, user: dict[str, Any], pid: str
  ) -> list[dict[str, Any]]:
    self.svc._project_row(pid, user)  # pylint: disable=protected-access
    out = []
    for s in self.store.query(
        "SELECT * FROM teach_sessions WHERE project_id=? ORDER BY created_at"
        " DESC, rowid DESC",
        [pid],
    ):
      d = self._fmt_session(s)
      d["cards"] = len(s.get("cards") or [])
      d["observations"] = self.store.scalar(
          "SELECT COUNT(*) FROM observations WHERE session_id=?", [s["id"]]
      )
      d["confirmed"] = self.store.scalar(
          "SELECT COUNT(*) FROM teachings WHERE session_id=? AND status IN"
          " ('confirmed','escalated')",
          [s["id"]],
      )
      out.append(d)
    return out

  def state(self, user: dict[str, Any], sid: str) -> dict[str, Any]:
    s = self._session(user, sid)
    pid = s["project_id"]
    p = self.svc._project_row(pid, user)  # pylint: disable=protected-access
    cards_out = []
    run_ids: list[str] = []
    obs = self.store.query(
        "SELECT * FROM observations WHERE session_id=? ORDER BY order_idx,"
        " created_at",
        [sid],
    )
    counts: dict[str, int] = {}
    for ob in obs:
      counts[ob["output_id"]] = counts.get(ob["output_id"], 0) + 1
    busy = False
    for i, c in enumerate(s.get("cards") or []):
      o = self.store.get("outputs", c["output_id"])
      if not o:
        continue
      srow = self.store.get("samples", o["sample_id"])
      if c.get("run_id") and c["run_id"] not in run_ids:
        run_ids.append(c["run_id"])
      if o.get("status") in ACTIVE:
        busy = True
      cards_out.append({
          **c,
          "index": i,
          "output": self.runs.fmt_output(o, srow, full=True),
          "sample": self._sample_brief(srow),
          "observation_count": counts.get(o["id"], 0),
      })
    if s.get("after_run_id") and s["after_run_id"] not in run_ids:
      run_ids.append(s["after_run_id"])
    runs = {}
    for rid in run_ids:
      r = self.store.get("runs", rid)
      if r:
        fr = self.runs.fmt_run(r)
        runs[rid] = {
            "id": rid,
            "kind": r.get("kind"),
            "status": fr.get("status"),
            "counts": fr.get("counts") or {},
        }
        cnt = fr.get("counts") or {}
        if fr.get("status") in ("queued", "running") and any(
            cnt.get(k) for k in ACTIVE
        ):
          busy = True
    effect = None
    if s.get("effect_id") and self.store.get("effects", s["effect_id"]):
      try:
        e = self.effects.get(user, s["effect_id"])
        effect = {
            "id": s["effect_id"],
            "status": e["effect"]["status"],
            "analysis_status": (e.get("analysis") or {}).get("status"),
            "rules": e.get("rules") or [],
            "pairs": {
                p_["key"]: {
                    "sample_id": p_["sample_id"],
                    "sample_title": p_.get("sample_title"),
                    "verdict": p_.get("verdict"),
                    "control": p_.get("control"),
                    "treatment": p_.get("treatment"),
                    "changes": [
                        {
                            "index": ch.get("index"),
                            "kind": ch.get("kind"),
                            "label": ch.get("label"),
                            "attribution": ch.get("attribution"),
                        }
                        for ch in p_.get("changes") or []
                    ],
                    **(p_.get("attribution") or {}),
                }
                for p_ in e.get("pairs") or []
            },
        }
        if effect["analysis_status"] in (None, "pending", "running") or effect[
            "status"
        ] in ("running", "analyzing"):
          busy = True
        elif s.get("status") == "finishing":
          self.store.update(
              "teach_sessions",
              sid,
              {"status": "done", "updated_at": util.now_iso()},
          )
          s = self.store.get("teach_sessions", sid)
      except ApiError:
        effect = None
    a = self.assembled(pid)
    labels = self._labels(pid, a)
    trows = self.store.query(
        "SELECT * FROM teachings WHERE project_id=? AND ((kind='rule' AND"
        " status IN ('confirmed','escalated')) OR (kind='rule' AND"
        " status='candidate' AND session_id=?) OR (kind='pattern' AND status"
        " NOT IN ('dismissed','merged')))"
        " ORDER BY order_idx",
        [pid, sid],
    )
    teachings = [self.fmt_teaching(t, labels) for t in trows]

    def tkey(t):
      lab = t["label"] or "Z9999"
      return (
          {"R": 0, "C": 1, "T": 2}.get(lab[:1], 3),
          int(lab[1:]) if lab[1:].isdigit() else 9999,
      )

    teachings.sort(key=tkey)
    with self._lock:
      distilling = sid in self._distilling
    if distilling:
      busy = True
    return {
        "session": self._fmt_session(s),
        "project": self.svc.format_project(p),
        "revision": self.svc.current_revision(pid),
        "cards": cards_out,
        "observations": [
            self._with_outcome(self.fmt_observation(ob), s, labels)
            for ob in obs
        ],
        "teachings": teachings,
        "runs": runs,
        "effect": effect,
        "busy": busy,
        "distilling": distilling,
    }

  # ------------------------------------------------------------- sessions
  def create_session(
      self, user: dict[str, Any], pid: str, body: dict[str, Any]
  ) -> dict[str, Any]:
    p = self.svc._project_row(pid, user, write=True)  # pylint: disable=protected-access
    body = body or {}
    groups = [str(g) for g in body.get("groups") or [] if isinstance(g, str)]
    brief = str(
        body.get("brief")
        if body.get("brief") is not None
        else (p.get("description") or "")
    ).strip()
    count = max(3, min(int(body.get("count") or 6), 8))
    sample_ids = [
        x
        for x in body.get("sample_ids") or []
        if isinstance(x, str) and self.store.get("samples", x)
    ]
    if not sample_ids:
      sample_ids = wizard_mod.pick_wizard_samples(
          self.svc,
          self.runs,
          user,
          groups=groups,
          brief=brief,
          count=min(count, 6),
      )
      if count > len(sample_ids):
        extra = self.store.query(
            "SELECT id, grp FROM samples ORDER BY created_at DESC, rowid DESC"
        )
        for r in extra:
          if len(sample_ids) >= count:
            break
          if r["id"] in sample_ids:
            continue
          if groups and r.get("grp") not in groups:
            continue
          sample_ids.append(r["id"])
    sample_ids = sample_ids[:8]
    if not sample_ids:
      raise ApiError(400, "Could not find scenarios to teach with")
    if (
        "name" in body
        and str(body.get("name") or "").strip()
        and not (p.get("name") or "").strip()
    ):
      self.svc.update_project(
          user, pid, {"name": str(body["name"]).strip()[:120]}
      )
    cur = self.svc.current_revision(pid)
    if cur and (cur.get("text_md") or "").strip():
      self.ledger.bind_revision(cur, created_by=user["ldap"])
    run = self.runs.create_run(
        user,
        pid,
        {
            "sample_ids": sample_ids,
            "viewports": ["phone"],
            "renderers": ["web"],
            "judge": False,
            "include_forms": False,
        },
        kind="teach_baseline",
    )
    n = (
        self.store.scalar(
            "SELECT COUNT(*) FROM teach_sessions WHERE project_id=?", [pid]
        )
        or 0
    ) + 1
    sid = util.new_id("ts")
    now = util.now_iso()
    name = str(body.get("session_name") or "").strip() or f"Session {n}"
    self.store.insert(
        "teach_sessions",
        {
            "id": sid,
            "project_id": pid,
            "name": name[:120],
            "status": "teaching",
            "brief": brief[:2000],
            "groups": groups,
            "cards": self._run_cards(run["id"], sample_ids, "baseline"),
            "baseline_run_id": run["id"],
            "after_run_id": None,
            "effect_id": None,
            "base_revision_id": cur["id"] if cur else None,
            "revision_id": None,
            "summary": {"theme_suggestions": []},
            "last_distilled_at": None,
            "created_by": user["ldap"],
            "created_at": now,
            "updated_at": now,
        },
    )
    return self.state(user, sid)

  def delete_session(self, user: dict[str, Any], sid: str) -> None:
    s = self._session(user, sid, write=True)
    for ob in self.store.query(
        "SELECT * FROM observations WHERE session_id=?", [sid]
    ):
      if ob.get("crop_file"):
        try:
          os.remove(self.store.file_path("teach", ob["crop_file"]))
        except OSError:
          pass
    self.store.execute("DELETE FROM observations WHERE session_id=?", [sid])
    self.store.execute(
        "UPDATE teachings SET status='dismissed' WHERE session_id=? AND"
        " status='candidate'",
        [sid],
    )
    self.store.delete("teach_sessions", s["id"])

  def _teach_run(
      self,
      user: dict[str, Any],
      pid: str,
      sample_ids: list[str],
      kind: str,
      extra: dict[str, Any] | None = None,
  ) -> dict[str, Any]:
    a = self.assembled(pid)
    cfg = {
        "rule_map": a["rule_map"],
        "judge_constitution": a["constitution_md"],
        **(extra or {}),
    }
    return self.runs.create_run(
        user,
        pid,
        {
            "sample_ids": sample_ids,
            "viewports": ["phone"],
            "renderers": ["web"],
            "judge": True,
            "include_forms": False,
        },
        kind=kind,
        constitution_override=a["constitution_md"],
        extra_config=cfg,
    )

  def add_cards(
      self, user: dict[str, Any], sid: str, body: dict[str, Any]
  ) -> dict[str, Any]:
    s = self._session(user, sid, write=True)
    pid = s["project_id"]
    ids = [
        x
        for x in (body or {}).get("sample_ids") or []
        if isinstance(x, str) and self.store.get("samples", x)
    ]
    brief = str((body or {}).get("brief") or "").strip()
    if not ids and brief:
      count = max(1, min(int((body or {}).get("count") or 3), 6))
      items = synthesizer.synthesize_brief(
          self.llm, brief, count, taxonomy.groups_api(self.store)
      )
      for it in items[:count]:
        if os.environ.get("A2UID_WEB_IMAGES") != "0":
          it = self.runs._with_web_images(it)  # pylint: disable=protected-access
        ids.append(self.svc.create_sample(user, it, source="synth")["id"])
    if not ids:
      raise ApiError(400, "Pick scenarios or describe the ones to synthesize")
    run = self._teach_run(user, pid, ids[:6], "teach_more")
    self._append_cards(sid, self._run_cards(run["id"], ids, "added"))
    return self.state(user, sid)

  # --------------------------------------------------------- observations
  def _card_output(self, s: dict[str, Any], oid: str) -> dict[str, Any]:
    if oid not in {c["output_id"] for c in s.get("cards") or []}:
      raise ApiError(400, "That output is not a card in this session")
    o = self.store.get("outputs", oid)
    if not o:
      raise ApiError(404, "Output not found")
    return o

  def _clean_anchors(
      self, anchors: Any, links: dict[str, Any], strict: bool
  ) -> list[dict[str, Any]]:
    comps = {c["key"]: c for c in links.get("components") or []}
    by_id: dict[str, list[dict[str, Any]]] = {}
    for c in comps.values():
      by_id.setdefault(c["id"], []).append(c)
    leaves = {l["path"]: l for l in links.get("leaves") or []}

    def comp_for(key: str) -> dict[str, Any] | None:
      if key in comps:
        return comps[key]
      base = key.split("|", 1)[0]
      cs = by_id.get(base) or []
      return cs[0] if cs else None

    def path_ok(path: str) -> bool:
      if path in leaves:
        return True
      if "*" in path:
        import re  # pylint: disable=g-import-not-at-top

        rx = re.compile("^" + re.escape(path).replace(r"\*", r"[^/]+") + "$")
        return any(rx.match(p) for p in leaves)
      return False

    out: list[dict[str, Any]] = []
    for a in anchors if isinstance(anchors, list) else []:
      if not isinstance(a, dict):
        continue
      kind = a.get("kind")
      bad = None
      item: dict[str, Any] = {"kind": kind}
      if kind == "component":
        key = str(a.get("key") or a.get("id") or "")
        if a.get("id") and "|" not in key and a.get("scope") is not None:
          key = f"{a.get('id')}|{a.get('scope') or ''}"
        c = comp_for(key)
        if not c:
          bad = f"unknown component {key!r}"
        else:
          item.update({
              "key": c["key"],
              "id": c["id"],
              "scope": c["scope"],
              "component": c["type"],
              "label": c["label"],
          })
      elif kind == "data":
        path = str(a.get("path") or "")
        if not path_ok(path):
          bad = f"unknown data path {path!r}"
        else:
          l = leaves.get(path) or {}
          item.update({
              "path": path,
              "value_preview": l.get("value_preview", ""),
              "represented": l.get("represented", ""),
          })
      elif kind == "relation":
        path = str(a.get("path") or "")
        key = str(a.get("key") or a.get("component_id") or "")
        c = comp_for(key)
        if not path_ok(path) or not c:
          bad = f"unknown relation {path!r} ⇄ {key!r}"
        else:
          item.update({
              "path": path,
              "key": c["key"],
              "id": c["id"],
              "component": c["type"],
              "label": c["label"],
          })
      elif kind == "region":
        box = a.get("box")
        try:
          x, y, w, h = (float(v) for v in box)
        except (TypeError, ValueError):
          bad = "region needs box [x, y, w, h]"
        else:
          hmax = float(links.get("height") or 4000)
          x, y = max(0.0, min(x, 2000.0)), max(0.0, min(y, hmax))
          w, h = max(4.0, min(w, 2000.0)), max(4.0, min(h, hmax))
          item["box"] = [round(x), round(y), round(w), round(h)]
      elif kind == "whole":
        pass
      else:
        bad = f"unknown anchor kind {kind!r}"
      if bad:
        if strict:
          raise ApiError(400, f"Bad anchor: {bad}")
        continue
      if a.get("why"):
        item["why"] = str(a["why"])[:120]
      out.append(item)
    return out[:12]

  def _save_crop(self, obid: str, data_url: Any) -> str | None:
    if not (isinstance(data_url, str) and data_url.startswith("data:image/")):
      return None
    head, _, b64 = data_url.partition(",")
    try:
      raw = base64.b64decode(b64)
    except ValueError as e:
      raise ApiError(400, "Invalid crop image") from e
    if len(raw) > 6 * 1024 * 1024:
      raise ApiError(400, "Crop too large")
    ext = "jpg" if "jpeg" in head else "png"
    fname = f"{obid}.{ext}"
    with open(self.store.file_path("teach", fname), "wb") as fh:
      fh.write(raw)
    return fname

  def add_observation(
      self, user: dict[str, Any], sid: str, body: dict[str, Any], strict=True
  ) -> dict[str, Any]:
    s = self._session(user, sid, write=True)
    body = body or {}
    o = self._card_output(s, str(body.get("output_id") or ""))
    pol = body.get("polarity")
    if pol not in POLARITIES:
      raise ApiError(400, "polarity must be good, bad or note")
    text = str(body.get("text") or "").strip()
    links = self.links_for_output(o)
    anchors = self._clean_anchors(body.get("anchors"), links, strict)
    if not text and not anchors:
      raise ApiError(400, "Say what you noticed (text or anchors)")
    about = body.get("about_teaching_id") or None
    if about:
      t = self.store.get("teachings", about)
      if not t or t["project_id"] != s["project_id"]:
        about = None
    obid = util.new_id("ob")
    now = util.now_iso()
    order = (
        self.store.scalar(
            "SELECT MAX(order_idx) FROM observations WHERE session_id=?", [sid]
        )
        or 0
    ) + 1
    ob = self.store.insert(
        "observations",
        {
            "id": obid,
            "session_id": sid,
            "project_id": s["project_id"],
            "output_id": o["id"],
            "sample_id": o["sample_id"],
            "polarity": pol,
            "text": text[:4000],
            "anchors": anchors,
            "crop_file": self._save_crop(obid, body.get("crop_data_url")),
            "about_teaching_id": about,
            "status": "open",
            "teaching_ids": [],
            "source": (
                body.get("source")
                if body.get("source") in ("composer", "voice", "typed")
                else "composer"
            ),
            "transcript": str(body.get("transcript") or "")[:4000],
            "order_idx": order,
            "created_by": user["ldap"],
            "created_at": now,
            "updated_at": now,
        },
    )
    if about:
      self.ledger.on_observation(ob)
    self.store.update("teach_sessions", sid, {"updated_at": now})
    return self.fmt_observation(ob)

  def _obs_row(self, user, obid, write=True):
    ob = self.store.get("observations", obid)
    if not ob:
      raise ApiError(404, "No such observation")
    self._session(user, ob["session_id"], write=write)
    return ob

  def patch_observation(
      self, user: dict[str, Any], obid: str, body: dict[str, Any]
  ) -> dict[str, Any]:
    ob = self._obs_row(user, obid)
    upd: dict[str, Any] = {"updated_at": util.now_iso()}
    if body.get("polarity") in POLARITIES:
      upd["polarity"] = body["polarity"]
    if "text" in body:
      upd["text"] = str(body.get("text") or "")[:4000]
    if "anchors" in body:
      o = self.store.get("outputs", ob["output_id"])
      upd["anchors"] = self._clean_anchors(
          body["anchors"], self.links_for_output(o), True
      )
    if "about_teaching_id" in body:
      upd["about_teaching_id"] = body.get("about_teaching_id") or None
    if body.get("status") in ("open", "distilled", "dismissed"):
      upd["status"] = body["status"]
    elif ("polarity" in body or "text" in body or "anchors" in body) and ob.get(
        "status"
    ) == "distilled":
      upd["status"] = "open"
    if body.get("crop_data_url"):
      upd["crop_file"] = self._save_crop(obid, body["crop_data_url"])
    ob = self.store.update("observations", obid, upd)
    self.ledger.on_observation(ob)
    return self.fmt_observation(ob)

  def delete_observation(self, user: dict[str, Any], obid: str) -> None:
    ob = self._obs_row(user, obid)
    if ob.get("crop_file"):
      try:
        os.remove(self.store.file_path("teach", ob["crop_file"]))
      except OSError:
        pass
    self.store.execute(
        "DELETE FROM teaching_trials WHERE source='designer' AND"
        " json_extract(evidence_json, '$.observation_id')=?",
        [obid],
    )
    self.store.delete("observations", obid)

  # -------------------------------------------------------------- distill
  def _teachings_digest(self, pid: str, sid: str) -> tuple[str, dict[str, Any]]:
    labels = self._labels(pid)
    rows = self.store.query(
        "SELECT * FROM teachings WHERE project_id=? AND ((status IN"
        " ('confirmed','escalated')) OR (status='candidate' AND session_id=?))"
        " ORDER BY order_idx",
        [pid, sid],
    )
    lines, by_id = [], {}
    for t in rows:
      eff = self.ledger.efficacy(t)
      by_id[t["id"]] = t
      lines.append(
          f"- [{t['id']} · {labels.get(t['id'], '')} · {t['status']} ·"
          f" v{t.get('version') or 1}] ({t.get('kind')};"
          f" {t.get('section') or '—'}; {t.get('polarity')})"
          f" {t.get('body_md')}\n  applies when:"
          f" {t.get('applies_when') or '—'}\n  trials: {eff['summary']} ·"
          f" signal: {eff['signal']}"
      )
    return ("\n".join(lines) or "(none yet)"), by_id

  def _obs_digest(self, ob: dict[str, Any], links: dict[str, Any]) -> str:
    comps = {c["key"]: c for c in links.get("components") or []}
    bits = []
    for a in ob.get("anchors") or []:
      k = a.get("kind")
      if k == "component":
        c = comps.get(a.get("key")) or {}
        reads = ", ".join((c.get("reads") or []) + (c.get("literal") or []))[
            :160
        ]
        bits.append(
            f"{a.get('label') or a.get('component')} #{a.get('id')}"
            + (f" (shows {reads})" if reads else "")
        )
      elif k == "data":
        bits.append(
            f"data {a.get('path')} = {a.get('value_preview', '')}"
            f" ({a.get('represented') or '?'})"
        )
      elif k == "relation":
        bits.append(
            f"relation {a.get('path')} ⇄ {a.get('label') or a.get('component')}"
            f" #{a.get('id')}"
        )
      elif k == "region":
        bits.append(f"region {a.get('box')}")
      elif k == "whole":
        bits.append("whole card")
    about = (
        f" (about {ob.get('about_teaching_id')})"
        if ob.get("about_teaching_id")
        else ""
    )
    return (
        f"- [{ob['id']}] {ob.get('polarity', 'note').upper()}{about} — anchors:"
        f" {'; '.join(bits) or 'whole card'} — \"{ob.get('text') or ''}\""
    )

  def _screenshot(self, o: dict[str, Any]) -> bytes | None:
    for p in (
        ((o.get("renders") or {}).get("web") or {}).get("screenshot_path"),
        o.get("screenshot_path"),
    ):
      if p and os.path.exists(p):
        try:
          with open(p, "rb") as fh:
            return fh.read()
        except OSError:
          pass
    return None

  def distill(
      self, user: dict[str, Any], sid: str, body: dict[str, Any] | None = None
  ) -> dict[str, Any]:
    s = self._session(user, sid, write=True)
    with self._lock:
      if sid in self._distilling:
        raise ApiError(409, "Distillation is already in progress")
      self._distilling.add(sid)
    try:
      return self._distill_locked(user, sid, s, body)
    finally:
      with self._lock:
        self._distilling.discard(sid)

  def _distill_locked(
      self,
      user: dict[str, Any],
      sid: str,
      s: dict[str, Any],
      body: dict[str, Any] | None = None,
  ) -> dict[str, Any]:
    pid = s["project_id"]
    p = self.store.get("projects", pid)
    want = (body or {}).get("output_id")
    obs = self.store.query(
        "SELECT * FROM observations WHERE session_id=? AND status='open' ORDER"
        " BY order_idx",
        [sid],
    )
    if want:
      obs = [o for o in obs if o["output_id"] == want]
    if not obs:
      raise ApiError(400, "No new observations to distill")
    by_out: dict[str, list[dict[str, Any]]] = {}
    for ob in obs:
      by_out.setdefault(ob["output_id"], []).append(ob)
    result = {
        "reply_md": "",
        "created": [],
        "updated": [],
        "suggestions": [],
        "out_of_scope": [],
        "theme_changes": [],
    }
    replies = []
    for idx, (oid, group) in enumerate(by_out.items(), start=1):
      o = self.store.get("outputs", oid)
      srow = self.store.get("samples", o["sample_id"]) or {}
      sample = self.svc.fmt_sample(srow) if srow else {}
      links = self.links_for_output(o)
      digest, by_id = self._teachings_digest(pid, sid)
      text = "\n\n".join([
          (
              f"# Project: {p.get('name') or 'Untitled'}\nBrief:"
              f" {s.get('brief') or p.get('description') or '(none)'}"
          ),
          "# Teachings so far (with how the model has followed them)\n"
          + digest,
          (
              f"# Card {idx}: {sample.get('title')} [{sample.get('genre')}]\n"
              f"Intent: {sample.get('intent') or ''}\nContext:"
              f" {json.dumps(sample.get('context') or {}, ensure_ascii=False)}"
          ),
          "## Input data\n```json\n"
          + common.clip_json(sample.get("payload"), 6000)
          + "\n```",
          "## Rendered structure\n```\n"
          + (links.get("outline") or "")
          + "\n```",
          "## Data not represented on the card\n"
          + (
              ", ".join(links.get("unrepresented")[:30])
              or "(every field is shown)"
          ),
          "## New observations from the designer\n"
          + "\n".join(self._obs_digest(ob, links) for ob in group),
      ])
      parts: list[Any] = [text]
      png = self._screenshot(o)
      if png:
        parts += ["Screenshot of the card:", Image(png, "image/png")]
      try:
        resp = self.llm.generate(
            purpose="teach_distill",
            system=common.prompt("teach_distill.md"),
            parts=parts,
            json_schema=DISTILL_SCHEMA,
            temperature=0.3,
            model=self._model(pid),
            timeout_s=180,
        )
      except LlmError as e:
        raise ApiError(502, f"Gemini could not distill right now: {e}") from e
      data = resp.data or {}
      if data.get("reply_md"):
        replies.append(str(data["reply_md"]))
      obs_ids = {ob["id"] for ob in group}
      touched: dict[str, set[str]] = {ob["id"]: set() for ob in group}
      for item in data.get("teachings") or []:
        if not isinstance(item, dict):
          continue
        try:
          out = self._apply_distill_item(user, s, item, by_id, obs_ids)
        except Exception as e:  # pylint: disable=broad-except
          # Keep the other learnings; report the one that failed.
          traceback.print_exc()
          result.setdefault("errors", []).append(
              f"{item.get('title') or item.get('body_md') or 'item'}: {e}"[:300]
          )
          continue
        if not out:
          continue
        kind, t = out
        if kind == "created":
          result["created"].append(t["id"])
        elif kind == "updated":
          result["updated"].append(t["id"])
        else:
          result["suggestions"].append({"teaching_id": t["id"], "kind": kind})
        for x in item.get("source_observation_ids") or []:
          if x in touched:
            touched[x].add(t["id"])
      for tc in data.get("theme_changes") or []:
        if isinstance(tc, dict) and (tc.get("token") or tc.get("key")):
          result["theme_changes"].append(tc)
      for oo in data.get("out_of_scope") or []:
        if isinstance(oo, dict):
          result["out_of_scope"].append(oo)
      now = util.now_iso()
      for ob in group:
        self.store.update(
            "observations",
            ob["id"],
            {
                "status": "distilled",
                "teaching_ids": sorted(touched.get(ob["id"]) or []),
                "updated_at": now,
            },
        )
    if result["theme_changes"] or result["out_of_scope"]:
      s = self.store.get("teach_sessions", sid)
      summ = dict(s.get("summary") or {})
      cur = {
          str(x.get("token")): x for x in summ.get("theme_suggestions") or []
      }
      for tc in result["theme_changes"]:
        tok = str(tc.get("token") or tc.get("key"))
        prev = cur.get(tok) or {}
        cur[tok] = {
            "token": tok,
            "value": tc.get("value"),
            "reason": tc.get("reason") or "",
            "observation_ids": sorted(
                set(prev.get("observation_ids") or [])
                | {
                    x
                    for x in tc.get("observation_ids") or []
                    if isinstance(x, str)
                }
            ),
        }
      summ["theme_suggestions"] = list(cur.values())
      oos = {
          x.get("observation_id"): x
          for x in summ.get("out_of_scope") or []
          if x.get("observation_id")
      }
      for x in result["out_of_scope"]:
        if x.get("observation_id"):
          oos[x["observation_id"]] = {
              "observation_id": x["observation_id"],
              "why": str(x.get("why") or ""),
              "suggested_home": x.get("suggested_home") or "",
          }
      summ["out_of_scope"] = list(oos.values())
      self.store.update("teach_sessions", sid, {"summary": summ})
    self.store.update(
        "teach_sessions",
        sid,
        {"last_distilled_at": util.now_iso(), "updated_at": util.now_iso()},
    )
    result["reply_md"] = "\n\n".join(replies)
    return {"result": result, "state": self.state(user, sid)}

  def _apply_distill_item(self, user, s, item, by_id, obs_ids):
    pid = s["project_id"]
    action = item.get("action") or "new"
    body = " ".join(str(item.get("body_md") or "").split())
    src = [x for x in item.get("source_observation_ids") or [] if x in obs_ids]
    sec = str(item.get("section") or "").strip()
    reason = str(item.get("reason") or "").strip()
    target = by_id.get(str(item.get("teaching_id") or ""))
    if action in ("suggest_template", "suggest_escalation"):
      if not target:
        return None
      self._set_extra(
          target["id"],
          {
              "suggestion": {
                  "kind": (
                      "template"
                      if action == "suggest_template"
                      else "escalation"
                  ),
                  "reason": reason,
                  "at": util.now_iso(),
              }
          },
      )
      return (action, target)
    if not body:
      return None
    common_kw = {
        "title": str(item.get("title") or "")[:200],
        "section": sec,
        "polarity": (
            item.get("polarity")
            if item.get("polarity") in ("do", "dont")
            else "do"
        ),
        "applies_when": str(item.get("applies_when") or ""),
        "data_signature": str(item.get("data_signature") or ""),
    }
    conf = item.get("confidence")
    conf = float(conf) if isinstance(conf, (int, float)) else None
    if action in ("refine", "merge") and target:
      if (
          target["status"] == "candidate"
          and target.get("session_id") == s["id"]
      ):
        t = self.ledger.add_version(
            target,
            body_md=body,
            title=common_kw["title"] or None,
            section=sec or None,
            applies_when=common_kw["applies_when"] or None,
            reason=reason or "refined from new observations",
            source="distill",
            created_by=user["ldap"],
        )
        ids = list(dict.fromkeys((t.get("source_observation_ids") or []) + src))
        self.store.update("teachings", t["id"], {"source_observation_ids": ids})
        return ("updated", t)
      t = self.ledger.create_teaching(
          pid,
          body_md=body,
          status="candidate",
          origin="distill",
          session_id=s["id"],
          confidence=conf,
          source_observation_ids=src,
          supersedes_id=target["id"],
          created_by=user["ldap"],
          reason=reason or f"refines {target.get('title')}",
          version_source="distill",
          **common_kw,
      )
      merged = [
          m
          for m in item.get("merged_ids") or []
          if m in by_id and m != target["id"]
      ]
      self._set_extra(
          t["id"],
          {"reason": reason, "merged_ids": merged, "refines": target["id"]},
      )
      return ("created", t)
    # new (or refine/merge without a valid target)
    for t0 in by_id.values():
      if t0["status"] == "candidate" and norm_text(t0["body_md"]) == norm_text(
          body
      ):
        return None
    t = self.ledger.create_teaching(
        pid,
        body_md=body,
        status="candidate",
        origin="distill",
        session_id=s["id"],
        confidence=conf,
        source_observation_ids=src,
        created_by=user["ldap"],
        reason=reason or "distilled from observations",
        version_source="distill",
        **common_kw,
    )
    if reason:
      self._set_extra(t["id"], {"reason": reason})
    return ("created", t)

  def _set_extra(self, tid: str, extra: dict[str, Any]) -> None:
    t = self.store.get("teachings", tid)
    cur = dict(t.get("extra") or {})
    cur.update(extra)
    self.store.update(
        "teachings", tid, {"extra": cur, "updated_at": util.now_iso()}
    )

  @staticmethod
  def _extra(t: dict[str, Any]) -> dict[str, Any]:
    return dict(t.get("extra") or {})

  # ------------------------------------------------------------ teachings
  def patch_teaching(
      self, user: dict[str, Any], tid: str, body: dict[str, Any]
  ) -> dict[str, Any]:
    t = self.ledger.teaching_row(user, tid, write=True)
    body = body or {}
    status = body.get("status")
    if (
        status == "confirmed"
        and t["status"] == "candidate"
        and t.get("supersedes_id")
    ):
      orig = self.store.get("teachings", t["supersedes_id"])
      if orig:
        ex = self._extra(t)
        self.ledger.add_version(
            orig,
            body_md=str(body.get("body_md") or t["body_md"]),
            title=t.get("title"),
            section=t.get("section") or None,
            applies_when=t.get("applies_when") or None,
            reason=ex.get("reason") or "refined in a teaching session",
            source="teach",
            created_by=user["ldap"],
        )
        ids = list(
            dict.fromkeys(
                (orig.get("source_observation_ids") or [])
                + (t.get("source_observation_ids") or [])
            )
        )
        self.store.update(
            "teachings",
            orig["id"],
            {
                "source_observation_ids": ids,
                "status": (
                    "confirmed"
                    if orig["status"] in ("retired", "candidate")
                    else orig["status"]
                ),
            },
        )
        for m in ex.get("merged_ids") or []:
          self.store.update(
              "teachings",
              m,
              {
                  "status": "merged",
                  "supersedes_id": orig["id"],
                  "updated_at": util.now_iso(),
              },
          )
        self.store.update(
            "teachings", tid, {"status": "merged", "updated_at": util.now_iso()}
        )
        return self.fmt_teaching(
            self.store.get("teachings", orig["id"]),
            self._labels(orig["project_id"]),
        )
    text_keys = ("body_md",)
    if (
        any(k in body for k in text_keys)
        and str(body.get("body_md") or "").strip()
    ):
      t = self.ledger.add_version(
          t,
          body_md=" ".join(str(body["body_md"]).split()),
          title=body.get("title"),
          section=body.get("section"),
          applies_when=body.get("applies_when"),
          reason=str(body.get("reason") or "edited by the designer"),
          source="user",
          created_by=user["ldap"],
      )
    upd: dict[str, Any] = {}
    for k in ("title", "section", "applies_when"):
      if k in body and "body_md" not in body:
        upd[k] = str(body.get(k) or "")[:2000]
    if body.get("polarity") in ("do", "dont"):
      upd["polarity"] = body["polarity"]
    if status in ("candidate", "confirmed", "dismissed", "retired"):
      upd["status"] = status
    if upd:
      upd["updated_at"] = util.now_iso()
      t = self.store.update("teachings", tid, upd)
    if status == "dismissed" and t.get("template_id"):
      self.store.update(
          "templates",
          t["template_id"],
          {"status": "archived", "updated_at": util.now_iso()},
      )
    return self.fmt_teaching(t, self._labels(t["project_id"]))

  def _library_pool(
      self, pid: str, exclude: set[str], limit: int = 40
  ) -> list[dict[str, Any]]:
    rows = self.store.query(
        "SELECT * FROM samples ORDER BY created_at DESC, rowid DESC"
    )
    by_grp: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
      if r["id"] in exclude:
        continue
      by_grp.setdefault(r.get("grp") or "?", []).append(r)
    out: list[dict[str, Any]] = []
    while len(out) < limit and any(by_grp.values()):
      for g in list(by_grp):
        if by_grp[g]:
          out.append(by_grp[g].pop(0))
          if len(out) >= limit:
            break
    return out

  def _applications_call(
      self,
      pid: str,
      subject: str,
      pool: list[dict[str, Any]],
      examples: list[dict[str, Any]] | None = None,
  ) -> dict[str, Any]:
    cands = []
    for r in pool:
      s = self.svc.fmt_sample(r)
      cands.append(
          f"## {r['id']} — {s.get('title')}"
          f" [{s.get('group') or r.get('grp')}/{s.get('genre')}]\nIntent:"
          f" {s.get('intent') or ''}\nContext: "
          + json.dumps(s.get("context") or {}, ensure_ascii=False)[:300]
          + "\nData:\n"
          + "\n".join(_data_outline(s.get("payload"))[:24])
      )
    ex_lines = []
    for e in examples or []:
      srow = self.store.get("samples", e.get("sample_id"))
      if srow:
        ex_lines.append(
            f"- {'APPLIES' if e.get('applies') else 'DOES NOT APPLY'}:"
            f" {srow['id']} — {srow.get('title')}"
        )
    text = (
        subject
        + (
            "\n\n# Designer's examples\n" + "\n".join(ex_lines)
            if ex_lines
            else ""
        )
        + f"\n\n# Candidate scenarios ({len(cands)})\n\n"
        + "\n\n".join(cands)
    )
    try:
      resp = self.llm.generate(
          purpose="teach_applications",
          system=common.prompt("teach_applications.md"),
          parts=[text],
          json_schema=APPLICATIONS_SCHEMA,
          temperature=0.2,
          model=self._model(pid),
          timeout_s=120,
      )
    except LlmError as e:
      raise ApiError(502, f"Gemini could not find applications: {e}") from e
    data = resp.data or {}
    valid = {r["id"]: r for r in pool}
    for e in examples or []:
      srow = self.store.get("samples", e.get("sample_id"))
      if srow:
        valid.setdefault(srow["id"], srow)

    def brief(sid: str) -> dict[str, Any]:
      r = valid[sid]
      return {
          "sample_id": sid,
          "title": r.get("title"),
          "genre": r.get("genre"),
      }

    applies, seen = [], set()
    for a in data.get("applies") or []:
      sid_ = a.get("sample_id")
      if sid_ in valid and sid_ not in seen:
        seen.add(sid_)
        applies.append({
            **brief(sid_),
            "how_it_applies": str(a.get("how_it_applies") or ""),
            "relevance": a.get("relevance") or "medium",
            "probe_output_id": None,
        })
    exceptions = []
    for a in data.get("exceptions") or []:
      sid_ = a.get("sample_id")
      if sid_ in valid and sid_ not in seen:
        seen.add(sid_)
        exceptions.append({**brief(sid_), "why": str(a.get("why") or "")})
    return {
        "applies": applies[:6],
        "exceptions": exceptions[:3],
        "none_reason": str(data.get("none_reason") or ""),
        "when_to_use": str(data.get("when_to_use") or ""),
    }

  def applications(self, user: dict[str, Any], tid: str) -> dict[str, Any]:
    t = self.ledger.teaching_row(user, tid, write=True)
    pid = t["project_id"]
    exclude: set[str] = set()
    if t.get("session_id"):
      s = self.store.get("teach_sessions", t["session_id"])
      exclude = {c["sample_id"] for c in (s or {}).get("cards") or []}
    subject = (
        f"# {'Template' if t.get('kind') == 'pattern' else 'Rule'}:"
        f" {t.get('title')}\n\n{t.get('body_md')}\n\nApplies when:"
        f" {t.get('applies_when') or '—'}\nData signature:"
        f" {t.get('data_signature') or '—'}"
    )
    res = self._applications_call(
        pid, subject, self._library_pool(pid, exclude)
    )
    res.pop("when_to_use", None)
    res.update({
        "computed_at": util.now_iso(),
        "for_version": int(t.get("version") or 1),
    })
    t = self.store.update(
        "teachings",
        tid,
        {
            "applications": res,
            "applications_status": "ready",
            "updated_at": util.now_iso(),
        },
    )
    return self.fmt_teaching(t, self._labels(pid))

  def probe(
      self, user: dict[str, Any], tid: str, body: dict[str, Any]
  ) -> dict[str, Any]:
    t = self.ledger.teaching_row(user, tid, write=True)
    sid = str((body or {}).get("session_id") or t.get("session_id") or "")
    s = self._session(user, sid, write=True)
    sample_id = str((body or {}).get("sample_id") or "")
    if not self.store.get("samples", sample_id):
      raise ApiError(400, "Unknown sample")
    run = self._teach_run(
        user,
        s["project_id"],
        [sample_id],
        "teach_probe",
        {"probe_teaching_id": tid},
    )
    cards = self._run_cards(run["id"], [sample_id], "probe", tid)
    self._append_cards(sid, cards)
    apps = dict(t.get("applications") or {})
    if cards and apps.get("applies"):
      for a in apps["applies"]:
        if a.get("sample_id") == sample_id:
          a["probe_output_id"] = cards[0]["output_id"]
      self.store.update("teachings", tid, {"applications": apps})
    return self.state(user, sid)

  def rephrase(self, user: dict[str, Any], tid: str) -> dict[str, Any]:
    d = self.ledger.detail(user, tid)
    t = d["teaching"]
    hist = self.ledger.history_md(self.store.get("teachings", tid))
    text = (
        f"# Rule\n{t['body_md']}\n\nApplies when:"
        f" {t['applies_when'] or '—'}\n\n# History and"
        f" trials\n{hist}\n\nRewrite the rule so a generative-UI engine"
        " actually follows it: be concrete about the content role, the"
        " component choice and where it goes; keep it one or two imperative"
        " sentences, no styling values. Do not repeat a phrasing that already"
        " failed."
    )
    try:
      resp = self.llm.generate(
          purpose="teach_rephrase",
          system=common.prompt("teach_distill.md"),
          parts=[text],
          json_schema=REPHRASE_SCHEMA,
          temperature=0.4,
          model=self._model(t["project_id"]),
          timeout_s=90,
      )
    except LlmError as e:
      raise ApiError(502, f"Gemini could not rephrase right now: {e}") from e
    data = resp.data or {}
    return {
        "suggestion": {
            "body_md": " ".join(str(data.get("body_md") or "").split()),
            "applies_when": str(data.get("applies_when") or t["applies_when"]),
            "reason": str(data.get("reason") or ""),
        }
    }

  # ------------------------------------------------------------- patterns
  def _selection_keys(
      self, o: dict[str, Any], selection: dict[str, Any], links: dict[str, Any]
  ) -> list[str]:
    sel = selection or {}
    comps = links.get("components") or []
    if sel.get("key"):
      key = str(sel["key"])
      if "|" not in key:
        key = next((c["key"] for c in comps if c["id"] == key), key)
      if not any(c["key"] == key for c in comps):
        raise ApiError(400, f"Unknown component {key!r}")
      return [key]
    if sel.get("region"):
      try:
        x, y, w, h = (float(v) for v in sel["region"])
      except (TypeError, ValueError) as e:
        raise ApiError(400, "region needs [x, y, w, h]") from e
      inside = []
      for c in comps:
        b = c.get("box")
        if not b:
          continue
        ix = max(0, min(x + w, b["x"] + b["w"]) - max(x, b["x"]))
        iy = max(0, min(y + h, b["y"] + b["h"]) - max(y, b["y"]))
        if b["w"] * b["h"] and ix * iy / (b["w"] * b["h"]) >= 0.8:
          inside.append(c)
      keys = {c["key"] for c in inside}
      tree = layout_diff.Tree(o.get("a2ui") or [])
      tops = []
      for c in inside:
        n = tree.by_key.get(c["key"])
        par = n.parent if n else None
        if par is not None and par.key in keys:
          continue
        tops.append(c["key"])
      if not tops:
        raise ApiError(400, "The region does not contain any component")
      return tops
    return [""]  # whole card

  def draft_pattern(
      self, user: dict[str, Any], body: dict[str, Any]
  ) -> dict[str, Any]:
    o = self.runs._output_row(user, str((body or {}).get("output_id") or ""))  # pylint: disable=protected-access
    links = self.links_for_output(o)
    keys = self._selection_keys(o, (body or {}).get("selection") or {}, links)
    outs = [data_links.subtree_outline(o.get("a2ui") or [], k) for k in keys]
    outline = "\n".join(x["outline"] for x in outs)
    skeleton = ", ".join(x["skeleton"] for x in outs if x["skeleton"])
    comps_types = list(dict.fromkeys(t for x in outs for t in x["components"]))
    sub = set()
    for k in keys:
      sub.update(
          data_links.subtree_keys(o.get("a2ui") or [], k)
          if k
          else [c["key"] for c in links["components"]]
      )
    data_paths = []
    for c in links.get("components") or []:
      if c["key"] in sub:
        data_paths += c.get("reads") or []
        data_paths += c.get("literal") or []
    data_paths = list(dict.fromkeys(data_paths))
    srow = self.store.get("samples", o["sample_id"]) or {}
    sample = self.svc.fmt_sample(srow) if srow else {}
    leaves = {l["path"]: l for l in links.get("leaves") or []}
    data_lines = [
        f"- {p}: {leaves.get(p, {}).get('value_preview', '')}"
        for p in data_paths[:40]
    ]
    text = (
        f"# Sample\n{sample.get('title')}\nIntent:"
        f" {sample.get('intent') or ''}\nContext:"
        f" {json.dumps(sample.get('context') or {}, ensure_ascii=False)}\n\n#"
        f" Selected subtree\n```\n{outline}\n```\nSkeleton: {skeleton}\n\n#"
        " Data feeding it\n"
        + ("\n".join(data_lines) or "(none)")
    )
    parts: list[Any] = [text]
    crop = (body or {}).get("crop_data_url")
    if isinstance(crop, str) and crop.startswith("data:image/"):
      try:
        parts += [
            "Crop of the selection:",
            Image(base64.b64decode(crop.partition(",")[2]), "image/png"),
        ]
      except ValueError:
        pass
    data: dict[str, Any] = {}
    try:
      resp = self.llm.generate(
          purpose="teach_pattern",
          system=common.prompt("teach_pattern.md"),
          parts=parts,
          json_schema=PATTERN_SCHEMA,
          temperature=0.3,
          model=self._model(o["project_id"]),
          timeout_s=120,
      )
      data = resp.data or {}
    except LlmError:
      data = {}
    trig = [
        {
            "group": (
                tg.get("group")
                if tg.get("group") in ("data", "intent", "context")
                else "data"
            ),
            "text": str(tg.get("text") or "").strip(),
            "on": bool(tg.get("on")),
        }
        for tg in data.get("triggers") or []
        if isinstance(tg, dict) and str(tg.get("text") or "").strip()
    ]
    return {
        "draft": {
            "name": str(
                data.get("name") or f"{(comps_types or ['Layout'])[0]} pattern"
            )[:120],
            "description": str(data.get("description") or ""),
            "structure_md": str(data.get("structure_md") or outline),
            "components": [
                str(c) for c in data.get("components") or comps_types
            ][:30],
            "skeleton": skeleton,
            "data_paths": data_paths,
            "triggers": trig,
            "when_to_use": str(data.get("when_to_use") or ""),
            "data_signature": str(data.get("data_signature") or ""),
            "outline": outline,
            "selection_keys": keys,
        }
    }

  def check_pattern(
      self, user: dict[str, Any], body: dict[str, Any]
  ) -> dict[str, Any]:
    body = body or {}
    pid = body.get("project_id")
    exclude = set(
        x for x in body.get("exclude_sample_ids") or [] if isinstance(x, str)
    )
    if body.get("output_id"):
      o = self.runs._output_row(user, str(body["output_id"]))  # pylint: disable=protected-access
      pid = o["project_id"]
      exclude.add(o["sample_id"])
    if body.get("session_id"):
      s = self._session(user, str(body["session_id"]))
      pid = s["project_id"]
      exclude |= {c["sample_id"] for c in s.get("cards") or []}
    if not pid:
      raise ApiError(400, "project_id, output_id or session_id is required")
    self.svc._project_row(pid, user)  # pylint: disable=protected-access
    examples = [e for e in body.get("examples") or [] if isinstance(e, dict)]
    subject = (
        f"# Template: {body.get('name') or 'Layout template'}\n\n"
        f"Structure:\n{body.get('structure_md') or ''}\n\n"
        f"When to use: {body.get('when_to_use') or '—'}"
    )
    pool = self._library_pool(
        pid, exclude | {e.get("sample_id") for e in examples}, 36
    )
    res = self._applications_call(pid, subject, pool, examples)
    if not examples or not res.get("when_to_use"):
      res["when_to_use"] = str(body.get("when_to_use") or "")
    return res

  def save_pattern(
      self, user: dict[str, Any], body: dict[str, Any]
  ) -> dict[str, Any]:
    body = body or {}
    o = self.runs._output_row(user, str(body.get("output_id") or ""))  # pylint: disable=protected-access
    pid = o["project_id"]
    self.svc._project_row(pid, user, write=True)  # pylint: disable=protected-access
    name = str(body.get("name") or "").strip()
    if not name:
      raise ApiError(400, "Name the template")
    when = str(body.get("when_to_use") or "").strip()
    examples = [e for e in body.get("examples") or [] if isinstance(e, dict)]
    pos = [
        self.store.get("samples", e.get("sample_id"))
        for e in examples
        if e.get("applies")
    ]
    neg = [
        self.store.get("samples", e.get("sample_id"))
        for e in examples
        if not e.get("applies")
    ]
    ex_txt = ""
    if any(pos) or any(neg):
      bits = []
      if any(pos):
        bits.append("e.g. " + "; ".join(r["title"] for r in pos if r)[:300])
      if any(neg):
        bits.append("not for " + "; ".join(r["title"] for r in neg if r)[:300])
      ex_txt = " (" + " · ".join(bits) + ")"
    sid = body.get("session_id") or None
    source = {
        "kind": "teach" if sid else "review",
        "session_id": sid,
        "output_id": o["id"],
        "selection": body.get("selection") or {},
        "skeleton": str(body.get("skeleton") or ""),
        "examples": examples,
        "triggers": body.get("triggers") or [],
    }
    tpl = self.imports.create_template(
        user,
        pid,
        {
            "name": name,
            "description": body.get("description") or "",
            "when_to_use": (when + ex_txt).strip(),
            "structure_md": body.get("structure_md") or "",
            "components": body.get("components") or [],
        },
        source=source,
    )
    crop = body.get("crop_data_url")
    if isinstance(crop, str) and crop.startswith("data:image/"):
      try:
        raw = base64.b64decode(crop.partition(",")[2])
        fname = f"{tpl['id']}.png"
        with open(self.store.file_path("templates", fname), "wb") as fh:
          fh.write(raw)
        self.store.update(
            "templates",
            tpl["id"],
            {"image_file": fname, "updated_at": util.now_iso()},
        )
      except (ValueError, OSError):
        pass
    t = self.ledger.create_teaching(
        pid,
        kind="pattern",
        title=name,
        body_md=str(
            body.get("description") or body.get("structure_md") or name
        ),
        section="Templates",
        applies_when=when,
        data_signature=str(body.get("data_signature") or ""),
        status="confirmed",
        origin="pattern",
        session_id=sid,
        template_id=tpl["id"],
        created_by=user["ldap"],
        reason="captured from an example",
        version_source="teach",
    )
    tr = self.store.get("templates", tpl["id"])
    labels = self._labels(pid)
    return {
        "template": self.imports.fmt_template(tr),
        "teaching": self.fmt_teaching(t, labels),
    }

  # --------------------------------------------------------------- finish
  def assemble(
      self, user: dict[str, Any], sid: str, body: dict[str, Any] | None = None
  ) -> dict[str, Any]:
    s = self._session(user, sid)
    pid = s["project_id"]
    a = self.assembled(pid)
    md = a["constitution_md"]
    hint = {k: v["t"] for k, v in a["rule_map"].items()}
    polished = False
    note = ""
    if (body or {}).get("polish") and md.strip():
      p = self.store.get("projects", pid)
      cat = self.svc.catalog_for(p)
      try:
        res = reviser.revise(
            self.llm,
            constitution_md=md,
            catalog_text=", ".join(cat.enabled_components()),
            history=[],
            text=(
                "Tighten the wording of every rule. Keep every rule, its"
                " meaning, its section and the order; one bullet per rule;"
                " add nothing new; no styling values."
            ),
            outputs=[],
            guidelines=[],
            model=self._model(pid),
        )
        cand = (
            (res.get("proposal") or {}).get("constitution_md") or ""
        ).strip()
        orig = layout_diff.parse_rules(md)
        new = layout_diff.parse_rules(cand)
        if cand and len(new) == len(orig):
          md = cand + "\n"
          hint = {
              r["id"]: a["rule_map"][o_["id"]]["t"]
              for r, o_ in zip(new, orig)
              if o_["id"] in a["rule_map"]
          }
          polished = True
        else:
          note = "Polish changed the number of rules, so it was not applied."
      except LlmError as e:
        note = f"Polish is unavailable right now ({e})."
    cur = self.svc.current_revision(pid)
    cur_md = (cur or {}).get("text_md") or ""
    n = (cur or {}).get("number") or 0
    summ = s.get("summary") or {}
    gl_preview = []
    for t in self._confirmed_rules(pid):
      if t.get("session_id") != sid and not set(
          t.get("source_observation_ids") or []
      ):
        continue
      obs = [
          self.store.get("observations", x)
          for x in t.get("source_observation_ids") or []
      ]
      obs = [
          ob
          for ob in obs
          if ob
          and ob["session_id"] == sid
          and ob.get("polarity") in ("good", "bad")
      ]
      if not obs:
        continue
      crops = [self._crop_url(ob) for ob in obs if self._crop_url(ob)][:2]
      gl_preview.append({
          "teaching_id": t["id"],
          "kind": t.get("polarity") or "do",
          "title": t.get("title") or "",
          "body_md": t.get("body_md") or "",
          "output_id": obs[0]["output_id"],
          "crops": crops,
      })
    return {
        "constitution_md": md,
        "diff": diffs.diff(cur_md, md, f"rev {n}", f"rev {n + 1}"),
        "sections": a["sections"],
        "rule_map": a["rule_map"],
        "bind_hint": hint,
        "theme_changes": summ.get("theme_suggestions") or [],
        "guideline_preview": gl_preview,
        "polished": polished,
        "note": note,
    }

  def finish(
      self, user: dict[str, Any], sid: str, body: dict[str, Any]
  ) -> dict[str, Any]:
    s = self._session(user, sid, write=True)
    pid = s["project_id"]
    body = body or {}
    md = str(body.get("constitution_md") or "").strip()
    if not md:
      md = self.assembled(pid)["constitution_md"].strip()
    if not md:
      raise ApiError(400, "Confirm at least one rule before finishing")
    hint = {
        k: v
        for k, v in (body.get("bind_hint") or {}).items()
        if isinstance(k, str) and isinstance(v, str)
    }
    n_rules = len(layout_diff.parse_rules(md))
    summary = f"Teaching session: {n_rules} rule{'s' if n_rules != 1 else ''}"
    now = util.now_iso()
    self.store.insert(
        "chat_messages",
        {
            "id": util.new_id("m"),
            "project_id": pid,
            "role": "assistant",
            "text_md": (
                f"Saved the constitution from **{s.get('name')}**:"
                f" {n_rules} confirmed rules assembled by section."
            ),
            "context": {
                "output_ids": [],
                "guideline_ids": [],
                "teach_session_id": sid,
            },
            "proposal": None,
            "out_of_scope": [],
            "proposal_status": "none",
            "accepted_revision_id": None,
            "author_ldap": "assistant",
            "created_at": now,
        },
    )
    rev = self.svc.add_revision(
        user, pid, md + "\n", summary, source="teach", bind_hint=hint
    )
    # Dos & Don'ts: one per confirmed rule with anchored good/bad observations.
    preview = self.assemble(user, sid)["guideline_preview"]
    want = body.get("guideline_teaching_ids")
    gids = []
    for g in preview:
      if isinstance(want, list) and g["teaching_id"] not in want:
        continue
      gl = self.svc.create_guideline(
          user,
          pid,
          {
              "kind": g["kind"] if g["kind"] in ("do", "dont") else "do",
              "title": g["title"] or g["body_md"][:120],
              "body_md": g["body_md"],
              "output_id": g["output_id"],
              "renderer": "web",
              "fixable": "yes",
              "tags": ["teach", f"teaching:{g['teaching_id']}"],
          },
      )
      for url in g.get("crops") or []:
        fname = url.split("/files/teach/", 1)[-1].split("?", 1)[0]
        path = self.store.file_path("teach", fname)
        try:
          with open(path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
          self.svc.add_guideline_screenshot(
              user, gl["id"], None, f"data:image/png;base64,{b64}"
          )
        except OSError:
          pass
      gids.append(gl["id"])
    # Tokens the designer kept.
    apply = body.get("apply_theme")
    sugg = (s.get("summary") or {}).get("theme_suggestions") or []
    if apply:
      chosen = [x for x in sugg if apply is True or x.get("token") in apply]
      if chosen:
        p = self.store.get("projects", pid)
        st = settings_mod.merged(p.get("settings"))
        theme, applied = settings_mod.apply_theme_changes(st["theme"], chosen)
        if applied:
          st["theme"] = theme
          self.svc.put_settings(user, pid, st, source="teach", summary=summary)
    non_probe_cards = [
        c for c in s.get("cards") or [] if c.get("origin") != "probe"
    ]
    base_cards = non_probe_cards or (s.get("cards") or [])
    sample_ids = list(dict.fromkeys(c["sample_id"] for c in base_cards))[:12]
    after = self.runs.create_run(
        user,
        pid,
        {
            "revision_id": rev["id"],
            "sample_ids": sample_ids,
            "viewports": ["phone"],
            "renderers": ["web"],
            "judge": True,
            "include_forms": False,
        },
        kind="teach_after",
    )
    base = (
        self.store.get("constitution_revisions", s.get("base_revision_id"))
        or {}
    )
    eff = self.effects.create_from_runs(
        user,
        pid,
        control_run_id=s["baseline_run_id"],
        treatment_run_id=after["id"],
        revision_id=rev["id"],
        labels={
            "control": (
                f"the previous constitution (Rev {base.get('number') or 0})"
                if (base.get("text_md") or "").strip()
                else "no constitution (Rev 0)"
            ),
            "treatment": f"the taught constitution (Rev {rev['number']})",
        },
    )
    summ = dict(s.get("summary") or {})
    summ.update({"guideline_ids": gids, "rules": n_rules})
    self.store.update(
        "teach_sessions",
        sid,
        {
            "status": "finishing",
            "revision_id": rev["id"],
            "after_run_id": after["id"],
            "effect_id": eff["id"],
            "summary": summ,
            "updated_at": util.now_iso(),
        },
    )
    return self.state(user, sid)

  def graduate(self, user: dict[str, Any], sid: str) -> dict[str, Any]:
    self._session(user, sid, write=True)
    self.store.update(
        "teach_sessions", sid, {"status": "done", "updated_at": util.now_iso()}
    )
    return self.state(user, sid)

  # ---------------------------------------------------------------- voice
  def voice_turn(
      self, user: dict[str, Any], sid: str, body: dict[str, Any]
  ) -> dict[str, Any]:
    s = self._session(user, sid, write=True)
    body = body or {}
    transcript = str(body.get("transcript") or "").strip()
    src = (
        body.get("source")
        if body.get("source") in ("voice", "typed")
        else "voice"
    )
    pending = (
        body.get("pending") if isinstance(body.get("pending"), dict) else None
    )
    capture = bool(body.get("capture"))
    if not transcript and not (capture and pending):
      raise ApiError(400, "Nothing was said")
    o = self._card_output(s, str(body.get("output_id") or ""))
    links = self.links_for_output(o)
    out: dict[str, Any] = {
        "intent": "capture" if capture and not transcript else "chat",
        "observation": pending,
        "question": "",
        "quick_replies": [],
        "say": "",
        "navigate": None,
        "template_from": "",
        "captured": None,
        "undone": None,
    }
    if transcript:
      srow = self.store.get("samples", o["sample_id"]) or {}
      sample = self.svc.fmt_sample(srow) if srow else {}
      comp_lines = [
          f"- {c['key']}: {c['label']}"
          + (
              f" (shows {', '.join((c['reads'] + c['literal'])[:4])})"
              if c["reads"] or c["literal"]
              else ""
          )
          for c in links["components"][:160]
          if c["type"] not in ("Column", "Row") or c["reads"]
      ]
      leaf_lines = [
          f"- {l['path']} = {l['value_preview']}"
          for l in links["leaves"][:200]
          if l["kind"] not in ("object",)
      ]
      labels = self._labels(s["project_id"])
      tlines = [
          f"- {t['id']} ({labels.get(t['id'], '')}): {t['title']}"
          for t in self._confirmed_rules(s["project_id"])
      ]
      text = "\n\n".join([
          (
              f"# Card: {sample.get('title')}\nIntent:"
              f" {sample.get('intent') or ''}"
          ),
          "# Components (key: label)\n" + "\n".join(comp_lines),
          "# Input data paths\n" + "\n".join(leaf_lines),
          "# Pointed\n"
          + json.dumps(body.get("pointed") or [], ensure_ascii=False)[:1500],
          "# Pending observation\n"
          + json.dumps(pending or {}, ensure_ascii=False)[:1500],
          "# Your last question\n"
          + (str(body.get("question") or "") or "(none)"),
          "# Teachings\n" + ("\n".join(tlines) or "(none)"),
          f"# Utterance ({src})\n{transcript}",
      ])
      try:
        resp = self.llm.generate(
            purpose="teach_voice_turn",
            system=common.prompt("teach_voice_turn.md"),
            parts=[text],
            json_schema=VOICE_TURN_SCHEMA,
            temperature=0.2,
            model=self._model(s["project_id"]),
            timeout_s=45,
        )
      except LlmError as e:
        raise ApiError(502, f"Gemini did not answer: {e}") from e
      data = resp.data or {}
      intent = data.get("intent") or "chat"
      out.update({
          "intent": intent,
          "question": str(data.get("question") or ""),
          "quick_replies": [str(x) for x in data.get("quick_replies") or []][
              :3
          ],
          "say": str(data.get("say") or ""),
          "navigate": data.get("navigate") if intent == "navigate" else None,
          "template_from": (
              str(data.get("template_from") or "")
              if intent == "template"
              else ""
          ),
      })
      if intent in ("observe", "answer", "capture"):
        ob = data.get("observation") or {}
        anchors = self._clean_anchors(ob.get("anchors"), links, strict=False)
        if not anchors and pending:
          anchors = pending.get("anchors") or []
        about = (
            ob.get("about_teaching_id")
            or (pending or {}).get("about_teaching_id")
            or None
        )
        out["observation"] = {
            "polarity": (
                ob.get("polarity")
                if ob.get("polarity") in POLARITIES
                else (pending or {}).get("polarity") or "note"
            ),
            "text": (
                str(ob.get("text") or (pending or {}).get("text") or "").strip()
            ),
            "anchors": anchors,
            "about_teaching_id": (
                about if about and self.store.get("teachings", about) else None
            ),
            "complete": bool(ob.get("complete")),
            "transcript": (
                (
                    (pending or {}).get("transcript", "") + "\n" + transcript
                ).strip()
            ),
        }
        if intent == "capture":
          capture = True
      elif intent == "undo":
        last = self.store.query(
            "SELECT id FROM observations WHERE session_id=? AND created_by=?"
            " AND source IN ('voice','typed') ORDER BY order_idx DESC LIMIT 1",
            [sid, user["ldap"]],
        )
        if last:
          self.delete_observation(user, last[0]["id"])
          out["undone"] = last[0]["id"]
    if capture and out.get("observation"):
      ob = out["observation"]
      if ob.get("text") or ob.get("anchors"):
        out["captured"] = self.add_observation(
            user,
            sid,
            {
                "output_id": o["id"],
                "polarity": ob.get("polarity") or "note",
                "text": ob.get("text") or "",
                "anchors": ob.get("anchors") or [],
                "about_teaching_id": ob.get("about_teaching_id"),
                "crop_data_url": body.get("crop_data_url"),
                "source": src,
                "transcript": ob.get("transcript") or transcript,
            },
            strict=False,
        )
        out["observation"] = None
    return out
