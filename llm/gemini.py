"""Stdlib Gemini REST client (generativelanguage.googleapis.com).

- Key: $GEMINI_API_KEY, then ~/.config/a2ui_designer/gemini_api_key.
- Retries 429/500/503 with jittered backoff, then falls back to other models.
- Thought parts are dropped; `thinkingLevel` is set for 3.x models.
- Parts: str (text) or Image(bytes, mime_type) (inline data).
"""

from __future__ import annotations

import base64
import dataclasses
import json
import os
import random
import threading
import time
from typing import Any, Protocol, Sequence
import urllib.error
import urllib.request

from core.a2ui import normalize

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_FALLBACKS = ("gemini-3.5-flash", "gemini-flash-latest")
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
DEFAULT_IMAGE_FALLBACKS = (
    "gemini-3.1-flash-image-preview",
    "gemini-2.5-flash-image",
)
KEY_PATHS = ("~/.config/a2ui_designer/gemini_api_key",)


class LlmError(RuntimeError):
  pass


@dataclasses.dataclass
class Image:
  data: bytes
  mime_type: str = "image/png"


@dataclasses.dataclass
class LlmResponse:
  text: str
  data: Any
  model: str
  latency_ms: int
  usage: dict[str, Any]


@dataclasses.dataclass
class LlmImageResponse:
  data: bytes
  mime_type: str
  model: str
  latency_ms: int
  usage: dict[str, Any]


class Llm(Protocol):

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
    ...

  def generate_image(
      self,
      *,
      prompt: str,
      aspect_ratio: str = "1:1",
      reference_images: Sequence[Image] = (),
      model: str | None = None,
      timeout_s: float = 120.0,
  ) -> LlmImageResponse:
    ...


def load_api_key() -> str:
  k = os.environ.get("GEMINI_API_KEY", "").strip()
  if k:
    return k
  for p in KEY_PATHS:
    fp = os.path.expanduser(p)
    if os.path.exists(fp):
      with open(fp, encoding="utf-8") as fh:
        k = fh.read().strip()
      if k:
        return k
  raise LlmError(
      "No Gemini API key: set GEMINI_API_KEY or write "
      "~/.config/a2ui_designer/gemini_api_key"
  )


def _part(p: Any) -> dict[str, Any]:
  if isinstance(p, Image):
    return {
        "inlineData": {
            "mimeType": p.mime_type,
            "data": base64.b64encode(p.data).decode("ascii"),
        }
    }
  if isinstance(p, dict):
    return p
  return {"text": str(p)}


class GeminiClient:
  """Thread-safe; one instance shared by the server."""

  def __init__(
      self,
      api_key: str | None = None,
      default_model: str = DEFAULT_MODEL,
      fallbacks: Sequence[str] = DEFAULT_FALLBACKS,
      default_image_model: str = DEFAULT_IMAGE_MODEL,
      image_fallbacks: Sequence[str] = DEFAULT_IMAGE_FALLBACKS,
      max_attempts: int = 4,
      max_concurrency: int = 6,
  ):
    self._key = api_key
    self.default_model = default_model
    self.fallbacks = tuple(fallbacks)
    self.default_image_model = default_image_model
    self.image_fallbacks = tuple(image_fallbacks)
    self.max_attempts = max_attempts
    self._sem = threading.BoundedSemaphore(max_concurrency)
    self.calls = 0

  @property
  def key(self) -> str:
    if not self._key:
      self._key = load_api_key()
    return self._key

  def _post(self, model: str, body: dict[str, Any], timeout_s: float) -> dict:
    req = urllib.request.Request(
        API.format(model=model) + "?key=" + self.key,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
      return json.loads(r.read().decode("utf-8"))

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
    del purpose
    gen: dict[str, Any] = {"temperature": temperature}
    if json_schema is not None:
      gen["responseMimeType"] = "application/json"
      gen["responseJsonSchema"] = json_schema
    elif json_mode:
      gen["responseMimeType"] = "application/json"
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [_part(p) for p in parts]}],
        "generationConfig": gen,
    }
    if system:
      body["systemInstruction"] = {"parts": [{"text": system}]}
    models = [model or self.default_model] + [
        m for m in self.fallbacks if m != (model or self.default_model)
    ]
    last_err = "unknown error"
    t0 = time.time()
    with self._sem:
      for mi, m in enumerate(models):
        b = json.loads(json.dumps(body))
        if m.startswith("gemini-3"):
          b["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "low"}
        attempts = self.max_attempts if mi == 0 else 2
        for attempt in range(attempts):
          try:
            self.calls += 1
            resp = self._post(m, b, timeout_s)
            text = _extract_text(resp)
            if not text:
              reason = (
                  (resp.get("candidates") or [{}])[0].get("finishReason")
                  or (resp.get("promptFeedback") or {}).get("blockReason")
                  or "empty response"
              )
              raise LlmError(f"{m}: {reason}")
            data = None
            if json_schema is not None or json_mode:
              data = normalize.parse_json_text(text)
              if data is None:
                raise LlmError(f"{m}: response was not valid JSON")
            return LlmResponse(
                text=text,
                data=data,
                model=m,
                latency_ms=int((time.time() - t0) * 1000),
                usage=resp.get("usageMetadata") or {},
            )
          except urllib.error.HTTPError as e:
            detail = ""
            try:
              detail = e.read().decode("utf-8", "replace")[:400]
            except OSError:
              pass
            last_err = f"{m}: HTTP {e.code} {detail}"
            if e.code in (400, 401, 403):
              if e.code == 400 and "thinking" in detail.lower():
                b["generationConfig"].pop("thinkingConfig", None)
                continue
              if (
                  e.code == 400
                  and json_schema is not None
                  and ("schema" in detail.lower())
              ):
                raise LlmError(last_err) from e
              break
            if e.code == 404:
              break
          except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = f"{m}: {e}"
          except LlmError as e:
            last_err = str(e)
          time.sleep(min(20.0, (1.6**attempt) + random.random()))
    raise LlmError(last_err)

  def generate_image(
      self,
      *,
      prompt: str,
      aspect_ratio: str = "1:1",
      reference_images: Sequence[Image] = (),
      model: str | None = None,
      timeout_s: float = 120.0,
  ) -> LlmImageResponse:
    parts: list[dict[str, Any]] = []
    for ref in reference_images or ():
      if isinstance(ref, Image) and ref.data:
        parts.append(_part(ref))
    parts.append({"text": str(prompt)})
    gen_cfg: dict[str, Any] = {
        "responseModalities": ["IMAGE", "TEXT"],
    }
    if aspect_ratio in ("1:1", "16:9", "9:16", "4:3", "3:4"):
      gen_cfg["imageConfig"] = {"aspectRatio": aspect_ratio}
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen_cfg,
    }
    primary = (model or self.default_image_model).strip()
    models = [primary] + [m for m in self.image_fallbacks if m != primary]
    last_err = "unknown image error"
    t0 = time.time()
    with self._sem:
      for mi, m in enumerate(models):
        b = json.loads(json.dumps(body))
        attempts = max(2, self.max_attempts - 1) if mi == 0 else 2
        for attempt in range(attempts):
          try:
            self.calls += 1
            resp = self._post(m, b, timeout_s)
            extracted = _extract_image(resp)
            if not extracted:
              reason = (
                  (resp.get("candidates") or [{}])[0].get("finishReason")
                  or (resp.get("promptFeedback") or {}).get("blockReason")
                  or "no inline image in response"
              )
              raise LlmError(f"{m}: {reason}")
            raw_bytes, mime = extracted
            return LlmImageResponse(
                data=raw_bytes,
                mime_type=mime,
                model=m,
                latency_ms=int((time.time() - t0) * 1000),
                usage=resp.get("usageMetadata") or {},
            )
          except urllib.error.HTTPError as e:
            detail = ""
            try:
              detail = e.read().decode("utf-8", "replace")[:400]
            except OSError:
              pass
            last_err = f"{m}: HTTP {e.code} {detail}"
            if e.code in (400, 401, 403, 404):
              if e.code == 400 and "imageconfig" in detail.lower():
                b["generationConfig"].pop("imageConfig", None)
                continue
              break
          except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = f"{m}: {e}"
          except LlmError as e:
            last_err = str(e)
          time.sleep(min(15.0, (1.5**attempt) + random.random()))
    raise LlmError(last_err)


def _extract_text(resp: dict[str, Any]) -> str:
  cands = resp.get("candidates") or []
  if not cands:
    return ""
  parts = (cands[0].get("content") or {}).get("parts") or []
  return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _extract_image(resp: dict[str, Any]) -> tuple[bytes, str] | None:
  cands = resp.get("candidates") or []
  if not cands:
    return None
  parts = (cands[0].get("content") or {}).get("parts") or []
  for part in parts:
    inline = part.get("inlineData") or part.get("inline_data") or {}
    mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
    b64 = inline.get("data")
    if b64 and mime.startswith("image/"):
      try:
        raw = base64.b64decode(b64) if isinstance(b64, str) else bytes(b64)
        if raw:
          return raw, mime
      except (ValueError, TypeError):
        continue
  return None
