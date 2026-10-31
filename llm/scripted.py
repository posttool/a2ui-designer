"""ScriptedLlm: deterministic test double implementing the Llm protocol."""

from __future__ import annotations

import collections
import json
from typing import Any, Callable, Sequence

from llm.gemini import Image, LlmError, LlmImageResponse, LlmResponse


class ScriptedLlm:
  """Returns queued responses per purpose, or calls a handler.

  push("generate", {...}) queues a JSON-able response for that purpose.
  handlers["judge"] = fn(system, parts) -> data lets tests compute replies.
  """

  def __init__(self):
    self.queues: dict[str, collections.deque] = collections.defaultdict(
        collections.deque
    )
    self.handlers: dict[str, Callable[[str, Sequence[Any]], Any]] = {}
    self.calls: list[dict[str, Any]] = []

  def push(self, purpose: str, data: Any) -> None:
    self.queues[purpose].append(data)

  def generate(
      self,
      *,
      purpose: str,
      system: str,
      parts: Sequence[Any],
      json_schema: dict[str, Any] | None = None,
      json_mode: bool = False,
      temperature: float = 0.7,
      model: str | None = None,
      timeout_s: float = 180.0,
  ) -> LlmResponse:
    del temperature, timeout_s
    self.calls.append({
        "purpose": purpose,
        "system": system,
        "parts": parts,
        "json_schema": json_schema,
        "model": model,
    })
    if self.queues[purpose]:
      data = self.queues[purpose].popleft()
    elif purpose in self.handlers:
      data = self.handlers[purpose](system, parts)
    else:
      raise LlmError(f"ScriptedLlm: nothing scripted for '{purpose}'")
    if isinstance(data, Exception):
      raise data
    text = data if isinstance(data, str) else json.dumps(data)
    parsed = data if not isinstance(data, str) else None
    if parsed is None and (json_schema is not None or json_mode):
      try:
        parsed = json.loads(text)
      except ValueError:
        parsed = None
    return LlmResponse(
        text=text,
        data=parsed,
        model=model or "scripted",
        latency_ms=1,
        usage={},
    )

  def generate_image(
      self,
      *,
      prompt: str,
      aspect_ratio: str = "1:1",
      reference_images: Sequence[Image] = (),
      model: str | None = None,
      timeout_s: float = 120.0,
  ) -> LlmImageResponse:
    del timeout_s
    self.calls.append({
        "purpose": "generate_image",
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "reference_images": list(reference_images or ()),
        "model": model,
    })
    if self.queues["generate_image"]:
      data = self.queues["generate_image"].popleft()
    elif "generate_image" in self.handlers:
      data = self.handlers["generate_image"](
          prompt, list(reference_images or ())
      )
    else:
      raise LlmError("ScriptedLlm: nothing scripted for 'generate_image'")
    if isinstance(data, Exception):
      raise data
    if isinstance(data, tuple) and len(data) == 2:
      raw, mime = data
    elif isinstance(data, bytes):
      raw, mime = data, "image/png"
    else:
      raise LlmError(
          "ScriptedLlm: generate_image expects bytes or (bytes, mime)"
      )
    return LlmImageResponse(
        data=raw,
        mime_type=mime,
        model=model or "scripted-image",
        latency_ms=1,
        usage={},
    )
