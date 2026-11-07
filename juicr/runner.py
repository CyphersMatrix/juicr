"""Runner: apply configuration, verify it, run attempts, restore prior state, write evidence.

Principles (plan sections 2, 3, 5, 9, 10):
* A load request being accepted is not evidence it took effect: every requested setting is
  recorded as verified | unverified | mismatch from API-visible state.
* A request sent to an unloaded model makes Lemonade silently load it with defaults (observed
  2026-11-07), so the configuration is re-verified immediately before EVERY request.
* Failed attempts are kept. Retries are separate accounted attempts.
* Restoration failure warns and continues; the next trial re-establishes its own configuration.
* Completed trials are never re-sent on resume."""
import time

from .lemonade import LemonadeError


class ConfigNotEstablished(Exception):
    pass


def _entry(health, model):
    for m in health.get("all_models_loaded", []):
        if m.get("model_name") == model:
            return m
    return None


def verify_settings(entry, requested):
    """requested: {'ctx_size': 8192, ...}. Returns {setting: {requested, effective, status}}.
    effective comes only from API-visible state (recipe_options, launch_command)."""
    out = {}
    ro = (entry or {}).get("recipe_options", {}) or {}
    cmd = (entry or {}).get("launch_command", []) or []
    for k, want in requested.items():
        eff, src = None, None
        if k in ro:
            eff, src = ro[k], "recipe_options"
        elif ("--" + k.replace("_", "-")) in cmd:
            i = cmd.index("--" + k.replace("_", "-"))
            if i + 1 < len(cmd):
                eff, src = cmd[i + 1], "launch_command"
        if eff is None:
            out[k] = {"requested": want, "effective": None, "status": "unverified", "source": None}
        elif str(eff) == str(want):
            out[k] = {"requested": want, "effective": eff, "status": "verified", "source": src}
        else:
            out[k] = {"requested": want, "effective": eff, "status": "mismatch", "source": src}
    return out


class Runner:
    def __init__(self, client, evidence, controller=None, clock=time.time, sleep=time.sleep,
                 restore_retries=2):
        self.client, self.ev, self.ctl = client, evidence, controller
        self.clock, self.sleep, self.restore_retries = clock, sleep, restore_retries
        self.warnings = []

    # -- state snapshot / restore ------------------------------------------------
    def snapshot_state(self):
        h = self.client.health()
        models = [{"model_name": m["model_name"], "checkpoint": m.get("checkpoint"),
                   "recipe": m.get("recipe"), "device": m.get("device"),
                   "recipe_options": m.get("recipe_options", {})}
                  for m in h.get("all_models_loaded", [])]
        return {"models": models, "version": h.get("version"), "captured": self.clock(),
                "scope": "API-visible only; hidden defaults, KV state and evicted models are not captured"}

    def _diff(self, prior):
        now = {m["model_name"]: m for m in self.snapshot_state()["models"]}
        want = {m["model_name"]: m for m in prior["models"]}
        extra = [n for n in now if n not in want]
        missing = [n for n in want if n not in now]
        changed = [n for n in want if n in now and now[n]["recipe_options"] != want[n]["recipe_options"]]
        return extra, missing, changed

    def restore(self, prior):
        """Bounded attempts; never raises. Returns evidence including what stayed unrestored."""
        attempts, errors = 0, []
        for _ in range(1 + self.restore_retries):
            attempts += 1
            try:
                extra, missing, changed = self._diff(prior)
                if not (extra or missing or changed):
                    return {"ok": True, "attempts": attempts, "unrestored": [], "errors": errors}
                for n in extra + changed:
                    self.client.unload(n)
                want = {m["model_name"]: m for m in prior["models"]}
                for n in missing + changed:
                    self.client.load(n, want[n]["recipe_options"])
            except LemonadeError as e:
                errors.append(str(e))
            self.sleep(0)
        try:
            extra, missing, changed = self._diff(prior)
        except LemonadeError as e:
            errors.append(str(e)); extra, missing, changed = [], [], ["state-unreadable"]
        un = {"extra": extra, "missing": missing, "changed": changed}
        self.warnings.append(f"restoration incomplete: {un}")
        return {"ok": False, "attempts": attempts, "unrestored": un, "errors": errors}

    # -- configuration ---------------------------------------------------------------
    def establish(self, config):
        """Load per config (non-persistent) and verify. Returns evidence; raises
        ConfigNotEstablished only when the model is not loaded afterwards or a setting mismatches."""
        model = config["model"]
        h = self.client.health()
        e = _entry(h, model)
        verdict = verify_settings(e, config.get("expect", {})) if e else None
        if e is None or any(v["status"] == "mismatch" for v in (verdict or {}).values()):
            if e is not None:
                self.client.unload(model)
            self.client.load(model, config.get("load_options", {}))
            h = self.client.health()
            e = _entry(h, model)
            if e is None:
                raise ConfigNotEstablished(f"{model} not loaded after load request")
            verdict = verify_settings(e, config.get("expect", {}))
        bad = {k: v for k, v in verdict.items() if v["status"] == "mismatch"}
        if bad:
            raise ConfigNotEstablished(f"settings mismatch: {bad}")
        return {"model": model, "settings": verdict, "launch_command": e.get("launch_command"),
                "backend": e.get("recipe_options", {}), "checkpoint": e.get("checkpoint"),
                "device": e.get("device"), "recipe": e.get("recipe")}

    def ensure_config(self, config):
        """Pre-dispatch guard: re-verify right before a request. One re-establish attempt."""
        try:
            return self.establish(config)
        except (ConfigNotEstablished, LemonadeError) as e:
            raise ConfigNotEstablished(str(e)) from None

    # -- trials ----------------------------------------------------------------------
    def execute(self, trial_id, config, spec, started=None):
        """Run one dispatched trial (call after controller.dispatch). spec: {'request': body,
        'deadline_s': 180, 'max_attempts': 1}. Returns the finalized manifest body.
        Idempotent: a trial with a finalized manifest is returned without sending anything."""
        done = self.ev.manifest("trial", trial_id)
        if done:
            return done["body"]
        started = self.clock() if started is None else started
        max_attempts = max(1, int(spec.get("max_attempts", 1)))
        attempts, sent, tokens, tokens_unknown = [], 0, 0, False
        outcome, classification, final = "failed", "infrastructure", None
        exp_id = None
        for n in range(1, max_attempts + 1):
            req_hash = self.ev.put_artifact(repr(sorted(spec["request"].items())).encode(), "text/plain")
            self.ev.append_event(trial_id, "attempt_start", {"attempt": n, "request": req_hash})
            try:
                cfg_ev = self.ensure_config(config)
            except ConfigNotEstablished as e:
                self.ev.append_event(trial_id, "attempt_end", {"attempt": n, "result": "config_not_established",
                                                              "error": str(e), "request_sent": False})
                attempts.append({"attempt": n, "result": "config_not_established", "request_sent": False})
                classification = "infrastructure"
                continue
            cancel = (lambda: self.ctl.is_cancelling_trial(trial_id)) if self.ctl else (lambda: False)
            res = self.client.chat_stream(spec["request"], spec.get("deadline_s", 180), cancel)
            sent += 1
            text_h = self.ev.put_artifact(res["text"].encode(), "text/plain")
            u = res["usage"]
            if u and u.get("completion_tokens") is not None:
                tokens += u["completion_tokens"]
            else:
                tokens_unknown = True
            rec = {"attempt": n, "result": res["ended"], "request_sent": True, "response": text_h,
                   "error": res["error"], "usage": u, "backend_timings": res["timings"],
                   "client": {"first_event_s": res["first_event_s"], "first_token_s": res["first_token_s"],
                             "total_s": res["total_s"], "events": res["events"]},
                   "cached_tokens": (u or {}).get("prompt_tokens_details", {}).get("cached_tokens") if u else None,
                   "config": cfg_ev}
            if res["ended"] == "cancelled":
                rec["server_cancellation"] = "unverified: closing the stream does not prove Lemonade stopped"
            self.ev.append_event(trial_id, "attempt_end", rec)
            attempts.append({k: rec[k] for k in ("attempt", "result", "request_sent")})
            final = rec
            if res["ended"] == "ok":
                outcome, classification = "done", "task_response"
                break
            if res["ended"] == "timeout":
                outcome, classification = "timeout", "timeout"
                break
            if res["ended"] == "cancelled":
                outcome, classification = "cancelled", "cancelled"
                break
            classification = "infrastructure"   # transport/server error: retry if attempts remain
        body = {"trial_id": trial_id, "outcome": outcome, "classification": classification,
                "attempts": attempts, "requests_sent": sent,
                "tokens": None if tokens_unknown else tokens,
                "final": final, "events_ref": f"events/{trial_id}.jsonl",
                "limits": "cache state not controlled (no verified reset API); usage as reported by backend"}
        self.ev.finalize("trial", trial_id, body)
        if self.ctl is not None:
            self.ctl.finish_trial(trial_id, outcome,
                                  {"requests": sent, "tokens": None if tokens_unknown else tokens}, started=started)
        return body

    # -- segments --------------------------------------------------------------------
    def run_segment(self, seg_id, config, trials):
        """trials: [(trial_id, spec)] already dispatched by the controller. Snapshots prior state,
        runs them, attempts restoration, finalizes an immutable segment manifest."""
        prior = self.snapshot_state()
        results, setup_error = [], None
        try:
            cfg_ev = self.establish(config)
        except (ConfigNotEstablished, LemonadeError) as e:
            cfg_ev, setup_error = None, str(e)
        for tid, spec in trials:
            results.append(self.execute(tid, config, spec)["outcome"])
        rest = self.restore(prior)
        self.ev.finalize("segment", seg_id, {"prior_state": prior, "config": cfg_ev, "setup_error": setup_error,
                                             "trials": [t for t, _ in trials], "outcomes": results,
                                             "restoration": rest, "warnings": list(self.warnings)})
        return rest
