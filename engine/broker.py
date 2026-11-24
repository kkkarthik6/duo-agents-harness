"""Durable task lineage and diagnostics. Duo records task state and passes information.
Claude and Codex use tools under their native permission settings.
Duo adds no tool restrictions or approval gates.

The event log records lifecycle. Task documents record agent-authored progress.
Worker output has no required schema.
Diagnostics are separate from assignments, progress, and product files.
"""
import argparse
import fcntl
import hashlib
import json
import os
import secrets
import shlex
import sys
import time
import re
from pathlib import Path


def _append(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(data, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _rows(path):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open() as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        for line in stream:
            try:
                rows.append(json.loads(line))
            except ValueError:
                # A torn final write is retained on disk and is not task evidence.
                continue
    return rows


def _document_text(value):
    if not value:
        return ""
    try:
        return Path(value).read_text(errors="replace")
    except OSError:
        return ""


def _mtime(value):
    try:
        return Path(value).stat().st_mtime if value else 0
    except OSError:
        return 0


def event(run_dir, kind, node=None, **data):
    row = dict(id="event-" + secrets.token_hex(8), time=time.time(), kind=kind, node=node, **data)
    _append(Path(run_dir) / "broker/events.jsonl", row)
    return row


def view(run_dir):
    nodes, progress, completion = {}, [], None
    for row in _rows(Path(run_dir) / "broker/events.jsonl"):
        node = row.get("node")
        kind = row["kind"]
        if kind == "completion_summary":
            completion = row
        if kind == "task_created":
            nodes[node] = dict(row["task"], id=node, created=row["time"], updated=row["time"],
                               status="ready", assignments=[], outputs=[], checkpoints=[], error_ids=[], requirements={})
        elif node in nodes:
            item = nodes[node]
            item["updated"] = row["time"]
            if kind == "assigned":
                item["workspace"] = row.get("workspace")
                item["status"] = "reviewing_previous" if row.get("role") == "continuation_review" else "working"
                item["assignments"].append(row)
                item["current_role"] = row.get("role")
            elif kind == "checkpoint":
                item["checkpoints"].append(row)
                item["outputs"] = row.get("outputs", [])
            elif kind == "progress":
                progress.append(row)
            elif kind == "task_status":
                item["status"] = row["status"]
            elif kind == "error_recorded":
                item["error_ids"].append(row["error_id"])
            elif kind == "requirement":
                item["requirements"][row["requirement"]] = row
            elif kind == "review_plan":
                item["recovery_plan"] = row["path"]
            elif kind == "task_documents":
                item.update(task_file=row["task_file"], summary_file=row["summary_file"])
            elif kind == "task_context":
                item['context_note']=row['context']
                item['context_note_updated']=row['time']
                item['context_note_source']=row.get('source','task agent')
            elif kind == "summary_state":
                item["summary_state"] = row["status"]
                item["summary_model"] = row.get("model")
                item["summary_process_id"] = row.get("process_id")
                if row["status"] == "ready":
                    item["summary_updated"] = row["time"]
            elif kind == "historical_role":
                item["original_role"] = row["role"]
                item.setdefault("current_role", row["role"])
    for item in nodes.values():
        if item.get("summary_state") == "updating" and item.get("summary_process_id"):
            try:
                os.kill(item["summary_process_id"], 0)
            except ProcessLookupError:
                item["summary_state"] = "interrupted"
            except PermissionError:
                pass
        task_path = item.get("task_file")
        task_text = _document_text(task_path)
        item["work_items"] = [{"checked": value.lower() == "x", "text": text}
                              for value, text in re.findall(r"(?m)^\s*[-*]\s+\[([ xX])\]\s+(.+)$", task_text)]
        summary_path = item.get("summary_file")
        summary_text = _document_text(summary_path)
        item["verbal_summary"] = summary_text.removeprefix("<!-- Duo prose summary -->\n")
        sections, current = {}, None
        for line in item["verbal_summary"].splitlines():
            match = re.match(r"^(?:#{1,4}\s*)?(Task focus|Goal|Plan|Finished|Doing|Results summary|Requirement(?:s)?|Outcomes|Next steps)\s*:?\s*(.*)$", line, re.I)
            if match:
                current = match.group(1).lower().replace("requirements", "requirement")
                sections[current] = [match.group(2)] if match.group(2) else []
            elif current:
                sections[current].append(line)
        item["summary_sections"] = {key: "\n".join(lines).strip() for key, lines in sections.items()}
        latest_input = max((row["time"] for row in item["checkpoints"]), default=0)
        latest_input = max(latest_input, _mtime(task_path))
        item["summary_is_current"] = bool(item.get("summary_updated", 0) >= latest_input and item.get("summary_updated"))
        for file in (task_path, summary_path):
            item["updated"] = max(item["updated"], _mtime(file))
    _returned_stage_history(run_dir, nodes, progress)
    job_records = {row["job_id"]: row for row in _rows(Path(run_dir) / "broker/jobs.jsonl")}
    from . import completion_summary
    completion = completion_summary.latest(run_dir) if completion else None
    requests = questions(run_dir)
    for item in nodes.values():
        item["questions"] = [row for row in requests if row["node"] == item["id"]]
    return {"nodes": list(nodes.values()), "progress": progress, "jobs": list(job_records.values()), "completion_summary":completion, "questions":requests,
            "diagnostics": str(Path(run_dir) / "diagnostics"), "updated": max((n["updated"] for n in nodes.values()), default=None)}


def node(run_dir, node_id):
    return next((n for n in view(run_dir)["nodes"] if n["id"] == node_id), None)


def ensure(run_dir, node_id, title, specification, logical=None, parent=None, **extra):
    if not node(run_dir, node_id):
        event(run_dir, "task_created", node_id, task=dict(title=title, specification=specification,
              logical_task_id=logical or node_id, parent=parent, **extra))
    return node_id


def paths(run_dir, node_id):
    # IDs are engine-owned identifiers, not filesystem paths from agent replies.
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in node_id)
    return Path(run_dir) / "broker/tasks" / (safe + ".json"), Path(run_dir) / "diagnostics" / safe / "errors.jsonl"


def save_record(run_dir, node_id):
    record, errors = paths(run_dir, node_id)
    record.parent.mkdir(parents=True, exist_ok=True)
    with (record.parent / ".snapshots.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = node(run_dir, node_id)
        data["diagnostics_path"] = str(errors)
        temp = record.with_name(record.name + "." + secrets.token_hex(8) + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        temp.replace(record)
    return record


def assign(run_dir, p, title, specification, root):
    key = p.get("broker_task_id") or p["task_id"]
    p["broker_task_id"] = key
    ensure(run_dir, key, title, specification, logical=p.get("logical_task_id", p.get("task", p["task_id"])))
    event(run_dir, "assigned", key, task_id=p["task_id"], attempt=p.get("delivery_id"),
          role=p.get("kind", p.get("stage")), agent=p.get("agent", "unassigned"), specification=p.get("assignment_text", specification),
          reply=str(Path(run_dir) / p.get("file", "replies/recovered.md")), outcome_dir=p.get("outcome_dir"),
          job_registry=p.get("job_registry"), job_task_id=p.get("job_task_id", p["task_id"]), workspace=str(root))
    if p.get("outcome_dir"):
        ensure_documents(run_dir, key, p["outcome_dir"])
        documents = node(run_dir, key)
        p["broker_document_paths"] = [documents["task_file"], documents["summary_file"]]
        p["summary_task_stamp"] = Path(documents["task_file"]).stat().st_mtime_ns
    p["broker_record"] = str(save_record(run_dir, key))
    p["diagnostics_path"] = str(paths(run_dir, key)[1])
    rel = lambda path: os.path.relpath(path, root)
    return ("\nIf you must ask the user (scope, compute, data policy, access or authorship only; decide everything else yourself): `" +
            shlex.join([sys.executable, str(Path(__file__).resolve()), "--run-dir", rel(run_dir), "--task-id", key]) +
            " ask --text '...' --option '...' --recommendation '...' --reason '...'`. Task record: " + rel(p["broker_record"]) + ".\n")


def artifact(root, rel):
    root = Path(root).resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return {"path": str(rel), "state": "unavailable"}
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": path.relative_to(root).as_posix(), "sha256": digest.hexdigest(), "state": "saved"}


def import_history(run_dir, root, state):
    """Index existing records once. Never manufacture missing assignment evidence."""
    if (Path(run_dir) / "broker/events.jsonl").exists():
        return
    records = state.get("entries", state.get("ledger", []))
    if not records:
        return
    for row in records:
        key = row.get("task_id") or row.get("task")
        if not key or row.get("author") in ("human", "system"):
            continue
        ensure(run_dir, key, state.get("title", state.get("task", "Task")), state.get("task", ""),
               logical=row.get("task") or key, imported=True)
        if row.get("outcome") in ("blocked", "needs_human") or row.get("invalid"):
            error(run_dir, key, row.get("prose") or row.get("summary") or "Saved task was interrupted.",
                  raw=row, source="legacy_import")
            event(run_dir, "task_status", key, status="partial")
            continue
        outputs = [artifact(root, value) for value in row.get("outputs", []) if isinstance(value, str)]
        event(run_dir, "checkpoint", key, outputs=outputs, reply=str(Path(run_dir) / row["file"]) if row.get("file") else None,
              verdict=row.get("verdict"), source="legacy_import_current_files", original_time=row.get("time", row.get("at")))
        if row.get("prose") or row.get("summary"):
            event(run_dir, "progress", key, text=row.get("prose") or row["summary"], source="historical_agent_report")
        if row.get("verdict") == "APPROVED":
            event(run_dir, "task_status", key, status="completed")
        save_record(run_dir, key)
    failed = state.get("failed_turn") or state.get("broker_failed")
    if failed and failed.get("task_id"):
        key = failed["task_id"]
        ensure(run_dir, key, state.get("title", state.get("task", "Task")), state.get("task", ""))
        assign(run_dir, failed, state.get("title", state.get("task", "Task")), state.get("task", ""), root)
        receipt = state.get("task_delivery", {})
        if receipt.get("outputs"):
            event(run_dir, "checkpoint", key, outputs=[dict(row, state="submitted", source="saved_receipt") for row in receipt["outputs"]])
        message = "\n".join(failed.get("failure_problems", [])) or (state.get("stop") or {}).get("why", "Saved task was interrupted.")
        error_id = error(run_dir, key, message, raw=failed, source="legacy_import", attempt=failed.get("delivery_id"))
        event(run_dir, "task_status", key, status="partial")
        state["stop"] = {"rule": "Saved task needs continuation", "why": "Historical diagnostics: " + error_id,
                         "next": "Continue to review saved work and create the remaining task.", "error_id": error_id}
        state["detail"] = "Saved task is ready for continuation review."
    event(run_dir, "history_imported")


def migrate_saved_failure(run_dir, state):
    """Historical control errors become references, even on repeated reloads."""
    name = "failed_turn" if state.get("failed_turn") else "broker_failed"
    failed = state.get(name)
    if not failed or not failed.get("task_id"):
        return
    key = failed.get("broker_task_id", failed["task_id"])
    records = errors(run_dir, key)
    if not records:
        return
    error_id = records[-1]["id"]
    state[name] = {k: value for k, value in failed.items() if k not in
                   ("failure_problems", "prompt", "bounced", "assignment_text", "wire_prompt")}
    state[name]["failure_diagnostic_id"] = error_id
    if state.get("stop") and not state["stop"].get("error_id"):
        state["stop"] = {"rule": "Saved task needs continuation", "why": "Historical diagnostics: " + error_id,
                         "next": "Continue to review saved work and create the remaining task.", "error_id": error_id}
        state["detail"] = "Saved task is ready for continuation review."


def diagnostic_entry(row):
    return row.get("kind") in ("warning", "error", "recovery") or bool(row.get("invalid")) or row.get("outcome") in ("blocked", "needs_human")


def import_diagnostic_history(run_dir, state):
    if any(row["kind"] == "diagnostic_history_imported" for row in _rows(Path(run_dir) / "broker/events.jsonl")):
        return
    previous = state.get("build_task_id") or "session"
    for row in state.get("entries", []):
        previous = row.get("task_id") or previous
        if diagnostic_entry(row):
            message = row.get("prose") or row.get("summary") or "Historical task diagnostic."
            if not any(record["message"] == message for record in errors(run_dir, previous)):
                error(run_dir, previous, message, raw=row, operation=row.get("kind"), source="legacy_import")
    event(run_dir, "diagnostic_history_imported")


def prepare_historical_documents(run_dir, root, state):
    if not any(row["kind"] == "historical_roles_imported" for row in _rows(Path(run_dir) / "broker/events.jsonl")):
        for row in state.get("entries", []):
            key = row.get("task_id")
            if not key or row.get("author") in ("human", "system") or diagnostic_entry(row) or not node(run_dir, key):
                continue
            role = row.get("kind", "assignment")
            event(run_dir, "historical_role", key, role=role)
            if role in ("assess", "exchange", "frame", "diverge", "score", "refs", "setup"):
                event(run_dir, "task_status", key, status="stage_completed")
        event(run_dir, "historical_roles_imported")
    for item in view(run_dir)["nodes"]:
        if not item.get("task_file"):
            prepare_task_documents(run_dir, root, item["id"])


def checkpoint(run_dir, p, root, outputs=(), summary=None, verdict=None, outcome="progress", live=False):
    key = p.get("broker_task_id", p["task_id"])
    records = [artifact(root, value) for value in outputs if isinstance(value, str)]
    if verdict == "APPROVED":
        for record in records:
            if record["state"] == "saved":
                record.update(state="reviewed", review_actor=p.get("agent", "unassigned"))
    event(run_dir, "checkpoint", key, attempt=p.get("delivery_id"), outputs=records,
          reply=str(Path(run_dir) / p.get("file", "replies/recovered.md")), verdict=verdict, outcome=outcome,
          job_registry=p.get("job_registry"), job_task_id=p.get("job_task_id", p["task_id"]))
    if summary and outcome == "progress":
        event(run_dir, "progress", key, text=summary, source="agent_report", outputs=records,
              actor=p.get("agent", "unassigned"), role=p.get("kind", p.get("stage")))
    if verdict == "APPROVED":
        event(run_dir, "task_status", key, status="completed")
    elif outcome == "progress" and not live:
        role = p.get("kind", p.get("stage"))
        status = "submitted" if role in ("build", "revise", "research") else "stage_completed"
        if role in ("critique", "review", "verify", "secure", "research_check", "check_refs", "deliver"):
            status = "needs_work" if verdict == "REVISE" else "review_pending"
        event(run_dir, "task_status", key, status=status)
    save_record(run_dir, key)


def ensure_documents(run_dir, task_id, outcome_dir):
    item = node(run_dir, task_id)
    folder = Path(outcome_dir)
    folder.mkdir(parents=True, exist_ok=True)
    task_file = folder / "task.md"
    summary_file = folder / "summary.md"
    ownership = folder / ".duo/task-documents.json"
    ownership.parent.mkdir(parents=True, exist_ok=True)
    ownership.write_text(json.dumps({"task_id": task_id, "files": [str(task_file), str(summary_file)]}) + "\n")
    if not task_file.exists():
        work = []
        if item.get("parent"):
            work.append("Review the previous task's saved work and define the remaining requirements.")
            parent = node(run_dir, item["parent"])
            work += [row["text"] for row in parent.get("work_items", []) if not row["checked"]]
        if not work:
            role = item.get("original_role")
            work = ["Complete the " + role.replace("_", " ") + " assignment." if role and item["status"] == "stage_completed" else item["title"]]
        text = "<!-- Duo task progress document -->\n# Task " + task_id + "\n\n## Requirement\n\n" + item["specification"] + "\n\n## Work items\n\n"
        mark = "x" if item["status"] == "stage_completed" else " "
        text += "\n".join("- [" + mark + "] " + value.replace("\n", " ") for value in work)
        task_file.write_text(text + "\n\nCheckmarks record progress. Requirement acceptance remains a separate review decision.\n")
    if not summary_file.exists():
        summary_file.write_text("<!-- Duo prose summary -->\n# Task summary\n\n## Requirement\n\n" + item["specification"] +
            "\n\n## Outcomes\n\nNo verbal outcome summary has been saved yet. Read the saved outputs and task state for current facts.\n\n"
            "## Next steps\n\nRead task.md for the current work items. A small summary agent will describe the next steps after a task update.\n")
    if item.get("task_file") != str(task_file) or item.get("summary_file") != str(summary_file):
        event(run_dir, "task_documents", task_id, task_file=str(task_file), summary_file=str(summary_file))


def prepare_task_documents(run_dir, root, task_id):
    item = node(run_dir, task_id)
    if not item:
        return
    outcome = next((row["outcome_dir"] for row in reversed(item["assignments"]) if row.get("outcome_dir")), None)
    folder = Path(outcome) if outcome else Path(root) / "output" / Path(root).name / Path(run_dir).name / task_id
    ensure_documents(run_dir, task_id, folder)
    save_record(run_dir, task_id)


def freeze_documents(run_dir, task_id):
    item = node(run_dir, task_id)
    saved = []
    for field in ("task_file", "summary_file"):
        source = Path(item[field]) if item.get(field) else None
        if not source or not source.is_file():
            continue
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        target = Path(run_dir) / "broker/document-versions" / task_id / (digest + "-" + source.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(data)
        saved.append({"path": str(target), "sha256": digest})
    event(run_dir, "documents_preserved", task_id, files=saved)


def error(run_dir, task_id, message, *, operation=None, attempt=None, raw=None, source="broker"):
    error_id = "error-" + secrets.token_hex(8)
    _, path = paths(run_dir, task_id)
    _append(path, dict(kind="error", id=error_id, task_id=task_id, attempt=attempt,
                       time=time.time(), message=message, operation=operation, raw=raw,
                       source=source, resolution="open"))
    event(run_dir, "error_recorded", task_id, error_id=error_id, path=str(path))
    if node(run_dir, task_id):
        save_record(run_dir, task_id)
    return error_id


def errors(run_dir, task_id=None):
    records = {}
    directory = Path(run_dir) / "diagnostics"
    files = [paths(run_dir, task_id)[1]] if task_id else directory.glob("*/errors.jsonl")
    for path in files:
        for row in _rows(path):
            if row["kind"] == "error":
                records[row["id"]] = dict(row, status_events=[], applicability={})
            elif row.get("error_id") in records:
                record = records[row["error_id"]]
                record["status_events"].append(row)
                if row.get("resolution"):
                    record["resolution"] = row["resolution"]
                    evidence = row.get("evidence", [])
                    if row["resolution"] in ("resolved", "obsolete") and evidence and row.get("workspace"):
                        if any(not isinstance(e, dict) or artifact(row["workspace"], e["path"]).get("sha256") != e.get("sha256") for e in evidence):
                            record["resolution"] = "needs_recheck"
                if row.get("applies_to"):
                    record["applicability"][row["applies_to"]] = row.get("effect", "uncertain")
    return list(records.values())


def route_result(run_dir, p, text, reply):
    """Keep an operational failure's full text only in diagnostic records."""
    if reply.get("outcome") not in ("blocked", "needs_human"):
        return text
    key = p.get("broker_task_id", p["task_id"])
    error_id = error(run_dir, key, text, attempt=p.get("delivery_id"),
                     operation=p.get("kind", p.get("stage")), raw=reply, source=p.get("agent", "agent"))
    clean = "Task work is incomplete. Saved product files remain available. Diagnostic reference: " + error_id + "."
    path = Path(run_dir) / p["file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(clean + "\n")
    return clean


def error_status(run_dir, error_id, actor, reason, *, resolution=None, applies_to=None, effect=None, evidence=()):
    record = next((row for row in errors(run_dir) if row["id"] == error_id), None)
    if not record:
        raise ValueError("Unknown diagnostic record: " + error_id)
    # A claim without evidence is recorded, not silently promoted to a repair.
    workspace = (node(run_dir, record["task_id"]) or {}).get("workspace") or str(Path.cwd())
    evidence_records = [artifact(workspace, value) for value in evidence]
    supported = evidence_records and all(value.get("sha256") for value in evidence_records)
    actual = resolution if supported or resolution not in ("resolved", "obsolete") else "reported_" + resolution
    row = dict(kind="status", error_id=error_id, time=time.time(), actor=actor, reason=reason,
               resolution=actual, applies_to=applies_to, effect=effect, evidence=evidence_records, workspace=workspace)
    _append(paths(run_dir, record["task_id"])[1], row)
    return row


def continuation(run_dir, parent_id, resume, root):
    parent = node(run_dir, parent_id)
    freeze_documents(run_dir, parent_id)
    logical = parent["logical_task_id"]
    number = 1 + sum(n.get("logical_task_id") == logical and n.get("parent") is not None for n in view(run_dir)["nodes"])
    key = logical + "-continuation-" + str(number)
    jobs = []
    for source in parent.get("assignments", []) + parent.get("checkpoints", []):
        if source.get("job_registry"):
            reference = {"registry": source["job_registry"], "task_id": source.get("job_task_id", parent_id)}
            if reference not in jobs:
                jobs.append(reference)
    for reference in parent.get("inherited_jobs", []):
        if reference not in jobs:
            jobs.append(reference)
    # Stored files are not assumed complete. The agent reviews their current hashes.
    inherited = [dict(item, current=artifact(root, item["path"])) for item in parent.get("outputs", [])]
    requirements = {}
    for identifier, requirement in parent.get("requirements", {}).items():
        item = dict(requirement)
        evidence = item.get("evidence", [])
        supported = evidence and all(isinstance(e, dict) and e.get("sha256") and
                                    artifact(root, e["path"]).get("sha256") == e["sha256"] for e in evidence)
        review = item.get("review")
        supported = supported and review and artifact(root, review["path"]).get("sha256") == review.get("sha256")
        if item.get("status") == "completed" and not supported:
            item["status"] = "needs_review"
        requirements[identifier] = item
    ensure(run_dir, key, parent["title"], parent["specification"], logical=logical, parent=parent_id,
           inherited_outputs=inherited, inherited_jobs=jobs, resume=resume,
           diagnostic_sources=[paths(run_dir, n)[1].as_posix() for n in ancestry(run_dir, parent_id)],
           inherited_requirements=requirements)
    event(run_dir, "task_status", parent_id, status="partial")
    event(run_dir, "task_status", key, status="reviewing_previous")
    save_record(run_dir, parent_id)
    save_record(run_dir, key)
    return key


def requirement(run_dir, task_id, identifier, status, text, evidence, actor, review_file=None):
    root = (node(run_dir, task_id) or {}).get("workspace") or Path.cwd()
    records = [artifact(root, path) for path in evidence]
    supported = records and all(row.get("sha256") for row in records)
    review = artifact(root, review_file) if review_file else None
    actual = status if status != "completed" or (supported and review and review.get("sha256")) else "reported_complete"
    event(run_dir, "requirement", task_id, requirement=identifier, status=actual,
          text=text, evidence=records, review=review, actor=actor)
    save_record(run_dir, task_id)


def ancestry(run_dir, key):
    result = []
    while key and key not in result:
        result.append(key)
        key = (node(run_dir, key) or {}).get("parent")
    return result


def recovery_prompt(run_dir, key):
    return ("You are continuing a partially finished task. Start by reviewing the previous task. "
            "Read the broker task record at " + str(save_record(run_dir, key)) + ". "
            "Read the inherited outputs, checkpoints, requirement evidence, and job references. "
            "Read relevant diagnostics from their separate files. Determine which work is complete, reusable, uncertain, or unfinished. "
            "Compare current output versions with their recorded versions. Do not assume saved files prove completion. "
            "Check existing jobs before proposing a replacement; an unknown job is not a failed job. "
            "Create the remaining work plan in ordinary prose. Preserve the original task scope and user requirements. "
            "Update the task-specific checkboxes in task.md so they describe the remaining work. "
            "Read inherited checkmarks as progress reports and check their supporting evidence before carrying completion forward. "
            "Explain which operations can continue independently. Do not copy raw operational errors into the plan. "
            "You may update error applicability or resolution with reasons and evidence through the optional broker state command. "
            "No particular check, command, reply format, or tool restriction is imposed by Duo. "
            "This assignment records the continuation review; the next assignment performs the remaining work.")


def finish_review(run_dir, key, path):
    event(run_dir, "review_plan", key, path=str(path))
    event(run_dir, "task_status", key, status="ready")
    item = node(run_dir, key)
    task_file = Path(item["task_file"]) if item.get("task_file") else None
    if task_file and task_file.is_file():
        text = task_file.read_text()
        source = "- [ ] Review the previous task's saved work and define the remaining requirements."
        updated = text.replace(source, source.replace("[ ]", "[x]"), 1)
        if updated != text:
            task_file.write_text(updated)
    save_record(run_dir, key)


def reserve_job(run_dir, task_id, signature, description):
    """Atomically share an intent to run work. Does not launch or deny tools."""
    path = Path(run_dir) / "broker/jobs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        records = {}
        for line in stream:
            row = json.loads(line)
            records[row["job_id"]] = row
        existing = next((r for r in records.values() if r["signature"] == signature and
                         r["status"] in ("reserved", "running", "unknown", "finished")), None)
        if existing:
            return dict(existing, reuse=True)
        row = dict(job_id="job-" + secrets.token_hex(8), task_id=task_id, signature=signature,
                   description=description, status="reserved", time=time.time())
        stream.write(json.dumps(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        return dict(row, reuse=False)


def job_update(run_dir, job_id, task_id, status, reference):
    records = _rows(Path(run_dir) / "broker/jobs.jsonl")
    old = next((r for r in reversed(records) if r["job_id"] == job_id), None)
    if not old:
        raise ValueError("Unknown recorded job: " + job_id)
    row = dict(old, status=status, updated_by=task_id, reference=reference, time=time.time())
    _append(Path(run_dir) / "broker/jobs.jsonl", row)
    return row


def read_output(root, value):
    """Read a project file for the local inspector; native tools are unaffected."""
    root = Path(root).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("This task file is unavailable in the project.")
    data = path.read_bytes()
    if b"\0" in data:
        return {"path": str(path), "kind": "binary", "size": len(data)}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": str(path), "kind": "binary", "size": len(data)}
    return {"path": str(path), "kind": "text", "content": text, "size": len(data)}


def questions(run_dir):
    records = {}
    for row in _rows(Path(run_dir)/"broker/events.jsonl"):
        if row["kind"] == "question":
            records.setdefault(row["question_id"],dict(row,status="open"))
        elif row["kind"] == "question_answer" and row["question_id"] in records:
            records[row["question_id"]].update(status="answered",answer=row["text"],answered_at=row["time"])
        elif row["kind"] == "question_context" and row["question_id"] in records:
            records[row["question_id"]].update(row["context"])
    return list(records.values())


def ask(run_dir, task_id, text, options=(), **context):
    text = str(text).strip()
    if not text: raise ValueError("Write the question first.")
    choices = [str(option) for option in options]
    identity = "question-"+hashlib.sha256(json.dumps([task_id,text,choices]).encode()).hexdigest()[:20]
    existing = next((row for row in questions(run_dir) if row["question_id"] == identity),None)
    if existing: return existing
    event(run_dir,"question",task_id,question_id=identity,text=text,options=choices)
    if context: question_context(run_dir,identity,**context)
    return next(row for row in questions(run_dir) if row["question_id"] == identity)


def question_context(run_dir, question_id, **context):
    event(run_dir,"question_context",question_id=question_id,context=context)


def import_user_questions(run_dir, task_id, text, source="agent handoff"):
    """Read labelled questions in ordinary Markdown. No reply format is required."""
    active=False; entries=[]; current=None
    for line in text.splitlines():
        if re.match(r"^#{1,5}\s",line):
            if active: break
            title=line.lower()
            active=bool(re.search(r"(user|human|your).*(decision|question)|(decision|question).*user",title))
            continue
        if not active: continue
        match=re.match(r"^[-*]\s+\*{0,2}(D\d+)[.:]?\*{0,2}\s*(.*)$",line)
        if match:
            current={"label":match[1],"lines":[match[2]],"options":[]};entries.append(current)
        elif current:
            option=re.match(r"^\s+[-*]\s+\(([a-zA-Z0-9]+)\)\s+(.+)$",line)
            if option:current['options'].append(option[2])
            elif line.strip():current['lines'].append(line.strip())
    for item in entries:
        old=next((q for q in questions(run_dir) if q.get('label')==item['label']),None)
        body='\n'.join(item['lines'])
        if old:
            if item['options'] and not old['options']: question_context(run_dir,old['question_id'],options=item['options'])
            continue
        recommendation=re.search(r"(?:I recommend[^\n]+|The default is[^\n]+|Placeholders by default[^\n]*)",body,re.I)
        ask(run_dir,task_id,item['label']+': '+body,item['options'],label=item['label'],scope='run',source=source,
            recommendation=recommendation[0] if recommendation else '',recommendation_actor='task agent')
    return len(entries)


def import_saved_questions(run_dir, root, state):
    path=Path(run_dir)/'plan.md'
    if path.is_file():
        task=state.get('build_task_id') or next((row['task_id'] for row in reversed(state.get('entries',[])) if row.get('task_id')),'run')
        import_user_questions(run_dir,task,path.read_text(errors='replace'),str(path))


def answer_question(run_dir, question_id, text):
    text = str(text).strip()
    if not text: raise ValueError("Write an answer or choose an option.")
    row = next((row for row in questions(run_dir) if row["question_id"] == question_id),None)
    if not row: raise ValueError("This question is unavailable.")
    if row["status"] == "answered":
        if row["answer"] == text: return dict(row,duplicate=True)
        raise ValueError("This question already has an answer. Send a new message to change the direction.")
    event(run_dir,"question_answer",row["node"],question_id=question_id,text=text,actor="human")
    save_record(run_dir,row["node"])
    return dict(row,status="answered",answer=text,duplicate=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--task-id", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("ask", "progress", "error", "error-status", "requirement", "job-reserve", "job-update"):
        command = sub.add_parser(action)
        command.add_argument("--text", default="")
        command.add_argument("--text-file")
        command.add_argument("--evidence", action="append", default=[])
        command.add_argument("--actor", default="task_agent")
        if action == "ask":
            command.add_argument("--option",action="append",default=[])
            command.add_argument("--recommendation",default='')
            command.add_argument("--reason",default='')
            command.add_argument("--depends-on",default='')
        if action == "error-status":
            command.add_argument("--error-id", required=True)
            command.add_argument("--resolution")
            command.add_argument("--effect")
        if action == "requirement":
            command.add_argument("--requirement", required=True)
            command.add_argument("--status", default="reported")
            command.add_argument("--review-file")
        if action == "job-reserve":
            command.add_argument("--signature", required=True)
        if action == "job-update":
            command.add_argument("--job-id", required=True)
            command.add_argument("--status", required=True)
    args = parser.parse_args()
    text = Path(args.text_file).read_text() if args.text_file else args.text
    if args.action == "ask":
        print(json.dumps(ask(args.run_dir,args.task_id,text,args.option,recommendation=args.recommendation,
                            reason=args.reason,depends_on=args.depends_on,recommendation_actor=args.actor)))
    elif args.action == "job-reserve":
        print(json.dumps(reserve_job(args.run_dir, args.task_id, args.signature, text)))
    elif args.action == "job-update":
        print(json.dumps(job_update(args.run_dir, args.job_id, args.task_id, args.status, text)))
    elif args.action == "error":
        print(error(args.run_dir, args.task_id, text, source=args.actor))
    elif args.action == "error-status":
        print(json.dumps(error_status(args.run_dir, args.error_id, args.actor, text,
                         resolution=args.resolution, applies_to=args.task_id, effect=args.effect, evidence=args.evidence)))
    elif args.action == "requirement":
        requirement(args.run_dir, args.task_id, args.requirement, args.status, text, args.evidence, args.actor, args.review_file)
    else:
        event(args.run_dir, "progress", args.task_id, text=text, source="agent_report", actor=args.actor,
              evidence=args.evidence)




CYCLE_ROLES = {'cycle_summary', 'goal_check', 'goal_assess', 'goal_assign'}


def _returned_stage_history(run_dir, nodes, progress):
    """Project saved replies omitted by older controllers without altering the event log."""
    try:
        state = json.loads((Path(run_dir) / 'state.json').read_text())
    except (OSError, ValueError):
        return
    pending = state.get('pending', [])
    if isinstance(pending, dict):
        pending = list(pending.values())
    active = {p.get('broker_task_id', p.get('task_id')) for p in pending}
    reports = {r.get('task_id'): r for r in state.get('entries', state.get('ledger', []))
               if r.get('kind', r.get('stage')) in CYCLE_ROLES}
    with_progress = {row.get('node') for row in progress}
    for key, node in nodes.items():
        row = reports.get(key)
        if node.get('current_role') not in CYCLE_ROLES or key in active or not row:
            continue
        if node['status'] in ('working', 'reviewing_previous'):
            node['status'] = 'stage_completed'
            node['status_source'] = 'saved_stage_reply'
        if key not in with_progress:
            text = row.get('prose') or row.get('summary')
            if not text and row.get('file'):
                text = _document_text(Path(run_dir) / row['file'])
            if text:
                progress.append(dict(id='saved-stage-' + key, time=row.get('time', node['updated']),
                    kind='progress', node=key, text=text, source='saved_stage_reply',
                    role=row.get('kind', row.get('stage')), recovered_from_saved_history=True))


def record_stage_reply(run_dir, pending, root, reply, artifacts=()):
    """Record stage handoffs before control passes to another worker."""
    data = reply if isinstance(reply, dict) else reply.to_dict()
    root = Path(root).resolve()
    paths = [Path(run_dir) / pending['file']] + [Path(p) for p in artifacts if p]
    outputs = [p.resolve().relative_to(root).as_posix() for p in paths
               if p.is_file() and p.resolve().is_relative_to(root)]
    checkpoint(run_dir, pending, root, outputs, summary=data.get('prose'),
               outcome=data.get('outcome') or 'progress')
    if data.get('outcome') in ('blocked', 'needs_human'):
        event(run_dir, 'task_status', pending.get('broker_task_id', pending['task_id']), status='partial')


if __name__ == "__main__":
    main()
