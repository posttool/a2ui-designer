"""End-to-end: connected apps (demo MCP server) and design references."""

import base64
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402
from llm.gemini import Image  # noqa: E402


def wait(fn, timeout=15.0):
  deadline = time.time() + timeout
  while time.time() < deadline:
    v = fn()
    if v:
      return v
    time.sleep(0.05)
  raise AssertionError("timed out")


def form_handler(system, parts):
  """LLM stand-in: a form for prompts that ask for one, else GOOD_A2UI."""
  del system
  text = parts[0] if parts else ""
  if "INPUT FORM" in text:
    return {
        "messages": [
            {
                "version": "v1.0",
                "createSurface": {
                    "surfaceId": "f",
                    "catalogId": (
                        "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"
                    ),
                },
            },
            {
                "version": "v1.0",
                "updateComponents": {
                    "surfaceId": "f",
                    "components": [
                        {
                            "id": "root",
                            "component": "Column",
                            "children": ["q", "b"],
                        },
                        {
                            "id": "q",
                            "component": "TextField",
                            "label": "Medication",
                            "value": {"@path": "/form/query"},
                        },
                        {
                            "id": "b",
                            "component": "Button",
                            "child": "bl",
                            "variant": "primary",
                            "action": {
                                "event": {
                                    "name": "submit",
                                    "context": {"form": {"@path": "/form"}},
                                }
                            },
                        },
                        {"id": "bl", "component": "Text", "text": "Search"},
                    ],
                },
            },
        ]
    }
  return fixtures.GOOD_A2UI


ANALYSIS = {
    "reply_md": "Cards lead with the medication; one primary action.",
    "observations": [{
        "topic": "hierarchy",
        "text": "Each card leads with the medication name.",
        "evidence": ["img:1"],
    }],
    "proposal": {
        "has_proposal": True,
        "constitution_md": (
            "## Results\n\n- Lead each result card with its name.\n"
        ),
        "change_summary": "Add result-card rule",
        "changed_sections": ["Results"],
        "theme_changes": [
            {
                "token": "primary",
                "value": "#0B57D0",
                "is_custom": False,
                "maps_to": "",
                "reason": "Primary button",
                "confidence": "exact",
            },
            {
                "token": "radius_card",
                "value": "24",
                "is_custom": False,
                "maps_to": "",
                "reason": "Cards",
                "confidence": "approx",
            },
        ],
        "catalog_changes": {
            "enable": [],
            "disable": ["Slider"],
            "proposed_components": [{
                "name": "StatusTimeline",
                "purpose": "Refill progress",
                "maps_to_basic": ["Column", "Row", "Icon", "Text", "Nope"],
                "props_schema_json": json.dumps({
                    "type": "object",
                    "properties": {"steps": {"type": "array"}},
                }),
            }],
        },
    },
    "out_of_scope": [],
    "formatting_feedback": [{
        "issue": "Device bezel visible",
        "fix": "Export the frame only",
        "severity": "medium",
    }],
}


class ConnectedAppsHttpTest(unittest.TestCase):

  def setUp(self):
    llm = fixtures.make_llm()
    llm.handlers["generate"] = form_handler
    llm.handlers["analyze_reference"] = lambda s, p: ANALYSIS
    self.s = fixtures.LiveServer(llm=llm)
    self.s.login("alice")
    st, body = self.s.call("POST", "/api/projects", {"name": "Pharmacy"})
    self.assertEqual(st, 201, body)
    self.pid = body["project"]["id"]

  def tearDown(self):
    self.s.close()

  def _demo(self):
    st, body = self.s.call("POST", "/api/connected_apps/demo")
    self.assertEqual(st, 200, body)
    app = body["app"]
    self.assertEqual(app["status"], "ok", app)
    return app

  def test_demo_app_form_call_import_and_run_stages(self):
    app = self._demo()
    self.assertEqual(app["tool_count"], 7)
    # Idempotent per user.
    self.assertEqual(self._demo()["id"], app["id"])
    aid = app["id"]
    tool = "pharmacy_search_medications"
    st, form = self.s.call(
        "POST",
        f"/api/connected_apps/{aid}/form",
        {"tool": tool, "project_id": self.pid, "prefill": {"query": "ibu"}},
    )
    self.assertEqual(st, 200, form)
    self.assertEqual(form["mode"], "llm", form)
    self.assertTrue(form["validation"]["ok"], form["validation"])
    st, simple = self.s.call(
        "POST",
        f"/api/connected_apps/{aid}/form",
        {"tool": tool, "mode": "schema"},
    )
    self.assertEqual(simple["mode"], "schema")
    self.assertEqual(simple["form"]["submit_event"], "submit")
    # Call with form-model values (enum/number strings get coerced).
    st, called = self.s.call(
        "POST",
        f"/api/connected_apps/{aid}/call",
        {"tool": tool, "args": {"query": "ibuprofen", "limit": "2"}},
    )
    self.assertEqual(st, 200, called)
    self.assertEqual(called["args"]["limit"], 2)
    self.assertEqual(len(called["result"]["structured"]["results"]), 2)
    st, body = self.s.call(
        "POST",
        f"/api/connected_apps/{aid}/import",
        {
            "tool": tool,
            "args": called["args"],
            "result": called["result"],
            "intent": "Find a cheaper ibuprofen",
        },
    )
    self.assertEqual(st, 201, body)
    sample = body["sample"]
    self.assertEqual(sample["source"], "mcp")
    self.assertEqual(sample["meta"]["form"]["tool"], tool)
    self.assertEqual(
        sample["meta"]["connected_app"]["args"]["query"], "ibuprofen"
    )
    self.assertEqual(sample["intent"], "Find a cheaper ibuprofen")
    # Error results import as error-state samples (tool called server-side).
    st, body = self.s.call(
        "POST",
        f"/api/connected_apps/{aid}/import",
        {
            "tool": "pharmacy_get_prescription",
            "args": {"prescription_id": "RX-0000"},
        },
    )
    self.assertEqual(body["sample"]["state"], "error")
    # A run gives the MCP sample a form + result output per viewport.
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/runs",
        {
            "sample_ids": [sample["id"]],
            "viewports": ["phone"],
            "renderers": ["web"],
        },
    )
    self.assertEqual(st, 201, body)
    rid = body["run"]["id"]
    wait(
        lambda: self.s.call("GET", f"/api/runs/{rid}")[1]["run"]["status"]
        == "done"
    )
    st, body = self.s.call("GET", f"/api/runs/{rid}/outputs")
    outs = body["outputs"]
    self.assertEqual(sorted(o["stage"] for o in outs), ["form", "result"])
    form_o = next(o for o in outs if o["stage"] == "form")
    res_o = next(o for o in outs if o["stage"] == "result")
    self.assertEqual(form_o["pair_id"], res_o["id"])
    self.assertEqual(res_o["pair_id"], form_o["id"])
    self.assertEqual(form_o["status"], "done", form_o)
    st, det = self.s.call("GET", f"/api/outputs/{form_o['id']}")
    self.assertEqual(det["output"]["pair_id"], res_o["id"])
    self.assertEqual(det["output"]["stage"], "form")
    gen_prompts = [
        c["parts"][0] for c in self.s.llm.calls if c["purpose"] == "generate"
    ]
    self.assertTrue(
        any("INPUT FORM" in p and "ibuprofen" in p for p in gen_prompts)
    )
    judge_texts = [
        c["parts"][0] for c in self.s.llm.calls if c["purpose"] == "judge"
    ]
    self.assertTrue(any("INPUT FORM stage" in t for t in judge_texts))
    # include_forms=false → result only.
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/runs",
        {
            "sample_ids": [sample["id"]],
            "viewports": ["phone"],
            "include_forms": False,
        },
    )
    rid2 = body["run"]["id"]
    st, body = self.s.call("GET", f"/api/runs/{rid2}/outputs")
    self.assertEqual([o["stage"] for o in body["outputs"]], ["result"])

  def test_remote_app_crud_masking_and_errors(self):
    url = f"http://127.0.0.1:{self.s.port}/mcp/demo"
    st, body = self.s.call(
        "POST",
        "/api/connected_apps",
        {
            "name": "Remote demo",
            "url": url,
            "headers": {"Authorization": "Bearer xyz"},
        },
    )
    self.assertEqual(st, 201, body)
    app = body["app"]
    self.assertEqual(app["status"], "ok")
    self.assertEqual(app["headers_masked"], {"Authorization": "Bearer ••••"})
    self.assertNotIn(
        "xyz", json.dumps(self.s.call("GET", "/api/connected_apps")[1])
    )
    # Masked value round-trips without overwriting the secret.
    st, body = self.s.call(
        "PATCH",
        f"/api/connected_apps/{app['id']}",
        {"headers": app["headers_masked"], "name": "Renamed"},
    )
    row = self.s.app.store.get("connected_apps", app["id"])
    self.assertEqual(row["headers"]["Authorization"], "Bearer xyz")
    self.assertEqual(body["app"]["name"], "Renamed")
    # Unreachable server: saved with status error.
    st, body = self.s.call(
        "POST",
        "/api/connected_apps",
        {"name": "Down", "url": "http://127.0.0.1:9/mcp"},
    )
    self.assertEqual(st, 201)
    self.assertEqual(body["app"]["status"], "error")
    st, body = self.s.call(
        "POST",
        f"/api/connected_apps/{body['app']['id']}/call",
        {"tool": "x", "args": {}},
    )
    self.assertEqual(st, 404)  # no tools snapshot
    # Other users can't see it.
    self.s.login("bob")
    st, _ = self.s.call("GET", f"/api/connected_apps/{app['id']}")
    self.assertEqual(st, 403)
    st, body = self.s.call(
        "POST", "/api/connected_apps", {"name": "x", "url": "ftp://x"}
    )
    self.assertEqual(st, 400)

  def test_screenshot_reference_analysis_and_accept(self):
    png = base64.b64encode(fixtures.PNG).decode()
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/references",
        {
            "kind": "screenshots",
            "notes": "Refill flow",
            "images": [{
                "data_url": "data:image/png;base64," + png,
                "caption": "Do: results list",
                "viewport_hint": "phone",
            }],
        },
    )
    self.assertEqual(st, 201, body)
    ref = body["reference"]
    self.assertEqual(ref["status"], "ready")
    self.assertTrue(ref["images"][0]["url"].startswith("/files/references/"))
    st, raw = self.s.call("GET", ref["images"][0]["url"], raw=True)
    self.assertEqual(st, 200)
    st, body = self.s.call(
        "PATCH",
        f"/api/references/{ref['id']}",
        {"images": [{"index": 0, "caption": "Do: compact results"}]},
    )
    self.assertEqual(
        body["reference"]["images"][0]["caption"], "Do: compact results"
    )
    st, body = self.s.call("POST", f"/api/references/{ref['id']}/analyze", {})
    self.assertEqual(st, 200, body)
    done = wait(
        lambda: (lambda r: r if r["status"] in ("analyzed", "error") else None)(
            self.s.call("GET", f"/api/references/{ref['id']}")[1]["reference"]
        )
    )
    self.assertEqual(done["status"], "analyzed", done)
    self.assertEqual(
        done["analysis"]["formatting_feedback"][0]["severity"], "medium"
    )
    call = next(
        c for c in self.s.llm.calls if c["purpose"] == "analyze_reference"
    )
    self.assertTrue(any(isinstance(p, Image) for p in call["parts"]))
    self.assertIn("compact results", "\n".join(str(p) for p in call["parts"]))
    st, body = self.s.call("GET", f"/api/projects/{self.pid}/chat")
    msg = next(
        m for m in body["messages"] if m["id"] == done["chat_message_id"]
    )
    self.assertEqual(msg["context"]["reference_ids"], [ref["id"]])
    prop = msg["proposal"]
    self.assertEqual(prop["catalog_changes"]["disable"], ["Slider"])
    pc = prop["catalog_changes"]["proposed_components"][0]
    self.assertEqual(pc["maps_to_basic"], ["Column", "Row", "Icon", "Text"])
    conf = {t["token"]: t["confidence"] for t in prop["theme_changes"]}
    self.assertEqual(conf, {"primary": "exact", "radius_card": "approx"})
    st, body = self.s.call(
        "POST", f"/api/projects/{self.pid}/chat/{msg['id']}/accept", {}
    )
    self.assertEqual(st, 200, body)
    self.assertTrue(body["has_constitution_change"])
    self.assertEqual(body["catalog_changes_applied"]["disabled"], ["Slider"])
    st, body = self.s.call("GET", f"/api/projects/{self.pid}/settings")
    cat = body["settings"]["catalog"]
    self.assertIn("Slider", cat["disabled_components"])
    self.assertEqual(cat["proposed_components"], [])
    self.assertNotIn("StatusTimeline", cat["custom_components"])
    # Proposed components become draft custom components.
    st, comps = self.s.call("GET", f"/api/projects/{self.pid}/components")
    self.assertEqual(st, 200, comps)
    drafts = [c for c in comps["components"] if c["name"] == "StatusTimeline"]
    self.assertEqual(len(drafts), 1)
    self.assertEqual(drafts[0]["status"], "draft")
    self.assertEqual(drafts[0]["source"]["kind"], "proposal")
    self.assertEqual(drafts[0]["source"]["reference_ids"], [ref["id"]])
    self.assertEqual(body["settings"]["theme"]["primary"].upper(), "#0B57D0")
    st, body = self.s.call("DELETE", f"/api/references/{ref['id']}")
    self.assertEqual(st, 200)

  def test_figma_reference_with_fake_fetch(self):
    st, body = self.s.call("GET", "/api/me/figma")
    self.assertIn("has_token", body)
    st, body = self.s.call("PUT", "/api/me/figma", {"token": "figd_test"})
    self.assertEqual(body, {"has_token": True, "source": "user"})
    seen = {}

    def fake_fetch(token, url):
      seen["token"], seen["url"] = token, url
      return {
          "file_key": "K",
          "node_ids": ["1:2"],
          "file_name": "Pharmacy kit",
          "frames": [{
              "id": "1:2",
              "name": "Refill @ Phone",
              "width": 412,
              "height": 915,
              "viewport_hint": "phone",
              "png": fixtures.PNG,
          }],
          "facts": {
              "file_name": "Pharmacy kit",
              "frames": [],
              "colors": [],
              "radii": [],
              "text_styles": [],
              "spacing": {},
              "components": [],
              "principles_text": ["One CTA per card"],
              "styles": [],
              "variables": {"available": False, "count": 0},
          },
      }

    self.s.app.refs.figma_fetch = fake_fetch
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/references",
        {
            "kind": "figma",
            "url": "https://www.figma.com/design/K/x?node-id=1-2",
        },
    )
    self.assertEqual(st, 201, body)
    rid = body["reference"]["id"]
    ref = wait(
        lambda: (lambda r: r if r["status"] != "fetching" else None)(
            self.s.call("GET", f"/api/references/{rid}")[1]["reference"]
        )
    )
    self.assertEqual(ref["status"], "ready", ref)
    self.assertEqual(ref["title"], "Pharmacy kit")
    self.assertEqual(ref["images"][0]["frame_id"], "1:2")
    self.assertEqual(seen["token"], "figd_test")
    self.assertNotIn(
        "figd_test", json.dumps(self.s.call("GET", "/api/me/figma")[1])
    )
    # Saved/imported Figma file appears in GET /api/figma/projects.
    st, projs = self.s.call("GET", "/api/figma/projects")
    self.assertEqual(st, 200, projs)
    self.assertTrue(
        any(
            f["key"] == "K" and f["name"] == "Pharmacy kit"
            for f in projs["files"]
        ),
        projs,
    )
    # POST /api/figma/frames lists pages & frames for a picked file.
    self.s.app.refs.figma_frames = lambda token, u: {
        "file": {"key": "K", "name": "Pharmacy kit", "url": u},
        "pages": [{
            "id": "0:1",
            "name": "Page 1",
            "is_divider": False,
            "frames": [{
                "id": "1:2",
                "name": "Refill @ Phone",
                "width": 412,
                "height": 915,
            }],
        }],
        "preselect": ["1:2"],
    }
    st, fbody = self.s.call(
        "POST", "/api/figma/frames", {"url": "https://www.figma.com/design/K/x"}
    )
    self.assertEqual(st, 200, fbody)
    self.assertEqual(fbody["pages"][0]["frames"][0]["id"], "1:2")
    # Creating a Figma reference with explicit node_ids scopes the URL.
    st, body2 = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/references",
        {
            "kind": "figma",
            "file_key": "K",
            "node_ids": ["1:2", "1:3"],
        },
    )
    self.assertEqual(st, 201, body2)
    self.assertIn("node-id=1-2,1-3", body2["reference"]["source"]["url"])
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/references",
        {"kind": "figma", "url": "https://www.figma.com/proto/K/x"},
    )
    self.assertEqual(st, 400)

  def test_keep_alive_drains_unread_bodies(self):
    import http.client  # pylint: disable=g-import-not-at-top

    conn = http.client.HTTPConnection("127.0.0.1", self.s.port, timeout=10)
    cookie = "; ".join(f"{c.name}={c.value}" for c in self.s.jar)
    hdrs = {"Content-Type": "application/json", "Cookie": cookie}
    conn.request("POST", "/api/connected_apps/demo", body="{}", headers=hdrs)
    r1 = conn.getresponse()
    r1.read()
    self.assertEqual(r1.status, 200)
    conn.request("GET", "/api/connected_apps", headers=hdrs)
    r2 = conn.getresponse()
    self.assertEqual(r2.status, 200, r2.read()[:200])
    conn.close()

  def test_stitch_key_screens_import_and_exact_tokens(self):
    from fake_stitch import PROJECT_ID, FakeStitch  # pylint: disable=g-import-not-at-top

    fake = FakeStitch().start()
    old = os.environ.get("DCB_STITCH_MCP_URL")
    os.environ["DCB_STITCH_MCP_URL"] = fake.mcp_url
    try:
      url = f"https://stitch.withgoogle.com/projects/{PROJECT_ID}"
      st, body = self.s.call("POST", "/api/stitch/screens", {"url": url})
      if self.s.app.refs.stitch_status({"ldap": "alice"})["source"] != "server":
        self.assertEqual(st, 400, body)  # no key yet
      st, body = self.s.call("PUT", "/api/me/stitch", {"key": "test-key"})
      self.assertEqual(body, {"has_key": True, "source": "user"})
      self.assertNotIn(
          "test-key", json.dumps(self.s.call("GET", "/api/me/stitch")[1])
      )
      st, plist = self.s.call("GET", "/api/stitch/projects")
      self.assertEqual(st, 200, plist)
      self.assertEqual(plist["projects"][0]["id"], PROJECT_ID)
      self.assertEqual(plist["projects"][0]["title"], "Pharmacy refills")
      st, body = self.s.call("POST", "/api/stitch/screens", {"url": url})
      self.assertEqual(st, 200, body)
      self.assertEqual(body["project"]["title"], "Pharmacy refills")
      self.assertEqual(len(body["screens"]), 4)
      self.assertEqual(body["design_systems"][0]["display_name"], "Pharmacy DS")
      st, bad = self.s.call(
          "POST", "/api/stitch/screens", {"url": "https://x.com/p"}
      )
      self.assertEqual(st, 400)
      ids = [sc["id"] for sc in body["screens"]]
      st, body = self.s.call(
          "POST",
          f"/api/projects/{self.pid}/references",
          {"kind": "stitch", "url": url, "screen_ids": ids},
      )
      self.assertEqual(st, 201, body)
      rid = body["reference"]["id"]
      ref = wait(
          lambda: (lambda r: r if r["status"] != "fetching" else None)(
              self.s.call("GET", f"/api/references/{rid}")[1]["reference"]
          )
      )
      self.assertEqual(ref["status"], "ready", ref)
      self.assertEqual(ref["kind"], "stitch")
      self.assertEqual(ref["title"], "Pharmacy refills")
      renders = [
          (im["frame_id"], im["render"], bool(im["url"]), bool(im["error"]))
          for im in ref["images"]
      ]
      self.assertEqual(
          renders,
          [
              ("scr_results", "screenshot", True, False),
              ("scr_empty", "screenshot", True, False),
              (
                  "scr_detail",
                  "html",
                  True,
                  False,
              ),  # rendered via headless Chrome
              ("scr_broken", None, False, True),  # both downloads blocked
          ],
      )
      self.assertTrue(any(u.startswith("file://") for u in self.s.pool.calls))
      facts = ref["facts"]
      self.assertEqual(facts["source"], "stitch")
      self.assertEqual(facts["design_system"]["fonts"]["headline"], "Open Sans")
      self.assertEqual(len(facts["html_outline"]), 3)
      # Analysis sees Stitch facts as exact.
      self.s.llm.handlers["analyze_reference"] = lambda sy, p: dict(
          ANALYSIS,
          proposal=dict(
              ANALYSIS["proposal"],
              theme_changes=[{
                  "token": "font_family_heading",
                  "value": "Open Sans",
                  "is_custom": False,
                  "maps_to": "",
                  "reason": "Stitch DS",
                  "confidence": "exact",
              }],
          ),
      )
      st, body = self.s.call("POST", f"/api/references/{rid}/analyze", {})
      self.assertEqual(st, 200, body)
      done = wait(
          lambda: (
              lambda r: r if r["status"] in ("analyzed", "error") else None
          )(self.s.call("GET", f"/api/references/{rid}")[1]["reference"])
      )
      self.assertEqual(done["status"], "analyzed", done)
      call = [
          c for c in self.s.llm.calls if c["purpose"] == "analyze_reference"
      ][-1]
      self.assertIn("Stitch facts", call["parts"][0])
      self.assertIn("Pharmacy DS", call["parts"][0])
      st, ch = self.s.call("GET", f"/api/projects/{self.pid}/chat")
      msg = next(
          m for m in ch["messages"] if m["id"] == done["chat_message_id"]
      )
      self.assertEqual(
          msg["proposal"]["theme_changes"][0]["confidence"], "exact"
      )
      # Rejected key surfaces as a readable error.
      self.s.call("PUT", "/api/me/stitch", {"key": "wrong"})
      st, body = self.s.call("POST", "/api/stitch/screens", {"url": url})
      self.assertEqual(st, 403, body)
      self.assertIn("rejected", body["error"])
    finally:
      fake.stop()
      if old is None:
        os.environ.pop("DCB_STITCH_MCP_URL", None)
      else:
        os.environ["DCB_STITCH_MCP_URL"] = old

  def test_docs_served(self):
    st, raw = self.s.call(
        "GET", "/docs/DESIGN_REFERENCE_GUIDELINES.md", raw=True
    )
    self.assertEqual(st, 200)
    st, _ = self.s.call("GET", "/docs/CONTRACT.md", raw=True)
    self.assertEqual(st, 404)


if __name__ == "__main__":
  unittest.main()
