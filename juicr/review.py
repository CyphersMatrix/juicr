"""Evaluations and blind human review. Rescoring appends new evaluation records; nothing is edited.
`unreviewed` is never zero: a dimension that needs a human stays None until a review supplies it."""
import hashlib
import json
import os
import time

from .evidence import EvidenceError, EvidenceStore

RUBRIC_VERSION = "pilot-rubric-1"
HUMAN_DIMENSIONS = {"D1": ["citation_support"], "W1": ["audience_clarity", "tone"]}


class EvalStore:
    def __init__(self, evidence: EvidenceStore, clock=time.time):
        self.ev, self.clock = evidence, clock
        os.makedirs(os.path.join(evidence.root, "evals"), mode=0o700, exist_ok=True)
        os.makedirs(os.path.join(evidence.root, "blind"), mode=0o700, exist_ok=True)

    def _p(self, trial_id):
        self.ev._events_path(trial_id)  # reuses id validation
        return os.path.join(self.ev.root, "evals", trial_id + ".jsonl")

    def _append(self, trial_id, rec):
        fd = os.open(self._p(trial_id), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(rec, sort_keys=True) + "\n").encode()); os.fsync(fd)
        finally:
            os.close(fd)

    def records(self, trial_id):
        p = self._p(trial_id)
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(l) for l in f if l.strip()]

    def add_auto(self, trial_id, evaluation):
        self._append(trial_id, {"kind": "auto", "ts": self.clock(), "evaluation": evaluation})

    def add_human(self, trial_id, reviewer, scores, annotations="", rubric_version=RUBRIC_VERSION):
        if not reviewer:
            raise EvidenceError("reviewer required")
        for k, v in scores.items():
            if v not in (0, 1, 2):
                raise EvidenceError(f"score for {k} must be 0, 1 or 2")
        self._append(trial_id, {"kind": "human", "ts": self.clock(), "reviewer": reviewer,
                                "rubric_version": rubric_version, "scores": scores, "annotations": annotations})

    def view(self, trial_id):
        """Effective view: latest auto evaluation + human scores (latest per reviewer and dimension).
        Dimensions that still need a human remain None and are listed as pending."""
        recs = self.records(trial_id)
        autos = [r for r in recs if r["kind"] == "auto"]
        if not autos:
            return None
        ev = json.loads(json.dumps(autos[-1]["evaluation"]))
        humans = [r for r in recs if r["kind"] == "human"]
        by_dim = {}
        for h in humans:
            for k, v in h["scores"].items():
                by_dim.setdefault(k, {})[h["reviewer"]] = v
        for k, per in by_dim.items():
            ev["dimensions"][k] = round(sum(per.values()) / len(per), 2)
            ev.setdefault("reviewers", {})[k] = sorted(per)
        ev["pending_human"] = [d for d in ev.get("needs_human", []) if ev["dimensions"].get(d) is None]
        ev["reviews"] = len(humans)
        return ev

    # -- blind packets -------------------------------------------------------------
    def blind_packet(self, trial_id, case, response_text, salt):
        """Reviewer sees case id, prompt label and response only: no temperature, seed or condition."""
        pid = hashlib.sha256(f"{salt}:{trial_id}".encode()).hexdigest()[:12]
        mp = os.path.join(self.ev.root, "blind", pid + ".map")
        if not os.path.exists(mp):
            fd = os.open(mp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
            try:
                os.write(fd, json.dumps({"packet": pid, "trial_id": trial_id}).encode())
            finally:
                os.close(fd)
        return {"packet_id": pid, "case": case, "response": response_text,
                "rubric_version": RUBRIC_VERSION, "rate": HUMAN_DIMENSIONS.get(case, [])}

    def unblind(self, packet_id):
        p = os.path.join(self.ev.root, "blind", packet_id + ".map")
        with open(p) as f:
            return json.load(f)["trial_id"]


def classify(trial_manifest, view):
    """Keep failure classes distinct (plan sections 9-12). Returns {execution, task, judgment}."""
    cls = trial_manifest.get("classification")
    outcome = trial_manifest.get("outcome")
    execution = {"task_response": "ok", "infrastructure": "infrastructure_failure", "timeout": "timeout",
                 "cancelled": "cancelled"}.get(cls, "unknown")
    if outcome != "done" or view is None:
        return {"execution": execution, "task": "not_evaluated",
                "judgment": "not_applicable" if view is None else "complete"}
    if view.get("status") == "evaluation_unavailable":     # oracle could not run: not a task result either way
        return {"execution": execution, "task": "not_evaluated", "judgment": "evaluation_unavailable"}
    crit = bool(view.get("critical_failures"))
    checks = [v for v in view["checks"].values() if v is not None]
    if view["status"] == "not_runnable":
        task = "task_failure"
    elif crit:
        task = "critical_failure"
    elif checks and all(checks):
        task = "task_success"
    else:
        task = "task_failure"
    judgment = "pending_human" if view.get("pending_human") else "complete"
    return {"execution": execution, "task": task, "judgment": judgment}
