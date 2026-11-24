"""Duo changes its own code, safely.

A feedback request becomes a proposal. Duo runs a normal session in a git worktree,
checks the diff with the security guard, and waits for you to Approve. Approved
work becomes one commit with a version tag. Roll back reverts that one commit.
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config, notify, personal, security, session

APP = config.APP_DIR
IGNORE = ".duo/\n__pycache__/\n*.pyc\nconfig.json\napp/src-tauri/target/\napp/node_modules/\n.superpowers/\n"
ID = ["-c", "user.name=Duo", "-c", "user.email=duo@localhost"]


def git(cwd, *args, check=True):
    r = subprocess.run(["git", *ID, *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise ValueError("git %s failed: %s" % (args[0], (r.stderr or r.stdout).strip()[:300]))
    return r.stdout.strip()


def ensure_repo():
    if (APP / ".git").exists():
        return
    git(APP, "init", "-q")
    (APP / ".gitignore").write_text(IGNORE)
    git(APP, "add", "-A")
    git(APP, "commit", "-q", "-m", "Duo baseline")
    git(APP, "tag", "v3.0")


def _next_tag():
    return "v3.%d" % len([t for t in git(APP, "tag", "--list", "v3.*").split() if t])


def task_text(p):
    return ("Change Duo: %s\n\n%s\n\nRules for this change:\n"
            "- Edit files only in engine/, web/, rubrics/ and tests/.\n"
            "- Do not edit engine/security.py, rubrics/security.json or tests/test_security.py.\n"
            "- Do not remove any call to the security guard.\n"
            "- Choose assessment methods relevant to the requested change."
            % (p["title"], p["request"]))


def prepare(p):
    ensure_repo()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    wt = config.root() / ".duo-self" / ("%s-%s" % (p["id"], stamp))
    wt.parent.mkdir(parents=True, exist_ok=True)
    base = git(APP, "rev-parse", "HEAD")
    branch = "self/%s-%s" % (p["id"], stamp)
    git(APP, "worktree", "add", "-q", "-b", branch, str(wt), "HEAD")
    return personal.update_proposal(p["id"], status="prepared", worktree=str(wt), branch=branch, base=base)


def run_tests(cwd):
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=cwd, capture_output=True, text=True,
                       timeout=900, env=dict(__import__("os").environ, PYTHONDONTWRITEBYTECODE="1"))
    tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-12:])
    return r.returncode == 0, tail


def finalize(p, verify_passed, runner=None):
    wt, base = Path(p["worktree"]), p["base"]
    git(wt, "reset", "-q", "--soft", base)
    git(wt, "add", "-A")
    changed = [l for l in git(wt, "diff", "--cached", "--name-only", base).splitlines() if l]
    found = security.check_proposal(wt, changed, base)
    numstat = [l.split("\t") for l in git(wt, "diff", "--cached", "--numstat", base).splitlines() if l]
    info = {"files": changed, "findings": [f.to_dict() for f in found],
            "added": sum(int(a) for a, _, _ in numstat if a.isdigit()), "removed": sum(int(r) for _, r, _ in numstat if r.isdigit())}
    deny = [f for f in found if f.action == "deny"]
    for f in found:
        security.log(f, run=p.get("run_id"), source="self-change", action="blocked" if f.action == "deny" else "needs review")
    if deny:
        return personal.update_proposal(p["id"], status="blocked", result="; ".join(f.reason for f in deny), **info)
    if not verify_passed:
        return personal.update_proposal(p["id"], status="failed", result="The agents did not approve the requested output.", **info)
    ok, tail = runner(wt) if runner else (True, "Assessment methods were selected by the agents.")
    if not ok:
        return personal.update_proposal(p["id"], status="failed", result="Tests failed.\n" + tail, **info)
    git(wt, "commit", "-q", "-m", "Duo: %s" % p["title"])
    return personal.update_proposal(p["id"], status="ready", sha=git(wt, "rev-parse", "HEAD"), result="Agent review completed.\n" + tail, **info)


def diff_text(pid):
    p = personal.get_proposal(pid)
    if not p.get("worktree") or not Path(p["worktree"]).exists():
        return ""
    if p.get("sha"):
        return git(p["worktree"], "diff", p["base"], p["sha"], check=False)[:200000]
    return git(p["worktree"], "diff", "--cached", p["base"], check=False)[:200000]


def _cleanup(p):
    if p.get("worktree") and Path(p["worktree"]).exists():
        git(APP, "worktree", "remove", "--force", p["worktree"], check=False)
    if p.get("branch"):
        git(APP, "branch", "-D", p["branch"], check=False)


def approve(pid):
    p = personal.get_proposal(pid)
    if p["status"] != "ready":
        raise ValueError("Only a reviewed change can be approved.")
    if any(f["action"] == "deny" for f in p.get("findings", [])):
        raise ValueError("Security denied this change. It cannot be approved.")
    if git(APP, "status", "--porcelain"):
        raise ValueError("Duo's own folder has local edits. Commit or discard them first.")
    r = subprocess.run(["git", *ID, "cherry-pick", p["sha"]], cwd=APP, capture_output=True, text=True)
    if r.returncode:
        git(APP, "cherry-pick", "--abort", check=False)
        raise ValueError("This change no longer fits the current code. Decline it and ask again.")
    tag = _next_tag()
    git(APP, "tag", tag)
    _cleanup(p)
    return personal.update_proposal(pid, status="approved", tag=tag, applied=git(APP, "rev-parse", "HEAD"),
                                    result="Applied as %s. It loads on next launch." % tag)


def rollback(pid):
    p = personal.get_proposal(pid)
    if p["status"] != "approved":
        raise ValueError("Only an approved change can be rolled back.")
    if git(APP, "status", "--porcelain"):
        raise ValueError("Duo's own folder has local edits. Commit or discard them first.")
    r = subprocess.run(["git", *ID, "revert", "--no-edit", p["applied"]], cwd=APP, capture_output=True, text=True)
    if r.returncode:
        git(APP, "revert", "--abort", check=False)
        raise ValueError("Later changes depend on this one. Roll those back first.")
    tag = _next_tag()
    git(APP, "tag", tag)
    return personal.update_proposal(pid, status="rolled_back", rollback_tag=tag, result="Rolled back as %s. It takes effect on next launch." % tag)


def decline(pid):
    p = personal.get_proposal(pid)
    if p["status"] in ("running", "testing"):
        raise ValueError("Stop the run first.")
    if p["status"] == "approved":
        raise ValueError("Roll it back instead.")
    _cleanup(p)
    return personal.update_proposal(pid, status="declined", result="Declined.")


def retry(pid):
    p = personal.get_proposal(pid)
    if p["status"] not in ("failed", "blocked") or not Path(p.get("worktree", "")).exists():
        raise ValueError("Nothing to retry.")
    personal.update_proposal(pid, status="testing")
    threading.Thread(target=lambda: _safe(p), daemon=True).start()


def _safe(p, verify=True):
    try:
        out = finalize(p, verify)
        if out["status"] == "ready":
            out = approve(p["id"])
        notify.send("Duo", {"ready": "A change to Duo is ready: %s", "approved": "Applied the requested Duo change: %s", "blocked": "Security blocked a change to Duo: %s",
                            "failed": "A change to Duo did not pass: %s"}[out["status"]] % out["title"])
    except Exception as ex:
        personal.update_proposal(p["id"], status="failed", result="Could not check the change: %s" % ex)
    finally:
        session.bump()


def tick(engine):
    """Called by the engine loop (holding the session lock): start queued work, finish done runs."""
    ps = personal.proposals()
    running = next((p for p in ps if p["status"] == "running"), None)
    if running:
        s = engine.get_session(running.get('run_id', ''))
        if s and s.st["status"] == "done":
            personal.update_proposal(running["id"], status="testing")
            threading.Thread(target=lambda: _safe(running, bool(s.st.get("verify_passed"))), daemon=True).start()
        return
    queued = next((p for p in ps if p["status"] == "queued"), None)
    busy = bool(engine.reservations()) if queued else False
    if queued and engine.agents and not busy:
        try:
            p = prepare(queued)
            engine.ensure_room(p['worktree'])
            s = session.Session.create(task_text(p), engine.agents, workspace=p["worktree"], task_type="programming",
                                       title="Duo improvement: %s" % p["title"])
            engine.session = s
            engine.managed()
            personal.update_proposal(p["id"], status="running", run_id=s.st["id"])
        except Exception as ex:
            personal.update_proposal(queued["id"], status="failed", result="Could not start: %s" % ex)
