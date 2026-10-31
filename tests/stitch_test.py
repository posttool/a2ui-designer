"""Stitch client, download outcomes and fact extraction against a fake server."""

import os
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(TESTS)
for p in (PKG, TESTS):
  if p not in sys.path:
    sys.path.insert(0, p)

from fake_stitch import PROJECT_ID, FakeStitch, screen_html  # noqa: E402
from refs import stitch  # noqa: E402


class StitchClientTest(unittest.TestCase):

  @classmethod
  def setUpClass(cls):
    cls.fake = FakeStitch().start()
    cls._env = os.environ.get("DCB_STITCH_MCP_URL")
    os.environ["DCB_STITCH_MCP_URL"] = cls.fake.mcp_url

  @classmethod
  def tearDownClass(cls):
    cls.fake.stop()
    if cls._env is None:
      os.environ.pop("DCB_STITCH_MCP_URL", None)
    else:
      os.environ["DCB_STITCH_MCP_URL"] = cls._env

  def test_parse_url(self):
    self.assertEqual(
        stitch.parse_url(
            f"https://stitch.withgoogle.com/projects/{PROJECT_ID}?pli=1"
        ),
        (PROJECT_ID, None),
    )
    self.assertEqual(stitch.parse_url(PROJECT_ID), (PROJECT_ID, None))
    self.assertEqual(
        stitch.parse_url(
            f"https://stitch.withgoogle.com/projects/{PROJECT_ID}?screen=abcdef1234"
        ),
        (PROJECT_ID, "abcdef1234"),
    )
    for bad in ("https://figma.com/design/x", "https://stitch.withgoogle.com/"):
      with self.assertRaises(stitch.StitchError):
        stitch.parse_url(bad)

  def test_project_screens_design_systems(self):
    c = stitch.StitchClient("test-key")
    projs = c.projects()
    self.assertEqual(len(projs), 1)
    self.assertEqual(projs[0]["id"], PROJECT_ID)
    self.assertEqual(projs[0]["title"], "Pharmacy refills")
    self.assertEqual(projs[0]["screen_count"], 4)
    self.assertEqual(c.project(PROJECT_ID)["title"], "Pharmacy refills")
    screens = c.screens(PROJECT_ID)
    self.assertEqual(
        [(s["id"], s["viewport_hint"], s["width"]) for s in screens][:3],
        [
            ("scr_results", "phone", 412),
            ("scr_empty", "phone", 412),
            ("scr_detail", "desktop", 1280),
        ],
    )
    self.assertIn("test-key", self.fake.seen_keys)
    ds = c.design_systems(PROJECT_ID)
    self.assertEqual(ds[0]["display_name"], "Pharmacy DS")

  def test_download_outcomes(self):
    c = stitch.StitchClient("test-key")
    by = {s["id"]: s for s in c.screens(PROJECT_ID)}
    raw = c.download(by["scr_results"]["screenshot_url"], "image")
    self.assertEqual(raw[:4], b"\x89PNG")
    wrapped = c.download(by["scr_empty"]["screenshot_url"], "image")  # JSON
    self.assertEqual(wrapped[:4], b"\x89PNG")
    html_json = c.download(by["scr_results"]["html_url"], "html")
    self.assertIn(b"tailwind.config", html_json)
    with self.assertRaises(stitch.StitchDownloadBlocked):
      c.download(by["scr_detail"]["screenshot_url"], "image")
    with self.assertRaises(stitch.StitchDownloadBlocked):
      c.download(by["scr_broken"]["html_url"], "html")

  def test_errors(self):
    with self.assertRaises(stitch.StitchError) as cm:
      stitch.StitchClient("wrong").screens(PROJECT_ID)
    self.assertEqual(cm.exception.status, 403)
    with self.assertRaises(stitch.StitchError) as cm:
      stitch.StitchClient("test-key").screens("1234567")
    self.assertEqual(cm.exception.status, 403)
    with self.assertRaises(stitch.StitchError):
      stitch.StitchClient("")


class StitchFactsTest(unittest.TestCase):

  def test_html_facts(self):
    f = stitch.html_facts(screen_html("Refills", 2))
    self.assertEqual(f["tailwind"]["colors"]["primary"], "#0B57D0")
    self.assertEqual(f["tailwind"]["fontFamily"]["headline"][0], "Inter")
    self.assertEqual(
        f["outline"], "header > (h1, nav), main > section×2 > (img, h3, button)"
    )
    self.assertEqual(f["tags"]["button"], 2)

  def test_design_system_facts(self):
    d = stitch.design_system_facts({
        "display_name": "DS",
        "theme": {
            "colorMode": "DARK",
            "headlineFont": "OPEN_SANS",
            "bodyFont": "PLUS_JAKARTA_SANS",
            "roundness": "ROUND_FULL",
            "customColor": "#0EA5E9",
            "overridePrimaryColor": "#996E47",
        },
    })
    self.assertEqual(d["color_mode"], "dark")
    self.assertEqual(
        d["fonts"],
        {"headline": "Open Sans", "body": "Plus Jakarta Sans", "label": None},
    )
    self.assertEqual(d["roundness_px"], 9999)
    self.assertEqual((d["seed_color"], d["primary"]), ("#0EA5E9", "#996E47"))
    self.assertIsNone(stitch.design_system_facts(None))

  def test_stitch_facts(self):
    screens = [
        {
            "id": "a",
            "title": "Results @ Phone",
            "width": 412,
            "height": 915,
            "viewport_hint": "phone",
            "device_type": "MOBILE",
        },
        {
            "id": "b",
            "title": "Empty @ Phone",
            "width": 412,
            "height": 915,
            "viewport_hint": "phone",
            "device_type": "MOBILE",
        },
    ]
    facts = stitch.stitch_facts(
        {"title": "Pharmacy"},
        screens,
        [{"display_name": "DS", "theme": {"roundness": "ROUND_EIGHT"}}],
        {"a": screen_html("R", 3), "b": screen_html("E", 0)},
    )
    self.assertEqual(facts["source"], "stitch")
    self.assertEqual(facts["file_name"], "Pharmacy")
    self.assertEqual(facts["design_system"]["roundness_px"], 8)
    self.assertIn("#0B57D0", [c["hex"] for c in facts["colors"]])
    self.assertIn(16.0, [r["px"] for r in facts["radii"]])  # rounded-lg = 1rem
    self.assertEqual(len(facts["html_outline"]), 2)
    self.assertEqual(facts["frames"][0]["device_type"], "MOBILE")
    self.assertEqual(facts["design_system"]["fonts"]["headline"], "Inter")
    self.assertEqual(facts["design_system"]["fonts"]["body"], "Inter")
    roles_found = {ts.get("role") for ts in facts["text_styles"]}
    self.assertIn("headline", roles_found)


if __name__ == "__main__":
  unittest.main()
