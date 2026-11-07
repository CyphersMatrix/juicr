"""Run: python -m juicr  (env: JUICR_DATA, JUICR_HOST, JUICR_PORT)."""
import json
import os
import secrets
import threading
import time
from pathlib import Path

import uvicorn

from .controller import Controller
from .fake_backend import FakeBackend
from .web import create_app, _hash


def main():
    data = Path(os.environ.get("JUICR_DATA", str(Path.home() / ".juicr")))
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(data, 0o700)
    tok_file, sec_file = data / "owner_token", data / "session_secret"
    ctl = Controller(str(data / "juicr.db"), FakeBackend())
    if not tok_file.exists():
        tok_file.write_text(secrets.token_urlsafe(32))
    os.chmod(tok_file, 0o600)
    try:
        ctl.set_owner_token(tok_file.read_text().strip())
    except Exception:
        pass  # already configured; token file is authoritative for login only if it matches
    if not sec_file.exists():
        sec_file.write_text(secrets.token_urlsafe(48))
    os.chmod(sec_file, 0o600)
    api_tokens = {}
    af = data / "api_tokens.json"  # {"name": {"role": "mcp", "sha256": "..."}}
    if af.exists():
        for name, d in json.loads(af.read_text()).items():
            if d["role"] in ("user", "mcp"):
                api_tokens[d["sha256"]] = (d["role"], name)
    n = ctl.reconcile_after_restart()
    stop = threading.Event()

    def loop():  # Phase 1: fake backend only; no real inference is possible
        while not stop.wait(2):
            try:
                ctl.sweep_expired()
                ctl.tick()
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True).start()
    app = create_app(ctl, sec_file.read_text().strip(), api_tokens)
    print(f"juicr: reconciled {n} interrupted trial(s)", flush=True)
    uvicorn.run(app, host=os.environ.get("JUICR_HOST", "127.0.0.1"),
                port=int(os.environ.get("JUICR_PORT", "8810")), log_level="warning", server_header=False)


if __name__ == "__main__":
    main()
