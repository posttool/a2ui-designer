"""Validator, catalog and prompt-schema tests (spec examples as fixtures)."""

import copy
import glob
import json
import os
import sys
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG)

from core.a2ui import catalog, normalize, prompt_schema, validator  # noqa: E402

EXAMPLES = sorted(
    glob.glob(os.path.join(PKG, "seeds", "spec_examples", "*.json"))
)
CUSTOM_CATALOG_EXAMPLES = {"37_native-grid.json"}


def _load(name):
  with open(
      os.path.join(PKG, "seeds", "spec_examples", name), encoding="utf-8"
  ) as fh:
    return json.load(fh)["messages"]


class ValidatorTest(unittest.TestCase):

  def setUp(self):
    self.cat = catalog.build_catalog(None)

  def test_basic_catalog_has_18_components(self):
    self.assertEqual(len(self.cat.components), 18)
    self.assertIn("ChoicePicker", self.cat.components)
    self.assertEqual(
        self.cat.components["Button"].required, ["child", "action"]
    )

  def test_all_spec_examples_validate(self):
    self.assertGreaterEqual(len(EXAMPLES), 30)
    for path in EXAMPLES:
      name = os.path.basename(path)
      if name in CUSTOM_CATALOG_EXAMPLES:
        continue
      with open(path, encoding="utf-8") as fh:
        msgs = json.load(fh)["messages"]
      r = validator.validate(msgs, self.cat)
      self.assertTrue(r.ok, f"{name}: {r.summary()}")

  def test_custom_components_validate_when_declared(self):
    msgs = _load("37_native-grid.json")
    r = validator.validate(msgs, self.cat)
    self.assertFalse(r.ok)
    cat = catalog.build_catalog({
        "custom_components": {
            "CustomGrid": {
                "type": "object",
                "properties": {
                    "children": {"type": "array"},
                    "columns": {"type": "number"},
                },
            },
            "CustomSlider": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {},
                    "min": {},
                    "max": {},
                },
            },
        }
    })
    names = {e["message"] for e in validator.validate(msgs, cat).errors}
    self.assertFalse(any("not in the catalog" in n for n in names), names)

  def _base(self):
    return copy.deepcopy(_load("01_flight-status.json"))

  def _components(self, msgs):
    for m in msgs:
      if "updateComponents" in m:
        return m["updateComponents"]["components"]
    raise AssertionError

  def test_unknown_component(self):
    msgs = self._base()
    self._components(msgs)[1]["component"] = "Carousel"
    r = validator.validate(msgs, self.cat)
    self.assertFalse(r.ok)
    self.assertIn("not in the catalog", r.errors[0]["message"])
    self.assertTrue(r.errors[0]["path"].endswith("/component"))

  def test_missing_required(self):
    msgs = self._base()
    comps = self._components(msgs)
    text = next(c for c in comps if c["component"] == "Text")
    del text["text"]
    r = validator.validate(msgs, self.cat)
    self.assertIn("missing required property 'text'", r.summary())

  def test_bad_enum_and_unknown_prop(self):
    msgs = self._base()
    comps = self._components(msgs)
    text = next(c for c in comps if c["component"] == "Text")
    text["variant"] = "giant"
    text["color"] = "red"
    s = validator.validate(msgs, self.cat).summary()
    self.assertIn("'giant' is not one of", s)
    self.assertIn("unknown property 'color'", s)

  def test_dangling_child_and_missing_root(self):
    msgs = self._base()
    comps = self._components(msgs)
    comps[0]["id"] = "top"
    s = validator.validate(msgs, self.cat).summary()
    self.assertIn("no component with id 'root'", s)
    msgs = self._base()
    self._components(msgs)[1]["children"].append("ghost")
    self.assertIn(
        "undefined component 'ghost'",
        validator.validate(msgs, self.cat).summary(),
    )

  def test_v08_shapes_are_explained(self):
    msgs = [
        {
            "version": "v0.9",
            "createSurface": {"surfaceId": "s", "catalogId": "x"},
        },
        {
            "version": "v0.9",
            "updateComponents": {
                "surfaceId": "s",
                "components": [{
                    "id": "root",
                    "component": {"Text": {"text": {"literalString": "hi"}}},
                }],
            },
        },
    ]
    s = validator.validate(msgs, self.cat).summary()
    self.assertIn("v0.8 shape", s)

  def test_function_args_are_closed(self):
    msgs = self._base()
    comps = self._components(msgs)
    text = next(c for c in comps if c["component"] == "Text")
    text["text"] = {
        "call": "formatString",
        "returnType": "string",
        "args": {"input": "Do not {a}", "values": {"a": {"path": ""}}},
    }
    s = validator.validate(msgs, self.cat).summary()
    self.assertIn("does not accept ['input', 'values']", s)
    self.assertIn("missing required args ['value']", s)

  def test_disabled_component(self):
    cat = catalog.build_catalog({"disabled_components": ["Card"]})
    r = validator.validate(self._base(), cat)
    self.assertIn("disabled", r.summary())

  def test_normalize(self):
    data = normalize.parse_json_text(
        'Sure!\n```json\n{"messages": '
        '[{"updateComponents": {"surfaceId": '
        '"x", "components": []}}]}\n```'
    )
    msgs = normalize.to_messages(data)
    self.assertIn("createSurface", msgs[0])
    self.assertEqual(msgs[0]["createSurface"]["surfaceId"], "x")
    self.assertEqual(msgs[1]["version"], "v0.9")

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


if __name__ == "__main__":
  unittest.main()
