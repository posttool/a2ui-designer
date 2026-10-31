"""End-to-end: design system — custom components, builder, package."""

import copy
import io
import json
import os
import sys
import time
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402
from components_test import TILE  # noqa: E402
from llm.gemini import Image  # noqa: E402


def wait(fn, timeout=15.0):
  deadline = time.time() + timeout
  while time.time() < deadline:
    v = fn()
    if v:
      return v
    time.sleep(0.05)
  raise AssertionError("timed out")


def build_reply(name="StatusTimeline", cls="custom"):
  if cls != "custom":
    return {
        "classification": cls,
        "reason": "It is a restyled Button.",
        "basic_component": "Button" if cls == "basic_equivalent" else "",
        "name": name,
        "description": "Primary button",
        "usage": "",
        "props_schema_json": "",
        "template": "",
        "css": "",
        "sample_props_json": "{}",
        "sample_data_json": "{}",
        "sample_components_json": "[]",
        "token_suggestions": [{
            "token": "radius_control",
            "value": "20",
            "is_custom": False,
            "maps_to": "",
            "reason": "Pill buttons",
        }],
        "notes": [],
    }
  return {
      "classification": "custom",
      "reason": "Timeline of order steps.",
      "basic_component": "",
      "name": name,
      "description": "Vertical timeline of order status steps.",
      "usage": "Use for 2-6 sequential steps with a current step.",
      "props_schema_json": json.dumps({
          "type": "object",
          "properties": {
              "title": {"$ref": "common_types.json#/$defs/DynamicString"},
              "steps": {
                  "type": "array",
                  "items": {
                      "type": "object",
                      "properties": {"label": {"type": "string"}},
                  },
              },
          },
          "required": ["title"],
      }),
      "template": (
          '<section class="tl"><h3>{{title}}</h3><ol>'
          '<li data-each="steps as s">{{s.label}}</li></ol></section>'
      ),
      "css": ".tl{color:var(--a2ui-on-surface);background:var(--a2ui-badge)}",
      "sample_props_json": json.dumps(
          {"title": "Order", "steps": [{"label": "Placed"}]}
      ),
      "sample_data_json": "{}",
      "sample_components_json": "[]",
      "token_suggestions": [{
          "token": "badge",
          "value": "#FFE08A",
          "is_custom": True,
          "maps_to": "",
          "reason": "Badge fill",
      }],
      "notes": ["Approximated the dotted connector."],
  }


def fake_scan(token, url):
  del token, url
  return {
      "file_key": "KEY",
      "file_name": "Shop UI kit",
      "components": [
          {
              "node_id": "1:2",
              "name": "Status timeline",
              "kind": "COMPONENT_SET",
              "description": "Order steps",
              "width": 360,
              "height": 200,
              "variant_count": 2,
              "variant_props": {"State": ["Default", "Done"]},
              "property_defs": {"Title": {"type": "TEXT", "default": "Order"}},
              "default_variant_id": "1:3",
              "suggested": True,
          },
          {
              "node_id": "4:5",
              "name": "Button/Primary",
              "kind": "COMPONENT",
              "description": "",
              "width": 120,
              "height": 40,
              "variant_count": 1,
              "variant_props": {},
              "property_defs": {},
              "default_variant_id": "4:5",
              "suggested": False,
          },
      ],
      "truncated": False,
  }


def fake_detail(token, key, node_id, palette):
  del token, key
  assert "primary" in palette
  return {
      "info": {
          "node_id": node_id,
          "name": "Status timeline" if node_id == "1:2" else "Button/Primary",
          "kind": "COMPONENT_SET",
          "description": "Order steps",
          "width": 360,
          "height": 200,
          "property_defs": {"Title": {"type": "TEXT"}},
          "variant_props": {"State": ["Default", "Done"]},
      },
      "outline": {"type": "FRAME", "name": "Default", "children": []},
      "variants": ["State=Default", "State=Done"],
      "reference_png": fixtures.PNG,
      "set_png": fixtures.PNG,
  }


class ComponentsHttpTest(unittest.TestCase):

  def setUp(self):
    llm = fixtures.make_llm()
    self.compare_scores = [2, 4]

    def build(system, parts):
      del system
      text = parts[0]
      if "Button/Primary" in text:
        return build_reply("PrimaryButton", "basic_equivalent")
      return build_reply()

    def compare(system, parts):
      del system, parts
      score = self.compare_scores.pop(0) if self.compare_scores else 5
      return {
          "score": score,
          "summary": "ok",
          "mismatches": [
              {"area": "header", "issue": "title too small", "fix": "h3 18px"}
          ],
      }

    llm.handlers["build_component"] = build
    llm.handlers["repair_component"] = lambda s, p: dict(
        build_reply(), css=".tl{color:var(--a2ui-primary)}"
    )
    llm.handlers["revise_component"] = lambda s, p: dict(
        build_reply(), description="Revised timeline."
    )
    llm.handlers["compare_component"] = compare
    llm.handlers["component_sample"] = lambda s, p: {
        "sample_props_json": json.dumps({"title": "Return", "steps": []}),
        "sample_data_json": "{}",
        "sample_components_json": "[]",
    }
    self.s = fixtures.LiveServer(llm=llm)
    self.s.app.builder.figma_scan = fake_scan
    self.s.app.builder.figma_detail = fake_detail
    self.s.app.builder.figma_thumbs = lambda t, k, ids: {
        i: fixtures.PNG for i in ids
    }
    self.s.login("alice")
    st, body = self.s.call("POST", "/api/projects", {"name": "Shop App"})
    self.assertEqual(st, 201, body)
    self.pid = body["project"]["id"]

  def tearDown(self):
    self.s.close()

  def get(self, cid):
    st, body = self.s.call("GET", f"/api/components/{cid}")
    self.assertEqual(st, 200, body)
    return body["component"]

  def done(self, cid):
    return wait(
        lambda: (
            self.get(cid)
            if (self.get(cid)["job"] or {}).get("stage") in ("done", "error")
            else None
        )
    )

  def test_manual_component_catalog_run_and_package(self):
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/components",
        {"definition": TILE, "status": "active"},
    )
    self.assertEqual(st, 201, body)
    comp = body["component"]
    self.assertEqual(comp["status"], "active")
    self.assertEqual(comp["issues"], [])
    self.assertTrue(comp["has_web"])
    self.assertIn("--a2ui-surface-container", comp["token_refs"])
    cid = comp["id"]
    # Preview rendered through /render/component/{cid}.
    wait(lambda: self.get(cid)["preview_url"])
    self.assertTrue(
        any(f"/render/component/{cid}" in u for u in self.s.pool.calls)
    )
    # Duplicate names are rejected.
    st, body = self.s.call(
        "POST", f"/api/projects/{self.pid}/components", {"definition": TILE}
    )
    self.assertEqual(st, 400)
    # Render data for headless previews (token-auth).
    token = self.s.app.runs.token(cid)
    st, rd = self.s.call("GET", f"/api/render_component/{cid}?token={token}")
    self.assertEqual(st, 200, rd)
    self.assertIn("ProductTile", rd["custom_components"])
    self.assertEqual(
        rd["messages"][0]["createSurface"]["catalogId"],
        "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json",
    )
    self.assertEqual(
        rd["messages"][1]["updateComponents"]["components"][0]["catalogId"],
        "https://posttool.github.io/a2ui-designer/catalogs/projects/shop-app/"
        "catalog.json",
    )
    st, _ = self.s.call("GET", f"/api/render_component/{cid}?token=bad")
    self.assertEqual(st, 403)
    # Catalog endpoints.
    st, cat = self.s.call("GET", f"/api/projects/{self.pid}/catalog")
    pt = next(c for c in cat["components"] if c["name"] == "ProductTile")
    self.assertTrue(pt["custom"] and pt["has_web"])
    self.assertEqual(pt["component_id"], cid)
    st, cj = self.s.call("GET", f"/api/projects/{self.pid}/catalog.json")
    self.assertEqual(
        cj["$id"],
        "https://posttool.github.io/a2ui-designer/catalogs/projects/shop-app/"
        "catalog.json",
    )
    self.assertEqual(cj["protocolVersion"], "1.0")
    self.assertIn("ProductTile", cj["components"])
    # The generator prompt lists the component; runs snapshot definitions.
    st, body = self.s.call(
        "POST",
        "/api/samples",
        {
            "title": "Shoes",
            "payload": {"items": [1]},
            "group_label": "Shop",
            "genre_label": "Products",
        },
    )
    self.assertEqual(st, 201, body)
    sid = body["sample"]["id"]
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/runs",
        {"sample_ids": [sid], "viewports": ["phone"], "renderers": ["web"]},
    )
    self.assertEqual(st, 201, body)
    rid = body["run"]["id"]
    run = self.s.app.store.get("runs", rid)
    snap = run["config"]["catalog"]["custom_components"]
    self.assertEqual(snap["ProductTile"]["web"], TILE["web"])
    gen_call = wait(
        lambda: next(
            (c for c in self.s.llm.calls if c["purpose"] == "generate"), None
        )
    )
    self.assertIn("ProductTile (custom)", gen_call["system"])
    st, outs = self.s.call("GET", f"/api/runs/{rid}/outputs")
    oid = outs["outputs"][0]["id"]
    st, rdata = self.s.call(
        "GET",
        f"/api/render_data/{oid}?token={self.s.app.runs.token(oid)}",
    )
    self.assertIn("ProductTile", rdata["custom_components"])
    # Package check + zip.
    st, chk = self.s.call("GET", f"/api/projects/{self.pid}/package")
    self.assertEqual(st, 200, chk)
    self.assertEqual(chk["counts"]["custom_active"], 1)
    self.assertFalse([i for i in chk["issues"] if i["level"] == "error"], chk)
    st, raw = self.s.call(
        "GET", f"/api/export/projects/{self.pid}/package.zip", raw=True
    )
    self.assertEqual(st, 200)
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = z.namelist()
    root = "shop-app-design-package/"
    for f in chk["files"]:
      self.assertIn(root + f, names)
    ex = json.loads(z.read(root + "catalog/examples/ProductTile.json"))
    self.assertTrue(ex["valid"])
    self.assertIn(
        "--a2ui-primary:", z.read(root + "tokens/tokens.css").decode()
    )
    dtcg = json.loads(z.read(root + "tokens/design-tokens.json"))
    self.assertEqual(dtcg["color"]["primary"]["$type"], "color")
    self.assertIn(
        "DESIGN-SYSTEM COMPONENTS", z.read(root + "catalog/rules.txt").decode()
    )
    # Package page: the file listing matches the zip, and each file is
    # served individually (nested paths included).
    st, listing = self.s.call("GET", f"/api/projects/{self.pid}/package/files")
    self.assertEqual(st, 200, listing)
    self.assertEqual(listing["root"], root)
    self.assertEqual(
        sorted(root + f["path"] for f in listing["files"]),
        sorted(n for n in names if not n.endswith("/")),
    )
    st, css = self.s.call(
        "GET",
        f"/api/projects/{self.pid}/package/files/tokens/tokens.css",
        raw=True,
    )
    self.assertEqual(st, 200)
    self.assertEqual(css, z.read(root + "tokens/tokens.css"))
    st, tpl = self.s.call(
        "GET",
        f"/api/projects/{self.pid}/package/files/catalog/components/"
        "ProductTile/template.html",
        raw=True,
    )
    self.assertEqual(st, 200)
    self.assertEqual(
        tpl, z.read(root + "catalog/components/ProductTile/template.html")
    )
    st, _ = self.s.call(
        "GET", f"/api/projects/{self.pid}/package/files/nope.txt", raw=True
    )
    self.assertEqual(st, 404)
    # Deactivate removes it from the catalog.
    st, body = self.s.call(
        "PATCH", f"/api/components/{cid}", {"status": "archived"}
    )
    self.assertEqual(st, 200, body)
    st, cj = self.s.call("GET", f"/api/projects/{self.pid}/catalog.json")
    self.assertNotIn("ProductTile", cj["components"])

  def test_activation_requires_clean_definition(self):
    bad = copy.deepcopy(TILE)
    bad["web"]["template"] = "<div onclick='x()'>{{nope}}</div>"
    st, body = self.s.call(
        "POST", f"/api/projects/{self.pid}/components", {"definition": bad}
    )
    self.assertEqual(st, 201, body)
    comp = body["component"]
    self.assertTrue(comp["issues"])
    st, body = self.s.call(
        "PATCH", f"/api/components/{comp['id']}", {"status": "active"}
    )
    self.assertEqual(st, 400)
    self.assertIn("issues", body["error"])

  def test_figma_scan_import_repair_and_basic_equivalent(self):
    st, body = self.s.call("PUT", "/api/me/figma", {"token": "figd_test"})
    url = "https://www.figma.com/design/KEY/Kit?node-id=0-1"
    st, scan = self.s.call(
        "POST", f"/api/projects/{self.pid}/components/figma/scan", {"url": url}
    )
    self.assertEqual(st, 200, scan)
    self.assertEqual(scan["file_name"], "Shop UI kit")
    self.assertEqual(
        [c["suggested"] for c in scan["components"]], [True, False]
    )
    self.assertTrue(
        scan["components"][0]["thumb_url"].startswith("/files/components/")
    )
    st, raw = self.s.call("GET", scan["components"][0]["thumb_url"], raw=True)
    self.assertEqual(st, 200)
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/components/import",
        {
            "source": "figma",
            "url": url,
            "node_ids": ["1:2", "4:5"],
            "file_name": "Shop UI kit",
            "names": {"1:2": "Status timeline", "4:5": "Button/Primary"},
        },
    )
    self.assertEqual(st, 201, body)
    ids = [c["id"] for c in body["components"]]
    self.assertEqual(len(ids), 2)
    # The project shows its linked Figma file.
    st, proj = self.s.call("GET", f"/api/projects/{self.pid}")
    self.assertEqual(
        proj["project"]["design_sources"],
        [{
            "kind": "figma",
            "key": "KEY",
            "name": "Shop UI kit",
            "url": "https://www.figma.com/design/KEY",
            "references": 0,
            "components": 2,
        }],
    )
    st, lst = self.s.call("GET", "/api/projects")
    self.assertEqual(lst["projects"][0]["design_sources"][0]["kind"], "figma")
    timeline = self.done(ids[0])
    self.assertEqual(timeline["job"]["stage"], "done", timeline)
    self.assertEqual(timeline["name"], "StatusTimeline")
    self.assertEqual(timeline["status"], "draft")
    self.assertEqual(timeline["classification"], "custom")
    self.assertEqual(timeline["issues"], [], timeline)
    self.assertTrue(timeline["reference_url"])
    self.assertTrue(timeline["preview_url"])
    # First compare scored 2 → one repair round → 4.
    self.assertEqual(timeline["fidelity"]["score"], 4)
    self.assertTrue(timeline["fidelity"].get("repaired"))
    self.assertIn("--a2ui-primary", timeline["token_refs"])
    self.assertEqual(
        timeline["source"]["variant_props"], {"State": ["Default", "Done"]}
    )
    build_call = next(
        c for c in self.s.llm.calls if c["purpose"] == "build_component"
    )
    self.assertTrue(any(isinstance(p, Image) for p in build_call["parts"]))
    self.assertIn("outline_of_default_variant", build_call["parts"][0])
    # Accept with token suggestions → custom token lands in the theme.
    st, body = self.s.call(
        "PATCH",
        f"/api/components/{ids[0]}",
        {"status": "active", "apply_token_suggestions": True},
    )
    self.assertEqual(st, 200, body)
    st, s = self.s.call("GET", f"/api/projects/{self.pid}/settings")
    self.assertIn("badge", s["settings"]["theme"]["custom_tokens"])
    # The Button is a Basic equivalent: tokens, not a component.
    btn = self.done(ids[1])
    self.assertEqual(btn["classification"], "basic_equivalent")
    self.assertEqual(btn["basic_equivalent"]["component"], "Button")
    st, body = self.s.call(
        "PATCH", f"/api/components/{ids[1]}", {"status": "active"}
    )
    self.assertEqual(st, 400)
    st, body = self.s.call("POST", f"/api/components/{ids[1]}/apply_tokens")
    self.assertEqual(st, 200, body)
    self.assertEqual(body["component"]["status"], "archived")
    st, s = self.s.call("GET", f"/api/projects/{self.pid}/settings")
    self.assertEqual(s["settings"]["theme"]["radius_control"], 20)
    # Editor helpers.
    st, rev = self.s.call(
        "POST", f"/api/components/{ids[0]}/revise", {"instruction": "tighter"}
    )
    self.assertEqual(st, 200, rev)
    self.assertEqual(rev["definition"]["description"], "Revised timeline.")
    self.assertEqual(rev["definition"]["name"], "StatusTimeline")
    self.assertEqual(
        self.get(ids[0])["definition"]["description"],
        "Vertical timeline of order status steps.",
    )
    st, smp = self.s.call("POST", f"/api/components/{ids[0]}/sample", {})
    self.assertEqual(smp["sample"]["props"]["title"], "Return")
    st, body = self.s.call("POST", f"/api/components/{ids[0]}/recheck")
    self.assertEqual(st, 200, body)
    again = self.done(ids[0])
    self.assertEqual(again["fidelity"]["score"], 5)

  def test_describe_and_screenshot_sources(self):
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/components/import",
        {"source": "describe", "text": "A timeline of order steps"},
    )
    self.assertEqual(st, 201, body)
    c = self.done(body["components"][0]["id"])
    self.assertEqual(c["classification"], "custom")
    self.assertIsNone(c["fidelity"])  # no reference image to compare
    import base64  # pylint: disable=g-import-not-at-top

    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/components/import",
        {
            "source": "screenshot",
            "image": {
                "data_url": (
                    "data:image/png;base64,"
                    + base64.b64encode(fixtures.PNG).decode()
                ),
                "name": "timeline.png",
            },
            "hint": "the card on the right",
        },
    )
    self.assertEqual(st, 201, body)
    c2 = self.done(body["components"][0]["id"])
    self.assertEqual(c2["name"], "StatusTimeline2")  # unique name
    self.assertTrue(c2["reference_url"])
    self.assertIsNotNone(c2["fidelity"])
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/components/import",
        {"source": "nope"},
    )
    self.assertEqual(st, 400)

  def test_legacy_settings_components_migrate(self):
    st, s = self.s.call("GET", f"/api/projects/{self.pid}/settings")
    settings = s["settings"]
    settings["catalog"]["custom_components"] = {
        "Chart": {"type": "object", "properties": {"series": {"type": "array"}}}
    }
    settings["catalog"]["proposed_components"] = [
        {"name": "Callout", "purpose": "Callout banner", "props_schema": {}}
    ]
    st, body = self.s.call(
        "PUT", f"/api/projects/{self.pid}/settings", settings
    )
    self.assertEqual(st, 200, body)
    st, body = self.s.call("GET", f"/api/projects/{self.pid}/components")
    by = {c["name"]: c for c in body["components"]}
    self.assertEqual(by["Chart"]["status"], "active")
    self.assertEqual(by["Chart"]["source"]["kind"], "legacy")
    self.assertFalse(by["Chart"]["has_web"])
    self.assertEqual(by["Callout"]["status"], "draft")
    st, s = self.s.call("GET", f"/api/projects/{self.pid}/settings")
    self.assertEqual(s["settings"]["catalog"]["custom_components"], {})
    st, chk = self.s.call("GET", f"/api/projects/{self.pid}/package")
    self.assertTrue(
        any(
            i["level"] == "error" and i.get("component") == "Chart"
            for i in chk["issues"]
        )
    )

  def test_auto_repair_validation_issues_and_propose_fix(self):
    # 1) When build_component returns a draft with validation issues (e.g. an
    # undeclared {{missing_prop}}), _run_job automatically runs a repair pass.
    calls = {"build": 0, "repair": 0}

    def broken_then_fixed_build(system, parts):
      del system, parts
      calls["build"] += 1
      bad = build_reply("BrokenDraft")
      bad["template"] = '<div class="tl">{{title}} {{missing_prop}}</div>'
      return bad

    def repair_validation(system, parts):
      del system
      calls["repair"] += 1
      self.assertIn("Validation errors", parts[0])
      self.assertIn("missing_prop", parts[0])
      fixed = build_reply("BrokenDraft")
      fixed["notes"] = ["Removed undeclared {{missing_prop}} binding."]
      return fixed

    self.s.llm.handlers["build_component"] = broken_then_fixed_build
    self.s.llm.handlers["repair_component"] = repair_validation
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/components/import",
        {"source": "describe", "text": "A status card"},
    )
    self.assertEqual(st, 201, body)
    c = self.done(body["components"][0]["id"])
    self.assertEqual(calls["build"], 1)
    self.assertGreaterEqual(calls["repair"], 1)
    self.assertEqual(c["issues"], [], c)

    # 2) Interactive revise with fix=True (empty instruction) fixes a draft
    # that has blocking validation issues and returns fixed_issues.
    bad_def = copy.deepcopy(TILE)
    bad_def["name"] = "NeedsFixTile"
    bad_def["web"][
        "template"
    ] = '<div onclick="evil()">{{title}} {{ghost}}</div>'
    st, body = self.s.call(
        "POST", f"/api/projects/{self.pid}/components", {"definition": bad_def}
    )
    self.assertEqual(st, 201, body)
    broken = body["component"]
    self.assertTrue(broken["issues"])
    cid = broken["id"]

    revise_prompts = []

    def revise_fix(system, parts):
      del system
      revise_prompts.append(parts[0])
      fixed = build_reply("NeedsFixTile")
      fixed["notes"] = ["Removed onclick attribute and undeclared {{ghost}}."]
      return fixed

    self.s.llm.handlers["revise_component"] = revise_fix
    st, rev = self.s.call(
        "POST",
        f"/api/components/{cid}/revise",
        {"fix": True, "issues": broken["issues"]},
    )
    self.assertEqual(st, 200, rev)
    self.assertTrue(revise_prompts)
    self.assertIn("Validation errors", revise_prompts[0])
    self.assertEqual(rev["issues"], [])
    self.assertTrue(rev["fixed_issues"])
    self.assertIn("onclick", " ".join(rev["fixed_issues"]))
    # Applying the proposed definition allows clean activation.
    st, patched = self.s.call(
        "PATCH",
        f"/api/components/{cid}",
        {"definition": rev["definition"], "status": "active"},
    )
    self.assertEqual(st, 200, patched)
    self.assertEqual(patched["component"]["status"], "active")
    self.assertEqual(patched["component"]["issues"], [])

  def test_component_strategies_lowering_and_to_template(self):
    basic_only_def = {
        "name": "MetricCallout",
        "description": (
            "Single KPI metric callout composed of Basic components."
        ),
        "usage": "Use for top-level KPI metrics.",
        "strategy": "basic_composition",
        "props": {
            "type": "object",
            "properties": {
                "label": {"$ref": "common_types.json#/$defs/DynamicString"},
                "value": {"$ref": "common_types.json#/$defs/DynamicString"},
            },
            "required": ["label", "value"],
        },
        "composition": {
            "root": "card",
            "components": [
                {"id": "card", "component": "Card", "child": "col"},
                {
                    "id": "col",
                    "component": "Column",
                    "children": ["lbl", "val"],
                },
                {
                    "id": "lbl",
                    "component": "Text",
                    "text": "{{label}}",
                    "variant": "caption",
                },
                {
                    "id": "val",
                    "component": "Text",
                    "text": "## {{value}}",
                },
            ],
        },
        "sample": {
            "props": {"label": "Revenue", "value": "$48.2K"},
            "data": {},
            "components": [],
        },
    }
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/components",
        {"definition": basic_only_def, "status": "active"},
    )
    self.assertEqual(st, 201, body)
    comp = body["component"]
    cid = comp["id"]
    self.assertEqual(comp["status"], "active")
    self.assertEqual(comp["issues"], [])
    self.assertTrue(comp["has_composition"])
    self.assertEqual(comp["strategy"], "basic_composition")

    # Render data lowers MetricCallout into Basic components automatically.
    token = self.s.app.runs.token(cid)
    st, rd = self.s.call("GET", f"/api/render_component/{cid}?token={token}")
    self.assertEqual(st, 200, rd)
    rendered_comps = rd["messages"][1]["updateComponents"]["components"]
    self.assertEqual(rendered_comps[0]["component"], "Card")

    # Promote component to a reusable Basic Catalog template row.
    st, tpl_res = self.s.call("POST", f"/api/components/{cid}/to_template", {})
    self.assertEqual(st, 201, tpl_res)
    self.assertEqual(tpl_res["template"]["name"], "MetricCallout")
    st, tpls = self.s.call("GET", f"/api/projects/{self.pid}/templates")
    self.assertEqual(st, 200, tpls)
    self.assertTrue(
        any(t["name"] == "MetricCallout" for t in tpls["templates"])
    )

    # The native-targets endpoint is gone.
    st, _ = self.s.call("POST", f"/api/components/{cid}/targets", {})
    self.assertIn(st, (404, 405))

    # Package check and zip export include composition.json and no native code.
    wait(lambda: self.get(cid)["preview_url"])
    st, chk = self.s.call("GET", f"/api/projects/{self.pid}/package")
    self.assertEqual(st, 200, chk)
    self.assertFalse([i for i in chk["issues"] if i["level"] == "error"], chk)
    st, raw = self.s.call(
        "GET", f"/api/export/projects/{self.pid}/package.zip", raw=True
    )
    self.assertEqual(st, 200)
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = z.namelist()
    root = "shop-app-design-package/"
    for f in chk["files"]:
      self.assertIn(root + f, names)
    self.assertIn(
        root + "catalog/components/MetricCallout/composition.json", names
    )
    self.assertFalse(
        [n for n in names if n.endswith((".kt", ".swift"))], names
    )

  def test_basic_fit_threshold_decides_auto_strategy_and_restrategize(self):
    # A Figma node whose structure the Basic catalog cannot express well:
    # free-form layout, custom vector artwork, an absolutely positioned notch.
    messy = {
        "type": "COMPONENT",
        "name": "Boarding pass",
        "absoluteBoundingBox": {"width": 320, "height": 200},
        "children": [
            {
                "type": "VECTOR",
                "name": "plane path",
                "absoluteBoundingBox": {"width": 240, "height": 80},
            },
            {
                "type": "ELLIPSE",
                "name": "notch",
                "layoutPositioning": "ABSOLUTE",
                "absoluteBoundingBox": {"width": 40, "height": 40},
            },
            {
                "type": "TEXT",
                "name": "Gate",
                "characters": "B12",
                "style": {"fontSize": 24, "fontWeight": 700},
                "absoluteBoundingBox": {"width": 80, "height": 30},
            },
        ],
    }

    def messy_detail(token, key, node_id, palette):
      d = fake_detail(token, key, node_id, palette)
      return dict(d, raw_node=messy)

    self.s.app.builder.figma_detail = messy_detail
    self.s.call("PUT", "/api/me/figma", {"token": "figd_test"})
    st, body = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/components/import",
        {
            "source": "figma",
            "url": "https://www.figma.com/design/KEY/Shop?node-id=0-1",
            "node_ids": ["1:2"],
            "file_name": "Shop UI kit",
            "names": {"1:2": "Boarding pass"},
        },
    )
    self.assertEqual(st, 201, body)
    cid = body["components"][0]["id"]
    comp = self.done(cid)
    self.assertEqual(comp["classification"], "custom")
    fit = comp["basic_fit"]
    self.assertEqual(fit["source"], "figma")
    self.assertLess(fit["score"], 0.7)
    self.assertTrue(
        any("vector" in s or "absolute" in s for s in fit["summary"]), fit
    )
    dec = comp["strategy_decision"]
    self.assertTrue(dec["auto"])
    self.assertEqual(dec["threshold"], 0.7)
    # Default threshold 0.7 → below → custom web component (strategy empty).
    self.assertEqual(dec["strategy"], "web_template")
    self.assertEqual(comp["strategy"], "")
    self.assertTrue(comp["has_web"])
    self.assertTrue(comp["has_composition"])

    # Lowering the threshold to 0 prefers Basic catalog templates: a dry run
    # reports the flip, applying it rewrites the strategy without any LLM call.
    st, sres = self.s.call("GET", f"/api/projects/{self.pid}/settings")
    self.assertEqual(st, 200, sres)
    settings = sres["settings"]
    settings["catalog"]["basic_fit_threshold"] = 0
    st, sres = self.s.call(
        "PUT", f"/api/projects/{self.pid}/settings", {"settings": settings}
    )
    self.assertEqual(st, 200, sres)
    self.assertEqual(sres["settings"]["catalog"]["basic_fit_threshold"], 0.0)
    st, dry = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/components/restrategize",
        {"dry_run": True},
    )
    self.assertEqual(st, 200, dry)
    self.assertEqual(dry["changed"], 1, dry)
    self.assertEqual(self.get(cid)["strategy"], "")
    st, res = self.s.call(
        "POST", f"/api/projects/{self.pid}/components/restrategize", {}
    )
    self.assertEqual(st, 200, res)
    self.assertEqual(res["changed"], 1, res)
    comp = self.get(cid)
    self.assertEqual(comp["strategy"], "basic_composition")
    self.assertTrue(comp["strategy_decision"]["auto"])
    self.assertEqual(comp["strategy_decision"]["threshold"], 0.0)

    # Pinning a strategy on the component takes it out of the auto rule…
    st, pres = self.s.call(
        "PATCH",
        f"/api/components/{cid}",
        {"definition": dict(comp["definition"], strategy="web_template")},
    )
    self.assertEqual(st, 200, pres)
    self.assertEqual(pres["component"]["strategy"], "web_template")
    self.assertFalse(pres["component"]["strategy_decision"]["auto"])
    st, res = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/components/restrategize",
        {"dry_run": True},
    )
    self.assertEqual(res["changed"], 0, res)
    self.assertEqual(res["items"][0]["status"], "pinned")

    # …and choosing Auto again re-decides immediately from the recorded fit.
    st, pres = self.s.call(
        "PATCH",
        f"/api/components/{cid}",
        {"definition": dict(pres["component"]["definition"], strategy="")},
    )
    self.assertEqual(st, 200, pres)
    self.assertEqual(pres["component"]["strategy"], "basic_composition")
    self.assertTrue(pres["component"]["strategy_decision"]["auto"])

    # Threshold out of range is clamped; an explicit project-wide strategy
    # makes the threshold irrelevant.
    settings["catalog"]["basic_fit_threshold"] = 7
    settings["catalog"]["component_strategy"] = "web_template"
    st, sres = self.s.call(
        "PUT", f"/api/projects/{self.pid}/settings", {"settings": settings}
    )
    self.assertEqual(sres["settings"]["catalog"]["basic_fit_threshold"], 1.0)
    st, res = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/components/restrategize",
        {"dry_run": True},
    )
    self.assertEqual(res["component_strategy"], "web_template")
    self.assertEqual(res["items"][0]["to"], "web_template")
    self.assertEqual(res["changed"], 1, res)


if __name__ == "__main__":
  unittest.main()
