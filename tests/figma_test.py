"""Figma URL parsing and design-fact extraction on a fixture node tree."""

import os
import sys
import unittest

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG not in sys.path:
  sys.path.insert(0, PKG)

from refs import figma  # noqa: E402
from refs.references import guess_viewport, png_size  # noqa: E402


def _solid(r, g, b, a=1.0):
  return {"type": "SOLID", "color": {"r": r, "g": g, "b": b, "a": a}}


FRAME = {
    "id": "1:2",
    "name": "Refill / Results / Content @ Phone",
    "type": "FRAME",
    "absoluteBoundingBox": {"x": 0, "y": 0, "width": 412, "height": 915},
    "fills": [_solid(1, 1, 1)],
    "layoutMode": "VERTICAL",
    "itemSpacing": 16,
    "paddingLeft": 24,
    "paddingTop": 24,
    "children": [
        {
            "id": "1:3",
            "name": "Card/Elevated",
            "type": "INSTANCE",
            "cornerRadius": 24,
            "fills": [_solid(0.941, 0.957, 0.976)],
            "layoutMode": "VERTICAL",
            "itemSpacing": 8,
            "children": [
                {
                    "id": "1:4",
                    "name": "Headline",
                    "type": "TEXT",
                    "characters": "Lisinopril 10 mg",
                    "style": {
                        "fontFamily": "Google Sans",
                        "fontSize": 24,
                        "fontWeight": 500,
                        "lineHeightPx": 32,
                    },
                    "fills": [_solid(0.122, 0.122, 0.122)],
                },
                {
                    "id": "1:5",
                    "name": "Button/Primary",
                    "type": "INSTANCE",
                    "cornerRadius": 12,
                    "fills": [_solid(0.043, 0.341, 0.816)],
                    "children": [],
                },
            ],
        },
        {
            "id": "1:6",
            "name": "Hidden",
            "type": "RECTANGLE",
            "visible": False,
            "fills": [_solid(1, 0, 0)],
        },
    ],
}
PRINCIPLES = {
    "id": "2:1",
    "name": "Principles",
    "type": "FRAME",
    "absoluteBoundingBox": {"width": 1600, "height": 400},
    "children": [{
        "id": "2:2",
        "name": "Rules",
        "type": "TEXT",
        "characters": (
            "• One primary action per card\n• Show at most 3 items on phone"
        ),
        "style": {"fontFamily": "Roboto", "fontSize": 14, "fontWeight": 400},
    }],
}


class FigmaTest(unittest.TestCase):

  def test_parse_url(self):
    key, ids = figma.parse_url(
        "https://www.figma.com/design/AbC123/My-File?node-id=12-34&t=x"
    )
    self.assertEqual((key, ids), ("AbC123", ["12:34"]))
    self.assertEqual(
        figma.parse_url("AbC1234567890123456789"),
        ("AbC1234567890123456789", []),
    )
    self.assertEqual(figma.parse_url("https://figma.com/file/K/x")[1], [])
    for bad in ("https://example.com/design/K", "https://figma.com/proto/K/x"):
      with self.assertRaises(figma.FigmaError):
        figma.parse_url(bad)

  def test_parse_source_url(self):
    self.assertEqual(
        figma.parse_source_url(
            "https://www.figma.com/files/team/111/project/222222/Team-Proj"
        ),
        {
            "kind": "project",
            "id": "222222",
            "url": "https://www.figma.com/files/project/222222",
        },
    )
    self.assertEqual(
        figma.parse_source_url(
            "https://www.figma.com/files/team/999999/My-Team"
        ),
        {
            "kind": "team",
            "id": "999999",
            "url": "https://www.figma.com/files/team/999999",
        },
    )
    self.assertEqual(
        figma.parse_source_url("https://www.figma.com/design/AbC123/My-File")[
            "kind"
        ],
        "file",
    )

  def test_extract_facts(self):
    f = figma.extract_facts(
        [FRAME, PRINCIPLES],
        [{"name": "Primary", "style_type": "FILL"}],
        None,
        "Pharmacy",
    )
    self.assertEqual(f["frames"][0]["viewport_hint"], "phone")
    hexes = [c["hex"] for c in f["colors"]]
    self.assertIn("#0B57D0", hexes)
    self.assertNotIn("#FF0000", hexes)  # invisible node ignored
    self.assertEqual(sorted(r["px"] for r in f["radii"]), [12, 24])
    self.assertEqual(f["text_styles"][0]["family"], "Google Sans")
    self.assertEqual(f["spacing"]["grid_hint"], 8)
    self.assertEqual(f["components"][0]["instances"], 1)
    self.assertEqual(
        f["principles_text"],
        ["One primary action per card", "Show at most 3 items on phone"],
    )
    self.assertFalse(f["variables"]["available"])

  def test_top_frames_from_nodes(self):
    nodes_doc = {
        "nodes": {
            "9:9": {
                "document": {
                    "id": "9:9",
                    "type": "SECTION",
                    "children": [FRAME, PRINCIPLES],
                }
            }
        }
    }
    frames = figma.top_frames({}, ["9:9"], nodes_doc)
    self.assertEqual([fr["id"] for fr in frames], ["1:2", "2:1"])

  def test_image_helpers(self):
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000003a800000780080600000000"
    )
    self.assertEqual(png_size(png), (936, 1920))
    self.assertEqual(guess_viewport(1236, 2745), "phone")  # 3x phone
    self.assertEqual(guess_viewport(1280, 800), "desktop")
    self.assertEqual(guess_viewport(0, 0), "unknown")

  def test_find_components(self):
    def box(w, h):
      return {"absoluteBoundingBox": {"x": 0, "y": 0, "width": w, "height": h}}

    page = {
        "id": "0:1",
        "type": "CANVAS",
        "children": [{
            "id": "1:1",
            "type": "SECTION",
            "children": [
                dict(
                    box(400, 600),
                    id="2:1",
                    type="COMPONENT_SET",
                    name="Order card",
                    componentPropertyDefinitions={
                        "Title#12:0": {"type": "TEXT", "defaultValue": "Order"},
                        "State": {
                            "type": "VARIANT",
                            "defaultValue": "Default",
                            "variantOptions": ["Default", "Done"],
                        },
                    },
                    children=[
                        dict(
                            box(360, 200),
                            id="2:2",
                            type="COMPONENT",
                            name="State=Default",
                        ),
                        dict(
                            box(360, 200),
                            id="2:3",
                            type="COMPONENT",
                            name="State=Done",
                        ),
                    ],
                ),
                dict(
                    box(380, 140),
                    id="3:1",
                    type="COMPONENT",
                    name="urgent card",
                ),
                dict(box(24, 24), id="3:2", type="COMPONENT", name="icon/star"),
                dict(
                    box(380, 140),
                    id="3:3",
                    type="COMPONENT",
                    name="_private part",
                ),
            ],
        }],
    }
    comps = figma.find_components(
        [page], {"componentSets": {"2:1": {"description": "Order summary"}}}
    )
    by = {c["node_id"]: c for c in comps}
    self.assertEqual(list(by), ["2:1", "3:1", "3:2", "3:3"])  # no variants
    s = by["2:1"]
    self.assertEqual(s["variant_count"], 2)
    self.assertEqual(s["default_variant_id"], "2:2")
    self.assertEqual((s["width"], s["height"]), (360, 200))
    self.assertEqual(s["description"], "Order summary")
    self.assertEqual(s["variant_props"], {"State": ["Default", "Done"]})
    self.assertEqual(s["property_defs"]["Title"]["type"], "TEXT")
    self.assertEqual(
        {k: v["suggested"] for k, v in by.items()},
        {"2:1": True, "3:1": True, "3:2": False, "3:3": False},
    )

  def test_component_outline_maps_tokens(self):
    node = {
        "type": "COMPONENT",
        "name": "Card",
        "absoluteBoundingBox": {"width": 300, "height": 100},
        "layoutMode": "VERTICAL",
        "itemSpacing": 8,
        "fills": [_solid(0x0B / 255, 0x57 / 255, 0xD0 / 255)],
        "cornerRadius": 16,
        "children": [{
            "type": "TEXT",
            "name": "Title",
            "characters": "Hello",
            "style": {"fontFamily": "Inter", "fontSize": 18, "fontWeight": 600},
            "fills": [_solid(1, 1, 1)],
        }],
    }
    out = figma.component_outline(
        node, {"primary": "#0B57D0", "on_primary": "#FFFFFF"}
    )
    self.assertEqual(out["layout"]["dir"], "column")
    self.assertEqual(out["fill"], "#0B57D0 = primary")
    self.assertEqual(out["radius"], 16)
    self.assertEqual(out["children"][0]["color"], "#FFFFFF = on_primary")
    self.assertEqual(out["children"][0]["font"]["size"], 18)


if __name__ == "__main__":
  unittest.main()
