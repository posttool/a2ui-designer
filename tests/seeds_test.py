"""Verify A2UI v1.0 catalog and examples are loaded from @a2ui/web_core (or synced seeds)."""

import json
import os
import unittest

from core.a2ui import catalog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NPM_SCHEMAS = os.path.join(
    ROOT, "node_modules", "@a2ui", "web_core", "src", "v1_0", "schemas"
)


class SeedsTest(unittest.TestCase):

  def test_npm_a2ui_web_core_installed_and_built(self):
    self.assertTrue(
        os.path.isdir(NPM_SCHEMAS),
        "node_modules/@a2ui/web_core not found — run `npm install`",
    )
    bundle = os.path.join(ROOT, "web", "vendor", "a2ui.bundle.js")
    versions = os.path.join(ROOT, "web", "vendor", "A2UI_VERSIONS.json")
    self.assertTrue(
        os.path.isfile(bundle) and os.path.isfile(versions),
        "web/vendor/a2ui.bundle.js not built — run `npm run build`",
    )
    meta = json.load(open(versions, encoding="utf-8"))
    self.assertEqual(meta["protocol"], "v1.0")
    self.assertIn("@a2ui/web_core", meta)
    self.assertIn("@a2ui/lit", meta)

  def test_basic_catalog_and_examples_loaded_from_npm_package(self):
    basic = catalog.load_basic_catalog()
    self.assertEqual(basic["protocolVersion"], "1.0")
    self.assertEqual(basic["catalogId"], catalog.BASIC_CATALOG_ID)
    self.assertEqual(len(basic["components"]), 18)
    examples = catalog.load_basic_examples()
    self.assertEqual(len(examples), 43)
    for fn in (
        "common_types.json",
        "catalog_definition.json",
        "agent_to_renderer.json",
        "renderer_to_agent.json",
    ):
      self.assertTrue(
          os.path.isfile(os.path.join(catalog.SPEC_JSON_DIR, fn)),
          f"missing schema {fn} in {catalog.SPEC_JSON_DIR}",
      )


if __name__ == "__main__":
  unittest.main()
