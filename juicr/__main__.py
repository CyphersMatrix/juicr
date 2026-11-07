"""Run: python -m juicr            (env: JUICR_DATA, JUICR_HOST, JUICR_PORT, JUICR_BACKEND=sim|real)
       python -m juicr token NAME [mcp|user]   (prints a new API bearer token ONCE; stores only its hash)"""
import json
import os
import secrets
import sys
import threading
from pathlib import Path

from .controller import Controller
from .evidence import EvidenceStore
from .service import Orchestrator, QueueBackend
from .web import create_app, _hash


def _data():
    data = Path(os.environ.get("JUICR_DATA", str(Path.home() / ".juicr")))
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(data, 0o700)
    return data


def make_token(name, role):
    if role not in ("mcp", "user") or not name.replace("-", "").replace("_", "").isalnum():
        sys.exit("usage: python -m juicr token NAME [mcp|user]  (NAME letters/digits/-/_)")
    af = _data() / "api_tokens.json"
    cur = json.loads(af.read_text()) if af.exists() else {}
    tok = secrets.token_urlsafe(32)
    cur[name] = {"role": role, "sha256": _hash(tok)}
    af.write_text(json.dumps(cur, indent=1))
    os.chmod(af, 0o600)
    print(tok)


def main():
    data = _data()
    mode = os.environ.get("JUICR_BACKEND", "sim")
    if mode not in ("sim", "real"):
        sys.exit("JUICR_BACKEND must be sim or real")
    tok_file, sec_file = data / "owner_token", data / "session_secret"
    backend = QueueBackend()
    ctl = Controller(str(data / "juicr.db"), backend)
    if not tok_file.exists():
        tok_file.write_text(secrets.token_urlsafe(32))
    os.chmod(tok_file, 0o600)
    try:
        ctl.set_owner_token(tok_file.read_text().strip())
    except Exception:
        pass  # already configured
    if not sec_file.exists():
        sec_file.write_text(secrets.token_urlsafe(48))
    os.chmod(sec_file, 0o600)
    api_tokens = {}
    af = data / "api_tokens.json"
    if af.exists():
        for name, d in json.loads(af.read_text()).items():
            if d["role"] in ("user", "mcp"):
                api_tokens[d["sha256"]] = (d["role"], name)
    client = None
    if mode == "real":
        from .lemonade import LemonadeClient
        client = LemonadeClient(os.environ.get("JUICR_LEMONADE_URL", "http://10.138.6.1:13305"),
                                key_file=str(data / "lemonade_key"))
    orch = Orchestrator(ctl, backend, EvidenceStore(str(data / "evidence")), mode=mode, client=client)
    n = ctl.reconcile_after_restart()
    threading.Thread(target=orch.run_forever, daemon=True).start()
    app = create_app(ctl, sec_file.read_text().strip(), api_tokens, orch=orch, mode=mode)
    print(f"juicr: backend={mode}; reconciled {n} interrupted trial(s)", flush=True)
    import uvicorn
    uvicorn.run(app, host=os.environ.get("JUICR_HOST", "127.0.0.1"),
                port=int(os.environ.get("JUICR_PORT", "8810")), log_level="warning", server_header=False)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "token":
        make_token(sys.argv[2] if len(sys.argv) > 2 else "", sys.argv[3] if len(sys.argv) > 3 else "mcp")
    else:
        main()
