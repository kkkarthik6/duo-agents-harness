"""Docker sandbox. Agents stay on the Mac; installs, builds and runs happen in one container per run.

The agent writes a Dockerfile and sandbox.json. Duo builds the image, starts the container with fixed
limits (agents never pass docker flags), and checks every prerequisite. A fresh container then runs the
smoke command, so the app must work from the image alone, not from state left behind by earlier installs.
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import config

DOCKER = ["docker"]
NOT_INSTALLED = "Docker is not installed. Install Docker Desktop to use the sandbox."
NOT_RUNNING = "Docker is not running. Start Docker Desktop, then try again."


def _run(args, timeout=120):
    try:
        return subprocess.run(DOCKER + list(args), capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", NOT_INSTALLED)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "Timed out after %d seconds." % timeout)


def tail(text, lines=40, chars=3000):
    out = "\n".join((text or "").strip().splitlines()[-lines:])
    return out[-chars:]


ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")


def strip_ansi(text):
    return ANSI.sub("", text)


def _out(r):
    return tail(strip_ansi((r.stdout or "") + "\n" + (r.stderr or "")))


def available():
    r = _run(["info"], timeout=20)
    if r.returncode == 0:
        return True, ""
    return False, NOT_INSTALLED if r.returncode == 127 else NOT_RUNNING


def name(sid):
    return "duo-%s" % sid.lower()


def load_spec(d):
    try:
        return json.loads((Path(d) / "sandbox.json").read_text())
    except (OSError, ValueError):
        return {}


def check_spec(d, review=False):
    d, out = Path(d), []
    df = d / "Dockerfile"
    if not df.exists():
        out.append("sandbox/Dockerfile is missing")
    elif not re.search(r"^\s*FROM\s+\S+", df.read_text(errors="replace"), re.M | re.I):
        out.append("Dockerfile has no FROM line")
    js = d / "sandbox.json"
    if not js.exists():
        out.append("sandbox/sandbox.json is missing")
    else:
        try:
            spec = json.loads(js.read_text())
            if not isinstance(spec, dict):
                raise ValueError
        except ValueError:
            spec = None
            out.append("sandbox/sandbox.json is not valid JSON")
        if spec is not None:
            tools = spec.get("tools")
            if not (isinstance(tools, list) and tools and all(isinstance(t, str) and t.strip() for t in tools)):
                out.append("sandbox.json needs a tools list with one version command for each prerequisite")
            for key in ("run", "smoke"):
                if not (isinstance(spec.get(key), str) and spec[key].strip()):
                    out.append("sandbox.json needs a %s command" % key)
            ports = spec.get("ports", [])
            if not (isinstance(ports, list) and all(isinstance(p, int) and not isinstance(p, bool) and 1024 <= p <= 65535 for p in ports)):
                out.append("sandbox.json ports must be numbers from 1024 to 65535")
    pre = d / "prereqs.md"
    if not pre.exists() or len([l for l in pre.read_text(errors="replace").splitlines() if l.strip()]) < 3:
        out.append("sandbox/prereqs.md needs one line for each prerequisite")
    if review and not (d / "review.md").exists():
        out.append("sandbox/review.md is missing")
    return out


def dockerfile_hash(d):
    f = Path(d) / "Dockerfile"
    return hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else ""


def build_image(d, sid):
    d = Path(d)
    r = _run(["build", "-t", name(sid), "-f", str(d / "Dockerfile"), str(d)], timeout=config.CFG["sandbox_build_timeout"])
    return r.returncode == 0, _out(r)


def _limits():
    return ["--cpus", str(config.CFG["sandbox_cpus"]), "--memory", str(config.CFG["sandbox_memory"]),
            "--pids-limit", "1024", "--security-opt", "no-new-privileges"]


def start_container(ws, sid, ports):
    _run(["rm", "-f", name(sid)], timeout=60)
    args = ["run", "-d", "--name", name(sid), "--label", "duo=1"] + _limits() + ["-v", "%s:/work" % ws, "-w", "/work"]
    for p in ports or []:
        args += ["-p", "127.0.0.1:%d:%d" % (p, p)]
    r = _run(args + [name(sid), "sleep", "infinity"], timeout=120)
    return r.returncode == 0, _out(r)


def write_wrapper(ws, sid):
    w = Path(ws) / ".duo" / "sbx"
    w.parent.mkdir(parents=True, exist_ok=True)
    w.write_text("#!/bin/sh\n# Duo: run a command in this run's container. Example: ./.duo/sbx 'pip install flask'\n"
                 'exec docker exec -i -w /work %s sh -lc "$*"\n' % name(sid))
    w.chmod(0o755)
    return w


def check_tools(sid, tools):
    for t in tools or []:
        r = _run(["exec", name(sid), "sh", "-lc", t], timeout=60)
        if r.returncode != 0:
            return False, "Prerequisite check failed: %s\n%s" % (t, _out(r))
    return True, ""


def smoke(ws, sid, spec):
    args = ["run", "--rm"] + _limits() + ["-v", "%s:/work" % ws, "-w", "/work", name(sid), "sh", "-lc", spec.get("smoke", "false")]
    r = _run(args, timeout=240)
    return r.returncode == 0, _out(r)


def exists(sid):
    """True when this run's container is still there (it is removed by quit, stop and some restarts)."""
    r = _run(["ps", "-a", "-q", "--filter", "name=^%s$" % name(sid)], timeout=30)
    return r.returncode == 0 and bool(r.stdout.strip())


def stop(sid):
    _run(["rm", "-f", name(sid)], timeout=60)


def stop_all():
    r = _run(["ps", "-aq", "--filter", "label=duo=1"], timeout=30)
    ids = r.stdout.split() if r.returncode == 0 else []
    if ids:
        _run(["rm", "-f"] + ids, timeout=120)
    return len(ids)


def serve(sid, spec):
    """Start the app in the run's container, in the background, so a screenshot can be taken. Returns (ok, log)."""
    cmd = "(%s) > /tmp/duo-app.log 2>&1 & echo $! > /tmp/duo-app.pid" % spec.get("run", "true")
    r = _run(["exec", "-w", "/work", name(sid), "sh", "-lc", cmd], timeout=60)
    return r.returncode == 0, _out(r)


def unserve(sid):
    """Stop the app started by serve(). Failure is ignored: the container is removed at quit anyway."""
    _run(["exec", name(sid), "sh", "-lc", "p=$(cat /tmp/duo-app.pid 2>/dev/null); kill $p 2>/dev/null; pkill -P $p 2>/dev/null; true"], timeout=30)
