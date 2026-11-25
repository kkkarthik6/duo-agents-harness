"""Plain task handoffs. Assignment identity belongs to the broker, not reply syntax.

Old JSON replies remain readable. Missing or malformed metadata never invalidates
an agent's output. A delivered build is submitted for review, not approved.
"""
import json
import os
import re
import shutil
import hashlib
import fcntl
import secrets
from datetime import datetime
from pathlib import Path

from . import protocol


GUIDANCE = """Reply in plain text or Markdown at the reply path: saved output paths, unfinished work, actual blockers. Reviewers start with APPROVED or REVISE on its own line, then every required fix, one per line as `file: problem -> fix`. Saving a handoff submits work; it does not approve it. Read the previous handoff and the files it names first."""


def filename(task_id, number, actor, suffix=""):
    # Task identity lives in the indexed step folder, not a request-sized filename.
    def short(value, limit=24):
        safe = re.sub(r'[^A-Za-z0-9_-]', '-', str(value)).strip('-')
        if len(safe) > limit:
            safe = safe[:limit-9] + '-' + hashlib.sha256(str(value).encode()).hexdigest()[:8]
        return safe
    return 'reply-%04d-%s%s.md' % (number, short(actor) or 'agent', '-' + short(suffix) if suffix else '')


def existing_directory(root, title, run_id):
    """Reuse valid legacy paths, without probing oversized path components."""
    name = re.sub(r"[^\w .-]", "-", (str(title).splitlines() or [''])[0]).strip(" .-") or "Task"
    run = re.sub(r"[^A-Za-z0-9_.-]", "-", str(run_id)).strip(".") or "run"
    if any(len(part.encode('utf-8')) > 255 for part in (name, run)):
        return None
    target = Path(root) / 'output' / name / run
    return target if target.is_dir() and not target.is_symlink() else None


def _numbered(parent, index, key, prefix, legacy=None, metadata=None):
    """Allocate a stable directory under an inter-process lock. Never reuse a number."""
    parent, index = Path(parent), Path(index)
    index.parent.mkdir(parents=True, exist_ok=True)
    with index.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(index.read_text()) if index.exists() else {'next':1, 'entries':{}}
        row = data['entries'].get(key)
        if row:
            folder = parent / row['folder']
        else:
            if legacy is not None:
                folder, number = Path(legacy), None
            else:
                number = data['next']
                while (parent / ('%s-%04d' % (prefix, number))).exists():
                    number += 1
                folder = parent / ('%s-%04d' % (prefix, number))
                data['next'] = number + 1
            row = dict(metadata or {}, folder=folder.relative_to(parent).as_posix(), number=number)
            data['entries'][key] = row
            tmp = index.with_name(index.name + '.' + secrets.token_hex(6) + '.tmp')
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
            os.replace(tmp, index)
        if not folder.resolve().is_relative_to(parent.resolve()):
            raise ValueError('Saved output directory is outside its assigned parent')
        folder.mkdir(parents=True, exist_ok=True)
        return folder


def directory(root, title, run_id):
    root = Path(root)
    title = str(title)
    target = _numbered(root / 'output', root / '.duo/output-paths.json',
                       json.dumps([str(run_id), title]), 'task',
                       legacy=existing_directory(root, title, run_id),
                       metadata={'run_id':str(run_id), 'title':title})
    contents = target / 'contents.md'
    try:
        with contents.open('x') as stream:
            stream.write('# Task ' + target.name + '\n\n' + title + '\n\nRun ID: `' + str(run_id) +
                         '`\n\nCreated: ' + datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z (%z)') +
                         '\n\nEach numbered step contains its handoff index and saved outputs.\n\n'
                         '<!-- Duo numbered steps -->\n<!-- End Duo numbered steps -->\n')
    except FileExistsError:
        pass
    return target


def task_directory(root, title, run_id, task_id):
    parent = directory(root, title, run_id)
    safe = str(task_id)
    legacy = parent / safe if safe not in ('', '.', '..') and '/' not in safe and '\\' not in safe and len(safe.encode()) <= 255 else None
    if legacy is not None and (not legacy.is_dir() or legacy.is_symlink()):
        legacy = None
    folder = _numbered(parent, parent / '.steps.json', str(task_id), 'step', legacy=legacy,
                       metadata={'task_id':str(task_id)})
    with (parent / '.steps.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        contents = parent / 'contents.md'
        text = contents.read_text()
        start, end = '<!-- Duo numbered steps -->', '<!-- End Duo numbered steps -->'
        if start in text and end in text:
            steps = json.loads((parent / '.steps.json').read_text())['entries'].values()
            links = '\n'.join('- [%s](%s/): `%s`' % (row['folder'],row['folder'],row['task_id']) for row in steps)
            section = start + '\n' + links + '\n' + end
            updated = text[:text.index(start)] + section + text[text.index(end)+len(end):]
            if updated != text:
                contents.write_text(updated)
    return folder


def instructions(root, folder):
    rel = Path(folder).relative_to(root).as_posix()
    return ("Outcome folder: `" + rel + "` (read earlier outcomes there). Save new deliverables, source captures and handoff "
            "notes in it, list their paths in your handoff, keep project source in place, and never overwrite user files.")


def relative_file(session_dir, folder, task_id, number, actor, suffix=""):
    return os.path.relpath(Path(folder) / filename(task_id, number, actor, suffix), session_dir)


def _link(path):
    """A markdown link destination: spaces and brackets in a path must be escaped or the link is broken."""
    from urllib.parse import quote
    return quote(str(path).replace(os.sep, "/"), safe="/._-~")


def index(root, folder, assignment, outputs=()):
    """Keep a broker-owned index; never replace the agent's contents.md."""
    from datetime import datetime
    folder = Path(folder)
    index_path = folder / "handoffs.json"
    records = json.loads(index_path.read_text()) if index_path.exists() else []
    record = {"task_id": assignment.get("task_id"), "role": assignment.get("kind", assignment.get("stage")),
              "reply": os.path.relpath(Path(assignment["reply_path"]), folder),
              "outputs": list(outputs), "saved": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z (%z)")}
    records = [r for r in records if r["reply"] != record["reply"]] + [record]
    index_path.write_text(json.dumps(records, indent=2) + "\n")
    lines = ["# Task outcomes", "", "Read the latest handoff and its referenced files. A saved handoff is not product approval.", ""]
    for row in records:
        lines.append("- [%s: %s](%s), saved %s" % (row["task_id"], row["role"], _link(row["reply"]), row["saved"]))
        for output in row["outputs"]:
            lines.append("  - [%s](%s)" % (output, _link(os.path.relpath(Path(root) / output, folder))))
    (folder / "handoffs.md").write_text("\n".join(lines) + "\n")
    contents = folder / "contents.md"
    marker = "<!-- Duo outcome index -->"
    end_marker = "<!-- End Duo outcome index -->"
    section = marker + "\n" + "\n".join(lines) + "\n" + end_marker
    if not contents.exists():
        contents.write_text(section + "\n\nOpen handoffs.md for the current inventory. "
                            "Read each handoff before using its outputs.\n")
    else:
        previous = contents.read_text()
        if marker in previous and end_marker in previous:
            start = previous.index(marker)
            end = previous.index(end_marker, start) + len(end_marker)
            contents.write_text(previous[:start] + section + previous[end:])


def collect(packet, root, folder, number):
    """Return isolated worker outcomes without moving existing project files."""
    packet, root, folder = Path(packet).resolve(), Path(root).resolve(), Path(folder).resolve()
    if packet == root:
        return
    relative = folder.relative_to(root)
    source = packet / relative
    if not source.is_dir():
        return
    for path in source.rglob("*"):
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(packet):
            continue
        target = folder / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != path.read_bytes():
            target = target.with_name(target.stem + "-handoff-%s" % number + target.suffix)
            if target.exists() and target.read_bytes() != path.read_bytes():
                raise FileExistsError("Conflicting saved task outcome: " + str(target))
        if not target.exists():
            shutil.copy2(path, target)


def review_notes(text):
    """Optional display notes. Missing notes never invalidate a review or trigger a turn."""
    outcomes = {}
    next_action = ""
    for line in text.splitlines():
        line = line.strip().lstrip("- ").strip()
        match = re.fullmatch(r"Outcome ([^:\n]{1,100}):\s*(.+)", line, re.I)
        if match:
            outcomes[match[1].strip()] = match[2].strip()
        elif line.lower().startswith("next:"):
            next_action = line[5:].strip()
    return {"review_outcomes": outcomes, "review_next": next_action} if outcomes or next_action else {}


def metadata(text):
    """Read optional old metadata without requiring it or trusting its identity."""
    for block in reversed(list(protocol.BLOCK.finditer(text.replace(protocol.END_MARK, "")))):
        try:
            value = json.loads(block.group(1))
            if isinstance(value, dict):
                return value
        except (ValueError, TypeError):
            pass
    return {}


REVIEW_KINDS = ("plan_review", "critique", "review", "verify", "secure", "research_check", "check_refs", "deliver")
MIN_APPROVAL_EVIDENCE = 15  # blocks content-free stamps only; it cannot judge how good the evidence is


CONTRARY = re.compile(r"\b(?:not|no|but|however|unless|except|until|once|if|before|after|needs?|requires?|fix|fixes|missing|fails?|failing)\b", re.I)


def _titled_verdict(plain):
    """`# APPROVED`, `**APPROVED** - tempconv` or `REVISE: 2 fixes` on the first line, with an optional short title.
    A title that hedges ("approved, but...") is not a verdict."""
    lines = [l for l in plain.splitlines() if l.strip()]
    if not lines:
        return None
    line = re.sub(r"^[\s#>*_`]+", "", lines[0]).rstrip("*_` ")
    m = re.match(r"(?i)(approved|revise)\b[*_`]*\s*(?:[-\u2013\u2014:]\s*(.*))?[.!]?$", line)
    if not m:
        return None
    verdict, title = m.group(1).upper(), (m.group(2) or "")
    if verdict == "APPROVED" and CONTRARY.search(title):
        return None
    return verdict


def _approval_has_evidence(plain, data=None):
    """An approval needs a non-empty `evidence` list, or text after the verdict line saying what was checked."""
    listed = (data or {}).get("evidence")
    if isinstance(listed, list) and any(isinstance(x, str) and x.strip() for x in listed):
        return True
    lines = plain.strip().splitlines()
    rest = " ".join(lines[1:]) if len(lines) > 1 else ""
    opening = re.sub(r"(?i)^[\s#*]*(?:review\s*:\s*)?approved[.!:\s-]*", "", lines[0]) if lines else ""
    return len((opening + " " + rest).strip()) >= MIN_APPROVAL_EVIDENCE


UNFINISHED = re.compile(r"(?im)^[ \t]*(?:work remains|unfinished|incomplete|remaining work\s*:|blocked\s*:|cannot continue\b)")
NOTHING = re.compile(r"(?i)^[^:\n]*[:\-\u2013\u2014]\s*(?:none|nothing|n/?a|nil|zero|0|no|no (?:unfinished|remaining) work|nothing (?:left|remains|unfinished))(?=\s*(?:[.;,!]|$))")


def _unfinished(plain):
    """The first line that really reports remaining work. "Unfinished work: none" says the opposite."""
    for match in UNFINISHED.finditer(plain):
        line = plain[match.start():plain.find("\n", match.start()) if plain.find("\n", match.start()) != -1 else len(plain)].strip()
        if not NOTHING.match(line):
            return match
    return None


def parse(text, assignment, root=None, changed=()):
    data = metadata(text)
    # Preserve the complete output for the next agent, including optional data.
    prose = text.replace(protocol.END_MARK, "").strip()
    plain = protocol.BLOCK.sub("", prose).strip() or prose
    kind = assignment.get("kind", assignment.get("stage", ""))
    r = protocol.Reply(freeform=True, prose=prose, summary=plain,
                       phase=assignment.get("phase", ""), task_id=assignment.get("task_id"))
    for key in ("options", "decisions", "handoffs", "security"):
        value = data.get(key)
        if isinstance(value, list):
            setattr(r, key, value)
    r.options = [o for o in r.options if isinstance(o, dict)]
    requests = data.get("stage_requests")
    allowed = {"research", "feasibility", "verify", "security"} if "phase" in assignment else {
        "research", "equip", "feasibility", "verify", "security"}
    r.stage_requests = {k: v for k, v in requests.items() if k in allowed and isinstance(v, str) and v.strip()} if isinstance(requests, dict) else {}
    if requests is not None:
        from . import relay
        r.errors.extend(relay.request_errors(requests, allowed))
    r.task_type = data.get("task_type") if data.get("task_type") in ("research", "programming", "general") else None
    r.outcome = data.get("outcome") if data.get("outcome") in ("progress", "blocked", "answered", "needs_human") else "progress"
    r.blocker = data.get("blocker") if isinstance(data.get("blocker"), str) else None
    r.blocker_detail = data.get("blocker_detail") if isinstance(data.get("blocker_detail"), dict) else None
    r.remaining = data.get("remaining") if isinstance(data.get("remaining"), list) else []
    r.build_evidence = {"behavior": plain}
    r.verdict = data.get("verdict", data.get("status"))
    if r.verdict not in ("APPROVED", "REVISE"):
        r.verdict = None
    if kind in ("plan_review", "critique", "review", "verify", "secure", "research_check", "check_refs", "deliver") and r.verdict is None:
        # Only a clear affirmative conclusion can approve. A quoted or negated
        # approval word elsewhere in a report cannot turn a review into a pass.
        opening = plain.split("\n\n", 1)[0].strip().lstrip("# *")
        titled = _titled_verdict(plain)
        reject = re.search(r"\b(revise|rejected|not approved|do not approve|changes (?:are )?required|required fixes? remain|does not meet)\b", opening, re.I)
        approve = re.fullmatch(r"(?:review\s*:\s*)?(?:approved[.!]?|(?:the )?(?:work|output|task|deliverable) meets (?:all |the )?(?:task )?requirements[.!]?)", opening, re.I)
        if titled and not reject:
            approve = approve or titled == "APPROVED"
            reject = reject or titled == "REVISE"
        if not reject and not approve:
            reject = re.search(r"(?im)^(?:the |this |i |we )?(?:work |output |deliverable )?(?:needs? (?:changes|revision|fixes)|requires? (?:changes|revision|fixes)|does not (?:pass|meet)|is not ready)\b", plain)
            approve = re.search(r"(?im)^(?:i approve (?:this|the) (?:work|output|deliverable)|(?:all |the )?(?:task )?requirements (?:are |have been )?(?:met|satisfied)|(?:the |this )?(?:work|output|deliverable) (?:is ready for delivery|passes review|meets (?:all |the )?(?:task )?requirements))[.!]?(?:\s|$)", plain)
        r.verdict = "REVISE" if reject else ("APPROVED" if approve else None)
    if r.verdict == "APPROVED" and kind in REVIEW_KINDS and not _approval_has_evidence(plain, data):
        # A review that only says "approved" shows nothing was checked. A revision never needs evidence to block.
        r.verdict = None
        r.errors.append("An approval must state what was checked: name the files read, the checks run, and their results.")
    unfinished = _unfinished(plain)
    r.complete = data.get("complete") if isinstance(data.get("complete"), bool) else not bool(unfinished)
    if unfinished and not r.remaining:
        r.remaining = [plain[unfinished.start():].strip()]
    if re.search(r"(?im)^\s*(?:blocked\s*:|cannot continue\b)", plain) and r.outcome == "progress":
        r.outcome, r.blocker = "blocked", r.blocker or "data_missing"
    r.ready_for_review = r.complete
    if kind == "diverge" and not r.options:
        r.options = [{"id": "O1", "title": "Proposed approach", "detail": prose}]
    if kind == "scout" and not r.options:
        r.options = [{"id": oid, "probe": prose} for oid in assignment.get("option_ids", [])]
    if r.outcome in ("blocked", "needs_human") and r.blocker_detail is None:
        r.blocker_detail = {"missing": plain, "attempts": [], "alternatives": [],
                            "affected": [(assignment.get("build_package") or {}).get("id", r.task_id)],
                            "retry_when": "Resolve the blocker described in the task handoff."}
    candidates = list(data.get("outputs") or []) if isinstance(data.get("outputs"), list) else []
    candidates += list((assignment.get("build_package") or {}).get("deliverables", []))
    candidates += list(assignment.get("inherited_outputs", []))
    candidates += list(changed)
    candidates += re.findall(r"`([^`\n]+)`", plain)
    candidates += re.findall(r"\]\(([^)\n]+)\)", plain)
    folder = assignment.get("outcome_dir")
    if root and folder and Path(folder).resolve().is_relative_to(Path(root).resolve()):
        candidates += [str(path) for path in Path(folder).rglob("*") if path.is_file() and not path.is_symlink()]
    r.outputs = []
    if root:
        base = Path(root).resolve()
        for value in candidates:
            if not isinstance(value, str):
                continue
            path = (base / value).resolve()
            if str(path) in {str(Path(p).resolve()) for p in assignment.get("broker_document_paths", [])}:
                continue
            if not path.is_relative_to(base) or not path.is_file():
                continue
            rel = path.relative_to(base).as_posix()
            if ".duo" in Path(rel).parts:
                continue
            if path.name in ("handoffs.json", "handoffs.md") or (path.name == "contents.md" and path.read_text(errors="replace").startswith("<!-- Duo outcome index -->")) or re.search(r"-\d{4}-agent-[a-f0-9]+(?:-fix-[a-f0-9]+)?\.md$", path.name):
                continue
            if rel.split("/")[0] in (".duo", "duo", ".git", ".cache", ".venv", "node_modules", "logs"):
                continue
            if rel not in r.outputs:
                r.outputs.append(rel)
        # A prose-only deliverable is itself an outcome. Review still decides
        # whether it satisfies the task; do not invent missing product files.
        if not r.outputs and assignment.get("reply_path"):
            path = Path(assignment["reply_path"]).resolve()
            if path.is_relative_to(base) and path.is_file():
                r.outputs = [path.relative_to(base).as_posix()]
    return r


def ready(path, idle=False):
    """A finished worker and a saved nonempty file replace textual sentinels."""
    path = Path(path)
    return bool(idle and path.is_file() and path.stat().st_size and path.read_text(errors="replace").strip())
