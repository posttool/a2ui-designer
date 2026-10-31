"""Application: routing table, auth, static files, all API handlers."""

from __future__ import annotations

import json
import mimetypes
import os
from typing import Any

from core import util
from core.version import APP_VERSION
from core.components import ComponentManager
from core.service import ApiError, Service
from exports import bundles as bundles_mod
from exports import package as package_mod
from mcp import demo_server
from refs.components_import import ComponentBuilder
from refs.imports import ImportManager
from refs.references import ReferenceManager
from render import placeholders
from server import auth
from server.http import (
    Request,
    Response,
    Router,
    download,
    file_response,
    json_response,
)
from sim import generator, imagery_gen, taxonomy, wizard as wizard_mod
from sim.connected_apps import ConnectedAppManager
from sim.effects import EffectManager
from sim.runs import RunManager
from sim.teach import TeachManager
from sim.teach_ledger import TeachLedger

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(PKG, "web")
SEEDS = os.path.join(PKG, "seeds")
# Upstream A2UI v1 Basic catalog examples (verbatim, seeds/a2ui/).
EXAMPLES_DIR = os.path.join(
    SEEDS, "a2ui", "catalogs", "basic", "v1", "examples"
)
DOCS = os.path.join(PKG, "docs")
PUBLIC_DOCS = ("DESIGN_REFERENCE_GUIDELINES.md",)


class App:

  def __init__(
      self,
      svc: Service,
      runs: RunManager,
      secret: bytes,
      auth_mode: str = "local",
      *,
      password: str | None = None,
  ):
    self.svc = svc
    self.runs = runs
    self.effects = EffectManager(svc, runs)
    self.apps = ConnectedAppManager(svc, runs)
    self.refs = ReferenceManager(svc, pool=runs.pool)
    self.components = ComponentManager(svc, runs)
    self.builder = ComponentBuilder(svc, self.components, self.refs)
    self.imports = ImportManager(
        svc, self.refs, self.builder, self.components, pool=runs.pool
    )
    self.ledger = TeachLedger(svc)
    self.teach = TeachManager(svc, runs, self.imports, self.effects)
    self.store = svc.store
    self.secret = secret
    if auth_mode not in ("local", "password"):
      raise ValueError(f"unknown auth_mode {auth_mode!r}")
    if auth_mode == "password" and not password:
      raise ValueError("auth_mode 'password' needs a password")
    self.auth_mode = auth_mode
    self.password = password
    self.router = Router()
    self._routes()

  # ------------------------------------------------------------ dispatch
  def handle(self, req: Request) -> Response:
    path = req.path
    if path == demo_server_path():
      if req.method != "POST":
        return Response(405, b"", headers={"Allow": "POST"})
      status, body, ctype, hdrs = demo_server.handle_http(
          req.body(), sse=req.query.get("sse") == "1"
      )
      return Response(status, body, ctype, hdrs)
    if path.startswith("/api/"):
      fn, params, auth_needed = self.router.match(
          req.method if req.method != "HEAD" else "GET", path
      )
      if fn is None:
        raise ApiError(
            405 if params is None and auth_needed else 404, "No such endpoint"
        )
      req.params = params
      req.user = self._current_user(req)
      if auth_needed and not req.user:
        raise ApiError(401, "Sign in required")
      return fn(req)
    return self._static(req)

  def _current_user(self, req: Request) -> dict[str, Any] | None:
    if self.auth_mode == "local":
      # No sign-in: a single implicit admin user.
      u = self.svc.user(auth.LOCAL_USER)
      if not u or not u["is_admin"]:
        u = self.svc.login(auth.LOCAL_USER, admin=True)
      return u
    for tok in req.cookies(auth.COOKIE):
      ldap = auth.verify(self.secret, tok)
      if ldap:
        if not self.store.get("users", ldap):
          self.svc.login(ldap)
        return self.svc.user(ldap)
    return None

  def _static(self, req: Request) -> Response:
    p = req.path
    if p.startswith("/files/"):
      user = self._current_user(req)
      if not user:
        # Screenshots are also needed by the headless render pipeline only via
        # /render, so files require sign-in.
        raise ApiError(401, "Sign in required")
      rel = os.path.normpath(p[len("/files/") :]).lstrip("/")
      if rel.startswith("..") or rel.split("/", 1)[0] not in (
          "screens",
          "thumbs",
          "guidelines",
          "references",
          "components",
          "imagery",
          "gen",
          "templates",
          "teach",
      ):
        raise ApiError(404, "Not found")
      return file_response(
          self.store.file_path(rel), cache="private, max-age=86400"
      )
    if p in ("/gen/img", "/gen/svg") or p.startswith(
        ("/gen/img/", "/gen/svg/")
    ):
      q = "&".join(f"{k}={v}" for k, v in (req.query or {}).items())
      full_url = f"{p}?{q}" if q else p
      resolved, _ = imagery_gen.resolve_gen_url(
          full_url,
          store=self.store,
          llm=self.svc.llm,
          project_id=req.query.get("pid")
          or req.query.get("project_id")
          or None,
      )
      if resolved.startswith("/api/gen/"):
        fname = os.path.basename(resolved[len("/api/gen/") :])
        fpath = self.store.file_path("gen", fname)
        if os.path.isfile(fpath):
          return file_response(fpath, cache="public, max-age=604800")
      prompt_q = req.query.get("prompt") or req.query.get("style") or "image"
      if p.startswith("/gen/svg"):
        body = imagery_gen.fallback_icon_svg(prompt_q).encode("utf-8")
      else:
        body = imagery_gen.fallback_raster_svg(prompt_q).encode("utf-8")
      return Response(
          200,
          body,
          "image/svg+xml",
          {"Cache-Control": "public, max-age=3600"},
      )
    if p.startswith("/img/"):
      parts = p.split("/")
      slug = parts[2] if len(parts) > 2 else "image"
      w, h = placeholders.parse_size(parts[3] if len(parts) > 3 else "800x600")
      return Response(
          200,
          placeholders.svg(slug, w, h),
          "image/svg+xml",
          {"Cache-Control": "public, max-age=604800"},
      )
    if p.startswith("/render/"):
      return file_response(os.path.join(WEB, "render.html"))
    if p.startswith("/docs/"):
      name = os.path.basename(p)
      if name not in PUBLIC_DOCS:
        raise ApiError(404, "Not found")
      return file_response(os.path.join(DOCS, name))
    if p.startswith("/seeds/spec_examples/"):
      return file_response(
          os.path.join(EXAMPLES_DIR, os.path.basename(p))
      )
    rel = os.path.normpath(p).lstrip("/")
    if rel.startswith(".."):
      raise ApiError(404, "Not found")
    full = os.path.join(WEB, rel)
    if rel and os.path.isfile(full):
      return file_response(full)
    if "." in os.path.basename(rel):
      raise ApiError(404, "Not found")
    return file_response(os.path.join(WEB, "index.html"))

  # -------------------------------------------------------------- routes
  def _routes(self) -> None:
    r = self.router.add
    # auth
    r("POST", "/api/login", self.login, auth=False)
    r("POST", "/api/logout", self.logout, auth=False)
    r("GET", "/api/me", self.me, auth=False)
    # meta
    r("GET", "/api/genres", lambda q: json_response(self.svc.genres()))
    r("POST", "/api/categories", self.category_create)
    r("PATCH", "/api/categories/{cid}", self.category_patch)
    r("DELETE", "/api/categories/{cid}", self.category_delete)
    r(
        "GET",
        "/api/viewports",
        lambda q: json_response(list(generator.VIEWPORTS.values())),
    )
    r("GET", "/api/renderers", self.renderers_list)
    r("GET", "/api/models", self.models_list, auth=False)
    r("GET", "/api/gen/{fname}", self.gen_file, auth=False)
    # projects
    r("GET", "/api/projects", self.projects_list)
    r("POST", "/api/projects", self.projects_create)
    r("GET", "/api/projects/{pid}", self.project_get)
    r("PATCH", "/api/projects/{pid}", self.project_patch)
    r("DELETE", "/api/projects/{pid}", self.project_delete)
    r("GET", "/api/projects/{pid}/settings", self.settings_get)
    r("PUT", "/api/projects/{pid}/settings", self.settings_put)
    r("GET", "/api/projects/{pid}/dependencies", self.dependencies_get)
    r("GET", "/api/projects/{pid}/token_revisions", self.token_revisions_list)
    r(
        "POST",
        "/api/projects/{pid}/token_revisions/{trid}/revert",
        self.token_revision_revert,
    )
    r("GET", "/api/projects/{pid}/catalog", self.catalog_get)
    r("GET", "/api/projects/{pid}/revisions", self.revisions_list)
    r("POST", "/api/projects/{pid}/revisions", self.revisions_create)
    r("GET", "/api/projects/{pid}/revisions/{rid}", self.revision_get)
    r(
        "POST",
        "/api/projects/{pid}/revisions/{rid}/revert",
        self.revision_revert,
    )
    r("GET", "/api/projects/{pid}/diff", self.revisions_diff)
    r("GET", "/api/projects/{pid}/chat", self.chat_list)
    r("POST", "/api/projects/{pid}/chat", self.chat_post)
    r("POST", "/api/projects/{pid}/chat/{mid}/accept", self.chat_accept)
    r("POST", "/api/projects/{pid}/chat/{mid}/discard", self.chat_discard)
    r("GET", "/api/projects/{pid}/suggestions", self.suggestions)
    r("POST", "/api/projects/{pid}/scope_check", self.scope_check)
    # samples
    r("GET", "/api/samples", self.samples_list)
    r("POST", "/api/samples", self.samples_create)
    r("POST", "/api/samples/synthesize", self.samples_synthesize)
    r("POST", "/api/samples/starter_library", self.samples_starter)
    r("POST", "/api/samples/resolve_images", self.samples_resolve_images)
    r("GET", "/api/samples/{sid}", self.sample_get)
    r("PATCH", "/api/samples/{sid}", self.sample_patch)
    r("DELETE", "/api/samples/{sid}", self.sample_delete)
    r("GET", "/api/sample_sets", self.sets_list)
    r("POST", "/api/sample_sets", self.sets_create)
    r("PATCH", "/api/sample_sets/{sid}", self.set_patch)
    r("DELETE", "/api/sample_sets/{sid}", self.set_delete)
    r("GET", "/api/jobs/{jid}", self.job_get)
    # runs
    r("GET", "/api/projects/{pid}/runs", self.runs_list)
    r("POST", "/api/projects/{pid}/runs", self.runs_create)
    r("GET", "/api/projects/{pid}/compare", self.compare)
    r("GET", "/api/projects/{pid}/effects", self.effects_list)
    r("POST", "/api/projects/{pid}/effects", self.effects_create)
    r("GET", "/api/effects/{eid}", self.effect_get)
    r("POST", "/api/effects/{eid}/analyze", self.effect_analyze)
    r("GET", "/api/runs/{rid}", self.run_get)
    r("DELETE", "/api/runs/{rid}", self.run_delete)
    r("POST", "/api/runs/{rid}/cancel", self.run_cancel)
    r("POST", "/api/runs/{rid}/resume", self.run_resume)
    r("POST", "/api/runs/{rid}/render", self.run_render)
    r("POST", "/api/runs/{rid}/retheme", self.run_retheme)
    r("GET", "/api/runs/{rid}/outputs", self.outputs_list)
    r("GET", "/api/outputs/{oid}", self.output_get)
    r("POST", "/api/outputs/{oid}/verdict", self.output_verdict)
    r("POST", "/api/outputs/{oid}/rejudge", self.output_rejudge)
    r("POST", "/api/outputs/{oid}/regenerate", self.output_regenerate)
    r("POST", "/api/outputs/{oid}/render", self.output_render)
    # render (token-auth; used by headless Chrome and iframes)
    r("GET", "/api/render_data/{oid}", self.render_data, auth=False)
    r("GET", "/api/render_example/{name}", self.render_example, auth=False)
    r("GET", "/api/render_examples", self.render_examples, auth=False)
    # guidelines
    r("GET", "/api/projects/{pid}/guidelines", self.guidelines_list)
    r("POST", "/api/projects/{pid}/guidelines", self.guidelines_create)
    r("GET", "/api/guidelines/{gid}", self.guideline_get)
    r("PATCH", "/api/guidelines/{gid}", self.guideline_patch)
    r("DELETE", "/api/guidelines/{gid}", self.guideline_delete)
    r("POST", "/api/guidelines/{gid}/screenshots", self.guideline_screenshot)
    r("POST", "/api/guidelines/{gid}/escalate", self.guideline_escalate)
    # exports
    r("GET", "/api/export/projects/{pid}/constitution.md", self.export_md)
    r("GET", "/api/export/projects/{pid}/history.json", self.export_history)
    r(
        "GET",
        "/api/export/projects/{pid}/guidelines.zip",
        self.export_guidelines,
    )
    # connected apps (MCP)
    r("GET", "/api/connected_apps", self.apps_list)
    r("POST", "/api/connected_apps", self.apps_create)
    r("POST", "/api/connected_apps/demo", self.apps_demo)
    r("GET", "/api/connected_apps/{aid}", self.app_get)
    r("PATCH", "/api/connected_apps/{aid}", self.app_patch)
    r("DELETE", "/api/connected_apps/{aid}", self.app_delete)
    r("POST", "/api/connected_apps/{aid}/sync", self.app_sync)
    r("POST", "/api/connected_apps/{aid}/form", self.app_form)
    r("POST", "/api/connected_apps/{aid}/call", self.app_call)
    r("POST", "/api/connected_apps/{aid}/import", self.app_import)
    # design references
    r("GET", "/api/me/figma", self.figma_get)
    r("PUT", "/api/me/figma", self.figma_put)
    r("GET", "/api/figma/projects", self.figma_projects_list)
    r("POST", "/api/figma/projects", self.figma_projects_add)
    r("DELETE", "/api/figma/projects/{key}", self.figma_projects_delete)
    r("POST", "/api/figma/frames", self.figma_frames_list)
    r("GET", "/api/me/stitch", self.stitch_get)
    r("PUT", "/api/me/stitch", self.stitch_put)
    r("GET", "/api/stitch/projects", self.stitch_projects_list)
    r("POST", "/api/stitch/screens", self.stitch_screens)
    r("GET", "/api/projects/{pid}/references", self.refs_list)
    r("POST", "/api/projects/{pid}/references", self.refs_create)
    r("GET", "/api/references/{ref}", self.ref_get)
    r("PATCH", "/api/references/{ref}", self.ref_patch)
    r("DELETE", "/api/references/{ref}", self.ref_delete)
    r("POST", "/api/references/{ref}/analyze", self.ref_analyze)
    # imports: analyze → questions → merge plan → apply (docs/IMPORTS.md)
    r("GET", "/api/projects/{pid}/imports", self.imports_list)
    r("GET", "/api/imports/{iid}", self.import_get)
    r("POST", "/api/imports/{iid}/analyze", self.import_analyze)
    r("POST", "/api/imports/{iid}/confirm", self.import_confirm)
    r("POST", "/api/imports/{iid}/questions/{qid}", self.import_answer)
    r("POST", "/api/imports/{iid}/extractions", self.import_x_add)
    r("POST", "/api/imports/{iid}/extractions/merge", self.import_x_merge)
    r("PATCH", "/api/imports/{iid}/extractions/{xid}", self.import_x_patch)
    r("DELETE", "/api/imports/{iid}/extractions/{xid}", self.import_x_delete)
    r("POST", "/api/imports/{iid}/merge/prepare", self.import_prepare)
    r("POST", "/api/imports/{iid}/merge/apply", self.import_apply)
    r("POST", "/api/imports/{iid}/data-source", self.import_data_source)
    r("GET", "/api/projects/{pid}/templates", self.templates_list)
    r("GET", "/api/templates/{tid}", self.template_get)
    r("PATCH", "/api/templates/{tid}", self.template_patch)
    r("DELETE", "/api/templates/{tid}", self.template_delete)
    # design system: custom components + package
    r("GET", "/api/projects/{pid}/components", self.components_list)
    r("POST", "/api/projects/{pid}/components", self.components_create)
    r(
        "POST",
        "/api/projects/{pid}/components/figma/scan",
        self.components_figma_scan,
    )
    r("POST", "/api/projects/{pid}/components/import", self.components_import)
    r(
        "POST",
        "/api/projects/{pid}/components/restrategize",
        self.components_restrategize,
    )
    r("GET", "/api/components/{cid}", self.component_get)
    r("PATCH", "/api/components/{cid}", self.component_patch)
    r("DELETE", "/api/components/{cid}", self.component_delete)
    r("POST", "/api/components/{cid}/revise", self.component_revise)
    r("POST", "/api/components/{cid}/sample", self.component_sample)
    r("POST", "/api/components/{cid}/recheck", self.component_recheck)
    r("POST", "/api/components/{cid}/apply_tokens", self.component_apply_tokens)
    r("POST", "/api/components/{cid}/to_template", self.component_to_template)
    r(
        "GET",
        "/api/render_component/{cid}",
        self.render_component,
        auth=False,
    )
    r("GET", "/api/projects/{pid}/catalog.json", self.catalog_json)
    r("GET", "/api/projects/{pid}/package", self.package_check)
    r("GET", "/api/projects/{pid}/package/files", self.package_files)
    r(
        "GET",
        "/api/projects/{pid}/package/files/{path:path}",
        self.package_file,
    )
    r(
        "GET",
        "/api/export/projects/{pid}/package.zip",
        self.export_package,
    )
    # imagery & iconography styles
    r("GET", "/api/projects/{pid}/imagery", self.imagery_list)
    r("POST", "/api/projects/{pid}/imagery", self.imagery_create)
    r("PATCH", "/api/projects/{pid}/imagery/{sid}", self.imagery_patch)
    r("DELETE", "/api/projects/{pid}/imagery/{sid}", self.imagery_delete)
    r(
        "POST",
        "/api/projects/{pid}/imagery/{sid}/preview",
        self.imagery_preview,
    )
    r(
        "GET",
        "/api/projects/{pid}/imagery/{sid}/refs/{idx}",
        self.imagery_ref_get,
    )
    # new-project setup (scenario domains for teaching sessions)
    r("GET", "/api/wizard/domains", self.wizard_domains)
    # teaching sessions + teachings ledger (docs/TEACH.md)
    r("GET", "/api/projects/{pid}/teach", self.teach_list)
    r("POST", "/api/projects/{pid}/teach", self.teach_create)
    r("GET", "/api/teach/{sid}", self.teach_get)
    r("DELETE", "/api/teach/{sid}", self.teach_delete)
    r("POST", "/api/teach/{sid}/cards", self.teach_add_cards)
    r("POST", "/api/teach/{sid}/observations", self.teach_obs_create)
    r("PATCH", "/api/teach/observations/{obid}", self.teach_obs_patch)
    r("DELETE", "/api/teach/observations/{obid}", self.teach_obs_delete)
    r("POST", "/api/teach/{sid}/distill", self.teach_distill)
    r("POST", "/api/teach/{sid}/assemble", self.teach_assemble)
    r("POST", "/api/teach/{sid}/finish", self.teach_finish)
    r("POST", "/api/teach/{sid}/graduate", self.teach_graduate)
    r("POST", "/api/teach/{sid}/voice_turn", self.teach_voice_turn)
    r("POST", "/api/teach/patterns/draft", self.pattern_draft)
    r("POST", "/api/teach/patterns/check", self.pattern_check)
    r("POST", "/api/teach/patterns", self.pattern_save)
    r("GET", "/api/projects/{pid}/teachings", self.teachings_list)
    r("GET", "/api/teachings/{tid}", self.teaching_get)
    r("PATCH", "/api/teachings/{tid}", self.teaching_patch)
    r("DELETE", "/api/teachings/{tid}", self.teaching_delete)
    r("POST", "/api/teachings/{tid}/applications", self.teaching_applications)
    r("POST", "/api/teachings/{tid}/probe", self.teaching_probe)
    r("POST", "/api/teachings/{tid}/rephrase", self.teaching_rephrase)
    r("POST", "/api/teachings/{tid}/escalate", self.teaching_escalate)
    r("POST", "/api/teachings/{tid}/split", self.teaching_split)
    r("GET", "/api/outputs/{oid}/links", self.output_links)
    # admin
    r("GET", "/api/admin/overview", self.admin_overview)
    r("GET", "/api/admin/inbox", self.admin_inbox)
    r("GET", "/api/admin/bundles", self.admin_bundles)
    r("POST", "/api/admin/bundles", self.admin_bundle_create)
    r("GET", "/api/admin/bundles/{bid}", self.admin_bundle_download)

  # ---------------------------------------------------- teaching sessions
  def teach_list(self, req: Request) -> Response:
    return json_response(
        {"sessions": self.teach.list_sessions(req.user, req.params["pid"])}
    )

  def teach_create(self, req: Request) -> Response:
    return json_response(
        self.teach.create_session(
            req.user, req.params["pid"], req.body() or {}
        ),
        201,
    )

  def teach_get(self, req: Request) -> Response:
    return json_response(self.teach.state(req.user, req.params["sid"]))

  def teach_delete(self, req: Request) -> Response:
    self.teach.delete_session(req.user, req.params["sid"])
    return json_response({"ok": True})

  def teach_add_cards(self, req: Request) -> Response:
    return json_response(
        self.teach.add_cards(req.user, req.params["sid"], req.body() or {})
    )

  def teach_obs_create(self, req: Request) -> Response:
    ob = self.teach.add_observation(
        req.user, req.params["sid"], req.body() or {}
    )
    return json_response(
        {
            "observation": ob,
            "state": self.teach.state(req.user, req.params["sid"]),
        },
        201,
    )

  def teach_obs_patch(self, req: Request) -> Response:
    return json_response({
        "observation": self.teach.patch_observation(
            req.user, req.params["obid"], req.body() or {}
        )
    })

  def teach_obs_delete(self, req: Request) -> Response:
    self.teach.delete_observation(req.user, req.params["obid"])
    return json_response({"ok": True})

  def teach_distill(self, req: Request) -> Response:
    return json_response(
        self.teach.distill(req.user, req.params["sid"], req.body() or {})
    )

  def teach_assemble(self, req: Request) -> Response:
    return json_response(
        self.teach.assemble(req.user, req.params["sid"], req.body() or {})
    )

  def teach_finish(self, req: Request) -> Response:
    return json_response(
        self.teach.finish(req.user, req.params["sid"], req.body() or {})
    )

  def teach_graduate(self, req: Request) -> Response:
    return json_response(self.teach.graduate(req.user, req.params["sid"]))

  def teach_voice_turn(self, req: Request) -> Response:
    return json_response(
        self.teach.voice_turn(req.user, req.params["sid"], req.body() or {})
    )

  def pattern_draft(self, req: Request) -> Response:
    return json_response(self.teach.draft_pattern(req.user, req.body() or {}))

  def pattern_check(self, req: Request) -> Response:
    return json_response(self.teach.check_pattern(req.user, req.body() or {}))

  def pattern_save(self, req: Request) -> Response:
    return json_response(
        self.teach.save_pattern(req.user, req.body() or {}), 201
    )

  def teachings_list(self, req: Request) -> Response:
    return json_response(
        self.ledger.list_project(
            req.user,
            req.params["pid"],
            template_fmt=self.imports.fmt_template,
            status=req.query.get("status") or None,
            kind=req.query.get("kind") or None,
            labels=self.teach.labels(req.params["pid"]),
        )
    )

  def _trial_output(self, oid: str | None) -> dict[str, Any] | None:
    o = self.store.get("outputs", oid) if oid else None
    if not o:
      return None
    fo = self.runs.fmt_output(o)
    return {
        "id": o["id"],
        "thumb_url": fo.get("thumb_url"),
        "screenshot_url": fo.get("screenshot_url"),
        "sample_title": fo.get("sample_title"),
        "run_id": o.get("run_id"),
    }

  def teaching_get(self, req: Request) -> Response:
    return json_response(
        self.ledger.detail(
            req.user,
            req.params["tid"],
            template_fmt=self.imports.fmt_template,
            output_fmt=self._trial_output,
            labels_fn=self.teach.labels,
        )
    )

  def teaching_patch(self, req: Request) -> Response:
    return json_response({
        "teaching": self.teach.patch_teaching(
            req.user, req.params["tid"], req.body() or {}
        )
    })

  def teaching_delete(self, req: Request) -> Response:
    self.ledger.delete(req.user, req.params["tid"])
    return json_response({"ok": True})

  def teaching_applications(self, req: Request) -> Response:
    return json_response(
        {"teaching": self.teach.applications(req.user, req.params["tid"])}
    )

  def teaching_probe(self, req: Request) -> Response:
    return json_response(
        self.teach.probe(req.user, req.params["tid"], req.body() or {})
    )

  def teaching_rephrase(self, req: Request) -> Response:
    return json_response(self.teach.rephrase(req.user, req.params["tid"]))

  def teaching_escalate(self, req: Request) -> Response:
    res = self.ledger.escalate(
        req.user, req.params["tid"], (req.body() or {}).get("note") or ""
    )
    labels = self.teach.labels(res["teaching"]["project_id"])
    return json_response({
        "teaching": self.teach.fmt_teaching(res["teaching"], labels),
        "guideline": res["guideline"],
    })

  def teaching_split(self, req: Request) -> Response:
    try:
      version = int((req.body() or {}).get("version") or 0)
    except (TypeError, ValueError) as e:
      raise ApiError(400, "version must be a number") from e
    res = self.ledger.split(req.user, req.params["tid"], version)
    labels = self.teach.labels(res["teaching"]["project_id"])
    return json_response({
        "teaching": self.teach.fmt_teaching(res["teaching"], labels),
        "new_teaching": self.teach.fmt_teaching(res["new_teaching"], labels),
    })

  def output_links(self, req: Request) -> Response:
    return json_response(self.teach.output_links(req.user, req.params["oid"]))

  # ------------------------------------------------------ connected apps
  def apps_list(self, req: Request) -> Response:
    return json_response({"apps": self.apps.list(req.user)})

  def apps_create(self, req: Request) -> Response:
    return json_response(
        {"app": self.apps.create(req.user, req.body() or {})}, 201
    )

  def apps_demo(self, req: Request) -> Response:
    return json_response({"app": self.apps.create_demo(req.user)})

  def app_get(self, req: Request) -> Response:
    return json_response({"app": self.apps.get(req.user, req.params["aid"])})

  def app_patch(self, req: Request) -> Response:
    return json_response(
        {"app": self.apps.update(req.user, req.params["aid"], req.body() or {})}
    )

  def app_delete(self, req: Request) -> Response:
    self.apps.delete(req.user, req.params["aid"])
    return json_response({"ok": True})

  def app_sync(self, req: Request) -> Response:
    return json_response({"app": self.apps.sync(req.user, req.params["aid"])})

  def app_form(self, req: Request) -> Response:
    return json_response(
        self.apps.form(req.user, req.params["aid"], req.body() or {})
    )

  def app_call(self, req: Request) -> Response:
    return json_response(
        self.apps.call(req.user, req.params["aid"], req.body() or {})
    )

  def app_import(self, req: Request) -> Response:
    return json_response(
        {
            "sample": self.apps.import_result(
                req.user, req.params["aid"], req.body() or {}
            )
        },
        201,
    )

  # --------------------------------------------------- design references
  def figma_get(self, req: Request) -> Response:
    return json_response(self.refs.figma_status(req.user))

  def figma_put(self, req: Request) -> Response:
    return json_response(
        self.refs.set_figma_token(req.user, (req.body() or {}).get("token", ""))
    )

  def figma_projects_list(self, req: Request) -> Response:
    return json_response(
        self.refs.list_figma_projects(
            req.user, refresh=req.query.get("refresh") in ("1", "true")
        )
    )

  def figma_projects_add(self, req: Request) -> Response:
    return json_response(
        self.refs.add_figma_source(
            req.user, str((req.body() or {}).get("url") or "")
        ),
        201,
    )

  def figma_projects_delete(self, req: Request) -> Response:
    return json_response(
        self.refs.remove_figma_source(req.user, req.params.get("key") or "")
    )

  def figma_frames_list(self, req: Request) -> Response:
    b = req.body() or {}
    return json_response(
        self.refs.list_figma_frames(
            req.user, str(b.get("url") or b.get("file_key") or "")
        )
    )

  def stitch_get(self, req: Request) -> Response:
    return json_response(self.refs.stitch_status(req.user))

  def stitch_put(self, req: Request) -> Response:
    return json_response(
        self.refs.set_stitch_key(req.user, (req.body() or {}).get("key", ""))
    )

  def stitch_projects_list(self, req: Request) -> Response:
    return json_response(self.refs.list_stitch_projects(req.user))

  def stitch_screens(self, req: Request) -> Response:
    return json_response(
        self.refs.list_stitch_screens(
            req.user, (req.body() or {}).get("url", "")
        )
    )

  def refs_list(self, req: Request) -> Response:
    return json_response(
        {"references": self.refs.list(req.user, req.params["pid"])}
    )

  def refs_create(self, req: Request) -> Response:
    return json_response(
        {
            "reference": self.refs.create(
                req.user, req.params["pid"], req.body() or {}
            )
        },
        201,
    )

  def ref_get(self, req: Request) -> Response:
    return json_response(
        {"reference": self.refs.get(req.user, req.params["ref"])}
    )

  def ref_patch(self, req: Request) -> Response:
    return json_response({
        "reference": self.refs.update(
            req.user, req.params["ref"], req.body() or {}
        )
    })

  def ref_delete(self, req: Request) -> Response:
    self.refs.delete(req.user, req.params["ref"])
    return json_response({"ok": True})

  def ref_analyze(self, req: Request) -> Response:
    return json_response({
        "reference": self.refs.analyze(
            req.user, req.params["ref"], req.body() or {}
        )
    })

  # ------------------------------------------------------------- imports
  def imports_list(self, req: Request) -> Response:
    return json_response(
        {"imports": self.imports.list(req.user, req.params["pid"])}
    )

  def import_get(self, req: Request) -> Response:
    return json_response(
        {"import": self.imports.get(req.user, req.params["iid"])}
    )

  def import_analyze(self, req: Request) -> Response:
    return json_response({
        "import": self.imports.analyze(
            req.user, req.params["iid"], req.body() or {}
        )
    })

  def import_confirm(self, req: Request) -> Response:
    return json_response({
        "import": self.imports.confirm(
            req.user, req.params["iid"], req.body() or {}
        )
    })

  def import_answer(self, req: Request) -> Response:
    return json_response({
        "import": self.imports.answer_question(
            req.user, req.params["iid"], req.params["qid"], req.body() or {}
        )
    })

  def import_x_add(self, req: Request) -> Response:
    return json_response(
        {
            "import": self.imports.add_extraction(
                req.user, req.params["iid"], req.body() or {}
            )
        },
        201,
    )

  def import_x_merge(self, req: Request) -> Response:
    return json_response({
        "import": self.imports.merge_extractions(
            req.user, req.params["iid"], req.body() or {}
        )
    })

  def import_x_patch(self, req: Request) -> Response:
    return json_response({
        "import": self.imports.patch_extraction(
            req.user, req.params["iid"], req.params["xid"], req.body() or {}
        )
    })

  def import_x_delete(self, req: Request) -> Response:
    return json_response({
        "import": self.imports.delete_extraction(
            req.user, req.params["iid"], req.params["xid"]
        )
    })

  def import_prepare(self, req: Request) -> Response:
    return json_response({
        "import": self.imports.prepare_merge(
            req.user, req.params["iid"], req.body() or {}
        )
    })

  def import_apply(self, req: Request) -> Response:
    return json_response(
        self.imports.apply_merge(req.user, req.params["iid"], req.body() or {})
    )

  def import_data_source(self, req: Request) -> Response:
    return json_response(
        self.imports.set_default_data_source(
            req.user, req.params["iid"], req.body() or {}
        )
    )

  def templates_list(self, req: Request) -> Response:
    return json_response(
        {"templates": self.imports.list_templates(req.user, req.params["pid"])}
    )

  def template_get(self, req: Request) -> Response:
    return json_response(
        {"template": self.imports.get_template(req.user, req.params["tid"])}
    )

  def template_patch(self, req: Request) -> Response:
    return json_response({
        "template": self.imports.update_template(
            req.user, req.params["tid"], req.body() or {}
        )
    })

  def template_delete(self, req: Request) -> Response:
    self.imports.delete_template(req.user, req.params["tid"])
    return json_response({"ok": True})

  # ------------------------------------------------------- design system
  def components_list(self, req: Request) -> Response:
    return json_response(
        self.components.list(
            req.user,
            req.params["pid"],
            archived=req.query.get("archived") in ("1", "true"),
        )
    )

  def components_create(self, req: Request) -> Response:
    return json_response(
        {
            "component": self.components.create(
                req.user, req.params["pid"], req.body() or {}
            )
        },
        201,
    )

  def components_figma_scan(self, req: Request) -> Response:
    b = req.body() or {}
    return json_response(
        self.builder.scan_figma(
            req.user, req.params["pid"], str(b.get("url") or "")
        )
    )

  def components_import(self, req: Request) -> Response:
    return json_response(
        self.builder.import_(req.user, req.params["pid"], req.body() or {}),
        201,
    )

  def components_restrategize(self, req: Request) -> Response:
    return json_response(
        self.components.restrategize(
            req.user, req.params["pid"], req.body() or {}
        )
    )

  def component_get(self, req: Request) -> Response:
    return json_response(
        {"component": self.components.get(req.user, req.params["cid"])}
    )

  def component_patch(self, req: Request) -> Response:
    return json_response({
        "component": self.components.update(
            req.user, req.params["cid"], req.body() or {}
        )
    })

  def component_delete(self, req: Request) -> Response:
    self.components.delete(req.user, req.params["cid"])
    return json_response({"ok": True})

  def component_revise(self, req: Request) -> Response:
    b = req.body() or {}
    return json_response(
        self.builder.revise(
            req.user,
            req.params["cid"],
            str(b.get("instruction") or ""),
            definition=b.get("definition")
            if isinstance(b.get("definition"), dict)
            else None,
            issues=b.get("issues")
            if isinstance(b.get("issues"), list)
            else None,
            fix=bool(b.get("fix")),
        )
    )

  def component_sample(self, req: Request) -> Response:
    b = req.body() or {}
    return json_response(
        self.builder.sample(
            req.user, req.params["cid"], str(b.get("instruction") or "")
        )
    )

  def component_recheck(self, req: Request) -> Response:
    req.body()
    return json_response(
        {"component": self.builder.recheck(req.user, req.params["cid"])}
    )

  def component_apply_tokens(self, req: Request) -> Response:
    req.body()
    return json_response(
        self.components.apply_tokens(req.user, req.params["cid"])
    )

  def component_to_template(self, req: Request) -> Response:
    req.body()
    return json_response(
        self.components.promote_to_template(req.user, req.params["cid"]), 201
    )

  def render_component(self, req: Request) -> Response:
    return json_response(
        self.components.render_data(
            req.params["cid"],
            req.query.get("token", ""),
            req.query.get("viewport"),
        )
    )

  def catalog_json(self, req: Request) -> Response:
    return json_response(
        self.components.catalog_json(req.user, req.params["pid"])
    )

  def package_check(self, req: Request) -> Response:
    return json_response(
        self.components.package_check(req.user, req.params["pid"])
    )

  def package_files(self, req: Request) -> Response:
    p = self._proj(req)
    root, files = package_mod.package_files(self.store, self.svc, p)
    return json_response({
        "root": root,
        "files": [
            {"path": path, "size": len(data)} for path, data in files.items()
        ],
    })

  def package_file(self, req: Request) -> Response:
    """Serves one package file inline (relative links in index.html work)."""
    p = self._proj(req)
    _, files = package_mod.package_files(self.store, self.svc, p)
    path = req.params["path"]
    if path not in files:
      raise ApiError(404, "No such file in the package")
    ctype = mimetypes.guess_type(path)[0] or "text/plain"
    if path.endswith((".kt", ".swift", ".md", ".txt")):
      ctype = "text/plain"
    if ctype.startswith("text/") or ctype == "application/json":
      ctype += "; charset=utf-8"
    return Response(200, files[path], ctype, {"Cache-Control": "no-store"})

  def export_package(self, req: Request) -> Response:
    p = self._proj(req)
    name, data = package_mod.package_zip(self.store, self.svc, p)
    return download(data, name, "application/zip")

  # ---------------------------------------------------------------- auth
  def login(self, req: Request) -> Response:
    if self.auth_mode == "local":
      return json_response({"user": self._current_user(req)})
    body = req.body() or {}
    if not auth.check_password(self.password, str(body.get("password") or "")):
      raise ApiError(401, "Wrong password")
    name = str(body.get("username") or body.get("ldap") or "").strip()
    user = self.svc.login(name or auth.DEFAULT_PASSWORD_USER)
    return json_response(
        {"user": user},
        headers={
            "Set-Cookie": auth.cookie_header(
                auth.sign(self.secret, user["ldap"])
            )
        },
    )

  def logout(self, req: Request) -> Response:
    del req
    return json_response(
        {"ok": True}, headers={"Set-Cookie": auth.cookie_header("", clear=True)}
    )

  def me(self, req: Request) -> Response:
    return json_response({
        "user": req.user,
        "auth_mode": self.auth_mode,
        "version": APP_VERSION,
    })

  def renderers_list(self, req: Request) -> Response:
    del req
    return json_response({"renderers": self.runs.renderer_statuses()})

  def models_list(self, req: Request) -> Response:
    del req
    return json_response(self.svc.models_catalog())

  def gen_file(self, req: Request) -> Response:
    fname = os.path.basename(req.params.get("fname") or "")
    if not fname or ".." in fname:
      raise ApiError(404, "Not found")
    path = self.store.file_path("gen", fname)
    if not os.path.isfile(path):
      raise ApiError(404, "Generated asset not found")
    return file_response(path, cache="public, max-age=604800")

  # --------------------------------------------------- imagery & icons
  def imagery_list(self, req: Request) -> Response:
    return json_response({
        "styles": self.svc.list_imagery_styles(
            req.user,
            req.params["pid"],
            include_archived=req.query.get("archived") in ("1", "true"),
        )
    })

  def imagery_create(self, req: Request) -> Response:
    return json_response(
        {
            "style": self.svc.upsert_imagery_style(
                req.user, req.params["pid"], req.body() or {}
            )
        },
        201,
    )

  def imagery_patch(self, req: Request) -> Response:
    return json_response({
        "style": self.svc.upsert_imagery_style(
            req.user,
            req.params["pid"],
            req.body() or {},
            sid=req.params["sid"],
        )
    })

  def imagery_delete(self, req: Request) -> Response:
    self.svc.delete_imagery_style(
        req.user, req.params["pid"], req.params["sid"]
    )
    return json_response({"ok": True})

  def imagery_preview(self, req: Request) -> Response:
    b = req.body() or {}
    return json_response(
        self.svc.preview_imagery_style(
            req.user,
            req.params["pid"],
            req.params["sid"],
            prompt=str(b.get("prompt") or ""),
        )
    )

  def imagery_ref_get(self, req: Request) -> Response:
    try:
      idx = int(req.params["idx"])
    except (TypeError, ValueError) as e:
      raise ApiError(400, "Invalid reference index") from e
    path = self.svc.imagery_ref_path(
        req.user, req.params["pid"], req.params["sid"], idx
    )
    return file_response(path, cache="private, max-age=86400")

  # ------------------------------------------------------------ projects
  def projects_list(self, req: Request) -> Response:
    return json_response({
        "projects": self.svc.list_projects(
            req.user, req.query.get("all") == "1"
        )
    })

  def projects_create(self, req: Request) -> Response:
    b = req.body()
    return json_response(
        {
            "project": self.svc.create_project(
                req.user,
                b.get("name", ""),
                b.get("description", ""),
                b.get("constitution_md", ""),
            )
        },
        201,
    )

  def project_get(self, req: Request) -> Response:
    return json_response(
        {"project": self.svc.get_project(req.user, req.params["pid"])}
    )

  def project_patch(self, req: Request) -> Response:
    return json_response({
        "project": self.svc.update_project(
            req.user, req.params["pid"], req.body()
        )
    })

  def project_delete(self, req: Request) -> Response:
    self.svc.delete_project(req.user, req.params["pid"])
    return json_response({"ok": True})

  def settings_get(self, req: Request) -> Response:
    return json_response(
        {"settings": self.svc.get_settings(req.user, req.params["pid"])}
    )

  def settings_put(self, req: Request) -> Response:
    b = req.body() or {}
    raw_settings = b.get("settings", b)
    source = (
        str(b.get("source") or "manual") if isinstance(b, dict) else "manual"
    )
    summary = str(b.get("summary") or "") if isinstance(b, dict) else ""
    merged = self.svc.put_settings(
        req.user,
        req.params["pid"],
        raw_settings,
        source=source,
        summary=summary,
    )
    tok_rev = self.svc.ensure_current_token_revision(
        req.params["pid"], req.user["ldap"]
    )
    return json_response({
        "settings": merged,
        "token_revision": (
            {
                "id": tok_rev["id"],
                "number": tok_rev["number"],
                "theme_hash": tok_rev["theme_hash"],
                "diff": tok_rev.get("diff") or {},
                "summary": tok_rev["summary"],
                "source": tok_rev["source"],
                "created_at": tok_rev["created_at"],
            }
            if tok_rev
            else None
        ),
    })

  def dependencies_get(self, req: Request) -> Response:
    return json_response(
        self.svc.get_dependencies(
            req.user,
            req.params["pid"],
            revision_id=req.query.get("revision_id") or None,
        )
    )

  def token_revisions_list(self, req: Request) -> Response:
    return json_response({
        "token_revisions": self.svc.list_token_revisions(
            req.user, req.params["pid"]
        )
    })

  def token_revision_revert(self, req: Request) -> Response:
    req.body()
    return json_response(
        self.svc.revert_token_revision(
            req.user, req.params["pid"], req.params["trid"]
        )
    )

  def catalog_get(self, req: Request) -> Response:
    return json_response(self.svc.get_catalog(req.user, req.params["pid"]))

  def revisions_list(self, req: Request) -> Response:
    return json_response(
        {"revisions": self.svc.list_revisions(req.user, req.params["pid"])}
    )

  def revisions_create(self, req: Request) -> Response:
    b = req.body()
    return json_response(
        {
            "revision": self.svc.add_revision(
                req.user,
                req.params["pid"],
                b.get("text_md", ""),
                b.get("summary", ""),
                "manual",
            )
        },
        201,
    )

  def revision_get(self, req: Request) -> Response:
    return json_response({
        "revision": self.svc.get_revision(
            req.user, req.params["pid"], req.params["rid"]
        )
    })

  def revision_revert(self, req: Request) -> Response:
    return json_response({
        "revision": self.svc.revert(
            req.user, req.params["pid"], req.params["rid"]
        )
    })

  def revisions_diff(self, req: Request) -> Response:
    pid = req.params["pid"]
    a = req.query.get("a")
    b = req.query.get("b") or self.svc.current_revision(pid)["id"]
    if not a:
      raise ApiError(400, "a is required")
    return json_response({"diff": self.svc.diff_revisions(req.user, pid, a, b)})

  def chat_list(self, req: Request) -> Response:
    return json_response(
        {"messages": self.svc.list_chat(req.user, req.params["pid"])}
    )

  def chat_post(self, req: Request) -> Response:
    b = req.body()
    return json_response(
        self.svc.post_chat(
            req.user,
            req.params["pid"],
            b.get("text", ""),
            b.get("output_ids"),
            b.get("guideline_ids"),
            b.get("draft_md"),
            selected_component=b.get("selected_component"),
            attached_images=b.get("attached_images"),
        )
    )

  def chat_accept(self, req: Request) -> Response:
    b = req.body() or {}
    res = self.svc.accept_chat(
        req.user,
        req.params["pid"],
        req.params["mid"],
        b.get("constitution_md"),
        apply_theme=b.get("apply_theme", True),
        apply_catalog=b.get("apply_catalog", True),
    )
    return json_response(res)

  def chat_discard(self, req: Request) -> Response:
    return json_response({
        "message": self.svc.discard_chat(
            req.user, req.params["pid"], req.params["mid"]
        )
    })

  def suggestions(self, req: Request) -> Response:
    return json_response(
        {"suggestions": self.svc.suggestions(req.user, req.params["pid"])}
    )

  def scope_check(self, req: Request) -> Response:
    return json_response(
        self.svc.scope_check(
            req.user, req.params["pid"], (req.body() or {}).get("text_md")
        )
    )

  # ------------------------------------------------------------- samples
  def samples_list(self, req: Request) -> Response:
    q = req.query
    return json_response({
        "samples": self.svc.list_samples(
            q.get("genre", ""),
            q.get("group", ""),
            q.get("q", ""),
            q.get("set_id", ""),
        )
    })

  def samples_create(self, req: Request) -> Response:
    return json_response(
        {"sample": self.svc.create_sample(req.user, req.body(), "manual")}, 201
    )

  def samples_synthesize(self, req: Request) -> Response:
    b = req.body()
    return json_response({
        "job": self.runs.synthesize(
            req.user,
            b.get("genres") or [],
            b.get("per_genre") or 3,
            b.get("set_name"),
            brief=b.get("brief") or "",
            count=b.get("count") or 0,
        )
    })

  def samples_resolve_images(self, req: Request) -> Response:
    b = req.body() or {}
    return json_response(
        {"job": self.runs.resolve_images(req.user, b.get("sample_ids"))}
    )

  def category_create(self, req: Request) -> Response:
    return json_response(
        {"category": self.svc.create_category(req.user, req.body() or {})},
        201,
    )

  def category_patch(self, req: Request) -> Response:
    return json_response({
        "category": self.svc.update_category(
            req.user, req.params["cid"], req.body() or {}
        )
    })

  def category_delete(self, req: Request) -> Response:
    self.svc.delete_category(req.user, req.params["cid"])
    return json_response({"ok": True})

  def samples_starter(self, req: Request) -> Response:
    return json_response({"job": self.runs.starter_library(req.user)})

  def sample_get(self, req: Request) -> Response:
    return json_response({"sample": self.svc.get_sample(req.params["sid"])})

  def sample_patch(self, req: Request) -> Response:
    return json_response({
        "sample": self.svc.update_sample(
            req.user, req.params["sid"], req.body()
        )
    })

  def sample_delete(self, req: Request) -> Response:
    self.svc.delete_sample(req.user, req.params["sid"])
    return json_response({"ok": True})

  def sets_list(self, req: Request) -> Response:
    del req
    return json_response({"sets": self.svc.list_sets()})

  def sets_create(self, req: Request) -> Response:
    b = req.body()
    return json_response(
        {
            "set": self.svc.create_set(
                req.user,
                b.get("name", ""),
                b.get("sample_ids") or [],
                b.get("description", ""),
            )
        },
        201,
    )

  def set_patch(self, req: Request) -> Response:
    return json_response(
        {"set": self.svc.update_set(req.user, req.params["sid"], req.body())}
    )

  def set_delete(self, req: Request) -> Response:
    self.svc.delete_set(req.user, req.params["sid"])
    return json_response({"ok": True})

  def job_get(self, req: Request) -> Response:
    return json_response({"job": self.runs.get_job(req.params["jid"])})

  # ---------------------------------------------------------------- runs
  def runs_list(self, req: Request) -> Response:
    return json_response(
        {"runs": self.runs.list_runs(req.user, req.params["pid"])}
    )

  def runs_create(self, req: Request) -> Response:
    return json_response(
        {"run": self.runs.create_run(req.user, req.params["pid"], req.body())},
        201,
    )

  def compare(self, req: Request) -> Response:
    return json_response(
        self.runs.compare(
            req.user,
            req.params["pid"],
            req.query.get("run_a", ""),
            req.query.get("run_b", ""),
        )
    )

  def effects_list(self, req: Request) -> Response:
    return json_response(
        {"effects": self.effects.list(req.user, req.params["pid"])}
    )

  def effects_create(self, req: Request) -> Response:
    return json_response(
        self.effects.create(req.user, req.params["pid"], req.body()), 201
    )

  def effect_get(self, req: Request) -> Response:
    return json_response(self.effects.get(req.user, req.params["eid"]))

  def effect_analyze(self, req: Request) -> Response:
    return json_response(self.effects.analyze(req.user, req.params["eid"]))

  def run_get(self, req: Request) -> Response:
    return json_response(
        {"run": self.runs.get_run(req.user, req.params["rid"])}
    )

  def run_delete(self, req: Request) -> Response:
    return json_response(
        {"ok": True, **self.runs.delete(req.user, req.params["rid"])}
    )

  def run_cancel(self, req: Request) -> Response:
    return json_response({"run": self.runs.cancel(req.user, req.params["rid"])})

  def run_resume(self, req: Request) -> Response:
    return json_response({"run": self.runs.resume(req.user, req.params["rid"])})

  def outputs_list(self, req: Request) -> Response:
    return json_response({
        "outputs": self.runs.list_outputs(
            req.user, req.params["rid"], req.query
        )
    })

  def output_get(self, req: Request) -> Response:
    return json_response(self.runs.get_output(req.user, req.params["oid"]))

  def output_verdict(self, req: Request) -> Response:
    b = req.body() or {}
    verdict = (
        b.get("human_verdict") if "human_verdict" in b else b.get("verdict")
    )
    note = b.get("human_note") if "human_note" in b else b.get("note")
    return json_response({
        "output": self.runs.verdict(
            req.user,
            req.params["oid"],
            verdict,
            note,
            selected_component=b.get("selected_component"),
        )
    })

  def output_rejudge(self, req: Request) -> Response:
    return json_response(
        {"output": self.runs.rejudge(req.user, req.params["oid"])}
    )

  def output_regenerate(self, req: Request) -> Response:
    return json_response(
        {"output": self.runs.regenerate(req.user, req.params["oid"])}
    )

  def output_render(self, req: Request) -> Response:
    b = req.body() or {}
    return json_response(
        self.runs.render_output(
            req.user,
            req.params["oid"],
            renderer=b.get("renderer"),
            renderers=b.get("renderers"),
        )
    )

  def run_render(self, req: Request) -> Response:
    b = req.body() or {}
    return json_response({
        "run": self.runs.render_run(
            req.user,
            req.params["rid"],
            renderer=b.get("renderer"),
            renderers=b.get("renderers"),
        )
    })

  def run_retheme(self, req: Request) -> Response:
    b = req.body() or {}
    return json_response(
        self.runs.retheme_run(
            req.user,
            req.params["rid"],
            token_revision_id=b.get("token_revision_id") or None,
            renderers=b.get("renderers"),
        )
    )

  # -------------------------------------------------------------- render
  def render_data(self, req: Request) -> Response:
    return json_response(
        self.runs.render_data(
            req.params["oid"],
            req.query.get("token", ""),
            req.query.get("viewport"),
            theme_mode=req.query.get("theme") or "run",
            token_revision_id=req.query.get("token_rev") or None,
        )
    )

  def render_example(self, req: Request) -> Response:
    name = os.path.basename(req.params["name"]).removesuffix(".json")
    path = os.path.join(EXAMPLES_DIR, name + ".json")
    if not os.path.isfile(path):
      raise ApiError(404, "No such example")
    with open(path, encoding="utf-8") as fh:
      ex = json.load(fh)
    from core.settings import DEFAULT_THEME  # pylint: disable=g-import-not-at-top

    vp = generator.VIEWPORTS.get(
        req.query.get("viewport") or "phone", generator.VIEWPORTS["phone"]
    )
    return json_response({
        "messages": ex.get("messages") or [],
        "theme": DEFAULT_THEME,
        "viewport": vp,
        "framing": "card",
        "name": ex.get("name"),
        "description": ex.get("description"),
    })

  def render_examples(self, req: Request) -> Response:
    del req
    names = sorted(
        f.removesuffix(".json")
        for f in os.listdir(EXAMPLES_DIR)
        if f.endswith(".json")
    )
    return json_response({"examples": names})

  # ---------------------------------------------------------- guidelines
  def guidelines_list(self, req: Request) -> Response:
    return json_response({
        "guidelines": self.svc.list_guidelines(
            req.user,
            req.params["pid"],
            req.query.get("kind", ""),
            req.query.get("status", ""),
        )
    })

  def guidelines_create(self, req: Request) -> Response:
    return json_response(
        {
            "guideline": self.svc.create_guideline(
                req.user, req.params["pid"], req.body()
            )
        },
        201,
    )

  def guideline_get(self, req: Request) -> Response:
    return json_response(
        {"guideline": self.svc.get_guideline(req.user, req.params["gid"])}
    )

  def guideline_patch(self, req: Request) -> Response:
    return json_response({
        "guideline": self.svc.update_guideline(
            req.user, req.params["gid"], req.body()
        )
    })

  def guideline_delete(self, req: Request) -> Response:
    self.svc.delete_guideline(req.user, req.params["gid"])
    return json_response({"ok": True})

  def guideline_screenshot(self, req: Request) -> Response:
    b = req.body()
    return json_response({
        "guideline": self.svc.add_guideline_screenshot(
            req.user,
            req.params["gid"],
            b.get("output_id"),
            b.get("data_url"),
            b.get("renderer"),
        )
    })

  def guideline_escalate(self, req: Request) -> Response:
    return json_response({
        "guideline": self.svc.escalate_guideline(
            req.user, req.params["gid"], (req.body() or {}).get("note", "")
        )
    })

  # ------------------------------------------------------------- exports
  def _proj(self, req: Request) -> dict[str, Any]:
    return self.svc._project_row(req.params["pid"], req.user)  # pylint: disable=protected-access

  def export_md(self, req: Request) -> Response:
    p = self._proj(req)
    rid = req.query.get("rev") or p["current_revision_id"]
    rev = self.svc.get_revision(req.user, p["id"], rid)
    name = f"{bundles_mod._slug(p['name'])}_rev{rev['number']}.md"  # pylint: disable=protected-access
    return download(
        bundles_mod.constitution_md(self.store, p, rev),
        name,
        "text/markdown; charset=utf-8",
    )

  def export_history(self, req: Request) -> Response:
    p = self._proj(req)
    data = bundles_mod.history_json(self.store, p)
    return download(
        json.dumps(data, indent=2, ensure_ascii=False),
        f"{bundles_mod._slug(p['name'])}_history.json",  # pylint: disable=protected-access
        "application/json; charset=utf-8",
    )

  def export_guidelines(self, req: Request) -> Response:
    p = self._proj(req)
    gls = self.svc.list_guidelines(
        req.user,
        p["id"],
        req.query.get("kind", ""),
        req.query.get("status", ""),
    )
    data = bundles_mod.guidelines_zip(self.store, p, gls)
    return download(
        data,
        f"{bundles_mod._slug(p['name'])}_dos_donts.zip",  # pylint: disable=protected-access
        "application/zip",
    )

  # --------------------------------------------------------------- admin
  def _admin(self, req: Request) -> None:
    if not req.user or not req.user["is_admin"]:
      raise ApiError(403, "Admins only")

  def admin_overview(self, req: Request) -> Response:
    self._admin(req)
    users = self.store.query("SELECT * FROM users ORDER BY last_seen_at DESC")
    projects = self.svc.list_projects(req.user, all_users=True)
    q = self.store.scalar
    totals = {
        "users": len(users),
        "projects": len(projects),
        "revisions": q("SELECT COUNT(*) FROM constitution_revisions"),
        "runs": q("SELECT COUNT(*) FROM runs"),
        "outputs": q("SELECT COUNT(*) FROM outputs"),
        "reviewed": q(
            "SELECT COUNT(*) FROM outputs WHERE human_verdict IS NOT NULL"
        ),
        "dos": q("SELECT COUNT(*) FROM guidelines WHERE kind='do'"),
        "donts": q("SELECT COUNT(*) FROM guidelines WHERE kind='dont'"),
        "escalated": q(
            "SELECT COUNT(*) FROM guidelines WHERE status='escalated'"
        ),
        "samples": q("SELECT COUNT(*) FROM samples"),
    }
    for u in users:
      u["is_admin"] = bool(u["is_admin"]) or u["ldap"] in self.svc.admins
      u["projects"] = q(
          "SELECT COUNT(*) FROM projects WHERE owner_ldap=?", [u["ldap"]]
      )
    return json_response(
        {"users": users, "projects": projects, "totals": totals}
    )

  def admin_inbox(self, req: Request) -> Response:
    self._admin(req)
    rows = self.store.query(
        "SELECT g.*, p.name AS project_name, p.owner_ldap AS owner_ldap FROM "
        "guidelines g JOIN projects p ON p.id = g.project_id WHERE "
        "g.status != 'packaged' AND (g.status='escalated' OR (g.kind='dont' "
        "AND g.fixable='no')) ORDER BY g.updated_at DESC"
    )
    return json_response(
        {"guidelines": [self.svc.fmt_guideline(g) for g in rows]}
    )

  def _fmt_bundle(self, b: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": b["id"],
        "kind": b["kind"],
        "created_by": b["created_by"],
        "created_at": b["created_at"],
        "size_bytes": b["size_bytes"],
        "counts": b.get("counts") or {},
        "guideline_ids": b.get("guideline_ids") or [],
        "project_ids": b.get("project_ids") or [],
        "download_url": f"/api/admin/bundles/{b['id']}.zip",
    }

  def admin_bundles(self, req: Request) -> Response:
    self._admin(req)
    return json_response({
        "bundles": [
            self._fmt_bundle(b)
            for b in self.store.query(
                "SELECT * FROM bundles ORDER BY created_at DESC"
            )
        ]
    })

  def admin_bundle_create(self, req: Request) -> Response:
    self._admin(req)
    b = req.body()
    kind = b.get("kind") or "benchmark"
    gids = [g for g in b.get("guideline_ids") or [] if isinstance(g, str)]
    pids = [p for p in b.get("project_ids") or [] if isinstance(p, str)]
    gls = [
        self.svc.fmt_guideline(g)
        for g in (self.store.get("guidelines", gid) for gid in gids)
        if g
    ]
    if not gls and not pids:
      raise ApiError(400, "Select guidelines or projects to package")
    bid = util.new_id("b")
    if kind == "guidelines":
      import io  # pylint: disable=g-import-not-at-top
      import zipfile  # pylint: disable=g-import-not-at-top

      buf = io.BytesIO()
      with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        by_proj: dict[str, list] = {}
        for g in gls:
          by_proj.setdefault(g["project_id"], []).append(g)
        for pid in pids:
          by_proj.setdefault(pid, self.svc.list_guidelines(req.user, pid))
        for pid, items in by_proj.items():
          p = self.store.get("projects", pid)
          if not p:
            continue
          inner = bundles_mod.guidelines_zip(self.store, p, items)
          z.writestr(f"{bundles_mod._slug(p['name'])}_{pid[-6:]}.zip", inner)  # pylint: disable=protected-access
      data, counts = buf.getvalue(), {
          "guidelines": len(gls),
          "projects": len(pids),
      }
    else:
      data, counts = bundles_mod.benchmark_zip(self.store, gls, pids)
    path = self.store.file_path("bundles", f"{bid}.zip")
    with open(path, "wb") as fh:
      fh.write(data)
    for g in gls:
      self.store.update(
          "guidelines",
          g["id"],
          {"status": "packaged", "updated_at": util.now_iso()},
      )
    row = self.store.insert(
        "bundles",
        {
            "id": bid,
            "kind": kind,
            "project_ids": pids,
            "guideline_ids": gids,
            "path": path,
            "size_bytes": len(data),
            "counts": counts,
            "created_by": req.user["ldap"],
            "created_at": util.now_iso(),
        },
    )
    return json_response({"bundle": self._fmt_bundle(row)}, 201)

  def admin_bundle_download(self, req: Request) -> Response:
    self._admin(req)
    bid = req.params["bid"].removesuffix(".zip")
    b = self.store.get("bundles", bid)
    if not b:
      raise ApiError(404, "Bundle not found")
    return file_response(
        b["path"], download_name=f"{b['kind']}_bundle_{bid}.zip"
    )

  # ---------------------------------------------------------------- wizard
  def wizard_domains(self, req: Request) -> Response:
    del req
    return json_response({"domains": wizard_mod.WIZARD_DOMAINS})


def demo_server_path() -> str:
  return "/mcp/demo"
