"""Revision chat, design token proposals, scope check and suggestions."""

from __future__ import annotations

import json
import os
from typing import Any

from core import diffs, imagery as imagery_mod, settings as settings_mod
from llm.gemini import Image, Llm
from sim import common
from sim.common import arr, obj, str_schema

IMAGERY_CHANGE_ITEM_SCHEMA = obj({
    "slug": str_schema(
        "Kebab-case identifier for the imagery or icon style preset (e.g."
        " 'weather-bg', 'list-item-icon')."
    ),
    "name": str_schema(
        "Human-readable preset name (e.g. 'Weather Full-Bleed Background',"
        " 'List Item SVG Icon')."
    ),
    "medium": {
        "type": "string",
        "enum": ["raster_image", "svg_icon", "svg_illustration"],
    },
    "target_slot": {
        "type": "string",
        "enum": [
            "card_background",
            "list_item_icon",
            "hero_banner",
            "inline_image",
            "custom_svg",
        ],
    },
    "incorporation": str_schema(
        "Where and how this generated asset should be incorporated (e.g. 'Use"
        " as a full-bleed HeroCard image in weather widgets with scrim"
        " auto' or 'Use a 24px generated SvgIcon next to each list item row')."
    ),
    "style_text": str_schema(
        "Visual style guidance for generating the image or SVG (e.g. 'Soft"
        " atmospheric editorial gouache painting, warm golden hour gradients,"
        " minimal clutter' or '2px rounded duotone outline icon using primary"
        " and primary_container colors')."
    ),
    "aspect_ratio": str_schema(
        "Aspect ratio such as '16:9', '4:3', '1:1', or '3:4'."
    ),
    "scrim": str_schema(
        "For card_background only: 'auto', 'dark', 'light', or 'none'."
    ),
    "use_attached_references": {
        "type": "boolean",
        "description": (
            "True if any reference image(s) attached to the designer's current"
            " chat message should be saved as style references on this preset."
        ),
    },
    "reason": str_schema("Short explanation of this imagery/icon preset."),
})

THEME_CHANGE_ITEM_SCHEMA = obj({
    "token": str_schema(
        "Canonical theme token key (e.g. primary, surface_container,"
        " radius_card, radius_control, radius_chip, font_family,"
        " font_family_heading, type_scale, heading_scale, heading_weight,"
        " space, card_padding, scheme) or custom token name (e.g."
        " brand-accent)."
    ),
    "value": str_schema(
        "New token value as a string (e.g. '#1A73E8', '16', '1.15',"
        " 'Merriweather', 'dark')."
    ),
    "is_custom": {"type": "boolean"},
    "maps_to": str_schema(
        "Optional canonical token key that a custom token maps to on native"
        " renderers, or ''."
    ),
    "reason": str_schema("Short explanation of this token change."),
})

REVISE_SCHEMA = obj({
    "reply_md": str_schema(),
    "proposal": obj({
        "has_proposal": {"type": "boolean"},
        "constitution_md": str_schema(
            "Full new constitution text if the constitution changed, or '' if"
            " only theme tokens are being changed."
        ),
        "change_summary": str_schema(),
        "changed_sections": arr(str_schema()),
        "theme_changes": arr(THEME_CHANGE_ITEM_SCHEMA),
        "imagery_changes": arr(IMAGERY_CHANGE_ITEM_SCHEMA),
        "custom_css": str_schema(
            "Optional custom CSS rules for the Web renderer if requested,"
            " or ''."
        ),
    }),
    "out_of_scope": arr(
        obj({
            "rule": str_schema(),
            "why": str_schema(),
            "suggested_home": {
                "type": "string",
                "enum": ["theme", "catalog", "component"],
            },
            "suggested_theme_change": obj(
                {"token": str_schema(), "value": str_schema()}
            ),
        })
    ),
})

SCOPE_SCHEMA = obj({
    "summary": str_schema(),
    "items": arr(
        obj({
            "rule_excerpt": str_schema(),
            "why": str_schema(),
            "suggested_home": {
                "type": "string",
                "enum": ["theme", "catalog", "component"],
            },
            "suggested_theme_change": obj(
                {"token": str_schema(), "value": str_schema()}
            ),
        })
    ),
})

GROUP_SCHEMA = obj({
    "groups": arr(
        obj({
            "title": str_schema("A short imperative title."),
            "body": str_schema(
                "The generalized rule suggestion, 1–2 sentences."
            ),
            "count": {"type": "integer"},
            "member_indexes": arr({"type": "integer"}),
        })
    ),
})


def _output_digest(o: dict[str, Any], sample: dict[str, Any] | None) -> str:
  j = o.get("judge") or {}
  lines = [
      f"## Output {o['id']} — {(sample or {}).get('title', '')} "
      f"[{(sample or {}).get('genre', '')}, {o.get('viewport')}]"
  ]
  if sample:
    lines.append("Intent: " + (sample.get("intent") or ""))
  if j:
    lines.append(
        f"Judge score {j.get('overall_score')}: {j.get('summary', '')}"
    )
    for f in (j.get("findings") or [])[:8]:
      if f.get("status") == "violated":
        lines.append(f"- violated: {f.get('rule')} — {f.get('evidence')}")
    for s in (j.get("issues") or [])[:5]:
      lines.append(f"- issue: {s}")
  if o.get("human_verdict"):
    lines.append(
        f"Designer verdict: {o['human_verdict']}"
        + (f" — {o.get('human_note')}" if o.get("human_note") else "")
    )
  val = o.get("validation") or {}
  if val and not val.get("ok"):
    lines.append(
        "Validation errors: "
        + "; ".join(e["message"] for e in (val.get("errors") or [])[:5])
    )
  comps = []
  for m in o.get("a2ui") or []:
    for c in (m.get("updateComponents") or {}).get("components") or []:
      comps.append(c.get("component"))
  if comps:
    lines.append(
        "Components used: "
        + ", ".join(
            f"{n}×{comps.count(n)}" for n in sorted(set(c for c in comps if c))
        )
    )
  return "\n".join(lines)


def _theme_digest(theme: dict[str, Any] | None) -> str:
  eff = settings_mod.resolve_effective_theme(theme)
  canonical = {
      k: eff[k]
      for k in settings_mod.DEFAULT_THEME
      if k not in ("custom_tokens", "custom_css")
  }
  lines = [
      (
          "# Current theme tokens (cross-platform: Web, Android Compose,"
          " RemoteCompose .rc, iOS SwiftUI)"
      ),
      "```json",
      json.dumps(canonical, indent=2),
      "```",
  ]
  if eff.get("custom_tokens"):
    lines.append(
        "Custom tokens:\n```json\n"
        + json.dumps(eff["custom_tokens"], indent=2)
        + "\n```"
    )
  if eff.get("custom_css"):
    lines.append(f"Custom Web CSS:\n```css\n{eff['custom_css']}\n```")
  return "\n".join(lines)


def _selected_component_digest(
    selected_component: dict[str, Any] | None,
    outputs: list[tuple[dict[str, Any], dict[str, Any] | None, bytes | None]],
) -> str:
  if not isinstance(selected_component, dict):
    return ""
  cid = str(selected_component.get("id") or "").strip()
  cname = str(selected_component.get("component") or "").strip()
  oid = str(selected_component.get("output_id") or "").strip()
  if not cid and not cname:
    return ""

  comp_node = (
      selected_component.get("props")
      if isinstance(selected_component.get("props"), dict)
      else None
  )
  all_by_id: dict[str, dict[str, Any]] = {}
  for o, _, _ in outputs:
    if oid and o.get("id") != oid:
      continue
    for msg in o.get("a2ui") or []:
      if not isinstance(msg, dict):
        continue
      uc = msg.get("updateComponents") or {}
      for c in uc.get("components") or []:
        if isinstance(c, dict) and c.get("id"):
          all_by_id[str(c["id"])] = c
          if str(c["id"]) == cid and comp_node is None:
            comp_node = c
  if comp_node and not cname:
    cname = str(comp_node.get("component") or "")

  lines = [
      (
          "# Selected component in Live View (assumed subject of the designer's"
          " message)"
      ),
      (
          "The designer clicked and selected this component in the Live View"
          " while reviewing the output. Unless they explicitly state otherwise,"
          " assume their message is referring to this component (and the"
          " layout, hierarchy, or token decisions affecting it):"
      ),
      (
          f"- Component: `{cname or 'Component'}` (id: `{cid}`"
          + (f", output: `{oid}`" if oid else "")
          + ")"
      ),
  ]
  if comp_node:
    lines.append(
        "Component A2UI definition:\n```json\n"
        + json.dumps(comp_node, indent=2)
        + "\n```"
    )
    # Include immediate child components if this is a container (Card, Column, Row, etc.)
    child_ids: list[str] = []
    for k in ("child", "contentChild", "trigger", "entryPointChild"):
      if isinstance(comp_node.get(k), str):
        child_ids.append(comp_node[k])
    ch = comp_node.get("children")
    if isinstance(ch, list):
      child_ids.extend(str(x) for x in ch if isinstance(x, str))
    elif isinstance(ch, dict) and isinstance(ch.get("componentId"), str):
      child_ids.append(ch["componentId"])
    child_nodes = [all_by_id[x] for x in child_ids[:8] if x in all_by_id]
    if child_nodes:
      lines.append(
          "Immediate child components:\n```json\n"
          + json.dumps(child_nodes, indent=2)
          + "\n```"
      )
  return "\n".join(lines)


def _templates_digest(templates: list[dict[str, Any]] | None) -> str:
  if not templates:
    return ""
  lines = ["# Templates (reusable layout patterns)\n"]
  for t in templates:
    if not isinstance(t, dict) or not t.get("name"):
      continue
    lines.append(f"## {t['name']}")
    if t.get("when_to_use"):
      lines.append(f"When to use: {t['when_to_use']}")
    if t.get("components"):
      lines.append("Components: " + ", ".join(str(c) for c in t["components"]))
    if t.get("structure_md"):
      lines.append(t["structure_md"])
    lines.append("")
  return "\n".join(lines).strip()


def revise(
    llm: Llm,
    *,
    constitution_md: str,
    catalog_text: str,
    history: list[dict[str, Any]],
    text: str,
    outputs: list[tuple[dict[str, Any], dict[str, Any] | None, bytes | None]],
    guidelines: list[dict[str, Any]],
    theme: dict[str, Any] | None = None,
    imagery_styles: list[dict[str, Any]] | None = None,
    templates: list[dict[str, Any]] | None = None,
    attached_images: list[tuple[bytes, str, str]] | None = None,
    selected_component: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
  parts: list[Any] = []
  cons = constitution_md.strip()
  ctx = [
      f"# Current constitution\n\n{cons if cons else '(empty)'}",
      _theme_digest(theme),
      "# Catalog (component names available)\n\n" + catalog_text,
  ]
  tpl_text = _templates_digest(templates)
  if tpl_text:
    ctx.append(tpl_text)
  imagery_text = imagery_mod.format_for_prompt(imagery_styles)
  if imagery_text:
    ctx.append(imagery_text)
  if guidelines:
    ctx.append(
        "# Attached Dos & Don'ts\n\n"
        + "\n\n".join(
            f"### {'DO' if g['kind'] == 'do' else 'DON’T'}: {g['title']}"
            " (fixable by constitution:"
            f" {g.get('fixable')})\n{g.get('body_md', '')}"
            for g in guidelines
        )
    )
  if history:
    ctx.append(
        "# Recent conversation\n\n"
        + "\n\n".join(
            f"**{m['role']}**: {m.get('text_md', '')[:1500]}"
            for m in history[-8:]
        )
    )
  parts.append("\n\n".join(ctx))
  for o, sample, png in outputs[:6]:
    parts.append(_output_digest(o, sample))
    if png:
      parts.append(Image(png, "image/png"))
  sc_digest = _selected_component_digest(selected_component, outputs)
  if sc_digest:
    parts.append(sc_digest)
  if attached_images:
    parts.append(
        "# Style reference images attached by designer in this message\n"
        "If creating or updating an imagery/icon preset in"
        " `proposal.imagery_changes` based on these images, set"
        " `use_attached_references: true`."
    )
    for img_bytes, mime, label in attached_images[:6]:
      if label:
        parts.append(f"Attached reference image: {label}")
      parts.append(Image(img_bytes, mime or "image/png"))
  parts.append("# Designer's message\n\n" + text)
  resp = llm.generate(
      purpose="revise",
      system=common.prompt("revise_constitution.md"),
      parts=parts,
      json_schema=REVISE_SCHEMA,
      temperature=0.4,
      model=model,
      timeout_s=240,
  )
  data = resp.data or {}
  prop = data.get("proposal") or {}
  proposal = None
  new_md = (prop.get("constitution_md") or "").strip()
  raw_theme_changes = prop.get("theme_changes") or []
  raw_imagery_changes = [
      it
      for it in prop.get("imagery_changes") or []
      if isinstance(it, dict) and (it.get("slug") or it.get("name"))
  ]
  raw_custom_css = (prop.get("custom_css") or "").strip()

  has_cons_change = bool(new_md and new_md != cons)
  _, all_applied = settings_mod.apply_theme_changes(
      theme, raw_theme_changes, raw_custom_css if raw_custom_css else None
  )
  preview_theme_changes = [
      c for c in all_applied if c.get("token") != "custom_css"
  ]
  has_theme_change = bool(all_applied)
  has_imagery_change = bool(raw_imagery_changes)

  if prop.get("has_proposal") and (
      has_cons_change or has_theme_change or has_imagery_change
  ):
    summary = prop.get("change_summary") or ""
    if not summary:
      parts_sum = []
      if has_cons_change:
        parts_sum.append("constitution")
      if has_theme_change:
        parts_sum.append(f"{len(preview_theme_changes)} theme token(s)")
      if has_imagery_change:
        parts_sum.append(f"{len(raw_imagery_changes)} imagery/icon style(s)")
      summary = "Update " + " & ".join(parts_sum)
    proposal = {
        "has_constitution_change": has_cons_change,
        "constitution_md": (new_md + "\n") if has_cons_change else "",
        "change_summary": summary,
        "changed_sections": prop.get("changed_sections") or [],
        "diff": (
            diffs.diff(constitution_md, new_md + "\n", "current", "proposed")
            if has_cons_change
            else None
        ),
        "theme_changes": preview_theme_changes,
        "raw_theme_changes": raw_theme_changes,
        "imagery_changes": raw_imagery_changes,
        "custom_css": raw_custom_css,
    }
  return {
      "reply_md": data.get("reply_md") or "",
      "proposal": proposal,
      "out_of_scope": data.get("out_of_scope") or [],
  }


def scope_check(
    llm: Llm, constitution_md: str, model: str | None = None
) -> dict[str, Any]:
  if not constitution_md.strip():
    return {"summary": "The constitution is empty.", "items": []}
  resp = llm.generate(
      purpose="scope_check",
      system=common.prompt("scope_check.md"),
      parts=["# Constitution\n\n" + constitution_md],
      json_schema=SCOPE_SCHEMA,
      temperature=0.2,
      model=model,
  )
  data = resp.data or {}
  items = []
  for it in data.get("items") or []:
    stc = it.get("suggested_theme_change") or {}
    if not stc.get("token"):
      it["suggested_theme_change"] = None
    items.append(it)
  return {"summary": data.get("summary") or "", "items": items}


def starter_suggestions() -> list[dict[str, Any]]:
  with open(
      os.path.join(common.PKG, "seeds", "suggestions.json"), encoding="utf-8"
  ) as fh:
    items = json.load(fh)
  return [
      {
          "id": f"starter:{s['id']}",
          "source": "starter",
          "title": s["title"],
          "body": s["body"],
          "example_md": s.get("example_md", ""),
      }
      for s in items
  ]


def group_judge_suggestions(
    llm: Llm | None, raw: list[tuple[str, str]], model: str | None = None
) -> list[dict[str, Any]]:
  """raw = [(output_id, suggestion)]. Groups with the LLM when many."""
  if not raw:
    return []
  if llm is None or len(raw) <= 6:
    return [
        {
            "id": f"judge:{i}",
            "source": "judge",
            "title": s[:90],
            "body": s,
            "count": 1,
            "ref_ids": [oid],
        }
        for i, (oid, s) in enumerate(raw[:20])
    ]
  listing = "\n".join(f"{i}. {s}" for i, (_, s) in enumerate(raw[:120]))
  resp = llm.generate(
      purpose="group_suggestions",
      system=(
          "Group these suggested design-constitution rules (from an "
          "automated judge) into at most 10 distinct generalized "
          "suggestions about layout/component decisions. Drop any that "
          "are about visual styling (colors, fonts, sizes, spacing). "
          "Order by count, highest first."
      ),
      parts=[listing],
      json_schema=GROUP_SCHEMA,
      temperature=0.2,
      model=model,
  )
  out = []
  for i, g in enumerate((resp.data or {}).get("groups") or []):
    idx = [k for k in g.get("member_indexes") or [] if 0 <= k < len(raw)]
    out.append({
        "id": f"judge:{i}",
        "source": "judge",
        "title": g.get("title"),
        "body": g.get("body"),
        "count": g.get("count") or len(idx),
        "ref_ids": sorted({raw[k][0] for k in idx}),
    })
  return out
