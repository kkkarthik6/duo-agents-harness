"""Harness rules: turn outcomes, blocker codes, preflight, loop flags, approval classes,
budgets, message intent, checkpoints and rollback. Pure where it can be; file work is explicit."""
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

OUTCOMES = ("progress", "blocked", "answered", "needs_human")
BLOCKERS = {
    "docker_unreachable": ("Docker is unreachable from the run container.", "Start Docker Desktop on the Mac, then resume."),
    "container_missing": ("The run container is missing and could not be started.", "Check that Docker is running, then resume. Duo starts the container again."),
    "approval_needed": ("An approval is needed before this work can continue.", "Answer the approval in the chat, then resume."),
    "data_missing": ("Required input data is missing.", "Check the named missing input and failed routes before retrying."),
    "evidence_missing": ("Required evidence is unavailable.", "Provide the named evidence or approve a plan change. A download retry alone may not help."),
    "tool_missing": ("A required tool is not installed.", "Install the tool, or tell Duo a substitute, then resume."),
    "network_access": ("The worker reported a network-access failure.", "Read the exact tool error. A native permission refusal is approval_needed, not a failed connection."),
    "credentials": ("A required authenticated service is unavailable.", "Use the provider sign-in or secret store, then resume."),
    "no_outcome": ("The turn did not state an outcome and changed no files.", "Tell Duo what to do next, or resume."),
}
# Blockers that stop the run until the machine is fixed. Other blockers only stop counting toward the guard.
PAUSE_BLOCKERS = ("docker_unreachable", "container_missing")
STALL_LIMIT = 2
LOOP_ANSWER_STREAK = 3
LOOP_REPEAT_LIMIT = 3       # identical replies from one agent in a row before the run stops
APPROVAL_REPEAT_LIMIT = 3
CHECKPOINT_CAP = 200 * 1024 * 1024
CHECKPOINT_KEEP = 20
COPY_LIMIT = 1024 * 1024
EXCLUDE_DIRS = {".duo", "duo", "data", ".cache", ".venv", "venv", "node_modules", "__pycache__", ".git", ".pytest_cache", ".ruff_cache"}
QUESTION_START = ("why", "what", "how", "when", "where", "who", "is ", "are ", "did ", "does ", "can you tell", "could you tell")


def classify(outcome, blocker, changed, has_log):
    """Return (outcome, blocker_code) for one agent turn. Progress needs evidence: a changed file or a log."""
    if outcome == "blocked":
        if blocker in BLOCKERS:
            return "blocked", blocker
        text = str(blocker or "").lower()
        if any(term in text for term in ("requires approval", "permission denied", "permission layer", "permissions to use", "approval needed")):
            return "blocked", "approval_needed"
        if any(term in text for term in ("network", "dns", "outbound", "huggingface.co", "raw.githubusercontent.com")):
            return "blocked", "network_access"
        if any(term in text for term in ("credential", "api key", "authentication")):
            return "blocked", "credentials"
        if any(term in text for term in ("not installed", "modulenotfounderror", "tool missing")):
            return "blocked", "tool_missing"
        return "blocked", "no_outcome"
    if outcome in ("answered", "needs_human"):
        return outcome, None
    if outcome == "progress":
        if changed or has_log:
            return "progress", None
        return "blocked", "no_outcome"
    # No outcome line: the old rule. Changed files mean progress; nothing changed means no outcome.
    if changed or has_log:
        return "progress", None
    return "blocked", "no_outcome"


def native_blocker(blocker, detail):
    """Keep a recorded native permission refusal separate from connectivity failures."""
    text = str(detail or "").lower()
    refusal = any(term in text for term in ("this command requires approval", "requested permissions to use", "denied by permission layer", "denied by the permission layer"))
    return "approval_needed" if blocker == "network_access" and refusal else blocker


def next_stall(stall, outcome, signal):
    """Stall count after a turn. Answered, blocked and needs_human turns neither add nor reset."""
    if outcome in ("answered", "blocked", "needs_human"):
        return stall
    return 0 if signal else stall + 1


def preflight(sandbox_on, docker_up, container_up):
    """Return the blocker code that stops a turn before it is issued, or None."""
    if not sandbox_on:
        return None
    if not docker_up:
        return "docker_unreachable"
    if not container_up:
        return "container_missing"
    return None


def fingerprint(text):
    norm = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def approval_key(category, question):
    last = (question or "").splitlines()[-1][:120] if question else ""
    return "%s|%s" % (category, last)


def intent(text):
    t = (text or "").strip().lower()
    return "question" if t.endswith("?") or t.startswith(QUESTION_START) else "instruction"


def budget(state):
    """Optional limit on observed active run time. Creation dates are not used."""
    from . import config
    hours = max(0.0, state.get("active_runtime_seconds", 0.0) / 3600)
    if not config.CFG.get("active_time_limit_enabled", False):
        return "ok", hours
    limit = config.CFG.get("active_time_limit_hours", 6.0)
    if hours >= limit:
        return "over", hours
    if hours >= limit * 5 / 6:
        return "warn", hours
    return "ok", hours


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _project_files(ws):
    ws = Path(ws)
    for root, dirs, files in os.walk(ws):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if f in ("task.md", "summary.md") and (Path(root) / ".duo/task-documents.json").is_file():
                continue
            # Broker handoffs are records, not new implementation progress.
            rel = (Path(root) / f).relative_to(ws)
            if rel.parts[0] == "output" and (f in ("handoffs.json", "handoffs.md") or (f == "contents.md" and (Path(root) / f).read_text(errors="replace").startswith("<!-- Duo outcome index -->")) or
                    re.search(r"-\d{4}-agent-[a-f0-9]+(?:-fix-[a-f0-9]+)?\.md$", f)):
                continue
            yield ws / Path(root).relative_to(ws) / f


def progress_state(ws):
    """Measure product changes without copying datasets or reading large data files each turn."""
    ws = Path(ws)
    files = list(_project_files(ws))
    data = ws / "data"
    if data.is_dir():
        for root, dirs, names in os.walk(data):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            files.extend(Path(root) / name for name in names)
    state = {}
    for p in files:
        try:
            st = p.stat()
            digest = ["stat", st.st_mtime_ns] if p.is_relative_to(data) and st.st_size > COPY_LIMIT else _sha(p)
            state[p.relative_to(ws).as_posix()] = ["M", digest, st.st_size]
        except OSError:
            continue
    return state


def snapshot(ws, dest, cap=CHECKPOINT_CAP):
    """Copy every small project file into dest and write a manifest of hashes. Over the cap, files are listed, not copied."""
    ws, dest = Path(ws), Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "files").mkdir(parents=True)
    manifest, skipped, total = {}, [], 0
    for p in _project_files(ws):
        rel = p.relative_to(ws).as_posix()
        try:
            size = p.stat().st_size
            manifest[rel] = _sha(p)
        except OSError:
            continue
        if size > COPY_LIMIT or total + size > cap:
            skipped.append(rel)
            continue
        target = dest / "files" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        total += size
    (dest / "manifest.json").write_text(json.dumps({"files": manifest, "skipped": skipped, "bytes": total}, indent=1))
    return {"files": len(manifest), "bytes": total, "skipped": len(skipped)}


def restore(ws, src):
    """Put back every checkpointed file that differs now. Files created after the checkpoint are kept and reported."""
    ws, src = Path(ws), Path(src)
    data = json.loads((src / "manifest.json").read_text())
    restored, kept_new = [], []
    for rel, digest in data["files"].items():
        if rel in data["skipped"]:
            continue
        target = ws / rel
        if target.exists() and _sha(target) == digest:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / "files" / rel, target)
        restored.append(rel)
    now = {p.relative_to(ws).as_posix() for p in _project_files(ws)}
    kept_new = sorted(now - set(data["files"]))
    return {"restored": sorted(restored), "kept_new": kept_new, "skipped": data["skipped"]}


def prune(checkpoint_root, keep=CHECKPOINT_KEEP):
    root = Path(checkpoint_root)
    if not root.exists():
        return
    dirs = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: int(d.name) if d.name.isdigit() else 0)
    for d in dirs[:-keep]:
        shutil.rmtree(d, ignore_errors=True)
