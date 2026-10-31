"""Unit tests for core/data_links.py (input data ↔ component links)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import data_links  # noqa: E402

SAMPLE = {
    "title": "Dinner",
    "intent": "Confirm the order",
    "context": {"urgency": "time-sensitive", "role": "parent"},
    "payload": {
        "restaurant": {
            "name": "Osteria Verde",
            "rating": 4.8,
            "hero": "/img/pasta/640x360",
        },
        "cart": [
            {"item": "GF Rigatoni", "price_usd": 19.5},
            {"item": "Margherita Pizza", "price_usd": 21.0},
            {"item": "Broccolini", "price_usd": 11.0},
        ],
        "total_usd": 93.95,
        "notes": "Leave at the door please",
        "count": 4,
    },
}

MSGS = [
    {
        "version": "v1.0",
        "createSurface": {"surfaceId": "main", "catalogId": "x"},
    },
    {
        "version": "v1.0",
        "updateComponents": {
            "surfaceId": "main",
            "components": [
                {
                    "id": "root",
                    "component": "Column",
                    "children": ["name", "img", "rating", "list", "total"],
                },
                {
                    "id": "name",
                    "component": "Text",
                    "text": {"@path": "/r/name"},
                },
                {"id": "img", "component": "Image", "url": {"@path": "/r/hero"}},
                {
                    "id": "rating",
                    "component": "Text",
                    "text": {
                        "@call": "formatString",
                        "args": {"value": "★ ${/r/rating}"},
                    },
                },
                {
                    "id": "list",
                    "component": "List",
                    "children": {"componentId": "row", "path": "/items"},
                },
                {"id": "row", "component": "Row", "children": ["it", "pr"]},
                {"id": "it", "component": "Text", "text": {"@path": "item"}},
                {"id": "pr", "component": "Text", "text": {"@path": "price"}},
                {"id": "total", "component": "Text", "text": "Total $93.95"},
            ],
        },
    },
    {
        "version": "v1.0",
        "updateDataModel": {
            "surfaceId": "main",
            "path": "/",
            "value": {
                "r": {
                    "name": "Osteria Verde",
                    "hero": "/img/pasta/640x360",
                    "rating": 4.8,
                },
                "items": [
                    {"item": "GF Rigatoni", "price": "$19.50"},
                    {"item": "Margherita Pizza", "price": "$21.00"},
                    {"item": "Broccolini", "price": "$11.00"},
                ],
            },
        },
    },
]

BOXES = [
    {"k": "row|/items/1", "x": 10, "y": 100, "w": 300, "h": 40},
    {"k": "total|", "x": 10, "y": 200, "w": 300, "h": 30},
]


class DataLinksTest(unittest.TestCase):

  def setUp(self):
    self.r = data_links.compute_links(MSGS, SAMPLE, BOXES, 400)
    self.leaves = {l["path"]: l for l in self.r["leaves"]}
    self.comps = {c["key"]: c for c in self.r["components"]}

  def test_bound_through_template_rows_by_scope(self):
    leaf = self.leaves["/cart/1/item"]
    self.assertEqual(leaf["represented"], "bound")
    self.assertEqual(leaf["components"], ["it|/items/1"])

  def test_formatstring_reference_is_bound(self):
    self.assertEqual(self.leaves["/restaurant/rating"]["represented"], "bound")
    self.assertIn("rating|", self.leaves["/restaurant/rating"]["components"])

  def test_literal_number_formats(self):
    # "$19.50" (a data-model string) matches the payload number 19.5.
    leaf = self.leaves["/cart/0/price_usd"]
    self.assertIn(leaf["represented"], ("literal", "bound"))
    self.assertIn("pr|/items/0", leaf["components"])
    self.assertEqual(self.leaves["/total_usd"]["represented"], "literal")
    self.assertEqual(self.leaves["/total_usd"]["components"], ["total|"])

  def test_image_url_and_list_binding(self):
    self.assertEqual(self.leaves["/restaurant/hero"]["represented"], "bound")
    self.assertEqual(self.leaves["/cart"]["represented"], "bound")
    self.assertEqual(self.leaves["/cart"]["count"], 3)

  def test_unrepresented_and_small_ints_not_guessed(self):
    self.assertIn("/notes", self.r["unrepresented"])
    self.assertEqual(self.leaves["/count"]["represented"], "none")
    self.assertIn("context.urgency", self.r["unrepresented"])

  def test_small_count_next_to_its_field_name(self):
    s = {"payload": {"events": [{"attendees": 4, "rooms": 2}]}}
    m = [{
        "version": "v1.0",
        "updateComponents": {
            "surfaceId": "main",
            "components": [{
                "id": "root",
                "component": "Text",
                "text": "Dana Chen · 4 attendees",
            }],
        },
    }]
    leaves = {l["path"]: l for l in data_links.compute_links(m, s)["leaves"]}
    self.assertEqual(leaves["/events/0/attendees"]["represented"], "literal")
    self.assertEqual(leaves["/events/0/rooms"]["represented"], "none")

  def test_boxes_merge_by_key(self):
    self.assertEqual(self.comps["total|"]["box"]["y"], 200)
    self.assertEqual(self.comps["row|/items/1"]["box"]["w"], 300)
    self.assertIsNone(self.comps["name|"]["box"])

  def test_subtree_outline_and_skeleton_match(self):
    sub = data_links.subtree_outline(MSGS, "list|")
    self.assertEqual(sub["skeleton"], "List[Row[Text×2]×3]")
    self.assertIn("Row", sub["components"])
    self.assertEqual(
        data_links.skeleton_match(MSGS, "List[Row[Text×2]×5]"), "exact"
    )
    self.assertEqual(data_links.skeleton_match(MSGS, "Tabs[Card×2]"), "none")
    self.assertIn("it|/items/0", data_links.subtree_keys(MSGS, "list|"))


if __name__ == "__main__":
  unittest.main()
