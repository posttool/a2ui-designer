"""Unit tests for core/layout_diff.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import layout_diff  # noqa: E402


def msgs(components, data=None):
  out = [
      {
          "version": "v0.9",
          "createSurface": {"surfaceId": "main", "catalogId": "x"},
      },
      {
          "version": "v0.9",
          "updateComponents": {"surfaceId": "main", "components": components},
      },
  ]
  if data is not None:
    out.append({
        "version": "v0.9",
        "updateDataModel": {"surfaceId": "main", "path": "/", "value": data},
    })
  return out


def t(cid, text, variant=None):
  c = {"id": cid, "component": "Text", "text": text}
  if variant:
    c["variant"] = variant
  return c


BASE = msgs([
    {"id": "root", "component": "Column", "children": ["title", "meds", "ok"]},
    t("title", "Morning meds", "h3"),
    {"id": "meds", "component": "Column", "children": ["m1", "m2", "m3"]},
    t("m1", "Ibuprofen"),
    t("m2", "Metformin"),
    t("m3", "Lisinopril"),
    {
        "id": "ok",
        "component": "Button",
        "child": "okl",
        "action": {"event": {"name": "ok"}},
    },
    t("okl", "Mark given"),
])


class LayoutDiffTest(unittest.TestCase):

  def kinds(self, d):
    return [c["kind"] for c in d["changes"]]

  def test_identical_has_no_changes(self):
    d = layout_diff.diff(BASE, BASE)
    self.assertEqual(d["changes"], [])
    self.assertFalse(d["changed"])
    self.assertEqual(d["magnitude"], 0)

  def test_container_swap_and_emphasis(self):
    b = msgs([
        {
            "id": "root",
            "component": "Column",
            "children": ["title", "meds", "ok"],
        },
        t("title", "Morning meds", "h1"),
        {"id": "meds", "component": "List", "children": ["m1", "m2", "m3"]},
        t("m1", "Ibuprofen"),
        t("m2", "Metformin"),
        t("m3", "Lisinopril"),
        {
            "id": "ok",
            "component": "Button",
            "child": "okl",
            "action": {"event": {"name": "ok"}},
        },
        t("okl", "Mark given"),
    ])
    d = layout_diff.diff(BASE, b)
    self.assertIn("container_swap", self.kinds(d))
    self.assertIn("emphasis", self.kinds(d))
    swap = next(c for c in d["changes"] if c["kind"] == "container_swap")
    self.assertEqual(swap["detail"]["from"], "Column")
    self.assertEqual(swap["detail"]["to"], "List")
    self.assertEqual(swap["a_keys"], ["meds|"])
    self.assertEqual(swap["b_keys"], ["meds|"])
    self.assertTrue(d["changed"])

  def test_reorder_add_remove(self):
    b = msgs([
        {
            "id": "root",
            "component": "Column",
            "children": ["ok", "title", "meds", "note"],
        },
        t("title", "Morning meds", "h3"),
        {"id": "meds", "component": "Column", "children": ["m1", "m2"]},
        t("m1", "Ibuprofen"),
        t("m2", "Metformin"),
        t("note", "Give with food"),
        {
            "id": "ok",
            "component": "Button",
            "child": "okl",
            "action": {"event": {"name": "ok"}},
        },
        t("okl", "Mark given"),
    ])
    d = layout_diff.diff(BASE, b)
    k = self.kinds(d)
    self.assertIn("reordered", k)
    self.assertIn("added", k)
    self.assertIn("removed", k)
    added = next(c for c in d["changes"] if c["kind"] == "added")
    self.assertEqual(added["b_keys"], ["note|"])
    removed = next(c for c in d["changes"] if c["kind"] == "removed")
    self.assertEqual(removed["a_keys"], ["m3|"])

  def test_new_card_grouping(self):
    b = msgs([
        {
            "id": "root",
            "component": "Column",
            "children": ["title", "card", "ok"],
        },
        t("title", "Morning meds", "h3"),
        {"id": "card", "component": "Card", "child": "meds"},
        {"id": "meds", "component": "Column", "children": ["m1", "m2", "m3"]},
        t("m1", "Ibuprofen"),
        t("m2", "Metformin"),
        t("m3", "Lisinopril"),
        {
            "id": "ok",
            "component": "Button",
            "child": "okl",
            "action": {"event": {"name": "ok"}},
        },
        t("okl", "Mark given"),
    ])
    d = layout_diff.diff(BASE, b)
    self.assertEqual(self.kinds(d), ["regrouped"])
    self.assertIn("card|", d["changes"][0]["b_keys"])

  def test_binding_matches_literal_and_template_scopes(self):
    data = {
        "meds": [{"n": "Ibuprofen"}, {"n": "Metformin"}, {"n": "Lisinopril"}]
    }
    b = msgs(
        [
            {
                "id": "root",
                "component": "Column",
                "children": ["title", "meds", "ok"],
            },
            t("title", {"path": "/title"}, "h3"),
            {
                "id": "meds",
                "component": "List",
                "children": {"componentId": "row", "path": "/meds"},
            },
            t("row", {"path": "n"}),
            {
                "id": "ok",
                "component": "Button",
                "child": "okl",
                "action": {"event": {"name": "ok"}},
            },
            t("okl", "Mark given"),
        ],
        dict(data, title="Morning meds"),
    )
    d = layout_diff.diff(BASE, b)
    self.assertEqual(self.kinds(d), ["container_swap"])
    tree = layout_diff.Tree(b)
    self.assertIn("row|/meds/1", tree.by_key)
    self.assertEqual(tree.by_key["row|/meds/1"].content, "t:metformin")

  def test_template_repeats_collapse(self):
    rows = [{"n": f"Item {i}", "d": f"Detail {i}"} for i in range(5)]
    a = msgs(
        [
            {
                "id": "root",
                "component": "Column",
                "children": {"componentId": "row", "path": "/rows"},
            },
            {"id": "row", "component": "Column", "children": ["n", "d"]},
            t("n", {"path": "n"}),
            t("d", {"path": "d"}),
        ],
        {"rows": rows},
    )
    b = msgs(
        [
            {
                "id": "root",
                "component": "Column",
                "children": {"componentId": "row", "path": "/rows"},
            },
            {"id": "row", "component": "Row", "children": ["n", "d"]},
            t("n", {"path": "n"}),
            t("d", {"path": "d"}),
        ],
        {"rows": rows},
    )
    d = layout_diff.diff(a, b)
    self.assertEqual(self.kinds(d), ["container_swap"])
    self.assertEqual(d["changes"][0]["repeat"], 5)
    self.assertEqual(len(d["changes"][0]["b_keys"]), 5)

  def test_same_change_and_rules(self):
    c1 = {"kind": "added", "anchor_contents": ["t:a", "t:b"], "detail": {}}
    c2 = {
        "kind": "added",
        "anchor_contents": ["t:a", "t:b", "t:c"],
        "detail": {},
    }
    self.assertTrue(layout_diff.same_change(c1, c2))
    self.assertFalse(layout_diff.same_change(c1, dict(c2, kind="removed")))
    rules = layout_diff.parse_rules(
        "# Lead\n\n- Put the decision first.\n  - even on phones\n- Group by"
        " time.\n\nProse rule\ncontinues.\n\n## Density\n1. One screen."
    )
    self.assertEqual([r["id"] for r in rules], ["R1", "R2", "R3", "R4"])
    self.assertEqual(
        rules[0]["text"], "Put the decision first. — even on phones"
    )
    self.assertEqual(rules[2]["text"], "Prose rule continues.")
    self.assertEqual(rules[3]["section"], "Density")


if __name__ == "__main__":
  unittest.main()
