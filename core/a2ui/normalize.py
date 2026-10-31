"""Tolerant extraction of A2UI message lists from model output."""

from __future__ import annotations

import json
import re
from typing import Any

from core.a2ui.catalog import BASIC_CATALOG_ID

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json_text(text: str) -> Any:
  """Parses JSON from raw model text (handles code fences / leading prose)."""
  t = (text or "").strip()
  if not t:
    return None
  try:
    return json.loads(t)
  except ValueError:
    pass
  m = _FENCE.search(t)
  if m:
    try:
      return json.loads(m.group(1))
    except ValueError:
      pass
  for open_c, close_c in (("{", "}"), ("[", "]")):
    a, b = t.find(open_c), t.rfind(close_c)
    if a != -1 and b > a:
      try:
        return json.loads(t[a : b + 1])
      except ValueError:
        continue
  return None


def to_messages(
    data: Any, surface_id: str = "main", catalog_id: str = BASIC_CATALOG_ID
) -> list[dict[str, Any]]:
  """Accepts {messages:[..]}, [..], or a single message; fills obvious gaps.

  Only structural fix-ups that cannot change design intent: add a missing
  "version", and add a createSurface when the model forgot it.
  """
  if isinstance(data, dict) and isinstance(data.get("messages"), list):
    msgs = data["messages"]
  elif isinstance(data, list):
    msgs = data
  elif isinstance(data, dict) and any(
      k in data for k in ("createSurface", "updateComponents")
  ):
    msgs = [data]
  else:
    return []
  out = []
  for m in msgs:
    if isinstance(m, dict):
      m = dict(m)
      m.setdefault("version", "v0.9")
      out.append(m)
  has_create = any("createSurface" in m for m in out)
  if out and not has_create:
    sid = surface_id
    for m in out:
      for k in ("updateComponents", "updateDataModel"):
        if isinstance(m.get(k), dict) and m[k].get("surfaceId"):
          sid = m[k]["surfaceId"]
          break
    out.insert(
        0,
        {
            "version": "v0.9",
            "createSurface": {"surfaceId": sid, "catalogId": catalog_id},
        },
    )
  return out
