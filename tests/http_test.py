"""End-to-end HTTP tests with a scripted LLM and a fake browser."""

import io
import json
import os
import sys
import time
import unittest
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402


def wait(fn, timeout=15.0):
  deadline = time.time() + timeout
  while time.time() < deadline:
    v = fn()
    if v:
      return v
    time.sleep(0.05)
  raise AssertionError("timed out")


class HttpTest(unittest.TestCase):

  def setUp(self):
    self.s = fixtures.LiveServer()

  def tearDown(self):
    self.s.close()

  def _project(self, ldap="alice"):
    self.s.login(ldap)
    st, body = self.s.call("POST", "/api/projects", {"name": "Care app"})
    self.assertEqual(st, 201, body)
    return body["project"]

  def _samples(self, n_genres=2):
    st, body = self.s.call(
        "POST",
        "/api/samples/synthesize",
        {
            "genres": ["elder_care", "event_planning"][:n_genres],
            "per_genre": 2,
            "set_name": "Mini",
        },
    )
    self.assertEqual(st, 200, body)
    jid = body["job"]["id"]
    job = wait(
        lambda: (lambda j: j if j["status"] in ("done", "failed") else None)(
            self.s.call("GET", f"/api/jobs/{jid}")[1]["job"]
        )
    )
    self.assertEqual(job["status"], "done", job)
    return job

  def test_auth_required_and_login(self):
    st, _ = self.s.call("GET", "/api/projects")
    self.assertEqual(st, 401)
    st, body = self.s.login("alice")
    self.assertEqual(body["user"]["ldap"], "alice")
    self.assertFalse(body["user"]["is_admin"])
    st, body = self.s.call("GET", "/api/me")
    self.assertEqual(body["user"]["ldap"], "alice")
    st, body = self.s.login("bad name!")
    self.assertEqual(st, 400)
    st, body = self.s.login("alice", password="wrong")
    self.assertEqual(st, 401)

  def test_multi_cookie_and_multi_secret_compat(self):
    from server import auth as auth_mod

    multi_secret = b"primary_secret_key\nlegacy_prod_secret_key\n"
    tok_legacy = auth_mod.sign(b"legacy_prod_secret_key", "dkaram")
    self.assertEqual(auth_mod.verify(multi_secret, tok_legacy), "dkaram")
    tok_primary = auth_mod.sign(multi_secret, "dkaram")
    self.assertEqual(
        auth_mod.verify(b"primary_secret_key", tok_primary), "dkaram"
    )
    valid_tok = auth_mod.sign(self.s.app.secret, "carol")
    req = urllib.request.Request(
        self.s.url("/api/me"),
        headers={"Cookie": f"dcb_session=stale.0.bad; dcb_session={valid_tok}"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
      data = json.loads(resp.read().decode("utf-8"))
    self.assertEqual(data["user"]["ldap"], "carol")

  def test_project_starts_empty_and_revisions(self):
    p = self._project()
    self.assertEqual(p["current_revision"]["number"], 0)
    self.assertTrue(p["current_revision"]["is_empty"])
    pid = p["id"]
    st, body = self.s.call(
        "POST",
        f"/api/projects/{pid}/revisions",
        {"text_md": "## A\n- one\n", "summary": "first"},
    )
    self.assertEqual(st, 201)
    r1 = body["revision"]
    self.assertEqual(r1["number"], 1)
    st, body = self.s.call("GET", f"/api/projects/{pid}/revisions")
    self.assertEqual([r["number"] for r in body["revisions"]], [1, 0])
    r0 = body["revisions"][1]["id"]
    st, body = self.s.call(
        "GET", f"/api/projects/{pid}/diff?a={r0}&b={r1['id']}"
    )
    self.assertEqual(body["diff"]["stats"]["added"], 2)
    st, body = self.s.call("POST", f"/api/projects/{pid}/revisions/{r0}/revert")
    self.assertEqual(body["revision"]["number"], 2)
    self.assertEqual(body["revision"]["source"], "revert")

  def test_authz_other_user_and_admin(self):
    p = self._project("alice")
    self.s.login("bob")
    st, _ = self.s.call("GET", f"/api/projects/{p['id']}")
    self.assertEqual(st, 403)
    st, _ = self.s.call("GET", "/api/admin/overview")
    self.assertEqual(st, 403)
    self.s.login("admin1")
    st, body = self.s.call("GET", f"/api/projects/{p['id']}")
    self.assertEqual(st, 200)
    st, body = self.s.call("GET", "/api/admin/overview")
    self.assertEqual(body["totals"]["projects"], 1)

  def test_full_loop(self):
    p = self._project()
    pid = p["id"]
    job = self._samples()
    self.assertEqual(len(job["result"]["sample_ids"]), 4)
    st, body = self.s.call("GET", "/api/sample_sets")
    set_id = body["sets"][0]["id"]
    self.assertEqual(body["sets"][0]["count"], 4)
    st, body = self.s.call("GET", "/api/samples?genre=elder_care")
    self.assertEqual(len(body["samples"]), 2)
    self.assertNotIn("payload", body["samples"][0])
    self.assertEqual(body["samples"][0]["group"], "care")

    # Default project settings use phone + web only.
    self.assertEqual(p["settings"]["viewports"], ["phone"])
    self.assertEqual(p["settings"]["renderers"], ["web"])
    # Run: 4 samples × 1 default viewport (phone).
    st, body = self.s.call(
        "POST", f"/api/projects/{pid}/runs", {"sample_set_id": set_id}
    )
    self.assertEqual(st, 201, body)
    run = body["run"]
    self.assertEqual(run["viewports"], ["phone"])
    self.assertEqual(run["renderers"], ["web"])
    self.assertEqual(run["counts"]["total"], 4)
    run = wait(
        lambda: (lambda r: r if r["status"] == "done" else None)(
            self.s.call("GET", f"/api/runs/{run['id']}")[1]["run"]
        )
    )
    self.assertEqual(run["counts"]["done"], 4)
    self.assertEqual(run["avg_score"], 6)
    st, body = self.s.call("GET", f"/api/runs/{run['id']}/outputs?sort=worst")
    outs = body["outputs"]
    self.assertEqual(len(outs), 4)
    o = outs[0]
    self.assertTrue(o["validation"]["ok"])
    self.assertTrue(o["thumb_url"].startswith("/files/thumbs/"))
    self.assertIn("token=", o["render_url"])
    self.assertEqual(len(self.s.pool.calls), 4)

    # Render data is token-protected.
    st, body = self.s.call("GET", f"/api/render_data/{o['id']}?token=nope")
    self.assertEqual(st, 403)
    token = o["render_url"].split("token=")[1]
    st, body = self.s.call("GET", f"/api/render_data/{o['id']}?token={token}")
    self.assertEqual(
        body["messages"][1]["updateComponents"]["components"][0]["id"], "root"
    )
    st, raw = self.s.call("GET", o["screenshot_url"], raw=True)
    self.assertEqual(st, 200)
    self.assertTrue(raw.startswith(b"\x89PNG"))

    # Verdict + Don't from output.
    st, body = self.s.call(
        "POST",
        f"/api/outputs/{o['id']}/verdict",
        {"human_verdict": "bad", "human_note": "buried"},
    )
    self.assertEqual(body["output"]["human_verdict"], "bad")
    st, body = self.s.call(
        "POST",
        f"/api/projects/{pid}/guidelines",
        {
            "kind": "dont",
            "title": "Don't bury decisions",
            "body_md": "**What**",
            "output_id": o["id"],
            "fixable": "no",
        },
    )
    self.assertEqual(st, 201, body)
    g = body["guideline"]
    self.assertEqual(len(g["screenshots"]), 1)
    st, body = self.s.call(
        "POST",
        f"/api/guidelines/{g['id']}/escalate",
        {"note": "catalog lacks it"},
    )
    self.assertEqual(body["guideline"]["status"], "escalated")
    st, _ = self.s.call(
        "POST",
        f"/api/projects/{pid}/guidelines",
        {"kind": "dont", "title": "Fixable", "body_md": "x", "fixable": "yes"},
    )

    # Suggestions merge starter + judge + fixable guideline.
    st, body = self.s.call("GET", f"/api/projects/{pid}/suggestions")
    sources = {x["source"] for x in body["suggestions"]}
    self.assertEqual(sources, {"starter", "judge", "guideline"})

    # Chat → proposal → accept → rev 1, then re-run & compare.
    st, body = self.s.call(
        "POST",
        f"/api/projects/{pid}/chat",
        {"text": "Decisions are buried", "output_ids": [o["id"]]},
    )
    am = body["assistant_message"]
    self.assertEqual(am["proposal_status"], "pending")
    self.assertTrue(am["proposal"]["diff"]["rows"])
    self.assertEqual(am["out_of_scope"][0]["suggested_home"], "theme")
    revise_call = [c for c in self.s.llm.calls if c["purpose"] == "revise"][-1]
    self.assertTrue(
        any(not isinstance(x, str) for x in revise_call["parts"]),
        "screenshot should be attached",
    )
    st, body = self.s.call(
        "POST", f"/api/projects/{pid}/chat/{am['id']}/accept", {}
    )
    rev = body["revision"]
    self.assertEqual((rev["number"], rev["source"]), (1, "chat"))
    st, body = self.s.call(
        "POST",
        f"/api/projects/{pid}/runs",
        {"sample_set_id": set_id, "viewports": ["phone", "desktop"]},
    )
    run2 = body["run"]
    wait(
        lambda: self.s.call("GET", f"/api/runs/{run2['id']}")[1]["run"][
            "status"
        ]
        == "done"
    )
    st, body = self.s.call(
        "GET",
        f"/api/projects/{pid}/compare?run_a={run['id']}&run_b={run2['id']}",
    )
    self.assertEqual(len(body["pairs"]), 8)
    self.assertEqual(sum(1 for x in body["pairs"] if x["a"]), 4)
    self.assertEqual(sum(1 for x in body["pairs"] if x["b"]), 8)
    gen_call = [c for c in self.s.llm.calls if c["purpose"] == "generate"][-1]
    self.assertIn("Put the decision first", gen_call["system"])

    # Scope check.
    st, body = self.s.call(
        "POST",
        f"/api/projects/{pid}/scope_check",
        {"text_md": "Use 28dp radius"},
    )
    self.assertEqual(
        body["items"][0]["suggested_theme_change"]["token"], "radius_card"
    )

    # Exports.
    st, raw = self.s.call(
        "GET", f"/api/export/projects/{pid}/constitution.md", raw=True
    )
    self.assertIn(b"Put the decision first", raw)
    st, raw = self.s.call(
        "GET", f"/api/export/projects/{pid}/history.json", raw=True
    )
    self.assertEqual(len(json.loads(raw)["revisions"]), 2)
    st, raw = self.s.call(
        "GET", f"/api/export/projects/{pid}/guidelines.zip", raw=True
    )
    names = zipfile.ZipFile(io.BytesIO(raw)).namelist()
    self.assertIn("README.md", names)
    self.assertTrue(any(n.endswith("screenshot_1.png") for n in names), names)
    self.assertTrue(any(n.endswith("output.a2ui.json") for n in names))

    # Admin inbox + benchmark bundle.
    self.s.login("admin1")
    st, body = self.s.call("GET", "/api/admin/inbox")
    self.assertEqual([x["id"] for x in body["guidelines"]], [g["id"]])
    st, body = self.s.call(
        "POST",
        "/api/admin/bundles",
        {"kind": "benchmark", "guideline_ids": [g["id"]], "project_ids": [pid]},
    )
    self.assertEqual(st, 201, body)
    b = body["bundle"]
    self.assertEqual(b["counts"]["guidelines"], 1)
    st, raw = self.s.call("GET", b["download_url"], raw=True)
    z = zipfile.ZipFile(io.BytesIO(raw))
    cases = [json.loads(l) for l in z.read("cases.jsonl").decode().splitlines()]
    self.assertEqual(cases[0]["guideline"]["fixable"], "no")
    self.assertEqual(cases[0]["sample"]["genre"], o["genre"])
    self.assertTrue(cases[0]["screenshot_ref"])
    self.assertIn(cases[0]["constitution_ref"], z.namelist())
    st, body = self.s.call("GET", "/api/admin/inbox")
    self.assertEqual(body["guidelines"], [])

  def test_invalid_output_repairs_and_records(self):
    p = self._project()
    self._samples(1)
    llm = self.s.llm
    llm.push("generate", fixtures.BAD_A2UI)
    llm.push("repair", fixtures.BAD_A2UI)
    st, body = self.s.call("GET", "/api/sample_sets")
    st, body = self.s.call(
        "POST",
        f"/api/projects/{p['id']}/runs",
        {
            "sample_ids": body["sets"][0]["sample_ids"][:1],
            "viewports": ["phone"],
            "judge": False,
        },
    )
    rid = body["run"]["id"]
    wait(
        lambda: self.s.call("GET", f"/api/runs/{rid}")[1]["run"]["status"]
        == "done"
    )
    o = self.s.call("GET", f"/api/runs/{rid}/outputs")[1]["outputs"][0]
    self.assertFalse(o["validation"]["ok"])
    self.assertEqual(o["repairs"], 1)
    self.assertIsNone(o["judge"])

  def test_cancel_and_resume(self):
    p = self._project()
    self._samples()
    gate = __import__("threading").Event()
    orig = self.s.llm.handlers["generate"]

    def slow(s, parts):
      gate.wait(5)
      return orig(s, parts)

    self.s.llm.handlers["generate"] = slow
    sets = self.s.call("GET", "/api/sample_sets")[1]["sets"]
    run = self.s.call(
        "POST",
        f"/api/projects/{p['id']}/runs",
        {"sample_set_id": sets[0]["id"], "viewports": ["phone"]},
    )[1]["run"]
    st, body = self.s.call("POST", f"/api/runs/{run['id']}/cancel")
    self.assertEqual(body["run"]["status"], "cancelled")
    gate.set()
    time.sleep(0.5)
    st, body = self.s.call("POST", f"/api/runs/{run['id']}/resume")
    wait(
        lambda: self.s.call("GET", f"/api/runs/{run['id']}")[1]["run"]["status"]
        == "done"
    )
    r = self.s.call("GET", f"/api/runs/{run['id']}")[1]["run"]
    self.assertEqual(r["counts"]["done"], 4)

  def test_delete_run(self):
    p = self._project()
    self._samples()
    sets = self.s.call("GET", "/api/sample_sets")[1]["sets"]
    run = self.s.call(
        "POST",
        f"/api/projects/{p['id']}/runs",
        {
            "sample_set_id": sets[0]["id"],
            "viewports": ["phone"],
            "judge": False,
        },
    )[1]["run"]
    rid = run["id"]
    wait(
        lambda: self.s.call("GET", f"/api/runs/{rid}")[1]["run"]["status"]
        == "done"
    )
    outs = self.s.call("GET", f"/api/runs/{rid}/outputs")[1]["outputs"]
    oid = outs[0]["id"]
    st, body = self.s.call(
        "POST",
        f"/api/projects/{p['id']}/guidelines",
        {"kind": "dont", "title": "x", "body_md": "y", "output_id": oid},
    )
    gid = body["guideline"]["id"]
    shots = [
        self.s.app.svc.store.get("outputs", o["id"]).get("screenshot_path")
        for o in outs
    ]
    # Another user can't delete it.
    self.s.login("mallory")
    st, _ = self.s.call("DELETE", f"/api/runs/{rid}")
    self.assertEqual(st, 403)
    self.s.login("alice")
    st, body = self.s.call("DELETE", f"/api/runs/{rid}")
    self.assertEqual(st, 200)
    self.assertEqual(body["deleted_run_ids"], [rid])
    st, _ = self.s.call("GET", f"/api/runs/{rid}")
    self.assertEqual(st, 404)
    runs = self.s.call("GET", f"/api/projects/{p['id']}/runs")[1]["runs"]
    self.assertEqual(runs, [])
    self.assertIsNone(self.s.app.svc.store.get("outputs", oid))
    for sp in shots:
      if sp:
        self.assertFalse(os.path.exists(sp))
    g = self.s.call("GET", f"/api/guidelines/{gid}")[1]["guideline"]
    self.assertIsNone(g.get("output_id"))

  def test_static_and_placeholders(self):
    st, raw = self.s.call("GET", "/img/tram/400x300.svg", raw=True)
    self.assertIn(b"<svg", raw)
    st, body = self.s.call("GET", "/api/render_example/01_flight-status")
    self.assertTrue(body["messages"])
    self.s.login("alice")
    st, body = self.s.call("GET", "/api/genres")
    genres = [g["id"] for grp in body["groups"] for g in grp["genres"]]
    self.assertEqual(len(genres), 26)
    for g in (
        "elder_care",
        "child_care",
        "dentistry",
        "self_care",
        "building_management",
        "event_planning",
        "dinner_ordering",
        "vip_comms",
        "scheduling",
        "group_coordination",
    ):
      self.assertIn(g, genres)

  def _wait_job(self, jid):
    return wait(
        lambda: (lambda j: j if j["status"] in ("done", "failed") else None)(
            self.s.call("GET", f"/api/jobs/{jid}")[1]["job"]
        )
    )

  def test_custom_categories_and_brief_synthesis(self):
    self.s.login("alice")
    # Create a category + subcategory, synthesize into the subcategory.
    st, body = self.s.call(
        "POST", "/api/categories", {"kind": "group", "label": "Travel ops"}
    )
    self.assertEqual(st, 201, body)
    gid = body["category"]["id"]
    st, body = self.s.call(
        "POST",
        "/api/categories",
        {"kind": "genre", "parent_id": gid, "label": "Lost luggage"},
    )
    self.assertEqual(st, 201, body)
    xid = body["category"]["id"]
    st, body = self.s.call(
        "POST",
        "/api/samples/synthesize",
        {"genres": [xid], "per_genre": 2, "brief": "Airline lost a bag"},
    )
    self.assertEqual(st, 200, body)
    job = self._wait_job(body["job"]["id"])
    self.assertEqual(job["status"], "done", job)
    st, body = self.s.call("GET", f"/api/samples?genre={xid}")
    self.assertEqual(len(body["samples"]), 2)
    self.assertEqual(body["samples"][0]["group"], gid)
    # Free-form brief: the model files samples into a NEW category.
    st, body = self.s.call(
        "POST",
        "/api/samples/synthesize",
        {"brief": "Hosting six friends for dinner", "count": 3},
    )
    self.assertEqual(st, 200, body)
    job = self._wait_job(body["job"]["id"])
    self.assertEqual(job["status"], "done", job)
    st, tax = self.s.call("GET", "/api/genres")
    grp = next(g for g in tax["groups"] if g["label"] == "Household logistics")
    self.assertTrue(grp["custom"])
    self.assertEqual(grp["genres"][0]["label"], "Dinner for guests")
    st, body = self.s.call("GET", f"/api/samples?group={grp['id']}")
    self.assertEqual(len(body["samples"]), 3)
    # Manual sample with brand-new labels; then move it via PATCH.
    st, body = self.s.call(
        "POST",
        "/api/samples",
        {
            "title": "x",
            "payload": {},
            "group_label": "Odd jobs",
            "genre_label": "Pet sitting",
        },
    )
    self.assertEqual(st, 201, body)
    sid = body["sample"]["id"]
    self.assertEqual(body["sample"]["genre"], "pet_sitting")
    st, body = self.s.call(
        "PATCH", f"/api/samples/{sid}", {"genre": "dinner_ordering"}
    )
    self.assertEqual(body["sample"]["group"], "assistant")
    # Delete rules: in-use subcategory blocked, empty one allowed.
    st, body = self.s.call("DELETE", f"/api/categories/{xid}")
    self.assertEqual(st, 409, body)
    st, body = self.s.call("DELETE", "/api/categories/pet_sitting")
    self.assertEqual(st, 200, body)
    st, body = self.s.call("DELETE", "/api/categories/elder_care")
    self.assertEqual(st, 400, body)
    st, body = self.s.call(
        "PATCH", f"/api/categories/{xid}", {"label": "Baggage issues"}
    )
    self.assertEqual(body["category"]["label"], "Baggage issues")

  def test_web_only_renderer_run_and_on_demand_render(self):
    p = self._project()
    pid = p["id"]
    self._samples(1)
    st, body = self.s.call("GET", "/api/renderers")
    self.assertEqual(st, 200)
    self.assertEqual([r["id"] for r in body["renderers"]], ["web"])
    sets = self.s.call("GET", "/api/sample_sets")[1]["sets"]
    st, body = self.s.call(
        "POST",
        f"/api/projects/{pid}/runs",
        {
            "sample_ids": sets[0]["sample_ids"][:1],
            "viewports": ["phone"],
            # Unknown/removed renderers are ignored.
            "renderers": ["web", "android_compose"],
        },
    )
    self.assertEqual(st, 201, body)
    run = body["run"]
    wait(
        lambda: self.s.call("GET", f"/api/runs/{run['id']}")[1]["run"]["status"]
        == "done"
    )
    outs = self.s.call("GET", f"/api/runs/{run['id']}/outputs")[1]["outputs"]
    self.assertEqual(len(outs), 1)
    o = outs[0]
    self.assertEqual(list(o["renders"]), ["web"])
    self.assertEqual(o["renders"]["web"]["status"], "done")
    self.assertTrue(o["renders"]["web"]["screenshot_url"])

    st, body = self.s.call(
        "POST", f"/api/outputs/{o['id']}/render", {"renderers": ["web"]}
    )
    self.assertEqual(st, 200, body)
    self.assertEqual(body["output"]["renders"]["web"]["status"], "done")
    st, body = self.s.call(
        "POST", f"/api/outputs/{o['id']}/render", {"renderer": "ios_swiftui"}
    )
    self.assertEqual(st, 400, body)

  def test_constitution_effect_study(self):
    p = self._project()
    pid = p["id"]
    self._samples(1)
    sets = self.s.call("GET", "/api/sample_sets")[1]["sets"]
    sids = sets[0]["sample_ids"][:2]
    # Empty constitution → nothing to compare.
    st, body = self.s.call(
        "POST", f"/api/projects/{pid}/effects", {"sample_ids": sids}
    )
    self.assertEqual(st, 400, body)
    st, _ = self.s.call(
        "POST",
        f"/api/projects/{pid}/revisions",
        {
            "text_md": (
                "# Lists\n\n- Put repeated items in a List.\n- Lead with the"
                " title.\n"
            )
        },
    )
    self.assertIn(st, (200, 201))

    treated = json.loads(json.dumps(fixtures.GOOD_A2UI))
    comps = treated["messages"][1]["updateComponents"]["components"]
    comps[1]["component"] = "List"  # col: Column → List
    comps[2]["text"]["args"]["value"] = "# ${/title}"  # title emphasis
    gen_systems = []

    def gen(system, parts):
      del parts
      gen_systems.append(system)
      return (
          fixtures.GOOD_A2UI
          if "No constitution provided" in system
          else treated
      )

    self.s.llm.handlers["generate"] = gen
    self.s.llm.handlers["attribute"] = lambda s, p: {
        "summary": "The List rule turned the column into a list.",
        "overall": "improved",
        "changes": [
            {
                "change": "C0",
                "cause": "noise",
                "rule_ids": [],
                "confidence": "low",
                "explanation": "emphasis",
            },
            {
                "change": "C1",
                "cause": "rule",
                "rule_ids": ["R1", "R99"],
                "confidence": "high",
                "explanation": "List rule",
            },
        ],
        "unapplied_rules": [{"rule_id": "R2", "why": "title not first"}],
    }
    st, body = self.s.call(
        "POST",
        f"/api/projects/{pid}/effects",
        {
            "sample_ids": sids,
            "viewports": ["phone"],
            "renderers": ["web"],
            "noise": True,
            "temperature": 0.3,
        },
    )
    self.assertEqual(st, 201, body)
    eid = body["effect"]["id"]
    runs = body["effect"]["runs"]
    self.assertEqual(runs["control"]["kind"], "effect_control")
    self.assertTrue(runs["control"]["no_constitution"])
    self.assertEqual(runs["control"]["label"], "No constitution")
    self.assertEqual(runs["treatment"]["kind"], "effect_treatment")
    self.assertEqual(runs["noise"]["kind"], "effect_noise")
    self.assertEqual(
        runs["treatment"]["config"]["generator"]["temperature"], 0.3
    )

    data = wait(
        lambda: (lambda d: d if d["analysis"]["status"] == "done" else None)(
            self.s.call("GET", f"/api/effects/{eid}")[1]
        ),
        timeout=20,
    )
    self.assertEqual(data["effect"]["status"], "done")
    self.assertEqual(
        sum("No constitution provided" in x for x in gen_systems), 4
    )
    self.assertEqual(len(data["pairs"]), 2)
    pair = data["pairs"][0]
    kinds = [c["kind"] for c in pair["changes"]]
    self.assertEqual(kinds, ["emphasis", "container_swap"])
    self.assertEqual(pair["noise_magnitude"], 0)
    self.assertEqual(pair["verdict"], "effect")
    swap = pair["changes"][1]
    self.assertEqual(swap["attribution"]["rule_ids"], ["R1"])  # R99 dropped
    self.assertFalse(swap["in_noise"])
    self.assertEqual(pair["attribution"]["overall"], "improved")
    self.assertEqual([r["id"] for r in data["rules"]], ["R1", "R2"])
    s = data["summary"]
    self.assertEqual(s["verdicts"], {"effect": 2})
    r1 = next(r for r in s["rules"] if r["id"] == "R1")
    self.assertEqual((r1["changes"], r1["pairs"]), (2, 2))
    r2 = next(r for r in s["rules"] if r["id"] == "R2")
    self.assertEqual((r2["changes"], r2["unapplied"]), (0, 2))
    # Judge scores both against the revision.
    self.assertEqual(s["judge"], {"control": 6, "treatment": 6})
    st, body = self.s.call("GET", f"/api/projects/{pid}/effects")
    self.assertEqual(body["effects"][0]["id"], eid)
    self.assertEqual(body["effects"][0]["headline"]["top_rules"], ["R1"])
    # Re-analyze.
    st, body = self.s.call("POST", f"/api/effects/{eid}/analyze")
    self.assertEqual(st, 200, body)

  def test_chat_token_changes_and_custom_tokens(self):
    p = self._project()
    pid = p["id"]
    rev0_num = p["current_revision"]["number"]

    # 1. Standalone token proposal in Chat (no constitution text change)
    self.s.llm.push(
        "revise",
        {
            "reply_md": "Updated your palette and card radius.",
            "proposal": {
                "has_proposal": True,
                "constitution_md": "",
                "change_summary": "Warm editorial palette and sharper cards",
                "changed_sections": [],
                "theme_changes": [
                    {
                        "token": "primary",
                        "value": "#854D0E",
                        "maps_to": "",
                        "reason": "Warm bronze primary",
                    },
                    {
                        "token": "--a2ui-radius-card",
                        "value": "10px",
                        "maps_to": "",
                        "reason": "Sharper card corners",
                    },
                    {
                        "token": "brand-highlight",
                        "value": "#FDE68A",
                        "maps_to": "secondary_container",
                        "reason": (
                            "Custom highlight token mapped to"
                            " secondary_container"
                        ),
                    },
                ],
                "custom_css": ".a2ui-Card { letter-spacing: 0.01em; }",
            },
            "out_of_scope": [],
        },
    )
    st, body = self.s.call(
        "POST",
        f"/api/projects/{pid}/chat",
        {"text": "Make the palette warm bronze with 10px card radius"},
    )
    self.assertEqual(st, 200, body)
    am = body["assistant_message"]
    self.assertEqual(am["proposal_status"], "pending")
    self.assertFalse(am["proposal"]["has_constitution_change"])
    self.assertEqual(len(am["proposal"]["theme_changes"]), 3)

    st, accept_body = self.s.call(
        "POST", f"/api/projects/{pid}/chat/{am['id']}/accept", {}
    )
    self.assertEqual(st, 200, accept_body)
    self.assertFalse(accept_body["has_constitution_change"])
    # Revision number did not increment because only tokens changed
    self.assertEqual(accept_body["revision"]["number"], rev0_num)
    theme = accept_body["settings"]["theme"]
    self.assertEqual(theme["primary"], "#854D0E")
    self.assertEqual(theme["radius_card"], 10)
    self.assertIn("brand-highlight", theme["custom_tokens"])
    self.assertEqual(
        theme["custom_tokens"]["brand-highlight"]["maps_to"],
        "secondary_container",
    )
    self.assertIn("letter-spacing", theme["custom_css"])

    # Verify effective theme resolution maps brand-highlight -> secondary_container
    from core import settings as settings_mod

    eff = settings_mod.resolve_effective_theme(theme)
    self.assertEqual(eff["secondary_container"], "#FDE68A")

    # 2. Combined Constitution + Token proposal in Chat
    self.s.llm.push(
        "revise",
        {
            "reply_md": "Added hierarchy rule and bumped heading weight.",
            "proposal": {
                "has_proposal": True,
                "constitution_md": "## Hierarchy\n\n- Use clear H1 headers.\n",
                "change_summary": "Add hierarchy section + bold headings",
                "changed_sections": ["Hierarchy"],
                "theme_changes": [{
                    "token": "heading_weight",
                    "value": "700",
                    "maps_to": "",
                    "reason": "Bolder headings",
                }],
                "custom_css": "",
            },
            "out_of_scope": [],
        },
    )
    st, body2 = self.s.call(
        "POST",
        f"/api/projects/{pid}/chat",
        {"text": "Add a hierarchy rule and make headings bold 700"},
    )
    self.assertEqual(st, 200, body2)
    am2 = body2["assistant_message"]
    self.assertTrue(am2["proposal"]["has_constitution_change"])
    self.assertEqual(len(am2["proposal"]["theme_changes"]), 1)

    st, accept_body2 = self.s.call(
        "POST", f"/api/projects/{pid}/chat/{am2['id']}/accept", {}
    )
    self.assertEqual(st, 200, accept_body2)
    self.assertTrue(accept_body2["has_constitution_change"])
    self.assertEqual(accept_body2["revision"]["number"], rev0_num + 1)
    self.assertEqual(accept_body2["settings"]["theme"]["heading_weight"], 700)

  def test_selected_component_in_verdict_and_chat(self):
    p = self._project()
    pid = p["id"]
    self._samples(1)
    sets = self.s.call("GET", "/api/sample_sets")[1]["sets"]
    st, body = self.s.call(
        "POST",
        f"/api/projects/{pid}/runs",
        {
            "sample_ids": sets[0]["sample_ids"][:1],
            "viewports": ["phone"],
            "renderers": ["web"],
        },
    )
    self.assertEqual(st, 201, body)
    run = body["run"]
    wait(
        lambda: self.s.call("GET", f"/api/runs/{run['id']}")[1]["run"]["status"]
        == "done"
    )
    outs = self.s.call("GET", f"/api/runs/{run['id']}/outputs")[1]["outputs"]
    oid = outs[0]["id"]

    # 1. Submitting a verdict with a selected component tags human_note
    st, vbody = self.s.call(
        "POST",
        f"/api/outputs/{oid}/verdict",
        {
            "human_verdict": "bad",
            "human_note": "too dense on mobile",
            "selected_component": {"id": "root", "component": "Card"},
        },
    )
    self.assertEqual(st, 200, vbody)
    self.assertEqual(vbody["output"]["human_verdict"], "bad")
    self.assertEqual(
        vbody["output"]["human_note"], "[Card #root] too dense on mobile"
    )

    # 2. Sending a chat message with selected_component includes the component
    #    and its immediate children in the prompt sent to reviser
    captured_parts = []

    def handle_revise(system, parts):
      del system
      captured_parts.extend(parts)
      return {
          "reply_md": "Focusing on `Card` (`#root`): tightened card density.",
          "proposal": {
              "has_proposal": True,
              "constitution_md": "## Cards\n\n- Keep root cards concise.\n",
              "change_summary": "Tighten Card density",
              "changed_sections": ["Cards"],
              "theme_changes": [],
              "custom_css": "",
          },
          "out_of_scope": [],
      }

    self.s.llm.handlers["revise"] = handle_revise
    st, cbody = self.s.call(
        "POST",
        f"/api/projects/{pid}/chat",
        {
            "text": "This card is too crowded",
            "selected_component": {
                "id": "root",
                "component": "Card",
                "output_id": oid,
            },
        },
    )
    self.assertEqual(st, 200, cbody)
    um = cbody["user_message"]
    self.assertIn(oid, um["context"]["output_ids"])
    self.assertEqual(um["context"]["selected_component"]["id"], "root")
    self.assertEqual(um["context"]["selected_component"]["component"], "Card")
    prompt_text = "\n".join(p for p in captured_parts if isinstance(p, str))
    self.assertIn("# Selected component in Live View", prompt_text)
    self.assertIn("`Card` (id: `root`", prompt_text)
    self.assertIn('"id": "col"', prompt_text)

  def test_wizard_domains_still_served(self):
    self.s.login("alice")
    st, dbody = self.s.call("GET", "/api/wizard/domains")
    self.assertEqual(st, 200, dbody)
    self.assertGreaterEqual(len(dbody["domains"]), 5)
    p = self._project()
    st, _ = self.s.call("POST", f"/api/projects/{p['id']}/wizard/start", {})
    self.assertIn(st, (404, 405))


if __name__ == "__main__":
  unittest.main()
