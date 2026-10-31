"""Teachings ledger: every learned rule/template, its phrasings and its trials.

A *teaching* is a stable object for one thing the project has learned (a
constitution rule or a layout template). Each time it is reworded a new
*phrasing* (teaching_versions row) is recorded with the reason. Each time an
output was generated where the teaching should apply, a *trial* records
whether the model followed it, from one of four instruments:

  judge      per-rule findings of the LLM judge (`findings[].rule_id`)
  effect     effect-study attribution (`changes[].rule_ids`, `unapplied_rules`)
  designer   an observation in a teaching session marked as about a teaching
  structure  deterministic template skeleton match on probe outputs

Every constitution revision is bound to teachings (`rule_bindings`): R1..Rn of
`layout_diff.parse_rules` → teaching ids, by exact text, then by similarity
(a new phrasing), else a new teaching. That is what lets the ledger notice an
instruction that kept being rephrased (in sessions, in chat or in the editor)
and was never followed.
"""

from __future__ import annotations

import difflib
import re
import threading
from typing import Any

from core import layout_diff, util
from core.service import ApiError, Service

SIGNALS = {
    "min_trials": 3,  # reliable / flaky need at least this many trials
    "reliable": 0.8,  # followed rate at or above → reliable
    "flaky": 0.3,  # below → not followed (if ≥ not_followed_min trials)
    "not_followed_min": 2,
    "rephrased_min_versions": 2,  # phrasings with ≥ min trials each …
    "rephrased_min_trials_per_version": 2,
    "rephrased_gain": 0.5,  # … none reaching this rate → rephrased_no_gain
    "similar": 0.75,  # text similarity for binding a reworded rule
}

FOLLOW_SCORE = {"yes": 1.0, "partial": 0.5, "no": 0.0}
_MD = re.compile(r"[*_`#>\[\]()~|]+")
_WS = re.compile(r"\s+")


def norm_text(s: str) -> str:
  s = _MD.sub(" ", s or "")
  s = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", s)
  return _WS.sub(" ", s).strip().lower()


def similarity(a: str, b: str) -> float:
  a, b = norm_text(a), norm_text(b)
  if not a or not b:
    return 0.0
  return difflib.SequenceMatcher(None, a, b).ratio()


def _title_from(body: str) -> str:
  t = _WS.sub(" ", _MD.sub(" ", body or "")).strip()
  t = re.split(r"(?<=[.;:])\s", t, maxsplit=1)[0].rstrip(".;:")
  return t if len(t) <= 90 else t[:89].rstrip() + "…"


class TeachLedger:
  """Project-wide store of teachings, phrasings, trials and bindings."""

  def __init__(self, svc: Service):
    self.svc = svc
    self.store = svc.store
    self._lock = threading.RLock()
    svc.teach_ledger = self

  # ------------------------------------------------------------ teachings
  def create_teaching(
      self,
      pid: str,
      *,
      kind: str = "rule",
      body_md: str,
      title: str = "",
      section: str = "",
      polarity: str = "do",
      applies_when: str = "",
      data_signature: str = "",
      status: str = "candidate",
      origin: str = "distill",
      session_id: str | None = None,
      confidence: float | None = None,
      source_observation_ids: list[str] | None = None,
      template_id: str | None = None,
      theme_changes: list[dict[str, Any]] | None = None,
      supersedes_id: str | None = None,
      created_by: str = "",
      reason: str = "",
      version_source: str = "",
      revision_id: str | None = None,
  ) -> dict[str, Any]:
    now = util.now_iso()
    tid = util.new_id("t")
    body_md = (body_md or "").strip()
    order = (
        self.store.scalar(
            "SELECT MAX(order_idx) FROM teachings WHERE project_id=?", [pid]
        )
        or 0
    ) + 1
    t = self.store.insert(
        "teachings",
        {
            "id": tid,
            "project_id": pid,
            "session_id": session_id,
            "kind": kind if kind in ("rule", "pattern") else "rule",
            "title": (title or _title_from(body_md))[:200],
            "body_md": body_md,
            "section": (section or "").strip()[:120],
            "polarity": polarity if polarity in ("do", "dont") else "do",
            "applies_when": (applies_when or "").strip()[:2000],
            "data_signature": (data_signature or "").strip()[:1000],
            "version": 1,
            "status": status,
            "origin": origin,
            "confidence": confidence,
            "source_observation_ids": list(source_observation_ids or []),
            "template_id": template_id,
            "supersedes_id": supersedes_id,
            "applications": None,
            "applications_status": "none",
            "theme_changes": list(theme_changes or []),
            "escalation_guideline_id": None,
            "order_idx": order,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        },
    )
    self._insert_version(
        t,
        1,
        reason=reason or "first phrasing",
        source=version_source or origin,
        revision_id=revision_id,
        created_by=created_by,
    )
    return t

  def _insert_version(
      self,
      t: dict[str, Any],
      number: int,
      *,
      reason: str,
      source: str,
      revision_id: str | None,
      created_by: str,
  ) -> None:
    self.store.insert(
        "teaching_versions",
        {
            "id": util.new_id("tv"),
            "teaching_id": t["id"],
            "number": number,
            "title": t.get("title") or "",
            "body_md": t.get("body_md") or "",
            "section": t.get("section") or "",
            "applies_when": t.get("applies_when") or "",
            "reason": (reason or "")[:500],
            "source": source or "",
            "revision_id": revision_id,
            "created_by": created_by,
            "created_at": util.now_iso(),
        },
    )

  def add_version(
      self,
      t: dict[str, Any],
      *,
      body_md: str,
      title: str | None = None,
      section: str | None = None,
      applies_when: str | None = None,
      reason: str = "",
      source: str = "user",
      revision_id: str | None = None,
      created_by: str = "",
  ) -> dict[str, Any]:
    """Records a new phrasing (no-op when the text is unchanged)."""
    with self._lock:
      t = self.store.get("teachings", t["id"]) or t
      body_md = (body_md or "").strip()
      same_text = norm_text(body_md) == norm_text(t.get("body_md") or "")
      upd: dict[str, Any] = {"updated_at": util.now_iso()}
      if section is not None:
        upd["section"] = section.strip()[:120]
      if applies_when is not None:
        upd["applies_when"] = applies_when.strip()[:2000]
      if title is not None and title.strip():
        upd["title"] = title.strip()[:200]
      if same_text:
        return self.store.update("teachings", t["id"], upd)
      number = int(t.get("version") or 1) + 1
      upd.update({"body_md": body_md, "version": number})
      if title is None or not title.strip():
        upd["title"] = _title_from(body_md)
      t2 = self.store.update("teachings", t["id"], upd)
      self._insert_version(
          t2,
          number,
          reason=reason,
          source=source,
          revision_id=revision_id,
          created_by=created_by,
      )
      return t2

  def versions(self, tid: str) -> list[dict[str, Any]]:
    return self.store.query(
        "SELECT * FROM teaching_versions WHERE teaching_id=? ORDER BY number",
        [tid],
    )

  def trials(self, tid: str) -> list[dict[str, Any]]:
    return self.store.query(
        "SELECT * FROM teaching_trials WHERE teaching_id=? ORDER BY"
        " created_at DESC, rowid DESC",
        [tid],
    )

  def teaching_row(self, user: dict[str, Any], tid: str, write=False):
    t = self.store.get("teachings", tid)
    if not t:
      raise ApiError(404, "No such teaching")
    self.svc._project_row(t["project_id"], user, write=write)  # pylint: disable=protected-access
    return t

  # -------------------------------------------------------------- binding
  def _live_rules(self, pid: str) -> list[dict[str, Any]]:
    return self.store.query(
        "SELECT * FROM teachings WHERE project_id=? AND kind='rule' AND status"
        " IN ('confirmed','retired','escalated') ORDER BY order_idx",
        [pid],
    )

  def bindings(self, revision_id: str) -> dict[str, dict[str, Any]]:
    rows = self.store.query(
        "SELECT * FROM rule_bindings WHERE revision_id=?", [revision_id]
    )
    return {r["rule_id"]: r for r in rows}

  def bind_revision(
      self,
      rev: dict[str, Any] | None,
      hint: dict[str, str] | None = None,
      *,
      created_by: str = "",
  ) -> dict[str, dict[str, Any]]:
    """Binds R1..Rn of a revision to teachings (idempotent)."""
    if not rev:
      return {}
    with self._lock:
      existing = self.bindings(rev["id"])
      if existing:
        return existing
      pid = rev["project_id"]
      rules = layout_diff.parse_rules(rev.get("text_md") or "")
      live = self._live_rules(pid)
      vers_by_t = {t["id"]: self.versions(t["id"]) for t in live}
      used: set[str] = set()
      source = rev.get("source") or "revision"
      out: dict[str, dict[str, Any]] = {}
      for r in rules:
        text = r["text"]
        t, how, version = None, "", 1
        hid = (hint or {}).get(r["id"])
        if hid:
          t = self.store.get("teachings", hid)
          if t and t["project_id"] == pid and t["id"] not in used:
            how = "assembled"
            if norm_text(t.get("body_md") or "") != norm_text(text):
              t = self.add_version(
                  t,
                  body_md=text,
                  section=r.get("section") or t.get("section") or "",
                  reason="reworded when the constitution was assembled",
                  source=source,
                  revision_id=rev["id"],
                  created_by=created_by,
              )
          else:
            t = None
        if t is None:
          nt = norm_text(text)
          for cand in live:
            if cand["id"] in used:
              continue
            for v in reversed(vers_by_t.get(cand["id"]) or []):
              if norm_text(v["body_md"]) == nt:
                t, how, version = cand, "exact", v["number"]
                break
            if t:
              break
        if t is None:
          best, best_r = None, 0.0
          for cand in live:
            if cand["id"] in used:
              continue
            sr = similarity(cand.get("body_md") or "", text)
            if sr > best_r:
              best, best_r = cand, sr
          if best is not None and best_r >= SIGNALS["similar"]:
            t = self.add_version(
                best,
                body_md=text,
                section=r.get("section") or best.get("section") or "",
                reason=f"rephrased in {source} (revision {rev.get('number')})",
                source=source,
                revision_id=rev["id"],
                created_by=created_by or rev.get("author_ldap") or "",
            )
            how = f"similar:{best_r:.2f}"
        if t is None:
          t = self.create_teaching(
              pid,
              body_md=text,
              section=r.get("section") or "",
              status="confirmed",
              origin="revision",
              reason=f"from revision {rev.get('number')} ({source})",
              version_source=source,
              revision_id=rev["id"],
              created_by=created_by or rev.get("author_ldap") or "",
          )
          how = "new"
        t = self.store.get("teachings", t["id"])
        if how != "exact":
          version = int(t.get("version") or 1)
        if t["status"] in ("retired", "candidate"):
          self.store.update(
              "teachings",
              t["id"],
              {"status": "confirmed", "updated_at": util.now_iso()},
          )
        used.add(t["id"])
        row = {
            "revision_id": rev["id"],
            "rule_id": r["id"],
            "teaching_id": t["id"],
            "version": version,
            "text": text,
            "section": r.get("section") or "",
            "how": how,
        }
        self.store.execute(
            "INSERT OR REPLACE INTO rule_bindings (revision_id, rule_id,"
            " teaching_id, version, text, section, how) VALUES"
            " (?,?,?,?,?,?,?)",
            list(row.values()),
        )
        out[r["id"]] = row
      proj = self.store.get("projects", pid) or {}
      if proj.get("current_revision_id") == rev["id"] and rev.get("parent_id"):
        # Rules that were in the previous revision and were dropped now.
        prev = {
            b["teaching_id"] for b in self.bindings(rev["parent_id"]).values()
        }
        for t in live:
          if (
              t["id"] in prev
              and t["id"] not in used
              and t["status"] == "confirmed"
          ):
            self.store.update(
                "teachings",
                t["id"],
                {"status": "retired", "updated_at": util.now_iso()},
            )
      return out

  def current_bindings(self, pid: str) -> dict[str, dict[str, Any]]:
    rev = self.svc.current_revision(pid)
    return self.bind_revision(rev) if rev else {}

  # --------------------------------------------------------------- trials
  def record_trial(
      self,
      teaching_id: str,
      *,
      version: int | None,
      output: dict[str, Any],
      expected: str,
      followed: str,
      source: str,
      evidence: dict[str, Any] | None = None,
  ) -> None:
    t = self.store.get("teachings", teaching_id)
    if not t:
      return
    run = self.store.get("runs", output.get("run_id")) or {}
    with self._lock:
      self.store.execute(
          "DELETE FROM teaching_trials WHERE teaching_id=? AND output_id=? AND"
          " source=?",
          [teaching_id, output["id"], source],
      )
      self.store.insert(
          "teaching_trials",
          {
              "id": util.new_id("tt"),
              "teaching_id": teaching_id,
              "version": int(version or t.get("version") or 1),
              "project_id": t["project_id"],
              "output_id": output["id"],
              "run_id": output.get("run_id"),
              "run_kind": run.get("kind") or "standard",
              "sample_id": output.get("sample_id"),
              "revision_id": output.get("revision_id"),
              "expected": expected,
              "followed": followed,
              "source": source,
              "evidence": evidence or {},
              "created_at": util.now_iso(),
          },
      )

  def rule_map_for_output(
      self, output: dict[str, Any]
  ) -> dict[str, dict[str, Any]]:
    """{'R1': {'t': tid, 'v': version}, 'T1': {...}} for an output's run."""
    run = self.store.get("runs", output.get("run_id")) or {}
    cfg = run.get("config") or {}
    rm = cfg.get("rule_map")
    out: dict[str, dict[str, Any]] = {}
    if isinstance(rm, dict) and rm:
      for k, v in rm.items():
        if isinstance(v, dict) and v.get("t"):
          out[k] = {"t": v["t"], "v": v.get("v")}
        elif isinstance(v, str):
          out[k] = {"t": v, "v": None}
    elif "constitution_override" not in cfg:
      rev = self.store.get("constitution_revisions", output.get("revision_id"))
      for rid, b in self.bind_revision(rev).items():
        out[rid] = {"t": b["teaching_id"], "v": b["version"]}
    if not any(k.startswith("T") for k in out):
      for i, tpl in enumerate(cfg.get("templates") or [], start=1):
        tid = tpl.get("id") if isinstance(tpl, dict) else None
        if not tid:
          continue
        t = self.store.query(
            "SELECT id, version FROM teachings WHERE template_id=? LIMIT 1",
            [tid],
        )
        if t:
          out[f"T{i}"] = {"t": t[0]["id"], "v": t[0]["version"]}
    return out

  def on_judged(self, output: dict[str, Any]) -> None:
    """Records judge trials from `findings[].rule_id` (text as fallback)."""
    j = output.get("judge") or {}
    findings = j.get("findings") or []
    if not findings:
      return
    rm = self.rule_map_for_output(output)
    if not rm:
      return
    texts = {}
    for k, ref in rm.items():
      t = self.store.get("teachings", ref["t"])
      if t:
        texts[k] = t.get("body_md") or t.get("title") or ""
    for f in findings:
      if not isinstance(f, dict):
        continue
      status = f.get("status")
      if status not in ("followed", "violated"):
        continue
      # The judge authors applicability explicitly; only applicable rules
      # are trials (an inapplicable rule is neither followed nor violated).
      if str(f.get("applies") or "yes").lower() not in ("yes", "true"):
        continue
      rid = str(f.get("rule_id") or "").strip().upper()
      if rid not in rm:
        best, best_r = None, 0.0
        for k, txt in texts.items():
          sr = similarity(txt, f.get("rule") or "")
          if sr > best_r:
            best, best_r = k, sr
        rid = best if best and best_r >= 0.6 else ""
      if not rid:
        continue
      ref = rm[rid]
      self.record_trial(
          ref["t"],
          version=ref.get("v"),
          output=output,
          expected="yes",
          followed="yes" if status == "followed" else "no",
          source="judge",
          evidence={
              "rule_id": rid,
              "finding": {
                  k: f.get(k)
                  for k in ("rule", "status", "evidence", "severity")
              },
          },
      )

  def on_structure(
      self, output: dict[str, Any], teaching_id: str, match: str
  ) -> None:
    t = self.store.get("teachings", teaching_id)
    if not t:
      return
    self.record_trial(
        teaching_id,
        version=t.get("version"),
        output=output,
        expected="yes",
        followed={"exact": "yes", "similar": "partial"}.get(match, "no"),
        source="structure",
        evidence={"structure_match": match},
    )

  def on_effect_analyzed(self, effect: dict[str, Any]) -> None:
    """Records effect trials for the treatment outputs of an effect study."""
    analysis = effect.get("analysis") or {}
    pairs = analysis.get("pairs") or {}
    if not pairs:
      return
    rev = self.store.get("constitution_revisions", effect.get("revision_id"))
    bindings = self.bind_revision(rev)
    if not bindings:
      return
    for key, res in pairs.items():
      if not isinstance(res, dict) or res.get("error"):
        continue
      sid, _, vp = key.partition("|")
      outs = self.store.query(
          "SELECT * FROM outputs WHERE run_id=? AND sample_id=? AND viewport=?"
          " AND COALESCE(stage,'result')='result' LIMIT 1",
          [effect.get("treatment_run_id"), sid, vp or "phone"],
      )
      if not outs:
        continue
      o = outs[0]
      followed: dict[str, list[dict[str, Any]]] = {}
      for ch in res.get("changes") or []:
        if ch.get("cause") in ("rule", "indirect"):
          for rid in ch.get("rule_ids") or []:
            followed.setdefault(rid, []).append(ch)
      for rid, chs in followed.items():
        b = bindings.get(rid)
        if b:
          self.record_trial(
              b["teaching_id"],
              version=b["version"],
              output=o,
              expected="yes",
              followed="yes",
              source="effect",
              evidence={
                  "rule_id": rid,
                  "changes": [
                      {
                          k: c.get(k)
                          for k in (
                              "change",
                              "cause",
                              "explanation",
                              "confidence",
                          )
                      }
                      for c in chs[:4]
                  ],
                  "effect_id": effect.get("id"),
              },
          )
      for u in res.get("unapplied_rules") or []:
        if str(u.get("applies") or "yes").lower() not in ("yes", "true"):
          continue
        b = bindings.get(u.get("rule_id"))
        if b and u.get("rule_id") not in followed:
          self.record_trial(
              b["teaching_id"],
              version=b["version"],
              output=o,
              expected="yes",
              followed="no",
              source="effect",
              evidence={
                  "rule_id": u.get("rule_id"),
                  "why": u.get("why") or "",
                  "effect_id": effect.get("id"),
              },
          )

  def on_observation(self, ob: dict[str, Any]) -> None:
    tid = ob.get("about_teaching_id")
    o = self.store.get("outputs", ob.get("output_id"))
    if not o:
      return
    if not tid or ob.get("polarity") not in ("good", "bad"):
      self.store.execute(
          "DELETE FROM teaching_trials WHERE output_id=? AND source='designer'"
          " AND json_extract(evidence_json, '$.observation_id')=?",
          [o["id"], ob["id"]],
      )
      return
    ref = self.rule_map_for_output(o)
    version = next((v.get("v") for v in ref.values() if v["t"] == tid), None)
    self.record_trial(
        tid,
        version=version,
        output=o,
        expected="yes",
        followed="yes" if ob["polarity"] == "good" else "no",
        source="designer",
        evidence={"observation_id": ob["id"], "text": ob.get("text") or ""},
    )

  # ------------------------------------------------------------- efficacy
  def efficacy(
      self,
      t: dict[str, Any],
      trials: list[dict[str, Any]] | None = None,
      versions: list[dict[str, Any]] | None = None,
  ) -> dict[str, Any]:
    trials = self.trials(t["id"]) if trials is None else trials
    versions = self.versions(t["id"]) if versions is None else versions
    counted = [
        x
        for x in trials
        if x.get("expected") == "yes" and x.get("followed") in FOLLOW_SCORE
    ]
    n = len(counted)
    score = sum(FOLLOW_SCORE[x["followed"]] for x in counted)
    rate = (score / n) if n else None
    by_v: dict[int, list[dict[str, Any]]] = {}
    for x in counted:
      by_v.setdefault(int(x.get("version") or 1), []).append(x)
    by_version = []
    for v in versions or [{"number": int(t.get("version") or 1)}]:
      xs = by_v.get(int(v["number"]), [])
      sc = sum(FOLLOW_SCORE[x["followed"]] for x in xs)
      by_version.append({
          "version": int(v["number"]),
          "trials": len(xs),
          "followed": sc,
          "rate": (sc / len(xs)) if xs else None,
      })
    phrasings = max(len(versions or []), 1)
    tested = [
        b
        for b in by_version
        if b["trials"] >= SIGNALS["rephrased_min_trials_per_version"]
    ]
    status = t.get("status")
    if status == "escalated":
      signal = "escalated"
    elif status == "retired":
      signal = "retired"
    elif status in ("candidate", "dismissed", "merged"):
      signal = status if status != "candidate" else "candidate"
    elif len(tested) >= SIGNALS["rephrased_min_versions"] and all(
        (b["rate"] or 0) < SIGNALS["rephrased_gain"] for b in tested
    ):
      signal = "rephrased_no_gain"
    elif (
        n >= SIGNALS["min_trials"]
        and rate is not None
        and rate >= SIGNALS["reliable"]
    ):
      signal = "reliable"
    elif (
        n >= SIGNALS["not_followed_min"]
        and rate is not None
        and rate < SIGNALS["flaky"]
    ):
      signal = "not_followed"
    elif n >= SIGNALS["min_trials"]:
      signal = "flaky"
    else:
      signal = "untested"
    fol = sum(1 for x in counted if x["followed"] == "yes")
    part = sum(1 for x in counted if x["followed"] == "partial")
    verb = "used" if t.get("kind") == "pattern" else "followed"
    shown = f"{score:g}"
    summary = (
        f"{verb} {shown} / {n}"
        + (" when expected" if t.get("kind") == "pattern" else "")
        + f" · {phrasings} phrasing{'s' if phrasings != 1 else ''}"
        if n
        else (
            f"no trials yet · {phrasings}"
            f" phrasing{'s' if phrasings != 1 else ''}"
        )
    )
    return {
        "trials": n,
        "expected": n,
        "followed": fol,
        "partial": part,
        "not_followed": n - fol - part,
        "score": score,
        "rate": rate,
        "phrasings": phrasings,
        "by_version": by_version,
        "signal": signal,
        "summary": summary,
        "last_trial_at": trials[0]["created_at"] if trials else None,
    }

  def fmt(
      self,
      t: dict[str, Any],
      *,
      label: str = "",
      template: dict[str, Any] | None = None,
  ) -> dict[str, Any]:
    return {
        "id": t["id"],
        "project_id": t["project_id"],
        "session_id": t.get("session_id"),
        "kind": t.get("kind") or "rule",
        "label": label,
        "title": t.get("title") or "",
        "body_md": t.get("body_md") or "",
        "section": t.get("section") or "",
        "polarity": t.get("polarity") or "do",
        "applies_when": t.get("applies_when") or "",
        "data_signature": t.get("data_signature") or "",
        "version": int(t.get("version") or 1),
        "status": t.get("status"),
        "origin": t.get("origin"),
        "confidence": t.get("confidence"),
        "source_observation_ids": t.get("source_observation_ids") or [],
        "supersedes_id": t.get("supersedes_id"),
        "template_id": t.get("template_id"),
        "template": template,
        "applications": t.get("applications"),
        "applications_status": t.get("applications_status") or "none",
        "theme_changes": t.get("theme_changes") or [],
        "escalation_guideline_id": t.get("escalation_guideline_id"),
        "suggestion": (t.get("extra") or {}).get("suggestion"),
        "refines": (
            (t.get("extra") or {}).get("refines") or t.get("supersedes_id")
        ),
        "reason": (t.get("extra") or {}).get("reason") or "",
        "efficacy": self.efficacy(t),
        "created_at": t.get("created_at"),
        "updated_at": t.get("updated_at"),
    }

  def labels(self, pid: str) -> dict[str, str]:
    """Ledger labels: R-ids of the current revision, T1.. for templates."""
    out: dict[str, str] = {}
    for rid, b in self.current_bindings(pid).items():
      out.setdefault(b["teaching_id"], rid)
    pats = self.store.query(
        "SELECT id FROM teachings WHERE project_id=? AND kind='pattern' ORDER"
        " BY order_idx",
        [pid],
    )
    for i, p in enumerate(pats, start=1):
      out[p["id"]] = f"T{i}"
    c = 0
    for t in self.store.query(
        "SELECT id FROM teachings WHERE project_id=? AND kind='rule' AND"
        " status='candidate' ORDER BY order_idx",
        [pid],
    ):
      c += 1
      out.setdefault(t["id"], f"C{c}")
    return out

  def list_project(
      self,
      user: dict[str, Any],
      pid: str,
      *,
      template_fmt=None,
      status: str | None = None,
      kind: str | None = None,
      labels: dict[str, str] | None = None,
  ) -> dict[str, Any]:
    self.svc._project_row(pid, user)  # pylint: disable=protected-access
    labels = labels if labels is not None else self.labels(pid)
    rows = self.store.query(
        "SELECT * FROM teachings WHERE project_id=? AND status NOT IN"
        " ('dismissed','merged') ORDER BY order_idx",
        [pid],
    )
    out = []
    for t in rows:
      if status and t["status"] != status:
        continue
      if kind and t["kind"] != kind:
        continue
      tpl = None
      if t.get("template_id") and template_fmt:
        tr = self.store.get("templates", t["template_id"])
        tpl = template_fmt(tr) if tr else None
      out.append(self.fmt(t, label=labels.get(t["id"], ""), template=tpl))
    sig = [t["efficacy"]["signal"] for t in out]
    trial_runs = self.store.scalar(
        "SELECT COUNT(DISTINCT run_id) FROM teaching_trials WHERE project_id=?",
        [pid],
    )
    summary = {
        "tracked": len(out),
        "rules": sum(1 for t in out if t["kind"] == "rule"),
        "templates": sum(1 for t in out if t["kind"] == "pattern"),
        "reliable": sig.count("reliable"),
        "flaky": sig.count("flaky"),
        "not_followed": sig.count("not_followed"),
        "rephrased_no_gain": sig.count("rephrased_no_gain"),
        "escalated": sig.count("escalated"),
        "retired": sig.count("retired"),
        "candidates": sig.count("candidate"),
        "untested": sig.count("untested"),
        "trials": sum(t["efficacy"]["trials"] for t in out),
        "runs": trial_runs or 0,
    }
    order = {
        "rephrased_no_gain": 0,
        "not_followed": 1,
        "flaky": 2,
        "candidate": 3,
        "untested": 4,
        "reliable": 5,
        "escalated": 6,
        "retired": 7,
    }

    def lab_key(t):
      lab = t["label"] or ""
      m = re.match(r"([A-Z])(\d+)", lab)
      return (lab[:1] or "Z", int(m.group(2)) if m else 9999)

    out.sort(key=lambda t: (order.get(t["efficacy"]["signal"], 9), lab_key(t)))
    return {"teachings": out, "summary": summary, "thresholds": dict(SIGNALS)}

  def detail(
      self,
      user: dict[str, Any],
      tid: str,
      *,
      template_fmt=None,
      output_fmt=None,
      labels_fn=None,
  ) -> dict[str, Any]:
    t = self.teaching_row(user, tid)
    labels = (labels_fn or self.labels)(t["project_id"])
    versions = self.versions(tid)
    trials = self.trials(tid)
    eff = self.efficacy(t, trials, versions)
    by_v = {b["version"]: b for b in eff["by_version"]}
    vout = []
    for v in versions:
      b = by_v.get(int(v["number"]), {})
      vout.append({
          **{
              k: v.get(k)
              for k in (
                  "id",
                  "teaching_id",
                  "number",
                  "title",
                  "body_md",
                  "section",
                  "applies_when",
                  "reason",
                  "source",
                  "revision_id",
                  "created_by",
                  "created_at",
              )
          },
          "trials": b.get("trials", 0),
          "followed": b.get("followed", 0),
          "rate": b.get("rate"),
      })
    tout = []
    for x in trials[:200]:
      item = dict(x)
      item.pop("project_id", None)
      if output_fmt:
        item["output"] = output_fmt(x.get("output_id"))
      tout.append(item)
    gls = [
        self.svc.fmt_guideline(g)
        for g in self.store.query(
            "SELECT * FROM guidelines WHERE project_id=? AND tags_json LIKE ?",
            [t["project_id"], f'%"teaching:{tid}"%'],
        )
    ]
    tpl = None
    if t.get("template_id") and template_fmt:
      tr = self.store.get("templates", t["template_id"])
      tpl = template_fmt(tr) if tr else None
    return {
        "teaching": self.fmt(t, label=labels.get(tid, ""), template=tpl),
        "versions": vout,
        "trials": tout,
        "guidelines": gls,
    }

  # -------------------------------------------------------------- actions
  def history_md(self, t: dict[str, Any]) -> str:
    eff = self.efficacy(t)
    lines = [
        f"**Teaching**: {t.get('title')}",
        f"**Signal**: {eff['signal']} — {eff['summary']}",
        "",
        "**Phrasings**",
    ]
    by_v = {b["version"]: b for b in eff["by_version"]}
    for v in self.versions(t["id"]):
      b = by_v.get(int(v["number"]), {})
      rate = (
          f"{b.get('followed', 0):g}/{b.get('trials', 0)}"
          if b.get("trials")
          else "untested"
      )
      lines.append(
          f"- v{v['number']} ({rate}): {v['body_md']} —"
          f" _{v.get('reason') or ''}_"
      )
    misses = [x for x in self.trials(t["id"]) if x.get("followed") == "no"][:5]
    if misses:
      lines += ["", "**Recent misses**"]
      for x in misses:
        ev = x.get("evidence") or {}
        why = (
            ev.get("why")
            or (ev.get("finding") or {}).get("evidence")
            or ev.get("text")
            or ""
        )
        lines.append(
            f"- {x.get('source')} on output `{x.get('output_id')}`: {why}"
        )
    return "\n".join(lines)

  def escalate(
      self, user: dict[str, Any], tid: str, note: str = ""
  ) -> dict[str, Any]:
    t = self.teaching_row(user, tid, write=True)
    miss = self.store.query(
        "SELECT output_id FROM teaching_trials WHERE teaching_id=? AND"
        " followed='no' ORDER BY created_at DESC LIMIT 1",
        [tid],
    )
    hist = self.history_md(t)
    g = self.svc.create_guideline(
        user,
        t["project_id"],
        {
            "kind": "dont",
            "title": (
                t.get("title")
                if (t.get("polarity") == "dont")
                else f"Model does not follow: {t.get('title')}"
            ),
            "body_md": (
                "This instruction was taught and rephrased but the generator"
                " did not follow it, so it is escalated as something the"
                " constitution cannot fix.\n\n"
                + hist
            ),
            "output_id": miss[0]["output_id"] if miss else None,
            "fixable": "no",
            "tags": ["unfixable", f"teaching:{tid}"],
        },
    )
    g = self.svc.escalate_guideline(user, g["id"], note or hist)
    t = self.store.update(
        "teachings",
        tid,
        {
            "status": "escalated",
            "escalation_guideline_id": g["id"],
            "updated_at": util.now_iso(),
        },
    )
    return {"teaching": t, "guideline": g}

  def split(
      self, user: dict[str, Any], tid: str, version: int
  ) -> dict[str, Any]:
    """Detaches phrasings ≥ version (a wrong similarity binding)."""
    t = self.teaching_row(user, tid, write=True)
    versions = self.versions(tid)
    keep = [v for v in versions if int(v["number"]) < int(version)]
    move = [v for v in versions if int(v["number"]) >= int(version)]
    if not keep or not move:
      raise ApiError(400, "Pick a phrasing after the first one to split off")
    with self._lock:
      first = move[0]
      nt = self.create_teaching(
          t["project_id"],
          kind=t.get("kind") or "rule",
          body_md=move[-1]["body_md"],
          section=move[-1].get("section") or t.get("section") or "",
          polarity=t.get("polarity") or "do",
          applies_when=move[-1].get("applies_when") or "",
          status=t["status"],
          origin=t.get("origin") or "revision",
          session_id=t.get("session_id"),
          created_by=user["ldap"],
          reason=f"split from {t.get('title')} (v{first['number']}+)",
          version_source="user",
          revision_id=first.get("revision_id"),
      )
      # Re-number moved trials and bindings to the new teaching (v1..).
      vmap = {int(v["number"]): i for i, v in enumerate(move, start=1)}
      for old, new in vmap.items():
        self.store.execute(
            "UPDATE teaching_trials SET teaching_id=?, version=? WHERE"
            " teaching_id=? AND version=?",
            [nt["id"], new, tid, old],
        )
        self.store.execute(
            "UPDATE rule_bindings SET teaching_id=?, version=? WHERE"
            " teaching_id=? AND version=?",
            [nt["id"], new, tid, old],
        )
      self.store.execute(
          "DELETE FROM teaching_versions WHERE teaching_id=? AND number>=?",
          [tid, int(version)],
      )
      last = keep[-1]
      t = self.store.update(
          "teachings",
          tid,
          {
              "body_md": last["body_md"],
              "title": last.get("title") or _title_from(last["body_md"]),
              "section": last.get("section") or t.get("section") or "",
              "applies_when": last.get("applies_when") or "",
              "version": int(last["number"]),
              "updated_at": util.now_iso(),
          },
      )
    return {
        "teaching": t,
        "new_teaching": self.store.get("teachings", nt["id"]),
    }

  def delete(self, user: dict[str, Any], tid: str) -> None:
    self.teaching_row(user, tid, write=True)
    with self._lock:
      self.store.execute(
          "DELETE FROM teaching_trials WHERE teaching_id=?", [tid]
      )
      self.store.execute(
          "DELETE FROM teaching_versions WHERE teaching_id=?", [tid]
      )
      self.store.execute("DELETE FROM rule_bindings WHERE teaching_id=?", [tid])
      self.store.delete("teachings", tid)

  def delete_trials_for_run(self, run_id: str) -> None:
    self.store.execute("DELETE FROM teaching_trials WHERE run_id=?", [run_id])
