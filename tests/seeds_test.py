"""Vendored upstream A2UI files must stay byte-identical to UPSTREAM.json."""

import hashlib
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "seeds", "a2ui")


def _sha(path):
  with open(path, "rb") as fh:
    return hashlib.sha256(fh.read()).hexdigest()


class SeedsTest(unittest.TestCase):

  def setUp(self):
    with open(os.path.join(DEST, "UPSTREAM.json"), encoding="utf-8") as fh:
      self.manifest = json.load(fh)

  def test_manifest_pins_upstream(self):
    self.assertEqual(
        self.manifest["repo"], "https://github.com/a2ui-project/a2ui"
    )
    self.assertRegex(self.manifest["commit"], r"^[0-9a-f]{40}$|^v")

  def test_checksums(self):
    for rel, digest in self.manifest["files"].items():
      with self.subTest(rel):
        p = os.path.join(DEST, rel)
        self.assertTrue(os.path.exists(p), f"missing vendored file {rel}")
        self.assertEqual(
            _sha(p),
            digest,
            f"{rel} differs from upstream; vendored files are verbatim. "
            "Use tools/sync_a2ui.py --ref <sha> to refresh.",
        )

  def test_no_unlisted_files(self):
    listed = set(self.manifest["files"]) | {"UPSTREAM.json"}
    for dirpath, _, names in os.walk(DEST):
      for n in names:
        rel = os.path.relpath(os.path.join(dirpath, n), DEST)
        self.assertIn(rel, listed, f"unlisted file in seeds/a2ui: {rel}")

  def test_expected_contents(self):
    files = self.manifest["files"]
    examples = [
        f for f in files if f.startswith("catalogs/basic/v1/examples/")
    ]
    self.assertEqual(len(examples), 43)
    for f in (
        "LICENSE",
        "catalogs/basic/v1/catalog.json",
        "specification/v1_0/json/common_types.json",
        "specification/v1_0/json/catalog_definition.json",
        "specification/v1_0/json/agent_to_renderer.json",
    ):
      self.assertIn(f, files)


if __name__ == "__main__":
  unittest.main()
