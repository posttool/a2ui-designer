"""Deterministic lowering of composite custom components to A2UI Basic Catalog.

When a custom component defines `composition: {root, components}` (either under
the `basic_composition` strategy or as the multi-platform fallback for
`native_targets`), `expand_composite_components` rewrites each instance of that
custom component in `updateComponents` into standard A2UI Basic Catalog nodes
(`Card`, `Row`, `Column`, `Text`, `Image`, `Icon`, `Button`, etc.).

This allows custom components to render natively on Android Jetpack Compose,
Android RemoteCompose, iOS SwiftUI, and Web without requiring custom native
renderer binaries at simulation time.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from core.a2ui import catalog as catalog_mod
from core.a2ui import custom_web

_SINGLE_BIND_RE = re.compile(r"^\{\{\s*([A-Za-z_]\w*(?:\.\w+)*)\s*\}\}$")
_INLINE_BIND_RE = re.compile(r"\{\{\s*([A-Za-z_]\w*(?:\.\w+)*)\s*\}\}")
_CHILD_ID_FIELDS = frozenset({"child", "content", "trigger"})


def has_composition(defn: dict[str, Any] | None) -> bool:
  """Returns True when `defn` contains a non-empty Basic composition tree."""
  if not isinstance(defn, dict):
    return False
  comp = defn.get("composition")
  if not isinstance(comp, dict):
    return False
  return bool(
      str(comp.get("root") or "").strip()
      and isinstance(comp.get("components"), list)
      and comp.get("components")
  )


def should_lower_for_renderer(
    defn: dict[str, Any] | None, renderer: str = "web"
) -> bool:
  """Returns True if `defn` should be lowered into Basic components on `renderer`."""
  if not has_composition(defn):
    return False
  if renderer in ("android_compose", "android_remote_compose", "ios_swiftui"):
    return True
  strategy = str((defn or {}).get("strategy") or "").strip().lower()
  has_web = bool(
      (((defn or {}).get("web") or {}).get("template") or "").strip()
  )
  return strategy == "basic_composition" or not has_web


def _lookup_expr(props: dict[str, Any], expr: str) -> Any:
  cur: Any = props
  for part in expr.split("."):
    if isinstance(cur, dict) and part in cur:
      cur = cur[part]
    else:
      return None
  return cur


def _default_props(defn: dict[str, Any]) -> dict[str, Any]:
  out: dict[str, Any] = {}
  properties = ((defn.get("props") or {}).get("properties")) or {}
  if not isinstance(properties, dict):
    return out
  for pname, pschema in properties.items():
    if isinstance(pschema, dict) and "default" in pschema:
      out[pname] = copy.deepcopy(pschema["default"])
  return out


def _slot_prop_names(defn: dict[str, Any]) -> set[str]:
  out: set[str] = set()
  properties = ((defn.get("props") or {}).get("properties")) or {}
  if not isinstance(properties, dict):
    return out
  for pname, pschema in properties.items():
    if not isinstance(pschema, dict):
      continue
    ref = str(pschema.get("$ref") or "")
    if ref.endswith("/ComponentId") or ref.endswith("/ChildList"):
      out.add(pname)
  return out


def _interpolate_value(val: Any, props: dict[str, Any]) -> Any:
  """Resolves `{{prop}}` bindings inside a template property value."""
  if isinstance(val, str):
    m = _SINGLE_BIND_RE.match(val.strip())
    if m:
      resolved = _lookup_expr(props, m.group(1))
      if resolved is not None:
        return copy.deepcopy(resolved)
      return ""

    if _INLINE_BIND_RE.search(val):

      def _repl(match: re.Match[str]) -> str:
        v = _lookup_expr(props, match.group(1))
        if v is None:
          return ""
        if isinstance(v, dict) and "path" in v:
          return ""
        return str(v)

      return _INLINE_BIND_RE.sub(_repl, val)
    return val

  if isinstance(val, list):
    return [_interpolate_value(item, props) for item in val]

  if isinstance(val, dict):
    return {k: _interpolate_value(v, props) for k, v in val.items()}

  return copy.deepcopy(val)


def _resolve_child_id(
    raw_ref: Any,
    id_map: dict[str, str],
    props: dict[str, Any],
) -> str | None:
  if not isinstance(raw_ref, str):
    return None
  s = raw_ref.strip()
  if not s:
    return None
  m = _SINGLE_BIND_RE.match(s)
  if m:
    v = _lookup_expr(props, m.group(1))
    if isinstance(v, str) and v.strip():
      return v.strip()
    return None
  return id_map.get(s, s)


def _resolve_children_list(
    raw_children: Any,
    id_map: dict[str, str],
    props: dict[str, Any],
) -> Any:
  if isinstance(raw_children, str):
    m = _SINGLE_BIND_RE.match(raw_children.strip())
    if m:
      v = _lookup_expr(props, m.group(1))
      if isinstance(v, list):
        return [
            str(x).strip() for x in v if isinstance(x, str) and str(x).strip()
        ]
      if isinstance(v, str) and v.strip():
        return [v.strip()]
      return []
    return [id_map.get(raw_children.strip(), raw_children.strip())]

  if isinstance(raw_children, list):
    out: list[str] = []
    for item in raw_children:
      if not isinstance(item, str):
        continue
      s = item.strip()
      if not s:
        continue
      m = _SINGLE_BIND_RE.match(s)
      if m:
        v = _lookup_expr(props, m.group(1))
        if isinstance(v, list):
          out.extend(
              str(x).strip() for x in v if isinstance(x, str) and str(x).strip()
          )
        elif isinstance(v, str) and v.strip():
          out.append(v.strip())
      else:
        out.append(id_map.get(s, s))
    return out

  if isinstance(raw_children, dict):
    out_dict = copy.deepcopy(raw_children)
    cid = out_dict.get("componentId")
    if isinstance(cid, str):
      resolved_cid = _resolve_child_id(cid, id_map, props)
      if resolved_cid:
        out_dict["componentId"] = resolved_cid
    return out_dict

  return []


def _expand_instance(
    inst: dict[str, Any],
    defn: dict[str, Any],
) -> list[dict[str, Any]]:
  """Expands a single custom component instance into Basic Catalog nodes."""
  comp_spec = defn.get("composition") or {}
  comp_root = str(comp_spec.get("root") or "").strip()
  tpl_nodes = [
      n for n in comp_spec.get("components") or [] if isinstance(n, dict)
  ]
  if not comp_root or not tpl_nodes:
    return [inst]

  inst_id = str(inst.get("id") or "root")
  props = _default_props(defn)
  for k, v in inst.items():
    if k not in ("id", "component", "weight", "accessibility"):
      props[k] = copy.deepcopy(v)

  internal_ids = {
      str(n.get("id")).strip()
      for n in tpl_nodes
      if isinstance(n.get("id"), str) and str(n.get("id")).strip()
  }
  if comp_root not in internal_ids:
    comp_root = str(tpl_nodes[0].get("id") or "root")

  id_map = {
      cid: inst_id if cid == comp_root else f"{inst_id}__{cid}"
      for cid in internal_ids
  }

  expanded: list[dict[str, Any]] = []
  extra_placeholders: list[dict[str, Any]] = []
  for node in tpl_nodes:
    raw_id = str(node.get("id") or "").strip()
    if not raw_id:
      continue
    is_root = raw_id == comp_root
    new_id = id_map[raw_id]
    cname = str(node.get("component") or "Column").strip()
    out_node: dict[str, Any] = {"id": new_id, "component": cname}
    if is_root:
      if "weight" in inst:
        out_node["weight"] = copy.deepcopy(inst["weight"])
      if "accessibility" in inst:
        out_node["accessibility"] = copy.deepcopy(inst["accessibility"])

    for k, v in node.items():
      if k in ("id", "component"):
        continue
      if k in _CHILD_ID_FIELDS:
        resolved_child = _resolve_child_id(v, id_map, props)
        if resolved_child:
          out_node[k] = resolved_child
        elif cname == "Card" and k == "child":
          ph_id = f"{new_id}__empty"
          out_node["child"] = ph_id
          extra_placeholders.append(
              {"id": ph_id, "component": "Column", "children": []}
          )
      elif k == "children":
        out_node["children"] = _resolve_children_list(v, id_map, props)
      elif k == "tabs" and cname == "Tabs" and isinstance(v, list):
        tabs_out = []
        for tab in v:
          if not isinstance(tab, dict):
            continue
          t_copy = _interpolate_value(tab, props)
          if isinstance(t_copy.get("child"), str):
            rc = _resolve_child_id(tab["child"], id_map, props)
            if rc:
              t_copy["child"] = rc
          tabs_out.append(t_copy)
        out_node["tabs"] = tabs_out
      else:
        interp = _interpolate_value(v, props)
        if interp == "" and k in (
            "icon",
            "url",
            "variant",
            "fit",
            "justify",
            "align",
        ):
          continue
        if k == "action" and not isinstance(interp, dict):
          continue
        out_node[k] = interp

    if cname == "Button" and "action" not in out_node:
      out_node["action"] = {"event": {"name": f"{inst_id}_tap"}}
    if cname == "Button" and "child" not in out_node:
      lbl_id = f"{new_id}__label"
      out_node["child"] = lbl_id
      extra_placeholders.append({
          "id": lbl_id,
          "component": "Text",
          "text": str(out_node.pop("label", "Action")),
      })

    expanded.append(out_node)

  expanded.extend(extra_placeholders)
  return expanded


def expand_composite_components(
    messages: list[dict[str, Any]],
    custom_defs: dict[str, dict[str, Any]] | catalog_mod.Catalog | None,
    *,
    renderer: str = "android_compose",
    max_depth: int = 3,
) -> list[dict[str, Any]]:
  """Expands custom components with `composition` into Basic Catalog components."""
  if not messages or not custom_defs:
    return messages
  defs_map: dict[str, dict[str, Any]]
  if isinstance(custom_defs, catalog_mod.Catalog):
    defs_map = custom_defs.custom_defs or {}
  elif isinstance(custom_defs, dict):
    defs_map = custom_defs
  else:
    return messages

  lowerable = {
      name: defn
      for name, defn in defs_map.items()
      if should_lower_for_renderer(defn, renderer)
  }
  if not lowerable:
    return messages

  out_msgs = copy.deepcopy(messages)
  for msg in out_msgs:
    if not isinstance(msg, dict):
      continue
    uc = msg.get("updateComponents")
    if not isinstance(uc, dict) or not isinstance(uc.get("components"), list):
      continue
    comps = uc["components"]
    for _ in range(max_depth):
      changed = False
      next_comps: list[dict[str, Any]] = []
      for comp in comps:
        if not isinstance(comp, dict):
          continue
        cname = str(comp.get("component") or "")
        if cname in lowerable:
          next_comps.extend(_expand_instance(comp, lowerable[cname]))
          changed = True
        else:
          next_comps.append(comp)
      comps = next_comps
      if not changed:
        break
    uc["components"] = comps
  return out_msgs


def definition_to_template_record(
    defn: dict[str, Any],
    *,
    project_id: str,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
  """Converts a component definition with `composition` into a `templates` row dict."""
  defn = custom_web.normalize_definition(defn)
  comp = defn.get("composition") or {}
  nodes = [n for n in comp.get("components") or [] if isinstance(n, dict)]
  basic_used = []
  for n in nodes:
    c = str(n.get("component") or "").strip()
    if c and c not in basic_used:
      basic_used.append(c)

  lines: list[str] = []
  for n in nodes:
    cid = n.get("id") or "node"
    cname = n.get("component") or "Component"
    extras = []
    for k in ("variant", "justify", "align", "text"):
      if n.get(k):
        extras.append(f"{k}={n[k]!r}")
    if n.get("child"):
      extras.append(f"child->{n['child']}")
    if n.get("children"):
      extras.append(f"children->{n['children']}")
    detail = f" ({', '.join(extras)})" if extras else ""
    lines.append(f"- `{cid}`: `{cname}`{detail}")

  structure_md = (
      "\n".join(lines) if lines else f"- Root `{defn['name']}` pattern"
  )
  return {
      "project_id": project_id,
      "name": defn["name"] or "Composite Pattern",
      "description": defn.get("description") or "",
      "when_to_use": defn.get("usage") or defn.get("description") or "",
      "structure_md": structure_md,
      "components": basic_used or ["Card", "Column", "Text"],
      "source": source or {"kind": "component_composition"},
  }
