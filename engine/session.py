"""One duo session: agents in tmux panes, driven through the breadth-first phases.

Flow per turn: nudge agent -> agent writes replies/NNNN-agent.md ending in <!-- END -->
-> parse + bounds -> bounce once if broken -> entry -> next phase step.
"""
import json
import hashlib
import os
import re
import secrets
import shutil
import statistics
import subprocess
import threading
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from . import agents as agentmod, harness, models, shots, bounds, config, history, insights, memory, notify, personal, phases, sandbox, security, trust
from . import build_workflow as buildflow, research_workflow as researchflow
from . import repeat_defect, loop_judge, jobs, delivery, model_policy, relay, task_output, broker, summary_agent, orchestration as orch
from .tmux import capture, pane_dead, pane_env, send_key, send_text, split, tail_lines, tmux
from .active_runtime import Counter
from . import initial_planning
from . import completion_summary
from . import task_context
from . import session_index
from . import workers
from . import run_chat, project_handoff, goal_loop, roleflow, skill_inventory, agent_failures, check_policy, token_ledger, handoff, triage, codex_guard

TURN_MARK = "DUO turn #"

LOCK = threading.RLock()
VERSION = [0]
ACTIVE = ("booting", "waiting", "running", "paused")
TRUST_HINTS = ("do you trust", "trust the files", "trust this folder", "trust this directory",
               "allow codex to work", "yes, proceed")
UPDATE_HINTS = ("update available", "update now", "new version available")
APPROVAL_HINTS = ("do you want to", "allow this", "approve this", "(y/n)", "[y/n]", "don't ask again", "do you approve")
APPROVE_KEYS = {"claude": "Enter", "codex": "y", "agy": "Enter"}
DENY_KEYS = {"claude": "Escape", "codex": "n", "agy": "Escape"}
LOGIN_HINTS = ("please log in", "please sign in", "run /login", "sign in with chatgpt", "select login method",
               "not logged in", "login required")
INSTRUCTIONS = {"cycle_summary": "Write the detailed cycle summary.", "goal_assess": "Assess the unfinished goal independently.", "goal_assign": "Plan substantial further work from both assessments.", "goal_check": "Assess the whole user goal using the saved goal input.", "continuation_review": "Review the previous task and define the remaining work.", 'research_expand': 'Expand the research coverage and derive a method that addresses a specific drawback.',
 'research_check': 'Independently challenge research coverage, full-paper reading, relevance, and the proposed method.',
 'feasibility': 'Check feasibility before build and save the build work packages.', 'frame': 'Restate the task. Set task_type. Write measurable acceptance requirements and constraints.',
 'build': 'Build the accepted plan. Finish the output before review.',
 'revise': 'Resolve every required defect together and reuse valid checks.',
 'critique': 'Review the completed output against the task requirements and saved evidence, using the assessment method that fits the task.',
 'verify': 'Assess the requested output for {chosen} using a method relevant to the task. Return '
           'APPROVED after every requirement passes.',
 'setup': 'Prepare the runtime for {chosen}. Write {d}/sandbox/Dockerfile, sandbox.json, prereqs.md, and review '
          'inputs. List tool versions, the run command, a short startup check, and ports. Pin dependencies. Keep '
          'full acceptance checks separate from the startup check.',
 'setup_check': 'Check {d}/sandbox/ against {chosen}. Fix missing dependencies, version gaps, and startup checks. '
                'Write review.md with Gaps found and Changes made.',
 'setup_fix': 'The sandbox build failed. Fix {d}/sandbox/Dockerfile or sandbox.json. Record the cause and change. '
              'Log: {gate_log}',
 'secure': 'Read the recorded changes for {chosen}. Check credentials, injection, network access, and command '
           'safety. Record concrete security defects. Return APPROVED after all high-severity defects are '
           'resolved.'}


def classify_screen(text):
    """What a startup screen is asking for: login, update, trust, or nothing (ready)."""
    t = text.lower()
    if any(h in t for h in LOGIN_HINTS):
        return "login"
    if any(h in t for h in UPDATE_HINTS):
        return "update"                      # never auto-answer: Enter usually means "Update now"
    if any(h in t for h in APPROVAL_HINTS):
        return "approval"
    if any(h in t for h in TRUST_HINTS):
        return "trust"
    return None


def question_text(screen):
    """The approval question plus the lines around it (the command often sits just above)."""
    lines = [l for l in screen.splitlines() if l.strip()]
    idx = next((i for i, l in enumerate(lines) if any(h in l.lower() for h in APPROVAL_HINTS)), len(lines) - 1)
    return "\n".join(lines[max(0, idx - 6): idx + 6]).strip()


def bump():
    VERSION[0] += 1


def resolve_sandbox(val):
    """Turn a sandbox choice into yes or no for one run. auto means yes only when Docker runs now."""
    if isinstance(val, bool):
        return val
    if val == "on":
        return True
    if val == "off":
        return False
    if val == "auto":
        return bool(sandbox.available()[0])
    raise ValueError("sandbox must be on, off or auto")


def now_hm():
    return datetime.now().strftime("%H:%M:%S")


def slugify(text, n=40):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:n].strip("-") or "task"


def index_path():
    return config.root() / ".duo-index.json"


def history_path():
    return config.root() / ".duo-history.jsonl"


def _git_status(ws):
    if not (Path(ws) / ".git").exists():
        return {}
    r = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ws,
                       capture_output=True, text=True)
    return {line[3:]: line[:2] for line in r.stdout.splitlines() if len(line) > 3}


def _worktree_state(ws):
    """Content fingerprints also work in projects without Git. Ignore harness reply files."""
    out = harness.progress_state(ws)
    files = []
    logs = Path(ws) / ".duo" / "logs"
    if logs.is_dir():
        files.extend(p for p in logs.rglob("*") if p.is_file())
    for p in files:
        path = p.relative_to(ws).as_posix()
        try:
            st = p.stat()
            out[path] = ["M", harness._sha(p), st.st_size]
        except OSError:
            continue
    return out


def _source_fingerprint(ws):
    # Data is excluded from routine project scans. Do not reuse a smoke result
    # when untracked data inputs could have changed outside that scan.
    if (Path(ws) / "data").exists():
        return None
    state = {k: v for k, v in _worktree_state(ws).items() if not k.startswith(".duo/")}
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


class Session:
    def __init__(self, st):
        self.st = st
        self._runtime = Counter(st)
        self._boot_prev = {}
        self._last_answer = {}
        broker.import_history(self.dir, self.ws, st)
        broker.import_diagnostic_history(self.dir, st)
        broker.migrate_saved_failure(self.dir, st)
        broker.prepare_historical_documents(self.dir, self.ws, st)
        broker.import_saved_questions(self.dir,self.ws,st)
        if st.get("status") == "done" and model_policy.native_launches(st):
            completion_summary.request(self.dir,self.ws,st)

    # ------------------------------------------------------------------ paths
    @property
    def ws(self):
        return Path(self.st["workspace"])

    @property
    def dir(self):
        return Path(self.st["duo_dir"])

    @property
    def rel(self):
        return ".duo/" + self.dir.name

    def label(self, agent):
        return orch.alias(self.st, agent)

    def _diagnostic(self, message, p=None, operation=None):
        p = p or self.st.get("failed_turn") or next(iter(self.st.get("pending", [])), None)
        key = (p or {}).get("broker_task_id") or (p or {}).get("task_id") or self.st.get("build_task_id") or "session"
        broker.ensure(self.dir, key, self.st.get("title", "Task"), self.st["task"])
        return broker.error(self.dir, key, message, operation=operation or (p or {}).get("kind"),
                            attempt=(p or {}).get("delivery_id"))

    def _queue_continuation(self, p):
        p = dict(p)
        p.setdefault("file", "replies/recovered-" + p["task_id"] + ".md")
        p.setdefault("kind", "revise")
        p.setdefault("phase", "deep")
        p.setdefault("agent", self.st["roles"]["builder"])
        parent = p.get("broker_task_id") or p["task_id"]
        broker.ensure(self.dir, parent, self.st.get("title", "Task"), self.st["task"])
        if not broker.node(self.dir, parent)["assignments"]:
            broker.assign(self.dir, dict(p, agent=p.get("agent", self.st["roles"]["builder"])),
                          self.st.get("title", "Task"), self.st["task"], self.ws)
            outputs = [x["path"] for x in self.st.get("task_delivery", {}).get("outputs", [])]
            broker.checkpoint(self.dir, p, self.ws, outputs)
        if p.get("failure_problems"):
            message = "\n".join(p["failure_problems"])
            if not any(row["message"] == message for row in broker.errors(self.dir, parent)):
                self._diagnostic(message, p)
        existing = [x["path"] for x in broker.node(self.dir, parent)["outputs"]]
        current = _worktree_state(self.ws)
        partial = [path for path, value in current.items() if p.get("base", {}).get(path) != value and (self.ws / path).is_file()]
        broker.checkpoint(self.dir, dict(p, agent=p.get("agent", self.st["roles"]["builder"])), self.ws, sorted(set(existing + partial)))
        resume = {key: p[key] for key in ("agent", "phase", "kind", "option_ids", "task_id") if key in p}
        child = broker.continuation(self.dir, parent, resume, self.ws)
        self.st.pop("research_findings", None)
        self.st.setdefault("broker_continuations", []).append({"id": child, "resume": resume})
        self.st.setdefault("broker_resume", {})[child] = resume
        self.st.pop("failed_turn", None)
        self.st["pending"] = [item for item in self.st.get("pending", []) if item.get("task_id") != p["task_id"]]
        return child

    def _dispatch_continuation(self):
        queue = self.st.get("broker_continuations", [])
        if queue:
            item = queue.pop(0)
            self.issue(phases.Turn(self.st["roles"]["critic"], "deep", "continuation_review"),
                       retry_of={"task_id": item["id"], "base": _worktree_state(self.ws), "issued_at": time.time()})
            return True
        ready = self.st.pop("broker_resume_ready", None)
        if ready:
            original = ready["resume"]
            kind = original["kind"]
            if kind in ("build", "revise", "critique", "verify", "secure"):
                kind, phase, agent = "revise", "deep", self.st["roles"]["builder"]
                self.st.update(build_task_id=ready["id"], phase="deep", deep_step="build", ready_for_review=False)
            else:
                phase, agent = original["phase"], original["agent"]
            self.issue(phases.Turn(agent, phase, kind, original.get("option_ids", [])),
                       retry_of={"task_id": ready["id"], "base": _worktree_state(self.ws), "issued_at": time.time()})
            return True
        return False

    # --------------------------------------------------------------- creation
    @classmethod
    def create(cls, task, agents, project=None, project_name=None, workspace=None, mode=None,
               task_type=None, max_rounds=None, approvals="auto", title=None, sandbox_on=False, planning_route=None):
        if not agents:
            raise ValueError("No agent CLIs found. Install Claude Code, Codex or Antigravity.")
        incompatible = [a.id for a in agents if a.capability_check and not a.capability_check['ready']]
        if incompatible:
            raise ValueError('CLI capability check failed: ' + ', '.join(incompatible))
        agents = agents[:3]
        mode = mode or config.CFG["decision_mode"]
        if mode not in config.MODES:
            raise ValueError("mode must be one of %s" % ", ".join(config.MODES))
        if task_type not in config.TASK_TYPES + (None, "auto"):
            raise ValueError("unknown task type")
        root = config.root()
        root.mkdir(parents=True, exist_ok=True)
        nonce = secrets.token_hex(4)
        sid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + slugify(title or task, 24) + '-' + nonce
        if project:
            ws = root / project
            if not ws.is_dir():
                raise ValueError("No project named '%s'" % project)
        elif workspace:
            ws = Path(os.path.expanduser(workspace)).resolve()
            if not ws.is_dir():
                raise ValueError("Folder does not exist: %s" % ws)
        else:
            ws = trust.unique_project_dir(project_name or slugify(title or task, 40))
        if trust.in_root(ws) or not ws.exists():
            trust.setup_project(ws)
        else:
            trust.trust_folder(ws)
        ws = ws.resolve()
        dynamic_models = any(a.capability_check is not None for a in agents)
        if dynamic_models:
            from . import model_discovery
            model_discovery.refresh(force=True, cwd=ws)
        size, why = triage.classify(task) if config.CFG.get("triage", "auto") == "auto" else ("standard", "triage is off")
        route = planning_route or initial_planning.select(task, agents, task_type)
        if route["profiles"] != ["direct"] or task_type == "research":
            size, why = "standard", "Initial planning: " + ", ".join(route["profiles"])
        if size == "trivial" and len(agents) > 1:        # one agent does a quick task, so only that one is started
            probe = {"agents": [a.id for a in agents], "workspace": str(ws), "dynamic_models": dynamic_models, "roles": {}}
            agent_failures.seed(probe)
            try:
                pick = roleflow.ensure(probe)["builder"]["agent"]
            except ValueError:
                pick = None
            standby = [a for a in agents if a.id != pick] if any(a.id == pick for a in agents) else []
            agents = [a for a in agents if a.id == pick] or agents
        else:
            standby = []
        d = ws / ".duo" / (sid[4:15] + '-' + nonce)
        (d / "replies").mkdir(parents=True, exist_ok=True)
        (d / "sandbox").mkdir(exist_ok=True)
        ids = [a.id for a in agents]
        ttype = task_type if task_type in config.TASK_TYPES else None
        st = {
            "dynamic_models": dynamic_models, "workflow": "roles", "goal_loop_version": 2, "goal": {"status": "active", "cycles": [], "no_progress": 0, "seen_evidence": {}},
            "id": sid, "title": title or task.strip().splitlines()[0], "task": task,
            "mode": mode, "approvals": approvals, "workspace": str(ws), "duo_dir": str(d), "project": ws.name,
            "created": datetime.now().isoformat(timespec="seconds"), "project_root": str(root.resolve()),
            "status": "booting", "detail": "Starting agents", "paused": False,
            "agents": ids, "agent_info": {a.id: {"label": a.label, "version": a.version,
                                                 "cmd": (agentmod.boxed(a.id, a.cmd_auto) if sandbox_on else a.cmd_auto) if approvals == "auto" else a.cmd_manual} for a in agents},
            "panes": {}, "booted_at": None, "auto_answered": {a: 0 for a in ids},
            # phase-engine state (engine.phases)
            "phase": None, "task_type": ttype, "user_task_type": ttype,
            "roles": {},
            "options": [], "round": 0, "max_rounds": int(max_rounds or config.CFG["max_rounds"]),
            "verify_failures": 0, "ranking": [], "chosen": None, "tried": [], "last_verdict": None,
            "verify_passed": None, "deep_step": None, "fallback": False,
            # collected data
            "entries": [], "pending": [], "handoff_log": [], "last_seen": {a: 0 for a in ids},
            "protocol_sent": {a: False for a in ids}, "single_agent": len(ids) == 1,
            "sandbox_on": bool(sandbox_on), "sandbox_ready": False, "setup_done": False, "smoke_ok": None,
            "dead": [], "restarts": {a: 0 for a in ids}, "needs_context": [], "renudge": [], "cycle": 1,
            "approval_counts": {"approved": 0, "denied": 0, "auto": 0, "waiting": 0},
        }
        st["standby"] = {a.id: {"label": a.label, "version": a.version,
                                "cmd": (agentmod.boxed(a.id, a.cmd_auto) if sandbox_on else a.cmd_auto) if approvals == "auto" else a.cmd_manual}
                         for a in standby}
        st["codex_guard_since"] = time.time()
        st.update(size=size, size_reason=why, initial_planning=route, quick_review=bool(config.CFG.get("quick_review", False)))
        if size == "trivial":
            (d / "plan.md").write_text("# Quick task\n\n%s\n" % task.strip())
            st.update(plan_verdict="APPROVED", stage_requests={}, task_type=ttype or "general",
                      accepted_plan_signature=roleflow.plan_signature(d / "plan.md"), chosen="O1",
                      options=[{"id": "O1", "title": "Direct approach", "detail": task.strip(), "proposer": "human",
                                "impact": "low", "probe": "", "pros": [], "cons": []}])
        project_handoff.attach(ws,d,st)
        s = cls(st)
        st["role_model_routing"] = model_policy.native_launches(st)
        skipped = agent_failures.seed(st)               # before roles are chosen, so an expired login is not assigned a turn
        model_policy.ensure(st)
        s.write_protocol()
        s.add_entry("human", "task", "task", task, summary=task)
        if skipped:
            names = {"anthropic": "Claude", "openai": "Codex"}
            who = " and ".join(names.get(p, p) for p in skipped)
            s.add_entry("system", "task", "notice", "%s login expired a few minutes ago, so this run skips it and uses the other CLI. "
                        "Sign in to it in Terminal to use it again; Duo tries it again after 15 minutes." % who,
                        summary="Skipping %s: login expired" % who)
        size_note = "Quick task: one agent does it directly, like a CLI alone." if st.get("size") == "trivial" else ""
        if size_note:
            s.add_entry("system", "task", "notice", size_note + " Set triage to full in settings for the whole pipeline.", summary=size_note)
        s.register()
        try:
            s.launch()
        except Exception:
            workers.close(st)
            st.update(status='error', detail='Worker startup failed. Saved task files remain available.')
            s.save()
            raise
        return s

    def register(self):
        session_index.remember(config.root())
        p = index_path()
        idx = json.loads(p.read_text()) if p.exists() else []
        idx = [i for i in idx if i["id"] != self.st["id"]]
        idx.append({"id": self.st["id"], "title": self.st["title"], "duo_dir": self.st["duo_dir"],
                    "created": self.st["created"]})
        p.write_text(json.dumps(idx, indent=1))

    def history_path(self):
        if self.st.get('project_root'):
            return Path(self.st['project_root']) / '.duo-history.jsonl'
        index, _ = session_index.locate(self.st['id'])
        return index.parent / '.duo-history.jsonl' if index else history_path()

    # ------------------------------------------------------------------- tmux
    def launch(self):
        workers.close(self.st)
        name = workers.new_name(self.st)
        self.st['worker_session'] = name
        self.st['worker_generation'] = secrets.token_hex(8)
        panes = {}
        for i, a in enumerate(self.st["agents"]):
            cmd = agentmod.launch_command(a, self.st["agent_info"][a]["cmd"])
            self.st["agent_info"][a]["cmd"] = cmd
            cmd = agentmod.guarded_command(a, cmd, self.ws, sandbox.name(self.st["id"]) if self.st.get("sandbox_on") else None)
            if i == 0:
                pid = tmux("new-session", *pane_env(), "-d", "-s", name, "-x", "240", "-y", "60", "-c", str(self.ws),
                           "-P", "-F", "#{pane_id}", cmd).strip()
                tmux('set-option', '-t', name, '@duo_run_id', self.st['id'])
                tmux("set-option", "-w", "-t", name, "remain-on-exit", "on", check=False)
            else:
                pid = split(panes[self.st["agents"][0]], str(self.ws), cmd)
            tmux("set-option", "-p", "-t", pid, "@duo_label", self.label(a).upper(), check=False)
            panes[a] = pid
        tmux("select-layout", "-t", name, "even-horizontal", check=False)
        tmux("set-option", "-t", name, "pane-border-status", "top", check=False)
        tmux("set-option", "-t", name, "pane-border-format", " #{@duo_label} ", check=False)
        self.st["panes"] = panes
        self.st["booted_at"] = time.time()
        self.st["status"], self.st["detail"] = "booting", "Starting %s" % ", ".join(self.label(a) for a in self.st["agents"])
        self.save()

    def panes_alive(self):
        return workers.alive(self.st)

    def _tmux_alive(self):
        return workers.attached(self.st)

    def _rejoin(self, agents):
        """Agents that start fresh re-read the rules and the log, and get their open turn again."""
        s = self.st
        for a in agents:
            s["protocol_sent"][a] = False
            for key in ("needs_context", "renudge"):
                if a not in s[key]:
                    s[key].append(a)
        s["status"], s["booted_at"] = "booting", time.time()
        self._boot_prev = {}

    def restart_agent(self, agent, auto=False):
        s = self.st
        if agent not in s["panes"]:
            raise ValueError("Unknown agent %s" % agent)
        if not self._tmux_alive():
            return self.resume()
        cmd = agentmod.launch_command(agent, s["agent_info"][agent]["cmd"])
        s["agent_info"][agent]["cmd"] = cmd
        cmd = agentmod.guarded_command(agent, cmd, self.ws, sandbox.name(s["id"]) if s.get("sandbox_on") else None)
        tmux("respawn-pane", *pane_env(), "-k", "-t", s["panes"][agent], "-c", str(self.ws), cmd)
        s.setdefault("model_now", {}).pop(agent, None)   # a fresh process starts on its default model
        if auto:
            s["restarts"][agent] = s["restarts"].get(agent, 0) + 1
        s["dead"] = [a for a in s["dead"] if a != agent]
        self._rejoin([agent])
        why = "it stopped" + (", restart %d" % s["restarts"][agent] if auto else ", restarted by you")
        self.add_entry("system", s["phase"] or "task", "restart", "Restarted %s (%s)." % (self.label(agent), why),
                       summary="Restarted %s (%s)" % (self.label(agent), why))
        s["detail"] = "Restarting %s" % self.label(agent)
        self.save()

    # ------------------------------------------------------------- watching
    def watch(self):
        """Observe native permission requests and task progress without adding broker decisions."""
        s = self.st
        now = time.time()
        for p in s["pending"]:
            pane = s["panes"].get(p["agent"])
            if not pane:
                continue
            screen = capture(pane)
            digest = hash(screen)
            if digest != p.get("screen"):
                p["screen"], p["changed_at"] = digest, now
            from . import pane as panectl
            kind = None if "Duo agentic worker:" in screen and panectl.idle(screen, p["agent"]) else classify_screen(screen)
            if kind is None and p.get("now") != (now_line := " / ".join(tail_lines(screen, 3))):
                p["now"] = now_line                   # no save(): view() reads pending from self.st
            if kind == "approval":
                q = question_text(screen)
                key = hash(q)
                if key in p.get("answered", []) or (p.get("waiting") or {}).get("key") == key:
                    continue
                self._on_approval(p, q, key)
            elif (p.get("waiting") or {}).get("kind") == "approval":
                p["waiting"] = None                   # answered in the pane itself
                self._recount_waiting()
                self.save()
            elif not p.get("stall_notified") and now - p.get("changed_at", now) > config.CFG["stall_minutes"] * 60 \
                    and not (self.dir / p["file"]).exists() and not self._wait_for_jobs(p):
                p["stall_notified"] = True
                p["waiting"] = {"kind": "stall", "question": "", "verdict": "allow"}
                notify.send("Duo", "%s seems stuck on %s. Nudge or restart it." % (self.label(p["agent"]), p["kind"]))
                self.save()

    def _recount_waiting(self):
        """Keep approval_counts['waiting'] equal to the number of pending approval questions."""
        counts = self.st.setdefault("approval_counts", {"approved": 0, "denied": 0, "auto": 0, "waiting": 0})
        counts["waiting"] = sum(1 for x in self.st["pending"] if (x.get("waiting") or {}).get("kind") == "approval")

    def _on_approval(self, p, question, key):
        s = self.st
        p["waiting"] = {"kind": "approval", "question": question, "verdict": "review", "key": key,
                        "findings": [], "category": "native", "why": "The CLI requires your approval."}
        self._recount_waiting()
        s["detail"] = "%s requires native approval." % self.label(p["agent"])
        notify.send("Duo", s["detail"])
        self.save()

    def answer_wait(self, agent, approve):
        s = self.st
        agent = orch.resolve(s, agent)
        p = next((p for p in s["pending"] if p["agent"] == agent and p.get("waiting")), None)
        if not p:
            raise ValueError("%s is not waiting for an answer." % self.label(agent))
        w = p["waiting"]
        if w["kind"] == "approval":
            send_key(s["panes"][agent], (APPROVE_KEYS if approve else DENY_KEYS).get(agent, "Enter"))
            p.setdefault("answered", []).append(w.get("key"))
            for f in w.get("findings", []):
                security.log(security.Finding(f["rule"], f["action"], f["reason"], f["detail"]), run=s["id"], agent=agent,
                             source="command", action="approved by you" if approve else "denied by you")
            self.add_entry("human", s["phase"] or "task", "approval", "%s %s's request:\n\n%s" % (
                "Approved" if approve else "Denied", self.label(agent), w["question"]),
                summary="You %s %s's request" % ("approved" if approve else "denied", self.label(agent)))
            counts = s.setdefault("approval_counts", {"approved": 0, "denied": 0, "auto": 0, "waiting": 0})
            counts["approved" if approve else "denied"] += 1
        p["waiting"] = None
        self._recount_waiting()
        s["detail"] = "%s: %s" % (self.label(agent), p["kind"])
        self.save()

    def follow_up(self, text):
        """Add to a run that is not active: start a new cycle (Frame to Verify) built on earlier decisions."""
        s = self.st
        if s["status"] in ACTIVE:
            raise ValueError("Stop the current run first.")
        text = str(text or "").strip()
        if not text:
            raise ValueError("Write what to add first.")
        s["cycle"] = s.get("cycle", 1) + 1
        if s.get('goal'):
            s.setdefault("goal_history", []).append(s.pop("goal"))
        s['goal_loop_version'] = 2
        s['goal'] = dict(status='active', cycles=[], no_progress=0, seen_evidence={}, record_offset=goal_loop.record_offset(self.dir))
        s.update(phase=None, options=[], ranking=[], chosen=None, tried=[], round=0,
                 last_verdict=None, verify_passed=None, deep_step=None, fallback=False,
                 stall=0, last_changed=None,
                 history_recorded=False, verify_failures=0, paused=False, pending=[])
        s.pop("build_task_id", None)
        s.pop("review_exchanges", None)
        s.pop("planned_current_goal", None)
        s.pop("failed_turn", None)
        s.pop("task_delivery", None)
        for key in ('relay_input', 'relay_step', 'assessments', 'stage_requests'):
            s.pop(key, None)
        for key in ("build_workflow", "feasibility_recheck", "feasibility_return_phase", "build_stage", "research_coverage", "research_action", "research_revisions", "research_return_phase", "research_findings"):
            s.pop(key, None)
        self.add_entry("human", "task", "followup", text, summary=text)
        self.resume()

    def drop_agent(self, agent):
        """An agent that keeps failing to start leaves the session; the others take over its turns."""
        s = self.st
        tmux("kill-pane", "-t", s["panes"].pop(agent), check=False)
        s["agents"] = [a for a in s["agents"] if a != agent]
        s["dead"] = [a for a in s["dead"] if a != agent]
        s["single_agent"] = len(s["agents"]) == 1
        s["roles"] = {}
        roleflow.ensure(s)
        mine = [p for p in s["pending"] if p["agent"] == agent]
        s["pending"] = [p for p in s["pending"] if p["agent"] != agent]
        text = "%s failed to start. Duo continues without %s." % (self.label(agent), self.label(agent))
        self.add_entry("system", s["phase"] or "task", "restart", text, summary=text)
        for p in mine:                        # issue() picks the agent for the turn's role from those left
            self.issue(phases.Turn(s["agents"][0], p["phase"], p["kind"], p["option_ids"]))
        self.save()

    def _pause(self, rule, why, nxt, blocker=None):
        """Stop the run for one named rule. The page shows the rule, why, and the next step."""
        s = self.st
        s["status"], s["paused"] = "waiting", True
        s["blocker"] = blocker
        s["stop"] = {"rule": rule, "why": why, "next": nxt}
        error_id = self._diagnostic(why, operation=rule)
        s["stop"] = {"rule": rule, "why": "A diagnostic was recorded: " + error_id,
                     "next": "Continue to review saved work and create the remaining task.", "error_id": error_id}
        readable = goal_loop.validation_pause(rule, why, nxt, error_id)
        if rule in ('Agent unavailable', 'No eligible model') or (blocker and not readable):
            # A named blocker has a fix the user can act on, so show it instead of the generic diagnostic notice.
            readable = {'rule': rule, 'why': why, 'next': nxt, 'error_id': error_id}
        if readable:
            s['stop'] = readable
        s["detail"] = readable['why'] if readable else "Task paused. Diagnostic details are available separately."
        if not s.get("failed_turn") and s.get("pending"):
            s["failed_turn"] = dict(s["pending"][0])
        self.save()

    def _pause_for_blocker(self, code, detail=None):
        text, nxt = harness.BLOCKERS[code]
        if detail and detail != code:
            text += " " + str(detail)
        return self._pause(code.replace("_", " ").capitalize(), text, nxt, blocker=code)

    def _restart_container_if_missing(self):
        """Quit removes the run's container but the image stays. Start the container again. True when it is up."""
        s = self.st
        if s.get("sandbox_on") and s.get("sandbox_ready") and not sandbox.exists(s["id"]):
            spec = sandbox.load_spec(self.dir / "sandbox")
            ok, log = sandbox.start_container(self.ws, s["id"], spec.get("ports"))
            if ok:
                sandbox.write_wrapper(self.ws, s["id"])
            s["sandbox_ready"] = bool(ok)
            if not ok:
                s["detail"] = "The run container failed to restart: %s" % sandbox.tail(log, 3, 300)
            return bool(ok)
        return True

    def _preflight_code(self, step):
        """Blocker code that stops a sandbox turn before it is issued, or None. Uses no turn."""
        s = self.st
        if not (s.get("sandbox_on") and any(t.kind in ("build", "revise", "critique", "verify", "secure") for t in step)):
            return None
        docker_up = bool(sandbox.available()[0])
        container_up = docker_up and sandbox.exists(s["id"])
        code = harness.preflight(True, docker_up, container_up)
        if code == "container_missing" and docker_up and self._restart_container_if_missing():
            return None
        return code

    def resume(self):
        """Start every agent again (after Stop, a closed tmux, or a Mac restart) and carry on."""
        s = self.st
        if s.get('pending') or (s.get('stop') or {}).get('rule') == 'Agent unavailable':
            if (s.get('stop') or {}).get('rule') == 'Agent unavailable':
                s['agent_availability'] = {}
            s.pop('availability_retry_at', None)
            s.pop('failed_turn', None)
            retry = list(s.get('pending', []))
            s.update(pending=[], stop=None, paused=False, status='running')
            self.launch()
            for p in retry:
                self.issue(phases.Turn(p['agent'], p['phase'], p['kind'], p.get('option_ids', [])), retry_of=p)
            self.save()
            return
        if (s.get('stop') or {}).get('rule') == 'Goal assessment invalid' and goal_loop.recover_saved_achievement(self.dir, self.ws, s):
            diagnostic = (s.get('stop') or {}).get('error_id')
            s.update(stop=None, blocker=None, paused=False, status='running', phase='done', goal_invalid_reports=0)
            self.finish()
            if diagnostic and s.get('status') == 'done':
                broker.error_status(self.dir, diagnostic, 'engine', 'The saved goal report now passes validation against unchanged product evidence.',
                                    resolution='resolved', evidence=[s['goal']['report_file']])
            self.save()
            return
        model_policy.ensure(s)
        if s.get("failed_turn") and not s.get("broker_continuations"):
            self._queue_continuation(s["failed_turn"])
        if (s.get("stop") or {}).get("rule") == "Long job time limit":
            jobs.extend_deadlines(s.get("pending", []), time.time())
            jobs.resume_budget(s, time.time())
            s.update(stop=None, blocker=None, paused=False, status="running",
                     detail="Resumed with an extended job deadline. Existing jobs remain assigned.")
            self.save()
            return
        if s.get("build_workflow") and s.get("blocker") in buildflow.BLOCKERS:
            s["feasibility_recheck"] = True
        if (s.get("stop") or {}).get("rule") == "Research coverage incomplete":
            s["research_revisions"] = 0
        s["stall"] = 0                                # a resume is a new try: the loop guard counts from here
        s["stop"], s["blocker"] = None, None
        s["task_recoveries"] = {}
        if s.get("task_type") == "research":
            s["sandbox_on"] = False
        # Old suspended builds receive the new contract rather than an obsolete prompt.
        if any(p.get("kind") in ("build", "revise") and not p.get("task_id") for p in s.get("pending", [])):
            s["pending"] = []
            s.update(phase="deep", deep_step="build", ready_for_review=False, last_changed=None)
        if any(p.get("review_root") and p.get('kind') not in ('assess', 'goal_assess', 'cycle_summary', 'goal_check', 'goal_assign') for p in s.get("pending", [])):
            s["pending"] = []
            s.update(phase="deep", deep_step="build", ready_for_review=bool(s.get("task_delivery")), last_changed=None)
        s["paused"] = False
        self.write_protocol()
        self._restart_container_if_missing()
        if s.get("broker_continuations") and self.panes_alive():
            s.update(status="running", dead=[], detail="Continuing saved work with fresh task workers.")
            self.save()
            return
        self.launch()
        for pending in s.get('pending', []):
            if pending.get('kind') in ('assess', 'goal_assess'):
                self._start_assessment(pending)
            elif pending.get('kind') in ('cycle_summary', 'goal_check', 'goal_assign'):
                self._start_continuation_worker(pending)
        s["dead"] = []
        self._rejoin(list(s["agents"]))
        s["detail"] = "Starting the agents again"
        self.add_entry("system", s["phase"] or "task", "restart", "Resumed the session.", summary="Resumed the session")
        self.save()

    # ------------------------------------------------------------ persistence
    def write_protocol(self):
        (self.dir / 'PROTOCOL.md').write_text(
            '# Duo workflow\n\nFollow the user task and the current assignment. '
            'Use native tools to complete substantial stages. Explain unfinished work in ordinary prose. '
            'Use the assigned acknowledgement path and reply path. Write ordinary text or Markdown. '
            'The reply filename identifies the task. Read the task outcome folder before continuing. '
            'The harness schedules the next stage. Use the agreed plan, preserve valid evidence, and report concrete blockers. '
            'The independent reviewer chooses how to assess the task requirements and relevant risks. '
            'Do not claim completion while required checks or required fixes remain.\n' + personal.protocol_rules())

    def next_n(self):
        ns = [e["n"] for e in self.st["entries"]] + [p["n"] for p in self.st["pending"]]
        return max(ns, default=0) + 1

    def add_entry(self, author, phase, kind, prose, summary="", n=None, **extra):
        n = n or self.next_n()
        if author == "system" and kind in ("warning", "error", "recovery"):
            error_id = self._diagnostic(prose, operation=kind)
            return {"n": n, "author": author, "kind": kind, "error_id": error_id}
        fname = extra.pop("file", None) or "replies/%04d-%s.md" % (n, author)
        if author in ("human", "system"):
            (self.dir / fname).write_text(prose + "\n")
        e = {"n": n, "author": author, "phase": phase, "kind": kind, "summary": summary or prose,
             "prose": prose, "options": [], "handoffs": [], "verdict": None,
             "bounds": [], "errors": [], "invalid": False, "bounced": [], "changed": [], "high_impact": False,
             "file": fname, "time": time.time(), "time_hm": now_hm(), "duration": 0,
             "cycle": self.st.get("cycle", 1)}
        e.update(extra)
        self.st["entries"].append(e)
        self.st["entries"].sort(key=lambda x: x["n"])
        self.save()
        if author not in ("human", "system") and extra.get("task_id") and extra.get("outcome", "progress") == "progress" and model_policy.native_launches(self.st):
            summary_agent.request(self.dir, extra["task_id"], self.st["agents"])
        return e

    def save(self):
        self._runtime.observe(self.st)
        payload = json.dumps(self.st, indent=1, default=str)
        if payload == getattr(self, "_saved_payload", None) and (self.dir / "state.json").exists():
            self._runtime.saved()
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(payload)
        tmp.replace(self.dir / "state.json")
        self._runtime.saved()
        self.write_chat()
        try:
            memory.write(self.st, self.dir, self.ws)
        except Exception as ex:                       # memory must never stop a session
            print("memory: %r" % ex)
        bump()
        self._saved_payload = payload
        task_context.update(self.dir,self.ws,self.st)
        if self.st.get("status") == "done" and model_policy.native_launches(self.st):
            completion_summary.request(self.dir,self.ws,self.st)

    def write_chat(self):
        s = self.st
        out = ["# Duo: %s" % s["title"], "",
               "Type: %s | mode: %s | agents: %s | status: %s" % (s["task_type"] or "auto", s["mode"],
                                                                  ", ".join(self.label(a) for a in s["agents"]), s["status"]), ""]
        for e in s["entries"]:
            if broker.diagnostic_entry(e):
                continue
            who = e["author"].upper() if e["author"] in ("human", "system") else self.label(e["author"]).upper()
            tag = " | VERDICT: %s" % e["verdict"] if e.get("verdict") else ""
            out += ["---", "", "## #%d | %s | %s/%s | %s%s" % (e["n"], who, e["phase"], e["kind"], e["time_hm"], tag),
                    "", "> %s" % e["summary"], "", e["prose"], ""]
        (self.dir / "chat.md").write_text(orch.redact(s, "\n".join(out)))

    def write_options(self):
        lines = ["# Options", ""]
        for o in self.st["options"]:
            lines += ["## %s: %s" % (o["id"], o["title"]), "Proposed by %s." % self.label(o["proposer"])]
            if o.get("detail"):
                lines.append(o["detail"])
            if o.get("probe"):
                lines.append("Probe: %s" % o["probe"])
            lines.append("")
        (self.dir / "options.md").write_text("\n".join(lines))

    # ------------------------------------------------------------------ turns
    def _new_files(self, agent, kind):
        hide = ("diverge", "score") if kind in ("diverge", "score") else ()
        new = [e for e in self.st["entries"] if e["n"] > self.st["last_seen"][agent] and e["author"] != agent
               and e.get("kind") not in ("warning", "error", "recovery") and not e.get("invalid")
               and not (e["phase"] in hide and e["author"] not in ("human", "system"))]
        return new

    def _research_question(self):
        cycle = self.st.get("cycle", 1)
        followups = [e["prose"] for e in self.st.get("entries", []) if e.get("author") == "human" and e.get("kind") == "followup" and e.get("cycle", 1) == cycle]
        return self.st["task"] + ("\nCurrent follow-up:\n" + "\n".join(followups) if followups else "")

    def _research_key(self):
        return buildflow.signature(self._research_question() + "\nCycle: " + str(self.st.get("cycle", 1)))

    def _ensure_research_coverage(self):
        s = self.st
        if not relay.required(s, 'research'):
            return False
        if s.get("task_type") != "research" or s.get("phase") not in ("scout", "research_check", "decide", "deep", "feasibility", "verify"):
            return False
        if researchflow.current(self.ws, s.get("research_coverage"), self._research_key()):
            return False
        _, errors = researchflow.validate(self.ws, "research")
        kind = "research_expand" if errors or s.get("research_action") == "expand" else "research_check"
        role = "scout" if kind == "research_expand" else "critic"
        agent = s["roles"][role]
        if kind == "research_check":
            researcher = s.get("research_author", s["roles"]["scout"])
            agent = next((a for a in s["agents"] if a != researcher), agent)
        self.issue(phases.Turn(agent, "scout" if kind == "research_expand" else "research_check", kind,
                               [s["chosen"]] if s.get("chosen") else []))
        return True

    def _build_context(self):
        plan = self.dir / "plan.md"
        return self.st["task"] + "\nChosen: " + str(self.st.get("chosen")) + "\n" + (plan.read_text() if plan.is_file() else "")

    def _build_package(self):
        workflow = self.st["build_workflow"]
        candidates = buildflow.ready(workflow)
        if candidates:
            return candidates[0]
        # Corrections requested by the unchanged Critic/Verifier flow cover the integrated output.
        package = {"id": buildflow.INTEGRATION_ID, "title": "Resolve integrated build defects", "kind": ("implementation" if any(p["kind"] == "implementation" for p in workflow["packages"]) else "document"),
                   "deliverables": sorted({p for item in workflow["packages"] for p in item["deliverables"]}),
                   "behavior": "Resolve all required review defects", "acceptance": "Accepted plan and focused regression checks",
                   "depends": [], "requires": [c["id"] for c in workflow["checks"]], "status": "pending"}
        previous = next((p for p in workflow["packages"] if p["id"] == buildflow.INTEGRATION_ID), None)
        if previous:
            previous.update(status="pending")
            previous.pop("artifact_baseline", None)
            return previous
        workflow["packages"].append(package)
        return package

    def _pause_build_workflow(self):
        self._pause("Build dependencies unresolved", buildflow.blocked_text(self.st["build_workflow"]),
                    "Provide the missing evidence or approve a plan change. Resume alone does not resolve a dependency.",
                    blocker="evidence_missing")

    def issue(self, turn, fix_of=None, problems=None, retry_of=None):
        s = self.st
        try:
            model_policy.ensure(s)
        except ValueError as error:
            self._pause('No eligible model', str(error), 'Configure an available model for the required role, then resume.')
            return
        try:
            spec = roleflow.ensure(s)[roleflow.role_for(turn.kind)]
        except ValueError as error:
            self._pause('No eligible model', str(error), 'Configure an available model for the required role, then resume.')
            return
        turn = phases.Turn(spec['agent'], turn.phase, turn.kind, turn.option_ids)
        if s.get('model_fallbacks') and not s.get('fallback_noted'):
            s['fallback_noted'] = True
            used = "; ".join("%s: %s (%s)" % (f['role'], f['model'] or 'its default model', f['how']) for f in s['model_fallbacks'])
            self.add_entry("system", s["phase"] or "task", "notice",
                           "Duo has no model rated for some roles on your CLIs, so it used the closest it has: %s. "
                           "Run `duo models` to rate your models once." % used,
                           summary="Using the closest available models for some roles")
        n = fix_of["n"] if fix_of else self.next_n()
        agent = turn.agent
        fname = "replies/%04d-%s%s.md" % (n, orch.alias(s, agent), "-fix-" + secrets.token_hex(4) if fix_of else "")
        building = turn.kind in ("build", "revise")
        if turn.kind in ("research_expand", "research_check"):
            s.setdefault("research_return_phase", s["phase"])
            s["phase"] = turn.phase
        if turn.kind == "feasibility":
            s.setdefault("feasibility_return_phase", s["phase"])
            s["phase"] = "feasibility"
        if building and s.get('accepted_plan_signature') != roleflow.plan_signature(self.dir / 'plan.md'):
            s.update(phase='frame', role_plan_step='draft', plan_verdict=None)
            return self.issue(phases.Turn(s['roles']['challenger'], 'frame', 'plan_review'))
        if building:
            s["build_stage"] = "build"
        elif turn.kind == "feasibility":
            s["build_stage"] = "feasibility"
        task_id = (retry_of or fix_of or {}).get("task_id") or (s.setdefault("build_task_id", "task-%s-%s" % (self.dir.name, secrets.token_hex(3)))
                   if turn.kind in ("build", "revise", "critique", "verify", "secure") else "turn-%s-%04d" % (self.dir.name, n))
        outcome_dir = task_output.task_directory(self.ws, s.get("title") or s["task"], s["id"], task_id)
        outcome_dir.mkdir(parents=True, exist_ok=True)
        fname = task_output.relative_file(self.dir, outcome_dir, task_id, n, orch.alias(s, agent),
                                         "-fix-" + secrets.token_hex(4) if fix_of else "")
        (self.dir / fname).parent.mkdir(parents=True, exist_ok=True)
        attempt = {}
        ack = delivery.prepare(attempt, "%s/acks/%04d.txt" % (self.rel, n), time.time())
        (self.ws / ack).parent.mkdir(parents=True, exist_ok=True)
        chosen = s.get("chosen") or ""
        ctitle = next((o["title"] for o in s["options"] if o["id"] == chosen), "")
        parts = ["DUO turn #%d: you are %s, role %s, phase %s, kind %s." % (
            n, self.label(agent), roleflow.role_for(turn.kind), turn.phase, turn.kind)]
        patch = self._review_patch(n) if turn.kind == "critique" else None
        if turn.kind in handoff.TASK and turn.kind not in ("exchange", "plan_review") or (turn.kind in ("exchange", "plan_review") and s.get("entries")):
            parts.append(handoff.brief(s, turn.kind, self.rel, patch))
        package = self._build_package() if building and s.get("build_workflow") else None
        if package:
            package.setdefault("artifact_baseline", buildflow.artifact_state(self.ws, package["deliverables"]))
            workflow = s["build_workflow"]
            if not workflow.get("review_baseline"):
                workflow["review_baseline"] = "build-review-baseline-" + workflow["key"][:12]
                harness.snapshot(self.ws, self.dir / workflow["review_baseline"])
            parts.append(buildflow.package_prompt(package))
        if s.get("task_type") == "research":
            if turn.kind in ("scout", "research_expand", "research_check"):
                parts.append(researchflow.prompt("research", self._research_question(), review=turn.kind == "research_check"))
                if s.get("research_findings"):
                    parts.append("Address the saved coverage findings: " + s["research_findings"])
                if turn.kind != "research_check":
                    parts.append("Evidence collection tasks for the Duo researcher (not a user upload request):\n" + researchflow.evidence_summary(self.ws, "research"))
            elif turn.kind in ("feasibility", "build", "revise", "critique", "verify") and (self.ws / "research/coverage-review.md").exists():
                parts.append("Read research/literature-review.md, research/evidence-matrix.md, research/method-proposal.md, and research/coverage-review.md. "
                             "Compare the method with its closest baseline and target drawback. Borrow only supported concepts; improvements remain hypotheses until tested. "
                             "Do not change accepted scope without approval.")
        if turn.kind == "feasibility":
            parts.append(buildflow.prompt(self.rel + "/feasibility.json", self._build_context()))
        if agent in s["needs_context"]:
            parts.append("You restarted, so read %s/PROTOCOL.md and %s/memory.md first." % (self.rel, self.rel))
            s["needs_context"].remove(agent)
            s["protocol_sent"][agent] = True
        if not s["protocol_sent"][agent]:
            if s.get("size") != "trivial":                 # a quick task needs no protocol reading
                parts.append("First read %s/PROTOCOL.md." % self.rel)
            s["protocol_sent"][agent] = True
        if turn.kind == 'cycle_summary':
            parts.append(goal_loop.prepare_summary(self.dir, self.ws, s))
            if problems:
                parts.append('Correct the summary: ' + '; '.join(problems))
        elif turn.kind == 'goal_assess':
            parts = [parts[0], goal_loop.assessment_prompt(s)]
        elif turn.kind == 'goal_assign':
            parts.append(goal_loop.assignment_prompt(s))
        elif turn.kind == 'goal_check':
            parts.append(goal_loop.prepare(self.dir, self.ws, s))
            if problems:
                parts.append('Correct the previous goal assessment: ' + '; '.join(problems))
        elif turn.kind in ('exchange', 'plan_review'):
            shared = relay.task_input(self._research_question(), relay.requirements(self.ws)) + project_handoff.prompt(s) + goal_loop.continuation(s)
            if turn.kind == 'exchange':
                s.pop('plan_verdict', None)
                parts = [parts[0], roleflow.plan_prompt(shared, s.get('plan_feedback', '')),
                         skill_inventory.prompt(self.ws, s['agents'], s['task'], index=os.path.normpath(self.rel + '/skills.md')),
                         'Save the plan to %s/plan.md. Set task_type in the reply.' % self.rel]
            else:
                parts = [parts[0], roleflow.review_prompt(shared, os.path.normpath(self.rel + '/plan.md'))]
        elif fix_of:
            self._diagnostic("\n".join(problems or []), {"task_id": task_id, "kind": turn.kind, "agent": agent})
            parts.append(INSTRUCTIONS[turn.kind].format(d=self.rel, chosen=chosen, title=ctitle, gate_log=""))
        elif not (s.get("size") == "trivial" and turn.kind == "build"):    # the quick brief already says what to do
            parts.append(INSTRUCTIONS[turn.kind].format(d=self.rel, chosen=chosen, title=ctitle,
                                                       gate_log=(s.get("gate_log") or "")))
            if turn.kind in ("build", "revise") and s.get("size") != "trivial":
                if not package:
                    parts.append("Advance the current goal, then the next ready goal. Follow the latest review within the accepted plan. If an item is blocked, finish independent goals. Report which items wait on it, "
                                 "who can unblock them, and what must change before retrying.")
            if turn.kind == "critique":
                if patch:
                    parts.append("Start from the diff; open other files only when a requirement needs them.")
                else:
                    parts.append("Changed in the latest build: %s." % ", ".join(s.get("last_build_changed", [])))
                parts.append(relay.REVIEW)
            if (turn.kind == "verify" and s.get("smoke_evidence") and s["smoke_evidence"].get("inputs") is not None and
                    s["smoke_evidence"].get("inputs") == _source_fingerprint(self.ws)):
                parts.append("The harness ran this smoke check successfully: %s. Read its log: %s. "
                             "Reuse that result while its inputs remain unchanged." %
                             (s["smoke_evidence"]["command"], s["smoke_evidence"]["log"]))
            if s.get("sandbox_ready") and turn.kind in ("build", "revise", "critique", "verify", "secure"):
                parts.append("Use Docker, Docker Compose, ./.duo/sbx, and native project tools. Native CLIs control tool permissions.")
            if turn.kind == "revise" and s.get("smoke_log"):
                parts.append("The smoke test failed in a fresh container: %s" % s["smoke_log"])
        if retry_of:
            parts.append("Resume the same task using saved files. Verify earlier evidence and finish the assigned output. "
                         "Read the current broker task record and relevant diagnostic files. ")
        if building and goal_loop.enabled(s) and s.get("goal", {}).get("next_action"):
            parts.append(goal_loop.continuation(s))
        if building and s.get("size") != "trivial":
            parts.append("Use the accepted plan at %s." % os.path.normpath(self.rel + "/plan.md"))
        if building and s.get("task_type") == "research":
            parts.append(goal_loop.CREATIVITY)
            parts.append("Deliver the research outputs in the accepted plan. " + orch.ACCESS_CHECK)
        if turn.kind in task_output.REVIEW_KINDS and turn.kind not in ("plan_review", "critique"):
            parts.append("Start your review with APPROVED or REVISE and give the evidence for your decision.")
        parts.append(orch.contract(task_id, ack, build=building))
        if s.get("size") != "trivial":
            parts.append(task_output.instructions(self.ws, outcome_dir))
        new = [] if turn.kind in ('assess', 'goal_assess') else self._new_files(agent, turn.kind)
        if s.get("size") == "trivial":                    # the ask and the notices are already in the brief; only a later user message is new
            new = [e for e in new if e["author"] == "human" and e["kind"] in ("message", "followup")]
        due = False
        if new:
            parts.append("Read: %s." % ", ".join(os.path.normpath(self.rel + "/" + e["file"]) for e in new))
            if any(e["author"] == "human" and e["kind"] == "message" for e in new):
                parts.append("A human message is included. Before any other work, answer it. Answer the question in ordinary prose, then continue the task.")
                s["unanswered"] = False
                due = True
        humans = [e for e in new if e["author"] == "human" and e["kind"] == "message"]
        if humans and all(e.get("intent") == "question" for e in humans):
            parts.append("Answer the human question, save the handoff, and continue the assigned task.")
        parts.append("Write your reply to `%s`" % os.path.normpath(self.rel + "/" + fname))
        ckpt = None
        if not fix_of and turn.kind in ("build", "revise"):
            ckpt = n
            harness.snapshot(self.ws, self.dir / "checkpoints" / str(n))
            harness.prune(self.dir / "checkpoints")
        prompt = "\n\n".join(parts)
        if turn.kind != 'exchange':
            prior = project_handoff.prompt(s)
            if prior and prior not in prompt: prompt += prior
        s["last_seen"][agent] = max([e["n"] for e in s["entries"]], default=0)
        p = {"n": n, "agent": agent, "phase": turn.phase, "kind": turn.kind, "option_ids": list(turn.option_ids),
             "file": fname, "issued_at": time.time(), "prompt": prompt, "base": ((fix_of or retry_of)["base"] if fix_of or retry_of else _worktree_state(self.ws)),
             "bounced": list(problems or []) if fix_of else (fix_of or {}).get("bounced", []),
             "answer_due": bool(fix_of.get("answer_due")) if fix_of else due, "outcome_dir": str(outcome_dir)}
        p.update(attempt)
        p.update(task_id=task_id, ack=ack, progress_at=time.time(), activity_base=harness.progress_state(self.ws))
        if package:
            p["build_package"] = package
            p["build_artifact_base"] = (fix_of or retry_of or {}).get("build_artifact_base", buildflow.artifact_state(self.ws, package["deliverables"]))

        if s.get("task_type") == "research" and turn.kind == "research_expand":
            p["research_progress_base"] = (fix_of or retry_of or {}).get("research_progress_base", researchflow.progress(self.ws, "research"))
        if building:
            jobs.begin(p, s["panes"].get(agent), self.dir / "jobs" / (task_id + ".json"))
            if s.get("size") != "trivial":
                p["prompt"] += jobs.instructions(p, self.ws)
        p["checkpoint"] = fix_of.get("checkpoint") if fix_of else ckpt
        if fix_of:
            p["bounced"] = list(problems)
            p["first_issued"] = fix_of.get("first_issued", fix_of["issued_at"])
        s["pending"].append(p)
        p['role_assignment'] = roleflow.assignment(s, turn.kind, task_id)
        if s.get("size") != "trivial" and turn.kind not in ('exchange', 'plan_review'):
            p['prompt'] += roleflow.instructions(p['role_assignment']['role'])
        if turn.kind == 'plan_review':
            p['plan_signature'] = roleflow.plan_signature(self.dir / 'plan.md')
        current_node = broker.node(self.dir, task_id)
        if current_node:
            p["inherited_outputs"] = [row["path"] for row in current_node.get("inherited_outputs", [])]
        self._start_continuation_worker(p)
        reply_root = Path(p.get("review_root", self.ws))
        (reply_root / self.rel / fname).parent.mkdir(parents=True, exist_ok=True)
        s["status"] = "running"
        s["detail"] = "%s: %s" % (self.label(agent), turn.kind)
        self.save()
        model = (retry_of or {}).get('_recovery_model') or model_policy.select(s, agent, turn.kind, task_id)
        if model is None and (config.CFG.get("automatic_model_switching") or config.CFG.get("models")):
            model = models.model_for(agent, turn.kind, s.get("task_type"), config.CFG.get("models"))
        if s.get("size") == "trivial":
            model = None                      # a quick task runs on the CLI's own default model, as it would alone
        p["_assigned_model"] = model
        self.save()
        if self._wait_for_jobs(p):
            self.save()
            return
        p["assignment_text"] = p["prompt"]
        assigned = broker.assign(self.dir, p, s.get("title", "Task"), s["task"], self.ws)
        if s.get("size") != "trivial":
            p["prompt"] += assigned
        if turn.kind == "continuation_review":
            p["prompt"] = broker.recovery_prompt(self.dir, task_id) + broker.assign(self.dir, p, s.get("title", "Task"), s["task"], self.ws) + task_output.instructions(self.ws, outcome_dir) + orch.contract(task_id, p["ack"]) + "\nWrite your reply to `%s`." % (self.dir / fname).resolve()
        route = s.get("initial_planning")
        if turn.kind == "exchange" and route and not route.get("planner_task_id") and not (retry_of or fix_of):
            p["prompt"] += "\n\n" + initial_planning.brief(route)
            route["planner_task_id"] = task_id
        p["prompt"] = check_policy.RULES + "\n" + task_output.GUIDANCE + "\n\n" + p["prompt"]   # fixed text first, so CLIs can cache it
        if p.get("answer_due"):
            humans = [e["prose"] for e in s["entries"] if e["author"] == "human" and e["kind"] == "message"]
            p["prompt"] += "\nAnswer these user messages in ordinary prose and continue: " + "\n".join(humans)
        token_ledger.record_issue(s, p, model)
        self.save()
        threading.Thread(target=self._nudge, args=(agent, p["prompt"], model), daemon=True).start()

    def _nudge(self, agent, prompt, model=None):
        pane = self.st["panes"].get(agent)
        generation = self.st.get('worker_generation')
        def current_worker():
            return (self.st.get('status') in ACTIVE and not self.st.get('paused')
                    and self.st.get('worker_generation') == generation
                    and self.st.get('panes', {}).get(agent) == pane and workers.owned(self.st))
        if not pane:
            return
        pending = None
        lease = secrets.token_hex(6)
        with LOCK:
            if not current_worker():
                return
            pending = next((p for p in self.st.get("pending", []) if p["agent"] == agent and p.get("prompt") == prompt), None)
            if pending is None and TURN_MARK in prompt:
                return
            if pending:
                if pending.get("delivery_owner") not in (None, delivery.RUNTIME_ID):
                    pending.pop("delivery_inflight_at", None)
                if pending.get("delivery_inflight_at") is not None or pending.get("interrupted"):
                    return
                delivery.migrate(pending, time.time())
                prompt = pending["prompt"]
                pending.update(delivery_inflight_at=time.time(), delivery_lease=lease, delivery_owner=delivery.RUNTIME_ID, last_delivery_attempt_at=time.time())
        try:
            from . import pane as panectl
            now = self.st.setdefault("model_now", {})
            cmd = models.switch_command(agent, model) if model else None
            if cmd and now.get(agent) != model:
                with LOCK:
                    if not current_worker():
                        return
                    ok, _ = panectl.command(pane, agent, cmd, timeout=20, picker_value=model)
                if not ok:
                    raise RuntimeError("Model switch is not ready; task delivery will retry")
                now[agent] = model
            if model and not panectl.wait_idle(pane, agent, 20)[0]:
                raise RuntimeError("Agent input is not ready; task delivery will retry")
            with LOCK:
                if not current_worker():
                    return
                if pending and (pending not in self.st.get("pending", []) or pending.get("delivery_lease") != lease
                                or pending.get("interrupted") or self.st.get("paused")):
                    return
            if check_policy.RULES not in prompt:
                prompt += "\n\n" + check_policy.RULES
            if task_output.GUIDANCE not in prompt:
                prompt += "\n\n" + task_output.GUIDANCE
            wire = prompt
            if pending:
                root = Path(pending.get("review_root", self.ws))
                rel = "%s/assignments/%04d-%s.md" % (self.rel, pending["n"], pending["delivery_id"])
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(prompt)
                # A review worker can resolve relative paths against the parent
                # project. Name the exact packet file inside its allowed root.
                wire = "DUO assignment: read %s and complete the assigned task." % path.resolve()
            with LOCK:
                if not current_worker():
                    return
                if pending and (pending not in self.st.get("pending", []) or pending.get("delivery_lease") != lease
                                or self.st.get("panes", {}).get(agent) != pane or self.st.get("paused")):
                    return
                send_text(pane, wire)
                if pending:
                    pending["sent_at"] = time.time()
                    pending.pop("delivery_error", None)
                    if (pending.get("waiting") or {}).get("kind") == "stall":
                        pending["waiting"] = None
                    pending.pop("stall_notified", None)
                    self.st["detail"] = "%s: %s" % (self.label(agent), pending["kind"])
            before = capture(pane)
            time.sleep(2.5)
            after = capture(pane)
            # Re-press Enter only if the TUI swallowed it. Never while a prompt is showing:
            # on an approval question, Enter means "Yes".
            if after == before and panectl.idle(after, agent) and classify_screen(after) is None:
                with LOCK:
                    if current_worker():
                        send_key(pane, "Enter")
        except Exception as ex:
            with LOCK:
                if pending and pending in self.st.get("pending", []):
                    pending["delivery_error"] = str(ex)
                    self.st["detail"] = "Could not reach %s: %s" % (self.label(agent), ex)
                    self.save()
                bump()
        finally:
            with LOCK:
                if pending and pending.get("delivery_lease") == lease:
                    pending.pop("delivery_inflight_at", None)
                    pending.pop("delivery_lease", None)

    # ---------------------------------------------------------------- replies
    def _start_continuation_worker(self, p):
        from .panes import TmuxDriver
        driver = TmuxDriver()
        driver.panes = dict(self.st["panes"])
        driver.commands = {actor: info["cmd"] for actor, info in self.st["agent_info"].items()}
        p["original_pane"] = self.st["panes"][p["agent"]]
        p["original_model"] = self.st.setdefault("model_now", {}).pop(p["agent"], None)
        driver.start_review(p["agent"], self.ws)
        self.st["panes"][p["agent"]] = driver.panes[p["agent"]]
        p["review_root"] = str(self.ws)

    def _add_agent(self, agent, info):
        """Start one more agent in the run's tmux session, as launch() does for the first ones."""
        s = self.st
        s["agent_info"][agent] = info
        cmd = agentmod.launch_command(agent, info["cmd"])
        s["agent_info"][agent]["cmd"] = cmd
        cmd = agentmod.guarded_command(agent, cmd, self.ws, sandbox.name(s["id"]) if s.get("sandbox_on") else None)
        first = next(iter(s["panes"].values()))
        pid = split(first, str(self.ws), cmd)
        tmux("set-option", "-p", "-t", pid, "@duo_label", self.label(agent).upper(), check=False)
        tmux("select-layout", "-t", s["worker_session"], "even-horizontal", check=False)
        s["panes"][agent] = pid
        s["agents"].append(agent)
        s["auto_answered"][agent] = 0
        s["restarts"][agent] = 0
        s["protocol_sent"][agent] = False
        s["last_seen"][agent] = max([e["n"] for e in s["entries"]], default=0)
        s["single_agent"] = len(s["agents"]) == 1

    def _escalate_quick(self):
        """A quick task did not finish. Bring back the other CLI and run the whole pipeline on what exists."""
        s = self.st
        s["escalation_noted"] = True
        added = []
        for agent, info in list((s.get("standby") or {}).items()):
            if agent not in s["agents"]:
                self._add_agent(agent, info)
                added.append(agent)
        s["standby"] = {}
        text = ("The one-agent attempt did not finish, so this is bigger than it looked. Duo is switching to the full process: "
                "plan, plan review, build, code review. The work already done is kept." +
                (" Starting %s for the independent reviews." % " and ".join(self.label(a) for a in added) if added else ""))
        self.add_entry("system", "deep", "notice", text, summary="Escalated to the full process")
        if added:
            s["status"], s["detail"], s["booted_at"] = "booting", "Starting %s" % ", ".join(self.label(a) for a in added), time.time()
        self.save()

    def _review_patch(self, n):
        """The latest build as one diff file, so a fresh reviewer starts from what changed instead of re-reading the project."""
        s = self.st
        changed = s.get("last_build_changed") or []
        baseline = (s.get("build_workflow") or {}).get("review_baseline")
        checkpoint = self.dir / baseline if baseline else self.dir / "checkpoints" / str(s.get("last_build_checkpoint"))
        if not changed or not checkpoint.is_dir():
            return None
        text = orch.review_diff(self.ws, checkpoint, changed)
        if not text:
            return None
        rel = os.path.normpath("%s/review-changes/%04d.patch" % (self.rel, n))
        (self.ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.ws / rel).write_text(orch.redact(s, text))
        return rel

    def _collect_blind_review(self, p):
        if p.get("outcome_dir"):
            task_output.collect(p["review_root"], self.ws, p["outcome_dir"], p["n"])
        if p["kind"] == "research_check":
            researchflow.collect_review(p["review_root"], self.ws, "research")
        for rel in ["%s/%s" % (self.rel, p["file"]), p["ack"]] + ([p['review_report']] if p.get('review_report') else []):
            src, dst = Path(p["review_root"]) / rel, self.ws / rel
            if src.is_file() and not src.is_symlink():
                dst.parent.mkdir(parents=True, exist_ok=True)
                clean = orch.redact(self.st, src.read_text(errors="replace"))
                if not dst.exists() or dst.read_text(errors="replace") != clean:
                    dst.write_text(clean)

    def _end_blind_review(self, p):
        if not p.get("review_root"):
            return
        tmux("kill-pane", "-t", self.st["panes"][p["agent"]], check=False)
        self.st["panes"][p["agent"]] = p["original_pane"]
        self.st.setdefault("model_now", {}).pop(p["agent"], None)
        if p.get("original_model"):
            self.st["model_now"][p["agent"]] = p["original_model"]

    def _recover_build(self, p, why):
        if self._wait_for_jobs(p, force=True):
            if p not in self.st["pending"]:
                self.st["pending"].append(p)
            return
        s = self.st
        self._diagnostic(why, p)
        if p in s["pending"]:
            send_key(s["panes"][p["agent"]], "C-c")
            s["pending"].remove(p)
        self._end_blind_review(p)
        self._queue_continuation(p)
        s.update(stall=0, last_changed=None, ready_for_review=False, phase="deep", deep_step="build")
        self.save()

    def _progress_watchdog(self):
        if self.st.get("paused"):
            return False
        now = time.time()
        for p in list(self.st.get("pending", [])):
            if p.get("task_id") and model_policy.native_launches(self.st):
                summary_agent.observe(self.dir, p, self.st["agents"])
            if not p.get("task_id") or p.get("interrupted"):
                continue
            if self._wait_for_jobs(p):
                continue
            if p.get("review_root"):
                self._collect_blind_review(p)
            if delivery.migrate(p, now):
                self.save()
            acknowledged = delivery.acknowledged(self.ws, p)
            if acknowledged:
                p.setdefault("acknowledged_at", now)
            pane = self.st["panes"].get(p["agent"])
            from . import pane as panectl
            if pane and model_policy.active(self.st):
                screen = capture(pane)
                if "DUO_WORKER_RESULT:failed" in screen.rsplit("Working on assignment...", 1)[-1] and panectl.idle(screen, p["agent"]):
                    self._handle_agent_failure(p, agent_failures.from_screen(screen))
                    return True
            action = delivery.recovery(p, now, bool(pane and panectl.idle(capture(pane), p["agent"])), acknowledged,
                                       config.CFG.get("task_ack_seconds", 45), config.CFG.get("task_recovery_limit", 3))
            if action in ("exhausted", "stuck_sender"):
                self._pause("Task delivery failed", "Task %s: %s." % (p["task_id"], action),
                            "Inspect the saved assignment and worker. Resume after correcting the delivery failure.")
                notify.send("Duo needs you", "Task delivery failed for " + p["task_id"])
                return True
            if action == "retry":
                delivery.retry(p, now)
                threading.Thread(target=self._nudge, args=(p["agent"], p["prompt"], p.get("_assigned_model")), daemon=True).start()
                self.save()
                return True
            if now - p.get("sampled_at", p["issued_at"]) < 15:
                continue
            p["sampled_at"] = now
            current = {k: v for k, v in _worktree_state(self.ws).items() if not k.startswith(".duo/")}
            if current != p.get("activity_base"):
                p["progress_at"] = now
                changed_paths = [path for path, value in current.items() if p.get("activity_base", {}).get(path) != value and (self.ws / path).is_file()]
                previous_outputs = [row["path"] for row in (broker.node(self.dir, p.get("broker_task_id", p["task_id"])) or {}).get("outputs", [])]
                broker.checkpoint(self.dir, p, self.ws, sorted(set(previous_outputs + changed_paths)), live=True)
            p["activity_base"] = current
            if (p["kind"] in ("build", "revise") and now - p["progress_at"] >= config.CFG.get("progress_nudge_seconds", 300)
                    and not p.get("progress_nudged") and (p.get("waiting") or {}).get("kind") != "approval"):
                p["progress_nudged"] = True
                note = "DUO progress request for task_id %s: save useful partial output now. Record remaining work, failed routes and job IDs. Continue building the same task." % p["task_id"]
                threading.Thread(target=self._nudge, args=(p["agent"], note), daemon=True).start()
            if (config.CFG.get('unfinished_timeouts_enabled', False) and p["kind"] in ("build", "revise") and (p.get("waiting") or {}).get("kind") != "approval"
                    and now - p["progress_at"] >= config.CFG.get("progress_timeout_seconds", 600)):
                self._recover_build(p, "No saved product progress")
                return True
        return False

    def _wait_for_jobs(self, p, force=False):
        if p.get("kind") not in ("build", "revise") or not p.get("task_id"):
            return False
        status = jobs.poll(p, time.time(), force=force)
        if status == "waiting":
            p["job_wait_displayed"] = True
            self.st["detail"] = "Task %s: waiting for a live job; no output is required yet." % p["task_id"]
        elif status == "idle" and p.pop("job_wait_displayed", False):
            self.st["detail"] = "Task %s: collecting job results." % p["task_id"]
        if status == "expired":
            self.st.setdefault("job_runtime_paused_at", time.time())
            if not p.get("job_timeout_notified"):
                p["job_timeout_notified"] = True
                notify.send("Duo needs you", jobs.timeout_message(p))
            self._pause("Long job time limit", "Task %s still has a live or unconfirmed job." % p["task_id"],
                        "Inspect the registered job before extending the limit. No replacement job was started.")
        return status in ("waiting", "expired")

    def _handle_agent_failure(self, p, failure):
        scope = agent_failures.record(self.st, p['agent'], p.get('_assigned_model'), failure)
        self._diagnostic('Agent unavailable: ' + failure['message'], p)
        self.save()
        try:
            replacement = agent_failures.replacement(self.st, p['agent'], p.get('_assigned_model'), p['kind'])
        except ValueError as error:
            agent_failures.schedule_retry(self.st, scope)
            self._pause('Agent unavailable', str(error) + '. ' + failure['message'],
                        'Restore account access or configure another model for this role, then resume.')
            return
        if self._wait_for_jobs(p, force=True):
            return
        if p in self.st['pending']:
            self.st['pending'].remove(p)
        self._end_blind_review(p)
        retry = dict(p, _recovery_model=replacement['model'])
        self.issue(phases.Turn(replacement['agent'], p['phase'], p['kind'], p.get('option_ids', [])), retry_of=retry)

    def _escalate_turn(self, p, why):
        if not model_policy.escalate(self.st, p.get("task_id"), p["kind"], why):
            return False
        from .phases import Turn
        pane = self.st["panes"].get(p["agent"])
        if pane:
            send_key(pane, "C-c")
        if p in self.st["pending"]:
            self.st["pending"].remove(p)
        self._end_blind_review(p)
        self.issue(Turn(p["agent"], p["phase"], p["kind"], p.get("option_ids", [])), fix_of=p,
                   problems=[why + ". Use the stronger model selected by the harness and complete the same task."])
        return True

    def collect(self):
        for p in list(self.st["pending"]):
            if self._wait_for_jobs(p):
                continue
            if p.get("review_root"):
                self._collect_blind_review(p)
            f = self.dir / p["file"]
            if not f.exists():
                continue
            screen = capture(self.st["panes"][p["agent"]]) if self.st.get("panes", {}).get(p["agent"]) else ""
            from . import pane as panectl
            if (model_policy.active(self.st) or "Duo agentic worker:" in screen) and not panectl.idle(screen, p["agent"]):
                continue
            text = f.read_text(errors="replace")
            if not task_output.ready(f, idle=panectl.idle(screen, p["agent"])) or time.time() - f.stat().st_mtime < 0.5:
                continue
            if self._wait_for_jobs(p, force=True):
                continue
            self.st["pending"].remove(p)
            self._end_blind_review(p)
            text = orch.redact(self.st, text)
            f.write_text(text)
            orch.sanitize_logs(self.ws, self.st)
            self.accept(p, text)

    def _problems(self, p, reply, changed):
        probs = list(reply.errors)
        if p['kind'] == 'plan_review':
            if reply.verdict not in ('APPROVED', 'REVISE'):
                probs.append('Return APPROVED or REVISE for the plan')
            if not p.get('plan_signature') or p.get('plan_signature') != roleflow.plan_signature(self.dir / 'plan.md'):
                probs.append('Plan changed during review; inspect the current plan again')
        if reply.stage_requests:
            probs += relay.request_errors(reply.stage_requests, {"research", "feasibility", "verify", "security"})
        if p['kind'] == 'exchange' and not reply.prose.strip():
            probs.append('Write the plan summary')
        if p['kind'] == 'exchange' and not (self.dir / 'plan.md').is_file():
            probs.append('Save the agreed plan to ' + self.rel + '/plan.md')
        if not reply.errors and p.get("task_id") and p["kind"] in ("build", "revise") and reply.outcome not in ("blocked", "needs_human", "answered"):
            if not reply.freeform:
                probs += orch.output_errors(self.ws, reply.to_dict(), p["task_id"])
        if p.get("build_package") and not reply.errors:
            package = p["build_package"]
            product_changes = buildflow.artifact_changes(self.ws, package, package.get("artifact_baseline", p.get("build_artifact_base", {}))) if reply.complete is True else []
            probs += buildflow.build_errors(self.ws, package, reply.to_dict(), changed + product_changes)
        if p["phase"] == "setup" and not reply.errors:
            probs += sandbox.check_spec(self.dir / "sandbox", review=p["kind"] == "setup_check")
        ste_v = []
        unparsed = any(x == "no duo block" or x.startswith("bad JSON") for x in reply.errors)
        b = [] if unparsed else bounds.check(reply, {"task": self.st["task"], "task_type": self.st.get("task_type"), "expected_phase": p["phase"], "changed_files": changed,
                                 "plan_folder": os.path.relpath(p["outcome_dir"], self.ws) if p["kind"] == "exchange" and p.get("outcome_dir") else None,
                                 "handoff_log": [tuple(x) for x in self.st["handoff_log"]], "author": p["agent"]})
        return probs, ste_v, b

    def accept(self, p, text):
        s = self.st
        baseline = p.get("base", {})
        current = _worktree_state(self.ws) if p["kind"] in ("build", "revise") else {}
        products = [name for name, value in current.items() if baseline.get(name) != value]
        reply = task_output.parse(text, dict(p, reply_path=str(self.dir / p["file"])), self.ws, products)
        if p['kind'] in ('cycle_summary', 'goal_assess', 'goal_assign'):
            return self._accept_cycle(p, reply)
        if p['kind'] == 'goal_check':
            return self._accept_goal(p, reply)
        broker.import_saved_questions(self.dir,self.ws,s)
        broker.import_user_questions(self.dir,p.get("broker_task_id",p["task_id"]),reply.prose,str(self.dir/p["file"]))
        if reply.outcome == "needs_human":
            broker.ask(self.dir,p.get("broker_task_id",p["task_id"]),reply.prose.strip() or "What information should I use to continue this task?")
        notes = task_output.review_notes(reply.summary) if p["kind"] == "critique" else {}
        clean = broker.route_result(self.dir, p, text, reply.to_dict())
        if clean != text:
            reply.prose = reply.summary = clean
        outcome_dir = Path(p.get("outcome_dir") or task_output.directory(self.ws, s.get("title") or s["task"], s["id"]))
        task_output.index(self.ws, outcome_dir, dict(p, reply_path=str(self.dir / p["file"])), reply.outputs)
        broker.ensure(self.dir, p.get("broker_task_id", p["task_id"]), s.get("title", "Task"), s["task"])
        broker.checkpoint(self.dir, p, self.ws, reply.outputs, summary=reply.prose if not reply.errors else None,
                          verdict=reply.verdict, outcome=reply.outcome)
        if reply.outcome in ("blocked", "needs_human"):
            broker.event(self.dir, "task_status", p.get("broker_task_id", p["task_id"]), status="partial")
            package_blocker = (p.get("build_package") and s.get("build_workflow") and
                               (reply.blocker in buildflow.BLOCKERS or
                                (reply.outcome == "needs_human" and not reply.blocker)))
            if not package_blocker:
                if p["kind"] in ("build", "revise", "critique"):
                    self.add_entry(p["agent"], p["phase"], p["kind"], reply.prose, summary=reply.summary,
                                   n=p["n"], file=p["file"], task_id=p["task_id"], verdict=reply.verdict,
                                   outcome=reply.outcome, blocker=reply.blocker, **notes)
                s['failed_turn'] = dict(p)
                self._pause('Task blocked', reply.summary,
                            notes.get("review_next") or 'Resolve the specific missing input or task blocker, then resume.',
                            blocker=reply.blocker or "needs_human")
                return
        if goal_loop.full_cycle(s) and p['kind'] == 'exchange':
            name = '02-build-assignment.md'
            archive = reply.prose
            plan = self.dir / 'plan.md'
            if p['kind'] == 'exchange' and plan.is_file():
                archive += '\n\n# Accepted plan\n\n' + plan.read_text()
            goal_loop.record_stage(self.dir, s, name, archive)
        if p["kind"] == "continuation_review":
            broker.finish_review(self.dir, p["task_id"], self.dir / p["file"])
            self.add_entry(p["agent"], p["phase"], p["kind"], reply.prose, n=p["n"], file=p["file"], task_id=p["task_id"])
            s["broker_resume_ready"] = {"id": p["task_id"], "resume": s["broker_resume"][p["task_id"]]}
            self.save()
            return
        if p.get("review_report"):
            report = self.ws / p["review_report"]
            if not report.is_file():
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(reply.prose + "\n")
        if p["agent"] in ("claude", "codex"):
            reply.blocker = harness.native_blocker(reply.blocker, reply.prose)
        if p["kind"] in ("critique", "verify", "secure") and reply.verdict is None and reply.outcome == "progress":
            key = p.get('task_id', '') + ':' + p['kind']
            counts = s.setdefault('invalid_review_counts', {})
            counts[key] = counts.get(key, 0) + 1
            if counts[key] >= config.CFG.get('task_recovery_limit', 3):
                self._pause('Invalid review', 'The reviewer repeatedly returned no verdict.', 'Inspect the saved review and retry with a working reviewer.')
                return
            self.add_entry(p["agent"], p["phase"], "review_note", reply.prose,
                           summary=reply.summary, n=p["n"], file=p["file"], task_id=p.get("task_id"))
            self.issue(phases.Turn(p["agent"], p["phase"], p["kind"], p.get("option_ids", [])),
                       retry_of=p, problems=["Read your saved handoff at %s. Explain whether the work meets the task requirements, or identify required fixes. Use ordinary prose; no special words or format are required." % (self.rel + "/" + p["file"])])
            return
        if p["kind"] in ("research_expand", "research_check"):
            reviewing = p["kind"] == "research_check"
            result, problems = researchflow.validate(self.ws, "research", review=reviewing, require_full=reviewing)
            problems += list(reply.errors)
            if not reviewing:
                if researchflow.progress(self.ws, "research") == p.get("research_progress_base"):
                    problems.append("Research added no new reading, search or method evidence; address the saved gaps")
                after = _worktree_state(self.ws)
                changed = [name for name in set(after) | set(p.get("base", {})) if after.get(name) != p.get("base", {}).get(name)]
                problems += bounds.check(reply, {"task": s["task"], "task_type": "research", "expected_phase": "scout",
                                                "changed_files": changed, "author": p["agent"], "handoff_log": []})
            if reviewing and result:
                reply.verdict = result.get("verdict")
            researchflow.write_evidence_needed(self.ws, "research")
            if problems:
                if not p.get("bounced"):
                    self.issue(phases.Turn(p["agent"], p["phase"], p["kind"]), fix_of=p, problems=problems)
                else:
                    s["failed_turn"] = dict(p, failure_problems=problems)
                    self._pause("Research coverage incomplete", "; ".join(problems), "Resolve the named reading or research gaps, then resume.")
                return
            self.add_entry(p["agent"], p["phase"], p["kind"], reply.prose, summary=reply.summary,
                           n=p["n"], file=p["file"], task_id=p["task_id"], verdict=reply.verdict)
            s["phase"] = s.pop("research_return_phase", "scout")
            if reviewing and result["verdict"] == "APPROVED":
                s["research_coverage"] = researchflow.receipt(self.ws, "research", self._research_key())
                s["research_coverage"].update(review_actor=self.label(p["agent"]), review_mode="isolated_worker" if p.get("review_root") else "shared_context")
                s["research_action"] = None
            elif reviewing:
                s["research_revisions"] = s.get("research_revisions", 0) + 1
                s["research_action"] = "expand"
                s["research_findings"] = result["reason"] + "; " + "; ".join(result["missing_lines"]) + "; " + result["method_assessment"]
                if s["research_revisions"] > 2 and not goal_loop.full_cycle(s):
                    self._pause("Research coverage incomplete", s["research_findings"], "Resolve the named research gaps, then resume.")
            else:
                s["research_author"] = p["agent"]
                _, reading_errors = researchflow.validate(self.ws, "research")
                s["research_action"] = "expand" if reading_errors or reply.complete is False else "check"
            self.save()
            return
        if p["kind"] == "feasibility":
            report, problems = buildflow.read_report(self.ws, self.rel + "/feasibility.json")
            problems += list(reply.errors)
            if reply.task_id != p["task_id"]:
                problems.append("Feasibility reply needs its assigned task_id")
            if reply.phase != p["phase"]:
                problems.append("Feasibility reply needs its assigned phase")
            if problems:
                if not p.get("bounced"):
                    self.issue(phases.Turn(p["agent"], "feasibility", "feasibility"), fix_of=p, problems=problems)
                else:
                    s["failed_turn"] = dict(p, failure_problems=problems)
                    self._pause("Feasibility report invalid", "; ".join(problems), "Correct the saved report, then resume.")
                return
            buildflow.install(s, report, buildflow.signature(self._build_context()), self.ws)
            s.pop("feasibility_recheck", None)
            if not buildflow.complete(s["build_workflow"]):
                s["ready_for_review"] = False
                s.pop("task_delivery", None)
            s["phase"] = s.pop("feasibility_return_phase", "deep")
            self.add_entry(p["agent"], "feasibility", "feasibility", reply.prose, summary=reply.summary,
                           n=p["n"], file=p["file"], task_id=p["task_id"])
            if not buildflow.ready(s["build_workflow"]) and not buildflow.complete(s["build_workflow"]):
                self._pause_build_workflow()
            self.save()
            return
        after = _worktree_state(self.ws)
        for k in set(p["base"]) - set(after):
            after[k] = ["D", None, None]
        changed = sorted(k for k, v in after.items() if p["base"].get(k) != v)
        if p.get("build_package"):
            changed = sorted(set(changed) | set(buildflow.artifact_changes(self.ws, p["build_package"], p.get("build_artifact_base", {}))))
        deleted = [k for k in changed if "D" in after.get(k, [""])[0]]
        probs, ste_v, b = self._problems(p, reply, changed)
        sec = security.check_changes(self.ws, changed) if changed and p["agent"] not in ("claude", "codex") else []
        sec_deny = [f for f in sec if f.action == "deny"]
        # Record prose warnings without spending another agent turn on style alone.
        all_probs = probs + b \
            + ["Security: %s (%s)" % (f.reason, f.detail) for f in sec_deny]
        if all_probs and not p.get("bounced"):
            model_policy.escalate(self.st, p.get("task_id"), p["kind"], "Invalid task reply")
            from .phases import Turn
            self.issue(Turn(p["agent"], p["phase"], p["kind"], p["option_ids"]), fix_of=p, problems=all_probs)
            return
        if all_probs and p.get("task_id"):
            if p["kind"] in ("build", "revise"):
                self._recover_build(p, "Invalid output: " + "; ".join(all_probs))
            else:
                s["failed_turn"] = dict(p, failure_problems=all_probs)
                self._pause("Invalid task output", "; ".join(all_probs), "Check the named files, then resume the same task.")
            return
        if reply.stage_requests:
            s.setdefault("stage_requests", {}).update(reply.stage_requests)
        real = [c for c in changed if not c.startswith(".duo/")]
        has_log = any(c.startswith(".duo/logs/") for c in changed)
        completed_output = bool(p.get("task_id") and reply.complete is True and not all_probs and reply.outputs)
        outcome, blocker = harness.classify(reply.outcome, reply.blocker, real, has_log or completed_output or p['kind'] in ('assess', 'exchange', 'plan_review'))
        started = p.get("first_issued", p["issued_at"])
        e = self.add_entry(p["agent"], p["phase"], p["kind"], reply.prose, summary=reply.summary or "(no summary)",
                           n=p["n"], file=p["file"], options=reply.options, decisions=reply.decisions,
                           handoffs=reply.handoffs, verdict=reply.verdict, ste=[asdict(v) for v in ste_v],
                           bounds=b, errors=probs, invalid=bool(probs), bounced=p.get("bounced", []),
                           changed=changed, outcome=outcome, blocker=blocker, checkpoint=p.get("checkpoint"), high_impact=bounds.is_high_impact(changed, deleted),
                           duration=int(time.time() - started), task_type=reply.task_type,
                           task_id=p.get("task_id"), outputs=reply.outputs, complete=reply.complete,
                           security=[f.to_dict() for f in sec], review=reply.security if p["kind"] == "secure" else [],
                           build_evidence=reply.build_evidence, blocker_detail=reply.blocker_detail,
                           build_package=(p.get("build_package") or {}).get("id"),
                           **notes)
        if p['kind'] in ('assess', 'exchange') and outcome in ('blocked', 'needs_human'):
            s['failed_turn'] = dict(p, failure_problems=[reply.summary])
            self._pause('Task assessment blocked', reply.summary, 'Resolve the named input or decision, then resume.')
            return
        if [c for c in changed if not c.startswith(".duo/")]:
            s["code_changed"] = True
        if p["kind"] in ("build", "revise"):
            s["last_changed"] = self._guard_value(outcome, blocker, len(real), has_log)
            if p.get("build_package"):
                workflow = s["build_workflow"]
                workflow["build_changed"] = sorted(set(workflow.get("build_changed", [])) | set(real))
                s["last_build_changed"] = workflow["build_changed"]
            else:
                s["last_build_changed"] = real
            s["last_build_checkpoint"] = p.get("checkpoint")
            s["ready_for_review"] = (reply.complete is True if p.get("task_id") else reply.ready_for_review)
            if s["ready_for_review"] and p.get("task_id") and not p.get("build_package"):
                s["task_delivery"] = orch.receipt(self.ws, p["task_id"], reply.outputs)
                s["task_delivery"]["status"] = "built"
                (self.dir / "task-delivery.json").write_text(json.dumps(s["task_delivery"], indent=2) + "\n")
        elif p["phase"] == "deep" and p["kind"] in ("critique", "verify", "secure"):
            guard_outcome = "progress" if (outcome == "blocked" and blocker == "no_outcome") else outcome
            s["stall"] = harness.next_stall(s.get("stall", 0), guard_outcome, bool(real) or has_log)
        if changed:
            s["sec_blocked"] = bool(sec_deny)
            for f in sec:
                security.log(f, run=s["id"], agent=p["agent"], source="file",
                             action="blocked" if f.action == "deny" else "needs review")
            if sec_deny:
                self.add_entry("system", p["phase"], "security", "Security blocked a change by %s.\n\n%s" % (
                    self.label(p["agent"]), "\n".join("- %s: %s" % (f.reason, f.detail) for f in sec_deny)),
                    summary="Security blocked a change by %s: %s" % (self.label(p["agent"]), sec_deny[0].reason),
                    findings=[f.to_dict() for f in sec])
        for h in reply.handoffs:
            if isinstance(h, dict):
                s["handoff_log"].append([p["agent"], str(h.get("to", "")), str(h.get("what", ""))])
        self._ledger(e, outcome, blocker, len(real), has_log, p)
        if (p.get("task_id") and p["kind"] in ("build", "revise") and
                outcome == "blocked" and blocker not in buildflow.BLOCKERS):
            self.absorb(e, reply)
            self._recover_build(p, blocker or "No product progress")
            return
        if p.get("build_package") and outcome != "answered":
            workflow = s["build_workflow"]
            package = next((x for x in workflow["packages"] if x["id"] == p["build_package"]["id"]), None)
            if outcome in ("blocked", "needs_human"):
                if package:
                    package.update(status="blocked", blocker_detail=reply.blocker_detail)
                else:
                    # An integration correction is part of the whole build, never silently complete.
                    workflow["packages"].append(dict(p["build_package"], status="blocked", blocker_detail=reply.blocker_detail))
                s.update(ready_for_review=False, last_changed=None)
            elif reply.complete is True:
                if package:
                    package["status"] = "done"
                workflow["receipts"][p["build_package"]["id"]] = orch.receipt(self.ws, p["task_id"], reply.outputs)
                s["ready_for_review"] = buildflow.complete(workflow)
                if s["ready_for_review"]:
                    outputs = sorted({x["path"] for receipt in workflow["receipts"].values() for x in receipt["outputs"]})
                    s["task_delivery"] = orch.receipt(self.ws, p["task_id"], outputs)
                    s["task_delivery"]["status"] = "built"
                    (self.dir / "task-delivery.json").write_text(json.dumps(s["task_delivery"], indent=2) + "\n")
            self.absorb(e, reply)
            if not buildflow.ready(workflow) and not buildflow.complete(workflow):
                # An incomplete feasible package remains ready; only exhausted feasible work stops the build.
                self._pause_build_workflow()
            self.save()
            return
        if outcome == "blocked" and blocker in harness.BLOCKERS and blocker != "no_outcome":
            self._pause_for_blocker(blocker, reply.summary + " " + "; ".join(reply.remaining or []))
        else:
            self._loop_checks(p["agent"], reply, outcome, changed=bool(real))
        self.absorb(e, reply)

    def _guard_value(self, outcome, blocker, n, has_log):
        """What the build or revise turn adds to the stall guard: >0 resets, 0 counts, None neither."""
        if outcome == "progress":
            return n
        if outcome == "blocked" and blocker == "no_outcome":
            return 0
        return None

    def _ledger(self, e, outcome, blocker, n, has_log, p):
        row = {"n": e["n"], "agent": e["author"], "phase": e["phase"], "kind": e["kind"], "outcome": outcome,
               "blocker": blocker, "changed": n, "logs": has_log, "duration": e.get("duration"), "checkpoint": p.get("checkpoint")}
        led = self.st.setdefault("ledger", [])
        token_ledger.record_reply(self.st, e["n"], len(e.get("prose") or ""), e.get("duration"))
        row["task_id"] = p.get("task_id")
        led.append(orch.redact(self.st, row))
        del led[:-300]

    def _loop_checks(self, agent, reply, outcome, changed=False):
        s = self.st
        fps = s.setdefault("last_fp", {})
        reps = s.setdefault("repeat_count", {})
        fp = harness.fingerprint(reply.prose)
        same = fps.get(agent) == fp and outcome != "answered" and not changed
        reps[agent] = reps.get(agent, 0) + 1 if same else 1
        fps[agent] = fp
        repeated = reps[agent] >= harness.LOOP_REPEAT_LIMIT
        s["answer_streak"] = s.get("answer_streak", 0) + 1 if outcome == "answered" else 0
        if repeated:
            return self._pause("Repeated reply", "%s wrote the same reply %d times in a row." % (self.label(agent), reps[agent]),
                               "Tell Duo what to change, or resume to try again.")
        if s["answer_streak"] >= harness.LOOP_ANSWER_STREAK:
            return self._pause("Questions instead of work", "The last %d turns only answered questions and changed nothing." % s["answer_streak"],
                               "Tell Duo what to build or change next, then resume.")

    def absorb(self, e, reply):
        """Fold one accepted reply into the phase-engine state."""
        s = self.st
        if e['kind'] == 'plan_review':
            s['plan_verdict'] = reply.verdict
            s['plan_feedback'] = reply.prose
            plan = self.dir / 'plan.md'
            s['accepted_plan_signature'] = roleflow.plan_signature(plan) if reply.verdict == 'APPROVED' else None
            s.pop('planned_current_goal', None)
            if s['accepted_plan_signature']:
                for line in plan.read_text(errors="replace").splitlines():
                    if line.strip().lower().startswith('current goal:'):
                        s['planned_current_goal'] = line.split(':', 1)[1].strip()
                        break
            self.save()
            return
        if e['kind'] == 'exchange':
            s['stage_requests'] = reply.stage_requests
            s['task_type'] = s.get('user_task_type') or reply.task_type or 'general'
            s['options'] = [{'id': 'O1', 'title': 'Agreed approach', 'detail': reply.prose,
                             'proposer': e['author'], 'impact': 'low', 'probe': '', 'pros': [], 'cons': []}]
            s['chosen'] = 'O1'
            self.write_options()
            if s.get('mode') == 'always' or (s.get('mode') == 'big' and any(
                    isinstance(d, dict) and d.get('impact') == 'high' for d in reply.decisions)):
                self._pause('Plan decision', 'The agreed plan needs your decision.', 'Read the plan, then resume to accept it or send a change.')
            self.save()
            return
        if e["kind"] == "critique":
            s["last_verdict"] = reply.verdict
            prints = s.get("review_prints", []) if reply.verdict != "APPROVED" else []
            s["review_prints"] = (prints + ([repeat_defect.fingerprint(reply.prose)] if reply.verdict != "APPROVED" else []))[-repeat_defect.LOOP_AFTER:]
            s.pop("repeat_defect", None)
            s.pop("loop_reason", None)
            s["review_run"] = s.get("review_run", 0) + 1 if reply.verdict != "APPROVED" else 0
            if loop_judge.due(s["review_run"]) and not repeat_defect.looping(s["review_prints"]):
                stuck, why = loop_judge.judge(s, self.ws, self.dir)
                if stuck:
                    s["repeat_defect"], s["loop_reason"] = True, why
        elif e["kind"] == "secure":
            high = [x for x in reply.security if isinstance(x, dict) and str(x.get("severity", "")).lower() == "high"]
            s["secure_ok"] = reply.verdict == "APPROVED" and not high and not s.get("sec_blocked")
            for x in reply.security:
                if isinstance(x, dict):
                    sev = str(x.get("severity", "low")).lower()
                    security.log(security.Finding("security_review", "deny" if sev == "high" else "review",
                                                  str(x.get("issue", "")), "%s. Fix: %s" % (x.get("file", ""), str(x.get("fix", "")))),
                                 run=s["id"], agent=e["author"], source="review", action="blocked" if sev == "high" else "needs review")
        elif e["phase"] == "verify":
            s["verify_passed"] = reply.verdict == "APPROVED" and not s.get("sec_blocked")
        self.save()

    # -------------------------------------------------------------------- loop
    def _active_budget(self):
        """Apply limits while agents are working, including turns that never return a reply."""
        s = self.st
        now = time.time()
        self._runtime.observe(s)
        band, hours = harness.budget(s)
        rule = None
        if band == "over":
            rule, why = "Active task time limit", "The run has used %.1f active hours of its %g-hour limit." % (hours, config.CFG["active_time_limit_hours"])
        else:
            for p in s.get("pending", []):
                if self._wait_for_jobs(p):
                    if s.get("paused"):
                        return True
                    continue
                key = "review_timeout_seconds" if p["kind"] in ("critique", "secure") else "turn_timeout_seconds"
                limit = config.CFG.get(key, 600 if key == "review_timeout_seconds" else 1800)
                if config.CFG.get('unfinished_timeouts_enabled', False) and now - p.get("resumed_at", p["issued_at"]) >= limit:
                    if p.get("task_id") and p["kind"] in ("build", "revise"):
                        self._recover_build(p, "Turn time limit")
                        return True
                    if p.get("task_id") and self._escalate_turn(p, "Turn time limit"):
                        return True
                    rule, why = "Turn time limit", "%s spent %d seconds on %s without a completed reply." % (p["agent"], limit, p["kind"])
                    break
        if rule is None:
            return False
        for p in s.get("pending", []):
            if self._wait_for_jobs(p):
                continue
            pane = s.get("panes", {}).get(p["agent"])
            if pane:
                send_key(pane, "C-c")
            p["interrupted"] = True
        next_step = ("Disable or increase the active task time limit in Defaults, then continue." if rule == "Active task time limit"
                     else "Check the saved work. Resume to continue. Run long experiments as separate recorded jobs.")
        self._pause(rule, why, next_step)
        return True

    def tick(self):
        s = self.st
        retry_at = s.get('availability_retry_at')
        if retry_at is not None and s.get('status') == 'waiting' and time.time() >= retry_at:
            s.pop('availability_retry_at', None)
            retry = list(s.get('pending', []))
            s.pop('failed_turn', None)
            s.update(pending=[], stop=None, paused=False, status='running')
            for p in retry:
                self._end_blind_review(p)
                self.issue(phases.Turn(p['agent'], p['phase'], p['kind'], p.get('option_ids', [])), retry_of=p)
            self.save()
            return
        if s.get("status") == "done" and model_policy.native_launches(s):
            completion_summary.request(self.dir,self.ws,s)
        self._runtime.observe(s)
        if self._runtime.due():
            self.save()
        if s["status"] not in ACTIVE:
            return
        if s.get("paused"):
            return
        if not self._tmux_alive():
            if any(self._wait_for_jobs(p) for p in s.get("pending", [])):
                return
            return self.resume()
        dead = [a for a, p in s["panes"].items() if pane_dead(p)]
        if dead:
            for a in dead:
                if any(p["agent"] == a and self._wait_for_jobs(p) for p in s.get("pending", [])):
                    continue
                if s["restarts"].get(a, 0) < config.CFG["auto_restart"]:
                    self.restart_agent(a, auto=True)
                elif len(s["agents"]) > 1:
                    self.drop_agent(a)
                elif a not in s["dead"]:
                    s["dead"].append(a)
            if s["dead"]:
                self.collect()
                detail = "%s stopped and did not restart. Restart it to continue." % " and ".join(self.label(a) for a in s["dead"])
                if (s["status"], s["detail"]) != ("waiting", detail):
                    s["status"], s["detail"] = "waiting", detail
                    self.save()
            return
        if s["status"] in ("booting", "waiting"):
            return self.check_boot()
        if self._active_budget():
            return
        self.watch()
        self.collect()
        if self._progress_watchdog():
            return
        if s["pending"] or s["status"] not in ("running", "paused"):
            return
        if s["paused"]:
            if s["status"] != "paused":
                s["status"], s["detail"] = "paused", "Resume to carry on."
                self.save()
            return
        if s["status"] == "paused":
            s["status"] = "running"
        if s.get("gate") and s["gate"].get("running"):
            return
        if s.get("gate") and s["gate"].get("done"):
            s["gate_result"] = s["gate"]["result"]
            s["gate"] = None
        if self._dispatch_continuation():
            return
        band, hours = harness.budget(s)
        if band == "over":
            return self._pause("Active task time limit", "The run has used %.1f active hours of its %g-hour limit." % (hours, config.CFG["active_time_limit_hours"]),
                               "Disable or increase the active task time limit in Defaults, then continue.")
        if band == "warn" and not s.get("budget_warned"):
            s["budget_warned"] = True
            message = "The run has used %.1f of %g active hours." % (hours, config.CFG["active_time_limit_hours"])
            self.add_entry("system", s["phase"] or "task", "warning", message, summary=message)
        if (not s.get("failed_turn") or s["failed_turn"].get("kind") in ("build", "revise")) and self._ensure_research_coverage():
            return
        if s.get("build_workflow") and not s.get("feasibility_recheck") and s.get("phase") == "deep" and s.get("deep_step") == "build" and s.get("stall", 0) >= 1 and s.get("last_changed") == 0 and s.get("build_task_id"):
            self._recover_build({"task_id": s["build_task_id"], "agent": s["roles"]["builder"]}, "Build replies changed no product files")
            return
        if s.get("failed_turn") and (s["failed_turn"].get("kind") not in ("build", "revise") or
                                     (s.get("build_workflow") and not s.get("feasibility_recheck"))):
            self.retry_failed_turn()
            return
        if relay.required(s, 'feasibility') and s.get("chosen") and s.get("phase") in ("decide", "deep", "feasibility"):
            if (not s.get("build_workflow") or s.get("feasibility_recheck") or
                    s["build_workflow"].get("key") != buildflow.signature(self._build_context())):
                self.issue(phases.Turn(s["roles"]["lead"], "feasibility", "feasibility", [s["chosen"]]))
                return
            workflow = s["build_workflow"]
            if not buildflow.ready(workflow) and not buildflow.complete(workflow):
                self._pause_build_workflow()
                return
        step = phases.next_turns(s)
        if s.get("escalated") and not s.get("escalation_noted"):
            return self._escalate_quick()
        if isinstance(step, str) and step.startswith("gate:"):
            return self.start_gate(step.split(":", 1)[1])
        if step == "pause":
            s["status"], s["detail"] = "waiting", "The sandbox build failed after three repairs. Check the log, then resume."
            s["paused"] = True
            self.add_entry("system", "setup", "warning", "The sandbox failed 3 times.\n\n%s" % (s.get("gate_log") or ""),
                           summary="The sandbox build failed")
            notify.send("Duo needs you", "The sandbox build failed.")
            self.save()
            return
        if step == "stalled":
            if s.get("repeat_defect") and s.get("loop_reason"):
                why = "a loop check found the run is stuck: %s" % s["loop_reason"]
            elif s.get("repeat_defect"):
                why = ("the reviewer has raised the same defect %d times in a row and the builder is not fixing it. "
                       "It is probably something only you can change, such as a file outside this project" % repeat_defect.LOOP_AFTER)
            elif s.get("stall", 0) >= phases.STALL_LIMIT:
                why = "the last %d build or revise turns changed no files%s" % (
                    phases.STALL_LIMIT, ", and a security block is still open" if s.get("sec_blocked") else "")
            else:
                why = "the turn budget (%s rounds) or the verify retries ran out" % s["max_rounds"]
            s["status"], s["paused"] = "waiting", True
            s["detail"] = "Duo stopped this run early: %s. Read the log, then tell Duo what to change, or start again." % why
            s["stop_reason"] = self.stop_reason()
            self.add_entry("system", s["phase"] or "deep", "warning", s["detail"], summary=s["detail"])
            self.save()
            return
        if isinstance(step, list):
            code = self._preflight_code(step)
            if code:
                return self._pause_for_blocker(code)
        if step == "done" and any(not c["done"] for c in s.get("checklist", [])):
            open_items = [c["text"] for c in s["checklist"] if not c["done"]]
            return self._pause("Checklist open", "%d checklist item(s) are not done: %s." % (len(open_items), "; ".join(open_items[:3])),
                               "Mark them done on the run page, or tell Duo what is left, then resume.")
        if step == "done":
            self.finish()
        else:
            for t in step:
                self.issue(t)

    # ----------------------------------------------------------------- sandbox
    def start_gate(self, kind):
        """Run Docker work in the background; tick() picks up the result."""
        s = self.st
        s["gate"] = {"kind": kind, "running": True}
        s["detail"] = "Building the sandbox image" if kind == "image" else "Running the app in a fresh container"
        self.save()
        threading.Thread(target=self._gate, args=(kind,), daemon=True).start()

    def _gate(self, kind):
        s = self.st
        try:
            if kind == "image":
                d = self.dir / "sandbox"
                ok, log = sandbox.build_image(d, s["id"])
                if ok:
                    spec = sandbox.load_spec(d)
                    ok, log = sandbox.start_container(self.ws, s["id"], spec.get("ports"))
                if ok:
                    sandbox.write_wrapper(self.ws, s["id"])
                    ok, log = sandbox.check_tools(s["id"], spec.get("tools"))
                with LOCK:
                    s["sandbox_ready"] = bool(ok)
            else:
                spec = sandbox.load_spec(self.dir / "sandbox")
                inputs = _source_fingerprint(self.ws)
                ok, log = sandbox.smoke(self.ws, s["id"], spec)
                log_path = self.dir / "smoke.log"
                log_path.write_text(log)
                with LOCK:
                    s["smoke_evidence"] = {"command": spec.get("smoke"), "log": str(log_path), "inputs": inputs} \
                        if ok and inputs is not None and inputs == _source_fingerprint(self.ws) else None
                if ok and spec.get("ports"):
                    self._shot(spec, log)
        except Exception as ex:
            ok, log = False, "Sandbox error: %s" % ex
        with LOCK:
            s["gate"] = {"kind": kind, "running": False, "done": True, "result": {"ok": bool(ok), "log": log}}
            self.save()

    def _shot(self, spec, smoke_log):
        """Take a screenshot of the running app after a passing smoke test. Never fails the run."""
        s = self.st
        try:
            up, log = sandbox.serve(s["id"], spec)
            if up:
                time.sleep(3)                       # give the app a moment to bind its port
                res = shots.capture(self.ws, s["id"], self.dir, s.get("round", 1), spec.get("ports"), smoke_log)
            else:
                saved = shots.capture(self.ws, s["id"], self.dir, s.get("round", 1), None, smoke_log)
                res = dict(saved, warning="The app did not start for a screenshot: " + log[-200:])
        except Exception as ex:
            res = {"kind": "text", "file": None, "warning": "Screenshot error: %s" % ex}
        finally:
            sandbox.unserve(s["id"])
        with LOCK:
            s.setdefault("shots", []).append(dict(res, round=s.get("round", 1), at=now_hm()))

    def check_boot(self):
        s = self.st
        if time.time() - s["booted_at"] < config.CFG["boot_seconds"]:
            return
        settled, blocked, login = True, [], []
        auto = config.CFG["auto_trust"] and trust.in_root(self.ws)
        update = []
        for a, pane in s["panes"].items():
            txt = capture(pane).lower()
            kind = classify_screen(txt)
            if kind == "login":
                login.append(self.label(a))
                continue
            if kind == "update":
                update.append(self.label(a))
                continue
            if kind == "trust":
                if auto and s["auto_answered"].get(a, 0) < 2 and time.time() - self._last_answer.get(a, 0) > 2:
                    send_key(pane, "Enter")
                    s["auto_answered"][a] = s["auto_answered"].get(a, 0) + 1
                    self._last_answer[a] = time.time()
                    settled = False
                    continue
                blocked.append(self.label(a))
            if self._boot_prev.get(a) != txt:
                settled = False
            self._boot_prev[a] = txt
        if login or blocked or update:
            detail = ("Sign in to %s in its pane" % " and ".join(login)) if login else \
                ("Choose Skip on the update prompt in the %s pane" % " and ".join(update)) if update else \
                "Answer the startup prompt in the %s pane" % " and ".join(blocked)
            if (s["status"], s["detail"]) != ("waiting", detail):
                s["status"], s["detail"] = "waiting", detail
                self.save()
            return
        if settled or time.time() - s["booted_at"] > config.CFG["boot_seconds"] * 4:
            s["status"], s["detail"] = "running", "Agents ready"
            self.renudge()
            self.save()

    def renudge(self):
        s = self.st
        for p in s["pending"]:
            if p["agent"] in s["renudge"]:
                ctx = "You restarted, so read %s/PROTOCOL.md and %s/memory.md first." % (self.rel, self.rel)
                if p.get('kind') not in ('assess', 'goal_assess') and ctx not in p["prompt"]:  # independent assessments never read peer history
                    head, _, rest = p["prompt"].partition(". ")
                    p["prompt"] = "%s. %s %s" % (head, ctx, rest)
                if p["agent"] in s["needs_context"]:
                    s["needs_context"].remove(p["agent"])
                s["protocol_sent"][p["agent"]] = True
                p.pop("interrupted", None)
                p.pop("delivery_version", None)
                delivery.migrate(p, time.time())
                p["resumed_at"] = time.time()
                threading.Thread(target=self._nudge, args=(p["agent"], p["prompt"], p.get("_assigned_model")), daemon=True).start()
        s["renudge"] = []

    def _issue_goal_assessments(self):
        s = self.st
        assigned = {p['agent'] for p in s.get('pending', []) if p['kind'] == 'goal_assess'}
        for actor in [s['roles']['lead']]:
            if actor not in s['goal'].get('assessments', {}) and actor not in assigned:
                self.issue(phases.Turn(actor, 'deep', 'goal_assess'))

    def _accept_cycle(self, p, reply):
        s = self.st
        kind = p['kind']
        artifacts = [s.get('goal', {}).get('detailed_summary')] if kind == 'cycle_summary' else []
        broker.record_stage_reply(self.dir, p, self.ws, reply, artifacts)
        self.add_entry(p['agent'], p['phase'], kind, reply.prose,
                       n=p['n'], file=p['file'], task_id=p['task_id'])
        if reply.outcome in ('blocked', 'needs_human'):
            self._pause('Cycle information needed', reply.prose, 'Resolve the named input, then resume.')
            return
        errors = goal_loop.accept_summary(self.dir, self.ws, s) if kind == 'cycle_summary' else ([] if reply.prose.strip() else ['Write the complete assessment or assignment.'])
        if errors:
            count = s.get('cycle_invalid_reports', 0) + 1
            s['cycle_invalid_reports'] = count
            if count < 3:
                self.issue(phases.Turn(p['agent'], p['phase'], kind), problems=errors)
            else:
                self._pause('Cycle report invalid', '; '.join(errors), 'Correct the named report, then resume.')
            return
        s['cycle_invalid_reports'] = 0
        if kind == 'cycle_summary':
            self.issue(phases.Turn(s['roles']['critic'], 'deep', 'goal_check'))
        elif kind == 'goal_assess':
            s['goal'].setdefault('assessments', {})[p['agent']] = reply.prose
            goal_loop.record_stage(self.dir, s, goal_loop.assessment_name(s, p['agent']), reply.prose)
            if all(a in s['goal']['assessments'] for a in [s['roles']['lead']]):
                self.issue(phases.Turn(s['roles']['builder'], 'deep', 'goal_assign'))
        else:
            s['goal']['status'] = 'active'
            assignment = goal_loop.record_stage(self.dir, s, '02-build-assignment.md', reply.prose)
            s['goal']['next_action'] += '\nBuild assignment: ' + str(assignment)
            s.update(phase='frame', role_plan_step='draft', plan_verdict=None)
            self.issue(phases.Turn(s['roles']['lead'], 'frame', 'exchange'))
        self.save()

    def _accept_goal(self, p, reply):
        s = self.st
        broker.record_stage_reply(self.dir, p, self.ws, reply, [s['goal'].get('report_file')])
        self.add_entry(p['agent'], p['phase'], 'goal_check', reply.prose,
                       n=p['n'], file=p['file'], task_id=p['task_id'])
        if goal_loop.full_cycle(s):
            goal_loop.record_stage(self.dir, s, '06-goal-review.md', reply.prose)
        status, errors = goal_loop.consume(self.dir, self.ws, s)
        if errors and goal_loop.full_cycle(s) and not goal_loop.summary_current(self.ws, s):
            s['goal_invalid_reports'] = 0
            self.issue(phases.Turn(s['roles']['critic'], 'deep', 'cycle_summary'))
            return
        if errors:
            count = s.get('goal_invalid_reports', 0) + 1
            s['goal_invalid_reports'] = count
            if count < 3:
                self.issue(phases.Turn(s['roles']['critic'], 'deep', 'goal_check'), problems=errors)
            else:
                self._pause('Goal assessment invalid', '; '.join(errors), 'Correct the goal report, then resume.')
            return
        s['goal_invalid_reports'] = 0
        if status == 'continue' and goal_loop.full_cycle(s):
            goal_loop.start_reassessment(self.dir, s)
            self._issue_goal_assessments()
        elif status == 'continue':
            if s['round'] >= s['max_rounds']:
                self._pause('Goal work budget', s['goal']['reason'], 'Increase the task budget, then resume.')
                # Resume must build, rather than reuse the earlier stage approval.
                s.update(phase='deep', deep_step='build', ready_for_review=False,
                         last_verdict=None, verify_passed=False)
            else:
                s['round'] += 1
                s.update(phase='deep', deep_step='build', ready_for_review=False,
                         last_verdict=None, verify_passed=False)
                self.issue(phases.Turn(s['roles']['builder'], 'deep', 'revise', [s['chosen']]))
        elif status == 'blocked':
            self._pause('Goal blocked', s['goal']['reason'], 'Resolve the named blocker or change the goal, then resume.')
        else:
            s['phase'] = 'done'
        self.save()

    def _check_codex_history(self):
        """Duo's Codex calls are ephemeral and must leave nothing in the user's Codex history. Say so if one did."""
        s = self.st
        if "codex" not in s.get("agents", []) or s.get("codex_guard_checked"):
            return
        s["codex_guard_checked"] = True
        try:
            found = codex_guard.leaked(s.get("codex_guard_since") or time.time())
        except OSError:
            return
        if found:
            s["codex_leaks"] = found
            text = ("%d Codex session(s) from this run were saved to your Codex history, which Duo is built to avoid. "
                    "They begin with a Duo prompt. Duo has not touched them. First: %s" % (len(found), found[0]))
            self.add_entry("system", "done", "notice", text, summary="Duo prompts found in your Codex history")
            self._diagnostic(text, operation="codex_history")

    def _closes_without_goal_check(self):
        """Approved, verified work with a recorded delivery needs no summary and goal-check turns.
        Anything unfinished, blocked, researched or asked to keep going still gets the full goal check."""
        s = self.st
        return bool(config.CFG.get("fast_close", True) and goal_loop.enabled(s)
                    and s.get("last_verdict") == "APPROVED" and s.get("verify_passed") and s.get("task_delivery")
                    and s.get("ready_for_review") is not False and not s.get("stop")
                    and s.get("task_type") != "research" and not s.get("checklist_open")
                    and s.get("goal", {}).get("status") in ("active", "achieved"))

    def finish(self):
        s = self.st
        ok = bool(s.get("verify_passed"))
        if ok and s.get("task_delivery") and not orch.unchanged(self.ws, s["task_delivery"]):
            if goal_loop.enabled(s):
                s['goal']['status'] = 'active'
            self._recover_build({"task_id": s["task_delivery"]["task_id"], "agent": s["roles"]["builder"]}, "Output changed after build approval")
            return
        if s.get('goal_loop_version') == 1:
            s['goal_loop_version'] = 2
        if goal_loop.full_cycle(s) and s.get('goal', {}).get('status') == 'assessing':
            self._issue_goal_assessments()
            return
        if goal_loop.full_cycle(s) and s.get('goal', {}).get('status') == 'assigning':
            self.issue(phases.Turn(s['roles']['builder'], 'deep', 'goal_assign'))
            return
        if ok and s.get("quick"):
            s['goal']['status'] = 'achieved'
            s['goal']['reason'] = 'Quick task: one agent built and checked it; no separate review.'
        elif ok and self._closes_without_goal_check():
            s['goal']['status'] = 'achieved'
            s['goal']['reason'] = 'The reviewer approved with check evidence and the plan was fully built.'
            self.add_entry("system", "done", "notice", s['goal']['reason'] + " No separate goal check was needed.",
                           summary="Closed without a separate goal check")
        elif goal_loop.enabled(s) and not goal_loop.achievement_current(self.ws, s):
            if not ok:
                self._pause('Goal unfinished', 'Required checks remain open.', 'Resolve the failed checks, then resume.')
                return
            kind = 'cycle_summary' if goal_loop.full_cycle(s) and not goal_loop.summary_current(self.ws, s) else 'goal_check'
            self.issue(phases.Turn(s['roles']['critic'], 'deep', kind))
            return
        self._check_codex_history()
        if s.get("task_delivery"):
            record = s["task_delivery"]
            record["status"] = "done" if ok else "unverified"
            folder = self.dir / "task-deliveries"
            folder.mkdir(exist_ok=True)
            (folder / (record["task_id"] + ".json")).write_text(json.dumps(record, indent=2) + "\n")
            (self.dir / "task-delivery.json").write_text(json.dumps(record, indent=2) + "\n")
        summary = ('Done: quick task, built and checked by one agent' if ok and s.get("quick")
                   else 'Done: independent review and required checks passed' if ok else 'Stopped: required checks remain open')
        if s.get("task_delivery"):
            summary += "; task_id %s; outputs: %s" % (s["task_delivery"]["task_id"], ", ".join(x["path"] for x in s["task_delivery"]["outputs"]))
        self.add_entry("system", "done", "done", summary, summary=summary)
        if not s.get("history_recorded"):
            durs = {}
            for e in s["entries"]:
                if e["author"] not in ("human", "system") and e.get("duration"):
                    durs.setdefault(e["phase"], []).append(e["duration"])
            history.record_durations(self.history_path(), s["task_type"] or "general",
                                     {p: int(statistics.median(v)) for p, v in durs.items()})
        s["history_recorded"] = True
        s["status"] = "done"
        s['detail'] = ('Built and checked by one agent (quick task)' if ok and s.get("quick")
                       else 'Independent review and required checks passed' if ok else 'Required checks remain open')
        self.save()

    # ---------------------------------------------------------------- controls
    def human_message(self, text, task_id=None):
        """Save a message for the agents. The next turn reads it. An exact repeat of the last message is not saved twice."""
        s = self.st
        last = next((e for e in reversed(s["entries"]) if e["author"] == "human" and e["kind"] == "message"), None)
        live = [p for p in s["pending"] if not p.get("waiting") and
                not p.get('interrupted') and s.get('status')=='running' and not s.get('paused') and
                (task_id is None or p.get("broker_task_id",p.get("task_id")) == task_id)]
        if last and last.get("prose", "").strip() == text.strip():
            return {"saved": False, "duplicate": True, "read_next": not live}
        self.add_entry("human", s["phase"] or "task", "message", text, summary=text,
                       intent=harness.intent(text))
        if text.startswith("Feedback from the user"):
            return {"saved": True, "duplicate": False, "read_next": not live}
        s["stall"] = 0                                # a person's message gets a fresh try, so the loop guard does not stop it
        if live:                                      # a turn is open: tell that agent now
            live[0]["answer_due"] = True
            note = "DUO note: the human wrote a message. Before anything else, answer it: %s Answer in ordinary prose and continue the task." % text
            threading.Thread(target=self._nudge, args=(live[0]["agent"], note), daemon=True).start()
            self.st["last_seen"][live[0]["agent"]] = min(self.st["last_seen"][live[0]["agent"]], self.st["entries"][-1]["n"] - 1)
        self.save()
        return {"saved": True, "duplicate": False, "read_next": not live}

    def ask(self, question='', focus=None, **options):
        """A durable read-only conversation never rewrites execution state."""
        return run_chat.request(self.dir,self.ws,self.st,question,focus,**options)

    def rollback(self, turn):
        s = self.st
        if [p for p in s["pending"] if not p.get("waiting")]:
            raise ValueError("An agent is still working. Wait for its turn to finish, then roll back.")
        path = self.dir / "checkpoints" / str(int(turn))
        if not path.exists():
            raise ValueError("No checkpoint for turn %s." % turn)
        res = harness.restore(self.ws, path)
        msg = "Rolled back to before turn %s: %d file(s) restored%s." % (
            turn, len(res["restored"]), (", %d file(s) created since were kept" % len(res["kept_new"])) if res["kept_new"] else "")
        self.add_entry("system", s["phase"] or "task", "rollback", msg, summary=msg)
        self.save()
        return res

    def checklist_edit(self, action, text=None, item_id=None):
        s = self.st
        items = s.setdefault("checklist", [])
        if action == "add":
            t = (text or "").strip()
            if not t:
                raise ValueError("Write the item first.")
            items.append({"id": max([i["id"] for i in items], default=0) + 1, "text": t, "done": False})
        elif action in ("toggle", "remove"):
            it = next((i for i in items if i["id"] == int(item_id or 0)), None)
            if not it:
                raise ValueError("No such checklist item.")
            if action == "remove":
                items.remove(it)
            else:
                it["done"] = not it["done"]
        else:
            raise ValueError("Unknown checklist action.")
        self.save()
        return items

    def control(self, action):
        s = self.st
        if action == "pause":
            s["paused"] = True
        elif action == "resume":
            s["paused"] = False
            s["stall"], s["last_changed"] = 0, None
            s["repeat_count"] = {}
            s["stop"], s["blocker"] = None, None
            for p in s.get("pending", []):
                if p.pop("interrupted", False):
                    p["resumed_at"] = time.time()
                    f = self.dir / p["file"]
                    if not f.exists() or not f.read_text(errors="replace").strip():
                        threading.Thread(target=self._nudge, args=(p["agent"], p["prompt"], p.get("_assigned_model")), daemon=True).start()
            if s.get("phase") == "setup":
                s["setup_tries"] = 0
            if s["status"] in ("stopped", "error"):
                return self.resume()
            if s["status"] == "paused":
                s["status"] = "running"
        elif action == "ready" and s["status"] in ("booting", "waiting"):
            s["status"], s["detail"] = "running", "Agents ready"
        elif action == "nudge":
            for p in s["pending"]:
                threading.Thread(target=self._nudge, args=(p["agent"], p["prompt"], p.get("_assigned_model")), daemon=True).start()
        elif action == "skip":
            for p in list(s["pending"]):
                s["pending"].remove(p)
                f = self.dir / p["file"]
                text = f.read_text(errors="replace") if f.exists() else ""
                if text.strip():
                    p["bounced"] = p.get("bounced") or ["skipped"]
                    self.accept(p, text)
                else:
                    self.add_entry(p["agent"], p["phase"], p["kind"], "(turn skipped, no reply)",
                                   summary="Skipped: no reply", n=p["n"], file=p["file"], invalid=True)
        elif action == "swap":
            r = s["roles"]
            r["builder"], r["critic"] = r["critic"], r["builder"]
        elif action == "more":
            s["max_rounds"] += 1
            if s["status"] == "done" and s.get("chosen") and not s.get("verify_passed"):
                s.update(status="running", phase="deep", deep_step="critique", last_verdict="REVISE")
        elif action == "stop":
            s["status"], s["detail"] = "stopped", "Stopped. Resume to carry on where it left off."
        elif action == "close":
            workers.close(s)
            if s["status"] in ACTIVE:
                s["status"] = "stopped"
            s["detail"] = "Agents closed. Resume starts them again."
        else:
            raise ValueError("unknown action %s" % action)
        self.save()

    # ------------------------------------------------------------------- views
    def stop_reason(self):
        """Why the run is paused or stopped, in plain words: the rule, the numbers, and what the person can do."""
        s = self.st
        if s.get("status") not in ("waiting", "stopped", "error") and not s.get("paused"):
            return None
        if s.get("stop"):
            stop = s["stop"]
            failed = s.get("failed_turn") or {}
            old_errors = failed.get("failure_problems", [])
            known = old_errors and all(x.startswith(("Source ", "Search ", "files changed before decide:")) for x in old_errors)
            if (stop.get("rule") == "Research coverage incomplete" and s.get("task_type") == "research" and
                    failed.get("kind") in ("scout", "research_expand") and known):
                _, errors = researchflow.validate(self.ws, "research", require_full=False)
                outside = [name for error in old_errors if error.startswith("files changed before decide:")
                           for name in error.split(":", 1)[1].strip().split(", ") if bounds._outside_scope(name, "scout", "research")]
                if not errors and not outside:
                    gaps = researchflow.evidence_gaps(self.ws, "research")
                    return {"rule": "Research reading pending", "why": "The saved draft passes current partial checks. %d evidence items remain for the Duo researcher." % len(gaps),
                            "next": "Review the evidence list below. Resume lets Duo collect the missing records. No new upload is currently requested.",
                            "report": "research/evidence-needed.md"}
            return stop
        stall = s.get("stall", 0)
        if stall >= phases.STALL_LIMIT:
            return {"rule": "No-progress guard", "why": "The last %d build or revise turns changed no files (count: %d of %d allowed). Duo stops here so it does not pay for another turn that repeats them." % (phases.STALL_LIMIT, stall, phases.STALL_LIMIT),
                    "next": "Tell Duo what to change in the chat, or resume. A message or a resume resets this count."}
        if s.get("verify_failures", 0) >= 2:
            return {"rule": "Verify failed twice", "why": "The verifier rejected the work %d times in a row." % s["verify_failures"],
                    "next": "Read the verifier's reply in the chat, then tell Duo what to fix, or resume."}
        if s.get("phase") == "deep" and s.get("round", 0) >= s.get("max_rounds", 0):
            return {"rule": "Round limit", "why": "Round %d of %d used, and the critic still asks for changes." % (s["round"], s["max_rounds"]),
                    "next": "Raise Build rounds in the run settings, then resume. New runs start at %d." % config.CFG["max_rounds"]}
        if s.get("paused") and s.get("detail", "").endswith("Resume to accept it."):
            return {"rule": "High-impact change", "why": s["detail"][:-len(" Resume to accept it.")],
                    "next": "Resume to accept it, or send a message to change it."}
        if s.get("status") == "waiting" and "sandbox" in s.get("detail", "").lower():
            return {"rule": "Sandbox", "why": s.get("detail", ""), "next": "Check the sandbox log in the chat, fix Docker or the plan, then resume."}
        if s.get("paused"):
            return {"rule": "Paused by you", "why": "The run is paused.", "next": "Resume when you are ready."}
        return {"rule": "Stopped", "why": s.get("detail") or "The run is stopped.", "next": "Resume to carry on where it left off."}

    def view(self):
        visible_models = model_policy.human_view(self.st)
        s = dict(self.st)
        for key in ("_role_models", "_model_assignments", "_model_escalations"):
            s.pop(key, None)
        s["role_models"] = visible_models
        if visible_models:
            s["human_agent_labels"] = {}
            for agent in self.st["agents"]:
                name = "Claude" if agent == "claude" else "Codex" if agent == "codex" else agent
                s["human_agent_labels"].update({agent: name, orch.alias(self.st, agent): name})
        s.pop("handoff_log", None)
        s["broker"] = broker.view(self.dir)
        s['conversation'] = run_chat.history(self.dir)
        s['task_context']=task_context.update(self.dir,self.ws,self.st,s['broker'])
        s["stop_reason"] = self.stop_reason()
        if self.st.get("task_type") == "research":
            s["research_evidence_needed"] = researchflow.evidence_gaps(self.ws, "research")
            s["research_evidence_report"] = "research/evidence-needed.md"
        now = time.time()
        s["pending"] = [dict(p, age=int(now - p.get("first_issued", p["issued_at"])), base=None) for p in self.st["pending"]]
        s["flags"] = insights.flags(self.st["entries"], integrated_review=bool(s.get('verify_passed')))
        s["stats"] = insights.stats(self.st["entries"], self.st)
        s["token_use"] = token_ledger.summary(self.st)
        s["progress"] = insights.progress(self.st, history.medians(self.history_path(), self.st["task_type"] or "general"))
        s["chat_path"] = str(self.dir / "chat.md")
        s["labels"] = {a: self.label(a) for a in self.st["agents"]}
        busy = {p["agent"] for p in self.st["pending"]}
        live = self.st["status"] in ACTIVE
        s["agent_status"] = {a: ("stopped" if a in self.st["dead"] or not live else "working" if a in busy else "idle")
                             for a in self.st["agents"]}
        s["waiting"] = [dict(p["waiting"], agent=p["agent"], key=None) for p in self.st["pending"] if p.get("waiting")]
        s["pending"] = [dict(p, screen=None, answered=None) for p in s["pending"]]
        # Routing keys stay stable for app actions. Display records are anonymous.
        for key in ("entries", "ledger", "detail", "stop_reason", "waiting"):
            s[key] = orch.redact(self.st, s.get(key))
        s["labels"].update({orch.alias(self.st, a): orch.alias(self.st, a) for a in self.st["agents"]})
        return s

    def changed_files(self):
        return [{"status": v.strip(), "path": k} for k, v in _git_status(self.ws).items()][:200]

    # ------------------------------------------------------------ run settings
    def steer(self, action, value=None):
        """Add a constraint that applies to the rest of the run."""
        s = self.st
        if action == "constrain":
            text = " ".join(str(value or "").split())
            if not text:
                raise ValueError("Write the constraint first.")
            s.setdefault("constraints", []).append(text)
            self.human_message("Constraint for the rest of the run: " + text)
        else:
            raise ValueError("unknown steer action %s" % action)
        self.save()

    def retry_failed_turn(self):
        p = self.st.pop("failed_turn")
        self._queue_continuation(p)
        self._dispatch_continuation()
        return

    def record_error(self, ex):
        """Keep a failed loop step on the run so the page can show it with Retry and Stop."""
        message = str(ex) or ex.__class__.__name__
        previous = self.st.get("tick_failure", {})
        count = previous.get("count", 0) + 1 if previous.get("message") == message else 1
        self.st["tick_failure"] = {"message": message, "count": count}
        self.st["error"] = {"message": message, "at": now_hm()}
        if count == 3:
            self._pause("Repeated engine error", message, "Inspect the saved error. Correct its cause, then retry.")
            notify.send("Duo needs you", "The engine stopped after three repeated errors: " + message)
        self.save()

    def entries_after(self, n):
        return [e for e in self.st["entries"] if e.get("n", 0) > n]

    def update_settings(self, changes):
        """Change run settings mid-run. Everything is checked first; nothing is applied if any value is bad."""
        s = self.st
        role_names = ("builder", "critic", "verifier", "lead", "scorer", "scout", "proposer", "security", "challenger")
        if not isinstance(changes, dict):
            raise ValueError("settings must be an object")
        changes = dict(changes)
        if "sandbox" in changes:
            changes["sandbox"] = resolve_sandbox(changes["sandbox"])
        for key, val in changes.items():
            if key not in ("mode", "max_rounds", "roles", "sandbox", "approvals"):
                raise ValueError("unknown setting: %s" % key)
            if key == "mode" and val not in config.MODES:
                raise ValueError("mode must be one of %s" % ", ".join(config.MODES))
            if key == "max_rounds" and not (isinstance(val, int) and not isinstance(val, bool) and 1 <= val <= 100):
                raise ValueError("max_rounds must be a whole number from 1 to 100")
            if key == "roles" and not (isinstance(val, dict) and all(r in role_names for r in val)
                                       and all(a in s["agents"] for a in val.values())):
                raise ValueError("roles must map role names to agents in this run")
            if key == "sandbox" and not isinstance(val, bool):
                raise ValueError("sandbox must be on, off or auto")
            if key == "approvals" and val not in ("auto", "manual"):
                raise ValueError("approvals must be auto or manual")

        notes, result = [], dict(changes)
        if "mode" in changes and changes["mode"] != s["mode"]:
            s["mode"] = changes["mode"]
            notes.append("mode=%s" % s["mode"])
        if "max_rounds" in changes and changes["max_rounds"] != s["max_rounds"]:
            s["max_rounds"] = changes["max_rounds"]
            notes.append("max_rounds=%s" % s["max_rounds"])
        if "roles" in changes:
            moved = {r: a for r, a in changes["roles"].items() if s["roles"].get(r) != a}
            if moved:
                s["roles"].update(moved)
                notes.append("roles=" + ", ".join("%s=%s" % kv for kv in moved.items()))
        if "sandbox" in changes and changes["sandbox"] != s["sandbox_on"]:
            s["sandbox_on"] = changes["sandbox"]
            notes.append("sandbox=%s" % s["sandbox_on"])
            if not s["sandbox_on"] and s.get("sandbox_ready"):
                sandbox.stop(s["id"])
                s["sandbox_ready"], s["setup_done"] = False, False
        if "approvals" in changes and changes["approvals"] != s["approvals"]:
            s["approvals"] = changes["approvals"]
            notes.append("approvals=%s" % s["approvals"])
            for a in s["agents"]:
                spec = dict(agentmod.AGENT_SPECS[a], **config.CFG.get("agents", {}).get(a, {}))
                if s["approvals"] == "auto":
                    cmd = agentmod.boxed(a, spec["auto"]) if s["sandbox_on"] else spec["auto"]
                else:
                    cmd = spec["manual"]
                s["agent_info"][a]["cmd"] = cmd
            for a in list(s["panes"]):
                self.restart_agent(a)
            result["relaunched"] = True

        if notes:
            text = "Settings changed: " + ", ".join(notes)
            self.add_entry("system", s["phase"] or "task", "settings", text, summary=text)
        self.save()
        return result

    def chat(self):
        """The conversation as the person reads it: their messages with the first answer attached, then helper questions."""
        items = []
        for e in self.st["entries"]:
            if e["author"] == "human" and e["kind"] == "message":
                items.append({"n": e["n"], "role": "you", "text": e["prose"], "time_hm": e["time_hm"], "answer": None})
            elif e["author"] not in ("human", "system") and e["prose"].strip().startswith("Answer:"):
                open_items = [i for i in items if i["role"] == "you" and i["answer"] is None]
                if open_items:
                    body = e["prose"].strip()[len("Answer:"):].strip()
                    open_items[-1]["answer"] = {"by": e["author"], "text": body.split("\n\n")[0].strip(), "n": e["n"]}
        helpers = sorted(self.st.get("helper", []), key=lambda h: h["at"])
        return items + [{"role": "helper", "q": h["q"], "a": h["a"], "focus": h["focus"], "at": h["at"]} for h in helpers] + [dict(row,role='helper') for row in run_chat.history(self.dir)]


def load(duo_dir):
    p = Path(duo_dir) / "state.json"
    return Session(json.loads(p.read_text())) if p.exists() else None


def _latest_contribution(state):
    for entry in reversed(state.get("entries", [])):
        if entry.get("invalid") or entry.get("kind") not in ("build", "revise", "critique"):
            continue
        notes = entry.get("review_outcomes") or {}
        label = "Reviewer" if entry["kind"] == "critique" else "Builder"
        text = " · ".join(f"{item}: {result}" for item, result in notes.items()) or entry.get("summary")
        if text:
            return orch.redact(state, label + ": " + " ".join(text[:240].split()))
    return ""


def list_sessions(all_projects=True):
    out = []
    for _, i in session_index.entries(all_projects=all_projects):
        sp = Path(i["duo_dir"]) / "state.json"
        if sp.exists():
            try:
                st = json.loads(sp.read_text())
                out.append({"id": st["id"], "title": st["title"], "created": st["created"], "status": st["status"],
                            "task_type": st.get("task_type"), "project": st.get("project"),
                            "images": list(dict.fromkeys(path for entry in reversed(st["entries"])
                                for path in (entry.get("outputs") or []) if isinstance(path, str)
                                and Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif")))[:6],
                            "contribution": _latest_contribution(st), "entries": len(st["entries"])})
            except Exception:
                pass
    out.sort(key=lambda item: str(item['created']), reverse=True)
    return out


def mark_stopped(sid, detail):
    """Change only the status in state.json, so state files from older versions never break startup."""
    for _, i in session_index.entries(all_projects=False):
        sp = Path(i["duo_dir"]) / "state.json"
        if i["id"] == sid and sp.exists():
            st = json.loads(sp.read_text())
            st["status"], st["detail"] = "stopped", detail
            sp.write_text(json.dumps(st, indent=1, default=str))


def find_or_explain(sid):
    """Load a run, or raise a ValueError that says why it cannot be opened."""
    _, entry = session_index.locate(sid)
    if not entry:
        raise ValueError("No such run.")
    if not (Path(entry["duo_dir"]) / "state.json").exists():
        raise ValueError("This run's project folder is gone: %s" % Path(entry["duo_dir"]).parent.parent)
    return load(entry["duo_dir"])


def delete_run(sid, live_id=None):
    """Delete one finished run: its .duo/<run> folder and its index entry. The project's own files stay."""
    _, entry = session_index.locate(sid)
    if not entry:
        raise ValueError("No such run.")
    if sid == live_id:
        raise ValueError("This run is open and running. Stop it before you delete it.")
    d = Path(entry["duo_dir"]).resolve()
    sp = d / "state.json"
    if sp.exists() and json.loads(sp.read_text()).get("status") in ACTIVE:
        raise ValueError("This run is still active. Stop it before you delete it.")
    if d.parent.name != ".duo":
        raise ValueError("Refusing to delete: this is not a run folder.")
    if d.exists():
        shutil.rmtree(d)
    session_index.remove(sid)


def find(sid, all_projects=True):
    _, entry = session_index.locate(sid, all_projects=all_projects)
    return load(entry['duo_dir']) if entry else None
