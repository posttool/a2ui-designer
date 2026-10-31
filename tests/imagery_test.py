"""Tests for generated imagery & SVG iconography styles, materialization, and model settings."""

import io
import json
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402
from core import imagery as imagery_mod  # noqa: E402
from core.a2ui import catalog as catalog_mod  # noqa: E402
from core.a2ui import custom_web  # noqa: E402
from core.a2ui import validator  # noqa: E402
from sim import imagery_gen  # noqa: E402

TINY_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class ImageryTest(unittest.TestCase):

  def setUp(self):
    super().setUp()
    self.s = fixtures.LiveServer()
    self.s.login("alice")
    _, body = self.s.call("POST", "/api/projects", {"name": "Weather & Tasks"})
    self.pid = body["project"]["id"]

  def tearDown(self):
    self.s.close()
    super().tearDown()

  def test_models_catalog_and_settings_selection(self):
    st, models_res = self.s.call("GET", "/api/models")
    self.assertEqual(st, 200)
    model_ids = [m["id"] for m in models_res["models"]]
    image_model_ids = [m["id"] for m in models_res["image_models"]]
    self.assertIn("gemini-3.8-flash", model_ids)
    self.assertIn("gemini-3.1-flash-image", image_model_ids)
    self.assertEqual(models_res["defaults"]["generator"], "gemini-3.8-flash")
    self.assertEqual(models_res["defaults"]["image"], "gemini-3.1-flash-image")
    self.assertEqual(models_res["defaults"]["svg"], "gemini-3.8-flash")

    # Update generator, judge, and imagery models in project settings
    _, cur = self.s.call("GET", f"/api/projects/{self.pid}/settings")
    s = cur["settings"]
    s["generator"]["model"] = "gemini-3.7-flash"
    s["judge"]["model"] = "gemini-3.1-pro-preview"
    s["imagery"] = {
        "enabled": True,
        "image_model": "gemini-3-pro-image",
        "svg_model": "gemini-3.8-flash",
    }
    st, saved = self.s.call(
        "PUT", f"/api/projects/{self.pid}/settings", {"settings": s}
    )
    self.assertEqual(st, 200)
    self.assertEqual(
        saved["settings"]["generator"]["model"], "gemini-3.7-flash"
    )
    self.assertEqual(
        saved["settings"]["judge"]["model"], "gemini-3.1-pro-preview"
    )
    self.assertEqual(
        saved["settings"]["imagery"]["image_model"], "gemini-3-pro-image"
    )

  def test_validator_and_custom_web_support_imagery_and_svg(self):
    cat = catalog_mod.build_catalog({})
    msgs = [
        {
            "version": "v0.9",
            "createSurface": {"surfaceId": "main", "catalogId": cat.catalog_id},
        },
        {
            "version": "v0.9",
            "updateComponents": {
                "surfaceId": "main",
                "components": [
                    {
                        "id": "root",
                        "component": "Card",
                        "child": "col",
                        "backgroundImage": {
                            "url": (
                                "/gen/img?style=weather-bg&prompt=sunny+coast&aspect=16:9"
                            ),
                            "scrim": "auto",
                            "fit": "cover",
                        },
                    },
                    {
                        "id": "col",
                        "component": "Column",
                        "children": ["row1", "row2"],
                    },
                    {
                        "id": "row1",
                        "component": "Row",
                        "children": ["ic1", "t1"],
                    },
                    {
                        "id": "ic1",
                        "component": "Icon",
                        "name": {
                            "url": (
                                "/gen/svg?style=list-item-icon&prompt=umbrella"
                            )
                        },
                        "size": 24,
                    },
                    {"id": "t1", "component": "Text", "text": "Bring umbrella"},
                    {
                        "id": "row2",
                        "component": "Row",
                        "children": ["ic2", "t2"],
                    },
                    {
                        "id": "ic2",
                        "component": "Icon",
                        "name": {
                            "svg": (
                                '<svg viewBox="0 0 24 24"><circle cx="12"'
                                ' cy="12" r="8"/></svg>'
                            )
                        },
                    },
                    {"id": "t2", "component": "Text", "text": "Inline SVG"},
                ],
            },
        },
    ]
    res = validator.validate(msgs, cat)
    self.assertTrue(res.ok, res.errors)

    chk = custom_web.check_definition(
        {
            "name": "WeatherWidget",
            "description": (
                "Weather summary card with background image and inline SVG"
            ),
            "props": {
                "type": "object",
                "properties": {
                    "bg": {"type": "string"},
                    "title": {"type": "string"},
                },
            },
            "web": {
                "template": (
                    '<div class="ww" data-bg="{{bg}}"><svg viewBox="0 0 24'
                    ' 24"><defs><linearGradient id="g"><stop offset="0%"'
                    ' stop-color="#fff"/></linearGradient></defs><path d="M4'
                    ' 12h16" stroke="url(#g)"/></svg><h3>{{title}}</h3></div>'
                ),
                "css": (
                    ".ww{padding:var(--a2ui-card-padding);color:var(--a2ui-on-surface)}"
                ),
            },
        },
        catalog_mod.basic_names(),
    )
    self.assertEqual(chk["issues"], [])

  def test_imagery_crud_preview_materialization_and_package(self):
    # Create a weather background preset with a reference image
    st, res = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/imagery",
        {
            "name": "Weather Full-Bleed Background",
            "slug": "weather-bg",
            "medium": "raster_image",
            "target_slot": "card_background",
            "aspect_ratio": "16:9",
            "scrim": "auto",
            "incorporation": (
                "Use as full-bleed Card.backgroundImage on weather widgets."
            ),
            "style_text": "Atmospheric gouache sky with warm horizon light.",
            "reference_images": [{
                "data_url": TINY_PNG_DATA_URL,
                "name": "sunset_ref.png",
                "caption": "Warm horizon palette",
            }],
        },
    )
    self.assertEqual(st, 201)
    weather_style = res["style"]
    self.assertEqual(weather_style["slug"], "weather-bg")
    self.assertEqual(len(weather_style["reference_images"]), 1)

    # Serve reference image
    ref_url = weather_style["reference_images"][0]["url"]
    st_ref, ref_bytes = self.s.call("GET", ref_url, raw=True)
    self.assertEqual(st_ref, 200)
    self.assertTrue(len(ref_bytes) > 10)

    # Create an SVG list-item icon preset
    st, res2 = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/imagery",
        {
            "name": "List Item Duotone Icon",
            "slug": "list-item-icon",
            "medium": "svg_icon",
            "target_slot": "list_item_icon",
            "aspect_ratio": "1:1",
            "incorporation": (
                "Use 24px generated SVG Icon next to each list item."
            ),
            "style_text": "2px rounded duotone outline icon.",
        },
    )
    self.assertEqual(st, 201)
    icon_style = res2["style"]

    # Generate live preview for both presets
    st, prev1 = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/imagery/{weather_style['id']}/preview",
        {"prompt": "golden hour over bay"},
    )
    self.assertEqual(st, 200)
    self.assertTrue(prev1["preview_url"].startswith("/api/gen/"))

    st_gen, gen_bytes = self.s.call("GET", prev1["preview_url"], raw=True)
    self.assertEqual(st_gen, 200)
    self.assertTrue(len(gen_bytes) > 20)

    st, prev2 = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/imagery/{icon_style['id']}/preview",
        {"prompt": "calendar reminder bell"},
    )
    self.assertEqual(st, 200)
    self.assertTrue(prev2["preview_url"].endswith(".svg"))

    # Materialize messages containing /gen/img and /gen/svg URLs
    a2ui_msgs = [
        {
            "version": "v0.9",
            "createSurface": {"surfaceId": "main", "catalogId": "basic"},
        },
        {
            "version": "v0.9",
            "updateComponents": {
                "surfaceId": "main",
                "components": [
                    {
                        "id": "root",
                        "component": "Card",
                        "child": "row",
                        "backgroundImage": {
                            "url": (
                                "/gen/img?style=weather-bg&prompt=partly+cloudy+68F&aspect=16:9"
                            ),
                            "scrim": "auto",
                        },
                    },
                    {
                        "id": "row",
                        "component": "Row",
                        "children": ["ic", "txt"],
                    },
                    {
                        "id": "ic",
                        "component": "Icon",
                        "name": {
                            "url": (
                                "/gen/svg?style=list-item-icon&prompt=rain+umbrella"
                            )
                        },
                    },
                    {
                        "id": "txt",
                        "component": "Text",
                        "text": "68°F Partly Cloudy",
                    },
                ],
            },
        },
    ]
    materialized, _ = imagery_gen.materialize_messages(
        a2ui_msgs,
        store=self.s.app.store,
        llm=self.s.llm,
        project_id=self.pid,
        styles=imagery_mod.list_styles(self.s.app.store, self.pid),
    )
    comps = materialized[1]["updateComponents"]["components"]
    bg_url = comps[0]["backgroundImage"]["url"]
    ic_url = comps[2]["name"]["url"]
    self.assertTrue(bg_url.startswith("/api/gen/"), bg_url)
    self.assertTrue(
        ic_url.startswith("/api/gen/") and ic_url.endswith(".svg"), ic_url
    )

    # Export design package zip and check imagery/styles.json is included
    st_zip, zip_bytes = self.s.call(
        "GET", f"/api/export/projects/{self.pid}/package.zip", raw=True
    )
    self.assertEqual(st_zip, 200)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
      names = zf.namelist()
      styles_entry = [n for n in names if n.endswith("imagery/styles.json")]
      self.assertEqual(len(styles_entry), 1)
      exported_styles = json.loads(zf.read(styles_entry[0]).decode("utf-8"))
      self.assertEqual(len(exported_styles), 2)

  def test_chat_proposal_creates_imagery_style_with_attached_references(self):
    self.s.llm.handlers["revise"] = lambda sys_prompt, parts: {
        "reply_md": (
            "Added a full-bleed weather background rule and style preset."
        ),
        "proposal": {
            "has_proposal": True,
            "constitution_md": (
                "# Weather Widgets\n\n- Use a full-bleed `Card.backgroundImage`"
                ' with `style=weather-bg` and `scrim: "auto"` on weather'
                " summary cards.\n"
            ),
            "change_summary": (
                "Add weather-bg full-bleed imagery rule and preset"
            ),
            "changed_sections": ["Weather Widgets"],
            "theme_changes": [],
            "imagery_changes": [{
                "slug": "weather-bg",
                "name": "Weather Full-Bleed Background",
                "medium": "raster_image",
                "target_slot": "card_background",
                "incorporation": (
                    "Full-bleed Card.backgroundImage on weather widgets"
                ),
                "style_text": "Soft atmospheric gouache sky matching reference",
                "aspect_ratio": "16:9",
                "scrim": "auto",
                "use_attached_references": True,
                "reason": (
                    "User requested full-bleed weather background with style"
                    " reference"
                ),
            }],
            "custom_css": "",
        },
        "out_of_scope": [],
    }
    st, chat_res = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/chat",
        {
            "text": (
                "Use a full-bleed background image of the weather in a weather"
                " widget referencing this style"
            ),
            "attached_images": [{
                "data_url": TINY_PNG_DATA_URL,
                "name": "weather_style.png",
                "caption": "Gouache clouds",
            }],
        },
    )
    self.assertEqual(st, 200)
    amsg = chat_res["assistant_message"]
    self.assertEqual(len(amsg["proposal"]["imagery_changes"]), 1)

    st, acc = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/chat/{amsg['id']}/accept",
        {},
    )
    self.assertEqual(st, 200)
    self.assertEqual(len(acc["imagery_changes_applied"]), 1)
    applied = acc["imagery_changes_applied"][0]
    self.assertEqual(applied["slug"], "weather-bg")
    self.assertEqual(len(applied["reference_images"]), 1)


if __name__ == "__main__":
  unittest.main()
