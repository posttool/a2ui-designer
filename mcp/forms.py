"""Deterministic JSON Schema → A2UI v0.9 form (the "simple form").

Used when LLM form generation fails or is not requested, and as the reference
shape for LLM-generated forms: values live at `/form/<field>`, and the primary
Button fires `action.event = {name: "submit", context: {form: {path:
"/form"}}}`.
"""

from __future__ import annotations

import re
from typing import Any

from core.a2ui.catalog import BASIC_CATALOG_ID

SUBMIT_EVENT = "submit"
DATA_ROOT = "/form"


def _label(name: str, spec: dict[str, Any]) -> str:
  t = spec.get("title")
  if t:
    return str(t)
  words = re.sub(r"[_\-]+", " ", name).strip()
  return words[:1].upper() + words[1:]


def _cid(name: str) -> str:
  return "f_" + re.sub(r"[^A-Za-z0-9_]", "_", name)


def default_values(
    schema: dict[str, Any], prefill: dict[str, Any] | None
) -> dict:
  props = (schema or {}).get("properties") or {}
  vals: dict[str, Any] = {}
  for name, spec in props.items():
    if prefill and name in prefill:
      v = prefill[name]
    elif "default" in spec:
      v = spec["default"]
    else:
      v = None
    t = spec.get("type")
    if t == "boolean":
      v = bool(v)
    elif t == "array":
      v = [str(x) for x in v] if isinstance(v, list) else []
    elif spec.get("enum"):
      v = [str(v)] if v is not None else []
    elif t in ("integer", "number"):
      v = v if isinstance(v, (int, float)) else (spec.get("minimum") or 0)
    else:
      v = "" if v is None else str(v)
    vals[name] = v
  return vals


def coerce_args(schema: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
  """Form data-model values → tool arguments (types per the schema)."""
  props = (schema or {}).get("properties") or {}
  out: dict[str, Any] = {}
  for name, spec in props.items():
    if name not in (raw or {}):
      continue
    v = raw[name]
    t = spec.get("type")
    if spec.get("enum") and t != "array":
      if isinstance(v, list):
        v = v[0] if v else None
      if v is not None and t in ("integer", "number"):
        try:
          v = int(v) if t == "integer" else float(v)
        except (TypeError, ValueError):
          pass
    elif t == "boolean":
      v = (
          bool(v)
          if not isinstance(v, str)
          else v.lower() in ("1", "true", "yes")
      )
    elif t in ("integer", "number"):
      if v in ("", None):
        v = None
      else:
        try:
          v = int(float(v)) if t == "integer" else float(v)
        except (TypeError, ValueError):
          v = None
    elif t == "array":
      if isinstance(v, str):
        v = [x.strip() for x in re.split(r"[,\n]", v) if x.strip()]
      elif not isinstance(v, list):
        v = []
    else:
      v = "" if v is None else str(v)
    if v in (None, "", []) and name not in (schema.get("required") or []):
      continue
    out[name] = v
  return out


def schema_form(
    tool: dict[str, Any],
    prefill: dict[str, Any] | None = None,
    app_name: str = "",
    surface_id: str = "tool_form",
) -> dict[str, Any]:
  """Returns {messages, form} for a tool summary (see mcp.client.tool_summary)."""
  schema = tool.get("input_schema") or {"type": "object", "properties": {}}
  props = schema.get("properties") or {}
  required = set(schema.get("required") or [])
  title = tool.get("title") or _label(tool.get("name") or "Tool", {})
  comps: list[dict[str, Any]] = []
  field_ids: list[str] = []
  for name, spec in props.items():
    cid = _cid(name)
    path = f"{DATA_ROOT}/{name}"
    label = _label(name, spec) + ("" if name in required else " (optional)")
    t = spec.get("type")
    enum = spec.get("enum")
    if t == "boolean":
      comps.append({
          "id": cid,
          "component": "CheckBox",
          "label": label,
          "value": {"path": path},
      })
    elif enum or (t == "array" and (spec.get("items") or {}).get("enum")):
      opts = enum or spec["items"]["enum"]
      comps.append({
          "id": cid,
          "component": "ChoicePicker",
          "label": label,
          "variant": (
              "multipleSelection" if t == "array" else "mutuallyExclusive"
          ),
          "displayStyle": "chips" if len(opts) <= 6 else "checkbox",
          "options": [
              {"label": _label(str(o), {}), "value": str(o)} for o in opts
          ],
          "value": {"path": path},
      })
    elif (
        t in ("integer", "number")
        and "minimum" in spec
        and "maximum" in spec
        and (spec["maximum"] - spec["minimum"] <= 1000)
    ):
      comps.append({
          "id": cid,
          "component": "Slider",
          "label": label,
          "min": spec["minimum"],
          "max": spec["maximum"],
          "value": {"path": path},
      })
    elif spec.get("format") in ("date", "date-time", "time"):
      f = spec["format"]
      comps.append({
          "id": cid,
          "component": "DateTimeInput",
          "label": label,
          "enableDate": f != "time",
          "enableTime": f != "date",
          "value": {"path": path},
      })
    else:
      variant = "shortText"
      if t in ("integer", "number"):
        variant = "number"
      elif (spec.get("maxLength") or 0) > 120 or spec.get(
          "format"
      ) == "textarea":
        variant = "longText"
      c: dict[str, Any] = {
          "id": cid,
          "component": "TextField",
          "label": label,
          "value": {"path": path},
          "variant": variant,
      }
      if spec.get("pattern"):
        c["validationRegexp"] = spec["pattern"]
      comps.append(c)
    field_ids.append(cid)
    if spec.get("description"):
      hid = cid + "_help"
      comps.append({
          "id": hid,
          "component": "Text",
          "text": str(spec["description"]),
          "variant": "caption",
      })
      field_ids.append(hid)
  submit_label = "Search" if "search" in (tool.get("name") or "") else "Submit"
  head = [
      {"id": "title", "component": "Text", "text": title, "variant": "h3"},
  ]
  if tool.get("description"):
    head.append({
        "id": "subtitle",
        "component": "Text",
        "text": tool["description"],
        "variant": "body",
    })
  if app_name:
    head.insert(
        0,
        {
            "id": "app",
            "component": "Text",
            "text": app_name,
            "variant": "caption",
        },
    )
  comps = (
      head
      + comps
      + [
          {
              "id": "submit",
              "component": "Button",
              "child": "submit_label",
              "variant": "primary",
              "action": {
                  "event": {
                      "name": SUBMIT_EVENT,
                      "context": {"form": {"path": DATA_ROOT}},
                  }
              },
          },
          {"id": "submit_label", "component": "Text", "text": submit_label},
      ]
  )
  comps.insert(
      0,
      {
          "id": "col",
          "component": "Column",
          "children": [c["id"] for c in head] + field_ids + ["submit"],
          "align": "stretch",
      },
  )
  comps.insert(0, {"id": "root", "component": "Card", "child": "col"})
  messages = [
      {
          "version": "v0.9",
          "createSurface": {
              "surfaceId": surface_id,
              "catalogId": BASIC_CATALOG_ID,
              "sendDataModel": True,
          },
      },
      {
          "version": "v0.9",
          "updateComponents": {"surfaceId": surface_id, "components": comps},
      },
      {
          "version": "v0.9",
          "updateDataModel": {
              "surfaceId": surface_id,
              "path": "/",
              "value": {"form": default_values(schema, prefill)},
          },
      },
  ]
  return {
      "messages": messages,
      "form": form_spec(tool, prefill, submit_label),
  }


def form_spec(
    tool: dict[str, Any], values: dict[str, Any] | None, submit_label: str = ""
) -> dict[str, Any]:
  return {
      "tool": tool.get("name"),
      "title": tool.get("title") or _label(tool.get("name") or "Tool", {}),
      "schema": (
          tool.get("input_schema") or {"type": "object", "properties": {}}
      ),
      "values": values or {},
      "data_root": DATA_ROOT,
      "submit_event": SUBMIT_EVENT,
      "submit_label": (
          submit_label
          or ("Search" if "search" in (tool.get("name") or "") else "Submit")
      ),
  }
