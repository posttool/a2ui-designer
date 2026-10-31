"""Starter-example cache: keys, hit/miss behaviour and staleness of the shipped cache."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402

from sim import starter_cache  # noqa: E402

ACTIVE = ("queued", "generating", "rendering", "judging")
BOXES = [
    {"k": "t", "id": "t", "x": 16, "y": 16, "w": 300, "h": 28},
    {"k": "b", "id": "b", "x": 16, "y": 60, "w": 120, "h": 40},
]


class BoxPool(fixtures.FakePool):
  """FakePool whose render meta carries layout boxes like the real one."""

  def screenshot(self, url, width, height, out_path, scale=2.0, timeout=25.0):
    meta = super().screenshot(url, width, height, out_path, scale, timeout)
    meta["boxes"] = [dict(b) for b in BOXES]
    return meta


def wait(fn, timeout=20.0):
  deadline = time.time() + timeout
  while time.time() < deadline:
    v = fn()
    if v:
      return v
    time.sleep(0.05)
  raise AssertionError("timed out")


SAMPLE = {
    "group": "assistant",
    "genre": "dinner",
    "title": "Dinner",
    "intent": "order",
    "context": {"surface": "card"},
    "payload": {"a": 1},
    "size_class": "m",
    "state": "content",
    "edge_cases": [],
    "meta": {"wizard_seed_id": "seed_x"},
}
CFG = {
    "generator": {
        "model": "m1",
        "framing": "card",
        "temperature": 0.7,
        "max_repairs": 1,
        "include_guidelines": False,
    },
    "imagery": {"enabled": True, "image_model": "i", "svg_model": "s"},
    "catalog": {"base": "basic", "catalog_id": "https://x/projects/a/c.json"},
    "catalog_hash": "c1",
    "theme_hash": "t1",
    "templates_hash": "p1",
}


class KeyTest(unittest.TestCase):

  def test_key_is_stable_and_sensitive(self):
    k = starter_cache.cache_key(CFG, SAMPLE, "phone")
    self.assertEqual(k, starter_cache.cache_key(dict(CFG), dict(SAMPLE), "phone"))
    self.assertEqual(len(k), 64)
    # Ids, owners, timestamps and the project-specific catalog id don't matter.
    other = dict(SAMPLE, id="s_other", owner_ldap="bob", created_at="x")
    cfg2 = dict(CFG, catalog=dict(CFG["catalog"], catalog_id="https://y"))
    self.assertEqual(k, starter_cache.cache_key(cfg2, other, "phone"))
    changed = [
        starter_cache.cache_key(CFG, SAMPLE, "tablet"),
        starter_cache.cache_key(dict(CFG, theme_hash="t2"), SAMPLE, "phone"),
        starter_cache.cache_key(dict(CFG, catalog_hash="c2"), SAMPLE, "phone"),
        starter_cache.cache_key(
            dict(CFG, generator=dict(CFG["generator"], model="m2")),
            SAMPLE,
            "phone",
        ),
        starter_cache.cache_key(
            dict(CFG, generator=dict(CFG["generator"], framing="page")),
            SAMPLE,
            "phone",
        ),
        starter_cache.cache_key(CFG, dict(SAMPLE, payload={"a": 2}), "phone"),
        starter_cache.cache_key(
            CFG, dict(SAMPLE, meta={"wizard_seed_id": "seed_y"}), "phone"
        ),
        starter_cache.cache_key(
            CFG, SAMPLE, "phone", constitution_md="## Rule"
        ),
    ]
    self.assertNotIn(k, changed)
    self.assertEqual(len(set(changed)), len(changed))
    with mock.patch.object(starter_cache, "prompt_sha", return_value="zz"):
      self.assertNotEqual(k, starter_cache.cache_key(CFG, SAMPLE, "phone"))
    inputs = starter_cache.key_inputs(CFG, SAMPLE, "phone")
    self.assertEqual(inputs["renderer"], "web")
    self.assertEqual(inputs["protocol"], "v1.0")
    self.assertEqual(inputs["seed_id"], "seed_x")

  def test_gen_assets_of(self):
    msgs = [{"a": "/api/gen/abc123.png", "b": ["x /api/gen/d_e.svg?y", 3]}]
    self.assertEqual(
        starter_cache.gen_assets_of(msgs), ["abc123.png", "d_e.svg"]
    )


class CacheHttpTest(unittest.TestCase):
  """Builds a cache with the scripted pipeline, then serves a project from it."""

  @classmethod
  def setUpClass(cls):
    cls.cache_dir = tempfile.mkdtemp(prefix="starter_cache_test_")
    cls.env = mock.patch.dict(
        os.environ,
        {"A2UID_STARTER_CACHE": "0", "A2UID_STARTER_CACHE_DIR": cls.cache_dir},
    )
    cls.env.start()
    cls.pool = BoxPool()
    cls.srv = fixtures.LiveServer(pool=cls.pool)
    cls.srv.login("alice")
    # 1) Live baseline, exported into the cache dir.
    st = cls._new_session("Builder")
    rid = st["session"]["baseline_run_id"]
    store = cls.srv.app.svc.store
    wait(
        lambda: all(
            o["status"] not in ACTIVE
            for o in store.query("SELECT status FROM outputs WHERE run_id=?", [rid])
        ),
        30,
    )
    run = store.get("runs", rid)
    # Put a generated asset in the payload so asset copying is covered.
    os.makedirs(store.file_path("gen"), exist_ok=True)
    with open(store.file_path("gen", "feedc0de.svg"), "w") as fh:
      fh.write("<svg xmlns='http://www.w3.org/2000/svg'/>")
    entries = []
    for o in store.query("SELECT * FROM outputs WHERE run_id=?", [rid]):
      o = store.get("outputs", o["id"])
      o["a2ui"] = list(o.get("a2ui") or []) + [
          {"note": "/api/gen/feedc0de.svg"}
      ]
      entries.append(starter_cache.export_entry(store, run, o, cls.cache_dir))
    starter_cache.write_index(cls.cache_dir, entries)
    cls.seed_ids = {e["seed_id"] for e in entries}
    cls.store = store

  @classmethod
  def tearDownClass(cls):
    cls.srv.close()
    cls.env.stop()
    shutil.rmtree(cls.cache_dir, ignore_errors=True)

  @classmethod
  def _new_session(cls, name, **body):
    code, p = cls.srv.call("POST", "/api/projects", {"name": name})
    assert code in (200, 201), p
    pid = p["project"]["id"]
    code, st = cls.srv.call(
        "POST",
        f"/api/projects/{pid}/teach",
        dict({"groups": ["assistant", "care"], "count": 6}, **body),
    )
    assert code in (200, 201), st
    return st

  def test_index_written(self):
    idx = starter_cache.load_index(self.cache_dir)
    self.assertEqual(len(idx["entries"]), 6)
    ent = next(iter(idx["entries"].values()))
    self.assertTrue(
        os.path.isfile(os.path.join(self.cache_dir, ent["path"], "screen.png"))
    )
    self.assertTrue(
        os.path.isfile(
            os.path.join(self.cache_dir, starter_cache.ASSETS_DIR, "feedc0de.svg")
        )
    )

  def test_hit_outputs_done_instantly(self):
    before = len(self.pool.calls)
    with mock.patch.dict(os.environ, {"A2UID_STARTER_CACHE": "1"}):
      # Wipe the gen dir so materialize has to copy the asset back.
      os.remove(self.store.file_path("gen", "feedc0de.svg"))
      st = self._new_session("Cached")
    rid = st["session"]["baseline_run_id"]
    # Synchronously done: no polling, no generation, no screenshots.
    outs = self.store.query("SELECT * FROM outputs WHERE run_id=?", [rid])
    self.assertEqual(len(outs), 6)
    for o in outs:
      self.assertEqual(o["status"], "done", o)
      self.assertTrue(os.path.isfile(o["screenshot_path"]))
      self.assertTrue(o["screenshot_path"].endswith(f"{o['id']}.png"))
      self.assertTrue(os.path.isfile(o["thumb_path"]))
      self.assertEqual(o["renders"]["web"]["status"], "done")
      self.assertEqual(o["render_meta"]["boxes"], BOXES)
      self.assertEqual(o["render_meta"]["height"], 300)
      self.assertTrue(o["a2ui"])
    self.assertTrue(os.path.isfile(self.store.file_path("gen", "feedc0de.svg")))
    self.assertEqual(self.store.get("runs", rid)["status"], "done")
    self.assertEqual(len(self.pool.calls), before)
    # Cards are ready in the session state and links resolve from cached boxes.
    code, state = self.srv.call("GET", f"/api/teach/{st['session']['id']}")
    self.assertEqual(code, 200)
    self.assertEqual(len(state["cards"]), 6)
    code, links = self.srv.call("GET", f"/api/outputs/{outs[0]['id']}/links")
    self.assertEqual(code, 200, links)
    self.assertIsInstance(links, dict)

  def test_miss_uses_live_pipeline(self):
    with mock.patch.dict(os.environ, {"A2UID_STARTER_CACHE": "1"}):
      # Different constitution → different key → live pipeline.
      code, p = self.srv.call(
          "POST",
          "/api/projects",
          {"name": "Imported", "constitution_md": "## Lead with the answer\n"},
      )
      self.assertIn(code, (200, 201), p)
      before = len(self.pool.calls)
      code, st = self.srv.call(
          "POST",
          f"/api/projects/{p['project']['id']}/teach",
          {"groups": ["assistant", "care"], "count": 6},
      )
      self.assertIn(code, (200, 201), st)
    rid = st["session"]["baseline_run_id"]
    wait(
        lambda: all(
            o["status"] == "done"
            for o in self.store.query(
                "SELECT status FROM outputs WHERE run_id=?", [rid]
            )
        ),
        30,
    )
    self.assertGreaterEqual(len(self.pool.calls) - before, 6)
    for o in self.store.query("SELECT * FROM outputs WHERE run_id=?", [rid]):
      self.assertNotIn("starter_cache", o.get("render_meta") or {})

  def test_disabled_via_env(self):
    with mock.patch.dict(os.environ, {"A2UID_STARTER_CACHE": "0"}):
      before = len(self.pool.calls)
      st = self._new_session("Disabled")
    rid = st["session"]["baseline_run_id"]
    wait(
        lambda: all(
            o["status"] == "done"
            for o in self.store.query(
                "SELECT status FROM outputs WHERE run_id=?", [rid]
            )
        ),
        30,
    )
    self.assertGreaterEqual(len(self.pool.calls) - before, 6)

  def test_standard_runs_never_use_cache(self):
    with mock.patch.dict(os.environ, {"A2UID_STARTER_CACHE": "1"}):
      code, p = self.srv.call("POST", "/api/projects", {"name": "Std"})
      sids = [
          r["id"]
          for r in self.store.query("SELECT id, meta_json FROM samples")
      ][:2]
      before = len(self.pool.calls)
      code, run = self.srv.call(
          "POST",
          f"/api/projects/{p['project']['id']}/runs",
          {"sample_ids": sids, "viewports": ["phone"], "judge": False},
      )
      self.assertIn(code, (200, 201), run)
    wait(lambda: len(self.pool.calls) - before >= 2, 30)


CACHE_INDEX = os.path.join(starter_cache.CACHE_DIR, starter_cache.INDEX_NAME)


def _committed_entries():
  try:
    with open(CACHE_INDEX, encoding="utf-8") as fh:
      return json.load(fh).get("entries") or {}
  except (OSError, ValueError):
    return {}


@unittest.skipUnless(
    _committed_entries(), "no shipped starter cache (seeds/starter_examples)"
)
class ShippedCacheFreshnessTest(unittest.TestCase):
  """Fails when the committed cache no longer matches the current inputs.

  Fix: python3 tools/build_starter_cache.py (needs a Gemini key + Chrome).
  """

  def test_committed_keys_match_current_inputs(self):
    with open(CACHE_INDEX, encoding="utf-8") as fh:
      idx = json.load(fh)
    self.assertFalse(idx.get("dry_run"), "committed cache came from --dry_run")
    entries = idx["entries"]
    env = {"A2UID_STARTER_CACHE": "1"}
    env.pop("A2UID_STARTER_CACHE_DIR", None)
    with mock.patch.dict(os.environ, env):
      os.environ.pop("A2UID_STARTER_CACHE_DIR", None)
      srv = fixtures.LiveServer()
      try:
        app = srv.app
        svc = app.svc
        user = svc.login("alice", admin=True)
        from sim import wizard as wizard_mod  # pylint: disable=g-import-not-at-top

        seeds = wizard_mod.ensure_starter_samples(svc, user, None)
        p = svc.create_project(user, "Fresh")
        by_vp = {}
        for e in entries.values():
          by_vp.setdefault(e["viewport"], set()).add(e["seed_id"])
        problems = []
        for vp, want in sorted(by_vp.items()):
          sids = [s["id"] for s in seeds if starter_cache.seed_id_of(s) in want]
          run = app.runs.create_run(
              user,
              p["id"],
              {
                  "sample_ids": sids,
                  "viewports": [vp],
                  "renderers": ["web"],
                  "judge": False,
                  "include_forms": False,
              },
              kind="teach_baseline",
          )
          rrow = svc.store.get("runs", run["id"])
          for o in svc.store.query(
              "SELECT * FROM outputs WHERE run_id=?", [run["id"]]
          ):
            inputs = starter_cache.inputs_for_output(svc.store, rrow, o)
            key = starter_cache.key_from_inputs(inputs)
            if key in entries:
              continue
            old = next(
                (
                    e["inputs"]
                    for e in entries.values()
                    if e["seed_id"] == inputs["seed_id"]
                    and e["viewport"] == vp
                ),
                {},
            )
            drift = sorted(
                k for k in set(old) | set(inputs) if old.get(k) != inputs.get(k)
            )
            problems.append(f"{inputs['seed_id']}/{vp}: {drift}")
        missing = {
            starter_cache.seed_id_of(s) for s in seeds
        } - by_vp.get("phone", set())
        if missing:
          problems.append(f"seeds without a phone entry: {sorted(missing)}")
        self.assertFalse(
            problems,
            "starter cache is stale; re-run tools/build_starter_cache.py:\n"
            + "\n".join(problems),
        )
      finally:
        srv.close()


if __name__ == "__main__":
  unittest.main()
