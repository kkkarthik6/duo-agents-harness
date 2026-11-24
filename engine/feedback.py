"""Feedback flow: Send makes a version clone of the user layer, writes feedback_reference.json, and starts a
detached worker that edits the clone and checks it. The worker outlives the engine, so a quit does not stop it.
At the next launch the page reads the reference file and shows "Version 3.1.0 is ready".

feedback_reference.json (in ~/Duo):
  {"request": text, "kind": "major|minor", "from_version": "3.0.0", "version": "c4", "number": "3.1.0",
   "path": ".../versions/c4", "status": "editing|repairing|ready|failed|stuck|blocked|no_change|waiting_for_core",
   "started": iso, "finished": iso|null, "detail": str}

"version" is the clone id (c4). "number" is the name people see (3.1.0).
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import clone_agent, config, userspace

REF_NAME = "feedback_reference.json"
LOG_NAME = "feedback_worker.log"
KINDS = userspace.KINDS


def ref_path(base=None):
    return userspace.home(base) / REF_NAME


def read(base=None):
    p = ref_path(base)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except ValueError:
        return None


def _write(base, data):
    userspace._write(ref_path(base), data)


def _now():
    return datetime.now().isoformat(timespec="seconds")


def submit(text, base=None, spawn=True, kind="minor", auto_fix=False, feature=None):
    """Clone the user layer and start the worker. kind is "major" or "minor" and sets the clone's number."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Write the feedback first.")
    if kind not in KINDS:
        raise ValueError("Choose major or minor.")
    base = base or config.DUO_HOME
    data = userspace.migrate_legacy(base)
    v = userspace.clone(base, text, kind, auto_fix=auto_fix, feature=feature)
    rec = {"request": text, "kind": kind, "from_version": data["number"], "version": v["id"], "number": v["number"],
           "path": v["path"], "status": "editing", "started": _now(), "finished": None,
           "detail": "Duo is editing a copy.", "auto_fix": bool(auto_fix)}
    if not auto_fix:                       # an auto fix must not hide the user's own request from the page
        _write(base, rec)
    if spawn:
        spawn_worker(v["id"], base)
    return rec


def spawn_worker(vid, base=None, mode="edit"):
    """Start the worker as its own process group, so it survives the engine's exit."""
    base = base or config.DUO_HOME
    userspace.home(base).mkdir(parents=True, exist_ok=True)
    log = open(userspace.home(base) / LOG_NAME, "ab")
    env = dict(os.environ, DUO_HOME=str(base), PYTHONPATH=str(config.APP_DIR))
    args = [sys.executable, "-m", "engine.feedback", vid] + (["repair"] if mode == "repair" else [])
    return subprocess.Popen(args, cwd=str(config.APP_DIR), env=env,
                            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)


def repair(vid, base=None, spawn=True):
    """Press Repair: up to five more repairs on a version that failed or got stuck."""
    base = base or config.DUO_HOME
    v = userspace.entry(base, vid)
    if v.get("status") not in ("failed", "stuck"):
        raise ValueError("Only a version that failed or got stuck can be repaired.")
    rec = read(base) or {}
    if rec.get("version") == vid:
        rec.update(status="repairing", detail="Repairing: starting.", finished=None)
        _write(base, rec)
    if spawn:
        spawn_worker(vid, base, mode="repair")
    return rec


def work(vid, base=None, mode="edit", after=None):
    """The worker body: edit the clone, check it, repair up to five times, record the outcome.
    Runs in its own process. `after(status)` runs at the end (auto fixes roll up there)."""
    base = base or config.DUO_HOME
    rec = read(base) or {}
    try:
        from . import review as reviewmod, vshots
        shoot = lambda clone, changed: vshots.take(clone, changed, base)
        if mode == "repair":
            status = clone_agent.repair(vid, base, shoot=shoot, review=reviewmod.codex_review)
        else:
            status = clone_agent.run(vid, userspace.entry(base, vid).get("feedback", ""), base,
                                     shoot=shoot, review=reviewmod.codex_review)
        if status == "ready" and not userspace.entry(base, vid).get("auto_fix"):
            number = userspace.roll_up(base, vid, auto=True)
            status = "rolled_up"
            userspace.update(base, vid, detail="Applied requested feature change as %s." % number)
        detail = userspace.entry(base, vid).get("detail", "")
    except Exception as ex:
        status, detail = "failed", "Duo stopped while editing: %s" % ex
        userspace.update(base, vid, status=status, detail=detail)
    if rec.get("version") == vid:
        rec.update(status=status, detail=detail, number=userspace.entry(base, vid)["number"], finished=_now())
        _write(base, rec)
    v = userspace.entry(base, vid)
    if v.get("auto_fix"):
        from . import coremgr
        coremgr.finish_fix(vid, status, base)
    if after:
        after(status)
    return status


def notice(base=None):
    """What the page shows at launch: the ready version, a failure, a core wait, or nothing."""
    rec = read(base)
    if not rec:
        return None
    try:
        v = userspace.entry(base, rec["version"])
    except ValueError:
        return None
    num, status = v["number"], v.get("status", rec.get("status"))
    if status == "rolled_up":
        return {"kind": "applied", "version": num, "id": v["id"], "text": "Applied your requested feature change."}
    if status == "ready":
        where = "; ".join(v.get("where") or [])
        return {"kind": "ready", "version": num, "id": v["id"],
                "text": "Version %s is ready. It has your change: %s.%s Open Feedback: press Use this version to try it, then Roll up."
                        % (num, rec["request"][:120], (" Where to see it: %s." % where) if where else "")}
    if status in ("failed", "stuck", "blocked", "no_change"):
        return {"kind": status, "version": num, "id": v["id"],
                "text": "Version %s did not pass: %s" % (num, (v.get("detail") or "")[:200])}
    if status == "waiting_for_core":
        return {"kind": status, "version": num, "id": v["id"],
                "text": "Version %s needs a core change. Open Feedback to copy the request." % num}
    if status in ("editing", "repairing"):
        return {"kind": "editing", "version": num, "id": v["id"], "text": "Duo is still editing version %s." % num}
    return None


if __name__ == "__main__":
    sys.exit(0 if work(sys.argv[1], mode=sys.argv[2] if len(sys.argv) > 2 else "edit") in ("ready", "rolled_up") else 1)
