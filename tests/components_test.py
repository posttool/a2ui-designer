"""Unit tests: custom component sanitizer, catalog emission, validator."""

from __future__ import annotations

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402,F401  (sys.path + env)

from core.a2ui import catalog as catalog_mod  # noqa: E402
from core.a2ui import custom_web  # noqa: E402
from core.a2ui import lowering  # noqa: E402
from core.a2ui import prompt_schema  # noqa: E402
from core.a2ui import validator  # noqa: E402
from core.components import sample_messages  # noqa: E402
from refs import figma  # noqa: E402
from refs import figma_compiler  # noqa: E402
from refs import stitch  # noqa: E402

DS = "common_types.json#/$defs/"

TILE = {
    "name": "ProductTile",
    "description": "One purchasable item.",
    "usage": "Use for 1-4 products with a price.",
    "props": {
        "type": "object",
        "properties": {
            "title": {"$ref": DS + "DynamicString"},
            "price": {"$ref": DS + "DynamicString"},
            "image": {"$ref": DS + "DynamicString"},
            "variant": {"type": "string", "enum": ["compact", "full"]},
            "features": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"label": {"type": "string"}},
                },
            },
            "footer": {"$ref": DS + "ComponentId"},
            "action": {"$ref": DS + "Action"},
        },
        "required": ["title", "price"],
    },
    "web": {
        "template": (
            '<article class="tile {{variant}}" data-action="action">'
            '<img data-if="image" src="{{image}}" alt="{{title}}">'
            "<h3>{{title}}</h3><p>{{price}}</p>"
            '<ul><li data-each="features as f">{{f.label}}</li></ul>'
            '<slot name="footer"></slot></article>'
        ),
        "css": (
            ".tile{background:var(--a2ui-surface-container);"
            "border-radius:var(--a2ui-radius-card)}"
        ),
    },
    "sample": {
        "props": {
            "title": "Trail Runner",
            "price": "$129",
            "features": [{"label": "Light"}],
            "footer": "cta",
            "action": {"event": {"name": "open"}},
        },
        "data": {},
        "components": [
            {
                "id": "cta",
                "component": "Button",
                "child": "cta-label",
                "action": {"event": {"name": "buy"}},
            },
            {"id": "cta-label", "component": "Text", "text": "Buy"},
        ],
    },
}


class SanitizerTest(unittest.TestCase):

  def test_strips_dangerous_markup(self):
    r = custom_web.sanitize_template(
        '<div onclick="x()"><script>alert(1)</script>'
        '<a href="javascript:alert(1)">{{t}}</a>'
        '<iframe src="https://e"></iframe>'
        '<p style="color:red; --w: {{pct}}">x</p></div>'
    )
    self.assertNotIn("script", r["html"])
    self.assertNotIn("onclick", r["html"])
    self.assertNotIn("href", r["html"])
    self.assertNotIn("iframe", r["html"])
    self.assertIn('style="--w: {{pct}}"', r["html"])
    self.assertIn("{{t}}", r["html"])  # <a> unwrapped, children kept
    self.assertEqual(sorted(r["bindings"]), ["pct", "t"])
    self.assertTrue(r["errors"])

  def test_loop_vars_are_not_bindings(self):
    r = custom_web.sanitize_template(
        '<li data-each="rows as row" class="{{row.kind}}" '
        'data-if="row.visible" data-unless="row.status:hidden" '
        'data-action="row.action">'
        '<span data-if="row.icon" data-icon="{{row.icon}}"></span>'
        "{{row.label}}</li>"
    )
    self.assertEqual(r["bindings"], ["rows"])
    self.assertEqual(r["actions"], [])
    self.assertEqual(r["errors"], [])

  def test_css(self):
    c = custom_web.sanitize_css(
        "@import url(x.css);"
        " .a{background:url(http://e/x.png);color:var(--a2ui-primary)}"
        " .b{background:url(data:image/png;base64,AA)}"
    )
    self.assertNotIn("@import", c["css"])
    self.assertNotIn("http://", c["css"])
    self.assertIn("data:image/png", c["css"])
    self.assertEqual(c["token_refs"], ["--a2ui-primary"])
    self.assertEqual(len(c["errors"]), 2)

  def test_literal_colors_warn(self):
    self.assertTrue(custom_web.literal_style_warnings(".a{color:#fff}"))
    self.assertFalse(
        custom_web.literal_style_warnings(".a{color:var(--a2ui-x, #fff)}")
    )

  def test_check_definition(self):
    chk = custom_web.check_definition(TILE, catalog_mod.basic_names())
    self.assertEqual(chk["issues"], [])
    bad = copy.deepcopy(TILE)
    bad["name"] = "Card"
    bad["web"]["template"] += '<p>{{nope}}</p><slot name="title"></slot>'
    bad["sample"]["props"].pop("price")
    issues = " | ".join(
        custom_web.check_definition(bad, catalog_mod.basic_names())["issues"]
    )
    self.assertIn("Basic catalog component name", issues)
    self.assertIn("'nope'", issues)
    self.assertIn('<slot name="title">', issues)
    self.assertIn("required 'price'", issues)


class CatalogTest(unittest.TestCase):

  def cat(self, **cfg):
    return catalog_mod.build_catalog(
        dict({"catalog_id": "https://dcb.local/catalogs/shop"}, **cfg),
        {"ProductTile": TILE},
    )

  def test_custom_spec_and_prompt(self):
    cat = self.cat()
    spec = cat.components["ProductTile"]
    self.assertTrue(spec.custom and spec.has_web)
    self.assertEqual(spec.usage, TILE["usage"])
    self.assertEqual(spec.props["footer"].type, "ComponentId")
    text = prompt_schema.catalog_prompt(cat)
    self.assertLess(text.index("ProductTile"), text.index("- Text"))
    self.assertIn("usage: Use for 1-4 products", text)

  def test_legacy_raw_schema_still_parses(self):
    cat = catalog_mod.build_catalog({
        "custom_components": {
            "Chart": {
                "type": "object",
                "properties": {"series": {"type": "array"}},
            }
        }
    })
    self.assertIn("Chart", cat.components)
    self.assertFalse(cat.components["Chart"].has_web)
    self.assertEqual(cat.catalog_id, catalog_mod.DEFAULT_PROJECT_CATALOG_ID)
    self.assertEqual(
        cat.components["Chart"].catalog_id,
        catalog_mod.DEFAULT_PROJECT_CATALOG_ID,
    )

  def test_catalog_json(self):
    cat = self.cat(disabled_components=["Slider"])
    cj = catalog_mod.to_a2ui_catalog_json(cat, cat.catalog_id, title="Shop")
    self.assertEqual(cj["$id"], cj["catalogId"])
    self.assertEqual(cj["catalogId"], "https://dcb.local/catalogs/shop")
    self.assertNotIn("Slider", cj["components"])
    self.assertIn("Text", cj["components"])
    self.assertEqual(cj["protocolVersion"], "1.0")
    self.assertEqual(catalog_mod.catalog_shape_issues(cj), [])
    body = cj["components"]["ProductTile"]
    self.assertNotIn("allOf", body)
    self.assertEqual(body["properties"]["component"], {"const": "ProductTile"})
    self.assertEqual(
        body["properties"]["title"]["$ref"],
        "common_types.json#/$defs/DynamicString",
    )
    self.assertEqual(body["required"], ["component", "title", "price"])
    self.assertIn("Use for 1-4 products", cj["instructions"])
    refs = [x["$ref"] for x in cj["$defs"]["anyComponent"]["oneOf"]]
    self.assertIn("#/components/ProductTile", refs)
    self.assertNotIn("#/components/Slider", refs)

  def test_validator_custom_instances(self):
    cat = self.cat()
    msgs = sample_messages(TILE, cat.catalog_id)
    self.assertEqual(
        msgs[0]["createSurface"]["catalogId"], catalog_mod.BASIC_CATALOG_ID
    )
    self.assertEqual(
        msgs[1]["updateComponents"]["components"][0]["catalogId"],
        "https://dcb.local/catalogs/shop",
    )
    res = validator.validate(msgs, cat)
    self.assertTrue(res.ok, res.errors)
    bad = copy.deepcopy(msgs)
    root = bad[1]["updateComponents"]["components"][0]
    root["variant"] = "huge"
    root["features"] = "nope"
    root["footer"] = "missing"
    del root["price"]
    errs = " | ".join(e["message"] for e in validator.validate(bad, cat).errors)
    self.assertIn("missing required property 'price'", errs)
    self.assertIn("'huge' is not one of", errs)
    self.assertIn("expected an array", errs)
    self.assertIn("undefined component 'missing'", errs)


class CompilerLoweringAndNativeCodegenTest(unittest.TestCase):

  def test_figma_compiler_and_resolve_box_node(self):
    frame = {
        "id": "10:0",
        "name": "CheckoutScreen",
        "type": "FRAME",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 400, "height": 800},
        "children": [{
            "id": "10:1",
            "name": "FlightCard",
            "type": "COMPONENT",
            "layoutMode": "VERTICAL",
            "itemSpacing": 8,
            "paddingTop": 16,
            "paddingRight": 16,
            "paddingBottom": 16,
            "paddingLeft": 16,
            "cornerRadius": 12,
            "absoluteBoundingBox": {
                "x": 20,
                "y": 80,
                "width": 360,
                "height": 160,
            },
            "children": [
                {
                    "id": "10:2",
                    "name": "RouteTitle",
                    "type": "TEXT",
                    "characters": "SFO → NRT",
                    "style": {"fontSize": 20, "fontWeight": 700},
                    "componentPropertyReferences": {"characters": "Route#1:0"},
                },
                {
                    "id": "10:3",
                    "name": "PriceText",
                    "type": "TEXT",
                    "characters": "$840",
                    "style": {"fontSize": 14, "fontWeight": 500},
                },
            ],
        }],
    }
    resolved = figma.resolve_box_node(frame, [5, 10, 90, 20])
    self.assertIsNotNone(resolved)
    self.assertEqual(resolved["node_id"], "10:1")

    web_skel = figma_compiler.compile_node_to_html_css(
        resolved["node"],
        palette={"primary": "#1A73E8", "surface": "#FFFFFF"},
        property_defs={"Route#1:0": {"type": "TEXT", "default": "SFO → NRT"}},
    )
    self.assertIn("{{route}}", web_skel["template"])
    self.assertIn("\n  ", web_skel["template"])
    self.assertGreater(len(web_skel["template"].splitlines()), 2)
    self.assertIn("var(--a2ui-", web_skel["css"])
    self.assertIn("\n  ", web_skel["css"])
    self.assertIn("route", web_skel["props"]["properties"])

    basic_skel = figma_compiler.compile_node_to_basic_skeleton(
        resolved["node"],
        property_defs={"Route#1:0": {"type": "TEXT", "default": "SFO → NRT"}},
    )
    comp = basic_skel["composition"]
    self.assertEqual(comp["root"], "card")
    comp_types = [c["component"] for c in comp["components"]]
    self.assertIn("Card", comp_types)
    self.assertIn("Column", comp_types)
    self.assertIn("Text", comp_types)
    # An auto-layout card of texts maps faithfully onto the Basic catalog.
    self.assertEqual(basic_skel["fit"]["score"], 1.0)
    self.assertEqual(basic_skel["fit"]["nodes"], 3)
    self.assertEqual(basic_skel["fit"]["summary"], [])

  def test_basic_fit_scores_what_the_basic_catalog_cannot_express(self):
    messy = {
        "type": "COMPONENT",
        "name": "Boarding pass",
        "absoluteBoundingBox": {"width": 320, "height": 200},
        "fills": [{"type": "GRADIENT_LINEAR"}],
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
            },
            {
                "type": "RECTANGLE",
                "name": "barcode",
                "rotation": 90,
                "absoluteBoundingBox": {"width": 200, "height": 60},
            },
        ],
    }
    fit = figma_compiler.compile_node_to_basic_skeleton(messy)["fit"]
    self.assertEqual(fit["nodes"], 5)
    self.assertLess(fit["score"], 0.5)
    reasons = {r["reason"] for r in fit["reasons"]}
    self.assertIn("absolute positioning", reasons)
    self.assertIn("custom vector artwork", reasons)
    self.assertIn("rotated layer", reasons)
    self.assertIn("gradient fill", reasons)
    self.assertTrue(
        any(r["reason"].startswith("free-form layout") for r in fit["reasons"])
    )
    self.assertTrue(all("×" in s for s in fit["summary"]))
    # Small leaf shapes are icons (faithful); outline trees (condensed
    # `component_outline` / Stitch DOM) score the same features by their
    # outline keys.
    icon_row = {
        "type": "FRAME",
        "name": "Row",
        "layout": {"dir": "row", "gap": 8},
        "size": [200, 24],
        "children": [
            {"type": "VECTOR", "name": "star", "size": [20, 20]},
            {
                "type": "TEXT",
                "name": "label",
                "text": "4.8",
                "font": {"size": 14},
            },
        ],
    }
    self.assertEqual(
        figma_compiler.compile_node_to_basic_skeleton(icon_row)["fit"]["score"],
        1.0,
    )
    gradient_box = dict(icon_row, fill="gradient_linear")
    g = figma_compiler.compile_node_to_basic_skeleton(gradient_box)["fit"]
    self.assertEqual(g["summary"], ["1× gradient fill"])
    self.assertAlmostEqual(g["score"], (0.5 + 1 + 1) / 3, places=3)
    # No structure → no score (the Gemini estimate is used instead).
    self.assertIsNone(
        figma_compiler.compile_node_to_basic_skeleton({})["fit"]["score"]
    )

  def test_component_strategy_decision_uses_fit_threshold(self):
    from core import settings as settings_mod  # pylint: disable=g-import-not-at-top

    base = {"catalog": {"basic_fit_threshold": 0.7}, "renderers": ["web"]}
    hi = settings_mod.component_strategy_decision(base, basic_fit=0.82)
    self.assertEqual(hi["strategy"], "basic_composition")
    self.assertTrue(hi["auto"])
    self.assertEqual(hi["threshold"], 0.7)
    self.assertIn("82% ≥ 70%", hi["reason"])
    lo = settings_mod.component_strategy_decision(base, basic_fit=0.4)
    self.assertEqual(lo["strategy"], "web_template")
    self.assertIn("40% < 70%", lo["reason"])
    # Exactly at the threshold counts as Basic.
    self.assertEqual(
        settings_mod.component_strategy_decision(base, basic_fit=0.7)[
            "strategy"
        ],
        "basic_composition",
    )
    # Threshold 0 → everything composes; 1 → only perfect matches.
    self.assertEqual(
        settings_mod.resolve_component_strategy(
            {"catalog": {"basic_fit_threshold": 0}}, basic_fit=0.01
        ),
        "basic_composition",
    )
    self.assertEqual(
        settings_mod.resolve_component_strategy(
            {"catalog": {"basic_fit_threshold": 1}}, basic_fit=0.99
        ),
        "web_template",
    )
    # Without a fit estimate the project falls back to a custom web component.
    self.assertEqual(
        settings_mod.resolve_component_strategy({"renderers": ["web"]}),
        "web_template",
    )
    # Pinned (override) and project-wide strategies ignore the fit.
    pinned = settings_mod.component_strategy_decision(
        base, override="web_template", basic_fit=0.95
    )
    self.assertEqual(pinned["strategy"], "web_template")
    self.assertFalse(pinned["auto"])
    proj = settings_mod.component_strategy_decision(
        {"catalog": {"component_strategy": "basic_composition"}}, basic_fit=0.1
    )
    self.assertEqual(proj["strategy"], "basic_composition")
    self.assertFalse(proj["auto"])
    # The removed `native_targets` strategy falls back to auto.
    legacy = settings_mod.component_strategy_decision(
        {"catalog": {"component_strategy": "native_targets"}}, basic_fit=0.1
    )
    self.assertEqual(legacy["strategy"], "web_template")
    self.assertTrue(legacy["auto"])
    # Settings normalization clamps / defaults the threshold.
    self.assertEqual(
        settings_mod.merged({"catalog": {"basic_fit_threshold": "abc"}})[
            "catalog"
        ]["basic_fit_threshold"],
        settings_mod.BASIC_FIT_THRESHOLD_DEFAULT,
    )
    self.assertEqual(
        settings_mod.merged({"catalog": {"basic_fit_threshold": -2}})[
            "catalog"
        ]["basic_fit_threshold"],
        0.0,
    )

  def test_stitch_dom_region_extractor(self):
    html_doc = """
    <html><body>
      <main>
        <article class="flight-card" style="display:flex;flex-direction:column;gap:8px;padding:16px;border-radius:12px">
          <h3>SFO to LHR</h3>
          <p>Nonstop · 10h 25m</p>
          <button>Select flight</button>
        </article>
      </main>
    </body></html>
    """
    res = stitch.extract_stitch_dom_region(html_doc, hint="flight card")
    self.assertTrue(res["web"]["template"])
    self.assertIn("\n  ", res["web"]["template"])
    self.assertGreater(len(res["web"]["template"].splitlines()), 2)
    self.assertIn("var(--a2ui-", res["web"]["css"])
    self.assertIn("\n  ", res["web"]["css"])
    self.assertTrue(lowering.has_composition(res["basic"]))

  def test_format_template_and_css(self):
    formatted_html = custom_web.format_template(TILE["web"]["template"])
    self.assertEqual(
        formatted_html,
        '<article class="tile {{variant}}" data-action="action">\n'
        '  <img data-if="image" src="{{image}}" alt="{{title}}">\n'
        "  <h3>{{title}}</h3>\n"
        "  <p>{{price}}</p>\n"
        "  <ul>\n"
        '    <li data-each="features as f">{{f.label}}</li>\n'
        "  </ul>\n"
        '  <slot name="footer"></slot>\n'
        "</article>",
    )
    formatted_css = custom_web.format_css(TILE["web"]["css"])
    self.assertEqual(
        formatted_css,
        ".tile {\n"
        "  background: var(--a2ui-surface-container);\n"
        "  border-radius: var(--a2ui-radius-card);\n"
        "}",
    )

  def test_lowering(self):
    defn = custom_web.normalize_definition({
        "name": "SummaryBanner",
        "description": "Compact summary banner.",
        "usage": "Use at the top of a checkout summary.",
        "strategy": "basic_composition",
        "props": {
            "type": "object",
            "properties": {
                "headline": {"$ref": DS + "DynamicString"},
                "subtitle": {"$ref": DS + "DynamicString"},
            },
            "required": ["headline"],
        },
        "composition": {
            "root": "card",
            "components": [
                {"id": "card", "component": "Card", "child": "col"},
                {
                    "id": "col",
                    "component": "Column",
                    "children": ["h_txt", "s_txt"],
                },
                {
                    "id": "h_txt",
                    "component": "Text",
                    "text": "{{headline}}",
                    "variant": "h3",
                },
                {
                    "id": "s_txt",
                    "component": "Text",
                    "text": "{{subtitle}}",
                    "variant": "caption",
                },
            ],
        },
        "sample": {"props": {"headline": "Ready", "subtitle": "2 items"}},
    })
    chk = custom_web.check_definition(defn, catalog_mod.basic_names())
    self.assertEqual(chk["issues"], [])
    self.assertTrue(lowering.should_lower_for_renderer(defn, "web"))
    msgs = sample_messages(defn, catalog_mod.BASIC_CATALOG_ID)
    lowered = lowering.expand_composite_components(
        msgs, {"SummaryBanner": defn}
    )
    comps = lowered[1]["updateComponents"]["components"]
    by_id = {c["id"]: c for c in comps}
    self.assertEqual(by_id["root"]["component"], "Card")
    # v0.9 "variant": "h3" in a stored composition lowers to Markdown.
    self.assertEqual(by_id["root__h_txt"]["text"], "### Ready")
    self.assertNotIn("variant", by_id["root__h_txt"])
    self.assertNotIn("catalogId", by_id["root"])
    self.assertEqual(by_id["root__s_txt"]["text"], "2 items")
    basic_cat = catalog_mod.build_catalog({})
    self.assertTrue(validator.validate(lowered, basic_cat).ok)
    # Native platform code is no longer part of a definition.
    self.assertNotIn(
        "targets",
        custom_web.normalize_definition(
            dict(defn, targets={"ios_swiftui": {"code": "x"}})
        ),
    )

if __name__ == "__main__":
  unittest.main()
