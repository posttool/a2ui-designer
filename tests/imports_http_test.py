# pylint: disable=g-long-ternary,g-multiple-import
"""End-to-end: imports — analyze → questions → merge plan → apply."""

import base64
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from components_http_test import build_reply  # pylint: disable=g-import-not-at-top
import fixtures  # pylint: disable=g-import-not-at-top


def wait(fn, timeout=15.0):
  deadline = time.time() + timeout
  while time.time() < deadline:
    v = fn()
    if v:
      return v
    time.sleep(0.05)
  raise AssertionError("timed out")


DATA_URL = "data:image/png;base64," + base64.b64encode(fixtures.PNG).decode()


def analysis_reply(system, parts):
  del system
  text = parts[0]
  updating = "# Current extractions" in text
  return {
      "summary_md": "A checkout screen and a palette sheet.",
      "extractions": [
          {
              "id": "",
              "kind": "template",
              "title": "Checkout review",
              "description": "Whole checkout screen.",
              "image": 1,
              "box_2d": [],
              "node_id": "",
              "instances": [],
              "confidence": 0.9,
              "rationale": "Viewport-sized screen of components.",
              "uses": ["Order summary", "Button"],
              "question": "",
              "question_options": [],
              "component": {
                  "purpose": "",
                  "maps_to_basic": [],
                  "props_schema_json": "",
                  "matches_existing": "",
              },
              "template": {
                  "when_to_use": "Reviewing an order before paying.",
                  "structure_md": "1. Header\n2. Order summary\n3. Pay button",
                  "slots": ["header", "summary", "actions"],
              },
              "tokens": [],
          },
          {
              "id": "",
              "kind": "component",
              "title": "Order summary",
              "description": "Line items with totals.",
              "image": 1,
              "box_2d": [200, 50, 600, 950],
              "node_id": "",
              "instances": [{"image": 2, "box_2d": [100, 100, 400, 900]}],
              "confidence": 0.45,
              "rationale": "Could be one-off.",
              "uses": [],
              "question": (
                  ""
                  if updating
                  else (
                      "Is the 'Order summary' block reusable or part of the"
                      " Checkout template?"
                  )
              ),  # pylint: disable=g-long-ternary
              "question_options": [],
              "component": {
                  "purpose": "Show items and totals.",
                  "maps_to_basic": ["Card", "NotAComponent"],
                  "props_schema_json": json.dumps({
                      "type": "object",
                      "properties": {"total": {"type": "string"}},
                  }),
                  "matches_existing": "",
              },
              "template": {"when_to_use": "", "structure_md": "", "slots": []},
              "tokens": [],
          },
          {
              "id": "",
              "kind": "token_reference",
              "title": "Palette",
              "description": "Brand colors.",
              "image": 2,
              "box_2d": [0, 0, 1000, 1000],
              "node_id": "",
              "instances": [],
              "confidence": 0.95,
              "rationale": "Swatches with hex labels.",
              "uses": [],
              "question": "",
              "question_options": [],
              "component": {
                  "purpose": "",
                  "maps_to_basic": [],
                  "props_schema_json": "",
                  "matches_existing": "",
              },
              "template": {"when_to_use": "", "structure_md": "", "slots": []},
              "tokens": [{
                  "token": "primary",
                  "value": "#123456",
                  "is_custom": False,
                  "maps_to": "",
                  "confidence": "approx",
                  "reason": "Main swatch",
              }],
          },
      ],
      "questions": (  # pylint: disable=g-long-ternary
          []
          if updating
          else [{
              "id": "q1",
              "text": "Are these colors your brand palette or sample content?",
              "options": [
                  {"value": "brand", "label": "Brand palette"},
                  {"value": "sample", "label": "Sample content"},
              ],
              "about": ["Palette"],
          }]
      ),
  }


def plan_reply(system, parts):
  del system
  text = parts[0]
  ids = {}
  for line in text.splitlines():
    line = line.strip()
    if line.startswith('"id": "x_'):
      cur = line.split('"')[3]
    if line.startswith('"kind":') and "cur" in dir():
      ids[line.split('"')[3]] = cur  # pylint: disable=undefined-variable
  return {
      "summary_md": "Adds tokens, one component, one template.",
      "tokens": [{
          "token": "primary",
          "value": "#123456",
          "is_custom": False,
          "maps_to": "",
          "confidence": "approx",
          "reason": "Brand",
          "sources": [ids.get("token_reference", "")],
      }],
      "components": [{
          "extraction_id": ids.get("component", ""),
          "action": "draft_custom",
          "name": "OrderSummary",
          "basic": "",
          "existing": "",
          "purpose": "Items and totals before paying.",
      }],
      "templates": [{
          "extraction_id": ids.get("template", ""),
          "name": "Checkout review",
          "description": "Order review screen.",
          "when_to_use": "Before payment.",
          "structure_md": "Header, OrderSummary, Pay",
          "components": ["OrderSummary", "Button"],
      }],
      "constitution": {
          "constitution_md": (
              "# Templates\n\nUse **Checkout review** when the user confirms"
              " an order."
          ),
          "change_summary": "Add Checkout review guidance",
          "changed_sections": ["Templates"],
      },
  }


class ImportsHttpTest(unittest.TestCase):

  def setUp(self):
    super().setUp()
    llm = fixtures.make_llm()
    llm.handlers["analyze_import"] = analysis_reply
    llm.handlers["plan_import_merge"] = plan_reply
    llm.handlers["build_component"] = lambda s, p: build_reply("OrderSummary")
    llm.handlers["repair_component"] = lambda s, p: build_reply("OrderSummary")
    llm.handlers["compare_component"] = lambda s, p: {
        "score": 4,
        "summary": "Close.",
        "mismatches": [],
    }
    self.llm = llm
    self.s = fixtures.LiveServer(llm=llm)
    self.s.login("alice")
    st, body = self.s.call("POST", "/api/projects", {"name": "Shop"})
    self.assertEqual(st, 201, body)
    self.pid = body["project"]["id"]

  def tearDown(self):
    super().tearDown()
    self.s.close()

  def imp(self, iid):
    st, body = self.s.call("GET", f"/api/imports/{iid}")
    self.assertEqual(st, 200, body)
    return body["import"]

  def settle(self, iid, phase):
    return wait(
        lambda: (lambda i: i if i["phase"] == phase else None)(self.imp(iid))
    )

  def create_screens(self):
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/references",
        {
            "kind": "screenshots",
            "origin": "design",
            "images": [
                {"data_url": DATA_URL, "name": "checkout.png"},
                {"data_url": DATA_URL, "name": "palette.png"},
            ],
        },
    )
    self.assertEqual(st, 201, body)
    return body["reference"]["id"]

  def test_analyze_question_merge_apply(self):
    iid = self.create_screens()
    imp = self.settle(iid, "review")
    self.assertEqual(imp["origin"], "design")
    self.assertIsInstance(imp["progress"], dict)
    self.assertEqual(
        [s["id"] for s in imp["progress"]["steps"]],
        ["upload_images", "gemini_analyze", "crop_regions"],
    )
    self.assertTrue(
        all(s["status"] == "done" for s in imp["progress"]["steps"]),
        imp["progress"],
    )
    xs = {x["title"]: x for x in imp["extractions"]}
    self.assertEqual(set(xs), {"Checkout review", "Order summary", "Palette"})
    summ, tpl, pal = xs["Order summary"], xs["Checkout review"], xs["Palette"]
    self.assertEqual(summ["status"], "needs_answer")
    self.assertEqual(len(summ["question"]["options"]), 4)  # default kinds
    self.assertEqual(summ["region"]["box"], [50, 200, 950, 600])  # x0,y0,x1,y1
    self.assertEqual(summ["region"]["instances"][0]["image_index"], 1)
    self.assertEqual(summ["payload"]["component"]["maps_to_basic"], ["Card"])
    self.assertIsNone(pal["region"]["box"])  # whole image
    self.assertEqual(tpl["status"], "proposed")
    self.assertEqual(
        [u.get("xid") or u.get("basic") for u in tpl["uses"]],
        [summ["id"], "Button"],
    )
    self.assertEqual(summ["used_by"], [tpl["id"]])
    self.assertTrue(summ["crop_url"])  # cropped with the (fake) pool
    self.assertEqual(imp["counts"]["open_questions"], 2)
    self.assertEqual(imp["questions"][0]["about"], [pal["id"]])

    # Merge is blocked while questions are open.
    st, body = self.s.call("POST", f"/api/imports/{iid}/merge/prepare", {})
    self.assertEqual(st, 409, body)

    st, body = self.s.call(
        "PATCH",
        f"/api/imports/{iid}/extractions/{summ['id']}",
        {"answer": {"choice": "component", "note": "used in 3 flows"}},
    )
    self.assertEqual(st, 200, body)
    st, body = self.s.call(
        "POST",
        f"/api/imports/{iid}/questions/q1",
        {"answer": {"choice": "brand"}},
    )
    self.assertEqual(st, 200, body)
    self.assertEqual(body["import"]["counts"]["open_questions"], 0)

    # Re-analysis keeps confirmed answers and answered questions.
    st, body = self.s.call(
        "POST", f"/api/imports/{iid}/analyze", {"instructions": "be brief"}
    )
    self.assertEqual(st, 200, body)
    imp = self.settle(iid, "review")
    ctx = [c for c in self.llm.calls if c["purpose"] == "analyze_import"][-1]
    self.assertIn("used in 3 flows", ctx["parts"][0])
    self.assertIn("→ brand", ctx["parts"][0])
    xs2 = {x["title"]: x for x in imp["extractions"]}
    self.assertEqual(xs2["Order summary"]["status"], "confirmed")
    self.assertEqual(imp["questions"][0]["answer"]["choice"], "brand")

    # Add a region by hand, then fold it into Order summary.
    st, body = self.s.call(
        "POST",
        f"/api/imports/{iid}/extractions",
        {
            "kind": "component",
            "title": "Summary again",
            "region": {"image_index": 1, "box": [10, 10, 500, 400]},
        },
    )
    self.assertEqual(st, 201, body)
    dup = next(
        x
        for x in body["import"]["extractions"]
        if x["title"] == "Summary again"
    )
    st, body = self.s.call(
        "POST",
        f"/api/imports/{iid}/extractions/merge",
        {"keep": summ["id"], "ids": [dup["id"]]},
    )
    self.assertEqual(st, 200, body)
    kept = next(
        x for x in body["import"]["extractions"] if x["id"] == summ["id"]
    )
    self.assertEqual(len(kept["region"]["instances"]), 2)

    st, body = self.s.call("POST", f"/api/imports/{iid}/merge/prepare", {})
    self.assertEqual(st, 200, body)
    imp = self.settle(iid, "plan_ready")
    self.assertEqual(
        [s["id"] for s in imp["progress"]["steps"]],
        ["gemini_plan", "build_diff"],
    )
    self.assertTrue(
        all(s["status"] == "done" for s in imp["progress"]["steps"]),
        imp["progress"],
    )
    lines = imp["merge_plan"]["lines"]
    types = [ln["type"] for ln in lines]
    self.assertEqual(
        sorted(set(types)),
        ["component", "constitution", "template", "tokens"],
    )
    tok = next(
        ln
        for ln in lines
        if ln["type"] == "tokens" and ln["detail"]["token"] == "primary"
    )
    self.assertEqual(tok["detail"]["after"], "#123456")
    tok_by_name = {
        ln["detail"]["token"]: ln["detail"]["after"]
        for ln in lines
        if ln["type"] == "tokens"
    }
    self.assertIn("primary_container", tok_by_name)
    self.assertIn("secondary", tok_by_name)
    self.assertIn("tertiary", tok_by_name)
    comp = next(ln for ln in lines if ln["type"] == "component")
    self.assertEqual(comp["detail"]["name"], "OrderSummary")
    self.assertEqual(comp["from"], [summ["id"]])

    # Apply everything except the constitution line.
    cons = next(ln for ln in lines if ln["type"] == "constitution")
    st, body = self.s.call(
        "POST",
        f"/api/imports/{iid}/merge/apply",
        {"lines": {cons["id"]: False}},
    )
    self.assertEqual(st, 200, body)
    imp = body["import"]
    self.assertEqual(imp["phase"], "merged")
    applied = {a["type"]: a for a in imp["merge_log"][0]["applied"]}
    self.assertEqual(set(applied), {"tokens", "component", "template"})
    self.assertIsNone(body["result"]["revision_id"])
    xs3 = {x["title"]: x for x in imp["extractions"]}
    self.assertEqual(xs3["Order summary"]["status"], "merged")
    # The component build job may record token targets on the extraction
    # concurrently, so only the presence (not the order) is asserted.
    self.assertIn(
        "component", {m["type"] for m in xs3["Order summary"]["merged_into"]}
    )
    self.assertIn(
        "template", {m["type"] for m in xs3["Checkout review"]["merged_into"]}
    )

    # Tokens landed in settings, including M3 complementary colors adapted from #123456.
    st, body = self.s.call("GET", f"/api/projects/{self.pid}/settings")
    self.assertEqual(body["settings"]["theme"]["primary"], "#123456")
    self.assertNotEqual(
        body["settings"]["theme"]["primary_container"],
        "#D3E3FD",
    )
    self.assertNotEqual(
        body["settings"]["theme"]["secondary"],
        "#5E5E5E",
    )
    # Component draft was created from the crop and built.
    cid = applied["component"]["target"]["id"]
    comp_row = wait(
        lambda: (
            lambda c: c
            if (c["job"] or {}).get("stage") in ("done", "error")
            else None
        )(self.s.call("GET", f"/api/components/{cid}")[1]["component"])
    )
    self.assertEqual(comp_row["source"]["kind"], "import")
    self.assertEqual(comp_row["source"]["import_id"], iid)
    self.assertEqual(comp_row["status"], "active")
    self.assertEqual(comp_row["name"], "OrderSummary")
    # Custom token --a2ui-badge read by OrderSummary was automatically brought
    # into settings.theme.custom_tokens during merge so zero missing-token
    # issues exist.
    st, s_after = self.s.call("GET", f"/api/projects/{self.pid}/settings")
    self.assertIn("badge", s_after["settings"]["theme"]["custom_tokens"])
    self.assertEqual(
        s_after["settings"]["theme"]["custom_tokens"]["badge"]["value"],
        "#FFE08A",
    )
    st, deps = self.s.call("GET", f"/api/projects/{self.pid}/dependencies")
    self.assertEqual(st, 200, deps)
    missing_tok_issues = [
        i
        for i in deps["issues"]
        if i.get("kind")
        in ("component_missing_token", "constitution_missing_token")
    ]
    self.assertEqual(missing_tok_issues, [])
    # Template is reusable.
    st, body = self.s.call("GET", f"/api/projects/{self.pid}/templates")
    self.assertEqual(len(body["templates"]), 1)
    t = body["templates"][0]
    self.assertEqual(t["components"], ["OrderSummary", "Button"])
    self.assertEqual(t["source"]["import_id"], iid)
    st, body = self.s.call(
        "PATCH", f"/api/templates/{t['id']}", {"when_to_use": "Paying."}
    )
    self.assertEqual(body["template"]["when_to_use"], "Paying.")
    # Short chat summary, no proposal.
    st, body = self.s.call("GET", f"/api/projects/{self.pid}/chat")
    msg = [m for m in body["messages"] if m["context"].get("import_id") == iid]
    self.assertEqual(len(msg), 1)
    self.assertEqual(msg[0]["proposal_status"], "none")
    self.assertIn("1 template", msg[0]["text_md"])
    # Package export includes the template.
    st, raw = self.s.call(
        "GET", f"/api/export/projects/{self.pid}/package.zip", raw=True
    )
    self.assertEqual(st, 200)
    import io, zipfile  # pylint: disable=g-import-not-at-top,multiple-imports

    names = zipfile.ZipFile(io.BytesIO(raw)).namelist()
    self.assertTrue(
        any(
            n.endswith("templates/checkout-review/template.json") for n in names
        ),
        names,
    )
    # Imports list record.
    st, body = self.s.call("GET", f"/api/projects/{self.pid}/imports")
    rec = body["imports"][0]
    self.assertEqual(rec["counts"]["merged"], 3)
    self.assertNotIn("extractions", rec)

  def test_plan_time_basic_fit_from_stored_figma_nodes(self):
    from refs import figma  # pylint: disable=g-import-not-at-top

    frame = {
        "id": "1:2",
        "name": "Home",
        "type": "FRAME",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 400, "height": 800},
        "exportSettings": [{"format": "PNG"}],  # dropped by trimming
        "children": [
            {
                "id": "5:1",
                "name": "Order card",
                "type": "COMPONENT",
                "layoutMode": "VERTICAL",
                "itemSpacing": 8,
                "absoluteBoundingBox": {
                    "x": 20,
                    "y": 80,
                    "width": 360,
                    "height": 160,
                },
                "children": [
                    {
                        "id": "5:2",
                        "name": "Title",
                        "type": "TEXT",
                        "characters": "Pad thai",
                        "style": {"fontSize": 20, "fontWeight": 600},
                        "fillGeometry": [{"path": "M0 0"}],  # dropped
                    },
                    {
                        "id": "5:3",
                        "name": "Subtitle",
                        "type": "TEXT",
                        "characters": "Ready in 20 min",
                        "style": {"fontSize": 14},
                    },
                ],
            },
            {
                "id": "6:1",
                "name": "Hero art",
                "type": "COMPONENT",
                "absoluteBoundingBox": {
                    "x": 0,
                    "y": 400,
                    "width": 400,
                    "height": 300,
                },
                "children": [
                    {
                        "id": "6:2",
                        "name": "blob",
                        "type": "VECTOR",
                        "absoluteBoundingBox": {"width": 300, "height": 200},
                    },
                    {
                        "id": "6:3",
                        "name": "badge",
                        "type": "ELLIPSE",
                        "layoutPositioning": "ABSOLUTE",
                        "absoluteBoundingBox": {"width": 60, "height": 60},
                    },
                ],
            },
        ],
    }
    trimmed = figma.trim_node_tree(frame)
    self.assertNotIn("exportSettings", trimmed)
    self.assertNotIn("fillGeometry", trimmed["children"][0]["children"][0])
    self.assertEqual(
        trimmed["children"][0]["children"][1]["characters"], "Ready in 20 min"
    )
    # Figma ingest stores the trimmed tree next to the frame PNG.
    mgr = self.s.app.imports
    nodes_fn = self.s.app.refs._save_image(  # pylint: disable=protected-access
        "ref_test", 0, json.dumps(trimmed).encode("utf-8"), "nodes.json"
    )
    r = {
        "kind": "figma",
        "images": [
            {"file": "x.png", "frame_id": "1:2", "nodes_file": nodes_fn}
        ],
    }
    by_node = mgr._plan_basic_fit(  # pylint: disable=protected-access
        r, {"region": {"image_index": 0, "node_id": "5:1"}}
    )
    self.assertEqual(by_node["source"], "figma")
    self.assertEqual(by_node["score"], 1.0)
    by_box = mgr._plan_basic_fit(  # pylint: disable=protected-access
        r, {"region": {"image_index": 0, "box": [0, 500, 1000, 875]}}
    )
    self.assertIsNotNone(by_box)
    self.assertLess(by_box["score"], 0.5)
    self.assertTrue(
        any("vector" in s or "absolute" in s for s in by_box["summary"]),
        by_box,
    )
    # Screenshots (no structure) and legacy imports without nodes → None.
    self.assertIsNone(
        mgr._plan_basic_fit(  # pylint: disable=protected-access
            {"kind": "screenshots", "images": [{"file": "x.png"}]},
            {"region": {"image_index": 0, "box": [0, 0, 500, 500]}},
        )
    )
    self.assertIsNone(
        mgr._plan_basic_fit(  # pylint: disable=protected-access
            {"kind": "figma", "images": [{"file": "x.png", "frame_id": "1:2"}]},
            {"region": {"image_index": 0, "node_id": "5:1"}},
        )
    )

  def test_figma_import_adds_components(self):
    st, _ = self.s.call("PUT", "/api/me/figma", {"token": "figd_test"})
    self.assertEqual(st, 200)

    def fake_fetch(token, url):
      del token, url
      return {
          "file_key": "K",
          "node_ids": ["1:2"],
          "file_name": "Kit",
          "frames": [{
              "id": "1:2",
              "name": "Home",
              "width": 412,
              "height": 915,
              "viewport_hint": "phone",
              "png": fixtures.PNG,
          }],
          "facts": {"file_name": "Kit", "frames": []},
      }

    self.s.app.refs.figma_fetch = fake_fetch
    self.s.app.builder.figma_scan = lambda t, u: {
        "file_key": "K",
        "file_name": "Kit",
        "components": [
            {"node_id": "1:2", "name": "Home", "kind": "COMPONENT"},
            {
                "node_id": "5:1",
                "name": "Chip",
                "kind": "COMPONENT_SET",
                "width": 80,
                "height": 32,
                "variant_count": 3,
                "variant_props": {"state": ["on", "off"]},
            },
        ],
    }
    self.s.app.builder.figma_thumbs = lambda t, k, ids: {
        i: fixtures.PNG for i in ids
    }
    self.llm.handlers["analyze_import"] = lambda s, p: {
        "summary_md": "Kit",
        "extractions": [{
            "id": "",
            "kind": "component",
            "title": "Chip",
            "description": "",
            "image": 0,
            "box_2d": [],
            "node_id": "5:1",
            "instances": [],
            "confidence": 0.9,
            "rationale": "Component set",
            "uses": [],
            "question": "",
            "question_options": [],
            "component": {
                "purpose": "Filter",
                "maps_to_basic": ["ChoicePicker"],
                "props_schema_json": "",
                "matches_existing": "",
            },
            "template": {"when_to_use": "", "structure_md": "", "slots": []},
            "tokens": [],
        }],
        "questions": [],
    }
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/references",
        {
            "kind": "figma",
            "url": "https://www.figma.com/design/K/x?node-id=1-2",
        },
    )
    self.assertEqual(st, 201, body)
    iid = body["reference"]["id"]
    imp = self.settle(iid, "review")
    self.assertEqual(
        [s["id"] for s in imp["progress"]["steps"]],
        [
            "fetch_file",
            "download_frames",
            "scan_components",
            "gemini_analyze",
            "crop_regions",
        ],
    )
    self.assertTrue(
        all(s["status"] == "done" for s in imp["progress"]["steps"]),
        imp["progress"],
    )
    roles = [(im["frame_id"], im.get("role")) for im in imp["images"]]
    self.assertEqual(roles, [("1:2", "frame"), ("5:1", "component")])
    labels = [p for p in self.llm.calls[-1]["parts"] if isinstance(p, str)]
    self.assertTrue(any("node_id=5:1" in p for p in labels), labels)
    self.assertEqual(
        [f["node_id"] for f in imp["facts"]["figma_components"]], ["1:2", "5:1"]
    )
    x = imp["extractions"][0]
    self.assertEqual(x["region"]["image_index"], 1)  # resolved from node id
    self.assertEqual(x["region"]["node_id"], "5:1")

  def test_figma_and_stitch_project_data_and_default_data_source(self):
    st, _ = self.s.call("PUT", "/api/me/figma", {"token": "figd_test"})
    self.assertEqual(st, 200)

    def fake_fetch(token, url):
      del token, url
      return {
          "file_key": "K2",
          "node_ids": ["10:1", "10:2"],
          "file_name": "Pharmacy Portal",
          "frames": [
              {
                  "id": "10:1",
                  "name": "Refill Dashboard",
                  "width": 412,
                  "height": 915,
                  "viewport_hint": "phone",
                  "png": fixtures.PNG,
              },
              {
                  "id": "10:2",
                  "name": "Order Details",
                  "width": 412,
                  "height": 915,
                  "viewport_hint": "phone",
                  "png": fixtures.PNG,
              },
          ],
          "facts": {
              "file_name": "Pharmacy Portal",
              "frames": [],
              "frame_contents": [
                  {
                      "id": "10:1",
                      "name": "Refill Dashboard",
                      "texts": [
                          "Active Prescriptions",
                          "Lisinopril 10mg",
                          "Ready for pickup at 14:30",
                      ],
                  },
                  {
                      "id": "10:2",
                      "name": "Order Details",
                      "texts": [
                          "Order #88412",
                          "2 items",
                          "$14.00 copay",
                      ],
                  },
              ],
          },
      }

    self.s.app.refs.figma_fetch = fake_fetch
    self.s.app.builder.figma_scan = lambda t, u: {
        "file_key": "K2",
        "file_name": "Pharmacy Portal",
        "components": [],
    }
    self.llm.handlers["analyze_import"] = lambda s, p: {
        "summary_md": "Pharmacy portal screens with prescription data.",
        "extractions": [{
            "id": "",
            "kind": "template",
            "title": "Refill Dashboard",
            "description": "Prescription overview screen.",
            "image": 1,
            "box_2d": [],
            "node_id": "10:1",
            "instances": [],
            "confidence": 0.92,
            "rationale": "Full screen template.",
            "uses": ["Card", "Button"],
            "question": "",
            "question_options": [],
            "component": {
                "purpose": "",
                "maps_to_basic": [],
                "props_schema_json": "",
                "matches_existing": "",
            },
            "template": {
                "when_to_use": "Showing active prescriptions.",
                "structure_md": "Header + prescription cards",
                "slots": ["header", "list"],
            },
            "tokens": [],
        }],
        "questions": [],
        "project_data": {
            "sample_set_name": "Pharmacy Portal — Figma data",
            "sample_set_description": "Samples extracted from Pharmacy Portal.",
            "samples": [
                {
                    "title": "Active Prescriptions — Refill Dashboard",
                    "category": "Healthcare",
                    "subcategory": "Prescriptions",
                    "intent": "View active prescriptions and pickup times.",
                    "size_class": "m",
                    "state": "content",
                    "tags": ["figma", "pharmacy"],
                    "context_json": json.dumps(
                        {"user_goal": "Check refill status"}
                    ),
                    "payload_json": json.dumps({
                        "prescriptions": [{
                            "name": "Lisinopril 10mg",
                            "status": "Ready for pickup at 14:30",
                        }],
                    }),
                    "edge_cases": [],
                },
                {
                    "title": "Order #88412 — Order Details",
                    "category": "Healthcare",
                    "subcategory": "Prescriptions",
                    "intent": "Inspect copay and items for order #88412.",
                    "size_class": "s",
                    "state": "content",
                    "tags": ["figma", "order"],
                    "context_json": json.dumps(
                        {"user_goal": "Review copay amount"}
                    ),
                    "payload_json": json.dumps({
                        "order_id": "88412",
                        "item_count": 2,
                        "copay": "$14.00",
                    }),
                    "edge_cases": [],
                },
            ],
        },
    }
    self.llm.handlers["plan_import_merge"] = lambda s, p: {
        "summary_md": "Merges template and 2 project data samples.",
        "tokens": [],
        "components": [],
        "templates": [{
            "extraction_id": "x_1",
            "name": "Refill Dashboard",
            "description": "Prescription overview screen.",
            "when_to_use": "Showing active prescriptions.",
            "structure_md": "Header + prescription cards",
            "components": ["Card", "Button"],
        }],
        "constitution": {
            "constitution_md": "",
            "change_summary": "",
            "changed_sections": [],
        },
        "data": {
            "sample_set_name": "Pharmacy Portal — Figma data",
            "sample_set_description": "Samples extracted from Pharmacy Portal.",
            "samples": [],
        },
    }

    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/references",
        {
            "kind": "figma",
            "url": "https://www.figma.com/design/K2/Pharmacy?node-id=10-1",
            "import_data": True,
            "set_default_data_source": True,
        },
    )
    self.assertEqual(st, 201, body)
    iid = body["reference"]["id"]
    imp = self.settle(iid, "review")
    self.assertEqual(imp["counts"]["data"], 2)
    self.assertEqual(len(imp["analysis"]["project_data"]["samples"]), 2)

    # Confirm extraction and prepare merge plan.
    st, body = self.s.call("POST", f"/api/imports/{iid}/confirm", {})
    self.assertEqual(st, 200, body)
    st, body = self.s.call("POST", f"/api/imports/{iid}/merge/prepare", {})
    self.assertEqual(st, 200, body)
    imp = self.settle(iid, "plan_ready")
    plan = imp["merge_plan"]
    self.assertTrue(plan["set_default_data_source"])
    self.assertEqual(plan["sample_set_name"], "Pharmacy Portal — Figma data")
    data_lines = [ln for ln in plan["lines"] if ln["type"] == "data"]
    self.assertEqual(len(data_lines), 2)

    # Apply merge and verify samples, sample set, and project default data_source.
    st, body = self.s.call(
        "POST",
        f"/api/imports/{iid}/merge/apply",
        {"lines": {}, "set_default_data_source": True},
    )
    self.assertEqual(st, 200, body)
    res = body["result"]
    self.assertTrue(res["default_data_source_set"])
    self.assertEqual(len(res["sample_ids"]), 2)
    set_id = res["sample_set_id"]
    self.assertTrue(set_id)

    # Verify project settings["data_source"] is set to the imported sample set.
    st, sbody = self.s.call("GET", f"/api/projects/{self.pid}/settings")
    self.assertEqual(st, 200, sbody)
    ds = sbody["settings"]["data_source"]
    self.assertEqual(ds["kind"], "set")
    self.assertEqual(ds["sample_set_id"], set_id)
    self.assertEqual(ds["import_id"], iid)
    self.assertEqual(ds["source_kind"], "figma")

    # Verify samples in library have source="figma".
    st, smp_body = self.s.call("GET", f"/api/samples?set_id={set_id}")
    self.assertEqual(st, 200, smp_body)
    self.assertEqual(len(smp_body["samples"]), 2)
    self.assertTrue(all(x["source"] == "figma" for x in smp_body["samples"]))

    # Starting a run without sample_set_id or sample_ids uses the project's
    # default data source automatically.
    st, rbody = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/runs",
        {"viewports": ["phone"], "judge": False},
    )
    self.assertEqual(st, 201, rbody)
    self.assertEqual(rbody["run"]["sample_set_id"], set_id)
    self.assertEqual(rbody["run"]["counts"]["total"], 2)
    self.assertEqual(len(rbody["run"]["sample_ids"]), 2)

    # Also verify POST /api/imports/{iid}/data-source endpoint.
    st, ds_body = self.s.call("POST", f"/api/imports/{iid}/data-source", {})
    self.assertEqual(st, 200, ds_body)
    self.assertEqual(ds_body["data_source"]["sample_set_id"], set_id)

  def test_import_merge_brings_in_missing_tokens_read_by_material(self):
    self.llm.handlers["analyze_import"] = lambda s, p: {
        "summary_md": "Salad restaurant screen with custom badges and stepper.",
        "extractions": [
            {
                "id": "",
                "kind": "component",
                "title": "Order Line Item Row",
                "description": (
                    "Line item row reading var(--a2ui-stepper-bg, #ebf0eb) and"
                    " var(--a2ui-eco-badge-bg)."
                ),
                "image": 1,
                "box_2d": [100, 50, 300, 950],
                "node_id": "",
                "instances": [],
                "confidence": 0.95,
                "rationale": "Reusable order row.",
                "uses": [],
                "question": "",
                "question_options": [],
                "component": {
                    "purpose": "Displays an item with quantity stepper.",
                    "maps_to_basic": [],
                    "props_schema_json": json.dumps({
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    }),
                    "matches_existing": "",
                },
                "template": {
                    "when_to_use": "",
                    "structure_md": "",
                    "slots": [],
                },
                "tokens": [{
                    "token": "eco-badge-bg",
                    "value": "#A9E8A8",
                    "is_custom": True,
                    "maps_to": "",
                    "confidence": "exact",
                    "reason": "Eco badge green background",
                }],
            },
            {
                "id": "",
                "kind": "template",
                "title": "Salad Bowl Customizer",
                "description": "Screen template.",
                "image": 1,
                "box_2d": [],
                "node_id": "",
                "instances": [],
                "confidence": 0.92,
                "rationale": "Full screen.",
                "uses": ["Order Line Item Row"],
                "question": "",
                "question_options": [],
                "component": {
                    "purpose": "",
                    "maps_to_basic": [],
                    "props_schema_json": "",
                    "matches_existing": "",
                },
                "template": {
                    "when_to_use": "Customizing a salad order.",
                    "structure_md": (
                        "1. Header with `var(--a2ui-header-pill-bg, #E2F1E0)`\n"
                        "2. OrderLineItemRow list"
                    ),
                    "slots": ["header", "items"],
                },
                "tokens": [],
            },
        ],
        "questions": [],
    }

    def custom_plan_reply(system, parts):
      del system
      text = parts[0]
      ids = {}
      cur = ""
      for line in text.splitlines():
        line = line.strip()
        if line.startswith('"id": "x_'):
          cur = line.split('"')[3]
        if line.startswith('"kind":') and cur:
          ids[line.split('"')[3]] = cur
      # Planner returns empty tokens[] to verify that _plan + apply_merge +
      # component builder automatically bring in all missing tokens read by
      # extractions, templates, constitution, and drafted component CSS.
      return {
          "summary_md": "Adds component, template, and constitution.",
          "tokens": [],
          "components": [{
              "extraction_id": ids.get("component", ""),
              "action": "draft_custom",
              "name": "OrderLineItemRow",
              "basic": "",
              "existing": "",
              "purpose": "Displays an item with quantity stepper.",
          }],
          "templates": [{
              "extraction_id": ids.get("template", ""),
              "name": "Salad bowl customizer",
              "description": "Screen template.",
              "when_to_use": "Customizing a salad order.",
              "structure_md": (
                  "1. Header (`var(--a2ui-header-pill-bg, #E2F1E0)`)\n"
                  "2. OrderLineItemRow"
              ),
              "components": ["OrderLineItemRow"],
          }],
          "constitution": {
              "constitution_md": (
                  (
                      "# Layout\n\nUse **Salad bowl customizer** with"
                      " `OrderLineItemRow` and `--a2ui- banner-tint`."
                  ).replace("--a2ui- banner-tint", "--a2ui-banner-tint")
              ),
              "change_summary": "Add salad customizer layout",
              "changed_sections": ["Layout"],
          },
      }

    def custom_build_reply(system, parts):
      del system, parts
      return {
          "classification": "custom",
          "reason": "Order row with stepper and deposit badge.",
          "basic_component": "",
          "name": "OrderLineItemRow",
          "description": "Order line item row.",
          "usage": "Use for order line items.",
          "props_schema_json": json.dumps({
              "type": "object",
              "properties": {
                  "name": {"$ref": "common_types.json#/$defs/DynamicString"}
              },
              "required": ["name"],
          }),
          "template": '<div class="row">{{name}}</div>',
          "css": (
              ".row {"
              " background: var(--a2ui-stepper-bg, #ebf0eb);"
              " color: var(--a2ui-deposit-badge-text);"
              " border-color: var(--a2ui-unregistered-pill-bg);"
              " box-shadow: var(--a2ui-card-shadow, 0 4px 16px"
              " rgba(0,0,0,0.03));"
              " }"
          ),
          "sample_props_json": json.dumps({"name": "Kale Caesar"}),
          "sample_data_json": "{}",
          "sample_components_json": "[]",
          "token_suggestions": [{
              "token": "deposit-badge-text",
              "value": "#B96815",
              "is_custom": True,
              "maps_to": "on_tertiary_container",
              "reason": "Amber deposit text",
          }],
          "notes": [],
      }

    self.llm.handlers["plan_import_merge"] = custom_plan_reply
    self.llm.handlers["build_component"] = custom_build_reply
    self.llm.handlers["repair_component"] = custom_build_reply

    iid = self.create_screens()
    self.settle(iid, "review")
    st, body = self.s.call("POST", f"/api/imports/{iid}/confirm", {})
    self.assertEqual(st, 200, body)
    st, body = self.s.call("POST", f"/api/imports/{iid}/merge/prepare", {})
    self.assertEqual(st, 200, body)
    imp = self.settle(iid, "plan_ready")

    # Merge plan already includes missing tokens read in extractions,
    # template structure_md, and constitution_md alongside component/template/constitution.
    plan_tok_map = {
        ln["detail"]["token"]: ln["detail"]["after"]
        for ln in imp["merge_plan"]["lines"]
        if ln["type"] == "tokens"
    }
    self.assertEqual(plan_tok_map.get("--a2ui-eco-badge-bg"), "#A9E8A8")
    self.assertEqual(plan_tok_map.get("--a2ui-stepper-bg"), "#ebf0eb")
    self.assertEqual(plan_tok_map.get("--a2ui-header-pill-bg"), "#E2F1E0")
    self.assertIn("--a2ui-banner-tint", plan_tok_map)

    # Apply merge and wait for component builder job to finish.
    st, body = self.s.call("POST", f"/api/imports/{iid}/merge/apply", {})
    self.assertEqual(st, 200, body)
    comp_applied = next(
        a for a in body["result"]["applied"] if a["type"] == "component"
    )
    cid = comp_applied["target"]["id"]
    wait(
        lambda: (
            lambda c: c
            if (c["job"] or {}).get("stage") in ("done", "error")
            else None
        )(self.s.call("GET", f"/api/components/{cid}")[1]["component"])
    )

    # Verify all tokens read by the drafted component (from token_suggestions,
    # CSS var(..., fallback), nested rgba() shadow fallback, and bare var(--a2ui-*))
    # were brought into settings.theme.custom_tokens.
    st, s_body = self.s.call("GET", f"/api/projects/{self.pid}/settings")
    self.assertEqual(st, 200, s_body)
    custom_tokens = s_body["settings"]["theme"]["custom_tokens"]
    self.assertEqual(custom_tokens["eco-badge-bg"]["value"], "#A9E8A8")
    self.assertEqual(custom_tokens["stepper-bg"]["value"], "#ebf0eb")
    self.assertEqual(custom_tokens["header-pill-bg"]["value"], "#E2F1E0")
    self.assertEqual(custom_tokens["deposit-badge-text"]["value"], "#B96815")
    self.assertEqual(
        custom_tokens["card-shadow"]["value"],
        "0 4px 16px rgba(0,0,0,0.03)",
    )
    self.assertIn("unregistered-pill-bg", custom_tokens)
    self.assertTrue(custom_tokens["unregistered-pill-bg"]["value"])
    self.assertIn("banner-tint", custom_tokens)

    # Dependency analyzer reports zero missing token issues!
    st, deps = self.s.call("GET", f"/api/projects/{self.pid}/dependencies")
    self.assertEqual(st, 200, deps)
    missing_issues = [
        i
        for i in deps["issues"]
        if i.get("kind")
        in ("component_missing_token", "constitution_missing_token")
    ]
    self.assertEqual(missing_issues, [])

  def test_partial_color_import_computes_complementary_m3_palette(self):
    st, _ = self.s.call("PUT", "/api/me/figma", {"token": "figd_test"})
    self.assertEqual(st, 200)

    def fake_fetch(token, url):
      del token, url
      return {
          "file_key": "K_PAL",
          "node_ids": ["1:1"],
          "file_name": "Forest Brand Kit",
          "frames": [{
              "id": "1:1",
              "name": "Home",
              "width": 412,
              "height": 915,
              "viewport_hint": "phone",
              "png": fixtures.PNG,
          }],
          "facts": {
              "file_name": "Forest Brand Kit",
              "frames": [],
              "design_system": {
                  "color_mode": "LIGHT",
                  "primary_color": "#1E6B3A",
                  "secondary_color": "#8C5A2B",
                  "background_color": "#F5F8F4",
              },
              "colors": [
                  {"hex": "#1E6B3A", "count": 14},
                  {"hex": "#8C5A2B", "count": 6},
                  {"hex": "#B8336A", "count": 4},
                  {"hex": "#F5F8F4", "count": 20},
              ],
          },
      }

    self.s.app.refs.figma_fetch = fake_fetch
    self.s.app.builder.figma_scan = lambda t, u: {
        "file_key": "K_PAL",
        "file_name": "Forest Brand Kit",
        "components": [],
    }
    # Extraction only specifies `primary`, leaving the other 21 color tokens unspecified.
    self.llm.handlers["analyze_import"] = lambda s, p: {
        "summary_md": "Forest brand kit with partial token override.",
        "extractions": [{
            "id": "",
            "kind": "token_reference",
            "title": "Brand palette",
            "description": "Forest green primary palette.",
            "image": 1,
            "box_2d": [],
            "node_id": "",
            "instances": [],
            "confidence": 0.96,
            "rationale": "Primary brand color.",
            "uses": [],
            "question": "",
            "question_options": [],
            "component": {
                "purpose": "",
                "maps_to_basic": [],
                "props_schema_json": "",
                "matches_existing": "",
            },
            "template": {
                "when_to_use": "",
                "structure_md": "",
                "slots": [],
            },
            "tokens": [{
                "token": "primary",
                "value": "#1E6B3A",
                "is_custom": False,
                "maps_to": "",
                "confidence": "exact",
                "reason": "Primary brand color",
            }],
        }],
        "questions": [],
    }
    self.llm.handlers["plan_import_merge"] = lambda s, p: {
        "summary_md": "Applies brand color palette.",
        "tokens": [{
            "token": "primary",
            "value": "#1E6B3A",
            "is_custom": False,
            "maps_to": "",
            "reason": "Primary brand color",
        }],
        "components": [],
        "templates": [],
        "constitution": {
            "constitution_md": "",
            "change_summary": "",
            "changed_sections": [],
        },
    }

    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/references",
        {
            "kind": "figma",
            "url": "https://www.figma.com/design/K_PAL/Forest?node-id=1-1",
        },
    )
    self.assertEqual(st, 201, body)
    iid = body["reference"]["id"]
    imp = self.settle(iid, "review")

    # 1. The token_reference extraction itself has all 22 COLOR_TOKENS populated.
    tok_ext = next(
        x for x in imp["extractions"] if x["kind"] == "token_reference"
    )
    ext_tokens = {t["token"]: t for t in tok_ext["payload"].get("tokens") or []}
    from core import material_color_utilities as mcu  # pylint: disable=g-import-not-at-top
    from core import settings as settings_mod  # pylint: disable=g-import-not-at-top

    expected_secondary = mcu.TonalPalette.from_hex("#8C5A2B").get_hex(40)
    expected_surface = mcu.TonalPalette.from_hex("#F5F8F4").get_hex(98)
    for col_tok in settings_mod.COLOR_TOKENS:
      self.assertIn(col_tok, ext_tokens, f"Missing {col_tok} in extraction")
    self.assertEqual(ext_tokens["primary"]["value"], "#1E6B3A")
    self.assertFalse(ext_tokens["primary"].get("complementary", False))
    self.assertEqual(ext_tokens["secondary"]["value"], expected_secondary)
    self.assertTrue(ext_tokens["secondary"].get("complementary"))
    self.assertTrue(ext_tokens["primary_container"].get("complementary"))

    # 2. Confirm and prepare merge plan; merge plan has complementary colors.
    st, body = self.s.call("POST", f"/api/imports/{iid}/confirm", {})
    self.assertEqual(st, 200, body)
    st, body = self.s.call("POST", f"/api/imports/{iid}/merge/prepare", {})
    self.assertEqual(st, 200, body)
    imp = self.settle(iid, "plan_ready")

    # 3. Apply merge and verify all 22 COLOR_TOKENS in settings.theme are adapted.
    st, body = self.s.call("POST", f"/api/imports/{iid}/merge/apply", {})
    self.assertEqual(st, 200, body)

    st, s_body = self.s.call("GET", f"/api/projects/{self.pid}/settings")
    self.assertEqual(st, 200, s_body)
    theme = s_body["settings"]["theme"]
    self.assertEqual(theme["primary"], "#1E6B3A")
    self.assertEqual(theme["secondary"], expected_secondary)
    self.assertEqual(theme["surface"], expected_surface)
    # Complementary container & on_* tokens adapted from the reference palette:
    self.assertNotEqual(
        theme["primary_container"],
        settings_mod.DEFAULT_THEME["primary_container"],
    )
    self.assertNotEqual(
        theme["secondary_container"],
        settings_mod.DEFAULT_THEME["secondary_container"],
    )
    self.assertNotEqual(
        theme["tertiary"],
        settings_mod.DEFAULT_THEME["tertiary"],
    )
    self.assertNotEqual(
        theme["surface_container"],
        settings_mod.DEFAULT_THEME["surface_container"],
    )
    self.assertNotEqual(
        theme["outline"],
        settings_mod.DEFAULT_THEME["outline"],
    )


if __name__ == "__main__":
  unittest.main()
