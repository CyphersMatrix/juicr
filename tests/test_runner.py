"""Phase 2 acceptance checks against a fake Lemonade (no real inference, no network beyond loopback)."""
import os
import stat
import tempfile
import unittest

from juicr.controller import Controller, Principal
from juicr.evidence import EvidenceError, EvidenceStore
from juicr.fake_backend import FakeBackend
from juicr.lemonade import LemonadeClient, LemonadeError
from juicr.runner import ConfigNotEstablished, Runner, verify_settings
from tests.fake_lemonade import DEFAULT_CTX, KEY, FakeLemonade

MODEL = "Gemma-4-E4B-it-GGUF"
CFG = {"model": MODEL, "load_options": {"ctx_size": 8192}, "expect": {"ctx_size": 8192}}
SPEC = {"request": {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "temperature": 0.0, "seed": 17},
        "deadline_s": 5, "max_attempts": 1}


class Base(unittest.TestCase):
    def setUp(self):
        self.fl = FakeLemonade(); self.st = self.fl.state
        self.d = tempfile.TemporaryDirectory()
        self.ev = EvidenceStore(os.path.join(self.d.name, "ev"))
        self.cl = LemonadeClient(self.fl.url, key=KEY, timeout=5)
        self.ctl = Controller(os.path.join(self.d.name, "j.db"), FakeBackend())
        self.ctl.set_owner_token("t"); self.owner = self.ctl.authenticate_owner("t")
        self.mcp = Principal("mcp", "hermes")
        self.r = Runner(self.cl, self.ev, self.ctl, sleep=lambda s: None)

    def tearDown(self):
        self.fl.close(); self.d.cleanup()

    def exp(self, budgets=None, eid="e1"):
        self.ctl.create_experiment(eid, self.mcp)
        import time; n = time.time()
        self.ctl.approve_window(self.owner, eid, n - 1, n + 600, 600, budgets or {"requests": 10, "tokens": 1000})

    def dispatch(self, tid, cost=None, eid="e1"):
        self.ctl.admit(self.mcp, eid, tid, cost or {"requests": 2, "tokens": 100}); self.ctl.dispatch(tid)

    def models(self):
        return sorted(m["model_name"] for m in self.st.loaded)


class TestConfigEvidence(Base):
    def test_verified_and_accepted_but_unverified_are_distinct(self):
        ev = self.r.establish(CFG)
        self.assertEqual(ev["settings"]["ctx_size"]["status"], "verified")
        self.st.ignore_options = True
        self.cl.unload(MODEL)
        with self.assertRaises(ConfigNotEstablished):          # reflects 65536, requested 8192 -> mismatch
            self.r.establish(CFG)
        e = {"recipe_options": {}, "launch_command": ["llama-server"]}
        self.assertEqual(verify_settings(e, {"ctx_size": 8192})["ctx_size"]["status"], "unverified")
        self.assertEqual(verify_settings({"recipe_options": {"ctx_size": 4}}, {"ctx_size": 8192})["ctx_size"]["status"], "mismatch")

    def test_load_is_non_persistent(self):
        self.r.establish(CFG)
        # the fake records nothing persistent; the client must always send save_options False
        sent = {}
        orig = self.cl._json
        def spy(m, p, b=None, timeout=None):
            if p == "/api/v1/load": sent.update(b)
            return orig(m, p, b, timeout)
        self.cl._json = spy
        self.cl.unload(MODEL); self.r.establish(CFG)
        self.assertIs(sent["save_options"], False)


class TestGuardAndAttempts(Base):
    def test_autoload_guard_reestablishes_before_sending(self):
        self.exp(); self.dispatch("t1")
        self.cl.unload(MODEL)                                   # model not loaded when trial starts
        out = self.r.execute("t1", CFG, SPEC)
        self.assertEqual(out["outcome"], "done")
        ctx = [m for m in self.st.loaded if m["model_name"] == MODEL][0]["recipe_options"]["ctx_size"]
        self.assertEqual(ctx, 8192)                             # never served on the 65536 default
        self.assertNotEqual(ctx, DEFAULT_CTX)

    def test_wrong_config_never_sends_request(self):
        self.exp(); self.dispatch("t1")
        self.st.ignore_options = True                           # load can't be made to match
        out = self.r.execute("t1", CFG, SPEC)
        self.assertEqual(self.st.chat_requests, [])             # no request sent on an unestablished config
        self.assertEqual(out["outcome"], "failed")
        self.assertEqual(out["classification"], "infrastructure")
        self.assertFalse(out["attempts"][0]["request_sent"])

    def test_failed_attempts_survive_retries_and_are_accounted(self):
        self.exp(); self.dispatch("t1", {"requests": 3, "tokens": 100})
        self.st.chat_fail = 2
        spec = dict(SPEC, max_attempts=3)
        out = self.r.execute("t1", CFG, spec)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual([a["result"] for a in out["attempts"]], ["error", "error", "ok"])
        self.assertEqual(out["requests_sent"], 3)
        kinds = [e["kind"] for e in self.ev.events("t1")]
        self.assertEqual(kinds.count("attempt_start"), 3); self.assertEqual(kinds.count("attempt_end"), 3)
        self.assertEqual(self.ctl.budget("e1")["requests"]["used"], 3)

    def test_timing_cache_and_usage_recorded_unknown_not_zero(self):
        self.exp(); self.dispatch("t1"); self.st.cached = 19
        out = self.r.execute("t1", CFG, SPEC)
        f = out["final"]
        self.assertEqual(f["cached_tokens"], 19)
        self.assertEqual(f["backend_timings"]["predicted_per_second"], 23.5)
        self.assertIsNotNone(f["client"]["first_token_s"])
        self.assertEqual(out["tokens"], 3)
        self.exp(eid="e2"); self.dispatch("t2", eid="e2"); self.st.no_usage = True
        out2 = self.r.execute("t2", CFG, SPEC)
        self.assertIsNone(out2["tokens"])                       # unknown, not 0
        self.assertEqual(self.ctl.budget("e2")["tokens"]["unknown"], 1)

    def test_timeout_classified_and_stream_closed(self):
        self.exp(); self.dispatch("t1"); self.st.stall = True
        out = self.r.execute("t1", CFG, dict(SPEC, deadline_s=1))
        self.assertEqual((out["outcome"], out["classification"]), ("timeout", "timeout"))

    def test_cancel_marks_server_cancellation_unverified(self):
        self.exp(); self.dispatch("t1")
        self.ctl.cancel(self.mcp, "e1")
        out = self.r.execute("t1", CFG, SPEC)
        self.assertEqual(out["outcome"], "cancelled")
        self.assertIn("unverified", out["final"]["server_cancellation"])


class TestRestorationAndResume(Base):
    def test_restore_success_returns_to_prior_state(self):
        prior = self.r.snapshot_state()
        self.r.establish(CFG)
        self.assertEqual(self.models(), sorted([MODEL, "hermes-model-FLM"]))
        res = self.r.restore(prior)
        self.assertTrue(res["ok"]); self.assertEqual(self.models(), ["hermes-model-FLM"])

    def test_restoration_failure_warns_continues_and_next_run_reverifies(self):
        self.exp(); self.dispatch("t1"); self.dispatch("t2")
        self.st.fail_unload = 99                                 # restoration can never unload
        rest = self.r.run_segment("s1", CFG, [("t1", SPEC)])
        self.assertFalse(rest["ok"]); self.assertEqual(rest["attempts"], 3)  # 1 + 2 retries
        self.assertIn(MODEL, rest["unrestored"]["extra"])
        self.assertTrue(self.r.warnings)
        seg = self.ev.manifest("segment", "s1")["body"]
        self.assertFalse(seg["restoration"]["ok"])               # discrepancy preserved as evidence
        # next experiment continues; model left loaded at 8192 but an UNRELATED config is required
        other = {"model": MODEL, "load_options": {"ctx_size": 4096}, "expect": {"ctx_size": 4096}}
        self.st.fail_unload = 0
        out = self.r.execute("t2", other, SPEC)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(out["final"]["config"]["settings"]["ctx_size"]["effective"], 4096)

    def test_resume_never_resends_completed_trials_or_resets_budget(self):
        self.exp(); self.dispatch("t1")
        self.r.execute("t1", CFG, SPEC)
        n = len(self.st.chat_requests); used = self.ctl.budget("e1")["requests"]["used"]
        again = self.r.execute("t1", CFG, SPEC)                  # same trial after a "resume"
        self.assertEqual(again["outcome"], "done")
        self.assertEqual(len(self.st.chat_requests), n)          # nothing re-sent
        self.assertEqual(self.ctl.budget("e1")["requests"]["used"], used)
        r2 = Runner(self.cl, self.ev, Controller(os.path.join(self.d.name, "j.db"), FakeBackend()), sleep=lambda s: None)
        r2.execute("t1", CFG, SPEC)                              # fresh process, same evidence
        self.assertEqual(len(self.st.chat_requests), n)


class TestEvidenceIntegrity(Base):
    def test_manifest_immutable_and_hash_verified(self):
        self.ev.finalize("trial", "x", {"a": 1})
        with self.assertRaises(EvidenceError):
            self.ev.finalize("trial", "x", {"a": 2})
        p = os.path.join(self.ev.root, "manifests", "trial-x.json")
        mode = stat.S_IMODE(os.stat(p).st_mode)
        if os.name == "nt":  # Windows only models the read-only attribute
            self.assertFalse(mode & 0o200)
        else:
            self.assertEqual(mode, 0o400)
        with open(p) as fh: txt = fh.read()
        os.chmod(p, 0o600)
        with open(p, "w") as fh: fh.write(txt.replace('"a": 1', '"a": 9'))
        os.chmod(p, 0o400)
        with self.assertRaises(EvidenceError):
            self.ev.manifest("trial", "x")
        os.chmod(p, 0o600)
        with open(p, "w") as fh: fh.write("{not json")
        os.chmod(p, 0o400)
        with self.assertRaises(EvidenceError):                   # corrupt file is an evidence error too
            self.ev.manifest("trial", "x")

    def test_artifacts_content_addressed_and_tamper_detected(self):
        h1 = self.ev.put_artifact(b"hello"); h2 = self.ev.put_artifact(b"hello")
        self.assertEqual(h1, h2)
        self.assertEqual(self.ev.get_artifact(h1), b"hello")
        p = os.path.join(self.ev.root, "artifacts", h1); os.chmod(p, 0o600)
        with open(p, "wb") as fh: fh.write(b"hellx")
        with self.assertRaises(EvidenceError):
            self.ev.get_artifact(h1)

    def test_events_append_only_and_ids_validated(self):
        self.ev.append_event("t", "a"); self.ev.append_event("t", "b")
        self.assertEqual([e["kind"] for e in self.ev.events("t")], ["a", "b"])
        for bad in ("../x", "a/b", "", "x.y"):
            with self.assertRaises(EvidenceError):
                self.ev.append_event(bad, "k")

    def test_key_never_appears_in_evidence_or_errors(self):
        self.exp(); self.dispatch("t1"); self.st.chat_fail = 5
        self.r.execute("t1", CFG, dict(SPEC, max_attempts=2))
        for root, _, files in os.walk(self.ev.root):
            for f in files:
                with open(os.path.join(root, f), "rb") as fh:
                    self.assertNotIn(KEY.encode(), fh.read(), f)
        bad = LemonadeClient(self.fl.url, key="wrong-key", timeout=5)
        try:
            bad.health()
        except LemonadeError as e:
            self.assertNotIn("wrong-key", str(e))
        else:
            self.fail("expected auth failure")


if __name__ == "__main__":
    unittest.main()
