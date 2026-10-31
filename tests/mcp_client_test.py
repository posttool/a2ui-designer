"""MCP client against the bundled demo server, and the schema form builder."""

import http.server
import json
import os
import sys
import threading
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG not in sys.path:
  sys.path.insert(0, PKG)

from core import settings as settings_mod  # noqa: E402
from core.a2ui import catalog as catalog_mod  # noqa: E402
from core.a2ui import validator  # noqa: E402
from mcp import demo_server, forms  # noqa: E402
from mcp.client import McpClient, McpError, tool_summary  # noqa: E402
from sim.connected_apps import mask_headers, shape_of  # noqa: E402


class _Handler(http.server.BaseHTTPRequestHandler):
  seen_headers: list = []

  def do_POST(self):  # pylint: disable=invalid-name
    _Handler.seen_headers.append(dict(self.headers))
    n = int(self.headers.get("Content-Length") or 0)
    body = json.loads(self.rfile.read(n) or b"null")
    if (
        "auth" in self.path
        and self.headers.get("Authorization") != "Bearer s3cret"
    ):
      self.send_response(401)
      self.end_headers()
      return
    status, data, ctype, hdrs = demo_server.handle_http(
        body, sse="sse" in self.path
    )
    self.send_response(status)
    self.send_header("Content-Type", ctype)
    for k, v in hdrs.items():
      self.send_header(k, v)
    self.end_headers()
    self.wfile.write(data)

  def log_message(self, *args):
    pass


class McpClientTest(unittest.TestCase):

  @classmethod
  def setUpClass(cls):
    cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
    cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

  @classmethod
  def tearDownClass(cls):
    cls.srv.shutdown()

  def test_json_and_sse_transports(self):
    for path in ("/mcp", "/mcp?sse=1"):
      c = McpClient(self.base + path)
      tools = c.list_tools()  # two pages via nextCursor
      self.assertEqual(len(tools), len(demo_server.TOOLS))
      self.assertEqual(c.server_info["name"], "dcb-demo-apps")
      self.assertEqual(c.session_id, "demo-session")
      r = c.call_tool(
          "pharmacy_search_medications", {"query": "ibuprofen", "limit": 2}
      )
      self.assertFalse(r["is_error"])
      self.assertEqual(len(r["structured"]["results"]), 2)
      self.assertEqual(len(c.list_resources()), 1)

  def test_session_header_and_tool_errors(self):
    _Handler.seen_headers.clear()
    c = McpClient(self.base + "/mcp")
    r = c.call_tool("pharmacy_get_prescription", {"prescription_id": "RX-0000"})
    self.assertTrue(r["is_error"])
    self.assertIn("No prescription", r["text"])
    self.assertEqual(
        _Handler.seen_headers[-1].get("Mcp-Session-Id"), "demo-session"
    )
    with self.assertRaises(McpError) as cm:
      c.call_tool("nope", {})
    self.assertEqual(cm.exception.code, -32602)

  def test_auth_headers(self):
    with self.assertRaises(McpError) as cm:
      McpClient(self.base + "/mcp/auth").list_tools()
    self.assertEqual(cm.exception.code, 401)
    tools = McpClient(
        self.base + "/mcp/auth", {"Authorization": "Bearer s3cret"}
    ).list_tools()
    self.assertTrue(tools)

  def test_sends_user_agent(self):
    _Handler.seen_headers.clear()
    McpClient(self.base + "/mcp").list_tools()
    ua = _Handler.seen_headers[-1].get("User-Agent", "")
    self.assertTrue(ua.startswith("design-constitution-builder/"), ua)

  def test_rejects_non_http_urls(self):
    with self.assertRaises(McpError):
      McpClient("file:///etc/passwd")


class FormsTest(unittest.TestCase):

  def test_schema_forms_validate_and_submit(self):
    cat = catalog_mod.build_catalog(settings_mod.merged({})["catalog"])
    for t in demo_server.TOOLS:
      tool = tool_summary(t)
      out = forms.schema_form(tool, {}, "Demo")
      res = validator.validate(out["messages"], cat)
      self.assertTrue(res.ok, (t["name"], res.summary(5)))
      buttons = [
          c
          for m in out["messages"]
          for c in (m.get("updateComponents") or {}).get("components") or []
          if c.get("component") == "Button"
      ]
      self.assertEqual(buttons[0]["action"]["event"]["name"], "submit")
      self.assertEqual(out["form"]["data_root"], "/form")

  def test_coerce_args(self):
    schema = tool_summary(demo_server.TOOLS[3])["input_schema"]  # find_slots
    args = forms.coerce_args(
        schema,
        {
            "start_date": "2026-10-19",
            "end_date": "2026-10-21",
            "duration_min": ["45"],
            "attendees": "a@x.com, b@x.com",
        },
    )
    self.assertEqual(args["duration_min"], 45)
    self.assertEqual(args["attendees"], ["a@x.com", "b@x.com"])

  def test_shape_and_masking(self):
    self.assertEqual(shape_of({"results": []}, False), ("xs", "empty"))
    self.assertEqual(
        shape_of({"results": list(range(12))}, False), ("l", "content")
    )
    self.assertEqual(shape_of({"text": "x"}, True), ("xs", "error"))
    self.assertEqual(
        mask_headers({"Authorization": "Bearer abc", "X-Key": "k"}),
        {"Authorization": "Bearer ••••", "X-Key": "••••"},
    )


if __name__ == "__main__":
  unittest.main()
