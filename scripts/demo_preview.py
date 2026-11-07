"""Render the dashboard with seeded demo data (temp DB) to static HTML for visual review.
Usage: python scripts/demo_preview.py OUTDIR   (needs fastapi; never touches real data)"""
import os, re, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi.testclient import TestClient
from juicr.controller import Controller, Principal
from juicr.fake_backend import FakeBackend
from juicr.web import create_app

out = sys.argv[1]; os.makedirs(out, exist_ok=True)
now = [10_000.0]
d = tempfile.mkdtemp(); ctl = Controller(os.path.join(d, "demo.db"), FakeBackend(), lambda: now[0])
ctl.set_owner_token("demo"); own = ctl.authenticate_owner("demo"); mcp = Principal("mcp", "hermes")
def exp(i, creator, approve=None):
    ctl.create_experiment(i, creator)
    if approve: ctl.approve_window(own, i, now[0] - 600, now[0] + approve[0] * 60, approve[1], approve[2])
exp("gemma-e4b-pilot", mcp, (42, 600, {"requests": 150, "tokens": 40000, "seconds": 3600}))
for i in range(30): ctl.admit(mcp, "gemma-e4b-pilot", f"p{i}", {"requests": 1, "tokens": 300, "seconds": 20})
for i in range(14):
    ctl.dispatch(f"p{i}")
    if i < 11: ctl.finish_trial(f"p{i}", "done", {"requests": 1, "tokens": 280, "seconds": 18})
    elif i == 11: ctl.finish_trial(f"p{i}", "failed", {"requests": 1, "tokens": None, "seconds": 25})
ctl.grant_protected(own, "gemma-e4b-pilot", "external_judging")
exp("batch-sweep-ub", mcp, (3, None, {"requests": 20, "tokens": 5000}))
for i in range(4): ctl.admit(mcp, "batch-sweep-ub", f"b{i}", {"requests": 5, "tokens": 1200})
for i in range(4):
    ctl.dispatch(f"b{i}")
    ctl.finish_trial(f"b{i}", "done", {"requests": 5, "tokens": 1500 if i == 3 else 1100})
exp("ctx-8k-vs-16k", mcp, (60, 600, {"requests": 40}))
ctl.admit(mcp, "ctx-8k-vs-16k", "c0", {"requests": 1}); ctl.cancel(own, "ctx-8k-vs-16k")
exp("kv-cache-types", mcp); exp("flash-attn-check", mcp)
c = TestClient(create_app(ctl, "s" * 40, clock=lambda: now[0]), follow_redirects=False)
login = c.get("/login").text
c.post("/login", data={"token": "demo"})
dash = c.get("/").text
err = TestClient(create_app(ctl, "s" * 40, clock=lambda: now[0]), follow_redirects=False).post("/login", data={"token": "x"}).text
open(f"{out}/login.html", "w").write(login); open(f"{out}/dashboard.html", "w").write(dash); open(f"{out}/login-error.html", "w").write(err)
print("rendered", len(dash), "bytes")
