"""Orchestrator: turns dispatched trials into evidence + evaluations, one active prediction at a time.

Modes: 'sim' (canned reference answers through the real oracles; no Lemonade) and 'real' (Lemonade via the
runner). The controller still decides WHAT may run (owner-approved window and budget); this only executes."""
import json
import queue
import threading
import time

from . import agent, cases, oracles, pilot, sandbox, simdata
from .evidence import EvidenceStore
from .review import EvalStore, classify
from .runner import Runner

TERMINAL = ("completed", "partial", "cancelled", "failed")


class QueueBackend:
    """Controller backend: dispatch enqueues; cancel flips a flag (the runner also polls the controller)."""

    def __init__(self):
        self.q = queue.Queue()
        self.cancelled = set()

    def start(self, trial_id):
        self.q.put(trial_id)
        return "q-" + trial_id

    def cancel(self, handle):
        self.cancelled.add(handle)


class Orchestrator:
    def __init__(self, ctl, backend, evidence, mode="sim", client=None, clock=time.time, sim_delay=0.05):
        assert mode in ("sim", "real")
        self.ctl, self.be, self.ev, self.mode, self.client = ctl, backend, evidence, mode, client
        self.es, self.clock, self.sim_delay = EvalStore(evidence), clock, sim_delay
        self.runner = Runner(client, evidence, ctl, clock=clock) if mode == "real" else None
        self.segments = {}
        self.stop = threading.Event()

    # -- loop ---------------------------------------------------------------------
    def step(self):
        """One scheduler pass; returns the trial ids processed. Used by the thread and by tests."""
        self.ctl.sweep_expired()
        self.ctl.tick(max_active=1)
        done = []
        while True:
            try:
                tid = self.be.q.get_nowait()
            except queue.Empty:
                break
            self.process(tid)
            done.append(tid)
        return done

    def run_forever(self, interval=1.0):
        while not self.stop.wait(interval):
            try:
                self.step()
            except Exception:  # never let the worker die; per-trial errors are recorded in process()
                pass

    # -- one trial ------------------------------------------------------------------
    def process(self, tid):
        meta = self.ctl.trial_meta(tid) or {}
        exp = self.ctl.trial_experiment(tid)
        spec = self.ctl.experiment_spec(exp) or {}
        case = meta.get("case")
        try:
            if self.ctl.is_cancelling_trial(tid):
                self.ctl.finish_trial(tid, "cancelled", {"requests": 0, "tokens": 0})
                return
            if self.mode == "sim":
                body = self._sim(tid, case, meta)
            elif case == "A1":
                body = self._real_agent(tid, spec, meta)
            else:
                body = self._real_single(tid, exp, spec, meta)
            self._evaluate(tid, case, body)
        except Exception as e:                      # infrastructure failure, never a task result
            self.ev.append_event(tid, "worker_error", {"error": f"{type(e).__name__}: {str(e)[:200]}"})
            try:
                self.ctl.finish_trial(tid, "failed", {"requests": None, "tokens": None})
            except Exception:
                pass
        finally:
            self._maybe_restore(exp)

    def _config(self, spec):
        load = spec.get("load", {"ctx_size": 8192})
        return {"model": spec["model"], "load_options": load, "expect": dict(load)}

    def _sim(self, tid, case, meta):
        time.sleep(self.sim_delay)
        if case == "A1":
            res = agent.run_agent(lambda m, t, it=iter(simdata.A1_REF_STEPS): next(it))
            text, trace, tokens, reqs = res["final_text"], res["trace"], res["tokens"], res["requests"]
        else:
            text, trace, tokens, reqs = simdata.SINGLE_REFS[case], None, 100, 1
        h = self.ev.put_artifact(text.encode(), "text/plain")
        th = self.ev.put_artifact(json.dumps(trace).encode(), "application/json") if trace is not None else None
        body = {"trial_id": tid, "outcome": "done", "classification": "task_response", "simulated": True,
                "attempts": [{"attempt": 1, "result": "ok", "request_sent": True}], "requests_sent": reqs, "tokens": tokens,
                "final": {"response": h, "trace": th, "backend_timings": None, "cached_tokens": None,
                          "client": {"first_token_s": None, "total_s": self.sim_delay}},
                "limits": "SIMULATED: canned reference answer; no Lemonade involved"}
        self.ev.finalize("trial", tid, body)
        self.ctl.finish_trial(tid, "done", {"requests": reqs, "tokens": tokens}, started=self.clock() - self.sim_delay)
        return body

    def _segment(self, exp, config):
        if exp not in self.segments:
            self.segments[exp] = {"prior": self.runner.snapshot_state(), "config": config, "trials": []}
        return self.segments[exp]

    def _real_single(self, tid, exp, spec, meta):
        config = self._config(spec)
        self._segment(exp, config)["trials"].append(tid)
        return self.runner.execute(tid, config, meta["spec"])

    def _real_agent(self, tid, spec, meta):
        exp = self.ctl.trial_experiment(tid)
        config = self._config(spec)
        self._segment(exp, config)["trials"].append(tid)
        base = dict(meta["spec"]["request"])
        started, n, reasons = self.clock(), [0], []

        def step(messages, tools):
            n[0] += 1
            self.runner.ensure_config(config)               # re-verify before EVERY request
            j = self.client.chat(dict(base, messages=messages, tools=tools, max_tokens=512))
            m = j["choices"][0]["message"]
            reasons.append(j["choices"][0].get("finish_reason"))
            return {"content": m.get("content") or "", "tokens": (j.get("usage") or {}).get("completion_tokens") or 0,
                    "tool_calls": [{"id": t.get("id"), "name": t["function"]["name"], "arguments": t["function"].get("arguments")}
                                   for t in (m.get("tool_calls") or [])]}
        res = agent.run_agent(step, limits=meta["spec"].get("limits"))
        h = self.ev.put_artifact(res["final_text"].encode(), "text/plain")
        th = self.ev.put_artifact(json.dumps(res["trace"]).encode(), "application/json")
        ok = res["outcome"] == "finished"
        body = {"trial_id": tid, "outcome": "done" if ok else "failed",
                "classification": "task_response" if ok else "task_limit:" + res["outcome"],
                "attempts": [{"attempt": 1, "result": res["outcome"], "request_sent": True}], "requests_sent": res["requests"],
                "tokens": res["tokens"], "final": {"response": h, "trace": th, "agent_outcome": res["outcome"], "finish_reasons": reasons,
                                                    "generation_truncated": "length" in reasons,
                                                    "client": {"total_s": res["elapsed_s"]}},
                "limits": "tool results simulated; usage as reported by backend"}
        self.ev.finalize("trial", tid, body)
        self.ctl.finish_trial(tid, "done" if ok else "failed",
                              {"requests": res["requests"], "tokens": res["tokens"], "tool_calls": res["tool_calls"]}, started=started)
        return body

    def _evaluate(self, tid, case, body):
        if body.get("outcome") != "done" or not body.get("final") or not body["final"].get("response"):
            return
        text = self.ev.get_artifact(body["final"]["response"]).decode()
        trace = None
        if body["final"].get("trace"):
            trace = json.loads(self.ev.get_artifact(body["final"]["trace"]))
        try:
            ev = oracles.evaluate(case, text, trace)
        except sandbox.SandboxUnavailable as e:
            ev = {"case": case, "evaluator": {"name": "unavailable", "version": oracles.EVAL_VERSION}, "checks": {},
                  "dimensions": {}, "critical_failures": [], "needs_human": [], "status": "evaluation_unavailable",
                  "detail": {"error": str(e)[:200]}}
        meta = self.ctl.trial_meta(tid) or {}
        cap = ((meta.get("spec") or {}).get("request") or {}).get("max_tokens")
        fin = body["final"]
        trunc = bool(fin.get("generation_truncated")) or (cap is not None and (body.get("tokens") or 0) >= cap)
        ev["detail"].update(completion_tokens=body.get("tokens"), visible_chars=len(text), max_tokens=cap,
                            generation_truncated=trunc, no_visible_output=not text.strip(),
                            truncation_basis="finish_reason" if fin.get("generation_truncated") else ("tokens>=cap (inferred)" if trunc else None))
        if trunc and ev.get("status") == "not_runnable" and "fenced" in str(ev["detail"].get("reason")):
            ev["detail"]["reason"] = "truncated at the token cap before the code block closed"
        self.es.add_auto(tid, ev)

    def rescore(self, exp_id):
        """Append a fresh automatic evaluation (current oracles) for every finished trial. Evidence and earlier
        evaluations are never edited; the effective view uses the latest."""
        n = 0
        for t in self.ctl.list_trials(exp_id):
            man = self.ev.manifest("trial", t["id"])
            if man and man["body"].get("outcome") == "done":
                self._evaluate(t["id"], (t["meta"] or {}).get("case"), man["body"]); n += 1
        return n

    def _maybe_restore(self, exp):
        if self.mode != "real" or exp not in self.segments:
            return
        if self.ctl.state(exp) not in TERMINAL:
            return
        seg = self.segments.pop(exp)
        rest = self.runner.restore(seg["prior"])
        try:
            self.ev.finalize("segment", "exp-" + exp, {"prior_state": seg["prior"], "config": seg["config"],
                                                        "trials": seg["trials"], "restoration": rest,
                                                        "warnings": list(self.runner.warnings)})
        except Exception:
            pass

    # -- pilot launcher / reads -----------------------------------------------------
    def pilot_admission(self, spec, exp_id):
        """Trials for the approved pilot pack as controller admit_many tuples. Trial ids are namespaced by
        experiment (`<experiment>__<trial>`) because trial ids and evidence files are global."""
        pack = pilot.build_pilot(spec["model"], ctx_size=spec.get("load", {}).get("ctx_size", 8192))
        return [(f"{exp_id}__{t['trial_id']}", t["max_cost"], {"case": t["case"], "pair_id": t["pair_id"], "temperature": t["temperature"],
                                                  "seed": t["seed"], "order_index": t["order_index"], "spec": t["spec"]})
                for t in pack["trials"]]

    def trial_rows(self, exp_id):
        rows = []
        for t in self.ctl.list_trials(exp_id):
            m = t["meta"]
            man = self.ev.manifest("trial", t["id"])
            body = man["body"] if man else None
            view = self.es.view(t["id"]) if man else None
            cls = classify(body, view) if body else None
            fin = (body or {}).get("final") or {}
            cl = fin.get("client") or {}
            bt = fin.get("backend_timings") or {}
            rows.append({"id": t["id"], "state": t["state"], "case": m.get("case"), "temperature": m.get("temperature"),
                         "seed": m.get("seed"), "order": m.get("order_index"), "class": cls,
                         "critical": (view or {}).get("critical_failures", []), "pending": (view or {}).get("pending_human", []),
                         "total_s": cl.get("total_s"), "first_token_s": cl.get("first_token_s"),
                         "tok_s": bt.get("predicted_per_second"), "cached": fin.get("cached_tokens"),
                         "simulated": bool((body or {}).get("simulated"))})
        return rows

    def summary(self, rows):
        """Per case x temperature: counts only, with n shown. No overall score (plan decision 13)."""
        out = {}
        for r in rows:
            if r["case"] is None:
                continue
            k = (r["case"], r["temperature"])
            d = out.setdefault(k, {"n": 0, "finished": 0, "success": 0, "critical": 0, "pending": 0, "infra": 0, "truncated": 0})
            d["n"] += 1
            if r["class"]:
                d["finished"] += 1
                d["success"] += r["class"]["task"] == "task_success"
                d["critical"] += r["class"]["task"] == "critical_failure"
                d["infra"] += r["class"]["execution"] in ("infrastructure_failure", "timeout")
                d["pending"] += r["class"]["judgment"] == "pending_human"
                d["truncated"] += bool(r["class"].get("note")) and ("truncated" in r["class"]["note"] or "token cap" in r["class"]["note"])
        return [dict(case=k[0], temperature=k[1], **v) for k, v in sorted(out.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0))]

    # -- blind review ------------------------------------------------------------------
    def pending_review(self, exp_id):
        out = []
        for t in self.ctl.list_trials(exp_id):
            man = self.ev.manifest("trial", t["id"])
            v = self.es.view(t["id"]) if man else None
            if v and v.get("pending_human"):
                out.append((t["id"], v["case"], v["pending_human"], man["body"]))
        return out

    def next_packet(self, exp_id, salt):
        p = self.pending_review(exp_id)
        if not p:
            return None
        tid, case, dims, body = p[0]
        text = self.ev.get_artifact(body["final"]["response"]).decode()
        pk = self.es.blind_packet(tid, case, text, salt)
        pk["rate"] = dims
        pk["remaining"] = len(p)
        return pk

    def submit_review(self, packet_id, reviewer, scores, notes=""):
        tid = self.es.unblind(packet_id)
        v = self.es.view(tid)
        need = v.get("pending_human") if v else None
        if not need:
            raise ValueError("nothing pending for this packet")
        if set(scores) != set(need):
            raise ValueError("rate exactly: " + ", ".join(need))
        self.es.add_human(tid, reviewer, scores, notes)
