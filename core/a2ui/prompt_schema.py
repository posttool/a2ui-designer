"""Render a catalog as compact prompt text for the generator (A2UI v1.0)."""

from __future__ import annotations

from core.a2ui.catalog import Catalog
from core.a2ui.catalog import EXTENSION_CATALOG_ID

# Short, spec-derived notes on how the shared v1.0 types are written.
TYPE_NOTES = """\
Value types (A2UI v1.0):
- DynamicString/Number/Boolean: a literal ("Hi", 3, true), a data binding {"@path": "/json/pointer"} (relative "name" inside template children), or a function call {"@call": "formatString", "args": {...}} (never send "returnType").
- DynamicStringList: ["a", "b"] or {"@path": "/selected"}.
- ComponentId / Child: the id string of another component in the same surface. Ids are identifiers: letters, digits and underscores, not starting with a digit, NO hyphens (user_card or userCard, never user-card).
- ChildList: ["id1", "id2"] or a template {"componentId": "rowTemplate", "path": "/items"} (plain "path" here) that repeats the template for each array item; bindings inside the template use paths relative to the item, and {"@call": "@index"} gives the 0-based position.
- Action: {"event": {"name": "submit", "context": {"key": {"@path": "/x"}}}} or {"functionCall": {"@call": "openUrl", "args": {"url": "https://..."}}}.
- CheckRule: {"condition": {"@call": "required", "args": {"value": {"@path": "/email"}}}, "message": "Required"} (message optional).
- formatString takes exactly one arg, "value", a template string with ${...} expressions: {"@call": "formatString", "args": {"value": "Due ${/task/due} · ${/task/owner}"}} (inside template children use relative paths: "${name}"). Function args are closed: use only the arg names listed for each function.
- Text: variant is only "body" (default) or "caption"; write headings and emphasis as Markdown in the text itself ("# Title", "## Section", "**bold**").
- Literal object keys that start with "@" must be escaped as "@@" (e.g. "@@type").
- Template children iterate arrays of OBJECTS and bind fields relatively. If the source data is an array of plain strings or numbers, put objects in the data model instead (e.g. [{"label": "spit"}, {"label": "smoke"}]) and bind {"@path": "label"}.
Every component may also have "weight" (flex grow, only as a direct child of Row/Column), "accessibility" {"label", "description"} and "catalogId" (the catalog the component comes from; the surface default is the Basic catalog)."""


def _clip(text: str, n: int = 160) -> str:
  t = " ".join((text or "").split())
  return t if len(t) <= n else t[: n - 1] + "…"


def catalog_instructions(catalog: Catalog) -> str:
  """Upstream Basic `instructions` (+ extension instructions when enabled)."""
  blocks = []
  for cid, text in catalog.instructions().items():
    text = (text or "").strip()
    if not text:
      continue
    title = (
        "A2UI Designer extension catalog"
        if cid == EXTENSION_CATALOG_ID
        else "Basic catalog"
    )
    blocks.append(f"## {title} instructions ({cid})\n\n{text}")
  return "\n\n".join(blocks)


def catalog_prompt(catalog: Catalog, include_instructions: bool = True) -> str:
  enabled = catalog.enabled_components()
  custom = [n for n, s in enabled.items() if s.custom]
  ext = [n for n, s in enabled.items() if s.extension]
  ordered = custom + ext + [n for n in enabled if n not in custom + ext]
  lines = []
  if custom:
    lines.append(
        "This project has design-system components (listed first, marked"
        " custom). Prefer them over assembling the same thing from Basic"
        " components whenever their usage notes fit the content; follow"
        " their usage notes. Slot props (ComponentId / ChildList) take ids of"
        " other components in the surface. Every custom component instance"
        f' carries "catalogId": "{catalog.catalog_id}".'
    )
    lines.append("")
  if ext:
    lines.append(
        "Extension components (marked extension) come from the A2UI Designer"
        f' extension catalog; every instance carries "catalogId":'
        f' "{EXTENSION_CATALOG_ID}".'
    )
    lines.append("")
  lines.append("Components you may use (exact names; no others):")
  for name in ordered:
    spec = enabled[name]
    tag = " (custom)" if spec.custom else " (extension)" if spec.extension else ""
    head = f"- {name}{tag}"
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
    for group in spec.one_of:
      lines.append(f"    exactly one of: {', '.join(group)}")
  lines.append("")
  lines.append('Functions (for "@call"): ' + ", ".join(catalog.functions))
  lines.append("")
  lines.append(TYPE_NOTES)
  if include_instructions:
    instr = catalog_instructions(catalog)
    if instr:
      lines.append("")
      lines.append(instr)
  return "\n".join(lines)
