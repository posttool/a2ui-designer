"""A2UI generation: constitution + catalog + sample → validated messages."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from core import imagery as imagery_mod
from core.a2ui import normalize, prompt_schema, validator
from core.a2ui.catalog import Catalog
from llm.gemini import Llm, LlmError
from sim import common

VIEWPORTS = {
    "phone": {"id": "phone", "label": "Phone", "width": 412, "height": 915},
    "tablet": {"id": "tablet", "label": "Tablet", "width": 834, "height": 1194},
    "desktop": {
        "id": "desktop",
        "label": "Desktop",
        "width": 1280,
        "height": 800,
    },
    "smart_display": {
        "id": "smart_display",
        "label": "Smart display",
        "width": 1024,
        "height": 600,
    },
    "watch": {"id": "watch", "label": "Watch", "width": 192, "height": 192},
}


@dataclasses.dataclass
class GenerationContext:
  constitution_md: str
  catalog: Catalog
  viewport: str
  framing: str = "card"
  guidelines: list[dict[str, Any]] = dataclasses.field(default_factory=list)
  imagery_styles: list[dict[str, Any]] = dataclasses.field(default_factory=list)
  templates: list[dict[str, Any]] = dataclasses.field(default_factory=list)
  model: str | None = None
  temperature: float = 0.7
  max_repairs: int = 1
  stage: str = "result"  # "result" | "form" (connected-app input form)


@dataclasses.dataclass
class GenerationResult:
  messages: list[dict[str, Any]]
  validation: validator.ValidationResult
  model: str
  latency_ms: int
  repairs: int
  prompt_text: str
  raw_text: str


def system_prompt(ctx: GenerationContext) -> str:
  vp = VIEWPORTS.get(ctx.viewport, VIEWPORTS["phone"])
  parts = [
      common.prompt("generate_ui.md").replace(
          "<catalog id>", ctx.catalog.catalog_id
      )
  ]
  cons = (ctx.constitution_md or "").strip()
  parts.append(
      "# Design Constitution\n\n"
      + (
          cons
          if cons
          else (
              "(No constitution provided. Use your own best "
              "judgment for structure, hierarchy and density.)"
          )
      )
  )
  parts.append("# Catalog\n\n" + prompt_schema.catalog_prompt(ctx.catalog))
  if ctx.templates:
    t_blocks = [
        "Reusable screen/section templates defined in this project's design"
        " system. When the input data and intent match a template's"
        " `when_to_use`, follow its regional structure and compose it from"
        " the listed components:"
    ]
    for t in ctx.templates:
      lines = [f"## {t.get('name') or 'Template'}"]
      if t.get("description"):
        lines.append(str(t["description"]).strip())
      if t.get("when_to_use"):
        lines.append(f"- **When to use**: {str(t['when_to_use']).strip()}")
      if t.get("components"):
        lines.append(
            "- **Components**: " + ", ".join(str(c) for c in t["components"])
        )
      if t.get("structure_md"):
        lines.append(f"- **Structure**:\n{str(t['structure_md']).strip()}")
      t_blocks.append("\n".join(lines))
    parts.append("# Templates\n\n" + "\n\n".join(t_blocks))
  framing = (
      "a card-like surface inside a larger screen"
      if ctx.framing == "card"
      else "the full page/screen"
  )
  parts.append(
      f"# Viewport\n\nTarget viewport: {vp['label']} ({vp['width']}×"
      f"{vp['height']} CSS px). The surface is {framing}. Content taller than "
      "the viewport scrolls; avoid horizontal overflow."
  )
  if ctx.imagery_styles:
    img_block = imagery_mod.format_for_prompt(ctx.imagery_styles)
    if img_block:
      parts.append(img_block)
  if ctx.guidelines:
    lines = []
    for g in ctx.guidelines:
      first = (g.get("body_md") or "").strip().splitlines()
      lines.append(
          f"- {'DO' if g['kind'] == 'do' else 'DON’T'}: {g['title']}"
          + (f" — {first[0][:200]}" if first else "")
      )
    parts.append("# Dos & Don'ts from the designer\n\n" + "\n".join(lines))
  return "\n\n".join(parts)


def user_prompt(sample: dict[str, Any]) -> str:
  return (
      "Input data to present:\n```json\n"
      + common.clip_json(common.sample_input(sample), 16000)
      + "\n```\nReturn the JSON object with the A2UI messages."
  )


def form_user_prompt(
    form: dict[str, Any], app_name: str = "", intent: str = ""
) -> str:
  """User turn for the input-form stage of a connected-app sample."""
  spec = {
      "app": app_name,
      "tool": form.get("tool"),
      "title": form.get("title"),
      "description": form.get("description") or "",
      "input_schema": form.get("schema") or {},
      "prefilled_values": form.get("values") or {},
      "submit_label_hint": form.get("submit_label") or "",
  }
  return (
      common.prompt("generate_form.md")
      + (f"\n\nWhat the person is trying to do: {intent}" if intent else "")
      + "\n\nTool to build the form for:\n```json\n"
      + common.clip_json(spec, 12000)
      + "\n```\nReturn the JSON object with the A2UI messages."
  )


def has_submit_action(messages: list[dict[str, Any]]) -> bool:
  for m in messages or []:
    for c in (m.get("updateComponents") or {}).get("components") or []:
      ev = (
          ((c.get("action") or {}).get("event") or {})
          if isinstance(c, dict)
          else {}
      )
      if c.get("component") == "Button" and ev.get("name") == "submit":
        return True
  return False


def generate(
    llm: Llm,
    ctx: GenerationContext,
    sample: dict[str, Any],
    user: str | None = None,
) -> GenerationResult:
  """Generates A2UI for a sample (or for `user`, a prebuilt user turn)."""
  system = system_prompt(ctx)
  user = user or user_prompt(sample)
  resp = llm.generate(
      purpose="generate",
      system=system,
      parts=[user],
      json_mode=True,
      temperature=ctx.temperature,
      model=ctx.model,
  )
  latency = resp.latency_ms
  msgs = normalize.to_messages(resp.data, catalog_id=ctx.catalog.catalog_id)
  result = validator.validate(msgs, ctx.catalog)
  raw = resp.text
  repairs = 0
  model = resp.model
  while not result.ok and repairs < ctx.max_repairs:
    repairs += 1
    repair = (
        user
        + "\n\nYour previous answer:\n```json\n"
        + common.clip_json({"messages": msgs} if msgs else raw, 20000)
        + "\n```\nIt failed A2UI validation:\n"
        + result.summary(30)
        + "\n\nReturn the corrected full JSON object. Keep the same design "
        "decisions; only fix what is invalid."
    )
    try:
      resp = llm.generate(
          purpose="repair",
          system=system,
          parts=[repair],
          json_mode=True,
          temperature=0.2,
          model=ctx.model,
      )
    except LlmError:
      break
    latency += resp.latency_ms
    new_msgs = normalize.to_messages(
        resp.data, catalog_id=ctx.catalog.catalog_id
    )
    new_result = validator.validate(new_msgs, ctx.catalog)
    if new_msgs and (
        new_result.ok or len(new_result.errors) <= len(result.errors)
    ):
      msgs, result, raw, model = new_msgs, new_result, resp.text, resp.model
  if not msgs:
    raise LlmError("model output contained no A2UI messages: " + raw[:300])
  return GenerationResult(
      messages=msgs,
      validation=result,
      model=model,
      latency_ms=latency,
      repairs=repairs,
      prompt_text=system + "\n\n---\n\n" + user,
      raw_text=raw,
  )


def dumps_messages(msgs: list[dict[str, Any]]) -> str:
  return json.dumps(msgs, ensure_ascii=False)
