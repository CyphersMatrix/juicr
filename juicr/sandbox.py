"""Disposable rootless-Podman sandbox for model-authored code (plan section 4, Phase 0 evidence).

Defaults: no network, all capabilities dropped, no-new-privileges, read-only rootfs, small tmpfs,
memory/PID/CPU ceilings, hard timeout with kill, output cap. Uses the CT's own python3.13 and its
stdlib bind-mounted read-only into the existing debian:13 image, so no new image is downloaded.
Reference/test code written by us may run elsewhere; MODEL-AUTHORED code must only run here."""
import os
import pwd
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

IMAGE = "docker.io/library/debian:13"
PY = "/usr/bin/python3.13"


class SandboxUnavailable(Exception):
    """Podman/runtime problem: an infrastructure failure, never a task failure."""


def available():
    return shutil.which("podman") is not None and os.path.exists(PY)


def _libexpat():
    p = os.path.realpath("/usr/lib/x86_64-linux-gnu/libexpat.so.1")
    return p if os.path.exists(p) else None


_PODMAN_ERR = re.compile(r"Failed to obtain podman configuration|^Error: |cannot chdir|newuidmap|"
                         r"no such image|unable to find image|permission denied.*(libpod|run/user)|"
                         r"cgroup|OCI runtime|crun:", re.I | re.M)


def _env():
    """Podman needs HOME and OUR user's runtime dir. A caller's shell may hand down another user's
    (e.g. root's /run/user/0 via runuser), so replace an unusable one; never trust the inherited value."""
    e = dict(os.environ)
    uid = os.getuid()
    e["HOME"] = pwd.getpwuid(uid).pw_dir
    rd = f"/run/user/{uid}"
    if os.path.isdir(rd):
        cur = e.get("XDG_RUNTIME_DIR")
        if not cur or not os.access(cur, os.W_OK):
            e["XDG_RUNTIME_DIR"] = rd
        if not e.get("DBUS_SESSION_BUS_ADDRESS") or e["XDG_RUNTIME_DIR"] == rd:
            e["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={rd}/bus"
    return e


def run_python(files, entry, timeout_s=10, memory="128m", pids=32, cpus="1", max_output=65536, env=None):
    """files: {name: str|bytes}. Runs `python3.13 -I /work/<entry>`. Returns dict with rc, stdout,
    stderr, timed_out, truncated, duration_s, limits. Raises SandboxUnavailable on runtime errors."""
    if not available():
        raise SandboxUnavailable("podman or python3.13 missing")
    for n in files:
        if "/" in n or n.startswith("."):
            raise ValueError("bad file name")
    work = tempfile.mkdtemp(prefix="juicr-sbx-")
    name = "juicr-sbx-" + uuid.uuid4().hex[:12]
    try:
        for n, c in files.items():
            with open(os.path.join(work, n), "wb") as f:
                f.write(c if isinstance(c, bytes) else c.encode())
        os.chmod(work, 0o755)
        for n in files:
            os.chmod(os.path.join(work, n), 0o644)
        cmd = ["podman", "run", "--rm", "--name", name, "--network=none", "--cap-drop=all",
               "--security-opt", "no-new-privileges", "--read-only", "--tmpfs", "/tmp:size=16m",
               f"--memory={memory}", f"--memory-swap={memory}", f"--pids-limit={pids}", f"--cpus={cpus}",
               "--timeout", str(int(timeout_s)), "-v", f"{work}:/work:ro",
               "-v", f"{PY}:{PY}:ro", "-v", "/usr/lib/python3.13:/usr/lib/python3.13:ro"]
        le = _libexpat()
        if le:
            cmd += ["-v", f"{le}:/usr/lib/x86_64-linux-gnu/libexpat.so.1:ro"]
        for k, v in (env or {}).items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [IMAGE, PY, "-I", f"/work/{entry}"]
        t0 = time.monotonic()
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=_env()["HOME"], env=_env())
        except OSError as e:
            raise SandboxUnavailable(str(e)) from None
        timed_out = threading.Event()

        def kill():
            timed_out.set()
            subprocess.run(["podman", "rm", "-f", name], capture_output=True, env=_env())
        timer = threading.Timer(timeout_s + 5, kill)
        timer.start()
        try:
            out = p.stdout.read(max_output + 1)
            truncated = len(out) > max_output
            if truncated:
                subprocess.run(["podman", "rm", "-f", name], capture_output=True, env=_env())
                # keep draining (discarding) so conmon/podman are never blocked on a full pipe and can exit
                while p.stdout.read(65536):
                    pass
            err = p.stderr.read(8192)
            p.wait()
        finally:
            timer.cancel()
            if p.poll() is None:
                p.kill()
            for fh in (p.stdout, p.stderr):
                try:
                    fh.close()
                except OSError:
                    pass
            subprocess.run(["podman", "rm", "-f", "--ignore", name], capture_output=True, env=_env())
        text_err = err.decode("utf-8", "replace")
        rc = p.returncode
        dur = time.monotonic() - t0
        # podman --timeout kills the container and exits non-zero (observed 255); require the run to
        # have lasted about the whole allowance so a quick genuine failure is not mislabelled
        to = timed_out.is_set() or (rc in (124, 137, 255) and dur >= timeout_s - 0.5)
        if not to and rc != 0 and (rc in (125, 126, 127) or (not out and _PODMAN_ERR.search(text_err))):
            raise SandboxUnavailable(f"podman rc={rc}: {text_err[-200:]}")
        return {"rc": rc, "stdout": out[:max_output].decode("utf-8", "replace"), "stderr": text_err,
                "timed_out": to, "truncated": truncated, "duration_s": dur,
                "limits": {"timeout_s": timeout_s, "memory": memory, "pids": pids, "cpus": cpus,
                           "network": "none", "caps": "dropped", "rootfs": "read-only"}}
    finally:
        shutil.rmtree(work, ignore_errors=True)
