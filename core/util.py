"""Small shared helpers: ids, clock, JSON."""

from __future__ import annotations

import datetime
import json
import secrets
from typing import Any

_ALPHABET = "abcdefghijkmnopqrstuvwxyz23456789"


def new_id(prefix: str = "") -> str:
  core = "".join(secrets.choice(_ALPHABET) for _ in range(10))
  return f"{prefix}_{core}" if prefix else core


def now_iso() -> str:
  return datetime.datetime.now(datetime.timezone.utc).strftime(
      "%Y-%m-%dT%H:%M:%SZ"
  )


def dumps(value: Any) -> str:
  return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(text: Any, default: Any = None) -> Any:
  if text is None or text == "":
    return default
  if not isinstance(text, (str, bytes)):
    return text
  try:
    return json.loads(text)
  except (ValueError, TypeError):
    return default
