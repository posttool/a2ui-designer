# pylint: disable=missing-function-docstring,g-long-ternary
"""Imports: ingest → analyze into extractions → questions → merge plan → apply.

Figma files, Stitch projects and screenshots never change a project directly.
Every import is split into *extractions* (component / template /
token_reference / ignore), the designer answers questions about the uncertain
ones, and only a reviewed merge plan writes tokens, components, templates or a
constitution revision. See docs/IMPORTS.md.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import traceback
from typing import Any

from core import diffs
from core import settings as settings_mod
from core import util
from core.a2ui import catalog as catalog_mod
from core.service import ApiError
from llm import gemini
from refs import figma
from refs import figma_compiler
from refs import references as refs_mod
from refs import stitch
from sim import common
from sim.reviser import _theme_digest  # pylint: disable=protected-access

KINDS = ("component", "template", "token_reference", "ignore")
KIND_LABELS = {
    "component": "Reusable component",
    "template": "Template (screen/section built from components)",
    "token_reference": "Token / style reference",
    "ignore": "Ignore",
}
STATUSES = ("proposed", "needs_answer", "confirmed", "ignored", "merged")
ASK_BELOW = 0.6  # confidence under this always becomes a question
MAX_COMPONENT_IMAGES = 30
MAX_PLAN_IMAGES = 24
LIVE_PHASES = ("ingesting", "analyzing", "planning")

_OPTION = common.obj(
    {"value": common.str_schema(), "label": common.str_schema()}
)
_TOKEN = common.obj({
    "token": common.str_schema(
        "Canonical theme token key (primary, on_primary, surface,"
        " surface_container, on_surface, outline, radius_card,"
        " radius_control, radius_chip, font_family, font_family_heading,"
        " type_scale, heading_scale, heading_weight, space, card_padding,"
        " scheme) or a custom token name (e.g. brand-accent)."
    ),
    "value": common.str_schema("Value as a string ('#0B57D0', '16', 'Inter')."),
    "is_custom": {"type": "boolean"},
    "maps_to": common.str_schema(
        "Canonical key a custom token maps to, or ''."
    ),
    "confidence": {"type": "string", "enum": ["exact", "approx"]},
    "reason": common.str_schema(),
})
_SAMPLE_ITEM = common.obj({
    "title": common.str_schema("Short descriptive sample title."),
    "category": common.str_schema(
        "Primary domain category label or slug, e.g. 'Travel' or 'Healthcare'."
    ),
    "subcategory": common.str_schema(
        "Subcategory label or slug, e.g. 'Flight Status' or 'Patient Portal'."
    ),
    "intent": common.str_schema(
        "User prompt or scenario that triggers this UI surface."
    ),
    "size_class": {"type": "string", "enum": ["s", "m", "l"]},
    "state": {
        "type": "string",
        "enum": ["content", "empty", "loading", "error"],
    },
    "context_json": common.str_schema(
        "JSON object string for sample context (locale, surface, user_state)."
    ),
    "payload_json": common.str_schema(
        "JSON object string with domain records, metrics, items and text from"
        " the imported screens."
    ),
    "edge_cases": common.arr(common.str_schema()),
    "tags": common.arr(common.str_schema()),
    "source_image": {
        "type": "integer",
        "description": (
            "1-based img:<n> index this sample came from (0 = synthesized"
            " across the project)."
        ),
    },
    "sources": common.arr(common.str_schema("Related extraction ids.")),
})

ANALYZE_SCHEMA = common.obj({
    "summary_md": common.str_schema("What the import contains (≤120 words)."),
    "extractions": common.arr(
        common.obj({
            "id": common.str_schema(
                "Id of an existing extraction you are updating, or '' for a"
                " new one."
            ),
            "kind": {"type": "string", "enum": list(KINDS)},
            "title": common.str_schema(
                "Short name, e.g. 'Order summary card'."
            ),
            "description": common.str_schema(),
            "image": {
                "type": "integer",
                "description": "img:<n> number the region is on (0 = none).",
            },
            "box_2d": common.arr(
                {"type": "integer"},
                "[ymin, xmin, ymax, xmax] normalized 0–1000 on that image;"
                " [] when the extraction is the whole image.",
            ),
            "node_id": common.str_schema("Figma node id when known, else ''."),
            "instances": common.arr(
                common.obj({
                    "image": {"type": "integer"},
                    "box_2d": common.arr({"type": "integer"}),
                }),
                "Other places the same thing appears.",
            ),
            "confidence": {
                "type": "number",
                "description": "0–1 confidence in the kind.",
            },
            "rationale": common.str_schema(
                "Why this kind (one or two sentences)."
            ),
            "uses": common.arr(
                common.str_schema(),
                "Templates: titles of component extractions or names of Basic /"
                " existing custom components the template is built from.",
            ),
            "question": common.str_schema(
                "A question for the designer when the kind is uncertain, else"
                " ''."
            ),
            "question_options": common.arr(_OPTION),
            "component": common.obj({
                "purpose": common.str_schema(),
                "maps_to_basic": common.arr(common.str_schema()),
                "props_schema_json": common.str_schema(
                    "JSON Schema of the props as a string, or ''."
                ),
                "matches_existing": common.str_schema(
                    "Name of an existing custom component it duplicates, or ''."
                ),
            }),
            "template": common.obj({
                "when_to_use": common.str_schema(),
                "structure_md": common.str_schema(
                    "Ordered outline of the regions and which components fill"
                    " them."
                ),
                "slots": common.arr(common.str_schema()),
            }),
            "tokens": common.arr(_TOKEN),
        })
    ),
    "questions": common.arr(
        common.obj({
            "id": common.str_schema("Short id like q1."),
            "text": common.str_schema(),
            "options": common.arr(_OPTION),
            "about": common.arr(
                common.str_schema("Titles of related extractions.")
            ),
        }),
        "Import-wide questions (e.g. is this palette the brand or sample"
        " content?). Usually 0–2.",
    ),
    "project_data": common.obj({
        "domain_summary": common.str_schema(
            "1-2 sentences describing the project domain and data entities."
        ),
        "default_category": common.str_schema(
            "Primary category label or slug for this project's data."
        ),
        "default_subcategory": common.str_schema(
            "Primary subcategory label or slug for this project's data."
        ),
        "samples": common.arr(_SAMPLE_ITEM),
    }),
})

PLAN_SCHEMA = common.obj({
    "summary_md": common.str_schema(
        "What applying this plan does (≤100 words)."
    ),
    "tokens": common.arr(
        common.obj(
            dict(
                _TOKEN["properties"],
                sources=common.arr(common.str_schema("extraction id")),
            )
        )
    ),
    "components": common.arr(
        common.obj({
            "extraction_id": common.str_schema(),
            "action": {
                "type": "string",
                "enum": ["draft_custom", "map_basic", "link_existing"],
            },
            "name": common.str_schema("PascalCase component name."),
            "basic": common.str_schema(
                "Basic component for map_basic, else ''."
            ),
            "existing": common.str_schema(
                "Existing custom component name for link_existing, else ''."
            ),
            "purpose": common.str_schema(),
        })
    ),
    "templates": common.arr(
        common.obj({
            "extraction_id": common.str_schema(),
            "name": common.str_schema(),
            "description": common.str_schema(),
            "when_to_use": common.str_schema(),
            "structure_md": common.str_schema(),
            "components": common.arr(common.str_schema()),
        })
    ),
    "constitution": common.obj({
        "constitution_md": common.str_schema(
            "Full new constitution text, or '' when it should not change."
        ),
        "change_summary": common.str_schema(),
        "changed_sections": common.arr(common.str_schema()),
    }),
    "data": common.obj({
        "sample_set_name": common.str_schema(
            "Readable name for the imported project data sample set."
        ),
        "sample_set_description": common.str_schema(
            "Short description of the project data sample set."
        ),
        "samples": common.arr(_SAMPLE_ITEM),
    }),
})


def _pascal(text: str) -> str:
  words = re.findall(r"[A-Za-z0-9]+", text or "")
  out = "".join(w[:1].upper() + w[1:] for w in words)
  if out and out[0].isdigit():
    out = "C" + out
  return out[:60]


def _find_figma_node(
    root: dict[str, Any], node_id: str, depth: int = 0
) -> dict[str, Any] | None:
  """Depth-first lookup of a Figma node by id inside a raw frame subtree."""
  if not isinstance(root, dict) or depth > 24:
    return None
  if str(root.get("id") or "") == node_id:
    return root
  for child in root.get("children") or []:
    hit = _find_figma_node(child, node_id, depth + 1)
    if hit:
      return hit
  return None


def _box_pct(box: Any) -> dict[str, float] | None:
  """Stored region box `[x0, y0, x1, y1]` (0–1000) → `{x, y, w, h}` percent for `figma.resolve_box_node`."""
  if not isinstance(box, (list, tuple)) or len(box) != 4:
    return None
  try:
    x0, y0, x1, y1 = (float(v) for v in box)
  except (TypeError, ValueError):
    return None
  if x1 <= x0 or y1 <= y0:
    return None
  return {"x": x0 / 10, "y": y0 / 10, "w": (x1 - x0) / 10, "h": (y1 - y0) / 10}


def _box(raw: Any) -> list[int] | None:
  """Gemini [ymin,xmin,ymax,xmax] (0–1000) → stored [x0,y0,x1,y1] or None."""
  if not isinstance(raw, list) or len(raw) != 4:
    return None
  try:
    y0, x0, y1, x1 = (max(0, min(1000, int(v))) for v in raw)
  except (TypeError, ValueError):
    return None
  x0, x1 = sorted((x0, x1))
  y0, y1 = sorted((y0, y1))
  if x1 - x0 < 5 or y1 - y0 < 5:
    return None
  if x0 <= 5 and y0 <= 5 and x1 >= 995 and y1 >= 995:
    return None  # whole image
  return [x0, y0, x1, y1]


def _clean_box(raw: Any) -> list[int] | None:
  """Client [x0,y0,x1,y1] (0–1000) → validated box or None."""
  if not isinstance(raw, list) or len(raw) != 4:
    return None
  try:
    x0, y0, x1, y1 = (max(0, min(1000, int(round(float(v))))) for v in raw)
  except (TypeError, ValueError):
    return None
  x0, x1 = sorted((x0, x1))
  y0, y1 = sorted((y0, y1))
  if x1 - x0 < 3 or y1 - y0 < 3:
    return None
  return [x0, y0, x1, y1]


def _options(raw: Any) -> list[dict[str, str]]:
  out = []
  for o in raw or []:
    if isinstance(o, dict) and str(o.get("value") or "").strip():
      out.append({
          "value": str(o["value"]).strip()[:60],
          "label": str(o.get("label") or o["value"]).strip()[:160],
      })
  return out[:6]


def _default_options() -> list[dict[str, str]]:
  return [{"value": k, "label": KIND_LABELS[k]} for k in KINDS]


_SIZE_MAP = {
    "s": "s",
    "m": "m",
    "l": "l",
    "compact": "s",
    "small": "s",
    "medium": "m",
    "expanded": "l",
    "large": "l",
}
_STATE_MAP = {
    "content": "content",
    "populated": "content",
    "partial": "content",
    "edge": "content",
    "empty": "empty",
    "loading": "loading",
    "error": "error",
}


def _parse_json_obj(raw: Any, fallback: dict[str, Any] | None = None) -> dict:
  if isinstance(raw, dict):
    return raw
  if isinstance(raw, str) and raw.strip():
    try:
      v = json.loads(raw)
      if isinstance(v, dict):
        return v
    except ValueError:
      pass
  return dict(fallback or {})


def _normalize_sample_item(
    raw: dict[str, Any],
    default_cat: str = "Imported",
    default_subcat: str = "General",
    idx: int = 0,
) -> dict[str, Any] | None:
  if not isinstance(raw, dict):
    return None
  title = str(raw.get("title") or "").strip()[:200]
  payload = _parse_json_obj(
      raw.get("payload") if "payload" in raw else raw.get("payload_json"),
      {},
  )
  if not title and not payload:
    return None
  if not title:
    title = f"Sample {idx + 1}"
  cat = str(raw.get("category") or default_cat or "Imported").strip()[:80]
  subcat = str(raw.get("subcategory") or default_subcat or "General").strip()[
      :80
  ]
  intent = str(raw.get("intent") or f"Show {title}").strip()[:500]
  size_raw = str(raw.get("size_class") or "m").strip().lower()
  size_class = _SIZE_MAP.get(size_raw, "m")
  state_raw = str(raw.get("state") or "content").strip().lower()
  state = _STATE_MAP.get(state_raw, "content")
  context = _parse_json_obj(
      raw.get("context") if "context" in raw else raw.get("context_json"),
      {"surface": "mobile", "locale": "en-US"},
  )
  if not payload:
    payload = {"title": title, "intent": intent}
  src_img = raw.get("source_image")
  try:
    src_img_int = int(src_img) if src_img is not None else 0
  except (TypeError, ValueError):
    src_img_int = 0
  return {
      "title": title,
      "category": cat,
      "subcategory": subcat,
      "intent": intent,
      "size_class": size_class,
      "state": state,
      "context": context,
      "payload": payload,
      "edge_cases": [
          str(x).strip()[:80]
          for x in raw.get("edge_cases") or []
          if str(x).strip()
      ][:8],
      "tags": [
          str(x).strip()[:60] for x in raw.get("tags") or [] if str(x).strip()
      ][:10],
      "source_image": max(0, src_img_int),
      "sources": [
          str(x).strip() for x in raw.get("sources") or [] if str(x).strip()
      ][:10],
  }


def _fallback_project_samples(
    r: dict[str, Any],
    xs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
  """Synthesizes structured project data samples from Figma/Stitch frames/facts when the LLM omits them."""
  facts = r.get("facts") or {}
  src = r.get("source") or {}
  import_title = (
      src.get("file_name")
      or facts.get("file_name")
      or r.get("title")
      or "Project"
  ).strip()
  clean_cat = import_title[:60] if import_title else "Project Data"
  contents = facts.get("frame_contents") or facts.get("screen_contents") or []
  images = [
      im for im in r.get("images") or [] if im.get("role") != "component"
  ] or list(r.get("images") or [])
  out: list[dict[str, Any]] = []
  by_name = {
      str(c.get("name") or "").strip().lower(): c.get("texts") or []
      for c in contents
      if isinstance(c, dict)
  }
  by_id = {
      str(c.get("id") or "").strip(): c.get("texts") or []
      for c in contents
      if isinstance(c, dict) and c.get("id")
  }
  xs_list = list(xs or [])

  for idx, im in enumerate(images[:8]):
    name = str(im.get("name") or f"Screen {idx + 1}").strip()
    fid = str(im.get("frame_id") or "").strip()
    texts = by_id.get(fid) or by_name.get(name.lower()) or []
    matching_xs = [
        x["id"]
        for x in xs_list
        if (x.get("region") or {}).get("image_index") == idx
        and x.get("kind") in ("template", "component")
    ]
    headline = texts[0] if texts else name
    items = texts[1:12] if len(texts) > 1 else [name]
    vp = im.get("viewport_hint") or "phone"
    out.append({
        "title": (
            f"{name} — {headline}"[:120] if headline != name else name[:120]
        ),
        "category": clean_cat,
        "subcategory": name[:60] or "Screen",
        "intent": f"Display {name} for {import_title}",
        "size_class": "l" if vp in ("desktop", "tablet") else "m",
        "state": "content",
        "context": {
            "surface": vp if vp != "unknown" else "phone",
            "source_kind": r.get("kind") or "import",
            "screen_name": name,
        },
        "payload": {
            "screen": name,
            "headline": headline,
            "items": items,
            "caption": im.get("caption") or "",
        },
        "edge_cases": [],
        "tags": [
            r.get("kind") or "import",
            name.lower().replace(" ", "_")[:32],
        ],
        "source_image": idx + 1,
        "sources": matching_xs[:6],
    })

  if not out and contents:
    for idx, c in enumerate(contents[:6]):
      name = str(c.get("name") or f"Screen {idx + 1}").strip()
      texts = list(c.get("texts") or [])
      headline = texts[0] if texts else name
      out.append({
          "title": name[:120],
          "category": clean_cat,
          "subcategory": name[:60] or "General",
          "intent": f"Display {name} for {import_title}",
          "size_class": "m",
          "state": "content",
          "context": {
              "surface": "phone",
              "source_kind": r.get("kind") or "import",
              "screen_name": name,
          },
          "payload": {
              "screen": name,
              "headline": headline,
              "items": texts[1:12],
          },
          "edge_cases": [],
          "tags": [r.get("kind") or "import"],
          "source_image": idx + 1,
          "sources": [],
      })
  return out


def _normalize_project_data(
    r: dict[str, Any],
    raw_pd: Any,
    xs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
  pd = raw_pd if isinstance(raw_pd, dict) else {}
  src = r.get("source") or {}
  facts = r.get("facts") or {}
  import_title = (
      src.get("file_name")
      or facts.get("file_name")
      or r.get("title")
      or "Project"
  ).strip()
  default_cat = str(
      pd.get("default_category") or import_title or "Imported"
  ).strip()[:80]
  default_subcat = str(pd.get("default_subcategory") or "Screens").strip()[:80]
  samples: list[dict[str, Any]] = []
  for idx, item in enumerate(pd.get("samples") or []):
    norm = _normalize_sample_item(item, default_cat, default_subcat, idx)
    if norm:
      samples.append(norm)
  if not samples and (
      r.get("kind") in ("figma", "stitch") or src.get("import_data")
  ):
    samples = _fallback_project_samples(r, xs)
  return {
      "domain_summary": str(
          pd.get("domain_summary")
          or f"Project data extracted from {import_title}."
      )[:600],
      "default_category": default_cat,
      "default_subcategory": default_subcat,
      "samples": samples[:16],
  }


class ImportManager:
  """Analysis, review and merge of imports (rows of `design_references`)."""

  def __init__(self, svc, refs, builder, comps, pool=None):
    self.svc = svc
    self.store = svc.store
    self.refs = refs
    self.builder = builder
    self.comps = comps
    self.pool = pool
    self.executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="imports"
    )
    refs.on_ready = self.on_ingested

  # ------------------------------------------------------------ helpers
  def _row(self, user, iid: str, write: bool = False) -> dict[str, Any]:
    r = self.store.get("design_references", iid)
    if not r:
      raise ApiError(404, "Import not found")
    self.svc._project_row(r["project_id"], user, write=write)  # pylint: disable=protected-access
    return r

  def _set(self, iid: str, **fields) -> None:
    fields["updated_at"] = util.now_iso()
    self.store.update("design_references", iid, fields)

  def _xrows(self, iid: str) -> list[dict[str, Any]]:
    return self.store.query(
        "SELECT * FROM import_extractions WHERE import_id=? ORDER BY"
        " order_idx, created_at",
        [iid],
    )

  def _x(self, iid: str, xid: str) -> dict[str, Any]:
    x = self.store.get("import_extractions", xid)
    if not x or x["import_id"] != iid:
      raise ApiError(404, "Extraction not found")
    return x

  @staticmethod
  def phase_of(r: dict[str, Any]) -> str:
    if r.get("phase"):
      return r["phase"]
    if r.get("status") == "error":
      return "error"
    if r.get("status") == "fetching":
      return "ingesting"
    # Design references from before Imports existed (analyzed into a chat
    # proposal, or never analyzed): the UI offers "Analyze as import".
    return "legacy"

  @staticmethod
  def _file_url(sub: str, fn: str | None, v: str = "") -> str | None:
    if not fn:
      return None
    return f"/files/{sub}/{fn}" + (f"?v={v}" if v else "")

  def fmt_x(self, x: dict[str, Any], used_by=None) -> dict[str, Any]:
    v = (x.get("updated_at") or "").replace(":", "")
    return {
        "id": x["id"],
        "import_id": x["import_id"],
        "kind": x.get("kind"),
        "status": x.get("status"),
        "title": x.get("title") or "",
        "description": x.get("description") or "",
        "region": (
            x.get("region")
            or {
                "image_index": None,
                "box": None,
                "node_id": None,
                "instances": [],
            }
        ),
        "crop_url": self._file_url("references", x.get("crop_file"), v),
        "confidence": x.get("confidence"),
        "rationale": x.get("rationale") or "",
        "uses": x.get("uses") or [],
        "used_by": used_by or [],
        "question": x.get("question"),
        "answer": x.get("answer"),
        "payload": x.get("payload") or {},
        "merged_into": x.get("merged_into") or [],
        "created_at": x.get("created_at"),
        "updated_at": x.get("updated_at"),
    }

  def fmt(self, r: dict[str, Any], with_x: bool = False) -> dict[str, Any]:
    out = self.refs.fmt(r)
    xs = self._xrows(r["id"])
    counts = {k: 0 for k in KINDS}
    counts.update(open_questions=0, merged=0, total=len(xs))
    for x in xs:
      if x.get("status") == "ignored" or x.get("kind") == "ignore":
        counts["ignore"] += 1
      elif x.get("kind") in counts:
        counts[x["kind"]] += 1
      if x.get("status") == "needs_answer":
        counts["open_questions"] += 1
      if x.get("status") == "merged":
        counts["merged"] += 1
    questions = r.get("questions") or []
    counts["open_questions"] += sum(1 for q in questions if not q.get("answer"))
    pd_samples = ((r.get("analysis") or {}).get("project_data") or {}).get(
        "samples"
    ) or []
    plan_data_lines = [
        ln
        for ln in (r.get("merge_plan") or {}).get("lines") or []
        if ln.get("type") == "data"
    ]
    counts["data"] = (
        len(plan_data_lines) if plan_data_lines else len(pd_samples)
    )
    out.update({
        "phase": self.phase_of(r),
        "origin": r.get("origin") or "imports",
        "counts": counts,
        "questions": questions,
        "merge_plan": r.get("merge_plan"),
        "merge_log": r.get("merge_log") or [],
    })
    if with_x:
      used_by: dict[str, list[str]] = {}
      for x in xs:
        for u in x.get("uses") or []:
          if u.get("xid"):
            used_by.setdefault(u["xid"], []).append(x["id"])
      out["extractions"] = [self.fmt_x(x, used_by.get(x["id"])) for x in xs]
    return out

  def list(self, user, pid: str) -> list[dict[str, Any]]:
    self.svc._project_row(pid, user)  # pylint: disable=protected-access
    rows = self.store.query(
        "SELECT * FROM design_references WHERE project_id=? ORDER BY"
        " created_at DESC",
        [pid],
    )
    return [self.fmt(r) for r in rows]

  def get(self, user, iid: str) -> dict[str, Any]:
    return self.fmt(self._row(user, iid), with_x=True)

  # ------------------------------------------------------------ ingest
  def _progress(self, iid: str, step_id: str, **kwargs) -> dict[str, Any]:
    r = self.store.get("design_references", iid) or {}
    row_fields = kwargs.pop("row_fields", None) or {}
    prog = refs_mod.step_progress(r.get("progress"), step_id, **kwargs)
    self._set(iid, progress=prog, **row_fields)
    return prog

  def on_ingested(self, iid: str, secrets: dict[str, Any] | None = None):
    """Called by ReferenceManager when fetch/upload finished successfully."""
    self.executor.submit(self._post_ingest, iid, dict(secrets or {}))

  def _post_ingest(self, iid: str, secrets: dict[str, Any]) -> None:
    try:
      r = self.store.get("design_references", iid)
      if not r:
        return
      if r["kind"] == "figma":
        if secrets.get("figma_token"):
          self._figma_components(r, secrets["figma_token"])
        else:
          self._progress(
              iid,
              "scan_components",
              status="done",
              step_detail="Skipped",
          )
      self._set(iid, phase="analyzing", error=None)
      self.run_analysis(iid)
    except Exception as e:  # pylint: disable=broad-except
      traceback.print_exc()
      r = self.store.get("design_references", iid) or {}
      cur_step = (r.get("progress") or {}).get("step") or "gemini_analyze"
      prog = refs_mod.step_progress(
          r.get("progress"),
          cur_step,
          status="error",
          label="Analysis failed",
          detail=str(e)[:200],
      )
      self._set(
          iid,
          phase="error",
          error=f"Analysis failed: {e}"[:600],
          progress=prog,
      )

  def _figma_components(self, r: dict[str, Any], token: str) -> None:
    """Adds the file's components (thumbnails + facts) to the import."""
    iid = r["id"]
    url = (r.get("source") or {}).get("url") or ""
    fname = (r.get("source") or {}).get("file_name") or r.get("title") or ""
    self._progress(
        iid,
        "scan_components",
        status="running",
        label="Scanning Figma file for reusable components…",
        detail=fname,
        step_detail="Scanning…",
    )
    try:
      res = self.builder.figma_scan(token, url)
    except Exception:  # pylint: disable=broad-except
      traceback.print_exc()
      self._progress(
          iid,
          "scan_components",
          status="done",
          step_detail="Scan unavailable",
      )
      return
    comps = (res.get("components") or [])[:MAX_COMPONENT_IMAGES]
    if not comps:
      self._progress(
          iid,
          "scan_components",
          status="done",
          label="No standalone Figma components found",
          step_detail="0 components",
      )
      return
    images = list(r.get("images") or [])
    have = {im.get("frame_id") for im in images if im.get("frame_id")}
    comps_new = [c for c in comps if c["node_id"] not in have]
    thumbs: dict[str, bytes] = {}
    if comps_new:
      names_preview = ", ".join(
          (c.get("name") or c["node_id"]) for c in comps_new[:4]
      )
      if len(comps_new) > 4:
        names_preview += f" +{len(comps_new) - 4} more"
      self._progress(
          iid,
          "scan_components",
          status="running",
          label=(
              f"Rendering {len(comps_new)} Figma component thumbnail"
              f"{'s' if len(comps_new) != 1 else ''}…"
          ),
          detail=names_preview,
          step_detail=f"0/{len(comps_new)} thumbnails",
          done=0,
          total=len(comps_new),
      )
      try:
        thumbs = self.builder.figma_thumbs(
            token, res.get("file_key") or "", [c["node_id"] for c in comps_new]
        )
      except Exception:  # pylint: disable=broad-except
        traceback.print_exc()
    for c in comps_new:
      data = thumbs.get(c["node_id"])
      fn = None
      if data:
        fn = self.refs._save_image(r["id"], len(images), data, "png")  # pylint: disable=protected-access
      images.append({
          "file": fn,
          "name": c.get("name") or c["node_id"],
          "caption": c.get("description") or "",
          "viewport_hint": "unknown",
          "width": c.get("width"),
          "height": c.get("height"),
          "frame_id": c["node_id"],
          "role": "component",
      })
    facts = dict(r.get("facts") or {})
    facts["figma_components"] = [
        {
            k: c.get(k)
            for k in (
                "node_id",
                "name",
                "kind",
                "description",
                "width",
                "height",
                "variant_count",
                "variant_props",
            )
        }
        for c in comps
    ]
    self._progress(
        iid,
        "scan_components",
        status="done",
        label=(
            f"Scanned {len(comps)} Figma component"
            f"{'s' if len(comps) != 1 else ''}"
        ),
        detail=fname,
        step_detail=f"{len(comps)} component{'s' if len(comps) != 1 else ''}",
        done=len(comps_new),
        total=len(comps_new),
        row_fields={"images": images, "facts": facts},
    )

  # ------------------------------------------------------------ analyze
  def analyze(self, user, iid: str, body: dict[str, Any]) -> dict[str, Any]:
    r = self._row(user, iid, write=True)
    ph = self.phase_of(r)
    if ph in LIVE_PHASES:
      raise ApiError(409, f"Import is {ph}; wait for it to finish")
    if not r.get("images") and not r.get("facts"):
      raise ApiError(400, "Nothing to analyze yet")
    prog = refs_mod.make_progress(r["kind"], "reanalyze")
    self._set(iid, phase="analyzing", error=None, progress=prog)
    instr = str(body.get("instructions") or "")[:2000]

    def job():
      try:
        self.run_analysis(iid, instr)
      except Exception as e:  # pylint: disable=broad-except
        traceback.print_exc()
        r_err = self.store.get("design_references", iid) or {}
        cur_step = (r_err.get("progress") or {}).get("step") or "gemini_analyze"
        p_err = refs_mod.step_progress(
            r_err.get("progress"),
            cur_step,
            status="error",
            label="Analysis failed",
            detail=str(e)[:200],
        )
        self._set(
            iid,
            phase="error",
            error=f"Analysis failed: {e}"[:600],
            progress=p_err,
        )

    self.executor.submit(job)
    return self.get(user, iid)

  def _image_parts(self, r: dict[str, Any]) -> list[Any]:
    parts: list[Any] = []
    for i, im in enumerate(r.get("images") or []):
      role = im.get("role") or (
          "screen"
          if r["kind"] == "stitch"
          else "frame"
          if r["kind"] == "figma"
          else "upload"
      )
      label = (
          f"img:{i + 1} — {role} “{im.get('name')}”"
          f" [{im.get('width')}×{im.get('height')}, viewport:"
          f" {im.get('viewport_hint')}]"
          + (f" node_id={im['frame_id']}" if im.get("frame_id") else "")
          + (f" — caption: {im['caption']}" if im.get("caption") else "")
      )
      parts.append(label)
      data = self._image_bytes(im)
      if data:
        parts.append(gemini.Image(data[0], data[1]))
      else:
        parts.append("(no image available)")
    return parts

  def _image_bytes(self, im: dict[str, Any]) -> tuple[bytes, str] | None:
    fn = im.get("file")
    if not fn:
      return None
    try:
      with open(self.store.file_path("references", fn), "rb") as fh:
        data = fh.read()
    except OSError:
      return None
    mime = {
        "jpg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(fn.rsplit(".", 1)[-1], "image/png")
    return data, mime

  def _project_ctx(
      self, pid: str
  ) -> tuple[dict[str, Any], dict[str, Any], list[str], Any]:
    p = self.store.get("projects", pid)
    s = settings_mod.merged(p["settings"])
    cat = self.svc.catalog_for(p)
    cons = (self.svc.current_revision(pid) or {}).get("text_md") or ""
    customs = self.store.query(
        "SELECT id, name, status, definition_json FROM custom_components"
        " WHERE project_id=? AND status!='archived'",
        [pid],
    )
    tpls = self.store.query(
        "SELECT id, name, when_to_use FROM templates WHERE project_id=? AND"
        " status='active'",
        [pid],
    )
    enabled = cat.enabled_components()
    ctx = [
        "# Current constitution\n\n" + (cons.strip() or "(empty)")[:6000],
        _theme_digest(s["theme"]),
        "# Basic catalog components\n\nEnabled: " + ", ".join(enabled),
    ]
    if customs:
      ctx.append(
          "# Existing custom components\n\n"
          + "\n".join(
              f"- {c['name']} ({c['status']}): "
              + str((c.get("definition") or {}).get("description") or "")[:160]
              for c in customs
          )
      )
    if tpls:
      ctx.append(
          "# Existing templates\n\n"
          + "\n".join(
              f"- {t['name']}: {t.get('when_to_use') or ''}" for t in tpls
          )
      )
    return p, s, ctx, cat

  def run_analysis(self, iid: str, instructions: str = "") -> None:
    r = self.store.get("design_references", iid)
    pid = r["project_id"]
    p, s, ctx, _ = self._project_ctx(pid)
    ctx.append(
        f"# Import\n\nKind: {r['kind']}. Title: {r.get('title') or ''}."
        f" Images: {len(r.get('images') or [])}."
    )
    if r.get("notes"):
      ctx.append("# Designer's notes\n\n" + r["notes"])
    if r.get("facts"):
      ctx.append(
          "# Extracted facts (exact values)\n\n```json\n"
          + common.clip_json(r["facts"], 16000)
          + "\n```"
      )
    prev = self._xrows(iid)
    if prev:
      ctx.append(
          "# Current extractions (keep their ids; never change kind of"
          " confirmed/ignored/merged ones; use the designer's answers)\n\n"
          "```json\n"
          + common.clip_json(
              [
                  {
                      "id": x["id"],
                      "kind": x["kind"],
                      "status": x["status"],
                      "title": x["title"],
                      "image": (
                          ((x.get("region") or {}).get("image_index") or 0) + 1
                          if (x.get("region") or {}).get("image_index")
                          is not None
                          else 0
                      ),
                      "box": (x.get("region") or {}).get("box"),
                      "answer": x.get("answer"),
                  }
                  for x in prev
              ],
              8000,
          )
          + "\n```"
      )
    answered = [q for q in r.get("questions") or [] if q.get("answer")]
    if answered:
      ctx.append(
          "# Designer's answers to import-wide questions\n\n"
          + "\n".join(
              f"- {q['text']} → {q['answer'].get('choice')}"
              + (f" ({q['answer']['note']})" if q["answer"].get("note") else "")
              for q in answered
          )
      )
    if instructions:
      ctx.append("# Designer's instructions\n\n" + instructions)
    images = r.get("images") or []
    n_comps = sum(1 for im in images if im.get("role") == "component")
    n_screens = len(images) - n_comps
    model_name = s["generator"].get("model") or "gemini-3.8-flash"
    noun = (
        "screen"
        if r["kind"] == "stitch"
        else "frame"
        if r["kind"] == "figma"
        else "screenshot"
    )
    bits = []
    if n_screens:
      bits.append(f"{n_screens} {noun}{'s' if n_screens != 1 else ''}")
    if n_comps:
      bits.append(f"{n_comps} component thumbnail{'s' if n_comps != 1 else ''}")
    if r.get("facts"):
      kb = max(1, round(len(common.clip_json(r["facts"], 16000)) / 1024))
      bits.append(f"{kb} KB of extracted tokens & styles")
    if prev:
      bits.append(
          f"{len(prev)} prior extraction{'s' if len(prev) != 1 else ''}"
      )
    self._progress(
        iid,
        "gemini_analyze",
        status="running",
        label=f"Separating components, templates & tokens with {model_name}…",
        detail=" · ".join(bits) or "Inspecting import…",
        step_detail=(
            f"{model_name} · {len(images)}"
            f" image{'s' if len(images) != 1 else ''}"
        ),
        extra={"model": model_name},
    )
    parts: list[Any] = ["\n\n".join(ctx)] + self._image_parts(r)
    try:
      resp = self.svc.llm.generate(
          purpose="analyze_import",
          system=common.prompt("analyze_import.md"),
          parts=parts,
          json_schema=ANALYZE_SCHEMA,
          temperature=0.2,
          model=s["generator"].get("model"),
          timeout_s=300,
      )
    except gemini.LlmError as e:
      raise RuntimeError(f"the model is unavailable ({e})") from e
    data = resp.data or {}
    self._store_analysis(r, data, prev, p)
    old_q = {q["id"]: q for q in r.get("questions") or []}
    questions = []
    for i, q in enumerate(data.get("questions") or []):
      text = str(q.get("text") or "").strip()
      if not text:
        continue
      qid = (
          re.sub(r"[^a-z0-9_]", "", str(q.get("id") or "").lower()) or f"q{i+1}"
      )
      prior = old_q.get(qid) or next(
          (o for o in old_q.values() if o["text"] == text), None
      )
      questions.append({
          "id": qid,
          "text": text[:500],
          "options": (
              _options(q.get("options"))
              or [
                  {"value": "yes", "label": "Yes"},
                  {"value": "no", "label": "No"},
              ]
          ),
          "about": self._titles_to_ids(iid, q.get("about") or []),
          "answer": (prior or {}).get("answer"),
      })
    # Answered questions the model dropped stay in the record.
    for q in old_q.values():
      if q.get("answer") and all(q["id"] != n["id"] for n in questions):
        questions.append(q)
    xs_now = self._xrows(iid)
    n_open = sum(1 for x in xs_now if x.get("status") == "needs_answer") + sum(
        1 for q in questions if not q.get("answer")
    )
    project_data = _normalize_project_data(r, data.get("project_data"), xs_now)
    analysis_info = {
        "summary": data.get("summary_md") or "",
        "model": resp.model,
        "at": util.now_iso(),
        "project_data": project_data,
    }
    self._progress(
        iid,
        "gemini_analyze",
        status="done",
        label=(
            f"Identified {len(xs_now)} extraction"
            f"{'s' if len(xs_now) != 1 else ''}"
        ),
        detail=resp.model or model_name,
        step_detail=(
            f"{len(xs_now)} extraction{'s' if len(xs_now) != 1 else ''}"
            + (
                f" · {n_open} question{'s' if n_open != 1 else ''}"
                if n_open
                else ""
            )
        ),
        row_fields={
            "questions": questions,
            "merge_plan": None,
            "analysis": analysis_info,
        },
    )
    self._make_crops(iid)
    r_final = self.store.get("design_references", iid) or {}
    self._set(
        iid,
        phase="review",
        error=None,
        progress=r_final.get("progress"),
    )
    self.svc.touch(pid)

  def _titles_to_ids(self, iid: str, titles: list[str]) -> list[str]:
    by = {x["title"].lower(): x["id"] for x in self._xrows(iid)}
    return [by[t.lower()] for t in titles if str(t).lower() in by]

  def _store_analysis(self, r, data, prev, project) -> None:
    iid = r["id"]
    now = util.now_iso()
    images = r.get("images") or []
    by_id = {x["id"]: x for x in prev}
    node_to_img = {
        im.get("frame_id"): i
        for i, im in enumerate(images)
        if im.get("frame_id")
    }
    seen: set[str] = set()
    pending_uses: list[tuple[str, list[str]]] = []
    for order, it in enumerate(data.get("extractions") or []):
      kind = it.get("kind") if it.get("kind") in KINDS else "component"
      title = str(it.get("title") or "").strip()[:120] or kind.replace("_", " ")
      img_n = it.get("image") or 0
      node_id = str(it.get("node_id") or "").strip() or None
      idx = (
          img_n - 1
          if isinstance(img_n, int) and 0 < img_n <= len(images)
          else None
      )
      if idx is None and node_id in node_to_img:
        idx = node_to_img[node_id]
      if node_id is None and idx is not None:
        node_id = images[idx].get("frame_id")
      instances = []
      for ins in it.get("instances") or []:
        n = ins.get("image") or 0
        if isinstance(n, int) and 0 < n <= len(images):
          instances.append(
              {"image_index": n - 1, "box": _box(ins.get("box_2d"))}
          )
      region = {
          "image_index": idx,
          "box": _box(it.get("box_2d")) if idx is not None else None,
          "node_id": node_id,
          "instances": instances[:12],
      }
      try:
        conf = max(0.0, min(1.0, float(it.get("confidence"))))
      except (TypeError, ValueError):
        conf = 0.5
      q_text = str(it.get("question") or "").strip()
      question = None
      if q_text or conf < ASK_BELOW:
        question = {
            "text": (
                q_text
                or f"Is “{title}” a reusable component, a template, or token reference material?"
            ),
            "options": (
                _options(it.get("question_options")) or _default_options()
            ),
        }
      comp = it.get("component") or {}
      try:
        props = json.loads(comp.get("props_schema_json") or "{}")
        if not isinstance(props, dict):
          props = {}
      except ValueError:
        props = {}
      match = None
      if comp.get("matches_existing"):
        rows = self.store.query(
            "SELECT id FROM custom_components WHERE project_id=? AND name=?",
            [r["project_id"], str(comp["matches_existing"])],
        )
        match = rows[0]["id"] if rows else None
      tpl = it.get("template") or {}
      raw_x_tokens = [
          t for t in it.get("tokens") or [] if t.get("token") and t.get("value")
      ][:40]
      if kind == "token_reference":
        proj_settings = settings_mod.merged((project or {}).get("settings"))
        raw_x_tokens = settings_mod.expand_complementary_color_tokens(
            raw_x_tokens,
            facts=r.get("facts") if isinstance(r, dict) else None,
            current_theme=proj_settings.get("theme"),
            include_facts_when_no_color_tokens=True,
        )[:60]
      payload = {
          "component": {
              "purpose": str(comp.get("purpose") or "")[:600],
              "maps_to_basic": [
                  m
                  for m in comp.get("maps_to_basic") or []
                  if m in catalog_mod.basic_names()
              ],
              "props_schema": props,
              "matches_component_id": match,
          },
          "template": {
              "when_to_use": str(tpl.get("when_to_use") or "")[:1000],
              "structure_md": str(tpl.get("structure_md") or "")[:3000],
              "slots": [str(x)[:60] for x in tpl.get("slots") or []][:16],
          },
          "tokens": raw_x_tokens,
      }
      fields = {
          "kind": kind,
          "title": title,
          "description": str(it.get("description") or "")[:1000],
          "region": region,
          "confidence": conf,
          "rationale": str(it.get("rationale") or "")[:600],
          "question": question,
          "payload": payload,
          "order_idx": order,
          "updated_at": now,
      }
      old = by_id.get(str(it.get("id") or ""))
      if old is None:
        # Models sometimes drop ids; match an unclaimed extraction by title.
        old = next(
            (
                x
                for x in prev
                if x["id"] not in seen and x["title"].lower() == title.lower()
            ),
            None,
        )
      if old and old["id"] in seen:
        old = None
      if old:
        seen.add(old["id"])
        if old["status"] in ("confirmed", "ignored", "merged"):
          # Locked by the designer: refresh details, keep kind/region/status.
          keep = {
              "description": fields["description"] or old.get("description"),
              "payload": (
                  payload if old["status"] != "merged" else old.get("payload")
              ),
              "rationale": fields["rationale"] or old.get("rationale"),
              "order_idx": order,
              "updated_at": now,
          }
          self.store.update("import_extractions", old["id"], keep)
        else:
          fields["status"] = "needs_answer" if question else "proposed"
          self.store.update("import_extractions", old["id"], fields)
        xid = old["id"]
      else:
        xid = util.new_id("x")
        self.store.insert(
            "import_extractions",
            dict(
                fields,
                id=xid,
                import_id=iid,
                project_id=r["project_id"],
                status="needs_answer" if question else "proposed",
                crop_file=None,
                uses=[],
                answer=None,
                merged_into=[],
                created_at=now,
            ),
        )
      seen.add(xid)
      pending_uses.append((xid, [str(u) for u in it.get("uses") or []]))
    # Drop unreviewed extractions the new analysis no longer reports.
    for x in prev:
      if x["id"] not in seen and x["status"] in ("proposed", "needs_answer"):
        self.store.delete("import_extractions", x["id"])
    self._resolve_uses(iid, project, pending_uses)

  def _resolve_uses(self, iid, project, pending) -> None:
    xs = self._xrows(iid)
    by_title = {x["title"].lower(): x for x in xs}
    basic = set(catalog_mod.basic_names())
    customs = {
        c["name"]: c["id"]
        for c in self.store.query(
            "SELECT id, name FROM custom_components WHERE project_id=? AND"
            " status!='archived'",
            [project["id"]],
        )
    }
    for xid, names in pending:
      uses, seen = [], set()
      for n in names:
        key = n.strip()
        if not key or key.lower() in seen:
          continue
        seen.add(key.lower())
        x = by_title.get(key.lower())
        if x and x["id"] != xid:
          uses.append({"xid": x["id"], "name": x["title"]})
        elif key in customs:
          uses.append({"component_id": customs[key], "name": key})
        elif key in basic:
          uses.append({"basic": key, "name": key})
        else:
          uses.append({"name": key[:80]})
      self.store.update("import_extractions", xid, {"uses": uses[:20]})

  # ------------------------------------------------------------ crops
  def _make_crops(self, iid: str) -> None:
    r = self.store.get("design_references", iid)
    images = r.get("images") or []
    xs = self._xrows(iid)
    box_xs = [
        x
        for x in xs
        if (x.get("region") or {}).get("box")
        and (x.get("region") or {}).get("image_index") is not None
        and 0 <= (x.get("region") or {}).get("image_index") < len(images)
        and images[(x.get("region") or {}).get("image_index")].get("file")
    ]
    total = len(box_xs) if self.pool else 0
    if total:
      self._progress(
          iid,
          "crop_regions",
          status="running",
          label=(
              f"Cropping {total} extracted UI region"
              f"{'s' if total != 1 else ''}…"
          ),
          detail=f"“{box_xs[0].get('title') or box_xs[0]['id']}”",
          step_detail=f"0/{total} crops",
          done=0,
          total=total,
      )
    done_boxes = 0
    for x in xs:
      reg = x.get("region") or {}
      is_box = bool(reg.get("box")) and total > 0
      if is_box:
        self._progress(
            iid,
            "crop_regions",
            status="running",
            label=f"Cropping extracted UI region {done_boxes + 1} of {total}…",
            detail=f"“{x.get('title') or x['id']}”",
            step_detail=f"{done_boxes}/{total} crops",
            done=done_boxes,
            total=total,
        )
      self._crop_one(r, images, x)
      if is_box:
        done_boxes += 1
        self._progress(
            iid,
            "crop_regions",
            status="running",
            label=f"Cropped region {done_boxes} of {total}…",
            detail=f"“{x.get('title') or x['id']}”",
            step_detail=f"{done_boxes}/{total} crops",
            done=done_boxes,
            total=total,
        )
    self._progress(
        iid,
        "crop_regions",
        status="done",
        label="Analysis complete",
        detail=(
            f"{total} region crop{'s' if total != 1 else ''} ready"
            if total
            else "Extractions ready for review"
        ),
        step_detail=(
            f"{total} region crop{'s' if total != 1 else ''}"
            if total
            else "Whole-frame images"
        ),
        done=total if total else None,
        total=total if total else None,
    )

  def _crop_one(self, r, images, x) -> None:
    reg = x.get("region") or {}
    idx = reg.get("image_index")
    if idx is None or not 0 <= idx < len(images):
      return
    im = images[idx]
    if not im.get("file"):
      return
    box = reg.get("box")
    if not box:
      # Whole image: reuse the image file itself.
      if x.get("crop_file") != im["file"]:
        self.store.update(
            "import_extractions", x["id"], {"crop_file": im["file"]}
        )
      return
    fn = f"{r['id']}_{x['id']}_crop.png"
    if self._crop(im, box, self.store.file_path("references", fn)):
      self.store.update(
          "import_extractions",
          x["id"],
          {"crop_file": fn, "updated_at": util.now_iso()},
      )

  def _crop(self, im: dict[str, Any], box: list[int], out: str) -> bool:
    """Crops an image region with headless Chrome (stdlib-only build)."""
    if not self.pool:
      return False
    src = self.store.file_path("references", im["file"])
    w0, h0 = im.get("width") or 0, im.get("height") or 0
    try:
      from refs.references import png_size  # pylint: disable=g-import-not-at-top

      with open(src, "rb") as fh:
        nw, nh = png_size(fh.read())
    except OSError:
      return False
    nw, nh = nw or w0, nh or h0
    if not nw or not nh:
      return False
    ds = 2 if nw >= 800 else 1
    iw, ih = nw / ds, nh / ds
    x0, y0, x1, y1 = (v / 1000 for v in box)
    cw = max(8, round((x1 - x0) * iw))
    ch = max(8, round((y1 - y0) * ih))
    page = (
        "<!doctype html><html><head><style>html,body{margin:0;background:#fff}"
        f"#c{{width:{cw}px;height:{ch}px;overflow:hidden;position:relative}}"
        f"#c img{{position:absolute;left:{-x0 * iw:.1f}px;top:{-y0 * ih:.1f}px;"
        f"width:{iw:.1f}px;height:{ih:.1f}px}}</style></head><body>"
        f'<div id="c"><img src="file://{src}"></div><script>'
        "function done(){window.__a2uiReady={height:%d};}" % ch
        + "var i=document.querySelector('img');"
        "if(i.complete)done();else{i.onload=done;i.onerror=done;}"
        "</script></body></html>"
    )
    tmp = out + ".html"
    try:
      with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(page)
      self.pool.screenshot(
          "file://" + tmp, cw, ch, out, scale=float(ds), timeout=20
      )
      return os.path.exists(out)
    except Exception:  # pylint: disable=broad-except
      traceback.print_exc()
      return False
    finally:
      try:
        os.remove(tmp)
      except OSError:
        pass

  # ------------------------------------------------------------ review
  def _editable(self, r) -> None:
    ph = self.phase_of(r)
    if ph in LIVE_PHASES:
      raise ApiError(409, f"Import is {ph}; wait for it to finish")
    if ph == "legacy":
      raise ApiError(
          409, "Legacy imports were analyzed into chat; re-analyze first"
      )

  def _invalidate_plan(self, iid: str) -> None:
    r = self.store.get("design_references", iid)
    if r.get("merge_plan") or self.phase_of(r) not in ("review", "merged"):
      self._set(iid, merge_plan=None, phase="review")

  def patch_extraction(self, user, iid, xid, body) -> dict[str, Any]:
    r = self._row(user, iid, write=True)
    self._editable(r)
    x = self._x(iid, xid)
    if x["status"] == "merged":
      raise ApiError(409, "This extraction was already merged")
    upd: dict[str, Any] = {}
    if "title" in body:
      upd["title"] = str(body["title"] or "").strip()[:120] or x["title"]
    if "description" in body:
      upd["description"] = str(body["description"] or "")[:1000]
    if body.get("kind") is not None:
      if body["kind"] not in KINDS:
        raise ApiError(400, f"kind must be one of {', '.join(KINDS)}")
      upd["kind"] = body["kind"]
      upd["status"] = "ignored" if body["kind"] == "ignore" else "confirmed"
    ans = body.get("answer")
    if isinstance(ans, dict) and ans.get("choice"):
      choice = str(ans["choice"])[:60]
      upd["answer"] = {
          "choice": choice,
          "note": str(ans.get("note") or "")[:1000],
          "by": user["ldap"],
          "at": util.now_iso(),
      }
      if choice in KINDS:
        upd["kind"] = choice
        upd["status"] = "ignored" if choice == "ignore" else "confirmed"
      else:
        upd["status"] = "confirmed"
    if body.get("status") is not None:
      st = body["status"]
      if st not in ("proposed", "confirmed", "ignored"):
        raise ApiError(400, "status must be proposed, confirmed or ignored")
      if st == "proposed" and x.get("question") and not x.get("answer"):
        st = "needs_answer"
      upd["status"] = st
    if isinstance(body.get("region"), dict):
      reg = dict(x.get("region") or {})
      rb = body["region"]
      if "image_index" in rb:
        n = len(r.get("images") or [])
        ii = rb.get("image_index")
        if ii is not None and not (isinstance(ii, int) and 0 <= ii < n):
          raise ApiError(400, "image_index out of range")
        reg["image_index"] = ii
      if "box" in rb:
        reg["box"] = _clean_box(rb["box"])
      upd["region"] = reg
    if upd:
      upd["updated_at"] = util.now_iso()
      self.store.update("import_extractions", xid, upd)
      if "region" in upd:
        self._crop_one(
            r, r.get("images") or [], self.store.get("import_extractions", xid)
        )
      self._invalidate_plan(iid)
    return self.get(user, iid)

  def add_extraction(self, user, iid, body) -> dict[str, Any]:
    r = self._row(user, iid, write=True)
    self._editable(r)
    kind = body.get("kind") or "component"
    if kind not in KINDS:
      raise ApiError(400, f"kind must be one of {', '.join(KINDS)}")
    rb = body.get("region") or {}
    ii = rb.get("image_index")
    images = r.get("images") or []
    if not (isinstance(ii, int) and 0 <= ii < len(images)):
      raise ApiError(400, "Pick the image the region is on")
    now = util.now_iso()
    xid = util.new_id("x")
    n = len(self._xrows(iid))
    x = self.store.insert(
        "import_extractions",
        {
            "id": xid,
            "import_id": iid,
            "project_id": r["project_id"],
            "kind": kind,
            "status": "ignored" if kind == "ignore" else "confirmed",
            "title": (
                str(body.get("title") or "").strip()[:120] or f"Region {n + 1}"
            ),
            "description": str(body.get("description") or "")[:1000],
            "region": {
                "image_index": ii,
                "box": _clean_box(rb.get("box")),
                "node_id": images[ii].get("frame_id"),
                "instances": [],
            },
            "crop_file": None,
            "confidence": 1.0,
            "rationale": "Added by the designer.",
            "uses": [],
            "question": None,
            "answer": {
                "choice": kind,
                "note": "added by hand",
                "by": user["ldap"],
                "at": now,
            },
            "payload": {"component": {}, "template": {}, "tokens": []},
            "merged_into": [],
            "order_idx": n,
            "created_at": now,
            "updated_at": now,
        },
    )
    self._crop_one(r, images, x)
    self._invalidate_plan(iid)
    return self.get(user, iid)

  def delete_extraction(self, user, iid, xid) -> dict[str, Any]:
    r = self._row(user, iid, write=True)
    self._editable(r)
    x = self._x(iid, xid)
    if x["status"] == "merged":
      raise ApiError(409, "This extraction was already merged")
    self.store.delete("import_extractions", xid)
    self._invalidate_plan(iid)
    return self.get(user, iid)

  def merge_extractions(self, user, iid, body) -> dict[str, Any]:
    r = self._row(user, iid, write=True)
    self._editable(r)
    keep = self._x(iid, str(body.get("keep") or ""))
    ids = [i for i in body.get("ids") or [] if i != keep["id"]]
    if not ids:
      raise ApiError(400, "Pick at least two extractions to merge")
    reg = dict(keep.get("region") or {})
    inst = list(reg.get("instances") or [])
    for i in ids:
      x = self._x(iid, i)
      if x["status"] == "merged":
        raise ApiError(409, f"“{x['title']}” was already merged")
      xr = x.get("region") or {}
      if xr.get("image_index") is not None:
        inst.append({"image_index": xr["image_index"], "box": xr.get("box")})
      inst.extend(xr.get("instances") or [])
      # Templates that used the duplicate now use the kept extraction.
      for other in self._xrows(iid):
        uses = other.get("uses") or []
        if any(u.get("xid") == i for u in uses):
          new = []
          for u in uses:
            if u.get("xid") == i:
              u = {"xid": keep["id"], "name": keep["title"]}
            if u not in new:
              new.append(u)
          self.store.update("import_extractions", other["id"], {"uses": new})
      self.store.delete("import_extractions", i)
    reg["instances"] = inst[:24]
    self.store.update(
        "import_extractions",
        keep["id"],
        {"region": reg, "updated_at": util.now_iso()},
    )
    self._invalidate_plan(iid)
    return self.get(user, iid)

  def confirm(self, user, iid, body) -> dict[str, Any]:
    r = self._row(user, iid, write=True)
    self._editable(r)
    ids = body.get("ids")
    now = util.now_iso()
    for x in self._xrows(iid):
      if ids is not None and x["id"] not in ids:
        continue
      if x["status"] == "proposed" or (
          ids is not None and x["status"] == "needs_answer"
      ):
        self.store.update(
            "import_extractions",
            x["id"],
            {
                "status": "ignored" if x["kind"] == "ignore" else "confirmed",
                "updated_at": now,
            },
        )
    self._invalidate_plan(iid)
    return self.get(user, iid)

  def answer_question(self, user, iid, qid, body) -> dict[str, Any]:
    r = self._row(user, iid, write=True)
    self._editable(r)
    ans = body.get("answer") or {}
    if not ans.get("choice"):
      raise ApiError(400, "Pick an answer")
    qs = [dict(q) for q in r.get("questions") or []]
    q = next((q for q in qs if q["id"] == qid), None)
    if not q:
      raise ApiError(404, "Question not found")
    q["answer"] = {
        "choice": str(ans["choice"])[:60],
        "note": str(ans.get("note") or "")[:1000],
        "by": user["ldap"],
        "at": util.now_iso(),
    }
    self._set(iid, questions=qs)
    self._invalidate_plan(iid)
    return self.get(user, iid)

  # ------------------------------------------------------------ plan
  def prepare_merge(self, user, iid, body) -> dict[str, Any]:
    r = self._row(user, iid, write=True)
    self._editable(r)
    xs = self._xrows(iid)
    open_x = [x["title"] for x in xs if x["status"] == "needs_answer"]
    open_q = [
        q["text"] for q in r.get("questions") or [] if not q.get("answer")
    ]
    if open_x or open_q:
      raise ApiError(
          409,
          f"Answer the open questions first ({len(open_x) + len(open_q)} left)",
      )
    has_pd_samples = bool(
        ((r.get("analysis") or {}).get("project_data") or {}).get("samples")
    )
    if not has_pd_samples and not any(
        x["status"] in ("confirmed", "proposed", "merged")
        and x["kind"] != "ignore"
        for x in xs
    ):
      raise ApiError(
          400, "Nothing to merge: every extraction is ignored or merged"
      )
    # Proposed items are confirmed implicitly by preparing the merge.
    for x in xs:
      if x["status"] == "proposed":
        self.store.update(
            "import_extractions",
            x["id"],
            {"status": "ignored" if x["kind"] == "ignore" else "confirmed"},
        )
    prog = refs_mod.make_progress(r["kind"], "plan")
    self._set(iid, phase="planning", error=None, progress=prog)
    instr = str(body.get("instructions") or "")[:2000]

    def job():
      try:
        self.run_plan(iid, instr)
      except Exception as e:  # pylint: disable=broad-except
        traceback.print_exc()
        r_err = self.store.get("design_references", iid) or {}
        cur_step = (r_err.get("progress") or {}).get("step") or "gemini_plan"
        p_err = refs_mod.step_progress(
            r_err.get("progress"),
            cur_step,
            status="error",
            label="Merge planning failed",
            detail=str(e)[:200],
        )
        self._set(
            iid,
            phase="review",
            error=f"Merge planning failed: {e}"[:600],
            progress=p_err,
        )

    self.executor.submit(job)
    return self.get(user, iid)

  def run_plan(self, iid: str, instructions: str = "") -> None:
    r = self.store.get("design_references", iid)
    pid = r["project_id"]
    _, s, ctx, _ = self._project_ctx(pid)
    cons = (self.svc.current_revision(pid) or {}).get("text_md") or ""
    xs = [
        x
        for x in self._xrows(iid)
        if x["status"] in ("confirmed", "merged") and x["kind"] != "ignore"
    ]
    model_name = s["generator"].get("model") or "gemini-3.8-flash"
    self._progress(
        iid,
        "gemini_plan",
        status="running",
        label=f"Drafting merge plan with {model_name}…",
        detail=(
            f"{len(xs)} confirmed extraction{'s' if len(xs) != 1 else ''}"
            " · tokens, components, templates, constitution & project data"
        ),
        step_detail=f"{model_name} · {len(xs)} confirmed",
        extra={"model": model_name},
    )
    ctx.append(
        "# Confirmed extractions\n\n```json\n"
        + common.clip_json(
            [
                {
                    "id": x["id"],
                    "kind": x["kind"],
                    "title": x["title"],
                    "description": x.get("description"),
                    "uses": [u.get("name") for u in x.get("uses") or []],
                    "answer_note": (x.get("answer") or {}).get("note") or "",
                    "payload": (
                        [
                            t
                            for t in (x.get("payload") or {}).get(
                                "tokens"
                            ) or []
                            if isinstance(t, dict)
                            and not t.get("complementary")
                        ]
                        if x["kind"] == "token_reference"
                        else (x.get("payload") or {}).get(x["kind"])
                    ),
                    "tokens": [
                        t
                        for t in (x.get("payload") or {}).get("tokens") or []
                        if isinstance(t, dict) and not t.get("complementary")
                    ],
                    "figma_node": (x.get("region") or {}).get("node_id"),
                }
                for x in xs
            ],
            20000,
        )
        + "\n```"
    )
    pd = ((r.get("analysis") or {}).get("project_data")) or {}
    if pd.get("samples"):
      ctx.append(
          "# Extracted project data samples from analysis\n\n```json\n"
          + common.clip_json(pd, 12000)
          + "\n```"
      )
    answered = [q for q in r.get("questions") or [] if q.get("answer")]
    if answered:
      ctx.append(
          "# Designer's answers\n\n"
          + "\n".join(
              f"- {q['text']} → {q['answer'].get('choice')}" for q in answered
          )
      )
    if r.get("facts"):
      ctx.append(
          "# Exact facts from the source\n\n```json\n"
          + common.clip_json(
              {k: v for k, v in r["facts"].items() if k != "figma_components"},
              8000,
          )
          + "\n```"
      )
    if instructions:
      ctx.append("# Designer's instructions\n\n" + instructions)
    parts: list[Any] = ["\n\n".join(ctx)]
    for x in xs[:MAX_PLAN_IMAGES]:
      if x.get("crop_file"):
        data = self._image_bytes({"file": x["crop_file"]})
        if data:
          parts.extend(
              [f"{x['id']} — {x['kind']} “{x['title']}”", gemini.Image(*data)]
          )
    try:
      resp = self.svc.llm.generate(
          purpose="plan_import_merge",
          system=common.prompt("plan_import_merge.md"),
          parts=parts,
          json_schema=PLAN_SCHEMA,
          temperature=0.2,
          model=s["generator"].get("model"),
          timeout_s=300,
      )
    except gemini.LlmError as e:
      raise RuntimeError(f"the model is unavailable ({e})") from e
    self._progress(
        iid,
        "build_diff",
        status="running",
        label="Computing token changes & constitution diff…",
        detail=resp.model or model_name,
    )
    plan = self._plan(resp.data or {}, xs, s, cons, pid, r=r)
    r_cur = self.store.get("design_references", iid) or {}
    prog_done = refs_mod.step_progress(
        r_cur.get("progress"),
        "build_diff",
        status="done",
        label="Merge plan ready",
        detail=(
            f"{len(plan['lines'])} line{'s' if len(plan['lines']) != 1 else ''}"
        ),
        step_detail=(
            f"{len(plan['lines'])} line{'s' if len(plan['lines']) != 1 else ''}"
        ),
    )
    self._set(
        iid,
        phase="plan_ready",
        merge_plan=plan,
        error=None,
        progress=prog_done,
    )

  def _image_node(self, im: dict[str, Any] | None) -> dict[str, Any] | None:
    """The Figma frame subtree stored with an import image, if any.

    Figma ingest stores `figma.trim_node_tree(frame)` as `images[i].nodes_file`
    (`refs/references.py`); older imports have none and return None.
    """
    if not isinstance(im, dict):
      return None
    if isinstance(im.get("raw_node"), dict):
      return im["raw_node"]
    fn = im.get("nodes_file")
    if not fn:
      return None
    try:
      with open(
          self.store.file_path("references", fn), "r", encoding="utf-8"
      ) as fh:
        node = json.load(fh)
    except (OSError, ValueError):
      return None
    return node if isinstance(node, dict) else None

  def _plan_basic_fit(
      self, r: dict[str, Any], x: dict[str, Any]
  ) -> dict[str, Any] | None:
    """Plan-time *Basic fit* of a component extraction, when its structure is known.

    Figma: the extraction's node (by `node_id`, or the frame child that best
    matches its box) is compiled with
    `figma_compiler.compile_node_to_basic_skeleton`.
    Stitch: the screen HTML region is compiled via
    `stitch.extract_stitch_dom_region`.
    Screenshots have no structure; their fit comes from the Gemini draft at
    build time.
    """
    try:
      reg = x.get("region") or {}
      imgs = r.get("images") or []
      if reg.get("image_index") is not None:
        idx = int(reg["image_index"])
      else:
        idx = int(reg.get("image") or 1) - 1
      im = imgs[idx] if 0 <= idx < len(imgs) else (imgs[0] if imgs else {})
      node = None
      if r.get("kind") == "figma":
        nid = reg.get("node_id")
        if nid:
          for cand in imgs:
            raw = self._image_node(cand)
            if isinstance(raw, dict):
              node = _find_figma_node(raw, str(nid))
              if node:
                break
        if node is None and reg.get("box"):
          frame = self._image_node(im)
          pct = _box_pct(reg["box"])
          if isinstance(frame, dict) and pct:
            hit = figma.resolve_box_node(frame, pct)
            node = (hit or {}).get("node") if isinstance(hit, dict) else None
        if not isinstance(node, dict):
          return None
        fit = figma_compiler.compile_node_to_basic_skeleton(node).get("fit")
        source = "figma"
      elif im.get("html_file"):
        with open(
            self.store.file_path("references", im["html_file"]),
            "r",
            encoding="utf-8",
        ) as fh:
          html_text = fh.read()
        dom = stitch.extract_stitch_dom_region(
            html_text, hint=x.get("description") or x.get("title") or ""
        )
        fit = (dom.get("basic") or {}).get("fit")
        source = "stitch"
      else:
        return None
    except Exception:  # pylint: disable=broad-except
      traceback.print_exc()
      return None
    if not isinstance(fit, dict) or fit.get("score") is None:
      return None
    return {
        "score": fit["score"],
        "source": source,
        "nodes": fit.get("nodes"),
        "faithful": fit.get("faithful"),
        "partial": fit.get("partial"),
        "lossy": fit.get("lossy"),
        "summary": list(fit.get("summary") or [])[:6],
        "reasons": list(fit.get("reasons") or [])[:12],
    }

  def _plan(self, data, xs, s, cons, pid, r=None) -> dict[str, Any]:
    lines: list[dict[str, Any]] = []
    xby = {x["id"]: x for x in xs}
    v_crop = lambda x: self._file_url("references", x.get("crop_file"))  # pylint: disable=unnecessary-lambda-assignment

    def add(type_, label, from_, detail, selected=True):
      lines.append({
          "id": f"l{len(lines) + 1}",
          "type": type_,
          "selected": selected,
          "label": label,
          "from": [f for f in from_ if f in xby],
          "detail": detail,
      })

    # Tokens: from the plan, plus any extraction token payloads across all
    # confirmed extractions (token_reference, component, template).
    raw_tokens = [
        t
        for t in data.get("tokens") or []
        if isinstance(t, dict) and t.get("token") and not t.get("complementary")
    ]
    for x in xs:
      for t in (x.get("payload") or {}).get("tokens") or []:
        if (
            isinstance(t, dict)
            and t.get("token")
            and not t.get("complementary")
        ):
          raw_tokens.append(
              dict(t, sources=list(t.get("sources") or [x["id"]]))
          )
    token_x = [x["id"] for x in xs if x["kind"] == "token_reference"]
    fallback_sources = token_x or [x["id"] for x in xs[:1]]

    # Deterministic backfill of typography tokens from source facts (Figma
    # text_styles or Stitch design_system / Tailwind fontFamily) when omitted.
    planned_token_names = {str(t.get("token") or "") for t in raw_tokens}
    facts = (r or {}).get("facts") if isinstance(r, dict) else {}
    if not isinstance(facts, dict):
      facts = {}
    ds_facts = facts.get("design_system") or {}
    ds_fonts = ds_facts.get("fonts") if isinstance(ds_facts, dict) else {}
    if not isinstance(ds_fonts, dict):
      ds_fonts = {}
    text_styles = facts.get("text_styles") or []

    def _infer_typography_backfill() -> list[dict[str, Any]]:
      inferred: list[dict[str, Any]] = []
      body_fam = ds_fonts.get("body")
      head_fam = ds_fonts.get("headline")
      mono_fam = ds_fonts.get("mono")
      head_weights: list[int] = []
      body_weights: list[int] = []
      for ts in text_styles:
        if not isinstance(ts, dict):
          continue
        fam = str(ts.get("family") or "").strip()
        role = str(ts.get("role") or ts.get("name") or "").lower()
        size = ts.get("size")
        weight = ts.get("weight")
        if fam:
          if not mono_fam and (
              "mono" in role
              or "code" in role
              or "mono" in fam.lower()
              or "code" in fam.lower()
          ):
            mono_fam = fam
          elif not head_fam and (
              any(
                  k in role for k in ("display", "heading", "headline", "serif")
              )
              or (isinstance(size, (int, float)) and size >= 20)
          ):
            head_fam = fam
          elif not body_fam and (
              any(k in role for k in ("sans", "body", "default"))
              or (isinstance(size, (int, float)) and 13 <= size <= 18)
          ):
            body_fam = fam
        if isinstance(weight, (int, float)) and 100 <= weight <= 900:
          if isinstance(size, (int, float)) and size >= 20:
            head_weights.append(int(weight))
          elif isinstance(size, (int, float)) and 13 <= size <= 18:
            body_weights.append(int(weight))
      if not body_fam and text_styles:
        first_fam = str((text_styles[0] or {}).get("family") or "").strip()
        if first_fam:
          body_fam = first_fam
      if body_fam and "font_family" not in planned_token_names:
        inferred.append({
            "token": "font_family",
            "value": body_fam,
            "confidence": "exact",
            "reason": (
                "Extracted primary body typeface from import source facts"
            ),
            "sources": fallback_sources,
        })
      if head_fam and "font_family_heading" not in planned_token_names:
        inferred.append({
            "token": "font_family_heading",
            "value": head_fam,
            "confidence": "exact",
            "reason": (
                "Extracted heading/display typeface from import source facts"
            ),
            "sources": fallback_sources,
        })
      if mono_fam and "font_family_mono" not in planned_token_names:
        inferred.append({
            "token": "font_family_mono",
            "value": mono_fam,
            "confidence": "exact",
            "reason": "Extracted monospace typeface from import source facts",
            "sources": fallback_sources,
        })
      if head_weights and "heading_weight" not in planned_token_names:
        most_common_hw = max(set(head_weights), key=head_weights.count)
        inferred.append({
            "token": "heading_weight",
            "value": str(most_common_hw),
            "confidence": "exact",
            "reason": "Extracted heading font weight from source text styles",
            "sources": fallback_sources,
        })
      if body_weights and "body_weight" not in planned_token_names:
        most_common_bw = max(set(body_weights), key=body_weights.count)
        inferred.append({
            "token": "body_weight",
            "value": str(most_common_bw),
            "confidence": "exact",
            "reason": "Extracted body font weight from source text styles",
            "sources": fallback_sources,
        })
      return inferred

    if fallback_sources and (ds_fonts or text_styles):
      raw_tokens.extend(_infer_typography_backfill())

    # If any imported reference material (extractions, linked components,
    # templates, or constitution) reads a token that is not available in the
    # project's theme (plus planned tokens), bring it in as a new token.
    preview_theme, _ = settings_mod.apply_theme_changes(
        s["theme"], raw_tokens, None
    )
    material_texts: list[str] = []
    material_refs: list[str] = []
    material_suggestions: list[dict[str, Any]] = []
    for x in xs:
      material_texts.append(str(x.get("description") or ""))
      material_texts.append(str((x.get("answer") or {}).get("note") or ""))
      pay = x.get("payload") or {}
      material_texts.append(json.dumps(pay))
      comp_pay = pay.get("component") if isinstance(pay, dict) else {}
      if isinstance(comp_pay, dict):
        for ts in comp_pay.get("token_suggestions") or []:
          if isinstance(ts, dict):
            material_suggestions.append(dict(ts, sources=[x["id"]]))
    for t_entry in data.get("templates") or []:
      if isinstance(t_entry, dict):
        material_texts.append(str(t_entry.get("structure_md") or ""))
        material_texts.append(str(t_entry.get("description") or ""))
        material_texts.append(str(t_entry.get("when_to_use") or ""))
    cp_plan = data.get("constitution") or {}
    if isinstance(cp_plan, dict):
      material_texts.append(str(cp_plan.get("constitution_md") or ""))
      material_texts.append(str(cp_plan.get("change_summary") or ""))
    raw_tokens.extend(
        settings_mod.resolve_missing_tokens_from_material(
            preview_theme,
            token_refs=material_refs,
            texts=material_texts,
            token_suggestions=material_suggestions,
            source_facts=facts,
            sources=fallback_sources,
        )
    )
    raw_tokens = settings_mod.expand_complementary_color_tokens(
        raw_tokens,
        facts=facts,
        current_theme=s["theme"],
        sources=fallback_sources,
        include_facts_when_no_color_tokens=bool(token_x),
    )

    seen_tokens: set[str] = set()
    for t in raw_tokens:
      _, applied = settings_mod.apply_theme_changes(s["theme"], [t], None)
      for a in applied:
        if a["token"] in seen_tokens or a["token"] == "custom_css":
          continue
        seen_tokens.add(a["token"])
        if str(a.get("before")) == str(a.get("after")):
          continue
        add(
            "tokens",
            f"{a['token']}: {a.get('before')} → {a.get('after')}",
            t.get("sources") or fallback_sources,
            {
                "token": a["token"],
                "before": a.get("before"),
                "after": a.get("after"),
                "confidence": t.get("confidence") or "approx",
                "reason": t.get("reason") or "",
                "is_custom": a.get("is_custom"),
                "maps_to": t.get("maps_to") or "",
                "raw": {
                    k: t.get(k)
                    for k in (
                        "token",
                        "value",
                        "is_custom",
                        "maps_to",
                        "reason",
                    )
                },
            },
        )

    # Components: one line per confirmed component extraction.
    customs = {
        c["name"]: c["id"]
        for c in self.store.query(
            "SELECT id, name FROM custom_components WHERE project_id=? AND"
            " status!='archived'",
            [pid],
        )
    }
    basic = set(catalog_mod.basic_names())
    planned = {c.get("extraction_id"): c for c in data.get("components") or []}
    used_names = set(customs) | basic
    for x in xs:
      if x["kind"] != "component":
        continue
      c = planned.get(x["id"]) or {}
      pay = (x.get("payload") or {}).get("component") or {}
      action = c.get("action") or (
          "link_existing" if pay.get("matches_component_id") else "draft_custom"
      )
      detail: dict[str, Any] = {
          "action": action,
          "purpose": (
              c.get("purpose") or pay.get("purpose") or x.get("description")
          ),
          "crop_url": v_crop(x),
          "node_id": (x.get("region") or {}).get("node_id"),
          "props_schema": pay.get("props_schema") or {},
      }
      if action == "map_basic":
        b = c.get("basic") or next(iter(pay.get("maps_to_basic") or []), "")
        has_custom_props = bool(
            ((pay.get("props_schema") or {}).get("properties"))
        )
        is_atomic_basic = _pascal(x["title"]) in basic and not has_custom_props
        if b not in basic or not is_atomic_basic:
          action = detail["action"] = "draft_custom"
        else:
          detail["basic"] = b
          add("component", f"{x['title']} → Basic {b}", [x["id"]], detail)
          continue
      if action == "link_existing":
        cid = customs.get(c.get("existing") or "") or pay.get(
            "matches_component_id"
        )
        name = next((n for n, i in customs.items() if i == cid), "")
        if cid and name:
          detail.update(component_id=cid, name=name)
          add("component", f"{x['title']} = existing {name}", [x["id"]], detail)
          continue
        action = detail["action"] = "draft_custom"
      base = _pascal(c.get("name") or x["title"]) or "Component"
      name, i = base, 2
      while name in used_names:
        name, i = f"{base}{i}", i + 1
      used_names.add(name)
      detail["name"] = name
      fit = self._plan_basic_fit(r, x) if r else None
      if fit:
        detail["basic_fit"] = fit
      detail["strategy_decision"] = settings_mod.component_strategy_decision(
          s, basic_fit=(fit or {}).get("score")
      )
      add("component", f"Draft custom component {name}", [x["id"]], detail)

    # Templates: one line per confirmed template extraction.
    tplan = {t.get("extraction_id"): t for t in data.get("templates") or []}
    for x in xs:
      if x["kind"] != "template":
        continue
      t = tplan.get(x["id"]) or {}
      pay = (x.get("payload") or {}).get("template") or {}
      comps = t.get("components") or [
          u.get("name") for u in x.get("uses") or []
      ]
      add(
          "template",
          f"Save template “{t.get('name') or x['title']}”",
          [x["id"]],
          {
              "name": (t.get("name") or x["title"])[:120],
              "description": t.get("description") or x.get("description") or "",
              "when_to_use": (
                  t.get("when_to_use") or pay.get("when_to_use") or ""
              ),
              "structure_md": (
                  t.get("structure_md") or pay.get("structure_md") or ""
              ),
              "components": [str(c)[:80] for c in comps if c][:20],
              "crop_url": v_crop(x),
          },
      )

    # Constitution: layout guidance (from templates) as one diff line.
    cp = data.get("constitution") or {}
    new_md = (cp.get("constitution_md") or "").strip()
    if new_md and new_md != cons.strip():
      add(
          "constitution",
          "Update the constitution with layout guidance",
          [x["id"] for x in xs if x["kind"] == "template"],
          {
              "constitution_md": new_md + "\n",
              "change_summary": cp.get("change_summary") or "",
              "changed_sections": cp.get("changed_sections") or [],
              "diff": diffs.diff(cons, new_md + "\n", "current", "proposed"),
          },
      )

    # Project data samples (from plan.data or analysis.project_data or fallback).
    r_row = r or {}
    src_opts = r_row.get("source") or {}
    import_data_default = bool(
        src_opts.get(
            "import_data",
            r_row.get("kind") in ("figma", "stitch"),
        )
    )
    set_default_ds = bool(
        src_opts.get(
            "set_default_data_source",
            import_data_default and r_row.get("kind") in ("figma", "stitch"),
        )
    )
    dplan = data.get("data") if isinstance(data.get("data"), dict) else {}
    analysis_pd = ((r_row.get("analysis") or {}).get("project_data")) or {}
    import_title = (
        src_opts.get("file_name")
        or (r_row.get("facts") or {}).get("file_name")
        or r_row.get("title")
        or "Imported Project"
    ).strip()
    default_cat = str(
        analysis_pd.get("default_category") or import_title or "Imported"
    ).strip()[:80]
    default_subcat = str(
        analysis_pd.get("default_subcategory") or "Screens"
    ).strip()[:80]
    raw_samples = list(dplan.get("samples") or [])
    if not raw_samples:
      raw_samples = list(analysis_pd.get("samples") or [])
    if not raw_samples and (
        r_row.get("kind") in ("figma", "stitch") or import_data_default
    ):
      raw_samples = _fallback_project_samples(r_row, xs)

    for idx, sm in enumerate(raw_samples[:16]):
      norm = _normalize_sample_item(sm, default_cat, default_subcat, idx)
      if not norm:
        continue
      from_xids = list(norm.get("sources") or [])
      if not from_xids and norm.get("source_image"):
        img_idx = norm["source_image"] - 1
        from_xids = [
            x["id"]
            for x in xs
            if (x.get("region") or {}).get("image_index") == img_idx
        ][:4]
      add(
          "data",
          f"Import sample “{norm['title']}” ({norm['category']} /"
          f" {norm['subcategory']})",
          from_xids,
          norm,
          selected=import_data_default,
      )

    set_name = str(
        dplan.get("sample_set_name") or f"{import_title} — Project Data"
    ).strip()[:120]
    set_desc = str(
        dplan.get("sample_set_description")
        or analysis_pd.get("domain_summary")
        or f"Project-related data samples imported from {import_title}."
    ).strip()[:600]

    return {
        "prepared_at": util.now_iso(),
        "summary": data.get("summary_md") or "",
        "sample_set_name": set_name,
        "sample_set_description": set_desc,
        "set_default_data_source": set_default_ds,
        "lines": lines,
    }

  # ------------------------------------------------------------ apply
  def apply_merge(self, user, iid, body) -> dict[str, Any]:
    r = self._row(user, iid, write=True)
    if self.phase_of(r) != "plan_ready" or not r.get("merge_plan"):
      raise ApiError(409, "Prepare a merge plan first")
    pid = r["project_id"]
    plan = r["merge_plan"]
    choose = body.get("lines") or {}
    sel = [
        ln
        for ln in plan["lines"]
        if bool(choose.get(ln["id"], ln.get("selected", True)))
    ]
    if not sel:
      raise ApiError(400, "Select at least one line to apply")
    applied: list[dict[str, Any]] = []
    merged_into: dict[str, list[dict[str, Any]]] = {}

    def record(ln, target):
      applied.append({
          "line_id": ln["id"],
          "type": ln["type"],
          "label": ln["label"],
          "target": target,
      })
      for xid in ln.get("from") or []:
        merged_into.setdefault(xid, []).append(target)

    # 1. Tokens.
    tok = [ln for ln in sel if ln["type"] == "tokens"]
    if tok:
      p = self.store.get("projects", pid)
      s = settings_mod.merged(p["settings"])
      unchecked_tokens = {
          ln["detail"]["token"]
          for ln in plan["lines"]
          if ln["type"] == "tokens" and ln not in sel
      }
      raw_tok_changes = settings_mod.expand_complementary_color_tokens(
          [ln["detail"]["raw"] for ln in tok],
          facts=r.get("facts") if isinstance(r, dict) else None,
          current_theme=s["theme"],
      )
      raw_tok_changes = [
          t
          for t in raw_tok_changes
          if settings_mod.normalize_token_key(str(t.get("token") or ""))
          not in unchecked_tokens
      ]
      theme, log = settings_mod.apply_theme_changes(
          s["theme"], raw_tok_changes, None
      )
      s["theme"] = theme
      self.svc.put_settings(
          user,
          pid,
          s,
          source="import_merge",
          summary=(
              f"Imported {len(tok)} token(s) from"
              f" {r.get('title') or 'design reference'}"
          ),
      )
      del log
      for ln in tok:
        record(
            ln,
            {
                "type": "tokens",
                "id": ln["detail"]["token"],
                "label": f"{ln['detail']['token']} = {ln['detail']['after']}",
                "url": f"/p/{pid}/design?tab=tokens",
            },
        )

    # 2. Components.
    figma_token = None
    for ln in [ln for ln in sel if ln["type"] == "component"]:
      d = ln["detail"]
      xid = (ln.get("from") or [None])[0]
      x = self.store.get("import_extractions", xid) if xid else None
      if d["action"] == "map_basic":
        p = self.store.get("projects", pid)
        s = settings_mod.merged(p["settings"])
        dis = list(s["catalog"].get("disabled_components") or [])
        if d["basic"] in dis:
          dis.remove(d["basic"])
          s["catalog"] = dict(s["catalog"], disabled_components=dis)
          self.svc.put_settings(user, pid, s)
        record(
            ln,
            {
                "type": "basic",
                "id": d["basic"],
                "label": f"Basic {d['basic']}",
                "url": f"/p/{pid}/design?tab=components",
            },
        )
      elif d["action"] == "link_existing":
        record(
            ln,
            {
                "type": "component",
                "id": d["component_id"],
                "label": d["name"],
                "url": f"/p/{pid}/design/components/{d['component_id']}",
            },
        )
      else:
        node_id = d.get("node_id")
        reg = (x or {}).get("region") or {}
        if reg.get("image_index") is not None:
          img_idx = int(reg["image_index"])
        else:
          img_idx = int(reg.get("image") or 1) - 1
        imgs = r.get("images") or []
        ref_im = (
            imgs[img_idx]
            if 0 <= img_idx < len(imgs)
            else (imgs[0] if imgs else {})
        )
        frame_node = (
            self._image_node(ref_im)
            if r["kind"] == "figma" and reg.get("box")
            else None
        )
        box_pct = (
            _box_pct(reg.get("box")) if isinstance(frame_node, dict) else None
        )
        if isinstance(frame_node, dict) and box_pct:
          resolved_node = figma.resolve_box_node(frame_node, box_pct)
          if resolved_node and resolved_node.get("node_id"):
            node_id = resolved_node["node_id"]
            reg = dict(reg, box=None)
        line_strategies = (
            body.get("strategies")
            if isinstance(body.get("strategies"), dict)
            else {}
        )
        req_strategy = (
            str(
                line_strategies.get(ln["id"])
                or body.get("component_strategy")
                or d.get("strategy")
                or ""
            )
            .strip()
            .lower()
        )
        if req_strategy not in settings_mod.COMPONENT_STRATEGIES:
          req_strategy = ""
        if req_strategy == "auto":
          req_strategy = ""
        src: dict[str, Any] = {
            "kind": "import",
            "import_id": iid,
            "extraction_id": xid,
            "auto_activate": True,
            "hint": (x or {}).get("description") or d.get("purpose") or "",
            "purpose": d.get("purpose") or "",
            "file_name": (
                (r.get("source") or {}).get("file_name") or r.get("title")
            ),
        }
        if req_strategy:
          src["strategy"] = req_strategy
        if ref_im.get("html_file"):
          src["html_file"] = ref_im["html_file"]
        secrets: dict[str, Any] = {}
        is_figma_comp = r["kind"] == "figma" and node_id and not reg.get("box")
        if is_figma_comp:
          if figma_token is None:
            figma_token = self.refs.figma_token(user)[0] or ""
          if figma_token:
            src.update(
                kind="figma",
                url=(r.get("source") or {}).get("url"),
                file_key=(r.get("source") or {}).get("file_key"),
                node_id=node_id,
            )
            secrets["figma_token"] = figma_token
        ref = None
        if x and x.get("crop_file"):
          ref = self._image_bytes({"file": x["crop_file"]})
        row = self.builder.queue_import_component(
            user,
            pid,
            d["name"],
            src,
            description=d.get("purpose") or "",
            props=d.get("props_schema") or {},
            reference=ref,
            secrets=secrets,
        )
        record(
            ln,
            {
                "type": "component",
                "id": row["id"],
                "label": row["name"],
                "url": f"/p/{pid}/design/components/{row['id']}",
            },
        )

    # 3. Templates.
    for ln in [ln for ln in sel if ln["type"] == "template"]:
      xid = (ln.get("from") or [None])[0]
      x = self.store.get("import_extractions", xid) if xid else None
      t = self.create_template(
          user,
          pid,
          ln["detail"],
          source={"import_id": iid, "extraction_id": xid},
          crop_file=(x or {}).get("crop_file"),
      )
      record(
          ln,
          {
              "type": "template",
              "id": t["id"],
              "label": t["name"],
              "url": f"/p/{pid}/design?tab=templates",
          },
      )

    # 4. Constitution.
    rev = None
    applied_cons_texts: list[str] = []
    for ln in [ln for ln in sel if ln["type"] == "constitution"]:
      text = body.get("constitution_md")
      if not isinstance(text, str) or not text.strip():
        text = ln["detail"]["constitution_md"]
      applied_cons_texts.append(text)
      summary = (
          ln["detail"].get("change_summary") or "Layout guidance from import"
      )
      summary = f"{summary} (import: {r.get('title') or iid})"
      rev = self.svc.add_revision(user, pid, text, summary, "import")
      record(
          ln,
          {
              "type": "revision",
              "id": rev["id"],
              "label": f"Revision {rev['number']}",
              "url": f"/p/{pid}/constitution?rev={rev['id']}",
          },
      )

    # 4b. Bring in any tokens read by merged material (extractions, linked
    # components, templates, or constitution) that are still not available in
    # the project's theme.
    p_cur = self.store.get("projects", pid) or {}
    s_cur = settings_mod.merged(p_cur.get("settings"))
    mat_refs: list[str] = []
    mat_texts: list[str] = list(applied_cons_texts)
    mat_sugs: list[dict[str, Any]] = []
    mat_sources: list[str] = []
    for ln in sel:
      for xid in ln.get("from") or []:
        if xid and xid not in mat_sources:
          mat_sources.append(xid)
        x_row = self.store.get("import_extractions", xid) if xid else None
        if x_row:
          mat_texts.append(str(x_row.get("description") or ""))
          pay = x_row.get("payload") or {}
          mat_texts.append(json.dumps(pay))
          for tok_item in pay.get("tokens") or []:
            if isinstance(tok_item, dict):
              mat_sugs.append(dict(tok_item, sources=[xid]))
          comp_pay = pay.get("component") if isinstance(pay, dict) else {}
          if isinstance(comp_pay, dict):
            for ts in comp_pay.get("token_suggestions") or []:
              if isinstance(ts, dict):
                mat_sugs.append(dict(ts, sources=[xid]))
      if ln["type"] == "template":
        d = ln.get("detail") or {}
        mat_texts.append(str(d.get("structure_md") or ""))
        mat_texts.append(str(d.get("description") or ""))
        mat_texts.append(str(d.get("when_to_use") or ""))
      elif ln["type"] == "component":
        d = ln.get("detail") or {}
        cid = d.get("component_id")
        if cid:
          c_row = self.store.get("custom_components", cid)
          if c_row:
            mat_refs.extend(c_row.get("token_refs") or [])
            web_d = (c_row.get("definition") or {}).get("web") or {}
            mat_texts.append(str(web_d.get("css") or ""))
            mat_texts.append(str(web_d.get("template") or ""))
            for ts in c_row.get("token_suggestions") or []:
              if isinstance(ts, dict):
                mat_sugs.append(dict(ts, sources=list(ln.get("from") or [])))
    missing_toks = settings_mod.resolve_missing_tokens_from_material(
        s_cur["theme"],
        token_refs=mat_refs,
        texts=mat_texts,
        token_suggestions=mat_sugs,
        source_facts=r.get("facts") if isinstance(r.get("facts"), dict) else {},
        sources=mat_sources,
    )
    if missing_toks:
      new_theme, extra_log = settings_mod.apply_theme_changes(
          s_cur["theme"], missing_toks, None
      )
      if extra_log:
        s_cur["theme"] = new_theme
        self.svc.put_settings(
            user,
            pid,
            s_cur,
            source="import_merge",
            summary=(
                f"Brought in {len(extra_log)} missing token(s) read by"
                f" {r.get('title') or 'design reference'}"
            ),
        )
        plan_lines = list(plan.get("lines") or [])
        for mt, a in zip(missing_toks, extra_log):
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
                "label": f"{a['token']}: {a.get('before')} → {a.get('after')}",
                "from": list(mt.get("sources") or mat_sources[:1]),
                "detail": {
                    "token": a["token"],
                    "before": a.get("before"),
                    "after": a.get("after"),
                    "confidence": mt.get("confidence") or "approx",
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
          record(
              existing_ln,
              {
                  "type": "tokens",
                  "id": a["token"],
                  "label": f"{a['token']} = {a['after']}",
                  "url": f"/p/{pid}/design?tab=tokens",
              },
          )
        plan = dict(plan, lines=plan_lines)

    # 5. Project data samples & default run data source.
    data_lines = [ln for ln in sel if ln["type"] == "data"]
    sample_set = None
    default_ds_set = False
    set_default_flag = (
        bool(body["set_default_data_source"])
        if "set_default_data_source" in body
        else bool(plan.get("set_default_data_source", True))
    )
    if data_lines:
      created_sids: list[str] = []
      sample_source = (
          r["kind"] if r.get("kind") in ("figma", "stitch") else "import"
      )
      for ln in data_lines:
        d = ln["detail"] or {}
        sample = self.svc.create_sample(
            user,
            {
                "title": d.get("title") or ln.get("label") or "Imported sample",
                "category_label": d.get("category") or "Imported",
                "subcategory_label": d.get("subcategory") or "Screens",
                "intent": d.get("intent") or "",
                "size_class": d.get("size_class") or "m",
                "state": d.get("state") or "content",
                "context": d.get("context") or {},
                "payload": d.get("payload") or {},
                "edge_cases": d.get("edge_cases") or [],
                "tags": d.get("tags") or [],
                "source": sample_source,
                "meta": {
                    "import_id": iid,
                    "project_id": pid,
                    "source_kind": r.get("kind") or "import",
                    "import_title": r.get("title") or "",
                    "extraction_ids": ln.get("from") or [],
                },
            },
            source=sample_source,
        )
        created_sids.append(sample["id"])
        record(
            ln,
            {
                "type": "data",
                "id": sample["id"],
                "label": sample["title"],
                "category": sample.get("group") or "",
                "subcategory": sample.get("genre") or "",
                "url": "/data",
            },
        )
      import_title = (
          (r.get("source") or {}).get("file_name")
          or r.get("title")
          or "Imported Project"
      ).strip()
      set_name = str(
          body.get("sample_set_name")
          or plan.get("sample_set_name")
          or f"{import_title} — Project Data"
      ).strip()[:120]
      set_desc = str(
          plan.get("sample_set_description")
          or f"Project-related data samples imported from {import_title}."
      ).strip()[:600]
      prev_set_id = next(
          (
              e.get("sample_set_id")
              for e in reversed(r.get("merge_log") or [])
              if e.get("sample_set_id")
              and self.store.get("sample_sets", e.get("sample_set_id"))
          ),
          None,
      )
      if prev_set_id:
        prev_st = self.store.get("sample_sets", prev_set_id) or {}
        merged_sids = list(
            dict.fromkeys((prev_st.get("sample_ids") or []) + created_sids)
        )
        sample_set = self.svc.update_set(
            user,
            prev_set_id,
            {
                "name": set_name,
                "description": set_desc,
                "sample_ids": merged_sids,
            },
        )
      else:
        sample_set = self.svc.create_set(
            user, set_name, created_sids, description=set_desc
        )
      for a in applied:
        if a["type"] == "data" and sample_set:
          a["target"]["sample_set_id"] = sample_set["id"]
          a["target"]["sample_set_name"] = sample_set["name"]
          a["target"]["url"] = f"/data?set={sample_set['id']}"
      if set_default_flag and sample_set:
        p = self.store.get("projects", pid)
        s = settings_mod.merged(p["settings"])
        s["data_source"] = {
            "kind": "set",
            "sample_set_id": sample_set["id"],
            "sample_set_name": sample_set["name"],
            "category": "",
            "subcategory": "",
            "sample_ids": list(sample_set.get("sample_ids") or []),
            "import_id": iid,
            "source_kind": r.get("kind") or "import",
        }
        self.svc.put_settings(
            user,
            pid,
            s,
            source="import_merge",
            summary=f"Set default run data source to {sample_set['name']}",
        )
        default_ds_set = True

    now = util.now_iso()
    for xid, targets in merged_into.items():
      x = self.store.get("import_extractions", xid)
      if not x:
        continue
      self.store.update(
          "import_extractions",
          xid,
          {
              "status": "merged",
              "merged_into": (x.get("merged_into") or []) + targets,
              "updated_at": now,
          },
      )
    msg = self._chat_summary(
        user, r, applied, sample_set=sample_set, default_ds_set=default_ds_set
    )
    r_latest = self.store.get("design_references", iid) or r
    latest_plan_lines = list(
        ((r_latest.get("merge_plan") or {}).get("lines")) or plan["lines"]
    )
    existing_line_ids = {ln["id"] for ln in plan["lines"]}
    for lln in latest_plan_lines:
      if lln["id"] not in existing_line_ids:
        plan["lines"].append(lln)
        existing_line_ids.add(lln["id"])
        if lln.get("applied") and lln.get("type") == "tokens":
          d_tok = lln.get("detail") or {}
          tok_id = d_tok.get("token")
          if tok_id and not any(
              (ap.get("target") or {}).get("id") == tok_id for ap in applied
          ):
            applied.append({
                "line_id": lln["id"],
                "type": "tokens",
                "label": lln["label"],
                "target": {
                    "type": "tokens",
                    "id": tok_id,
                    "label": f"{tok_id} = {d_tok.get('after')}",
                    "url": f"/p/{pid}/design?tab=tokens",
                },
            })
    log = list(r_latest.get("merge_log") or [])
    log.append({
        "at": now,
        "by": user["ldap"],
        "revision_id": rev["id"] if rev else None,
        "chat_message_id": msg["id"],
        "sample_set_id": sample_set["id"] if sample_set else None,
        "sample_set_name": sample_set["name"] if sample_set else None,
        "default_data_source_set": default_ds_set,
        "applied": applied,
    })
    lines = [
        dict(
            ln,
            applied=bool(
                ln.get("applied")
                or any(a["line_id"] == ln["id"] for a in applied)
            ),
        )
        for ln in plan["lines"]
    ]
    self._set(
        iid,
        phase="merged",
        merge_plan=dict(
            plan,
            lines=lines,
            set_default_data_source=set_default_flag,
            sample_set_id=sample_set["id"] if sample_set else None,
            sample_set_name=sample_set["name"] if sample_set else None,
            applied_at=now,
        ),
        merge_log=log,
        chat_message_id=msg["id"],
    )
    self.svc.touch(pid)
    return {
        "import": self.get(user, iid),
        "result": {
            "applied": applied,
            "revision_id": rev["id"] if rev else None,
            "chat_message_id": msg["id"],
            "sample_set": sample_set,
            "sample_set_id": sample_set["id"] if sample_set else None,
            "sample_ids": (
                list(sample_set.get("sample_ids") or []) if sample_set else []
            ),
            "default_data_source_set": default_ds_set,
        },
    }

  def set_default_data_source(
      self, user: dict[str, Any], iid: str, body: dict[str, Any]
  ) -> dict[str, Any]:
    """Sets (or updates) this import's sample set as the project's default run data source."""
    r = self._row(user, iid, write=True)
    pid = r["project_id"]
    set_id = str(body.get("sample_set_id") or "").strip()
    if not set_id:
      set_id = str(
          (r.get("merge_plan") or {}).get("sample_set_id")
          or next(
              (
                  e.get("sample_set_id")
                  for e in reversed(r.get("merge_log") or [])
                  if e.get("sample_set_id")
              ),
              "",
          )
      ).strip()
    st = self.store.get("sample_sets", set_id) if set_id else None
    if not st:
      # If the import hasn't created a sample set yet, materialize it from analysis/plan data.
      plan_lines = [
          ln
          for ln in (r.get("merge_plan") or {}).get("lines") or []
          if ln.get("type") == "data"
      ]
      raw_samples = (
          [ln.get("detail") or {} for ln in plan_lines]
          if plan_lines
          else list(
              ((r.get("analysis") or {}).get("project_data") or {}).get(
                  "samples"
              )
              or _fallback_project_samples(r, self._xrows(iid))
          )
      )
      if not raw_samples:
        raise ApiError(400, "No project data samples available in this import")
      sample_source = (
          r["kind"] if r.get("kind") in ("figma", "stitch") else "import"
      )
      sids: list[str] = []
      for idx, sm in enumerate(raw_samples[:16]):
        norm = _normalize_sample_item(sm, idx=idx)
        if not norm:
          continue
        created = self.svc.create_sample(
            user,
            {
                "title": norm["title"],
                "category_label": norm["category"],
                "subcategory_label": norm["subcategory"],
                "intent": norm["intent"],
                "size_class": norm["size_class"],
                "state": norm["state"],
                "context": norm["context"],
                "payload": norm["payload"],
                "edge_cases": norm["edge_cases"],
                "tags": norm["tags"],
                "source": sample_source,
                "meta": {
                    "import_id": iid,
                    "project_id": pid,
                    "source_kind": r.get("kind") or "import",
                    "import_title": r.get("title") or "",
                },
            },
            source=sample_source,
        )
        sids.append(created["id"])
      import_title = (
          (r.get("source") or {}).get("file_name")
          or r.get("title")
          or "Imported Project"
      ).strip()
      st = self.svc.create_set(
          user,
          f"{import_title} — Project Data"[:120],
          sids,
          description=(
              f"Project-related data samples imported from {import_title}."
          ),
      )
      set_id = st["id"]
    p = self.store.get("projects", pid)
    s = settings_mod.merged(p["settings"])
    s["data_source"] = {
        "kind": "set",
        "sample_set_id": st["id"],
        "sample_set_name": st["name"],
        "category": "",
        "subcategory": "",
        "sample_ids": list(st.get("sample_ids") or []),
        "import_id": iid,
        "source_kind": r.get("kind") or "import",
    }
    saved = self.svc.put_settings(
        user,
        pid,
        s,
        source="import_data_source",
        summary=f"Set default run data source to {st['name']}",
    )
    return {
        "import": self.get(user, iid),
        "sample_set": self.svc.fmt_set(st),
        "data_source": s["data_source"],
        "settings": saved,
    }

  def _chat_summary(
      self,
      user,
      r,
      applied,
      sample_set: dict[str, Any] | None = None,
      default_ds_set: bool = False,
  ) -> dict[str, Any]:
    by: dict[str, list[str]] = {}
    for a in applied:
      by.setdefault(a["type"], []).append(a["target"]["label"])
    bits = []
    names = {
        "tokens": "token change",
        "component": "component",
        "template": "template",
        "constitution": "constitution revision",
        "data": "data sample",
    }
    for t in ("tokens", "component", "template", "constitution", "data"):
      if by.get(t):
        n = len(by[t])
        bits.append(f"{n} {names[t]}{'s' if n != 1 else ''}")
    detail = []
    if by.get("component"):
      detail.append("Components: " + ", ".join(by["component"][:8]))
    if by.get("template"):
      detail.append("Templates: " + ", ".join(by["template"][:8]))
    if by.get("constitution"):
      detail.append("Constitution: " + ", ".join(by["constitution"]))
    if by.get("data"):
      ds_note = (
          f" (saved to sample set **{sample_set['name']}**"
          + (
              " and set as default data source for runs"
              if default_ds_set
              else ""
          )
          + ")"
          if sample_set
          else ""
      )
      detail.append("Project data: " + ", ".join(by["data"][:6]) + ds_note)
    text = (
        f"Merged import **{r.get('title') or 'Import'}**: "
        + (", ".join(bits) or "nothing")
        + " applied by "
        + user["ldap"]
        + "."
        + ("\n\n" + "\n".join(f"- {d}" for d in detail) if detail else "")
    )
    return self.store.insert(
        "chat_messages",
        {
            "id": util.new_id("m"),
            "project_id": r["project_id"],
            "role": "assistant",
            "text_md": text,
            "context": {
                "output_ids": [],
                "guideline_ids": [],
                "reference_ids": [r["id"]],
                "import_id": r["id"],
                "import_title": r.get("title") or "",
            },
            "proposal": None,
            "out_of_scope": [],
            "proposal_status": "none",
            "accepted_revision_id": None,
            "author_ldap": "assistant",
            "created_at": util.now_iso(),
        },
    )

  # ------------------------------------------------------------ templates
  def fmt_template(self, t: dict[str, Any]) -> dict[str, Any]:
    v = (t.get("updated_at") or "").replace(":", "")
    return {
        "id": t["id"],
        "project_id": t["project_id"],
        "name": t.get("name") or "",
        "description": t.get("description") or "",
        "when_to_use": t.get("when_to_use") or "",
        "structure_md": t.get("structure_md") or "",
        "components": t.get("components") or [],
        "image_url": self._file_url("templates", t.get("image_file"), v),
        "source": t.get("source") or {},
        "status": t.get("status") or "active",
        "author_ldap": t.get("author_ldap"),
        "created_at": t.get("created_at"),
        "updated_at": t.get("updated_at"),
    }

  def create_template(self, user, pid, d, source=None, crop_file=None):
    now = util.now_iso()
    tid = util.new_id("tpl")
    image_file = None
    if crop_file:
      ext = crop_file.rsplit(".", 1)[-1]
      image_file = f"{tid}.{ext}"
      try:
        os.makedirs(self.store.file_path("templates"), exist_ok=True)
        shutil.copyfile(
            self.store.file_path("references", crop_file),
            self.store.file_path("templates", image_file),
        )
      except OSError:
        image_file = None
    taken = {
        t["name"]
        for t in self.store.query(
            "SELECT name FROM templates WHERE project_id=?", [pid]
        )
    }
    name = str(d.get("name") or "Template").strip()[:120]
    base, i = name, 2
    while name in taken:
      name, i = f"{base} {i}", i + 1
    t = self.store.insert(
        "templates",
        {
            "id": tid,
            "project_id": pid,
            "name": name,
            "description": str(d.get("description") or "")[:1000],
            "when_to_use": str(d.get("when_to_use") or "")[:2000],
            "structure_md": str(d.get("structure_md") or "")[:6000],
            "components": [str(c)[:80] for c in d.get("components") or []][:30],
            "image_file": image_file,
            "source": source or {},
            "status": "active",
            "author_ldap": user["ldap"],
            "created_at": now,
            "updated_at": now,
        },
    )
    return self.fmt_template(t)

  def _template(self, user, tid, write=False):
    t = self.store.get("templates", tid)
    if not t:
      raise ApiError(404, "Template not found")
    self.svc._project_row(t["project_id"], user, write=write)  # pylint: disable=protected-access
    return t

  def list_templates(self, user, pid) -> list[dict[str, Any]]:
    self.svc._project_row(pid, user)  # pylint: disable=protected-access
    return [
        self.fmt_template(t)
        for t in self.store.query(
            "SELECT * FROM templates WHERE project_id=? ORDER BY status,"
            " created_at DESC",
            [pid],
        )
    ]

  def get_template(self, user, tid):
    return self.fmt_template(self._template(user, tid))

  def update_template(self, user, tid, body):
    self._template(user, tid, write=True)
    upd: dict[str, Any] = {}
    limits = {
        "name": 120,
        "description": 1000,
        "when_to_use": 2000,
        "structure_md": 6000,
    }
    for k, n in limits.items():
      if k in body:
        upd[k] = str(body[k] or "")[:n]
    if "name" in upd and not upd["name"].strip():
      raise ApiError(400, "Name is required")
    if isinstance(body.get("components"), list):
      upd["components"] = [str(c)[:80] for c in body["components"]][:30]
    if body.get("status") is not None:
      if body["status"] not in ("active", "archived"):
        raise ApiError(400, "status must be active or archived")
      upd["status"] = body["status"]
    if upd:
      upd["updated_at"] = util.now_iso()
      self.store.update("templates", tid, upd)
    return self.get_template(user, tid)

  def delete_template(self, user, tid) -> None:
    t = self._template(user, tid, write=True)
    if t.get("image_file"):
      try:
        os.remove(self.store.file_path("templates", t["image_file"]))
      except OSError:
        pass
    self.store.delete("templates", tid)

  def active_templates(self, pid: str) -> list[dict[str, Any]]:
    return [
        self.fmt_template(t)
        for t in self.store.query(
            "SELECT * FROM templates WHERE project_id=? AND status='active'"
            " ORDER BY name",
            [pid],
        )
    ]
