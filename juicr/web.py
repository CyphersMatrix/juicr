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

from . import cases
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


def create_app(controller, session_secret, api_tokens=None, clock=time.time, orch=None, mode="sim"):
    """api_tokens: {sha256(token): (role, name)} for non-owner API callers."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.controller = controller
    signer = TimestampSigner(session_secret, salt="juicr-session")
    limiter = LoginLimiter(clock=clock)
    api_tokens = api_tokens or {}
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals["mode"] = mode
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
        exps = controller.snapshot()
        now = clock()
        for e in exps:
            we = e.get("window_end")
            e["remaining_min"] = None if we is None else max(0.0, (we - now) / 60)
            e["window_open"] = we is not None and now <= we
            tot = sum(e["trials"].values())
            e["trial_total"] = tot
            e["segments"] = []
            x = 0.0
            for st in ("done", "running", "queued", "failed", "timeout", "interrupted", "cancelled"):
                n = e["trials"].get(st, 0)
                if n and tot:
                    w = 100.0 * n / tot
                    e["segments"].append({"state": st, "n": n, "x": round(x, 2), "w": round(w, 2)})
                    x += w
            for b in e["budgets"].values():
                lim = b["lim"] or 1
                b["pct_used"] = round(min(100.0, 100.0 * b["used"] / lim), 1)
                b["pct_res"] = round(min(100.0 - b["pct_used"], 100.0 * b["reserved"] / lim), 1)
                b["over"] = b["used"] > b["lim"]
        stats = {
            "total": len(exps),
            "pending": sum(1 for e in exps if e["state"] == "awaiting_approval"),
            "active": sum(1 for e in exps if e["state"] in ("queued", "running", "cancelling")),
            "finished": sum(1 for e in exps if e["state"] in ("completed", "partial", "cancelled", "failed")),
        }
        return {"exps": exps, "pending": [e for e in exps if e["state"] == "awaiting_approval"],
                "live": [e for e in exps if e["state"] != "awaiting_approval"], "stats": stats,
                "csrf": sess["c"], "dims": DIMS,
                "grants": ("publication", "external_judging")}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        p, sess = who(request)
        if not p or not sess:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "index.html", page_ctx(request, sess))

    @app.get("/experiments", response_class=HTMLResponse)
    def experiments_partial(request: Request):
        p, sess = need_owner(request)
        return templates.TemplateResponse(request, "_board.html", page_ctx(request, sess))

    @app.get("/experiments/live", response_class=HTMLResponse)
    def experiments_live(request: Request):
        p, sess = need_owner(request)
        return templates.TemplateResponse(request, "_live.html", page_ctx(request, sess))

    def pilot_spec(pack="pilot-1"):
        if pack not in cases.PACKS:
            raise PolicyError("unknown pack")
        return {"pack": pack, "model": "Gemma-4-E4B-it-GGUF", "load": {"ctx_size": 8192}}

    def rerender(request, sess):
        return templates.TemplateResponse(request, "_board.html", page_ctx(request, sess))

    def fail(request, sess, msg, code=400):
        ctx = page_ctx(request, sess)
        ctx["error"] = msg
        return templates.TemplateResponse(request, "_board.html", ctx, status_code=code)

    @app.post("/experiments", response_class=HTMLResponse)
    def create(request: Request, exp_id: str = Form(...), kind: str = Form("custom"), csrf: str = Form("")):
        p, sess = need_owner(request)
        check_csrf(request, sess, csrf)
        if not exp_id.replace("-", "").replace("_", "").isalnum() or len(exp_id) > 40 or exp_id == "live":
            return fail(request, sess, "Experiment id: letters, digits, - and _ only (max 40).")
        try:
            controller.create_experiment(exp_id, p, pilot_spec({"pilot": "pilot-1", "pilot2": "pilot-2"}[kind]) if kind in ("pilot", "pilot2") else None)
        except Exception as e:  # duplicate id etc.
            return fail(request, sess, f"Could not create: {type(e).__name__}")
        return rerender(request, sess)

    @app.post("/experiments/{exp_id}/approve", response_class=HTMLResponse)
    def approve(request: Request, exp_id: str, window_min: float = Form(...), grace: str = Form("10"),
                requests: float = Form(0), tokens: float = Form(0), seconds: float = Form(0),
                tool_calls: float = Form(0), csrf: str = Form("")):
        p, sess = need_owner(request)
        check_csrf(request, sess, csrf)
        budgets = {k: v for k, v in (("requests", requests), ("tokens", tokens), ("seconds", seconds),
                                     ("tool_calls", tool_calls)) if v > 0}
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

    # -- experiment detail, pilot launcher, blind review -----------------------------
    def detail_ctx(request, sess, exp_id, extra=None):
        e = next((x for x in page_ctx(request, sess)["exps"] if x["id"] == exp_id), None)
        if e is None:
            raise HTTPException(404, "no such experiment")
        rows = orch.trial_rows(exp_id) if orch else []
        ctx = {"e": e, "rows": rows, "summary": orch.summary(rows) if orch else [], "csrf": sess["c"],
               "can_load_pilot": bool(orch) and e["spec"].get("pack") in cases.PACKS and e["state"] == "queued" and not e["trials"],
               "pending_reviews": len(orch.pending_review(exp_id)) if orch else 0}
        ctx.update(extra or {})
        return ctx

    @app.get("/experiments/{exp_id}", response_class=HTMLResponse)
    def experiment_page(request: Request, exp_id: str):
        p, sess = who(request)
        if not p or not sess:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "experiment.html", detail_ctx(request, sess, exp_id))

    @app.post("/experiments/{exp_id}/pilot", response_class=HTMLResponse)
    def load_pilot(request: Request, exp_id: str, csrf: str = Form("")):
        p, sess = need_owner(request)
        check_csrf(request, sess, csrf)
        spec = controller.experiment_spec(exp_id)
        if not orch or not spec or spec.get("pack") not in cases.PACKS:
            raise HTTPException(400, "not a pilot experiment")
        try:
            controller.admit_many(p, exp_id, orch.pilot_admission(spec, exp_id))
        except PolicyError as e:
            return templates.TemplateResponse(request, "experiment.html", detail_ctx(request, sess, exp_id, {"error": str(e)}), status_code=409)
        except Exception as e:
            return templates.TemplateResponse(request, "experiment.html", detail_ctx(request, sess, exp_id, {"error": f"Could not load pilot: {type(e).__name__}"}), status_code=409)
        return RedirectResponse(f"/experiments/{exp_id}", status_code=303)

    @app.get("/experiments/{exp_id}/results", response_class=HTMLResponse)
    def results_partial(request: Request, exp_id: str):
        p, sess = need_owner(request)
        return templates.TemplateResponse(request, "_results.html", detail_ctx(request, sess, exp_id))

    @app.get("/experiments/{exp_id}/review", response_class=HTMLResponse)
    def review_page(request: Request, exp_id: str):
        p, sess = who(request)
        if not p or not sess:
            return RedirectResponse("/login", status_code=303)
        pk = orch.next_packet(exp_id, salt=session_secret) if orch else None
        return templates.TemplateResponse(request, "review.html", {"pk": pk, "exp_id": exp_id, "csrf": sess["c"], "error": None})

    @app.post("/experiments/{exp_id}/review", response_class=HTMLResponse)
    async def review_submit(request: Request, exp_id: str):
        p, sess = need_owner(request)
        form = await request.form()
        check_csrf(request, sess, form.get("csrf", ""))
        pid = str(form.get("packet_id", ""))
        try:
            scores = {k[2:]: int(v) for k, v in form.items() if k.startswith("s_")}
            orch.submit_review(pid, "owner", scores, str(form.get("notes", ""))[:2000])
        except (ValueError, KeyError, OSError, Exception) as e:
            pk = orch.next_packet(exp_id, salt=session_secret) if orch else None
            return templates.TemplateResponse(request, "review.html", {"pk": pk, "exp_id": exp_id, "csrf": sess["c"],
                                                                      "error": "Could not save: " + str(e)[:120]}, status_code=400)
        return RedirectResponse(f"/experiments/{exp_id}/review", status_code=303)

    # -- MCP over HTTP (JSON-RPC 2.0, bearer token, never the owner cookie) -----------
    MCP_TOOLS = [
        {"name": "list_experiments", "description": "List experiments with state, budgets and trial counts.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "get_experiment", "description": "Experiment detail: budgets, trial states, per case/temperature summary.",
         "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
        {"name": "create_pilot_experiment", "description": "Create an experiment holding an approved pilot pack (pilot-1: all five cases; pilot-2: rerun of C1, W1, A1 with raised caps). It runs nothing until the owner opens a window and approves a budget.",
         "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "pack": {"type": "string", "enum": ["pilot-1", "pilot-2"]}}, "required": ["id"]}},
        {"name": "load_pilot", "description": "Queue the experiment's pilot trials (30 for pilot-1, 18 for pilot-2). Only works inside an owner-approved window; budgets still apply.",
         "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
        {"name": "cancel_experiment", "description": "Cancel an experiment; active work is stopped where supported.",
         "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
        {"name": "check_protected", "description": "Ask whether the owner has granted publication or external_judging for an experiment. This never grants anything.",
         "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "kind": {"type": "string", "enum": ["publication", "external_judging", "budget_increase"]}}, "required": ["id", "kind"]}},
        {"name": "get_results", "description": "Per-trial outcome classes, critical failures, timings and pending human reviews. Unavailable values are null, never zero.",
         "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}]

    def mcp_call(p, name, a):
        eid = str(a.get("id", ""))
        if name == "list_experiments":
            return [{"id": e["id"], "state": e["state"], "trials": e["trials"], "approvals": e["approvals"],
                     "budgets": {d: {"limit": b["lim"], "used": b["used"], "reserved": b["reserved"], "unknown": bool(b["unknown"])} for d, b in e["budgets"].items()}}
                    for e in controller.snapshot()]
        if name == "create_pilot_experiment":
            if not eid.replace("-", "").replace("_", "").isalnum() or len(eid) > 40 or eid == "live":
                raise PolicyError("id: letters, digits, - and _ only (max 40)")
            pk = str(a.get("pack", "pilot-1"))
            controller.create_experiment(eid, p, pilot_spec(pk))
            return {"created": eid, "pack": pk, "state": "awaiting_approval", "note": "owner must open a window and approve a budget"}
        if name == "load_pilot":
            spec = controller.experiment_spec(eid)
            if not orch or not spec or spec.get("pack") not in cases.PACKS:
                raise PolicyError("not a pilot experiment")
            adm = orch.pilot_admission(spec, eid)
            controller.admit_many(p, eid, adm)
            return {"queued": len(adm), "pack": spec["pack"]}
        if name == "cancel_experiment":
            controller.cancel(p, eid); return {"cancelling": eid}
        if name == "check_protected":
            return {"granted": controller.has_approval(eid, str(a.get("kind", "")))}
        e = next((x for x in controller.snapshot() if x["id"] == eid), None)
        if e is None:
            raise PolicyError("no such experiment")
        if name == "get_experiment":
            rows = orch.trial_rows(eid) if orch else []
            return {"id": eid, "state": e["state"], "trials": e["trials"], "approvals": e["approvals"],
                    "summary": orch.summary(rows) if orch else []}
        if name == "get_results":
            return orch.trial_rows(eid) if orch else []
        raise PolicyError("unknown tool")

    @app.post("/mcp")
    async def mcp(request: Request):
        if request.headers.get("origin"):          # browsers send Origin; MCP clients do not (DNS-rebinding guard)
            raise HTTPException(403, "browser origins are not allowed")
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise HTTPException(401, "bearer token required")
        p, _ = who(request)
        if not p or p.role == "owner":
            raise HTTPException(401, "invalid token")
        raw = await request.body()
        if len(raw) > 65536:
            raise HTTPException(413, "too large")
        try:
            msg = json.loads(raw)
        except ValueError:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        if not isinstance(msg, dict):
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "batches not supported"}})
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:                             # notification
            from fastapi import Response
            return Response(status_code=202)
        def ok(r): return JSONResponse({"jsonrpc": "2.0", "id": mid, "result": r})
        if method == "initialize":
            return ok({"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                       "serverInfo": {"name": "juicr", "version": "0.1"},
                       "instructions": "Benchmark controller. Approvals, publication and budgets belong to the owner; you can queue and cancel within approved limits."})
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": MCP_TOOLS})
        if method == "tools/call":
            name = params.get("name"); args = params.get("arguments") or {}
            if name not in {t["name"] for t in MCP_TOOLS} or not isinstance(args, dict):
                return JSONResponse({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool or bad arguments"}})
            try:
                out = mcp_call(p, name, args)
                return ok({"content": [{"type": "text", "text": json.dumps(out, default=str)}], "isError": False})
            except PolicyError as e:
                return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
            except Exception as e:
                return ok({"content": [{"type": "text", "text": f"error: {type(e).__name__}"}], "isError": True})
        return JSONResponse({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}})

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
