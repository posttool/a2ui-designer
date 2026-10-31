"""Component builder: design sources → custom component drafts (Gemini).

Sources: Figma component sets/components (REST), screenshot crops, Stitch
screens, written descriptions, and component proposals from design-reference
analysis. Each import creates a draft row in `custom_components` and runs a
background job: extract facts → draft a definition (classification, props,
web template + CSS, sample, usage) → render a preview → compare it with the
reference image → one repair round if the match is weak.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import threading
import traceback
from typing import Any

from core import settings as settings_mod
from core import util
from core.a2ui import catalog as catalog_mod
from core.a2ui import custom_web
from core.a2ui import lowering
from core.service import ApiError
from llm.gemini import Image, LlmError
from refs import figma
from refs import figma_compiler
from refs import stitch
from refs.references import decode_data_url
from sim import common
from sim import images
from sim.common import arr, obj, str_schema
from sim.reviser import THEME_CHANGE_ITEM_SCHEMA, _theme_digest  # pylint: disable=protected-access

MAX_IMPORT = 30
REPAIR_BELOW = 3  # compare score ≤ this triggers one repair round

BUILD_SCHEMA = obj({
    "classification": {
        "type": "string",
        "enum": ["custom", "basic_equivalent", "skip"],
    },
    "reason": str_schema("Why this classification (one sentence)."),
    "basic_component": str_schema(
        "For basic_equivalent: the Basic catalog component name, else ''."
    ),
    "basic_fit": {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "description": (
            "0–1: how faithfully this component can be rebuilt from Basic"
            " catalog components only (Card, Row, Column, Text, Image, Icon,"
            " Button, Divider). 1 = exact; 0 = needs custom drawing or layout."
        ),
    },
    "basic_fit_reason": str_schema(
        "One sentence: what a Basic-only rebuild would lose, or ''."
    ),
    "name": str_schema("PascalCase component name."),
    "description": str_schema("One sentence: what it is."),
    "usage": str_schema("2-5 sentences for the generating LLM."),
    "props_schema_json": str_schema(
        "JSON Schema object of the props, serialized to a string."
    ),
    "template": str_schema("HTML template (binding syntax), or ''."),
    "css": str_schema("Scoped CSS using var(--a2ui-*) tokens, or ''."),
    "sample_props_json": str_schema("JSON object string of sample props."),
    "sample_data_json": str_schema("JSON object string of a data model, '{}'."),
    "sample_components_json": str_schema(
        "JSON array string of Basic components used by slot props, '[]'."
    ),
    "token_suggestions": arr(THEME_CHANGE_ITEM_SCHEMA),
    "notes": arr(str_schema()),
})

COMPARE_SCHEMA = obj({
    "score": {"type": "integer", "minimum": 1, "maximum": 5},
    "summary": str_schema(),
    "mismatches": arr(
        obj({"area": str_schema(), "issue": str_schema(), "fix": str_schema()})
    ),
})

SAMPLE_SCHEMA = obj({
    "sample_props_json": str_schema(),
    "sample_data_json": str_schema(),
    "sample_components_json": str_schema(),
})


def _loads(text: Any, default: Any) -> Any:
  if isinstance(text, (dict, list)):
    return text
  try:
    v = json.loads(text or "")
  except (TypeError, ValueError):
    return default
  return v if isinstance(v, type(default)) else default


def _pascal(text: str) -> str:
  words = re.findall(r"[A-Za-z0-9]+", text or "")
  name = "".join(w[:1].upper() + w[1:] for w in words)
  if name and name[0].isdigit():
    name = "C" + name
  return name[:60]


def _clamp01(v: Any) -> float | None:
  try:
    f = float(v)
  except (TypeError, ValueError):
    return None
  if f != f:  # NaN
    return None
  return round(min(1.0, max(0.0, f)), 3)


def basic_fit_info(
    src: dict[str, Any] | None,
    data: dict[str, Any] | None,
    basic_skel: dict[str, Any] | None,
) -> dict[str, Any] | None:
  """Best available *Basic fit* (0–1) for a component draft, or None.

  Precedence: the deterministic skeleton fit computed from Figma node / Stitch
  DOM structure (`figma_compiler.compile_node_to_basic_skeleton`), then the
  Gemini draft's own `basic_fit` estimate, then the estimate recorded on the
  component's `source` by an earlier draft.
  """
  src = src or {}
  data = data or {}
  kind = str(src.get("kind") or "")
  if kind == "figma":
    structure = "figma"
  elif kind == "stitch" or src.get("html_file") or src.get("delegate_to"):
    structure = "stitch"
  else:
    structure = "structure"
  model_est = _clamp01(data.get("basic_fit"))
  model_reason = str(data.get("basic_fit_reason") or "").strip()[:300]
  skel_fit = basic_skel.get("fit") if isinstance(basic_skel, dict) else None
  if isinstance(skel_fit, dict) and _clamp01(skel_fit.get("score")) is not None:
    return {
        "score": _clamp01(skel_fit.get("score")),
        "source": structure,
        "nodes": int(skel_fit.get("nodes") or 0),
        "faithful": int(skel_fit.get("faithful") or 0),
        "partial": int(skel_fit.get("partial") or 0),
        "lossy": int(skel_fit.get("lossy") or 0),
        "summary": list(skel_fit.get("summary") or [])[:6],
        "reasons": list(skel_fit.get("reasons") or [])[:12],
        "model_estimate": model_est,
        "model_reason": model_reason,
    }
  if model_est is not None:
    return {
        "score": model_est,
        "source": "gemini",
        "summary": [model_reason] if model_reason else [],
        "reasons": [],
        "model_estimate": model_est,
        "model_reason": model_reason,
    }
  prev = src.get("basic_fit")
  if isinstance(prev, dict) and _clamp01(prev.get("score")) is not None:
    return prev
  return None


class ComponentBuilder:
  """Imports + Gemini jobs for custom components."""

  def __init__(
      self,
      svc,
      comps,
      refs,
      figma_scan=None,
      figma_detail=None,
      figma_thumbs=None,
  ):
    self.svc = svc
    self.store = svc.store
    self.comps = comps
    self.refs = refs
    self.executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=3, thread_name_prefix="cbuild"
    )
    self._theme_lock = threading.Lock()
    # Injectable for tests.
    self.figma_scan = figma_scan or figma.scan_components
    self.figma_detail = figma_detail or figma.component_detail
    self.figma_thumbs = figma_thumbs or figma.thumbnails

  # ------------------------------------------------------------ helpers
  def _figma_token(self, user) -> str:
    token, _ = self.refs.figma_token(user)
    if not token:
      raise ApiError(
          400, "Add a Figma personal access token first (read-only scope)"
      )
    return token

  def _palette(self, pid: str) -> dict[str, str]:
    p = self.store.get("projects", pid) or {}
    s = settings_mod.merged(p.get("settings"))
    eff = settings_mod.resolve_effective_theme(s["theme"])
    out = {}
    for k, v in eff.items():
      if isinstance(v, str) and v.startswith("#"):
        out[k] = v
    for slug, ent in (eff.get("custom_tokens") or {}).items():
      val = ent.get("value") if isinstance(ent, dict) else ent
      if isinstance(val, str) and val.startswith("#"):
        out[f"--a2ui-{slug}"] = val
    return out

  def _unique_name(self, pid: str, name: str, cid: str | None) -> str:
    base = _pascal(name) or "Component"
    taken = self.comps.names_in_project(pid, exclude=cid) | set(
        catalog_mod.basic_names()
    )
    if base not in taken:
      return base
    i = 2
    while f"{base}{i}" in taken:
      i += 1
    return f"{base}{i}"

  def _model(self, pid: str) -> str | None:
    p = self.store.get("projects", pid) or {}
    return settings_mod.merged(p.get("settings"))["generator"].get("model")

  # ------------------------------------------------------------ figma scan
  def scan_figma(self, user, pid: str, url: str) -> dict[str, Any]:
    self.svc._project_row(pid, user)  # pylint: disable=protected-access
    token = self._figma_token(user)
    try:
      res = self.figma_scan(token, url)
    except figma.FigmaError as e:
      raise ApiError(
          e.status if e.status in (400, 403, 404) else 502, str(e)
      ) from e
    comps = res.get("components") or []
    key = res.get("file_key") or ""
    thumbs: dict[str, bytes] = {}
    if comps:
      try:
        thumbs = self.figma_thumbs(token, key, [c["node_id"] for c in comps])
      except Exception:  # pylint: disable=broad-except
        traceback.print_exc()
    out = []
    for c in comps:
      thumb_url = None
      data = thumbs.get(c["node_id"])
      if data:
        fn = (
            f"figma_{re.sub(r'[^A-Za-z0-9]', '_', key)}_"
            + re.sub(r"[^0-9A-Za-z]", "_", c["node_id"])
            + ".png"
        )
        path = self.store.file_path("components", fn)
        import os  # pylint: disable=g-import-not-at-top

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
          fh.write(data)
        thumb_url = f"/files/components/{fn}"
      out.append(dict(c, thumb_url=thumb_url))
    return {
        "file_key": key,
        "file_name": res.get("file_name") or "",
        "components": out,
        "truncated": bool(res.get("truncated")),
    }

  # ------------------------------------------------------------ import
  def import_(self, user, pid: str, body: dict[str, Any]) -> dict[str, Any]:
    self.svc._project_row(pid, user, write=True)  # pylint: disable=protected-access
    src = body.get("source")
    created: list[dict[str, Any]] = []
    queued = {"stage": "queued", "updated_at": util.now_iso()}

    if src == "figma":
      req_strategy = str(body.get("strategy") or "").strip().lower()
    else:
      req_strategy = str(body.get("strategy") or "").strip().lower()

    def new(name, source, notes=""):
      name = self._unique_name(pid, name, None)
      init_defn: dict[str, Any] = {"name": name, "description": ""}
      if (
          req_strategy in settings_mod.COMPONENT_STRATEGIES
          and req_strategy != "auto"
      ):
        source = dict(source or {}, strategy=req_strategy)
        init_defn["strategy"] = req_strategy
      r = self.comps.new_row(
          pid,
          user["ldap"],
          init_defn,
          status="draft",
          source=source,
          job=dict(queued),
          notes=notes,
      )
      created.append(r)
      return r

    if src == "figma":
      url = str(body.get("url") or "").strip()
      try:
        key, _ = figma.parse_url(url)
      except figma.FigmaError as e:
        raise ApiError(400, str(e)) from e
      token = self._figma_token(user)
      ids = [str(x) for x in body.get("node_ids") or [] if x]
      ids = list(dict.fromkeys(ids))
      if not ids:
        raise ApiError(400, "Pick at least one component")
      if len(ids) > MAX_IMPORT:
        raise ApiError(400, f"Import at most {MAX_IMPORT} components at once")
      names = body.get("names") or {}
      for nid in ids:
        r = new(
            str(names.get(nid) or f"Figma {nid}"),
            {
                "kind": "figma",
                "url": url,
                "file_key": key,
                "node_id": nid,
                "file_name": str(body.get("file_name") or "")[:200],
            },
        )
        self.executor.submit(self._job, r["id"], {"figma_token": token})
    elif src == "screenshot":
      img = body.get("image") or {}
      data, ext = decode_data_url(img.get("data_url") or "")
      name = str(body.get("name") or img.get("name") or "Component")
      name = re.sub(r"\.(png|jpe?g|webp|gif)$", "", name, flags=re.I)
      r = new(
          name,
          {
              "kind": "screenshot",
              "hint": str(body.get("hint") or "")[:1000],
              "file_name": str(img.get("name") or "")[:120],
          },
      )
      self.comps.save_reference(r["id"], data, ext)
      w, h = _png_size(data)
      if w:
        self._merge_source(
            r["id"], {"width": w // 2 if w > 900 else w, "height": h}
        )
      self.executor.submit(self._job, r["id"], {})
    elif src == "stitch":
      key, _ = self.refs.stitch_key(user)
      if not key:
        raise ApiError(
            400, "Add a Stitch API key first (Stitch → Settings → API keys)"
        )
      try:
        spid, pre = stitch.parse_url(str(body.get("url") or ""))
      except stitch.StitchError as e:
        raise ApiError(400, str(e)) from e
      screen_id = str(body.get("screen_id") or pre or "")
      prompt_text = str(body.get("prompt") or "").strip()
      if not screen_id and prompt_text:
        try:
          gen = stitch.StitchClient(key).generate_component_screen(
              spid,
              prompt_text,
              name=str(body.get("name") or prompt_text[:40]),
          )
          screen_id = str(gen.get("screen_id") or "")
        except stitch.StitchError as e:
          raise ApiError(502, str(e)) from e
      if not screen_id:
        raise ApiError(400, "Pick a Stitch screen")
      r = new(
          str(
              body.get("name")
              or body.get("hint")
              or prompt_text[:40]
              or "Stitch component"
          )[:60],
          {
              "kind": "stitch",
              "url": f"https://stitch.withgoogle.com/projects/{spid}",
              "project_id": spid,
              "screen_id": screen_id,
              "hint": str(body.get("hint") or prompt_text or "")[:1000],
          },
      )
      self.executor.submit(self._job, r["id"], {"stitch_key": key})
    elif src == "describe":
      text = str(body.get("text") or "").strip()
      if not text:
        raise ApiError(400, "Describe the component")
      delegate_to = str(body.get("delegate_to") or "").strip().lower()
      stitch_key, _ = self.refs.stitch_key(user)
      r = new(
          str(body.get("name") or text[:40]),
          {
              "kind": "describe",
              "text": text[:4000],
              **(
                  {
                      "delegate_to": "stitch",
                      "stitch_project_id": str(
                          body.get("stitch_project_id") or ""
                      ),
                  }
                  if delegate_to == "stitch"
                  else {}
              ),
          },
      )
      self.executor.submit(
          self._job,
          r["id"],
          {"stitch_key": stitch_key} if stitch_key else {},
      )
    elif src == "proposal":
      name = re.sub(r"[^A-Za-z0-9]", "", str(body.get("name") or ""))
      row = None
      if body.get("component_id"):
        row = self.store.get("custom_components", body["component_id"])
        if row and row["project_id"] != pid:
          row = None
      if row is None and name:
        rows = self.store.query(
            "SELECT * FROM custom_components WHERE project_id=? AND name=? AND"
            " status='draft'",
            [pid, name],
        )
        row = rows[0] if rows else None
      if row is None:
        mid = body.get("chat_message_id")
        m = self.store.get("chat_messages", mid) if mid else None
        if not m or m["project_id"] != pid:
          raise ApiError(404, "Proposal not found")
        pcs = ((m.get("proposal") or {}).get("catalog_changes") or {}).get(
            "proposed_components"
        ) or []
        pc = next((x for x in pcs if x.get("name") == name), None)
        if not pc:
          raise ApiError(404, f"No proposed component named {name}")
        row = new(
            name,
            {
                "kind": "proposal",
                "chat_message_id": mid,
                "reference_ids": (
                    (m.get("context") or {}).get("reference_ids") or []
                ),
                "purpose": pc.get("purpose") or "",
                "maps_to_basic": pc.get("maps_to_basic") or [],
            },
        )
        defn = dict(
            row["definition"],
            description=pc.get("purpose") or "",
            props=pc.get("props_schema") or {},
        )
        self.store.update(
            "custom_components",
            row["id"],
            {"definition": custom_web.normalize_definition(defn)},
        )
      else:
        self.store.update("custom_components", row["id"], {"job": dict(queued)})
        created.append(self.store.get("custom_components", row["id"]))
      self._attach_reference_image(row)
      self.executor.submit(self._job, row["id"], {})
    else:
      raise ApiError(
          400, "source must be figma, screenshot, stitch, describe or proposal"
      )
    self.svc.touch(pid)
    return {
        "components": [
            self.comps.fmt(self.store.get("custom_components", r["id"]))
            for r in created
        ]
    }

  def queue_import_component(
      self,
      user,
      pid: str,
      name: str,
      source: dict[str, Any],
      *,
      description: str = "",
      props: dict[str, Any] | None = None,
      reference: tuple[bytes, str] | None = None,
      secrets: dict[str, Any] | None = None,
  ) -> dict[str, Any]:
    """Draft + build a component for a confirmed import extraction."""
    name = self._unique_name(pid, name, None)
    defn: dict[str, Any] = {"name": name, "description": description or ""}
    if props:
      defn["props"] = props
    source = dict(source or {}, auto_activate=True)
    r = self.comps.new_row(
        pid,
        user["ldap"],
        defn,
        status="draft",
        source=source,
        job={"stage": "queued", "updated_at": util.now_iso()},
        notes="Created from an import merge.",
    )
    if reference:
      ext = {"image/jpeg": "jpg", "image/webp": "webp"}.get(reference[1], "png")
      self.comps.save_reference(r["id"], reference[0], ext)
      w, h = _png_size(reference[0])
      if w:
        self._merge_source(
            r["id"], {"width": w // 2 if w > 900 else w, "height": h}
        )
    self.executor.submit(self._job, r["id"], dict(secrets or {}))
    return self.store.get("custom_components", r["id"])

  def _merge_source(self, cid: str, extra: dict[str, Any]) -> None:
    r = self.store.get("custom_components", cid)
    self.store.update(
        "custom_components",
        cid,
        {"source": dict(r.get("source") or {}, **extra)},
    )

  def _attach_reference_image(self, row: dict[str, Any]) -> None:
    """Proposal drafts: use the first image of their design reference."""
    if row.get("reference_file"):
      return
    for ref_id in (row.get("source") or {}).get("reference_ids") or []:
      ref = self.store.get("design_references", ref_id)
      for im in (ref or {}).get("images") or []:
        if im.get("file"):
          try:
            with open(
                self.store.file_path("references", im["file"]), "rb"
            ) as fh:
              data = fh.read()
          except OSError:
            continue
          self.comps.save_reference(
              row["id"], data, im["file"].rsplit(".", 1)[-1]
          )
          return

  # ------------------------------------------------------------ job
  def _job(self, cid: str, secrets: dict[str, Any]) -> None:
    try:
      self._run_job(cid, secrets)
    except Exception as e:  # pylint: disable=broad-except
      traceback.print_exc()
      self.comps.set_job(cid, "error", str(e) or e.__class__.__name__)

  def _run_job(self, cid: str, secrets: dict[str, Any]) -> None:
    r = self.store.get("custom_components", cid)
    if not r:
      return
    src = r.get("source") or {}
    facts: dict[str, Any] = {}
    images: list[tuple[str, bytes, str]] = []
    self.comps.set_job(cid, "extracting")
    if src.get("kind") == "figma":
      palette = self._palette(r["project_id"])
      det = self.figma_detail(
          secrets["figma_token"],
          src["file_key"],
          src["node_id"],
          palette,
      )
      info = det.get("info") or {}
      raw_node = det.get("raw_node") or det.get("outline") or {}
      web_skel = figma_compiler.compile_node_to_html_css(
          raw_node,
          palette=palette,
          property_defs=info.get("property_defs"),
          variant_props=info.get("variant_props"),
      )
      basic_skel = figma_compiler.compile_node_to_basic_skeleton(
          raw_node,
          property_defs=info.get("property_defs"),
      )
      facts = {
          "figma_component": {
              "name": info.get("name"),
              "kind": info.get("kind"),
              "description": info.get("description"),
              "size": [info.get("width"), info.get("height")],
              "component_properties": info.get("property_defs"),
              "variant_properties": info.get("variant_props"),
              "variants": det.get("variants"),
          },
          "outline_of_default_variant": det.get("outline"),
          "deterministic_web_skeleton": web_skel,
          "deterministic_basic_skeleton": basic_skel,
      }
      self._merge_source(
          cid,
          {
              "figma_name": info.get("name"),
              "variant_props": info.get("variant_props") or {},
              "width": info.get("width"),
              "height": info.get("height"),
          },
      )
      if det.get("reference_png"):
        self.comps.save_reference(cid, det["reference_png"], "png")
      if det.get("set_png"):
        images.append(
            ("all variants (component set)", det["set_png"], "image/png")
        )
    elif src.get("kind") == "stitch":
      res = self.refs.stitch_fetch(
          secrets["stitch_key"],
          src["project_id"],
          [src["screen_id"]],
          f"cc_{cid}",
      )
      imgs = res.get("images") or []
      if imgs and imgs[0].get("file"):
        with open(
            self.store.file_path("references", imgs[0]["file"]), "rb"
        ) as fh:
          data = fh.read()
        self.comps.save_reference(cid, data, imgs[0]["file"].rsplit(".", 1)[-1])
        self._merge_source(cid, {"screen_title": imgs[0].get("name")})
      if res.get("file_name"):
        self._merge_source(cid, {"file_name": res["file_name"]})
      sf = res.get("facts") or {}
      htmls = res.get("htmls") or []
      html_text = htmls[0] if htmls else ""
      if not html_text and imgs and imgs[0].get("html_file"):
        try:
          with open(
              self.store.file_path("references", imgs[0]["html_file"]),
              "r",
              encoding="utf-8",
          ) as fh:
            html_text = fh.read()
        except OSError:
          html_text = ""
      dom_region = (
          stitch.extract_stitch_dom_region(
              html_text,
              hint=src.get("hint") or r.get("name") or "",
              palette=self._palette(r["project_id"]),
          )
          if html_text
          else {}
      )
      facts = {
          "stitch_design_system": sf.get("design_system"),
          "stitch_html_outline": sf.get("html_outline"),
          "tailwind": sf.get("tailwind"),
      }
      if dom_region.get("web"):
        facts["deterministic_web_skeleton"] = dom_region["web"]
      if dom_region.get("basic"):
        facts["deterministic_basic_skeleton"] = dom_region["basic"]
    elif (
        src.get("kind") == "describe"
        and src.get("delegate_to") == "stitch"
        and secrets.get("stitch_key")
    ):
      try:
        cli = stitch.StitchClient(secrets["stitch_key"])
        spid = str(src.get("stitch_project_id") or "").strip()
        if not spid:
          proj = cli.create_project(f"DCB Components ({r['project_id']})")
          spid = proj.get("project_id") or ""
        if spid:
          gen = cli.generate_component_screen(
              spid,
              src.get("text") or r["name"],
              name=r["name"],
          )
          html_text = gen.get("html") or ""
          if gen.get("png"):
            self.comps.save_reference(cid, gen["png"], "png")
          if html_text:
            dom_region = stitch.extract_stitch_dom_region(
                html_text,
                hint=src.get("text") or r["name"],
                palette=self._palette(r["project_id"]),
            )
            facts = {
                "stitch_generated_screen_id": gen.get("screen_id"),
                "deterministic_web_skeleton": dom_region.get("web"),
                "deterministic_basic_skeleton": dom_region.get("basic"),
            }
      except Exception:  # pylint: disable=broad-except
        traceback.print_exc()
    elif src.get("html_file"):
      try:
        with open(
            self.store.file_path("references", src["html_file"]),
            "r",
            encoding="utf-8",
        ) as fh:
          html_text = fh.read()
      except OSError:
        html_text = ""
      if html_text:
        dom_region = stitch.extract_stitch_dom_region(
            html_text,
            hint=src.get("hint") or r.get("name") or "",
            palette=self._palette(r["project_id"]),
        )
        if dom_region.get("web"):
          facts["deterministic_web_skeleton"] = dom_region["web"]
        if dom_region.get("basic"):
          facts["deterministic_basic_skeleton"] = dom_region["basic"]
    r = self.store.get("custom_components", cid)
    self.comps.set_job(cid, "drafting")
    data = self._draft(r, facts, images)
    keep_name = bool(src.get("auto_activate") or src.get("kind") == "import")
    r = self._apply_draft(cid, data, keep_name=keep_name, facts=facts)
    if (r.get("classification") or "custom") != "custom":
      self.comps.set_job(cid, "done")
      return
    if r.get("issues"):
      self.comps.set_job(cid, "repairing")
      data2 = self._draft(
          r,
          facts,
          images,
          issues=r.get("issues") or [],
          warnings=r.get("warnings") or [],
      )
      if (data2.get("classification") or "custom") == "custom":
        r = self._apply_draft(cid, data2, keep_name=True, facts=facts)
    if (
        src.get("auto_activate")
        or src.get("kind") in ("import", "proposal")
        or src.get("import_id")
    ):
      self._sync_import_component_tokens(cid, facts=facts)
    if src.get("auto_activate") and not (r.get("issues") or []):
      self.store.update(
          "custom_components",
          cid,
          {"status": "active", "updated_at": util.now_iso()},
      )
    self._render_compare_repair(cid, facts, images, allow_repair=True)
    if (
        src.get("auto_activate")
        or src.get("kind") in ("import", "proposal")
        or src.get("import_id")
    ):
      self._sync_import_component_tokens(cid, facts=facts)
    if src.get("auto_activate"):
      cur = self.store.get("custom_components", cid)
      if (
          cur
          and (cur.get("classification") or "custom") == "custom"
          and not (cur.get("issues") or [])
      ):
        self.store.update(
            "custom_components",
            cid,
            {"status": "active", "updated_at": util.now_iso()},
        )
    self.comps.set_job(cid, "done")

  def _sync_import_component_tokens(
      self, cid: str, facts: dict[str, Any] | None = None
  ) -> list[dict[str, Any]]:
    """Brings in any tokens read or suggested by an imported component that are not yet defined in the project's theme."""
    with self._theme_lock:
      r = self.store.get("custom_components", cid)
      if not r or (r.get("classification") or "custom") != "custom":
        return []
      pid = r["project_id"]
      p = self.store.get("projects", pid)
      if not p:
        return []
      s = settings_mod.merged(p.get("settings"))
      src = r.get("source") or {}
      iid = src.get("import_id")
      xid = src.get("extraction_id")
      imp = self.store.get("design_references", iid) if iid else None
      merged_facts: dict[str, Any] = {}
      if isinstance(imp, dict) and isinstance(imp.get("facts"), dict):
        merged_facts.update(imp["facts"])
      if isinstance(facts, dict):
        merged_facts.update(facts)
      web_defn = ((r.get("definition") or {}).get("web")) or {}
      missing_toks = settings_mod.resolve_missing_tokens_from_material(
          s["theme"],
          token_refs=r.get("token_refs") or [],
          texts=[
              str(web_defn.get("css") or ""),
              str(web_defn.get("template") or ""),
          ],
          token_suggestions=r.get("token_suggestions") or [],
          source_facts=merged_facts,
          sources=[xid] if xid else [],
          reason_prefix=(
              f"Brought in missing token read by {r.get('name') or 'component'}"
          ),
      )
      if not missing_toks:
        return []
      new_theme, applied_log = settings_mod.apply_theme_changes(
          s["theme"], missing_toks, None
      )
      if not applied_log:
        return []
      s["theme"] = new_theme
      user_dict = {
          "ldap": r.get("created_by") or p.get("owner_ldap") or "system",
          "is_admin": True,
      }
      self.svc.put_settings(
          user_dict,
          pid,
          s,
          source="import_merge",
          summary=(
              f"Brought in {len(applied_log)} missing token(s) read by"
              f" {r.get('name') or 'imported component'}"
          ),
      )
      remaining_sugs = [
          ts
          for ts in r.get("token_suggestions") or []
          if isinstance(ts, dict)
          and not settings_mod.is_token_available(
              new_theme, str(ts.get("token") or "")
          )
      ]
      self.store.update(
          "custom_components",
          cid,
          {"token_suggestions": remaining_sugs, "updated_at": util.now_iso()},
      )
      imp = self.store.get("design_references", iid) if iid else None
      if imp:
        now = util.now_iso()
        plan = dict(imp.get("merge_plan") or {})
        plan_lines = list(plan.get("lines") or [])
        log = list(imp.get("merge_log") or [])
        new_targets: list[dict[str, Any]] = []
        for mt, a in zip(missing_toks, applied_log):
          target = {
              "type": "tokens",
              "id": a["token"],
              "label": f"{a['token']} = {a['after']}",
              "url": f"/p/{pid}/design?tab=tokens",
          }
          new_targets.append(target)
          existing_ln = next(
              (
                  pln
                  for pln in plan_lines
                  if pln.get("type") == "tokens"
                  and (pln.get("detail") or {}).get("token") == a["token"]
              ),
              None,
          )
          if existing_ln is None:
            existing_ln = {
                "id": f"l{len(plan_lines) + 1}",
                "type": "tokens",
                "selected": True,
                "applied": True,
                "label": f"{a['token']}: {a.get('before')} → {a.get('after')}",
                "from": [xid] if xid else [],
                "detail": {
                    "token": a["token"],
                    "before": a.get("before"),
                    "after": a.get("after"),
                    "confidence": mt.get("confidence") or "exact",
                    "reason": mt.get("reason") or "",
                    "is_custom": a.get("is_custom", True),
                    "maps_to": mt.get("maps_to") or "",
                    "raw": {
                        k: mt.get(k)
                        for k in (
                            "token",
                            "value",
                            "is_custom",
                            "maps_to",
                            "reason",
                        )
                    },
                },
            }
            plan_lines.append(existing_ln)
          else:
            existing_ln["applied"] = True
          if log:
            last_entry = dict(log[-1])
            last_applied = list(last_entry.get("applied") or [])
            if not any(
                (ap.get("target") or {}).get("id") == a["token"]
                for ap in last_applied
            ):
              last_applied.append({
                  "line_id": existing_ln["id"],
                  "type": "tokens",
                  "label": existing_ln["label"],
                  "target": target,
              })
              last_entry["applied"] = last_applied
              log[-1] = last_entry
        plan["lines"] = plan_lines
        upd_ref: dict[str, Any] = {"merge_plan": plan, "updated_at": now}
        if log:
          upd_ref["merge_log"] = log
        self.store.update("design_references", iid, upd_ref)
        if xid:
          x_row = self.store.get("import_extractions", xid)
          if x_row:
            cur_merged = list(x_row.get("merged_into") or [])
            for tgt in new_targets:
              if not any(
                  m.get("type") == "tokens" and m.get("id") == tgt["id"]
                  for m in cur_merged
              ):
                cur_merged.append(tgt)
            self.store.update(
                "import_extractions",
                xid,
                {"merged_into": cur_merged, "updated_at": now},
            )
      return applied_log

  def _render_compare_repair(
      self,
      cid: str,
      facts: dict[str, Any],
      images: list[tuple[str, bytes, str]],
      allow_repair: bool,
  ) -> None:
    if self.comps.pool is None:
      return
    self.comps.set_job(cid, "rendering")
    self.comps.render_preview(cid)
    r = self.store.get("custom_components", cid)
    if not self.comps.reference_bytes(r):
      return
    self.comps.set_job(cid, "comparing")
    fid = self._compare(r)
    if fid is None:
      return
    self.store.update("custom_components", cid, {"fidelity": fid})
    if not allow_repair or (fid.get("score") or 5) > REPAIR_BELOW:
      return
    self.comps.set_job(cid, "repairing")
    before = self.store.get("custom_components", cid)
    data = self._draft(before, facts, images, mismatches=fid)
    if (data.get("classification") or "custom") != "custom":
      return
    self._apply_draft(cid, data, keep_name=True, facts=facts)
    self.comps.set_job(cid, "rendering")
    self.comps.render_preview(cid)
    after = self.store.get("custom_components", cid)
    fid2 = self._compare(after)
    if fid2 is None:
      return
    if (fid2.get("score") or 0) >= (fid.get("score") or 0):
      fid2["repaired"] = True
      self.store.update("custom_components", cid, {"fidelity": fid2})
    else:
      # Repair made it worse: restore the first draft.
      self.comps.apply_definition(
          cid,
          before["definition"],
          {
              "token_suggestions": before.get("token_suggestions") or [],
              "fidelity": dict(fid, repair_rejected=True),
          },
      )
      self.comps.render_preview(cid)

  # ------------------------------------------------------------ LLM
  def _context(self, r: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    pid = r["project_id"]
    p = self.store.get("projects", pid) or {}
    s = settings_mod.merged(p.get("settings"))
    src = r.get("source") or {}
    others = sorted(self.comps.names_in_project(pid, exclude=r["id"]))
    ctx = [
        f"# Source\n\nKind: {src.get('kind')}",
        _theme_digest(s["theme"]),
        "# Basic catalog components\n\n"
        + ", ".join(sorted(catalog_mod.basic_names())),
    ]
    if others:
      ctx.append(
          "# Existing custom components (do not duplicate)\n\n"
          + ", ".join(others)
      )
    if src.get("hint"):
      ctx.append("# Which part of the image is the component\n\n" + src["hint"])
    if src.get("text"):
      ctx.append("# Designer's description\n\n" + src["text"])
    if src.get("kind") in ("proposal", "import") or src.get("auto_activate"):
      defn = r.get("definition") or {}
      ctx.append(
          "# Proposal from design-reference analysis\n\n"
          + json.dumps(
              {
                  "name": r["name"],
                  "purpose": src.get("purpose") or defn.get("description"),
                  "maps_to_basic": src.get("maps_to_basic"),
                  "props_schema": defn.get("props"),
              },
              indent=1,
          )
      )
    if facts:
      ctx.append(
          "# Extracted facts (exact values from the source)\n\n```json\n"
          + common.clip_json(facts, 16000)
          + "\n```"
      )
    cons = (self.svc.current_revision(pid) or {}).get("text_md") or ""
    if cons.strip():
      ctx.append(
          "# Project design constitution (context for usage notes)\n\n"
          + cons[:4000]
      )
    return ctx

  def _images(self, r, images) -> list[Any]:
    parts: list[Any] = []
    ref = self.comps.reference_bytes(r)
    if ref:
      parts.extend(["Reference image of the component:", Image(ref[0], ref[1])])
    for label, data, mime in images:
      parts.extend([f"Additional image — {label}:", Image(data, mime)])
    return parts

  def _draft(
      self,
      r: dict[str, Any],
      facts: dict[str, Any],
      images: list[tuple[str, bytes, str]],
      mismatches: dict[str, Any] | None = None,
      instruction: str | None = None,
      issues: list[str] | None = None,
      warnings: list[str] | None = None,
      defn_override: dict[str, Any] | None = None,
  ) -> dict[str, Any]:
    ctx = self._context(r, facts)
    defn = (
        defn_override
        if isinstance(defn_override, dict)
        else (r.get("definition") or {})
    )
    has_impl = bool((defn.get("web") or {}).get("template"))
    has_schema = bool((defn.get("props") or {}).get("properties"))
    if (mismatches or instruction or issues or warnings) and (
        has_impl or has_schema
    ):
      ctx.append(
          "# Current definition\n\n```json\n"
          + json.dumps(
              {
                  "name": defn.get("name") or r.get("name"),
                  "description": defn.get("description"),
                  "usage": defn.get("usage"),
                  "props": defn.get("props"),
                  "template": (defn.get("web") or {}).get("template"),
                  "css": (defn.get("web") or {}).get("css"),
                  "sample": defn.get("sample"),
                  "token_suggestions": r.get("token_suggestions") or [],
              },
              indent=1,
          )
          + "\n```"
      )
    if issues or warnings:
      err_block: dict[str, Any] = {}
      if issues:
        err_block["blocking_issues_must_fix"] = list(issues)
      if warnings:
        err_block["warnings_to_address"] = list(warnings)
      ctx.append(
          "# Validation errors & warnings in the current definition (fix"
          " EVERY blocking issue so the component can be activated)\n\n"
          "```json\n"
          + json.dumps(err_block, indent=1)
          + "\n```"
      )
    if mismatches:
      ctx.append(
          "# Visual mismatches found by comparing the reference with our"
          " render (fix them; keep what already matches)\n\n```json\n"
          + json.dumps(mismatches.get("mismatches") or [], indent=1)
          + "\n```"
      )
    if instruction:
      ctx.append(
          "# Revision instruction from the designer (apply it; keep the"
          " classification custom)\n\n"
          + instruction
      )
    parts: list[Any] = ["\n\n".join(ctx)] + self._images(r, images)
    if mismatches or instruction or issues:
      prev = self.comps.preview_bytes(r["id"])
      if prev:
        parts.extend(["Our current render:", Image(prev, "image/png")])
    purpose = (
        "revise_component"
        if instruction
        else (
            "repair_component" if (mismatches or issues) else "build_component"
        )
    )
    try:
      resp = self.svc.llm.generate(
          purpose=purpose,
          system=common.prompt("build_component.md"),
          parts=parts,
          json_schema=BUILD_SCHEMA,
          temperature=0.3,
          model=self._model(r["project_id"]),
          timeout_s=300,
      )
    except LlmError as e:
      raise RuntimeError(f"the model is unavailable ({e})") from e
    return resp.data or {}

  def _definition_from(
      self,
      r: dict[str, Any],
      data: dict[str, Any],
      keep_name: bool = False,
      base_defn: dict[str, Any] | None = None,
      facts: dict[str, Any] | None = None,
  ) -> dict[str, Any]:
    cur = (
        base_defn
        if isinstance(base_defn, dict)
        else (r.get("definition") or {})
    )
    name = cur.get("name") or r["name"]
    if not keep_name and data.get("name"):
      name = self._unique_name(r["project_id"], data["name"], r["id"])
    p = self.store.get("projects", r["project_id"]) or {}
    src = r.get("source") or {}
    facts = facts or {}
    web_skel = facts.get("deterministic_web_skeleton") or {}
    basic_skel = facts.get("deterministic_basic_skeleton") or {}
    fit_info = basic_fit_info(src, data, basic_skel)
    # A strategy already written on the definition is honored as pinned by the
    # designer — unless it is the frozen result of an earlier `auto` decision,
    # in which case it is re-decided against the current fit + threshold.
    cur_strategy = str(cur.get("strategy") or "").strip().lower()
    prev_decision = src.get("strategy_decision") or {}
    frozen_auto = bool(prev_decision.get("auto")) and (
        str(prev_decision.get("strategy") or "") == cur_strategy
    )
    explicit_strategy = "" if frozen_auto else cur_strategy
    if not explicit_strategy:
      explicit_strategy = str(src.get("strategy") or "").strip().lower()
    decision = settings_mod.component_strategy_decision(
        p.get("settings"),
        override=explicit_strategy or None,
        basic_fit=(fit_info or {}).get("score"),
    )
    eff_strategy = decision["strategy"]
    if r.get("id"):
      self._merge_source(
          r["id"],
          {"basic_fit": fit_info, "strategy_decision": decision},
      )
    props = _loads(data.get("props_schema_json"), cur.get("props") or {})
    if not (props.get("properties") if isinstance(props, dict) else None):
      if isinstance(basic_skel.get("props"), dict) and basic_skel["props"].get(
          "properties"
      ):
        props = basic_skel["props"]
      elif isinstance(web_skel.get("props"), dict) and web_skel["props"].get(
          "properties"
      ):
        props = web_skel["props"]
    tpl = data.get("template") or (cur.get("web") or {}).get("template") or ""
    css = data.get("css") or (cur.get("web") or {}).get("css") or ""
    if not tpl.strip() and web_skel.get("template"):
      tpl = web_skel["template"]
      css = css or web_skel.get("css") or ""
    if tpl:
      tpl = custom_web.format_template(tpl)
    if css:
      css = custom_web.format_css(css)
    composition = _loads(data.get("composition_json"), {})
    if not lowering.has_composition({"composition": composition}):
      if lowering.has_composition(cur):
        composition = cur.get("composition") or {}
      elif isinstance(basic_skel.get("composition"), dict):
        composition = basic_skel["composition"]
    sample_props = _loads(data.get("sample_props_json"), {})
    if not sample_props:
      sample_props = (
          ((cur.get("sample") or {}).get("props"))
          or basic_skel.get("sample_props")
          or web_skel.get("sample_props")
          or {}
      )
    raw_defn: dict[str, Any] = {
        "name": name,
        "description": data.get("description") or cur.get("description") or "",
        "usage": data.get("usage") or cur.get("usage") or "",
        "props": props,
        "web": {
            "template": tpl,
            "css": css,
        },
        "sample": {
            "props": sample_props,
            "data": _loads(
                data.get("sample_data_json"),
                (cur.get("sample") or {}).get("data") or {},
            ),
            "components": _loads(
                data.get("sample_components_json"),
                (cur.get("sample") or {}).get("components") or [],
            ),
        },
    }
    if (
        explicit_strategy in settings_mod.COMPONENT_STRATEGIES
        and explicit_strategy != "auto"
    ):
      raw_defn["strategy"] = explicit_strategy
    elif eff_strategy != "web_template":
      raw_defn["strategy"] = eff_strategy
    if lowering.has_composition({"composition": composition}):
      raw_defn["composition"] = composition
    defn = custom_web.normalize_definition(raw_defn)
    if (
        lowering.has_composition(defn)
        or eff_strategy == "basic_composition"
        or not ((defn.get("web") or {}).get("template") or "").strip()
    ):
      defn = self.comps._ensure_composition(defn)  # pylint: disable=protected-access
    return custom_web.normalize_definition(defn)

  def _apply_draft(
      self,
      cid: str,
      data: dict[str, Any],
      keep_name: bool = False,
      facts: dict[str, Any] | None = None,
  ) -> dict[str, Any]:
    r = self.store.get("custom_components", cid)
    cls = data.get("classification") or "custom"
    if cls not in ("custom", "basic_equivalent", "skip"):
      cls = "custom"
    if (r.get("source") or {}).get("auto_activate") and (
        data.get("template") or ""
    ).strip():
      cls = "custom"
    tokens = [
        t
        for t in data.get("token_suggestions") or []
        if isinstance(t, dict) and t.get("token")
    ]
    notes = [str(n) for n in data.get("notes") or [] if n]
    extra = {
        "classification": cls,
        "token_suggestions": tokens,
        "notes": "\n".join(notes)[:4000],
        "basic_equivalent": (
            {
                "component": data.get("basic_component") or "",
                "notes": data.get("reason") or "",
            }
            if cls == "basic_equivalent"
            else None
        ),
    }
    if cls != "custom":
      defn = dict(
          r.get("definition") or {},
          name=self._unique_name(
              r["project_id"], data.get("name") or r["name"], cid
          ),
          description=data.get("description") or data.get("reason") or "",
      )
      if cls == "skip":
        extra["notes"] = (data.get("reason") or "") + (
            "\n" + extra["notes"] if extra["notes"] else ""
        )
      extra.update({
          "definition": custom_web.normalize_definition(defn),
          "name": defn["name"],
          "issues": [],
          "warnings": [],
          "updated_at": util.now_iso(),
      })
      return self.store.update("custom_components", cid, extra)
    defn = self._definition_from(r, data, keep_name=keep_name, facts=facts)
    defn["sample"] = self._resolve_images(defn["sample"])
    return self.comps.apply_definition(cid, defn, extra)

  def _resolve_images(self, sample: dict[str, Any]) -> dict[str, Any]:
    """Swaps /img/<slug>/WxH placeholders in a sample for real photos."""
    try:
      if not images.has_placeholders(sample):
        return sample
      resolved, _ = images.Resolver(self.store).resolve(sample)
      return resolved if isinstance(resolved, dict) else sample
    except Exception:  # pylint: disable=broad-except
      traceback.print_exc()
      return sample

  def _compare(self, r: dict[str, Any]) -> dict[str, Any] | None:
    ref = self.comps.reference_bytes(r)
    prev = self.comps.preview_bytes(r["id"])
    if not ref or not prev:
      return None
    try:
      resp = self.svc.llm.generate(
          purpose="compare_component",
          system=common.prompt("compare_component.md"),
          parts=[
              f"Component: {r['name']}",
              "img:1 — design reference:",
              Image(ref[0], ref[1]),
              "img:2 — our web implementation:",
              Image(prev, "image/png"),
          ],
          json_schema=COMPARE_SCHEMA,
          temperature=0.2,
          model=self._model(r["project_id"]),
          timeout_s=180,
      )
    except LlmError:
      traceback.print_exc()
      return None
    d = resp.data or {}
    try:
      score = max(1, min(5, int(d.get("score") or 0)))
    except (TypeError, ValueError):
      score = None
    return {
        "score": score,
        "summary": str(d.get("summary") or "")[:1000],
        "mismatches": [
            {
                "area": str(m.get("area") or "")[:80],
                "issue": str(m.get("issue") or "")[:400],
                "fix": str(m.get("fix") or "")[:400],
            }
            for m in (d.get("mismatches") or [])[:8]
            if isinstance(m, dict)
        ],
        "checked_at": util.now_iso(),
    }

  # ------------------------------------------------------------ editor
  def revise(
      self,
      user,
      cid: str,
      instruction: str = "",
      *,
      definition: dict[str, Any] | None = None,
      issues: list[str] | None = None,
      fix: bool = False,
  ) -> dict[str, Any]:
    r = self.comps.row(user, cid, write=True)
    self._attach_reference_image(r)
    r = self.store.get("custom_components", cid) or r
    defn_in = (
        custom_web.normalize_definition(definition)
        if isinstance(definition, dict)
        else custom_web.normalize_definition(r.get("definition") or {})
    )
    chk_in = self.comps.check(defn_in)
    extra_issues = [
        str(x).strip()
        for x in issues or []
        if isinstance(x, str) and str(x).strip()
    ]
    cur_issues = list(dict.fromkeys(chk_in["issues"] + extra_issues))
    cur_warns = list(chk_in["warnings"])
    fid = r.get("fidelity") if isinstance(r.get("fidelity"), dict) else None
    mismatches = fid if (fix and fid and fid.get("mismatches")) else None
    instruction = (instruction or "").strip()
    if (
        not instruction
        and not fix
        and not cur_issues
        and not cur_warns
        and not mismatches
    ):
      raise ApiError(400, "Say what to change")
    eff_instruction = instruction
    if fix or (not eff_instruction and (cur_issues or cur_warns or mismatches)):
      has_tpl = bool(((defn_in.get("web") or {}).get("template") or "").strip())
      if not has_tpl:
        auto_msg = (
            "Write a complete web implementation (declarative HTML template,"
            " scoped CSS using var(--a2ui-*) tokens, props schema, usage"
            " notes, and realistic sample props) so this component renders and"
            " passes validation with zero issues."
        )
      else:
        auto_msg = (
            "Fix every validation issue and warning in this component"
            " definition so it can be activated cleanly with zero issues."
            " Preserve the component's intent and visual design, declare all"
            " bound props in props_schema_json, include all required props in"
            " sample_props_json, use only allowed HTML tags/attributes/"
            "directives, and replace hard-coded CSS colors with var(--a2ui-*)"
            " theme tokens (or propose custom tokens in token_suggestions)."
            " Explain the fixes briefly in notes."
        )
      eff_instruction = (
          f"{auto_msg}\n\nAdditional designer request: {instruction}"
          if instruction
          else auto_msg
      )
    try:
      data = self._draft(
          r,
          {},
          [],
          mismatches=mismatches,
          instruction=eff_instruction[:4000],
          issues=cur_issues,
          warnings=cur_warns,
          defn_override=defn_in,
      )
      defn = self._definition_from(r, data, keep_name=True, base_defn=defn_in)
      defn["sample"] = self._resolve_images(defn["sample"])
      chk = self.comps.check(defn)
      if chk["issues"]:
        data2 = self._draft(
            r,
            {},
            [],
            instruction=(
                "Fix the remaining validation issues so the component passes"
                " all checks with zero issues."
            ),
            issues=chk["issues"],
            warnings=chk["warnings"],
            defn_override=defn,
        )
        defn2 = self._definition_from(r, data2, keep_name=True, base_defn=defn)
        defn2["sample"] = self._resolve_images(defn2["sample"])
        chk2 = self.comps.check(defn2)
        if len(chk2["issues"]) <= len(chk["issues"]):
          defn, chk, data = defn2, chk2, data2
    except RuntimeError as e:
      raise ApiError(502, str(e)) from e
    notes = [str(n) for n in data.get("notes") or [] if n]
    token_sugs = [
        t
        for t in (
            data.get("token_suggestions") or r.get("token_suggestions") or []
        )
        if isinstance(t, dict) and t.get("token")
    ]
    return {
        "definition": defn,
        "notes": notes,
        "token_suggestions": token_sugs,
        "issues": chk["issues"],
        "warnings": chk["warnings"],
        "fixed_issues": [i for i in cur_issues if i not in chk["issues"]],
    }

  def sample(self, user, cid: str, instruction: str = "") -> dict[str, Any]:
    r = self.comps.row(user, cid)
    defn = r.get("definition") or {}
    parts = [
        "Write realistic sample data for previewing this A2UI custom"
        " component. Return sample props (matching the props schema), an"
        " optional data model, and any Basic components referenced by slot"
        " props. Images: /img/<photo-subject-slug>/<w>x<h>.\n\n```json\n"
        + json.dumps(
            {
                "name": defn.get("name"),
                "description": defn.get("description"),
                "usage": defn.get("usage"),
                "props": defn.get("props"),
                "current_sample": defn.get("sample"),
            },
            indent=1,
        )
        + "\n```"
        + (f"\n\nDesigner's request: {instruction}" if instruction else "")
    ]
    try:
      resp = self.svc.llm.generate(
          purpose="component_sample",
          system="You write realistic, varied sample data for UI previews.",
          parts=parts,
          json_schema=SAMPLE_SCHEMA,
          temperature=0.8,
          model=self._model(r["project_id"]),
          timeout_s=120,
      )
    except LlmError as e:
      raise ApiError(502, f"the model is unavailable ({e})") from e
    d = resp.data or {}
    return {
        "sample": self._resolve_images({
            "props": _loads(d.get("sample_props_json"), {}),
            "data": _loads(d.get("sample_data_json"), {}),
            "components": _loads(d.get("sample_components_json"), []),
        })
    }

  def recheck(self, user, cid: str) -> dict[str, Any]:
    r = self.comps.row(user, cid, write=True)
    if not self.comps.reference_bytes(r):
      raise ApiError(400, "This component has no reference image to compare")
    self.comps.set_job(cid, "queued")

    def job():
      try:
        self._render_compare_repair(cid, {}, [], allow_repair=False)
        self.comps.set_job(cid, "done")
      except Exception as e:  # pylint: disable=broad-except
        traceback.print_exc()
        self.comps.set_job(cid, "error", str(e))

    self.executor.submit(job)
    return self.comps.fmt(self.store.get("custom_components", cid))


def _png_size(data: bytes) -> tuple[int, int]:
  from refs.references import png_size  # pylint: disable=g-import-not-at-top

  return png_size(data)
