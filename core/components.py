"""Custom components (design system): storage, catalog, previews, package.

Rows live in the `custom_components` table (see docs/CONTRACT.md "Design
system"). Active rows join the project catalog (`Service.catalog_for`) and
are snapshotted into run configs. Building components from Figma /
screenshots / Stitch / descriptions lives in `refs/components_import.py`.
"""

from __future__ import annotations

import concurrent.futures
import copy
import os
import traceback
from typing import Any

from core import dependencies as deps_mod
from core import settings as settings_mod
from core import util
from core.a2ui import catalog as catalog_mod
from core.a2ui import custom_web
from core.a2ui import lowering
from core.a2ui import validator
from core.service import ApiError
from refs import figma_compiler

STATUSES = ("draft", "active", "archived")
CLASSIFICATIONS = ("custom", "basic_equivalent", "skip")
PREVIEW_MIN_W, PREVIEW_MAX_W = 192, 1280


def sample_messages(
    defn: dict[str, Any], catalog_id: str, surface_id: str = "preview"
) -> list[dict[str, Any]]:
  """A2UI surface showing one instance of `defn` with its sample props."""
  sample = defn.get("sample") or {}
  props = dict(sample.get("props") or {})
  props.pop("id", None)
  props.pop("component", None)
  comps = [{"id": "root", "component": defn.get("name"), **props}]
  for c in sample.get("components") or []:
    if isinstance(c, dict) and c.get("id") and c.get("id") != "root":
      comps.append(c)
  msgs: list[dict[str, Any]] = [
      {
          "version": "v0.9",
          "createSurface": {"surfaceId": surface_id, "catalogId": catalog_id},
      },
      {
          "version": "v0.9",
          "updateComponents": {"surfaceId": surface_id, "components": comps},
      },
  ]
  data = sample.get("data")
  if data:
    msgs.append({
        "version": "v0.9",
        "updateDataModel": {
            "surfaceId": surface_id,
            "path": "/",
            "value": data,
        },
    })
  return msgs


class ComponentManager:
  """CRUD + catalog + previews for custom components."""

  def __init__(self, svc, runs):
    self.svc = svc
    self.svc.components = self
    self.store = svc.store
    self.runs = runs
    self.executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="components"
    )

  # ------------------------------------------------------------ helpers
  @property
  def pool(self):
    return getattr(self.runs, "pool", None)

  def _project(self, user, pid: str, write: bool = False) -> dict[str, Any]:
    return self.svc._project_row(pid, user, write=write)  # pylint: disable=protected-access

  def row(self, user, cid: str, write: bool = False) -> dict[str, Any]:
    r = self.store.get("custom_components", cid)
    if not r:
      raise ApiError(404, "Component not found")
    self._project(user, r["project_id"], write=write)
    return r

  def fmt(self, r: dict[str, Any]) -> dict[str, Any]:
    defn = r.get("definition") or {}
    if (r.get("source") or {}).get("kind", "manual") != "manual" and isinstance(
        defn.get("web"), dict
    ):
      tpl = str(defn["web"].get("template") or "")
      css = str(defn["web"].get("css") or "")
      if (tpl and "\n" not in tpl.strip()) or (css and "\n" not in css.strip()):
        defn = dict(defn, web=dict(defn["web"]))
        if tpl and "\n" not in tpl.strip():
          defn["web"]["template"] = custom_web.format_template(tpl)
        if css and "\n" not in css.strip():
          defn["web"]["css"] = custom_web.format_css(css)
    v = r.get("updated_at") or ""
    has_web = bool(((defn.get("web") or {}).get("template") or "").strip())
    has_comp = lowering.has_composition(defn)
    issues = r.get("issues") or []
    warnings = r.get("warnings") or []
    token_refs = r.get("token_refs") or []
    if (has_web or has_comp) and (
        r.get("classification") or "custom"
    ) == "custom":
      chk = self.check(defn)
      if (
          chk["issues"] != issues
          or chk["warnings"] != warnings
          or chk["token_refs"] != token_refs
      ):
        issues = chk["issues"]
        warnings = chk["warnings"]
        token_refs = chk["token_refs"]
        if r.get("id"):
          self.store.update(
              "custom_components",
              r["id"],
              {
                  "issues": issues,
                  "warnings": warnings,
                  "token_refs": token_refs,
              },
          )
    return {
        "id": r["id"],
        "project_id": r["project_id"],
        "name": r["name"],
        "status": r.get("status") or "draft",
        "classification": r.get("classification") or "custom",
        "definition": defn,
        "basic_equivalent": r.get("basic_equivalent"),
        "token_suggestions": r.get("token_suggestions") or [],
        "source": r.get("source") or {},
        "fidelity": r.get("fidelity"),
        "job": r.get("job"),
        "issues": issues,
        "warnings": warnings,
        "token_refs": token_refs,
        "has_web": has_web,
        "has_composition": has_comp,
        "strategy": defn.get("strategy") or "",
        "basic_fit": (r.get("source") or {}).get("basic_fit"),
        "strategy_decision": (r.get("source") or {}).get("strategy_decision"),
        "preview_url": (
            f"/files/components/{r['preview_file']}?v={v}"
            if r.get("preview_file")
            else None
        ),
        "reference_url": (
            f"/files/components/{r['reference_file']}"
            if r.get("reference_file")
            else None
        ),
        "notes": r.get("notes") or "",
        "author_ldap": r.get("author_ldap"),
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
    }

  def names_in_project(self, pid: str, exclude: str | None = None) -> set[str]:
    return {
        r["name"]
        for r in self.store.query(
            "SELECT id, name FROM custom_components WHERE project_id=? AND"
            " status!='archived'",
            [pid],
        )
        if r["id"] != exclude
    }

  def check(self, defn: dict[str, Any]) -> dict[str, Any]:
    return custom_web.check_definition(defn, catalog_mod.basic_names())

  def new_row(
      self,
      pid: str,
      user_ldap: str,
      defn: dict[str, Any],
      *,
      status: str = "draft",
      source: dict[str, Any] | None = None,
      job: dict[str, Any] | None = None,
      notes: str = "",
  ) -> dict[str, Any]:
    defn = custom_web.normalize_definition(defn)
    chk = (
        self.check(defn)
        if (
            defn.get("web", {}).get("template")
            or lowering.has_composition(defn)
        )
        else None
    )
    now = util.now_iso()
    return self.store.insert(
        "custom_components",
        {
            "id": util.new_id("cc"),
            "project_id": pid,
            "name": defn["name"] or "Untitled",
            "status": status,
            "classification": "custom",
            "definition": defn,
            "source": source or {"kind": "manual"},
            "fidelity": None,
            "job": job,
            "issues": chk["issues"] if chk else [],
            "warnings": chk["warnings"] if chk else [],
            "token_refs": chk["token_refs"] if chk else [],
            "token_suggestions": [],
            "basic_equivalent": None,
            "preview_file": None,
            "reference_file": None,
            "notes": notes,
            "author_ldap": user_ldap,
            "created_at": now,
            "updated_at": now,
        },
    )

  def set_job(self, cid: str, stage: str, error: str | None = None) -> None:
    job = {"stage": stage, "updated_at": util.now_iso()}
    if error:
      job["error"] = error[:2000]
    self.store.update("custom_components", cid, {"job": job})

  def save_reference(self, cid: str, data: bytes, ext: str = "png") -> str:
    fn = f"{cid}_ref.{ext}"
    path = self.store.file_path("components", fn)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
      fh.write(data)
    self.store.update("custom_components", cid, {"reference_file": fn})
    return fn

  def reference_bytes(self, r: dict[str, Any]) -> tuple[bytes, str] | None:
    fn = r.get("reference_file")
    if not fn:
      return None
    try:
      with open(self.store.file_path("components", fn), "rb") as fh:
        data = fh.read()
    except OSError:
      return None
    mime = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(fn.rsplit(".", 1)[-1], "image/png")
    return data, mime

  def apply_definition(
      self, cid: str, defn: dict[str, Any], extra: dict[str, Any] | None = None
  ) -> dict[str, Any]:
    """Stores a definition with fresh issues/warnings/token refs."""
    defn = custom_web.normalize_definition(defn)
    chk = self.check(defn)
    fields = {
        "definition": defn,
        "name": defn["name"] or "Untitled",
        "issues": chk["issues"],
        "warnings": chk["warnings"],
        "token_refs": chk["token_refs"],
        "updated_at": util.now_iso(),
    }
    fields.update(extra or {})
    return self.store.update("custom_components", cid, fields)

  # ------------------------------------------------------------ CRUD
  def list(self, user, pid: str, archived: bool = False) -> dict[str, Any]:
    p = self._project(user, pid)
    self.svc.migrate_legacy_catalog(p)
    rows = self.store.query(
        "SELECT * FROM custom_components WHERE project_id=?"
        + ("" if archived else " AND status!='archived'")
        + " ORDER BY status='active' DESC, updated_at DESC",
        [pid],
    )
    return {
        "components": [self.fmt(r) for r in rows],
        "catalog_id": self.svc.catalog_id_for(p),
    }

  def get(self, user, cid: str) -> dict[str, Any]:
    return self.fmt(self.row(user, cid))

  def create(self, user, pid: str, body: dict[str, Any]) -> dict[str, Any]:
    self._project(user, pid, write=True)
    defn = body.get("definition")
    if not isinstance(defn, dict):
      raise ApiError(400, "definition must be an object")
    defn = custom_web.normalize_definition(defn)
    if not defn["name"]:
      raise ApiError(400, "Give the component a name")
    if defn["name"] in catalog_mod.basic_names():
      raise ApiError(400, f"'{defn['name']}' is a Basic catalog component")
    if defn["name"] in self.names_in_project(pid):
      raise ApiError(400, f"A component named {defn['name']} already exists")
    status = body.get("status") or "draft"
    if status not in ("draft", "active"):
      raise ApiError(400, "status must be draft or active")
    r = self.new_row(pid, user["ldap"], defn, source={"kind": "manual"})
    r = self.apply_definition(r["id"], defn)
    if status == "active":
      r = self._activate(r)
    self.queue_preview(r["id"])
    self.svc.touch(pid)
    return self.fmt(self.store.get("custom_components", r["id"]))

  def update(self, user, cid: str, body: dict[str, Any]) -> dict[str, Any]:
    r = self.row(user, cid, write=True)
    pid = r["project_id"]
    changed_def = False
    if "definition" in body:
      defn = body["definition"]
      if not isinstance(defn, dict):
        raise ApiError(400, "definition must be an object")
      defn = custom_web.normalize_definition(defn)
      if not defn["name"]:
        raise ApiError(400, "Give the component a name")
      if defn["name"] in self.names_in_project(pid, exclude=cid):
        raise ApiError(400, f"A component named {defn['name']} already exists")
      defn, src_extra = self._strategy_change(r, defn)
      extra = (
          {"source": dict(r.get("source") or {}, **src_extra)}
          if src_extra
          else None
      )
      r = self.apply_definition(cid, defn, extra)
      changed_def = True
    if "notes" in body:
      r = self.store.update(
          "custom_components", cid, {"notes": str(body["notes"] or "")[:4000]}
      )
    status = body.get("status")
    if status:
      if status not in STATUSES:
        raise ApiError(400, f"status must be one of {STATUSES}")
      if status == "active":
        if body.get("apply_token_suggestions") and r.get("token_suggestions"):
          self._apply_tokens(user, r)
        r = self._activate(r)
      else:
        r = self.store.update(
            "custom_components",
            cid,
            {"status": status, "updated_at": util.now_iso()},
        )
    if changed_def:
      self.queue_preview(cid)
    self.svc.touch(pid)
    return self.fmt(self.store.get("custom_components", cid))

  def _activate(self, r: dict[str, Any]) -> dict[str, Any]:
    if (r.get("classification") or "custom") != "custom":
      raise ApiError(
          400,
          "This component matches a Basic component; apply its tokens"
          " instead of activating it",
      )
    defn = r.get("definition") or {}
    chk = self.check(defn)
    issues = chk["issues"]
    if issues != (r.get("issues") or []):
      self.store.update(
          "custom_components",
          r["id"],
          {
              "issues": issues,
              "warnings": chk["warnings"],
              "token_refs": chk["token_refs"],
          },
      )
    if issues:
      raise ApiError(
          400, "Fix the component's issues first: " + "; ".join(issues[:5])
      )
    name = defn.get("name") or r["name"]
    clash = self.store.scalar(
        "SELECT COUNT(*) FROM custom_components WHERE project_id=? AND"
        " name=? AND status='active' AND id!=?",
        [r["project_id"], name, r["id"]],
    )
    if clash:
      raise ApiError(400, f"An active component named {name} already exists")
    return self.store.update(
        "custom_components",
        r["id"],
        {"status": "active", "updated_at": util.now_iso()},
    )

  def delete(self, user, cid: str) -> None:
    r = self.row(user, cid, write=True)
    for fn in (r.get("preview_file"), r.get("reference_file")):
      if fn:
        try:
          os.remove(self.store.file_path("components", fn))
        except OSError:
          pass
    self.store.delete("custom_components", cid)
    self.svc.touch(r["project_id"])

  # ------------------------------------------------------------ tokens
  def _apply_tokens(self, user, r: dict[str, Any]) -> list[dict[str, Any]]:
    p = self._project(user, r["project_id"], write=True)
    s = settings_mod.merged(p["settings"])
    changes = []
    for t in r.get("token_suggestions") or []:
      if not isinstance(t, dict) or not t.get("token"):
        continue
      changes.append({
          "token": t["token"],
          "value": t.get("value"),
          "is_custom": bool(t.get("is_custom")),
          "maps_to": t.get("maps_to") or "",
          "reason": t.get("reason") or f"From component {r['name']}",
      })
    preview_theme, _ = settings_mod.apply_theme_changes(
        s["theme"], changes, None
    )
    web_defn = ((r.get("definition") or {}).get("web")) or {}
    changes.extend(
        settings_mod.resolve_missing_tokens_from_material(
            preview_theme,
            token_refs=r.get("token_refs") or [],
            texts=[
                str(web_defn.get("css") or ""),
                str(web_defn.get("template") or ""),
            ],
            token_suggestions=r.get("token_suggestions") or [],
            reason_prefix=f"From component {r['name']}",
        )
    )
    if not changes:
      return []
    theme, applied = settings_mod.apply_theme_changes(s["theme"], changes, None)
    s["theme"] = theme
    self.svc.put_settings(
        user,
        r["project_id"],
        s,
        source="component_promote",
        summary=f"Applied tokens from component {r['name']}",
    )
    return applied

  def apply_tokens(self, user, cid: str) -> dict[str, Any]:
    r = self.row(user, cid, write=True)
    applied = self._apply_tokens(user, r)
    fields: dict[str, Any] = {"updated_at": util.now_iso()}
    if (r.get("classification") or "custom") != "custom":
      fields["status"] = "archived"
    else:
      fields["token_suggestions"] = []
    self.store.update("custom_components", cid, fields)
    return {
        "component": self.fmt(self.store.get("custom_components", cid)),
        "applied": applied,
    }

  # ------------------------------------------------------------ render
  def defs_for_render(
      self, pid: str, extra: dict[str, Any] | None = None
  ) -> dict[str, Any]:
    defs = dict(self.svc.custom_defs(pid))
    if extra and extra.get("name"):
      defs[extra["name"]] = extra
    return defs

  def preview_width(self, r: dict[str, Any]) -> int:
    w = (r.get("source") or {}).get("width")
    try:
      w = int(float(w))
    except (TypeError, ValueError):
      w = 0
    if not w:
      return 412
    return max(PREVIEW_MIN_W, min(PREVIEW_MAX_W, w + 32))

  def render_data(
      self, cid: str, token: str, viewport: str | None
  ) -> dict[str, Any]:
    if not self.runs.check_token(cid, token):
      raise ApiError(403, "Bad render token")
    r = self.store.get("custom_components", cid)
    if not r:
      raise ApiError(404, "Component not found")
    p = self.store.get("projects", r["project_id"]) or {}
    s = settings_mod.merged(p.get("settings"))
    defn = r.get("definition") or {}
    from sim import generator  # pylint: disable=g-import-not-at-top

    vp = dict(
        generator.VIEWPORTS.get(
            viewport or "phone", generator.VIEWPORTS["phone"]
        )
    )
    if not viewport:
      vp["width"] = self.preview_width(r)
    msgs = sample_messages(defn, self.svc.catalog_id_for(p))
    custom_defs = self.defs_for_render(r["project_id"], defn)
    if lowering.should_lower_for_renderer(defn, "web"):
      msgs = lowering.expand_composite_components(
          msgs, custom_defs, renderer="web"
      )
    return {
        "messages": msgs,
        "theme": settings_mod.resolve_effective_theme(s["theme"]),
        "viewport": vp,
        "framing": "page",
        "custom_components": custom_defs,
    }

  def render_path(self, cid: str, width: int | None = None) -> str:
    q = f"?token={self.runs.token(cid)}"
    if width:
      q += f"&width={width}"
    return f"/render/component/{cid}{q}"

  def queue_preview(self, cid: str) -> None:
    if self.pool is None:
      return
    self.executor.submit(self._preview_safe, cid)

  def _preview_safe(self, cid: str) -> None:
    try:
      self.render_preview(cid)
    except Exception:  # pylint: disable=broad-except
      traceback.print_exc()

  def render_preview(self, cid: str) -> str | None:
    """Screenshots the component's sample surface (web). Returns file name."""
    r = self.store.get("custom_components", cid)
    if not r or self.pool is None:
      return None
    defn = r.get("definition") or {}
    if not (
        ((defn.get("web") or {}).get("template") or "").strip()
        or lowering.has_composition(defn)
    ):
      return None
    self._resolve_sample_images(r)
    w = self.preview_width(r)
    fn = f"{cid}.png"
    self.pool.screenshot(
        self.runs.base_url + self.render_path(cid, w),
        w,
        # Small viewport: the capture grows to the content height, so the
        # preview hugs the component instead of padding to a phone screen.
        120,
        self.store.file_path("components", fn),
        scale=2.0,
        timeout=20,
    )
    self.store.update(
        "custom_components",
        cid,
        {"preview_file": fn, "updated_at": util.now_iso()},
    )
    return fn

  def _resolve_sample_images(self, r: dict[str, Any]) -> None:
    """Swaps /img/<slug>/WxH sample placeholders for real photos (saved)."""
    from sim import images  # pylint: disable=g-import-not-at-top

    defn = r.get("definition") or {}
    sample = defn.get("sample") or {}
    try:
      if not images.has_placeholders(sample):
        return
      resolved, _ = images.Resolver(self.store).resolve(sample)
    except Exception:  # pylint: disable=broad-except
      traceback.print_exc()
      return
    if isinstance(resolved, dict) and resolved != sample:
      defn = dict(defn, sample=resolved)
      self.store.update("custom_components", r["id"], {"definition": defn})
      r["definition"] = defn

  def preview_bytes(self, cid: str) -> bytes | None:
    try:
      with open(self.store.file_path("components", f"{cid}.png"), "rb") as fh:
        return fh.read()
    except OSError:
      return None

  # ------------------------------------------------------------ package
  def catalog_json(self, user, pid: str) -> dict[str, Any]:
    p = self._project(user, pid)
    cat = self.svc.catalog_for(p)
    return catalog_mod.to_a2ui_catalog_json(
        cat,
        cat.catalog_id,
        title=f"{p['name']} — A2UI catalog",
        description=(p.get("description") or "")
        or "Design-system catalog exported by Design Constitution Builder.",
    )

  def package_check(self, user, pid: str) -> dict[str, Any]:
    p = self._project(user, pid)
    cat = self.svc.catalog_for(p)
    rows = self.store.query(
        "SELECT * FROM custom_components WHERE project_id=? AND"
        " status!='archived'",
        [pid],
    )
    issues: list[dict[str, Any]] = []
    drafts = [r for r in rows if r["status"] == "draft"]
    active = [r for r in rows if r["status"] == "active"]
    for r in drafts:
      issues.append({
          "level": "warn",
          "component": r["name"],
          "component_id": r["id"],
          "message": "Draft not in the catalog yet (accept or discard it)",
      })
    for r in active:
      defn = r.get("definition") or {}
      if not (
          ((defn.get("web") or {}).get("template") or "").strip()
          or lowering.has_composition(defn)
      ):
        issues.append({
            "level": "error",
            "component": r["name"],
            "component_id": r["id"],
            "message": "No web implementation (renders as a placeholder)",
        })
      for i in r.get("issues") or []:
        issues.append({
            "level": "error",
            "component": r["name"],
            "component_id": r["id"],
            "message": i,
        })
      for w in r.get("warnings") or []:
        issues.append({
            "level": "warn",
            "component": r["name"],
            "component_id": r["id"],
            "message": w,
        })
      res = validator.validate(sample_messages(defn, cat.catalog_id), cat)
      for e in res.errors[:5]:
        issues.append({
            "level": "error",
            "component": r["name"],
            "component_id": r["id"],
            "message": f"Example surface: {e['message']} ({e['path']})",
        })
    dep_info = deps_mod.analyze_project_dependencies(
        self.store, self.svc, p, self.svc.current_revision(pid)
    )
    for di in dep_info.get("issues") or []:
      issues.append({
          "level": di.get("level") or "warn",
          "kind": di.get("kind"),
          "component": di.get("component"),
          "component_id": di.get("component_id"),
          "message": di.get("message") or "",
      })
    basic = catalog_mod.basic_names()
    from exports import package as package_mod  # pylint: disable=g-import-not-at-top

    return {
        "catalog_id": cat.catalog_id,
        "counts": {
            "basic_enabled": len([n for n in basic if n not in cat.disabled]),
            "basic_disabled": len([n for n in cat.disabled if n in basic]),
            "custom_active": len(active),
            "drafts": len(drafts),
        },
        "issues": issues,
        "files": package_mod.file_list(
            [r["name"] for r in active],
            active,
        ),
    }

  def _ensure_composition(self, defn: dict[str, Any]) -> dict[str, Any]:
    """Ensures `defn` has a valid Basic Catalog `composition` matching its props."""
    props = ((defn.get("props") or {}).get("properties")) or {}
    if lowering.has_composition(defn):
      comp_issues, _, _ = custom_web.check_composition(
          defn.get("composition"), props, catalog_mod.basic_names()
      )
      if not comp_issues:
        return defn
    children = []
    for k, pspec in props.items():
      if not isinstance(pspec, dict):
        continue
      ref = str(pspec.get("$ref") or "")
      ptype = pspec.get("type")
      if ptype == "string" or ref.endswith("DynamicString"):
        if "image" in k.lower() or "url" in k.lower() or "avatar" in k.lower():
          children.append({
              "kind": "RECTANGLE",
              "name": k,
              "image_fill": True,
              "bound_prop": k,
          })
        else:
          kl = k.lower()
          if (
              any(
                  w in kl
                  for w in (
                      "title",
                      "heading",
                      "headline",
                      "header",
                      "hero",
                      "price",
                      "score",
                      "metric",
                  )
              )
              and "subtitle" not in kl
          ):
            font_meta = {"size": 20, "weight": 600}
          elif any(w in kl for w in ("subtitle", "subhead")):
            font_meta = {"size": 17, "weight": 500}
          elif any(
              w in kl
              for w in (
                  "caption",
                  "meta",
                  "badge",
                  "tag",
                  "status",
                  "date",
                  "time",
                  "label",
                  "eyebrow",
                  "overline",
              )
          ):
            font_meta = {"size": 12, "weight": 400}
          else:
            font_meta = {"size": 15, "weight": 400}
          children.append({
              "kind": "TEXT",
              "name": k,
              "text": k,
              "bound_prop": k,
              "font": font_meta,
          })
    skel = figma_compiler.compile_node_to_basic_skeleton({
        "name": defn.get("name") or "Card",
        "kind": "FRAME",
        "children": children,
    })
    updated = dict(defn, composition=skel["composition"])
    if not props:
      updated["props"] = skel["props"]
    return custom_web.normalize_definition(updated)

  # ------------------------------------------------------- strategy / fit
  def _strategy_change(
      self, r: dict[str, Any], defn: dict[str, Any]
  ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pins or un-pins the strategy when a definition update changes it.

    Picking a concrete strategy in the editor pins it (`strategy_decision.auto
    = False`); picking "Auto" clears the pin and re-decides immediately from
    the recorded Basic fit and the project's threshold. Returns `(definition,
    source_extra)`.
    """
    old = str((r.get("definition") or {}).get("strategy") or "").strip().lower()
    new = str(defn.get("strategy") or "").strip().lower()
    if new == old:
      return defn, {}
    src = r.get("source") or {}
    if new:
      decision = settings_mod.component_strategy_decision(
          (self.store.get("projects", r["project_id"]) or {}).get("settings"),
          override=new,
          basic_fit=(src.get("basic_fit") or {}).get("score"),
      )
      return defn, {"strategy_decision": decision, "strategy": new}
    decision = self.auto_decision(r["project_id"], src)
    defn = self._apply_strategy(defn, decision["strategy"])
    return defn, {"strategy_decision": decision, "strategy": None}

  def auto_decision(
      self, pid: str, src: dict[str, Any] | None
  ) -> dict[str, Any]:
    """The project's `auto` strategy decision for a component source."""
    p = self.store.get("projects", pid) or {}
    return settings_mod.component_strategy_decision(
        p.get("settings"),
        basic_fit=((src or {}).get("basic_fit") or {}).get("score"),
    )

  def _apply_strategy(
      self, defn: dict[str, Any], strategy: str
  ) -> dict[str, Any]:
    """Writes an auto-decided `strategy` on `defn` with the artifacts it needs."""
    defn = dict(defn)
    if strategy == "web_template":
      defn.pop("strategy", None)
      return defn
    defn["strategy"] = strategy
    defn = self._ensure_composition(defn)
    return defn

  @staticmethod
  def effective_strategy(r: dict[str, Any]) -> str:
    """How `r` renders today: its strategy, else web template, else composition."""
    defn = r.get("definition") or {}
    s = str(defn.get("strategy") or "").strip().lower()
    if s in ("web_template", "basic_composition"):
      return s
    has_web = bool(((defn.get("web") or {}).get("template") or "").strip())
    return "web_template" if has_web else "basic_composition"

  def restrategize(
      self, user, pid: str, body: dict[str, Any] | None = None
  ) -> dict[str, Any]:
    """Re-applies the project's `auto` strategy rule to its custom components.

    Components pinned to a strategy by the designer and components without a
    Basic-fit estimate are left alone. `dry_run` only reports what would
    change. Flipping needs no LLM call: both the web template and the Basic
    composition are kept on the definition (a missing composition is derived
    deterministically from the props).
    """
    self._project(user, pid, write=True)
    body = body or {}
    dry = bool(body.get("dry_run"))
    only = set(body.get("component_ids") or [])
    p = self.store.get("projects", pid) or {}
    s = settings_mod.merged(p.get("settings"))
    items: list[dict[str, Any]] = []
    changed = unchanged = skipped = 0
    for r in self.store.query(
        "SELECT * FROM custom_components WHERE project_id=? AND"
        " status!='archived' ORDER BY name",
        [pid],
    ):
      if only and r["id"] not in only:
        continue
      if (r.get("classification") or "custom") != "custom":
        continue
      src = r.get("source") or {}
      prev = src.get("strategy_decision") or {}
      fit = (src.get("basic_fit") or {}).get("score")
      item = {
          "id": r["id"],
          "name": r["name"],
          "fit": fit,
          "from": self.effective_strategy(r),
      }
      if prev and not prev.get("auto"):
        skipped += 1
        items.append(dict(item, to=item["from"], status="pinned"))
        continue
      if fit is None:
        skipped += 1
        items.append(dict(item, to=item["from"], status="no_fit"))
        continue
      decision = settings_mod.component_strategy_decision(s, basic_fit=fit)
      to = decision["strategy"]
      defn = r.get("definition") or {}
      has_web = bool(((defn.get("web") or {}).get("template") or "").strip())
      if to == "web_template" and not has_web:
        skipped += 1
        items.append(dict(item, to=item["from"], status="no_web_template"))
        continue
      if to == item["from"]:
        unchanged += 1
        items.append(
            dict(item, to=to, status="unchanged", reason=decision["reason"])
        )
        if not dry and prev != decision:
          self.store.update(
              "custom_components",
              r["id"],
              {"source": dict(src, strategy_decision=decision)},
          )
        continue
      changed += 1
      items.append(
          dict(item, to=to, status="changed", reason=decision["reason"])
      )
      if dry:
        continue
      new_defn = self._apply_strategy(defn, to)
      self.apply_definition(
          r["id"],
          new_defn,
          {"source": dict(src, strategy_decision=decision)},
      )
      self.queue_preview(r["id"])
    if not dry and changed:
      self.svc.touch(pid)
    return {
        "dry_run": dry,
        "threshold": settings_mod.basic_fit_threshold(p.get("settings")),
        "component_strategy": s["catalog"].get("component_strategy") or "auto",
        "changed": changed,
        "unchanged": unchanged,
        "skipped": skipped,
        "items": items,
    }

  def promote_to_template(self, user, cid: str) -> dict[str, Any]:
    """Converts a custom component into a reusable Basic Catalog template row."""
    r = self.row(user, cid, write=True)
    defn = self._ensure_composition(dict(r.get("definition") or {}))
    if not lowering.has_composition(defn.get("definition") or defn):
      raise ApiError(
          400, "Component has no Basic composition to save as template"
      )
    if not lowering.has_composition(r.get("definition") or {}):
      r = self.apply_definition(cid, defn)
    tpl_rec = lowering.definition_to_template_record(
        defn, project_id=r["project_id"], source=r.get("source")
    )
    if not tpl_rec:
      raise ApiError(400, "Could not convert component to a Basic template")
    self.store.insert("templates", tpl_rec)
    self.svc.touch(r["project_id"])
    return {
        "template": tpl_rec,
        "component": self.fmt(self.store.get("custom_components", cid)),
    }

  def snapshot_rows(self, pid: str) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(r)
        for r in self.store.query(
            "SELECT * FROM custom_components WHERE project_id=? AND"
            " status='active' ORDER BY name",
            [pid],
        )
    ]
