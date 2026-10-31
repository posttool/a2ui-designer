"""Render a catalog as compact prompt text for the generator."""

from __future__ import annotations

from core.a2ui.catalog import Catalog

# Short, spec-derived notes on how the shared types are written.
TYPE_NOTES = """\
Value types:
- DynamicString/Number/Boolean: a literal ("Hi", 3, true), a data binding {"path": "/json/pointer"} (relative "name" inside template children), or a function call {"call": "formatString", "args": {...}, "returnType": "string"}.
- DynamicStringList: ["a", "b"] or {"path": "/selected"}.
- ComponentId: the id string of another component in the same surface.
- ChildList: ["id1", "id2"] or a template {"componentId": "row-template", "path": "/items"} that repeats the template for each array item (bindings inside use paths relative to the item).
- Action: {"event": {"name": "submit", "context": {"key": {"path": "/x"}}}} or {"functionCall": {"call": "openUrl", "args": {"url": "https://..."}, "returnType": "void"}}.
- CheckRule: {"condition": {"call": "required", "args": {"value": {"path": "/email"}}, "returnType": "boolean"}, "message": "Required"}.
- formatString takes exactly one arg, "value", a template string with ${...} expressions: {"call": "formatString", "args": {"value": "Due ${/task/due} · ${/task/owner}"}, "returnType": "string"} (inside template children use relative paths: "${name}"). Function args are closed: use only the arg names listed for each function.
- Template children iterate arrays of OBJECTS and bind fields relatively. If the source data is an array of plain strings or numbers, put objects in the data model instead (e.g. [{"label": "spit"}, {"label": "smoke"}]) and bind {"path": "label"}.
Every component may also have "weight" (flex grow, only as a direct child of Row/Column) and "accessibility" {"label", "description"}."""


def _clip(text: str, n: int = 160) -> str:
  t = " ".join((text or "").split())
  return t if len(t) <= n else t[: n - 1] + "…"


def catalog_prompt(catalog: Catalog) -> str:
  enabled = catalog.enabled_components()
  custom = [n for n, s in enabled.items() if s.custom]
  ordered = custom + [n for n in enabled if n not in custom]
  lines = []
  if custom:
    lines.append(
        "This project has design-system components (listed first, marked"
        " custom). Prefer them over assembling the same thing from Basic"
        " components whenever their usage notes fit the content; follow"
        " their usage notes. Slot props (ComponentId / ChildList) take ids of"
        " other components in the surface."
    )
    lines.append("")
  lines.append("Components you may use (exact names; no others):")
  for name in ordered:
    spec = enabled[name]
    head = f"- {name}" + (" (custom)" if spec.custom else "")
    if spec.description:
      head += f" — {_clip(spec.description, 140)}"
    lines.append(head)
    if spec.usage:
      lines.append(f"    usage: {_clip(spec.usage, 600)}")
    for p in spec.props.values():
      s = f"    {p.name}: {p.type}"
      if p.required:
        s += " (required)"
      if p.enum and "|" not in p.type:
        s += " ∈ " + "|".join(p.enum)
      elif p.enum:
        s += " ∈ " + "|".join(p.enum[:60])
      if p.default is not None:
        s += f" (default {p.default})"
      if p.description and p.name not in ("checks",):
        s += f" — {_clip(p.description, 120)}"
      lines.append(s)
  lines.append("")
  lines.append('Functions (for "call"): ' + ", ".join(catalog.functions))
  lines.append("")
  lines.append(TYPE_NOTES)
  return "\n".join(lines)
