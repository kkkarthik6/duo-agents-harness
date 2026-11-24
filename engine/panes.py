"""tmux driver: one pane per agent in one session. The Run talks to this through four methods,
so a fake driver can replace it in tests."""
import shlex
import time
import secrets
from pathlib import Path

from . import agents as agentmod, config, cli_adapters
from .tmux import capture, pane_dead, pane_env, send_key, send_text, split, tmux
from . import pane as panectl


class TmuxDriver:
    discovery_enabled = True
    supports_role_models = True
    supports_plan_discussion = True
    def __init__(self, session_name=None):
        self.name = session_name or config.CFG["tmux_session"]
        self.panes = {}
        self.model_now = {}
        self.review_panes = {}
        self.folders = {}

    def attach(self, agents, cwd):
        text = tmux("list-panes", "-t", self.name, "-F", "#{pane_id}\t#{@duo_label}", check=False)
        labels = {}
        for line in text.splitlines():
            fields = line.split("\t", 1)
            if len(fields) == 2:
                labels[fields[1]] = fields[0]
        if all("Worker %d" % (i + 1) in labels for i in range(len(agents))):
            self.panes = {actor: labels["Worker %d" % (i + 1)] for i, actor in enumerate(agents)}
            self.folders = {actor: Path(cwd) for actor in agents}
            return True
        if labels:
            # Keep any surviving old workers and their child jobs intact.
            self.name += "-continuation-" + secrets.token_hex(4)
        return False

    def launch(self, agents, cwd):
        tmux("kill-session", "-t", self.name, check=False)
        self.panes = {}
        for i, a in enumerate(agents):
            default = agentmod.AGENT_SPECS.get(a, {})
            if not default:
                default = {'auto': cli_adapters.get(a).launch()}
            spec = dict(default, **config.CFG.get("agents", {}).get(a, {}))
            cmd = agentmod.launch_command(a, spec.get("auto", a))
            if (cwd / ".duo/sbx").exists():
                cmd = agentmod.boxed(a, cmd)
            cmd = agentmod.guarded_command(a, cmd, cwd)
            if i == 0:
                pid = tmux("new-session", *pane_env(), "-d", "-s", self.name, "-x", "240", "-y", "60", "-c", str(cwd), "-P", "-F", "#{pane_id}", cmd).strip()
                tmux("set-option", "-w", "-t", self.name, "remain-on-exit", "on", check=False)
            else:
                pid = split(self.panes[agents[0]], str(cwd), cmd)
            tmux("set-option", "-p", "-t", pid, "@duo_label", "Worker %d" % (i + 1), check=False)
            self.panes[a] = pid
            self.folders[a] = Path(cwd)
        tmux("select-layout", "-t", self.name, "even-horizontal", check=False)
        tmux("set-option", "-t", self.name, "pane-border-status", "top", check=False)
        tmux("set-option", "-t", self.name, "pane-border-format", " #{@duo_label} ", check=False)
        return dict(self.panes)

    def alive(self, agent):
        p = self.panes.get(agent)
        return bool(p) and not pane_dead(p)

    def start_review(self, agent, cwd):
        """Fresh process and sanitized workspace: no builder conversation in context."""
        if agent in self.review_panes:
            raise RuntimeError("Review already running")
        from . import trust
        trust.trust_folder(cwd)
        default = agentmod.AGENT_SPECS.get(agent, {})
        if not default:
            default = {'auto': cli_adapters.get(agent).launch()}
        spec = dict(default, **config.CFG.get("agents", {}).get(agent, {}))
        cmd = agentmod.launch_command(agent, getattr(self, "commands", {}).get(agent, spec.get("auto", agent)))
        cmd = agentmod.guarded_command(agent, cmd, cwd)
        original = self.panes[agent]
        pid = split(original, str(cwd), cmd)
        self.review_panes[agent] = (original, self.model_now.pop(agent, None), self.folders.get(agent))
        self.panes[agent] = pid
        self.folders[agent] = Path(cwd)
        tmux("set-option", "-p", "-t", pid, "@duo_label", "Reviewer", check=False)
        time.sleep(config.CFG.get("boot_seconds", 6))

    def end_review(self, agent):
        if agent in self.review_panes:
            tmux("kill-pane", "-t", self.panes[agent], check=False)
            original, model, folder = self.review_panes.pop(agent)
            self.panes[agent] = original
            self.folders[agent] = folder
            self.model_now.pop(agent, None)
            if model:
                self.model_now[agent] = model

    def screen(self, agent):
        return capture(self.panes[agent]) if agent in self.panes else ""

    def key(self, agent, key):
        """Press one key in the agent's pane (approve or deny an approval question)."""
        if agent in self.panes:
            send_key(self.panes[agent], key)

    def idle(self, agent):
        return panectl.idle(self.screen(agent), agent)

    def worker_failed(self, agent):
        screen = self.screen(agent)
        tail = screen.rsplit("Working on assignment...", 1)[-1]
        return "DUO_WORKER_RESULT:failed" in tail and self.idle(agent)

    def send(self, agent, prompt, model=None, skill=None):
        """Switch model when needed, then send the prompt (through the skill's slash command for Claude)."""
        p = self.panes[agent]
        ok, _ = panectl.wait_idle(p, agent, 20)
        if not ok:
            raise RuntimeError("Agent input is not ready; task was not sent")
        if model and self.model_now.get(agent) != model:
            ok, _ = panectl.command(p, agent, "/model " + model, timeout=20, picker_value=model)
            if ok:
                self.model_now[agent] = model
            else:
                raise RuntimeError("Model switch did not finish; task was not sent")
        text = prompt
        if skill and agent == "claude":
            text = "/%s %s" % (skill, prompt)
        # Long prompts are files. The terminal carries only a short assignment pointer.
        rel = "duo/assignments/%s.md" % secrets.token_hex(8)
        path = self.folders[agent] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        wire = "DUO assignment: read %s and complete the assigned task." % path.resolve()
        send_text(p, wire)
        time.sleep(2.0)
        after = capture(p)
        if panectl.idle(after, agent) and wire in after and not panectl.has_question(after):
            send_text(p, "", enter=True)          # the TUI swallowed the Enter

    def usage(self, agent):
        return panectl.usage(self.panes[agent], agent) if agent in self.panes else None

    def nudge(self, agent, text):
        if not panectl.has_question(self.screen(agent)):
            send_text(self.panes[agent], text)

    def compact_if_needed(self, agent, turns_since):
        return panectl.compact_if_needed(self.panes[agent], agent, turns_since) if agent in self.panes else False

    def kill(self):
        tmux("kill-session", "-t", self.name, check=False)
