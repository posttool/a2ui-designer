"""Synthetic sample generation (one LLM call per genre batch or brief)."""

from __future__ import annotations

import json
from typing import Any

from llm.gemini import Llm, LlmError
from sim import common, taxonomy
from sim.common import arr, obj, str_schema

_SAMPLE_PROPS = {
    "title": str_schema("Short human label for the sample."),
    "intent": str_schema("One sentence: what the person needs right now."),
    "context": obj({
        "surface": str_schema(),
        "user_state": str_schema(),
        "role": str_schema(),
        "locale": str_schema(),
        "time_of_day": str_schema(),
        "urgency": str_schema(),
    }),
    "size_class": {"type": "string", "enum": taxonomy.SIZES},
    "state": {
        "type": "string",
        "enum": ["content", "loading", "empty", "error"],
    },
    "edge_cases": arr(str_schema()),
    "tags": arr(str_schema()),
    "payload_json": str_schema(
        "The content payload as a JSON object serialized to a string."
    ),
}

SAMPLE_SCHEMA = obj({"samples": arr(obj(dict(_SAMPLE_PROPS)))})

# Free-form mode: the model also files each sample into a category.
CATEGORIZED_SCHEMA = obj({
    "samples": arr(
        obj({
            "category": str_schema(
                "Top-level category label. Reuse an existing one when it fits;"
                " otherwise a new concise label (1-4 words)."
            ),
            "subcategory": str_schema(
                "Subcategory label within the category. Reuse an existing one"
                " when it fits; otherwise a new concise label (1-4 words)."
            ),
            "subcategory_description": str_schema(
                "One sentence describing the subcategory (used when new)."
            ),
            **_SAMPLE_PROPS,
        })
    )
})


def _parse(it: dict[str, Any], plan: dict[str, Any], i: int, label: str):
  payload = it.get("payload_json")
  if isinstance(payload, str):
    try:
      payload = json.loads(payload)
    except ValueError:
      payload = {"text": payload}
  if payload is None:
    payload = it.get("payload") or {}
  ctx = dict(it.get("context") or {})
  ctx.setdefault("locale", plan.get("locale", "en-US"))
  return {
      "title": (it.get("title") or f"{label} sample {i + 1}")[:200],
      "intent": it.get("intent") or "",
      "context": ctx,
      "payload": payload,
      "size_class": it.get("size_class") or plan.get("size_class", "m"),
      "state": it.get("state") or plan.get("state", "content"),
      "edge_cases": it.get("edge_cases") or plan.get("edge_cases", []),
      "tags": it.get("tags") or [],
      "source": "synth",
  }


def synthesize(
    llm: Llm,
    genre: str | dict[str, Any],
    n: int,
    model: str | None = None,
    seed: int | None = None,
    brief: str = "",
) -> list[dict[str, Any]]:
  """Returns sample dicts (without ids) for one genre (built-in or custom).

  `genre` is a built-in id or a genre dict from `taxonomy.all_genres()`.
  `brief` is optional extra direction from the person synthesizing.
  """
  g = taxonomy.GENRES[genre] if isinstance(genre, str) else genre
  plans = taxonomy.axis_plan(g, n, seed=seed)
  user = (
      f"Genre: {g['label']} ({g['id']}) — group: {g['group_label']}\n"
      f"Genre description: {g.get('description') or g['label']}\n\n"
      + (
          f"Extra direction from the designer:\n{brief.strip()}\n\n"
          if brief
          else ""
      )
      + f"Create exactly {n} samples. Per-sample axis assignments (sample i "
      f"must follow entry i):\n{json.dumps(plans, indent=1)}\n\n"
      'Return {"samples": [...]} where payload_json is a JSON object '
      "serialized as a string."
  )
  resp = llm.generate(
      purpose="synthesize",
      system=common.prompt("synthesize_samples.md"),
      parts=[user],
      json_schema=SAMPLE_SCHEMA,
      temperature=1.0,
      model=model,
      timeout_s=240,
  )
  items = (resp.data or {}).get("samples") or []
  out = []
  for i, it in enumerate(items[:n]):
    if not isinstance(it, dict):
      continue
    plan = plans[i] if i < len(plans) else {}
    s = _parse(it, plan, i, g["label"])
    s.update({"group": g["group"], "genre": g["id"]})
    out.append(s)
  if not out:
    raise LlmError(f"synthesizer returned no samples for {g['id']}")
  return out


def synthesize_brief(
    llm: Llm,
    brief: str,
    n: int,
    existing: dict[str, Any],
    model: str | None = None,
    seed: int | None = None,
) -> list[dict[str, Any]]:
  """Free-form synthesis from a designer's brief; the model picks categories.

  Returned samples carry `category_label`, `subcategory_label` and
  `subcategory_description`; the caller maps them onto (possibly new)
  categories. `existing` is `taxonomy.groups_api(store)`.
  """
  plans = taxonomy.axis_plan(
      {"id": "brief", "label": "Brief", "group": "brief"}, n, seed=seed
  )
  cats = [
      {
          "category": g["label"],
          "subcategories": [x["label"] for x in g["genres"]],
      }
      for g in existing.get("groups") or []
  ]
  user = (
      f"Designer's brief:\n{brief.strip()}\n\n"
      "Existing categories (reuse when one genuinely fits; invent a new"
      " category or subcategory when nothing does):\n"
      f"{json.dumps(cats, ensure_ascii=False)}\n\n"
      f"Create exactly {n} samples that realize the brief. Per-sample axis "
      "assignments (sample i must follow entry i):\n"
      f"{json.dumps(plans, indent=1)}\n\n"
      'Return {"samples": [...]} where payload_json is a JSON object '
      "serialized as a string."
  )
  resp = llm.generate(
      purpose="synthesize",
      system=common.prompt("synthesize_samples.md"),
      parts=[user],
      json_schema=CATEGORIZED_SCHEMA,
      temperature=1.0,
      model=model,
      timeout_s=240,
  )
  items = (resp.data or {}).get("samples") or []
  out = []
  for i, it in enumerate(items[:n]):
    if not isinstance(it, dict):
      continue
    plan = plans[i] if i < len(plans) else {}
    s = _parse(it, plan, i, "Brief")
    s["category_label"] = str(it.get("category") or "").strip()
    s["subcategory_label"] = str(it.get("subcategory") or "").strip()
    s["subcategory_description"] = str(
        it.get("subcategory_description") or ""
    ).strip()
    out.append(s)
  if not out:
    raise LlmError("synthesizer returned no samples for the brief")
  return out
