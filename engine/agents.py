"""Find the agent CLIs installed on this Mac."""
import os
import shlex
import shutil
import subprocess
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

from . import config
from . import cli_adapters

# Use the native Codex configuration and tools. Ephemeral calls still avoid saved threads.
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
CODEX_HOME.mkdir(parents=True, exist_ok=True)  # Codex refuses to start when this folder is missing
os.environ["CODEX_HOME"] = str(CODEX_HOME)     # helper calls (codex exec) and trust settings use the same home
CODEX_ENV = "CODEX_HOME=%s " % shlex.quote(str(CODEX_HOME))

# Agents must never update themselves mid-session: an update makes the CLI exit.
CODEX_FLAGS = "--no-daemon -c check_for_update_on_startup=false -c disable_paste_burst=true -c web_search=live"
AGENT_SPECS = {
    "claude": {"label": "Claude",
               "auto": "DISABLE_AUTOUPDATER=1 claude --permission-mode auto",
               "manual": "DISABLE_AUTOUPDATER=1 claude"},
    "codex": {"label": "Codex",
              "auto": CODEX_ENV + "codex %s --approve-for-me" % CODEX_FLAGS,
              "manual": CODEX_ENV + "codex %s" % CODEX_FLAGS},
    "agy": {"label": "Antigravity", "auto": "agy --mode accept-edits", "manual": "agy"},
}


def launch_command(agent_id, cmd):
    """Keep Codex overrides in Duo's home and out of the shared app server."""
    old_claude = "DISABLE_AUTOUPDATER=1 claude --permission-mode acceptEdits"
    if agent_id == "claude":
        return AGENT_SPECS["claude"]["auto"] if cmd == old_claude else cmd
    if agent_id != "codex":
        return cmd
    words = shlex.split(cmd)
    i = 0
    while i < len(words) and "=" in words[i]:
        if words[i].startswith("CODEX_HOME="):
            words[i] = "CODEX_HOME=" + str(CODEX_HOME)
        i += 1
    if i == len(words) or Path(words[i]).name != "codex":
        return cmd
    old_flags = ["--sandbox", "workspace-write", "--ask-for-approval", "on-request", "-c", "sandbox_workspace_write.network_access=true"]
    tail = words[i + 1:]
    if tail == shlex.split(CODEX_FLAGS) + old_flags:
        words = words[:i + 1] + shlex.split(CODEX_FLAGS) + ["--approve-for-me"]
    if "--no-daemon" not in words:
        words.insert(i + 1, "--no-daemon")
    if not any(w.startswith("CODEX_HOME=") for w in words[:i]):
        words.insert(0, "CODEX_HOME=" + str(CODEX_HOME))
    return " ".join(shlex.quote(w) for w in words)


def codex_env():
    """Explicit environment for headless calls, independent of import order."""
    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    return dict(os.environ, CODEX_HOME=str(CODEX_HOME))


def _codex_position(words, start):
    """First positional argument, excluding option values and environment assignments."""
    value_flags = {"-c", "--config", "--enable", "--disable", "-m", "--model", "-p", "--profile",
                   "-s", "--sandbox", "-a", "--ask-for-approval", "-C", "--cd", "--add-dir",
                   "--remote", "--remote-auth-token-env", "-i", "--image", "-o", "--output-last-message",
                   "--output-schema", "--thread-source", "--color", "--cyber-access-program"}
    n = start + 1
    while n < len(words):
        if words[n] in value_flags:
            n += 2
        elif words[n].startswith("-"):
            n += 1
        else:
            return n
    return len(words)


def guarded_command(agent_id, cmd, workspace, container=None):
    """Prepare ephemeral workers; leave tool permissions and URL decisions to the CLI."""
    if agent_id not in ("claude", "codex"):
        try:
            adapter = cli_adapters.get(agent_id)
        except ValueError:
            return cmd
        words = shlex.split(cmd)
        i = 0
        while i < len(words) and "=" in words[i]:
            i += 1
        if i == len(words) or Path(words[i]).name != Path(adapter.executable).name:
            raise ValueError('Configured worker executable does not match adapter ' + agent_id)
        if adapter.protocol == 'antigravity' and any(
                w in ('--continue', '-c', '--conversation') or w.startswith('--conversation=') for w in words[i + 1:]):
            raise ValueError('Duo requires a fresh Antigravity call; conversation resume is disabled')
        return shlex.join(words[:i] + [sys.executable, str(config.APP_DIR / 'engine/codex_worker.py'),
                                      '--adapter', adapter.id, '--'] + words[i:])
    cmd = launch_command(agent_id, cmd)
    words = shlex.split(cmd)
    i = 0
    while i < len(words) and "=" in words[i]:
        i += 1
    if i == len(words) or Path(words[i]).name != agent_id:
        raise ValueError("Native worker requires the %s executable" % agent_id)
    # Old runs saved this exact Duo default before native permissions became authoritative.
    # Preserve any custom settings, including user or organization restrictions.
    if agent_id == "claude" and "--settings" in words:
        j = words.index("--settings")
        legacy = {"permissions": {"allow": ["WebSearch"] + ["WebFetch(domain:%s)" % domain for domain in
                  ("arxiv.org", "export.arxiv.org", "aclanthology.org", "openreview.net", "proceedings.neurips.cc",
                   "doi.org", "api.crossref.org", "dl.acm.org")]},
                  "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False}}
        try:
            supplied = json.loads(words[j + 1])
            boxed_legacy = dict(legacy, sandbox=dict(legacy["sandbox"],
                                excludedCommands=["./.duo/sbx *", "docker *", "docker-compose *"]))
            if supplied in (legacy, boxed_legacy):
                del words[j:j + 2]
        except (ValueError, IndexError):
            pass  # Let the CLI validate user-supplied settings and paths.
    if agent_id == "claude" and "-p" not in words and "--print" not in words:
        words = words[:i] + [sys.executable, str(config.APP_DIR / "engine/codex_worker.py"), "--"] + words[i:]
    if agent_id == "codex":
        # The interactive CLI does not support --ephemeral. A small terminal worker
        # starts an ephemeral exec call per assignment and keeps context only in RAM.
        args = words[i + 1:]
        pos = _codex_position(words, i)
        action = words[pos] if pos < len(words) else None
        if action not in (None, "exec") or any(x == "--worktree" or x == "--remote" or x.startswith("--remote=") for x in args):
            raise ValueError("Duo requires a new ephemeral Codex call; saved-thread and remote launches are disabled")
        nested = _codex_position(words, pos) if action == "exec" else len(words)
        if nested < len(words) and words[nested] in ("resume", "fork", "review"):
            raise ValueError("Duo cannot resume or fork a saved Codex thread")
        words += ["-c", 'history.persistence="none"']
        if action == "exec":
            if "--ephemeral" not in words:
                words.insert(pos + 1, "--ephemeral")
        else:
            # Preserve leading environment assignments for the worker and its children.
            words = words[:i] + [sys.executable, str(config.APP_DIR / "engine/codex_worker.py"), "--"] + words[i:]
    return shlex.join(words)


def guarded_argv(agent_id, argv, workspace):
    words = shlex.split(guarded_command(agent_id, shlex.join(argv), workspace))
    if words and words[0].startswith("CODEX_HOME="):
        words.pop(0)  # subprocess callers receive codex_env() separately.
    return words


@dataclass
class Agent:
    id: str
    label: str
    cmd_auto: str
    cmd_manual: str
    version: str
    capability_check: dict = None

    def capabilities(self):
        try:
            adapter = cli_adapters.get(self.id)
            return {'protocol': adapter.protocol, 'tools': 'tools' in adapter.capabilities,
                    'fresh_sessions': 'fresh' in adapter.capabilities,
                    'model_selection': bool(adapter.model_flag), 'source': 'adapter declaration'}
        except ValueError:
            return {'tools': False, 'fresh_sessions': False, 'model_selection': False, 'source': 'no adapter'}

    def to_dict(self):
        return dict(asdict(self), capabilities=self.capabilities())


def detect(which=shutil.which, run=subprocess.run):
    found = []
    specs = dict(AGENT_SPECS)
    adapters = cli_adapters.registry()
    for aid, adapter in adapters.items():
        specs.setdefault(aid, {'label': adapter.label, 'auto': adapter.launch(), 'manual': adapter.launch()})
    for aid, spec in specs.items():
        spec = dict(spec, **config.CFG.get("agents", {}).get(aid, {}))
        adapter = adapters.get(aid)
        path = which(adapter.executable if adapter else aid)
        if not path:
            continue
        try:
            r = run([path] + list(adapter.version_args if adapter else ('--version',)), capture_output=True, text=True, timeout=10)
        except Exception:
            continue
        if r.returncode != 0:
            continue
        version = (r.stdout or r.stderr or "").strip().splitlines()
        check = None
        if adapter:
            try:
                help_result = run([path] + list(adapter.help_args), capture_output=True, text=True, timeout=10)
                help_text = (help_result.stdout or '') + (help_result.stderr or '')
                missing = [flag for flag in adapter.required_flags if flag and flag not in help_text]
                check = {'ready': help_result.returncode == 0 and not missing, 'missing_flags': missing,
                         'source': 'CLI help; tool and fresh-session behavior declared by adapter'}
            except (OSError, subprocess.TimeoutExpired):
                check = {'ready': False, 'source': 'CLI help unavailable'}
        found.append(Agent(aid, spec["label"], launch_command(aid, spec["auto"]), launch_command(aid, spec["manual"]),
                           version[0] if version else "", check))
    return found


# Other agent CLIs Duo can see on this Mac. Duo lists them but does not launch them: a launch needs a
# reviewed command with a sandbox or approval mode, added per CLI. "bins" are command names on PATH;
# "dirs" are the CLI's own folder in your home, which shows it is set up even when PATH misses it.
OTHER_AGENTS = {
    "hermes": {"label": "Hermes", "bins": ["hermes"], "dirs": [".hermes"]},
    "gemini": {"label": "Gemini CLI", "bins": ["gemini"], "dirs": [".gemini"]},
    "opencode": {"label": "opencode", "bins": ["opencode"], "dirs": [".config/opencode"]},
    "aider": {"label": "Aider", "bins": ["aider"], "dirs": []},
    "qwen": {"label": "Qwen Code", "bins": ["qwen"], "dirs": [".qwen"]},
    "goose": {"label": "Goose", "bins": ["goose"], "dirs": [".config/goose"]},
    "amp": {"label": "Amp", "bins": ["amp"], "dirs": []},
    "cursor-agent": {"label": "Cursor Agent", "bins": ["cursor-agent"], "dirs": []},
    "cline": {"label": "Cline", "bins": ["cline"], "dirs": []},
}


def detect_other(which=shutil.which, run=subprocess.run, catalog=OTHER_AGENTS, known=AGENT_SPECS, home=None):
    """CLIs from the catalog that are installed but not launchable yet.
    Each entry: {id, label, version, path, launchable, found_by}. found_by is "path" or "folder"."""
    home = Path(home or os.path.expanduser("~"))
    found = []
    for aid, spec in catalog.items():
        if aid in known or aid in cli_adapters.registry():
            continue
        path = next((p for p in (which(b) for b in spec["bins"]) if p), None)
        folder = next((d for d in spec.get("dirs", []) if (home / d).exists()), None)
        if not path and not folder:
            continue
        version = ""
        if path:
            try:
                r = run([path, "--version"], capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    version = ((r.stdout or r.stderr or "").strip().splitlines() or [""])[0]
            except Exception:
                pass
        found.append({"id": aid, "label": spec["label"], "version": version, "path": path or "",
                      "launchable": False, "found_by": "path" if path else "folder"})
    return found


def boxed(agent_id, cmd):
    """Keep provider permissions unchanged when a task uses Docker."""
    return cmd
