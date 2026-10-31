"""Validator, catalog, normalize and prompt-schema tests (A2UI v1.0).

Fixtures are the verbatim upstream files in seeds/a2ui/: all 43 v1 Basic
catalog examples must validate, and selected upstream schema test cases
(specification/v1_0/test/cases) must agree with our validator.
"""

import copy
import json
import os
import sys
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG)

from core.a2ui import catalog, normalize, prompt_schema, validator  # noqa: E402

CASES_DIR = os.path.join(
    catalog.A2UI_SEEDS, "specification", "v1_0", "test", "cases"
)
TESTING_CATALOG = os.path.join(
    catalog.A2UI_SEEDS, "specification", "v1_0", "test", "testing_catalog.json"
)
# Upstream cases about agent→renderer messages we validate. Renderer→agent
# schemas, catalog-definition cases and callRendererFunction (tolerated, not
# rendered) are out of scope.
UPSTREAM_CASES = (
    "button_checks.json",
    "checkable_components.json",
    "contact_form_example_test.json",
    "dynamic_value_validation.json",
    "function_catalog_validation.json",
    "icon_checks.json",
    "initial_state_validation.json",
    "tabs_checks.json",
    "text_variants.json",
)
EXT = catalog.EXTENSION_CATALOG_ID


def _example(name):
  for ex in catalog.load_basic_examples():
    if ex["file"] == name:
      return copy.deepcopy(ex["messages"])
  raise KeyError(name)


def _components(msgs):
  for m in msgs:
    if "updateComponents" in m:
      return m["updateComponents"]["components"]
  raise AssertionError("no updateComponents")


def _surface(components, data=None, catalog_id=catalog.BASIC_CATALOG_ID):
  msgs = [
      {
          "version": "v1.0",
          "createSurface": {"surfaceId": "main", "catalogId": catalog_id},
      },
      {
          "version": "v1.0",
          "updateComponents": {"surfaceId": "main", "components": components},
      },
  ]
  if data is not None:
    msgs.append({
        "version": "v1.0",
        "updateDataModel": {"surfaceId": "main", "path": "/", "value": data},
    })
  return msgs


class CatalogTest(unittest.TestCase):

  def test_basic_catalog_is_upstream_v1(self):
    cat = catalog.build_catalog(None)
    self.assertEqual(len(cat.components), 18)
    self.assertEqual(
        cat.components["Button"].required, ["child", "action"]
    )
    self.assertEqual(cat.components["Text"].props["variant"].enum,
                     ["caption", "body"])
    self.assertEqual(cat.basic_catalog_id, catalog.BASIC_CATALOG_ID)
    self.assertEqual(cat.catalog_id, catalog.DEFAULT_PROJECT_CATALOG_ID)
    self.assertNotIn("backgroundImage", cat.components["Card"].props)
    self.assertNotIn("size", cat.components["Icon"].props)
    self.assertEqual(catalog.load_basic_catalog()["protocolVersion"], "1.0")

  def test_upstream_catalogs_have_v1_shape(self):
    self.assertEqual(
        catalog.catalog_shape_issues(catalog.load_basic_catalog()), []
    )

  def test_extension_catalog_shape(self):
    ext = catalog.load_extension_catalog()
    self.assertEqual(catalog.catalog_shape_issues(ext), [])
    self.assertEqual(ext["catalogId"], EXT)
    self.assertEqual(ext["$id"], EXT)
    self.assertEqual(set(ext["components"]), {"HeroCard", "SvgIcon"})
    self.assertIn("HeroCard", ext["instructions"])
    hero = ext["components"]["HeroCard"]
    self.assertEqual(hero["required"], ["component", "child", "image"])
    self.assertEqual(hero["properties"]["scrim"]["enum"],
                     ["auto", "dark", "light", "none"])
    self.assertEqual(catalog.extension_catalog_json(), ext)

  def test_extension_is_opt_in(self):
    off = catalog.build_catalog({})
    self.assertNotIn("HeroCard", off.components)
    on = catalog.build_catalog({"extensions": ["a2ui-designer"]})
    self.assertEqual(on.components["HeroCard"].catalog_id, EXT)
    self.assertTrue(on.components["SvgIcon"].extension)
    self.assertEqual(on.components["SvgIcon"].one_of, [["url", "svg"]])
    self.assertIn(EXT, on.instructions())

  def test_project_catalog_json_shape(self):
    cat = catalog.build_catalog({
        "catalog_id": "https://example.com/c.json",
        "disabled_components": ["Video"],
        "custom_components": {
            "PriceTag": {
                "name": "PriceTag",
                "description": "A price",
                "usage": "Use for prices.",
                "props": {
                    "type": "object",
                    "properties": {
                        "amount": {
                            "$ref": "https://a2ui.org/specification/v0_9/"
                            "common_types.json#/$defs/DynamicString"
                        },
                        "onTap": {"$ref": "common_types.json#/$defs/Action"},
                    },
                    "required": ["amount"],
                },
                "web": {"template": "<b>{{amount}}</b>", "css": ""},
            }
        },
    })
    full = catalog.to_a2ui_catalog_json(cat, cat.catalog_id, title="Shop")
    self.assertEqual(catalog.catalog_shape_issues(full), [])
    self.assertEqual(full["protocolVersion"], "1.0")
    self.assertEqual(set(full["$defs"]), {"anyComponent", "anyFunction"})
    self.assertNotIn("Video", full["components"])
    self.assertIn("Text", full["components"])
    tag = full["components"]["PriceTag"]
    self.assertEqual(
        tag["properties"]["amount"]["$ref"],
        "common_types.json#/$defs/DynamicString",
    )
    self.assertIn("weight", tag["properties"])
    self.assertNotIn("allOf", tag)
    self.assertNotIn("unevaluatedProperties", tag)
    self.assertIn("Use for prices.", full["instructions"])
    self.assertIn("Video", full["instructions"])
    proj = catalog.project_catalog_json(cat)
    self.assertEqual(catalog.catalog_shape_issues(proj), [])
    self.assertEqual(list(proj["components"]), ["PriceTag"])
    self.assertEqual(proj["functions"], {})

  def test_shape_issues_catch_v09_habits(self):
    bad = {
        "catalogId": "x",
        "$id": "x",
        "protocolVersion": "1.0",
        "components": {
            "Foo": {
                "allOf": [
                    {"$ref": "common_types.json#/$defs/ComponentCommon"},
                    {"properties": {"component": {"const": "Foo"}}},
                ],
                "unevaluatedProperties": False,
            },
            "bar-baz": {"properties": {"component": {"const": "bar-baz"}}},
        },
        "functions": {},
        "$defs": {"anyComponent": {}, "anyFunction": {}, "theme": {}},
    }
    issues = " | ".join(catalog.catalog_shape_issues(bad))
    self.assertIn("ComponentCommon", issues)
    self.assertIn("unevaluatedProperties", issues)
    self.assertIn("UAX #31", issues)
    self.assertIn("$defs", issues)


class ValidatorTest(unittest.TestCase):

  def setUp(self):
    self.cat = catalog.build_catalog(None)

  def test_all_upstream_examples_validate(self):
    examples = catalog.load_basic_examples()
    self.assertEqual(len(examples), 43)
    for ex in examples:
      r = validator.validate(ex["messages"], self.cat)
      self.assertTrue(r.ok, f"{ex['file']}: {r.summary()}")

  def test_upstream_schema_cases(self):
    with open(TESTING_CATALOG, encoding="utf-8") as fh:
      testing = catalog.catalog_from_json(json.load(fh))
    total = 0
    for fn in UPSTREAM_CASES:
      with open(os.path.join(CASES_DIR, fn), encoding="utf-8") as fh:
        suite = json.load(fh)
      cat = testing if suite.get("catalog") else self.cat
      for t in suite["tests"]:
        data = copy.deepcopy(t["data"])
        msgs = data if isinstance(data, list) else [data]
        # Schema cases validate single messages: give them a surface.
        sids = set()
        for m in msgs:
          for k, body in m.items():
            if isinstance(body, dict) and k != "createSurface":
              sids.add(body.get("surfaceId"))
          if isinstance(m.get("createSurface"), dict):
            m["createSurface"].setdefault("catalogId", cat.basic_catalog_id)
            sids.discard(m["createSurface"].get("surfaceId"))
        pre = [
            {
                "version": "v1.0",
                "createSurface": {
                    "surfaceId": s,
                    "catalogId": cat.basic_catalog_id,
                },
            }
            for s in sorted(x for x in sids if x)
        ]
        r = validator.validate(pre + msgs, cat, complete=False)
        total += 1
        self.assertEqual(
            r.ok, t["valid"], f"{fn}: {t['description']}: {r.summary()}"
        )
    self.assertGreater(total, 100)

  def test_v09_payload_rejected_with_hint(self):
    msgs = [
        {
            "version": "v0.9",
            "createSurface": {
                "surfaceId": "s",
                "catalogId": "https://a2ui.org/specification/v0_9/catalogs/"
                "basic/catalog.json",
                "theme": {"primaryColor": "#f00"},
            },
        },
        {
            "version": "v0.9",
            "updateComponents": {
                "surfaceId": "s",
                "components": [{
                    "id": "root",
                    "component": "Text",
                    "text": {"path": "/t"},
                    "variant": "h1",
                }],
            },
        },
    ]
    r = validator.validate(msgs, self.cat)
    self.assertFalse(r.ok)
    self.assertIn("A2UI v0.9 is not supported", r.summary())
    self.assertIn("@path", r.summary())

  def test_v09_syntax_in_v1_message(self):
    msgs = _surface([
        {"id": "root", "component": "Column", "children": ["t"]},
        {
            "id": "t",
            "component": "Text",
            "text": {"path": "/t"},
            "variant": "h2",
        },
    ])
    s = validator.validate(msgs, self.cat).summary()
    self.assertIn("v0.9 binding syntax", s)
    self.assertIn("Markdown", s)

  def test_unknown_component(self):
    msgs = _example("01_flight-status.json")
    _components(msgs)[1]["component"] = "Carousel"
    r = validator.validate(msgs, self.cat)
    self.assertFalse(r.ok)
    self.assertIn("not in the catalog", r.errors[0]["message"])
    self.assertTrue(r.errors[0]["path"].endswith("/component"))

  def test_missing_required(self):
    msgs = _example("01_flight-status.json")
    text = next(c for c in _components(msgs) if c["component"] == "Text")
    del text["text"]
    r = validator.validate(msgs, self.cat)
    self.assertIn("missing required property 'text'", r.summary())

  def test_bad_enum_and_unknown_prop(self):
    msgs = _example("01_flight-status.json")
    text = next(c for c in _components(msgs) if c["component"] == "Text")
    text["variant"] = "giant"
    text["color"] = "red"
    s = validator.validate(msgs, self.cat).summary()
    self.assertIn("'giant' is not one of", s)
    self.assertIn("unknown property 'color'", s)

  def test_dangling_child_and_missing_root(self):
    msgs = _example("01_flight-status.json")
    _components(msgs)[0]["id"] = "top"
    s = validator.validate(msgs, self.cat).summary()
    self.assertIn("no component with id 'root'", s)
    msgs = _example("01_flight-status.json")
    comps = _components(msgs)
    col = next(c for c in comps if isinstance(c.get("children"), list))
    col["children"].append("ghost")
    self.assertIn(
        "undefined component 'ghost'",
        validator.validate(msgs, self.cat).summary(),
    )

  def test_ids_must_be_identifiers(self):
    msgs = _surface([
        {"id": "root", "component": "Column", "children": ["my-title"]},
        {"id": "my-title", "component": "Text", "text": "Hi"},
    ])
    s = validator.validate(msgs, self.cat).summary()
    self.assertIn("UAX #31", s)
    msgs = _surface([{"id": "root", "component": "Text", "text": "Hi"}])
    self.assertTrue(validator.validate(msgs, self.cat).ok)

  def test_v08_shapes_are_explained(self):
    msgs = _surface([{
        "id": "root",
        "component": {"Text": {"text": {"literalString": "hi"}}},
    }])
    s = validator.validate(msgs, self.cat).summary()
    self.assertIn("v0.8 shape", s)

  def test_function_args_are_closed_and_no_return_type(self):
    msgs = _surface([{
        "id": "root",
        "component": "Text",
        "text": {
            "@call": "formatString",
            "returnType": "string",
            "args": {"input": "Do not {a}", "values": {"a": {"@path": ""}}},
        },
    }])
    s = validator.validate(msgs, self.cat).summary()
    self.assertIn("does not accept ['input', 'values']", s)
    self.assertIn("missing required args ['value']", s)
    self.assertIn("returnType", s)

  def test_escaping_and_reserved_keys(self):
    def button(ctx):
      return _surface([
          {
              "id": "root",
              "component": "Button",
              "child": "lbl",
              "action": {"event": {"name": "go", "context": ctx}},
          },
          {"id": "lbl", "component": "Text", "text": "Go"},
      ])

    self.assertTrue(
        validator.validate(button({"x": {"@@type": "lit"}}), self.cat).ok
    )
    self.assertTrue(
        validator.validate(button({"x": {"@path": "/a"}}), self.cat).ok
    )
    r = validator.validate(button({"x": {"@type": "lit"}}), self.cat)
    self.assertIn("escaped as '@@'", r.summary())

  def test_check_rule_message_optional(self):
    msgs = _surface(
        [{
            "id": "root",
            "component": "TextField",
            "label": "Email",
            "value": {"@path": "/e"},
            "checks": [
                {"condition": {"@call": "email", "args": {"value": {
                    "@path": "/e"}}}}
            ],
        }],
        {"e": ""},
    )
    self.assertTrue(validator.validate(msgs, self.cat).ok)
    comps = _components(msgs)
    comps[0]["checks"] = [{"condition": True, "message": "x"}]
    self.assertIn(
        "data binding or function call",
        validator.validate(msgs, self.cat).summary(),
    )

  def test_update_data_model_value_required(self):
    msgs = _surface([{"id": "root", "component": "Text", "text": "Hi"}])
    msgs.append(
        {"version": "v1.0", "updateDataModel": {"surfaceId": "main"}}
    )
    self.assertIn("needs 'value'", validator.validate(msgs, self.cat).summary())
    msgs[-1]["updateDataModel"]["value"] = None
    self.assertTrue(validator.validate(msgs, self.cat).ok)

  def test_tolerated_messages(self):
    msgs = _surface([{"id": "root", "component": "Text", "text": "Hi"}])
    msgs.append({
        "version": "v1.0",
        "agentFunctionResponse": {"functionCallId": "c1", "value": 1},
    })
    self.assertTrue(validator.validate(msgs, self.cat).ok)

  def test_disabled_component_is_lint(self):
    cat = catalog.build_catalog({"disabled_components": ["Card"]})
    r = validator.validate(_example("01_flight-status.json"), cat)
    self.assertTrue(r.ok)
    self.assertFalse(r.clean)
    self.assertIn("disabled", r.summary())

  def test_mixed_catalog_resolution(self):
    cat = catalog.build_catalog({
        "extensions": ["a2ui-designer"],
        "catalog_id": "https://example.com/p.json",
        "custom_components": {
            "Badge": {
                "name": "Badge",
                "description": "b",
                "props": {
                    "type": "object",
                    "properties": {
                        "label": {"$ref": "common_types.json#/$defs/DynamicString"}
                    },
                },
                "web": {"template": "<span>{{label}}</span>", "css": ""},
            }
        },
    })
    good = _surface([
        {"id": "root", "component": "Column", "children": ["hero", "b", "i"]},
        {
            "id": "hero",
            "component": "HeroCard",
            "catalogId": EXT,
            "image": "/img/x/400x200",
            "child": "t",
        },
        {"id": "t", "component": "Text", "text": "# Hi"},
        {
            "id": "b",
            "component": "Badge",
            "catalogId": "https://example.com/p.json",
            "label": "New",
        },
        {
            "id": "i",
            "component": "SvgIcon",
            "catalogId": EXT,
            "url": "/api/gen/abc.svg",
            "size": 24,
        },
    ])
    r = validator.validate(good, cat)
    self.assertTrue(r.ok, r.summary())
    bad = copy.deepcopy(good)
    del _components(bad)[1]["catalogId"]
    s = validator.validate(bad, cat).summary()
    self.assertIn(f'"catalogId": "{EXT}"', s)
    bad = copy.deepcopy(good)
    _components(bad)[4]["svg"] = "<svg/>"
    _components(bad)[4]["size"] = 200
    s = validator.validate(bad, cat).summary()
    self.assertIn("exactly one of ['url', 'svg']", s)
    self.assertIn("<= 96", s)
    off = catalog.build_catalog({})
    self.assertIn(
        "'HeroCard' is not in the catalog",
        validator.validate(good, off).summary(),
    )


class NormalizeTest(unittest.TestCase):

  def test_fills_gaps(self):
    data = normalize.parse_json_text(
        'Sure!\n```json\n{"messages": '
        '[{"updateComponents": {"surfaceId": '
        '"x", "components": []}}]}\n```'
    )
    msgs = normalize.to_messages(data)
    self.assertIn("createSurface", msgs[0])
    self.assertEqual(msgs[0]["createSurface"]["surfaceId"], "x")
    self.assertEqual(
        msgs[0]["createSurface"]["catalogId"], catalog.BASIC_CATALOG_ID
    )
    self.assertEqual(msgs[1]["version"], "v1.0")

  def test_upgrades_v09_llm_habits(self):
    cat = catalog.build_catalog({
        "extensions": ["a2ui-designer"],
        "catalog_id": "https://example.com/p.json",
    })
    data = {"messages": [
        {"version": "v0.9", "createSurface": {
            "surfaceId": "main", "catalogId": "https://example.com/p.json",
            "theme": {"primaryColor": "#f00"}}},
        {"version": "v0.9", "updateComponents": {"surfaceId": "main",
                                                 "components": [
            {"id": "root", "component": "Column",
             "children": ["hero-card", "row-list"]},
            {"id": "hero-card", "component": "HeroCard",
             "image": {"path": "/img"}, "child": "title"},
            {"id": "title", "component": "Text", "text": {
                "call": "formatString", "returnType": "string",
                "args": {"value": "# ${/t}"}}},
            {"id": "row-list", "component": "List",
             "children": {"componentId": "item-tpl", "@path": "/items"}},
            {"id": "item-tpl", "component": "Text",
             "text": {"path": "name"}},
        ]}},
        {"updateDataModel": {"surfaceId": "main", "value": {
            "t": "Hi", "img": "/a.png", "items": [{"name": "a"}]}}},
    ]}
    msgs = normalize.to_messages(data, catalog=cat)
    r = validator.validate(msgs, cat)
    self.assertTrue(r.ok, r.summary())
    self.assertEqual(r.warnings, [])
    cs = msgs[0]["createSurface"]
    self.assertEqual(cs["catalogId"], catalog.BASIC_CATALOG_ID)
    self.assertNotIn("theme", cs)
    comps = _components(msgs)
    self.assertEqual(comps[0]["children"], ["hero_card", "row_list"])
    self.assertEqual(comps[1]["catalogId"], EXT)
    self.assertEqual(comps[1]["image"], {"@path": "/img"})
    self.assertEqual(
        comps[2]["text"],
        {"@call": "formatString", "args": {"value": "# ${/t}"}},
    )
    self.assertEqual(
        comps[3]["children"], {"componentId": "item_tpl", "path": "/items"}
    )
    self.assertEqual(msgs[2]["updateDataModel"]["value"]["img"], "/a.png")

  def test_basic_components_keep_surface_default(self):
    cat = catalog.build_catalog({})
    msgs = normalize.to_messages(
        _example("04_weather-current.json"), catalog=cat
    )
    self.assertEqual(msgs, _example("04_weather-current.json"))


class PromptSchemaTest(unittest.TestCase):

  def test_prompt_schema(self):
    cat = catalog.build_catalog({
        "disabled_components": ["Video"],
        "custom_components": {
            "Chart": {
                "type": "object",
                "description": "A chart",
                "properties": {"series": {"type": "array"}},
            }
        },
    })
    text = prompt_schema.catalog_prompt(cat)
    self.assertIn("- Text", text)
    self.assertNotIn("- Video", text)
    self.assertIn("- Chart (custom)", text)
    self.assertIn("formatCurrency", text)
    self.assertIn('{"@path"', text)
    self.assertIn('"@call"', text)
    self.assertIn("Markdown", text)
    self.assertIn("no hyphens", text.lower())
    self.assertIn(catalog.basic_instructions()[:60], text)
    self.assertNotIn("HeroCard", text)
    self.assertNotIn('{"path": "/json', text)

  def test_extension_prompt_only_when_enabled(self):
    on = catalog.build_catalog({"extensions": ["a2ui-designer"]})
    text = prompt_schema.catalog_prompt(on)
    self.assertIn("- HeroCard (extension)", text)
    self.assertIn("exactly one of: url, svg", text)
    self.assertIn(catalog.extension_instructions()[:60], text)


if __name__ == "__main__":
  unittest.main()
