"""Lemonade API adapter (stdlib). Never logs or returns the key; errors are redacted.

Only documented/observed endpoints: GET /api/v1/health, POST /api/v1/load, POST /api/v1/unload,
POST /api/v1/chat/completions (SSE). Accepting a request is not proof of effective behaviour;
callers verify through health() (plan section 2)."""
import json
import time
import urllib.error
import urllib.request


class LemonadeError(Exception):
    pass


class LemonadeClient:
    def __init__(self, base_url, key_file=None, key=None, timeout=60):
        self.base, self.timeout = base_url.rstrip("/"), timeout
        self._key_file, self._key = key_file, key

    def _k(self):
        if self._key:
            return self._key
        with open(self._key_file) as f:
            return f.read().strip()

    def _redact(self, text):
        try:
            k = self._k()
        except OSError:
            return text
        return text.replace(k, "<redacted>") if k else text

    def _req(self, method, path, body=None, timeout=None):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data, method=method, headers={
            "Authorization": "Bearer " + self._k(), "Content-Type": "application/json"})
        return urllib.request.urlopen(r, timeout=timeout or self.timeout)

    def _json(self, method, path, body=None, timeout=None):
        try:
            with self._req(method, path, body, timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise LemonadeError(self._redact(f"HTTP {e.code} {path}")) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise LemonadeError(self._redact(f"{type(e).__name__} {path}: {e}")) from None

    def health(self):
        return self._json("GET", "/api/v1/health", timeout=15)

    def load(self, model, options=None):
        """Per-load, non-persistent: save_options is always false."""
        body = {"model_name": model, "save_options": False}
        body.update(options or {})
        return self._json("POST", "/api/v1/load", body, timeout=300)

    def unload(self, model):
        return self._json("POST", "/api/v1/unload", {"model_name": model}, timeout=60)

    def chat_stream(self, body, deadline_s=180.0, should_cancel=lambda: False):
        """Stream one chat completion. Returns a dict with text, usage, timings, client timing and
        how it ended: ok | timeout | cancelled | error. Never raises for transport errors."""
        b = dict(body)
        b.update(stream=True, stream_options={"include_usage": True})
        t0 = time.monotonic()
        resp = None
        out = {"text": "", "reasoning": "", "usage": None, "timings": None, "first_event_s": None,
               "first_token_s": None, "events": 0, "ended": "ok", "error": None, "total_s": None}
        try:
            resp = self._req("POST", "/api/v1/chat/completions", b, timeout=min(deadline_s, 30))
            for raw in resp:
                if should_cancel():
                    out["ended"] = "cancelled"; resp.close(); break
                if time.monotonic() - t0 > deadline_s:
                    out["ended"] = "timeout"; resp.close(); break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:") or line.endswith("[DONE]"):
                    continue
                try:
                    j = json.loads(line[5:])
                except ValueError:
                    continue
                out["events"] += 1
                if out["first_event_s"] is None:
                    out["first_event_s"] = time.monotonic() - t0
                ch = (j.get("choices") or [{}])[0].get("delta", {})
                if ch.get("content"):
                    if out["first_token_s"] is None:
                        out["first_token_s"] = time.monotonic() - t0
                    out["text"] += ch["content"]
                if ch.get("reasoning_content"):
                    out["reasoning"] += ch["reasoning_content"]
                if j.get("usage"):
                    out["usage"] = j["usage"]
                if j.get("timings"):
                    out["timings"] = j["timings"]
        except urllib.error.HTTPError as e:
            out["ended"], out["error"] = "error", self._redact(f"HTTP {e.code}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            out["ended"] = "timeout" if "timed out" in str(e) else "error"
            out["error"] = self._redact(f"{type(e).__name__}: {e}")
        finally:
            if resp is not None:
                try:
                    resp.close()
                except OSError:
                    pass
        out["total_s"] = time.monotonic() - t0
        return out
