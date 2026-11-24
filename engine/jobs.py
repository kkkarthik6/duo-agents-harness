"""Task-related process liveness. Quiet jobs are not stalled agent turns."""
import argparse
import fcntl
import json
import math
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import config
from engine import tmux as tmuxmod


def process_table():
    result = subprocess.run(["ps", "-axo", "pid=,ppid=,stat=,lstart=,time=,args="],
                            capture_output=True, text=True, timeout=3)
    if result.returncode:
        raise OSError("Process inspection failed")
    rows = {}
    for line in result.stdout.splitlines():
        fields = line.split(None, 9)
        if len(fields) != 10:
            continue
        try:
            pid, parent = int(fields[0]), int(fields[1])
            argv = shlex.split(fields[9])
        except (ValueError, IndexError):
            continue
        name = Path(argv[0]).name if argv else ""
        infrastructure = name in {"codex", "claude", "ps", "tmux"}
        infrastructure |= bool(re.match(r"codex[-_]", name))
        infrastructure |= any(Path(a).name == "codex_worker.py" for a in argv)
        infrastructure |= any("mcp" in a.lower() or "language-server" in a.lower() for a in argv[:3])
        rows[pid] = {"pid": pid, "parent": parent, "identity": "%s@%s" % (pid, " ".join(fields[3:8])),
                     "state": fields[2], "cpu": fields[8], "infrastructure": infrastructure}
    return rows


def descendants(table, root):
    found = {root} if root else set()
    while True:
        more = {pid for pid, row in table.items() if row["parent"] in found}
        if more <= found:
            return found
        found |= more


def pane_pid(pane):
    if not pane:
        return None
    result = subprocess.run(tmuxmod.command("display-message", "-p", "-t", pane, "#{pane_pid}"),
                            capture_output=True, text=True, timeout=2)
    return int(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip().isdigit() else None


def load(path, task_id):
    try:
        data = json.loads(Path(path).read_text())
        return data.get("jobs", []) if data.get("task_id") == task_id else []
    except (OSError, ValueError):
        return []


def save(path, task_id, records, extend=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        merged = {r["identity"]: r for r in load(path, task_id)}
        for record in records:
            old = merged.get(record["identity"], {})
            item = dict(record)
            if old.get("deadline_at") and not extend:
                for key in ("deadline_at", "runtime_limit_seconds", "estimated_runtime_seconds", "estimate_basis", "runtime_multiplier"):
                    if key in old:
                        item[key] = old[key]
            merged[record["identity"]] = item
        tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
        tmp.write_text(json.dumps({"task_id": task_id, "jobs": list(merged.values())}, indent=2))
        os.replace(tmp, path)


def begin(pending, pane, registry):
    pending["job_registry"] = str(registry)
    try:
        root = pane_pid(pane)
        table = process_table() if root else {}
        pending["process_root"] = root
        pending["process_baseline"] = [table[pid]["identity"] for pid in descendants(table, root) if pid in table]
    except (OSError, subprocess.TimeoutExpired):
        pending["process_root"] = None
        pending["process_baseline"] = []


def inspect_container(container):
    result = subprocess.run(["docker", "container", "inspect", container], capture_output=True, text=True, timeout=3)
    if result.returncode:
        if re.search(r"no such (?:object|container)", result.stderr, re.I):
            return {"identity": None, "alive": False, "task_id": None}
        raise OSError("Container inspection failed")
    data = json.loads(result.stdout)[0]
    return {"identity": data["Id"] + "@" + data["State"]["StartedAt"],
            "alive": bool(data["State"].get("Running")),
            "task_id": (data.get("Config", {}).get("Labels") or {}).get("duo.task_id")}


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Runtime values must be finite positive numbers")
    return number


def set_deadline(record, pending, now):
    if record.get("deadline_at"):
        return
    estimate = record.get("estimated_runtime_seconds") or pending.get("estimated_runtime_seconds")
    factor = positive(config.CFG.get("job_runtime_multiplier", 1.5))
    limit = positive(positive(estimate) * factor) if estimate else positive(config.CFG.get("long_job_timeout_seconds", 21600))
    record.update(runtime_limit_seconds=limit, runtime_multiplier=factor,
                  deadline_at=record.get("observed_at", now) + limit,
                  estimated_runtime_seconds=positive(estimate) if estimate else None,
                  estimate_basis=record.get("estimate_basis") or pending.get("estimate_basis") or "No estimate; fallback limit")


def budget_extension(state, pending, now):
    """Credit the union of monitored job waits, never their sum across parallel tasks."""
    checked = state.get("job_budget_checked_at", now)
    if state.get("job_budget_waiting"):
        end = min(now, state.get("job_budget_deadline_at", now))
        state["job_budget_credit_seconds"] = state.get("job_budget_credit_seconds", 0) + max(0, end - checked)
    waiting = []
    for p in pending:
        if p.get("stage", p.get("kind")) in ("build", "revise", "research") and p.get("task_id"):
            if poll(p, now) == "waiting":
                waiting.append(p.get("job_deadline_at", now))
    state.update(job_budget_checked_at=now, job_budget_waiting=bool(waiting),
                 job_budget_deadline_at=max(waiting, default=now))
    return state.get("budget_extension", 0) + state.get("job_budget_credit_seconds", 0) / 3600


def extend_deadlines(pending, now):
    """Called only by the user resume action after a runtime pause."""
    for p in pending:
        if p.get("job_status") != "expired":
            continue
        records = load(p["job_registry"], p["task_id"])
        for record in records:
            if record.get("deadline_at", math.inf) <= now:
                record["deadline_at"] = now + record.get("runtime_limit_seconds", 21600)
        save(p["job_registry"], p["task_id"], records, extend=True)
        unknown = p.get("unconfirmed_job", {})
        if unknown.get("deadline_at", math.inf) <= now:
            unknown["deadline_at"] = now + unknown["runtime_limit_seconds"]
        p.pop("jobs_sampled_at", None)
        p.pop("job_timeout_notified", None)


def resume_budget(state, now):
    paused = state.pop("job_runtime_paused_at", now)
    state["budget_extension"] = state.get("budget_extension", 0) + max(0, now - paused) / 3600
    state.update(job_budget_checked_at=now, job_budget_waiting=False)


def timeout_message(pending):
    hours = pending.get("job_limit_seconds", config.CFG.get("long_job_timeout_seconds", 21600)) / 3600
    return ("Task %s exceeded its %.2g-hour job limit. The job was preserved. "
            "Resume to extend the deadline." % (pending["task_id"], hours))


def poll(pending, now, force=False):
    """Return waiting, expired, or idle. Never treat liveness as completion."""
    registry = pending.get("job_registry")
    try:
        stamp = Path(registry).stat().st_mtime_ns if registry else 0
    except OSError:
        stamp = 0
    if not force and now - pending.get("jobs_sampled_at", -1e30) < 15 and stamp == pending.get("jobs_registry_stamp", 0):
        return pending.get("job_status", "idle")
    records = load(registry, pending["task_id"]) if registry else []
    if not pending.get("process_root") and not records:
        return "idle"
    pending["jobs_sampled_at"] = now
    table, unavailable = {}, False
    try:
        if pending.get("process_root") or any(r.get("pid") for r in records):
            table = process_table()
    except (OSError, subprocess.TimeoutExpired):
        unavailable = True
    known = {r.get("identity") for r in records}
    baseline = set(pending.get("process_baseline", []))
    root = pending.get("process_root")
    if root in table and table[root]["identity"] not in baseline:
        root = None  # The original terminal PID was reused.
    for pid in descendants(table, root):
        row = table.get(pid)
        if row and not row["infrastructure"] and row["identity"] not in baseline | known:
            records.append({"pid": pid, "identity": row["identity"], "observed_at": now})
            known.add(row["identity"])
    active = []
    for record in records:
        if record.get("container"):
            try:
                row = inspect_container(record["container"])
                alive = row["alive"] and row["identity"] == record["identity"] and row["task_id"] == pending["task_id"]
            except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
                alive, unavailable = True, True  # Do not start a duplicate job on inspection failure.
        else:
            row = table.get(record.get("pid"), {})
            alive = (row.get("identity") == record.get("identity") and
                     not any(s in row.get("state", "") for s in ("Z", "X")))
            if unavailable:
                alive = True
        if alive:
            set_deadline(record, pending, now)
            active.append(record)
    if unavailable and pending.get("process_root") and not active:
        unknown = pending.setdefault("unconfirmed_job", {"inspection": "unavailable", "observed_at": now})
        set_deadline(unknown, pending, now)
        active.append(unknown)
    if registry and records:
        save(registry, pending["task_id"], records)
        stamp = Path(registry).stat().st_mtime_ns
    pending["jobs_registry_stamp"] = stamp
    previous = pending.get("job_status")
    if active:
        pending.setdefault("job_wait_started", now)
        first = min(active, key=lambda r: r["deadline_at"])
        pending["job_deadline_at"] = first["deadline_at"]
        pending["job_limit_seconds"] = first["runtime_limit_seconds"]
        timed = config.CFG.get('unfinished_timeouts_enabled', False) or unavailable
        pending["job_status"] = "expired" if timed and now >= first["deadline_at"] else "waiting"
        if not timed:
            pending['job_deadline_at'] = max(first['deadline_at'], now + 30)
        observe_logs(pending, active, now)
        pending["active_jobs"] = active
        pending["job_inspection_unavailable"] = unavailable
    else:
        pending["job_status"] = "idle"
        pending["active_jobs"] = []
        if previous in ("waiting", "expired"):
            pending["progress_at"] = now  # Give the agent time to collect the result.
            pending["resumed_at"] = now
            pending["progress_nudged"] = False
        pending.pop("job_wait_started", None)
    return pending["job_status"]


def observe_logs(pending, records, now):
    """Watch only logs registered to this task, not another run's output."""
    versions = {}
    for record in records:
        for name in record.get('log_paths', []):
            try:
                stamp = Path(name).stat()
                versions[name] = [stamp.st_size, stamp.st_mtime_ns]
            except OSError:
                continue
    if any(value != pending.get('job_log_versions', {}).get(name) for name, value in versions.items()):
        pending['job_log_updated_at'] = now
    pending['job_log_versions'] = versions


def instructions(pending, workspace):
    script = Path(__file__).resolve()
    registry = os.path.relpath(pending["job_registry"], workspace)
    command = shlex.join([sys.executable, str(script), "register", "--workspace", ".",
                          "--registry", registry, "--task-id", pending["task_id"]])
    return ("\nLong jobs: before detaching one that runs over about 2 minutes, register it once with `" + command +
            " --pid <PID> --log <log path> --estimate-seconds <total> --estimate-basis '<why>'` "
            "(add --container <ID> for a container labelled duo.task_id=" + pending["task_id"] +
            "). Reuse the recorded job ID; registered jobs have no time cutoff.\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("register",))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--estimate-seconds", type=positive)
    parser.add_argument("--estimate-basis", default="")
    parser.add_argument("--log", action='append', default=[])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pid", type=int)
    group.add_argument("--container")
    args = parser.parse_args()
    root, registry = Path(args.workspace).resolve(), Path(args.registry).resolve()
    if root not in registry.parents or registry.parent.name != "jobs" or registry.name != args.task_id + ".json":
        parser.error("Use the task's assigned jobs/<task_id>.json registry inside the project")
    log_paths = []
    for name in args.log:
        candidate = (root / name).resolve()
        if not candidate.is_relative_to(root):
            parser.error('Job logs must stay inside the assigned project')
        log_paths.append(str(candidate))
    record = {"observed_at": time.time(), "log_paths": log_paths}
    if args.estimate_seconds:
        if not args.estimate_basis.strip():
            parser.error("Explain the runtime estimate with --estimate-basis")
        record.update(estimated_runtime_seconds=args.estimate_seconds, estimate_basis=args.estimate_basis)
    if args.pid:
        row = process_table().get(args.pid)
        if not row or row["infrastructure"]:
            parser.error("PID is not a live workload")
        # A detached job must have its working directory in the assigned project.
        proc_cwd = Path('/proc/%s/cwd' % args.pid)
        if proc_cwd.exists():
            cwd = proc_cwd.resolve()
        else:
            result = subprocess.run(["lsof", "-a", "-p", str(args.pid), "-d", "cwd", "-Fn"],
                                    capture_output=True, text=True, timeout=3)
            cwd = next((Path(x[1:]).resolve() for x in result.stdout.splitlines() if x.startswith("n")), None)
        if cwd != root and (cwd is None or root not in cwd.parents):
            parser.error("PID working directory must be inside the assigned project")
        record.update(pid=args.pid, identity=row["identity"])
    else:
        row = inspect_container(args.container)
        if not row["alive"] or row["task_id"] != args.task_id:
            parser.error("Container must be running with the assigned duo.task_id label")
        record.update(container=args.container, identity=row["identity"])
    records = load(registry, args.task_id)
    old = next((r for r in records if r.get("identity") == record["identity"]), {})
    if not args.log and old.get('log_paths'):
        record['log_paths'] = old['log_paths']
    if args.estimate_seconds and old.get("estimated_runtime_seconds") and args.estimate_seconds != old["estimated_runtime_seconds"]:
        parser.error("The job estimate is fixed. Resume the paused run to extend its deadline")
    initial_estimate = bool(args.estimate_seconds and old.get("deadline_at") and not old.get("estimated_runtime_seconds"))
    if initial_estimate:
        if time.time() >= old["deadline_at"]:
            parser.error("The job deadline has passed. Resume the paused run before changing its budget")
        record["observed_at"] = old.get("observed_at", record["observed_at"])
        set_deadline(record, {}, record["observed_at"])
    records = [r for r in records if r.get("identity") != record["identity"]] + [record]
    save(registry, args.task_id, records, extend=initial_estimate)
    print("Registered job for task_id " + args.task_id)


if __name__ == "__main__":
    main()
