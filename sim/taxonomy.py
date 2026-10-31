"""Genre taxonomy and synthesis axes for synthetic samples."""

from __future__ import annotations

import random
from typing import Any

from core import util

GROUPS: list[dict[str, Any]] = [
    {
        "id": "agentic",
        "label": "Agentic",
        "genres": [
            (
                "agent_task",
                "Agent task",
                (
                    "An agent mid-task: plan steps, tool calls, partial"
                    " results, and a decision or approval the person must make."
                ),
            ),
            (
                "long_running",
                "Long-running job",
                (
                    "Background job progress: ETA, milestones, logs, retries or"
                    " failures, pause/cancel/resume options."
                ),
            ),
            (
                "disambiguation",
                "Disambiguation",
                (
                    "The agent needs input: clarifying questions, choices among"
                    " options, a short form, confirmation or a permission"
                    " request."
                ),
            ),
        ],
    },
    {
        "id": "editorial",
        "label": "Documents & editorial",
        "genres": [
            (
                "research_report",
                "Research report",
                (
                    "A synthesized report: sections, findings, citations,"
                    " tables, figures and key takeaways."
                ),
            ),
            (
                "magazine",
                "Magazine",
                (
                    "Editorial content: hero image, headline and deck, byline,"
                    " body with pull quotes and inline images, related stories."
                ),
            ),
            (
                "website",
                "Website",
                (
                    "A landing or documentation page: navigation, hero,"
                    " features, pricing tiers, FAQ, footer links."
                ),
            ),
        ],
    },
    {
        "id": "apps",
        "label": "Apps & data",
        "genres": [
            (
                "dynamic_app",
                "Dynamic app",
                (
                    "A small full app generated on the fly (CRM, habit tracker,"
                    " inventory): views, navigation, lists, detail records and"
                    " forms."
                ),
            ),
            (
                "dashboard",
                "Dashboard",
                (
                    "Operational metrics: KPIs, deltas, time series values and"
                    " tables (there is no chart component)."
                ),
            ),
            (
                "commerce",
                "Commerce",
                (
                    "Products, menus or comparisons: options with images,"
                    " prices, variants, ratings, availability and a cart."
                ),
            ),
            (
                "itinerary",
                "Itinerary",
                (
                    "Travel or schedules: timeline of legs and events,"
                    " bookings, conflicts, locations and static map images."
                ),
            ),
        ],
    },
    {
        "id": "comms",
        "label": "Communication & media",
        "genres": [
            (
                "messaging",
                "Messaging",
                (
                    "Threads, notifications, inbox triage and summaries with"
                    " senders, timestamps, attachments and reply options."
                ),
            ),
            (
                "media",
                "Media",
                (
                    "Music, video, podcasts or photo galleries: now playing,"
                    " playlists, episodes, galleries and controls."
                ),
            ),
        ],
    },
    {
        "id": "care",
        "label": "Care & health",
        "genres": [
            (
                "elder_care",
                "Caring for an older person",
                (
                    "Coordinating care for an aging parent or relative:"
                    " medication schedule, appointments, vitals and fall"
                    " alerts, caregiver hand-offs, insurance paperwork."
                ),
            ),
            (
                "child_care",
                "Caring for children",
                (
                    "Pediatric visits, vaccination schedule, symptom and"
                    " temperature log, allergies, school or daycare forms,"
                    " pickups."
                ),
            ),
            (
                "dentistry",
                "Dentistry",
                (
                    "Dental appointment prep, treatment plan with options and"
                    " costs, X-ray images, post-procedure instructions,"
                    " coverage."
                ),
            ),
            (
                "self_care",
                "Self care",
                (
                    "Personal wellbeing: symptom check-ins, medication"
                    " reminders, sleep and activity logs, lab results, therapy"
                    " or mindfulness plans."
                ),
            ),
            (
                "chronic_condition",
                "Chronic condition",
                (
                    "Managing diabetes, hypertension or asthma: readings over"
                    " time, dosing changes, pharmacy refills, care-team"
                    " messages."
                ),
            ),
            (
                "telehealth",
                "Telehealth visit",
                (
                    "Visit prep and after-visit summary: intake questions,"
                    " consent, diagnosis summary, prescriptions, next steps,"
                    " follow-up booking."
                ),
            ),
        ],
    },
    {
        "id": "enterprise",
        "label": "Enterprise",
        "genres": [
            (
                "building_management",
                "Building management",
                (
                    "Facilities operations: work orders, HVAC and energy"
                    " alerts, occupancy, maintenance schedules, vendor"
                    " contracts, inspections, floor plans."
                ),
            ),
            (
                "event_planning",
                "Event planning",
                (
                    "Corporate events: venues, run-of-show, vendors, budget,"
                    " RSVPs and seating, AV checklist, day-of timeline and"
                    " incidents."
                ),
            ),
            (
                "it_service_desk",
                "IT service desk",
                (
                    "Incidents and requests: tickets, SLAs, approvals, change"
                    " windows, status pages and runbooks."
                ),
            ),
            (
                "people_ops",
                "People operations",
                (
                    "HR workflows: onboarding checklists, time-off requests and"
                    " approvals, reviews, org changes, policy acknowledgements."
                ),
            ),
        ],
    },
    {
        "id": "assistant",
        "label": "Personal assistant & coordination",
        "genres": [
            (
                "dinner_ordering",
                "Ordering dinner",
                (
                    "Getting dinner sorted: restaurant and dish options with"
                    " photos, prices, ratings and delivery ETAs, dietary needs"
                    " across a household or group, a cart to confirm, order"
                    " tracking, substitutions, a late or wrong order."
                ),
            ),
            (
                "vip_comms",
                "VIP communication",
                (
                    "Messages from or to the people who matter most (exec,"
                    " board member, key client, partner, family): priority"
                    " triage, what they need and by when, context from past"
                    " threads, suggested replies or a drafted response to"
                    " approve."
                ),
            ),
            (
                "scheduling",
                "Scheduling",
                (
                    "Finding and changing times: proposed slots across"
                    " calendars and time zones, conflicts and trade-offs,"
                    " reschedules with knock-on effects, holds, confirmations"
                    " and reminders."
                ),
            ),
            (
                "group_coordination",
                "Group coordination",
                (
                    "Coordinating several people toward one plan: polls and"
                    " RSVPs, who is bringing or doing what, carpools, shared"
                    " costs and splits, last-minute changes and nudges to"
                    " people who have not answered."
                ),
            ),
        ],
    },
]

GENRES: dict[str, dict[str, str]] = {}
for _g in GROUPS:
  for _gid, _label, _desc in _g["genres"]:
    GENRES[_gid] = {
        "id": _gid,
        "label": _label,
        "description": _desc,
        "group": _g["id"],
        "group_label": _g["label"],
    }

CARE_ENTERPRISE = {"care", "enterprise"}
SIZES = ["xs", "s", "m", "l", "xl"]
STATES = ["content", "content", "content", "loading", "empty", "error"]
URGENCY = ["routine", "time-sensitive", "urgent", "safety-critical"]
PARTICIPANTS = [
    "solo",
    "two people",
    "small team",
    "care team / vendors / approvers",
]
PERMISSIONS = ["viewer", "editor", "approver", "admin"]
LOCALES = [
    "en-US",
    "en-US",
    "en-GB",
    "es-MX",
    "de-DE",
    "ja-JP",
    "ar-EG",
    "pt-BR",
    "fr-FR",
    "hi-IN",
]
EDGE_CASES = [
    "very long titles and names",
    "missing images",
    "zero items in a list",
    "hundreds of items",
    "mixed currencies or units",
    "very long free-text notes",
    "stale data with a last-updated time",
    "partial data",
    "duplicate-looking entries",
    "urgent item buried in routine ones",
]
SURFACES = [
    "chat reply",
    "notification",
    "home screen card",
    "full screen",
    "side panel",
    "ambient display",
]


def groups_api(store: Any = None) -> dict[str, Any]:
  """Built-in groups/genres merged with user-created ones (when `store`)."""
  groups = [
      {
          "id": g["id"],
          "label": g["label"],
          "custom": False,
          "genres": [
              {"id": gid, "label": label, "description": desc, "custom": False}
              for gid, label, desc in g["genres"]
          ],
      }
      for g in GROUPS
  ]
  if store is None:
    return {"groups": groups}
  by_id = {g["id"]: g for g in groups}
  rows = store.query("SELECT * FROM categories ORDER BY created_at, rowid")
  for r in rows:
    if r["kind"] == "group" and r["id"] not in by_id:
      g = {
          "id": r["id"],
          "label": r["label"],
          "description": r.get("description") or "",
          "custom": True,
          "owner_ldap": r.get("owner_ldap"),
          "genres": [],
      }
      by_id[r["id"]] = g
      groups.append(g)
  for r in rows:
    if r["kind"] != "genre":
      continue
    parent = by_id.get(r.get("parent_id") or "")
    if parent is None:
      continue
    parent["genres"].append({
        "id": r["id"],
        "label": r["label"],
        "description": r.get("description") or "",
        "custom": True,
        "owner_ldap": r.get("owner_ldap"),
    })
  return {"groups": groups}


def all_genres(store: Any = None) -> dict[str, dict[str, Any]]:
  """genre id -> {id, label, description, group, group_label, custom}."""
  out: dict[str, dict[str, Any]] = {}
  for g in groups_api(store)["groups"]:
    for x in g["genres"]:
      out[x["id"]] = {
          **x,
          "group": g["id"],
          "group_label": g["label"],
      }
  return out


def slugify(label: str) -> str:
  s = "".join(c.lower() if c.isalnum() else "_" for c in label or "")
  s = "_".join(p for p in s.split("_") if p)
  return s[:48] or "category"


def _norm(label: str) -> str:
  return " ".join((label or "").lower().split())


def ensure_group(
    store: Any, owner: str, label: str, description: str = ""
) -> str:
  """Returns the id of the group with this label, creating it if new."""
  label = " ".join((label or "").split())[:80]
  if not label:
    raise ValueError("Category name is required")
  groups = groups_api(store)["groups"]
  for g in groups:
    if _norm(g["label"]) == _norm(label) or g["id"] == label:
      return g["id"]
  taken = {g["id"] for g in groups} | set(GENRES)
  base = slugify(label)
  gid, k = base, 2
  while gid in taken:
    gid, k = f"{base}_{k}", k + 1
  store.insert(
      "categories",
      {
          "id": gid,
          "kind": "group",
          "parent_id": None,
          "label": label,
          "description": (description or "")[:600],
          "owner_ldap": owner,
          "created_at": _now(),
      },
  )
  return gid


def ensure_genre(
    store: Any,
    owner: str,
    group_id: str,
    label: str,
    description: str = "",
) -> str:
  """Returns the id of `label` under `group_id`, creating it if new."""
  label = " ".join((label or "").split())[:80]
  if not label:
    raise ValueError("Subcategory name is required")
  groups = groups_api(store)["groups"]
  grp = next((g for g in groups if g["id"] == group_id), None)
  if grp is None:
    raise ValueError(f"Unknown category '{group_id}'")
  for x in grp["genres"]:
    if _norm(x["label"]) == _norm(label) or x["id"] == label:
      return x["id"]
  taken = {x["id"] for g in groups for x in g["genres"]} | {
      g["id"] for g in groups
  }
  base = slugify(label)
  gid, k = base, 2
  while gid in taken:
    gid, k = f"{base}_{k}", k + 1
  store.insert(
      "categories",
      {
          "id": gid,
          "kind": "genre",
          "parent_id": group_id,
          "label": label,
          "description": (description or "")[:600],
          "owner_ldap": owner,
          "created_at": _now(),
      },
  )
  return gid


def ensure_path(
    store: Any,
    owner: str,
    group_label: str,
    genre_label: str,
    description: str = "",
) -> tuple[str, str]:
  """(group id, genre id) for labels, creating either when new."""
  gid = ensure_group(store, owner, group_label or UNCATEGORIZED_LABEL)
  xid = ensure_genre(
      store, owner, gid, genre_label or group_label or "General", description
  )
  return gid, xid


UNCATEGORIZED_LABEL = "Uncategorized"


def _now() -> str:
  return util.now_iso()


def axis_plan(
    genre: str | dict[str, Any], n: int, seed: int | None = None
) -> list[dict[str, Any]]:
  """Spreads samples across axes so a batch covers the space."""
  rnd = random.Random(seed)
  g = GENRES[genre] if isinstance(genre, str) else genre
  sizes = SIZES[:]
  rnd.shuffle(sizes)
  plans = []
  for i in range(n):
    plan = {
        "size_class": sizes[i % len(sizes)],
        "state": "content" if i == 0 else rnd.choice(STATES),
        "locale": "en-US" if i < 2 else rnd.choice(LOCALES),
        "surface": rnd.choice(SURFACES),
        "edge_cases": rnd.sample(EDGE_CASES, k=1 if i % 2 else 2),
    }
    if g["group"] in CARE_ENTERPRISE:
      plan["urgency"] = (
          URGENCY[i % len(URGENCY)] if n >= 4 else rnd.choice(URGENCY)
      )
      plan["participants"] = rnd.choice(PARTICIPANTS)
      plan["permissions"] = rnd.choice(PERMISSIONS)
    plans.append(plan)
  return plans
