"""Phase 4 web checks: detail/results, pilot launcher, blind review, MCP. Needs fastapi (runs in the CT)."""
import json
import os
import re
import tempfile
import time
import unittest

try:
    from fastapi.testclient import TestClient
    HAVE = True
except Exception:  # pragma: no cover
    HAVE = False

from juicr import cases
from juicr.controller import Controller
from juicr.evidence import EvidenceStore
from juicr.service import Orchestrator, QueueBackend
if HAVE:
    from juicr.web import _hash, create_app

TOKEN, MCP = "owner-token-for-tests", "mcp-secret-token"


@unittest.skipUnless(HAVE, "fastapi not installed")
class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.be = QueueBackend()
        self.ctl = Controller(os.path.join(self.d.name, "j.db"), self.be)
        self.ctl.set_owner_token(TOKEN)
        self.o = Orchestrator(self.ctl, self.be, EvidenceStore(os.path.join(self.d.name, "ev")), mode="sim", sim_delay=0)
        self.app = create_app(self.ctl, "s" * 40, {_hash(MCP): ("mcp", "hermes")}, orch=self.o, mode="sim")
        self.c = TestClient(self.app, follow_redirects=False)
        self.b = TestClient(self.app, follow_redirects=False)

    def tearDown(self):
        self.d.cleanup()

    def login(self):
        self.assertEqual(self.c.post("/login", data={"token": TOKEN}).status_code, 303)
        self.csrf = re.search(r'X-CSRF-Token": "([^"]+)"', self.c.get("/").text).group(1)
        self.H = {"X-CSRF-Token": self.csrf}

    def make_pilot(self, eid="p1", approve=True):
        self.login()
        self.c.post("/experiments", data={"exp_id": eid, "kind": "pilot"}, headers=self.H)
        if approve:
            r = self.c.post(f"/experiments/{eid}/approve", data={"window_min": 60, "grace": "10", "requests": 150,
                            "tokens": 60000, "seconds": 3600, "tool_calls": 24}, headers=self.H)
            self.assertEqual(r.status_code, 200)

    def run_all(self, eid="p1"):
        for _ in range(80):
            self.o.step()
            if self.ctl.state(eid) in ("completed", "partial", "cancelled", "failed"):
                return

    def rpc(self, method, params=None, mid=1, client=None, headers=None):
        h = {"Authorization": f"Bearer {MCP}", **(headers or {})}
        return (client or self.b).post("/mcp", content=json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}), headers=h)

    def tool(self, name, **args):
        r = self.rpc("tools/call", {"name": name, "arguments": args}).json()["result"]
        return r["isError"], r["content"][0]["text"]


class TestPilotUi(Base):
    def test_detail_requires_login_and_pilot_launch_rules(self):
        self.make_pilot(approve=False)
        self.assertEqual(TestClient(self.app, follow_redirects=False).get("/experiments/p1").status_code, 303)
        page = self.c.get("/experiments/p1"); self.assertEqual(page.status_code, 200)
        self.assertNotIn("Queue the 30 pilot trials", page.text)               # not offered before approval
        self.assertEqual(self.c.post("/experiments/p1/pilot", data={"csrf": self.csrf}).status_code, 409)   # refused: no window
        self.make_pilot("p2")
        self.assertIn("Queue the 30 pilot trials", self.c.get("/experiments/p2").text)
        self.assertEqual(self.c.post("/experiments/p2/pilot", data={}).status_code, 403)                     # CSRF
        self.assertEqual(self.b.post("/experiments/p2/pilot", data={"csrf": "x"}, headers={"Authorization": f"Bearer {MCP}"}).status_code, 403)  # not owner
        self.assertEqual(self.c.post("/experiments/p2/pilot", data={"csrf": self.csrf}).status_code, 303)
        self.assertEqual(len(self.ctl.list_trials("p2")), 30)
        self.assertEqual(self.c.post("/experiments/p2/pilot", data={"csrf": self.csrf}).status_code, 409)    # never duplicated
        self.assertEqual(len(self.ctl.list_trials("p2")), 30)

    def test_results_page_shows_counts_unavailable_as_dash_and_no_overall_score(self):
        self.make_pilot(); self.c.post("/experiments/p1/pilot", data={"csrf": self.csrf}); self.run_all()
        t = self.c.get("/experiments/p1/results").text
        self.assertIn("By case and temperature", t); self.assertIn("p1__A1-s17", t)
        self.assertIn('class="na"', t)                                       # simulated runs have no first-token time -> dash
        self.assertNotIn("overall", t.lower().replace("there is no overall score", ""))
        self.assertEqual(self.c.get("/experiments/p1/results").headers["cache-control"], "no-store")
        self.assertEqual(TestClient(self.app).get("/experiments/p1/results").status_code, 403)

    def test_blind_review_page_and_submission(self):
        self.make_pilot(); self.c.post("/experiments/p1/pilot", data={"csrf": self.csrf}); self.run_all()
        page = self.c.get("/experiments/p1/review").text
        self.assertIn("Blind review", page)
        for leak in ("temperature", "seed ", "0.7"):
            self.assertNotIn(leak, page.replace("Temperature, seed and condition are hidden.", ""))
        pid = re.search(r'name="packet_id" value="([0-9a-f]+)"', page).group(1)
        dims = re.findall(r'name="s_([a-z_]+)"', page)
        form = {"csrf": self.csrf, "packet_id": pid, **{f"s_{d}": "2" for d in set(dims)}}
        self.assertEqual(self.c.post("/experiments/p1/review", data={k: v for k, v in form.items() if k != "csrf"}).status_code, 403)
        self.assertEqual(self.b.post("/experiments/p1/review", data=form, headers={"Authorization": f"Bearer {MCP}"}).status_code, 403)
        bad = dict(form); bad.pop(f"s_{dims[0]}")
        self.assertEqual(self.c.post("/experiments/p1/review", data=bad).status_code, 400)                  # must rate every dimension
        self.assertEqual(self.c.post("/experiments/p1/review", data={**form, f"s_{dims[0]}": "5"}).status_code, 400)
        self.assertEqual(self.c.post("/experiments/p1/review", data=form).status_code, 303)
        self.assertEqual(self.c.post("/experiments/p1/review", data=form).status_code, 400)                 # already rated
        self.assertEqual(TestClient(self.app, follow_redirects=False).get("/experiments/p1/review").status_code, 303)

    def test_reserved_and_bad_ids(self):
        self.login()
        r = self.c.post("/experiments", data={"exp_id": "live", "kind": "custom"}, headers=self.H)
        self.assertIn("letters, digits", r.text)


class TestMcp(Base):
    def test_authentication_rules(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(TestClient(self.app).post("/mcp", content=body).status_code, 401)
        self.assertEqual(TestClient(self.app).post("/mcp", content=body, headers={"Authorization": "Bearer nope"}).status_code, 401)
        self.login()                                                         # owner cookie is NOT accepted on /mcp
        self.assertEqual(self.c.post("/mcp", content=body).status_code, 401)
        self.assertEqual(self.rpc("ping", headers={"Origin": "http://evil.example"}).status_code, 403)       # browser origin refused
        self.assertEqual(self.rpc("ping").json()["result"], {})

    def test_protocol_basics(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-03-26"}).json()["result"]
        self.assertEqual(init["serverInfo"]["name"], "juicr"); self.assertIn("tools", init["capabilities"])
        names = [t["name"] for t in self.rpc("tools/list").json()["result"]["tools"]]
        self.assertEqual(len(names), 7)
        self.assertFalse([n for n in names if re.search(r"approv|grant|publish|window|budget", n)])        # no way to approve anything
        self.assertEqual(self.rpc("nope").json()["error"]["code"], -32601)
        self.assertEqual(self.rpc("tools/call", {"name": "sudo", "arguments": {}}).json()["error"]["code"], -32602)
        n = self.b.post("/mcp", content=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), headers={"Authorization": f"Bearer {MCP}"})
        self.assertEqual(n.status_code, 202)
        batch = self.b.post("/mcp", content=json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]), headers={"Authorization": f"Bearer {MCP}"})
        self.assertEqual(batch.json()["error"]["code"], -32600)
        self.assertEqual(self.b.post("/mcp", content="{bad", headers={"Authorization": f"Bearer {MCP}"}).json()["error"]["code"], -32700)
        self.assertEqual(self.b.post("/mcp", content="x" * 70000, headers={"Authorization": f"Bearer {MCP}"}).status_code, 413)

    def test_mcp_can_create_and_queue_only_within_owner_approval(self):
        err, txt = self.tool("create_pilot_experiment", id="h1"); self.assertFalse(err)
        err, txt = self.tool("load_pilot", id="h1")
        self.assertTrue(err); self.assertIn("window", txt.lower())                                          # no approved window yet
        self.assertEqual(len(self.ctl.list_trials("h1")), 0)
        self.login()
        self.c.post("/experiments/h1/approve", data={"window_min": 60, "grace": "10", "requests": 150, "tokens": 60000,
                                                       "seconds": 3600, "tool_calls": 24}, headers=self.H)
        err, txt = self.tool("load_pilot", id="h1"); self.assertFalse(err)
        self.assertEqual(len(self.ctl.list_trials("h1")), 30)
        err, _ = self.tool("load_pilot", id="h1"); self.assertTrue(err)                                     # no duplicates
        err, txt = self.tool("create_pilot_experiment", id="live"); self.assertTrue(err)
        err, txt = self.tool("create_pilot_experiment", id="x/../y"); self.assertTrue(err)

    def test_check_protected_never_grants_and_cancel_works(self):
        self.tool("create_pilot_experiment", id="h2")
        err, txt = self.tool("check_protected", id="h2", kind="publication"); self.assertEqual(json.loads(txt), {"granted": False})
        self.assertEqual(self.ctl.has_approval("h2", "publication"), False)                                  # asking changed nothing
        self.login()
        self.c.post("/experiments/h2/approve", data={"window_min": 5, "requests": 5, "grace": "1"}, headers=self.H)
        self.c.post("/experiments/h2/grant", data={"kind": "publication"}, headers=self.H)
        self.assertEqual(json.loads(self.tool("check_protected", id="h2", kind="publication")[1]), {"granted": True})
        err, _ = self.tool("cancel_experiment", id="h2"); self.assertFalse(err)
        self.assertEqual(self.ctl.state("h2"), "cancelled")

    def test_results_use_null_not_zero_and_unknown_experiment_errors(self):
        self.make_pilot(); self.c.post("/experiments/p1/pilot", data={"csrf": self.csrf}); self.run_all()
        rows = json.loads(self.tool("get_results", id="p1")[1])
        self.assertEqual(len(rows), 30); self.assertTrue(all(r["total_s"] is not None for r in rows))
        self.assertTrue(all(r["first_token_s"] is None and r["tok_s"] is None for r in rows))                # unavailable stays null
        summ = json.loads(self.tool("get_experiment", id="p1")[1])["summary"]
        self.assertEqual(len(summ), 10)
        self.assertTrue(self.tool("get_experiment", id="nope")[0])
        listing = json.loads(self.tool("list_experiments")[1]); self.assertEqual(listing[0]["id"], "p1")


if __name__ == "__main__":
    unittest.main()
