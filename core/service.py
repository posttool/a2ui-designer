"""Domain service: users, projects, revisions, chat, samples, guidelines.

Runs/outputs/jobs live in `sim/runs.py` (RunManager) and are reached via
`Service.runs`. Routes call these methods; they raise `ApiError` for 4xx.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import threading
import traceback
from typing import Any, Iterable
import urllib.parse

from core import dependencies as deps_mod, diffs, imagery as imagery_mod, settings as settings_mod, util
from core.a2ui import catalog as catalog_mod
from core.a2ui import custom_web
from core.storage import Store
from llm.gemini import Llm, LlmError
from sim import generator, imagery_gen, reviser, taxonomy


class ApiError(Exception):

  def __init__(self, status: int, message: str):
    super().__init__(message)
    self.status = status


PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_admins(path: str | None = None) -> set[str]:
  p = path or os.path.join(PKG, "admins.json")
  try:
    with open(p, encoding="utf-8") as fh:
      return {str(x).strip().lower() for x in json.load(fh)}
  except (OSError, ValueError):
    return set()


class Service:
  """All non-run domain operations."""

  def __init__(self, store: Store, llm: Llm, admins: Iterable[str] = ()):
    self.store = store
    self.llm = llm
    self.admins = {a.lower() for a in admins}
    self.runs = None  # set by RunManager
    self.components = None  # set by ComponentManager
    self._chat_locks: dict[str, threading.Lock] = {}

  # ------------------------------------------------------------- users
  def login(self, ldap: str) -> dict[str, Any]:
    ldap = (ldap or "").strip().lower().removesuffix("@google.com")
    if (
        not ldap
        or not ldap.replace("-", "").replace("_", "").replace(".", "").isalnum()
        or len(ldap) > 64
    ):
      raise ApiError(400, "Enter a valid LDAP username")
    now = util.now_iso()
    u = self.store.get("users", ldap)
    is_admin = 1 if ldap in self.admins else 0
    if u is None:
      self.store.insert(
          "users",
          {
              "ldap": ldap,
              "display_name": ldap,
              "is_admin": is_admin,
              "created_at": now,
              "last_seen_at": now,
          },
      )
    else:
      self.store.update(
          "users",
          ldap,
          {"last_seen_at": now, "is_admin": is_admin},
          key_col="ldap",
      )
    return self.user(ldap)

  def user(self, ldap: str | None) -> dict[str, Any] | None:
    u = self.store.get("users", ldap) if ldap else None
    if not u:
      return None
    return {
        "ldap": u["ldap"],
        "display_name": u["display_name"] or u["ldap"],
        "is_admin": bool(u["is_admin"]) or u["ldap"] in self.admins,
    }

  # ---------------------------------------------------------- projects
  def _project_row(
      self, pid: str, user: dict[str, Any], write: bool = False
  ) -> dict[str, Any]:
    p = self.store.get("projects", pid)
    if not p:
      raise ApiError(404, "Project not found")
    if p["owner_ldap"] != user["ldap"] and not user["is_admin"]:
      raise ApiError(403, "Not your project")
    del write
    return p

  def design_sources(self, pid: str) -> list[dict[str, Any]]:
    """Figma files / Stitch projects linked to a project.

    Collected from design references and custom components; one entry per
    Figma file key / Stitch project id: {kind, key, name, url, references,
    components}.
    """
    found: dict[tuple[str, str], dict[str, Any]] = {}

    def add(kind: str, src: dict[str, Any], field: str) -> None:
      url = str(src.get("url") or "")
      if kind == "figma":
        key = str(src.get("file_key") or "")
        if not key:
          m = re.search(r"figma\.com/(?:design|file)/([^/?#]+)", url)
          key = m.group(1) if m else ""
        if not key:
          return
        m = re.search(r"/(?:design|file)/[^/?#]+/([^/?#]+)", url)
        slug = urllib.parse.unquote(m.group(1)) if m else ""
        guess = re.sub(r"[-_]+", " ", slug).strip()
        url = f"https://www.figma.com/design/{key}"
      else:
        key = str(src.get("project_id") or "")
        if not key:
          m = re.search(r"projects/(\d+)", url)
          key = m.group(1) if m else ""
        if not key:
          return
        guess = ""
        url = f"https://stitch.withgoogle.com/projects/{key}"
      ent = found.setdefault(
          (kind, key),
          {
              "kind": kind,
              "key": key,
              "name": "",
              "url": url,
              "references": 0,
              "components": 0,
          },
      )
      ent[field] += 1
      name = str(src.get("file_name") or "").strip()
      if name:
        ent["name"] = name
      elif not ent["name"] and guess:
        ent["guess"] = guess

    for r in self.store.query(
        "SELECT kind, source_json FROM design_references WHERE project_id=?"
        " AND kind IN ('figma','stitch')",
        [pid],
    ):
      add(r["kind"], r.get("source") or {}, "references")
    for r in self.store.query(
        "SELECT source_json FROM custom_components WHERE project_id=? AND"
        " status!='archived'",
        [pid],
    ):
      src = r.get("source") or {}
      if src.get("kind") in ("figma", "stitch"):
        add(src["kind"], src, "components")
    out = []
    for ent in found.values():
      guess = ent.pop("guess", "")
      if not ent["name"]:
        ent["name"] = guess or (
            "Untitled file" if ent["kind"] == "figma" else "Untitled project"
        )
      out.append(ent)
    out.sort(key=lambda e: (e["kind"], e["name"].lower()))
    return out

  def format_project(self, p: dict[str, Any]) -> dict[str, Any]:
    pid = p["id"]
    rev = self.store.get("constitution_revisions", p["current_revision_id"])
    tok_rev = self.ensure_current_token_revision(pid, p.get("owner_ldap") or "")
    q = self.store.scalar
    stats = {
        "revisions": q(
            "SELECT COUNT(*) FROM constitution_revisions WHERE project_id=?",
            [pid],
        ),
        "token_revisions": q(
            "SELECT COUNT(*) FROM token_revisions WHERE project_id=?",
            [pid],
        ),
        "runs": q("SELECT COUNT(*) FROM runs WHERE project_id=?", [pid]),
        "dos": q(
            "SELECT COUNT(*) FROM guidelines WHERE project_id=? AND kind='do'",
            [pid],
        ),
        "donts": q(
            "SELECT COUNT(*) FROM guidelines WHERE project_id=? AND "
            "kind='dont'",
            [pid],
        ),
        "imagery_styles": q(
            "SELECT COUNT(*) FROM imagery_styles WHERE project_id=? AND "
            "enabled=1",
            [pid],
        ),
        "components": q(
            "SELECT COUNT(*) FROM custom_components WHERE project_id=? AND "
            "status!='archived'",
            [pid],
        ),
        "templates": q(
            "SELECT COUNT(*) FROM templates WHERE project_id=? AND "
            "status='active'",
            [pid],
        ),
        "last_run_at": q(
            "SELECT MAX(created_at) FROM runs WHERE project_id=?", [pid]
        ),
    }
    return {
        "id": pid,
        "design_sources": self.design_sources(pid),
        "owner_ldap": p["owner_ldap"],
        "name": p["name"],
        "description": p["description"] or "",
        "archived": bool(p["archived"]),
        "created_at": p["created_at"],
        "updated_at": p["updated_at"],
        "current_revision": (
            {
                "id": rev["id"],
                "number": rev["number"],
                "summary": rev["summary"],
                "created_at": rev["created_at"],
                "is_empty": not (rev["text_md"] or "").strip(),
                "ds_snapshot": rev.get("ds_snapshot"),
            }
            if rev
            else None
        ),
        "current_token_revision": (
            {
                "id": tok_rev["id"],
                "number": tok_rev["number"],
                "theme_hash": tok_rev["theme_hash"],
                "summary": tok_rev["summary"],
                "source": tok_rev["source"],
                "created_at": tok_rev["created_at"],
            }
            if tok_rev
            else None
        ),
        "settings": settings_mod.merged(p["settings"]),
        "stats": stats,
    }

  def list_projects(
      self, user: dict[str, Any], all_users: bool = False
  ) -> list[dict[str, Any]]:
    if all_users and user["is_admin"]:
      rows = self.store.query("SELECT * FROM projects ORDER BY updated_at DESC")
    else:
      rows = self.store.query(
          "SELECT * FROM projects WHERE owner_ldap=? ORDER BY updated_at DESC",
          [user["ldap"]],
      )
    return [self.format_project(p) for p in rows]

  def create_project(
      self,
      user: dict[str, Any],
      name: str,
      description: str = "",
      constitution_md: str = "",
  ) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
      raise ApiError(400, "Project name is required")
    pid = util.new_id("p")
    rid = util.new_id("r")
    now = util.now_iso()
    source = "import" if (constitution_md or "").strip() else "seed"
    self.store.insert(
        "constitution_revisions",
        {
            "id": rid,
            "project_id": pid,
            "number": 0,
            "text_md": constitution_md or "",
            "summary": (
                "Imported constitution"
                if source == "import"
                else "Empty constitution"
            ),
            "source": source,
            "parent_id": None,
            "chat_message_id": None,
            "author_ldap": user["ldap"],
            "created_at": now,
        },
    )
    self.store.insert(
        "projects",
        {
            "id": pid,
            "owner_ldap": user["ldap"],
            "name": name[:120],
            "description": (description or "")[:2000],
            "current_revision_id": rid,
            "settings": settings_mod.merged(None),
            "archived": 0,
            "created_at": now,
            "updated_at": now,
        },
    )
    tok_rev = self.ensure_current_token_revision(
        pid, user["ldap"], source="seed", summary="Initial tokens"
    )
    p_row = self.store.get("projects", pid)
    ds_snap = deps_mod.build_ds_snapshot(self.store, self, p_row, tok_rev)
    self.store.update("constitution_revisions", rid, {"ds_snapshot": ds_snap})
    return self.format_project(self.store.get("projects", pid))

  def get_project(self, user: dict[str, Any], pid: str) -> dict[str, Any]:
    return self.format_project(self._project_row(pid, user))

  def update_project(
      self, user: dict[str, Any], pid: str, fields: dict[str, Any]
  ) -> dict[str, Any]:
    self._project_row(pid, user, write=True)
    upd: dict[str, Any] = {}
    if "name" in fields and str(fields["name"]).strip():
      upd["name"] = str(fields["name"]).strip()[:120]
    if "description" in fields:
      upd["description"] = str(fields["description"] or "")[:2000]
    if "archived" in fields:
      upd["archived"] = 1 if fields["archived"] else 0
    upd["updated_at"] = util.now_iso()
    self.store.update("projects", pid, upd)
    return self.get_project(user, pid)

  def delete_project(self, user: dict[str, Any], pid: str) -> None:
    self._project_row(pid, user, write=True)
    for table in (
        "constitution_revisions",
        "token_revisions",
        "chat_messages",
        "runs",
        "outputs",
        "guidelines",
        "imagery_styles",
        "custom_components",
        "templates",
        "design_references",
        "import_extractions",
    ):
      self.store.execute(f"DELETE FROM {table} WHERE project_id=?", [pid])
    self.store.delete("projects", pid)

  def touch(self, pid: str) -> None:
    self.store.update("projects", pid, {"updated_at": util.now_iso()})

  # -------------------------------------------------- token revisions
  def ensure_current_token_revision(
      self,
      pid: str,
      author_ldap: str = "",
      *,
      source: str = "seed",
      summary: str = "",
  ) -> dict[str, Any] | None:
    p = self.store.get("projects", pid)
    if not p:
      return None
    cur_theme = settings_mod.merged(p.get("settings"))["theme"]
    cur_hash = settings_mod.compute_theme_hash(cur_theme)
    rows = self.store.query(
        "SELECT * FROM token_revisions WHERE project_id=? ORDER BY number DESC"
        " LIMIT 1",
        [pid],
    )
    now = util.now_iso()
    ldap = author_ldap or p.get("owner_ldap") or ""
    if not rows:
      return self.store.insert(
          "token_revisions",
          {
              "id": util.new_id("tr"),
              "project_id": pid,
              "number": 1,
              "theme_hash": cur_hash,
              "theme": cur_theme,
              "diff": {
                  "changes": [],
                  "custom_css_changed": False,
                  "changed_css_vars": [],
                  "summary": "Initial tokens",
              },
              "summary": (summary or "Initial tokens")[:300],
              "source": source or "seed",
              "author_ldap": ldap,
              "created_at": now,
          },
      )
    latest = rows[0]
    if latest.get("theme_hash") == cur_hash:
      return latest
    diff = settings_mod.compute_theme_diff(latest.get("theme"), cur_theme)
    next_num = int(latest.get("number") or 1) + 1
    return self.store.insert(
        "token_revisions",
        {
            "id": util.new_id("tr"),
            "project_id": pid,
            "number": next_num,
            "theme_hash": cur_hash,
            "theme": cur_theme,
            "diff": diff,
            "summary": (
                summary or diff.get("summary") or f"Token revision {next_num}"
            )[:300],
            "source": source or "manual",
            "author_ldap": ldap,
            "created_at": now,
        },
    )

  def list_token_revisions(
      self, user: dict[str, Any], pid: str
  ) -> list[dict[str, Any]]:
    p = self._project_row(pid, user)
    self.ensure_current_token_revision(pid, p.get("owner_ldap") or "")
    return self.store.query(
        "SELECT * FROM token_revisions WHERE project_id=? ORDER BY number DESC",
        [pid],
    )

  def revert_token_revision(
      self, user: dict[str, Any], pid: str, trid: str
  ) -> dict[str, Any]:
    p = self._project_row(pid, user, write=True)
    tr = self.store.get("token_revisions", trid)
    if not tr or tr.get("project_id") != pid:
      raise ApiError(404, "Token revision not found")
    s = settings_mod.merged(p.get("settings"))
    s["theme"] = tr.get("theme") or {}
    merged = self.put_settings(
        user,
        pid,
        s,
        source="revert",
        summary=f"Revert to token revision T{tr['number']}",
    )
    cur_tr = self.ensure_current_token_revision(pid, user["ldap"])
    return {"settings": merged, "token_revision": cur_tr}

  def get_dependencies(
      self,
      user: dict[str, Any],
      pid: str,
      *,
      revision_id: str | None = None,
      text_md: str | None = None,
  ) -> dict[str, Any]:
    p = self._project_row(pid, user)
    self.ensure_current_token_revision(pid, p.get("owner_ldap") or "")
    rev = (
        self.get_revision(user, pid, revision_id)
        if revision_id
        else self.current_revision(pid)
    )
    return deps_mod.analyze_project_dependencies(
        self.store, self, p, rev, text_md=text_md
    )

  # ---------------------------------------------------------- settings
  def get_settings(self, user: dict[str, Any], pid: str) -> dict[str, Any]:
    return settings_mod.merged(self._project_row(pid, user)["settings"])

  def put_settings(
      self,
      user: dict[str, Any],
      pid: str,
      new: dict[str, Any],
      *,
      source: str = "manual",
      summary: str = "",
  ) -> dict[str, Any]:
    self._project_row(pid, user, write=True)
    if not isinstance(new, dict):
      raise ApiError(400, "settings must be an object")
    prev_tok = self.ensure_current_token_revision(pid, user["ldap"])
    merged = settings_mod.merged(new)
    vps = [v for v in merged.get("viewports") or [] if v in generator.VIEWPORTS]
    merged["viewports"] = vps or ["phone"]
    customs = merged["catalog"].get("custom_components") or {}
    if not isinstance(customs, dict):
      raise ApiError(400, "custom_components must be an object of schemas")
    self.store.update(
        "projects", pid, {"settings": merged, "updated_at": util.now_iso()}
    )
    new_hash = settings_mod.compute_theme_hash(merged["theme"])
    if prev_tok and new_hash != prev_tok.get("theme_hash"):
      diff = settings_mod.compute_theme_diff(
          prev_tok.get("theme"), merged["theme"]
      )
      self.ensure_current_token_revision(
          pid,
          user["ldap"],
          source=source or "manual",
          summary=summary or diff.get("summary") or "",
      )
      if self.components is not None:
        changed_vars = set(diff.get("changed_css_vars") or [])
        css_changed = bool(diff.get("custom_css_changed"))
        for c in self.store.query(
            "SELECT id, token_refs_json FROM custom_components WHERE"
            " project_id=? AND status!='archived'",
            [pid],
        ):
          trefs = set(c.get("token_refs") or [])
          if css_changed or (trefs & changed_vars):
            self.components.queue_preview(c["id"])
    return merged

  @staticmethod
  def default_catalog_id(project_row: dict[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (project_row.get("name") or "").lower())
    slug = slug.strip("-")[:48] or str(project_row.get("id") or "project")
    return f"https://dcb.local/catalogs/{slug}"

  def catalog_id_for(self, project_row: dict[str, Any]) -> str:
    s = settings_mod.merged(project_row.get("settings"))
    cid = str(s["catalog"].get("catalog_id") or "").strip()
    return cid or self.default_catalog_id(project_row)

  def custom_defs(self, pid: str) -> dict[str, dict[str, Any]]:
    """Active custom component Definitions for a project (name → def)."""
    rows = self.store.query(
        "SELECT name, definition_json FROM custom_components WHERE"
        " project_id=? AND status='active' ORDER BY name",
        [pid],
    )
    out = {}
    for r in rows:
      d = r.get("definition")
      if isinstance(d, dict):
        out[r["name"]] = d
    return out

  def migrate_legacy_catalog(self, project_row: dict[str, Any]) -> None:
    """Moves settings.catalog custom/proposed components into the table."""
    s = project_row.get("settings") or {}
    cat = s.get("catalog") or {}
    legacy = cat.get("custom_components") or {}
    proposed = cat.get("proposed_components") or []
    if isinstance(proposed, dict):
      proposed = [{"name": k, **(v or {})} for k, v in proposed.items()]
    if not legacy and not proposed:
      return
    pid = project_row["id"]
    existing = {
        r["name"]
        for r in self.store.query(
            "SELECT name FROM custom_components WHERE project_id=?", [pid]
        )
    }
    now = util.now_iso()
    basic = catalog_mod.basic_names()

    def add(name, defn, status, source, notes=""):
      if not name or name in existing or name in basic:
        return
      existing.add(name)
      self.store.insert(
          "custom_components",
          {
              "id": util.new_id("cc"),
              "project_id": pid,
              "name": name,
              "status": status,
              "classification": "custom",
              "definition": defn,
              "source": source,
              "fidelity": None,
              "job": None,
              "issues": [],
              "warnings": [],
              "token_refs": [],
              "token_suggestions": [],
              "basic_equivalent": None,
              "preview_file": None,
              "reference_file": None,
              "notes": notes,
              "author_ldap": project_row.get("owner_ldap"),
              "created_at": now,
              "updated_at": now,
          },
      )

    for name, schema in legacy.items():
      if not isinstance(schema, dict):
        continue
      if custom_web.is_definition(schema):
        defn = custom_web.normalize_definition(dict(schema, name=name))
      else:
        defn = custom_web.normalize_definition({
            "name": name,
            "description": schema.get("description") or "",
            "props": schema,
        })
      add(
          name,
          defn,
          "active",
          {"kind": "legacy"},
          "Migrated from Settings; needs a web implementation.",
      )
    for pc in proposed:
      if not isinstance(pc, dict):
        continue
      name = re.sub(r"[^A-Za-z0-9]", "", str(pc.get("name") or ""))[:60]
      defn = custom_web.normalize_definition({
          "name": name,
          "description": pc.get("purpose") or "",
          "props": pc.get("props_schema") or {},
      })
      add(
          name,
          defn,
          "draft",
          {
              "kind": "proposal",
              "purpose": pc.get("purpose") or "",
              "maps_to_basic": pc.get("maps_to_basic") or [],
          },
          "Proposed by design-reference analysis; build it to add a web"
          " implementation.",
      )
    cat = dict(cat)
    cat["custom_components"] = {}
    cat["proposed_components"] = []
    s = dict(s, catalog=cat)
    project_row["settings"] = s
    self.store.update("projects", pid, {"settings": s})

  def catalog_for(self, project_row: dict[str, Any]) -> catalog_mod.Catalog:
    """Live project catalog (settings + active custom components).

    Rows without an `id` (run snapshots: {"settings": {...}}) use only the
    snapshot in settings.catalog (which carries Definitions + catalog_id).
    """
    s = settings_mod.merged(project_row["settings"])
    if not project_row.get("id"):
      return catalog_mod.build_catalog(s["catalog"])
    self.migrate_legacy_catalog(project_row)
    s = settings_mod.merged(project_row["settings"])
    cfg = dict(s["catalog"], catalog_id=self.catalog_id_for(project_row))
    return catalog_mod.build_catalog(cfg, self.custom_defs(project_row["id"]))

  def catalog_snapshot(self, project_row: dict[str, Any]) -> dict[str, Any]:
    """settings.catalog-shaped snapshot stored in run configs."""
    cat = self.catalog_for(project_row)
    s = settings_mod.merged(project_row["settings"])
    return dict(
        s["catalog"],
        catalog_id=cat.catalog_id,
        custom_components=dict(cat.custom_defs),
        proposed_components=[],
    )

  def get_catalog(self, user: dict[str, Any], pid: str) -> dict[str, Any]:
    cat = self.catalog_for(self._project_row(pid, user))
    ids = {
        r["name"]: r["id"]
        for r in self.store.query(
            "SELECT id, name FROM custom_components WHERE project_id=? AND"
            " status='active'",
            [pid],
        )
    }
    comps = []
    for n, c in cat.components.items():
      d = c.to_dict(enabled=n not in cat.disabled)
      if c.custom:
        d["component_id"] = ids.get(n)
      comps.append(d)
    return {
        "catalog_id": cat.catalog_id,
        "components": comps,
        "functions": list(cat.functions),
    }

  # --------------------------------------------------------- revisions
  def list_revisions(
      self, user: dict[str, Any], pid: str
  ) -> list[dict[str, Any]]:
    self._project_row(pid, user)
    rows = self.store.query(
        "SELECT * FROM constitution_revisions WHERE project_id=? ORDER BY "
        "number DESC",
        [pid],
    )
    for r in rows:
      r["chars"] = len(r.pop("text_md") or "")
    return rows

  def get_revision(
      self, user: dict[str, Any], pid: str, rid: str
  ) -> dict[str, Any]:
    self._project_row(pid, user)
    r = self.store.get("constitution_revisions", rid)
    if not r or r["project_id"] != pid:
      raise ApiError(404, "Revision not found")
    return r

  def current_revision(self, pid: str) -> dict[str, Any]:
    p = self.store.get("projects", pid)
    return self.store.get("constitution_revisions", p["current_revision_id"])

  def add_revision(
      self,
      user: dict[str, Any],
      pid: str,
      text_md: str,
      summary: str,
      source: str = "manual",
      chat_message_id: str | None = None,
      bind_hint: dict[str, str] | None = None,
  ) -> dict[str, Any]:
    p = self._project_row(pid, user, write=True)
    cur = self.current_revision(pid)
    number = (
        self.store.scalar(
            "SELECT MAX(number) FROM constitution_revisions WHERE project_id=?",
            [pid],
        )
        or 0
    ) + 1
    tok_rev = self.ensure_current_token_revision(pid, user["ldap"])
    ds_snap = deps_mod.build_ds_snapshot(self.store, self, p, tok_rev)
    rid = util.new_id("r")
    rev = self.store.insert(
        "constitution_revisions",
        {
            "id": rid,
            "project_id": pid,
            "number": number,
            "text_md": text_md or "",
            "summary": (summary or "").strip()[:300] or f"Revision {number}",
            "source": source,
            "parent_id": cur["id"] if cur else None,
            "chat_message_id": chat_message_id,
            "author_ldap": user["ldap"],
            "created_at": util.now_iso(),
            "ds_snapshot": ds_snap,
        },
    )
    self.store.update(
        "projects",
        pid,
        {"current_revision_id": rid, "updated_at": util.now_iso()},
    )
    ledger = getattr(self, "teach_ledger", None)
    if ledger is not None and (text_md or "").strip():
      try:
        ledger.bind_revision(rev, bind_hint, created_by=user["ldap"])
      except Exception:  # pylint: disable=broad-except
        traceback.print_exc()
    return rev

  def revert(self, user: dict[str, Any], pid: str, rid: str) -> dict[str, Any]:
    r = self.get_revision(user, pid, rid)
    return self.add_revision(
        user, pid, r["text_md"], f"Revert to revision {r['number']}", "revert"
    )

  def diff_revisions(
      self, user: dict[str, Any], pid: str, a: str, b: str
  ) -> dict[str, Any]:
    ra = self.get_revision(user, pid, a)
    rb = self.get_revision(user, pid, b)
    return diffs.diff(
        ra["text_md"],
        rb["text_md"],
        f"rev {ra['number']}",
        f"rev {rb['number']}",
    )

  # -------------------------------------------------------------- chat
  def list_chat(self, user: dict[str, Any], pid: str) -> list[dict[str, Any]]:
    self._project_row(pid, user)
    return [
        self._fmt_chat(m)
        for m in self.store.query(
            "SELECT * FROM chat_messages WHERE project_id=? ORDER BY"
            " created_at, rowid",
            [pid],
        )
    ]

  @staticmethod
  def _fmt_chat(m: dict[str, Any]) -> dict[str, Any]:
    m = dict(m)
    m["context"] = m.get("context") or {"output_ids": [], "guideline_ids": []}
    m["context"].setdefault("reference_ids", [])
    m["context"].setdefault("selected_component", None)
    m["context"].setdefault("attached_images", [])
    m["out_of_scope"] = m.get("out_of_scope") or []
    m["proposal_status"] = m.get("proposal_status") or "none"
    return m

  def post_chat(
      self,
      user: dict[str, Any],
      pid: str,
      text: str,
      output_ids: list[str] | None = None,
      guideline_ids: list[str] | None = None,
      draft_md: str | None = None,
      *,
      selected_component: dict[str, Any] | None = None,
      attached_images: list[dict[str, Any]] | None = None,
  ) -> dict[str, Any]:
    p = self._project_row(pid, user)
    text = (text or "").strip()
    if not text:
      raise ApiError(400, "Message is empty")
    output_ids = [o for o in output_ids or [] if isinstance(o, str)][:6]
    guideline_ids = [g for g in guideline_ids or [] if isinstance(g, str)]
    clean_sc = None
    if isinstance(selected_component, dict) and (
        selected_component.get("id") or selected_component.get("component")
    ):
      clean_sc = {
          "id": str(selected_component.get("id") or ""),
          "component": str(selected_component.get("component") or ""),
          "output_id": str(selected_component.get("output_id") or ""),
          "props": (
              selected_component.get("props")
              if isinstance(selected_component.get("props"), dict)
              else None
          ),
      }
      if clean_sc["output_id"] and clean_sc["output_id"] not in output_ids:
        output_ids = [clean_sc["output_id"]] + output_ids[:5]
    saved_chat_refs = imagery_mod.save_reference_images(
        self.store, f"{pid}_chat", attached_images or []
    )
    loaded_chat_refs = imagery_mod.load_reference_images(
        self.store, saved_chat_refs
    )
    now = util.now_iso()
    history = self.list_chat(user, pid)
    umsg = self.store.insert(
        "chat_messages",
        {
            "id": util.new_id("m"),
            "project_id": pid,
            "role": "user",
            "text_md": text,
            "context": {
                "output_ids": output_ids,
                "guideline_ids": guideline_ids,
                "selected_component": clean_sc,
                "attached_images": saved_chat_refs,
            },
            "proposal": None,
            "out_of_scope": [],
            "proposal_status": "none",
            "accepted_revision_id": None,
            "author_ldap": user["ldap"],
            "created_at": now,
        },
    )
    cur = self.current_revision(pid)
    cons = draft_md if draft_md is not None else (cur["text_md"] or "")
    outs = []
    for oid in output_ids:
      o = self.store.get("outputs", oid)
      if not o or o["project_id"] != pid:
        continue
      sample = self.store.get("samples", o["sample_id"])
      png = None
      if o.get("screenshot_path") and os.path.exists(o["screenshot_path"]):
        thumb = o.get("thumb_path") or o["screenshot_path"]
        with open(
            thumb if os.path.exists(thumb) else o["screenshot_path"], "rb"
        ) as fh:
          png = fh.read()
      outs.append((o, sample, png))
    gls = [
        g
        for g in (self.store.get("guidelines", gid) for gid in guideline_ids)
        if g and g["project_id"] == pid
    ]
    cat = self.catalog_for(p)
    cat_text = ", ".join(cat.enabled_components())
    s = settings_mod.merged(p["settings"])
    styles = imagery_mod.list_styles(self.store, pid)
    tpls = self.store.query(
        "SELECT name, description, when_to_use, structure_md, components_json"
        " FROM templates WHERE project_id=? AND status='active' ORDER BY"
        " created_at",
        [pid],
    )
    try:
      res = reviser.revise(
          self.llm,
          constitution_md=cons,
          catalog_text=cat_text,
          history=history,
          text=text,
          outputs=outs,
          guidelines=gls,
          theme=s["theme"],
          imagery_styles=styles,
          templates=tpls,
          attached_images=loaded_chat_refs,
          selected_component=clean_sc,
          model=s["generator"]["model"],
      )
    except LlmError as e:
      res = {
          "reply_md": (
              f"_The model is unavailable right now ({e}). Try "
              "again in a moment._"
          ),
          "proposal": None,
          "out_of_scope": [],
      }
    if (
        res["proposal"]
        and draft_md is not None
        and res["proposal"].get("constitution_md")
    ):
      res["proposal"]["diff"] = diffs.diff(
          cons, res["proposal"]["constitution_md"], "draft", "proposed"
      )
    if res["proposal"] and saved_chat_refs:
      res["proposal"]["attached_reference_images"] = saved_chat_refs
    amsg = self.store.insert(
        "chat_messages",
        {
            "id": util.new_id("m"),
            "project_id": pid,
            "role": "assistant",
            "text_md": res["reply_md"],
            "context": {
                "output_ids": [],
                "guideline_ids": [],
                "attached_images": saved_chat_refs,
            },
            "proposal": res["proposal"],
            "out_of_scope": res["out_of_scope"],
            "proposal_status": "pending" if res["proposal"] else "none",
            "accepted_revision_id": None,
            "author_ldap": "assistant",
            "created_at": util.now_iso(),
        },
    )
    self.touch(pid)
    return {
        "user_message": self._fmt_chat(umsg),
        "assistant_message": self._fmt_chat(amsg),
    }

  def _chat_msg(self, pid: str, mid: str) -> dict[str, Any]:
    m = self.store.get("chat_messages", mid)
    if not m or m["project_id"] != pid:
      raise ApiError(404, "Message not found")
    return m

  def accept_chat(
      self,
      user: dict[str, Any],
      pid: str,
      mid: str,
      constitution_md: str | None = None,
      apply_theme: bool = True,
      apply_catalog: bool = True,
  ) -> dict[str, Any]:
    p = self._project_row(pid, user, write=True)
    m = self._chat_msg(pid, mid)
    prop = m.get("proposal")
    if not prop:
      raise ApiError(400, "This message has no proposal")

    s = settings_mod.merged(p["settings"])
    applied_theme_changes: list[dict[str, Any]] = []
    raw_tc = prop.get("raw_theme_changes") or prop.get("theme_changes") or []
    raw_css = prop.get("custom_css")
    if apply_theme and (raw_tc or raw_css):
      # Convert preview entries `{token, after, ...}` if `raw_theme_changes` wasn't stored
      normalized_tc = []
      for item in raw_tc:
        if isinstance(item, dict):
          if "value" in item:
            normalized_tc.append(item)
          elif "after" in item:
            normalized_tc.append({
                "token": item.get("token"),
                "value": item.get("after"),
                "is_custom": item.get("is_custom", False),
                "maps_to": item.get("maps_to", ""),
                "reason": item.get("reason", ""),
            })
      updated_theme, applied_theme_changes = settings_mod.apply_theme_changes(
          s["theme"], normalized_tc, raw_css if raw_css else None
      )
      s["theme"] = updated_theme
      s = self.put_settings(
          user,
          pid,
          s,
          source="chat",
          summary=str(prop.get("change_summary") or "Applied tokens from chat"),
      )

    catalog_applied: dict[str, Any] = {
        "enabled": [],
        "disabled": [],
        "proposed_components": [],
    }
    cc = prop.get("catalog_changes") or {}
    if apply_catalog and any(
        cc.get(k) for k in ("enable", "disable", "proposed_components")
    ):
      cat = dict(s["catalog"])
      dis = list(cat.get("disabled_components") or [])
      for n in cc.get("enable") or []:
        if n in dis:
          dis.remove(n)
          catalog_applied["enabled"].append(n)
      for n in cc.get("disable") or []:
        if n not in dis:
          dis.append(n)
          catalog_applied["disabled"].append(n)
      cat["disabled_components"] = dis
      s["catalog"] = cat
      s = self.put_settings(user, pid, s)
      # Proposed components become draft custom components (Design system
      # page) that the designer builds into web implementations.
      have = {
          r["name"]
          for r in self.store.query(
              "SELECT name FROM custom_components WHERE project_id=?", [pid]
          )
      } | set(catalog_mod.basic_names())
      ref_ids = (m.get("context") or {}).get("reference_ids") or []
      now = util.now_iso()
      for pc in cc.get("proposed_components") or []:
        name = re.sub(r"[^A-Za-z0-9]", "", str(pc.get("name") or ""))[:60]
        if not name or name in have:
          continue
        have.add(name)
        defn = custom_web.normalize_definition({
            "name": name,
            "description": pc.get("purpose") or "",
            "props": pc.get("props_schema") or {},
        })
        self.store.insert(
            "custom_components",
            {
                "id": util.new_id("cc"),
                "project_id": pid,
                "name": name,
                "status": "draft",
                "classification": "custom",
                "definition": defn,
                "source": {
                    "kind": "proposal",
                    "chat_message_id": mid,
                    "reference_ids": ref_ids,
                    "purpose": pc.get("purpose") or "",
                    "maps_to_basic": pc.get("maps_to_basic") or [],
                },
                "fidelity": None,
                "job": None,
                "issues": ["web template is empty"],
                "warnings": [],
                "token_refs": [],
                "token_suggestions": [],
                "basic_equivalent": None,
                "preview_file": None,
                "reference_file": None,
                "notes": (
                    "Proposed by design-reference analysis; build it to"
                    " add a web implementation."
                ),
                "author_ldap": user["ldap"],
                "created_at": now,
                "updated_at": now,
            },
        )
        catalog_applied["proposed_components"].append(name)

    has_cons_change = prop.get(
        "has_constitution_change", bool(prop.get("constitution_md"))
    )
    imagery_applied: list[dict[str, Any]] = []
    raw_imagery = prop.get("imagery_changes") or []
    if raw_imagery:
      attached_refs = (
          prop.get("attached_reference_images")
          or (m.get("context") or {}).get("attached_images")
          or []
      )
      imagery_applied = imagery_mod.apply_imagery_changes(
          self.store,
          pid,
          user["ldap"],
          raw_imagery,
          attached_refs=attached_refs,
      )
    text = (
        constitution_md
        if constitution_md is not None
        else (prop.get("constitution_md") or "")
    )
    cur = self.current_revision(pid)
    if has_cons_change or (
        constitution_md is not None
        and constitution_md.strip() != (cur["text_md"] or "").strip()
    ):
      summary = prop.get("change_summary") or "Revision from chat"
      if constitution_md and constitution_md != prop.get("constitution_md"):
        summary += " (edited)"
      rev = self.add_revision(user, pid, text, summary, "chat", mid)
    else:
      rev = cur

    self.store.update(
        "chat_messages",
        mid,
        {"proposal_status": "accepted", "accepted_revision_id": rev["id"]},
    )
    return {
        "revision": rev,
        "settings": s,
        "theme_changes_applied": applied_theme_changes,
        "catalog_changes_applied": catalog_applied,
        "imagery_changes_applied": imagery_applied,
        "has_constitution_change": bool(rev["id"] != cur["id"]),
    }

  def discard_chat(
      self, user: dict[str, Any], pid: str, mid: str
  ) -> dict[str, Any]:
    self._project_row(pid, user, write=True)
    self._chat_msg(pid, mid)
    return self._fmt_chat(
        self.store.update(
            "chat_messages", mid, {"proposal_status": "discarded"}
        )
    )

  # ------------------------------------------------- suggestions/scope
  def suggestions(self, user: dict[str, Any], pid: str) -> list[dict[str, Any]]:
    p = self._project_row(pid, user)
    out = reviser.starter_suggestions()
    rows = self.store.query(
        "SELECT id, judge_json FROM outputs WHERE project_id=? AND judge_json "
        "IS NOT NULL ORDER BY created_at DESC LIMIT 200",
        [pid],
    )
    raw = []
    cur = p["current_revision_id"]
    latest_rev_rows = {
        r["id"]
        for r in self.store.query(
            "SELECT id FROM outputs WHERE project_id=? AND revision_id=?",
            [pid, cur],
        )
    }
    for r in rows:
      if latest_rev_rows and r["id"] not in latest_rev_rows:
        continue
      for s in (r.get("judge") or {}).get(
          "suggested_constitution_changes"
      ) or []:
        if isinstance(s, str) and s.strip():
          raw.append((r["id"], s.strip()))
    try:
      out += reviser.group_judge_suggestions(self.llm, raw)
    except LlmError:
      out += reviser.group_judge_suggestions(None, raw)
    for g in self.store.query(
        "SELECT * FROM guidelines WHERE project_id=? AND kind='dont' AND "
        "fixable='yes' AND status='open' ORDER BY created_at DESC",
        [pid],
    ):
      out.append({
          "id": f"guideline:{g['id']}",
          "source": "guideline",
          "title": g["title"],
          "body": (g["body_md"] or "")[:600],
          "ref_ids": [g["id"]],
      })
    return out

  def scope_check(
      self, user: dict[str, Any], pid: str, text_md: str | None
  ) -> dict[str, Any]:
    p = self._project_row(pid, user)
    text = (
        text_md
        if text_md is not None
        else self.current_revision(pid)["text_md"]
    )
    try:
      return reviser.scope_check(
          self.llm,
          text or "",
          settings_mod.merged(p["settings"])["generator"]["model"],
      )
    except LlmError as e:
      raise ApiError(503, f"Model unavailable: {e}") from e

  # ----------------------------------------------------------- samples
  @staticmethod
  def fmt_sample(s: dict[str, Any], full: bool = True) -> dict[str, Any]:
    s = dict(s)
    s["group"] = s.pop("grp", None)
    if not full:
      s.pop("payload", None)
    return s

  def list_samples(
      self, genre: str = "", group: str = "", q: str = "", set_id: str = ""
  ) -> list[dict[str, Any]]:
    sql = "SELECT * FROM samples WHERE 1=1"
    args: list[Any] = []
    if genre:
      sql += " AND genre=?"
      args.append(genre)
    if group:
      sql += " AND grp=?"
      args.append(group)
    if q:
      sql += " AND (title LIKE ? OR intent LIKE ? OR tags_json LIKE ?)"
      args += [f"%{q}%"] * 3
    sql += " ORDER BY created_at DESC, rowid DESC"
    rows = self.store.query(sql, args)
    if set_id:
      st = self.store.get("sample_sets", set_id)
      ids = (st or {}).get("sample_ids") or []
      order = {sid: i for i, sid in enumerate(ids)}
      rows = sorted(
          [r for r in rows if r["id"] in order], key=lambda r: order[r["id"]]
      )
    return [self.fmt_sample(r, full=False) for r in rows]

  def get_sample(self, sid: str) -> dict[str, Any]:
    s = self.store.get("samples", sid)
    if not s:
      raise ApiError(404, "Sample not found")
    return self.fmt_sample(s)

  # -------------------------------------------------------- categories
  def genres(self) -> dict[str, Any]:
    return taxonomy.groups_api(self.store)

  def resolve_category(
      self, user: dict[str, Any], fields: dict[str, Any]
  ) -> tuple[str, str]:
    """(group id, genre id) for a sample, creating categories when new.

    Accepts an existing `genre` id, or labels (`group_label`/`category_label`
    + `genre_label`/`subcategory_label`), or an unknown `genre` string, which
    becomes a new subcategory. Nothing given -> "Uncategorized".
    """
    known = taxonomy.all_genres(self.store)
    genre = str(fields.get("genre") or "").strip()
    if genre in known:
      return known[genre]["group"], genre
    groups = {g["id"]: g for g in taxonomy.groups_api(self.store)["groups"]}
    group = str(fields.get("group") or "").strip()
    group_label = str(
        fields.get("group_label") or fields.get("category_label") or ""
    ).strip()
    if not group_label and group:
      group_label = groups[group]["label"] if group in groups else group
    genre_label = str(
        fields.get("genre_label")
        or fields.get("subcategory_label")
        or genre.replace("_", " ")
    ).strip()
    try:
      return taxonomy.ensure_path(
          self.store,
          user["ldap"],
          group_label,
          genre_label,
          str(
              fields.get("genre_description")
              or fields.get("subcategory_description")
              or ""
          ),
      )
    except ValueError as e:
      raise ApiError(400, str(e)) from e

  def create_category(
      self, user: dict[str, Any], fields: dict[str, Any]
  ) -> dict[str, Any]:
    label = str(fields.get("label") or "").strip()
    desc = str(fields.get("description") or "")
    try:
      if fields.get("kind") == "group" or not fields.get("parent_id"):
        cid = taxonomy.ensure_group(self.store, user["ldap"], label, desc)
        return {"id": cid, "kind": "group"}
      cid = taxonomy.ensure_genre(
          self.store, user["ldap"], fields["parent_id"], label, desc
      )
      return {"id": cid, "kind": "genre", "parent_id": fields["parent_id"]}
    except ValueError as e:
      raise ApiError(400, str(e)) from e

  def _custom_category(self, user: dict[str, Any], cid: str) -> dict[str, Any]:
    c = self.store.get("categories", cid)
    if not c:
      builtin = cid in taxonomy.GENRES or any(
          g["id"] == cid for g in taxonomy.GROUPS
      )
      raise ApiError(
          400 if builtin else 404,
          "Built-in categories can't be changed"
          if builtin
          else "Category not found",
      )
    if c["owner_ldap"] != user["ldap"] and not user["is_admin"]:
      raise ApiError(403, "Only the creator can change this category")
    return c

  def update_category(
      self, user: dict[str, Any], cid: str, fields: dict[str, Any]
  ) -> dict[str, Any]:
    c = self._custom_category(user, cid)
    upd = {}
    if "label" in fields:
      label = " ".join(str(fields["label"] or "").split())[:80]
      if not label:
        raise ApiError(400, "Name is required")
      upd["label"] = label
    if "description" in fields:
      upd["description"] = str(fields["description"] or "")[:600]
    if "parent_id" in fields and c["kind"] == "genre":
      groups = {g["id"] for g in taxonomy.groups_api(self.store)["groups"]}
      if fields["parent_id"] not in groups:
        raise ApiError(400, "Unknown parent category")
      upd["parent_id"] = fields["parent_id"]
      self.store.execute(
          "UPDATE samples SET grp=? WHERE genre=?", [fields["parent_id"], cid]
      )
    self.store.update("categories", cid, upd)
    return self.store.get("categories", cid)

  def delete_category(self, user: dict[str, Any], cid: str) -> None:
    c = self._custom_category(user, cid)
    if c["kind"] == "group":
      n = self.store.scalar(
          "SELECT COUNT(*) FROM categories WHERE parent_id=?", [cid]
      )
      if n:
        raise ApiError(409, "Delete or move its subcategories first")
    else:
      n = self.store.scalar("SELECT COUNT(*) FROM samples WHERE genre=?", [cid])
      if n:
        raise ApiError(
            409, f"{n} sample(s) use this subcategory — move them first"
        )
    self.store.delete("categories", cid)

  # ----------------------------------------------------------- samples
  def create_sample(
      self, user: dict[str, Any], fields: dict[str, Any], source: str = "manual"
  ) -> dict[str, Any]:
    if "payload" not in fields:
      raise ApiError(400, "payload is required")
    grp, genre = self.resolve_category(user, fields)
    sid = util.new_id("s")
    self.store.insert(
        "samples",
        {
            "id": sid,
            "owner_ldap": user["ldap"],
            "grp": grp,
            "genre": genre,
            "title": str(fields.get("title") or "Untitled sample")[:200],
            "intent": str(fields.get("intent") or ""),
            "context": fields.get("context") or {},
            "payload": fields.get("payload"),
            "size_class": fields.get("size_class") or "m",
            "state": fields.get("state") or "content",
            "edge_cases": fields.get("edge_cases") or [],
            "tags": fields.get("tags") or [],
            "meta": fields.get("meta") or {},
            "source": fields.get("source") or source,
            "created_at": util.now_iso(),
        },
    )
    return self.get_sample(sid)

  def update_sample(
      self, user: dict[str, Any], sid: str, fields: dict[str, Any]
  ) -> dict[str, Any]:
    s = self.store.get("samples", sid)
    if not s:
      raise ApiError(404, "Sample not found")
    if s["owner_ldap"] != user["ldap"] and not user["is_admin"]:
      raise ApiError(403, "Only the owner can edit this sample")
    upd = {
        k: fields[k]
        for k in (
            "title",
            "intent",
            "context",
            "payload",
            "size_class",
            "state",
            "edge_cases",
            "tags",
            "meta",
        )
        if k in fields
    }
    cat_keys = (
        "group_label",
        "category_label",
        "genre_label",
        "subcategory_label",
    )
    genre_changed = (
        "genre" in fields and fields["genre"] and fields["genre"] != s["genre"]
    )
    if genre_changed or any(fields.get(k) for k in cat_keys):
      upd["grp"], upd["genre"] = self.resolve_category(user, fields)
    self.store.update("samples", sid, upd)
    return self.get_sample(sid)

  def delete_sample(self, user: dict[str, Any], sid: str) -> None:
    s = self.store.get("samples", sid)
    if not s:
      raise ApiError(404, "Sample not found")
    if s["owner_ldap"] != user["ldap"] and not user["is_admin"]:
      raise ApiError(403, "Only the owner can delete this sample")
    self.store.delete("samples", sid)

  # ------------------------------------------------------- sample sets
  def fmt_set(self, st: dict[str, Any]) -> dict[str, Any]:
    st = dict(st)
    st["sample_ids"] = st.get("sample_ids") or []
    st["count"] = len(st["sample_ids"])
    return st

  def list_sets(self) -> list[dict[str, Any]]:
    return [
        self.fmt_set(s)
        for s in self.store.query(
            "SELECT * FROM sample_sets ORDER BY created_at DESC, rowid DESC"
        )
    ]

  def create_set(
      self,
      user: dict[str, Any],
      name: str,
      sample_ids: list[str],
      description: str = "",
  ) -> dict[str, Any]:
    if not (name or "").strip():
      raise ApiError(400, "Set name is required")
    sid = util.new_id("set")
    return self.fmt_set(
        self.store.insert(
            "sample_sets",
            {
                "id": sid,
                "name": name.strip()[:120],
                "description": description or "",
                "owner_ldap": user["ldap"],
                "sample_ids": list(sample_ids or []),
                "created_at": util.now_iso(),
            },
        )
    )

  def update_set(
      self, user: dict[str, Any], set_id: str, fields: dict[str, Any]
  ) -> dict[str, Any]:
    st = self.store.get("sample_sets", set_id)
    if not st:
      raise ApiError(404, "Set not found")
    if st["owner_ldap"] != user["ldap"] and not user["is_admin"]:
      raise ApiError(403, "Only the owner can edit this set")
    upd = {
        k: fields[k]
        for k in ("name", "description", "sample_ids")
        if k in fields
    }
    return self.fmt_set(self.store.update("sample_sets", set_id, upd))

  def delete_set(self, user: dict[str, Any], set_id: str) -> None:
    st = self.store.get("sample_sets", set_id)
    if not st:
      raise ApiError(404, "Set not found")
    if st["owner_ldap"] != user["ldap"] and not user["is_admin"]:
      raise ApiError(403, "Only the owner can delete this set")
    self.store.delete("sample_sets", set_id)

  # -------------------------------------------------------- guidelines
  def fmt_guideline(self, g: dict[str, Any]) -> dict[str, Any]:
    g = dict(g)
    g["tags"] = g.get("tags") or []
    g["screenshots"] = g.get("screenshots") or []
    g["renderer"] = g.get("renderer") or "web"
    return g

  def _guideline(self, user: dict[str, Any], gid: str) -> dict[str, Any]:
    g = self.store.get("guidelines", gid)
    if not g:
      raise ApiError(404, "Guideline not found")
    self._project_row(g["project_id"], user)
    return g

  def list_guidelines(
      self, user: dict[str, Any], pid: str, kind: str = "", status: str = ""
  ) -> list[dict[str, Any]]:
    self._project_row(pid, user)
    sql = "SELECT * FROM guidelines WHERE project_id=?"
    args: list[Any] = [pid]
    if kind:
      sql += " AND kind=?"
      args.append(kind)
    if status:
      sql += " AND status=?"
      args.append(status)
    sql += " ORDER BY created_at DESC"
    return [self.fmt_guideline(g) for g in self.store.query(sql, args)]

  def _copy_output_screenshot(
      self, gid: str, output_id: str, renderer: str | None = None
  ) -> dict[str, Any] | None:
    o = self.store.get("outputs", output_id)
    if not o:
      return None
    renders = o.get("renders") or {}
    src_path = None
    actual_renderer = renderer or "web"
    if (
        renderer
        and renderer != "all"
        and isinstance(renders.get(renderer), dict)
    ):
      sp = renders[renderer].get("screenshot_path")
      if sp and os.path.exists(sp):
        src_path = sp
    if (
        not src_path
        and o.get("screenshot_path")
        and os.path.exists(o["screenshot_path"])
    ):
      src_path = o["screenshot_path"]
      if not renderer or renderer == "all":
        actual_renderer = "web"
    if not src_path:
      for rid, rinfo in renders.items():
        if (
            isinstance(rinfo, dict)
            and rinfo.get("screenshot_path")
            and os.path.exists(rinfo["screenshot_path"])
        ):
          src_path = rinfo["screenshot_path"]
          actual_renderer = rid
          break
    if not src_path:
      return None
    fname = f"{gid}_{actual_renderer}_{util.new_id()}.png"
    shutil.copyfile(src_path, self.store.file_path("guidelines", fname))
    return {
        "url": f"/files/guidelines/{fname}",
        "output_id": output_id,
        "renderer": actual_renderer,
        "file": fname,
    }

  def create_guideline(
      self, user: dict[str, Any], pid: str, fields: dict[str, Any]
  ) -> dict[str, Any]:
    self._project_row(pid, user, write=True)
    kind = fields.get("kind")
    if kind not in ("do", "dont"):
      raise ApiError(400, "kind must be 'do' or 'dont'")
    title = str(fields.get("title") or "").strip()
    if not title:
      raise ApiError(400, "Title is required")
    gid = util.new_id("g")
    output_id = fields.get("output_id")
    renderer = str(fields.get("renderer") or "web")
    o = self.store.get("outputs", output_id) if output_id else None
    if o and o["project_id"] != pid:
      o, output_id = None, None
    shots = []
    if o:
      if renderer == "all":
        for rid in settings_mod.RENDERER_IDS:
          rinfo = (o.get("renders") or {}).get(rid)
          if (
              isinstance(rinfo, dict)
              and rinfo.get("screenshot_path")
              and os.path.exists(rinfo["screenshot_path"])
          ):
            sh = self._copy_output_screenshot(gid, o["id"], rid)
            if sh:
              shots.append(sh)
      if not shots:
        sh = self._copy_output_screenshot(gid, o["id"], renderer)
        if sh:
          shots.append(sh)
    now = util.now_iso()
    fixable = fields.get("fixable") or "unsure"
    if fixable not in ("yes", "no", "unsure"):
      fixable = "unsure"
    g = self.store.insert(
        "guidelines",
        {
            "id": gid,
            "project_id": pid,
            "kind": kind,
            "title": title[:200],
            "body_md": str(fields.get("body_md") or ""),
            "tags": fields.get("tags") or [],
            "screenshots": shots,
            "output_id": output_id,
            "sample_id": o["sample_id"] if o else None,
            "revision_id": (
                o["revision_id"] if o else self.current_revision(pid)["id"]
            ),
            "renderer": renderer,
            "fixable": fixable,
            "status": "open",
            "escalation_note": None,
            "addressed_revision_id": None,
            "created_by": user["ldap"],
            "created_at": now,
            "updated_at": now,
        },
    )
    self.touch(pid)
    return self.fmt_guideline(g)

  def get_guideline(self, user: dict[str, Any], gid: str) -> dict[str, Any]:
    return self.fmt_guideline(self._guideline(user, gid))

  def update_guideline(
      self, user: dict[str, Any], gid: str, fields: dict[str, Any]
  ) -> dict[str, Any]:
    self._guideline(user, gid)
    upd: dict[str, Any] = {}
    for k in ("title", "body_md", "tags", "renderer"):
      if k in fields:
        upd[k] = fields[k]
    if fields.get("kind") in ("do", "dont"):
      upd["kind"] = fields["kind"]
    if fields.get("fixable") in ("yes", "no", "unsure"):
      upd["fixable"] = fields["fixable"]
    if fields.get("status") in ("open", "addressed", "escalated", "packaged"):
      upd["status"] = fields["status"]
    if "addressed_revision_id" in fields:
      upd["addressed_revision_id"] = fields["addressed_revision_id"]
    if "screenshots" in fields and isinstance(fields["screenshots"], list):
      upd["screenshots"] = fields["screenshots"]
    upd["updated_at"] = util.now_iso()
    return self.fmt_guideline(self.store.update("guidelines", gid, upd))

  def delete_guideline(self, user: dict[str, Any], gid: str) -> None:
    self._guideline(user, gid)
    self.store.delete("guidelines", gid)

  def add_guideline_screenshot(
      self,
      user: dict[str, Any],
      gid: str,
      output_id: str | None,
      data_url: str | None,
      renderer: str | None = None,
  ) -> dict[str, Any]:
    g = self._guideline(user, gid)
    shots = list(g.get("screenshots") or [])
    if output_id:
      sh = self._copy_output_screenshot(gid, output_id, renderer)
      if not sh:
        raise ApiError(400, "That output has no screenshot")
      shots.append(sh)
    elif data_url and data_url.startswith("data:image/"):
      head, _, b64 = data_url.partition(",")
      ext = "png" if "png" in head else ("jpg" if "jpeg" in head else "img")
      try:
        raw = base64.b64decode(b64)
      except ValueError as e:
        raise ApiError(400, "Invalid image data") from e
      if len(raw) > 12 * 1024 * 1024:
        raise ApiError(400, "Image too large (12 MB max)")
      fname = f"{gid}_{util.new_id()}.{ext}"
      with open(self.store.file_path("guidelines", fname), "wb") as fh:
        fh.write(raw)
      shots.append({
          "url": f"/files/guidelines/{fname}",
          "output_id": None,
          "file": fname,
      })
    else:
      raise ApiError(400, "Provide output_id or data_url")
    return self.fmt_guideline(
        self.store.update(
            "guidelines",
            gid,
            {"screenshots": shots, "updated_at": util.now_iso()},
        )
    )

  def escalate_guideline(
      self, user: dict[str, Any], gid: str, note: str = ""
  ) -> dict[str, Any]:
    self._guideline(user, gid)
    return self.fmt_guideline(
        self.store.update(
            "guidelines",
            gid,
            {
                "status": "escalated",
                "escalation_note": note or "",
                "updated_at": util.now_iso(),
            },
        )
    )

  # --------------------------------------------------- models & imagery
  @staticmethod
  def models_catalog() -> dict[str, Any]:
    return {
        "models": list(settings_mod.GEMINI_MODELS),
        "image_models": list(settings_mod.GEMINI_IMAGE_MODELS),
        "defaults": {
            "generator": settings_mod.DEFAULT_MODEL,
            "judge": settings_mod.DEFAULT_MODEL,
            "image": settings_mod.DEFAULT_IMAGE_MODEL,
            "svg": settings_mod.DEFAULT_SVG_MODEL,
        },
    }

  def list_imagery_styles(
      self,
      user: dict[str, Any],
      pid: str,
      include_archived: bool = False,
  ) -> list[dict[str, Any]]:
    self._project_row(pid, user)
    return imagery_mod.list_styles(
        self.store, pid, include_archived=include_archived
    )

  def get_imagery_style(
      self, user: dict[str, Any], pid: str, sid: str
  ) -> dict[str, Any]:
    self._project_row(pid, user)
    st = imagery_mod.get_style(self.store, pid, sid)
    if not st:
      raise ApiError(404, "Imagery style not found")
    return st

  def upsert_imagery_style(
      self,
      user: dict[str, Any],
      pid: str,
      fields: dict[str, Any],
      sid: str | None = None,
  ) -> dict[str, Any]:
    self._project_row(pid, user, write=True)
    if not isinstance(fields, dict):
      raise ApiError(400, "Body must be an object")
    st = imagery_mod.upsert_style(
        self.store, pid, user["ldap"], fields, sid=sid
    )
    self.touch(pid)
    return st

  def delete_imagery_style(
      self, user: dict[str, Any], pid: str, sid: str
  ) -> None:
    self._project_row(pid, user, write=True)
    if not imagery_mod.delete_style(self.store, pid, sid):
      raise ApiError(404, "Imagery style not found")
    self.touch(pid)

  def preview_imagery_style(
      self,
      user: dict[str, Any],
      pid: str,
      sid: str,
      prompt: str = "",
  ) -> dict[str, Any]:
    p = self._project_row(pid, user, write=True)
    st = imagery_mod.get_style(self.store, pid, sid)
    if not st:
      raise ApiError(404, "Imagery style not found")
    s = settings_mod.merged(p["settings"])
    img_cfg = s.get("imagery") or {}
    res = imagery_gen.generate_style_preview(
        self.store,
        self.llm,
        st,
        prompt=prompt,
        image_model=img_cfg.get("image_model"),
        svg_model=img_cfg.get("svg_model"),
    )
    self.touch(pid)
    return res

  def imagery_ref_path(
      self, user: dict[str, Any], pid: str, sid: str, idx: int
  ) -> str:
    self._project_row(pid, user)
    st = imagery_mod.get_style(self.store, pid, sid)
    if not st:
      raise ApiError(404, "Imagery style not found")
    refs = st.get("reference_images") or []
    if idx < 0 or idx >= len(refs):
      raise ApiError(404, "Reference image not found")
    fname = os.path.basename(str(refs[idx].get("file") or ""))
    if not fname:
      raise ApiError(404, "Reference image file missing")
    path = self.store.file_path("imagery", st["id"], fname)
    if not os.path.isfile(path):
      path = self.store.file_path("imagery", fname)
    if not os.path.isfile(path):
      raise ApiError(404, "Reference image file not found")
    return path
