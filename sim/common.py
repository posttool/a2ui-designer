"""Shared helpers for sim modules: prompt loading, sample digests."""

from __future__ import annotations

import functools
import json
import os
from typing import Any

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS = os.path.join(PKG, "llm", "prompts")


@functools.lru_cache(maxsize=None)
def prompt(name: str) -> str:
  with open(os.path.join(PROMPTS, name), encoding="utf-8") as fh:
    return fh.read()


def sample_input(sample: dict[str, Any]) -> dict[str, Any]:
  """What the generator sees for a sample (no ids/owner metadata)."""
  return {
      "genre": sample.get("genre"),
      "title": sample.get("title"),
      "intent": sample.get("intent"),
      "context": sample.get("context") or {},
      "state": sample.get("state"),
      "payload": sample.get("payload"),
  }


def clip_json(value: Any, limit: int = 12000) -> str:
  s = json.dumps(value, ensure_ascii=False, indent=1)
  if len(s) > limit:
    s = s[:limit] + f"\n… (truncated, {len(s) - limit} more chars)"
  return s


def str_schema(desc: str = "") -> dict[str, Any]:
  d: dict[str, Any] = {"type": "string"}
  if desc:
    d["description"] = desc
  return d


def arr(items: dict[str, Any], desc: str = "") -> dict[str, Any]:
  d: dict[str, Any] = {"type": "array", "items": items}
  if desc:
    d["description"] = desc
  return d


def obj(
    props: dict[str, Any], required: list[str] | None = None, desc: str = ""
) -> dict[str, Any]:
  d: dict[str, Any] = {
      "type": "object",
      "properties": props,
      "required": required if required is not None else list(props),
  }
  if desc:
    d["description"] = desc
  return d
