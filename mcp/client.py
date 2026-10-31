"""Stdlib MCP client for the Streamable HTTP transport (JSON-RPC 2.0).

Talks to server-side MCP servers the way Gemini's Connected Apps platform does:
`initialize` → `notifications/initialized` → `tools/list` / `tools/call` /
`resources/list` / `resources/read`. Responses may come back as plain JSON or
as a `text/event-stream`; both are handled. The `Mcp-Session-Id` header from
`initialize` is echoed on later requests.
"""

from __future__ import annotations

import itertools
import json
import threading
from typing import Any
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "design-constitution-builder", "version": "0.1"}
# Cloudflare-fronted servers reject Python's default "Python-urllib" agent (403).
USER_AGENT = "design-constitution-builder/0.1 (MCP client)"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class McpError(RuntimeError):
  """A transport, protocol or JSON-RPC error from an MCP server."""

  def __init__(self, message: str, code: int | None = None):
    super().__init__(message)
    self.code = code


class McpClient:
  """One logical session with an MCP server. Thread-safe for sequential use."""

  def __init__(
      self,
      url: str,
      headers: dict[str, str] | None = None,
      timeout_s: float = 30.0,
  ):
    if not url or not url.lower().startswith(("http://", "https://")):
      raise McpError("MCP server URL must start with http:// or https://")
    self.url = url
    self.headers = {
        str(k): str(v) for k, v in (headers or {}).items() if k and v
    }
    self.timeout_s = timeout_s
    self.session_id: str | None = None
    self.server_info: dict[str, Any] = {}
    self.capabilities: dict[str, Any] = {}
    self.protocol_version: str | None = None
    self._ids = itertools.count(1)
    self._lock = threading.Lock()
    self._initialized = False

  # ----------------------------------------------------------- transport
  def _post(self, payload: dict[str, Any]) -> tuple[Any, dict[str, str]]:
    body = json.dumps(payload).encode("utf-8")
    hdrs = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": USER_AGENT,
        **self.headers,
    }
    if self.session_id:
      hdrs["Mcp-Session-Id"] = self.session_id
    if self.protocol_version:
      hdrs["MCP-Protocol-Version"] = self.protocol_version
    req = urllib.request.Request(
        self.url, data=body, headers=hdrs, method="POST"
    )
    try:
      with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
        raw = r.read(MAX_RESPONSE_BYTES + 1)
        resp_headers = {k.lower(): v for k, v in r.headers.items()}
        status = r.status
    except urllib.error.HTTPError as e:
      detail = ""
      try:
        detail = e.read().decode("utf-8", "replace")[:300]
      except OSError:
        pass
      if e.code == 401:
        raise McpError(
            "HTTP 401: the server requires authentication. Add an"
            " Authorization (or API key) header. "
            + detail,
            e.code,
        ) from e
      if e.code == 403:
        raise McpError(
            "HTTP 403: the server refused the request (bad credentials, or"
            " blocked by the host). "
            + detail,
            e.code,
        ) from e
      raise McpError(f"HTTP {e.code} {detail}".strip(), e.code) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
      raise McpError(f"Could not reach the MCP server: {e}") from e
    if len(raw) > MAX_RESPONSE_BYTES:
      raise McpError("MCP response is too large (> 8 MB)")
    if status == 202 or not raw.strip():
      return None, resp_headers
    ctype = resp_headers.get("content-type", "")
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" in ctype:
      return self._parse_sse(text, payload.get("id")), resp_headers
    try:
      return json.loads(text), resp_headers
    except ValueError as e:
      raise McpError(f"MCP server returned non-JSON: {text[:200]}") from e

  @staticmethod
  def _parse_sse(text: str, want_id: Any) -> Any:
    """Returns the JSON-RPC message with id == want_id from an SSE body."""
    found = None
    data_lines: list[str] = []

    def flush():
      nonlocal found, data_lines
      if not data_lines:
        return
      chunk = "\n".join(data_lines)
      data_lines = []
      try:
        msg = json.loads(chunk)
      except ValueError:
        return
      msgs = msg if isinstance(msg, list) else [msg]
      for m in msgs:
        if (
            isinstance(m, dict)
            and m.get("id") == want_id
            and ("result" in m or "error" in m)
        ):
          found = m

    for line in text.splitlines():
      if not line.strip():
        flush()
      elif line.startswith("data:"):
        data_lines.append(line[5:].lstrip())
    flush()
    if found is None:
      raise McpError("MCP event stream ended without a response")
    return found

  def _request(self, method: str, params: dict[str, Any] | None = None) -> Any:
    rid = next(self._ids)
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
      msg["params"] = params
    data, hdrs = self._post(msg)
    if method == "initialize" and hdrs.get("mcp-session-id"):
      self.session_id = hdrs["mcp-session-id"]
    if isinstance(data, list):
      data = next((m for m in data if m.get("id") == rid), None)
    if not isinstance(data, dict):
      raise McpError(f"{method}: empty response")
    if data.get("error"):
      err = data["error"]
      raise McpError(
          f"{method}: {err.get('message', 'error')}", err.get("code")
      )
    return data.get("result")

  def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
      msg["params"] = params
    try:
      self._post(msg)
    except McpError:
      pass  # notifications are fire-and-forget

  # --------------------------------------------------------------- API
  def initialize(self) -> dict[str, Any]:
    with self._lock:
      if self._initialized:
        return self.server_info
      res = (
          self._request(
              "initialize",
              {
                  "protocolVersion": PROTOCOL_VERSION,
                  "capabilities": {},
                  "clientInfo": CLIENT_INFO,
              },
          )
          or {}
      )
      self.server_info = res.get("serverInfo") or {}
      self.capabilities = res.get("capabilities") or {}
      self.protocol_version = res.get("protocolVersion") or PROTOCOL_VERSION
      self._notify("notifications/initialized")
      self._initialized = True
      return self.server_info

  def _paged(self, method: str, key: str, limit: int = 500) -> list[dict]:
    self.initialize()
    out: list[dict] = []
    cursor = None
    for _ in range(50):
      res = self._request(method, {"cursor": cursor} if cursor else {}) or {}
      out.extend(res.get(key) or [])
      cursor = res.get("nextCursor")
      if not cursor or len(out) >= limit:
        break
    return out[:limit]

  def list_tools(self) -> list[dict[str, Any]]:
    return self._paged("tools/list", "tools")

  def list_resources(self) -> list[dict[str, Any]]:
    if "resources" not in self.capabilities and self._initialized:
      return []
    try:
      return self._paged("resources/list", "resources")
    except McpError as e:
      if e.code == -32601:  # method not found
        return []
      raise

  def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    self.initialize()
    res = (
        self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        or {}
    )
    return normalize_tool_result(res)

  def read_resource(self, uri: str) -> dict[str, Any]:
    self.initialize()
    return self._request("resources/read", {"uri": uri}) or {}


def normalize_tool_result(res: dict[str, Any]) -> dict[str, Any]:
  """MCP CallToolResult → {structured, text, is_error, content}."""
  content = res.get("content") or []
  texts = [
      c.get("text", "")
      for c in content
      if isinstance(c, dict) and c.get("type") == "text"
  ]
  text = "\n".join(t for t in texts if t)
  structured = res.get("structuredContent")
  if structured is None and len(texts) == 1:
    try:
      parsed = json.loads(texts[0])
      if isinstance(parsed, (dict, list)):
        structured = parsed
    except ValueError:
      pass
  return {
      "structured": structured,
      "text": text,
      "is_error": bool(res.get("isError")),
      "content": content,
  }


def result_payload(result: dict[str, Any]) -> Any:
  """The value imported as a sample payload."""
  if result.get("structured") is not None:
    return result["structured"]
  return {"text": result.get("text") or ""}


def tool_summary(tool: dict[str, Any]) -> dict[str, Any]:
  return {
      "name": tool.get("name"),
      "title": (
          tool.get("title")
          or (tool.get("annotations") or {}).get("title")
          or ""
      ),
      "description": tool.get("description") or "",
      "input_schema": (
          tool.get("inputSchema") or {"type": "object", "properties": {}}
      ),
      "output_schema": tool.get("outputSchema"),
  }
