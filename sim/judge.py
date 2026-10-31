"""LLM judge: rubric = the constitution + layout-decision quality across renderers."""

from __future__ import annotations

from typing import Any

from core import imagery as imagery_mod
from core import layout_diff
from core import settings as settings_mod
from llm.gemini import Image, Llm
from sim import common
from sim.common import arr, obj, str_schema

JUDGE_SCHEMA = obj({
    "overall_score": {"type": "integer", "minimum": 1, "maximum": 10},
    "verdict": {"type": "string", "enum": ["pass", "warn", "fail"]},
    "summary": str_schema("Two sentences at most."),
    "findings": arr(
        obj({
            "rule_id": str_schema(
                "Id of the numbered rule (R1, R2, …) or template (T1, …) this"
                " finding is about, or '' for general observations."
            ),
            "rule": str_schema(),
            "applies": {
                "type": "string",
                "enum": ["yes", "no"],
                "description": (
                    "Does this rule/template apply to THIS input (its data"
                    " shape and intent)? 'no' → status must be not_applicable."
                ),
            },
            "status": {
                "type": "string",
                "enum": ["followed", "violated", "not_applicable"],
            },
            "evidence": str_schema(),
            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        })
    ),
    "strengths": arr(str_schema()),
    "issues": arr(str_schema()),
    "platform_notes": obj({
        "web": str_schema("Rendering note for Web (HTML/CSS), if rendered."),
        "android_compose": str_schema(
            "Rendering note for Android Jetpack Compose, if rendered."
        ),
        "android_remote_compose": str_schema(
            "Rendering note for Android RemoteCompose (.rc wire), if rendered."
        ),
        "ios_swiftui": str_schema(
            "Rendering note for iOS SwiftUI, if rendered."
        ),
    }),
    "suggested_constitution_changes": arr(str_schema()),
    "likely_unfixable_by_constitution": {"type": "boolean"},
    "unfixable_reason": str_schema(),
})

RENDERER_LABELS = {r["id"]: r["label"] for r in settings_mod.RENDERERS}


def judge(
    llm: Llm,
    *,
    constitution_md: str,
    sample: dict[str, Any],
    messages: list[dict[str, Any]],
    validation: dict[str, Any],
    viewport: dict[str, Any],
    render_meta: dict[str, Any] | None,
    screenshot_png: bytes | None,
    screenshots: dict[str, bytes] | None = None,
    renders: dict[str, Any] | None = None,
    imagery_styles: list[dict[str, Any]] | None = None,
    model: str | None = None,
    stage: str = "result",
    templates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
  cons = (constitution_md or "").strip() or "(empty — no constitution yet)"
  rules = layout_diff.parse_rules(constitution_md or "")
  if rules:
    cons += (
        "\n\n# Numbered rules (cite these ids in findings[].rule_id)\n\n"
        + (
            "\n".join(
                f"- {r['id']}"
                + (f" [{r['section']}]" if r.get("section") else "")
                + f": {r['text']}"
                for r in rules
            )
        )
    )
  tpl_lines = []
  for i, tp in enumerate(templates or [], start=1):
    if isinstance(tp, dict) and tp.get("name"):
      tpl_lines.append(
          f"- T{i}: {tp.get('name')}"
          + (
              f" — when to use: {tp.get('when_to_use')}"
              if tp.get("when_to_use")
              else ""
          )
      )
  if tpl_lines:
    cons += (
        "\n\n# Layout templates (cite T-ids when a template applies)\n\n"
        + "\n".join(tpl_lines)
    )
  img_block = (
      imagery_mod.format_for_prompt(imagery_styles) if imagery_styles else ""
  )
  text = (
      f"# Design Constitution\n\n{cons}\n\n"
      + (f"{img_block}\n\n" if img_block else "")
      + f"# Viewport\n\n{viewport['label']} {viewport['width']}×"
      f"{viewport['height']}\n\n"
      "# Input"
      f" data\n\n```json\n{common.clip_json(common.sample_input(sample), 9000)}\n```\n\n"
      + (
          "# Stage\n\nThis is the INPUT FORM stage of a connected-app journey:"
          " the surface collects the arguments of the tool below (its JSON"
          " Schema, prefilled with `values`) before the tool runs; the input"
          " data above is what the tool will return. Judge it as a form under"
          " the constitution: field order, grouping, labels, optional/required"
          " clarity, one clear submit action.\n\n```json\n"
          + common.clip_json((sample.get("meta") or {}).get("form") or {}, 6000)
          + "\n```\n\n"
          if stage == "form"
          else ""
      )
      + "# Generated"
      f" A2UI\n\n```json\n{common.clip_json(messages, 14000)}\n```\n\n"
      f"# Validation\n\n{'valid' if validation.get('ok') else 'INVALID: ' + '; '.join(e['path'] + ' ' + e['message'] for e in (validation.get('errors') or [])[:12])}\n"
  )
  if render_meta:
    text += (
        f"\n# Web render notes\n\nheight={render_meta.get('height')} px, "
        f"horizontal overflow={render_meta.get('overflowX')}, "
        f"missing images={render_meta.get('missingImages')}, "
        f"unsupported={render_meta.get('unsupported')}, "
        f"errors={render_meta.get('errors')}\n"
    )
  if renders:
    notes = []
    for rid, info in renders.items():
      if not isinstance(info, dict):
        continue
      meta = info.get("meta") or {}
      lbl = RENDERER_LABELS.get(rid, rid)
      extra = ""
      if meta.get("rc_byte_size"):
        extra = (
            f", rc_bytes={meta.get('rc_byte_size')},"
            f" rc_ops={meta.get('rc_op_count')}"
        )
      notes.append(
          f"- {lbl} ({rid}): status={info.get('status')}, "
          f"size={meta.get('width')}x{meta.get('height')}{extra}"
      )
    if notes:
      text += "\n# Platform render summary\n\n" + "\n".join(notes) + "\n"

  parts: list[Any] = [text]
  if screenshots:
    for rid, png_bytes in screenshots.items():
      if png_bytes:
        lbl = RENDERER_LABELS.get(rid, rid)
        parts.append(f"Screenshot of rendered result on {lbl} ({rid}):")
        parts.append(Image(png_bytes, "image/png"))
  elif screenshot_png:
    parts.append("Screenshot of the rendered result:")
    parts.append(Image(screenshot_png, "image/png"))

  resp = llm.generate(
      purpose="judge",
      system=common.prompt("judge.md"),
      parts=parts,
      json_schema=JUDGE_SCHEMA,
      temperature=0.2,
      model=model,
  )
  data = resp.data or {}
  score = data.get("overall_score")
  try:
    score = max(1, min(10, int(score)))
  except (TypeError, ValueError):
    score = None
  data["overall_score"] = score
  data["model"] = resp.model
  return data
