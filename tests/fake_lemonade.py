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
        self.truncate = False        # stream hidden reasoning + an unfinished code block, finish_reason=length
        self.agent_never_answers = False  # tool-using chat keeps asking for tools
        self.served_ctx = []         # ctx_size in effect for the target model when each chat was served
        self.drop_after_chat = False # unload the model after every chat (forces the pre-dispatch guard to matter)


def make(state):
    state_ref = state
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

        def _nonstream(self, b):
            """Scripted A1 tool-call dialogue keyed on how many tool results the conversation already holds."""
            n = len([m for m in b.get("messages", []) if m.get("role") == "tool"])
            def tc(i, name, args):
                return {"id": f"call{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            if b.get("tools") and state_ref.agent_never_answers:
                msg = {"content": "", "tool_calls": [tc(n, "search_documents", {"query": "again"})]}
            elif b.get("tools") and n == 0:
                msg = {"content": "", "tool_calls": [tc(0, "search_documents", {"query": "Cedar launch"})]}
            elif b.get("tools") and n == 1:
                msg = {"content": "", "tool_calls": [tc(1, "search_documents", {"query": "Cedar launch date"})]}
            elif b.get("tools") and n == 2:
                msg = {"content": "", "tool_calls": [tc(2, "read_document", {"id": "A"}), tc(3, "read_document", {"id": "B"})]}
            else:
                msg = {"content": "Draft: Cedar launches May 2 (B) with a $40,000 budget (A). Not sent.", "tool_calls": None}
            return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {"completion_tokens": 20, "prompt_tokens": 50}}

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
                    state.served_ctx.append([m for m in state.loaded if m["model_name"] == b["model"]][0]["recipe_options"]["ctx_size"])
                    if state.drop_after_chat:
                        pending_drop = b["model"]
                    else:
                        pending_drop = None
                def drop():
                    if pending_drop:
                        with state.lock:
                            state.loaded = [m for m in state.loaded if m["model_name"] != pending_drop]
                if b.get("stream") is False:
                    out = self._nonstream(b); drop()
                    return self._json(200, out)
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                def ev(o): self.wfile.write(b"data: " + json.dumps(o).encode() + b"\n\n"); self.wfile.flush()
                if state.truncate:
                    ev({"choices": [{"delta": {"reasoning_content": "let me think about intervals..."}}]})
                    ev({"choices": [{"delta": {"content": "```python\ndef merge_intervals(intervals):\n    "}}]})
                    ev({"choices": [{"delta": {}, "finish_reason": "length"}], "usage": {"completion_tokens": 1024, "prompt_tokens": 50}})
                    self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush(); drop(); return
                ev({"choices": [{"delta": {"content": "Rivers "}}]})
                if state.stall:
                    threading.Event().wait(30); return
                ev({"choices": [{"delta": {"content": "flow."}}]})
                last = {"choices": [{"delta": {}}]}
                if not state.no_usage:
                    last["usage"] = {"completion_tokens": 3, "prompt_tokens": 24, "prompt_tokens_details": {"cached_tokens": state.cached}}
                    last["timings"] = {"predicted_per_second": 23.5, "prompt_n": 24, "cache_n": state.cached}
                ev(last); self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush(); drop(); return
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
