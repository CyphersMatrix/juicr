"""Controller core. One authority for windows, queueing, budget reservations and
cancellation. UI and MCP callers go through the same methods (plan section 4).

Stdlib only. SQLite is the single source of truth so reservations and consumption
survive restart (plan section 6).
"""
import hashlib
import hmac
import json
import sqlite3
import threading
import time
from dataclasses import dataclass

DIMS = ("seconds", "requests", "tokens", "tool_calls", "retries", "sandbox_cpu_s", "spend_usd")
PROTECTED = ("budget_increase", "publication", "external_judging")
ACTIVE = ("preparing", "running", "cancelling", "restoring")

SCHEMA = """
CREATE TABLE IF NOT EXISTS owner(id INTEGER PRIMARY KEY CHECK(id=1), token_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS experiment(
  id TEXT PRIMARY KEY, state TEXT NOT NULL, creator TEXT NOT NULL,
  window_start REAL, window_end REAL, grace_s REAL,           -- grace_s NULL = unlimited
  created REAL NOT NULL, spec TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS budget(
  experiment_id TEXT NOT NULL, dim TEXT NOT NULL, lim REAL NOT NULL,
  reserved REAL NOT NULL DEFAULT 0, used REAL NOT NULL DEFAULT 0,
  unknown INTEGER NOT NULL DEFAULT 0, overrun REAL NOT NULL DEFAULT 0,
  hard INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(experiment_id, dim));
CREATE TABLE IF NOT EXISTS trial(
  id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, state TEXT NOT NULL,
  admitted REAL NOT NULL, cost TEXT NOT NULL, handle TEXT);
CREATE TABLE IF NOT EXISTS reservation(
  id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL,
  trial_id TEXT NOT NULL, cost TEXT NOT NULL, open INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS approval(
  id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL,
  kind TEXT NOT NULL, detail TEXT, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS event(
  id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT, ts REAL NOT NULL,
  kind TEXT NOT NULL, detail TEXT);
"""


class PolicyError(Exception):
    """A request violated an approval, window or budget rule."""


@dataclass(frozen=True)
class Principal:
    role: str  # 'owner' | 'user' | 'mcp'
    name: str = ""
    _issued_by: object = None  # set only by Controller.authenticate_owner


class Controller:
    def __init__(self, path, backend, clock=time.time):
        self.db_path, self.backend, self.clock = path, backend, clock
        self._lock = threading.Lock()
        self._token = object()
        c = self._conn()
        c.executescript(SCHEMA)
        c.commit()
        c.close()

    # -- infrastructure -------------------------------------------------
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def _tx(self):
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        return c

    def _event(self, c, exp, kind, detail=""):
        c.execute("INSERT INTO event(experiment_id,ts,kind,detail) VALUES(?,?,?,?)",
                  (exp, self.clock(), kind, detail))

    # -- owner authentication (bootstrap) --------------------------------
    def set_owner_token(self, token):
        """First-run bootstrap only; refuses to replace an existing owner."""
        c = self._tx()
        try:
            if c.execute("SELECT 1 FROM owner").fetchone():
                raise PolicyError("owner already configured")
            c.execute("INSERT INTO owner VALUES(1,?)", (hashlib.sha256(token.encode()).hexdigest(),))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK"); raise
        finally:
            c.close()

    def authenticate_owner(self, token):
        c = self._conn()
        row = c.execute("SELECT token_hash FROM owner").fetchone()
        c.close()
        h = hashlib.sha256(token.encode()).hexdigest()
        if not row or not hmac.compare_digest(row["token_hash"], h):
            raise PolicyError("owner authentication failed")
        return Principal("owner", "owner", self._token)

    def issue_owner_principal(self):
        """For the web layer only, after it has verified an owner session. Library callers
        should use authenticate_owner(token)."""
        return Principal("owner", "owner", self._token)

    def _require_owner(self, p):
        if not isinstance(p, Principal) or p.role != "owner" or p._issued_by is not self._token:
            raise PolicyError("owner authorization required")

    # -- experiments ------------------------------------------------------
    def create_experiment(self, exp_id, creator, spec=None):
        c = self._tx()
        try:
            c.execute("INSERT INTO experiment(id,state,creator,created,spec) VALUES(?,?,?,?,?)",
                      (exp_id, "awaiting_approval", creator.name, self.clock(), json.dumps(spec or {})))
            self._event(c, exp_id, "created", creator.name)
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK"); raise
        finally:
            c.close()

    def approve_window(self, owner, exp_id, start, end, grace_s, budgets):
        """Owner opens a window and approves the budget vector.
        budgets: {dim: limit} or {dim: (limit, hard)}. grace_s None = unlimited."""
        self._require_owner(owner)
        c = self._tx()
        try:
            st = c.execute("SELECT state FROM experiment WHERE id=?", (exp_id,)).fetchone()
            if not st or st["state"] != "awaiting_approval":
                raise PolicyError("experiment not awaiting approval")
            for dim, v in budgets.items():
                if dim not in DIMS:
                    raise PolicyError(f"unknown budget dimension {dim}")
                lim, hard = (v if isinstance(v, tuple) else (v, True))
                c.execute("INSERT INTO budget(experiment_id,dim,lim,hard) VALUES(?,?,?,?)",
                          (exp_id, dim, lim, int(hard)))
            c.execute("UPDATE experiment SET state='queued',window_start=?,window_end=?,grace_s=? WHERE id=?",
                      (start, end, grace_s, exp_id))
            c.execute("INSERT INTO approval(experiment_id,kind,detail,ts) VALUES(?,?,?,?)",
                      (exp_id, "run", json.dumps(budgets), self.clock()))
            self._event(c, exp_id, "approved", "window+budget")
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK"); raise
        finally:
            c.close()

    def grant_protected(self, owner, exp_id, kind, detail=""):
        """Budget increase, publication and external judging need separate owner approval."""
        self._require_owner(owner)
        if kind not in PROTECTED:
            raise PolicyError("unknown protected action")
        c = self._tx()
        try:
            if kind == "budget_increase":
                d = json.loads(detail)  # {dim: new_limit}
                for dim, lim in d.items():
                    cur = c.execute("SELECT lim FROM budget WHERE experiment_id=? AND dim=?", (exp_id, dim)).fetchone()
                    if not cur or lim <= cur["lim"]:
                        raise PolicyError("budget increase must raise an existing limit")
                    c.execute("UPDATE budget SET lim=? WHERE experiment_id=? AND dim=?", (lim, exp_id, dim))
            c.execute("INSERT INTO approval(experiment_id,kind,detail,ts) VALUES(?,?,?,?)",
                      (exp_id, kind, detail, self.clock()))
            self._event(c, exp_id, "protected_grant", kind)
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK"); raise
        finally:
            c.close()

    def has_approval(self, exp_id, kind):
        c = self._conn()
        r = c.execute("SELECT 1 FROM approval WHERE experiment_id=? AND kind=?", (exp_id, kind)).fetchone()
        c.close()
        return bool(r)

    def request_protected(self, caller, exp_id, kind):
        """Ordinary callers can never self-approve; they only ask."""
        if kind not in PROTECTED:
            raise PolicyError("unknown protected action")
        if not self.has_approval(exp_id, kind):
            raise PolicyError(f"{kind} requires separate owner approval for this experiment")
        return True

    # -- admission / dispatch ---------------------------------------------
    def _window_check(self, c, exp, trial_admitted, now):
        if exp["state"] in ("cancelling", "cancelled", "completed", "failed", "partial"):
            raise PolicyError(f"experiment is {exp['state']}")
        if exp["window_start"] is None or now < exp["window_start"]:
            raise PolicyError("window not open")

    def admit(self, caller, exp_id, trial_id, cost):
        """Queue a trial. New admission only while the window is open; grace never admits."""
        now = self.clock()
        c = self._tx()
        try:
            exp = c.execute("SELECT * FROM experiment WHERE id=?", (exp_id,)).fetchone()
            if not exp:
                raise PolicyError("no such experiment")
            self._window_check(c, exp, now, now)
            if now > exp["window_end"]:
                raise PolicyError("window closed: grace does not admit new work")
            c.execute("INSERT INTO trial(id,experiment_id,state,admitted,cost) VALUES(?,?,?,?,?)",
                      (trial_id, exp_id, "queued", now, json.dumps(cost)))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK"); raise
        finally:
            c.close()

    def dispatch(self, trial_id):
        """Reserve budget transactionally, then hand to the backend. Returns the handle."""
        now = self.clock()
        c = self._tx()
        try:
            t = c.execute("SELECT * FROM trial WHERE id=?", (trial_id,)).fetchone()
            if not t or t["state"] != "queued":
                raise PolicyError("trial not queued")
            exp = c.execute("SELECT * FROM experiment WHERE id=?", (t["experiment_id"],)).fetchone()
            self._window_check(c, exp, t["admitted"], now)
            if now > exp["window_end"]:
                g = exp["grace_s"]
                if g is not None and now > exp["window_end"] + g:
                    raise PolicyError("grace expired")
                # unlimited or finite grace: only work admitted inside the window continues
                if t["admitted"] > exp["window_end"]:
                    raise PolicyError("not admitted inside window")
            cost = json.loads(t["cost"])
            for dim, amt in cost.items():
                b = c.execute("SELECT * FROM budget WHERE experiment_id=? AND dim=?", (exp["id"], dim)).fetchone()
                if b is None:
                    raise PolicyError(f"no approved budget for {dim}")
                if b["hard"] and b["used"] + b["reserved"] + amt > b["lim"]:
                    self._exhaust(c, exp["id"], dim)
                    c.execute("COMMIT")
                    c.close()
                    raise PolicyError(f"budget exhausted: {dim}")
            for dim, amt in cost.items():
                c.execute("UPDATE budget SET reserved=reserved+? WHERE experiment_id=? AND dim=?", (amt, exp["id"], dim))
            c.execute("INSERT INTO reservation(experiment_id,trial_id,cost) VALUES(?,?,?)",
                      (exp["id"], trial_id, t["cost"]))
            c.execute("UPDATE trial SET state='running' WHERE id=?", (trial_id,))
            c.execute("UPDATE experiment SET state='running' WHERE id=? AND state='queued'", (exp["id"],))
            c.execute("COMMIT")
        except PolicyError:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except BaseException:
            c.execute("ROLLBACK"); raise
        finally:
            try:
                c.close()
            except sqlite3.Error:
                pass
        handle = self.backend.start(trial_id)
        c = self._tx()
        c.execute("UPDATE trial SET handle=? WHERE id=?", (handle, trial_id))
        c.execute("COMMIT")
        c.close()
        return handle

    def _exhaust(self, c, exp_id, dim):
        """Stop scheduling and cancel active work (called inside a transaction)."""
        c.execute("UPDATE experiment SET state='cancelling' WHERE id=? AND state IN('queued','running')", (exp_id,))
        c.execute("UPDATE trial SET state='cancelled' WHERE experiment_id=? AND state='queued'", (exp_id,))
        for r in c.execute("SELECT handle FROM trial WHERE experiment_id=? AND state='running'", (exp_id,)).fetchall():
            if r["handle"]:
                self.backend.cancel(r["handle"])
        self._event(c, exp_id, "exhausted", dim)

    def settle(self, trial_id, actual):
        """Reconcile observed usage. actual[dim]=None means unknown (not zero)."""
        c = self._tx()
        try:
            t = c.execute("SELECT * FROM trial WHERE id=?", (trial_id,)).fetchone()
            res = c.execute("SELECT * FROM reservation WHERE trial_id=? AND open=1", (trial_id,)).fetchone()
            if not res:
                raise PolicyError("no open reservation")
            cost = json.loads(res["cost"])
            for dim, amt in cost.items():
                c.execute("UPDATE budget SET reserved=reserved-? WHERE experiment_id=? AND dim=?", (amt, t["experiment_id"], dim))
            for dim, val in actual.items():
                if val is None:
                    c.execute("UPDATE budget SET unknown=1 WHERE experiment_id=? AND dim=?", (t["experiment_id"], dim))
                    continue
                c.execute("UPDATE budget SET used=used+? WHERE experiment_id=? AND dim=?", (val, t["experiment_id"], dim))
                b = c.execute("SELECT * FROM budget WHERE experiment_id=? AND dim=?", (t["experiment_id"], dim)).fetchone()
                if b["used"] > b["lim"]:
                    c.execute("UPDATE budget SET overrun=? WHERE experiment_id=? AND dim=?",
                              (b["used"] - b["lim"], t["experiment_id"], dim))
            c.execute("UPDATE reservation SET open=0 WHERE id=?", (res["id"],))
            if t["state"] == "running":
                c.execute("UPDATE trial SET state='done' WHERE id=?", (trial_id,))
            self._event(c, t["experiment_id"], "settled", trial_id)
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK"); raise
        finally:
            c.close()

    # -- completion, restart reconciliation, scheduler ----------------------
    def finish_trial(self, trial_id, outcome, actual, started=None):
        """Settle a trial and set its terminal state. outcome: done|failed|timeout|cancelled.
        Wall-clock seconds are charged from `started` unless the caller supplies them."""
        if outcome not in ("done", "failed", "timeout", "cancelled"):
            raise PolicyError("bad outcome")
        actual = dict(actual)
        if started is not None and "seconds" not in actual:
            actual["seconds"] = max(0.0, self.clock() - started)
        self.settle(trial_id, actual)
        c = self._tx()
        try:
            t = c.execute("SELECT experiment_id FROM trial WHERE id=?", (trial_id,)).fetchone()
            c.execute("UPDATE trial SET state=? WHERE id=?", (outcome, trial_id))
            self._maybe_complete(c, t["experiment_id"])
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK"); raise
        finally:
            c.close()

    def _maybe_complete(self, c, exp_id):
        """Move an experiment to a terminal state once no trial is queued or running."""
        left = c.execute("SELECT COUNT(*) n FROM trial WHERE experiment_id=? AND state IN('queued','running')",
                         (exp_id,)).fetchone()["n"]
        st = c.execute("SELECT state FROM experiment WHERE id=?", (exp_id,)).fetchone()["state"]
        if left or st in ("completed", "partial", "cancelled", "failed", "awaiting_approval"):
            return
        bad = c.execute("SELECT COUNT(*) n FROM trial WHERE experiment_id=? AND state!='done'", (exp_id,)).fetchone()["n"]
        total = c.execute("SELECT COUNT(*) n FROM trial WHERE experiment_id=?", (exp_id,)).fetchone()["n"]
        if st == "cancelling":
            new = "cancelled"
        elif total and not bad:
            new = "completed"
        else:
            new = "partial"
        c.execute("UPDATE experiment SET state=? WHERE id=?", (new, exp_id))
        self._event(c, exp_id, "terminal", new)

    def reconcile_after_restart(self):
        """Trials that were running when the process died have unknown usage: settle their
        reservations as unknown (never zero), mark them interrupted, and re-evaluate the
        experiment. Does not resume work or authorize new inference."""
        c = self._tx()
        try:
            rows = c.execute("SELECT * FROM reservation WHERE open=1").fetchall()
            n = 0
            for r in rows:
                cost = json.loads(r["cost"])
                for dim, amt in cost.items():
                    c.execute("UPDATE budget SET reserved=reserved-?,unknown=1 WHERE experiment_id=? AND dim=?",
                              (amt, r["experiment_id"], dim))
                c.execute("UPDATE reservation SET open=0 WHERE id=?", (r["id"],))
                c.execute("UPDATE trial SET state='interrupted' WHERE id=?", (r["trial_id"],))
                self._event(c, r["experiment_id"], "interrupted", r["trial_id"])
                n += 1
            for e in c.execute("SELECT id FROM experiment WHERE state IN('queued','running','cancelling')").fetchall():
                self._maybe_complete(c, e["id"])
            c.execute("COMMIT")
            return n
        except BaseException:
            c.execute("ROLLBACK"); raise
        finally:
            c.close()

    def tick(self, max_active=1):
        """Dispatch queued trials in admission order while fewer than max_active are running.
        Returns the list of trial ids dispatched. Policy refusals leave the trial queued or
        cancelled by the rules in dispatch(); they never raise out of the scheduler."""
        dispatched = []
        c = self._conn()
        active = c.execute("SELECT COUNT(*) n FROM trial WHERE state='running'").fetchone()["n"]
        queued = [r["id"] for r in c.execute(
            "SELECT t.id FROM trial t JOIN experiment e ON e.id=t.experiment_id "
            "WHERE t.state='queued' AND e.state IN('queued','running') ORDER BY t.admitted, t.rowid")]
        c.close()
        for tid in queued:
            if active >= max_active:
                break
            try:
                self.dispatch(tid)
                dispatched.append(tid); active += 1
            except PolicyError:
                continue
        return dispatched

    def sweep_expired(self):
        """Cancel queued trials of experiments whose grace has ended."""
        now = self.clock()
        c = self._tx()
        try:
            for e in c.execute("SELECT * FROM experiment WHERE state IN('queued','running') "
                               "AND window_end IS NOT NULL AND grace_s IS NOT NULL").fetchall():
                if now > e["window_end"] + e["grace_s"]:
                    c.execute("UPDATE trial SET state='cancelled' WHERE experiment_id=? AND state='queued'", (e["id"],))
                    self._event(c, e["id"], "grace_expired", "")
                    self._maybe_complete(c, e["id"])
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK"); raise
        finally:
            c.close()

    def snapshot(self):
        """Read-only view for UIs: experiments, budgets, trial counts."""
        c = self._conn()
        out = []
        for e in c.execute("SELECT * FROM experiment ORDER BY created DESC"):
            d = dict(e)
            d["budgets"] = {r["dim"]: dict(r) for r in c.execute("SELECT * FROM budget WHERE experiment_id=?", (e["id"],))}
            d["trials"] = {r["state"]: r["n"] for r in c.execute(
                "SELECT state, COUNT(*) n FROM trial WHERE experiment_id=? GROUP BY state", (e["id"],))}
            d["approvals"] = [r["kind"] for r in c.execute("SELECT kind FROM approval WHERE experiment_id=?", (e["id"],))]
            out.append(d)
        c.close()
        return out

    # -- cancellation -----------------------------------------------------
    def cancel(self, caller, exp_id):
        """Cancellation overrides grace. Propagates to the backend where supported."""
        c = self._tx()
        try:
            exp = c.execute("SELECT state FROM experiment WHERE id=?", (exp_id,)).fetchone()
            if not exp:
                raise PolicyError("no such experiment")
            c.execute("UPDATE experiment SET state='cancelling' WHERE id=?", (exp_id,))
            c.execute("UPDATE trial SET state='cancelled' WHERE experiment_id=? AND state='queued'", (exp_id,))
            for r in c.execute("SELECT handle FROM trial WHERE experiment_id=? AND state='running'", (exp_id,)).fetchall():
                if r["handle"]:
                    self.backend.cancel(r["handle"])
            self._event(c, exp_id, "cancel", caller.name)
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK"); raise
        finally:
            c.close()

    # -- reads ------------------------------------------------------------
    def budget(self, exp_id):
        c = self._conn()
        rows = {r["dim"]: dict(r) for r in c.execute("SELECT * FROM budget WHERE experiment_id=?", (exp_id,))}
        c.close()
        return rows

    def state(self, exp_id):
        c = self._conn()
        r = c.execute("SELECT state FROM experiment WHERE id=?", (exp_id,)).fetchone()
        c.close()
        return r["state"] if r else None

    def trial_state(self, trial_id):
        c = self._conn()
        r = c.execute("SELECT state FROM trial WHERE id=?", (trial_id,)).fetchone()
        c.close()
        return r["state"] if r else None
