"""Shared task contracts, anonymous records and isolated review packets.

Routing state remains private to the harness. Review packets never contain it.
Blinding prevents accidental identity cues; it is not an OS security boundary.
"""
import json
import re
import secrets
import shutil
import difflib
from pathlib import Path

from . import harness


def alias(state, agent):
    if agent in ("human", "system", None):
        return agent
    return state.setdefault("actor_ids", {}).setdefault(agent, "agent-" + secrets.token_hex(5))


def resolve(state, actor):
    return next((a for a, random_id in state.get("actor_ids", {}).items() if random_id == actor), actor)


def redact(state, value):
    """Remove provider/model attribution from public records, including dictionary keys."""
    replacements = {str(k): alias(state, k) for k in state.get("agents", [])}
    model_ids = state.setdefault("model_ids", {})
    for agent, info in state.get("agent_info", {}).items():
        if info.get("label"):
            replacements[info["label"]] = alias(state, agent)
    for agent, model in state.get("model_now", {}).items():
        if model:
            replacements[model] = alias(state, agent)
    def clean(v):
        if isinstance(v, str):
            for name in sorted(replacements, key=len, reverse=True):
                v = re.sub(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", replacements[name], v, flags=re.I)
            return re.sub(r"\b(?:gpt-[\w.\-]+|claude-[\w.\-]+|(?:opus|sonnet|haiku|astra|terra|sol)[- ]\d[\w.\-]*)\b",
                          lambda m: model_ids.setdefault(m.group(0).lower(), "model-" + secrets.token_hex(5)), v, flags=re.I)
        if isinstance(v, dict):
            return {clean(k): clean(x) for k, x in v.items() if k not in ("actor_ids", "model_ids", "agent_info", "model_now", "_role_models", "_model_assignments", "_model_escalations", "role_models", "human_agent_labels", "_assigned_model")}
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        return v
    return clean(value)


def strip_attribution(state, text):
    """Preserve scientific model names and quotations in the actual deliverable."""
    pattern = r"(?im)^[ \t#>*-]*(?:(?:built|generated|written|implemented|authored|reviewed) by\b|(?:builder|builder model|agent model|provider|author)[ \t]*:).*$"
    return re.sub(pattern, lambda m: redact(state, m.group(0)), text)


def contract(task_id, ack, build=False):
    text = "Assigned task_id: %s. First write exactly %s to %s to acknowledge receipt." % (task_id, task_id, ack)
    if build:
        text += " Finish the whole assigned output; a progress report is not completion."
    return text


def output_errors(root, data, task_id, owns=None):
    if data.get("task_id", data.get("task")) != task_id:
        return ["Reply task_id does not match assigned task_id %s" % task_id]
    if not isinstance(data.get("complete"), bool):
        return ["Build reply needs complete: true or false"]
    if not isinstance(data.get("remaining"), list):
        return ["Build reply needs a remaining list"]
    if not data["complete"]:
        return []
    if data["remaining"]:
        return ["Completed task still has remaining requirements"]
    outputs = data.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return ["Completed task needs output file paths"]
    base = Path(root).resolve()
    errors = []
    for rel in outputs:
        if not isinstance(rel, str):
            errors.append("Output paths must be strings")
            continue
        p = (base / rel).resolve()
        if Path(rel).is_absolute() or base not in p.parents or not p.is_file():
            errors.append("Missing or unsafe output: %s" % rel)
        elif Path(rel).parts[0] in ("duo", ".duo", ".git"):
            errors.append("Harness records are not task outputs: %s" % rel)
        elif owns and not any(o == "." or rel == o.rstrip("/") or rel.startswith(o.rstrip("/") + "/") for o in owns):
            errors.append("Output is outside task ownership: %s" % rel)
    return errors


def _deliverable(root, path):
    """Duo rewrites its own indexes (contents.md, handoffs, task and summary pages) after every step.
    They are records, not the deliverable, so they must not make approved work look changed."""
    from . import goal_loop
    return not goal_loop.control_artifact(root, Path(root) / path)


def receipt(root, task_id, outputs):
    return {"task_id": task_id, "outputs": [{"path": p, "sha256": harness._sha(Path(root) / p)} for p in outputs if _deliverable(root, p)]}


def unchanged(root, record):
    try:
        rows = [x for x in record.get("outputs", []) if _deliverable(root, x["path"])]
        return bool(rows) and all(harness._sha(Path(root) / x["path"]) == x["sha256"] for x in rows)
    except OSError:
        return False


def review_diff(root, checkpoint, changed):
    root, checkpoint = Path(root), Path(checkpoint)
    patches = []
    for rel in changed:
        current, before = root / rel, checkpoint / "files" / rel
        if root.resolve() not in current.resolve().parents:
            continue
        if any(p.is_file() and p.stat().st_size > harness.COPY_LIMIT for p in (current, before)):
            patches.append("Large output changed: %s\n" % rel)
            continue
        old = before.read_text(errors="replace").splitlines(True) if before.is_file() else []
        new = current.read_text(errors="replace").splitlines(True) if current.is_file() else []
        patches.extend(difflib.unified_diff(old, new, fromfile="before/" + rel, tofile="after/" + rel))
    return "".join(patches)


def sanitize_logs(root, state):
    for directory in (Path(root) / ".duo/logs", Path(root) / "duo/runs/logs"):
        if not directory.is_dir():
            continue
        for p in directory.rglob("*"):
            if p.is_file() and not p.is_symlink() and p.suffix in (".log", ".md", ".txt", ".json"):
                text = p.read_text(errors="replace")
                clean = redact(state, text)
                if clean != text:
                    p.write_text(clean)


def review_packet(root, dest, state, task_id, spec, logs=(), outputs=()):
    """Copy product inputs, sanitized task spec and relevant logs, never history or routing."""
    root, dest = Path(root), Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    excluded = harness.EXCLUDE_DIRS | {".claude", ".codex", ".agents", ".aws", "AGENTS.md", "CLAUDE.md", "contents.md"}
    for p in root.iterdir():
        if p.name in excluded or p.name.startswith(".duo") or p.name == ".env" or p.is_symlink():
            continue
        target = dest / p.name
        if p.is_dir():
            shutil.copytree(p, target, ignore=lambda d, names: [n for n in names if n in excluded or (Path(d) / n).is_symlink()])
        elif p.is_file():
            shutil.copy2(p, target)
    # Preserve product content. Only attribution is blinded; logs are fully anonymized.
    for p in dest.rglob("*"):
        if p.is_file() and p.suffix in (".md", ".log", ".txt", ".jsonl"):
            text = p.read_text(errors="replace")
            p.write_text(redact(state, text) if p.suffix in (".log", ".jsonl") else strip_attribution(state, text))
    task = dest / "duo/tasks" / (task_id + ".md")
    task.parent.mkdir(parents=True, exist_ok=True)
    task.write_text(strip_attribution(state, spec))
    for rel in logs:
        p = root / rel
        if p.is_file() and root.resolve() in p.resolve().parents:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(redact(state, p.read_text(errors="replace")))
    for rel in ("duo/brief.md", "duo/references.md", "duo/refs/source-notes.md"):
        p = root / rel
        if p.is_file() and not p.is_symlink():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(strip_attribution(state, p.read_text(errors="replace")))
    for rel in outputs:
        p, target = root / rel, dest / rel
        if (not Path(rel).is_absolute() and root.resolve() in p.resolve().parents and
                dest.resolve() in target.resolve().parents and p.is_file() and not p.is_symlink() and not target.exists()):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
    return dest


ACCESS_CHECK = ("Before research, test available search, HTML reading and PDF extraction separately. "
                "Use one public primary source for each check. Save the actual result, tool name, URL and failure in an access log. Record the denying layer when known. A sandbox denial is not an explicit user rejection. "
                "Search access is separate from shell network access. Try a permitted web tool, publisher HTML, "
                "or downloaded PDF extraction if one route fails. Never infer no web access from a failed curl command. "
                "Read source content and record claim support and page or section, not just citation metadata. "
                "Keep verified source notes as you work. An unavailable source blocks its claims, not all research. ")
