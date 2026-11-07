"""Evidence store: content-addressed artifacts, append-only attempt events, immutable manifests.

Rules (plan section 10): events are only appended; artifacts are written once and verified by
hash on read; a finalized manifest can never be replaced. Rescoring or rerunning creates new
records, never edits old ones."""
import hashlib
import json
import os
import stat
import time


class EvidenceError(Exception):
    pass


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class EvidenceStore:
    def __init__(self, root, clock=time.time):
        self.root, self.clock = root, clock
        for d in ("artifacts", "events", "manifests"):
            os.makedirs(os.path.join(root, d), mode=0o700, exist_ok=True)

    # -- artifacts ----------------------------------------------------------------
    def put_artifact(self, data, media_type="application/octet-stream", sensitivity="private"):
        h = hashlib.sha256(data).hexdigest()
        p = os.path.join(self.root, "artifacts", h)
        if not os.path.exists(p):
            tmp = p + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data); f.flush(); os.fsync(f.fileno())
            os.chmod(tmp, 0o400)
            os.replace(tmp, p)
            _fsync_dir(os.path.dirname(p))
        meta = os.path.join(self.root, "artifacts", h + ".json")
        if not os.path.exists(meta):
            with open(meta, "w") as f:
                json.dump({"hash": h, "size": len(data), "media_type": media_type,
                           "sensitivity": sensitivity, "created": self.clock()}, f)
        return h

    def get_artifact(self, h):
        p = os.path.join(self.root, "artifacts", h)
        with open(p, "rb") as f:
            data = f.read()
        if hashlib.sha256(data).hexdigest() != h:
            raise EvidenceError(f"artifact {h[:12]} failed hash verification")
        return data

    # -- attempt events (append-only JSONL) -------------------------------------
    def _events_path(self, trial_id):
        if not trial_id or any(c in trial_id for c in "/\\.\0"):
            raise EvidenceError("bad trial id")
        return os.path.join(self.root, "events", trial_id + ".jsonl")

    def append_event(self, trial_id, kind, detail=None):
        rec = {"ts": self.clock(), "kind": kind, "detail": detail or {}}
        line = (json.dumps(rec, sort_keys=True) + "\n").encode()
        fd = os.open(self._events_path(trial_id), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line); os.fsync(fd)
        finally:
            os.close(fd)
        return rec

    def events(self, trial_id):
        p = self._events_path(trial_id)
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(l) for l in f if l.strip()]

    # -- manifests ----------------------------------------------------------------
    def _manifest_path(self, kind, mid):
        if not mid or any(c in mid for c in "/\\.\0"):
            raise EvidenceError("bad manifest id")
        return os.path.join(self.root, "manifests", f"{kind}-{mid}.json")

    def finalize(self, kind, mid, body):
        """Write a manifest exactly once, read-only, with a hash over its canonical content."""
        p = self._manifest_path(kind, mid)
        canon = json.dumps(body, sort_keys=True, separators=(",", ":"))
        doc = {"kind": kind, "id": mid, "finalized": self.clock(),
               "sha256": hashlib.sha256(canon.encode()).hexdigest(), "body": body}
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        except FileExistsError:
            raise EvidenceError(f"{kind} {mid} already finalized; manifests are immutable") from None
        try:
            os.write(fd, json.dumps(doc, indent=1, sort_keys=True).encode()); os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_dir(os.path.dirname(p))
        return doc

    def manifest(self, kind, mid):
        p = self._manifest_path(kind, mid)
        if not os.path.exists(p):
            return None
        try:
            with open(p) as f:
                doc = json.load(f)
            canon = json.dumps(doc["body"], sort_keys=True, separators=(",", ":"))
            ok = hashlib.sha256(canon.encode()).hexdigest() == doc["sha256"]
        except (ValueError, KeyError, TypeError):
            raise EvidenceError(f"{kind} {mid} manifest is corrupt") from None
        if not ok:
            raise EvidenceError(f"{kind} {mid} manifest failed hash verification")
        return doc
