"""Connected apps: registered MCP servers as sample data sources.

Mirrors Gemini's Connected Apps flow: register a server-side MCP server,
snapshot
its tools, let the person fill a tool's arguments in an A2UI form generated from
the tool's input schema (under the project's constitution), call the tool, and
import the result as a Sample that remembers the form for the run's form stage.
"""

from __future__ import annotations

import time
from typing import Any

from core import settings as settings_mod
from core import util
from core.a2ui import catalog as catalog_mod
from core.service import ApiError
from llm.gemini import LlmError
from mcp import forms
from mcp.client import McpClient, McpError, result_payload, tool_summary
from sim import generator

DEMO_URL = "/mcp/demo"  # resolved against the server's own base URL
MASK = "••••"
SIZE_STEPS = ((1, "xs"), (3, "s"), (8, "m"), (20, "l"))


def mask_headers(headers: dict[str, str] | None) -> dict[str, str]:
  out = {}
  for k, v in (headers or {}).items():
    v = str(v or "")
    if not v:
      continue
    if k.lower() in ("authorization", "proxy-authorization") and " " in v:
      scheme = v.split(" ", 1)[0]
      out[k] = f"{scheme} {MASK}"
    else:
      out[k] = MASK
  return out


def _longest_list(v: Any, depth: int = 0) -> int:
  if depth > 4:
    return 0
  if isinstance(v, list):
    return max([len(v)] + [_longest_list(x, depth + 1) for x in v[:20]])
  if isinstance(v, dict):
    return max([0] + [_longest_list(x, depth + 1) for x in v.values()])
  return 0


def shape_of(payload: Any, is_error: bool) -> tuple[str, str]:
  """(size_class, state) from the payload's structure."""
  if is_error:
    return "xs", "error"
  n = _longest_list(payload)
  if (
      isinstance(payload, dict)
      and any(isinstance(v, list) for v in payload.values())
      and n == 0
  ):
    return "xs", "empty"
  for limit, label in SIZE_STEPS:
    if n <= limit:
      return label, "content"
  return "xl", "content"


class ConnectedAppManager:
  """CRUD + MCP calls for a user's connected apps."""

  def __init__(self, svc, runs):
    self.svc = svc
    self.runs = runs
    self.store = svc.store

  # -------------------------------------------------------------- helpers
  def _url(self, app: dict[str, Any]) -> str:
    if app.get("kind") == "demo":
      return self.runs.base_url.rstrip("/") + DEMO_URL
    return app["url"]

  def _client(self, app: dict[str, Any]) -> McpClient:
    return McpClient(self._url(app), app.get("headers") or {}, timeout_s=30)

  def _row(self, user: dict[str, Any], aid: str) -> dict[str, Any]:
    a = self.store.get("connected_apps", aid)
    if not a:
      raise ApiError(404, "Connected app not found")
    if a["owner_ldap"] != user["ldap"] and not user["is_admin"]:
      raise ApiError(403, "Not your connected app")
    return a

  def fmt(self, a: dict[str, Any]) -> dict[str, Any]:
    tools = a.get("tools") or []
    return {
        "id": a["id"],
        "name": a["name"],
        "description": a.get("description") or "",
        "url": DEMO_URL if a.get("kind") == "demo" else a["url"],
        "kind": a.get("kind") or "remote",
        "status": a.get("status") or "error",
        "last_error": a.get("last_error"),
        "last_sync_at": a.get("last_sync_at"),
        "server_info": a.get("server_info") or {},
        "tool_count": len(tools),
        "tools": tools,
        "resources": a.get("resources") or [],
        "headers_masked": mask_headers(a.get("headers")),
        "owner_ldap": a["owner_ldap"],
        "created_at": a.get("created_at"),
        "updated_at": a.get("updated_at"),
    }

  @staticmethod
  def _clean_headers(raw: Any) -> dict[str, str]:
    if raw in (None, ""):
      return {}
    if not isinstance(raw, dict):
      raise ApiError(400, "headers must be an object of name → value")
    out = {}
    for k, v in raw.items():
      k = str(k).strip()
      if not k or v in (None, ""):
        continue
      if any(c in k for c in "\r\n:") or any(c in str(v) for c in "\r\n"):
        raise ApiError(400, f"Invalid header {k!r}")
      out[k] = str(v)
    return out

  # ----------------------------------------------------------------- CRUD
  def list(self, user: dict[str, Any]) -> list[dict[str, Any]]:
    rows = self.store.query(
        "SELECT * FROM connected_apps WHERE owner_ldap=? ORDER BY created_at",
        [user["ldap"]],
    )
    return [self.fmt(a) for a in rows]

  def get(self, user: dict[str, Any], aid: str) -> dict[str, Any]:
    return self.fmt(self._row(user, aid))

  def create(self, user: dict[str, Any], body: dict[str, Any]) -> dict:
    name = str(body.get("name") or "").strip()
    url = str(body.get("url") or "").strip()
    if not name:
      raise ApiError(400, "Name is required")
    if not url.lower().startswith(("http://", "https://")):
      raise ApiError(400, "URL must start with http:// or https://")
    now = util.now_iso()
    aid = util.new_id("app")
    self.store.insert(
        "connected_apps",
        {
            "id": aid,
            "owner_ldap": user["ldap"],
            "name": name[:120],
            "description": str(body.get("description") or "")[:2000],
            "url": url,
            "headers": self._clean_headers(body.get("headers")),
            "kind": "remote",
            "server_info": {},
            "tools": [],
            "resources": [],
            "status": "new",
            "last_error": None,
            "last_sync_at": None,
            "created_at": now,
            "updated_at": now,
        },
    )
    return self.sync(user, aid)

  def create_demo(self, user: dict[str, Any]) -> dict[str, Any]:
    row = self.store.query(
        "SELECT id FROM connected_apps WHERE owner_ldap=? AND kind='demo'",
        [user["ldap"]],
    )
    if row:
      return self.sync(user, row[0]["id"])
    now = util.now_iso()
    aid = util.new_id("app")
    self.store.insert(
        "connected_apps",
        {
            "id": aid,
            "owner_ldap": user["ldap"],
            "name": "Demo apps (pharmacy, calendar, shop)",
            "description": (
                "Bundled demo MCP server with deterministic fake data."
                " Arguments steer list sizes and empty/error states."
            ),
            "url": DEMO_URL,
            "headers": {},
            "kind": "demo",
            "server_info": {},
            "tools": [],
            "resources": [],
            "status": "new",
            "last_error": None,
            "last_sync_at": None,
            "created_at": now,
            "updated_at": now,
        },
    )
    return self.sync(user, aid)

  def update(
      self, user: dict[str, Any], aid: str, body: dict[str, Any]
  ) -> dict:
    a = self._row(user, aid)
    upd: dict[str, Any] = {}
    if "name" in body:
      if not str(body["name"] or "").strip():
        raise ApiError(400, "Name is required")
      upd["name"] = str(body["name"]).strip()[:120]
    if "description" in body:
      upd["description"] = str(body["description"] or "")[:2000]
    if "url" in body and a.get("kind") != "demo":
      url = str(body["url"] or "").strip()
      if not url.lower().startswith(("http://", "https://")):
        raise ApiError(400, "URL must start with http:// or https://")
      upd["url"] = url
    resync = "url" in upd
    if "headers" in body:
      new = self._clean_headers(body["headers"])
      old = a.get("headers") or {}
      masked = mask_headers(old)
      merged = {}
      for k, v in new.items():
        merged[k] = old[k] if k in old and v == masked.get(k) else v
      upd["headers"] = merged
      resync = True
    if upd:
      upd["updated_at"] = util.now_iso()
      self.store.update("connected_apps", aid, upd)
    return self.sync(user, aid) if resync else self.get(user, aid)

  def delete(self, user: dict[str, Any], aid: str) -> None:
    self._row(user, aid)
    self.store.delete("connected_apps", aid)

  def sync(self, user: dict[str, Any], aid: str) -> dict[str, Any]:
    a = self._row(user, aid)
    try:
      c = self._client(a)
      info = c.initialize()
      tools = [tool_summary(t) for t in c.list_tools()]
      try:
        resources = c.list_resources()
      except McpError:
        resources = []
      upd = {
          "server_info": {
              "name": info.get("name"),
              "version": info.get("version"),
              "protocol_version": c.protocol_version,
          },
          "tools": tools,
          "resources": [
              {
                  "uri": r.get("uri"),
                  "name": r.get("name"),
                  "description": r.get("description") or "",
                  "mime_type": r.get("mimeType"),
              }
              for r in resources
          ],
          "status": "ok",
          "last_error": None,
      }
    except McpError as e:
      upd = {"status": "error", "last_error": str(e)[:800]}
    upd["last_sync_at"] = util.now_iso()
    self.store.update("connected_apps", aid, upd)
    return self.get(user, aid)

  def _tool(self, a: dict[str, Any], name: str) -> dict[str, Any]:
    for t in a.get("tools") or []:
      if t.get("name") == name:
        return t
    raise ApiError(404, f"Tool {name!r} not found; try Sync")

  # ---------------------------------------------------------------- forms
  def form(self, user: dict[str, Any], aid: str, body: dict[str, Any]) -> dict:
    a = self._row(user, aid)
    tool = self._tool(a, str(body.get("tool") or ""))
    prefill = body.get("prefill") or {}
    if not isinstance(prefill, dict):
      raise ApiError(400, "prefill must be an object")
    mode = body.get("mode") or "llm"
    simple = forms.schema_form(tool, prefill, a["name"])
    if mode == "schema":
      return {
          **simple,
          "validation": {"ok": True, "errors": []},
          "model": None,
          "latency_ms": 0,
          "mode": "schema",
          "fallback_reason": None,
      }
    pid = body.get("project_id") or None
    constitution = ""
    s = settings_mod.merged({})
    if pid:
      p = self.svc._project_row(pid, user)  # pylint: disable=protected-access
      s = settings_mod.merged(p["settings"])
      constitution = (self.svc.current_revision(pid) or {}).get("text_md") or ""
    cat = catalog_mod.build_catalog(s["catalog"])
    vp = body.get("viewport") or "phone"
    if vp not in generator.VIEWPORTS:
      vp = "phone"
    ctx = generator.GenerationContext(
        constitution_md=constitution,
        catalog=cat,
        viewport=vp,
        framing=s["generator"].get("framing", "card"),
        model=s["generator"].get("model"),
        temperature=float(s["generator"].get("temperature", 0.7)),
        max_repairs=int(s["generator"].get("max_repairs", 1)),
        stage="form",
    )
    spec = forms.form_spec(
        tool, forms.default_values(tool["input_schema"], prefill)
    )
    spec["description"] = tool.get("description") or ""
    reason = None
    try:
      res = generator.generate(
          self.svc.llm,
          ctx,
          {},
          user=generator.form_user_prompt(spec, a["name"]),
      )
      if not generator.has_submit_action(res.messages):
        reason = "the generated form had no submit button"
      else:
        return {
            "messages": res.messages,
            "validation": res.validation.to_dict(),
            "form": spec,
            "model": res.model,
            "latency_ms": res.latency_ms,
            "mode": "llm",
            "fallback_reason": None,
        }
    except LlmError as e:
      reason = f"form generation failed: {e}"[:300]
    return {
        **simple,
        "validation": {"ok": True, "errors": []},
        "model": None,
        "latency_ms": 0,
        "mode": "schema",
        "fallback_reason": reason,
    }

  # ---------------------------------------------------------------- calls
  def _call(self, a: dict[str, Any], tool: dict[str, Any], args: Any) -> dict:
    if not isinstance(args, dict):
      raise ApiError(400, "args must be an object")
    schema = tool.get("input_schema") or {}
    if schema.get("properties"):
      coerced = forms.coerce_args(schema, args)
      extra = {k: v for k, v in args.items() if k not in schema["properties"]}
      args = {**extra, **coerced}
    t0 = time.time()
    try:
      res = self._client(a).call_tool(tool["name"], args)
    except McpError as e:
      raise ApiError(502, f"{a['name']}: {e}") from e
    return {
        "result": res,
        "args": args,
        "latency_ms": int((time.time() - t0) * 1000),
    }

  def call(self, user: dict[str, Any], aid: str, body: dict[str, Any]) -> dict:
    a = self._row(user, aid)
    tool = self._tool(a, str(body.get("tool") or ""))
    return self._call(a, tool, body.get("args") or {})

  def import_result(
      self, user: dict[str, Any], aid: str, body: dict[str, Any]
  ) -> dict[str, Any]:
    a = self._row(user, aid)
    tool = self._tool(a, str(body.get("tool") or ""))
    args = body.get("args") or {}
    result = body.get("result")
    latency = None
    if not isinstance(result, dict) or not (
        "structured" in result or "text" in result
    ):
      called = self._call(a, tool, args)
      result, args, latency = (
          called["result"],
          called["args"],
          called["latency_ms"],
      )
    payload = result_payload(result)
    is_error = bool(result.get("is_error"))
    size, state = shape_of(payload, is_error)
    tool_title = tool.get("title") or tool["name"]
    arg_bits = ", ".join(
        f"{v}" for v in list(args.values())[:2] if v not in (None, "", [])
    )
    title = str(body.get("title") or "").strip() or (
        f"{tool_title}: {arg_bits}" if arg_bits else tool_title
    )
    spec = forms.form_spec(tool, args)
    spec["description"] = tool.get("description") or ""
    sample = self.svc.create_sample(
        user,
        {
            "group_label": a["name"],
            "genre_label": tool_title,
            "genre_description": tool.get("description") or "",
            "title": title[:200],
            "intent": str(body.get("intent") or tool.get("description") or ""),
            "context": {
                "surface": "connected app result",
                "user_state": "",
                "role": "",
                "locale": "en-US",
                "time_of_day": "",
                "urgency": "",
            },
            "payload": payload,
            "size_class": size,
            "state": state,
            "edge_cases": ["tool returned an error"] if is_error else [],
            "tags": ["mcp", a["name"], tool["name"]],
            "meta": {
                "connected_app": {
                    "app_id": a["id"],
                    "app_name": a["name"],
                    "tool": tool["name"],
                    "tool_title": tool_title,
                    "args": args,
                    "called_at": util.now_iso(),
                    "latency_ms": latency,
                    "is_error": is_error,
                },
                "form": spec,
            },
        },
        "mcp",
    )
    set_id = body.get("set_id")
    if set_id:
      st = self.store.get("sample_sets", set_id)
      if st:
        self.svc.update_set(
            user,
            set_id,
            {"sample_ids": list(st.get("sample_ids") or []) + [sample["id"]]},
        )
    return sample
