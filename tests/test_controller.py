"""Phase 1 acceptance checks, run against the fake backend (no network, no Lemonade)."""
import os
import tempfile
import threading
import unittest

from juicr.controller import Controller, PolicyError, Principal
from juicr.fake_backend import FakeBackend


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "j.db")
        self.clk = Clock()
        self.be = FakeBackend()
        self.c = Controller(self.path, self.be, self.clk)
        self.c.set_owner_token("secret-owner-token")
        self.owner = self.c.authenticate_owner("secret-owner-token")
        self.user = Principal("user", "tester")
        self.mcp = Principal("mcp", "hermes")

    def tearDown(self):
        self.dir.cleanup()

    def new_exp(self, eid="e1", start=900, end=1600, grace=600, budgets=None):
        self.c.create_experiment(eid, self.mcp)
        self.c.approve_window(self.owner, eid, start, end, grace, budgets or {"requests": 10, "tokens": 1000})


class TestApproval(Base):
    def test_ordinary_callers_cannot_approve(self):
        self.c.create_experiment("e1", self.mcp)
        for p in (self.user, self.mcp, Principal("owner", "forged")):
            with self.assertRaises(PolicyError):
                self.c.approve_window(p, "e1", 0, 1, 0, {"requests": 1})

    def test_forged_owner_principal_rejected_for_protected(self):
        self.new_exp()
        with self.assertRaises(PolicyError):
            self.c.grant_protected(Principal("owner", "x"), "e1", "publication")

    def test_bad_token_rejected_and_owner_not_replaceable(self):
        with self.assertRaises(PolicyError):
            self.c.authenticate_owner("wrong")
        with self.assertRaises(PolicyError):
            self.c.set_owner_token("other")

    def test_protected_actions_need_separate_owner_approval_per_experiment(self):
        self.new_exp("e1"); self.new_exp("e2")
        with self.assertRaises(PolicyError):
            self.c.request_protected(self.mcp, "e1", "external_judging")
        self.c.grant_protected(self.owner, "e1", "external_judging")
        self.assertTrue(self.c.request_protected(self.mcp, "e1", "external_judging"))
        with self.assertRaises(PolicyError):  # not inherited by another experiment
            self.c.request_protected(self.mcp, "e2", "external_judging")
        with self.assertRaises(PolicyError):  # run approval is not publication approval
            self.c.request_protected(self.mcp, "e1", "publication")

    def test_budget_increase_only_by_owner_and_only_upward(self):
        self.new_exp()
        with self.assertRaises(PolicyError):
            self.c.grant_protected(self.owner, "e1", "budget_increase", '{"requests": 5}')
        self.c.grant_protected(self.owner, "e1", "budget_increase", '{"requests": 20}')
        self.assertEqual(self.c.budget("e1")["requests"]["lim"], 20)


class TestBudget(Base):
    def test_no_dispatch_after_exhaustion_and_active_work_cancelled(self):
        self.new_exp(budgets={"requests": 3})
        for i in range(4):
            self.c.admit(self.mcp, "e1", f"t{i}", {"requests": 1})
        for i in range(3):
            self.c.dispatch(f"t{i}")
        with self.assertRaises(PolicyError):
            self.c.dispatch("t3")
        self.assertEqual(self.c.state("e1"), "cancelling")
        self.assertEqual(len(self.be.started), 3)          # nothing new dispatched
        self.assertEqual(len(self.be.cancelled), 3)        # active work cancelled
        self.assertEqual(self.c.trial_state("t3"), "cancelled")
        with self.assertRaises(PolicyError):
            self.c.admit(self.mcp, "e1", "t9", {"requests": 1})

    def test_unknown_usage_is_not_zero_and_overrun_recorded(self):
        self.new_exp(budgets={"requests": 5, "tokens": 100})
        self.c.admit(self.mcp, "e1", "t0", {"requests": 1, "tokens": 90})
        self.c.dispatch("t0")
        self.c.settle("t0", {"requests": 1, "tokens": 130})
        b = self.c.budget("e1")
        self.assertEqual(b["tokens"]["overrun"], 30)
        self.c.admit(self.mcp, "e1", "t2", {"requests": 1})
        self.c.dispatch("t2")
        self.c.settle("t2", {"requests": None})
        self.assertEqual(self.c.budget("e1")["requests"]["unknown"], 1)

    def test_unapproved_dimension_refused(self):
        self.new_exp(budgets={"requests": 5})
        self.c.admit(self.mcp, "e1", "t0", {"tool_calls": 1})
        with self.assertRaises(PolicyError):
            self.c.dispatch("t0")

    def test_concurrent_admission_never_oversubscribes(self):
        self.new_exp(budgets={"requests": 10})
        for i in range(40):
            self.c.admit(self.mcp, "e1", f"t{i}", {"requests": 1})
        ok, fail = [], []
        def go(i):
            try:
                self.c.dispatch(f"t{i}"); ok.append(i)
            except PolicyError:
                fail.append(i)
        ths = [threading.Thread(target=go, args=(i,)) for i in range(40)]
        [t.start() for t in ths]; [t.join() for t in ths]
        self.assertEqual(len(ok), 10)
        self.assertEqual(len(self.be.started), 10)
        b = self.c.budget("e1")["requests"]
        self.assertLessEqual(b["used"] + b["reserved"], 10)

    def test_consumption_persists_across_restart(self):
        self.new_exp(budgets={"requests": 3})
        for i in range(3):
            self.c.admit(self.mcp, "e1", f"t{i}", {"requests": 1})
        self.c.dispatch("t0"); self.c.settle("t0", {"requests": 1})
        self.c.dispatch("t1")                                  # reservation still open
        c2 = Controller(self.path, FakeBackend(), self.clk)    # "restart"
        b = c2.budget("e1")["requests"]
        self.assertEqual((b["used"], b["reserved"]), (1, 1))
        c2.admit(self.mcp, "e1", "t5", {"requests": 1})
        c2.dispatch("t2")                                      # third unit
        with self.assertRaises(PolicyError):
            c2.dispatch("t5")                                  # budget did not reset


class TestWindowAndGrace(Base):
    def test_grace_never_expands_budget(self):
        self.new_exp(budgets={"requests": 1}, end=1600, grace=None)
        self.c.admit(self.mcp, "e1", "a", {"requests": 1})
        self.c.admit(self.mcp, "e1", "b", {"requests": 1})
        self.clk.t = 1700                                      # after window, unlimited grace
        self.c.dispatch("a")
        with self.assertRaises(PolicyError):
            self.c.dispatch("b")

    def test_grace_does_not_admit_new_work_but_runs_queued(self):
        self.new_exp(end=1600, grace=600)
        self.c.admit(self.mcp, "e1", "a", {"requests": 1})
        self.clk.t = 1700
        with self.assertRaises(PolicyError):
            self.c.admit(self.mcp, "e1", "late", {"requests": 1})
        self.c.dispatch("a")                                   # queued inside window, within grace

    def test_finite_grace_expires(self):
        self.new_exp(end=1600, grace=600)
        self.c.admit(self.mcp, "e1", "a", {"requests": 1})
        self.clk.t = 2300
        with self.assertRaises(PolicyError):
            self.c.dispatch("a")

    def test_unlimited_grace_continues_queued(self):
        self.new_exp(end=1600, grace=None)
        self.c.admit(self.mcp, "e1", "a", {"requests": 1})
        self.clk.t = 10**6
        self.c.dispatch("a")

    def test_cancellation_overrides_grace(self):
        self.new_exp(end=1600, grace=None)
        for t in "ab":
            self.c.admit(self.mcp, "e1", t, {"requests": 1})
        self.c.dispatch("a")
        self.clk.t = 1700
        self.c.cancel(self.user, "e1")
        self.assertEqual(self.be.cancelled, ["h1"])
        with self.assertRaises(PolicyError):
            self.c.dispatch("b")
        self.assertEqual(self.c.trial_state("b"), "cancelled")

    def test_window_not_open_yet(self):
        self.new_exp(start=2000, end=3000)
        with self.assertRaises(PolicyError):
            self.c.admit(self.mcp, "e1", "a", {"requests": 1})


if __name__ == "__main__":
    unittest.main()
