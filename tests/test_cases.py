"""Phase 3 acceptance: reference outputs pass, intentional wrong outputs fail, classes stay distinct."""
import os
import tempfile
import unittest

from juicr import agent, cases, oracles, pilot, sandbox
from juicr.evidence import EvidenceError, EvidenceStore
from juicr.review import EvalStore, classify
from tests import refs

HAVE_SBX = sandbox.available()


class TestSingleResponseOracles(unittest.TestCase):
    def test_d1_reference_and_wrong(self):
        r = oracles.evaluate_d1(refs.D1_REF)
        self.assertTrue(all(r["checks"].values()), r["checks"])
        self.assertEqual(r["critical_failures"], [])
        self.assertEqual(r["status"], "needs_review")
        self.assertIsNone(r["dimensions"]["citation_support"])        # unreviewed, not zero
        for name, text in refs.D1_WRONG.items():
            w = oracles.evaluate_d1(text)
            self.assertFalse(all(w["checks"].values()), name)
        self.assertIn("invented_owner", oracles.evaluate_d1(refs.D1_WRONG["invented_owner"])["critical_failures"])
        self.assertEqual(oracles.evaluate_d1(refs.D1_WRONG["stale_date"])["dimensions"]["launch_date"], 0)

    def test_w1_reference_and_wrong(self):
        r = oracles.evaluate_w1(refs.W1_REF)
        self.assertTrue(all(r["checks"].values()), r["checks"])
        self.assertEqual(r["critical_failures"], [])
        self.assertEqual((r["dimensions"]["factual_preservation"], r["dimensions"]["structure_length"]), (2, 2))
        self.assertEqual(r["needs_human"], ["audience_clarity", "tone"])
        self.assertIsNone(r["dimensions"]["tone"])
        self.assertIn("unsupported_guarantee", oracles.evaluate_w1(refs.W1_WRONG["guarantee"])["critical_failures"])
        self.assertIn("unsupported_rollout_date", oracles.evaluate_w1(refs.W1_WRONG["rollout_date"])["critical_failures"])
        self.assertFalse(oracles.evaluate_w1(refs.W1_WRONG["four_bullets"])["checks"]["three_bullets"])
        self.assertFalse(oracles.evaluate_w1(refs.W1_WRONG["too_short"])["checks"]["words_100_140"])
        self.assertFalse(oracles.evaluate_w1(refs.W1_WRONG["missing_budget"])["checks"]["budget_40000"])

    def test_w1_rollout_heuristic_handles_negation(self):
        for ok in ("No production rollout date has been set.", "The pilot begins May 2. Any production rollout will be decided later.",
                   "Cedar begins May 2 for 12 analysts."):
            self.assertFalse(oracles.w1_rollout_claim(ok), ok)
        for bad in ("Production rollout will happen in June 2027.", "We will roll out to everyone by Q3."):
            self.assertTrue(oracles.w1_rollout_claim(bad), bad)

    def test_w1_word_rule_is_frozen(self):
        self.assertEqual(oracles.w1_word_count("a b - c --- d"), 4)    # bullet markers/punctuation tokens excluded

    def test_e1_reference_and_wrong(self):
        r = oracles.evaluate_e1(refs.E1_REF)
        self.assertTrue(all(r["checks"].values()), r["checks"])
        self.assertEqual(r["critical_failures"], [])
        self.assertEqual(r["detail"]["field_accuracy"], "6/6")
        self.assertIn("invented_due_date", oracles.evaluate_e1(refs.E1_WRONG["invented_due_date"])["critical_failures"])
        self.assertFalse(oracles.evaluate_e1(refs.E1_WRONG["superseded_total"])["checks"]["field_total"])
        self.assertFalse(oracles.evaluate_e1(refs.E1_WRONG["string_total"])["checks"]["schema_valid"])
        self.assertFalse(oracles.evaluate_e1(refs.E1_WRONG["extra_field"])["checks"]["schema_valid"])
        self.assertFalse(oracles.evaluate_e1(refs.E1_WRONG["bad_date_format"])["checks"]["schema_valid"])
        u = oracles.evaluate_e1(refs.E1_WRONG["unparseable"])
        self.assertFalse(u["checks"]["json_parses"]); self.assertTrue(u["detail"]["raw_preserved"])
        self.assertTrue(oracles.evaluate_e1("```json\n" + refs.E1_REF + "\n```")["checks"]["schema_valid"])

    def test_e1_parse_and_schema_reported_separately(self):
        r = oracles.evaluate_e1(refs.E1_WRONG["string_total"])
        self.assertTrue(r["checks"]["json_parses"]); self.assertFalse(r["checks"]["schema_valid"])
        self.assertEqual(r["dimensions"]["parse_schema"], 1)


@unittest.skipUnless(HAVE_SBX, "podman sandbox not available here (runs in CT 641)")
class TestC1InSandbox(unittest.TestCase):
    def test_reference_passes_all_checks(self):
        r = oracles.evaluate_c1(refs.C1_REF)
        self.assertEqual(r["detail"]["passed"], r["detail"]["total"], [k for k, v in r["checks"].items() if not v])
        self.assertTrue(r["detail"]["full_success"]); self.assertEqual(r["status"], "scored")

    def test_each_wrong_solution_fails(self):
        for name, text in refs.C1_WRONG.items():
            r = oracles.evaluate_c1(text, timeout_s=5)
            self.assertFalse(r["detail"].get("full_success"), name)
            self.assertLess(r["detail"]["passed"], r["detail"]["total"], name)

    def test_specific_failures_are_detected_by_the_right_checks(self):
        self.assertFalse(oracles.evaluate_c1(refs.C1_WRONG["merges_adjacent"])["checks"]["adjacent_not_merged"])
        self.assertFalse(oracles.evaluate_c1(refs.C1_WRONG["mutates_input"])["checks"]["input_not_mutated"])
        self.assertFalse(oracles.evaluate_c1(refs.C1_WRONG["accepts_bool"])["checks"]["bool_rejected"])
        self.assertFalse(oracles.evaluate_c1(refs.C1_WRONG["no_end_validation"])["checks"]["start_gt_end"])

    def test_infinite_loop_is_not_runnable_timeout(self):
        r = oracles.evaluate_c1(refs.C1_WRONG["infinite_loop"], timeout_s=3)
        self.assertEqual(r["status"], "not_runnable"); self.assertEqual(r["detail"]["reason"], "timeout")

    def test_model_code_cannot_write_the_workdir_or_reach_network(self):
        r = sandbox.run_python({"t.py": "import socket,sys\n"
                                "try:\n open('/work/x','w').write('x'); print('WROTE')\nexcept Exception as e: print('ro-ok')\n"
                                "try:\n socket.create_connection(('10.138.6.1',13305),2); print('NET')\nexcept Exception as e: print('net-blocked')\n"}, "t.py")
        self.assertIn("ro-ok", r["stdout"]); self.assertIn("net-blocked", r["stdout"])
        self.assertNotIn("WROTE", r["stdout"]); self.assertNotIn("NET", r["stdout"])

    def test_podman_failure_is_infrastructure_not_task_failure(self):
        real = sandbox._env
        def broken():
            e = real(); e["XDG_RUNTIME_DIR"] = "/nonexistent-runtime-dir"; return e
        sandbox._env = broken
        try:
            with self.assertRaises(sandbox.SandboxUnavailable):
                sandbox.run_python({"t.py": "print(1)"}, "t.py")
            with self.assertRaises(sandbox.SandboxUnavailable):      # the oracle must not score it as a task failure
                oracles.evaluate_c1(refs.C1_REF)
        finally:
            sandbox._env = real

    def test_output_cap_and_memory_ceiling(self):
        r = sandbox.run_python({"t.py": "print('x'*10_000_000)"}, "t.py", max_output=1000)
        self.assertTrue(r["truncated"]); self.assertLessEqual(len(r["stdout"]), 1000)
        r = sandbox.run_python({"t.py": "a=bytearray(400*1024*1024); print('survived')"}, "t.py", memory="64m")
        self.assertNotIn("survived", r["stdout"])


class TestAgentCase(unittest.TestCase):
    def run_steps(self, steps, **kw):
        out = agent.run_agent(refs.scripted(steps), **kw)
        return out, oracles.evaluate_a1(out["trace"], out["final_text"])

    def test_reference_passes_and_errors_consume_budget(self):
        out, r = self.run_steps(refs.A1_REF_STEPS)
        self.assertEqual(out["outcome"], "finished")
        self.assertEqual((out["requests"], out["tool_calls"]), (4, 4))     # failed search counted
        self.assertTrue(all(r["checks"].values()), r["checks"]); self.assertEqual(r["critical_failures"], [])
        self.assertTrue(out["trace"][1]["result_error"])                    # first search failed transiently

    def test_no_recovery_is_scored_separately(self):
        steps = [refs.A1_REF_STEPS[0],
                 {"content": "Cedar launches May 2 with $40,000.", "tool_calls": [], "tokens": 10}]
        out, r = self.run_steps(steps)
        self.assertFalse(r["checks"]["recovered_after_transient_error"])
        self.assertIn("fabricated_tool_result", r["critical_failures"])     # facts stated without reading B/A

    def test_claims_of_send_or_save_are_critical(self):
        steps = list(refs.A1_REF_STEPS[:3]) + [{"content": "Cedar launches May 2, $40,000. I have sent the note to the team.", "tool_calls": [], "tokens": 10}]
        _, r = self.run_steps(steps)
        self.assertIn("claimed_or_attempted_send_save", r["critical_failures"]); self.assertEqual(r["dimensions"]["no_write_compliance"], 0)

    def test_write_tool_is_refused_and_flagged(self):
        steps = [{"content": "", "tool_calls": [refs.call("send_email", {"to": "x"})], "tokens": 5},
                 {"content": "done", "tool_calls": [], "tokens": 5}]
        out, r = self.run_steps(steps)
        self.assertTrue(out["trace"][1]["result_error"]); self.assertFalse(r["checks"]["no_write_tool_attempts"])

    def test_invalid_arguments_detected(self):
        steps = [{"content": "", "tool_calls": [refs.call("read_document", {"id": "Z"})], "tokens": 5},
                 {"content": "x", "tool_calls": [], "tokens": 5}]
        _, r = self.run_steps(steps)
        self.assertFalse(r["checks"]["valid_tool_arguments"])

    def test_caps_enforced(self):
        many = [{"content": "", "tool_calls": [refs.call("search_documents", {"query": "q"})], "tokens": 5}] * 10
        out, _ = self.run_steps(many)
        self.assertEqual(out["outcome"], "limit_requests"); self.assertEqual(out["requests"], 4)
        five_calls = [{"content": "", "tool_calls": [refs.call("search_documents", {"query": "q"})] * 5, "tokens": 5}]
        out, _ = self.run_steps(five_calls)
        self.assertEqual((out["outcome"], out["tool_calls"]), ("limit_tool_calls", 4))
        out, _ = self.run_steps([{"content": "", "tool_calls": [refs.call("search_documents", {"query": "q"})], "tokens": 5000}])
        self.assertEqual(out["outcome"], "limit_tokens")
        t = iter([0, 0, 1000, 1000, 1000]); out = agent.run_agent(refs.scripted(refs.A1_REF_STEPS), clock=lambda: next(t))
        self.assertEqual(out["outcome"], "limit_time")

    def test_a1_admission_is_blocked_until_compat_verified(self):
        self.assertIn("blocked", cases.CASES["A1"]["admission"])
        p = pilot.build_pilot("M")
        self.assertTrue(all("blocked" in t["spec"]["admission"] for t in p["trials"] if t["case"] == "A1"))


class TestPilotPack(unittest.TestCase):
    def test_arithmetic_matches_plan(self):
        a = pilot.accounting(pilot.build_pilot("Gemma-4-E4B-it-GGUF"))
        self.assertEqual((a["top_level"], a["single_requests"], a["agent_requests_max"], a["measured_requests_max"]), (30, 24, 24, 48))

    def test_pairs_are_matched_randomized_and_reproducible(self):
        p = pilot.build_pilot("M"); q = pilot.build_pilot("M"); r = pilot.build_pilot("M", order_seed=1)
        self.assertEqual([t["trial_id"] for t in p["trials"]], [t["trial_id"] for t in q["trials"]])
        self.assertNotEqual([t["trial_id"] for t in p["trials"]], [t["trial_id"] for t in r["trials"]])
        self.assertEqual(p["order_rng_seed"], pilot.ORDER_RNG_SEED)
        by = {}
        for t in p["trials"]:
            by.setdefault(t["pair_id"], []).append(t)
        self.assertEqual(len(by), 15)
        for pid, ts in by.items():
            self.assertEqual(sorted(x["temperature"] for x in ts), [0.0, 0.7]); self.assertEqual(len({x["seed"] for x in ts}), 1)
        self.assertEqual(len({t["trial_id"] for t in p["trials"]}), 30)
        orders = {tuple(x["order"]) for x in p["pairs"]}
        self.assertEqual(orders, {(0.0, 0.7), (0.7, 0.0)})                  # both orders occur

    def test_requests_use_frozen_sampling_only_and_limits(self):
        for t in pilot.build_pilot("M")["trials"]:
            b = t["spec"]["request"]
            self.assertEqual((b["top_p"], b["top_k"]), (0.95, 64)); self.assertEqual(b["seed"], t["seed"])
            if t["spec"]["kind"] == "single":
                self.assertEqual(b["max_tokens"], 1024); self.assertEqual(t["spec"]["deadline_s"], 180)

    def test_pack_is_marked_proposed_until_owner_review(self):
        self.assertEqual(cases.PACK_STATUS, "proposed-pending-owner-review")


class TestReviewAndClassification(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); self.ev = EvidenceStore(os.path.join(self.d.name, "ev")); self.es = EvalStore(self.ev)

    def tearDown(self):
        self.d.cleanup()

    def test_unreviewed_is_not_zero_then_reviews_fill_in(self):
        self.es.add_auto("t1", oracles.evaluate_w1(refs.W1_REF))
        v = self.es.view("t1")
        self.assertIsNone(v["dimensions"]["tone"]); self.assertEqual(v["pending_human"], ["audience_clarity", "tone"])
        self.es.add_human("t1", "chris", {"audience_clarity": 2, "tone": 1}, "calm but dry")
        self.es.add_human("t1", "sam", {"tone": 2})
        v = self.es.view("t1")
        self.assertEqual(v["dimensions"]["tone"], 1.5); self.assertEqual(v["pending_human"], [])
        self.assertEqual(v["reviewers"]["tone"], ["chris", "sam"])

    def test_rescoring_appends_and_never_edits(self):
        self.es.add_auto("t1", oracles.evaluate_e1(refs.E1_REF))
        before = self.es.records("t1")[0]
        self.es.add_auto("t1", oracles.evaluate_e1(refs.E1_WRONG["superseded_total"]))
        recs = self.es.records("t1")
        self.assertEqual(len(recs), 2); self.assertEqual(recs[0], before)

    def test_review_validation(self):
        with self.assertRaises(EvidenceError): self.es.add_human("t1", "", {"tone": 1})
        with self.assertRaises(EvidenceError): self.es.add_human("t1", "c", {"tone": 3})
        with self.assertRaises(EvidenceError): self.es.add_human("../x", "c", {"tone": 1})

    def test_blind_packet_hides_condition_and_unblinds(self):
        pk = self.es.blind_packet("W1-s17-t07", "W1", "text", salt="s")
        self.assertNotIn("temperature", str(pk)); self.assertNotIn("seed", str(pk)); self.assertNotIn("t07", str(pk))
        self.assertEqual(pk["rate"], ["audience_clarity", "tone"])
        self.assertEqual(self.es.unblind(pk["packet_id"]), "W1-s17-t07")
        self.assertEqual(self.es.blind_packet("W1-s17-t07", "W1", "text", salt="s")["packet_id"], pk["packet_id"])

    def test_failure_classes_stay_distinct(self):
        ok = {"outcome": "done", "classification": "task_response"}
        good = oracles.evaluate_e1(refs.E1_REF); bad = oracles.evaluate_e1(refs.E1_WRONG["invented_due_date"])
        self.es.add_auto("a", good); self.es.add_auto("b", bad); self.es.add_auto("c", oracles.evaluate_w1(refs.W1_REF))
        self.assertEqual(classify(ok, self.es.view("a")), {"execution": "ok", "task": "task_success", "judgment": "complete"})
        self.assertEqual(classify(ok, self.es.view("b"))["task"], "critical_failure")
        self.assertEqual(classify(ok, self.es.view("c"))["judgment"], "pending_human")
        self.assertEqual(classify({"outcome": "failed", "classification": "infrastructure"}, None)["execution"], "infrastructure_failure")
        self.assertEqual(classify({"outcome": "timeout", "classification": "timeout"}, None)["execution"], "timeout")
        self.assertEqual(classify({"outcome": "cancelled", "classification": "cancelled"}, None)["execution"], "cancelled")
        self.assertEqual(classify({"outcome": "done", "classification": "task_response"}, oracles.evaluate_c1("no code"))["task"], "task_failure")


if __name__ == "__main__":
    unittest.main()
