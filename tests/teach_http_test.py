"""End-to-end HTTP test of teaching sessions (scripted Gemini, fake browser)."""

import base64
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402

CROP = "data:image/png;base64," + base64.b64encode(fixtures.PNG).decode()


def wait(fn, timeout=20.0):
  deadline = time.time() + timeout
  while time.time() < deadline:
    v = fn()
    if v:
      return v
    time.sleep(0.05)
  raise AssertionError("timed out")


def _text(parts):
  return "\n".join(p for p in parts if isinstance(p, str))


def distill_handler(system, parts):
  del system
  obs = re.findall(r"\[(ob_[A-Za-z0-9]+)\]", _text(parts))
  return {
      "reply_md": "Lead with the decision; totals stay compact.",
      "teachings": [{
          "action": "new",
          "teaching_id": "",
          "merged_ids": [],
          "title": "Lead with the decision",
          "body_md": "Lead every card with the decision the person must make.",
          "section": "Hierarchy & lead-in",
          "polarity": "do",
          "applies_when": "a card asks the person to confirm or choose",
          "data_signature": "decision + details",
          "reason": "from the designer's notes",
          "source_observation_ids": obs[:2],
          "confidence": 0.8,
      }],
      "theme_changes": [
          {"token": "radius_card", "value": "20", "reason": "softer"}
      ],
      "out_of_scope": [],
  }


def applications_handler(system, parts):
  del system
  ids = re.findall(r"^## (s_[A-Za-z0-9]+)", _text(parts), re.M)
  return {
      "applies": [
          {
              "sample_id": i,
              "how_it_applies": "the decision leads",
              "relevance": "high",
          }
          for i in ids[:2]
      ],
      "exceptions": (
          [{"sample_id": ids[2], "why": "read-only"}] if len(ids) > 2 else []
      ),
      "none_reason": "",
      "when_to_use": "Use when a priced list must be confirmed.",
  }


def judge_handler(system, parts):
  del system
  has_r1 = "- R1" in _text(parts)
  return {
      **fixtures.JUDGE,
      "findings": (
          [{
              "rule_id": "R1",
              "rule": "Lead with the decision",
              "status": "violated",
              "evidence": "CTA last",
              "severity": "high",
          }]
          if has_r1
          else []
      ),
  }


def voice_handler(system, parts):
  del system
  t = _text(parts)
  utter = t.rsplit("# Utterance", 1)[-1]
  if "save" in utter:
    return {"intent": "capture", "observation": {}, "say": "Saved."}
  return {
      "intent": "observe",
      "observation": {
          "polarity": "bad",
          "text": "The title should lead",
          "anchors": [
              {"kind": "component", "key": "t|", "why": "pointed"},
              {"kind": "component", "key": "nope|", "why": "bogus"},
          ],
          "complete": True,
      },
      "question": "",
      "quick_replies": [],
      "say": "Noted.",
  }


class TeachHttpTest(unittest.TestCase):

  def setUp(self):
    llm = fixtures.make_llm()
    llm.handlers["teach_distill"] = distill_handler
    llm.handlers["teach_applications"] = applications_handler
    llm.handlers["judge"] = judge_handler
    llm.handlers["teach_voice_turn"] = voice_handler
    llm.handlers["teach_pattern"] = lambda s, p: {
        "name": "Title and action",
        "description": "Headline then one action.",
        "structure_md": "- {headline}\n- {primary action}",
        "components": ["Column", "Text", "Button"],
        "triggers": [
            {"group": "data", "text": "a headline and one action", "on": True},
            {"group": "intent", "text": "confirming something", "on": False},
        ],
        "when_to_use": "Use when there is one decision.",
        "data_signature": "title + action",
    }
    llm.handlers["teach_rephrase"] = lambda s, p: {
        "body_md": "Put the decision in the first Text of the card.",
        "applies_when": "a decision card",
        "reason": "names the component",
    }
    llm.handlers["attribute"] = lambda s, p: {
        "summary": "",
        "overall": "neutral",
        "changes": [],
        "unapplied_rules": [],
    }
    self.s = fixtures.LiveServer(llm=llm)
    self.s.login("alice")
    st, body = self.s.call("POST", "/api/projects", {"name": "Teach"})
    self.assertEqual(st, 201, body)
    self.pid = body["project"]["id"]

  def tearDown(self):
    self.s.close()

  def _settle(self, sid):
    return wait(
        lambda: (lambda st: st if not st["busy"] else None)(
            self.s.call("GET", f"/api/teach/{sid}")[1]
        )
    )

  def test_full_session(self):
    st, state = self.s.call(
        "POST",
        f"/api/projects/{self.pid}/teach",
        {"groups": ["assistant"], "count": 3, "brief": "calm cards"},
    )
    self.assertEqual(st, 201, state)
    sid = state["session"]["id"]
    self.assertEqual(len(state["cards"]), 3)
    self.assertEqual(
        state["runs"][state["session"]["baseline_run_id"]]["kind"],
        "teach_baseline",
    )
    state = self._settle(sid)
    card = state["cards"][0]
    oid = card["output_id"]
    self.assertEqual(card["output"]["status"], "done")

    # Links
    st, links = self.s.call("GET", f"/api/outputs/{oid}/links")
    self.assertEqual(st, 200, links)
    keys = {c["key"] for c in links["components"]}
    self.assertIn("t|", keys)
    leaf = next(l for l in links["leaves"] if l["kind"] in ("text", "number"))

    # Observations with every anchor kind + crop
    st, res = self.s.call(
        "POST",
        f"/api/teach/{sid}/observations",
        {
            "output_id": oid,
            "polarity": "bad",
            "text": "The decision is buried",
            "anchors": [
                {"kind": "component", "key": "t|"},
                {"kind": "data", "path": leaf["path"]},
                {"kind": "relation", "path": leaf["path"], "key": "b|"},
                {"kind": "region", "box": [0, 10, 200, 80]},
            ],
            "crop_data_url": CROP,
        },
    )
    self.assertEqual(st, 201, res)
    ob = res["observation"]
    self.assertEqual(len(ob["anchors"]), 4)
    self.assertTrue(ob["crop_url"].startswith("/files/teach/"))
    st, raw = self.s.call("GET", ob["crop_url"], raw=True)
    self.assertEqual(st, 200)
    st, err = self.s.call(
        "POST",
        f"/api/teach/{sid}/observations",
        {
            "output_id": oid,
            "polarity": "bad",
            "text": "x",
            "anchors": [{"kind": "component", "key": "missing|"}],
        },
    )
    self.assertEqual(st, 400, err)
    self.s.call(
        "POST",
        f"/api/teach/{sid}/observations",
        {
            "output_id": oid,
            "polarity": "good",
            "text": "Nice title",
        },
    )

    # Distill → candidate
    st, res = self.s.call("POST", f"/api/teach/{sid}/distill", {})
    self.assertEqual(st, 200, res)
    self.assertEqual(len(res["result"]["created"]), 1)
    tid = res["result"]["created"][0]
    state = res["state"]
    t = next(x for x in state["teachings"] if x["id"] == tid)
    self.assertEqual(t["status"], "candidate")
    self.assertEqual(t["label"], "C1")
    self.assertTrue(
        all(o["status"] == "distilled" for o in state["observations"])
    )
    self.assertTrue(
        any(
            x["id"] == tid
            for o in state["observations"]
            for x in o["outcome"]["teachings"]
        )
    )
    self.assertEqual(
        state["session"]["summary"]["theme_suggestions"][0]["token"],
        "radius_card",
    )
    st, again = self.s.call("POST", f"/api/teach/{sid}/distill", {})
    self.assertEqual(st, 400, again)
    self.s.app.teach._distilling.add(sid)
    try:
      st, in_prog = self.s.call("POST", f"/api/teach/{sid}/distill", {})
      self.assertEqual(st, 409, in_prog)
      st, busy_st = self.s.call("GET", f"/api/teach/{sid}")
      self.assertEqual(st, 200)
      self.assertTrue(busy_st["distilling"])
      self.assertTrue(busy_st["busy"])
    finally:
      self.s.app.teach._distilling.discard(sid)

    # Confirm → R1
    st, res = self.s.call(
        "PATCH", f"/api/teachings/{tid}", {"status": "confirmed"}
    )
    self.assertEqual(st, 200, res)
    state = self.s.call("GET", f"/api/teach/{sid}")[1]
    t = next(x for x in state["teachings"] if x["id"] == tid)
    self.assertEqual(t["label"], "R1")

    # Applications + probe → judge trial
    st, res = self.s.call("POST", f"/api/teachings/{tid}/applications", {})
    self.assertEqual(st, 200, res)
    apps = res["teaching"]["applications"]
    self.assertTrue(apps["applies"])
    target = apps["applies"][0]["sample_id"]
    st, state = self.s.call(
        "POST",
        f"/api/teachings/{tid}/probe",
        {"session_id": sid, "sample_id": target},
    )
    self.assertEqual(st, 200, state)
    probe = state["cards"][-1]
    self.assertEqual(probe["origin"], "probe")
    self.assertEqual(probe["teaching_id"], tid)
    state = self._settle(sid)
    run = self.s.app.store.get("runs", probe["run_id"])
    self.assertEqual(run["kind"], "teach_probe")
    self.assertIn("R1", run["config"]["rule_map"])
    self.assertIn("Lead every card", run["config"]["constitution_override"])
    st, det = self.s.call("GET", f"/api/teachings/{tid}")
    self.assertEqual(st, 200, det)
    self.assertIn(
        ("judge", "no"), {(x["source"], x["followed"]) for x in det["trials"]}
    )
    t = next(x for x in state["teachings"] if x["id"] == tid)
    self.assertEqual(
        t["applications"]["applies"][0]["probe_output_id"], probe["output_id"]
    )

    # Designer trial on the probe card
    st, res = self.s.call(
        "POST",
        f"/api/teach/{sid}/observations",
        {
            "output_id": probe["output_id"],
            "polarity": "bad",
            "text": "Still ignores it",
            "about_teaching_id": tid,
        },
    )
    self.assertEqual(st, 201, res)
    det = self.s.call("GET", f"/api/teachings/{tid}")[1]
    self.assertIn("designer", {x["source"] for x in det["trials"]})
    self.assertEqual(det["teaching"]["efficacy"]["trials"], 2)

    # Rephrase suggestion → PATCH creates a phrasing
    st, sug = self.s.call("POST", f"/api/teachings/{tid}/rephrase", {})
    self.assertEqual(st, 200, sug)
    st, res = self.s.call(
        "PATCH",
        f"/api/teachings/{tid}",
        {
            "body_md": sug["suggestion"]["body_md"],
            "reason": sug["suggestion"]["reason"],
        },
    )
    self.assertEqual(st, 200, res)
    self.assertEqual(res["teaching"]["version"], 2)

    # Template from an example
    st, dr = self.s.call(
        "POST",
        "/api/teach/patterns/draft",
        {"output_id": oid, "selection": {"key": "col|"}},
    )
    self.assertEqual(st, 200, dr)
    draft = dr["draft"]
    self.assertTrue(draft["skeleton"].startswith("Column["))
    self.assertEqual(len(draft["triggers"]), 2)
    st, chk = self.s.call(
        "POST",
        "/api/teach/patterns/check",
        {
            "session_id": sid,
            "name": draft["name"],
            "structure_md": draft["structure_md"],
            "when_to_use": draft["when_to_use"],
            "examples": [{"sample_id": target, "applies": False}],
        },
    )
    self.assertEqual(st, 200, chk)
    self.assertEqual(
        chk["when_to_use"], "Use when a priced list must be confirmed."
    )
    st, sv = self.s.call(
        "POST",
        "/api/teach/patterns",
        {
            "session_id": sid,
            "output_id": oid,
            "selection": {"key": "col|"},
            "crop_data_url": CROP,
            "name": draft["name"],
            "description": draft["description"],
            "structure_md": draft["structure_md"],
            "components": draft["components"],
            "skeleton": draft["skeleton"],
            "when_to_use": chk["when_to_use"],
            "examples": [{"sample_id": target, "applies": False}],
        },
    )
    self.assertEqual(st, 201, sv)
    self.assertEqual(sv["teaching"]["kind"], "pattern")
    self.assertTrue(sv["template"]["image_url"])
    self.assertIn("not for", sv["template"]["when_to_use"])
    tpls = self.s.call("GET", f"/api/projects/{self.pid}/templates")[1][
        "templates"
    ]
    self.assertEqual(len(tpls), 1)

    # Voice: observe (bad anchor dropped) then capture
    st, vt = self.s.call(
        "POST",
        f"/api/teach/{sid}/voice_turn",
        {
            "output_id": oid,
            "transcript": "this title should lead",
            "source": "voice",
            "pointed": [{"kind": "component", "key": "t|"}],
        },
    )
    self.assertEqual(st, 200, vt)
    self.assertEqual(vt["intent"], "observe")
    self.assertEqual([a["key"] for a in vt["observation"]["anchors"]], ["t|"])
    self.assertIsNone(vt["captured"])
    st, vt2 = self.s.call(
        "POST",
        f"/api/teach/{sid}/voice_turn",
        {
            "output_id": oid,
            "transcript": "save that",
            "source": "voice",
            "pending": vt["observation"],
        },
    )
    self.assertEqual(st, 200, vt2)
    self.assertEqual(vt2["captured"]["source"], "voice")
    self.assertIn("this title should lead", vt2["captured"]["transcript"])

    # Finish (polish keeps the single rule)
    st, asm = self.s.call(
        "POST", f"/api/teach/{sid}/assemble", {"polish": True}
    )
    self.assertEqual(st, 200, asm)
    self.assertTrue(asm["polished"], asm)
    self.assertEqual(asm["bind_hint"], {"R1": tid})
    self.assertEqual(len(asm["guideline_preview"]), 1)
    st, state = self.s.call(
        "POST",
        f"/api/teach/{sid}/finish",
        {
            "constitution_md": asm["constitution_md"],
            "bind_hint": asm["bind_hint"],
            "apply_theme": ["radius_card"],
        },
    )
    self.assertEqual(st, 200, state)
    sess = state["session"]
    self.assertEqual(sess["status"], "finishing")
    rev = self.s.app.store.get("constitution_revisions", sess["revision_id"])
    self.assertEqual(rev["source"], "teach")
    b = self.s.app.ledger.bindings(rev["id"])
    self.assertEqual(b["R1"]["teaching_id"], tid)
    self.assertEqual(b["R1"]["how"], "assembled")
    gls = self.s.call("GET", f"/api/projects/{self.pid}/guidelines")[1][
        "guidelines"
    ]
    self.assertEqual(len(gls), 1)
    self.assertIn(f"teaching:{tid}", gls[0]["tags"])
    self.assertGreaterEqual(len(gls[0]["screenshots"]), 2)
    settings = self.s.call("GET", f"/api/projects/{self.pid}/settings")[1][
        "settings"
    ]
    self.assertEqual(settings["theme"]["radius_card"], 20)
    state = self._settle(sid)
    self.assertEqual(state["session"]["status"], "done")
    self.assertIsNotNone(state["effect"])
    eff = self.s.app.store.get("effects", sess["effect_id"])
    self.assertEqual(eff["control_run_id"], sess["baseline_run_id"])
    self.assertEqual(eff["options"]["source"], "teach")

    # Ledger page
    st, led = self.s.call("GET", f"/api/projects/{self.pid}/teachings")
    self.assertEqual(st, 200, led)
    self.assertEqual(led["summary"]["templates"], 1)
    self.assertGreaterEqual(led["summary"]["trials"], 2)

    # Sessions list + access control
    st, lst = self.s.call("GET", f"/api/projects/{self.pid}/teach")
    self.assertEqual(lst["sessions"][0]["id"], sid)
    self.s.login("bob")
    st, _ = self.s.call("GET", f"/api/teach/{sid}")
    self.assertIn(st, (403, 404))


if __name__ == "__main__":
  unittest.main()
