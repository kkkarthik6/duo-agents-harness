"""Apply feedback to a version clone of the user layer, then wait for you to roll it up or reject it.

An agent edits ~/Duo/versions/<id>/, a copy of ~/Duo/user/. It may change only features/, instructions.md
and contract.json. Core is never in the copy, so the agent cannot change it. Duo checks the changed files,
checks each changed feature (contract, imports, own tests), and stores a diff. If the feature needs something
core.api does not offer, the agent writes core_request.json: the version waits for a core update and the
request is saved in ~/Duo/core-requests/ for upstream.
"""
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config, features, userspace

MAX_REPAIRS = 5          # repair tries per run; the page's Repair button starts another run of up to five
STALL_LIMIT = 2          # this many repairs in a row without fewer failing tests counts as no progress
TAIL_LINES = 60

ALLOWED_DIRS = ("features/",)
ALLOWED_FILES = ("instructions.md", "contract.json")
REQUEST = "core_request.json"
REF = "_core_ref"         # a read-only copy of core.api and the example feature, so the agent can read them in its folder
SKIPPED = ("__pycache__", ".DS_Store")
TEXT_SUFFIXES = (".py", ".html", ".json", ".md", ".txt", ".css", ".js")


def _skipped(rel):
    return (any(part in SKIPPED for part in rel.parts) or rel.suffix == ".pyc" or str(rel) == REQUEST
            or (rel.parts and rel.parts[0] == REF))


def stage_ref(clone, core_dir=None):
    """Put a copy of engine/coreapi.py and the example feature in <clone>/_core_ref/. Agents that may read
    only their own folder (claude -p) can then read the API. Duo removes the copy before checks and roll-up."""
    import shutil
    core_dir = Path(core_dir or config.APP_DIR)
    ref = Path(clone) / REF
    if ref.exists():
        shutil.rmtree(ref)
    ref.mkdir(parents=True)
    api = core_dir / "engine" / "coreapi.py"
    if api.exists():
        shutil.copy2(api, ref / "coreapi.py")
    ex = core_dir / "tests" / "fixtures" / "feature_export"
    if ex.is_dir():
        shutil.copytree(ex, ref / "example", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (ref / "README.md").write_text("A read-only copy of Duo's core API and an example feature. Read it; do not edit it.\n"
                                   "Duo deletes this folder after you finish. Changes here are ignored.\n")
    return ref


STATUS = re.compile(r"^\s*DUO-STATUS:\s*(done|blocked)\s*(?::\s*(.*))?$", re.I | re.M)


def agent_status(log):
    """The agent's last DUO-STATUS line: ("done", "") or ("blocked", reason), else (None, "")."""
    found = STATUS.findall(log or "")
    if not found:
        return None, ""
    word, reason = found[-1]
    return word.lower(), (reason or "").strip()


def _last_words(log, n=600):
    text = re.sub(r"\s+", " ", STATUS.sub("", log or "")).strip()
    return text[-n:]


def preflight(clone, agent):
    """Problems that stop the agent before it starts: no copy, a copy Duo cannot write, no core reference,
    or no agent command."""
    clone = Path(clone)
    probs = []
    if not clone.is_dir():
        return ["The version copy is missing: %s." % clone]
    probe = clone / ".duo-write-check"
    try:
        probe.write_text("x")
        probe.unlink()
    except OSError as ex:
        probs.append("Duo cannot write in the version copy: %s." % ex)
    if not (clone / REF / "coreapi.py").is_file():
        probs.append("The core reference _core_ref/coreapi.py is missing from the copy.")
    if agent is claude_agent and not shutil.which("claude"):
        probs.append("The claude command is not on the PATH, so the feedback agent cannot run.")
    return probs


def drop_ref(clone):
    import shutil
    ref = Path(clone) / REF
    if ref.exists():
        shutil.rmtree(ref, ignore_errors=True)


def _files(root):
    out = {}
    for p in Path(root).rglob("*"):
        if p.is_file() and not _skipped(p.relative_to(root)):
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


def changes(origin, clone):
    """Changed paths (added, removed or edited) between the source copy and the agent's copy."""
    a, b = _files(origin), _files(clone)
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def violations(paths):
    return [p for p in paths if not (p.startswith(ALLOWED_DIRS) or p in ALLOWED_FILES)]


def diff_text(origin, clone, paths):
    out = []
    for rel in paths:
        a = Path(origin, rel)
        b = Path(clone, rel)
        if rel.endswith(TEXT_SUFFIXES):
            old = a.read_text(errors="replace").splitlines(True) if a.exists() else []
            new = b.read_text(errors="replace").splitlines(True) if b.exists() else []
            out.extend(difflib.unified_diff(old, new, "user/" + rel, "clone/" + rel))
        else:
            out.append("Binary or other file changed: %s\n" % rel)
    return "".join(out)


def prompt(feedback, core_dir=None):
    core_dir = core_dir or config.APP_DIR
    return (
        "Change Duo to do this: %s\n\n"
        "You are in a copy of the user's Duo features. Duo's core is sealed and is not in this folder.\n"
        "Read these first (a read-only copy inside this folder): _core_ref/coreapi.py is Duo's public API, with its "
        "contract history at the top; _core_ref/example/ is a complete example feature with tests. "
        "Core itself is at %s. Do not edit _core_ref/: Duo deletes it when you finish.\n"
        "For past sessions and runs use ctx.sessions(), ctx.session(id), ctx.read_session_file(id, path) and "
        "ctx.write_session(id, path, text) (contract 1.1.0).\n"
        "UI (contract 1.2.0): a whole new screen is api.page(id, title, 'ui/x.js'), with its own side bar button. "
        "Controls (contract 1.3.0): declare scalar feature settings with api.setting(key, default, label=..., "
        "description=..., choices=..., minimum=..., maximum=...). Duo generates controls on the Features screen. "
        "Read values with req['ctx'].setting(key) or ctx.setting(key). Use api.slot for panels and api.on, "
        "api.stage, and api.gate for workflow extensions. Features expose their own settings; core guards remain fixed. "
        "A small panel inside a core screen is api.slot(slot, 'ui/x.js'). Do not squeeze a new screen into a slot.\n\n"
        "Before you write code, list each part you add (pages, panels, routes, stages, gates, rules) and the contract "
        "method for it, in features/<name>/PLAN.md, one line each: '- <part>: <contract method>'. If a part has no exact "
        "contract method, write core_request.json instead of making it fit.\n"
        "In feature.json, add \"where_to_see\": where the user finds the change, for example \"Side bar: Sessions\".\n\n"
        "Rules:\n"
        "- Edit files only in features/, instructions.md and contract.json.\n"
        "- A feature is features/<name>/ with __init__.py, feature.json {\"name\", \"version\", \"contract\": \">=1.0\", "
        "\"description\"}, and feature.py with register(api).\n"
        "- Import only the standard library, your feature's own modules and engine.coreapi. Never import other engine modules.\n"
        "- Add and extend only: listen to events, add routes, add UI panels in slots, add rules, add new Stage or Gate subclasses. "
        "Never replace a core stage, gate, prompt or default.\n"
        "- If core.api does not offer what the feature needs, do not work around it. Write core_request.json with "
        "{\"missing\": what is missing, \"why\": why the feature needs it, \"feature\": the feature name}, and stop.\n"
        "- Keep the change small.\n"
        "- Choose checks relevant to the requested change.\n"
        "- End your last message with one line: DUO-STATUS: done, or DUO-STATUS: blocked: <cause>.\n"
    ) % (feedback, core_dir)


def repair_prompt(feedback, tail):
    return (
        "Duo checked your change and it is not ready. Fix what the output below names: failing tests, missing "
        "PLAN.md or where_to_see, or the reviewer's notes. Keep the rest of the change.\n\n"
        "Original request: %s\n\nOutput (last lines):\n%s\n\n"
        "The same rules apply. Choose checks relevant to the requested change.\n"
        "End your last message with one line: DUO-STATUS: done, or DUO-STATUS: blocked: <why>.\n"
    ) % (feedback, tail)


COUNT = re.compile(r"(failures|errors)=(\d+)")
ERROR_LINE = re.compile(r"^(ERROR:|FAIL:|\w*Error\b|\w*Exception\b)")


def failure_count(tail):
    """Failing tests (failures + errors) from unittest's summary line, or None when it is not there."""
    found = COUNT.findall(tail or "")
    return sum(int(n) for _, n in found) if found else None


def signature(tail):
    """The set of error headers in the output. The same signature twice means the repair changed nothing that matters."""
    lines = {l.strip() for l in (tail or "").splitlines() if ERROR_LINE.match(l.strip())}
    return "\n".join(sorted(lines))


def claude_agent(cwd, text):
    """Run Claude Code once, headless, in the copy. Returns (ok, log tail)."""
    env = dict(os.environ, DISABLE_AUTOUPDATER="1")
    try:
        from . import agents
        cmd = agents.guarded_argv("claude", ["claude", "-p", text, "--permission-mode", "acceptEdits"], cwd)
        r = subprocess.run(cmd, cwd=str(cwd),
                           capture_output=True, text=True, timeout=1800, env=env)
    except (OSError, subprocess.TimeoutExpired) as ex:
        return False, "The agent did not finish: %s" % ex
    return r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-2000:]


def check_clone(clone, core_dir, changed):
    """Check each changed feature: its structural contract and imports against core_dir.
    Returns (ok, output) in unittest's style, so the repair loop can count failures."""
    names = sorted({p.split("/")[1] for p in changed if p.startswith("features/") and p.count("/") >= 2})
    errors, tails = [], []
    present = {e["name"]: e for e in features.discover(clone)}
    to_test = []
    for n in names:
        e = present.get(n)
        if e is None:
            if Path(clone, "features", n).exists():
                errors.append("ERROR: %s: feature.json is missing." % n)
            continue                              # a removed feature needs no tests
        for prob in features.check(e):
            errors.append("ERROR: %s: %s" % (n, prob))
        plan = e["dir"] / "PLAN.md"
        if not plan.is_file() or not plan.read_text(errors="replace").strip():
            errors.append("ERROR: %s: PLAN.md is missing. List each part and its contract method in features/%s/PLAN.md." % (n, n))
        if not str(e["manifest"].get("where_to_see") or "").strip():
            errors.append("ERROR: %s: feature.json has no where_to_see. Say where the user finds the change." % n)
    if not errors and not tails:
        return True, "OK"
    count = len(errors) + sum(failure_count(t) or 1 for t in tails)
    out = "\n".join(errors + tails) + "\nFAILED (errors=%d)" % count
    return False, "\n".join(out.splitlines()[-TAIL_LINES * 2:])


def run_tests(cwd, changed):
    return check_clone(cwd, config.APP_DIR, changed)


def _origin(base, v):
    """The user layer this clone was made from: the current one, or the kept copy after a roll-up."""
    data = userspace.load(base)
    if v.get("base") != data["number"]:
        kept = userspace.history_dir(base) / str(v.get("base"))
        if kept.exists():
            return kept
    return userspace.user_dir(base)


def _core_request(vid, base, clone, v):
    raw = Path(clone, REQUEST).read_text(errors="replace")
    try:
        req = json.loads(raw)
        if not isinstance(req, dict):
            raise ValueError
    except ValueError:
        req = {"missing": raw[:2000], "why": "", "feature": ""}
    req.update(version=v["number"], version_id=vid, feedback=v.get("feedback", ""), core=_core_version())
    d = userspace.home(base) / "core-requests"
    d.mkdir(parents=True, exist_ok=True)
    (d / (vid + ".json")).write_text(json.dumps(req, indent=1) + "\n")
    return req


def _where(clone, paths):
    """where_to_see of each changed feature, for the ready notice."""
    out = []
    for n in sorted({p.split("/")[1] for p in paths if p.startswith("features/") and p.count("/") >= 2}):
        f = Path(clone, "features", n, "feature.json")
        try:
            w = str(json.loads(f.read_text()).get("where_to_see") or "").strip()
        except (OSError, ValueError):
            w = ""
        if w:
            out.append(w)
    return out


def _core_version():
    p = Path(config.APP_DIR) / "CORE_VERSION"
    return p.read_text().strip() if p.exists() else "unknown"


def _finish(vid, base, origin, clone, paths, status, detail, log, **extra):
    drop_ref(clone)
    diff_file = userspace.versions_dir(base) / (vid + ".diff")
    diff_file.parent.mkdir(parents=True, exist_ok=True)
    diff_file.write_text(diff_text(origin, clone, paths))
    userspace.update(base, vid, status=status, detail=detail, changed=paths, agent_log=log,
                     diff=str(diff_file), **extra)
    return status


def _repair(vid, feedback, base, agent, tests, origin, clone, log, rounds, last_sig=None, last_count=None,
            shoot=None, review=None):
    """Check the copy, then repair it up to `rounds` times. Stops early on the same error twice or no progress."""
    repairs, stalls, sig, count = 0, 0, last_sig, last_count
    while True:
        if Path(clone, REQUEST).exists():
            req = _core_request(vid, base, clone, userspace.entry(base, vid))
            return _finish(vid, base, origin, clone, changes(origin, clone), "waiting_for_core",
                           "This feature needs a core change: %s. Duo saved the request for upstream. "
                           "Duo tries again after a core update adds it." % str(req.get("missing", ""))[:300], log)
        paths = changes(origin, clone)
        bad = violations(paths)
        if not paths:
            word, why = agent_status(log)
            if word == "blocked":
                return _finish(vid, base, origin, clone, paths, "blocked",
                               "The agent could not make the change: %s" % (why or _last_words(log)), log)
            last = _last_words(log)
            return _finish(vid, base, origin, clone, paths, "no_change",
                           "The agent made no change." + (" Its last message: %s" % last if last else ""), log)
        if bad:
            return _finish(vid, base, origin, clone, paths, "blocked",
                           "The agent changed files it may not change: %s" % ", ".join(bad), log)
        tests_ok, tail = tests(clone, paths)
        if tests_ok:
            pics, note = shoot(clone, paths) if shoot else ([], "")
            if pics or note:
                userspace.update(base, vid, shots=pics, shots_note=note)
            verdict = {"result": "skipped", "notes": "No review ran."}
            if review:
                userspace.update(base, vid, status="reviewing", detail="A second provider is reviewing the change.")
                rok, notes = review(clone, feedback, diff_text(origin, clone, paths), pics)
                if rok is False:
                    tests_ok = False
                    tail = "FAIL: review by a second provider\n%s\nFAILED (failures=1)" % notes
                verdict = {"result": "approved" if rok else ("changes" if rok is False else "skipped"), "notes": notes}
            if tests_ok:
                where = _where(clone, paths)
                detail = "Tests passed." + (" Review: %s." % verdict["result"]) + (" Where to see it: %s." % "; ".join(where) if where else "")
                return _finish(vid, base, origin, clone, paths, "ready", detail, log, repairs=repairs, test_tail="",
                               last_sig="", last_count=0, review=verdict, where=where)
            userspace.update(base, vid, review=verdict)
        now_sig, now_count = signature(tail), failure_count(tail)
        if now_count is not None and count is not None and now_count >= count:
            stalls += 1
        else:
            stalls = 0
        if now_sig and now_sig == sig:
            reason = "The same error came back after a repair."
        elif stalls >= STALL_LIMIT:
            reason = "No progress: %d repairs in a row left %d failing tests." % (stalls, now_count)
        else:
            reason = None
        if reason or repairs >= rounds:
            if reason:
                status, detail = "stuck", "Duo stopped repairing. " + reason
            else:
                status = "failed"
                detail = "Still failing after %d repairs. Press Repair to try %d more." % (repairs, rounds)
            detail += "\n" + "\n".join(tail.splitlines()[-12:])
            return _finish(vid, base, origin, clone, paths, status, detail, log, repairs=repairs,
                           test_tail=tail[-4000:], last_sig=now_sig, last_count=now_count)
        repairs += 1
        userspace.update(base, vid, status="repairing", detail="Repair %d of %d: fixing what the checks found." % (repairs, rounds),
                         repairs=repairs)
        sig, count = now_sig, now_count
        ok, log = agent(clone, repair_prompt(feedback, tail))
        if not ok:
            return _finish(vid, base, origin, clone, changes(origin, clone), "failed",
                           "The agent stopped with an error during a repair.", log, repairs=repairs)


def _blocked_before(vid, base, origin, clone, probs):
    return _finish(vid, base, origin, clone, [], "blocked", "Duo could not start the agent. " + " ".join(probs), "")


def run(vid, feedback, base=None, agent=claude_agent, tests=run_tests, rounds=MAX_REPAIRS, shoot=None, review=None):
    """Edit the clone <vid> from the feedback text, then check and repair it. Never changes the user layer.
    shoot(clone, changed) -> (pngs, note) and review(clone, feedback, diff, pngs) -> (True|False|None, notes)
    run after the tests pass; the worker passes the real ones (vshots.take, review.codex_review)."""
    v = userspace.entry(base, vid)
    origin, clone = _origin(base, v), Path(v["path"])
    userspace.update(base, vid, status="editing", repairs=0)
    if not clone.is_dir():
        return _blocked_before(vid, base, origin, clone, preflight(clone, agent))
    stage_ref(clone)
    probs = preflight(clone, agent)
    if probs:
        return _blocked_before(vid, base, origin, clone, probs)
    ok, log = agent(clone, prompt(feedback))
    if not ok:
        return _finish(vid, base, origin, clone, [], "failed", "The agent stopped with an error. " + _last_words(log, 400), log)
    return _repair(vid, feedback, base, agent, tests, origin, clone, log, rounds, shoot=shoot, review=review)


def repair(vid, base=None, agent=claude_agent, tests=run_tests, rounds=MAX_REPAIRS, shoot=None, review=None):
    """Another run of up to `rounds` repairs on a version that failed or got stuck. Keeps its changes.
    The version takes the next free minor number: 3.1.0 becomes 3.1.1."""
    v = userspace.entry(base, vid)
    if v.get("status") not in ("failed", "stuck"):
        raise ValueError("Only a version that failed or got stuck can be repaired.")
    origin, clone = _origin(base, v), Path(v["path"])
    userspace.update(base, vid, status="repairing", detail="Repairing: starting.", repairs=0,
                     number=userspace.repair_number(base, vid))
    if not clone.is_dir():
        return _blocked_before(vid, base, origin, clone, preflight(clone, agent))
    stage_ref(clone)
    probs = preflight(clone, agent)
    if probs:
        return _blocked_before(vid, base, origin, clone, probs)
    return _repair(vid, v.get("feedback", ""), base, agent, tests, origin, clone, v.get("agent_log", ""), rounds,
                   last_sig=v.get("last_sig"), last_count=v.get("last_count"), shoot=shoot, review=review)


def accept(vid, base=None):
    """Roll up: the ready clone becomes your Duo. Returns the new user number."""
    return userspace.roll_up(base, vid)


def reject(vid, base=None):
    userspace.entry(base, vid)
    userspace.update(base, vid, status="rejected")
