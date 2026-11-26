"""What agents may and may not assign or do (spec §7.3)."""
import fnmatch
import re
from pathlib import PurePosixPath

from . import config

PRE_DECIDE = ("frame",)   # planning may write only under .duo/
MANIFESTS = ("package.json", "requirements*.txt", "pyproject.toml", "Cargo.toml", "go.mod", "Gemfile", "Podfile")


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


RESEARCH_SCOUT_FILES = ("research/source-notes.md", "research/access-log.md", "research/coverage.json", "research/literature-review.md", "research/evidence-matrix.md", "research/method-proposal.md")
RESEARCH_SOURCE_DIRS = ("research/sources/", "research/searches/")
RESEARCH_DOCUMENT_SUFFIXES = {".md", ".txt", ".json", ".jsonl", ".log", ".html", ".pdf", ".csv", ".bib", ".ris", ".xml", ".png", ".jpg", ".jpeg", ".webp", ".tex", ".zip", ".gz"}


def _outside_scope(path, phase, task_type=None, plan_folder=None):
    parts = PurePosixPath(path).parts
    if ".." in parts or PurePosixPath(path).is_absolute():
        return True
    if phase in PRE_DECIDE:
        if plan_folder and PurePosixPath(path).is_relative_to(plan_folder):
            return False
        return not (parts and parts[0] == ".duo")
    if phase == "scout":
        if task_type == "research" and (path in RESEARCH_SCOUT_FILES or
                parts and parts[0] == "research" and PurePosixPath(path).suffix.lower() in RESEARCH_DOCUMENT_SUFFIXES):
            return False
        return not (len(parts) >= 3 and parts[0] == ".duo" and parts[2] == "scout")
    return False


def check(reply, ctx):
    out = []
    if reply.phase != ctx["expected_phase"]:
        out.append("wrong phase: expected %s" % ctx["expected_phase"])
    if ctx.get("author") in ("claude", "codex"):
        return out
    bad = [p for p in ctx.get("changed_files", []) if _outside_scope(p, ctx["expected_phase"], ctx.get("task_type"), ctx.get("plan_folder"))]
    if bad:
        out.append(("files outside research assignment: %s" if ctx.get("task_type") == "research" and ctx["expected_phase"] == "scout" else "files changed before decide: %s") % ", ".join(bad))
    task = _norm(ctx["task"])
    log = list(ctx.get("handoff_log", []))
    for h in reply.handoffs:
        if not isinstance(h, dict):
            continue
        to = str(h.get("to", "")).lower()
        if to in ("human", "user"):
            out.append("handoff to human not allowed")
            continue
        ref = _norm(h.get("ask_ref"))
        if len(ref.split()) < 3 or ref not in task:
            out.append("handoff outside the ask")
            continue
        pair = {ctx["author"], to}
        what = _norm(h.get("what"))
        repeats = 1 + sum(1 for a, b, w in log if {a, b} == pair and _norm(w) == what)
        if repeats >= config.CFG["ping_pong_cap"]:
            out.append("ping-pong limit reached")
        log.append((ctx["author"], to, what))
    return out


def is_high_impact(changed_files, deleted):
    if deleted:
        return True
    return any(fnmatch.fnmatch(PurePosixPath(p).name, m) for p in changed_files for m in MANIFESTS)
