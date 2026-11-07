"""Web layer: owner login, CSRF-protected htmx testing UI, small JSON API.

Trust model: every request is either the owner (signed session cookie obtained with the
owner token) or an API caller (bearer token, role 'user' or 'mcp', configured by the owner
out of band). Owner-only actions never accept a bearer token. Everything else is refused.
"""
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from .controller import DIMS, PolicyError, Principal

HERE = Path(__file__).parent
SESSION_MAX_AGE = 8 * 3600
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


def _hash(t):
    return hashlib.sha256(t.encode()).hexdigest()


class LoginLimiter:
    def __init__(self, n=5, window=300, clock=time.time):
        self.n, self.window, self.clock, self.fails = n, window, clock, {}

    def blocked(self, key):
        now = self.clock()
        self.fails[key] = [t for t in self.fails.get(key, []) if now - t < self.window]
        return len(self.fails[key]) >= self.n

    def fail(self, key):
        self.fails.setdefault(key, []).append(self.clock())


def create_app(controller, session_secret, api_tokens=None, clock=time.time):
    """api_tokens: {sha256(token): (role, name)} for non-owner API callers."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.controller = controller
    signer = TimestampSigner(session_secret, salt="juicr-session")
    limiter = LoginLimiter(clock=clock)
    api_tokens = api_tokens or {}
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # -- identity ----------------------------------------------------------
    def read_session(request):
        raw = request.cookies.get("juicr_session")
        if not raw:
            return None
        try:
            val = signer.unsign(raw, max_age=SESSION_MAX_AGE).decode()
            return json.loads(val)
        except (BadSignature, SignatureExpired, ValueError):
            return None

    def who(request):
        """Returns (Principal|None, session|None). Bearer wins only for non-owner roles."""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
            for h, (role, name) in api_tokens.items():
                if hmac.compare_digest(h, _hash(tok)):
                    return Principal(role, name), None
            return None, None
        sess = read_session(request)
        if sess and sess.get("o") is True:
            return controller.issue_owner_principal(), sess
        return None, None

    def need_user(request):
        p, sess = who(request)
        if not p:
            raise HTTPException(401, "authentication required")
        return p, sess

    def need_owner(request):
        p, sess = who(request)
        if not p or p.role != "owner":
            raise HTTPException(403, "owner session required")
        return p, sess

    def check_csrf(request, sess, form_token=None):
        """Cookie sessions must prove they know the per-session CSRF token. Bearer calls
        are not cookie-authenticated, so they carry no ambient credential to forge."""
        if sess is None:
            return
        tok = request.headers.get("x-csrf-token") or form_token or ""
        if not hmac.compare_digest(tok, sess.get("c", "")):
            raise HTTPException(403, "bad CSRF token")

    # -- pages ---------------------------------------------------------------
    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    def login(request: Request, token: str = Form(...)):
        key = request.client.host if request.client else "?"
        if limiter.blocked(key):
            return templates.TemplateResponse(request, "login.html", {"error": "Too many attempts. Wait 5 minutes."}, status_code=429)
        try:
            controller.authenticate_owner(token)
        except PolicyError:
            limiter.fail(key)
            return templates.TemplateResponse(request, "login.html", {"error": "Invalid token."}, status_code=401)
        sess = json.dumps({"o": True, "c": secrets.token_urlsafe(24)})
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("juicr_session", signer.sign(sess).decode(), max_age=SESSION_MAX_AGE,
                        httponly=True, samesite="strict", path="/")
        return resp

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        _, sess = need_user(request)
        check_csrf(request, sess, csrf)
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie("juicr_session", path="/")
        return resp

    def page_ctx(request, sess):
        return {"exps": controller.snapshot(), "csrf": sess["c"], "dims": DIMS,
                "grants": ("budget_increase", "publication", "external_judging")}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        p, sess = who(request)
        if not p or not sess:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "index.html", page_ctx(request, sess))

    @app.get("/experiments", response_class=HTMLResponse)
    def experiments_partial(request: Request):
        p, sess = need_owner(request)
        return templates.TemplateResponse(request, "_experiments.html", page_ctx(request, sess))

    def rerender(request, sess):
        return templates.TemplateResponse(request, "_experiments.html", page_ctx(request, sess))

    def fail(request, sess, msg, code=400):
        ctx = page_ctx(request, sess)
        ctx["error"] = msg
        return templates.TemplateResponse(request, "_experiments.html", ctx, status_code=code)

    @app.post("/experiments", response_class=HTMLResponse)
    def create(request: Request, exp_id: str = Form(...), csrf: str = Form("")):
        p, sess = need_owner(request)
        check_csrf(request, sess, csrf)
        if not exp_id.replace("-", "").replace("_", "").isalnum() or len(exp_id) > 40:
            return fail(request, sess, "Experiment id: letters, digits, - and _ only (max 40).")
        try:
            controller.create_experiment(exp_id, p)
        except Exception as e:  # duplicate id etc.
            return fail(request, sess, f"Could not create: {type(e).__name__}")
        return rerender(request, sess)

    @app.post("/experiments/{exp_id}/approve", response_class=HTMLResponse)
    def approve(request: Request, exp_id: str, window_min: float = Form(...), grace: str = Form("10"),
                requests: float = Form(0), tokens: float = Form(0), seconds: float = Form(0),
                csrf: str = Form("")):
        p, sess = need_owner(request)
        check_csrf(request, sess, csrf)
        budgets = {k: v for k, v in (("requests", requests), ("tokens", tokens), ("seconds", seconds)) if v > 0}
        if not budgets or window_min <= 0:
            return fail(request, sess, "Set a positive window and at least one budget.")
        g = None if grace.strip().lower() == "unlimited" else float(grace) * 60
        now = clock()
        try:
            controller.approve_window(p, exp_id, now, now + window_min * 60, g, budgets)
        except PolicyError as e:
            return fail(request, sess, str(e), 409)
        return rerender(request, sess)

    @app.post("/experiments/{exp_id}/cancel", response_class=HTMLResponse)
    def cancel(request: Request, exp_id: str, csrf: str = Form("")):
        p, sess = need_owner(request)
        check_csrf(request, sess, csrf)
        try:
            controller.cancel(p, exp_id)
        except PolicyError as e:
            return fail(request, sess, str(e), 404)
        return rerender(request, sess)

    @app.post("/experiments/{exp_id}/grant", response_class=HTMLResponse)
    def grant(request: Request, exp_id: str, kind: str = Form(...), csrf: str = Form("")):
        p, sess = need_owner(request)
        check_csrf(request, sess, csrf)
        if kind == "budget_increase":
            return fail(request, sess, "Budget increases are entered as explicit new limits; not available in the UI yet.", 400)
        try:
            controller.grant_protected(p, exp_id, kind)
        except PolicyError as e:
            return fail(request, sess, str(e), 400)
        return rerender(request, sess)

    # -- JSON API ------------------------------------------------------------
    @app.get("/api/experiments")
    def api_list(request: Request):
        need_user(request)
        return JSONResponse(controller.snapshot())

    @app.post("/api/experiments/{exp_id}/trials")
    async def api_admit(request: Request, exp_id: str):
        p, sess = need_user(request)
        check_csrf(request, sess)
        body = await request.json()
        cost = body.get("cost", {})
        if not isinstance(cost, dict) or any(k not in DIMS or not isinstance(v, (int, float)) or v < 0 for k, v in cost.items()):
            raise HTTPException(400, "bad cost")
        try:
            controller.admit(p, exp_id, str(body.get("trial_id", "")), cost)
        except PolicyError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.post("/api/experiments/{exp_id}/cancel")
    def api_cancel(request: Request, exp_id: str):
        p, sess = need_user(request)
        check_csrf(request, sess)
        try:
            controller.cancel(p, exp_id)
        except PolicyError as e:
            raise HTTPException(404, str(e))
        return {"ok": True}

    @app.post("/api/experiments/{exp_id}/request/{kind}")
    def api_request_protected(request: Request, exp_id: str, kind: str):
        """Non-owner callers can ask whether a protected action has been granted. Never grant."""
        p, sess = need_user(request)
        check_csrf(request, sess)
        try:
            controller.request_protected(p, exp_id, kind)
        except PolicyError as e:
            raise HTTPException(403, str(e))
        return {"ok": True}

    return app
