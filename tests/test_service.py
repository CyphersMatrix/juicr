"""Phase 4 orchestrator checks: sim and real modes end to end, review, cancellation, infrastructure errors."""
import os
import tempfile
import time
import unittest

from juicr import cases, oracles, sandbox
from juicr.controller import Controller, Principal
from juicr.evidence import EvidenceStore
from juicr.lemonade import LemonadeClient
from juicr.service import Orchestrator, QueueBackend
from tests.fake_lemonade import KEY, FakeLemonade

BUDGETS = {"requests": 150, "tokens": 60000, "seconds": 3600, "tool_calls": 24}


class Base(unittest.TestCase):
    mode = "sim"

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.be = QueueBackend()
        self.ctl = Controller(os.path.join(self.d.name, "j.db"), self.be)
        self.ctl.set_owner_token("t"); self.owner = self.ctl.authenticate_owner("t"); self.mcp = Principal("mcp", "hermes")
        self.ev = EvidenceStore(os.path.join(self.d.name, "ev"))
        self.fl = None; client = None
        if self.mode == "real":
            self.fl = FakeLemonade(); client = LemonadeClient(self.fl.url, key=KEY, timeout=5)
        self.o = Orchestrator(self.ctl, self.be, self.ev, mode=self.mode, client=client, sim_delay=0)

    def tearDown(self):
        if self.fl: self.fl.close()
        self.d.cleanup()

    def pilot(self, eid="p1", budgets=None, load=True):
        spec = {"pack": cases.PACK_VERSION, "model": "Gemma-4-E4B-it-GGUF", "load": {"ctx_size": 8192}}
        self.ctl.create_experiment(eid, self.mcp, spec)
        n = time.time()
        self.ctl.approve_window(self.owner, eid, n - 1, n + 3600, 600, budgets or BUDGETS)
        if load:
            self.ctl.admit_many(self.mcp, eid, self.o.pilot_admission(spec, eid))

    def run_all(self, limit=80):
        for _ in range(limit):
            self.o.step()
            if self.ctl.state("p1") in ("completed", "partial", "cancelled", "failed"):
                return
        self.fail("did not finish")


class TestSimEndToEnd(Base):
    def test_full_pilot_flows_through_oracles(self):
        self.pilot(); self.run_all()
        self.assertEqual(self.ctl.state("p1"), "completed")
        rows = self.o.trial_rows("p1")
        self.assertEqual(len(rows), 30); self.assertTrue(all(r["state"] == "done" and r["simulated"] for r in rows))
        s = {(x["case"], x["temperature"]): x for x in self.o.summary(rows)}
        self.assertEqual(len(s), 10); self.assertTrue(all(x["n"] == 3 and x["finished"] == 3 for x in s.values()))
        for c in ("D1", "E1", "W1", "A1"):                 # references pass their oracles, 3 seeds each
            for t in (0.0, 0.7):
                self.assertEqual(s[(c, t)]["success"], 3, (c, t))
        for t in (0.0, 0.7):                               # C1 needs the sandbox; otherwise it is "not evaluated", never a failure
            self.assertEqual(s[("C1", t)]["success"], 3 if sandbox.available() else 0)
        self.assertEqual(sum(x["critical"] for x in s.values()), 0)
        self.assertEqual(sum(x["pending"] for x in s.values() if x["case"] in ("D1", "W1")), 12)   # human judgment pending, not zero
        b = self.ctl.budget("p1")
        self.assertGreater(b["requests"]["used"], 0); self.assertEqual(b["requests"]["reserved"], 0)

    def test_c1_without_sandbox_is_evaluation_unavailable_not_task_failure(self):
        real = sandbox.available; sandbox.available = lambda: False
        try:
            self.pilot(); self.run_all()
        finally:
            sandbox.available = real
        rows = [r for r in self.o.trial_rows("p1") if r["case"] == "C1"]
        self.assertTrue(rows and all(r["class"]["task"] == "not_evaluated" and r["class"]["judgment"] == "evaluation_unavailable" for r in rows))
        self.assertEqual(sum(x["critical"] + x["success"] for x in self.o.summary(rows) if x["case"] == "C1"), 0)

    def test_cancel_mid_run_stops_remaining_trials(self):
        self.pilot()
        self.o.step()                                      # one trial runs (max_active=1)
        self.ctl.cancel(self.owner, "p1"); self.o.step(); self.o.step()
        self.assertEqual(self.ctl.state("p1"), "cancelled")
        states = {t["state"] for t in self.ctl.list_trials("p1")}
        self.assertTrue(states <= {"done", "cancelled"}); self.assertIn("cancelled", states)

    def test_trial_cancelled_after_dispatch_never_executes(self):
        self.pilot()
        tid = self.ctl.tick(max_active=1)[0]                    # dispatched into the worker queue...
        self.ctl.cancel(self.owner, "p1")                       # ...then cancelled before the worker picks it up
        self.o.step()
        self.assertIsNone(self.ev.manifest("trial", tid))       # nothing was executed or recorded as a result
        self.assertEqual(self.ctl.trial_state(tid), "cancelled")
        self.assertEqual(self.ctl.state("p1"), "cancelled")

    def test_two_pilots_in_one_database_do_not_collide(self):
        self.pilot("p1"); self.pilot("p2")
        a = {t["id"] for t in self.ctl.list_trials("p1")}; b = {t["id"] for t in self.ctl.list_trials("p2")}
        self.assertEqual((len(a), len(b)), (30, 30)); self.assertFalse(a & b)
        self.assertTrue(all(i.startswith("p1__") for i in a))
        for _ in range(140):
            self.o.step()
        self.assertEqual((self.ctl.state("p1"), self.ctl.state("p2")), ("completed", "completed"))
        self.assertEqual(len(self.o.trial_rows("p1")), 30); self.assertEqual(len(self.o.trial_rows("p2")), 30)
        self.assertIsNotNone(self.ev.manifest("trial", sorted(a)[0])); self.assertIsNotNone(self.ev.manifest("trial", sorted(b)[0]))

    def test_nothing_runs_without_an_approved_window(self):
        spec = {"pack": cases.PACK_VERSION, "model": "M", "load": {"ctx_size": 8192}}
        self.ctl.create_experiment("p1", self.mcp, spec)
        with self.assertRaises(Exception):
            self.ctl.admit_many(self.mcp, "p1", self.o.pilot_admission(spec, "p1"))
        self.assertEqual(self.o.step(), [])

    def test_budget_exhaustion_stops_the_pilot(self):
        self.pilot(budgets={"requests": 3, "tokens": 60000, "seconds": 3600, "tool_calls": 24})
        self.run_all()
        rows = self.o.trial_rows("p1")
        self.assertLess(sum(1 for r in rows if r["state"] == "done"), 30)
        self.assertIn(self.ctl.state("p1"), ("partial", "cancelled"))


class TestReview(Base):
    def test_blind_review_flow(self):
        self.pilot(); self.run_all()
        pk = self.o.next_packet("p1", "salt")
        self.assertIsNotNone(pk)
        blob = str(pk)
        for leak in ("temperature", "seed", "0.7", "-t0", "s17", "s43", "s101"):
            self.assertNotIn(leak, blob.replace("rubric_version", ""))
        self.assertEqual(pk["packet_id"], self.o.next_packet("p1", "salt")["packet_id"])
        with self.assertRaises(ValueError): self.o.submit_review(pk["packet_id"], "owner", {"tone": 2})   # wrong dimensions
        dims = {d: 2 for d in pk["rate"]}
        before = pk["remaining"]
        self.o.submit_review(pk["packet_id"], "owner", dims, "ok")
        self.assertEqual(self.o.next_packet("p1", "salt")["remaining"], before - 1)
        with self.assertRaises(ValueError): self.o.submit_review(pk["packet_id"], "owner", dims)         # already done
        with self.assertRaises(Exception): self.o.submit_review("nope", "owner", dims)
        # all reviews -> nothing pending
        while True:
            nxt = self.o.next_packet("p1", "salt")
            if not nxt: break
            self.o.submit_review(nxt["packet_id"], "owner", {d: 1 for d in nxt["rate"]})
        s = self.o.summary(self.o.trial_rows("p1"))
        self.assertEqual(sum(x["pending"] for x in s), 0)


class TestRealMode(Base):
    mode = "real"

    def test_pilot_against_fake_lemonade_restores_state_and_keeps_key_out_of_evidence(self):
        before = sorted(m["model_name"] for m in self.fl.state.loaded)
        self.pilot(); self.run_all(limit=120)
        self.assertEqual(self.ctl.state("p1"), "completed")
        rows = self.o.trial_rows("p1")
        self.assertTrue(all(r["state"] == "done" and not r["simulated"] for r in rows))
        a1 = [r for r in rows if r["case"] == "A1"]
        self.assertEqual(len(a1), 6); self.assertTrue(all(r["class"]["task"] == "task_success" for r in a1), [r["class"] for r in a1])
        self.assertEqual(sorted(m["model_name"] for m in self.fl.state.loaded), before)      # state restored at the end
        self.assertIsNotNone(self.ev.manifest("segment", "exp-p1"))
        self.assertEqual(self.fl.state.unauth, 0)
        for root, _, files in os.walk(self.ev.root):
            for f in files:
                with open(os.path.join(root, f), "rb") as fh:
                    self.assertNotIn(KEY.encode(), fh.read())
        for body in self.fl.state.chat_requests:                                             # frozen sampling on every request
            self.assertEqual((body.get("top_p"), body.get("top_k")), (0.95, 64))

    def test_server_errors_are_infrastructure_not_task_failures(self):
        self.fl.state.chat_fail = 10**6
        self.pilot(); self.run_all(limit=120)
        rows = [r for r in self.o.trial_rows("p1") if r["case"] != "A1"]
        self.assertTrue(all(r["class"]["execution"] == "infrastructure_failure" and r["class"]["task"] == "not_evaluated" for r in rows))
        s = self.o.summary(self.o.trial_rows("p1"))
        self.assertEqual(sum(x["success"] + x["critical"] for x in s), 0)
        self.assertGreater(sum(x["infra"] for x in s), 0)

    def test_every_request_is_served_on_the_approved_context_even_if_the_model_drops(self):
        self.fl.state.drop_after_chat = True                   # model vanishes after each chat; Lemonade would auto-load defaults
        self.pilot(); self.run_all(limit=150)
        self.assertGreater(len(self.fl.state.served_ctx), 30)  # singles + multi-turn agent trials
        self.assertEqual(set(self.fl.state.served_ctx), {8192}, "a request was served on a non-approved (default) context")


if __name__ == "__main__":
    unittest.main()
