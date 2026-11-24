"""The helper: a read-only agent that watches the run and answers the person's questions.

It runs one non-interactive CLI call for each question (claude -p, codex exec). It never touches the panes
of the working agents, so a question never interrupts the work. If no CLI answers, a digest built from the
run state answers instead.
"""
import os
import shutil
import subprocess
import time
import json
from pathlib import Path

from . import agents, broker, cli_adapters, model_catalog, agent_failures

CLI = {"claude": lambda p: ["claude", "-p", p], "codex": lambda p: ["codex", "--no-daemon", "exec", "--ephemeral", p]}
RULES = ("You are the Duo helper. You watch a run in which AI agents build something for the person. "
         "Answer the person's question from the run facts below. Write short, plain sentences. "
         "If the facts do not answer it, say what is missing. Do not change any file and do not run any command that changes anything.")


def _short(text, n=220):
    return str(text or "")


def context(st, focus=None):
    if st.get("broker_dir"):
        facts = broker.view(st["broker_dir"])
        lines = ["Overall task: " + st.get("task", ""),
                 "Run status: " + st.get("status", "unknown"),
                 "Task state is broker-recorded. Agent progress is reported progress, not automatic proof of completion."]
        if facts.get("completion_summary"):
            completion = facts["completion_summary"]
            lines += ["Detailed completion handoff: " + str({key:completion.get(key) for key in
                       ("status","model","path","next_run","version","time")}),
                      "The handoff stage is separate from task completion. Read its user actions and next-run plan when available."]
        for task in facts["nodes"]:
            if focus and focus in {n["id"] for n in facts["nodes"]} and task["id"] != focus:
                continue
            lines += ["Task: %s. Parent: %s. State: %s. Last update: %s." %
                      (task["id"], task.get("parent"), task["status"], task["updated"]),
                      "Specification: " + task["specification"],
                      "Task record: " + str(broker.paths(st["broker_dir"], task["id"])[0]),
                      "Diagnostics file: " + str(broker.paths(st["broker_dir"], task["id"])[1]),
                      "Inherited diagnostic files: " + str(task.get("diagnostic_sources", [])),
                      "Saved outputs: " + str(task["outputs"]),
                      "Requirements: " + str(task["requirements"]),
                      "Inherited jobs: " + str(task.get("inherited_jobs", []))]
        lines += ["User questions and recorded answers: " + str(facts.get("questions",[]))]
        lines += ["Successful progress reports (read their evidence and timestamps):"]
        lines += ["%s / %s / %s: %s" % (row["time"], row["node"], row.get("source"), row["text"]) for row in facts["progress"]]
        lines += ["Read the named error files when answering diagnostic questions. Explain the recorded operation, effect, status, and uncertainty. "
                  "Do not treat an old error as a current blocker without checking its applicability. Do not modify tasks, files, or error status."]
        return "\n".join(lines)
    lines = ["Task: %s" % _short(st.get("task"), 600), "Phase: %s. Status: %s." % (st.get("phase"), st.get("status"))]
    lines.append("Recent events:")
    for e in st.get("entries", []):
        lines.append("- #%s %s %s: %s" % (e["n"], e["author"], e["kind"], _short(e.get("prose") or e.get("summary"))))
    return "\n".join(lines)


def run_cli(agent, cwd, prompt, timeout=120, model=None, read_only=False):
    if agent not in CLI:
        try:
            adapter = cli_adapters.get(agent)
        except ValueError as error:
            return False, str(error)
        exe = shutil.which(adapter.executable)
        if not exe:
            return False, agent + ' is not available'
        try:
            result = subprocess.run(adapter.argv([exe], model, prompt), input=adapter.encode_prompt(prompt) if adapter.prompt_mode == 'stdin' else '', cwd=cwd,
                                    capture_output=True, text=True, timeout=timeout)
            if result.returncode:
                return False, result.stderr or result.stdout
            if adapter.protocol == 'text':
                return bool(result.stdout.strip()), result.stdout.strip()
            replies = []
            for line in result.stdout.splitlines():
                event = json.loads(line)
                for operation, value in adapter.events(event):
                    if operation == 'failure':
                        return False, json.dumps(value)
                    if operation == 'text' and value and value not in replies:
                        replies.append(value)
            return bool(replies), '\n'.join(replies)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            return False, str(error)
    exe = shutil.which(agent)
    if agent not in CLI or not exe:
        return False, "%s is not available" % agent
    cmd = CLI[agent](prompt)
    cmd[0] = exe
    if model:
        cmd[-1:-1] = ["--model", model]
    if read_only:
        if agent == 'codex':
            cmd[-1:-1] = ['--sandbox','read-only','--skip-git-repo-check']
        elif agent == 'claude':
            cmd[-1:-1] = ['--tools','Read,Glob,Grep','--allowedTools','Read,Glob,Grep',
                         '--permission-mode','dontAsk','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--']
    cmd = agents.guarded_argv(agent, cmd, cwd)
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env=dict(agents.codex_env() if agent == "codex" else os.environ, DISABLE_AUTOUPDATER="1"))
    except Exception as ex:
        return False, str(ex)
    out = (r.stdout or "").strip()
    return (r.returncode == 0 and bool(out)), out or (r.stderr or "").strip()


def model_routes(run_dir, providers, tier, setting):
    """Respect run availability in optional summaries as well as task workers."""
    try:
        state = json.loads((Path(run_dir) / 'state.json').read_text())
    except (OSError, ValueError):
        state = {'agents': list(providers)}
    return [(r['cli'], r['id']) for r in model_catalog.candidates(state, tier)]


def digest(st, focus):
    if st.get("broker_dir"):
        facts = broker.view(st["broker_dir"])
        selected = [n for n in facts["nodes"] if n["id"] == focus] if focus else facts["nodes"]
        lines = ["The helper agent is unavailable. These are saved task facts."]
        lines += ["%s: %s; last saved update %s." % (n["id"], n["status"], n["updated"]) for n in selected]
        lines += [row["text"] for row in facts["progress"] if not focus or row["node"] == focus]
        lines += ["Recorded diagnostic for %s: %s. Resolution: %s. Meaning requires checking the full diagnostic." % (r["task_id"], r["message"], r["resolution"]) for r in broker.errors(st["broker_dir"]) if not focus or r["task_id"] == focus]
        return "\n".join(lines)
    lines = ["The helper agent is unavailable. This is the recorded run summary.",
             "Phase %s, status %s." % (st.get("phase"), st.get("status"))]
    return "\n".join(lines)


def ask(st, agents, cwd, question, focus=None):
    prompt = "%s\n\n--- Run facts ---\n%s\n\n--- Question ---\n%s" % (RULES, context(st, focus), question)
    ok, text = False, ""
    for a in agents:
        ok, text = run_cli(a, cwd, prompt)
        if ok:
            break
    item = {"q": question, "focus": focus, "a": text if ok else digest(st, focus), "fallback": not ok, "at": time.time()}
    st.setdefault("helper", []).append(item)
    return item
