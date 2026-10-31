"""Exports: constitution markdown/history, guidelines ZIP, benchmark bundles."""

from __future__ import annotations

import io
import json
import os
from typing import Any
import zipfile

from core import util
from core.storage import Store


def _slug(text: str, n: int = 48) -> str:
  s = "".join(c.lower() if c.isalnum() else "-" for c in text or "")
  while "--" in s:
    s = s.replace("--", "-")
  return s.strip("-")[:n] or "item"


def constitution_md(
    store: Store, project: dict[str, Any], rev: dict[str, Any]
) -> str:
  head = (
      f"<!-- Design constitution: {project['name']} — revision "
      f"{rev['number']} ({rev['created_at']}) by {rev['author_ldap']}. "
      f"Summary: {rev['summary']} -->\n\n"
  )
  return head + (rev["text_md"] or "")


def history_json(store: Store, project: dict[str, Any]) -> dict[str, Any]:
  revs = store.query(
      "SELECT * FROM constitution_revisions WHERE project_id=? ORDER BY number",
      [project["id"]],
  )
  chat = {
      m["id"]: m
      for m in store.query(
          "SELECT id, text_md, created_at FROM chat_messages WHERE"
          " project_id=?",
          [project["id"]],
      )
  }
  return {
      "project": {
          "id": project["id"],
          "name": project["name"],
          "owner_ldap": project["owner_ldap"],
          "description": project.get("description"),
      },
      "exported_at": util.now_iso(),
      "revisions": [
          {
              "number": r["number"],
              "id": r["id"],
              "summary": r["summary"],
              "source": r["source"],
              "author_ldap": r["author_ldap"],
              "created_at": r["created_at"],
              "text_md": r["text_md"],
              "chat_message": (
                  (chat.get(r["chat_message_id"]) or {}).get("text_md")
                  if r.get("chat_message_id")
                  else None
              ),
          }
          for r in revs
      ],
  }


def _guideline_entry(
    store: Store,
    z: zipfile.ZipFile,
    prefix: str,
    g: dict[str, Any],
    project: dict[str, Any],
) -> dict:
  folder = (
      f"{prefix}{'do' if g['kind'] == 'do' else 'dont'}-{_slug(g['title'])}-{g['id'][-6:]}/"
  )
  shots = []
  for i, sh in enumerate(g.get("screenshots") or []):
    f = sh.get("file")
    path = store.file_path("guidelines", f) if f else None
    if path and os.path.exists(path):
      name = f"screenshot_{i + 1}{os.path.splitext(f)[1]}"
      z.write(path, folder + name)
      shots.append(name)
  sample = (
      store.get("samples", g.get("sample_id")) if g.get("sample_id") else None
  )
  output = (
      store.get("outputs", g.get("output_id")) if g.get("output_id") else None
  )
  rev = (
      store.get("constitution_revisions", g.get("revision_id"))
      if g.get("revision_id")
      else None
  )
  if sample:
    sample = dict(sample)
    sample["group"] = sample.pop("grp", None)
    z.writestr(
        folder + "sample.json", json.dumps(sample, indent=2, ensure_ascii=False)
    )
  if output and output.get("a2ui"):
    z.writestr(
        folder + "output.a2ui.json",
        json.dumps(output["a2ui"], indent=2, ensure_ascii=False),
    )
    if output.get("judge"):
      z.writestr(
          folder + "judge.json",
          json.dumps(output["judge"], indent=2, ensure_ascii=False),
      )
  if rev:
    z.writestr(folder + "constitution_rev.md", rev["text_md"] or "")
  md = [
      f"# {'Do' if g['kind'] == 'do' else 'Don’t'}: {g['title']}",
      "",
      f"- Project: {project['name']} (`{project['id']}`)",
      f"- Renderer: `{g.get('renderer') or 'web'}`",
      f"- Fixable by constitution: **{g.get('fixable')}**",
      f"- Status: {g.get('status')}",
      f"- Tags: {', '.join(g.get('tags') or []) or '—'}",
      f"- Created by {g.get('created_by')} at {g.get('created_at')}",
  ]
  if rev:
    md.append(f"- Constitution revision: {rev['number']}")
  if output:
    md.append(
        f"- Viewport: {output.get('viewport')}; human verdict: "
        f"{output.get('human_verdict') or '—'}"
    )
  if g.get("escalation_note"):
    md += ["", "## Escalation note", "", g["escalation_note"]]
  md += ["", "## Description", "", g.get("body_md") or "", ""]
  for s in shots:
    md.append(f"![{s}]({s})")
  z.writestr(folder + "entry.md", "\n".join(md) + "\n")
  return {
      "id": g["id"],
      "kind": g["kind"],
      "title": g["title"],
      "fixable": g.get("fixable"),
      "status": g.get("status"),
      "folder": folder,
      "project": project["name"],
  }


def guidelines_zip(
    store: Store, project: dict[str, Any], guidelines: list[dict[str, Any]]
) -> bytes:
  buf = io.BytesIO()
  with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    index = [
        _guideline_entry(store, z, "entries/", g, project) for g in guidelines
    ]
    lines = [
        f"# Dos & Don'ts — {project['name']}",
        "",
        f"Exported {util.now_iso()}. {len(index)} entries.",
        "",
        "| Kind | Title | Fixable by constitution | Status | Folder |",
        "|---|---|---|---|---|",
    ]
    for e in index:
      lines.append(
          f"| {e['kind']} | {e['title']} | {e['fixable']} | "
          f"{e['status']} | [{e['folder']}]({e['folder']}entry.md) |"
      )
    z.writestr("README.md", "\n".join(lines) + "\n")
    cur = store.get("constitution_revisions", project["current_revision_id"])
    if cur:
      z.writestr("constitution.md", constitution_md(store, project, cur))
  return buf.getvalue()


BENCHMARK_README = """# Design Constitution benchmark bundle

Exported from the Design Constitution Builder. Each line of `cases.jsonl` is
one judged case: the synthetic input sample, the constitution revision that
guided generation, the generated A2UI v1.0 output, its screenshot, the human
verdict and (when present) the Do/Don't the designer catalogued.

Fields:
- case_id, project, constitution_ref (path to the markdown snapshot)
- catalog: base catalog id + disabled/custom components used for the run
- sample: {genre, group, title, intent, context, state, payload}
- a2ui_output: A2UI v1.0 message list
- viewport: {id, width, height}
- screenshot_ref: path to the PNG in this bundle (may be null)
- human_verdict (good|bad|mixed|null), human_note
- guideline: {kind, title, body_md, fixable, status, escalation_note} or null
- judge: automated judge result (advisory)
"""


def benchmark_zip(
    store: Store, guidelines: list[dict[str, Any]], project_ids: list[str]
) -> tuple[bytes, dict[str, int]]:
  """Cases from guidelines (with outputs) + reviewed outputs of projects."""
  from sim.generator import VIEWPORTS  # pylint: disable=g-import-not-at-top

  buf = io.BytesIO()
  cases: list[dict[str, Any]] = []
  seen_outputs: set[str] = set()
  cons_written: set[str] = set()
  counts = {"cases": 0, "guidelines": 0, "screenshots": 0}
  with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:

    def add_case(output: dict[str, Any] | None, g: dict[str, Any] | None):
      pid = (output or g)["project_id"]
      project = store.get("projects", pid) or {"name": pid, "settings": {}}
      rev_id = (output or {}).get("revision_id") or (g or {}).get("revision_id")
      rev = store.get("constitution_revisions", rev_id) if rev_id else None
      cref = None
      if rev:
        cref = f"constitutions/{pid}_rev{rev['number']}.md"
        if cref not in cons_written:
          z.writestr(cref, rev["text_md"] or "")
          cons_written.add(cref)
      sample = None
      sid = (output or {}).get("sample_id") or (g or {}).get("sample_id")
      if sid:
        s = store.get("samples", sid)
        if s:
          sample = {
              k: s.get(k)
              for k in (
                  "genre",
                  "title",
                  "intent",
                  "context",
                  "state",
                  "payload",
                  "size_class",
                  "edge_cases",
              )
          }
          sample["group"] = s.get("grp")
      shot_ref = None
      screenshots_by_renderer: dict[str, str] = {}
      if output:
        for rid, rinfo in (output.get("renders") or {}).items():
          sp = rinfo.get("screenshot_path") if isinstance(rinfo, dict) else None
          if sp and os.path.exists(sp):
            ref = f"screenshots/{output['id']}_{rid}.png"
            z.write(sp, ref)
            screenshots_by_renderer[rid] = ref
            counts["screenshots"] += 1
        if output.get("screenshot_path") and os.path.exists(
            output["screenshot_path"]
        ):
          shot_ref = f"screenshots/{output['id']}.png"
          if "web" not in screenshots_by_renderer:
            z.write(output["screenshot_path"], shot_ref)
            counts["screenshots"] += 1
          else:
            shot_ref = screenshots_by_renderer["web"]
        elif screenshots_by_renderer:
          shot_ref = next(iter(screenshots_by_renderer.values()))
      elif g:
        for sh in g.get("screenshots") or []:
          p = store.file_path("guidelines", sh.get("file") or "")
          if sh.get("file") and os.path.exists(p):
            shot_ref = f"screenshots/{sh['file']}"
            z.write(p, shot_ref)
            counts["screenshots"] += 1
            break
      run = store.get("runs", output["run_id"]) if output else None
      cat = (
          ((run or {}).get("config") or {}).get("catalog")
          or (project.get("settings") or {}).get("catalog")
          or {}
      )
      case = {
          "case_id": f"case_{len(cases) + 1:05d}",
          "project": {
              "id": pid,
              "name": project.get("name"),
              "owner_ldap": project.get("owner_ldap"),
          },
          "constitution_ref": cref,
          "constitution_revision": rev["number"] if rev else None,
          "catalog": {
              "base": "a2ui-basic-v1",
              "disabled_components": cat.get("disabled_components", []),
              "custom_components": list(
                  (cat.get("custom_components") or {}).keys()
              ),
          },
          "sample": sample,
          "a2ui_output": (output or {}).get("a2ui"),
          "viewport": VIEWPORTS.get((output or {}).get("viewport") or "phone"),
          "screenshot_ref": shot_ref,
          "screenshots_by_renderer": screenshots_by_renderer,
          "human_verdict": (output or {}).get("human_verdict"),
          "human_note": (output or {}).get("human_note"),
          "guideline": (
              {
                  k: g.get(k)
                  for k in (
                      "kind",
                      "title",
                      "body_md",
                      "renderer",
                      "fixable",
                      "status",
                      "escalation_note",
                      "tags",
                  )
              }
              if g
              else None
          ),
          "judge": (output or {}).get("judge"),
      }
      cases.append(case)

    for g in guidelines:
      o = store.get("outputs", g["output_id"]) if g.get("output_id") else None
      if o:
        seen_outputs.add(o["id"])
      add_case(o, g)
      counts["guidelines"] += 1
    for pid in project_ids:
      for o in store.query(
          "SELECT * FROM outputs WHERE project_id=? AND human_verdict IS NOT "
          "NULL ORDER BY created_at",
          [pid],
      ):
        if o["id"] not in seen_outputs:
          add_case(o, None)
    counts["cases"] = len(cases)
    z.writestr(
        "cases.jsonl",
        "\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n",
    )
    z.writestr("README.md", BENCHMARK_README)
  return buf.getvalue(), counts
