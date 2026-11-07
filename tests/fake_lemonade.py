"""Fake Lemonade for Phase 2 checks. Mimics observed behaviour: two-LLM health listing,
auto-load with DEFAULT options on a chat to an unloaded model, SSE streaming with usage+timings.
Failure injection via attributes."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = "test-lemonade-key-0123456789"
DEFAULT_CTX = 65536


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.loaded = [{"model_name": "hermes-model-FLM", "checkpoint": "flm:e4b", "recipe": "flm", "device": "npu",
                        "recipe_options": {"ctx_size": 131072}, "launch_command": ["flm", "serve", "--ctx-len", "131072"]}]
        self.chat_requests = []      # bodies received (to prove no duplicates)
        self.ignore_options = False  # accept load but don't reflect options (unverified case)
        self.fail_unload = 0         # next N unload calls return 500
        self.fail_load = 0
        self.chat_fail = 0           # next N chats return 500
        self.stall = False           # chat never finishes (timeout)
        self.no_usage = False
        self.cached = 0
        self.unauth = 0


def make(state):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _auth(self):
            if self.headers.get("Authorization") != "Bearer " + KEY:
                state.unauth += 1
                self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":"Invalid or missing API key"}')
                return False
            return True

        def _json(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def _body(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")

        def _do_load(self, name, opts):
            ctx = DEFAULT_CTX if state.ignore_options or "ctx_size" not in opts else opts["ctx_size"]
            state.loaded = [m for m in state.loaded if m["model_name"] != name]
            state.loaded.append({"model_name": name, "checkpoint": "unsloth/x:Q4", "recipe": "llamacpp", "device": "gpu",
                                 "recipe_options": {"ctx_size": ctx}, "launch_command": ["llama-server", "--ctx-size", str(ctx)]})

        def do_GET(self):
            if not self._auth(): return
            if self.path == "/api/v1/health":
                with state.lock:
                    return self._json(200, {"status": "ok", "version": "test", "all_models_loaded": list(state.loaded)})
            self._json(404, {})

        def do_POST(self):
            if not self._auth(): return
            b = self._body()
            if self.path == "/api/v1/load":
                with state.lock:
                    if state.fail_load:
                        state.fail_load -= 1; return self._json(500, {"error": "boom"})
                    self._do_load(b["model_name"], {k: v for k, v in b.items() if k not in ("model_name", "save_options")})
                return self._json(200, {"status": "success"})
            if self.path == "/api/v1/unload":
                with state.lock:
                    if state.fail_unload:
                        state.fail_unload -= 1; return self._json(500, {"error": "boom"})
                    state.loaded = [m for m in state.loaded if m["model_name"] != b["model_name"]]
                return self._json(200, {"status": "success"})
            if self.path == "/api/v1/chat/completions":
                with state.lock:
                    state.chat_requests.append(b)
                    if state.chat_fail:
                        state.chat_fail -= 1; return self._json(500, {"error": "server error"})
                    if not any(m["model_name"] == b["model"] for m in state.loaded):
                        self._do_load(b["model"], {})          # silent auto-load with DEFAULT options
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                def ev(o): self.wfile.write(b"data: " + json.dumps(o).encode() + b"\n\n"); self.wfile.flush()
                ev({"choices": [{"delta": {"content": "Rivers "}}]})
                if state.stall:
                    threading.Event().wait(30); return
                ev({"choices": [{"delta": {"content": "flow."}}]})
                last = {"choices": [{"delta": {}}]}
                if not state.no_usage:
                    last["usage"] = {"completion_tokens": 3, "prompt_tokens": 24, "prompt_tokens_details": {"cached_tokens": state.cached}}
                    last["timings"] = {"predicted_per_second": 23.5, "prompt_n": 24, "cache_n": state.cached}
                ev(last); self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush(); return
            self._json(404, {})
    return H


class FakeLemonade:
    def __init__(self):
        self.state = State()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make(self.state))
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown(); self.srv.server_close()
