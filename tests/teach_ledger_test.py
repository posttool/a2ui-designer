"""Tests for the teachings ledger: bindings, phrasings, trials and signals."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402
from core import util  # noqa: E402


class TeachLedgerTest(unittest.TestCase):

  def setUp(self):
    self.s = fixtures.LiveServer()
    self.s.login("alice")
    st, body = self.s.call("POST", "/api/projects", {"name": "Ledger"})
    self.assertEqual(st, 201, body)
    self.pid = body["project"]["id"]
    self.svc = self.s.app.svc
    self.ledger = self.s.app.ledger
    self.user = self.svc.user("alice")

  def tearDown(self):
    self.s.close()

  def _rev(self, md, source="manual"):
    return self.svc.add_revision(self.user, self.pid, md, "r", source=source)

  def _teachings(self):
    return {
        t["body_md"]: t
        for t in self.s.app.store.query(
            "SELECT * FROM teachings WHERE project_id=?", [self.pid]
        )
    }

  def _fake_output(self, run_kind="standard"):
    run_id = util.new_id("run")
    self.s.app.store.insert(
        "runs",
        {
            "id": run_id,
            "project_id": self.pid,
            "kind": run_kind,
            "status": "done",
            "config": {},
            "created_at": util.now_iso(),
        },
    )
    oid = util.new_id("o")
    self.s.app.store.insert(
        "outputs",
        {
            "id": oid,
            "run_id": run_id,
            "project_id": self.pid,
            "sample_id": "s_x",
            "viewport": "phone",
            "status": "done",
            "stage": "result",
            "created_at": util.now_iso(),
        },
    )
    return self.s.app.store.get("outputs", oid)

  def test_bind_exact_similar_new_and_retire(self):
    r1 = self._rev(
        "## Hierarchy\n\n- Lead with the decision the person must make.\n"
        "- Keep one primary action visible on the card.\n"
    )
    b1 = self.ledger.bindings(r1["id"])
    self.assertEqual({b["how"] for b in b1.values()}, {"new"})
    ts = self._teachings()
    self.assertEqual(len(ts), 2)
    self.assertTrue(all(t["status"] == "confirmed" for t in ts.values()))

    r2 = self._rev(
        "## Hierarchy\n\n- Lead with the decision the person must make.\n"
        "- Keep one primary action visible near the top of the card.\n",
        source="chat",
    )
    b2 = self.ledger.bindings(r2["id"])
    self.assertEqual(b2["R1"]["how"], "exact")
    self.assertTrue(b2["R2"]["how"].startswith("similar:"), b2["R2"])
    self.assertEqual(b2["R2"]["teaching_id"], b1["R2"]["teaching_id"])
    vs = self.ledger.versions(b2["R2"]["teaching_id"])
    self.assertEqual(len(vs), 2)
    self.assertIn("chat", vs[1]["reason"])

    r3 = self._rev(
        "## Hierarchy\n\n- Lead with the decision the person must make.\n"
    )
    del r3
    t2 = self.s.app.store.get("teachings", b1["R2"]["teaching_id"])
    self.assertEqual(t2["status"], "retired")

  def test_signals(self):
    self._rev("- Rule A stays.\n- Rule B gets rephrased a lot.\n")
    ts = self._teachings()
    a, b = ts["Rule A stays."], ts["Rule B gets rephrased a lot."]
    for _ in range(3):
      self.ledger.record_trial(
          a["id"],
          version=1,
          output=self._fake_output(),
          expected="yes",
          followed="yes",
          source="judge",
      )
    self.assertEqual(
        self.ledger.efficacy(self.s.app.store.get("teachings", a["id"]))[
            "signal"
        ],
        "reliable",
    )
    for _ in range(2):
      self.ledger.record_trial(
          b["id"],
          version=1,
          output=self._fake_output(),
          expected="yes",
          followed="no",
          source="judge",
      )
    tb = self.s.app.store.get("teachings", b["id"])
    self.assertEqual(self.ledger.efficacy(tb)["signal"], "not_followed")
    tb = self.ledger.add_version(
        tb,
        body_md="Rule B, now in other words.",
        reason="not followed",
        source="user",
    )
    for _ in range(2):
      self.ledger.record_trial(
          b["id"],
          version=2,
          output=self._fake_output(),
          expected="yes",
          followed="no",
          source="effect",
      )
    eff = self.ledger.efficacy(self.s.app.store.get("teachings", b["id"]))
    self.assertEqual(eff["signal"], "rephrased_no_gain")
    self.assertEqual(eff["phrasings"], 2)
    self.assertEqual([x["trials"] for x in eff["by_version"]], [2, 2])

    st, lst = self.s.call("GET", f"/api/projects/{self.pid}/teachings")
    self.assertEqual(st, 200, lst)
    self.assertEqual(lst["summary"]["rephrased_no_gain"], 1)
    self.assertEqual(
        lst["teachings"][0]["efficacy"]["signal"], "rephrased_no_gain"
    )
    self.assertIn("thresholds", lst)

  def test_effect_trials_and_judge_trials(self):
    rev = self._rev("- Lead with the decision.\n- Group the totals.\n")
    o = self._fake_output()
    self.s.app.store.update(
        "outputs",
        o["id"],
        {
            "revision_id": rev["id"],
            "judge": {
                "findings": [
                    {"rule_id": "R1", "rule": "Lead", "status": "followed"},
                    {
                        "rule_id": "",
                        "rule": "Group the totals",
                        "status": "violated",
                    },
                    {
                        "rule_id": "R9",
                        "rule": "nothing like it",
                        "status": "violated",
                    },
                    {
                        "rule_id": "R2",
                        "rule": "Group",
                        "status": "violated",
                        "applies": "no",
                    },
                ]
            },
        },
    )
    self.ledger.on_judged(self.s.app.store.get("outputs", o["id"]))
    b = self.ledger.bindings(rev["id"])
    t1 = self.ledger.trials(b["R1"]["teaching_id"])
    t2 = self.ledger.trials(b["R2"]["teaching_id"])
    self.assertEqual([x["followed"] for x in t1], ["yes"])
    self.assertEqual([x["followed"] for x in t2], ["no"])  # text fallback

    eff_out = self._fake_output()
    self.s.app.store.update("outputs", eff_out["id"], {"sample_id": "s_y"})
    effect = {
        "id": "eff_x",
        "revision_id": rev["id"],
        "treatment_run_id": eff_out["run_id"],
        "analysis": {
            "pairs": {
                "s_y|phone": {
                    "changes": [
                        {"change": "C0", "cause": "rule", "rule_ids": ["R2"]}
                    ],
                    "unapplied_rules": [
                        {"rule_id": "R1", "why": "ignored"},
                        {
                            "rule_id": "R2",
                            "applies": "no",
                            "why": "not this data",
                        },
                    ],
                }
            }
        },
    }
    self.ledger.on_effect_analyzed(effect)
    src = {
        (x["source"], x["followed"])
        for x in self.ledger.trials(b["R2"]["teaching_id"])
    }
    self.assertIn(("effect", "yes"), src)
    self.assertNotIn(("effect", "no"), src)
    src1 = {
        (x["source"], x["followed"])
        for x in self.ledger.trials(b["R1"]["teaching_id"])
    }
    self.assertIn(("effect", "no"), src1)

  def test_escalate_and_split(self):
    self._rev("- Use tabs for three parallel sections.\n")
    t = list(self._teachings().values())[0]
    self.ledger.record_trial(
        t["id"],
        version=1,
        output=self._fake_output(),
        expected="yes",
        followed="no",
        source="judge",
    )
    t = self.ledger.add_version(
        t, body_md="Tabs for 3+ parallel sections.", reason="x"
    )
    t = self.ledger.add_version(
        t, body_md="Totally different rule about images.", reason="y"
    )
    st, res = self.s.call(
        "POST", f"/api/teachings/{t['id']}/split", {"version": 3}
    )
    self.assertEqual(st, 200, res)
    self.assertEqual(res["teaching"]["version"], 2)
    self.assertEqual(
        res["new_teaching"]["body_md"], "Totally different rule about images."
    )
    st, res = self.s.call(
        "POST", f"/api/teachings/{t['id']}/escalate", {"note": "never"}
    )
    self.assertEqual(st, 200, res)
    self.assertEqual(res["teaching"]["status"], "escalated")
    self.assertEqual(res["guideline"]["fixable"], "no")
    self.assertEqual(res["guideline"]["status"], "escalated")
    self.assertIn(f"teaching:{t['id']}", res["guideline"]["tags"])
    st, det = self.s.call("GET", f"/api/teachings/{t['id']}")
    self.assertEqual(st, 200, det)
    self.assertEqual(len(det["versions"]), 2)
    self.assertEqual(len(det["guidelines"]), 1)


class TeachMigrationTest(unittest.TestCase):

  def test_old_teachings_table_gets_new_columns(self):
    import sqlite3  # pylint: disable=g-import-not-at-top
    import tempfile  # pylint: disable=g-import-not-at-top
    from core.storage import Store  # pylint: disable=g-import-not-at-top

    d = tempfile.mkdtemp(prefix="dcb_mig_")
    c = sqlite3.connect(os.path.join(d, "app.db"))
    c.execute(
        "CREATE TABLE teachings (id TEXT PRIMARY KEY, project_id TEXT, status"
        " TEXT)"
    )
    c.commit()
    c.close()
    st = Store(d)
    cols = {
        r["name"] for r in st.conn().execute("PRAGMA table_info(teachings)")
    }
    self.assertIn("extra_json", cols)
    st.insert("teachings", {"id": "t1", "project_id": "p", "extra": {"a": 1}})
    self.assertEqual(st.get("teachings", "t1")["extra"], {"a": 1})


if __name__ == "__main__":
  unittest.main()
