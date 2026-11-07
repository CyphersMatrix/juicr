"""Web-layer checks: auth, CSRF, role separation, escaping, headers. Needs fastapi (venv)."""
import os
import re
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
    HAVE = True
except Exception:  # pragma: no cover
    HAVE = False

from juicr.controller import Controller
from juicr.fake_backend import FakeBackend
if HAVE:
    from juicr.web import create_app, _hash

TOKEN = "owner-token-for-tests"
MCP = "mcp-secret-token"


@unittest.skipUnless(HAVE, "fastapi not installed")
class WebBase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.ctl = Controller(os.path.join(self.d.name, "j.db"), FakeBackend())
        self.ctl.set_owner_token(TOKEN)
        self.app = create_app(self.ctl, "s" * 40, {_hash(MCP): ("mcp", "hermes")})
        self.c = TestClient(self.app, follow_redirects=False)

    def tearDown(self):
        self.d.cleanup()

    def login(self, c=None):
        c = c or self.c
        r = c.post("/login", data={"token": TOKEN})
        self.assertEqual(r.status_code, 303)
        html = c.get("/").text
        return re.search(r'X-CSRF-Token": "([^"]+)"', html).group(1)

    def bearer(self):
        return {"Authorization": f"Bearer {MCP}"}


class TestAuth(WebBase):
    def test_unauthenticated_refused(self):
        self.assertEqual(self.c.get("/").status_code, 303)
        self.assertEqual(self.c.get("/experiments").status_code, 403)
        self.assertEqual(self.c.get("/api/experiments").status_code, 401)
        self.assertEqual(self.c.post("/experiments", data={"exp_id": "x"}).status_code, 403)
        self.assertEqual(self.c.get("/healthz").status_code, 200)

    def test_login_flags_and_failures(self):
        self.assertEqual(self.c.post("/login", data={"token": "bad"}).status_code, 401)
        r = self.c.post("/login", data={"token": TOKEN})
        sc = r.headers["set-cookie"].lower()
        self.assertIn("httponly", sc); self.assertIn("samesite=strict", sc)

    def test_lockout_after_five_failures(self):
        for _ in range(5):
            self.assertEqual(self.c.post("/login", data={"token": "bad"}).status_code, 401)
        self.assertEqual(self.c.post("/login", data={"token": TOKEN}).status_code, 429)

    def test_tampered_cookie_rejected(self):
        self.login()
        v = self.c.cookies.get("juicr_session")
        self.c.cookies.clear()
        self.c.cookies.set("juicr_session", v[:-2] + ("aa" if v[-2:] != "aa" else "bb"))
        self.assertEqual(self.c.get("/experiments").status_code, 403)

    def test_security_headers(self):
        h = self.c.get("/login").headers
        self.assertIn("default-src 'none'", h["content-security-policy"])
        self.assertNotIn("unsafe-inline", h["content-security-policy"])
        self.assertEqual(h["x-frame-options"], "DENY")


class TestCsrfAndRoles(WebBase):
    def test_csrf_required_for_cookie_posts(self):
        csrf = self.login()
        self.assertEqual(self.c.post("/experiments", data={"exp_id": "e1"}).status_code, 403)
        self.assertEqual(self.c.post("/experiments", data={"exp_id": "e1"}, headers={"X-CSRF-Token": "wrong"}).status_code, 403)
        r = self.c.post("/experiments", data={"exp_id": "e1"}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(r.status_code, 200); self.assertIn("e1", r.text)
        r = self.c.post("/experiments", data={"exp_id": "e2", "csrf": csrf})   # hidden form field also accepted
        self.assertEqual(r.status_code, 200)

    def test_bearer_cannot_use_owner_actions(self):
        csrf = self.login()
        self.c.post("/experiments", data={"exp_id": "e1"}, headers={"X-CSRF-Token": csrf})
        b = TestClient(self.app, follow_redirects=False)
        for path, data in (("/experiments", {"exp_id": "x"}),
                           ("/experiments/e1/approve", {"window_min": 5, "requests": 5}),
                           ("/experiments/e1/grant", {"kind": "publication"}),
                           ("/experiments/e1/cancel", {})):
            self.assertEqual(b.post(path, data=data, headers=self.bearer()).status_code, 403, path)

    def test_mcp_cannot_self_grant_but_can_ask(self):
        csrf = self.login(); H = {"X-CSRF-Token": csrf}
        self.c.post("/experiments", data={"exp_id": "e1"}, headers=H)
        self.assertEqual(self.c.post("/experiments/e1/approve", data={"window_min": 30, "requests": 5, "grace": "10"}, headers=H).status_code, 200)
        b = TestClient(self.app, follow_redirects=False)
        self.assertEqual(b.post("/api/experiments/e1/request/publication", headers=self.bearer()).status_code, 403)
        self.assertEqual(self.c.post("/experiments/e1/grant", data={"kind": "publication"}, headers=H).status_code, 200)
        self.assertEqual(b.post("/api/experiments/e1/request/publication", headers=self.bearer()).status_code, 200)
        self.assertEqual(b.post("/api/experiments/e2/request/publication", headers=self.bearer()).status_code, 403)

    def test_bad_bearer_is_not_downgraded_to_session(self):
        self.login()
        r = self.c.get("/api/experiments", headers={"Authorization": "Bearer nope"})
        self.assertEqual(r.status_code, 401)   # a wrong bearer never falls back to the cookie

    def test_api_admit_validates_cost_and_budget_flow(self):
        csrf = self.login(); H = {"X-CSRF-Token": csrf}
        self.c.post("/experiments", data={"exp_id": "e1"}, headers=H)
        self.c.post("/experiments/e1/approve", data={"window_min": 30, "requests": 2, "grace": "10"}, headers=H)
        b = TestClient(self.app, follow_redirects=False)
        bad = b.post("/api/experiments/e1/trials", json={"trial_id": "t", "cost": {"bogus": 1}}, headers=self.bearer())
        self.assertEqual(bad.status_code, 400)
        for i in range(3):
            self.assertEqual(b.post("/api/experiments/e1/trials", json={"trial_id": f"t{i}", "cost": {"requests": 1}}, headers=self.bearer()).status_code, 200)
        self.assertEqual(self.ctl.tick(max_active=5), ["t0", "t1"])
        self.assertEqual(self.ctl.tick(max_active=5), [])
        self.assertEqual(self.ctl.trial_state("t2"), "cancelled")

    def test_experiment_id_validation_and_output_escaping(self):
        csrf = self.login(); H = {"X-CSRF-Token": csrf}
        r = self.c.post("/experiments", data={"exp_id": "<script>alert(1)</script>"}, headers=H)
        self.assertIn("letters, digits", r.text); self.assertNotIn("<script>alert", r.text)
        from juicr.controller import Principal
        self.ctl.create_experiment("<b>x</b>", Principal("mcp", "<i>evil</i>"))   # bypass UI validation
        t = self.c.get("/experiments").text
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", t); self.assertNotIn("<b>x</b>", t); self.assertNotIn("<i>evil</i>", t)

    def test_logout_requires_csrf(self):
        csrf = self.login()
        self.assertEqual(self.c.post("/logout").status_code, 403)
        self.assertEqual(self.c.post("/logout", data={"csrf": csrf}).status_code, 303)


if __name__ == "__main__":
    unittest.main()
