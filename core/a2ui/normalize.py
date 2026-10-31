"""Tolerant extraction + structural fix-ups of A2UI v1.0 messages.

`to_messages` accepts model output in several shapes and applies only
fix-ups that cannot change design intent:

- add a missing `"version": "v1.0"` (and upgrade a stray `"v0.9"`);
- add a `createSurface` (Basic catalogId) when the model forgot it, drop the
  removed `createSurface.theme`, and point a surface default that names the
  project catalog back at the Basic catalog (custom components carry their
  own catalogId);
- inject the per-component `catalogId` for extension/custom components;
- rewrite common v0.9 habits to v1.0 where unambiguous: `{"path": ..}` →
  `{"@path": ..}`, `{"call": .., "returnType": ..}` → `{"@call": ..}`,
  template children `{"componentId", "@path"}` → `{"componentId", "path"}`;
- rename component ids that are not UAX #31 identifiers (`user-card` →
  `user_card`) together with every reference to them.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.a2ui.catalog import BASIC_CATALOG_ID
from core.a2ui.catalog import Catalog
from core.a2ui.catalog import PROTOCOL_VERSION

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_IDENT_RE = re.compile(r"^[^\W\d]\w*$")
_REF_FIELDS = frozenset({"child", "content", "trigger"})
_DYN_TYPES = frozenset({
    "DynamicString",
    "DynamicNumber",
    "DynamicBoolean",
    "DynamicStringList",
    "DynamicValue",
    "Action",
    "CheckRule",
    "DataBinding",
    "FunctionCall",
})
_SKIP_KEYS = frozenset({"id", "component", "catalogId", "weight", "metadata"})


def parse_json_text(text: str) -> Any:
  """Parses JSON from raw model text (handles code fences / leading prose)."""
  t = (text or "").strip()
  if not t:
    return None
  try:
    return json.loads(t)
  except ValueError:
    pass
  m = _FENCE.search(t)
  if m:
    try:
      return json.loads(m.group(1))
    except ValueError:
      pass
  for open_c, close_c in (("{", "}"), ("[", "]")):
    a, b = t.find(open_c), t.rfind(close_c)
    if a != -1 and b > a:
      try:
        return json.loads(t[a : b + 1])
      except ValueError:
        continue
  return None


# ---------------------------------------------------------------- values
def upgrade_value(v: Any) -> Any:
  """Rewrites v0.9 dynamic values ({path}/{call}) to v1.0 (@path/@call).

  Applied to component property values (never to data models). Plain
  objects other than an exact v0.9 binding/call shape are recursed into.
  """
  if isinstance(v, list):
    return [upgrade_value(x) for x in v]
  if not isinstance(v, dict):
    return v
  keys = set(v)
  if keys == {"path"} and isinstance(v["path"], str):
    return {"@path": v["path"]}
  if (
      isinstance(v.get("call"), str)
      and "@call" not in v
      and keys <= {"call", "args", "returnType", "catalogId"}
  ):
    out: dict[str, Any] = {"@call": v["call"]}
    if "args" in v:
      out["args"] = _upgrade_args(v["args"])
    if "catalogId" in v:
      out["catalogId"] = v["catalogId"]
    return out
  if "@call" in v:
    out = {k: x for k, x in v.items() if k != "returnType"}
    if "args" in out:
      out["args"] = _upgrade_args(out["args"])
    return out
  if "componentId" in v and ("path" in v or "@path" in v):
    out = dict(v)
    if "@path" in out and "path" not in out:
      out["path"] = out.pop("@path")
    return out
  if "@path" in v:
    return v
  return {k: upgrade_value(x) for k, x in v.items()}


def _upgrade_args(args: Any) -> Any:
  if not isinstance(args, dict):
    return args
  return {k: upgrade_value(x) for k, x in args.items()}


def _prop_type(catalog: Catalog | None, comp_name: str, prop: str) -> str:
  if catalog is None:
    return ""
  spec = catalog.components.get(comp_name)
  if spec is None or prop not in spec.props:
    return ""
  return spec.props[prop].type


def upgrade_component(
    comp: dict[str, Any], catalog: Catalog | None = None
) -> dict[str, Any]:
  """v0.9 → v1.0 rewrites inside one component's properties."""
  if not isinstance(comp, dict):
    return comp
  name = str(comp.get("component") or "")
  spec = catalog.components.get(name) if catalog else None
  out: dict[str, Any] = {}
  for k, v in comp.items():
    if k in _SKIP_KEYS:
      out[k] = v
      continue
    t = _prop_type(catalog, name, k)
    if spec is not None and spec.custom and t in ("object", "any", "string"):
      out[k] = v  # free-form custom prop: leave literal objects alone
      continue
    out[k] = upgrade_value(v)
  if name == "Text":
    _heading_variant(out)
  return out


def _heading_variant(comp: dict[str, Any]) -> None:
  """v0.9 Text.variant h1..h5 → a Markdown heading in the text (v1.0)."""
  m = re.fullmatch(r"h([1-6])", str(comp.get("variant") or ""))
  if not m:
    return
  hashes = "#" * int(m.group(1))
  text = comp.get("text")
  if isinstance(text, str):
    if not text.lstrip().startswith("#"):
      comp["text"] = f"{hashes} {text}"
  elif isinstance(text, dict) and isinstance(text.get("@path"), str):
    comp["text"] = {
        "@call": "formatString",
        "args": {"value": f"{hashes} ${{{text['@path']}}}"},
    }
  elif (
      isinstance(text, dict)
      and text.get("@call") == "formatString"
      and isinstance((text.get("args") or {}).get("value"), str)
  ):
    val = text["args"]["value"]
    if not val.lstrip().startswith("#"):
      comp["text"] = dict(
          text, args=dict(text["args"], value=f"{hashes} {val}")
      )
  else:
    return
  comp.pop("variant", None)


# ---------------------------------------------------------------- ids
def _sanitize_id(cid: str, taken: set[str]) -> str:
  s = re.sub(r"\W+", "_", cid).strip("_") or "node"
  if s[0].isdigit():
    s = "n" + s
  base, n = s, 2
  while s in taken:
    s = f"{base}_{n}"
    n += 1
  return s


def _ref_props(catalog: Catalog | None, name: str) -> tuple[set[str], set[str]]:
  """(single-ref props, list-ref props) for a component type."""
  singles, lists = set(_REF_FIELDS), {"children"}
  if catalog is not None:
    spec = catalog.components.get(name)
    if spec is not None:
      for p in spec.props.values():
        if p.type in ("ComponentId", "Child"):
          singles.add(p.name)
        elif p.type == "ChildList" or p.type.startswith("array<ComponentId>"):
          lists.add(p.name)
  return singles, lists


def _rename_refs(
    comp: dict[str, Any], mapping: dict[str, str], catalog: Catalog | None
) -> None:
  singles, lists = _ref_props(catalog, str(comp.get("component") or ""))
  for k in list(comp):
    v = comp[k]
    if k == "id" and isinstance(v, str):
      comp[k] = mapping.get(v, v)
    elif k in singles and isinstance(v, str):
      comp[k] = mapping.get(v, v)
    elif k in lists:
      if isinstance(v, list):
        comp[k] = [mapping.get(x, x) if isinstance(x, str) else x for x in v]
      elif isinstance(v, dict) and isinstance(v.get("componentId"), str):
        comp[k] = dict(v, componentId=mapping.get(v["componentId"], v["componentId"]))
    elif k == "tabs" and isinstance(v, list):
      comp[k] = [
          dict(t, child=mapping.get(t["child"], t["child"]))
          if isinstance(t, dict) and isinstance(t.get("child"), str)
          else t
          for t in v
      ]


def _component_lists(msgs: list[dict[str, Any]]):
  for m in msgs:
    for kind in ("createSurface", "updateComponents"):
      body = m.get(kind)
      if isinstance(body, dict) and isinstance(body.get("components"), list):
        yield body


def fix_ids(
    msgs: list[dict[str, Any]], catalog: Catalog | None = None
) -> dict[str, str]:
  """Renames non-UAX#31 component ids (and references); returns the map."""
  ids: set[str] = set()
  for body in _component_lists(msgs):
    for c in body["components"]:
      if isinstance(c, dict) and isinstance(c.get("id"), str):
        ids.add(c["id"])
  mapping: dict[str, str] = {}
  taken = {i for i in ids if _IDENT_RE.match(i)}
  for i in sorted(ids):
    if not _IDENT_RE.match(i):
      new = _sanitize_id(i, taken)
      taken.add(new)
      mapping[i] = new
  if mapping:
    for body in _component_lists(msgs):
      for c in body["components"]:
        if isinstance(c, dict):
          _rename_refs(c, mapping, catalog)
  return mapping


# ---------------------------------------------------------------- catalogs
def assign_catalog_ids(
    msgs: list[dict[str, Any]], catalog: Catalog
) -> None:
  """Sets per-component catalogId for extension/custom components."""
  members = catalog.catalog_members()
  surface_default: dict[str, str | None] = {}
  for m in msgs:
    cs = m.get("createSurface")
    if isinstance(cs, dict):
      surface_default[str(cs.get("surfaceId"))] = cs.get("catalogId")
  for body in _component_lists(msgs):
    default = surface_default.get(str(body.get("surfaceId")))
    for c in body["components"]:
      if not isinstance(c, dict):
        continue
      name = c.get("component")
      if not isinstance(name, str):
        continue
      home = catalog.catalog_id_for(name)
      if not home:
        continue
      cur = c.get("catalogId")
      resolved = cur or default
      if resolved and name in members.get(resolved, set()):
        if cur is None and home != catalog.basic_catalog_id:
          c["catalogId"] = home  # explicit for extension/custom instances
        continue
      if home == default:
        c.pop("catalogId", None)
      else:
        c["catalogId"] = home


def to_messages(
    data: Any,
    surface_id: str = "main",
    catalog_id: str = BASIC_CATALOG_ID,
    catalog: Catalog | None = None,
) -> list[dict[str, Any]]:
  """Accepts {messages:[..]}, [..], or a single message; fills obvious gaps.

  `catalog_id` is the surface default used for an injected createSurface
  (always the Basic catalog when `catalog` is given). With `catalog`,
  per-component catalogIds are injected and ids/refs are fixed using the
  catalog's slot props.
  """
  if isinstance(data, dict) and isinstance(data.get("messages"), list):
    msgs = data["messages"]
  elif isinstance(data, list):
    msgs = data
  elif isinstance(data, dict) and any(
      k in data for k in ("createSurface", "updateComponents")
  ):
    msgs = [data]
  else:
    return []
  basic_id = catalog.basic_catalog_id if catalog else BASIC_CATALOG_ID
  if catalog is not None or not catalog_id:
    catalog_id = basic_id
  out = []
  for m in msgs:
    if not isinstance(m, dict):
      continue
    m = dict(m)
    ver = m.get("version")
    if ver is None or (isinstance(ver, str) and ver.startswith("v0.")):
      m["version"] = PROTOCOL_VERSION
    for kind in ("createSurface", "updateComponents"):
      body = m.get(kind)
      if isinstance(body, dict):
        body = dict(body)
        if isinstance(body.get("components"), list):
          body["components"] = [
              upgrade_component(c, catalog) if isinstance(c, dict) else c
              for c in body["components"]
          ]
        if kind == "createSurface":
          body.pop("theme", None)
          cid = body.get("catalogId")
          if not cid or (catalog is not None and cid == catalog.catalog_id):
            body["catalogId"] = catalog_id
        m[kind] = body
    out.append(m)
  has_create = any("createSurface" in m for m in out)
  if out and not has_create:
    sid = surface_id
    for m in out:
      for k in ("updateComponents", "updateDataModel"):
        if isinstance(m.get(k), dict) and m[k].get("surfaceId"):
          sid = m[k]["surfaceId"]
          break
    out.insert(
        0,
        {
            "version": PROTOCOL_VERSION,
            "createSurface": {"surfaceId": sid, "catalogId": catalog_id},
        },
    )
  fix_ids(out, catalog)
  if catalog is not None:
    assign_catalog_ids(out, catalog)
  return out
