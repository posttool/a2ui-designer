"""New-project setup helpers: scenario domains and starter-sample selection.

Used by teaching sessions (sim/teach.py). The original 3-step card-critique
wizard (baseline run → verdicts → bootstrap Revision 1) was removed on
2026-10-19; new projects start a teaching session instead.
"""

from __future__ import annotations

import json
import os
from typing import Any

from core import diffs, settings as settings_mod, util
from core.service import ApiError, Service
from llm.gemini import Image, LlmError
from sim import common, reviser, synthesizer, taxonomy
from sim.common import arr, obj, str_schema

WIZARD_DOMAINS: list[dict[str, Any]] = [
    {
        "id": "assistant",
        "label": "Personal assistant",
        "icon": "assistant",
        "description": (
            "Ordering dinner, scheduling conflicts, VIP messages & group plans."
        ),
        "examples": ["Family dinner order", "Calendar conflict"],
    },
    {
        "id": "care",
        "label": "Care & health",
        "icon": "favorite",
        "description": (
            "Elder care, pediatrics, medication safety, vitals & telehealth."
        ),
        "examples": ["Mom's morning meds & BP", "Toddler fever dosing"],
    },
    {
        "id": "agentic",
        "label": "Agent tasks & approvals",
        "icon": "smart_toy",
        "description": (
            "Mid-task approvals, expiring holds, disambiguation & background"
            " jobs."
        ),
        "examples": ["Flight rebooking hold", "Recipient disambiguation"],
    },
    {
        "id": "apps",
        "label": "Commerce & itineraries",
        "icon": "shopping_bag",
        "description": (
            "Product comparisons, carts, travel timelines & operational"
            " dashboards."
        ),
        "examples": ["Espresso machine compare", "Kyoto transit timeline"],
    },
    {
        "id": "enterprise",
        "label": "Enterprise & ops",
        "icon": "domain",
        "description": (
            "Facilities alerts, event run-of-show, IT incidents & approvals."
        ),
        "examples": ["Lab chiller alarm", "Keynote run-of-show"],
    },
    {
        "id": "editorial",
        "label": "Reports & editorial",
        "icon": "article",
        "description": (
            "Research syntheses, benchmark tables, magazine features & guides."
        ),
        "examples": ["Battery research synthesis", "Architecture walk guide"],
    },
    {
        "id": "comms",
        "label": "Messaging & media",
        "icon": "chat",
        "description": (
            "Priority inbox triage, quick replies, podcasts & media playback."
        ),
        "examples": ["Priority inbox triage", "Podcast chapters"],
    },
]

WIZARD_SELECT_SCHEMA = obj({
    "selected_sample_ids": arr(
        str_schema("ID of an existing sample from the candidate list.")
    ),
    "needs_synthesis": {
        "type": "boolean",
        "description": (
            "Set true ONLY if the designer's brief describes a specific"
            " product/domain that the existing candidate samples do not"
            " reasonably cover."
        ),
    },
    "synthesis_reason": str_schema(
        "Brief explanation of why existing samples fit or why new ones should"
        " be synthesized."
    ),
})

def _load_seed_samples() -> list[dict[str, Any]]:
  path = os.path.join(common.PKG, "seeds", "starter_samples.json")
  try:
    with open(path, encoding="utf-8") as fh:
      data = json.load(fh)
    return data if isinstance(data, list) else []
  except (OSError, ValueError):
    return []


def ensure_starter_samples(
    svc: Service,
    user: dict[str, Any],
    groups: list[str] | None = None,
) -> list[dict[str, Any]]:
  """Ensures starter samples exist in the DB for the requested groups."""
  seeds = _load_seed_samples()
  if not seeds:
    return []
  existing_rows = svc.store.query("SELECT * FROM samples")
  by_seed_id: dict[str, dict[str, Any]] = {}
  for r in existing_rows:
    meta = r.get("meta") or {}
    sid = meta.get("wizard_seed_id")
    if sid:
      by_seed_id[str(sid)] = r

  want_groups = set(groups) if groups else None
  ensured: list[dict[str, Any]] = []
  for item in seeds:
    if not isinstance(item, dict):
      continue
    grp = str(item.get("group") or "")
    if want_groups and grp not in want_groups:
      continue
    seed_id = str(item.get("seed_id") or "")
    if seed_id and seed_id in by_seed_id:
      ensured.append(svc.fmt_sample(by_seed_id[seed_id]))
      continue
    meta = dict(item.get("meta") or {})
    if seed_id:
      meta["wizard_seed_id"] = seed_id
    created = svc.create_sample(
        user,
        {
            "group": grp,
            "genre": item.get("genre") or "",
            "title": item.get("title") or "Starter sample",
            "intent": item.get("intent") or "",
            "context": item.get("context") or {},
            "payload": item.get("payload") or {},
            "size_class": item.get("size_class") or "m",
            "state": item.get("state") or "content",
            "edge_cases": item.get("edge_cases") or [],
            "tags": item.get("tags") or [],
            "meta": meta,
            "source": "seed",
        },
        source="seed",
    )
    ensured.append(created)
  return ensured


def pick_wizard_samples(
    svc: Service,
    runs: Any,
    user: dict[str, Any],
    *,
    groups: list[str] | None = None,
    brief: str = "",
    count: int = 5,
) -> list[str]:
  """Picks or synthesizes `count` diverse sample IDs for Step 1 of the wizard."""
  count = max(3, min(int(count or 5), 6))
  valid_group_ids = {d["id"] for d in WIZARD_DOMAINS}
  clean_groups = [g for g in groups or [] if g in valid_group_ids]

  # Always make sure starter samples exist for the chosen groups (or all groups).
  ensure_starter_samples(svc, user, clean_groups or None)

  all_rows = svc.store.query(
      "SELECT * FROM samples ORDER BY created_at DESC, rowid DESC"
  )
  candidates = [
      r for r in all_rows if not clean_groups or r.get("grp") in clean_groups
  ]
  if len(candidates) < count:
    ensure_starter_samples(svc, user, None)
    all_rows = svc.store.query(
        "SELECT * FROM samples ORDER BY created_at DESC, rowid DESC"
    )
    seen_cids = {r["id"] for r in candidates}
    for r in all_rows:
      if r["id"] not in seen_cids:
        candidates.append(r)
        seen_cids.add(r["id"])

  brief_clean = (brief or "").strip()
  # If the designer gave a specific brief, ask the LLM to pick the best existing
  # samples or request synthesis of custom samples for that brief.
  if brief_clean and svc.llm is not None:
    catalog_lines = [
        f"- id: {r['id']} | group: {r.get('grp')} | genre: {r.get('genre')} |"
        f" title: {r.get('title')} | intent: {r.get('intent')}"
        for r in candidates[:40]
    ]
    prompt = (
        f"Designer's project brief:\n{brief_clean}\n\n"
        f"Selected domain groups: {clean_groups or '(any)'}\n\n"
        f"Available candidate samples ({len(catalog_lines)}):\n"
        + "\n".join(catalog_lines)
        + f"\n\nSelect the {count} best-matching sample IDs from the candidate"
        " list. Set needs_synthesis=true ONLY if the designer's brief"
        " describes a specialized domain/surface not reasonably represented by"
        " the candidates."
    )
    try:
      resp = svc.llm.generate(
          purpose="wizard_select_samples",
          system=(
              "You select starter UI scenarios for a new Design Constitution"
              " project. Prefer diverse scenarios across size and urgency."
          ),
          parts=[prompt],
          json_schema=WIZARD_SELECT_SCHEMA,
          temperature=0.2,
          timeout_s=60,
      )
      data = resp.data or {}
      if data.get("needs_synthesis") and not clean_groups:
        existing_tax = taxonomy.groups_api(svc.store)
        synth_items = synthesizer.synthesize_brief(
            svc.llm, brief_clean, count, existing_tax
        )
        new_ids: list[str] = []
        for it in synth_items[:count]:
          if runs is not None and os.environ.get("A2UID_WEB_IMAGES") != "0":
            it = runs._with_web_images(it)  # pylint: disable=protected-access
          created = svc.create_sample(user, it, source="synth")
          new_ids.append(created["id"])
        if len(new_ids) >= 3:
          return new_ids[:count]
      valid_ids = {r["id"] for r in candidates}
      picked = [
          sid
          for sid in data.get("selected_sample_ids") or []
          if sid in valid_ids
      ]
      picked = list(dict.fromkeys(picked))
      if len(picked) >= count:
        return picked[:count]
    except LlmError:
      pass

  # Deterministic round-robin spread across chosen groups & genres so the user
  # gets a balanced variety of scenarios.
  by_group: dict[str, list[dict[str, Any]]] = {}
  target_groups = clean_groups or [d["id"] for d in WIZARD_DOMAINS]
  for r in candidates:
    g = r.get("grp") or "other"
    by_group.setdefault(g, []).append(r)

  picked_ids: list[str] = []
  seen_genres: set[str] = set()
  # Pass 1: one per group with distinct genres
  while len(picked_ids) < count:
    added_any = False
    for gid in target_groups:
      bucket = by_group.get(gid) or []
      chosen = next(
          (
              r
              for r in bucket
              if r["id"] not in picked_ids and r.get("genre") not in seen_genres
          ),
          None,
      )
      if chosen is None:
        chosen = next((r for r in bucket if r["id"] not in picked_ids), None)
      if chosen is not None:
        picked_ids.append(chosen["id"])
        if chosen.get("genre"):
          seen_genres.add(str(chosen["genre"]))
        added_any = True
        if len(picked_ids) >= count:
          break
    if not added_any:
      break

  for r in candidates:
    if len(picked_ids) >= count:
      break
    if r["id"] not in picked_ids:
      picked_ids.append(r["id"])

  return picked_ids[:count]
