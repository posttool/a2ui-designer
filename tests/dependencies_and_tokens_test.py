"""Tests for the 4-layer Design System Dependency Graph, Independent Token Revisions, and Run Token Re-Theming."""

from __future__ import annotations

import copy
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402

from core import dependencies as deps_mod  # noqa: E402
from core import material_color_utilities as mcu  # noqa: E402
from core import settings as settings_mod  # noqa: E402
from exports import package as package_mod  # noqa: E402


def wait(fn, timeout=15.0):
  deadline = time.time() + timeout
  while time.time() < deadline:
    v = fn()
    if v:
      return v
    time.sleep(0.05)
  raise AssertionError("timed out")


class ThemeDiffAndDepsUnitTest(unittest.TestCase):

  def test_theme_hash_and_diff(self):
    t1 = copy.deepcopy(settings_mod.DEFAULT_THEME)
    t2 = copy.deepcopy(settings_mod.DEFAULT_THEME)
    self.assertEqual(
        settings_mod.compute_theme_hash(t1),
        settings_mod.compute_theme_hash(t2),
    )

    t2["primary"] = "#7A2E1D"
    t2["radius_card"] = 16
    t2["custom_tokens"] = {
        "brand-accent": {
            "value": "#FF5722",
            "type": "color",
            "maps_to": "tertiary",
        }
    }
    self.assertNotEqual(
        settings_mod.compute_theme_hash(t1),
        settings_mod.compute_theme_hash(t2),
    )

    diff = settings_mod.compute_theme_diff(t1, t2)
    tokens_changed = {c["token"]: c for c in diff["changed"]}
    self.assertIn("primary", tokens_changed)
    self.assertEqual(tokens_changed["primary"]["before"], "#0B57D0")
    self.assertEqual(tokens_changed["primary"]["after"], "#7A2E1D")
    self.assertIn("radius_card", tokens_changed)
    self.assertIn("--a2ui-brand-accent", tokens_changed)
    self.assertIn("--a2ui-primary", diff["changed_css_vars"])
    self.assertIn("--a2ui-radius-card", diff["changed_css_vars"])
    self.assertIn("--a2ui-radius-lg", diff["changed_css_vars"])
    self.assertIn("--a2ui-brand-accent", diff["changed_css_vars"])

  def test_package_css_vars_and_google_fonts_import(self):
    theme = copy.deepcopy(settings_mod.DEFAULT_THEME)
    theme["font_family"] = "JetBrains Mono"
    theme["font_family_heading"] = "Noto Serif"
    cv = package_mod.css_vars(theme)
    self.assertTrue(cv["--a2ui-font"].startswith('"JetBrains Mono"'))
    self.assertTrue(cv["--a2ui-font"].endswith("monospace"))
    self.assertTrue(cv["--a2ui-font-heading"].startswith('"Noto Serif"'))
    self.assertTrue(cv["--a2ui-font-heading"].endswith("serif"))
    css = package_mod.tokens_css(theme)
    self.assertIn("fonts.googleapis.com/css?family=JetBrains+Mono:", css)
    self.assertIn("Noto+Serif:", css)

  def test_box_spacing_tokens_and_canonical_margins(self):
    self.assertEqual(settings_mod.parse_box_px(16), (16, 16, 16, 16))
    self.assertEqual(settings_mod.parse_box_px("12px 20px"), (12, 20, 12, 20))
    self.assertEqual(
        settings_mod.parse_box_px("12px 20px 8px"), (12, 20, 8, 20)
    )
    self.assertEqual(
        settings_mod.parse_box_px("12px 20px 8px 16px"), (12, 20, 8, 16)
    )
    self.assertEqual(
        settings_mod.coerce_token_value("card_padding", "16px 16px 16px 16px"),
        16,
    )
    self.assertEqual(
        settings_mod.coerce_token_value("card_padding", "12px 20px 8px 16px"),
        "12px 20px 8px 16px",
    )
    self.assertEqual(
        settings_mod.coerce_token_value("card_margin", [4, 8, 12, 8]),
        "4px 8px 12px 8px",
    )
    self.assertEqual(
        settings_mod.coerce_token_value("item_margin", "6px 10px"),
        "6px 10px 6px 10px",
    )

    updated, applied = settings_mod.apply_theme_changes(
        settings_mod.DEFAULT_THEME,
        [
            {"token": "card_padding", "value": "12px 20px 8px 16px"},
            {"token": "surface_padding", "value": "24px 16px"},
            {"token": "--a2ui-card-margin", "value": "8px 4px 12px 4px"},
            {"token": "--a2ui-item-margin", "value": "6px 8px 6px 8px"},
        ],
    )
    self.assertEqual(len(applied), 4)
    self.assertEqual(updated["card_padding"], "12px 20px 8px 16px")
    self.assertEqual(updated["surface_padding"], "24px 16px 24px 16px")
    self.assertEqual(updated["card_margin"], "8px 4px 12px 4px")
    self.assertEqual(updated["item_margin"], "6px 8px 6px 8px")

    cv = package_mod.css_vars(updated)
    self.assertEqual(cv["--a2ui-card-padding"], "12px 20px 8px 16px")
    self.assertEqual(cv["--a2ui-surface-padding"], "24px 16px 24px 16px")
    self.assertEqual(cv["--a2ui-card-margin"], "8px 4px 12px 4px")
    self.assertEqual(cv["--a2ui-item-margin"], "6px 8px 6px 8px")

  def test_material_color_utilities_hct_tonal_palette_and_contrast(self):
    # Official material-color-utilities test vectors for Blue (#0000FF).
    blue = mcu.argb_from_hex("#0000FF")
    hct_blue = mcu.Hct.from_int(blue)
    self.assertAlmostEqual(hct_blue.hue, 282.788, places=2)
    self.assertAlmostEqual(hct_blue.chroma, 87.231, places=2)
    self.assertAlmostEqual(hct_blue.tone, 32.302, places=2)
    self.assertEqual(hct_blue.to_int(), blue)

    tp = mcu.TonalPalette.from_int(blue)
    self.assertEqual(mcu.hex_from_argb(tp.tone(0)), "#000000")
    self.assertEqual(mcu.hex_from_argb(tp.tone(10)), "#00006E")
    self.assertEqual(mcu.hex_from_argb(tp.tone(20)), "#0001AC")
    self.assertEqual(mcu.hex_from_argb(tp.tone(30)), "#0000EF")
    self.assertEqual(mcu.hex_from_argb(tp.tone(40)), "#343DFF")
    self.assertEqual(mcu.hex_from_argb(tp.tone(50)), "#5A64FF")
    self.assertEqual(mcu.hex_from_argb(tp.tone(60)), "#7C84FF")
    self.assertEqual(mcu.hex_from_argb(tp.tone(70)), "#9DA3FF")
    self.assertEqual(mcu.hex_from_argb(tp.tone(80)), "#BEC2FF")
    self.assertEqual(mcu.hex_from_argb(tp.tone(90)), "#E0E0FF")
    self.assertEqual(mcu.hex_from_argb(tp.tone(95)), "#F1EFFF")
    self.assertEqual(mcu.hex_from_argb(tp.tone(99)), "#FFFBFF")
    self.assertEqual(mcu.hex_from_argb(tp.tone(100)), "#FFFFFF")

    # TemperatureCache complement test vectors.
    self.assertEqual(
        mcu.hex_from_argb(
            mcu.TemperatureCache(mcu.Hct.from_int(0xFFFF0000))
            .get_complement()
            .to_int()
        ),
        "#007BFC",
    )
    self.assertEqual(
        mcu.hex_from_argb(
            mcu.TemperatureCache(mcu.Hct.from_int(0xFF0000FF))
            .get_complement()
            .to_int()
        ),
        "#9D0002",
    )

    # Blend.harmonize test vector.
    self.assertEqual(
        mcu.hex_from_argb(mcu.Blend.harmonize(0xFFFF0000, 0xFF0000FF)),
        "#FB0057",
    )

    # Contrast ratio test vector.
    self.assertAlmostEqual(
        mcu.Contrast.ratio_of_tones(0.0, 100.0), 21.0, places=2
    )

  def test_expand_complementary_color_tokens_adapts_reference_palette(self):
    # Partial override with primary + secondary + dark surface.
    expanded = settings_mod.expand_complementary_color_tokens(
        [
            {"token": "primary", "value": "#2E6B3E"},
            {"token": "secondary", "value": "#D97724"},
            {"token": "surface", "value": "#111612"},
        ],
        current_theme=settings_mod.DEFAULT_THEME,
    )
    by_tok = {t["token"]: t for t in expanded}
    for tok_name in settings_mod.COLOR_TOKENS:
      self.assertIn(tok_name, by_tok, f"Missing color token {tok_name}")
      self.assertTrue(mcu.is_valid_hex(by_tok[tok_name]["value"]))

    # Explicit tokens preserved verbatim and not marked complementary.
    self.assertEqual(by_tok["primary"]["value"], "#2E6B3E")
    self.assertFalse(by_tok["primary"].get("complementary", False))
    self.assertEqual(by_tok["secondary"]["value"], "#D97724")
    self.assertFalse(by_tok["secondary"].get("complementary", False))
    self.assertEqual(by_tok["surface"]["value"], "#111612")
    self.assertFalse(by_tok["surface"].get("complementary", False))

    # Dark surface -> dark scheme + light on_surface with >= 4.5 contrast.
    self.assertEqual(by_tok["scheme"]["value"], "dark")
    self.assertTrue(by_tok["primary_container"].get("complementary"))
    surf_tone = mcu.lstar_from_argb(
        mcu.argb_from_hex(by_tok["surface"]["value"])
    )
    on_surf_tone = mcu.lstar_from_argb(
        mcu.argb_from_hex(by_tok["on_surface"]["value"])
    )
    self.assertGreaterEqual(
        mcu.Contrast.ratio_of_tones(surf_tone, on_surf_tone), 4.5
    )

    # When all 22 COLOR_TOKENS are explicitly specified, none are overwritten.
    full_raw = [
        {"token": k, "value": "#123456"}
        for k in settings_mod.ORDERED_COLOR_TOKENS
    ]
    full_expanded = settings_mod.expand_complementary_color_tokens(
        full_raw, current_theme=settings_mod.DEFAULT_THEME
    )
    for item in full_expanded:
      if item["token"] in settings_mod.COLOR_TOKENS:
        self.assertEqual(item["value"], "#123456")
        self.assertFalse(item.get("complementary", False))


class DependenciesAndTokenRevisionsHttpTest(unittest.TestCase):

  def setUp(self):
    self.s = fixtures.LiveServer()
    self.s.login("alice")

  def tearDown(self):
    self.s.close()

  def _create_project(self, name="Design System App"):
    st, body = self.s.call("POST", "/api/projects", {"name": name})
    self.assertEqual(st, 201, body)
    return body["project"]

  def test_independent_token_revisions_and_revert(self):
    p = self._create_project()
    pid = p["id"]

    # T1 is automatically seeded on project creation.
    self.assertEqual(p["current_token_revision"]["number"], 1)
    self.assertEqual(p["stats"]["token_revisions"], 1)

    st, body = self.s.call("GET", f"/api/projects/{pid}/token_revisions")
    self.assertEqual(st, 200, body)
    revs = body["token_revisions"]
    self.assertEqual(len(revs), 1)
    t1 = revs[0]
    self.assertEqual(t1["number"], 1)

    # Updating non-theme settings (e.g. renderers) should NOT create T2.
    st, sbody = self.s.call("GET", f"/api/projects/{pid}/settings")
    settings = sbody["settings"]
    settings["renderers"] = ["web", "android_compose"]
    st, ubody = self.s.call(
        "PUT", f"/api/projects/{pid}/settings", {"settings": settings}
    )
    self.assertEqual(st, 200, ubody)
    self.assertEqual(ubody["token_revision"]["number"], 1)

    # Updating theme tokens creates T2 with structured diff.
    settings["theme"]["primary"] = "#7A2E1D"
    settings["theme"]["radius_card"] = 20
    st, ubody = self.s.call(
        "PUT",
        f"/api/projects/{pid}/settings",
        {"settings": settings, "source": "design-tokens"},
    )
    self.assertEqual(st, 200, ubody)
    t2 = ubody["token_revision"]
    self.assertEqual(t2["number"], 2)
    self.assertEqual(t2["source"], "design-tokens")
    changed_keys = {c["token"] for c in t2["diff"]["changed"]}
    self.assertEqual(changed_keys, {"primary", "radius_card"})

    # Reverting to T1 creates T3 with T1's theme values.
    st, rbody = self.s.call(
        "POST",
        f"/api/projects/{pid}/token_revisions/{t1['id']}/revert",
    )
    self.assertEqual(st, 200, rbody)
    t3 = rbody["token_revision"]
    self.assertEqual(t3["number"], 3)
    self.assertEqual(t3["source"], "revert")
    self.assertEqual(rbody["settings"]["theme"]["primary"], "#0B57D0")
    self.assertEqual(rbody["settings"]["theme"]["radius_card"], 24)

  def test_dependency_graph_and_package_validation(self):
    p = self._create_project()
    pid = p["id"]

    # 1. Create a draft custom component that uses --a2ui-primary.
    comp_def = {
        "name": "CareSummaryCard",
        "description": "Care summary card.",
        "usage": "Use at the top of elder care screens.",
        "props": {
            "type": "object",
            "properties": {
                "title": {"$ref": "common_types.json#/$defs/DynamicString"}
            },
            "required": ["title"],
        },
        "web": {
            "template": '<div class="care">{{title}}</div>',
            "css": ".care { color: var(--a2ui-primary); }",
        },
        "sample": {
            "props": {"title": "Morning Meds"},
            "data": {},
            "components": [],
        },
    }
    st, cbody = self.s.call(
        "POST",
        f"/api/projects/{pid}/components",
        {"definition": comp_def, "status": "draft"},
    )
    self.assertEqual(st, 201, cbody)
    cid = cbody["component"]["id"]

    # 2. Insert a template directly via storage to test template dependency edges.
    self.s.app.store.insert(
        "templates",
        {
            "id": "tpl_meds",
            "project_id": pid,
            "name": "Medication Checklist",
            "description": "Daily medication schedule.",
            "when_to_use": "Use for 3+ medications.",
            "components": ["CareSummaryCard", "CheckBox", "DateTimeInput"],
            "structure_md": "- `CareSummaryCard` header\n- `CheckBox` rows",
            "status": "active",
            "created_at": "2026-10-18T00:00:00Z",
            "updated_at": "2026-10-18T00:00:00Z",
        },
    )

    # 3. Disable `DateTimeInput` in catalog settings.
    st, sbody = self.s.call("GET", f"/api/projects/{pid}/settings")
    settings = sbody["settings"]
    settings["catalog"]["disabled_components"] = ["DateTimeInput"]
    self.s.call("PUT", f"/api/projects/{pid}/settings", {"settings": settings})

    # 4. Save a constitution revision referencing CareSummaryCard, DateTimeInput,
    #    Medication Checklist template, --a2ui-primary, --a2ui-missing-tok, and style=watercolor.
    const_md = (
        "## Hierarchy & Layout\n- Always open with `CareSummaryCard` above the"
        " fold.\n- Follow the **Medication Checklist** template when listing 3+"
        " items.\n\n## Controls & Tokens\n- Use `DateTimeInput` for"
        " rescheduling reminders.\n- Highlight active status using"
        " `--a2ui-primary` and `--a2ui-missing-tok`.\n- Render header"
        " illustrations with `style=watercolor`.\n"
    )
    st, rev_body = self.s.call(
        "POST",
        f"/api/projects/{pid}/revisions",
        {"text_md": const_md, "summary": "Add rules"},
    )
    self.assertEqual(st, 201, rev_body)
    self.assertEqual(
        rev_body["revision"]["ds_snapshot"]["token_revision_number"], 1
    )

    # 5. Fetch the dependency graph.
    st, dbody = self.s.call("GET", f"/api/projects/{pid}/dependencies")
    self.assertEqual(st, 200, dbody)

    cdeps = dbody["constitution_deps"]
    comp_Statuses = {c["name"]: c["status"] for c in cdeps["components"]}
    self.assertEqual(comp_Statuses.get("CareSummaryCard"), "draft")
    self.assertEqual(comp_Statuses.get("DateTimeInput"), "disabled")

    tpl_names = {t["name"]: t["status"] for t in cdeps["templates"]}
    self.assertEqual(tpl_names.get("Medication Checklist"), "active")

    tok_statuses = {t["css_var"]: t["status"] for t in cdeps["tokens"]}
    self.assertEqual(tok_statuses.get("--a2ui-primary"), "defined")
    self.assertEqual(tok_statuses.get("--a2ui-missing-tok"), "undefined")

    img_statuses = {im["slug"]: im["status"] for im in cdeps["imagery"]}
    self.assertEqual(img_statuses.get("watercolor"), "missing")

    # Reverse used_by map should attribute CareSummaryCard to both R1 and Medication Checklist.
    used_by_comp = dbody["used_by"]["components"]["CareSummaryCard"]
    self.assertIn("R1", used_by_comp["constitution_rules"])
    self.assertIn("Medication Checklist", used_by_comp["templates"])

    # Issues should flag disabled basic component, draft custom component, undefined token, etc.
    issue_kinds = {i["kind"] for i in dbody["issues"]}
    self.assertIn("constitution_draft_component", issue_kinds)
    self.assertIn("constitution_disabled_component", issue_kinds)
    self.assertIn("constitution_missing_token", issue_kinds)
    self.assertIn("template_disabled_component", issue_kinds)

    # Package readiness endpoint should also surface these cross-layer issues.
    st, pkg_body = self.s.call("GET", f"/api/projects/{pid}/package")
    self.assertEqual(st, 200, pkg_body)
    pkg_issue_kinds = {
        i.get("kind") for i in pkg_body["issues"] if i.get("kind")
    }
    self.assertIn("constitution_disabled_component", pkg_issue_kinds)

    # Activating CareSummaryCard clears the draft component warning.
    st, act_body = self.s.call(
        "PATCH", f"/api/components/{cid}", {"status": "active"}
    )
    self.assertEqual(st, 200, act_body)
    st, dbody2 = self.s.call("GET", f"/api/projects/{pid}/dependencies")
    issue_kinds2 = {i["kind"] for i in dbody2["issues"]}
    self.assertNotIn("constitution_draft_component", issue_kinds2)

  def test_run_token_snapshot_isolation_and_zero_llm_retheme(self):
    p = self._create_project("Run Token Isolation")
    pid = p["id"]

    # Synthesize a sample and start a run under initial tokens (T1, primary=#0B57D0).
    st, jbody = self.s.call(
        "POST",
        "/api/samples/synthesize",
        {"genres": ["elder_care"], "per_genre": 1, "set_name": "MiniSet"},
    )
    self.assertEqual(st, 200, jbody)
    jid = jbody["job"]["id"]
    wait(
        lambda: (lambda j: j if j["status"] in ("done", "failed") else None)(
            self.s.call("GET", f"/api/jobs/{jid}")[1]["job"]
        )
    )
    st, sbody = self.s.call("GET", "/api/samples")
    sample_id = sbody["samples"][0]["id"]

    st, rbody = self.s.call(
        "POST",
        f"/api/projects/{pid}/runs",
        {"sample_ids": [sample_id], "viewports": ["phone"], "judge": False},
    )
    self.assertEqual(st, 201, rbody)
    rid = rbody["run"]["id"]
    self.assertEqual(rbody["run"]["token_revision_number"], 1)
    self.assertEqual(rbody["run"]["rendered_token_revision_number"], 1)
    self.assertFalse(rbody["run"]["theme_drifted"])

    run = wait(
        lambda: (lambda r: r if r["status"] in ("done", "failed") else None)(
            self.s.call("GET", f"/api/runs/{rid}")[1]["run"]
        )
    )
    self.assertEqual(run["status"], "done")

    st, obody = self.s.call("GET", f"/api/runs/{rid}/outputs")
    out0 = obody["outputs"][0]
    oid = out0["id"]
    token = out0["render_url"].split("token=")[1]

    # Verify render_data returns T1 primary (#0B57D0).
    st, rd1 = self.s.call("GET", f"/api/render_data/{oid}?token={token}")
    self.assertEqual(st, 200, rd1)
    self.assertEqual(rd1["theme"]["primary"], "#0B57D0")

    # Now update project tokens to T2 (primary=#123456).
    st, set_body = self.s.call("GET", f"/api/projects/{pid}/settings")
    settings = set_body["settings"]
    settings["theme"]["primary"] = "#123456"
    st, ubody = self.s.call(
        "PUT",
        f"/api/projects/{pid}/settings",
        {"settings": settings, "source": "design-tokens"},
    )
    self.assertEqual(st, 200, ubody)
    self.assertEqual(ubody["token_revision"]["number"], 2)

    # 1. Run metadata now reports theme_drifted=True (rendered T1 vs current T2).
    st, run_after = self.s.call("GET", f"/api/runs/{rid}")
    self.assertTrue(run_after["run"]["theme_drifted"])
    self.assertEqual(run_after["run"]["rendered_token_revision_number"], 1)
    self.assertEqual(run_after["run"]["current_token_revision_number"], 2)

    # 2. Default render_data(oid) STILL returns the run's snapshotted T1 theme (#0B57D0)!
    st, rd_snap = self.s.call("GET", f"/api/render_data/{oid}?token={token}")
    self.assertEqual(rd_snap["theme"]["primary"], "#0B57D0")

    # 3. render_data(oid?theme=live) returns live T2 theme (#123456)!
    st, rd_live = self.s.call(
        "GET", f"/api/render_data/{oid}?token={token}&theme=live"
    )
    self.assertEqual(rd_live["theme"]["primary"], "#123456")

    # 4. Zero-LLM retheme endpoint updates the run to T2 and re-renders outputs.
    llm_calls_before = len(self.s.llm.calls)
    st, rt_body = self.s.call(
        "POST", f"/api/runs/{rid}/retheme", {"renderers": ["web"]}
    )
    self.assertEqual(st, 200, rt_body)
    self.assertEqual(rt_body["rethemed_outputs"], 1)
    self.assertEqual(rt_body["token_revision"]["number"], 2)
    self.assertFalse(rt_body["run"]["theme_drifted"])
    self.assertEqual(rt_body["run"]["rendered_token_revision_number"], 2)
    self.assertEqual(rt_body["run"]["initial_token_revision_number"], 1)
    self.assertEqual(len(self.s.llm.calls), llm_calls_before)

    # 5. Default render_data(oid) now returns T2 (#123456).
    st, rd_after = self.s.call("GET", f"/api/render_data/{oid}?token={token}")
    self.assertEqual(rd_after["theme"]["primary"], "#123456")

  def test_building_components_and_component_errors_in_dependencies(self):
    proj = self._create_project("RunReadinessProj")
    pid = proj["id"]
    # 1. Create a building component (job.stage == "drafting").
    st, c1 = self.s.call(
        "POST",
        f"/api/projects/{pid}/components",
        {
            "status": "draft",
            "definition": {
                "name": "HeroBanner",
                "description": "Hero banner",
            },
        },
    )
    self.assertEqual(st, 201, c1)
    c1_id = c1["component"]["id"]
    self.s.app.store.execute(
        "UPDATE custom_components SET job_json=? WHERE id=?",
        ['{"stage":"drafting"}', c1_id],
    )

    # 2. Create a component with a build error referenced in constitution.
    st, c2 = self.s.call(
        "POST",
        f"/api/projects/{pid}/components",
        {
            "status": "draft",
            "definition": {
                "name": "BrokenTile",
                "description": "Broken tile",
            },
        },
    )
    self.assertEqual(st, 201, c2)
    c2_id = c2["component"]["id"]
    self.s.app.store.execute(
        "UPDATE custom_components SET job_json=? WHERE id=?",
        ['{"stage":"error","error":"Renderer syntax error"}', c2_id],
    )

    # 3. Save a constitution referencing both components.
    st, rbody = self.s.call(
        "POST",
        f"/api/projects/{pid}/revisions",
        {
            "text_md": (
                "### R1: Hero & Tiles\n"
                "Always place `HeroBanner` above `BrokenTile`."
            ),
            "summary": "Use HeroBanner and BrokenTile",
        },
    )
    self.assertEqual(st, 201, rbody)

    st, dbody = self.s.call("GET", f"/api/projects/{pid}/dependencies")
    self.assertEqual(st, 200, dbody)
    building = dbody.get("building_components") or []
    self.assertEqual(len(building), 1)
    self.assertEqual(building[0]["name"], "HeroBanner")
    self.assertEqual(building[0]["stage"], "drafting")
    self.assertIn("R1", building[0]["rule_ids"])

    issue_kinds = {i["kind"] for i in dbody["issues"]}
    self.assertIn("constitution_draft_component", issue_kinds)
    self.assertIn("component_build_error", issue_kinds)
    draft_iss = [
        i
        for i in dbody["issues"]
        if i["kind"] == "constitution_draft_component"
        and i.get("component") == "HeroBanner"
    ][0]
    self.assertTrue(draft_iss.get("building"))
    self.assertEqual(draft_iss.get("stage"), "drafting")


if __name__ == "__main__":
  unittest.main()
